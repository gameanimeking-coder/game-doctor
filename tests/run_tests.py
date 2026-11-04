"""Regression tests for Game Doctor.

  python tests/run_tests.py            run everything
  python tests/run_tests.py --update   accept the current results as the new snapshots

1. Snapshots: scans the real games listed in GAMES and compares every finding with
   the saved snapshot, so changes to old checks are always noticed.
2. Fixtures: small test games with planted bugs. Each must produce the expected
   findings (and clean ones must produce none of the listed kinds).
3. Accuracy: real games in tests/accuracy/*.json, each "Broken" finding checked by hand.
   A Broken finding not in the verified list fails the test; precision is printed.
   --full-accuracy also scans the big games (several minutes); --no-accuracy skips this part.
4. Missed bugs: one known bug at a time is planted in a copy of a small game and must be found (tests/missed_bugs.py,
   about 3-4 minutes); fails when the score drops below its floor. --no-missed skips this part.
"""
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import scanner  # noqa: E402

GAME_DIR = r'E:\E-Games\English\Condemned town version 1.08a'
GAMES = {
    '1.08a': os.path.join(GAME_DIR, 'Condemned town version 1.08a.html'),
    '1.08b': os.path.join(GAME_DIR, 'Condemned town version 1.08b (fixed).html'),
    'V3': os.path.join(GAME_DIR, 'Condemned_town_version_1_08_fixed V3.html'),
}
SNAP_DIR = os.path.join(HERE, 'snapshots')
FIX_DIR = os.path.join(HERE, 'fixtures')


def key(i):
    return f'{i.severity} | {i.category} | {i.title} | {i.passage}'


def run_snapshots(update):
    ok = True
    os.makedirs(SNAP_DIR, exist_ok=True)
    for name, path in GAMES.items():
        if not os.path.isfile(path):
            print(f'  [skip] {name}: game file not found')
            continue
        t = time.time()
        res = scanner.scan(path)
        keys = sorted(key(i) for i in res.issues)
        snap = os.path.join(SNAP_DIR, name + '.json')
        info = f'{len(keys)} findings, health {res.stats.get("health")}%, {time.time() - t:.1f}s'
        if update or not os.path.isfile(snap):
            with open(snap, 'w', encoding='utf-8') as f:
                json.dump(dict(keys=keys, stats={k: v for k, v in res.stats.items() if k != 'seconds'}), f, indent=0)
            print(f'  [saved] {name}: {info}')
            continue
        old = json.load(open(snap, encoding='utf-8'))
        before, after = set(old['keys']), set(keys)
        gone, new = sorted(before - after), sorted(after - before)
        if gone or new:
            ok = False
            print(f'  [CHANGED] {name}: {info}')
            for k in gone[:40]:
                print('      - ' + k)
            for k in new[:40]:
                print('      + ' + k)
            if len(gone) > 40 or len(new) > 40:
                print(f'      ... {len(gone)} gone, {len(new)} new in total')
        else:
            print(f'  [same] {name}: {info}')
    return ok


def run_fixtures():
    ok = True
    if not os.path.isdir(FIX_DIR):
        return ok
    for name in sorted(os.listdir(FIX_DIR)):
        d = os.path.join(FIX_DIR, name)
        spec_file = os.path.join(d, 'expect.json')
        if not os.path.isfile(spec_file):
            continue
        spec = json.load(open(spec_file, encoding='utf-8'))
        target = os.path.join(d, spec['open'])
        try:
            res = scanner.scan_any(target)
        except Exception as e:
            print(f'  [FAIL] {name}: scan crashed: {e!r}')
            ok = False
            continue
        titles = [f'{i.severity}|{i.category}|{i.title}' for i in res.issues]
        missing = [m for m in spec.get('must', []) if not any(m.lower() in t.lower() for t in titles)]
        unwanted = [m for m in spec.get('must_not', []) if any(m.lower() in t.lower() for t in titles)]
        texts = [f'{i.title}: {i.what} {i.fix}' for i in res.issues]
        unwanted += [f'text "{m}" in: {t[:160]}' for m in spec.get('must_not_text', []) for t in texts if m.lower() in t.lower()]
        scanner_errors = [t for t in titles if '|Scanner|' in t]
        if missing or unwanted or scanner_errors:
            ok = False
            print(f'  [FAIL] {name} ({res.stats.get("format")}, {len(titles)} findings)')
            for m in missing:
                print(f'      missing: {m}')
            for m in unwanted:
                print(f'      should not appear: {m}')
            for m in scanner_errors:
                print(f'      scanner problem: {m}')
            if missing:
                for t in titles:
                    print(f'        got: {t}')
        else:
            print(f'  [ok] {name}: {len(spec.get("must", []))} expected findings present ({res.stats.get("format")})')
    return ok


