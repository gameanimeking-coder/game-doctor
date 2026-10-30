"""Tiny translation layer.

T('English text with {name}', name=x) looks the English text up in the current
language file and fills in the {placeholders}. Missing translations fall back to
English, so nothing ever shows up blank.

Language files are lang/<code>.txt:
    #name Español
    <id> <translation>          (one per line; id = first 8 hex digits of sha1(English text))

Technical-mode texts from the scanner stay English on purpose: they quote code,
and game engines, their manuals and error messages are English.
"""
import hashlib
import os
import sys

_table = {}
_code = 'en'
_ids = {}
LANGUAGES = {}   # code -> native name, filled by available()


def text_id(english):
    i = _ids.get(english)
    if i is None:
        i = _ids[english] = hashlib.sha1(english.encode('utf-8')).hexdigest()[:8]
    return i


def _lang_dir():
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'lang')


def read_file(path):
    name, table = None, {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.rstrip('\n').rstrip('\r')
            if line.startswith('#name '):
                name = line[6:].strip()
            elif len(line) > 9 and line[8] == ' ' and not line.startswith('#'):
                table[line[:8]] = line[9:]
    return name, table


def available():
    LANGUAGES.clear()
    LANGUAGES['en'] = 'English'
    d = _lang_dir()
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith('.txt'):
                try:
                    with open(os.path.join(d, f), encoding='utf-8') as fh:
                        first = fh.readline().strip()
                    if first.startswith('#name '):
                        LANGUAGES[f[:-4]] = first[6:].strip()
                except OSError:
                    pass
    return LANGUAGES


def set_language(code):
    global _table, _code
    _code = code or 'en'
    _table = {}
    if _code != 'en':
        try:
            _, _table = read_file(os.path.join(_lang_dir(), _code + '.txt'))
        except OSError:
            _code, _table = 'en', {}
    return _code


def current():
    return _code


def T(_english, **kw):
    s = _table.get(text_id(_english)) or _english if _table else _english
    if kw:
        try:
            return s.format(**kw)
        except (KeyError, IndexError, ValueError):
            return _english.format(**kw)   # broken translation: fall back to English
    return s
