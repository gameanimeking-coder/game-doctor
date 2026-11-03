"""Tested fixes: before a suggested fix is shown as a fix, Game Doctor tries it on a hidden copy of the game.

Shared core (every engine): apply the change to an in-memory copy of the file (the game on disk is never
touched: only this thread's open() sees the changed text), scan the copy again and compare.
  - works: the problem is gone and nothing new appeared
  - failed: the problem is still there, or the change caused a new problem
Per-format layer:
  - SugarCube, Harlowe 3, X-Lowe: the scene with the fix is also played in the invisible browser (Full scan
    machinery, with the fixed copy) and compared with the same scene played from the original.
  - Chapbook, Snowman, other HTML games: the fixed copy is loaded in the invisible browser; start-up errors
    are compared with the original's.
  - Ren'Py, RPG Maker, Ink, ChoiceScript: the scan again (no browser).
When there are several possible fixes (several "Did you mean" names, several clearly mistyped file names),
each is tried; only a working one is offered, and when more than one works the closest name is picked.

Speed: fixes are tried together in a few rounds (one rescan per round); a round with a surprise is split in
halves until the fix that caused it is found. This runs after the list is shown; results fill in.
"""
import builtins
import collections
import copy
import difflib
import io
import os
import re
import threading
import time

import fixes
import scanner

WORKS, FAILED = 'works', 'failed'
MAX_ALTERNATIVES = 3       # other names tried besides the scanner's own suggestion
MAX_PLAY_SCENES = 40       # scenes played per round; more fixes are checked by scanning only
PLAY_BUDGET = 24           # ways to play each scene (a fix only needs the scene to show)

# ---------------------------------------------------------------------- the hidden copy
_local = threading.local()
_real_open = builtins.open


def _open(file, mode='r', *args, **kw):
    over = getattr(_local, 'files', None)
    if over and isinstance(file, (str, os.PathLike)) and not set(mode) & set('wax+'):
        text = over.get(os.path.normcase(os.path.abspath(os.fspath(file))))
        if text is not None:
            return io.BytesIO(text.encode('utf-8')) if 'b' in mode else io.StringIO(text)
    return _real_open(file, mode, *args, **kw)


builtins.open = _open


class hidden_copy:
    """While active, this thread reads the changed text instead of these files: {path: text}."""
    def __init__(self, files):
        self.files = {os.path.normcase(os.path.abspath(p)): t for p, t in files.items()}

    def __enter__(self):
        _local.files = self.files

    def __exit__(self, *exc):
        _local.files = None


# ---------------------------------------------------------------------- what can be tried
class Candidate:
    def __init__(self, issue, clone, label, rank):
        self.issue, self.clone, self.label, self.rank = issue, clone, label, rank
        self.edits = {}            # (path, line) -> (line now, line after the fix), as stored in the file
        self.ok = None             # True / False after testing
        self.why = ''              # why it failed
        self.revealed = []         # problems on the fixed line that this one was hiding
        self.played = ''           # scene played with the fix
        self.play_gone = False     # an error that showed while playing the original is gone

    def lines(self):
        return {k for k in self.edits}


def _wanted(i):
    m = re.search(r'missing (?:passage|label|knot/stitch|scene) "(.+?)"|not found: (.+)$|^\$(\w+) is used', i.title)
    return next((g for g in m.groups() if g), '') if m else ''


def _label(i):
    """The name the scanner's own fix puts in."""
    for pat in (r'Did you mean "(?:-> )?(.+?)"\?', r'file meant is probably "(.+?)"', r'very close to (<<.+?>>|\(.+?:\))',
                r'misspelling of (\$\w+)', r'reads (\$\w+)', r'Did you mean (\S+?)\?', r'Change it to "(?:-> )?(.+?)"'):
        m = re.search(pat, (i.fix or '') + ' ' + (i.what or ''))
        if m:
            return m.group(1)
    if i.rename:
        return i.rename[1]
    return ''


