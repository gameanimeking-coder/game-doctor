"""Settled results: Game Doctor decides "Worth checking" (Warning) results itself instead of leaving them to the user.

For every Warning the app gathers proof and moves it:
  - to Broken (Error), with the proof (e.g. "seen while playing: a '>' shows on screen", "the game has no such macro")
  - to Fine (a note), with the proof (e.g. "the file loaded fine while playing", "both definitions are the same")
  - or it stays Worth checking, with a one-line plain reason why it cannot be proven (e.g. "only breaks on Android").
Nothing is dropped and the game file is never changed.

Evidence, cheapest first:
  - the scan itself (both widget definitions the same, which links lead into an empty scene, variable counts ...)
  - the game started once in the invisible browser ("facts": which macros exist, values at the start, styles, whether
    the browser can show a picture)
  - the scenes with such results played in the invisible browser (Full scan machinery, small budget), looking at what
    shows on screen ("probes": text players see, boxes with the same id, page anchors) and which files are asked for.
This runs after the list is shown; results fill in. A finished Full scan adds its file evidence.
"""
import collections
import json
import os
import re
import time
import urllib.parse

import scanner
from scanner import ERROR, WARNING, INFO

BROKEN, FINE, OPEN = 'broken', 'fine', 'open'
PLAY_BUDGET = 40          # ways to play each scene (enough to open most parts of a small scene)
NOTE = 'Checked by Game Doctor:'
PLAIN = r"[A-Za-z0-9 ,.!?]"   # text that shows on screen exactly as written (no markup characters)
DYNAMIC_VARS = re.compile(r'State\.variables\s*\[|variables\(\)\s*\[|\bV\s*\[|State\.setVar\(\s*[^"\'\s]|State\.temporary\s*\[|\bT\s*\[')
INCLUDE_RE = re.compile(r'<<(?:include|display)\b|\(display:|\{embed passage', re.I)


def _verdict(state, text):
    return dict(state=state, proof=text)


def broken(text):
    return _verdict(BROKEN, text)


def fine(text):
    return _verdict(FINE, text)


def unsure(reason):
    return _verdict(OPEN, reason)


# ---------------------------------------------------------------------- one Warning and what settles it
class Case:
    """One Warning. A rule either settles it at once (self.verdict) or asks for evidence (probes, facts, a played
    scene) and gives a judge(ev) that decides from it."""

    def __init__(self, issue, ctx):
        self.issue, self.ctx = issue, ctx
        self.verdict = None
        self.judge = None
        self.scene = ''            # scene to play (probes are looked for there)
        self.scenes = []           # more scenes to play
        self.probes = []           # [{'id':.., 'text'|'count'|'anchor': ..}]
        self.n = 0

    def probe(self, kind, value):
        self.n += 1
        pid = f'{id(self) % 10 ** 8}_{self.n}'
        self.probes.append({'id': pid, kind: re.sub(r'\s+', '', value) if kind == 'text' else value})   # screen text: spaces left out
        return pid

    def play(self, judge, scene=None):
        self.scene = scene or self.issue.passage
        self.judge = judge


class Context:
    """What the rules can look at without the browser."""

    def __init__(self, res):
        self.res = res
        sc = res.context
        self.sc = sc
        self.passages = {p.name: p for p in getattr(sc, 'passages', [])}
        self.fmt = res.stats.get('format', '').lower()
        self.sugarcube = self.fmt.startswith('sugarcube')
        self.harlowe = self.fmt.startswith(('harlowe', 'x-lowe'))
        self.js = getattr(sc, 'js_text', '') or ''
        self.css = getattr(sc, 'css_text', '') or ''
        self.raw = getattr(sc, 'raw', '') or ''
        self.counts = getattr(sc, 'var_counts', None)
        self.dynamic_vars = bool(DYNAMIC_VARS.search(self.js))
        self.texts = {n: p.text for n, p in self.passages.items()}
        self.playable = False      # set by Settler

    def line_text(self, i):
        """(passage text, offset where the issue's line starts, the line) or None."""
        p = self.passages.get(i.passage)
        if not p or not i.line:
            return None
        rel = i.line - p.line
        lines = p.text.split('\n')
        if not 0 <= rel < len(lines):
            return None
        return p.text, sum(len(x) + 1 for x in lines[:rel]), lines[rel]

    def engine_name(self):
        return {'renpy': "Ren'Py", 'rpgmaker': 'RPG Maker', 'ink': 'Ink', 'choicescript': 'ChoiceScript'}.get(
            self.res.engine, self.res.stats.get('format') or 'this kind of')


def _plain_after(text, at):
    m = re.match(r'\s*(' + PLAIN + r'{3,24})', text[at:])
    return m.group(1).strip() if m and re.search(r'\w{2}', m.group(1)) else ''


def _plain_before(text, at):
    m = re.search(r'(' + PLAIN + r'{3,24})\s*$', text[:at])
    return m.group(1).strip() if m and re.search(r'\w{2}', m.group(1)) else ''


RULES = []


def rule(pattern):
    def deco(fn):
        RULES.append((re.compile(pattern), fn))
        return fn
    return deco


def _cannot_play(c):
    """The reason when this needs the game played but Game Doctor can't play it."""
    ctx = c.ctx
    if ctx.res.engine not in ('twine', 'html'):
        return f'Game Doctor cannot play {ctx.engine_name()} games, so it cannot see what players see here'
    if not (ctx.sugarcube or ctx.harlowe):
        return (f'Game Doctor can only play SugarCube and Harlowe games scene by scene, not '
                f'{ctx.res.stats.get("format") or "this kind of"} games')
    return ''


