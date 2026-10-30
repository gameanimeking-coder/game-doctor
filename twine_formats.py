"""Rules for Twine story formats other than SugarCube: Harlowe 3, Chapbook 1-2, Snowman 2.
Called from Scanner.check_format(); uses the scanner's passages and add()."""
import collections
import re

HARLOWE_MACROS = set('''
set put move unpack print display if unless else-if elseif else hidden show hide rerun replace append prepend
append-with prepend-with replace-with link link-goto link-reveal link-reveal-goto link-repeat link-rerun link-undo
link-fullscreen link-show link-storylet click click-goto click-undo click-replace click-append click-prepend
click-rerun mouseover mouseout mouseover-replace mouseover-append mouseover-prepend mouseout-replace mouseout-append
mouseout-prepend mouseover-goto mouseout-goto goto go-to undo redirect restart reload either random round floor ceil
abs max min sqrt sin cos tan pow exp log log10 log2 sign num number str string text source upperfirst lowerfirst
uppercase lowercase words str-repeated string-repeated str-reversed string-reversed str-find string-find str-nth
string-nth trimmed plural digit-format joined a array ds dataset dm datamap dataentries data-entries datanames
data-names datavalues data-values range subarray substring shuffled sorted rotated rotated-to reversed repeated
interlaced permutations altered folded find all-pass any-pass none-pass count dm-names dm-values dm-entries
dm-altered history visited passage passages cond nth more live event after after-error stop time transition t8n
transition-time t8n-time transition-delay t8n-delay transition-skip t8n-skip transition-depart t8n-depart
transition-arrive t8n-arrive align box float-box bg background colour color text-colour text-color text-indent
text-rotate text-rotate-z text-rotate-x text-rotate-y text-size size text-style font hover-style css char-style
line-style link-style verbatim v6m verbatim-print verbatim-source collapse nobr enchant enchant-in hook hooks-named
change changer macro output output-data out error rgb rgba hsl hsla lch lcha complement palette mix gradient stripes
grayscale greyscale b4r b4r-colour b4r-color b4r-size b4r-style border border-colour border-color border-size
corner-radius opacity button columns column dialog alert prompt confirm input force-input input-box force-input-box
checkbox checkbox-fullscreen cycling-link seq-link sequence-link dropdown meter save-game load-game saved-games scroll
icon-undo icon-redo icon-fullscreen icon-restart icon-counter redo page-url current-date current-time monthday
weekday open-url goto-url seed debug ignore test-true test-false metadata storylet open-storylets exclusivity urgency
mock-turns mock-visits datatype datapattern partial bind 2bind bind-2 dm-named-entries split splitted
'''.split())

CHAPBOOK_INSERTS = {
    'back link', 'cycling link', 'dropdown menu', 'embed passage', 'embed passage named', 'embed image',
    'embed flickr image', 'embed unsplash image', 'embed youtube video', 'link to', 'reveal link',
    'restart link', 'text input', 'ambient sound', 'sound effect', 'restart', 'party', 'meter',
    'conditional display', 'random', 'insert date',
}
CHAPBOOK_MODIFIERS = {'after', 'if', 'else', 'unless', 'continue', "cont'd", 'cont', 'align', 'note',
                      'javascript', 'css', 'transition', 'append', 'fork', 'ambient sound', 'sound effect'}


def check(sc):
    fmt = sc.format.lower()
    if fmt.startswith('harlowe'):
        Harlowe(sc).run()
    elif fmt.startswith('chapbook'):
        Chapbook(sc).run()
    elif fmt.startswith('snowman'):
        Snowman(sc).run()


def _strip_strings(s):
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', lambda m: '"' + ' ' * (len(m.group()) - 2) + '"', s)


def _match_paren(t, start):
    """Index just after the ')' matching the '(' at `start`, or -1."""
    depth = 0
    i = start
    in_str = None
    while i < len(t):
        c = t[i]
        if in_str:
            if c == '\\':
                i += 1
            elif c == in_str:
                in_str = None
        elif c in '"\'':
            in_str = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