def alternatives(i, res):
    """Other names worth trying for this problem: [(label, clone of the issue with that name as the fix)].
    Missing files: only names that are clearly mistyped (scanner.file_typo), never look-alike pictures."""
    out = []
    first = _label(i)
    m = re.search(r'to missing (?:passage|scene) "(.+?)"', i.title)
    if m and res.engine == 'twine':
        names = [n for n in res.passages if n != i.passage and n != first and not scanner._numbers_differ(m.group(1), n)]
        for n in difflib.get_close_matches(m.group(1), names, n=MAX_ALTERNATIVES, cutoff=0.75):
            c = copy.copy(i)
            c.fix = f'Did you mean "{n}"?'
            out.append((n, c))
        return out
    m = re.match(r'^(\w+) not found: (.+)$', i.title)
    if m and 'file meant is probably' in i.what:
        path = m.group(2)
        folder = os.path.dirname(path.replace('\\', '/')).lower()
        found = []
        for root, _, files in os.walk(os.path.join(res.game_dir, folder)):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), res.game_dir).replace('\\', '/')
                if os.path.dirname(rel).lower() == folder and rel != first and scanner.file_typo(path, rel):
                    found.append(rel)
            break
        for rel in sorted(found, key=lambda r: -difflib.SequenceMatcher(None, path.lower(), r.lower()).ratio())[:MAX_ALTERNATIVES]:
            c = copy.copy(i)
            c.what = re.sub(r'file meant is probably "(.+?)"', f'file meant is probably "{rel}"', i.what)
            out.append((rel, c))
        return out
    m = re.match(r'^\$(\w+) is used but never set$', i.title)
    if m and re.search(r'misspelling of \$|reads \$', i.what):
        names = [v.lstrip('$') for v in res.known_vars if v.lstrip('$') not in (m.group(1), first.lstrip('$'))]
        for n in difflib.get_close_matches(m.group(1), names, n=MAX_ALTERNATIVES, cutoff=0.8):
            c = copy.copy(i)
            c.what = re.sub(r'misspelling of \$\w+|reads \$\w+', f'misspelling of ${n}', i.what)
            out.append(('$' + n, c))
    return out


def _file_lines(res, path):
    if res.engine == 'twine' and os.path.normcase(path) == os.path.normcase(res.html_path) and res.raw_lines:
        return res.raw_lines
    try:
        with _real_open(path, encoding='utf-8' if res.engine in ('twine', 'html') else 'utf-8-sig', errors='replace') as f:
            return f.read().split('\n')
    except OSError:
        return []


def _edits(c, res, cache):
    """The changed lines of one candidate, as stored in the file. Empty when it cannot be expressed exactly."""
    i = c.clone
    path = os.path.abspath(i.file or res.html_path)
    if path not in cache:
        cache[path] = _file_lines(res, path)
    lines = cache[path]
    ff = fixes.forms(i, res, 'file')
    if not ff or not i.line or i.line > len(lines) or lines[i.line - 1].rstrip('\r') != ff[0]:
        return {}
    out = {(path, i.line): (ff[0], ff[1])}
    if i.rename and i.also_lines:
        wrong, right = i.rename
        if res.engine == 'twine':
            wrong, right = fixes.escape_twine(wrong), fixes.escape_twine(right)
        for n in i.also_lines:
            if 0 < n <= len(lines) and (path, n) not in out:
                old = lines[n - 1].rstrip('\r')
                new = fixes.apply_rename(old, (wrong, right))
                if new != old:
                    out[(path, n)] = (old, new)
    return out


def candidates(res):
    """{issue key: [Candidate, ...]} for every finding with a suggested change, best first."""
    out, cache = {}, {}
    for i in res.issues:
        if not (i.old or i.new) or i.category == 'Play-test' or i.key in out:
            continue
        cands = [Candidate(i, i, _label(i), 0)]
        for k, (label, clone) in enumerate(alternatives(i, res), 1):
            clone.old = clone.new = ''
            clone.rename, clone.also_lines = (), []
            fixes.enrich_issue(clone, res)
            if clone.old or clone.new:
                cands.append(Candidate(i, clone, label, k))
        for c in cands:
            c.edits = _edits(c, res, cache)
        cands = [c for c in cands if c.edits]
        if cands:
            out[i.key] = cands
    return out