def _ran(ev, c):
    """Did the part of the scene that holds this line show while playing? True, False, or None (not played)."""
    import fullscan
    scene = ev.scene(c.scene)
    if not scene or not scene.get('renders'):
        return None
    lt = c.ctx.line_text(c.issue)
    if lt is None:
        return bool(scene['covered']) or not scene['branches']
    text, _, _ = lt
    start = c.ctx.res.passages.get(c.scene, (0, []))[0]
    b = fullscan._branch_at(scene['branches'], c.issue.line - start, text)
    return b is None or str(b['id']) in scene['covered']


def _values(ev, c, pid):
    import fullscan
    e = ev.seen(c.scene, pid)
    job = e[1] if e and e[1] else None
    return fullscan.show_values(fullscan._job(job)) if job else 'the starting values'


def _not_shown(ev, c, what):
    r = _ran(ev, c)
    if r is None:
        return unsure(ev.why_not_played(c))
    return unsure(f'this part of "{c.scene}" never showed with any values Game Doctor tried, so it could not see {what}')


# ---------------------------------------------------------------------- text that should not show on screen
def _text_probe(c, sure, maybe, label):
    """Settle by looking at the screen: `sure` = text that only shows when the problem is real, `maybe` = text near
    it that shows whenever this part of the scene shows."""
    c.p_sure, c.p_near = c.probe('text', sure), c.probe('text', maybe)

    def judge(ev):
        got, near = ev.seen(c.scene, c.p_sure), ev.seen(c.scene, c.p_near)
        if got and got[0]:
            return broken(f'seen while playing: when the game shows "{c.scene}" with {_values(ev, c, c.p_sure)}, '
                          f'players see {label} on screen.')
        if near and near[0]:
            return fine(f'seen while playing: "{c.scene}" was shown and the text around it appears, but {label} '
                        'does not. The game reads it the right way.')
        return _not_shown(ev, c, 'the screen there')
    c.play(judge)


@rule(r'^Extra ">" after a macro tag$|^Stray ">" after an HTML tag$')
def _extra_gt(c):
    lt = c.ctx.line_text(c.issue)
    if not lt:
        return unsure('Game Doctor could not find the line again')
    text, base, line = lt
    rx = r'(<<[A-Za-z/][^<>\n]*?>>)(>+)' if 'macro' in c.issue.title else r'(?<!<)(<[A-Za-z][\w-]*(?:\s[^<>]*)?>)(>+)(?!>)'
    m = re.search(rx, line)
    if not m:
        return unsure('Game Doctor could not find the spot on the line again')
    # (a macro inside an HTML tag, <span <<if $x>>class="a"<</if>>>, is no exception: SugarCube shows it as text)
    if _cannot_play(c):
        return unsure(_cannot_play(c))
    after = _plain_after(text, base + m.end())
    if not after:
        return unsure('no plain text follows it, so Game Doctor cannot tell on screen whether the ">" shows')
    _text_probe(c, m.group(2) + after, after, f'a stray ">" before "{after}"')


@rule(r'^Extra "<" before a macro tag$')
def _extra_lt(c):
    lt = c.ctx.line_text(c.issue)
    if not lt:
        return unsure('Game Doctor could not find the line again')
    text, base, line = lt
    m = re.search(r'<<<+(?=[A-Za-z/])', line)
    if not m:
        return unsure('Game Doctor could not find the spot on the line again')
    if _cannot_play(c):
        return unsure(_cannot_play(c))
    before = _plain_before(text, base + m.start())
    if not before:
        return unsure('no plain text comes before it, so Game Doctor cannot tell on screen whether the "<" shows')
    _text_probe(c, before + '<' * (len(m.group()) - 2), before, f'a stray "<" after "{before}"')


@rule(r'^"(.+)" text shown to players$|^Placeholder text shown to players$')
def _placeholder(c):
    if _cannot_play(c):
        return unsure(_cannot_play(c))
    lt = c.ctx.line_text(c.issue)
    m = re.search(r'^"(.+)" text', c.issue.title)
    if not lt or not m or c.issue.passage not in c.ctx.res.passages:
        return unsure('Game Doctor could not find the spot again')
    text, base, line = lt
    at = line.find(m.group(1))
    if at < 0:
        return unsure('Game Doctor could not find the spot on the line again')
    word = re.sub(r'\s+', '', m.group(1))
    after = _plain_after(text, base + at + len(m.group(1)))
    sure = word + after[:12]
    c.p_sure = c.probe('text', sure)

    def judge(ev):
        got = ev.seen(c.scene, c.p_sure)
        if got and got[0]:
            return broken(f'seen while playing: when the game shows "{c.scene}" with {_values(ev, c, c.p_sure)}, '
                          f'"{m.group(1)}" is on the screen.')
        r = _ran(ev, c)
        if r:
            return fine(f'seen while playing: this part of "{c.scene}" was shown, but "{m.group(1)}" did not appear '
                        'on the screen (it is hidden or only used in code).')
        return _not_shown(ev, c, 'whether the text shows')
    c.play(judge)


# ---------------------------------------------------------------------- macros
def _macro_name(title):
    m = re.search(r'<<([\w-]+)>>|\(([\w-]+):\)|\{([\w ]+):', title)
    return (m.group(1) or m.group(2) or m.group(3)) if m else ''


@rule(r'^Unknown macro <<[\w-]+>>$|^<<[\w-]+>> used \d+ more times$|^Unknown macro \([\w-]+:\)$')
def _unknown_macro(c):
    name = _macro_name(c.issue.title)
    ctx = c.ctx
    if not (ctx.sugarcube or ctx.harlowe):
        return unsure(_cannot_play(c) or 'Game Doctor cannot check this kind of game\'s commands')
    c.fact = ('macros', name)
    shown = f'<<{name}>>' if ctx.sugarcube else f'({name}:)'

    def judge(ev):
        has = ev.fact('macros', name)
        if has is True:
            return fine(f'Game Doctor started the game in the invisible browser: it makes {shown} itself while it '
                        'starts (in a script the scan could not read), so this works.')
        if has is False:
            return broken(f'Game Doctor started the game in the invisible browser: there is no {shown} in it. When this '
                          'part is shown, players see a red error box instead.')
        return unsure(ev.why_no_facts())
    c.judge = judge