class Harlowe:
    def __init__(self, sc):
        self.sc = sc

    def run(self):
        from scanner import ERROR, WARNING, INFO, _close, _typo_of
        sc = self.sc
        known = sorted(HARLOWE_MACROS)
        sets, reads, first = collections.Counter(), collections.Counter(), {}
        for p in sc.story_passages():
            t = re.sub(r'`[^`]*`', lambda m: ' ' * len(m.group()), sc.markup_text(p))  # verbatim markup
            # macros
            for m in re.finditer(r'\(([A-Za-z][\w-]*)\s*:', t):
                name = m.group(1).lower()
                end = _match_paren(t, m.start())
                snip = sc.snippet_at(p, m.start())
                if end == -1:
                    sc.add(ERROR, 'Macro tags', f'({m.group(1)}:) is never closed with ")"', p, m.start(), snippet=snip,
                           what='The macro call has no closing bracket, so Harlowe shows an error and the rest of the passage may break.',
                           fix='Add the missing ")" at the end of the macro call.')
                    continue
                if name not in HARLOWE_MACROS:
                    near = _close(name, known, cutoff=0.75)
                    sc.add(ERROR if near else WARNING, 'Unknown macros', f'Unknown macro ({m.group(1)}:)', p, m.start(), snippet=snip,
                           what=f'Harlowe has no macro called "{m.group(1)}".' + (f' It is very close to ({near}:).' if near else ''),
                           fix=(f'Change it to ({near}:).' if near else 'Check the spelling. Custom macros are called with their variable, e.g. ($name: ...).'))
                args = _strip_strings(t[m.end():end - 1])
                if name in ('set', 'put', 'move'):
                    if re.search(r'(?<![=!<>])=(?![=>])', args) and ' to ' not in f' {args} ' and ' into ' not in f' {args} ':
                        sc.add(ERROR, 'Code', f'({m.group(1)}:) uses "=" instead of "to"', p, m.start(), snippet=snip,
                               what='Harlowe sets variables with "to" (or "into" for put). "=" causes an error.',
                               fix='Write e.g. (set: $gold to 5).')
                    pattern = r'\$([A-Za-z_]\w*)\s+to\b' if name == 'set' else r'into\s+\$([A-Za-z_]\w*)'
                    for v in re.finditer(pattern, args):
                        sets[v.group(1)] += 1
                if name in ('if', 'unless', 'else-if', 'elseif'):
                    if re.search(r'(?<![=!<>])=(?![=>])', args):
                        sc.add(ERROR, 'Conditions', 'Single "=" inside a condition', p, m.start(), snippet=snip,
                               what='Harlowe compares with "is", not "=". This causes an error.',
                               fix='Use "is" (or "is not"), e.g. (if: $gold is 5).')
                    for mm in re.finditer(r'(\$\w+)\s+is\s+([^\s()]+)\s+or\s+("[^"]*"|\d+|true|false)\s*$', args):
                        sc.add(WARNING, 'Conditions', f'"{mm.group(1)} is {mm.group(2)} or {mm.group(3)}" does not compare both', p, m.start(), snippet=snip,
                               what=f'The part after "or" is checked on its own. Harlowe actually understands this form ("elided comparison"), '
                                    f'but only for "is"; double-check it does what you want.',
                               fix=f'Clearer: {mm.group(1)} is {mm.group(2)} or {mm.group(1)} is {mm.group(3)}.')
                if name in ('if', 'unless', 'else-if', 'elseif', 'else', 'hidden', 'link', 'link-reveal', 'click',
                            'after', 'live', 'event', 'link-repeat', 'link-rerun', 'show', 'replace', 'append', 'prepend'):
                    rest = t[end:end + 80]
                    if not re.match(r'\s*(?:\+\s*\([^)]*\)\s*)*(?:\[(?!\[)|\|[\w-]+>\[|\?\w+)', rest) and not re.match(r'\s*\+', rest):
                        if name in ('else', 'if', 'unless', 'else-if', 'elseif') or name.startswith('link'):
                            sc.add(WARNING, 'Macro tags', f'({m.group(1)}:) has no hook [ ] after it', p, m.start(), snippet=snip,
                                   what=f'({m.group(1)}:) changes the hook right after it, written in square brackets. Here nothing follows, '
                                        'so Harlowe shows an error or the text is not controlled by it.',
                                   fix=f'Put the text it controls in square brackets right after it: ({m.group(1)}: ...)[text].')
            # hooks: count [ and ] outside [[links]]
            hook_text = re.sub(r'\[\[.*?\]\]', lambda m: ' ' * len(m.group()), t, flags=re.S)
            depth, last_open = 0, []
            for m in re.finditer(r'[\[\]]', hook_text):
                if m.group() == '[':
                    last_open.append(m.start())
                elif last_open:
                    last_open.pop()
                else:
                    sc.add(ERROR, 'Macro tags', 'Hook closed with "]" but never opened', p, m.start(), snippet=sc.snippet_at(p, m.start()),
                           what='There is a "]" with no "[" before it.', fix='Delete the extra "]" or add the missing "[".')
            for off in last_open:
                sc.add(ERROR, 'Macro tags', 'Hook "[" is never closed with "]"', p, off, snippet=sc.snippet_at(p, off),
                       what='A hook starts with "[" but never ends. Everything after it becomes part of the hook.',
                       fix='Add "]" where the hook should end.')
            for m in re.finditer(r'(?<![\w$])\$([A-Za-z_]\w*)', t):
                reads[m.group(1)] += 1
                first.setdefault(m.group(1), (p, m.start()))
        set_names = sorted(sets)
        sc.known_vars = set(sets) | set(reads)
        for v, n in reads.items():
            if sets[v] == 0:
                near = _typo_of(v, set_names)
                p, off = first[v]
                sc.add(WARNING if near else INFO, 'Variables', f'${v} is used but never set', p, off, snippet=sc.snippet_at(p, off),
                       what=f'${v} never gets a value with (set:) or (put:), so it is always 0/empty.' + (f' It looks like a misspelling of ${near}.' if near else ''),
                       fix=(f'Rename ${v} to ${near}.' if near else f'Set it first, e.g. (set: ${v} to 0) in the startup passage.'))