# ---------------------------------------------------------------------- testing
def _lk(i):
    """Identity of a finding for before/after. Notes that only count things ("48 values are set but never
    checked") stay the same note when the number changes."""
    k = fixes.loose_key(i)
    return re.sub(r'\d+', '#', k) if i.severity == scanner.INFO else k


def _counts(issues):
    return collections.Counter(_lk(i) for i in issues if i.category != 'Play-test')


class Tester:
    def __init__(self, res, target, game_dir=None, progress=None, stop=None, play=True):
        self.res, self.target, self.game_dir = res, target, game_dir
        self.progress = progress or (lambda p, m: None)
        self.stop = stop or (lambda: False)
        self.play = play
        base = [i for i in getattr(res, 'quick_issues', res.issues) if i.category != 'Play-test']
        self.before = _counts(base)
        self.before_keys = {fixes.make_key(i) for i in base}
        self.base_lines = {}
        self.scans = 0

    def text_with(self, cands):
        files = {}
        for c in cands:
            for (path, n), (old, new) in c.edits.items():
                if path not in files:
                    files[path] = list(self.base_lines.setdefault(path, _file_lines(self.res, path)))
                files[path][n - 1] = new
        return {p: '\n'.join(ls) for p, ls in files.items()}

    def scan_with(self, cands):
        files = self.text_with(cands)
        self.scans += 1
        with hidden_copy(files):
            return scanner.scan_any(self.target, self.game_dir), files

    # -- one group of fixes that touch different lines
    def check(self, group):
        if not group or self.stop():
            return
        res2, _ = self.scan_with(group)
        after = _counts(res2.issues)
        per_key = collections.Counter(_lk(c.issue) for c in group)
        surplus = {k: after[k] - self.before[k] for k in after if after[k] > self.before[k]}
        new = [j for j in res2.issues if j.category != 'Play-test' and surplus.get(_lk(j))
               and fixes.make_key(j) not in self.before_keys]
        blamed, loose = collections.defaultdict(list), []
        for j in new:
            jpath = os.path.abspath(j.file or res2.html_path)
            on = [c for c in group if (jpath, j.line) in c.edits]
            near = on or [c for c in group if j.passage and j.passage == c.issue.passage]
            if len(near) == 1:
                blamed[id(near[0])].append((j, bool(on)))
            else:
                loose.append(j)
        gone = {id(c): after[_lk(c.issue)] <= self.before[_lk(c.issue)] - per_key[_lk(c.issue)]
                for c in group}
        shared_left = any(not gone[id(c)] and per_key[_lk(c.issue)] > 1 for c in group)
        if len(group) > 1 and (loose or shared_left):
            half = len(group) // 2           # a surprise: find the fix that caused it
            self.check(group[:half])
            self.check(group[half:])
            return
        for c in group:
            caused = [j for j, on in blamed.get(id(c), []) if not on] + (loose if len(group) == 1 else [])
            c.revealed = [j.title for j, on in blamed.get(id(c), []) if on]
            if not gone[id(c)]:
                c.ok, c.why = False, 'the problem was still there afterwards'
            elif caused:
                c.ok, c.why = False, f'it caused a new problem: {caused[0].title}'
            else:
                c.ok = True

    # -- playing the changed scenes in the invisible browser
    def replay(self, rounds):
        import fullscan
        import playtest
        res = self.res
        if not self.play or res.engine != 'twine' or not playtest.find_browser() or self.stop():
            return
        rounds = [[c for c in g if c.ok] for g in rounds]
        if fullscan.can_play(res):
            scenes = set(fullscan.scene_names(res))
            rounds = [[c for c in g if c.issue.passage in scenes][:MAX_PLAY_SCENES] for g in rounds]
            rounds = [g for g in rounds if g]
            if not rounds:
                return
            self.progress(80, 'Playing the fixed scenes in the invisible browser')
            base = self._play(res, {c.issue.passage for g in rounds for c in g}, None)
            if base is None:
                return
            for group in rounds:
                if self.stop():
                    return
                only = {c.issue.passage for c in group}
                fixed = self._play_fixed(group, only)
                if fixed is None:
                    return
                for scene in only:
                    here = [c for c in group if c.issue.passage == scene]
                    worse = fixed.get(scene, set()) - base.get(scene, set())
                    if worse and len(here) > 1:
                        for c in here:          # several fixes in one scene: play each alone
                            self._judge(c, scene, base, self._play_fixed([c], {scene}) or {})
                        continue
                    for c in here:
                        self._judge(c, scene, base, fixed)
                        c.play_gone = c.play_gone and len(here) == 1   # with several fixes, which one did it is unknown
            return
        rounds = [g for g in rounds if g]
        if not rounds:
            return
        self.progress(80, 'Loading the fixed game in the invisible browser')
        main = os.path.abspath(res.html_path)
        base = self._load(res, None)
        for group in rounds:
            if base is None or self.stop():
                return
            fixed = self._load(res, self.text_with(group).get(main))
            if fixed is None or not fixed - base:
                continue
            for c in group:
                alone = self._load(res, self.text_with([c]).get(main))
                if alone and alone - base:
                    c.ok, c.why = False, f'the game then shows a new error when it starts: {sorted(alone - base)[0]}'

    def _judge(self, c, scene, base, fixed):
        worse = fixed.get(scene, set()) - base.get(scene, set())
        if worse:
            c.ok, c.why = False, f'playing the scene "{scene}" then shows a new problem: {sorted(worse)[0]}'
        else:
            c.played = scene
            c.play_gone = bool(base.get(scene, set()) - fixed.get(scene, set()))

    def _play_fixed(self, group, only):
        res2, files = self.scan_with(group)
        text = files.get(os.path.abspath(self.res.html_path))
        return self._play(res2, only, (lambda _t: text) if text is not None else None)

    def _play(self, res, only, patch):
        import fullscan
        from playtest import PlaytestError
        try:
            issues, _ = fullscan.run(res, stop=self.stop, state={}, budget=PLAY_BUDGET, only=only, patch=patch)
        except (PlaytestError, OSError):
            return None
        out = collections.defaultdict(set)
        for j in issues:
            if j.passage and j.severity != scanner.INFO:
                out[j.passage].add(re.sub(r'\s+', ' ', j.title))
        return out

    def _load(self, res, text):
        import playtest
        try:
            with playtest.launch(res, patch=(lambda _t: text) if text is not None else None) as ses:
                time.sleep(1.0)
                return {j.title for j in playtest.runtime_issues([], ses.load_events, ses.port, res)}
        except (playtest.PlaytestError, OSError):
            return None

    # -- everything
    def run(self):
        """Returns {issue key: verdict dict} (see verdict())."""
        t0 = time.time()
        allc = candidates(self.res)
        if not allc:
            return {}
        todo = [c for cs in allc.values() for c in cs]
        rounds = []
        while todo:                          # each round: different lines only, and one try per problem
            used, keys, this, rest = set(), set(), [], []
            for c in todo:
                if c.lines() & used or c.issue.key in keys:
                    rest.append(c)
                else:
                    this.append(c)
                    used |= c.lines()
                    keys.add(c.issue.key)
            rounds.append(this)
            todo = rest
        for k, group in enumerate(rounds):
            if self.stop():
                return {}
            self.progress(int(100 * k / (len(rounds) + 1)), ('Testing the suggested fixes on a hidden copy: round {k} of {n}',
                                                              {'k': k + 1, 'n': len(rounds)}))
            self.check(group)
        self.replay(rounds)
        if self.stop():
            return {}
        out = {key: verdict(cs) for key, cs in allc.items()}
        self.seconds = time.time() - t0
        return out