@rule(r'^\(([\w-]+):\) has no hook \[ \] after it$')
def _no_hook(c):
    if _cannot_play(c):
        return unsure(_cannot_play(c))
    name = _macro_name(c.issue.title)

    def judge(ev):
        scene = ev.scene(c.scene)
        boxes = [m for m in (scene or {}).get('errors', {}) if name in m or 'hook' in m.lower() or 'changer' in m.lower()]
        if boxes:
            return broken(f'seen while playing: when the game shows "{c.scene}", players see a red error box '
                          f'saying: {boxes[0][:160]}')
        return _not_shown(ev, c, 'what happens there') if not _ran(ev, c) else unsure(
            f'"{c.scene}" was played and showed no error, but the text after ({name}:) may not be controlled by it; '
            'only the writer knows which text was meant')
    c.play(judge)


# ---------------------------------------------------------------------- widgets, scenes
@rule(r'^Widget "(.+)" is defined twice$')
def _widget_twice(c):
    name = re.match(r'^Widget "(.+)" is defined', c.issue.title).group(1)
    rx = re.compile(r'<<widget\s+(?:["\']' + re.escape(name) + r'["\']|' + re.escape(name) + r'\b)[^>]*>>(.*?)<</widget>>', re.S)
    bodies = [(n, ' '.join(m.group(1).split())) for n, t in c.ctx.texts.items() for m in rx.finditer(t)]
    if len(bodies) < 2:
        return unsure('Game Doctor could not find both definitions')

    def static():
        if len({b for _, b in bodies}) == 1:
            return fine(f'both definitions of <<{name}>> (in {", ".join(sorted({n for n, _ in bodies}))}) are exactly '
                        'the same, so it does not matter which one the game uses.')
        return unsure('the definitions are different and the game uses only the one it loads last; only the writer '
                      'knows which version is right')
    if not c.ctx.sugarcube:
        return static()
    c.fact = ('alerts', name)

    def judge(ev):
        alerts = ev.fact_raw().get('alerts') or []
        said = [a for a in alerts if f'"{name}"' in a]
        if said:
            return broken(f'Game Doctor started the game in the invisible browser: it stops at once with an error '
                          f'pop-up saying: {" ".join(said[0].split())[:200]}')
        said = [a for a in alerts if f'[{c.issue.passage}]' in a and 'widget' in a]
        if said:     # SugarCube stops reading the widget passage at its first error, so this one is lost too
            return broken(f'Game Doctor started the game in the invisible browser: it shows an error pop-up for the '
                          f'passage "{c.issue.passage}" and stops reading it there, so this definition is never made. '
                          f'The pop-up says: {" ".join(said[0].split())[:200]}')
        return static()
    c.judge = judge


@rule(r'^Passage "(.+)" is empty$')
def _empty_passage(c):
    name = c.issue.passage
    ways = []
    for f, t, off, how in c.ctx.res.link_edges:
        if t == name:
            at = (c.ctx.res.passage_texts.get(f) or '')[off:off + 20]
            ways.append((f, 'include' if INCLUDE_RE.match(at) or how in ('<<include>>', '<<display>>') else 'go'))
    go = sorted({f for f, k in ways if k == 'go'})
    if go:
        return broken(f'players who follow the link in "{go[0]}" land on an empty page'
                      + (f' (also from {len(go) - 1} other scene(s))' if len(go) > 1 else '') + '.')
    if ways:
        return fine('it is only put inside other scenes (include/display), where an empty part simply shows nothing.')
    return unsure('the game only reaches it in a way the scan cannot follow')


# ---------------------------------------------------------------------- conditions and code
@rule(r'^"is not" instead of "isnot"$')
def _is_not(c):
    lt = c.ctx.line_text(c.issue)
    snip = lt[2] if lt else c.issue.snippet
    m = re.search(r'(\$[\w.]+)\s+is\s+not\s+("[^"]*"|\'[^\']*\'|-?\d[\d.]*|true|false|\$[\w.]+)', snip)
    if not m:
        return unsure('Game Doctor could not read the condition')
    lhs, rhs = m.group(1), m.group(2)
    if rhs[0] in '"\'' or rhs[0].isdigit() or rhs[0] == '-':
        return broken(f'SugarCube reads "{lhs} is not {rhs}" as "{lhs} is false", so this check never works the way it '
                      'reads when the value is a number or text.')
    c.fact = ('vars', lhs[1:].split('.')[0])

    def judge(ev):
        t = ev.fact('vars', lhs[1:].split('.')[0])
        if rhs in ('true', 'false') and t == 'boolean':
            return fine(f'SugarCube reads it as "{lhs} is {"false" if rhs == "true" else "true"}". Game Doctor started '
                        f'the game: {lhs} is a yes/no value, so that gives the same answer.')
        if t in ('number', 'string'):
            return broken(f'SugarCube reads it as "{lhs} is (not {rhs})", and Game Doctor started the game: {lhs} holds '
                          f'a {"number" if t == "number" else "text"}, so the check never works the way it reads.')
        return unsure(ev.why_no_facts() if t is None else f'{lhs} has no value when the game starts, so Game Doctor '
                      'cannot tell what kind of value it will hold')
    c.judge = judge


