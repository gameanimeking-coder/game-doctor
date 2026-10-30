"""AI helper: a plain-text log of a scan for AI assistants, and "Fix with AI", which sends
the scan's problems to an AI service (online or on this computer), checks every change it
suggests and writes the accepted ones to a COPY of the game. The game itself is never changed.

Only Python's own urllib is used, so no AI company's add-on package is needed."""
import base64
import ctypes
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import datetime
from types import SimpleNamespace

import fixes
import scanner

# kind: 'anthropic', 'gemini' or 'openai' (the shared format most services use)
PROVIDERS = {
    'Claude (Anthropic)': dict(kind='anthropic', url='https://api.anthropic.com/v1', key=True, model='claude-sonnet-5-5'),
    'Gemini (Google)': dict(kind='gemini', url='https://generativelanguage.googleapis.com/v1beta', key=True, model=''),
    'ChatGPT (OpenAI)': dict(kind='openai', url='https://api.openai.com/v1', key=True, model=''),
    'Grok (xAI)': dict(kind='openai', url='https://api.x.ai/v1', key=True, model=''),
    'DeepSeek': dict(kind='openai', url='https://api.deepseek.com/v1', key=True, model=''),
    'Mistral': dict(kind='openai', url='https://api.mistral.ai/v1', key=True, model=''),
    'Groq': dict(kind='openai', url='https://api.groq.com/openai/v1', key=True, model=''),
    'OpenRouter (many models)': dict(kind='openai', url='https://openrouter.ai/api/v1', key=True, model=''),
    'LM Studio (on this computer)': dict(kind='openai', url='http://localhost:1234/v1', key=False, model=''),
    'Ollama (on this computer)': dict(kind='openai', url='http://localhost:11434/v1', key=False, model=''),
    'Other (works like OpenAI)': dict(kind='openai', url='http://localhost:8080/v1', key=False, model=''),
}
DEFAULT_PROVIDER = 'Claude (Anthropic)'
BATCH = 25          # problems per request, so answers stay short enough for every model
CONTEXT = 3         # lines shown before and after each problem line
LONG_LINE = 700     # longer lines are cut down around the problem
ALSO = 5            # other lines with the same wrong name that are shown (the app renames the rest itself)


class AIError(Exception):
    pass


def is_local(url):
    host = (urllib.parse.urlparse(url).hostname or '').lower()
    return host in ('localhost', '127.0.0.1', '::1') or host.endswith('.localhost')


# ---------------------------------------------------------------------- keys (scrambled by Windows)
class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _crypt(data, protect):
    try:
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    except AttributeError:
        return None
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect_key(key):
    """Key -> text that only this Windows user can turn back into the key ('' if that fails)."""
    blob = _crypt(key.encode('utf-8'), True) if key else None
    return base64.b64encode(blob).decode('ascii') if blob else ''


def unprotect_key(text):
    try:
        blob = _crypt(base64.b64decode(text), False) if text else None
    except (ValueError, TypeError):
        blob = None
    return blob.decode('utf-8') if blob else ''