def verdict(cands):
    """What to show for one problem, from its tested candidates (the scanner's own suggestion first)."""
    good = [c for c in cands if c.ok]
    wrong = _wanted(cands[0].issue)
    if good:
        best = min(good, key=lambda c: c.rank)
        how = ('Game Doctor tried this change on a hidden copy of the game (your file was not changed) and checked '
               'it again: the problem is gone and nothing new broke.')
        if best.played:
            how += f' It also played the scene "{best.played}" with the change in the invisible browser: no new problems.'
            if best.play_gone:
                how += ' The error that showed while playing it is gone too.'
        others = [c.label for c in good if c is not best and c.label]
        if others:
            how += (' {others} would also remove the problem. "{best}" was picked because its name is the closest to '
                    'what the game wrote{wrong}.').format(others=', '.join(f'"{o}"' for o in others), best=best.label,
                                                          wrong=f' ("{wrong}")' if wrong else '')
        if best.revealed:
            how += (f' After this change Game Doctor sees another problem on the same line that this one was hiding: '
                    f'{best.revealed[0]}. Fix that one next.')
        return dict(state=WORKS, how=how, picked=best.label, others=others, rank=best.rank,
                    old=best.clone.old, new=best.clone.new, rename=best.clone.rename,
                    also_lines=list(best.clone.also_lines), search=best.clone.search)
    tried = '; '.join((f'"{c.label}": ' if c.label and len(cands) > 1 else '') + (c.why or 'it could not be tested')
                      for c in cands)
    how = ('Game Doctor could not find a working fix. It tried the change it would suggest on a hidden copy of the '
           f'game (your file was not changed), but {tried}. So it is not offered as a fix: fix this by hand.')
    return dict(state=FAILED, how=how, tried=[(c.clone.old, c.clone.new) for c in cands])