@rule(r'^"(.+) is (.+) or (.+)" does not compare both$')
def _or_compare(c):
    m = re.match(r'^"(.+) is (.+) or (.+)" does not', c.issue.title)
    a, b, x = m.groups()
    ctx = c.ctx
    if ctx.harlowe:
        ver = re.search(r'(\d+)', ctx.fmt)
        if ctx.fmt.startswith('x-lowe') or (ver and int(ver.group(1)) >= 3):
            return fine(f'Harlowe 3 reads "{a} is {b} or {x}" as "{a} is {b} or {a} is {x}" (it fills in the missing '
                        'part itself), so this works.')
        return unsure('this older Harlowe version may not fill in the missing part; Game Doctor cannot play it to check')
    falsy = x in ('0', '""', "''", 'false')
    return broken(f'SugarCube reads it as "({a} is {b}) or {x}". "{x}" on its own is '
                  + ('never true, so the "or" part never counts.' if falsy else 'always true, so the whole check is '
                     'always true, whatever ' + a + ' is.'))


@rule(r'^Condition mixes "and" and "or" without brackets$')
def _and_or(c):
    return unsure('both ways of reading it are valid code; only the writer knows which grouping was meant')


@rule(r'^<<set>> does not change anything$')
def _set_nothing(c):
    lt = c.ctx.line_text(c.issue)
    snip = lt[2] if lt else c.issue.snippet
    m = re.search(r'<<set\s+\$([A-Za-z_]\w*)\s*>>', snip)
    if not m or not c.ctx.counts:
        return unsure('Game Doctor could not tell which value this was meant to change')
    v = m.group(1)
    reads, sets = c.ctx.counts[0], c.ctx.counts[1]
    if c.ctx.dynamic_vars:
        return unsure('the game also changes values from its own code in ways the scan cannot follow')
    if sets[v] == 0 and reads[v] > 1:
        return broken(f'this is the only place that should give ${v} a value, and it does not, so ${v} stays empty '
                      'everywhere the game checks or shows it.')
    if reads[v] <= 1:
        return fine(f'nothing in the game reads ${v}, so this line doing nothing changes nothing for players.')
    return unsure(f'${v} gets values elsewhere, so whether this line was meant to change it only the writer knows')


# ---------------------------------------------------------------------- variables
def _var_rule(c, sigil):
    m = re.search(r'([$_]|s\.)?\b(?:Temporary variable _)?([A-Za-z_]\w*) is used but never set', c.issue.title)
    v = m.group(2)
    near = re.search(r'misspelling of \$?(\S+?)\.|Did you mean _?(\S+?)\?', c.issue.what)
    near = (near.group(1) or near.group(2)) if near else ''
    ctx = c.ctx
    if ctx.dynamic_vars:
        return unsure('the game makes value names in its own code while playing, which the scan cannot follow')
    if sigil == '_' or not (ctx.sugarcube or ctx.harlowe):
        if sigil == '_' and ctx.sugarcube:
            return broken(f'nothing anywhere gives _{v} a value (a temporary value lives only in its scene), so it is '
                          'always empty here' + (f'; the scene does set _{near}.' if near else '.'))
        return unsure(_cannot_play(c) or 'Game Doctor cannot start this kind of game to check')
    c.fact = ('vars', v)

    def judge(ev):
        t = ev.fact('vars', v)
        if t is None:
            return unsure(ev.why_no_facts())
        if t != 'undefined':
            return fine(f'Game Doctor started the game in the invisible browser: ${v} does get a value when the game '
                        'starts (from a script the scan could not read).')
        return broken(f'Game Doctor started the game in the invisible browser: ${v} has no value, and nothing in the '
                      f'game gives it one, so it is always empty' + (f'. ${near} is the one the game sets.' if near else '.'))
    c.judge = judge


@rule(r'^\$\w+ is used but never set$|^s\.\w+ is used but never set$|^\w+ is used but never set$')
def _never_set(c):
    return _var_rule(c, '$')


@rule(r'^Temporary variable _\w+ is used but never set$')
def _temp_never_set(c):
    return _var_rule(c, '_')


@rule(r'^\$(\w+) is set but never checked or shown$')
def _never_read(c):
    v = re.match(r'^\$(\w+)', c.issue.title).group(1)
    near = re.search(r'does read \$(\w+)', c.issue.what)
    if c.ctx.dynamic_vars or re.search(r'["\']' + re.escape(v) + r'["\']', c.ctx.js):
        return unsure('the game reads values from its own code in ways the scan cannot follow')
    return broken(f'nothing in the game reads ${v}, so every value given to it is lost'
                  + (f'; the game reads ${near.group(1)} instead.' if near else '.'))


@rule(r'^\$(\w+) and \$(\w+) differ only in capital letters$')
def _caps_vars(c):
    g, main = re.match(r'^\$(\w+) and \$(\w+)', c.issue.title).groups()
    if not c.ctx.counts:
        return unsure('Game Doctor could not count where they are used')
    if c.ctx.dynamic_vars:
        return unsure('the game also uses values from its own code in ways the scan cannot follow')
    reads, sets = c.ctx.counts[0], c.ctx.counts[1]

    def only(x):
        if sets[x] == 0:
            return 'read', 'never given a value'
        if reads[x] <= sets[x]:
            return 'set', 'never read'
        return '', ''
    for x, y in ((g, main), (main, g)):
        kind, words = only(x)
        if kind:
            return broken(f'${x} is {words}, while ${y} is used, so the two never meet: one of them is a misspelling.')
    return unsure('both are given values and both are read, so they may really be two different values')


@rule(r'^"\$(\w+)-(\w+)" is read as "\$\w+" plus text$')
def _hyphen_var(c):
    a, b = re.match(r'^"\$(\w+)-(\w+)"', c.issue.title).groups()
    known = {k.lower(): k for k in c.ctx.res.known_vars}
    for cand in (f'{a}_{b}', f'{a}{b}', f'{a}{b[:1].upper()}{b[1:]}'):
        if cand.lower() in known:
            return broken(f'the game has a value called ${known[cand.lower()]}, but here it shows ${a} followed by '
                          f'the text "-{b}".')
    if c.ctx.counts and c.ctx.counts[1][a] > 0:
        return fine(f'the game gives ${a} a value, and "-{b}" is just text after it (like "Kim-{b}").')
    return unsure(f'the game never gives ${a} a value and has no value like ${a}_{b}, so Game Doctor cannot tell what '
                  'was meant')