# ---------------------------------------------------------------------- talking to the services
def _http(url, body=None, headers=None, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8') if body is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    handlers = [urllib.request.ProxyHandler({})] if is_local(url) else []
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', 'replace')
            detail = json.loads(detail).get('error', detail)
            if isinstance(detail, dict):
                detail = detail.get('message', detail)
        except Exception:
            detail = ''
        msg = {401: 'The API key was not accepted.', 403: 'The API key is not allowed to use this.',
               404: 'The service or model was not found. Check the model name and address.',
               429: 'Too many requests or no credit left on the account. Wait a moment or check your account.'}.get(e.code, '')
        if not msg and e.code == 400 and 'key' in str(detail).lower():
            msg = 'The API key was not accepted.'
        raise AIError(f'{msg} (HTTP {e.code}) {str(detail)[:300]}'.strip())
    except urllib.error.URLError as e:
        hint = ' Is the program running and is a model loaded?' if is_local(url) else ' Check your internet connection.'
        raise AIError(f'Could not connect to {url}.{hint} ({e.reason})')
    except TimeoutError:
        raise AIError('The AI took too long to answer.')
    except ValueError:
        raise AIError('The service sent back something that is not JSON.')


def _headers(cfg):
    kind, key = cfg['kind'], cfg.get('key', '')
    if kind == 'anthropic':
        return {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    if kind == 'gemini':
        return {'x-goog-api-key': key}
    return {'Authorization': f'Bearer {key}'} if key else {}


def list_models(cfg):
    base = cfg['url'].rstrip('/')
    data = _http(base + '/models', headers=_headers(cfg), timeout=30)
    if cfg['kind'] == 'gemini':
        names = [m['name'].split('/', 1)[-1] for m in data.get('models', [])
                 if 'generateContent' in m.get('supportedGenerationMethods', [])]
    else:
        names = [m.get('id', '') for m in data.get('data', data.get('models', []))]
    return sorted(n for n in names if n)


def chat(cfg, system, user):
    """One question, one answer (text)."""
    base, model = cfg['url'].rstrip('/'), cfg.get('model', '')
    if not model:
        raise AIError('Pick a model first.')
    if cfg['kind'] == 'anthropic':
        data = _http(base + '/messages', dict(model=model, max_tokens=16000, system=system,
                                               messages=[dict(role='user', content=user)]), _headers(cfg))
        return ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
    if cfg['kind'] == 'gemini':
        data = _http(f'{base}/models/{urllib.parse.quote(model)}:generateContent',
                     dict(systemInstruction=dict(parts=[dict(text=system)]),
                          contents=[dict(role='user', parts=[dict(text=user)])],
                          generationConfig=dict(responseMimeType='application/json')), _headers(cfg))
        try:
            return ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts'])
        except (KeyError, IndexError):
            raise AIError('Gemini sent no answer (it may have blocked the text).')
    data = _http(base + '/chat/completions', dict(model=model, messages=[dict(role='system', content=system),
                                                                         dict(role='user', content=user)]), _headers(cfg))
    try:
        return data['choices'][0]['message']['content'] or ''
    except (KeyError, IndexError, TypeError):
        raise AIError('The service sent no answer.')


# ---------------------------------------------------------------------- describing the scan
SEV_WORD = {scanner.ERROR: 'Broken', scanner.WARNING: 'Probably broken', scanner.INFO: 'Worth checking'}
CHECKS = ['passages / scenes and links', 'macro tags', 'conditions and code logic', 'variables', 'HTML tags',
          'CSS classes', 'images, audio, video and other files', 'text (typos, placeholders)', 'JavaScript',
          'story-format rules']


CHECK_NOTE = 'Check this one yourself: a similar file name is often a different picture (another character, scene or one of several random versions).'


def missing_file(i):
    return i.category == 'Missing files' and bool(re.match(r'^\w+ not found: ', i.title))


def issue_id(res, i):
    return res.issues.index(i) + 1


def readable_lines(res, path=''):
    """Lines the way a person reads them (Twine passage text is stored with &lt; etc. in the file)."""
    return res.lines_of(path)


def _cut(line, i):
    if len(line) <= LONG_LINE:
        return line
    pos = line.find(i.snippet[:60].strip()) if i.snippet else -1
    a = max(0, (pos if pos >= 0 else 0) - LONG_LINE // 3)
    return ('… ' if a else '') + line[a:a + LONG_LINE] + (' …' if a + LONG_LINE < len(line) else '')


def describe_issue(res, i, context=True):
    path = i.file or res.html_path
    lines = readable_lines(res, i.file)
    out = [f'#{issue_id(res, i)} [{SEV_WORD[i.severity]}] {i.category}: {i.title}']
    where = [f'File: {os.path.basename(path)}']
    if i.line:
        where.append(f'Line: {i.line}')
    if i.passage:
        where.append(f'Scene/passage: "{i.passage}"')
    out.append('   ' + '   '.join(where))
    if i.unreachable:
        out.append('   Note: this is in a scene nothing links to, so players never see it.')
    if missing_file(i):
        out.append('   Careful: a similar file name is often a different picture (another character, scene or random version).')
    if i.what:
        out.append('   What is wrong: ' + i.what)
    if i.fix:
        out.append('   How to fix: ' + i.fix.replace('\n', '\n      '))
    if i.old and i.new is not None and i.line:
        out.append('   Scanner\'s suggested change for line %d:' % i.line)
        out.append('      now:       ' + _cut(i.old, i) )
        out.append('      change to: ' + _cut(i.new, i))
    if i.rename:
        out.append(f'   Rename everywhere: "{i.rename[0]}" -> "{i.rename[1]}"')
    if i.also_lines:
        out.append('   The same wrong name is also on these lines (fix them too):')
        for n in i.also_lines[:ALSO]:
            if 0 < n <= len(lines):
                out.append(f'   {n:>8} | ' + _cut(lines[n - 1].rstrip('\r'), SimpleNamespace(snippet=i.rename[0] if i.rename else '')))
        if len(i.also_lines) > ALSO:
            out.append(f'   … and {len(i.also_lines) - ALSO} more lines. Fix the problem line; the same rename is then '
                       'made on all the other lines automatically.')
    if context and i.line and 0 < i.line <= len(lines):
        out.append('   Code around it (line number | text):')
        for n in range(max(1, i.line - CONTEXT), min(len(lines), i.line + CONTEXT) + 1):
            mark = '>>' if n == i.line else '  '
            out.append(f'   {mark}{n:>6} | ' + _cut(lines[n - 1].rstrip('\r'), i))
    return '\n'.join(out)


def build_log(app_version, res, active, ignored, playtest=None, health=None):
    """Plain-text log of everything scanned and found, written so an AI assistant can use it."""
    st = res.stats
    twine = res.engine == 'twine'
    L = ['GAME BUG SCANNER LOG', '=' * 20,
         f'Made by Game Doctor {app_version} on {datetime.now():%Y-%m-%d %H:%M}.', '',
         'FOR THE AI READING THIS',
         '- This log lists every problem the scanner found in the game, with the file, line, the code around it,',
         '  what is wrong and how to fix it. Fix the problems in the game file(s) you were given.',
         '- Change only what is needed to fix each problem. Keep everything else exactly as it is.',
         '- Line numbers are lines of the file as it is on disk (line 1 = first line).',
         ('- This is a Twine game: in the .html file the passage text is stored HTML-escaped (< is &lt;, > is &gt;, '
          '" is &quot;, \' is &#39;, & is &amp;). The code in this log is shown UNescaped, the way it looks in Twine. '
          'When editing the .html file directly, escape it again.' if twine else
          '- The code in this log is shown exactly as it is in the files.'),
         '- "Worth checking" problems are often fine; problems marked "players never see this" are in scenes',
         '  nothing links to. Leave the problems listed under "Left on purpose" alone.',
         '- Problems with "Scanner\'s suggested change" already have a tested fix; use it unless it is clearly wrong.',
         '- Missing pictures, sounds or videos: a similar file name is often a different picture (another character,',
         '  scene or one of several random versions). Only point a link at another file when the name is clearly a typo',
         '  (the scanner says when it looks mistyped); otherwise tell the user which files are missing.', '',
         'GAME',
         f'File: {res.html_path}', f'Folder (pictures, sounds): {res.game_dir}',
         f'Engine / story format: {st.get("format", res.engine)}',
         f'Scenes / parts: {st.get("passages", 0)}   Files in folder: {st.get("files_in_folder", 0)}']
    if res.files:
        L.append('Other script files: ' + ', '.join(os.path.relpath(p, res.game_dir) for p in sorted(res.files)))
    if health:
        L.append(f'Code health: {health[0]}% ({health[1]})')
    n = {s: sum(1 for i in active if i.severity == s) for s in SEV_WORD}
    L += [f'Problems: {n[scanner.ERROR]} broken, {n[scanner.WARNING]} probably broken, {n[scanner.INFO]} worth checking'
          + (f', {len(ignored)} left on purpose' if ignored else ''), '',
          'WHAT WAS CHECKED', '- ' + '\n- '.join(CHECKS if res.engine in ('twine', 'html') else
                                                   ['scenes / labels and jumps', 'script syntax for this engine',
                                                    'pictures, sounds and other files', 'text (typos, placeholders)'])]
    if playtest:
        issues, summary = playtest
        L.append(f'- play-test: {summary.get("passages", 0)} scenes played {len(summary.get("modes", []))} ways '
                 f'in a real browser, {len(issues)} problem(s) found while playing')
    L += ['', 'STEPS FOR FIXING', '1. Back up the game first.',
          '2. Go through the problems below, most serious first (they are in that order).',
          '3. After fixing, scan the game again with Game Doctor to check that each problem is gone and nothing new broke.', '']
    for sev in (scanner.ERROR, scanner.WARNING, scanner.INFO):
        group = [i for i in active if i.severity == sev]
        if group:
            L += [f'PROBLEMS: {SEV_WORD[sev].upper()} ({len(group)})', '']
            for i in group:
                L += [describe_issue(res, i), '']
    if ignored:
        L += [f'LEFT ON PURPOSE - DO NOT CHANGE ({len(ignored)})', '']
        L += [f'#{issue_id(res, i)} {i.title}  (line {i.line}{", " + i.passage if i.passage else ""})' for i in ignored]
        L.append('')
    L.append('END OF LOG')
    return '\n'.join(L)


# ---------------------------------------------------------------------- asking for fixes
SYSTEM = """You fix bugs in a game's code. A scanner found the problems listed by the user.
Rules:
- Only fix the listed problems. Change as little as possible. Never rewrite story text except to fix a listed typo.
- Each change replaces one exact piece of text on one line with new text. It must not add or remove lines.
- "find" must be copied EXACTLY from the shown line (same spaces, quotes and capitals) and be long enough to be unique on that line.
- Only change lines that are shown to you.
- If the scanner gives a suggested change, use it unless it is clearly wrong.
- For a missing picture, sound or video, a similar file name is often a different picture (another character, scene or
  random version). Only point it at another file when the name is clearly a typo; otherwise skip it and say the file is missing.
- If you are not sure how to fix a problem, skip it and say why. Skipping is better than guessing.
Answer with JSON only, no other text, in this form:
{"fixes": [{"problem": 12, "line": 345, "find": "exact text now on that line", "replace": "new text", "why": "short reason"}],
 "skipped": [{"problem": 13, "why": "short reason"}]}"""


def batches(res, issues):
    out = [issues[k:k + BATCH] for k in range(0, len(issues), BATCH)]
    for group in out:
        note = ('This is a Twine game. The code is shown the way it looks in Twine (not HTML-escaped); '
                'write "find" and "replace" that way too.\n\n' if res.engine == 'twine' else '')
        yield group, note + 'Problems to fix:\n\n' + '\n\n'.join(describe_issue(res, i) for i in group)


def parse_answer(text):
    """The JSON object in an AI answer (it may be wrapped in ``` fences or have words around it)."""
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', (text or '').strip(), flags=re.S)
    a, b = t.find('{'), t.rfind('}')
    if a < 0 or b < a:
        raise AIError('The AI did not answer in the expected format.')
    try:
        data = json.loads(t[a:b + 1])
    except ValueError:
        raise AIError('The AI answer could not be read (broken JSON).')
    if not isinstance(data, dict):
        raise AIError('The AI did not answer in the expected format.')
    return data


def check_change(res, sent, item):
    """Turn one suggested change into a Change, or explain why it can't be used."""
    by_id = {issue_id(res, i): i for i in sent}
    try:
        pid, line = int(item.get('problem')), int(item.get('line'))
    except (TypeError, ValueError):
        return None, 'no problem or line number'
    i = by_id.get(pid)
    if i is None:
        return None, f'problem #{pid} was not asked about'
    find, repl = item.get('find'), item.get('replace')
    if not isinstance(find, str) or not isinstance(repl, str) or not find:
        return None, 'no text to find or replace'
    if '\n' in find or '\n' in repl:
        return None, 'it would add or remove lines'
    allowed = {i.line, *i.also_lines} | set(range(i.line - CONTEXT, i.line + CONTEXT + 1))
    lines = readable_lines(res, i.file)
    if line not in allowed or not 0 < line <= len(lines):
        return None, f'line {line} is not near the problem'
    cur = lines[line - 1].rstrip('\r')
    if cur.count(find) != 1:
        return None, f'the text to change is {"not on" if find not in cur else "more than once on"} line {line}'
    if find == repl:
        return None, 'it changes nothing'
    return SimpleNamespace(issue=i, file=i.file, line=line, find=find, replace=repl, why=str(item.get('why', ''))[:300],
                           accepted=True, check=CHECK_NOTE if missing_file(i) else ''), ''


def ask(cfg, res, issues, progress=None, stop=None):
    """Send the problems in batches. Returns (changes, notes): notes are skipped problems and rejected changes."""
    changes, notes, seen = [], [], set()
    groups = list(batches(res, issues))
    for k, (group, prompt) in enumerate(groups, 1):
        if stop and stop():
            notes.append('Stopped before all problems were sent.')
            break
        if progress:
            progress(k, len(groups))
        try:
            data = parse_answer(chat(cfg, SYSTEM, prompt))
        except AIError as e:
            notes.append(f'Problems #{issue_id(res, group[0])}-#{issue_id(res, group[-1])}: {e}')
            if k == 1 and len(groups) > 1 and 'connect' in str(e).lower():
                break
            continue
        for item in data.get('fixes') or []:
            if not isinstance(item, dict):
                continue
            ch, why = check_change(res, group, item)
            if ch is None:
                notes.append(f'Change for problem #{item.get("problem")} not used: {why}.')
            elif (ch.file, ch.line, ch.find) not in seen:
                seen.add((ch.file, ch.line, ch.find))
                changes.append(ch)
        for ch in list(changes):
            for extra in same_rename(res, ch, seen):
                changes.append(extra)
        for item in data.get('skipped') or []:
            if isinstance(item, dict):
                notes.append(f'Problem #{item.get("problem")} skipped by the AI: {str(item.get("why", ""))[:200]}')
    return changes, notes


def same_rename(res, ch, seen):
    """When the AI fixes a misspelled name on the problem line, make the same rename on every other
    line the scanner found it on (the AI only sees a few of them)."""
    i = ch.issue
    if not i.rename or ('rename', id(i)) in seen:
        return []
    lines = readable_lines(res, i.file)
    wrong, right = i.rename
    cur = lines[ch.line - 1]
    new = cur.replace(ch.find, ch.replace, 1)
    if not (new.count(wrong) < cur.count(wrong) and new.count(right) > cur.count(right)):
        return []   # this change is not the rename itself
    seen.add(('rename', id(i)))
    out = []
    for n in [i.line] + i.also_lines:
        cur = lines[n - 1] if 0 < n <= len(lines) else ''
        key = (ch.file, n, i.rename[0])
        if cur.count(i.rename[0]) == 1 and key not in seen and not any(k[:2] == key[:2] for k in seen):
            seen.add(key)
            out.append(SimpleNamespace(issue=i, file=i.file, line=n, find=i.rename[0], replace=i.rename[1],
                                       why=f'Same rename as on line {i.line} (made by the scanner)', accepted=True,
                                       check=ch.check))
    return out


# ---------------------------------------------------------------------- writing the fixed copy
def apply_changes(res, changes):
    """{path: new file lines (as stored on disk)} with every accepted change, or raises AIError."""
    out = {}
    by_line = {}
    for ch in changes:
        if ch.accepted:
            by_line.setdefault((ch.file or res.html_path, ch.line), []).append(ch)
    for (path, line), group in sorted(by_line.items()):
        same = os.path.normcase(path) == os.path.normcase(res.html_path)
        readable = readable_lines(res, '' if same else path)[line - 1].rstrip('\r')
        new = readable
        for ch in group:
            if new.count(ch.find) != 1:
                raise AIError(f'Two accepted changes on line {line} overlap. Untick one of them.')
            new = new.replace(ch.find, ch.replace, 1)
        if same and res.engine == 'twine':
            f = fixes.forms(SimpleNamespace(old=readable, new=new, line=line), res, 'file')
            if not f:
                raise AIError(f'The change on line {line} could not be written back into the file safely.')
            stored = f[1]
        else:
            stored = new
        if path not in out:
            if same and res.raw_lines:
                out[path] = list(res.raw_lines)
            else:
                with open(path, encoding='utf-8', errors='replace', newline='') as fh:
                    out[path] = fh.read().split('\n')
        cr = out[path][line - 1].endswith('\r')
        out[path][line - 1] = stored + ('\r' if cr and not stored.endswith('\r') else '')
    return out


def change_list(res, changes, notes, cfg_name):
    L = [f'Changes suggested by {cfg_name} and accepted in Game Doctor ({datetime.now():%Y-%m-%d %H:%M})',
         f'Game: {res.html_path}', '']
    for ch in changes:
        if ch.accepted:
            L += [f'{os.path.basename(ch.file or res.html_path)} line {ch.line}  (problem #{issue_id(res, ch.issue)}: {ch.issue.title})',
                  f'   was:  {ch.find}', f'   now:  {ch.replace}'] + ([f'   why:  {ch.why}'] if ch.why else []) + ['']
    if notes:
        L += ['Notes'] + ['- ' + n for n in notes]
    return '\n'.join(L)


def write_copy(res, changes, dest):
    """Write the fixed copy. dest is a file (one-file games) or a folder (games with several script files).
    Returns the list of files written."""
    files = apply_changes(res, changes)
    written = []
    for path, lines in files.items():
        if len(files) == 1 and not os.path.isdir(dest):
            target = dest
        else:
            target = os.path.join(dest, os.path.relpath(path, res.game_dir))
            os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(path)):
            raise AIError('The fixed copy must not replace the original game file.')
        with open(target, 'w', encoding='utf-8', newline='') as fh:
            fh.write('\n'.join(lines))
        written.append(target)
    return written


def compare(res, res2, changes=()):
    """(fixed, new, revealed): problems gone after the changes, problems that were not there before on
    untouched lines, and problems on changed lines (often the next problem on that line, now visible)."""
    def key(i):
        # one finding that sums up many ("49 values are set but…") keeps its identity when the number changes
        k = fixes.loose_key(i)
        return re.sub(r'\d+', '#', k) if not i.line else k

    before, after = {}, {}
    for i in res.issues:
        before[key(i)] = before.get(key(i), 0) + 1
    for i in res2.issues:
        after[key(i)] = after.get(key(i), 0) + 1
    fixed = sum(max(0, n - after.get(k, 0)) for k, n in before.items())
    touched = {ch.line for ch in changes if ch.accepted}
    added = [i for i in res2.issues if key(i) not in before]
    revealed = [i for i in added if i.line in touched]
    return fixed, [i for i in added if i not in revealed], revealed
