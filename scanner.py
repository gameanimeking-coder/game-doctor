"""Game Doctor scan engine.

Scans an HTML game (Twine 2 / SugarCube focus, with general HTML checks for
everything else) plus its folder, and returns a list of Issue objects. It only
reads files; it never changes anything.
"""
import collections
import difflib
import html
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field

import knowledge as K

ERROR, WARNING, INFO = 'Error', 'Warning', 'Info'
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Issue:
    severity: str
    category: str
    title: str
    line: int = 0               # line in the HTML file (1-based, 0 = not tied to a line)
    passage: str = ''           # Twine passage name, if any
    snippet: str = ''           # the offending code
    what: str = ''              # explanation of the problem
    fix: str = ''               # how to fix it
    file: str = ''              # file the issue is in (defaults to the scanned HTML)
    # filled in by fixes.enrich():
    key: str = ''               # stable identity across rescans (line numbers left out)
    old: str = ''               # exact text on the line that should change ...
    new: str = ''               # ... and what it should become
    search: str = ''            # text to search for in an editor
    difficulty: str = ''        # 'easy' | 'medium' | 'dev'
    priority: int = 0           # higher = fix first
    advice: str = ''            # can it be ignored?
    rename: tuple = ()          # (wrong name, right name) when the fix is a rename used on several lines
    also_lines: list = field(default_factory=list)  # other lines with the same wrong name
    unreachable: bool = False   # in a scene players can't reach (shown as a note, not counted as broken)

    def as_dict(self):
        return self.__dict__.copy()


@dataclass
class Passage:
    pid: str
    name: str
    tags: list
    text: str                   # unescaped text, comments replaced by blank lines
    raw_text: str               # unescaped text including comments
    line: int                   # file line where the passage text starts


@dataclass
class ScanResult:
    issues: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    lines: list = field(default_factory=list)   # unescaped lines of the main file, for context view
    html_path: str = ''                          # main file (the HTML game, or the main script of other engines)
    game_dir: str = ''
    files: dict = field(default_factory=dict)    # other file path -> lines (engines with several script files)
    passages: dict = field(default_factory=dict)  # passage/scene name -> (first line, tags)
    passage_texts: dict = field(default_factory=dict)  # name -> markup text (offsets match link_edges)
    link_edges: list = field(default_factory=list)
    known_vars: set = field(default_factory=set)
    context: object = None                       # the scanner object, for testing single lines
    engine: str = 'html'
    raw_lines: list = field(default_factory=list)  # Twine games: file lines exactly as stored (text HTML-escaped)
    unreachable: set = field(default_factory=set)  # passages players can't reach

    def lines_of(self, path=''):
        if not path or os.path.normcase(path) == os.path.normcase(self.html_path):
            return self.lines
        return self.files.get(path, [])


def _short(s, n=240):
    s = s.strip().replace('\t', ' ')
    return s if len(s) <= n else s[:n] + ' …'


def _numbers_differ(a, b):
    """'chapter1' vs 'chaptr2', 'rank 3' vs 'rank 4': different numbers mean different things, not a typo."""
    na, nb = re.findall(r'\d+', a), re.findall(r'\d+', b)
    return (na or nb) and na != nb


def _close(word, candidates, n=1, cutoff=0.8):
    """Best near-match: exact case-insensitive first, then fuzzy."""
    lw = word.lower()
    for c in candidates:
        if c.lower() == lw and c != word:
            return c
    m = [c for c in difflib.get_close_matches(word, candidates, n=n + 3, cutoff=cutoff) if not _numbers_differ(word, c)]
    return m[0] if m else None


def _typo_of(word, candidates):
    """A candidate that `word` is very probably a misspelling of, or None."""
    lw = word.lower()
    for c in candidates:
        if c != word and c.lower() == lw:
            return c
    if len(word) >= 4:
        for c in candidates:
            if c != word and _one_edit_apart(lw, c.lower()) and not _numbers_differ(word, c):
                return c
    m = [c for c in difflib.get_close_matches(word, candidates, n=4, cutoff=0.9) if c != word and not _numbers_differ(word, c)]
    return m[0] if m else None


def _one_edit_apart(a, b):
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diffs = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diffs) == 1:
            return True
        return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def file_typo(wanted, real):
    """True when `real` is very probably the file `wanted` meant, typed slightly wrong: same name with
    another file type (.jpg/.webp), different spaces/underscores/capitals, or one wrong letter with the
    same numbers. Names that differ more are usually different pictures (bathroom2 vs bathroom, _win vs _you)."""
    a, b = [x.replace('\\', '/').rsplit('/', 1)[-1].lower() for x in (wanted, real)]
    sa, sb = a.rsplit('.', 1)[0], b.rsplit('.', 1)[0]
    if sa == sb:
        return True
    squash = lambda s: re.sub(r'[\s_\-.]+', '', s)
    if squash(sa) == squash(sb):
        return True
    if a.rsplit('.', 1)[-1] != b.rsplit('.', 1)[-1] or _numbers_differ(sa, sb):
        return False
    # one wrong letter inside one real word (4+ letters); "_a"/"_b", "v1"/"v2", "win"/"won" are versions, not typos
    ta, tb = re.split(r'[\s_\-.]+', sa), re.split(r'[\s_\-.]+', sb)
    diff = [(x, y) for x, y in zip(ta, tb) if x != y]
    return len(ta) == len(tb) and len(diff) == 1 and min(len(diff[0][0]), len(diff[0][1])) >= 4 \
        and _one_edit_apart(*diff[0]) and not re.search(r'\d', diff[0][0] + diff[0][1])


def _blank(m):
    """Replace matched text with the same number of newlines (keeps line numbers)."""
    return '\n' * m.group().count('\n')


COMMENT_RE = re.compile(r'/\*.*?\*/|<!--.*?-->|/%.*?%/', re.S)
SCRIPT_BODY_RE = re.compile(r'(<<script\b[^>]*>>)(.*?)(<</script>>|<<endscript>>)|(<script\b[^>]*>)(.*?)(</script>)', re.S | re.I)
DEAD = '\0dead branch'   # stands for "a link nobody can click" in the reachability check
QUOTED_NAME = r'''(?:"([^"]+)"|'([^']+)')'''   # "..." or '...', each may hold the other quote
MACRO_SCRIPT_TAG_RE = re.compile(r'<<script\b[^>]*>>|<</script>>|<<endscript>>', re.I)


class _ScriptMatch:
    """Looks like a SCRIPT_BODY_RE match; groups 1-3 = <<script>> open/body/close, 4-6 = <script> open/body/close."""
    def __init__(self, text, spans):
        self._text, self._spans = text, spans   # spans[i] = (start, end) of group i, or None

    def group(self, i=0):
        s = self._spans[i]
        return None if s is None else self._text[s[0]:s[1]]

    def start(self, i=0):
        s = self._spans[i]
        return -1 if s is None else s[0]

    def end(self, i=0):
        s = self._spans[i]
        return -1 if s is None else s[1]


def script_matches(text):
    """Script blocks in a passage. A <<script>> inside a <<script>> (e.g. in a text the script shows with
    Dialog.wiki) is counted the way SugarCube counts it, so the outer block ends at its own <</script>>."""
    out = []
    pos = 0
    while True:
        m = SCRIPT_BODY_RE.search(text, pos)
        if not m:
            return out
        if m.group(1) is None:   # <script>...</script>
            out.append(m)
            pos = m.end()
            continue
        depth, close = 0, None
        for t in MACRO_SCRIPT_TAG_RE.finditer(text, m.start()):
            depth += -1 if t.group().lower().startswith(('<</', '<<end')) else 1
            if depth == 0:
                close = t
                break
        if close is None:
            pos = m.end(1)   # never closed: left as it is, so the macro check reports "<<script>> is never closed"
            continue
        out.append(_ScriptMatch(text, [(m.start(), close.end()), m.span(1), (m.end(1), close.start()), close.span(),
                                       None, None, None]))
        pos = close.end()


STYLE_BODY_RE = re.compile(r'(<style\b[^>]*>)(.*?)(</style>)', re.S | re.I)
# a whole macro tag with every quote closed, read the way SugarCube reads it (">>" inside quotes is text)
QUOTED_ARGS = r'(?:"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\\n])*`|[^>"\'`]|>(?!>))*?'
MACRO_TAG_RE = re.compile(r'<<[A-Za-z][\w-]*' + QUOTED_ARGS + '>>')
# a whole HTML tag with every quoted value closed (">" inside a quoted value is text;
# a "<" inside one means the quote ran on into the next tag)
HTML_TAG_RE = re.compile(r'<[A-Za-z][\w-]*(?:\s(?:"[^"<]*"|\'[^\'<]*\'|[^"\'<>])*)?>')
TAG_END_RE = re.compile(r'''<<[A-Za-z][\w-]*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[^>"'`]|>(?!>))*?>>''')   # quoted text may span lines
UNFINISHED_RE = re.compile(r'(?:\b(?:is|isnot|eq|neq|gt|gte|lt|lte|and|or|not|to)|==|!=|<=|>=|<|>|&&|\|\||(?<![-+])[-+]|[*/%])\s*$')   # code cut off
VERBATIM_RE = re.compile(r'"""(.*?)"""|<nowiki>(.*?)</nowiki>|\{\{\{(.*?)\}\}\}', re.S)