def run_fix_application():
    """Apply every suggested fix (file form) to a temporary copy, rescan, and check that
    exactly those findings disappear and nothing new appears."""
    import shutil
    import tempfile
    import fixes
    ok = True
    targets = [os.path.join(FIX_DIR, 'sugarcube_bugs', 'game.html'), GAMES['1.08b'], GAMES['1.08a']]
    for src in targets:
        if not os.path.isfile(src):
            continue
        res = scanner.scan_any(src)
        lines = list(res.raw_lines or open(src, encoding='utf-8').read().split('\n'))
        applied = {}
        changed = set()
        for i in res.issues:
            f = fixes.forms(i, res, 'file')
            if not f or not i.line:
                continue
            cur = lines[i.line - 1].rstrip('\r')
            if i.line in changed or cur != f[0]:
                continue   # one fix per line per round, so fixes never overwrite each other
            lines[i.line - 1] = f[1]
            applied[i.line] = i
            changed.add(i.line)
            for n in i.also_lines:   # a renamed variable must be renamed on every line it appears
                if n not in changed:
                    lines[n - 1] = fixes.apply_rename(lines[n - 1], i.rename)
                    changed.add(n)
        tmp = tempfile.mkdtemp()
        try:
            dst = os.path.join(tmp, os.path.basename(src))
            with open(dst, 'w', encoding='utf-8', newline='') as fh:
                fh.write('\n'.join(lines))
            res2 = scanner.Scanner(dst, os.path.dirname(src)).run()
            before = {fixes.loose_key(i) for i in res.issues}
            after_counts = {}
            for i in res2.issues:
                after_counts[fixes.loose_key(i)] = after_counts.get(fixes.loose_key(i), 0) + 1
            before_counts = {}
            for i in res.issues:
                before_counts[fixes.loose_key(i)] = before_counts.get(fixes.loose_key(i), 0) + 1
            not_fixed = [i for i in applied.values()
                         if after_counts.get(fixes.loose_key(i), 0) >= before_counts.get(fixes.loose_key(i), 0)]
            new_issues = [i for i in res2.issues if fixes.loose_key(i) not in before
                          and not re.match(r'(Unused files|Broken files|Portability|HTML)\|\d+ ', fixes.loose_key(i))]
            # A fix can uncover the next problem on the same line (e.g. <<elesif $x=1>> -> <<elseif $x=1>>
            # now shows the "=" problem). That is expected; anything new on an untouched line is a regression.
            revealed = [i for i in new_issues if i.line in changed or i.category == 'Variables']
            new = [i for i in new_issues if i not in revealed]
            name = os.path.basename(src)
            for i in revealed:
                print(f'      (revealed on a fixed line, expected) {i.title} line {i.line}')
            if not_fixed or new:
                ok = False
                print(f'  [FAIL] {name}: applied {len(applied)} fixes')
                for i in not_fixed:
                    print(f'      still there after fix: {i.title} (line {i.line})')
                for i in new:
                    print(f'      NEW after fixing: {fixes.loose_key(i)} line {i.line}')
            else:
                print(f'  [ok] {name}: {len(applied)} suggested fixes applied, all gone, nothing new '
                      f'({len(res.issues)} -> {len(res2.issues)} findings, health {res.stats["health"]}% -> {res2.stats["health"]}%)')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return ok


ACC_DIR = os.path.join(HERE, 'accuracy')
GAMES_ROOT = r'E:\E-Games\English'


