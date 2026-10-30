"""'How do players reach this scene?' for Twine games.

For a passage, list every passage that links to / includes / jumps to it, and the
<<if>> conditions that must be true at that spot for the link to appear.
"""
import re

from i18n import T

TOKEN = re.compile(r'<<(/?)(if|elseif|else)\b(.*?)>>|\((if|unless|else-if|elseif|else):(.*?)\)', re.S)


def conditions_at(text, offset):
    """The chain of conditions that must hold at `offset` (SugarCube and Harlowe)."""
    stack = []          # each: [list of previous branch conditions, current condition]
    for m in TOKEN.finditer(text, 0, offset):
        if m.group(2):   # SugarCube
            closing, kind, expr = m.group(1) == '/', m.group(2), m.group(3).strip()
            if closing:
                if stack:
                    stack.pop()
            elif kind == 'if':
                stack.append([[], expr])
            elif stack:
                prev, cur = stack[-1]
                prev.append(cur)
                stack[-1] = [prev, expr if kind == 'elseif' else None]
    out = []
    for prev, cur in stack:
        parts = [f'not ({p})' for p in prev if p]
        if cur:
            parts.append(cur)
        if parts:
            out.append(' and '.join(parts))
    return out


def plain(expr):
    """Make a SugarCube condition a bit easier to read."""
    e = expr
    for a, b in ((' gte ', ' ≥ '), (' lte ', ' ≤ '), (' gt ', ' > '), (' lt ', ' < '), ('>=', '≥'), ('<=', '≤'),
                 ('&&', T('and')), ('||', T('or')), (' isnot ', ' ≠ '), (' is ', ' = '), ('==', '='), ('!=', '≠'),
                 (' true', ' ' + T('true')), (' false', ' ' + T('false'))):
        e = e.replace(a, b)
    e = re.sub(r'\bnot\b', T('not'), e)
    e = re.sub(r'\band\b', T('and'), e)
    e = re.sub(r'\bor\b', T('or'), e)
    return re.sub(r'\s+', ' ', e).strip()


def how_to_reach(res, passage):
    """Return list of dicts: source, kind, conditions (list of str)."""
    out = []
    for src, dst, off, kind in res.link_edges:
        if dst != passage:
            continue
        text = res.passage_texts.get(src, '')
        conds = conditions_at(text, off) if text else []
        out.append(dict(source=src, kind=kind, conditions=conds))
    return out


def describe(res, passage, simple=True):
    ways = how_to_reach(res, passage)
    if not ways:
        start = res.context.start_passage if res.context is not None else ''
        if passage == start:
            return T('This is the first scene of the game.')
        return T('No scene links to "{name}" in a way the scanner can follow. Players may never see it, '
                 'or it is reached through code that builds the scene name from variables.', name=passage)
    lines = [T('Players reach "{name}" from {n} place(s):', name=passage, n=len(ways))]
    seen = set()
    for w in ways:
        cond = ' ' + T('and') + ' '
        c = cond.join((plain(x) if simple else x) for x in w['conditions'])
        key = (w['source'], c)
        if key in seen:
            continue
        seen.add(key)
        how = {'Link': T('a link'), '<<include>>': T('an include'), '<<goto>>': T('an automatic jump')}.get(w['kind'], w['kind'])
        if c:
            lines.append('• ' + T('From "{src}" ({how}), when: {cond}', src=w['source'], how=how, cond=c))
        else:
            lines.append('• ' + T('From "{src}" ({how}), always.', src=w['source'], how=how))
        if len(seen) >= 25:
            lines.append('• ' + T('…and more.'))
            break
    return '\n'.join(lines)