class Chapbook:
    def __init__(self, sc):
        self.sc = sc

    def run(self):
        from scanner import ERROR, WARNING, INFO, _close, _typo_of
        sc = self.sc
        sets, uses, first = collections.Counter(), collections.Counter(), {}
        inserts = sorted(CHAPBOOK_INSERTS)
        for p in sc.story_passages():
            t = sc.markup_text(p)
            body_start = 0
            m = re.search(r'^--\s*$', t, re.M)
            if m:
                body_start = m.end()
                for vm in re.finditer(r'^\s*([A-Za-z_][\w.]*)\s*(?:\([^)]*\))?\s*:', t[:m.start()], re.M):
                    sets[vm.group(1).split('.')[0]] += 1
            body = t[body_start:]
            depth = 0
            for bm in re.finditer(r'[{}]', body):
                depth += 1 if bm.group() == '{' else -1
                if depth < 0:
                    sc.add(ERROR, 'Macro tags', 'Insert closed with "}" but never opened', p, body_start + bm.start(),
                           snippet=sc.snippet_at(p, body_start + bm.start()), what='There is a "}" with no "{" before it.',
                           fix='Delete the extra "}" or add the missing "{".')
                    depth = 0
            if depth > 0:
                off = body_start + body.rfind('{')
                sc.add(ERROR, 'Macro tags', 'Insert "{" is never closed with "}"', p, off, snippet=sc.snippet_at(p, off),
                       what='An insert starts with "{" but never ends.', fix='Add the missing "}".')
            for im in re.finditer(r'\{([^{}:]+?)(:|\})', body):
                name = im.group(1).strip()
                off = body_start + im.start()
                if im.group(2) == ':' and name.lower() not in CHAPBOOK_INSERTS:
                    near = _close(name.lower(), inserts, cutoff=0.7)
                    sc.add(ERROR if near else WARNING, 'Unknown macros', f'Unknown insert {{{name}: ...}}', p, off,
                           snippet=sc.snippet_at(p, off),
                           what=f'Chapbook has no insert called "{name}".' + (f' Did you mean "{near}"?' if near else ''),
                           fix=(f'Change it to {{{near}: ...}}.' if near else 'Check the spelling of the insert.'))
                elif im.group(2) == '}' and re.fullmatch(r'[A-Za-z_][\w.]*', name) and name.lower() not in CHAPBOOK_INSERTS:
                    uses[name.split('.')[0]] += 1
                    first.setdefault(name.split('.')[0], (p, off))
            for mm in re.finditer(r'^\[([^\]\n]+)\]\s*$', body, re.M):
                mod = mm.group(1).strip()
                word = mod.split()[0].lower() if mod.split() else ''
                if word not in CHAPBOOK_MODIFIERS and mod.lower() not in CHAPBOOK_MODIFIERS:
                    near = _close(word, sorted(CHAPBOOK_MODIFIERS), cutoff=0.7)
                    if near:
                        off = body_start + mm.start()
                        sc.add(WARNING, 'Unknown macros', f'Unknown modifier [{mod}]', p, off, snippet=sc.snippet_at(p, off),
                               what=f'"{word}" is not a Chapbook modifier. Did you mean "{near}"?', fix=f'Change it to [{near} ...].')
                if word in ('if', 'unless') and re.search(r'(?<![=!<>])=(?![=>])', mod):
                    off = body_start + mm.start()
                    sc.add(ERROR, 'Conditions', 'Single "=" inside a condition', p, off, snippet=sc.snippet_at(p, off),
                           what='Chapbook conditions are JavaScript: "=" sets the variable instead of comparing.',
                           fix='Use "===" to compare, e.g. [if gold === 5].')
        builtin = {'config', 'browser', 'engine', 'passage', 'now', 'random', 'story', 'window', 'document', 'Math', 'true', 'false'}
        set_names = sorted(sets)
        sc.known_vars = set(sets) | set(uses)
        for v in uses:
            if sets[v] == 0 and v not in builtin:
                near = _typo_of(v, set_names)
                p, off = first[v]
                sc.add(WARNING if near else INFO, 'Variables', f'{v} is used but never set', p, off, snippet=sc.snippet_at(p, off),
                       what=f'"{v}" is shown with {{{v}}} but no passage sets it in its vars section.' + (f' Did you mean {near}?' if near else ''),
                       fix=(f'Rename it to {near}.' if near else f'Set it in a vars section at the top of a passage: "{v}: 0" followed by "--".'))