def apply(res):
    """Put the test results (res.fix_tests) on the findings. A fix that did not work is not offered any more
    (the change that was tried is kept in i.tried, so nothing is hidden)."""
    tests = getattr(res, 'fix_tests', None) or {}
    for lst in (res.issues, getattr(res, 'quick_issues', None) or []):
        for i in lst:
            v = tests.get(i.key)
            if not v or getattr(i, 'tested', None):     # already done (also on copies made for a Full scan)
                continue
            i.tested = v
            if v['state'] == WORKS and v['rank']:
                i.old, i.new, i.rename, i.also_lines, i.search = v['old'], v['new'], v['rename'], list(v['also_lines']), v['search']
            elif v['state'] == FAILED:
                i.tried = (i.old, i.new)
                i.old, i.new, i.rename, i.also_lines = '', '', (), []
                i.difficulty = fixes.difficulty(i)
                i.priority = fixes.priority(i)


def count_testable(res):
    return sum(1 for i in res.issues if (i.old or i.new) and i.category != 'Play-test')


def estimate_seconds(res):
    """Rough extra time: a few rescans, plus the browser for games it can play."""
    n = count_testable(res)
    if not n:
        return 0
    scan = max(0.2, float(res.stats.get('seconds', 1) or 1))
    rounds = 2 + min(4, n // 20)
    extra = 20 if res.engine == 'twine' else 0
    return int(scan * rounds * 1.5 + extra)


def status_word(i):
    v = getattr(i, 'tested', None)
    return v['state'] if v else ''


if __name__ == '__main__':
    import sys
    res = scanner.scan_any(sys.argv[1])
    t = time.time()
    tester = Tester(res, sys.argv[1], play='--no-play' not in sys.argv,
                    progress=lambda p, m: print(f'  {p}% {m[0].format(**m[1]) if isinstance(m, tuple) else m}', flush=True))
    out = tester.run()
    for key, v in out.items():
        print(f'{v["state"]:7} {key[:100]}\n        {v["how"][:400]}')
    print(f'{len(out)} fixes tested, {tester.scans} rescans, {time.time() - t:.0f}s')