def run_accuracy(full):
    """Real games whose every "Broken" (Error) finding was checked by hand. Fails when a scan gives a
    Broken finding that is not in the game's verified list (a new false alarm, or a real problem nobody
    checked yet), or when a confirmed one disappears. Prints how many Broken results are real."""
    ok = True
    total_real = total_all = 0
    if not os.path.isdir(ACC_DIR):
        return ok
    for fn in sorted(os.listdir(ACC_DIR)):
        if not fn.endswith('.json'):
            continue
        spec = json.load(open(os.path.join(ACC_DIR, fn), encoding='utf-8'))
        name = fn[:-5]
        if spec.get('optional') and not full:
            print(f'  [skip] {name}: big game, only with --full-accuracy ({spec.get("seconds", "?")}s)')
            continue
        path = os.path.join(GAMES_ROOT, spec['path'])
        if not os.path.isfile(path):
            print(f'  [skip] {name}: game file not found')
            continue
        t = time.time()
        res = scanner.scan(path)
        got = collections.Counter(key(i) for i in res.issues if i.severity == scanner.ERROR)
        real = collections.Counter(c['key'] for c in spec['confirmed'])
        false = collections.Counter(c['key'] for c in spec.get('known_false', []))
        unchecked = got - real - false
        lost = real - got
        n_real = sum((got & real).values())
        n_all = sum(got.values())
        total_real += n_real
        total_all += n_all
        pct = f'{100 * n_real / n_all:.1f}%' if n_all else '-'
        line = f'{name}: {n_real}/{n_all} Broken results real ({pct}), {time.time() - t:.0f}s'
        if unchecked or lost:
            ok = False
            print(f'  [FAIL] {line}')
            for k in sorted(unchecked.elements()):
                print(f'      not verified (check by hand, then add to tests/accuracy/{fn}): {k}')
            for k in sorted(lost.elements()):
                print(f'      confirmed real problem no longer found: {k}')
        else:
            print(f'  [ok] {line}')
    if total_all:
        pct = 100 * total_real / total_all
        print(f'  Precision over the set: {total_real}/{total_all} Broken results real = {pct:.1f}% (target 99%)')
        if pct < 99:
            ok = False
    return ok


def run_playtest():
    """Play the real games in the invisible browser; the expected runtime errors must match."""
    import playtest
    if not playtest.find_browser():
        print('  [skip] Edge/Chrome not installed')
        return True
    expect = {'1.08a': {'MC alone time with Mark', 'Secretary information', 'Gf in her bathroom'}, '1.08b': set()}
    ok = True
    for name, want in expect.items():
        if not os.path.isfile(GAMES[name]):
            continue
        res = scanner.scan_any(GAMES[name])
        t = time.time()
        issues, summ = playtest.run(res)
        got = {i.passage for i in issues if i.title.startswith('Error box')}
        other = [i.title for i in issues if not i.title.startswith('Error box')]
        if got != want or summ['renders'] < 2000:
            ok = False
            print(f'  [FAIL] {name}: scenes with errors {sorted(got)}, expected {sorted(want)}; {summ}')
        else:
            print(f'  [ok] {name}: {summ["renders"]} scene renders in {time.time() - t:.0f}s, error scenes as expected {sorted(got)}')
        for o in other:
            print(f'      also reported: {o}')
    return ok


MB_GAME = os.path.join(GAMES_ROOT, 'MB.5', 'MB.5', 'MB 0.24.6.html')     # its Harlowe 3.3.7 runs the Harlowe test game


def engine_copy(src, engine_path, version, tmp):
    """The test game in folder `src` (story.html, no engine of its own) put into a real game's engine, in tmp."""
    import re
    import shutil
    shutil.copytree(src, tmp, dirs_exist_ok=True)
    story = open(os.path.join(src, 'story.html'), encoding='utf-8').read()
    engine = open(engine_path, encoding='utf-8').read()
    ours = re.search(r'<tw-storydata.*?</tw-storydata>', story, re.S).group(0).replace(
        'format-version="1.0"', f'format-version="{version}"')
    a, b = re.search(r'<tw-storydata.*?</tw-storydata>', engine, re.S).span()
    game = os.path.join(tmp, 'game.html')
    with open(game, 'w', encoding='utf-8') as f:
        f.write(engine[:a] + ours + engine[b:])
    return game


