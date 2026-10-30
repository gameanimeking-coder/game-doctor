"""Engine detection and scanners for non-HTML game engines:
Ren'Py, RPG Maker MV/MZ, Ink and ChoiceScript. HTML games go to scanner.Scanner.

Every engine scanner returns a scanner.ScanResult; issues carry `file` and `line`
so the window can show and open the right file.
"""
import collections
import difflib
import glob
import json
import os
import re
import time

import knowledge as K
import media
import scanner
from scanner import ERROR, WARNING, INFO, Issue, ScanResult, _close, _typo_of, _js_bracket_problem, health_score

SKIP_DIRS = {'.git', '.svn', 'node_modules', '__pycache__', '.claude', '.vscode', '.idea', 'Game Bug Scanner', 'Game Doctor',
             'cache', 'saves', 'tl'}


# ---------------------------------------------------------------------- detection
def detect(path):
    """Return (engine, main_path, root_dir)."""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        low = path.lower()
        folder = os.path.dirname(path)
        if low.endswith(('.html', '.htm')):
            return 'html', path, folder
        if low.endswith('.rpy'):
            root = folder
            while os.path.basename(root).lower() != 'game' and os.path.dirname(root) != root:
                if os.path.isdir(os.path.join(root, 'game')):
                    root = os.path.join(root, 'game')
                    break
                root = os.path.dirname(root)
            if os.path.basename(root).lower() != 'game':
                root = folder
            return 'renpy', path, root
        if low.endswith('.ink'):
            return 'ink', path, folder
        if os.path.basename(low) == 'system.json' or low.endswith('.rpgproject'):
            d = folder
            if os.path.basename(low) == 'system.json':
                d = os.path.dirname(folder)
            return 'rpgmaker', os.path.join(_rpg_data(d) or folder, 'System.json'), d
        if low.endswith('.txt'):
            scenes = folder
            return 'choicescript', path, scenes
        return 'html', path, folder
    d = path
    if _rpg_data(d):
        return 'rpgmaker', os.path.join(_rpg_data(d), 'System.json'), d
    for cand in ('game', '.'):
        rp = glob.glob(os.path.join(d, cand, '*.rpy'))
        if rp:
            gd = os.path.normpath(os.path.join(d, cand))
            main = next((f for f in rp if os.path.basename(f).lower() == 'script.rpy'), sorted(rp)[0])
            return 'renpy', main, gd
    for cand in ('scenes', os.path.join('mygame', 'scenes'), os.path.join('web', 'mygame', 'scenes'), '.'):
        s = os.path.join(d, cand, 'startup.txt')
        if os.path.isfile(s):
            return 'choicescript', s, os.path.dirname(s)
    inks = glob.glob(os.path.join(d, '*.ink'))
    if inks:
        return 'ink', _ink_main(inks), d
    htmls = sorted(glob.glob(os.path.join(d, '*.htm*')), key=lambda f: -os.path.getsize(f))
    for f in htmls:
        with open(f, encoding='utf-8', errors='replace') as fh:
            head = fh.read(400000)
        if '<tw-storydata' in head or 'tiddler="' in head:
            return 'html', f, d
    if htmls:
        return 'html', htmls[0], d
    raise FileNotFoundError('No game found in this folder. Pick the game\'s .html file, or a Ren\'Py, RPG Maker, '
                            'Ink or ChoiceScript game folder.')


def _rpg_data(d):
    for cand in ('data', os.path.join('www', 'data')):
        if os.path.isfile(os.path.join(d, cand, 'System.json')):
            return os.path.join(d, cand)
    return None


def _ink_main(inks):
    included = set()
    for f in inks:
        try:
            for m in re.finditer(r'^\s*INCLUDE\s+(.+?)\s*$', open(f, encoding='utf-8', errors='replace').read(), re.M):
                included.add(os.path.normcase(os.path.join(os.path.dirname(f), m.group(1))))
        except OSError:
            pass
    rest = [f for f in inks if os.path.normcase(f) not in included]
    return sorted(rest or inks)[0]


def scan_path(path, game_dir=None, progress=None):
    engine, main, root = detect(path)
    if engine == 'html':
        res = scanner.Scanner(main, game_dir or root, progress).run()
    else:
        cls = {'renpy': RenPy, 'rpgmaker': RpgMaker, 'ink': Ink, 'choicescript': ChoiceScript}[engine]
        res = cls(main, game_dir or root, progress).run()
    import fixes
    fixes.enrich(res)
    return res


