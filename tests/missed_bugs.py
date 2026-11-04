"""Missed-bug test: how many real bugs does the scan FIND? (a developer test, not part of the app)

  python tests/missed_bugs.py            everything, prints the score per kind and per story format
  python tests/missed_bugs.py --misses   also lists every planted bug the Quick scan did not report
  python tests/missed_bugs.py --no-play  Quick scan only (no invisible browser)
  python tests/missed_bugs.py --only Harlowe    one format

How it works: a small real game (or a test game for the smaller formats) is copied in memory, ONE known bug is planted,
the game is scanned, and the bug must come back as a new finding at the right place (scene and line). Games on disk are
never touched: fixtest.hidden_copy makes only this thread's open() see the changed text.

Score = reported as Broken or Worth checking. A bug that only comes back as a note ("info") is counted apart and is not
in the score: most players never look at notes.
Full scan: for SugarCube and Harlowe every kind of plant is also played once (each in its own scene, all in one copy,
compared with the same scenes played without plants), which shows what only playing finds.
run() fails when a score drops below FLOOR. Raise FLOOR when the scanner gets better; never lower it without a reason.
"""
import collections
import concurrent.futures
import dataclasses
import hashlib
import html
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import fixtest  # noqa: E402
import formats  # noqa: E402
import fullscan  # noqa: E402
import media  # noqa: E402
import playtest  # noqa: E402
import scanner  # noqa: E402
import twine_formats  # noqa: E402

GAMES_ROOT = r'E:\E-Games\English'
FIX_DIR = os.path.join(HERE, 'fixtures')
CT = os.path.join(GAMES_ROOT, 'Condemned town version 1.08a', 'Condemned town version 1.08b (fixed).html')
MB = os.path.join(GAMES_ROOT, 'MB.5', 'MB.5', 'MB 0.24.6.html')
TGP = os.path.join(GAMES_ROOT, 'TGP Chapter 5', 'index.html')

# Lowest allowed share of planted bugs found by the Quick scan, per format (see the module text).
FLOOR = {'SugarCube': 98.2, 'Harlowe': 99.2, 'Chapbook': 100, 'Snowman': 100, 'Plain HTML': 100, "Ren'Py": 87.0,
         'Ink': 100, 'ChoiceScript': 100, 'RPG Maker': 100}
FLOOR_ALL = 98.4     # only when all the real games were there

SPOT_LABEL = dict(plain='plain text', branch='only inside a branch', click='inside a link/button',
                  part='inside a widget / included part', rare='rarely reached scene', file='in the file', game='whole game')
PLAY_BUDGET = 10
_markup = {}
ZQ = 'Zqx Nowhere'    # a name nothing in any game uses
ZF = 'zqxfile'


# ---------------------------------------------------------------------- one planted bug
@dataclasses.dataclass
class Plant:
    fmt: str             # story format / engine
    game: str
    kind: str            # e.g. "broken link"
    label: str           # the planted text, or what was changed
    spot: str            # see SPOT_LABEL
    where: str           # scene (or file name) the finding must name
    line: int            # line the finding must be on (0 = not checked: e.g. a tag that is never closed is
    #                      reported at its opening tag, wherever that is)
    expect: str          # regular expression the finding's title + snippet must match
    edits: dict = dataclasses.field(default_factory=dict)   # scene -> new text (Twine); file -> new text (others)
    js: str = ''         # added to the Story JavaScript
    add: tuple = ()      # new passages: (name, tags, text)


def fast_scan(path, game_dir, files):
    """The normal scan on a hidden copy, without the two steps no plant needs (they take most of the time):
    spelling and checking every media file for damage."""
    keep_media, keep_text, keep_markup = media.check_files, scanner.Scanner.check_text, scanner.Scanner.markup_text

    def remembered_markup(self, p):      # only the planted scene changes, so the others need working out once
        if p.text not in _markup:
            _markup[p.text] = keep_markup(self, p)
        return _markup[p.text]
    media.check_files = lambda *a, **k: None
    scanner.Scanner.check_text = lambda self: None
    scanner.Scanner.markup_text = remembered_markup
    try:
        with fixtest.hidden_copy(files):
            if path.lower().endswith(('.html', '.htm')):
                return scanner.Scanner(path, game_dir).run()
            return formats.scan_path(path, game_dir)
    finally:
        media.check_files, scanner.Scanner.check_text, scanner.Scanner.markup_text = keep_media, keep_text, keep_markup


def findings(res):
    return [dict(sev=i.severity, title=i.title, passage=i.passage, snippet=i.snippet or '', line=i.line,
                 file=os.path.basename(i.file or ''), cat=i.category) for i in res.issues]


def fkey(f):
    return f['sev'], f['title'], f['passage'], f['snippet']


# ---------------------------------------------------------------------- Twine: building a copy with plants
PASSAGE_RE = re.compile(r'<tw-passagedata\b([^>]*)>(.*?)</tw-passagedata>', re.S)


def build_twine(raw, plant_list):
    """The game file with these plants. Only scenes with a plant are changed; html.escape keeps them readable by the
    game and the scanner the same way Twine's own escaping does."""
    edits, js, add = {}, '', []
    for pl in plant_list:
        edits.update(pl.edits)
        js += pl.js
        add += list(pl.add)
    out, pos = [], 0
    for m in PASSAGE_RE.finditer(raw):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
        name = html.unescape(a.get('name', ''))
        if name in edits:
            out += [raw[pos:m.start(2)], html.escape(edits.pop(name))]
            pos = m.end(2)
    out.append(raw[pos:])
    text = ''.join(out)
    if js:
        text = re.sub(r'(<script[^>]*id="twine-user-script"[^>]*>)(.*?)(</script>)',
                      lambda m: m.group(1) + m.group(2) + js + m.group(3), text, count=1, flags=re.S)
    if add:
        extra = ''.join(f'<tw-passagedata pid="9{n}999" name="{html.escape(a)}" tags="{b}" position="0,0" size="100,100">'
                        f'{html.escape(c)}</tw-passagedata>' for n, (a, b, c) in enumerate(add))
        text = text.replace('</tw-storydata>', extra + '</tw-storydata>', 1)
    return text


def _read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def _scan_twine(args):
    path, game_dir, pl = args
    return findings(fast_scan(path, game_dir, {path: build_twine(_read(path), [pl])}))


def _scan_files(args):
    target, game_dir, pl = args
    return findings(fast_scan(target, game_dir, pl.edits))


