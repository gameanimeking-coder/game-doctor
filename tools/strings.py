"""Collect every translatable text and check language files.

  python tools/strings.py extract        writes lang/en.json (the list of all texts)
  python tools/strings.py check          checks every lang/*.json: missing texts, broken {placeholders}
"""
import ast
import json
import os
import re
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ['app.py', 'ui_dialogs.py', 'simple.py', 'content.py', 'fixes.py', 'reach.py']
DICT_NAMES = {'_SEV', '_MEANING', '_CATEGORY', '_KIND', 'EFFORT'}
EXTRA = ['Excellent', 'Great', 'Good', 'Fine', 'Needs work', 'Poor',
         'Easy fix', 'Takes a little care', 'Needs the developer', 'Optional',
         # progress messages sent by scanner.py, formats.py and playtest.py
         'Reading file', 'Finding story data', 'Collecting scripts and styles', 'Checking passages and links',
         'Checking macro tags', 'Checking conditions and code logic', 'Checking variables', 'Checking HTML tags',
         'Checking CSS classes', 'Checking images, audio, video and files', 'Checking text (typos, placeholders)',
         'Checking JavaScript', 'Checking story-format rules', 'Done', 'Reading the {label} game', 'Checking files for damage',
         'Starting the invisible browser', 'Loading the game', "Checking scripts with the browser's JavaScript engine",
         'Playing scenes ({mode}): {k} of {n}', 'Play-test finished']
# New texts the user chose to keep English-only for now (shown in English in every language).
# Texts starting with these words are not counted as missing.
ENGLISH_ONLY = ('The name looks mistyped: the file meant is probably',
                'Put the file in the game folder with exactly this name. Only change the name')


def extract():
    found = []
    for name in SOURCES:
        tree = ast.parse(open(os.path.join(ROOT, name), encoding='utf-8').read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'T' and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    found.append(a.value)
                elif isinstance(a, ast.Subscript) and isinstance(a.value, ast.Dict) or isinstance(a, ast.Call):
                    for sub in ast.walk(a):
                        if isinstance(sub, ast.Dict):
                            found += [v.value for v in sub.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) in DICT_NAMES for t in node.targets):
                if isinstance(node.value, ast.Dict):
                    found += [v.value for v in node.value.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    found += EXTRA
    seen, out = set(), []
    for s in found:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def placeholders(s):
    return sorted({f[1] for f in string.Formatter().parse(s) if f[1] is not None})


def check():
    sys.path.insert(0, ROOT)
    import i18n
    texts = extract()
    ids = [i18n.text_id(t) for t in texts]
    ok = len(set(ids)) == len(ids)
    if not ok:
        print('  [FAIL] two texts share an id')
    d = os.path.join(ROOT, 'lang')
    for f in sorted(os.listdir(d)):
        if not f.endswith('.txt'):
            continue
        name, by_id = i18n.read_file(os.path.join(d, f))
        tr = {t: by_id[i18n.text_id(t)] for t in texts if i18n.text_id(t) in by_id}
        extra = set(by_id) - set(ids)
        if extra:
            print(f'       {len(extra)} unused lines in {f}')
        missing = [t for t in texts if t not in tr and not t.startswith(ENGLISH_ONLY)]
        bad = [t for t in texts if t in tr and placeholders(t) != placeholders(tr[t])]
        broken = []
        for t in texts:
            if t in tr:
                try:
                    string.Formatter().parse(tr[t]) and list(string.Formatter().parse(tr[t]))
                except ValueError:
                    broken.append(t)
        same = [t for t in texts if tr.get(t) == t and re.search(r'[a-z]{4}', t)]
        status = 'ok' if not (missing or bad or broken) else 'FAIL'
        if status == 'FAIL':
            ok = False
        print(f'  [{status}] {f}: {len(tr)} texts, {len(missing)} missing, {len(bad)} wrong placeholders, '
              f'{len(broken)} broken, {len(same)} left in English')
        for t in (missing[:5] + bad[:5] + broken[:5]):
            print('       ', repr(t[:90]))
    eo = [t for t in texts if t.startswith(ENGLISH_ONLY)]
    if eo:
        print(f'  ({len(eo)} texts are English-only for now; see ENGLISH_ONLY)')
    return ok


if __name__ == '__main__':
    if sys.argv[1:] == ['extract']:
        sys.path.insert(0, ROOT)
        import i18n
        texts = extract()
        with open(os.path.join(ROOT, 'tools', 'english.txt'), 'w', encoding='utf-8') as f:
            f.write('#name English (source texts for translators)\n')
            for t in texts:
                f.write(f'{i18n.text_id(t)} {t}\n')
        print(len(texts), 'texts,', sum(len(t) for t in texts), 'characters -> tools/english.txt')
    else:
        sys.exit(0 if check() else 1)