# ---------------------------------------------------------------------- shared base
class EngineScan:
    engine = ''
    label = ''

    def __init__(self, main, root, progress=None):
        self.main = os.path.abspath(main)
        self.root = os.path.abspath(root)
        self.progress = progress or (lambda p, m: None)
        self.issues = []
        self.files = {}          # path -> lines
        self.units = 0           # labels / knots / scenes / maps, for the health score
        self.unit_names = {}     # name -> (file, line)
        self.known_vars = set()
        self._index = None

    def add(self, severity, category, title, file='', line=0, where='', **kw):
        self.issues.append(Issue(severity, category, title, line, where, file=file or '', **kw))

    def read(self, path):
        if path not in self.files:
            try:
                with open(path, encoding='utf-8-sig', errors='replace') as f:
                    self.files[path] = f.read().split('\n')
            except OSError:
                self.files[path] = []
        return self.files[path]

    def index(self):
        """lower-case relative path -> real relative path, for every file under root."""
        if self._index is None:
            self._index = {}
            self._all = []
            for r, dirs, fnames in os.walk(self.root):
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith('.')]
                for fn in fnames:
                    full = os.path.join(r, fn)
                    rel = os.path.relpath(full, self.root).replace('\\', '/')
                    self._index[rel.lower()] = rel
                    self._all.append((rel, full))
        return self._index

    def check_ref(self, ref, base=None, exts=None):
        """('ok'|'case'|'missing', real_or_suggestion) for a file reference."""
        idx = self.index()
        base = base or self.root
        tries = [ref] if not exts else [ref + e for e in exts]
        for t in tries:
            rel = os.path.relpath(os.path.normpath(os.path.join(base, t.replace('\\', '/'))), self.root).replace('\\', '/')
            real = idx.get(rel.lower())
            if real:
                return ('ok', real) if real == rel else ('case', real)
        name = os.path.basename(tries[0].replace('\\', '/')).lower()
        folder = os.path.dirname(os.path.relpath(os.path.normpath(os.path.join(base, tries[0].replace('\\', '/'))), self.root).replace('\\', '/')).lower()
        pool = [v for k, v in idx.items() if os.path.dirname(k) == folder]
        real = next((p for p in pool if scanner.file_typo(name, p)), None)
        if real is None:
            near = difflib.get_close_matches(name, [os.path.basename(p).lower() for p in pool], n=1, cutoff=0.75)
            return ('missing', ('near', next(p for p in pool if os.path.basename(p).lower() == near[0]))) if near else ('missing', None)
        return ('missing', real)

    def file_issue(self, ref, status, real, file, line, where, kind='File', snippet=''):
        if status == 'missing':
            near = real[1] if isinstance(real, tuple) else None
            real = None if near else real
            self.add(ERROR, 'Missing files', f'{kind} not found: {ref}', file, line, where, snippet=snippet or ref,
                     what=f'The game uses "{ref}", but there is no such file.'
                          + (f' The name looks mistyped: the file meant is probably "{real}".' if real else '')
                          + ((' Files with similar names exist (e.g. "{near}"), but in games those are usually different pictures '
                            '(another character, another scene, or one of several random versions).').format(near=os.path.basename(near)) if near else ''),
                     fix=(f'Check whether "{real}" is the right file. If it is, correct the name in the game yourself; '
                                   'if not, put the missing file in the game folder.' if real
                          else 'Put the missing file in the game folder with exactly this name. Only change the name '
                               'in the game if you know which file was meant.'))
        elif status == 'case':
            self.add(WARNING, 'Missing files', f'{kind} path has wrong capital letters: {ref}', file, line, where, snippet=snippet or ref,
                     what=f'The real file is "{real}". Works on Windows, but not on Mac, Linux, Android or the web.',
                     fix=f'Change it to match "{real}".')

    def placeholders(self, path, lines, text_re=None):
        ph = re.compile(r'\b(%s)\b' % '|'.join(re.escape(w) for w in K.PLACEHOLDER_WORDS))
        for n, l in enumerate(lines, 1):
            for m in ph.finditer(l):
                if text_re and not text_re(l):
                    continue
                self.add(WARNING, 'Unfinished content', f'"{m.group(1)}" text shown to players', path, n, snippet=l.strip(),
                         what='This looks like a note the developer left in. Players may see it.',
                         fix='Write the real content, or remove the note.')

    def damaged_media(self):
        self.index()
        for rel, full in self._all:
            ext = rel.rsplit('.', 1)[-1].lower() if '.' in rel.rsplit('/', 1)[-1] else ''
            if ext in K.MEDIA_EXT:
                r = media.check_file(full)
                if r and r[0] in ('cut', 'html', 'unknown'):
                    what = {'cut': 'The file is cut off (incomplete download or copy).',
                            'html': 'The file is a web page/data file, not real media.',
                            'unknown': 'The file does not look like a real ' + ext.upper() + ' file.'}[r[0]]
                    self.add(ERROR if r[0] != 'unknown' else WARNING, 'Broken files', f'Damaged file: {rel}', full, 0,
                             snippet=rel, what=what, fix='Copy or download this file again.')

    def run(self):
        t0 = time.time()
        self.progress(5, ('Reading the {label} game', {'label': self.label}))
        try:
            self.scan()
        except Exception as e:
            self.add(INFO, 'Scanner', f'The {self.label} check stopped early: {e}',
                     what='The scanner hit something it did not expect. Other results are still valid.')
        self.progress(90, 'Checking files for damage')
        try:
            self.damaged_media()
        except Exception:
            pass
        self.issues.sort(key=lambda i: (scanner.SEVERITY_ORDER[i.severity], i.category, i.file, i.line))
        main_lines = self.read(self.main)
        res = ScanResult(self.issues, {}, main_lines, self.main, self.root)
        res.files = {p: l for p, l in self.files.items() if os.path.normcase(p) != os.path.normcase(self.main)}
        res.engine = self.engine
        res.known_vars = self.known_vars
        res.passages = {n: (ln, []) for n, (f, ln) in self.unit_names.items()}
        counts = collections.Counter(i.severity for i in self.issues)
        self.index()
        res.stats = {'format': self.label, 'passages': self.units, 'errors': counts[ERROR], 'warnings': counts[WARNING],
                     'infos': counts[INFO], 'seconds': round(time.time() - t0, 2),
                     'assets_referenced': getattr(self, 'refs', 0), 'files_in_folder': len(self._all)}
        for i in self.issues:  # health is per unit (label/knot/scene/map)
            if not i.passage and i.file:
                i.passage = os.path.relpath(i.file, self.root) if i.file.startswith(self.root) else os.path.basename(i.file)
        res.stats['health'], res.stats['health_label'] = health_score(self.issues, max(self.units, len(self.files), 1))
        self.progress(100, 'Done')
        return res


def _strip_py_strings(line):
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', line)


# ---------------------------------------------------------------------- Ren'Py
def rpa_names(path):
    """File names inside a Ren'Py .rpa archive (RPA-2.0/3.0), read from its index without unpacking.
    The index is a pickle; it is read with an unpickler that refuses to build any object, so it is safe."""
    import io
    import pickle
    import zlib

    class Safe(pickle.Unpickler):
        def find_class(self, module, name):
            raise pickle.UnpicklingError('not allowed')
    try:
        with open(path, 'rb') as f:
            head = f.readline().decode('ascii', 'replace').split()
            if not head or head[0] not in ('RPA-3.0', 'RPA-2.0') or len(head) < 2:
                return set()
            f.seek(int(head[1], 16))
            index = Safe(io.BytesIO(zlib.decompress(f.read()))).load()
        return {str(k).replace('\\', '/') for k in index} if isinstance(index, dict) else set()
    except Exception:
        return set()