class Scanner:
    def __init__(self, html_path, game_dir=None, progress=None, options=None):
        self.html_path = os.path.abspath(html_path)
        self.game_dir = os.path.abspath(game_dir or os.path.dirname(self.html_path))
        self.progress = progress or (lambda pct, msg: None)
        self.options = options or {}
        self.issues = []
        self.passages = []
        self.format = 'HTML'
        self.format_version = ''
        self.raw = ''
        self.lines = []
        self.js_text = ''          # all JavaScript found (story script, <script>, <<script>>)
        self.css_text = ''         # all CSS found
        self.widgets = {}          # name -> (is_container, passage)
        self.js_macros = {}        # name -> is_container
        self.passage_names = set()
        self.start_passage = ''
        self.link_edges = []       # (from passage, to passage, offset in from-passage text, kind)
        self.near_edges = []       # (from passage, passage a misspelled link probably means)
        self.known_vars = set()
        self.unreachable = set()   # passages players can't reach
        self.dead_widget_spans = collections.defaultdict(list)   # passage -> [(first line, last line, widget nothing calls)]

    # ------------------------------------------------------------------ helpers
    def add(self, severity, category, title, passage=None, offset=None, line=None, **kw):
        if line is None:
            line = 0
            if passage is not None and offset is not None:
                line = passage.line + passage.text.count('\n', 0, offset)
            elif passage is not None:
                line = passage.line
        pname = passage.name if isinstance(passage, Passage) else (passage or '')
        self.issues.append(Issue(severity, category, title, line, pname, **kw))

    def line_text(self, line):
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1]
        return ''

    def snippet_at(self, p, offset, length=200):
        text = p.text
        start = text.rfind('\n', 0, offset) + 1
        end = text.find('\n', offset)
        if end == -1:
            end = len(text)
        line = text[start:end]
        if len(line) > length:
            rel = offset - start
            a = max(0, rel - length // 3)
            line = ('… ' if a else '') + line[a:a + length] + ' …'
        return line.strip()

    # ------------------------------------------------------------------ main
    def run(self):
        t0 = time.time()
        self.progress(2, 'Reading file')
        with open(self.html_path, encoding='utf-8', errors='replace') as f:
            self.raw = f.read()
        self.lines = html.unescape(self.raw).split('\n')

        self.progress(6, 'Finding story data')
        self.parse()
        steps = [
            (12, 'Collecting scripts and styles', self.collect_code),
            (18, 'Checking passages and links', self.check_passages),
            (30, 'Checking macro tags', self.check_macros),
            (45, 'Checking conditions and code logic', self.check_logic),
            (55, 'Checking variables', self.check_variables),
            (65, 'Checking HTML tags', self.check_html),
            (72, 'Checking CSS classes', self.check_css_classes),
            (78, 'Checking images, audio, video and files', self.check_assets),
            (90, 'Checking text (typos, placeholders)', self.check_text),
            (94, 'Checking JavaScript', self.check_js),
            (97, 'Checking story-format rules', self.check_format),
        ]
        for pct, msg, fn in steps:
            self.progress(pct, msg)
            try:
                fn()
            except Exception as e:  # one broken check must not stop the scan
                self.add(INFO, 'Scanner', f'Check "{msg}" stopped early: {e}',
                         what='The scanner hit something it did not expect. Other results are still valid.')
        mark_dead_widgets(self.issues, self.dead_widget_spans)
        mark_unreachable(self.issues, self.unreachable)
        self.issues.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.category, i.line))
        self.progress(100, 'Done')
        res = ScanResult(self.issues, {}, self.lines, self.html_path, self.game_dir)
        counts = collections.Counter(i.severity for i in self.issues)
        res.stats = {
            'format': f'{self.format} {self.format_version}'.strip(),
            'passages': len(self.passages),
            'errors': counts[ERROR], 'warnings': counts[WARNING], 'infos': counts[INFO],
            'seconds': round(time.time() - t0, 2),
            'assets_referenced': getattr(self, '_asset_ref_count', 0),
            'files_in_folder': getattr(self, '_asset_file_count', 0),
        }
        res.stats['health'], res.stats['health_label'] = health_score(self.issues, len(self.passages))
        res.passages = {p.name: (p.line, p.tags) for p in self.passages}
        res.passage_texts = {p.name: self.markup_text(p) for p in self.story_passages()}
        res.link_edges = self.link_edges
        res.unreachable = self.unreachable
        res.known_vars = self.known_vars
        res.context = self
        res.engine = 'twine' if self.is_twine else 'html'
        if self.is_twine:
            res.raw_lines = self.raw.split('\n')
        return res

    def check_format(self):
        """Rules for Twine story formats other than SugarCube."""
        import twine_formats
        twine_formats.check(self)

    # ------------------------------------------------------------------ parsing
    def parse(self):
        raw = self.raw
        m = re.search(r'<tw-storydata\b([^>]*)>', raw)
        if m:
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
            self.format = attrs.get('format', 'Twine 2') or 'Twine 2'
            self.format_version = attrs.get('format-version', '')
            startnode = attrs.get('startnode', '')
            if not attrs.get('ifid'):
                self.add(INFO, 'Story setup', 'Story has no IFID',
                         what='Twine uses the IFID to tell stories apart (for saves and archives).',
                         fix='Open the story in Twine and re-publish it; Twine creates an IFID automatically.')
            for pm in re.finditer(r'<tw-passagedata\b([^>]*)>(.*?)</tw-passagedata>', raw, re.S):
                a = dict(re.findall(r'([\w-]+)="([^"]*)"', pm.group(1)))
                self._add_passage(a.get('pid', ''), html.unescape(a.get('name', '')),
                                  html.unescape(a.get('tags', '')).split(), pm.group(2), pm.start(2))
            if startnode:
                for p in self.passages:
                    if p.pid == startnode:
                        self.start_passage = p.name
                if not self.start_passage:
                    self.add(ERROR, 'Story setup', 'Start passage is missing',
                             what=f'The story says it starts at passage id {startnode}, but no passage has that id.',
                             fix='In Twine, right-click (or hover) the passage the game should start on and choose "Start Story Here".')
            # Story JavaScript / Stylesheet blocks
            # Twine stores these two as they are (not HTML-escaped like passages): unescaping them would turn
            # "&quot;" inside the script into a real quote and break it
            for sm in re.finditer(r'<script[^>]*id="twine-user-script"[^>]*>(.*?)</script>', raw, re.S):
                self.js_text += '\n' + sm.group(1)
            for sm in re.finditer(r'<style[^>]*id="twine-user-stylesheet"[^>]*>(.*?)</style>', raw, re.S):
                self.css_text += '\n' + sm.group(1)
        elif re.search(r'<div[^>]+tiddler="', raw):
            self.format = 'Twine 1'
            for pm in re.finditer(r'<div\b[^>]*tiddler="([^"]*)"([^>]*)>(.*?)</div>', raw, re.S):
                tags = re.search(r'tags="([^"]*)"', pm.group(2))
                body = pm.group(3).replace('\\n', '\n').replace('\\t', '\t').replace('\\s', '\\')
                self._add_passage('', html.unescape(pm.group(1)), (tags.group(1) if tags else '').split(),
                                  body, pm.start(3), escaped=True)
            self.start_passage = 'Start'
        else:
            self.format = 'HTML'
            # Treat the whole page as one "passage" so the general checks still run.
            body = raw
            self.passages.append(Passage('', '(page)', [], COMMENT_RE.sub(_blank, body), body, 1))
        names = collections.Counter(p.name for p in self.passages)
        for name, n in names.items():
            if n > 1 and name != '(page)':
                p = next(p for p in self.passages if p.name == name)
                self.add(ERROR, 'Passages', f'Passage name used {n} times: "{name}"', p,
                         what='Two or more passages share this name. Only one of them can ever be shown; links and includes always go to the same one.',
                         fix='Rename the extra passages (and update the links that should point to them).')
        self.passage_names = set(names)

    def _add_passage(self, pid, name, tags, raw_body, raw_pos, escaped=False):
        text = html.unescape(raw_body)
        # count lines on from the previous passage instead of from the top of the file (passages come in order)
        last_pos, last_line = getattr(self, '_line_mark', (0, 1))
        if raw_pos < last_pos:
            last_pos, last_line = 0, 1
        line = last_line + self.raw.count('\n', last_pos, raw_pos)
        self._line_mark = (raw_pos, line)
        clean = COMMENT_RE.sub(_blank, text) if 'script' not in tags and 'stylesheet' not in tags else text
        self.passages.append(Passage(pid, name, tags, clean, text, line))

    # ------------------------------------------------------------------ code collection
    def collect_code(self):
        for p in self.passages:
            if 'script' in p.tags:
                self.js_text += '\n' + p.raw_text
            elif 'stylesheet' in p.tags:
                self.css_text += '\n' + p.raw_text
            else:
                for m in script_matches(p.text):
                    self.js_text +='\n' + (m.group(2) or m.group(5) or '')
                for m in STYLE_BODY_RE.finditer(p.text):
                    self.css_text += '\n' + m.group(2)
        if self.format == 'HTML':
            pass  # page scripts were already included via the (page) passage above
        # external CSS files
        for m in re.finditer(r'<link\b[^>]*href=["\']([^"\']+\.css)["\']', self.raw, re.I):
            fp = os.path.join(self.game_dir, m.group(1).replace('\\', '/'))
            if os.path.isfile(fp):
                try:
                    self.css_text += '\n' + open(fp, encoding='utf-8', errors='replace').read()
                except OSError:
                    pass
        for m in re.finditer(r'<script\b[^>]*src=["\']([^"\']+\.js)["\']', self.raw, re.I):
            fp = os.path.join(self.game_dir, m.group(1).replace('\\', '/'))
            if os.path.isfile(fp):
                try:
                    self.js_text += '\n' + open(fp, encoding='utf-8', errors='replace').read()
                except OSError:
                    pass
        # Macros defined in JavaScript
        for m in re.finditer(r'Macro\.add\(\s*(\[[^\]]*\]|["\'][^"\']+["\'])\s*,(?=(.{0,600}))', self.js_text, re.S):   # look ahead: the next Macro.add may be close
            names = re.findall(r'["\']([^"\']+)["\']', m.group(1))
            container = bool(re.search(r'\btags\s*:', m.group(2)))
            for n in names:
                self.js_macros[n] = container
        # Widgets
        for p in self.passages:
            for m in re.finditer(r'<<widget\s+(?:["\']([^"\']+)["\']|([A-Za-z_][\w-]*))([^>]*)>>', p.text):   # name may be unquoted
                name, container = m.group(1) or m.group(2), 'container' in m.group(3)
                if name in self.widgets:
                    self.add(WARNING, 'Widgets', f'Widget "{name}" is defined twice', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what=f'"{name}" is also defined in passage "{self.widgets[name][1]}". The game uses only the one it loads last; '
                                  'that is a problem only if the two are different.',
                             fix='Delete or rename one of the two definitions.')
                self.widgets[name] = (container, p.name)
                if self.format.lower().startswith('sugarcube') and 'widget' not in p.tags:
                    self.add(ERROR, 'Widgets', f'Widget "{name}" is in a passage without the "widget" tag', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what='SugarCube only loads widgets from passages tagged "widget". This widget will not exist in the game.',
                             fix=f'Add the tag "widget" to passage "{p.name}" in Twine.')

    def story_passages(self):
        """Passages that hold normal story markup (not scripts or stylesheets)."""
        return [p for p in self.passages if 'script' not in p.tags and 'stylesheet' not in p.tags]

    def markup_text(self, p):
        """Passage text with script/style/verbatim bodies blanked out. Same length as p.text, so an offset
        in it points at the same place (line and snippet) in p.text."""
        t = p.text
        for m in reversed(script_matches(t)):
            g = 2 if m.group(2) is not None else 5
            t = t[:m.start(g)] + _blank_keep(m.group(g)) + t[m.end(g):]
        t = STYLE_BODY_RE.sub(lambda m: m.group(1) + _blank_keep(m.group(2)) + m.group(3), t)
        t = VERBATIM_RE.sub(lambda m: _blank_keep(m.group()), t)
        return t

    @property
    def is_sugarcube(self):
        return self.format.lower().startswith('sugarcube')

    @property
    def is_twine(self):
        return self.format != 'HTML'

    # ------------------------------------------------------------------ passages & links
    def check_passages(self):
        if not self.is_twine:
            self.check_anchor_links()
            return
        names = self.passage_names
        names_list = sorted(names)
        referenced = collections.Counter()
        dynamic_refs = 0

        def target_check(p, target, offset, how):
            nonlocal dynamic_refs
            t = target.strip()
            if not t:
                self.add(ERROR, 'Broken links', f'{how} has an empty target', p, offset,
                         snippet=self.snippet_at(p, offset),
                         what='The link or include does not name any passage.',
                         fix='Write the passage name to go to, e.g. [[Go home|Home]].')
                return
            if re.search(r'[$_]\w|\(|\)|`|\+|setup\.|previous\(|State\.', t) and t not in names:
                dynamic_refs += 1
                return
            if t in names:
                referenced[t] += 1
                self.link_edges.append((p.name, t, offset, how))
                return
            near = _close(t, names_list, cutoff=0.82)
            if near and re.sub(r'\d+', '#', near) == re.sub(r'\d+', '#', t) and near.lower() != t.lower():
                near = None  # "rank 4" vs "rank 3" are different passages, not a typo
            if near == p.name:
                near = None  # pointing a passage at itself would loop forever, never suggest it
            if near:
                referenced[near] += 1
                self.near_edges.append((p.name, near))
            if re.match(r'https?://|mailto:|#', t):
                return
            self.add(ERROR, 'Broken links', f'{how} to missing passage "{_short(t, 80)}"', p, offset,
                     snippet=self.snippet_at(p, offset),
                     what=f'There is no passage called "{t}". Clicking this link or reaching this include will show an error or a blank screen.',
                     fix=(f'Did you mean "{near}"? Change the name to match exactly (capital letters and spaces count).' if near
                          else 'Create a passage with this exact name, or point the link at an existing passage.'))

        for p in self.story_passages():
            t = self.markup_text(p)
            # [[links]] and [img[...][link]]
            code = [c.span() for c in re.finditer(r'<<(?:set|run|print|=|-|if|elseif|for|switch|case|unset)\b' + QUOTED_ARGS + '>>', t)]
            closed = []
            for m in re.finditer(r'\[\[(?!\[)(.*?)\]\]', t, re.S):   # "[[[" starts a link at its last "[["
                if any(a < m.start() < b for a, b in code):
                    continue   # [[0,1],[2,3]] inside <<set>> etc. is a JavaScript list, not a link
                closed.append(m.span())
                body = m.group(1)
                if '\n' in body or '[[' in body:
                    self.add(ERROR, 'Broken links', 'Link is not closed with ]] on the same line', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what='A [[link is missing its closing ]] (or has a line break inside), so the rest of the text is swallowed.',
                             fix='Add ]] at the end of the link text.')
                    continue
                body = re.sub(r'\]\[.*$', '', body)  # [[link][$x to 1]] setter
                if '|' in body:
                    target = body.rsplit('|', 1)[1]
                elif '->' in body:
                    target = body.rsplit('->', 1)[1]
                elif '<-' in body:
                    target = body.split('<-', 1)[0]
                else:
                    target = body
                target_check(p, target, m.start(), 'Link')
            if self.is_sugarcube or self.format == 'Twine 1':
                for m in re.finditer(r'\[\[(?!\[)', t):   # no "]]" anywhere after it
                    if not any(a <= m.start() < b for a, b in closed + code):
                        self.add(ERROR, 'Broken links', 'Link is not closed with ]] on the same line', p, m.start(),
                                 snippet=self.snippet_at(p, m.start()),
                                 what='A [[link is missing its closing ]], so the game shows the code as text instead of a link.',
                                 fix='Add ]] at the end of the link text.')
            for m in re.finditer(r'\[img\[[^\]]*\]\[([^\]]+)\]', t):
                target_check(p, m.group(1), m.start(), 'Image link')
            if self.is_sugarcube or self.format == 'Twine 1':
                for m in re.finditer(r'<<(goto|include|display)\s+(?:"([^"]*)"|\'([^\']*)\'|\[\[([^\]]*)\]\])', t):
                    tgt = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
                    if m.group(4) is not None and '|' in tgt:
                        tgt = tgt.rsplit('|', 1)[1]
                    target_check(p, tgt, m.start(), f'<<{m.group(1)}>>')
                for m in re.finditer(r'<<(link|button|linkreplace|linkappend|linkprepend|back|return|actions|choice|click)\s+(?:"[^"]*"|\'[^\']*\'|\[\[[^\]]*\]\])\s+(?:"([^"]*)"|\'([^\']*)\')', t):
                    tgt = m.group(2) if m.group(2) is not None else m.group(3)
                    if m.group(1) in ('linkreplace', 'linkappend', 'linkprepend'):
                        continue  # second argument is "transition"/"t8n", not a passage
                    target_check(p, tgt, m.start(), f'<<{m.group(1)}>>')
                for m in re.finditer(r'<<(link|button|back|return|click)\s+\[\[([^\]]*)\]\]', t):
                    b = m.group(2)
                    target_check(p, b.rsplit('|', 1)[1] if '|' in b else b, m.start(), f'<<{m.group(1)}>>')
            if self.is_sugarcube or self.format == 'Twine 1':
                for m in re.finditer(r'<<(?:textbox|numberbox)\s+(?:"[^"]*"|\'[^\']*\')\s+(?:"[^"]*"|\'[^\']*\'|[$_]?[\w.]+)\s+' + QUOTED_NAME, t):
                    if (m.group(1) or m.group(2)) != 'autofocus':   # the box's third value: the scene to go to
                        target_check(p, m.group(1) or m.group(2), m.start(), 'Input box')
                for sm in script_matches(p.text):
                    g = 2 if sm.group(2) is not None else 5
                    for m in re.finditer(r'\bEngine\.play\(\s*' + QUOTED_NAME, sm.group(g) or ''):
                        target_check(p, m.group(1) or m.group(2), sm.start(g) + m.start(), 'Script link')
            for m in re.finditer(r'data-passage\s*=\s*(?:"([^"]+)"|\'([^\']+)\')', t):   # "Justin's house" holds a '
                target_check(p, m.group(1) or m.group(2), m.start(), 'data-passage link')
            for m in re.finditer(r'\((?:link-goto|click-goto|goto|go-to|display|redirect|link-reveal-goto|mouseover-goto|mouseout-goto|visited|passage):'
                                 r'\s*(?:(?:"[^"]*"|\'[^\']*\'|\?[\w-]+)\s*,\s*)?' + QUOTED_NAME + r'\s*\)', t, re.I):
                target_check(p, m.group(1) or m.group(2), m.start(), 'Harlowe link')
            for m in re.finditer(r'(?:Engine\.play|Story\.get)\(\s*' + QUOTED_NAME, t):   # "Mandy's house" holds a '
                target_check(p, m.group(1) or m.group(2), m.start(), 'Script link')
            if self.format.lower().startswith('chapbook'):
                for m in re.finditer(r'\{(?:link to|back link|embed passage(?: named)?|restart link)\s*:?\s*' + QUOTED_NAME, t):
                    target_check(p, m.group(1) or m.group(2), m.start(), 'Chapbook link')
                for m in re.finditer(r'\{(?:reveal link|back link)[^{}]*?\bpassage\s*:\s*' + QUOTED_NAME, t):   # {reveal link: 'Go', passage: 'Name'}
                    target_check(p, m.group(1) or m.group(2), m.start(), 'Chapbook link')
            if self.format.lower().startswith('snowman'):
                for m in re.finditer(r'\b(?:story|s)\.(?:show|passage|render)\(\s*' + QUOTED_NAME, t):
                    target_check(p, m.group(1) or m.group(2), m.start(), 'Script link')
        # JS references to passages count as uses
        dom_call = re.compile(r'(?:\.(?:css|attr|prop|data|addClass|removeClass|toggleClass|hasClass|on|off|one|trigger|'
                              r'getAttribute|setAttribute|removeAttribute|querySelector|querySelectorAll|getElementById|'
                              r'getElementsByClassName|createElement|addEventListener|removeEventListener)|\bjQuery|\$)\(\s*$')
        for m in re.finditer(r'["\']([^"\'\n]{1,80})["\']', self.js_text):
            if m.group(1) in names and not dom_call.search(self.js_text, max(0, m.start() - 40), m.start()):
                referenced[m.group(1)] += 1   # (not .css("display", ...) when a passage happens to be called "display")

        # Unused passages
        maybe = (f' ({dynamic_refs} links are built from variables and could not be followed, so it might still be reachable.)'
                 if dynamic_refs else '')
        commented = self.links_in_comments(names)
        unused = set()
        for p in self.passages:
            if p.name in K.SPECIAL_PASSAGES or set(p.tags) & K.SPECIAL_TAGS or p.name == self.start_passage:
                continue
            if referenced[p.name] == 0 and p.name != '(page)':
                unused.add(p.name)
                if p.name in commented:
                    q, off = commented[p.name]
                    line = q.line + q.raw_text.count('\n', 0, off)
                    self.add(INFO, 'Unused passages', f'Passage "{p.name}" is only linked inside a hidden note', p.name, line=line,
                             snippet=_short(q.raw_text[q.raw_text.rfind('\n', 0, off) + 1:].split('\n', 1)[0]),
                             what=f'The only link to this passage is in "{q.name}" (line {line}), inside a hidden note /* ... */. '
                                  'Everything between /* and */ is switched off, so players can never reach this passage.' + maybe,
                             fix=f'If players should reach it, remove the /* and */ around the link in "{q.name}" (line {line}). '
                                 'If it was switched off on purpose, leave it.')
                else:
                    self.add(INFO, 'Unused passages', f'Passage "{p.name}" is never linked or included', p,
                             what='Nothing in the game links to, includes or jumps to this passage, so players can never see it.' + maybe,
                             fix='If the passage is needed, add a link to it; if it is left over, it can be deleted.')
            if not p.raw_text.strip() and not set(p.tags) & K.SPECIAL_TAGS:
                self.add(WARNING if referenced[p.name] else INFO, 'Passages', f'Passage "{p.name}" is empty', p,
                         what='The passage has no content. Players who reach it see a blank page.',
                         fix='Write the passage content, or remove the links to it.')
        self.unreachable = self.find_unreachable(unused, referenced)
        for name in sorted(self.unreachable - unused):
            p = next(x for x in self.passages if x.name == name)
            via = sorted({f for f, t, *_ in self.link_edges if t == name})
            self.add(INFO, 'Unused passages', f'Passage "{name}" is only linked from scenes players can\'t reach', p,
                     what='The only links to this passage are in passages players can\'t reach ('
                          + ', '.join(f'"{v}"' for v in via[:5]) + (' …' if len(via) > 5 else '')
                          + '), so players can never see it either.' + maybe,
                     fix='If the passage is needed, add a link to it from a scene players can reach; if it is left over, it can be deleted.')
        self.check_anchor_links()

    def links_in_comments(self, names):
        """Passage name -> (passage, offset) of the first link to it inside a /* */ or <!-- --> note."""
        found = {}
        for q in self.passages:
            for c in COMMENT_RE.finditer(q.raw_text):
                body = c.group()
                for m in re.finditer(r'\[\[([^\]]*)\]\]|["\']([^"\'\n]{1,80})["\']', body):
                    t = m.group(2)
                    if t is None:
                        t = m.group(1)
                        t = t.rsplit('|', 1)[1] if '|' in t else t.rsplit('->', 1)[1] if '->' in t else t.split('<-', 1)[0]
                    t = t.strip()
                    if t in names and t != q.name and t not in found:
                        found[t] = (q, c.start() + m.start())
        return found

    def find_unreachable(self, unused, referenced):
        """`unused` plus passages whose every link comes from an unreachable passage (scripts count as
        reachable, so nothing is hidden on a guess; a misspelled link counts as a link to the passage it
        probably means)."""
        dead = self.dead_branches()
        into = collections.defaultdict(list)
        for f, t, off, *_ in self.link_edges:
            # a link inside <<if $devMode>> where nothing ever sets $devMode can never be clicked
            into[t].append(DEAD if any(a <= off < b for a, b in dead.get(f, ())) else f)
        for f, t in self.near_edges:
            into[t].append(f)
        # Start from every passage reached only by plain links, then keep removing those with a link from
        # outside the group. What stays can't be reached, including groups that only link to each other
        # (e.g. a cheat menu and its pages that nothing else links to).
        out = set(unused) | {DEAD}
        for p in self.passages:
            n = p.name
            if n in out or n in K.SPECIAL_PASSAGES or set(p.tags) & K.SPECIAL_TAGS or n == self.start_passage:
                continue
            src = into.get(n)
            if src and len(src) == referenced[n]:
                out.add(n)
        changed = True
        while changed:
            changed = False
            for n in list(out - unused):
                if any(f not in out for f in into[n]):
                    out.discard(n)
                    changed = True
        return out - {DEAD}

    def dead_branches(self):
        """Passage -> [(start, end)] of <<if $x>> / <<if $x is true>> blocks where $x is never set: the game only
        ever checks it (its <<set>> commented out, a developer switch), so nothing in the block can run.
        Careful on purpose: a variable named anywhere else (scripts, settings, setter links) counts as set."""
        if not (self.is_sugarcube or self.format == 'Twine 1'):
            return {}
        texts = [(p, self.markup_text(p)) for p in self.passages if 'stylesheet' not in p.tags]
        cond_re = re.compile(r'<<(?:else\s*)?if\b(' + QUOTED_ARGS + ')>>')
        total, in_cond = collections.Counter(), collections.Counter()
        for p, t in texts:
            total.update(re.findall(r'\$([A-Za-z_]\w*)', t))
            for c in cond_re.finditer(t):
                in_cond.update(re.findall(r'\$([A-Za-z_]\w*)', c.group(1)))
        never = {v for v in total if total[v] == in_cond[v] and not re.search(r'\b%s\b' % re.escape(v), self.js_text)}
        spans = collections.defaultdict(list)
        if not never:
            return spans
        for p, t in texts:
            for m in re.finditer(r'<<if\s+\$([A-Za-z_]\w*)(?:\s*(?:==|===|\bis\b|\beq\b)\s*true)?\s*>>', t):
                if m.group(1) not in never:
                    continue
                depth = 0
                for tag in re.finditer(r'<<(/?)(if|else|elseif|endif)\b', t[m.end():]):
                    closing = tag.group(1) or tag.group(2) == 'endif'
                    if tag.group(2) == 'if' and not closing:
                        depth += 1
                    elif closing and depth:
                        depth -= 1
                    elif not depth:   # <<else>>, <<elseif>> or the <</if>> of this block
                        spans[p.name].append((m.end(), m.end() + tag.start()))
                        break
        return spans

    def check_anchor_links(self):
        ids = set(re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', self.raw))
        ids |= set(re.findall(r'\bname\s*=\s*["\']([^"\']+)["\']', self.raw))
        for p in self.story_passages():
            for m in re.finditer(r'href\s*=\s*["\']#([^"\']+)["\']', p.text):
                if m.group(1) not in ids and '$' not in m.group(1):
                    self.add(WARNING, 'Broken links', f'Link to missing page anchor "#{m.group(1)}"', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what='No element on the page has this id, so the link goes nowhere.',
                             fix=f'Add id="{m.group(1)}" to the element it should jump to, or fix the spelling.')

    # ------------------------------------------------------------------ macros
    def check_macros(self):
        if not (self.is_sugarcube or self.format == 'Twine 1'):
            return
        known = set(K.SUGARCUBE_MACROS) | set(self.widgets) | set(self.js_macros)
        containers = dict(K.SUGARCUBE_CONTAINERS)
        for w, (is_c, _) in self.widgets.items():
            if is_c:
                containers.setdefault(w, set())
        for n, is_c in self.js_macros.items():
            if is_c:
                containers.setdefault(n, set())
        known_list = sorted(known)
        unknown_seen = collections.defaultdict(list)
        widget_used = collections.Counter()

        for p in self.story_passages():
            t = self.markup_text(p)
            stack, else_seen = [], set()
            for m in re.finditer(r'<<(/?)\s*([A-Za-z][\w-]*|=|-)', t):
                closing, name = m.group(1) == '/', m.group(2)
                if name.startswith('end') and name[3:] in containers:
                    closing, name = True, name[3:]
                if name in self.widgets:
                    widget_used[name] += 1
                if name not in known and not closing:
                    unknown_seen[name].append((p, m.start()))
                    continue
                if name in K.CHILD_TAGS and not closing:
                    parent = K.CHILD_TAGS[name]
                    if not stack or stack[-1][0] not in parent.split('/'):
                        self.add(ERROR, 'Macro tags', f'<<{name}>> is outside its <<{parent}>>', p, m.start(),
                                 snippet=self.snippet_at(p, m.start()),
                                 what=f'<<{name}>> only works between <<{parent}>> and <</{parent.split("/")[0]}>>. Here it is not inside one (often because a closing tag above it is misplaced). The game shows an error.',
                                 fix=f'Check the <<{parent}>> above: make sure it is opened before this <<{name}>> and closed after it.')
                    if name in ('else', 'elseif') and stack and stack[-1][0] == 'if':
                        if stack[-1][1] in else_seen:
                            self.add(ERROR, 'Macro tags', f'<<{name}>> comes after <<else>>', p, m.start(),
                                     snippet=self.snippet_at(p, m.start()),
                                     what='<<else>> must be the last part of an <<if>>. The game shows the error "<<else>> must be the final clause".',
                                     fix=f'Move <<else>> to the end of the <<if>>, or change this <<{name}>>.')
                        if name == 'else':
                            else_seen.add(stack[-1][1])
                            args = t[m.end():t.find('>>', m.end())].strip()
                            if args:
                                wrote_if = re.match(r'if\b', args)
                                self.add(ERROR, 'Macro tags', '<<else if>> must be written <<elseif>>' if wrote_if else '<<else>> cannot have a condition',
                                         p, m.start(), snippet=self.snippet_at(p, m.start()),
                                         what='<<else>> takes no condition, so the game shows an error here.',
                                         fix='Write <<elseif condition>>' + ('.' if wrote_if else ', or remove the condition.'))
                    continue
                if name not in containers:
                    if closing and name in known:
                        self.add(ERROR, 'Macro tags', f'<</{name}>> closes a macro that has no closing tag', p, m.start(),
                                 snippet=self.snippet_at(p, m.start()),
                                 what=f'<<{name}>> is not a container macro, so <</{name}>> does not mean anything and shows an error.',
                                 fix=f'Delete <</{name}>>.')
                    continue
                if not closing:
                    stack.append((name, m.start()))
                    continue
                if stack and stack[-1][0] == name:
                    stack.pop()
                    continue
                if any(s[0] == name for s in stack):
                    # something in between was never closed
                    while stack and stack[-1][0] != name:
                        oname, ooff = stack.pop()
                        self.add(ERROR, 'Macro tags', f'<<{oname}>> is never closed', p, ooff,
                                 snippet=self.snippet_at(p, ooff),
                                 what=f'This <<{oname}>> has no matching <</{oname}>> before the <</{name}>> further down. The game shows "cannot find a closing tag".',
                                 fix=f'Add <</{oname}>> where this block should end (before the <</{name}>> on line {p.line + t.count(chr(10), 0, m.start())}).')
                    stack.pop()
                else:
                    self.add(ERROR, 'Macro tags', f'<</{name}>> has no opening <<{name}>>', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what=f'There is no open <<{name}>> for this closing tag to close. The game shows an error here.',
                             fix=f'Delete this extra <</{name}>>, or add the missing <<{name}>> above it.')
            for name, off in stack:
                self.add(ERROR, 'Macro tags', f'<<{name}>> is never closed', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'This <<{name}>> has no matching <</{name}>> in the passage. The game shows "cannot find a closing tag for macro <<{name}>>".',
                         fix=f'Add <</{name}>> where the block should end.')

            # Malformed tags
            for m in re.finditer(r'<<\s*/\s*([A-Za-z][\w-]*)\s*>(?!>)', t):
                self.add(ERROR, 'Macro tags', f'Closing tag <</{m.group(1)}> is missing a ">"', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='The closing tag ends with a single ">" instead of ">>", so the game does not see it.',
                         fix=f'Change <</{m.group(1)}> to <</{m.group(1)}>>.')
            for m in re.finditer(r'(<<[A-Za-z/][^<>\n]*?>>)(>+)', t):
                if re.search(r'[=<>]\s*>>>$', m.group(0)):  # e.g. <<if $a >>>  is ambiguous; still report
                    pass
                self.add(WARNING, 'Macro tags', 'Extra ">" after a macro tag', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what=f'"{_short(m.group(0), 80)}" has {len(m.group(2))} extra ">" that will show up as text on screen.',
                         fix=f'Remove the extra ">" so the tag ends with exactly ">>".')
            for m in re.finditer(r'<<<+(?=[A-Za-z/])', t):
                self.add(WARNING, 'Macro tags', 'Extra "<" before a macro tag', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='There is one "<" too many before the macro; it shows up as text.',
                         fix='Remove the extra "<" so the tag starts with exactly "<<".')
            for m in re.finditer(r'<<(?=\s*(?:\n|$|<<))', t):
                self.add(ERROR, 'Macro tags', 'Stray "<<" with no macro name', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='"<<" starts a macro, but nothing follows it. This breaks the macro after it.',
                         fix='Delete the stray "<<", or finish writing the macro.')
            not_closed = set()
            for m in re.finditer(r'<<([A-Za-z][\w-]*)\b([^<>]*?)(?:\n\s*\n|$)', t):
                seg = m.group(2)
                if '>>' not in seg and m.group(1) not in ('script', 'set', 'run', 'print', 'if', 'elseif', 'for', 'switch', 'case'):
                    nxt = t[m.end(): m.end() + 1]
                    not_closed.add(m.start())
                    self.add(ERROR, 'Macro tags', f'<<{m.group(1)} ...> is not closed with ">>"', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what='The macro tag never ends with ">>", so it eats the text after it.',
                             fix='Add ">>" at the end of the macro tag.')

            # a tag that runs on into the next macro, or to the end of the scene: <<print 1>  (">" for ">>")
            for m in re.finditer(r'<<([A-Za-z][\w-]*)\b', t):
                if m.group(1) not in K.SUGARCUBE_MACROS or m.start() in not_closed:   # a game's own macros may hold free text
                    continue
                tag = TAG_END_RE.match(t, m.start())
                rest = t[m.end():].split('\n', 1)[0] if tag is None else ''
                if tag is None and (rest.count('"') % 2 or rest.count("'") % 2):
                    continue   # an open quote: reported as "Unclosed quote"
                if tag is None or '<<' in re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '', tag.group()[2:]):
                    self.add(ERROR, 'Macro tags', f'<<{m.group(1)} ...> is not closed with ">>"', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what='The macro tag never ends with ">>", so it eats the text after it and the game shows an error.',
                             fix='Add ">>" at the end of the macro tag.')

            # Deprecated macros
            for m in re.finditer(r'<<(click|display|remember|actions|choice|setplaylist|stopallaudio)\b', t):
                if m.group(1) in self.widgets or m.group(1) in self.js_macros:
                    continue
                self.add(INFO, 'Outdated code', f'<<{m.group(1)}>> is an old SugarCube macro', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='This macro is deprecated and may be removed in a future SugarCube version.',
                         fix=K.DEPRECATED_MACROS[m.group(1)])
            for m in re.finditer(r'\$args\b|\bstate\.active\b|\bstate\.history\b', t):
                self.add(INFO, 'Outdated code', f'"{m.group()}" is old SugarCube 1 syntax', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='This still works for now but is deprecated.',
                         fix='Use _args instead of $args (inside widgets) and State instead of state.')

        for name, places in unknown_seen.items():
            near = _close(name, known_list, cutoff=0.75)
            for p, off in places[:50]:
                self.add(ERROR if near else WARNING, 'Unknown macros', f'Unknown macro <<{name}>>', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'SugarCube has no macro, widget or custom macro called "{name}". The game shows "macro <<{name}>> does not exist".'
                              + (f' It is very close to <<{near}>>.' if near else ''),
                         fix=(f'Change it to <<{near}>>.' if near else 'Check the spelling, or define it as a widget / custom macro.'))
            if len(places) > 50:
                self.add(WARNING, 'Unknown macros', f'<<{name}>> used {len(places) - 50} more times', places[50][0], places[50][1],
                         what='Only the first 50 uses are listed.', fix='Fix the spelling everywhere it is used.')
        unused = {name for name in self.widgets if widget_used[name] == 0 and name not in self.js_text}
        for name in unused:
            self.add(INFO, 'Widgets', f'Widget <<{name}>> is never used', line=0, passage=self.widgets[name][1],
                     what='It is defined but no passage calls it.',
                     fix='Remove it if it is left over.')
        # Code inside a widget that nothing calls never runs, so its problems are not certain to show up
        # (see mark_dead_widgets, which runs after all checks).
        spans = self.dead_widget_spans
        for p in self.story_passages():
            if 'widget' not in p.tags:
                continue
            t = self.markup_text(p)
            for m in re.finditer(r'<<widget\s+["\']?([\w-]+)["\']?[^>]*>>(.*?)(?:<</widget>>|<<endwidget>>|$)', t, re.S):
                if m.group(1) in unused:
                    spans[p.name].append((p.line + t.count('\n', 0, m.start()), p.line + t.count('\n', 0, m.end()), m.group(1)))

    # ------------------------------------------------------------------ logic
    def check_logic(self):
        if not (self.is_sugarcube or self.format == 'Twine 1'):
            return
        for p in self.story_passages():
            t = self.markup_text(p)
            for m in re.finditer(r'<<(if|elseif)\b(' + QUOTED_ARGS + r')>>|<<(if|elseif)\b(.*?)>>', t, re.S):   # ">>" in quotes is text
                expr, kind = (m.group(2), m.group(1)) if m.group(1) else (m.group(4), m.group(3))
                e = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', expr)
                snip = self.snippet_at(p, m.start())
                if not e.strip():
                    self.add(ERROR, 'Conditions', f'<<{kind}>> has no condition', p, m.start(), snippet=snip,
                             what='The condition is empty; SugarCube shows an error.',
                             fix='Write the condition, e.g. <<if $money gte 10>>.')
                    continue
                op = re.search(r'([-+*/%]?)(?<![=!<>])=(?![=>])', e)
                if op and op.group(1):
                    self.add(ERROR, 'Conditions', f'"{op.group(1)}=" inside a condition', p, m.start(), snippet=snip,
                             what=f'"{op.group(1)}=" changes the variable instead of comparing it, so the condition is almost always true '
                                  'and the value changes every time this line runs.',
                             fix='Compare instead, e.g. <<if $money gte 125>>.')
                elif op:
                    self.add(ERROR, 'Conditions', 'Single "=" inside a condition', p, m.start(), snippet=snip,
                             what='"=" means "set to", not "is equal to". SugarCube shows the error "assignment operator found within <<if>> clause" and the condition does not work.',
                             fix='Use "is" or "==" to compare, e.g. <<if _x is 1>>.')
                if re.search(r'=>|=<', re.sub(r'[(,]\s*(?:[A-Za-z]\w*|\([^()]*\))\s*=>', '', e)):   # not JS "item => ..." functions
                    self.add(ERROR, 'Conditions', 'Wrong comparison operator "=>" or "=<"', p, m.start(), snippet=snip,
                             what='"=>" and "=<" are not comparisons in JavaScript ("=>" makes a function). The condition will not work as intended.',
                             fix='Use ">=" (or gte) and "<=" (or lte).')
                if re.search(r'\bis\s+not\b', e):
                    self.add(WARNING, 'Conditions', '"is not" instead of "isnot"', p, m.start(), snippet=snip,
                             what='In SugarCube "$a is not 5" means "$a is equal to (not 5)", which is almost always false.',
                             fix='Use "isnot" (one word) or "!==".')
                for mm in re.finditer(r'(\$\w+(?:\.\w+)*)\s+(?:is|==|===|eq)\s+([^\s&|()]+)\s+(?:or|\|\|)\s+(["\d][^\s&|()]*|true|false)(?=\s*(?:$|\)|&&|\|\||and\b|or\b))', e):
                    self.add(WARNING, 'Conditions', f'"{mm.group(1)} is {mm.group(2)} or {mm.group(3)}" does not compare both', p, m.start(), snippet=snip,
                             what=f'The part after "or" is checked on its own, and "{mm.group(3)}" is always true, so the whole condition is always true.',
                             fix=f'Repeat the variable: {mm.group(1)} is {mm.group(2)} or {mm.group(1)} is {mm.group(3)}.')
                if UNFINISHED_RE.search(e):
                    self.add(ERROR, 'Conditions', 'Condition is unfinished', p, m.start(), snippet=snip,
                             what='The condition ends in the middle: nothing follows the last word or sign. SugarCube shows an error.',
                             fix='Finish the comparison, e.g. <<if $money gte 10>>.')
                ops, depth = _top_level_ops(e)
                if depth != 0:
                    self.add(ERROR, 'Conditions', 'Brackets do not match in condition', p, m.start(), snippet=snip,
                             what=f'There are {"more ( than )" if depth > 0 else "more ) than ("} in this condition. SugarCube shows an error.',
                             fix='Add or remove brackets so every ( has a matching ).')
                elif ops == {'and', 'or'}:
                    self.add(WARNING, 'Conditions', 'Condition mixes "and" and "or" without brackets', p, m.start(), snippet=snip,
                             what='"and" is checked before "or", so "A and B or C" means "(A and B) or C". The scene can show when it should not '
                                  '(for example when a character is kidnapped or a stat is too low).',
                             fix='Add brackets to group the parts that belong together, e.g. A and (B or C).')
            for m in re.finditer(r'<<set\b(' + QUOTED_ARGS + r')>>|<<set\b(.*?)>>', t, re.S):   # ">>" in quotes is text
                body = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', m.group(1) if m.group(1) is not None else m.group(2))
                snip = self.snippet_at(p, m.start())
                if not body.strip():
                    self.add(ERROR, 'Code', '<<set>> with nothing to set', p, m.start(), snippet=snip,
                             what='The macro is empty and shows an error.', fix='Delete it or write e.g. <<set $x to 1>>.')
                    continue
                # a function call or an if (...) {...} can change things too: <<set if (x === 0) { showNotice(); }>>
                if not re.search(r'\bto\b|(?<![=!<>])=(?!=)|\+=|-=|\*=|/=|%=|\+\+|--|\w\s*\(|\bdelete\b|\bif\b', body):
                    if re.search(r'==|\bis\b|\beq\b', body):
                        self.add(ERROR, 'Code', '<<set>> compares instead of setting', p, m.start(), snippet=snip,
                                 what='"is" / "==" compares values; inside <<set>> nothing is changed.',
                                 fix='Use "to" or "=", e.g. <<set $x to 5>>.')
                    else:
                        self.add(WARNING, 'Code', '<<set>> does not change anything', p, m.start(), snippet=snip,
                                 what='There is no "to", "=", "+=", "++" etc. in this <<set>>, so no variable changes.',
                                 fix='Write what the variable should become, e.g. <<set $x to 5>> or <<set $x++>>.')
                if UNFINISHED_RE.search(body):
                    self.add(ERROR, 'Code', '<<set>> is unfinished', p, m.start(), snippet=snip,
                             what='The code ends in the middle: nothing follows the last word or sign. SugarCube shows an error.',
                             fix='Finish it, e.g. <<set $x to 5>>.')
                if '<<' not in body and _brackets_off(body):
                    self.add(ERROR, 'Code', 'Brackets do not match in <<set>>', p, m.start(), snippet=snip,
                             what='Every (, [ and { needs a partner. SugarCube shows an error.', fix='Add or remove brackets.')
            for m in re.finditer(r'<<(run|print|=|-)(?=[\s(])(' + QUOTED_ARGS + r')>>', t):
                body = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', m.group(2))
                if '<<' not in body and _brackets_off(body):
                    self.add(ERROR, 'Code', f'Brackets do not match in <<{m.group(1)}>>', p, m.start(), snippet=self.snippet_at(p, m.start()),
                             what='Every (, [ and { needs a partner. SugarCube shows an error.', fix='Add or remove brackets.')
            # Odd number of quotes inside a macro
            for m in re.finditer(r'<<(?!script)([A-Za-z][\w-]*)([^<>]*?)>>', t):
                args = m.group(2)
                stripped = re.sub(r"'[^'\n]*'", '', re.sub(r'\\"', '', args))
                if stripped.count('"') % 2 == 1:
                    if MACRO_TAG_RE.match(t, m.start()):
                        continue   # ">>" inside a quoted text, e.g. <<button ">>">>: SugarCube reads it as text too
                    self.add(ERROR, 'Code', f'Unclosed quote in <<{m.group(1)}>>', p, m.start(), snippet=self.snippet_at(p, m.start()),
                             what='The macro has an odd number of " quotes, so one text value never ends.',
                             fix='Add the missing " or remove the extra one.')
            # hyphen right after a naked variable: "$mother-relation"
            vis = re.sub(r'<<.*?>>', lambda x: ' ' * len(x.group()), t, flags=re.S)
            for m in re.finditer(r'(?<![\w$])\$([A-Za-z_]\w*)-([A-Za-z]\w*)', vis):
                self.add(WARNING, 'Variables', f'"${m.group(1)}-{m.group(2)}" is read as "${m.group(1)}" plus text', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='Variable names cannot contain "-". The game prints $' + m.group(1) + ' (probably empty) followed by "-' + m.group(2) + '".',
                         fix=f'Did you mean ${m.group(1)}_{m.group(2)}? Use an underscore.')

    # ------------------------------------------------------------------ variables
    def check_variables(self):
        if not (self.is_sugarcube or self.format == 'Twine 1'):
            return
        sets = collections.Counter()
        reads = collections.Counter()
        first = {}
        tsets, treads = collections.Counter(), collections.Counter()
        tfirst = {}
        for p in self.passages:
            if 'stylesheet' in p.tags:
                continue
            t = p.text
            is_js = 'script' in p.tags
            if not is_js:
                for m in re.finditer(r'<<set\b(.*?)>>', t, re.S):
                    for v in re.finditer(r'([$_][A-Za-z_]\w*)\s*(?:to\b|=(?!=)|\+=|-=|\*=|/=|%=|\+\+|--)', m.group(1)):
                        (sets if v.group(1)[0] == '$' else tsets)[v.group(1)[1:]] += 1
                for m in re.finditer(r'<<(?:textbox|textarea|numberbox|checkbox|radiobutton|cycle|listbox|capture)\s+["\']?([$_][A-Za-z_]\w*)', t):
                    (sets if m.group(1)[0] == '$' else tsets)[m.group(1)[1:]] += 1
                for m in re.finditer(r'<<for\s+(?:_(\w+)\s*,\s*)?_(\w+)\b', t):
                    for g in m.groups():
                        if g:
                            tsets[g] += 1
                for m in re.finditer(r'<<(?:run|script)\b.*?>>', t, re.S):
                    for v in re.finditer(r'\$([A-Za-z_]\w*)\s*\.(?:push|splice|pop|shift|unshift|pushUnique|delete|deleteAt|concat)\b', m.group()):
                        sets[v.group(1)] += 1
            for m in re.finditer(r'(?<![\w$])([$])([A-Za-z_]\w*)', t):
                reads[m.group(2)] += 1
                first.setdefault(m.group(2), (p, m.start()))
            if not is_js:
                # temporary variables: only look inside macros, where "_x" cannot be plain text
                for mac in re.finditer(r'<<.*?>>', self.markup_text(p), re.S):
                    body = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', lambda q: ' ' * len(q.group()), mac.group())
                    for m in re.finditer(r'(?<![\w$.])_([A-Za-z]\w*)', body):
                        treads[m.group(1)] += 1
                        tfirst.setdefault(m.group(1), (p, mac.start() + m.start()))
        js = self.js_text
        for m in re.finditer(r'(?:State\.variables|variables\(\)|\bV)\.(\w+)', js):
            sets[m.group(1)] += 1
            reads[m.group(1)] += 1
        for m in re.finditer(r'State\.(?:setVar|getVar)\(\s*["\']\$(\w+)', js):
            sets[m.group(1)] += 1
            reads[m.group(1)] += 1
        for m in re.finditer(r'State\.temporary\.(\w+)|\bT\.(\w+)', js):
            tsets[m.group(1) or m.group(2)] += 1

        all_vars = sorted(set(sets) | set(reads))
        self.known_vars = set(all_vars)
        self.var_counts = (reads, sets, treads, tsets)   # for settle.py
        set_names = sorted(sets)
        unused_vars = []
        for v in all_vars:
            if v not in first:
                continue   # only seen in scripts (no place in a passage to point at); was a crash on Destroyer
            used = reads[v] - sets[v]  # reads also counts the "$x" in "<<set $x to>>"
            if sets[v] == 0:
                near = _typo_of(v, set_names)
                p, off = first[v]
                self.add(WARNING if near else INFO, 'Variables', f'${v} is used but never set', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'${v} is read {reads[v]} time(s) but nothing ever gives it a value, so it is always empty/undefined.'
                              + (f' It looks like a misspelling of ${near}.' if near else ''),
                         fix=(f'Rename ${v} to ${near} everywhere it appears.' if near
                              else f'Give it a starting value in StoryInit (e.g. <<set ${v} to 0>>), or fix the name.'))
            elif used <= 0 and reads[v] <= sets[v]:
                near = _typo_of(v, [x for x in all_vars if x != v and reads[x] - sets[x] > 0])
                p, off = first[v]
                if not near:
                    unused_vars.append((v, p, off))
                    continue
                self.add(WARNING if near else INFO, 'Variables', f'${v} is set but never checked or shown', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'${v} gets a value {sets[v]} time(s) but nothing ever reads it.'
                              + (f' The game does read ${near}, so this is probably a misspelling and the change is lost.' if near else ' It might be left over.'),
                         fix=(f'Rename ${v} to ${near}.' if near else 'Remove it if it is not needed.'))
        self.report_unused_vars(unused_vars)
        # same name, different capitals or one letter apart
        lower = collections.defaultdict(list)
        for v in all_vars:
            lower[v.lower()].append(v)
        for group in lower.values():
            if len(group) > 1:
                group.sort(key=lambda x: -(reads[x] + sets[x]))
                main = group[0]
                for g in group[1:]:
                    p, off = first[g]
                    self.add(WARNING, 'Variables', f'${g} and ${main} differ only in capital letters', p, off,
                             snippet=self.snippet_at(p, off),
                             what=f'Variable names are case-sensitive, so these are two different variables. ${main} is used {reads[main]} times, ${g} only {reads[g]}.',
                             fix=f'Rename ${g} to ${main} (if they are meant to be the same).')
        reported = set()
        frequent = [v for v in all_vars if reads[v] >= 5 and len(v) >= 5]
        rare = [v for v in all_vars if reads[v] <= 3 and len(v) >= 5]
        for r in rare:
            for f in frequent:
                if r != f and r.lower() != f.lower() and _one_edit_apart(r.lower(), f.lower()) \
                        and not re.sub(r'\d', '', r) == re.sub(r'\d', '', f) and (r, f) not in reported:
                    if sets[r] == 0 or reads[r] <= sets[r]:
                        continue  # already reported above as never set / never read
                    reported.add((r, f))
                    p, off = first[r]
                    self.add(INFO, 'Variables', f'${r} looks like a typo of ${f}', p, off,
                             snippet=self.snippet_at(p, off),
                             what=f'${r} is used {reads[r]} time(s), ${f} is used {reads[f]} times, and the names differ by one letter.',
                             fix=f'If they are meant to be the same, rename ${r} to ${f}.')
        builtin_temp = {'args', 'contents', 'i', 'j', 'k'}
        for v, n in treads.items():
            if tsets[v] == 0 and v not in builtin_temp and not re.search(r'\b_' + re.escape(v) + r'\b', js):
                p, off = tfirst[v]
                near = _close(v, sorted(tsets), cutoff=0.8)
                self.add(WARNING if near else INFO, 'Variables', f'Temporary variable _{v} is used but never set', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'_{v} never gets a value anywhere, so it is always empty.' + (f' Did you mean _{near}?' if near else ''),
                         fix=(f'Rename it to _{near}.' if near else f'Set it first, e.g. <<set _{v} to 0>>. (If it is just text with an underscore, ignore this.)'))

    def report_unused_vars(self, unused_vars):
        """Values that get set but are never read: one note for all of them, not one each."""
        if len(unused_vars) == 1:
            v, p, off = unused_vars[0]
            self.add(INFO, 'Variables', f'${v} is set but never checked or shown', p, off,
                     snippet=self.snippet_at(p, off),
                     what=f'${v} gets a value but nothing ever reads it. It might be left over.',
                     fix='Remove it if it is not needed.')
        elif unused_vars:
            unused_vars.sort(key=lambda x: x[0].lower())
            where = [f'  ${v}  ({p.name}, line {p.line + p.text.count(chr(10), 0, off)})' for v, p, off in unused_vars]
            self.add(INFO, 'Variables', f'{len(unused_vars)} values are set but never checked or shown', line=0,
                     snippet=', '.join('$' + v for v, *_ in unused_vars[:15]) + (' …' if len(unused_vars) > 15 else ''),
                     what='These values get changed, but nothing in the game ever reads them. They are usually left over '
                          'from older versions or kept for later. Nothing breaks.',
                     fix='Nothing needed. Remove the ones you are sure are not needed. Full list (where each is first set):\n'
                         + '\n'.join(where))

    # ------------------------------------------------------------------ HTML
    def check_html(self):
        balance_tags = ['div', 'span', 'center', 'table', 'tr', 'td', 'ul', 'ol', 'a', 'b', 'i', 'u', 'strong', 'em', 'button', 'select', 'label', 'section', 'h1', 'h2', 'h3']
        all_bad_units = []
        try:
            self._check_html_passages(balance_tags, all_bad_units)
        finally:
            if all_bad_units:
                total = sum(len(b) for _, b in all_bad_units)
                units = sorted({u for _, b in all_bad_units for *_, u in b})
                p, b = all_bad_units[0]
                off, attr, num, unit = b[0]
                ranked = sorted(all_bad_units, key=lambda x: -len(x[1]))
                self.add(INFO, 'HTML', f'{total} width/height values in {len(all_bad_units)} passages use an invalid unit ("{", ".join(units)}")', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'The width and height attributes only take a number of pixels, so the browser ignores "{units[0]}" and uses e.g. {num} pixels. '
                              'Images still show, just not at the size the unit suggests.'
                              + (' ("wv" is not a unit at all; "vw" means viewport width.)' if 'wv' in [u.lower() for u in units] else ''),
                         fix=f'Write {attr}="{num}" (pixels), or use style="{attr}:{num}vw" if a size relative to the window was meant.\n'
                             'Passages with the most:\n' + '\n'.join(f'  {pp.name}: {len(bb)}' for pp, bb in ranked[:40])
                             + (f'\n  … and {len(ranked) - 40} more passages' if len(ranked) > 40 else ''))

    def _check_html_passages(self, balance_tags, all_bad_units):
        for p in self.story_passages():
            t = self.markup_text(p)
            if self.format == 'HTML':
                t = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', lambda m: _blank_keep(m.group()), t, flags=re.S | re.I)
            bad_units = []
            # quotes inside tags
            for m in re.finditer(r'(?<!<)<([A-Za-z][\w-]*)(\s[^<>]*)?>', t):   # not <<macro "a > b">>
                attrs = m.group(2) or ''
                if '<<' in attrs or '>>' in attrs:
                    continue
                if attrs.count('"') % 2 == 1:
                    if HTML_TAG_RE.match(t, m.start()):
                        continue   # a ">" inside a quoted value (onclick="if(a>0)..."): the browser reads it as text too
                    self.add(ERROR, 'HTML', f'<{m.group(1)}> tag has an unclosed quote', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what='One attribute value is missing its closing ", so the browser reads the rest of the tag wrongly (images may not show).',
                             fix='Add the missing " at the end of the attribute value.')
                for a in re.finditer(r'\b(width|height)\s*=\s*["\']?(\d+(?:\.\d+)?)([A-Za-z]+)["\']?', attrs):
                    if a.group(3).lower() != 'px':
                        bad_units.append((m.start(), a.group(1), a.group(2), a.group(3)))
                for a in re.finditer(r'\b(src|href)\s*=\s*(["\'])\2', attrs):
                    self.add(WARNING, 'HTML', f'Empty {a.group(1)}="" in <{m.group(1)}>', p, m.start(),
                             snippet=self.snippet_at(p, m.start()),
                             what='The tag points at nothing. Browsers may show a broken image or reload the page.',
                             fix='Fill in the file path or remove the tag.')
            if bad_units:
                all_bad_units.append((p, bad_units))
            for m in re.finditer(r'(?<!<)(<[A-Za-z][\w-]*(?:\s[^<>]*)?>)(>+)(?!>)', t):
                if '<<' in m.group(1):
                    continue
                self.add(WARNING, 'HTML', 'Stray ">" after an HTML tag', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what=f'"{_short(m.group(1), 60)}" is followed by an extra ">" that shows up as text on screen.',
                         fix='Delete the extra ">".')
            # tag balance
            for tag in balance_tags:
                tokens = re.finditer(r'<(/?)%s(?=[\s>/])' % tag, t, re.I)
                stack, extra, n_open, n_close = [], [], 0, 0
                for m in tokens:
                    if m.group(1):
                        n_close += 1
                        if stack:
                            stack.pop()
                        else:
                            extra.append(m.start())
                    else:
                        n_open += 1
                        stack.append(m.start())
                if n_open == n_close and not extra:
                    continue
                more = 'opened' if n_open > n_close else 'closed'
                title = f'<{tag}> {more} more times than {"closed" if more == "opened" else "opened"} ({n_open} open, {n_close} close)'
                if more == 'opened':
                    off = stack[0]  # the outermost tag that is never closed
                    ln = p.line + t.count('\n', 0, off)
                    # a box left open at the very end is closed by the browser when the passage ends
                    quiet = not extra and tag in ('div', 'center', 'section')
                    self.add(INFO if quiet else WARNING, 'HTML', title, p, off, snippet=self.snippet_at(p, off),
                             what=f'The <{tag}> on line {ln} is never closed with </{tag}>.'
                                  + (' The browser closes it by itself where the passage ends, so usually nothing looks wrong. '
                                     'Text added after it later would end up inside this box.' if quiet else
                                     f' Unbalanced <{tag}> tags can break the page layout of this and following text (things end up inside the wrong box).'),
                             fix=f'Add </{tag}> where the box made by the <{tag}> on line {ln} should end.')
                else:
                    off = extra[0] if extra else t.lower().rfind('</%s' % tag)
                    ln = p.line + t.count('\n', 0, off)
                    self.add(WARNING, 'HTML', title, p, off, snippet=self.snippet_at(p, off),
                             what=f'The </{tag}> on line {ln} closes a <{tag}> that was never opened. '
                                  f'Unbalanced <{tag}> tags can break the page layout of this and following text (things end up inside the wrong box).',
                             fix=f'Remove the extra </{tag}> on line {ln}, or add the missing <{tag}>.')
            # duplicate ids inside one passage
            ids = collections.Counter(re.findall(r'\bid\s*=\s*["\']([^"\'$<]+)["\']', t))
            for i, n in ids.items():
                if n > 1:
                    off = t.find(i)
                    self.add(WARNING, 'HTML', f'id="{i}" used {n} times in one passage', p, off,
                             snippet=self.snippet_at(p, off),
                             what='An id must be unique on the page. Scripts and <<replace "#id">> will only find the first one.',
                             fix='Give each element its own id, or use a class instead.')

    def check_css_classes(self):
        css = COMMENT_RE.sub('', self.css_text)
        defined = set()
        parts = re.split(r'([{}])', css)   # (a findall for "text{" took forever on a big embedded font)
        for sel in (parts[i - 1] for i in range(1, len(parts)) if parts[i] == '{' and parts[i - 1].strip()):
            if sel.strip().startswith('@') and 'media' not in sel and 'supports' not in sel:
                continue
            defined.update(re.findall(r'\.(-?[_a-zA-Z][\w-]*)', sel))
        if not defined:
            return
        defined_list = sorted(defined)
        # names the scripts mention ("on", '.on', ' on '), collected once: searching the whole script again
        # for every class took minutes on College Daze
        js_words = set(re.findall(r'(?<=["\'.\s])([\w-]+)(?=["\'\s])', self.js_text))
        used = collections.defaultdict(list)
        for p in self.story_passages():
            t = self.markup_text(p)
            for m in re.finditer(r'\bclass\s*=\s*["\']([^"\']*)["\']', t):
                if '$' in m.group(1) or '<<' in m.group(1) or '_' == m.group(1)[:1]:
                    continue
                for c in m.group(1).split():
                    used[c].append((p, m.start()))
            for m in re.finditer(r'@@\.([\w-]+)', t):
                used[m.group(1)].append((p, m.start()))
        for c, places in used.items():
            if c in defined or c in K.BUILTIN_CLASSES or c in js_words:
                continue
            near = _close(c, defined_list, cutoff=0.8)
            p, off = places[0]
            if near:
                self.add(WARNING, 'CSS', f'Class "{c}" does not match any style (did you mean "{near}"?)', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'Class names are case-sensitive. The stylesheet defines ".{near}" but this uses "{c}", so the element is not styled ({len(places)} use(s)).',
                         fix=f'Change class="{c}" to class="{near}" in all {len(places)} places.')
            elif c not in K.BUILTIN_CLASSES and not c.startswith('macro-'):
                self.add(INFO, 'CSS', f'Class "{c}" has no style rule', p, off,
                         snippet=self.snippet_at(p, off),
                         what=f'No CSS rule uses ".{c}" ({len(places)} use(s)). It does nothing unless a script uses it.',
                         fix='Remove it, or add a style rule for it in the Story Stylesheet.')

    # ------------------------------------------------------------------ assets
    def check_assets(self):
        base = self.game_dir  # paths in the game resolve from the game folder
        # Index of every file in the game folder
        files_lower = {}
        all_files = []
        skip_dirs = {'.git', '.svn', 'node_modules', '__pycache__', '.claude', '.vscode', '.idea', 'Game Bug Scanner', 'Game Doctor'}
        for root, dirs, fnames in os.walk(self.game_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
            for fn in fnames:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, base).replace('\\', '/')
                files_lower[rel.lower()] = rel
                all_files.append((rel, full))
        self._asset_file_count = len(all_files)
        ext_re = '|'.join(sorted(K.ALL_ASSET_EXT, key=len, reverse=True))
        ref_re = re.compile(r'''(?:src|href|poster|data-src)\s*=\s*["']([^"'<>]+)["']|url\(\s*["']?([^"')]+?)["']?\s*\)|\[img\[(?:[^\]|]*\|)?([^\]]+)\]|["']([^"'<>\n]{1,260}?\.(?:%s))(?:[?#][^"']*)?["']|\b(?:src|poster)\s*=\s*([^\s"'<>]+\.(?:%s))(?=[\s>/])''' % (ext_re, ext_re), re.I)
        referenced = set()
        dynamic = 0
        ref_count = 0
        by_problem = collections.defaultdict(list)
        in_tag = set()  # paths written straight into an <img src=...>-style tag at least once
        hidden_if_missing, shown_if_missing = set(), set()   # missing paths in a tag with / without onerror="..."
        backslash = collections.defaultdict(list)

        sources = [(p, p.text) for p in self.passages]
        sources.append((None, self.js_text))
        if self.format != 'HTML':
            # template/head part of the file outside story data
            head = re.sub(r'<tw-storydata.*</tw-storydata>', _blank, self.raw, flags=re.S)
            sources.append(('(page)', html.unescape(head)))
        # The page template holds the story-format engine; only real attributes count there.
        attr_re = re.compile(r'''(?:src|href|poster|data-src)\s*=\s*["']([^"'<>]+)["']|url\(\s*["']?([^"')]+?)["']?\s*\)''', re.I)
        for p, text in sources:
            for m in (attr_re if p == '(page)' else ref_re).finditer(text):
                ref = next(g for g in m.groups() if g is not None).strip()
                if not ref or ref.startswith(('http://', 'https://', '//', 'data:', 'mailto:', 'javascript:', '#', 'blob:')):
                    continue
                path = re.split(r'[?#]', ref)[0]
                if re.search(r'%[0-9A-Fa-f]{2}', path):
                    path = urllib.parse.unquote(path)   # the browser reads "stage%202" as "stage 2"
                ext = path.rsplit('.', 1)[-1].lower() if '.' in path.rsplit('/', 1)[-1].rsplit('\\', 1)[-1] else ''
                if ext not in K.ALL_ASSET_EXT:
                    continue
                if re.search(r'[$`{}+]|<<|\b_\w|\(', path):
                    dynamic += 1
                    continue
                ref_count += 1
                ppos = (p, m.start()) if isinstance(p, Passage) else (None, 0)
                if re.match(r'^[A-Za-z]:[\\/]|^file:', path):
                    by_problem[('abs', path)].append(ppos)
                    continue
                if '\\' in path:
                    backslash[p.name if isinstance(p, Passage) else '(script)'].append(ppos)
                norm = os.path.normpath(os.path.join(base, path.replace('\\', '/')))
                rel = os.path.relpath(norm, base).replace('\\', '/')
                key = rel.lower()
                if key in files_lower:
                    real = files_lower[key]
                    referenced.add(key)
                    if real != rel:
                        by_problem[('case', path, real)].append(ppos)
                elif os.path.exists(norm):
                    referenced.add(key)
                else:
                    by_problem[('missing', path)].append(ppos)
                    if p == '(page)' or m.group(4) is None:
                        in_tag.add(path)
                    end = text.find('>', m.end())
                    tag = text[text.rfind('<', 0, m.start()) + 1:end if end != -1 else len(text)]
                    (hidden_if_missing if re.search(r'\bonerror\s*=', tag, re.I) else shown_if_missing).add(path)
        self._asset_ref_count = ref_count

        # Second look at "missing" files, so games that build paths in code are not flooded with false alarms.
        by_stem = collections.defaultdict(list)  # file name without its ending -> every path with that name
        for k in files_lower:
            by_stem[k.rsplit('/', 1)[-1].rsplit('.', 1)[0]].append(k)
        # file types the game's code names on their own ("opus", ".ogg"): a sign it swaps formats while playing
        code_exts = {e.lower() for e in re.findall(r'["\']\.?(%s)["\']' % ext_re, self.js_text, re.I)}
        format_swap = {}  # path -> the file with another ending that is there instead
        for key in [k for k in by_problem if k[0] == 'missing']:
            places = by_problem[key]
            rel = os.path.relpath(os.path.normpath(os.path.join(base, key[1].replace('\\', '/'))), base).replace('\\', '/').lower()
            stem, ext = rel.rsplit('.', 1)
            same_kind = lambda k: K.asset_kind(k.rsplit('.', 1)[-1]) == K.asset_kind(ext)
            looks = [k for k in by_stem.get(stem.rsplit('/', 1)[-1], []) if k.rsplit('.', 1)[0] == stem]
            if all(pp is None for pp, _ in places) or key[1] not in in_tag:
                # Only named as text in code (JavaScript, or a quoted name in a scene's macros): the code usually
                # puts a folder in front of it ("img/" + "places/bar.jpg"), so a file ending in this path anywhere
                # counts. Names straight in an <img src=...> tag are checked strictly: a wrong path there is broken.
                tail = '/' + stem.lstrip('./')
                looks += [k for k in by_stem.get(stem.rsplit('/', 1)[-1], []) if k.rsplit('.', 1)[0].endswith(tail)]
            found = [k for k in looks if k.rsplit('.', 1)[-1] == ext]
            if found:
                referenced.update(found)
                del by_problem[key]
                continue
            # same name with another ending of the same kind (song.mp3 -> song.opus)
            alt = [k for k in looks if same_kind(k)]
            if alt:
                referenced.update(alt)
                if ext in code_exts and alt[0].rsplit('.', 1)[-1] in code_exts:
                    del by_problem[key]  # the game's code names both file types: it switches while playing
                else:
                    format_swap[key[1]] = files_lower[alt[0]]

        def where(places):
            names = [pp.name for pp, _ in places if pp]
            if not names:
                return "the game's JavaScript"
            c = collections.Counter(names)
            return ', '.join(f'"{n}"' + (f' ×{k}' if k > 1 else '') for n, k in list(c.items())[:6]) + (' …' if len(c) > 6 else '')

        for key, places in sorted(by_problem.items(), key=lambda x: x[0][1].lower()):
            kind = key[0]
            path = key[1]
            p, off = places[0]
            line_args = dict(passage=p, offset=off) if p else dict(line=0)
            snip = self.snippet_at(p, off) if p else path
            ext = path.rsplit('.', 1)[-1].lower()
            label = K.asset_kind(ext)
            if kind == 'missing' and path in format_swap:
                self.add(WARNING, 'Missing files', f'{label} not found: {path} (a {format_swap[path].rsplit(".", 1)[-1]} file is there instead)',
                         **line_args, snippet=snip,
                         what=f'The game asks for "{path}", but the folder has "{format_swap[path]}". Many games switch file types '
                              f'in their code while playing; if this one does not, it will not load. Used in: {where(places)}.',
                         fix=f'If it does not load in the game, change the name to "{format_swap[path]}".')
            elif kind == 'missing' and all(pp is None for pp, _ in places):
                self.add(WARNING, 'Missing files', f'{label} not found: {path}', **line_args, snippet=snip,
                         what=f'This name is only in the game\'s JavaScript and no file ending in it is in the game folder. '
                              f'The script may put a folder in front of it that this check cannot see. Used in: {where(places)}.',
                         fix='If the picture or sound is missing while playing, put the file in the game folder.')
            elif kind == 'missing' and path in hidden_if_missing and path not in shown_if_missing:
                self.add(WARNING, 'Missing files', f'{label} not found: {path}', **line_args, snippet=snip,
                         what=f'The file is not in the game folder, but the game handles that (onerror="..." on the tag, usually '
                              f'hiding the picture), so players see no broken image, just nothing. Used in: {where(places)}.',
                         fix='If something should be shown there, put the missing file in the game folder.')
            elif kind == 'missing' and path not in in_tag and not re.search(r'[/\\]', path):
                self.add(WARNING, 'Missing files', f'{label} not found: {path}', **line_args, snippet=snip,
                         what=f'This bare file name (no folder) is only written as text in the scenes\' code, and no file with this name '
                              f'is in the game folder. It may only be shown as a label, or the code may load it from a folder this check '
                              f'cannot see. Used in: {where(places)}.',
                         fix='If the picture, sound or video is missing while playing, put the file in the game folder.')
            elif kind == 'missing':
                fname = path.replace('\\', '/').rsplit('/', 1)[-1].lower()
                stem = fname.rsplit('.', 1)[0]
                folder = os.path.dirname(path.replace('\\', '/')).lower()
                # only files of the same kind: a sound is never "the picture meant"
                cands = [r for r in files_lower.values() if r.rsplit('/', 1)[-1].lower().rsplit('.', 1)[0] == stem
                         and K.asset_kind(r.rsplit('.', 1)[-1].lower()) == label]
                near = []
                if not cands:
                    pool = [r for r in files_lower.values() if os.path.dirname(r).lower() == folder
                            and K.asset_kind(r.rsplit('.', 1)[-1].lower()) == label]
                    cands = [r for r in pool if file_typo(path, r)][:1]
                    near = difflib.get_close_matches(fname, [r.rsplit('/', 1)[-1] for r in pool], n=1, cutoff=0.75)
                if cands:
                    hint = f' The name looks mistyped: the file meant is probably "{cands[0]}".'
                elif near:
                    hint = (' Files with similar names exist (e.g. "{near}"), but in games those are usually different pictures '
                            '(another character, another scene, or one of several random versions).').format(near=near[0])
                else:
                    hint = ''
                self.add(ERROR, 'Missing files', f'{label} not found: {path}', **line_args, snippet=snip,
                         what=f'The file does not exist in the game folder, so it will not load (broken image / no sound / no video). Used in: {where(places)}.' + hint,
                         fix=(f'Check whether "{cands[0]}" is the right file. If it is, correct the name in the game yourself; '
                                   'if not, put the missing file in the game folder.' if cands
                              else 'Put the missing file in the game folder with exactly this name and path. Only change the name '
                                   'in the game if you know which file was meant.'))
            elif kind == 'case':
                self.add(WARNING, 'Missing files', f'{label} path has wrong capital letters: {path}', **line_args, snippet=snip,
                         what=f'The real file is "{key[2]}". It loads on Windows, but on a website, Mac, Linux or Android capital letters matter and it will be missing. Used in: {where(places)}.',
                         fix=f'Change the path to "{key[2]}" (or rename the file).')
            elif kind == 'abs':
                self.add(ERROR, 'Missing files', f'{label} uses a full path from someone\'s PC: {path}', **line_args, snippet=snip,
                         what=f'Paths like "C:\\..." or "D:\\..." only work on the computer that made the game. Used in: {where(places)}.',
                         fix='Use a path relative to the game folder, e.g. "images/characters/name.jpg".')
        if backslash:
            total = sum(len(v) for v in backslash.values())
            ranked = sorted(backslash.items(), key=lambda x: -len(x[1]))
            p, off = ranked[0][1][0]
            self.add(INFO, 'Portability', f'{total} file paths in {len(backslash)} passages use "\\" instead of "/"',
                     **(dict(passage=p, offset=off) if p else dict(line=0)),
                     snippet=self.snippet_at(p, off) if p else '',
                     what='Backslashes in file paths work on Windows, but images/sounds will not load on websites, Mac, Linux or Android.',
                     fix='Use forward slashes, e.g. images/characters/name.jpg. A find-and-replace of "\\" with "/" inside src="..." values fixes them all.\n'
                         'Passages with the most:\n' + '\n'.join(f'  {n}: {len(v)}' for n, v in ranked[:40])
                         + (f'\n  … and {len(ranked) - 40} more passages' if len(ranked) > 40 else ''))
        # unused files
        unused = []
        html_name = os.path.basename(self.html_path).lower()
        for rel, full in all_files:
            ext = rel.rsplit('.', 1)[-1].lower() if '.' in rel else ''
            if ext not in K.MEDIA_EXT and ext not in ('css', 'js'):
                continue
            if rel.lower() in referenced:
                continue
            unused.append(rel)
        if unused:
            folders = collections.Counter(os.path.dirname(u) for u in unused)
            ranked = sorted(folders.items(), key=lambda x: (-x[1], x[0].lower()))
            listing = []
            for folder, n in ranked:
                listing.append(f'{folder or "."}  ({n}):')
                listing += ['  ' + u.rsplit('/', 1)[-1] for u in sorted(unused, key=str.lower) if os.path.dirname(u) == folder]
            title = (f'{len(unused)} file(s) in "{ranked[0][0] or "."}" are never used' if len(ranked) == 1
                     else f'{len(unused)} files in {len(ranked)} folders are never used')
            self.add(INFO, 'Unused files', title, line=0,
                     snippet=', '.join(f'{f or "."} ({n})' for f, n in ranked[:10]) + (' …' if len(ranked) > 10 else ''),
                     what='No passage, script or style refers to these files. They make the download bigger.'
                          + (f' Note: {dynamic} paths are built from variables and could not be checked, so some of these may still be used.' if dynamic else ''),
                     fix='Delete them if they are left over, or check whether a link to them is missing. Full list:\n' + '\n'.join(listing))
        large = []
        for rel, full in all_files:
            ext = rel.rsplit('.', 1)[-1].lower() if '.' in rel else ''
            if ext in K.MEDIA_EXT:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size == 0:
                    self.add(WARNING, 'Broken files', f'Empty file (0 bytes): {rel}', line=0,
                             what='The file has no content, so it will not display or play.', fix='Replace it with the real file.')
                elif (ext in K.IMAGE_EXT and size > 15 * 1024 * 1024) or size > 80 * 1024 * 1024:
                    large.append((size, rel))
        if large:
            large.sort(reverse=True)
            self.add(INFO, 'Large files', f'{len(large)} very large media file(s)', line=0,
                     snippet=', '.join(r.rsplit('/', 1)[-1] for _, r in large[:8]) + (' …' if len(large) > 8 else ''),
                     what='Images over 15 MB (or other media over 80 MB) load slowly, especially on phones.',
                     fix='Consider compressing them (e.g. lower-resolution WebP). Files:\n'
                         + '\n'.join(f'  {s / 1048576:.1f} MB  {r}' for s, r in large))
        import media
        media.check_files(self, all_files, referenced)

    # ------------------------------------------------------------------ text
    def check_text(self):
        typos = K.MISSPELLINGS
        typo_re = re.compile(r"\b(%s)\b" % '|'.join(sorted(map(re.escape, typos), key=len, reverse=True)), re.I)
        rep_re = re.compile(r"\b([A-Za-z']{2,})\s+\1\b", re.I)
        ph_re = re.compile(r'\b(%s)\b' % '|'.join(re.escape(w) for w in K.PLACEHOLDER_WORDS))
        typo_hits = collections.defaultdict(list)
        for p in self.story_passages():
            t = self.markup_text(p)
            # visible text only
            # tags become "|" so words on either side of a tag never join up
            vis = re.sub(r'<<.*?>>', lambda m: _blank_keep(m.group(), '|'), t, flags=re.S)
            vis = re.sub(r'<[^<>]*>', lambda m: _blank_keep(m.group(), '|'), vis)
            vis = re.sub(r'\[\[[^\]]*\]\]', lambda m: _blank_keep(m.group(), '|'), vis)
            vis = re.sub(r'[$_][A-Za-z_]\w*', lambda m: _blank_keep(m.group()), vis)
            vis = re.sub(r'https?://\S+', lambda m: _blank_keep(m.group()), vis)
            for m in ph_re.finditer(vis):
                self.add(WARNING, 'Unfinished content', f'"{m.group(1)}" text shown to players', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='This looks like a note the developer left in. Players will see it on screen.',
                         fix='Write the real content, or remove the note (or put it in a comment /* like this */).')
            for m in typo_re.finditer(vis):
                w = m.group(1)
                typo_hits[w.lower()].append((p, m.start(), w))
            for m in rep_re.finditer(vis):
                w = m.group(1).lower()
                if w not in K.REPEAT_CHECK:
                    continue  # sound effects and emphasis ("glug glug", "my my") are usually on purpose
                if re.match(r'\s+' + re.escape(m.group(1)) + r'\b', vis[m.end():], re.I):
                    continue  # three or more in a row: deliberate
                self.add(INFO, 'Text', f'Repeated word "{m.group(1)} {m.group(1)}"', p, m.start(),
                         snippet=self.snippet_at(p, m.start()),
                         what='The same word appears twice in a row.', fix='Remove one of them (unless it is on purpose).')
            # placeholders inside comments (raw text) are only notes for the dev
            for m in ph_re.finditer(COMMENT_RE.sub(lambda x: x.group(), p.raw_text)):
                pass
        for w, hits in typo_hits.items():
            corr = typos[w]
            two = K.AMBIGUOUS.get(w)
            for p, off, orig in hits[:20]:
                if two:
                    self.add(INFO, 'Spelling', f'"{orig}" → "{two[0]}" or "{two[1]}"', p, off, snippet=self.snippet_at(p, off),
                             what=f'"{orig}" is a common misspelling ({len(hits)} time(s) in the game). '
                                  f'It can mean "{two[0]}" or "{two[1]}", depending on the sentence.',
                             fix=f'Read the sentence and change it to "{two[0]}" or to "{two[1]}", whichever fits.')
                    continue
                self.add(INFO, 'Spelling', f'"{orig}" → "{corr}"', p, off, snippet=self.snippet_at(p, off),
                         what=f'"{orig}" is a common misspelling ({len(hits)} time(s) in the game).',
                         fix=f'Change it to "{corr}".')

    # ------------------------------------------------------------------ JavaScript
    def check_js(self):
        blocks = []
        for p in self.passages:
            if 'script' in p.tags:
                blocks.append((p, p.raw_text, 0))
            else:
                for m in script_matches(p.text):
                    if m.start() and p.text[m.start() - 1] in '"\'':
                        continue  # "<script>" + ... is text the passage builds up, not a script of its own
                    body = m.group(2) if m.group(2) is not None else m.group(5)
                    if body is not None and not re.search(r'\bsrc\s*=', m.group(4) or ''):
                        blocks.append((p, body, m.start(2) if m.group(2) is not None else m.start(5)))
        if self.is_twine:
            for sm in re.finditer(r'<script[^>]*id="twine-user-script"[^>]*>(.*?)</script>', self.raw, re.S):
                line = self.raw.count('\n', 0, sm.start(1)) + 1
                code = sm.group(1)  # stored as it is, see parse()
                blocks.append((Passage('', 'Story JavaScript', ['script'], code, code, line), code, 0))
        nobr_all = re.search(r'Config\.passages\.nobr\s*=\s*true', self.js_text)
        for p, code, base in blocks:
            if 'script' not in p.tags and (nobr_all or 'nobr' in p.tags):
                code = code.replace('\n', ' ')   # SugarCube turns line breaks into spaces in nobr passages, scripts included
            problem = _js_bracket_problem(code)
            if problem:
                off, msg = problem
                self.add(ERROR, 'JavaScript', f'JavaScript brackets/quotes problem: {msg}', p, base + off,
                         snippet=self.snippet_at(p, base + off) if p.text else '',
                         what='The script has an unclosed or extra bracket or quote, so the whole script fails to run.',
                         fix='Check the brackets and quotes around this line; every (, [, { and quote needs a partner.')
            for m in re.finditer(r'\bconsole\.log\(|\bdebugger\b|\balert\(', code):
                self.add(INFO, 'JavaScript', f'Leftover debug code "{m.group().rstrip("(")}"', p, base + m.start(),
                         snippet=self.snippet_at(p, base + m.start()) if p.text else '',
                         what='Debug code like this is usually left over from testing.', fix='Remove it if it is not needed.')