# ---------------------------------------------------------------------- HTML and CSS
def _code_refs(ctx, ident):
    """Where the game looks up this id in code (not counting styles)."""
    body = '\n'.join(t for n, t in ctx.texts.items() if 'stylesheet' not in (ctx.res.passages.get(n, (0, []))[1] or []))
    rx = r'#' + re.escape(ident) + r'(?![\w-])|getElementById\(\s*["\']' + re.escape(ident) + r'["\']'
    return bool(re.search(rx, body) or re.search(rx, ctx.js))


@rule(r'^id="(.+)" used (\d+) times in one passage$')
def _dup_id(c):
    ident = re.match(r'^id="(.+)" used', c.issue.title).group(1)
    if not _code_refs(c.ctx, ident):
        css = re.search(r'#' + re.escape(ident) + r'(?![\w-])', c.ctx.css)
        return fine('nothing in the game looks this id up in code' + (' (only the styles use it, and styles work on '
                    'every copy)' if css else '') + ', so having it twice changes nothing players see.')
    if _cannot_play(c):
        return unsure(_cannot_play(c))
    c.p_count = c.probe('count', ident)

    def judge(ev):
        got = ev.seen(c.scene, c.p_count)
        if got and got[0] >= 2:
            return broken(f'seen while playing: when the game shows "{c.scene}" with {_values(ev, c, c.p_count)}, the '
                          f'page has {got[0]} boxes with id="{ident}" at once, and the game\'s code changes only the first.')
        scene = ev.scene(c.scene)
        if scene and scene.get('renders') and len(scene['covered']) >= len(scene['branches']):
            return fine(f'seen while playing: every part of "{c.scene}" was shown and there was never more than one box '
                        f'with id="{ident}" at a time (they are in different "if" parts).')
        return unsure(ev.why_not_played(c) if not scene else f'not every part of "{c.scene}" showed while playing, so '
                      'two boxes with this id might still appear together')
    c.play(judge)


@rule(r'^Link to missing page anchor "#(.+)"$')
def _anchor(c):
    ident = re.match(r'^Link to missing page anchor "#(.+)"', c.issue.title).group(1)
    if _cannot_play(c):
        return unsure(_cannot_play(c))
    c.p_anchor = c.probe('anchor', ident)

    def judge(ev):
        got = ev.seen(c.scene, c.p_anchor)
        if got and got[0]:
            return fine(f'seen while playing: the game makes a spot called "{ident}" when "{c.scene}" is shown, so the '
                        'link has somewhere to jump to.')
        if _ran(ev, c):
            return broken(f'seen while playing: "{c.scene}" was shown and the page has no spot called "{ident}", so '
                          'clicking the link does nothing.')
        return _not_shown(ev, c, 'the page there')
    c.play(judge)


@rule(r'^Empty (src|href)="" in <(\w+)>$')
def _empty_attr(c):
    attr, tag = re.match(r'^Empty (src|href)="" in <(\w+)>', c.issue.title).groups()
    lt = c.ctx.line_text(c.issue)
    line = lt[2] if lt else c.issue.snippet
    m = re.search(r'<' + tag + r'\b[^<>]*' + attr + r'\s*=\s*(["\'])\1[^<>]*>', line, re.I)
    whole = m.group() if m else ''
    if re.search(r'\b(?:data-passage|data-setter|onclick|@' + attr + r')\s*=', whole, re.I):
        return fine('the tag also has code that decides what happens (data-passage, onclick or @' + attr + '), so the '
                    'empty value is never used.')
    ident = re.search(r'\bid\s*=\s*["\']([^"\']+)["\']', whole)
    if ident and _code_refs(c.ctx, ident.group(1)):
        return fine(f'the game\'s code fills it in later (it looks up id="{ident.group(1)}").')
    if attr == 'href':
        return broken('clicking this link loads the page again, which throws players out of the scene.')
    return broken(f'the <{tag}> has no file name and nothing fills one in, so it never shows anything.')


@rule(r'^<(\w+)> (opened|closed) more times than')
def _tag_balance(c):
    tag, more = re.match(r'^<(\w+)> (opened|closed)', c.issue.title).groups()
    if _cannot_play(c):
        return unsure(_cannot_play(c))

    def judge(ev):
        scene = ev.scene(c.scene)
        boxes = [m for m in (scene or {}).get('errors', {}) if re.search(r'\b' + tag + r'\b', m, re.I)]
        if boxes:
            return broken(f'seen while playing: when the game shows "{c.scene}", players see a red error box saying: '
                          f'{boxes[0][:160]}')
        if not _ran(ev, c):
            return _not_shown(ev, c, 'what happens there')
        if more == 'closed':
            return fine(f'seen while playing: "{c.scene}" was shown without an error. An extra </{tag}> cannot close '
                        'boxes outside the scene; the browser ignores it.')
        return unsure(f'"{c.scene}" showed without an error; the text after the open <{tag}> ends up inside it, and '
                      'whether that looks wrong can only be judged by eye')
    c.play(judge)


@rule(r'^Class "(.+)" does not match any style')
def _css_class(c):
    cls = re.match(r'^Class "(.+)" does not', c.issue.title).group(1)
    near = re.search(r'did you mean "(.+)"\?', c.issue.title)
    if c.ctx.res.engine not in ('twine', 'html'):
        return unsure(_cannot_play(c))
    c.fact = ('classes', cls)

    def judge(ev):
        has = ev.fact('classes', cls)
        if has is True:
            return fine(f'Game Doctor started the game in the invisible browser: a style for ".{cls}" is loaded (from '
                        'a file the scan did not read), so the element is styled.')
        if has is False and not ev.fact_raw().get('sheetsHidden'):
            return broken(f'Game Doctor started the game in the invisible browser and read every loaded style: there is '
                          f'none for ".{cls}", so the element looks plain' + (f' (".{near.group(1)}" exists).' if near else '.'))
        return unsure(ev.why_no_facts() if has is None else 'some of the game\'s styles come from another website and '
                      'cannot be read')
    c.judge = judge