def _fullscan_test_game(folder, engine_path, version, label):
    """Full scan a test game from tests/fixtures/<folder>: its story put into a real game's engine. Quick scan
    findings are laid next to what playing showed (fullscan.merge_quick) before checking expect_full.json."""
    import re
    import shutil
    import tempfile
    import fullscan
    src = os.path.join(FIX_DIR, folder)
    spec = json.load(open(os.path.join(src, 'expect_full.json'), encoding='utf-8'))
    tmp = tempfile.mkdtemp()
    try:
        game = engine_copy(src, engine_path, version, tmp)
        res = scanner.scan_any(game)
        t = time.time()
        state = {}
        fullscan.run(res, state=state)
        issues, summ = fullscan.merge_quick(res, list(res.issues), state)
        texts = [f'{i.title} | {i.what} | {i.fix}' for i in issues]
        missing = [m for m in spec['must'] if not any(m in t for t in texts)]
        unwanted = [m for m in spec['must_not'] for t in texts if m in t]
        share = summ['covered'] / max(1, summ['branches'])
        if missing or unwanted or share < spec['min_covered']:
            print(f'  [FAIL] {label}: {summ["covered"]}/{summ["branches"]} branches')
            for m in missing:
                print(f'      missing: {m}')
            for m in unwanted:
                print(f'      should not appear: {m}')
            for t in texts:
                print(f'        got: {t[:200]}')
            return False
        print(f'  [ok] {label}: every branch-gated bug found, {summ["covered"]}/{summ["branches"]} branches, '
              f'{time.time() - t:.0f}s')
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_fullscan():
    """Full scan of test games whose bugs only show behind branches, buttons and dice (SugarCube, Harlowe).
    The test games have no engine of their own; it is taken from real games. Then Condemned Town 1.08a in full."""
    import fullscan
    import playtest
    if not playtest.find_browser():
        print('  [skip] Edge/Chrome not installed')
        return True
    if not os.path.isfile(GAMES['1.08a']):
        print('  [skip] Condemned Town (needed for its SugarCube engine) not found')
        return True
    ok = _fullscan_test_game('fullscan_sugarcube', GAMES['1.08a'], '2.37.3', 'SugarCube test game')
    if os.path.isfile(MB_GAME):
        ok = _fullscan_test_game('fullscan_harlowe', MB_GAME, '3.3.7', 'Harlowe test game') and ok
    else:
        print('  [skip] MB 0.24.6 (needed for its Harlowe engine) not found')
    res = scanner.scan_any(GAMES['1.08a'])
    t = time.time()
    issues, summ = fullscan.run(res)
    want = {'MC alone time with Mark', 'Secretary information', 'Gf in her bathroom', 'Sleep_event Mark house',
            'Military base office', 'Sleep with Ying'}
    got = {i.passage for i in issues if i.severity == 'Error' and i.title.startswith('Error box')}
    files = {i.title for i in issues if i.severity == 'Error' and i.title.startswith('File failed')}
    share = summ['covered'] / max(1, summ['branches'])
    if not want <= got or share < 0.85 or len(files) != 2:
        ok = False
        print(f'  [FAIL] 1.08a: error scenes {sorted(got)}, missing files {sorted(files)}, {summ["covered"]}/{summ["branches"]}')
    else:
        print(f'  [ok] 1.08a: {summ["renders"]} plays in {time.time() - t:.0f}s, {summ["covered"]}/{summ["branches"]} '
              f'branches ({share:.0%}), {len(got)} scenes with error boxes, {len(files)} missing files')
    return ok