# ---------------------------------------------------------------------- utilities
def mark_dead_widgets(issues, spans):
    """Code inside a widget that nothing calls never runs: its problems are not certain, so they are Warnings."""
    for i in issues:
        if i.severity != ERROR or i.passage not in spans:
            continue
        for a, b, w in spans[i.passage]:
            if a <= i.line <= b:
                i.severity = WARNING
                i.what += f' (It is inside the widget <<{w}>>, which nothing seems to call, so players may never see this.)'
                break


def mark_unreachable(issues, passages):
    """Problems in scenes players can't reach can't hurt players: show them as notes."""
    for i in issues:
        if i.passage in passages and i.category != 'Unused passages' and 'is empty' not in i.title:
            i.unreachable = True
            i.severity = INFO


HEALTH_BANDS = [(100, 'Excellent'), (90, 'Great'), (80, 'Good'), (70, 'Fine'), (50, 'Needs work'), (0, 'Poor')]


def health_score(issues, passage_count):
    """0-100: share of passages without problems (errors count fully, warnings half,
    notes a little), minus a capped penalty for the total number of errors/warnings."""
    n = max(1, passage_count)
    worst = {}
    for i in issues:
        if i.passage:
            worst[i.passage] = min(worst.get(i.passage, 9), SEVERITY_ORDER[i.severity])
    weights = {0: 1.0, 1: 0.5, 2: 0.1}
    share = (n - sum(weights[w] for w in worst.values())) / n
    errors = sum(1 for i in issues if i.severity == ERROR)
    warnings = sum(1 for i in issues if i.severity == WARNING)
    score = share * 100 - min(30, errors * 0.8 + warnings * 0.1)
    if errors or warnings:
        score = min(score, 99)
    score = max(0, int(score))
    label = next(lbl for limit, lbl in HEALTH_BANDS if score >= limit)
    return score, label


