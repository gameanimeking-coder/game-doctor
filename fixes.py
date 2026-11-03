"""Adds hand-holding data to every finding:

  key         stable identity (for ignore lists, "check me", compare versions)
  old / new   the exact line now, and the same line fixed (when the fix is certain)
  search      text to search for in an editor
  difficulty  'easy' | 'medium' | 'dev' | 'optional'
  priority    higher = fix first
  advice      can this be ignored?

Suggestions are only made when the change is mechanical and certain; anything
that needs a decision (story logic, missing content) gets none.
"""
import os
import re

from i18n import T
import scanner

PLAYER_VISIBLE = {'Broken links', 'Missing files', 'Macro tags', 'Unknown macros', 'Code', 'Conditions',
                  'Unfinished content', 'Broken files', 'Variables', 'JavaScript', 'Play-test'}
LOW_IMPACT = {'Unused files', 'Portability', 'Large files', 'Outdated code', 'Spelling', 'Text', 'Unused passages',
              'Widgets', 'Story setup', 'CSS'}


def loose_key(i):
    return f'{i.category}|{i.title}|{i.passage}|{os.path.basename(i.file or "")}'


def make_key(i):
    return loose_key(i) + '|' + re.sub(r'\s+', ' ', (i.snippet or '').strip())[:160]


def enrich(res):
    for i in res.issues:
        enrich_issue(i, res)


def enrich_issue(i, res):
    i.key = make_key(i)
    line = ''
    if i.line:
        lines = res.lines_of(i.file)
        if 0 < i.line <= len(lines):
            line = lines[i.line - 1].rstrip('\r')
    new = None
    if line:
        try:
            new = suggest(i, line, res)
        except Exception:
            new = None
    if new is not None and new != line:
        i.old, i.new = line, new
    rn = rename_of(i, res)
    if rn:
        i.rename = rn
        pat = re.compile(re.escape(rn[0]) + r'(?![\w])')
        i.also_lines = [n for n, l in enumerate(res.lines_of(i.file), 1) if n != i.line and pat.search(l)][:200]
    i.search = search_text(i, line)
    i.difficulty = difficulty(i)
    i.priority = priority(i)
    i.advice = advice(i)