def run_fixtests(play):
    """Tested fixes: every suggested fix is tried on a hidden copy. The planted bugs in the fixtest_* games must get
    the right tested fix (and the right pick when several names work); the planted dead end must get none; the game
    file must stay unchanged. With the browser, the scene is played too (engine taken from a real game)."""
    import hashlib
    import shutil
    import tempfile
    import fixtest
    import playtest
    ok = True
    play = play and playtest.find_browser()
    for folder, engine, version in (('fixtest_sugarcube', GAMES['1.08a'], '2.37.3'), ('fixtest_harlowe', MB_GAME, '3.3.7')):
        src = os.path.join(FIX_DIR, folder)
        spec = json.load(open(os.path.join(src, 'expect_fixtest.json'), encoding='utf-8'))
        tmp = tempfile.mkdtemp()
        try:
            with_engine = play and os.path.isfile(engine)
            game = engine_copy(src, engine, version, tmp) if with_engine else os.path.join(src, 'story.html')
            before = hashlib.sha1(open(game, 'rb').read()).hexdigest()
            res = scanner.scan_any(game)
            t = time.time()
            res.fix_tests = fixtest.Tester(res, game, play=with_engine).run()
            fixtest.apply(res)
            bad = []

            def find(k):
                title, _, text = k.partition(' | ')
                return [i for i in res.issues if title.lower() in i.title.lower() and text in (i.snippet or '')]
            for k, pick in spec['works'].items():
                got = find(k)
                v = getattr(got[0], 'tested', None) if got else None
                if not v or v['state'] != 'works' or v['picked'] != pick:
                    bad.append(f'{k}: expected a tested fix "{pick}", got {v and (v["state"], v.get("picked"), v["how"])}')
                elif with_engine and spec.get('played') and got[0].passage == spec['played'] and 'played the scene' not in v['how']:
                    bad.append(f'{k}: the scene was not played: {v["how"]}')
            for k in spec['failed'] + (spec.get('failed_playing', []) if with_engine else []):
                got = find(k)
                v = getattr(got[0], 'tested', None) if got else None
                if not v or v['state'] != 'failed' or got[0].new or got[0].old:
                    bad.append(f'{k}: expected "no working fix", got {v and v["state"]}')
            several = [i for i in res.issues if getattr(i, 'tested', None) and i.tested.get('others')]
            if not any('was picked because' in i.tested['how'] for i in several):
                bad.append('no finding says which of several working names was picked and why')
            if hashlib.sha1(open(game, 'rb').read()).hexdigest() != before:
                bad.append('THE GAME FILE WAS CHANGED')
            label = f'{folder} ({"scan again + played" if with_engine else "scan again only"})'
            if bad:
                ok = False
                print(f'  [FAIL] {label}')
                for b in bad:
                    print(f'      {b}')
            else:
                print(f'  [ok] {label}: {len(res.fix_tests)} fixes tested, right ones offered, dead end refused, '
                      f'game unchanged, {time.time() - t:.0f}s')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    # other formats: the scan-again part, and nothing crashes; a tested fix that works must really remove the problem
    for folder in ('chapbook_bugs', 'snowman_bugs', 'renpy_bugs', 'rpgmaker_bugs', 'ink_bugs', 'choicescript_bugs', 'sugarcube_bugs'):
        d = os.path.join(FIX_DIR, folder)
        open_ = json.load(open(os.path.join(d, 'expect.json'), encoding='utf-8'))['open']
        target = os.path.join(d, open_) if open_ else d
        res = scanner.scan_any(target)
        n = fixtest.count_testable(res)
        out = fixtest.Tester(res, target, play=False).run()
        works = sum(1 for v in out.values() if v['state'] == 'works')
        if n and not out:
            ok = False
            print(f'  [FAIL] {folder}: {n} suggested fixes, none tested')
        else:
            print(f'  [ok] {folder}: {len(out)} of {n} suggested fixes tested, {works} work')
    return ok