def _blank_str(s):
    return '\n' * s.count('\n')


def _blank_keep(s, ch=' '):
    """Replace every character except newlines (so offsets stay put)."""
    return re.sub(r'[^\n]', ch, s)


def _top_level_ops(expr):
    depth, ops = 0, set()
    s = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', expr)
    for tok in re.finditer(r'\(|\)|\[|\]|&&|\|\||\band\b|\bor\b', s):
        g = tok.group()
        if g in '([':
            depth += 1
        elif g in ')]':
            depth -= 1
        elif depth == 0:
            ops.add('and' if g in ('&&', 'and') else 'or')
    return ops, depth


def _brackets_off(code):
    """Brackets in macro code (texts in quotes already blanked to ""). Quotes left over mean text that spans lines
    or \\' marks, which only the game can read, so then only ( and ) are counted."""
    if _top_level_ops(code)[1] != 0:
        return True
    return not re.search(r"""['"`]""", code.replace('""', '')) and bool(_js_bracket_problem(code))


def _js_bracket_problem(code):
    """Very small JS tokenizer: returns (offset, message) for the first unbalanced
    bracket or unterminated string/comment, or None."""
    pairs = {')': '(', ']': '[', '}': '{'}
    stack = []
    i, n = 0, len(code)
    prev_sig = ''
    word_re = re.compile(r'[\w$]+')

    def template_end(j):
        """Read the text part of `text ${...} text` from j: returns (where to go on, True if a ${ starts there),
        or (None, False) when the closing ` never comes."""
        while j < n:
            if code[j] == '\\':
                j += 2
                continue
            if code[j] == '`':
                return j + 1, False
            if code.startswith('${', j):
                return j + 2, True
            j += 1
        return None, False

    while i < n:
        c = code[i]
        if c == '`' or (c == '}' and stack and stack[-1][0] == '${'):
            # template text: `...${ code }...` (the code part can hold more templates)
            start = i if c == '`' else stack.pop()[1]
            j, inner = template_end(i + 1)
            if j is None:
                return start, 'text string starting with ` is never closed'
            if inner:
                stack.append(('${', start))
                prev_sig = '('
            else:
                prev_sig = 'x'
            i = j
            continue
        if c in '"\'':
            q, j = c, i + 1
            while j < n and code[j] != q:
                if code[j] == '\\':
                    j += 1
                elif code[j] == '\n':
                    return i, f'text string starting with {q} is not closed on its line'
                j += 1
            if j >= n:
                return i, f'text string starting with {q} is never closed'
            i = j + 1
            prev_sig = 'x'
            continue
        if code.startswith('//', i):
            j = code.find('\n', i)
            i = n if j == -1 else j
            continue
        if code.startswith('/*', i):
            j = code.find('*/', i + 2)
            if j == -1:
                return i, 'comment /* is never closed with */'
            i = j + 2
            continue
        if c == '/' and prev_sig in ('', '(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', 'return', 'op'):
            # regex literal
            j = i + 1
            in_class = False
            while j < n and (code[j] != '/' or in_class):
                if code[j] == '\\':
                    j += 1
                elif code[j] == '[':
                    in_class = True
                elif code[j] == ']':
                    in_class = False
                elif code[j] == '\n':
                    break
                j += 1
            if j < n and code[j] == '/':
                i = j + 1
                prev_sig = 'x'
                continue
        if c in '([{':
            stack.append((c, i))
        elif c in ')]}':
            if not stack or stack[-1][0] != pairs[c]:
                return i, f'"{c}" has no matching "{pairs[c]}"'
            stack.pop()
        if not c.isspace():
            if c.isalnum() or c in '_$':
                word = word_re.match(code, i).group()  # match in place: copying the rest of a big script per word is very slow
                prev_sig = 'return' if word in ('return', 'typeof', 'case', 'in', 'of', 'else', 'do', 'void', 'delete',
                                                 'throw', 'new', 'yield', 'await', 'instanceof') else 'x'
                i += len(word)
                continue
            prev_sig = c if c in '(,=:[!&|?{};' else 'op' if c in '+-*%<>~^' else 'x'
        i += 1
    if stack:
        c, off = stack[-1]
        return off, ('text string starting with ` is never closed' if c == '${' else f'"{c}" is never closed')
    return None


def scan(html_path, game_dir=None, progress=None):
    import fixes
    res = Scanner(html_path, game_dir, progress).run()
    fixes.enrich(res)
    return res


def scan_any(path, game_dir=None, progress=None):
    """Scan an HTML game file, or a game folder / script file of another engine."""
    import formats
    return formats.scan_path(path, game_dir, progress)