# ---------------------------------------------------------------------- the two ways people edit
def escape_twine(s):
    """How Twine stores passage text inside the published .html file."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;')


def twine_text(line):
    """A file line reduced to the passage text you see in Twine (no <tw-passagedata> wrapper)."""
    if '<tw-passagedata' in line:
        line = line[line.rfind('<tw-passagedata'):]
        line = line[line.find('>') + 1:]
    if '</tw-passagedata>' in line:
        line = line[:line.find('</tw-passagedata>')]
    return line


def forms(i, res, mode):
    """(old, new) as the user should see/copy them.
    mode 'twine': text as shown in the Twine editor. mode 'file': text as stored in the file.
    Returns None when the fix cannot be expressed in that form."""
    if not i.old and i.new == '':
        return None
    if res.engine != 'twine':
        return i.old, i.new
    if mode == 'twine':
        return twine_text(i.old), twine_text(i.new)
    raw = res.raw_lines[i.line - 1].rstrip('\r') if res.raw_lines and 0 < i.line <= len(res.raw_lines) else ''
    if not raw:
        return None
    old, new = i.old, i.new
    a = 0
    while a < min(len(old), len(new)) and old[a] == new[a]:
        a += 1
    b = 0
    while b < min(len(old), len(new)) - a and old[len(old) - 1 - b] == new[len(new) - 1 - b]:
        b += 1
    for k in (40, 20, 10, 5, 2, 0):
        pa, pb = min(k, a), min(k, b)
        o_seg = old[a - pa:len(old) - b + pb]
        n_seg = new[a - pa:len(new) - b + pb]
        eo = escape_twine(o_seg)
        if eo and raw.count(eo) == 1:
            return raw, raw.replace(eo, escape_twine(n_seg))
    return None


def search_for(i, res, mode):
    s = i.search
    if res.engine == 'twine' and mode == 'file':
        return escape_twine(s)
    return s


# ---------------------------------------------------------------------- suggestions
def _sep_like(path, like):
    return path.replace('/', '\\') if '\\' in like else path


def _replace_word(line, word, repl, prefix=''):
    pat = re.escape(prefix + word) + r'(?![\w])'
    return re.sub(pat, (prefix + repl).replace('\\', r'\\'), line)


def _first_macro(line, names):
    for m in re.finditer(r'<<(%s)\b(.*?)>>|\((%s):(.*?)\)' % ('|'.join(names), '|'.join(names)), line):
        return m
    return None


def suggest(i, line, res):
    t = i.title
    m = re.match(r'^Closing tag <</(.+)> is missing a ">"$', t)
    if m:
        return re.sub(re.escape(f'<</{m.group(1)}>') + r'(?!>)', f'<</{m.group(1)}>>', line, count=1)
    if t == 'Extra ">" after a macro tag':
        return re.sub(r'(<<[A-Za-z/][^<>\n]*?>>)>+', r'\1', line, count=1)
    if t == 'Stray ">" after an HTML tag':
        return re.sub(r'(?<!<)(<[A-Za-z][\w-]*(?:\s[^<>]*)?>)>+(?!>)', r'\1', line, count=1)
    if t == 'Extra "<" before a macro tag':
        return re.sub(r'<<<+(?=[A-Za-z/])', '<<', line, count=1)
    if t == 'Stray "<<" with no macro name':
        return '' if line.strip() == '<<' else re.sub(r'<<(?=\s*$)', '', line, count=1)
    m = re.match(r'^<</(\w[\w-]*)>> has no opening', t)
    if m and line.count(f'<</{m.group(1)}>>') == 1 and line.strip() == f'<</{m.group(1)}>>':
        return ''
    if t == '<<set>> compares instead of setting':
        return re.sub(r'(<<set\s+[$_][\w.]+)\s*(?:===|==|\bis\b|\beq\b)\s*', r'\1 to ', line, count=1)
    if t == '<<set>> does not change anything':
        return re.sub(r'(<<set\s+[$_][\w.]+)\s*([-+*/])\s*(?!=)', r'\1 \2= ', line, count=1)
    if t == 'Single "=" inside a condition' or t.endswith('uses "=" instead of "to"'):
        names = ['if', 'elseif', 'else-if', 'unless'] if 'condition' in t else ['set', 'put', 'move']
        repl = ' to ' if 'instead of "to"' in t else (' === ' if (res.stats.get('format', '').lower().startswith('chapbook')) else ' is ')
        if res.stats.get('format', '').lower().startswith('chapbook'):
            m = re.search(r'\[(if|unless)\b([^\]]*)\]', line)
            if m:
                fixed = re.sub(r'\s*(?<![=!<>])=(?![=>])\s*', repl, m.group(2))
                return line[:m.start(2)] + fixed + line[m.end(2):]
            return None
        m = _first_macro(line, names)
        if m:
            g = 2 if m.group(1) else 4
            expr = m.group(g)
            if re.search(r'(?<![=!<>])=(?![=>])', re.sub(r'"[^"]*"|\'[^\']*\'', '""', expr)):
                fixed = re.sub(r'\s*(?<![=!<>])=(?![=>])\s*', repl, expr, count=0 if 'condition' in t else 1)
                return line[:m.start(g)] + fixed + line[m.end(g):]
        return None
    if t.startswith('Wrong comparison operator'):
        return line.replace('=>', '>=').replace('=<', '<=')
    if t == '"is not" instead of "isnot"':
        return re.sub(r'\bis\s+not\b', 'isnot', line, count=1)
    m = re.match(r'^"(\S+) is (\S+) or (\S+)" does not compare both$', t)
    if m:
        v, a, b = m.groups()
        return re.sub(rf'{re.escape(v)}\s+is\s+{re.escape(a)}\s+or\s+{re.escape(b)}', f'{v} is {a} or {v} is {b}', line, count=1)
    m = re.match(r'^Unknown macro <<(.+)>>$', t)
    if m:
        near = re.search(r'very close to <<(.+?)>>', i.what)
        if near:
            return re.sub(r'<<' + re.escape(m.group(1)) + r'(?![\w-])', '<<' + near.group(1), line, count=1)
        return None
    m = re.match(r'^Unknown macro \((.+):\)$', t)
    if m:
        near = re.search(r'very close to \((.+?):\)', i.what)
        return re.sub(r'\(' + re.escape(m.group(1)) + r'\s*:', f'({near.group(1)}:', line, count=1) if near else None
    m = re.match(r'^Unknown command \*(\w+)$', t)
    if m:
        near = re.search(r'Did you mean \*(\w+)\?', i.what)
        return re.sub(r'\*' + re.escape(m.group(1)) + r'\b', '*' + near.group(1), line, count=1) if near else None
    m = re.match(r'^Unknown insert \{(.+?): \.\.\.\}$', t)
    if m:
        near = re.search(r'Did you mean "(.+?)"\?', i.what)
        return line.replace('{' + m.group(1), '{' + near.group(1), 1) if near else None
    m = re.search(r'to missing (?:passage|label|knot/stitch|scene) "(.+?)"', t)
    if m:
        near = re.search(r'Did you mean "(.+?)"\?', i.fix + ' ' + i.what) or re.search(r'Change it to "(?:-> )?(.+?)"', i.fix)
        if near and m.group(1) in line:
            return line.replace(m.group(1), near.group(1), 1)
        return None
    m = re.match(r'^Screen "(.+?)" does not exist$', t)
    if m:
        near = re.search(r'Did you mean "(.+?)"\?', i.what)
        return _replace_word(line, m.group(1), near.group(1)) if near else None
    m = re.match(r'^\$(\w+) is (?:used but never set|set but never checked or shown)$', t)
    if m:
        near = re.search(r'misspelling of \$(\w+)|reads \$(\w+)', i.what)
        if near:
            return _replace_word(line, m.group(1), near.group(1) or near.group(2), '$')
        return None
    m = re.match(r'^\$(\w+) and \$(\w+) differ only in capital letters$', t)
    if m:
        return _replace_word(line, m.group(1), m.group(2), '$')
    m = re.match(r'^s\.(\w+) is used but never set$', t)
    if m:
        near = re.search(r'Did you mean s\.(\w+)\?', i.what)
        return _replace_word(line, m.group(1), near.group(1), 's.') if near else None
    m = re.match(r'^(\w+) is used but never set$', t)
    if m:
        near = re.search(r'Did you mean (\w+)\?', i.what)
        return _replace_word(line, m.group(1), near.group(1)) if near else None
    m = re.match(r'^"(\w+)" looks like a misspelling of "(\w+)"$', t)
    if m:
        return re.sub(r'\b' + re.escape(m.group(1)) + r'\b', m.group(2), line)
    m = re.match(r'^(?:\*set on undeclared variable|Assignment to undeclared variable) "(\w+)"$', t)
    if m:
        near = re.search(r'Did you mean "(\w+)"\?', i.what)
        return re.sub(r'\b' + re.escape(m.group(1)) + r'\b', near.group(1), line, count=1) if near else None
    m = re.match(r'^\$\{(\w+)\} is not a declared variable$', t)
    if m:
        near = re.search(r'Did you mean "(\w+)"\?', i.what)
        return line.replace('{' + m.group(1) + '}', '{' + near.group(1) + '}') if near else None
    m = re.match(r'^"\$(\w+)-(\w+)" is read as', t)
    if m:
        return line.replace(f'${m.group(1)}-{m.group(2)}', f'${m.group(1)}_{m.group(2)}')
    m = re.match(r'^\w+ not found: (.+)$', t)
    if m:
        cand = re.search(r'file meant is probably "(.+?)"', i.what)   # only when the name is clearly mistyped
        if cand and m.group(1) in line:
            return line.replace(m.group(1), _sep_like(cand.group(1), m.group(1)), 1)
        return None
    m = re.match(r'^\w+ path has wrong capital letters: (.+)$', t)
    if m:
        real = re.search(r'"(.+?)"', i.what)
        if real and m.group(1) in line:
            return line.replace(m.group(1), _sep_like(real.group(1), m.group(1)), 1)
        return None
    m = re.match(r"^\w+ uses a full path from someone's PC: (.+)$", t)
    if m and m.group(1) in line:
        parts = re.split(r'[\\/]', m.group(1))
        for k in range(1, len(parts)):
            rel = '/'.join(parts[k:])
            if os.path.exists(os.path.join(res.game_dir, rel)):
                return line.replace(m.group(1), _sep_like(rel, m.group(1)), 1)
        return None
    m = re.match(r'^Class "(.+?)" does not match any style \(did you mean "(.+?)"\?\)$', t)
    if m:
        return re.sub(r'(class\s*=\s*["\'][^"\']*?)(?<![\w-])' + re.escape(m.group(1)) + r'(?![\w-])', r'\g<1>' + m.group(2), line, count=1)
    m = re.match(r'^"(.+)" → "(.+)"$', t)
    if m and '(' not in m.group(2) and '" or "' not in m.group(2):
        orig, corr = m.groups()
        corr = corr[0].upper() + corr[1:] if orig[0].isupper() else corr
        return re.sub(r'\b' + re.escape(orig) + r'\b', corr, line, count=1)
    m = re.match(r'^Repeated word "(\S+) \S+"$', t)
    if m:
        return re.sub(r'\b(' + re.escape(m.group(1)) + r')\s+\1\b', r'\1', line, count=1, flags=re.I)
    if t == 'Tab character used for indentation':
        lead = line[:len(line) - len(line.lstrip())]
        return lead.replace('\t', '    ') + line.lstrip()
    m = re.match(r'^"(\w+)" line is missing ":" at the end$', t)
    if m:
        return line.rstrip() + ':'
    return None


def rename_of(i, res=None):
    """(wrong, right) when the fix renames a variable, class or file path that may be used on several lines."""
    t = i.title
    m = re.match(r'^\w+ not found: (.+)$', t)
    if m:
        cand = re.search(r'file meant is probably "(.+?)"', i.what)
        return (m.group(1), _sep_like(cand.group(1), m.group(1))) if cand else ()
    m = re.match(r'^\w+ path has wrong capital letters: (.+)$', t)
    if m:
        real = re.search(r'"(.+?)"', i.what)
        return (m.group(1), _sep_like(real.group(1), m.group(1))) if real else ()
    m = re.match(r"^\w+ uses a full path from someone's PC: (.+)$", t)
    if m and res is not None:
        parts = re.split(r'[\\/]', m.group(1))
        for k in range(1, len(parts)):
            rel = '/'.join(parts[k:])
            if os.path.exists(os.path.join(res.game_dir, rel)):
                return (m.group(1), _sep_like(rel, m.group(1)))
        return ()
    m = re.match(r'^\$(\w+) is (?:used but never set|set but never checked or shown)$', t)
    if m:
        near = re.search(r'misspelling of \$(\w+)|reads \$(\w+)', i.what)
        return ('$' + m.group(1), '$' + (near.group(1) or near.group(2))) if near else ()
    m = re.match(r'^\$(\w+) and \$(\w+) differ only in capital letters$', t)
    if m:
        return ('$' + m.group(1), '$' + m.group(2))
    m = re.match(r'^"(\w+)" looks like a misspelling of "(\w+)"$', t)
    if m:
        return (m.group(1), m.group(2))
    m = re.match(r'^Class "(.+?)" does not match any style \(did you mean "(.+?)"\?\)$', t)
    if m:
        return (m.group(1), m.group(2))
    return ()


def apply_rename(line, rename):
    wrong, right = rename
    return re.sub(re.escape(wrong) + r'(?![\w])', right.replace('\\', r'\\'), line)


def search_text(i, line):
    """A short, distinctive piece of text to find the spot with Ctrl+F."""
    if i.old and i.new is not None:
        # the part that changes, with a little context
        a = 0
        while a < min(len(i.old), len(i.new)) and i.old[a] == i.new[a]:
            a += 1
        start = max(0, a - 25)
        s = i.old[start:a + 35].strip()
        if len(s) >= 4:
            return s
    snip = (i.snippet or '').replace('…', '').strip()
    if line:
        for cand in re.findall(r'<<[^<>]{2,60}>>|<[a-z][^<>]{2,60}>|\[\[[^\]]{1,60}\]\]|\$\w+', snip):
            if cand in line:
                return cand
    return snip[:60] if snip else ''


def difficulty(i):
    if i.new is not None and i.old:
        return 'easy'
    t, c = i.title, i.category
    if c in ('Unused files', 'Portability', 'Large files', 'Outdated code') or (i.severity == scanner.INFO and c in ('HTML', 'CSS', 'Widgets', 'Story setup', 'Text')):
        return 'optional'
    if c in ('Unused passages', 'Unfinished content', 'Passages', 'Story setup') or 'mixes "and" and "or"' in t \
            or ('missing passage' in t and 'Did you mean' not in i.fix) or c == 'Play-test':
        return 'dev'
    return 'medium'


EFFORT = {'easy': 'About 1 minute', 'medium': '5-15 minutes', 'dev': 'Needs the developer (a story or design decision)',
          'optional': 'Optional'}


def effort_text(i):
    return T(EFFORT.get(i.difficulty, ''))


def difficulty_label(i):
    return T({'easy': 'Easy fix', 'medium': 'Takes a little care', 'dev': 'Needs the developer',
              'optional': 'Optional'}.get(i.difficulty, ''))


def priority(i):
    p = {scanner.ERROR: 100, scanner.WARNING: 60, scanner.INFO: 10}[i.severity]
    if i.category in PLAYER_VISIBLE:
        p += 20
    if i.category in LOW_IMPACT:
        p -= 5
    p += {'easy': 10, 'medium': 0, 'dev': -10, 'optional': -15}.get(i.difficulty, 0)
    if 'name looks mistyped' in i.what:
        p += 10   # a broken link with an obvious typo: quick for the developer to check and fix
    return p


def advice(i):
    t, c = i.title, i.category
    if i.unreachable:
        return T("Yes. It is in a scene nobody can reach, so players never see it. Fix it only if you add a link to that scene later.")
    if i.severity == scanner.ERROR:
        return T('No. Players will run into this.')
    if (getattr(i, 'settle', None) or {}).get('state') == 'fine':
        return T('Yes. Game Doctor checked it and nothing is wrong.')
    if 'mixes "and" and "or"' in t:
        return T('Only if you have checked the logic and it does what you want. Then mark it as intentional.')
    if c == 'Unused passages':
        return T('Yes, if the scene is old or not meant to be played.')
    if c in ('Unused files', 'Large files'):
        return T('Yes. Nothing breaks.')
    if c == 'Portability':
        return T('Yes, if the game is only played on Windows.')
    if c == 'Outdated code':
        return T('Yes, for now. It may break in a future engine update.')
    if c in ('Spelling', 'Text'):
        return T('Yes, but players will see the typo.')
    if c == 'Unfinished content':
        return T('Only if the scene is unfinished on purpose.')
    if c == 'HTML' and 'invalid unit' in t:
        return T('Yes. Pictures still show.')
    if c == 'CSS' and 'has no style rule' in t:
        return T('Yes. Nothing visible changes.')
    if c == 'Variables' and i.severity == scanner.INFO:
        return T('Usually yes. It is often left over from older versions.')
    if i.severity == scanner.WARNING:
        return T('Better not. It is very likely a real mistake.')
    return T('Probably yes, but have a quick look.')