def run_settle(play):
    """Settled results: the planted "worth checking" results in the settle_* games must end as Broken, Fine or open
    (with a reason), each with its proof; nothing may be dropped and the game file must stay unchanged. Played with a
    real game's engine; without the browser only the results that need no playing are checked."""
    import hashlib
    import shutil
    import tempfile
    import playtest
    import settle
    ok = True
    play = play and playtest.find_browser()
    for folder, engine, version in (('settle_sugarcube', GAMES['1.08a'], '2.37.3'),
                                   ('settle_widgets', GAMES['1.08a'], '2.37.3'), ('settle_harlowe', MB_GAME, '3.3.7')):
        src = os.path.join(FIX_DIR, folder)
        spec = json.load(open(os.path.join(src, 'expect_settle.json'), encoding='utf-8'))
        if not (play and os.path.isfile(engine)):
            print(f'  [skip] {folder}: needs Edge/Chrome and the engine of a real game')
            continue
        tmp = tempfile.mkdtemp()
        try:
            game = engine_copy(src, engine, version, tmp)
            before = hashlib.sha1(open(game, 'rb').read()).hexdigest()
            res = scanner.scan_any(game)
            n_before = len(res.issues)
            warned = [i for i in res.issues if i.severity == scanner.WARNING]
            t = time.time()
            res.settled = settle.Settler(res).run()
            settle.apply(res)
            bad = []

            def find(k):
                title, _, text = k.partition(' | ')
                return [i for i in res.issues if title.lower() in i.title.lower() and text in (i.snippet or '')]
            for want, keys in ((settle.BROKEN, spec['broken']), (settle.FINE, spec['fine']), (settle.OPEN, spec['open'])):
                sev = settle.SEVERITY[want]
                for k in keys:
                    got = find(k)
                    v = getattr(got[0], 'settle', None) if got else None
                    if not got:
                        bad.append(f'{k}: no such finding')
                    elif not v or v['state'] != want or got[0].severity != sev or settle.NOTE not in got[0].what:
                        bad.append(f'{k}: expected {want}, got {v and v["state"]}: {v and v["proof"]}')
            if len(res.issues) != n_before or any(not getattr(i, 'settle', None) for i in warned):
                bad.append('a "worth checking" result was dropped or left without a note')
            if hashlib.sha1(open(game, 'rb').read()).hexdigest() != before:
                bad.append('THE GAME FILE WAS CHANGED')
            if bad:
                ok = False
                print(f'  [FAIL] {folder}')
                for b in bad:
                    print(f'      {b}')
            else:
                c = collections.Counter(i.settle['state'] for i in warned)
                print(f'  [ok] {folder}: {len(warned)} worth checking -> {c[settle.BROKEN]} broken, {c[settle.FINE]} fine, '
                      f'{c[settle.OPEN]} open with a reason, game unchanged, {time.time() - t:.0f}s')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    # other formats (no playing): every "worth checking" result gets a verdict or a plain reason, nothing crashes
    for folder in ('sugarcube_bugs', 'harlowe_bugs', 'chapbook_bugs', 'snowman_bugs', 'plain_html', 'renpy_bugs',
                   'rpgmaker_bugs', 'ink_bugs', 'choicescript_bugs'):
        d = os.path.join(FIX_DIR, folder)
        if not os.path.isfile(os.path.join(d, 'expect.json')):
            continue
        open_ = json.load(open(os.path.join(d, 'expect.json'), encoding='utf-8'))['open']
        res = scanner.scan_any(os.path.join(d, open_) if open_ else d)
        warned = [i for i in res.issues if i.severity == scanner.WARNING and not i.unreachable]
        res.settled = settle.Settler(res, play=False).run()
        settle.apply(res)
        bad = [i.title for i in warned if not getattr(i, 'settle', None) or not i.settle['proof']]
        if bad:
            ok = False
            print(f'  [FAIL] {folder}: no verdict for {bad[:3]}')
        else:
            c = collections.Counter(i.settle['state'] for i in warned)
            print(f'  [ok] {folder} (no playing): {len(warned)} worth checking -> {c[settle.BROKEN]} broken, '
                  f'{c[settle.FINE]} fine, {c[settle.OPEN]} open with a reason')
    return ok


if __name__ == '__main__':
    import re
    update = '--update' in sys.argv
    print('Snapshots of real games:')
    a = run_snapshots(update)
    print('Test games with planted bugs:')
    b = run_fixtures()
    print('Applying every suggested fix:')
    c = run_fix_application()
    print('Tested fixes (each suggestion tried on a hidden copy):')
    c = run_fixtests('--no-playtest' not in sys.argv) and c
    print('Settled results ("worth checking" results proven broken or fine):')
    c = run_settle('--no-playtest' not in sys.argv) and c
    e = True
    if '--no-accuracy' not in sys.argv:
        print('Real games: are the "Broken" results really broken?')
        e = run_accuracy('--full-accuracy' in sys.argv)
    if '--no-missed' not in sys.argv:
        print('Missed bugs: planted bugs the scan must find:')
        import missed_bugs
        e = missed_bugs.run(play='--no-playtest' not in sys.argv, misses=True) and e
    d = True
    if '--no-playtest' not in sys.argv:
        print('Play-testing in the invisible browser:')
        d = run_playtest()
        print('Full scan (every branch played):')
        d = run_fullscan() and d
    print('\nRESULT:', 'PASS' if a and b and c and d and e else 'FAIL (see above)')
    sys.exit(0 if a and b and c and d and e else 1)