class RenPy(EngineScan):
    engine, label = 'renpy', "Ren'Py"
    SPECIAL = {'start', 'splashscreen', 'main_menu', 'after_load', 'quit', 'after_warp', 'before_main_menu',
               'hide_windows', 'save_screenshot', 'main_menu_screen'}
    BUILTINS = {'True', 'False', 'None', 'persistent', 'config', 'renpy', 'store', 'gui', 'preferences', 'build',
                'len', 'str', 'int', 'float', 'range', 'abs', 'min', 'max', 'round', 'not', 'and', 'or', 'in', 'is',
                'if', 'else', 'elif', 'for', 'while', 'list', 'dict', 'set', 'tuple', '_', '__', 'achievement',
                'im', 'ui', 'Character', 'Image', 'Transform', 'Solid', 'Text', 'Movie', 'Dissolve', 'Fade',
                'dissolve', 'fade', 'pixellate', 'move', 'ease', 'moveinright', 'moveinleft', 'vpunch', 'hpunch',
                'random', 'math', 'time', 'datetime', 'print', 'sum', 'any', 'all', 'sorted', 'enumerate', 'zip',
                'lambda', 'return', 'self', 'nvl', 'narrator', 'extend', 'centered', '_return', 'main_menu', 'screen'}

    def scan(self):
        files = []
        for r, dirs, fnames in os.walk(self.root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith('.')]
            files += [os.path.join(r, f) for f in fnames if f.lower().endswith('.rpy')]
        labels, screens, images = {}, set(), set()
        jumps = []                     # (target, file, line, kind)
        defined, assigned, used = set(), set(), []
        media_refs = []
        for path in sorted(files):
            lines = self.read(path)
            for n, raw in enumerate(lines, 1):
                line = raw.rstrip('\r')
                code = _strip_py_strings(line.split('#', 1)[0] if '"' not in line.split('#', 1)[0] else line)
                if '\t' in line[:len(line) - len(line.lstrip())]:
                    self.add(ERROR, 'Code', 'Tab character used for indentation', path, n, snippet=line,
                             what="Ren'Py does not allow tab characters for indenting. The game will not start.",
                             fix='Replace the tab with spaces (4 spaces per level).')
                s = code.strip()
                m = re.match(r'label\s+([\w.]+)\s*(\(.*\))?\s*:', s)
                if m:
                    name = m.group(1)
                    if name in labels:
                        f0, l0 = labels[name]
                        self.add(ERROR, 'Passages', f'Label "{name}" is defined twice', path, n, name, snippet=line,
                                 what=f'"{name}" is also defined in {os.path.basename(f0)} line {l0}. Ren\'Py refuses to start.',
                                 fix='Rename one of the two labels.')
                    labels[name] = (path, n)
                    self.unit_names[name] = (path, n)
                m = re.match(r'screen\s+(\w+)', s)
                if m:
                    screens.add(m.group(1))
                m = re.match(r'image\s+([\w ]+?)\s*(=|:)', s)
                if m:
                    images.add(' '.join(m.group(1).split()))
                m = re.match(r'layeredimage\s+([\w ]+?)\s*:', s)
                if m:
                    images.add(' '.join(m.group(1).split()))
                for m in re.finditer(r'\b(jump|call)\s+(?!expression\b|screen\b)([\w.]+)', s):
                    if s.startswith(('jump', 'call')) or re.search(r'\bJump\(|\bCall\(', s) is None:
                        jumps.append((m.group(2), path, n, m.group(1)))
                for m in re.finditer(r'\b(Jump|Call)\(\s*["\']([\w.]+)["\']', line):
                    jumps.append((m.group(2), path, n, m.group(1).lower()))
                m = re.match(r'(?:call|show|hide)\s+screen\s+(\w+)', s)
                if m:
                    jumps.append(('screen:' + m.group(1), path, n, 'screen'))
                m = re.match(r'(define|default)\s+([\w.]+)\s*=', s)
                if m:
                    defined.add(m.group(2).split('.')[-1] if m.group(2).startswith(('persistent.', 'config.', 'gui.')) else m.group(2))
                m = re.match(r'\$\s*([A-Za-z_]\w*)\s*(=|\+=|-=|\*=|/=)', s)
                if m:
                    assigned.add(m.group(1))
                for m in re.finditer(r'\b(?:for)\s+([A-Za-z_]\w*)\s+in\b', s):
                    assigned.add(m.group(1))
                if re.match(r'(if|elif|while)\b', s):
                    for v in re.findall(r'\b([A-Za-z_]\w*)\b', s[3:]):
                        used.append((v, path, n, line))
                for m in re.finditer(r'\[([A-Za-z_]\w*)(?:[.!\]:])', line):
                    if '"' in line:
                        used.append((m.group(1), path, n, line))
                code = '' if s.startswith('#') else line   # a "# ..." line is a note, not code
                for m in re.finditer(r'(?:play|queue)\s+(?:music|sound|voice|audio|\w+)\s+["\']([^"\']+)["\']|\bvoice\s+["\']([^"\']+)["\']', code):
                    media_refs.append((m.group(1) or m.group(2), path, n, line, 'Audio'))
                for m in re.finditer(r'["\']([^"\'\[\]{}]+\.(?:png|jpe?g|webp|gif|avif|ogg|mp3|wav|opus|webm|mp4|ogv|ttf|otf))["\']', code, re.I):
                    if not any(m.group(1) == r[0] for r in media_refs if r[1] == path and r[2] == n):
                        media_refs.append((m.group(1), path, n, line, K.asset_kind(m.group(1).rsplit('.', 1)[-1])))
                block = re.match(r'(if|elif|else|while|menu)\b', s)
                # a condition can continue on the next lines inside brackets, or after a "\"
                open_brackets = s.count('(') + s.count('[') + s.count('{') > s.count(')') + s.count(']') + s.count('}')
                if block and ':' not in s and not open_brackets and not s.endswith('\\'):
                    self.add(ERROR, 'Code', f'"{block.group(1)}" line is missing ":" at the end', path, n, snippet=line,
                             what=f'In Ren\'Py, a "{block.group(1)}" line must end with a colon, followed by an indented block. '
                                  'The game will not start.',
                             fix='Add ":" at the end of the line.')
            self.placeholders(path, lines, text_re=lambda l: '"' in l)
        self.units = len(labels) or len(files)
        # image files in images/ define images automatically
        idx = self.index()
        for rel in idx.values():
            if rel.lower().startswith('images/') and rel.rsplit('.', 1)[-1].lower() in K.IMAGE_EXT:
                images.add(' '.join(os.path.splitext(os.path.basename(rel))[0].replace('_', ' ').lower().split()))
        label_list = sorted(labels)
        used_labels = collections.Counter()
        for target, path, n, kind in jumps:
            if target.startswith('screen:'):
                sname = target[7:]
                if sname not in screens and sname not in ('say', 'choice', 'nvl', 'main_menu', 'game_menu', 'navigation',
                                                         'save', 'load', 'preferences', 'history', 'help', 'about',
                                                         'confirm', 'skip_indicator', 'notify', 'input', 'quick_menu'):
                    near = _close(sname, sorted(screens), cutoff=0.8)
                    self.add(ERROR, 'Broken links', f'Screen "{sname}" does not exist', path, n, snippet=self.files[path][n - 1],
                             what=f'No "screen {sname}:" is defined.' + (f' Did you mean "{near}"?' if near else ''),
                             fix=(f'Change it to "{near}".' if near else 'Define the screen, or fix the name.'))
                continue
            name = target.lstrip('.')
            full = name
            if target.startswith('.'):
                parents = [l for l, (f, ln) in labels.items() if f == path and ln < n and '.' not in l]
                if parents:
                    full = max(parents, key=lambda l: labels[l][1]) + target
            if full in labels or name in labels or any(l.endswith('.' + name) for l in labels):
                used_labels[full if full in labels else name] += 1
                continue
            near = _close(name, label_list, cutoff=0.8)
            self.add(ERROR, 'Broken links', f'{kind.capitalize()} to missing label "{target}"', path, n,
                     snippet=self.files[path][n - 1],
                     what=f'There is no "label {target}:". The game stops with an error when it gets here.'
                          + (f' Did you mean "{near}"?' if near else ''),
                     fix=(f'Change it to "{near}".' if near else 'Create the label, or fix the name.'))
        for name, (path, n) in labels.items():
            if used_labels[name] == 0 and name not in self.SPECIAL and not name.startswith('_') and '.' not in name:
                self.add(INFO, 'Unused passages', f'Label "{name}" is never jumped to or called', path, n, name,
                         what='Nothing in the scripts jumps to or calls this label (the story may still flow into it '
                              'from the label above, if there is no return/jump before it).',
                         fix='If players should reach it, add a jump or call to it.')
        known = defined | assigned | self.BUILTINS | set(labels) | screens
        self.known_vars = defined | assigned
        known_list = sorted(defined | assigned)
        seen = set()
        for v, path, n, line in used:
            if v in known or v in seen or v.isupper() or len(v) < 3:
                continue
            near = _typo_of(v, known_list)
            if near:
                seen.add(v)
                self.add(WARNING, 'Variables', f'"{v}" looks like a misspelling of "{near}"', path, n, snippet=line,
                         what=f'"{v}" is never defined or assigned, but "{near}" is. The game will stop with "NameError".',
                         fix=f'Change "{v}" to "{near}".')
        self.refs = len(media_refs)
        packed = set()
        for rel, full in self._all:
            if rel.lower().endswith('.rpa'):
                packed |= {x.lower() for x in rpa_names(full)}
        if self._all and any(rel.lower().endswith('.rpa') for rel, _ in self._all) and not packed:
            self.add(INFO, 'Missing files', 'Packed .rpa archives could not be read', '', 0, '',
                     what='This game keeps files in .rpa archives in a format the scanner cannot read, so missing pictures '
                          'and sounds inside them may be reported wrongly.',
                     fix='Unpack the archives (e.g. with unrpa or rpatool) and scan again.')
        for ref, path, n, line, kind in media_refs:
            if re.search(r'[\[\]{}%]', ref):
                continue
            status, real = self.check_ref(ref, self.root)
            if status != 'ok' and kind in ('Image', 'Audio'):
                # Ren'Py also looks in game/images for pictures and game/audio for sounds
                st2, real2 = self.check_ref(ref, os.path.join(self.root, 'images' if kind == 'Image' else 'audio'))
                if st2 == 'ok':
                    continue
                if status == 'missing' and st2 == 'case':
                    status, real = st2, real2
            if status == 'case':
                continue   # Ren'Py finds files whatever their capital letters
            if status == 'missing' and packed:
                r = ref.replace('\\', '/').lower()
                sub = 'images/' if kind == 'Image' else 'audio/' if kind == 'Audio' else ''
                if r in packed or sub + r in packed:
                    continue   # inside a .rpa archive
            self.file_issue(ref, status, real, path, n, '', kind, line.strip())
        for path in sorted(files):
            for n, line in enumerate(self.files[path], 1):
                m = re.match(r'\s*(show|scene)\s+(?!expression\b|screen\b)([A-Za-z_][\w ]*?)(?:\s+(?:at|with|as|behind|onlayer|zorder)\b.*)?\s*:?\s*$', line)
                if m:
                    tag = m.group(2).split()[0].lower() if m.group(2).split() else ''
                    if tag in ('black', 'white', 'bg') and tag in [i.split()[0] for i in images]:
                        continue
                    if tag and tag not in ('black', 'text') and not any(i.split()[0].lower() == tag for i in images if i):
                        near = _close(tag, sorted({i.split()[0].lower() for i in images if i}), cutoff=0.8)
                        self.add(WARNING, 'Missing files', f'Image "{m.group(2).strip()}" is not defined', path, n, snippet=line.strip(),
                                 what=f'No image named "{tag} ..." is defined and no file in images/ matches. '
                                      'Players see a placeholder or the game shows an error.' + (f' Did you mean "{near}"?' if near else ''),
                                 fix=(f'Change it to "{near}".' if near else 'Add the picture to images/ or define it with an "image" statement.'))