# ---------------------------------------------------------------------- files
def _wanted_file(title):
    m = re.search(r'not found: (.+?)(?: \(a \w+ file is there instead\))?$', title) or re.search(r'capital letters: (.+)$', title)
    return m.group(1).strip() if m else ''


def _same_file(url_path, wanted):
    a = urllib.parse.unquote(url_path).replace('\\', '/').lower().lstrip('/')
    b = wanted.replace('\\', '/').lower().lstrip('./')
    return a == b or a.endswith('/' + b)


def _line_ran(ev, ctx, scene, rel):
    """Did line `rel` (counted from the scene's first line) of a played scene show? None when not played."""
    import fullscan
    r = ev.scene(scene)
    if not r or not r.get('renders'):
        return None
    b = fullscan._branch_at(r['branches'], rel, ctx.texts.get(scene, ''))
    return b is None or str(b['id']) in r['covered']


def _users(c, wanted):
    """Scenes where the game may load this file: the scene itself, and the scenes that use the value it is put in
    (<<set $face to "smile.png">> in StoryInit, <img @src="'images/' + $face"> in another scene)."""
    import fullscan
    ctx = c.ctx
    lt = ctx.line_text(c.issue)
    scenes = set(fullscan.scene_names(ctx.res))
    out = {}
    if c.issue.passage in scenes and lt:
        out[c.issue.passage] = [c.issue.line - ctx.res.passages[c.issue.passage][0]]
    base = wanted.replace('\\', '/').rsplit('/', 1)[-1]
    for m in re.finditer(r'[$_]([A-Za-z_]\w*)\s*(?:to\b|=)\s*["\'][^"\'\n]*' + re.escape(base), lt[2] if lt else ''):
        rx = re.compile(r'(?<![\w$])\$' + re.escape(m.group(1)) + r'(?!\w)')
        for name in sorted(scenes):
            text = ctx.texts.get(name, '')
            rows = [n for n, row in enumerate(text.split('\n')) if rx.search(row)]
            if rows and name not in out:
                out[name] = rows
    return dict(list(out.items())[:8])


@rule(r'not found: ')
def _missing_file(c):
    i, ctx = c.issue, c.ctx
    wanted = _wanted_file(i.title)
    swap = re.search(r'\(a (\w+) file is there instead\)', i.title)
    handled = 'onerror' in i.what
    js_only = "only in the game's JavaScript" in i.what
    if not wanted:
        return unsure('Game Doctor could not read the file name')
    if _cannot_play(c):
        return unsure(_cannot_play(c))
    users = {} if js_only else _users(c, wanted)

    def judge(ev):
        asked = [a for a in ev.files(None, wanted) if js_only or a[0] in users or ev.full]
        bad = [(s, st) for s, st, _ in asked if st >= 400]
        good = [(s, st) for s, st, _ in asked if 200 <= st < 400]
        if good:
            return fine(f'seen while playing: the game asked for "{wanted}" in "{good[0][0]}" and it loaded fine (the '
                        'game puts the right folder or file type in front itself).')
        if bad:
            if handled:
                return unsure(f'seen while playing: "{wanted}" really is missing, but the game hides the picture on '
                              'purpose when it is missing, so it may be an optional picture')
            return broken(f'seen while playing: when the game shows "{bad[0][0]}", it asks for "{wanted}" and the file '
                          f'is not there (error {bad[0][1]}). Players see a broken picture or hear nothing.')
        if js_only:
            return unsure("the name is only in the game's own script and no scene asked for it while playing"
                          + ('' if ev.full else '; a Full scan (plays every scene) can settle it'))
        if not users:
            return unsure('Game Doctor could not tell which scene loads it')
        ran = {n: [_line_ran(ev, ctx, n, r) for r in rows] for n, rows in users.items()}
        if any(v is None for vs in ran.values() for v in vs):
            return unsure(ev.problem or f'"{next(n for n, vs in ran.items() if None in vs)}" could not be played on its '
                          'own, so Game Doctor could not see which file it asks for')
        if not all(all(vs) for vs in ran.values()):
            return unsure('the part of the scene that uses it never showed with any values Game Doctor tried')
        where = ', '.join(f'"{n}"' for n in users)
        if swap:
            other = [p for s, st, p in ev.files(None, None) if s in users and p.lower().endswith('.' + swap.group(1).lower())]
            if other:
                return fine(f'seen while playing: {where} was shown and the game asked for the .{swap.group(1)} file '
                            f'instead of "{wanted}", so the right sound or picture plays.')
            return unsure(f'{where} was shown but the game asked for neither file')
        return fine(f'seen while playing: {where} was shown, including the part that uses it, and the game never asked '
                    f'for "{wanted}", so the name is only text there, not a file it loads.')
    c.play(judge, scene=next(iter(users), i.passage or ''))
    c.scenes = list(users)


@rule(r'path has wrong capital letters: ')
def _case(c):
    return unsure('it loads on Windows; it only breaks where capital letters matter (a website, Mac, Linux or '
                  'Android), which Game Doctor cannot try here')


def _named(ctx, rel):
    base = rel.replace('\\', '/').rsplit('/', 1)[-1].lower()
    return base in ctx.raw.lower() or any(base in ' '.join(ls).lower() for ls in ctx.res.files.values())