# ---------------------------------------------------------------------- reading a real game to find spots
def typo(word, avoid=()):
    """The word with two letters in the middle swapped: the commonest slip when typing a name."""
    letters = [i for i in range(1, len(word) - 2) if word[i].isalpha() and word[i + 1].isalpha() and word[i] != word[i + 1]]
    for i in letters[len(letters) // 2:] + letters[:len(letters) // 2]:
        t = word[:i] + word[i + 1] + word[i] + word[i + 2:]
        if t not in avoid and t.lower() != word.lower():
            return t
    return word + 'x'


class Game:
    """What the plants need to know about one Twine game: spots to plant in, and real names to mistype."""
    OPEN = None

    def __init__(self, fmt, name, path):
        self.fmt, self.name, self.path = fmt, name, path
        self.game_dir = os.path.dirname(path)
        self.res = fast_scan(path, self.game_dir, {})
        self.sc = self.res.context
        self.base = findings(self.res)
        self.by_name = {p.name: p for p in self.sc.passages}
        self.names = set(self.by_name)
        self.scenes = [p for p in self.sc.story_passages() if p.name not in self.res.unreachable and p.raw_text.strip()
                       and not (set(p.tags) & (scanner.K.SPECIAL_TAGS | {'widget'})) and p.name not in scanner.K.SPECIAL_PASSAGES]
        self.depth = self._depths()
        self.scenes.sort(key=lambda p: (self.depth.get(p.name, 99), p.name))
        self.spots = self.find_spots()
        self.counter = collections.Counter()

    def _depths(self):
        out, todo = {self.sc.start_passage: 0}, [self.sc.start_passage]
        edges = collections.defaultdict(set)
        for f, t, *_ in self.res.link_edges:
            edges[f].add(t)
        while todo:
            n = todo.pop(0)
            for t in sorted(edges[n]):
                if t not in out:
                    out[t] = out[n] + 1
                    todo.append(t)
        return out

    def in_degree(self):
        c = collections.Counter()
        for f, t, *_ in self.res.link_edges:
            c[t] += 1
        return c

    def blocked(self, text):
        """Parts of a scene where a planted text would not count as code: notes, scripts, styles, inside tags."""
        spans = [m.span() for m in scanner.COMMENT_RE.finditer(text)]
        spans += [m.span() for m in scanner.SCRIPT_BODY_RE.finditer(text)]
        spans += [m.span() for m in re.finditer(r'<style\b.*?</style>', text, re.S | re.I)]
        spans += [m.span() for m in re.finditer(r'<[^<>]*>', text)]
        return spans

    def is_free(self, spans, off):
        return not any(a < off < b for a, b in spans)

    def line_of(self, p, off):
        return p.line + p.raw_text.count('\n', 0, off)

    def insert(self, kind, label, text, expect, spot, loose=False, pick=0, start=False):
        cands = self.spots.get(spot) or []
        if not cands:
            return None
        p, off = cands[pick % len(cands)]
        off = 0 if start else off
        return Plant(self.fmt, self.name, kind, label, spot, p.name, 0 if loose else self.line_of(p, off), expect,
                     {p.name: p.raw_text[:off] + text + p.raw_text[off:]})

    def change(self, kind, label, p, a, b, new, expect, spot=None, loose=False):
        spot = spot or ('branch' if self.depth_at(p.raw_text, a) else 'plain')
        return Plant(self.fmt, self.name, kind, label, spot, p.name, 0 if loose else self.line_of(p, a), expect,
                     {p.name: p.raw_text[:a] + new + p.raw_text[b:]})

    def real_var(self):
        """A variable the game really uses and sets, long enough for a typo to look like one."""
        c = collections.Counter()
        for p in self.scenes:
            c.update(re.findall(r'(?<![\w$])\$([A-Za-z]{5,12})\b', p.text))
        for v, n in c.most_common():
            if n >= 5 and self.var_is_set(v):
                return v
        return c.most_common(1)[0][0]

    def real_image(self):
        for root, dirs, names in sorted(os.walk(self.game_dir)):
            for n in sorted(names):
                if re.search(r'\.(jpe?g|png|gif|webp)$', n) and re.fullmatch(r'[\w./ -]+', n):
                    rel = os.path.relpath(os.path.join(root, n), self.game_dir).replace('\\', '/')
                    if '/' in rel:
                        return rel
        return None

    def real_scene(self):
        return next(p.name for p in self.scenes if len(p.name) >= 6 and re.fullmatch(r'[A-Za-z ]+', p.name)
                    and p.name != self.sc.start_passage)

    def tokens(self):
        v = self.real_var()
        scene = self.real_scene()
        img = self.real_image() or 'images/none.jpg'
        d, b = os.path.split(img)
        out = {'{V}': v, '{VT}': typo(v), '{VC}': v[0].upper() + v[1:] if v[0].islower() else v[0].lower() + v[1:],
               '{IMG}': img, '{IMGC}': (d + '/' if d else '') + b[:-len(b.rsplit('.', 1)[1])] + b.rsplit('.', 1)[1].upper(),
               '{IMGF}': (d.upper() + '/' + b) if d else b.upper(),
               '{P}': scene, '{PT}': typo(scene, self.names), '{PL}': scene.lower() if scene.lower() != scene else scene.upper(),
               '{PS}': scene + ' ', '{ZQ}': ZQ, '{ZF}': ZF}
        return out

    def from_templates(self, templates, n_spots=2):
        """Each planted text goes into `n_spots` kinds of place; which ones turns over, so every place gets every sort."""
        tok = self.tokens()
        order = list(self.spots)
        plants = []

        def sub(s, esc=False):
            for k, v in tok.items():
                s = s.replace(k, re.escape(v) if esc else v)
            return s
        for i, tpl in enumerate(templates):
            kind, text, expect = tpl[:3]
            opts = tpl[3] if len(tpl) > 3 else {}
            allowed = [s for s in order if s in opts.get('spots', order)]
            chosen = [allowed[(i + j * 2) % len(allowed)] for j in range(min(n_spots, len(allowed)))]
            for s in dict.fromkeys(chosen):
                self.counter[s] += 1
                pl = self.insert(kind, text, sub(text), sub(expect, True), s, opts.get('loose', False), self.counter[s] - 1, opts.get('start', False))
                if pl:
                    plants.append(pl)
        return plants


# ---------------------------------------------------------------------- SugarCube
SC_BLOCKS = 'if|for|link|button|switch|linkreplace|linkappend|linkprepend|widget|silently|timed|repeat|replace|append|prepend|capture'
ANY_FILE = r'not found: '


def miss_file(name):
    return ANY_FILE + re.escape(name)


def link_to(name='{ZQ}'):
    return r'missing passage "' + name + '"'


PLAIN_ONLY = dict(spots=('plain', 'rare'))
LOOSE = dict(loose=True)
SC_TEMPLATES = [
    # broken link
    ('broken link', '[[Go there|{ZQ}]]', link_to()),
    ('broken link', '[[{ZQ}]]', link_to()),
    ('broken link', '[[Go->{ZQ}]]', link_to()),
    ('broken link', '[[{ZQ}<-Go]]', link_to()),
    ('broken link', '[[Go|{ZQ}][${V} to 1]]', link_to()),
    ('broken link', '<<link "Go" "{ZQ}">><</link>>', link_to()),
    ('broken link', '<<button "Go" "{ZQ}">><</button>>', link_to()),
    ('broken link', '<<link [[Go|{ZQ}]]>><</link>>', link_to()),
    ('broken link', '<<button [[Go|{ZQ}]]>><</button>>', link_to()),
    ('broken link', '<a data-passage="{ZQ}">Go</a>', link_to()),
    ('broken link', '[img[{IMG}][{ZQ}]]', link_to()),
    ('broken link', '<<return "Back" "{ZQ}">>', link_to()),
    ('broken link', '<<back "Back" "{ZQ}">>', link_to()),
    ('broken link', '<<textbox "$zqxname" "" "{ZQ}">>', link_to()),
    # broken goto / include
    ('broken goto/include', '<<goto "{ZQ}">>', link_to()),
    ('broken goto/include', "<<goto '{ZQ}'>>", link_to()),
    ('broken goto/include', '<<goto [[{ZQ}]]>>', link_to()),
    ('broken goto/include', '<<include "{ZQ}">>', link_to()),
    ('broken goto/include', '<<include "{ZQ}" "div">>', link_to()),
    ('broken goto/include', '<<include [[{ZQ}]]>>', link_to()),
    ('broken goto/include', '<<display "{ZQ}">>', link_to()),
    ('broken goto/include', '<<run Engine.play("{ZQ}")>>', link_to()),
    ('broken goto/include', '<<script>>Engine.play("{ZQ}");<</script>>', link_to()),
    # misspelled scene name
    ('misspelled scene name', '[[Go|{PT}]]', link_to('{PT}')),
    ('misspelled scene name', '<<goto "{PT}">>', link_to('{PT}')),
    ('misspelled scene name', '<<include "{PT}">>', link_to('{PT}')),
    ('misspelled scene name', '<<link "Go" "{PT}">><</link>>', link_to('{PT}')),
    ('misspelled scene name', '[[Go|{PL}]]', link_to('{PL}')),
    # missing files
    ('missing picture', f'<img src="images/{ZF}.jpg">', miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f"<img src='images/{ZF}.jpg'>", miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f'<img src=images/{ZF}.jpg>', miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f'[img[images/{ZF}.png]]', miss_file(f'images/{ZF}.png')),
    ('missing picture', f'[img[A picture|images/{ZF}.png]]', miss_file(f'images/{ZF}.png')),
    ('missing picture', f'<img @src="\'images/{ZF}.jpg\'">', miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f'<<set _zqxpic to "images/{ZF}.jpg">>', miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f'<div style="background-image:url(\'images/{ZF}.png\')">x</div>', miss_file(f'images/{ZF}.png')),
    ('missing picture', f'<img src="images/{ZF}.gif" onerror="this.remove()">', miss_file(f'images/{ZF}.gif')),
    ('missing picture', f'<img @src="\'images/{ZF}\' + 1 + \'.jpg\'">', ZF + r'1\.jpg'),      # name made in code
    ('missing sound', f'<audio src="audio/{ZF}.mp3"></audio>', miss_file(f'audio/{ZF}.mp3')),
    ('missing sound', f'<audio controls><source src="audio/{ZF}.ogg"></audio>', miss_file(f'audio/{ZF}.ogg')),
    ('missing sound', f'<<cacheaudio "zqxtrack" "audio/{ZF}.mp3">>', miss_file(f'audio/{ZF}.mp3')),
    ('missing sound', f'<<script>>new Audio("audio/{ZF}.mp3").play();<</script>>', miss_file(f'audio/{ZF}.mp3')),
    ('missing video', f'<video src="video/{ZF}.mp4" autoplay></video>', miss_file(f'video/{ZF}.mp4')),
    ('missing video', f'<video><source src="video/{ZF}.webm"></video>', miss_file(f'video/{ZF}.webm')),
    ('missing video', f'<video poster="images/{ZF}.jpg"></video>', miss_file(f'images/{ZF}.jpg')),
    # wrong capitals
    ('wrong capital letters', '<img src="{IMGC}">', r'wrong capital letters: {IMGC}'),
    ('wrong capital letters', '<img src="{IMGF}">', r'wrong capital letters: {IMGF}'),
    ('wrong capital letters', '[img[{IMGC}]]', r'wrong capital letters: {IMGC}'),
    # unclosed macro tags
    ('unclosed macro tag', '<<if ${V} gt 1>>x', r'<<if>> is never closed|<</if>> ', LOOSE),
    ('unclosed macro tag', '<<for _zqxi to 0; _zqxi lt 1; _zqxi++>>x', r'<<for>> is never closed', LOOSE),
    ('unclosed macro tag', '<<link "Go">>x', r'<<link>> is never closed', LOOSE),
    ('unclosed macro tag', '<<button "Go">>x', r'<<button>> is never closed', LOOSE),
    ('unclosed macro tag', '<<switch ${V}>><<case 1>>x', r'<<switch>> is never closed', LOOSE),
    ('unclosed macro tag', '<<linkreplace "Go">>x', r'<<linkreplace>> is never closed', LOOSE),
    ('unclosed macro tag', '<<silently>>x', r'<<silently>> is never closed', LOOSE),
    ('unclosed macro tag', '<<print 1>', r'not closed'),
    ('unclosed macro tag', '<<print "a" ', r'not closed'),
    ('unclosed macro tag', '<<if ${V}>x<</if>', r'missing a|not closed|never closed|<<if', LOOSE),
    ('unclosed macro tag', '<<', r'Stray "<<"'),
    # stray macro tags
    ('stray macro tag', '<</if>>', r'<</if>> ', LOOSE),
    ('stray macro tag', '<</for>>', r'<</for>> ', LOOSE),
    ('stray macro tag', '<</link>>', r'<</link>> ', LOOSE),
    ('stray macro tag', '<<endif>>', r'<</if>> |never closed', LOOSE),
    ('stray macro tag', '<</set>>', r'<</set>> '),
    ('stray macro tag', '<<else>>', r'<<else>> is outside', PLAIN_ONLY),
    ('stray macro tag', '<<elseif ${V}>>', r'<<elseif>> is outside', PLAIN_ONLY),
    ('stray macro tag', '<<case 1>>', r'<<case>> is outside'),
    ('stray macro tag', '<<default>>', r'<<default>> is outside'),
    ('stray macro tag', '<<<print 1>>', r'Extra "<"'),
    ('stray macro tag', '<<print 1>>>', r'Extra ">"'),
    ('stray macro tag', '<<if ${V}>><<link "Go">><</if>><</link>>', r'<<link>> is never closed|<</link>> ', LOOSE),
    # misspelled macro
    ('misspelled macro', '<<prnt 1>>', r'Unknown macro <<prnt>>'),
    ('misspelled macro', '<<inclde "{P}">>', r'Unknown macro <<inclde>>'),
    ('misspelled macro', '<<linkk "Go">>x<</linkk>>', r'Unknown macro <<linkk>>'),
    ('misspelled macro', '<<sett ${V} to 1>>', r'Unknown macro <<sett>>'),
    ('misspelled macro', '<<gotoo "{P}">>', r'Unknown macro <<gotoo>>'),
    ('misspelled macro', '<<IF ${V}>>x<</IF>>', r'Unknown macro <<IF>>'),
    ('misspelled macro', '<<zqxmacro>>', r'Unknown macro <<zqxmacro>>'),
    ('misspelled macro', '<<if ${V}>>a<<elsif ${V} gt 1>>b<</if>>', r'Unknown macro <<elsif>>'),
    ('misspelled macro', '<<if ${V}>>a<<else if ${V} gt 1>>b<</if>>', r'<<else if>>'),
    ('misspelled macro', '<<if ${V}>>a<<else ${V}>>b<</if>>', r'<<else>> cannot have a condition'),
    ('misspelled macro', '<<if ${V}>>a<<else>>b<<else>>c<</if>>', r'after <<else>>'),
    ('misspelled macro', '<<if ${V}>>a<<else>>b<<elseif ${V}>>c<</if>>', r'after <<else>>'),
    # "=" in a condition
    ('"=" in a condition', '<<if ${V} = 1>>x<</if>>', r'Single "="'),
    ('"=" in a condition', '<<if ${V}=1>>x<</if>>', r'Single "="'),
    ('"=" in a condition', '<<if ${V} += 1>>x<</if>>', r'"\+=" inside'),
    ('"=" in a condition', '<<if ${V} is 1 or ${V} = 2>>x<</if>>', r'Single "="'),
    ('"=" in a condition', '<<if ${V}>>a<<elseif ${V} = 2>>b<</if>>', r'Single "="'),
    ('"=" in a condition', '<<if (${V} = 1) and ${V}>>x<</if>>', r'Single "="'),
    # other broken conditions
    ('other broken condition', '<<if ${V} => 1>>x<</if>>', r'"=>" or "=<"'),
    ('other broken condition', '<<if ${V} =< 1>>x<</if>>', r'"=>" or "=<"'),
    ('other broken condition', '<<if (${V} gt 1>>x<</if>>', r'Brackets do not match'),
    ('other broken condition', '<<if ${V} gt 1)>>x<</if>>', r'Brackets do not match'),
    ('other broken condition', '<<if ${V} is not 1>>x<</if>>', r'"is not"'),
    ('other broken condition', '<<if ${V} is 1 or 2>>x<</if>>', r'does not compare both'),
    ('other broken condition', '<<if>>x<</if>>', r'has no condition'),
    ('other broken condition', '<<if ${V} is>>x<</if>>', r'unfinished'),
    ('other broken condition', '<<if ${V} gt>>x<</if>>', r'unfinished'),
    ('other broken condition', '<<if ${V} and>>x<</if>>', r'unfinished'),
    ('other broken condition', '<<if ${V} is "a>>x<</if>>', r'[Uu]nclosed quote'),
    # variable typo
    ('variable typo', '<<print ${VT}>>', r'\${VT} '),
    ('variable typo', 'Value: ${VT}.', r'\${VT} '),
    ('variable typo', '<<if ${VT} gt 1>>x<</if>>', r'\${VT} '),
    ('variable typo', '<<set ${VT} to 1>>', r'\${VT} '),
    ('variable typo', '<<set ${V} to ${VT} + 1>>', r'\${VT} '),
    ('variable typo', '<<print ${VC}>>', r'\${VC} '),
    ('variable typo', '<<set _zqxtemp to 1>><<print _zqxtmep>>', r'_zqxtmep '),
    ('variable typo', '${V}-zqx', r'\${V}-zqx'),
    # broken HTML
    ('broken HTML', '<span>x', r'<span> opened more', LOOSE),
    ('broken HTML', '<b>x', r'<b> opened more', LOOSE),
    ('broken HTML', '<a href="{IMG}">x', r'<a> opened more', LOOSE),
    ('broken HTML', '<table><tr><td>x', r'<table> opened more', LOOSE),
    ('broken HTML', '</span>', r'<span> closed more', LOOSE),
    ('broken HTML', '</div>', r'<div> closed more', LOOSE),
    ('broken HTML', '<img src="{IMG}>', r'unclosed quote|not found'),
    ('broken HTML', '<img src="">', r'Empty src'),
    ('broken HTML', '<a href="#zqxanchor">x</a>', r'#zqxanchor'),
    ('broken HTML', '<span>>x</span>', r'Stray ">"'),
    ('broken HTML', '<span id="zqxid">a</span><span id="zqxid">b</span>', r'id="zqxid"'),
    # broken JavaScript / code
    ('broken JavaScript', '<<script>>if (zqx { y(); <</script>>', r'JavaScript brackets'),
    ('broken JavaScript', '<script>if (zqx { y();</script>', r'JavaScript brackets'),
    ('broken JavaScript', '<<script>>var zqx = "abc;<</script>>', r'JavaScript brackets'),
    ('broken JavaScript', '<<script>>}<</script>>', r'JavaScript brackets'),
    ('broken JavaScript', '<<run zqxFn(>>', r'Brackets|brackets'),
    ('broken JavaScript', '<<print (1>>', r'Brackets|brackets'),
    ('broken JavaScript', '<<run zqxDoesNotExist()>>', r'zqxDoesNotExist'),
    ('broken code in <<set>>', '<<set ${V} to (1>>', r'Brackets do not match'),
    ('broken code in <<set>>', '<<set ${V} is 1>>', r'compares instead'),
    ('broken code in <<set>>', '<<set ${V}>>', r'does not change'),
    ('broken code in <<set>>', '<<set>>', r'nothing to set'),
    ('broken code in <<set>>', '<<set ${V} to "abc>>', r'[Uu]nclosed quote'),
    ('broken code in <<set>>', '<<print "abc>>', r'[Uu]nclosed quote'),
    ('broken code in <<set>>', '<<set ${V} to [1,2>>', r'[Bb]rackets'),
    ('broken code in <<set>>', '<<set ${V} to {a:1>>', r'[Bb]rackets'),
    ('broken code in <<set>>', '<<set ${V} to 1 +>>', r'unfinished'),
    # SugarCube only
    ('SugarCube rules', '[[Go|]]', r'empty target'),
    ('SugarCube rules', '[[Go there', r'not closed with \]\]'),
    ('SugarCube rules', '<<goto "">>', r'empty target'),
    ('SugarCube rules', '<<widget "zqxwidget">>x<</widget>>', r'without the "widget" tag', dict(spots=('plain', 'branch', 'rare'))),
]


class SugarCube(Game):
    def var_is_set(self, v):
        return self.sc.var_counts[1][v] > 0

    def depth_at(self, text, off):
        depth = 0
        for m in re.finditer(r'<<(/?)(?:%s)\b' % SC_BLOCKS, text[:off]):
            depth += -1 if m.group(1) else 1
        return depth > 0

    def find_spots(self):
        dead = self.sc.dead_branches()
        spots = dict(plain=[], branch=[], click=[], part=[], rare=[])
        indeg = self.in_degree()
        for p in self.scenes:
            t = p.raw_text
            spans = self.blocked(t) + [m.span() for m in re.finditer(r'<<.*?>>', t, re.S)]
            if self.depth.get(p.name, 99) <= 5 and len(spots['plain']) < 8:
                for m in re.finditer(r'^', t, re.M):
                    if m.start() and self.is_free(spans, m.start()) and not self.depth_at(t, m.start()):
                        spots['plain'].append((p, m.start()))
                        break
            if not dead.get(p.name) and len(spots['branch']) < 12:
                m = re.search(r'<<if\b[^<>]*>>', t)
                if m and self.is_free(self.blocked(t), m.start() + 1) and not self.depth_at(t, m.start()) \
                        and not scanner.COMMENT_RE.search(t[:m.start()]):
                    spots['branch'].append((p, m.end()))
            if len(spots['click']) < 8:
                m = re.search(r'<<(?:link|button)\s+"[^"]*"(?:\s+"[^"]*")?\s*>>', t)
                if m and self.is_free(self.blocked(t), m.start() + 1):
                    spots['click'].append((p, m.end()))
        for p in sorted(self.sc.passages, key=lambda q: q.name):      # widgets something calls
            if 'widget' in p.tags:
                for m in re.finditer(r'<<widget\s+"([\w-]+)"[^>]*>>', p.raw_text):
                    if len(spots['part']) < 8 and sum(q.text.count('<<' + m.group(1)) for q in self.scenes) \
                            and not scanner.COMMENT_RE.search(p.raw_text[:m.start()]):
                        spots['part'].append((p, m.end()))
                        break
        for p in sorted(self.scenes, key=lambda q: (-self.depth.get(q.name, 0), q.name)):
            if indeg[p.name] == 1 and self.depth.get(p.name, 99) < 99 and len(spots['rare']) < 8:
                spans = self.blocked(p.raw_text) + [m.span() for m in re.finditer(r'<<.*?>>', p.raw_text, re.S)]
                for m in re.finditer(r'^', p.raw_text, re.M):
                    if m.start() and self.is_free(spans, m.start()) and not self.depth_at(p.raw_text, m.start()):
                        spots['rare'].append((p, m.start()))
                        break
        return spots

    def mutations(self):
        """Bugs made by changing what the game really says, not by adding to it. One outside and one inside a branch."""
        out, want = [], collections.Counter()
        names, tok = self.names, self.tokens()

        def take(kind, p, a, b, new, expect, loose=False):
            spot = 'branch' if self.depth_at(p.raw_text, a) else 'plain'
            if want[kind, spot] >= 2:
                return
            want[kind, spot] += 1
            out.append(self.change(kind, f'real code changed: {p.raw_text[a:b][:50]!r} -> {new[:50]!r}', p, a, b, new, expect, spot, loose))

        for p in self.scenes:
            t = p.raw_text
            free = self.blocked(t)
            for m in re.finditer(r'\[\[([^\]\[|]+)\|([^\]\[|]+)\]\]', t):
                tgt = m.group(2)
                if tgt in names and re.search('[a-z]{3}', tgt) and not re.search(r'\d', tgt) and self.is_free(free, m.start() + 1):
                    new = typo(tgt, names)
                    take('misspelled scene name', p, m.start(2), m.end(2), new, link_to(re.escape(new)))
            for m in re.finditer(r'<<(goto|include) "([^"]+)">>', t):
                if m.group(2) in names and not re.search(r'\d', m.group(2)):
                    new = typo(m.group(2), names)
                    take('misspelled scene name', p, m.start(2), m.end(2), new, link_to(re.escape(new)))
            for m in re.finditer(r'<img\b[^<>]*?\bsrc="([\w./ -]+?)\.(jpe?g|png|gif|webp)"', t, re.I):
                path = f'{m.group(1)}.{m.group(2)}'
                if os.path.isfile(os.path.join(self.game_dir, path)):
                    take('missing picture', p, m.start(1), m.end(1), m.group(1) + 'zqx', miss_file(m.group(1) + 'zqx'))
                    new = m.group(1)[:-1] + m.group(1)[-1].swapcase()
                    if new != m.group(1):
                        take('wrong capital letters', p, m.start(1), m.end(1), new, 'wrong capital letters: ' + re.escape(new))
            for m in re.finditer(r'<</if>>', t):
                if self.is_free(free, m.start() + 1):
                    take('unclosed macro tag', p, m.start(), m.end(), '', r'<<if>> is never closed|<</\w+>> ', True)
            for m in re.finditer(r'<<(if|elseif)\s+(\$\w+)\s+(?:is|==|eq)\s+([^<>"]+?)>>', t):
                take('"=" in a condition', p, m.start(), m.end(), f'<<{m.group(1)} {m.group(2)} = {m.group(3)}>>', r'Single "="')
            for m in re.finditer(r'<<(print|include|goto|set)\b', t):
                new = typo(m.group(1), scanner.K.SUGARCUBE_MACROS) if len(m.group(1)) > 3 else m.group(1) + 'x'
                take('misspelled macro', p, m.start(1), m.end(1), new, f'Unknown macro <<{re.escape(new)}>>')
            for m in re.finditer(r'<<if\s+\$(' + re.escape(tok['{V}']) + r')\b', t):
                take('variable typo', p, m.start(1), m.end(1), tok['{VT}'], r'\$' + re.escape(tok['{VT}']) + ' ')
            for m in re.finditer(r'\$(' + re.escape(tok['{V}']) + r')\b', t):
                if not t[:m.start()].rstrip().endswith('<<set'):
                    take('variable typo', p, m.start(1), m.end(1), tok['{VT}'], r'\$' + re.escape(tok['{VT}']) + ' ')
        return out

    def game_wide(self):
        s = self.scenes[3]
        return [
            Plant(self.fmt, self.name, 'broken JavaScript', 'Story JavaScript: unclosed {', 'game', 'Story JavaScript', 0,
                  r'JavaScript brackets', js='\nwindow.zqxBroken = function () { if (1) {\n'),
            Plant(self.fmt, self.name, 'broken JavaScript', 'Story JavaScript: unclosed quote', 'game', 'Story JavaScript', 0,
                  r'JavaScript brackets', js='\nwindow.zqxBroken = "abc;\n'),
            Plant(self.fmt, self.name, 'SugarCube rules', 'two scenes with the same name', 'game', s.name, 0,
                  r'name used 2 times', add=((s.name, '', 'A copy.'),)),
            Plant(self.fmt, self.name, 'missing picture', 'Story JavaScript names a missing picture', 'game', '', 0,
                  miss_file(f'images/{ZF}.jpg'), js=f'\nwindow.zqxPic = "images/{ZF}.jpg";\n'),
        ]

    def plants(self):
        return self.from_templates(SC_TEMPLATES, 1) + self.mutations() + self.game_wide()


# ---------------------------------------------------------------------- Harlowe
HW_TEMPLATES = [
    ('broken link', '[[Go->{ZQ}]]', link_to()),
    ('broken link', '[[{ZQ}<-Go]]', link_to()),
    ('broken link', '[[{ZQ}]]', link_to()),
    ('broken link', '(link-goto:"Go","{ZQ}")', link_to()),
    ('broken link', '(link-goto:"{ZQ}")', link_to()),
    ('broken link', '(link-reveal-goto:"Go","{ZQ}")', link_to()),
    ('broken link', "(link-goto:'Go','{ZQ}')", link_to()),
    ('broken link', '(click-goto:"Go","{ZQ}")', link_to()),
    ('broken link', '(click-goto:?zqxh,"{ZQ}")', link_to()),
    ('broken link', '(mouseover-goto:"Go","{ZQ}")', link_to()),
    ('broken goto/include', '(go-to:"{ZQ}")', link_to()),
    ('broken goto/include', '(goto:"{ZQ}")', link_to()),
    ('broken goto/include', "(go-to:'{ZQ}')", link_to()),
    ('broken goto/include', '(redirect:"{ZQ}")', link_to()),
    ('broken goto/include', '(display:"{ZQ}")', link_to()),
    ('broken goto/include', "(display:'{ZQ}')", link_to()),
    ('broken goto/include', '(link:"Go")[(go-to:"{ZQ}")]', link_to()),
    ('misspelled scene name', '[[Go->{PT}]]', link_to('{PT}')),
    ('misspelled scene name', '(go-to:"{PT}")', link_to('{PT}')),
    ('misspelled scene name', '(display:"{PT}")', link_to('{PT}')),
    ('misspelled scene name', '(link-goto:"Go","{PT}")', link_to('{PT}')),
    ('misspelled scene name', '[[Go->{PL}]]', link_to('{PL}')),
    ('missing picture', f'<img src="images/{ZF}.jpg">', miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f"<img src='images/{ZF}.jpg'>", miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f'<img src=images/{ZF}.jpg>', miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f'<div style="background-image:url(\'images/{ZF}.png\')">x</div>', miss_file(f'images/{ZF}.png')),
    ('missing picture', f'(set:$zqxpic to "images/{ZF}.jpg")', miss_file(f'images/{ZF}.jpg')),
    ('missing picture', f'(print:\'<img src="images/{ZF}\' + "1.jpg" + \'">\')', ZF + r'1\.jpg'),   # name made in code
    ('missing sound', f'<audio src="audio/{ZF}.mp3"></audio>', miss_file(f'audio/{ZF}.mp3')),
    ('missing sound', f'<audio controls><source src="audio/{ZF}.ogg"></audio>', miss_file(f'audio/{ZF}.ogg')),
    ('missing video', f'<video src="video/{ZF}.mp4" autoplay></video>', miss_file(f'video/{ZF}.mp4')),
    ('missing video', f'<video><source src="video/{ZF}.webm"></video>', miss_file(f'video/{ZF}.webm')),
    ('wrong capital letters', '<img src="{IMGC}">', r'wrong capital letters: {IMGC}'),
    ('wrong capital letters', '<img src="{IMGF}">', r'wrong capital letters: {IMGF}'),
    ('unclosed macro tag', '(if:${V} > 1)[x', r'Hook "\[" is never closed', LOOSE),
    ('unclosed macro tag', '(if:${V} > 1[x]', r'never closed with "\)"|no hook', LOOSE),
    ('unclosed macro tag', '(print:1', r'never closed with "\)"', LOOSE),
    ('unclosed macro tag', '(link:"Go")[x', r'Hook "\[" is never closed', LOOSE),
    ('unclosed macro tag', '|zqxhook>[x', r'Hook "\[" is never closed', LOOSE),
    ('stray macro tag', 'x]', r'never opened', LOOSE),
    ('stray macro tag', '(else:)[x]', r'\(else:\) has no \(if:\)', dict(spots=('plain', 'rare'), start=True)),
    ('stray macro tag', '(else-if:${V})[x]', r'\(else-if:\) has no \(if:\)', dict(spots=('plain', 'rare'), start=True)),
    ('stray macro tag', '(if:${V} > 1)', r'no hook'),
    ('stray macro tag', '(link:"Go")', r'no hook'),
    ('misspelled macro', '(prnt:1)', r'Unknown macro \(prnt:\)'),
    ('misspelled macro', '(iff:${V})[x]', r'Unknown macro \(iff:\)'),
    ('misspelled macro', '(sett:${V} to 1)', r'Unknown macro \(sett:\)'),
    ('misspelled macro', '(go-too:"{P}")', r'Unknown macro \(go-too:\)'),
    ('misspelled macro', '(linkk:"Go")[x]', r'Unknown macro \(linkk:\)'),
    ('misspelled macro', '(zqxmacro:1)', r'Unknown macro \(zqxmacro:\)'),
    ('"=" in a condition', '(if:${V} = 1)[x]', r'Single "="'),
    ('"=" in a condition', '(if:${V}=1)[x]', r'Single "="'),
    ('"=" in a condition', '(unless:${V} = 1)[x]', r'Single "="'),
    ('"=" in a condition', '(if:${V} is 1 or ${V} = 2)[x]', r'Single "="'),
    ('"=" in a condition', '(if:${V} > 1)[a](else-if:${V} = 2)[b]', r'Single "="'),
    ('other broken condition', '(if:${V} => 1)[x]', r'"=>" or "=<"'),
    ('other broken condition', '(if:${V} =< 1)[x]', r'"=>" or "=<"'),
    ('other broken condition', '(if:${V} is)[x]', r'unfinished'),
    ('other broken condition', '(if:${V} >)[x]', r'unfinished'),
    ('other broken condition', '(if:)[x]', r'has no condition'),
    ('other broken condition', '(if:(${V} > 1)[x]', r'never closed with "\)"|no hook', LOOSE),
    ('variable typo', '(print:${VT})', r'\${VT} '),
    ('variable typo', 'Value: ${VT}.', r'\${VT} '),
    ('variable typo', '(if:${VT} > 1)[x]', r'\${VT} '),
    ('variable typo', '(set:${VT} to 1)', r'\${VT} '),
    ('variable typo', '(set:${V} to ${VT} + 1)', r'\${VT} '),
    ('variable typo', '(print:${VC})', r'\${VC} '),
    ('variable typo', '(set:_zqxtemp to 1)(print:_zqxtmep)', r'_zqxtmep '),
    ('broken HTML', '<span>x', r'<span> opened more', LOOSE),
    ('broken HTML', '<b>x', r'<b> opened more', LOOSE),
    ('broken HTML', '</span>', r'<span> closed more', LOOSE),
    ('broken HTML', '</div>', r'<div> closed more', LOOSE),
    ('broken HTML', '<img src="{IMG}>', r'unclosed quote|not found'),
    ('broken HTML', '<img src="">', r'Empty src'),
    ('broken HTML', '<a href="#zqxanchor">x</a>', r'#zqxanchor'),
    ('broken HTML', '<span>>x</span>', r'Stray ">"'),
    ('broken HTML', '<span id="zqxid">a</span><span id="zqxid">b</span>', r'id="zqxid"'),
    ('broken JavaScript', '<script>if (zqx { y();</script>', r'JavaScript brackets'),
    ('broken JavaScript', '<script>var zqx = "abc;</script>', r'JavaScript brackets'),
    ('broken JavaScript', '<script>}</script>', r'JavaScript brackets'),
    ('broken code in (set:)', '(set:${V} = 1)', r'uses "="'),
    ('broken code in (set:)', '(set:${V} to)', r'unfinished'),
    ('broken code in (set:)', '(set:${V})', r'no "to"'),
    ('broken code in (set:)', '(set:zqxname to 1)', r'has no "\$"'),
    ('broken code in (set:)', '(put:${V} into 1)', r'not a variable'),
    ('broken code in (set:)', '(print:${V} +)', r'unfinished'),
    ('Harlowe rules', '[[Go->]]', r'empty target'),
    ('Harlowe rules', '[[Go there', r'not closed with \]\]|Hook "\[" is never closed'),
    ('Harlowe rules', '(replace:?zqxhook)[x]', r'\?zqxhook'),
    ('Harlowe rules', '(click:?zqxhook)[x]', r'\?zqxhook'),
    ('Harlowe rules', '(show:?zqxhook)', r'\?zqxhook'),
    ('Harlowe rules', '(print:(passage:"{ZQ}")\'s name)', link_to()),
    ('Harlowe rules', '(visited:"{ZQ}")', link_to()),
]


class Harlowe(Game):
    def var_is_set(self, v):
        return any(re.search(r'\(set:[^)]*\$%s\s+to\b' % v, p.text) for p in self.sc.passages)

    def hook_spans(self, t):
        """The [ ] hooks of a scene, as (start, end, what opens it)."""
        out, stack = [], []
        text = re.sub(r'\[\[.*?\]\]', lambda m: ' ' * len(m.group()), t, flags=re.S)
        for m in re.finditer(r'[\[\]]', text):
            if m.group() == '[':
                stack.append(m.start())
            elif stack:
                out.append((stack.pop(), m.start()))
        return out

    def depth_at(self, text, off):
        return any(a < off < b for a, b in self.hook_spans(text))

    def macro_spans(self, t):
        return [(m.start(), twine_formats._match_paren(t, m.start())) for m in re.finditer(r'\([A-Za-z][\w-]*\s*:', t)]

    def line_start(self, p):
        t = p.raw_text
        spans = self.blocked(t) + [s for s in self.macro_spans(t) if s[1] > 0] + self.hook_spans(t)
        for m in re.finditer(r'^', t, re.M):
            if m.start() and self.is_free(spans, m.start()):
                return m.start()
        return None

    def opener(self, t, name_re):
        """The offset right after "(macro: ...)[" for the first such macro outside notes and scripts, or None."""
        spans = self.blocked(t)
        for m in re.finditer(r'\((?:%s)\s*:' % name_re, t):
            end = twine_formats._match_paren(t, m.start())
            if end > 0 and t[end:end + 1] == '[' and t[end:end + 2] != '[[' and self.is_free(spans, m.start()) \
                    and not self.depth_at(t, m.start()):
                return end + 1
        return None

    def find_spots(self):
        spots = dict(plain=[], branch=[], click=[], part=[], rare=[])
        indeg = self.in_degree()
        shown = {m for p in self.scenes for m in re.findall(r'\(display:\s*"([^"]+)"\)', p.text)}
        for p in self.scenes:
            t = p.raw_text
            if self.depth.get(p.name, 99) <= 5 and len(spots['plain']) < 8:
                off = self.line_start(p)
                if off is not None:
                    spots['plain'].append((p, off))
            if len(spots['branch']) < 12:
                off = self.opener(t, 'if|unless')
                if off is not None:
                    spots['branch'].append((p, off))
            if len(spots['click']) < 8:
                off = self.opener(t, 'link|link-reveal|link-repeat|click')
                if off is not None:
                    spots['click'].append((p, off))
            if p.name in shown and len(spots['part']) < 8:
                off = self.line_start(p)
                if off is not None:
                    spots['part'].append((p, off))
        for p in sorted(self.scenes, key=lambda q: (-self.depth.get(q.name, 0), q.name)):
            if indeg[p.name] == 1 and self.depth.get(p.name, 99) < 99 and len(spots['rare']) < 8:
                off = self.line_start(p)
                if off is not None:
                    spots['rare'].append((p, off))
        return {k: v for k, v in spots.items() if v}

    def mutations(self):
        out, want = [], collections.Counter()
        names, tok = self.names, self.tokens()

        def take(kind, p, a, b, new, expect, loose=False):
            spot = 'branch' if self.depth_at(p.raw_text, a) else 'plain'
            if want[kind, spot] >= 2:
                return
            want[kind, spot] += 1
            out.append(self.change(kind, f'real code changed: {p.raw_text[a:b][:50]!r} -> {new[:50]!r}', p, a, b, new, expect, spot, loose))
        for p in self.scenes:
            t = p.raw_text
            free = self.blocked(t)
            for m in re.finditer(r'\[\[[^\]\[]*?->([^\]\[]+)\]\]', t):
                tgt = m.group(1)
                if tgt in names and re.search('[a-z]{3}', tgt) and not re.search(r'\d', tgt) and self.is_free(free, m.start() + 1):
                    new = typo(tgt, names)
                    take('misspelled scene name', p, m.start(1), m.end(1), new, link_to(re.escape(new)))
            for m in re.finditer(r'\((?:go-to|goto|display):\s*"([^"]+)"\)', t):
                if m.group(1) in names and not re.search(r'\d', m.group(1)):
                    new = typo(m.group(1), names)
                    take('misspelled scene name', p, m.start(1), m.end(1), new, link_to(re.escape(new)))
            for m in re.finditer(r'<img\b[^<>]*?\bsrc="([\w./ -]+?)\.(jpe?g|png|gif|webp)"', t, re.I):
                if os.path.isfile(os.path.join(self.game_dir, f'{m.group(1)}.{m.group(2)}')):
                    take('missing picture', p, m.start(1), m.end(1), m.group(1) + 'zqx', miss_file(m.group(1) + 'zqx'))
                    new = m.group(1)[:-1] + m.group(1)[-1].swapcase()
                    if new != m.group(1):
                        take('wrong capital letters', p, m.start(1), m.end(1), new, 'wrong capital letters: ' + re.escape(new))
            for m in re.finditer(r'\((if|else-if|unless):\s*(\$\w+)\s+is\s+', t):
                take('"=" in a condition', p, m.start(), m.end(), f'({m.group(1)}: {m.group(2)} = ', r'Single "="')
            for m in re.finditer(r'\((if|set|print|display)\s*:', t):
                new = {'if': 'iff', 'set': 'sett'}.get(m.group(1), typo(m.group(1), twine_formats.HARLOWE_MACROS))
                take('misspelled macro', p, m.start(1), m.end(1), new, r'Unknown macro \(' + re.escape(new) + ':')
            for m in re.finditer(r'\$(' + re.escape(tok['{V}']) + r')\b', t):
                take('variable typo', p, m.start(1), m.end(1), tok['{VT}'], r'\$' + re.escape(tok['{VT}']) + ' ')
            for a, b in self.hook_spans(t):
                if self.is_free(free, a):
                    take('unclosed macro tag', p, b, b + 1, '', r'Hook "\[" is never closed|never opened', True)
                    break
        return out

    def game_wide(self):
        s = self.scenes[3]
        return [
            Plant(self.fmt, self.name, 'broken JavaScript', 'Story JavaScript: unclosed {', 'game', 'Story JavaScript', 0,
                  r'JavaScript brackets', js='\nwindow.zqxBroken = function () { if (1) {\n'),
            Plant(self.fmt, self.name, 'Harlowe rules', 'two scenes with the same name', 'game', s.name, 0,
                  r'name used 2 times', add=((s.name, '', 'A copy.'),)),
        ]

    def plants(self):
        if self.name == 'MB':
            return self.from_templates(HW_TEMPLATES, 2) + self.mutations() + self.game_wide()
        return self.from_templates(HW_TEMPLATES[::2], 1) + self.mutations()    # a second game, for the same picture


# ---------------------------------------------------------------------- small test games: the other formats
def lines_plant(fmt, game, path, kind, label, anchor, text, expect, where='', loose=False, replace=False):
    """Plant into a script file: the text goes on a new line after the first line holding `anchor`."""
    lines = _read(path).split('\n')
    n = next(i for i, l in enumerate(lines) if anchor in l)
    if replace:
        lines[n] = text
    else:
        lines[n + 1:n + 1] = text.split('\n')
    line = 0 if loose or '\n' in text else (n + 1 if replace else n + 2)
    return Plant(fmt, game, kind, label or text, 'file', where or os.path.basename(path), line, expect, {path: '\n'.join(lines)})


def passage_plants(fmt, game, path, scene, table):
    """Small Twine test games: plant at the start of a scene that holds no bugs of its own."""
    raw = _read(path)
    m = next(m for m in PASSAGE_RE.finditer(raw) if f'name="{scene}"' in m.group(1))
    body = html.unescape(m.group(2))
    out = []
    for kind, text, expect in table:
        new = raw[:m.start(2)] + html.escape(text + '\n' + body) + raw[m.end(2):]
        out.append(Plant(fmt, game, kind, text, 'file', scene, 0, expect, {path: new}))
    return out


def chapbook_plants():
    path = os.path.join(FIX_DIR, 'chapbook_bugs', 'game.html')
    return path, passage_plants('Chapbook', 'test game', path, 'Town', [
        ('broken link', "[[Go->%s]]" % ZQ, link_to(ZQ)),
        ('broken link', "{link to: '%s', label: 'Go'}" % ZQ, link_to(ZQ)),
        ('broken link', '{link to: "%s", label: "Go"}' % ZQ, link_to(ZQ)),
        ('broken link', "{reveal link: 'Go', passage: '%s'}" % ZQ, link_to(ZQ)),
        ('broken goto/include', "{embed passage: '%s'}" % ZQ, link_to(ZQ)),
        ('broken goto/include', "{embed passage named: '%s'}" % ZQ, link_to(ZQ)),
        ('misspelled scene name', "{link to: 'Strat', label: 'Go'}", link_to('Strat')),
        ('missing picture', f'<img src="images/{ZF}.jpg">', miss_file(f'images/{ZF}.jpg')),
        ('missing picture', f"{{embed image: 'images/{ZF}.jpg'}}", miss_file(f'images/{ZF}.jpg')),
        ('missing sound', f"{{ambient sound: 'audio/{ZF}.mp3'}}", miss_file(f'audio/{ZF}.mp3')),
        ('missing video', f'<video src="video/{ZF}.mp4"></video>', miss_file(f'video/{ZF}.mp4')),
        ('unclosed macro tag', '{embed passage', r'never closed'),
        ('stray macro tag', 'x }', r'never opened'),
        ('misspelled macro', "{lnk to: 'Start', label: 'Go'}", r'Unknown insert \{lnk to'),
        ('misspelled macro', "{embd passage: 'Start'}", r'Unknown insert \{embd passage'),
        ('misspelled macro', '[iff gold > 1]\nx\n[continue]', r'Unknown modifier \[iff'),
        ('"=" in a condition', '[if gold = 5]\nx\n[continue]', r'Single "="'),
        ('"=" in a condition', '[unless gold = 5]\nx\n[continue]', r'Single "="'),
        ('variable typo', '{glod}', r'glod is used but never set'),
        ('broken HTML', '<span>x', r'<span> opened more'),
        ('broken JavaScript', '<script>if (zqx { y();</script>', r'JavaScript brackets'),
        ('Chapbook rules', '[javascript]\nif (zqx {\n[continue]', r'Script code problem'),
    ])


def snowman_plants():
    path = os.path.join(FIX_DIR, 'snowman_bugs', 'game.html')
    return path, passage_plants('Snowman', 'test game', path, 'Town', [
        ('broken link', '[[Go->%s]]' % ZQ, link_to(ZQ)),
        ('broken link', '<a data-passage="%s">Go</a>' % ZQ, link_to(ZQ)),
        ('broken goto/include', '<%% story.show("%s") %%>' % ZQ, link_to(ZQ)),
        ('broken goto/include', "<%% window.story.show('%s') %%>" % ZQ, link_to(ZQ)),
        ('broken goto/include', '<%%= story.render("%s") %%>' % ZQ, link_to(ZQ)),
        ('broken goto/include', '<%% story.passage("%s") %%>' % ZQ, link_to(ZQ)),
        ('misspelled scene name', '<% story.show("Strat") %>', link_to('Strat')),
        ('missing picture', f'<img src="images/{ZF}.jpg">', miss_file(f'images/{ZF}.jpg')),
        ('missing sound', f'<audio src="audio/{ZF}.mp3"></audio>', miss_file(f'audio/{ZF}.mp3')),
        ('missing video', f'<video src="video/{ZF}.mp4"></video>', miss_file(f'video/{ZF}.mp4')),
        ('unclosed macro tag', '<% s.gold = 1;', r'Script tags do not match'),
        ('stray macro tag', 's.gold %>', r'Script tags do not match'),
        ('"=" in a condition', '<% if (s.gold = 5) { %>x<% } %>', r'Single "="'),
        ('variable typo', '<%= s.glod %>', r's\.glod is used but never set'),
        ('broken HTML', '<span>x', r'<span> opened more'),
        ('broken JavaScript', '<% if (s.gold { %>', r'Script code problem'),
        ('broken JavaScript', '<% var a = "abc; %>', r'Script code problem'),
        ('broken JavaScript', '<script>if (zqx { y();</script>', r'JavaScript brackets'),
    ])


def plain_plants():
    path = os.path.join(FIX_DIR, 'plain_html', 'index.html')
    a = '</body>'
    fmt, g = 'Plain HTML', 'test game'
    table = [
        ('missing picture', f'<img src="img/{ZF}.png">', miss_file(f'img/{ZF}.png')),
        ('missing picture', f"<img src='img/{ZF}.png'>", miss_file(f'img/{ZF}.png')),
        ('missing picture', f'<img src=img/{ZF}.png>', miss_file(f'img/{ZF}.png')),
        ('missing picture', f'<div style="background:url(img/{ZF}.png)"></div>', miss_file(f'img/{ZF}.png')),
        ('missing sound', f'<audio src="snd/{ZF}.mp3"></audio>', miss_file(f'snd/{ZF}.mp3')),
        ('missing video', f'<video src="vid/{ZF}.mp4"></video>', miss_file(f'vid/{ZF}.mp4')),
        ('wrong capital letters', '<img src="IMG/logo.png">', r'wrong capital letters'),
        ('broken link', f'<a href="#{ZF}">x</a>', '#' + ZF),
        ('broken link', f'<a href="{ZF}.html">x</a>', ZF + r'\.html'),
        ('broken link', f'<script src="{ZF}.js"></script>', ZF + r'\.js'),
        ('broken link', f'<link rel="stylesheet" href="{ZF}.css">', ZF + r'\.css'),
        ('broken HTML', '<span>x', r'<span> opened more'),
        ('broken HTML', '</div>', r'<div> closed more'),
        ('broken JavaScript', '<script>if (zqx { y();</script>', r'JavaScript brackets'),
        ('broken JavaScript', '<script>var zqx = "abc;</script>', r'JavaScript brackets'),
        ('other', '<div class="boxy">x</div>', r'"boxy"'),
    ]
    return path, [lines_plant(fmt, g, path, k, t, a, t, e, '(page)', replace=False) for k, t, e in
                  [(k, t, e) for k, t, e in table]]


def renpy_plants():
    d = os.path.join(FIX_DIR, 'renpy_bugs', 'game')
    path = os.path.join(d, 'script.rpy')
    fmt, g, a = "Ren'Py", 'test game', 'label lonely:'
    ifb = '    if gold > 1:\n        '
    table = [
        ('broken link', '    jump zqxlabel', r'Jump to missing label "zqxlabel"'),
        ('broken link', '    call zqxlabel', r'Call to missing label "zqxlabel"'),
        ('broken link', ifb + 'jump zqxlabel', r'Jump to missing label "zqxlabel"'),
        ('broken link', '    menu:\n        "Go":\n            jump zqxlabel', r'Jump to missing label "zqxlabel"'),
        ('misspelled scene name', '    jump lonley', r'Jump to missing label "lonley"'),
        ('misspelled scene name', '    call chapter_tow', r'Call to missing label "chapter_tow"'),
        ('broken link', '    call screen zqxscreen', r'Screen "zqxscreen" does not exist'),
        ('broken link', '    show screen zqxscreen', r'Screen "zqxscreen" does not exist'),
        ('missing picture', '    show zqximage', r'Image "zqximage" is not defined'),
        ('missing picture', '    scene zqximage room', r'Image "zqximage room" is not defined'),
        ('missing picture', '    show eilen happy', r'Image "eilen happy" is not defined'),
        ('missing picture', '    show eileen zqxsad', r'eileen zqxsad'),
        ('missing picture', f'    scene bg {ZF}', rf'bg {ZF}'),
        ('missing picture', f'image zqximg = "images/{ZF}.png"\nlabel zqxl:\n    show zqximg', miss_file(f'images/{ZF}.png')),
        ('missing picture', f'    add "images/{ZF}.png"', miss_file(f'images/{ZF}.png')),
        ('missing sound', f'    play music "audio/{ZF}.ogg"', miss_file(f'audio/{ZF}.ogg')),
        ('missing sound', f'    play sound "audio/{ZF}.ogg"', miss_file(f'audio/{ZF}.ogg')),
        ('missing sound', f'    voice "audio/{ZF}.ogg"', miss_file(f'audio/{ZF}.ogg')),
        ('missing sound', ifb + f'play music "audio/{ZF}.ogg"', miss_file(f'audio/{ZF}.ogg')),
        ('missing video', f'    $ renpy.movie_cutscene("video/{ZF}.webm")', miss_file(f'video/{ZF}.webm')),
        ('variable typo', '    if glod > 3:\n        pass', r'"glod" looks like a misspelling'),
        ('variable typo', '    e "You have [glod] gold."', r'"glod" looks like a misspelling'),
        ('variable typo', '    $ gold = glod + 1', r'"glod" looks like a misspelling'),
        ('variable typo', '    $ glod += 1', r'"glod" looks like a misspelling'),
        ("Ren'Py rules", '    if gold > 1\n        pass', r'"if" line is missing'),
        ("Ren'Py rules", '    while gold > 1\n        pass', r'"while" line is missing'),
        ("Ren'Py rules", '    menu\n        "Go":\n            pass', r'"menu" line is missing'),
        ("Ren'Py rules", '\tjump lonely', r'Tab character'),
        ("Ren'Py rules", 'label chapter_two:\n    return', r'defined twice'),
        ("Ren'Py rules", '    e "This text never ends', r'[Qq]uote'),
        ("Ren'Py rules", '    $ gold = (1 + 2', r'[Bb]racket'),
    ]
    return d, [lines_plant(fmt, g, path, k, t, a, t, e) for k, t, e in table]


def ink_plants():
    d = os.path.join(FIX_DIR, 'ink_bugs')
    path = os.path.join(d, 'story.ink')
    fmt, g, a = 'Ink', 'test game', '=== lonely ==='
    table = [
        ('broken link', '-> zqxknot', r'Divert to missing knot/stitch "zqxknot"'),
        ('broken link', '* [Go] -> zqxknot', r'Divert to missing knot/stitch "zqxknot"'),
        ('broken link', '{gold > 1: -> zqxknot}', r'Divert to missing knot/stitch "zqxknot"'),
        ('broken link', '<- zqxknot', r'zqxknot'),
        ('broken link', '-> zqxknot ->', r'Divert to missing knot/stitch "zqxknot"'),
        ('misspelled scene name', '-> lonley', r'Divert to missing knot/stitch "lonley"'),
        ('misspelled scene name', '-> extra.zqxstitch', r'extra\.zqxstitch'),
        ('variable typo', '~ glod = 3', r'Assignment to undeclared variable "glod"'),
        ('variable typo', '{glod}', r'"glod" looks like a misspelling'),
        ('variable typo', '{glod > 1: x}', r'"glod"'),
        ('variable typo', '* {glod > 1} [Go] -> END', r'"glod"'),
        ('variable typo', '~ gold = glod + 1', r'"glod"'),
        ('stray macro tag', 'x }', r'Extra "}"'),
        ('Ink rules', 'INCLUDE zqxfile.ink', r'Included file not found'),
        ('Ink rules', '=== town ===', r'defined twice'),
        ('Ink rules', '~ zqxfunc()', r'zqxfunc'),
    ]
    out = [lines_plant(fmt, g, path, k, t, a, t, e) for k, t, e in table]
    out.append(lines_plant(fmt, g, os.path.join(d, 'extra.ink'), 'unclosed macro tag', None, '=== extra ===', '{gold > 1: x', r'never closed'))
    return path, out


def choicescript_plants():
    d = os.path.join(FIX_DIR, 'choicescript_bugs')
    scenes = os.path.join(d, 'scenes')
    path = os.path.join(scenes, 'chapter1.txt')
    fmt, g, a = 'ChoiceScript', 'test game', '*label top'
    table = [
        ('broken link', '*goto zqxlabel', r'\*goto to missing label "zqxlabel"'),
        ('broken link', '*gosub zqxlabel', r'\*gosub to missing label "zqxlabel"'),
        ('broken link', '*goto_scene zqxscene', r'\*goto_scene to missing scene "zqxscene"'),
        ('broken link', '*gosub_scene zqxscene', r'\*gosub_scene to missing scene "zqxscene"'),
        ('broken link', '*goto_scene startup zqxlabel', r'missing label "zqxlabel"'),
        ('broken link', '*if gold > 1\n  *goto zqxlabel', r'\*goto to missing label "zqxlabel"'),
        ('misspelled scene name', '*goto_scene chaptr1', r'missing scene "chaptr1"'),
        ('misspelled macro', '*gotoo top', r'Unknown command \*gotoo'),
        ('misspelled macro', '*sett gold 1', r'Unknown command \*sett'),
        ('misspelled macro', '*iff gold > 1', r'Unknown command \*iff'),
        ('variable typo', '*set glod 1', r'\*set on undeclared variable "glod"'),
        ('variable typo', 'You have ${glod} coins.', r'\$\{glod\} is not a declared'),
        ('variable typo', 'You have @{glod one|two}.', r'glod'),
        ('variable typo', '*if glod > 1\n  x', r'"?glod'),
        ('variable typo', '*set gold glod', r'"?glod'),
        ('missing picture', f'*image {ZF}.png', miss_file(f'{ZF}.png')),
        ('missing sound', f'*sound {ZF}.mp3', miss_file(f'{ZF}.mp3')),
        ('ChoiceScript rules', '*label top', r'defined twice|top'),
        ('ChoiceScript rules', '*if (gold > 1\n  x', r'[Bb]racket'),
        ('ChoiceScript rules', '\t x', r'mixed'),
    ]
    return d, [lines_plant(fmt, g, path, k, t, a, t, e, 'chapter1') for k, t, e in table]


def rpg_plants():
    d = os.path.join(FIX_DIR, 'rpgmaker_bugs')
    path = os.path.join(d, 'data', 'Map001.json')
    common = os.path.join(d, 'data', 'CommonEvents.json')
    data, cdata = json.loads(_read(path)), json.loads(_read(common))
    fmt, g = 'RPG Maker', 'test game'

    def cmd(kind, code, params, expect, label):
        new = json.loads(json.dumps(data))
        new['events'][1]['pages'][0]['list'].append(dict(code=code, parameters=params))
        return Plant(fmt, g, kind, label, 'file', 'Map001.json', 0, expect, {path: json.dumps(new)})

    def ccmd(kind, code, params, expect, label):
        new = json.loads(json.dumps(cdata))
        new[1]['list'].append(dict(code=code, parameters=params))
        return Plant(fmt, g, kind, label + ' (common event)', 'file', 'CommonEvents.json', 0, expect, {common: json.dumps(new)})
    out = [
        cmd('missing picture', 231, [1, ZF], miss_file(f'img/pictures/{ZF}'), 'Show Picture'),
        cmd('missing picture', 101, [ZF, 0, 0, 2], miss_file(f'img/faces/{ZF}'), 'Message face'),
        ccmd('missing picture', 231, [1, ZF], miss_file(f'img/pictures/{ZF}'), 'Show Picture'),
        cmd('missing picture', 283, [ZF, ''], miss_file(f'img/battlebacks1/{ZF}'), 'Battle back'),
        cmd('missing picture', 284, [ZF], miss_file(f'img/parallaxes/{ZF}'), 'Parallax'),
        cmd('missing sound', 241, [dict(name=ZF)], miss_file(f'audio/bgm/{ZF}'), 'Play BGM'),
        cmd('missing sound', 245, [dict(name=ZF)], miss_file(f'audio/bgs/{ZF}'), 'Play BGS'),
        cmd('missing sound', 249, [dict(name=ZF)], miss_file(f'audio/me/{ZF}'), 'Play ME'),
        cmd('missing sound', 250, [dict(name=ZF)], miss_file(f'audio/se/{ZF}'), 'Play SE'),
        ccmd('missing sound', 250, [dict(name=ZF)], miss_file(f'audio/se/{ZF}'), 'Play SE'),
        cmd('missing video', 261, [ZF], rf'movies/{ZF}', 'Play Movie'),
        cmd('wrong capital letters', 241, [dict(name='theme1')], r'wrong capital letters: audio/bgm/theme1', 'BGM capitals'),
        cmd('wrong capital letters', 101, [dict and 'actor1', 0, 0, 2], r'wrong capital letters: img/faces/actor1', 'Face capitals'),
        cmd('broken link', 201, [0, 77, 1, 1, 0, 0], r'missing map #77', 'Transfer to missing map'),
        ccmd('broken link', 201, [0, 77, 1, 1, 0, 0], r'missing map #77', 'Transfer to missing map'),
        cmd('broken link', 117, [77], r'missing common event #77', 'Call missing common event'),
        cmd('variable typo', 121, [77, 77, 0], r'Switch #77 does not exist', 'Switch out of list'),
        cmd('variable typo', 122, [77, 77, 0, 0, 1], r'Variable #77 does not exist', 'Variable out of list'),
        cmd('variable typo', 111, [0, 77, 0], r'Switch #77 does not exist', 'Condition on missing switch'),
        cmd('variable typo', 111, [1, 77, 0, 0, 1, 0], r'Variable #77 does not exist', 'Condition on missing variable'),
        Plant(fmt, g, 'RPG Maker rules', 'Damaged map file', 'file', 'Map001.json', 0, r'Damaged data file',
              {path: _read(path)[:-5]}),
    ]
    plug = os.path.join(d, 'js', 'plugins.js')
    txt = _read(plug)
    i = txt.rfind('{"name"')
    out.append(Plant(fmt, g, 'RPG Maker rules', 'Plugin that is not there', 'file', 'plugins.js', 0, 'Plugin file missing: ZqxPlugin',
                     {plug: txt[:i] + '{"name":"ZqxPlugin","status":true,"description":"","parameters":{}},\n' + txt[i:]}))
    return d, out


# ---------------------------------------------------------------------- judging
def judge(pl, base, found):
    """How the scan reported this plant: 'found', 'note', or 'missed'. Also says when it was reported in the wrong place."""
    left = collections.Counter(fkey(f) for f in base)
    new = []
    for f in found:
        k = fkey(f)
        if left[k] > 0:
            left[k] -= 1
        else:
            new.append(f)
    hits = [f for f in new if re.search(pl.expect, f"{f['title']} | {f['snippet']}") and _right_place(pl, f)]
    if any(f['sev'] != scanner.INFO for f in hits):
        return 'found', new
    return ('note' if hits else 'missed'), new


def _right_place(pl, f):
    if not pl.where:
        return True
    named = pl.where in (f['passage'], f['file'])
    if not named:
        return False
    return not pl.line or f['line'] == pl.line


def report_line(pl, verdict, new):
    near = [f'{f["sev"][0]} {f["title"][:70]} @{f["passage"][:24]}:{f["line"]}' for f in new[:3]]
    return (f'    [{pl.fmt} / {pl.game} / {pl.kind} / {pl.spot}] {verdict}: {pl.label[:80]}  in "{pl.where[:30]}" line {pl.line}\n'
            + ''.join(f'         got: {n}\n' for n in near))


# ---------------------------------------------------------------------- Full scan: every sort of plant played once
def full_scan_part(game, plants):
    """One copy of the game holds one plant of each sort, each in a scene of its own; those scenes are played with and
    without the plants. Returns {plant label: True when playing showed something new in that scene}."""
    res = game.res
    names = set(fullscan.scene_names(res))
    used, chosen = set(), []
    labels = {}
    for pl in plants:
        labels.setdefault((pl.kind, pl.label), []).append(pl)
    for key in labels:
        if key[1].startswith('real code') or any(p.spot == 'game' for p in labels[key]):
            continue
        chosen.append(key)
    # Distinct scenes, each with a branch to plant in
    free = [(p, off) for p, off in game.all_branches() if p.name in names]
    tok = game.tokens()
    placed = []
    templates = {t[0:2]: t for t in (SC_TEMPLATES if game.fmt == 'SugarCube' else HW_TEMPLATES)}
    for key in chosen:
        while free and free[0][0].name in used:
            free.pop(0)
        if not free or key not in templates:
            continue
        p, off = free.pop(0)
        used.add(p.name)
        text = key[1]
        for k, v in tok.items():
            text = text.replace(k, v)
        placed.append((key, Plant(game.fmt, game.name, key[0], key[1], 'branch', p.name, 0, '',
                                  {p.name: p.raw_text[:off] + text + p.raw_text[off:]})))
    only = {pl.where for _, pl in placed}
    text = build_twine(_read(game.path), [pl for _, pl in placed])
    before, _ = fullscan.run(res, state={}, budget=PLAY_BUDGET, only=only)
    res2 = fast_scan(game.path, game.game_dir, {game.path: text})
    after, summ = fullscan.run(res2, state={}, budget=PLAY_BUDGET, only=only, patch=lambda _t: text)
    was = collections.Counter((i.passage, re.sub(r'\s+', ' ', i.title)) for i in before)
    out = {}
    for key, pl in placed:
        out[key] = False
        for i in after:
            k = (i.passage, re.sub(r'\s+', ' ', i.title))
            if i.passage == pl.where and not was[k] and i.category == 'Play-test':
                out[key] = True
                break
    return out, summ, len(placed)


def _branches_of(game):
    out = []
    for p in game.scenes:
        t = p.raw_text
        if game.fmt == 'SugarCube':
            m = re.search(r'<<if\b[^<>]*>>', t)
            if m and game.is_free(game.blocked(t), m.start() + 1) and not scanner.COMMENT_RE.search(t[:m.start()]) \
                    and not game.depth_at(t, m.start()):
                out.append((p, m.end()))
        else:
            off = game.opener(t, 'if|unless')
            if off is not None:
                out.append((p, off))
    return out


Game.all_branches = _branches_of


# ---------------------------------------------------------------------- running it all
def _pct(a, b):
    return f'{100 * a / b:.0f}%' if b else '-'


def run(play=True, misses=False, only=None, workers=None):
    """Prints the scores. Returns False when a score is below its floor or a game file changed."""
    t0 = time.time()
    results = []      # (plant, verdict, new findings)
    full_results = {}   # format -> {(kind, label): bool}
    full_note = {}
    workers = workers or max(1, min(3, (os.cpu_count() or 2) - 1))
    twine = [('SugarCube', 'Condemned Town', CT, SugarCube, True), ('Harlowe', 'MB', MB, Harlowe, True), ('Harlowe', 'TGP', TGP, Harlowe, False)]
    hashes = {}
    with concurrent.futures.ProcessPoolExecutor(workers) as pool:
        for fmt, name, path, cls, _ in twine:
            if only and fmt != only:
                continue
            if not os.path.isfile(path):
                print(f'  [skip] {name}: game not found')
                continue
            hashes[path] = hashlib.sha1(open(path, 'rb').read()).hexdigest()
            game = cls(fmt, name, path)
            plants = game.plants()
            found = pool.map(_scan_twine, [(path, game.game_dir, p) for p in plants])
            for pl, f in zip(plants, found):
                results.append((pl, *judge(pl, game.base, f)))
            print(f'  {name}: {len(plants)} plants, {time.time() - t0:.0f}s so far')
        for maker in (chapbook_plants, snowman_plants, plain_plants, renpy_plants, ink_plants, choicescript_plants, rpg_plants):
            target, plants = maker()
            if only and plants[0].fmt != only:
                continue
            game_dir = target if os.path.isdir(target) else os.path.dirname(target)
            base = findings(fast_scan(target, game_dir, {}))
            found = pool.map(_scan_files, [(target, game_dir, p) for p in plants])
            for pl, f in zip(plants, found):
                results.append((pl, *judge(pl, base, f)))
    if play and playtest.find_browser():
        for fmt, name, path, cls, playable in twine:
            if not playable or (only and fmt != only) or not os.path.isfile(path):
                continue
            t1 = time.time()
            game = cls(fmt, name, path)
            out, summ, n = full_scan_part(game, [r[0] for r in results if r[0].game == name])
            full_results[fmt] = out
            full_note[fmt] = f'{n} sorts of bug played in their own scenes, {time.time() - t1:.0f}s'
    return report(results, full_results, full_note, misses, hashes, t0)


def report(results, full_results, full_note, misses, hashes, t0):
    ok = True
    by_fmt = collections.defaultdict(list)
    for r in results:
        by_fmt[r[0].fmt].append(r)
    print('\n  Score = planted bugs reported as Broken or Worth checking, at the right place (note = only as a small note, not counted)')
    total, total_found, total_notes = 0, 0, 0
    for fmt, rows in by_fmt.items():
        n = len(rows)
        got = sum(1 for r in rows if r[1] == 'found')
        notes = sum(1 for r in rows if r[1] == 'note')
        total, total_found, total_notes = total + n, total_found + got, total_notes + notes
        games = ', '.join(sorted({r[0].game for r in rows}))
        print(f'\n  {fmt} ({games}): {got}/{n} found = {_pct(got, n)}' + (f', {notes} only as a note' if notes else ''))
        fr = full_results.get(fmt, {})
        kinds = collections.defaultdict(list)
        for r in rows:
            kinds[r[0].kind].append(r)
        for kind, kr in sorted(kinds.items(), key=lambda x: x[0]):
            g = sum(1 for r in kr if r[1] == 'found')
            nt = sum(1 for r in kr if r[1] == 'note')
            extra = ''
            if fr:
                played = {k: v for k, v in fr.items() if k[0] == kind}
                missed_labels = {r[0].label for r in kr if r[1] != 'found'}
                only_full = sum(1 for k, v in played.items() if v and k[1] in missed_labels)
                seen = sum(1 for v in played.values() if v)
                extra = f'   Full scan shows {seen}/{len(played)} sorts' + (f', {only_full} of them missed by Quick' if only_full else '')
            print(f'    {kind:<26} {g:>3}/{len(kr):<3} {_pct(g, len(kr)):>5}' + (f'  ({nt} note)' if nt else '') + extra)
        spots = collections.defaultdict(list)
        for r in rows:
            spots[r[0].spot].append(r)
        if len(spots) > 1:
            print('    by place: ' + ', '.join(f'{SPOT_LABEL[s]} {sum(1 for r in v if r[1] == "found")}/{len(v)}' for s, v in spots.items()))
        if fmt in full_note:
            print(f'    Full scan: {full_note[fmt]}')
            quick_missed = {r[0].label for r in rows if r[1] != 'found'}
            for k, seen in sorted(fr.items()):
                if k[1] in quick_missed:
                    print(f'      {"only the Full scan finds" if seen else "neither scan finds"}: {k[1][:70]}')
        floor = FLOOR.get(fmt)
        if floor is not None and 100 * got / n < floor:
            ok = False
            print(f'    FAIL: {100 * got / n:.1f}% is below the floor {floor}%')
    if total:
        share = 100 * total_found / total
        print(f'\n  Overall: {total_found}/{total} planted bugs found = {share:.1f}%, {total_notes} more only as a note, {time.time() - t0:.0f}s')
        if len(by_fmt) == len(FLOOR) and {'Condemned Town', 'MB', 'TGP'} <= {r[0].game for r in results} and share < FLOOR_ALL:
            ok = False
            print(f'  FAIL: below the floor {FLOOR_ALL}%')
    if misses:
        print('\n  Not reported by the Quick scan:')
        for pl, verdict, new in results:
            if verdict != 'found':
                print(report_line(pl, verdict, new), end='')
    for path, h in hashes.items():
        if hashlib.sha1(open(path, 'rb').read()).hexdigest() != h:
            ok = False
            print(f'  FAIL: THE GAME FILE WAS CHANGED: {path}')
    return ok


only = None
if __name__ == '__main__':
    if '--only' in sys.argv:
        only = sys.argv[sys.argv.index('--only') + 1]
    good = run(play='--no-play' not in sys.argv, misses='--misses' in sys.argv, only=only)
    print('\nRESULT:', 'PASS' if good else 'FAIL')
    sys.exit(0 if good else 1)