# ---------------------------------------------------------------------- RPG Maker MV / MZ
class RpgMaker(EngineScan):
    engine, label = 'rpgmaker', 'RPG Maker MV/MZ'
    AUDIO_EXT = ['.ogg', '.m4a', '.rpgmvo', '.ogg_', '.m4a_']
    IMG_EXT = ['.png', '.rpgmvp', '.png_']

    def scan(self):
        data = os.path.dirname(self.main)
        base = os.path.dirname(data)        # folder with img/ audio/ js/
        self.base = base
        self.label = 'RPG Maker MZ' if os.path.isfile(os.path.join(base, 'js', 'rmmz_core.js')) else 'RPG Maker MV'
        system = self.load(os.path.join(data, 'System.json')) or {}
        switches = system.get('switches') or []
        variables = system.get('variables') or []
        mapinfos = self.load(os.path.join(data, 'MapInfos.json')) or []
        common = self.load(os.path.join(data, 'CommonEvents.json')) or []
        map_ids = {m['id'] for m in mapinfos if m}
        common_ids = {c['id'] for c in common if c}
        self.refs = 0
        self.seen_refs = {}
        for key in ('title1Name', 'title2Name'):
            if system.get(key):
                self.asset(f'img/{"titles1" if key == "title1Name" else "titles2"}/' + system[key], self.IMG_EXT, 'System.json', 'title screen')
        for key in ('titleBgm', 'battleBgm', 'victoryMe', 'defeatMe', 'gameoverMe'):
            a = system.get(key) or {}
            if a.get('name'):
                folder = 'bgm' if key.endswith('Bgm') else 'me'
                self.asset(f'audio/{folder}/' + a['name'], self.AUDIO_EXT, 'System.json', key)
        maps = sorted(glob.glob(os.path.join(data, 'Map[0-9][0-9][0-9].json')))
        self.units = len(maps) + len([c for c in common if c])
        for mi in mapinfos:
            if mi and not os.path.isfile(os.path.join(data, f'Map{mi["id"]:03d}.json')):
                self.add(ERROR, 'Missing files', f'Map file missing: Map{mi["id"]:03d}.json ({mi.get("name", "")})',
                         os.path.join(data, 'MapInfos.json'), 0, what='The map list mentions this map, but its file is missing.',
                         fix='Restore the map file from a backup or the original game.')
        for path in maps:
            mp = self.load(path)
            if not mp:
                continue
            mid = int(re.search(r'Map(\d+)', path).group(1))
            mname = next((m.get('name') for m in mapinfos if m and m['id'] == mid), '')
            self.unit_names[f'Map {mid} {mname}'.strip()] = (path, 0)
            for key, folder in (('parallaxName', 'parallaxes'), ('battleback1Name', 'battlebacks1'), ('battleback2Name', 'battlebacks2')):
                if mp.get(key):
                    self.asset(f'img/{folder}/' + mp[key], self.IMG_EXT, path, f'Map {mid} "{mname}" settings')
            for a_key, folder in (('bgm', 'bgm'), ('bgs', 'bgs')):
                a = mp.get(a_key) or {}
                if a.get('name'):
                    self.asset(f'audio/{folder}/' + a['name'], self.AUDIO_EXT, path, f'Map {mid} "{mname}" settings')
            for ev in mp.get('events') or []:
                if not ev:
                    continue
                for pn, page in enumerate(ev.get('pages') or [], 1):
                    where = f'Map {mid} "{mname}", event {ev["id"]} "{ev.get("name", "")}", page {pn}'
                    img = page.get('image') or {}
                    if img.get('characterName'):
                        self.asset('img/characters/' + img['characterName'], self.IMG_EXT, path, where)
                    cond = page.get('conditions') or {}
                    if cond.get('switch1Valid'):
                        self.check_id('switch', cond.get('switch1Id'), switches, path, where)
                    if cond.get('switch2Valid'):
                        self.check_id('switch', cond.get('switch2Id'), switches, path, where)
                    if cond.get('variableValid'):
                        self.check_id('variable', cond.get('variableId'), variables, path, where)
                    self.commands(page.get('list') or [], path, where, switches, variables, map_ids, common_ids)
        for c in common:
            if c:
                self.unit_names[f'Common event {c["id"]} {c.get("name", "")}'.strip()] = (os.path.join(data, 'CommonEvents.json'), 0)
                self.commands(c.get('list') or [], os.path.join(data, 'CommonEvents.json'),
                              f'Common event {c["id"]} "{c.get("name", "")}"', switches, variables, map_ids, common_ids)
        for fname in ('Troops.json',):
            troops = self.load(os.path.join(data, fname)) or []
            for tr in troops:
                if tr:
                    for pn, page in enumerate(tr.get('pages') or [], 1):
                        self.commands(page.get('list') or [], os.path.join(data, fname),
                                      f'Troop {tr["id"]} "{tr.get("name", "")}" page {pn}', switches, variables, map_ids, common_ids)
        self.plugins()

    def load(self, path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                txt = f.read()
            self.files.setdefault(path, txt.split('\n'))
            return json.loads(txt)
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as e:
            self.add(ERROR, 'Broken files', f'Damaged data file: {os.path.basename(path)}', path, 0,
                     what=f'This file is not valid JSON ({e}). The game will not start.',
                     fix='Restore it from a backup, or fix the broken part shown in the error.')
            return None

    def asset(self, ref, exts, file, where):
        self.refs += 1
        key = ref.lower()
        if key in self.seen_refs:
            return
        status, real = self.check_ref(ref, self.base, exts)
        self.seen_refs[key] = status
        kind = 'Audio' if ref.startswith('audio/') else 'Image'
        self.file_issue(ref, status, real, file, 0, where, kind, ref)

    def check_id(self, kind, i, names, file, where):
        if i is None:
            return
        if i <= 0 or i >= len(names):
            self.add(ERROR, 'Variables', f'{kind.capitalize()} #{i} does not exist', file, 0, where,
                     what=f'The game only has {max(0, len(names) - 1)} {kind}es/s; #{i} is outside that list.',
                     fix=f'Pick an existing {kind}, or add more in the Database (System tab).')
        elif not (names[i] or '').strip():
            self.add(INFO, 'Variables', f'{kind.capitalize()} #{i} has no name', file, 0, where,
                     what=f'This {kind} is used but has no name in the Database, which makes it easy to mix up.',
                     fix=f'Give {kind} #{i} a name in the Database.')

    def commands(self, cmds, file, where, switches, variables, map_ids, common_ids):
        for c in cmds:
            code, p = c.get('code'), c.get('parameters') or []
            try:
                if code in (241, 245, 249, 250) and p and isinstance(p[0], dict) and p[0].get('name'):
                    folder = {241: 'bgm', 245: 'bgs', 249: 'me', 250: 'se'}[code]
                    self.asset(f'audio/{folder}/' + p[0]['name'], self.AUDIO_EXT, file, where)
                elif code == 231 and len(p) > 1 and p[1]:
                    self.asset('img/pictures/' + p[1], self.IMG_EXT, file, where)
                elif code == 101 and p and p[0]:
                    self.asset('img/faces/' + p[0], self.IMG_EXT, file, where)
                elif code == 322 and len(p) > 5:
                    for idx, folder in ((1, 'characters'), (3, 'faces'), (5, 'sv_actors')):
                        if p[idx]:
                            self.asset(f'img/{folder}/' + p[idx], self.IMG_EXT, file, where)
                elif code == 283 and p:
                    for idx, folder in ((0, 'battlebacks1'), (1, 'battlebacks2')):
                        if len(p) > idx and p[idx]:
                            self.asset(f'img/{folder}/' + p[idx], self.IMG_EXT, file, where)
                elif code == 284 and p and p[0]:
                    self.asset('img/parallaxes/' + p[0], self.IMG_EXT, file, where)
                elif code == 261 and p and p[0]:
                    self.asset('movies/' + p[0], ['.webm', '.mp4'], file, where)
                elif code == 121 and len(p) > 1:
                    for i in range(p[0], p[1] + 1):
                        self.check_id('switch', i, switches, file, where)
                elif code == 122 and len(p) > 1:
                    for i in range(p[0], p[1] + 1):
                        self.check_id('variable', i, variables, file, where)
                elif code == 111 and len(p) > 1:
                    if p[0] == 0:
                        self.check_id('switch', p[1], switches, file, where)
                    elif p[0] == 1:
                        self.check_id('variable', p[1], variables, file, where)
                elif code == 201 and len(p) > 1 and p[0] == 0 and p[1] not in map_ids:
                    self.add(ERROR, 'Broken links', f'Transfer to missing map #{p[1]}', file, 0, where,
                             what='This event moves the player to a map that does not exist. The game crashes or freezes there.',
                             fix='Pick an existing map in the Transfer Player command.')
                elif code == 117 and p and p[0] not in common_ids:
                    self.add(ERROR, 'Broken links', f'Call to missing common event #{p[0]}', file, 0, where,
                             what='This event calls a common event that does not exist.', fix='Pick an existing common event.')
                elif code == 205 and len(p) > 1 and isinstance(p[1], dict):
                    for mc in p[1].get('list') or []:
                        mp = mc.get('parameters') or []
                        if mc.get('code') == 41 and mp and mp[0]:
                            self.asset('img/characters/' + mp[0], self.IMG_EXT, file, where)
                        elif mc.get('code') == 44 and mp and isinstance(mp[0], dict) and mp[0].get('name'):
                            self.asset('audio/se/' + mp[0]['name'], self.AUDIO_EXT, file, where)
                elif code == 401 and p and isinstance(p[0], str):
                    if re.search(r'\b(PLACEHOLDER|TODO|FIXME)\b', p[0]):
                        self.add(WARNING, 'Unfinished content', 'Placeholder text shown to players', file, 0, where, snippet=p[0],
                                 what='A message box contains a developer note.', fix='Write the real text.')
            except (TypeError, IndexError, KeyError, AttributeError):
                continue

    def plugins(self):
        pj = os.path.join(self.base, 'js', 'plugins.js')
        if not os.path.isfile(pj):
            return
        txt = open(pj, encoding='utf-8-sig', errors='replace').read()
        self.files[pj] = txt.split('\n')
        for m in re.finditer(r'\{"name":"([^"]+)","status":(true|false)', txt):
            name, on = m.group(1), m.group(2) == 'true'
            fp = os.path.join(self.base, 'js', 'plugins', name + '.js')
            line = txt.count('\n', 0, m.start()) + 1
            if not os.path.isfile(fp):
                if on:
                    self.add(ERROR, 'Missing files', f'Plugin file missing: {name}.js', pj, line, 'Plugin list',
                             what='This plugin is switched on, but its file is missing. The game shows an error at start.',
                             fix=f'Put {name}.js in js/plugins/, or switch the plugin off in the Plugin Manager.')
                continue
            if on:
                code = open(fp, encoding='utf-8-sig', errors='replace').read()
                prob = _js_bracket_problem(code)
                if prob:
                    ln = code.count('\n', 0, prob[0]) + 1
                    self.files[fp] = code.split('\n')
                    self.add(ERROR, 'JavaScript', f'Plugin {name}.js: {prob[1]}', fp, ln, 'Plugin',
                             snippet=self.files[fp][ln - 1].strip()[:200],
                             what='The plugin has an unclosed or extra bracket or quote, so it fails to load.',
                             fix='Check the brackets and quotes around this line, or re-download the plugin.')


# ---------------------------------------------------------------------- Ink
class Ink(EngineScan):
    engine, label = 'ink', 'Ink'
    BUILTIN_TARGETS = {'END', 'DONE'}

    def scan(self):
        files, queue = [], [self.main]
        while queue:
            f = queue.pop()
            if f in files:
                continue
            files.append(f)
            for n, line in enumerate(self.read(f), 1):
                m = re.match(r'\s*INCLUDE\s+(.+?)\s*$', line)
                if m:
                    inc = os.path.join(os.path.dirname(f), m.group(1))
                    if os.path.isfile(inc):
                        queue.append(inc)
                    else:
                        self.add(ERROR, 'Missing files', f'Included file not found: {m.group(1)}', f, n, snippet=line,
                                 what='INCLUDE names a file that does not exist. Ink will not compile.', fix='Fix the file name.')
        knots, stitches, labels = {}, {}, set()
        globals_, temps = set(), set()
        diverts, assigns, prints = [], [], []
        for f in files:
            knot = None
            for n, raw in enumerate(self.read(f), 1):
                line = re.sub(r'//.*$', '', raw)
                m = re.match(r'\s*={2,}\s*(?:function\s+)?(\w+)\s*(\(([^)]*)\))?\s*=*\s*$', line)
                if m:
                    knot = m.group(1)
                    if knot in knots:
                        self.add(ERROR, 'Passages', f'Knot "{knot}" is defined twice', f, n, knot, snippet=raw,
                                 what='Two knots share this name. Ink will not compile.', fix='Rename one of them.')
                    knots[knot] = (f, n)
                    self.unit_names[knot] = (f, n)
                    for prm in re.findall(r'(?:ref\s+)?(\w+)', m.group(3) or ''):
                        temps.add(prm)
                    continue
                m = re.match(r'\s*=\s*(\w+)\s*(\(([^)]*)\))?\s*$', line)
                if m:
                    stitches[f'{knot}.{m.group(1)}' if knot else m.group(1)] = (f, n)
                    for prm in re.findall(r'(?:ref\s+)?(\w+)', m.group(3) or ''):
                        temps.add(prm)
                    continue
                for lm in re.finditer(r'^\s*(?:[*+-]\s*)+\((\w+)\)', line):
                    labels.add(lm.group(1))
                    if knot:
                        labels.add(f'{knot}.{lm.group(1)}')
                m = re.match(r'\s*(VAR|CONST|LIST)\s+(\w+)\s*=', line)
                if m:
                    globals_.add(m.group(2))
                    if m.group(1) == 'LIST':
                        globals_.update(re.findall(r'\(?(\w+)\)?', line.split('=', 1)[1]))
                for tm in re.finditer(r'~\s*temp\s+(\w+)', line):
                    temps.add(tm.group(1))
                for dm in re.finditer(r'->\s*([\w.]+)', line):
                    diverts.append((dm.group(1), knot, f, n, raw))
                am = re.match(r'\s*~\s*(?!temp\b|return\b)(\w+)\s*(=|\+=|-=|\+\+|--)', line)
                if am:
                    assigns.append((am.group(1), f, n, raw))
                for pm in re.finditer(r'\{\s*(\w+)\s*\}', line):
                    prints.append((pm.group(1), f, n, raw))
            # curly brackets may span lines (multi-line conditions), so count over the whole file
            depth, last_open = 0, 0
            for n, raw in enumerate(self.read(f), 1):
                line = re.sub(r'//.*$', '', re.sub(r'"(?:\\.|[^"\\])*"', '""', raw))
                for ch in line:
                    if ch == '{':
                        depth += 1
                        last_open = n
                    elif ch == '}':
                        depth -= 1
                        if depth < 0:
                            self.add(ERROR, 'Macro tags', 'Extra "}" with no matching "{"', f, n, snippet=raw,
                                     what='There is a "}" with no "{" before it. Ink will not compile.', fix='Remove the extra "}".')
                            depth = 0
            if depth > 0:
                self.add(ERROR, 'Macro tags', '"{" is never closed with "}"', f, last_open, snippet=self.read(f)[last_open - 1],
                         what='A "{" block is never closed. Ink will not compile.', fix='Add the missing "}".')
            self.placeholders(f, self.read(f))
        self.units = len(knots) or 1
        targets = set(knots) | set(stitches) | labels | self.BUILTIN_TARGETS | globals_ | temps
        names = sorted(set(knots) | set(stitches) | labels)
        used = collections.Counter()
        for tgt, knot, f, n, raw in diverts:
            if tgt in targets or (knot and f'{knot}.{tgt}' in targets) or tgt.split('.')[0] in globals_ | temps:
                used[tgt.split('.')[0]] += 1
                continue
            near = _close(tgt, names, cutoff=0.8)
            self.add(ERROR, 'Broken links', f'Divert to missing knot/stitch "{tgt}"', f, n, knot or '', snippet=raw.strip(),
                     what=f'"-> {tgt}" goes to something that does not exist. Ink will not compile.' + (f' Did you mean "{near}"?' if near else ''),
                     fix=(f'Change it to "-> {near}".' if near else 'Create the knot or fix the name.'))
        declared = globals_ | temps | set(knots)
        self.known_vars = globals_
        for v, f, n, raw in assigns:
            if v not in declared:
                near = _typo_of(v, sorted(declared))
                self.add(ERROR, 'Variables', f'Assignment to undeclared variable "{v}"', f, n, snippet=raw.strip(),
                         what=f'"{v}" is set but never declared with VAR (or ~ temp). Ink will not compile.'
                              + (f' Did you mean "{near}"?' if near else ''),
                         fix=(f'Change it to "{near}".' if near else f'Declare it at the top: VAR {v} = 0'))
        for v, f, n, raw in prints:
            if v not in declared and v not in labels and v not in ('true', 'false'):
                near = _typo_of(v, sorted(declared))
                if near:
                    self.add(WARNING, 'Variables', f'"{v}" looks like a misspelling of "{near}"', f, n, snippet=raw.strip(),
                             what=f'{{{v}}} prints something that is never declared.', fix=f'Change it to {{{near}}}.')
        for k, (f, n) in knots.items():
            if used[k] == 0 and f == self.main and n != min(ln for ff, ln in knots.values() if ff == f):
                self.add(INFO, 'Unused passages', f'Knot "{k}" is never diverted to', f, n, k,
                         what='No "-> ' + k + '" exists, so players never reach it (unless it is called as a function).',
                         fix='Add a divert to it, or remove it if it is left over.')


# ---------------------------------------------------------------------- ChoiceScript
class ChoiceScript(EngineScan):
    engine, label = 'choicescript', 'ChoiceScript'
    COMMANDS = set('''
achieve achievement allow_reuse author bug check_achievements check_purchase check_registration choice comment create
delay_break delay_ending disable_reuse else elseif elsif ending fake_choice finish gosub gosub_scene goto goto_random_scene
goto_scene gotoref hide_reuse if image input_number input_text label line_break link link_button looplimit more_games
page_break params print purchase purchase_discount rand redirect_scene reset restart restore_game restore_purchases return
save_game scene_list selectable_if set setref share_this_game show_password sound stat_chart subscribe temp text_image
title youtube kindle_search kindle_product abort create_array temp_array delete config ifid product
'''.split())

    def scan(self):
        scenes_dir = os.path.dirname(self.main)
        startup = os.path.join(scenes_dir, 'startup.txt')
        scene_list, created = [], set()
        for n, line in enumerate(self.read(startup), 1):
            if re.match(r'\s*\*create\s+(\w+)', line):
                created.add(re.match(r'\s*\*create\s+(\w+)', line).group(1).lower())
            elif re.match(r'\s*\*create_array\s+(\w+)\s+(\d+)', line):
                m = re.match(r'\s*\*create_array\s+(\w+)\s+(\d+)', line)
                for i in range(1, int(m.group(2)) + 1):
                    created.add(f'{m.group(1)}_{i}'.lower())
                created.add(m.group(1).lower())
        in_list = False
        for n, line in enumerate(self.read(startup), 1):
            if re.match(r'\s*\*scene_list', line):
                in_list = True
                continue
            if in_list:
                if line.strip() and (line.startswith((' ', '\t'))):
                    scene_list.append((line.strip().lstrip('$ ').strip(), n))
                elif line.strip():
                    in_list = False
        files = sorted(glob.glob(os.path.join(scenes_dir, '*.txt')))
        scene_names = {os.path.splitext(os.path.basename(f))[0].lower(): f for f in files}
        for s, n in scene_list:
            if s.lower() not in scene_names:
                near = _close(s.lower(), sorted(scene_names), cutoff=0.8)
                self.add(ERROR, 'Missing files', f'Scene "{s}" in the scene list has no file', startup, n, 'startup',
                         snippet=self.files[startup][n - 1], what=f'There is no {s}.txt in the scenes folder.' + (f' Did you mean "{near}"?' if near else ''),
                         fix=(f'Change it to "{near}".' if near else f'Create {s}.txt or remove it from *scene_list.'))
        self.units = len(files)
        labels_by_scene = {}
        for f in files:
            sname = os.path.splitext(os.path.basename(f))[0].lower()
            labels_by_scene[sname] = {}
            for n, line in enumerate(self.read(f), 1):
                m = re.match(r'\s*\*label\s+(\S+)', line)
                if m:
                    labels_by_scene[sname][m.group(1).lower()] = n
        cmds = sorted(self.COMMANDS)
        for f in files:
            sname = os.path.splitext(os.path.basename(f))[0].lower()
            lines = self.read(f)
            temps = set()
            for line in lines:
                m = re.match(r'\s*\*temp\s+(\w+)', line)
                if m:
                    temps.add(m.group(1).lower())
                m = re.match(r'\s*\*params\s+(.*)', line)
                if m:
                    temps.update(v.lower() for v in m.group(1).split())
            known = created | temps
            known_list = sorted(known)
            indent_kind = None
            self.unit_names[sname] = (f, 1)
            for n, line in enumerate(lines, 1):
                lead = line[:len(line) - len(line.lstrip())]
                if lead:
                    kind = 'tab' if '\t' in lead else 'space'
                    if '\t' in lead and ' ' in lead:
                        kind = 'mixed'
                    if kind == 'mixed' or (indent_kind and kind != indent_kind):
                        self.add(ERROR, 'Code', 'Tabs and spaces mixed for indenting', f, n, sname, snippet=line,
                                 what='ChoiceScript stops with an error when a scene mixes tabs and spaces for indenting.',
                                 fix='Use only spaces (or only tabs) in the whole scene.')
                        indent_kind = kind if kind != 'mixed' else indent_kind
                    indent_kind = indent_kind or kind
                s = line.strip()
                m = re.match(r'\*(\w+)\b\s*(.*)', s)
                if m:
                    cmd, args = m.group(1).lower(), m.group(2)
                    if cmd not in self.COMMANDS:
                        near = _close(cmd, cmds, cutoff=0.75)
                        self.add(ERROR, 'Unknown macros', f'Unknown command *{cmd}', f, n, sname, snippet=line,
                                 what=f'ChoiceScript has no *{cmd} command.' + (f' Did you mean *{near}?' if near else ''),
                                 fix=(f'Change it to *{near}.' if near else 'Check the spelling.'))
                        continue
                    if cmd in ('goto', 'gosub'):
                        tgt = args.split()[0].lower() if args.split() else ''
                        if tgt and not tgt.startswith('{') and tgt not in labels_by_scene.get(sname, {}):
                            near = _close(tgt, sorted(labels_by_scene.get(sname, {})), cutoff=0.8)
                            self.add(ERROR, 'Broken links', f'*{cmd} to missing label "{tgt}"', f, n, sname, snippet=line,
                                     what=f'There is no "*label {tgt}" in this scene.' + (f' Did you mean "{near}"?' if near else ''),
                                     fix=(f'Change it to "{near}".' if near else 'Add the label or fix the name.'))
                    elif cmd in ('goto_scene', 'gosub_scene', 'redirect_scene'):
                        parts = args.split()
                        if parts and not parts[0].startswith('{'):
                            sc = parts[0].lower()
                            if sc not in scene_names:
                                near = _close(sc, sorted(scene_names), cutoff=0.8)
                                self.add(ERROR, 'Broken links', f'*{cmd} to missing scene "{parts[0]}"', f, n, sname, snippet=line,
                                         what=f'There is no {parts[0]}.txt.' + (f' Did you mean "{near}"?' if near else ''),
                                         fix=(f'Change it to "{near}".' if near else 'Create the scene or fix the name.'))
                            elif len(parts) > 1 and parts[1].lower() not in labels_by_scene.get(sc, {}):
                                self.add(ERROR, 'Broken links', f'*{cmd} to missing label "{parts[1]}" in {parts[0]}', f, n, sname,
                                         snippet=line, what=f'Scene {parts[0]} has no "*label {parts[1]}".', fix='Fix the label name.')
                    elif cmd == 'set':
                        v = args.split()[0].lower() if args.split() else ''
                        if v and not v.startswith('{') and v not in known:
                            near = _typo_of(v, known_list)
                            self.add(ERROR, 'Variables', f'*set on undeclared variable "{v}"', f, n, sname, snippet=line,
                                     what=f'"{v}" was never created with *create (startup) or *temp. The game stops with an error.'
                                          + (f' Did you mean "{near}"?' if near else ''),
                                     fix=(f'Change it to "{near}".' if near else f'Add "*create {v} 0" to startup.txt.'))
                    elif cmd == 'image':
                        img = args.split()[0] if args.split() else ''
                        if img:
                            status, real = self.check_ref(img, os.path.dirname(scenes_dir))
                            self.file_issue(img, status, real, f, n, sname, 'Image', line)
                for vm in re.finditer(r'[$@]!{0,2}\{(\w+)\}', line):
                    v = vm.group(1).lower()
                    if v not in known and v not in ('choice_randomtest', 'choice_quicktest'):
                        near = _typo_of(v, known_list)
                        self.add(ERROR if near else WARNING, 'Variables', f'${{{vm.group(1)}}} is not a declared variable', f, n, sname,
                                 snippet=line, what='This variable was never created, so the game stops with an error here.'
                                                    + (f' Did you mean "{near}"?' if near else ''),
                                 fix=(f'Change it to ${{{near}}}.' if near else f'Create it with *create in startup.txt.'))
            self.placeholders(f, lines, text_re=lambda l: not l.strip().startswith('*'))
        self.known_vars = created