@rule(r'^Empty file \(0 bytes\): |^Damaged \w+ \(cut off\): |^Not a real \w+: |^Unrecognised or damaged \w+: |^Damaged file: ')
def _bad_file(c):
    i = c.issue
    rel = i.title.split(': ', 1)[1].strip()
    named = _named(c.ctx, rel)
    if i.title.startswith('Empty file') and named:
        return broken(f'the game names "{rel}", and the file is empty, so nothing shows or plays there.')
    if c.ctx.res.engine not in ('twine', 'html'):
        return unsure(_cannot_play(c))
    c.fact = ('decode', rel)

    def judge(ev):
        ok = ev.fact('decode', rel)
        used = ev.asked_anywhere(rel)
        if ok is True:
            return fine(f'Game Doctor opened "{rel}" in the invisible browser and it shows / plays fine.')
        if ok is None:
            return unsure(ev.why_no_facts())
        if used:
            return broken(f'the browser cannot show or play "{rel}", and the game asks for it while playing ("{used}").')
        if named and not i.title.startswith(('Damaged', 'Not a real')):
            return broken(f'the browser cannot show or play "{rel}", and the game names it.')
        return unsure(f'the browser cannot show or play "{rel}", but no scene names it; the game may build its name '
                      'while playing' + ('' if ev.full else ' (a Full scan settles it)'))
    c.judge = judge


@rule(r'^File cannot be read: ')
def _unreadable(c):
    return unsure('Windows would not let Game Doctor read the file (it is locked or has no permission)')


@rule(r'^The scene froze the game while it was tested$')
def _froze(c):
    if 'with the starting values' in c.issue.what:
        return broken('it froze with the values the game really starts with.')
    return unsure('it only froze with values an earlier scene may never give, and Game Doctor cannot know the '
                  'player\'s earlier choices')


@rule(r'is not defined$|looks like a misspelling of|is not a declared variable$')
def _other_engines(c):
    return unsure(_cannot_play(c) or 'Game Doctor cannot run this kind of game to check')


# ---------------------------------------------------------------------- evidence
FACTS_JS = r'''
(async (want) => {
  const out = {macros: {}, vars: {}, classes: {}, decode: {}, sheetsHidden: false};
  const SC = window.SugarCube;
  let H = null;
  try { H = typeof require === 'function' ? require('macros') : null; } catch (e) {}
  for (const n of want.macros) {
    let has = null;
    try { if (SC) has = !!SC.Macro.has(n); else if (H && H.has) has = !!H.has(n); } catch (e) {}
    out.macros[n] = has;
  }
  let V = null;
  try { V = SC ? SC.State.variables : require('state').variables; } catch (e) {}
  for (const v of want.vars) {
    out.vars[v] = !V ? null : V[v] === undefined ? 'undefined' : V[v] === null ? 'null' : typeof V[v];
  }
  const sel = [];
  const walk = rules => { for (const r of rules) { if (r.selectorText) sel.push(r.selectorText); if (r.cssRules) walk(r.cssRules); } };
  for (const s of document.styleSheets) { try { walk(s.cssRules); } catch (e) { out.sheetsHidden = true; } }
  const all = sel.join(' , ');
  for (const c of want.classes) {
    out.classes[c] = new RegExp('\\.' + c.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&') + '(?![\\w-])').test(all);
  }
  const IMG = /\.(png|jpe?g|gif|webp|bmp|svg|avif|ico)$/i;
  await Promise.all(want.files.map(f => new Promise(done => {
    const url = encodeURI(f.replace(/\\/g, '/'));
    const end = v => { out.decode[f] = v; done(); };
    const timer = setTimeout(() => end(false), 8000);
    const fin = v => { clearTimeout(timer); end(v); };
    if (IMG.test(f)) {
      const im = new Image();
      im.onload = () => (im.decode ? im.decode().then(() => fin(true), () => fin(false)) : fin(true));
      im.onerror = () => fin(false);
      im.src = url;
    } else {
      const m = document.createElement(/\.(mp4|webm|ogv|m4v|mov)$/i.test(f) ? 'video' : 'audio');
      m.preload = 'auto'; m.muted = true;
      m.onloadedmetadata = () => fin(true);
      m.onerror = () => fin(false);
      m.src = url;
    }
  })));
  out.alerts = window.__gbfAlerts || [];
  return out;
})
'''


class Evidence:
    def __init__(self, state, full, problem=''):
        self.state = state or {}
        self.full = full or {}
        self.problem = problem       # why the browser could not be used ('' = it was)
        self._facts = self.state.get('facts') or None

    def scene(self, name):
        return self.state.get('scenes', {}).get(name) if name else None

    def seen(self, scene, pid):
        s = self.scene(scene)
        return (s or {}).get('seen', {}).get(pid)

    def fact(self, kind, name):
        return (self._facts or {}).get(kind, {}).get(name)

    def fact_raw(self):
        return self._facts or {}

    def why_no_facts(self):
        return self.problem or 'the game could not be started in the invisible browser'

    def why_not_played(self, c):
        if self.problem:
            return self.problem
        if not c.scene:
            return 'it is not tied to one scene, so Game Doctor does not know where to look'
        if c.scene not in self.state.get('scenes', {}):
            return f'"{c.scene}" is a part that is only shown through other scenes, so it was not played on its own'
        return f'"{c.scene}" could not be shown while playing'

    def files(self, scene, wanted):
        """[(scene, status, url)] of files asked for while playing (in this scene, or anywhere when scene is None)
        whose path is `wanted` (or every file when wanted is None)."""
        out = []
        for st in (self.state, self.full):
            status = st.get('file_status', {})
            for name, r in st.get('scenes', {}).items():
                if scene and name != scene:
                    continue
                for u in r.get('files', {}):
                    path, code = status.get(u, ('', 0))
                    if wanted is None or _same_file(path or u, wanted):
                        out.append((name, code, path or u))
        return out

    def asked_anywhere(self, rel):
        hit = self.files(None, rel)
        return hit[0][0] if hit else ''