class Snowman:
    def __init__(self, sc):
        self.sc = sc

    def run(self):
        from scanner import ERROR, WARNING, INFO, _typo_of, _js_bracket_problem
        sc = self.sc
        sets, reads, first = collections.Counter(), collections.Counter(), {}
        for p in sc.story_passages():
            t = sc.markup_text(p)
            opens, closes = len(re.findall(r'<%', t)), len(re.findall(r'%>', t))
            if opens != closes:
                off = t.rfind('<%') if opens > closes else t.rfind('%>')
                sc.add(ERROR, 'Macro tags', f'Script tags do not match ({opens} "<%", {closes} "%>")', p, off,
                       snippet=sc.snippet_at(p, off), what='Every "<%" needs a matching "%>". The passage will show an error.',
                       fix='Add the missing "%>" or "<%".')
            code = '\n'.join(m.group(1) for m in re.finditer(r'<%(?!=)(.*?)%>', t, re.S))
            prob = _js_bracket_problem(code) if code.strip() else None
            if prob:
                sc.add(ERROR, 'JavaScript', f'Script code problem: {prob[1]}', p, snippet=code[max(0, prob[0] - 60):prob[0] + 60],
                       what='The JavaScript inside <% %> has an unclosed or extra bracket or quote.', fix='Check the brackets and quotes.')
            for m in re.finditer(r'\b(?:s|story\.state)\.([A-Za-z_]\w*)\s*(=(?!=)|\+=|-=|\+\+|--)', t):
                sets[m.group(1)] += 1
            for m in re.finditer(r'\b(?:s|story\.state)\.([A-Za-z_]\w*)', t):
                reads[m.group(1)] += 1
                first.setdefault(m.group(1), (p, m.start()))
        set_names = sorted(sets)
        sc.known_vars = set(sets) | set(reads)
        for v, n in reads.items():
            if sets[v] == 0:
                near = _typo_of(v, set_names)
                p, off = first[v]
                sc.add(WARNING if near else INFO, 'Variables', f's.{v} is used but never set', p, off, snippet=sc.snippet_at(p, off),
                       what=f's.{v} is never given a value, so it is always undefined.' + (f' Did you mean s.{near}?' if near else ''),
                       fix=(f'Rename it to s.{near}.' if near else f'Set it first, e.g. <% s.{v} = 0 %>.'))