class Settler:
    def __init__(self, res, play=True, stop=None, progress=None, full_state=None):
        self.res = res
        self.play = play
        self.stop = stop or (lambda: False)
        self.progress = progress or (lambda p, m: None)
        self.full = full_state or {}
        self.seconds = 0

    def cases(self):
        ctx = Context(self.res)
        out = []
        for i in self.res.issues:
            if i.severity != WARNING or i.unreachable:
                continue
            c = Case(i, ctx)
            fn = next((f for rx, f in RULES if rx.search(i.title)), None)
            try:
                c.verdict = fn(c) if fn else unsure('Game Doctor has no way yet to prove this kind of result')
            except Exception as e:          # one rule must never stop the rest
                c.verdict = unsure(f'the check stopped early ({e})')
            out.append(c)
        return out

    def run(self):
        """Returns {issue key: verdict dict(state, proof)}."""
        t0 = time.time()
        cases = self.cases()
        waiting = [c for c in cases if c.verdict is None and c.judge]
        state, problem = {}, ''
        if waiting:
            state, problem = self.gather(waiting)
        ev = Evidence(state, self.full, problem)
        out = {}
        for c in cases:
            v = c.verdict
            if v is None and c.judge:
                try:
                    v = c.judge(ev)
                except Exception as e:
                    v = unsure(f'the check stopped early ({e})')
            out[c.issue.key] = v or unsure('Game Doctor has no way yet to prove this kind of result')
        self.seconds = time.time() - t0
        return out

    def gather(self, waiting):
        """Start the game once (facts) and play the scenes that need looking at. Returns (state, problem)."""
        import fullscan
        import playtest
        res = self.res
        if not self.play:
            return {}, 'playing was turned off for this check'
        if res.engine not in ('twine', 'html'):
            return {}, ''
        if not playtest.find_browser():
            return {}, 'it needs Microsoft Edge or Google Chrome to play the game, and neither was found'
        want = {'macros': set(), 'vars': set(), 'classes': set(), 'files': set(), 'alerts': set()}
        for c in waiting:
            f = getattr(c, 'fact', None)
            if f:
                want['files' if f[0] == 'decode' else f[0]].add(f[1])
        facts = FACTS_JS + '(' + json.dumps({k: sorted(v) for k, v in want.items()}) + ')' if any(want.values()) else None
        scenes = set(fullscan.scene_names(res)) if fullscan.can_play(res) else set()
        probes = collections.defaultdict(list)
        only = set()
        for c in waiting:
            if c.scene and c.scene in scenes:
                only.add(c.scene)
                probes[c.scene] += c.probes
            only |= set(c.scenes) & scenes
        state = {}
        self.progress(5, ('Checking {n} "worth checking" result(s) by playing the game in the invisible browser',
                          {'n': len(waiting)}))
        try:
            if fullscan.can_play(res):
                fullscan.run(res, stop=self.stop, state=state, budget=PLAY_BUDGET, only=only, probes=dict(probes),
                             facts=facts)
            elif facts:
                with playtest.launch(res) as ses:
                    time.sleep(1.0)
                    state['facts'] = ses.dt.evaluate(facts, 90)
        except (playtest.PlaytestError, OSError) as e:
            return state, f'the game could not be played in the invisible browser ({str(e)[:80]})'
        return state, ''


# ---------------------------------------------------------------------- results on the findings
SEVERITY = {BROKEN: ERROR, FINE: INFO, OPEN: WARNING}
HEAD = {BROKEN: f'{NOTE} broken.', FINE: f'{NOTE} fine.', OPEN: f'{NOTE} could not be settled automatically, because'}


def note(v):
    if v['state'] == OPEN:
        return f'{HEAD[OPEN]} {v["proof"].rstrip(".")}.'
    return f'{HEAD[v["state"]]} {v["proof"][:1].upper()}{v["proof"][1:]}'


def apply(res):
    """Put the settled results (res.settled) on the findings: severity moved with the proof added to the explanation
    (also in Simple mode). A result settled before keeps it, except one that could not be settled then."""
    import fixes
    out = getattr(res, 'settled', None) or {}
    for lst in (res.issues, getattr(res, 'quick_issues', None) or []):
        for i in lst:
            v = out.get(i.key)
            old = getattr(i, 'settle', None)
            if not v or (old and (old['state'] != OPEN or v == old)):
                continue
            if old:     # an earlier "could not be settled" note is replaced
                i.what = i.what.replace('\n\n' + note(old), '')
            i.settle = v
            i.severity = SEVERITY[v['state']]
            i.what = f'{i.what}\n\n{note(v)}'
            i.priority = fixes.priority(i)
            i.advice = fixes.advice(i)
    c = collections.Counter(i.severity for i in res.issues)
    res.stats.update(errors=c[ERROR], warnings=c[WARNING], infos=c[INFO])
    res.stats['health'], res.stats['health_label'] = scanner.health_score(res.issues, res.stats.get('passages', 0))


def count(res):
    return sum(1 for i in res.issues if i.severity == WARNING and not i.unreachable
               and (getattr(i, 'settle', None) or {}).get('state') in (None, OPEN))


def estimate_seconds(res):
    """Rough time: starting the game once, plus a few seconds per scene to play."""
    n = count(res)
    if not n:
        return 0
    scenes = {i.passage for i in res.issues if i.severity == WARNING and i.passage}
    return (10 + 2 * min(len(scenes), 40)) if res.engine in ('twine', 'html') else 1


def outcome(i):
    v = getattr(i, 'settle', None)
    return v['state'] if v else ''


if __name__ == '__main__':
    import sys
    res = scanner.scan_any(sys.argv[1])
    t = time.time()
    out = Settler(res, play='--no-play' not in sys.argv).run()
    by = {i.key: i for i in res.issues}
    for key, v in out.items():
        print(f'{v["state"]:6} {by[key].title[:90]}\n        {v["proof"][:300]}')
    print(f'{len(out)} settled in {time.time() - t:.0f}s')
