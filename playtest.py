"""Play-test: run the game in an invisible Microsoft Edge (or Chrome) and record
what actually goes wrong while it runs.

- Errors and failed file loads while the game starts.
- Syntax errors in every script, checked by the browser's own JavaScript engine
  (understands all modern JavaScript), with line numbers.
- SugarCube games: every passage is rendered four times (starting values, all
  numbers high, all numbers 0, all yes/no values flipped) and every error box is
  recorded. Pictures/sounds that fail to load while rendering are recorded too,
  including ones whose names are built from variables.

The game is served from a temporary web server on 127.0.0.1 (this computer only)
and the browser uses a temporary empty profile that is deleted afterwards.
Nothing in the game folder is changed.
"""
import base64
import contextlib
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import scanner
from scanner import ERROR, WARNING, INFO, Issue

BROWSERS = [
    r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe',
    r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe',
    r'%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe',
    r'%ProgramFiles%\Google\Chrome\Application\chrome.exe',
    r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe',
    r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe',
]

MODES = [('start', 'with the starting values'), ('high', 'with all numbers set high (100)'),
         ('zero', 'with all numbers set to 0'), ('flip', 'with every yes/no value flipped')]


class PlaytestError(Exception):
    pass


def find_browser():
    for p in BROWSERS:
        full = os.path.expandvars(p)
        if '%' not in full and os.path.isfile(full):
            return full
    return None


# ---------------------------------------------------------------------- tiny web server
class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    fallback = None   # pictures/sounds folder, when it is not the folder the game file is in
    patch = None      # (url path of the game file, function changing its text): only the browser's copy changes

    def do_GET(self):
        if self.patch and urllib.parse.unquote(self.path.split('?', 1)[0]) == self.patch[0]:
            try:
                with open(self.translate_path(self.path), encoding='utf-8', errors='replace') as f:
                    body = self.patch[1](f.read()).encode('utf-8')
            except OSError:
                return super().do_GET()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *a):
        pass

    def translate_path(self, path):
        p = super().translate_path(path)
        if self.fallback and not os.path.exists(p):
            served, self.directory = self.directory, self.fallback
            try:
                alt = super().translate_path(path)
            finally:
                self.directory = served
            if os.path.exists(alt):
                return alt
        return p

    def end_headers(self):
        self.send_header('Cache-Control', 'max-age=3600')
        super().end_headers()


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass   # the browser cancelling a download is normal; never print tracebacks


def start_server(root, fallback=None, patch=None):
    class Handler(_QuietHandler):
        pass
    Handler.fallback = fallback
    Handler.patch = patch
    handler = lambda *a, **kw: Handler(*a, directory=root, **kw)
    srv = _Server(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------------- minimal WebSocket + DevTools client
class DevTools:
    def __init__(self, ws_url, timeout=60):
        u = urllib.parse.urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f'GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(req.encode())
        resp = b''
        while b'\r\n\r\n' not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise PlaytestError('The browser closed the connection.')
            resp += chunk
        if b' 101 ' not in resp.split(b'\r\n', 1)[0]:
            raise PlaytestError('The browser refused the test connection.')
        self.buf = resp.split(b'\r\n\r\n', 1)[1]
        self.next_id = 0
        self.events = []

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise PlaytestError('The browser closed the connection.')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, data, opcode=1):
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(head + mask + masked)

    def _recv_message(self):
        parts = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._recv_exact(8))[0]
            if b2 & 0x80:
                mask = self._recv_exact(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._recv_exact(n)))
            else:
                data = self._recv_exact(n)
            if opcode == 9:
                self._send_frame(data, 10)
                continue
            if opcode == 8:
                raise PlaytestError('The browser closed the connection.')
            parts.append(data)
            if fin:
                return b''.join(parts).decode('utf-8', 'replace')

    def call(self, method, params=None, timeout=120):
        self.next_id += 1
        my_id = self.next_id
        self._send_frame(json.dumps({'id': my_id, 'method': method, 'params': params or {}}).encode())
        end = time.time() + timeout
        self.sock.settimeout(timeout)
        while True:
            if time.time() > end:
                raise PlaytestError(f'The browser did not answer in time ({method}).')
            msg = json.loads(self._recv_message())
            if msg.get('id') == my_id:
                if 'error' in msg:
                    raise PlaytestError(msg['error'].get('message', 'error'))
                return msg.get('result', {})
            if 'method' in msg:
                self.events.append(msg)

    def wait_event(self, name, timeout=60):
        end = time.time() + timeout
        for e in self.events:
            if e['method'] == name:
                return e
        self.sock.settimeout(timeout)
        while time.time() < end:
            msg = json.loads(self._recv_message())
            if 'method' in msg:
                self.events.append(msg)
                if msg['method'] == name:
                    return msg
        return None

    def evaluate(self, expr, timeout=120):
        r = self.call('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': True,
                                           'timeout': int(timeout * 1000)}, timeout=timeout + 10)
        if 'exceptionDetails' in r:
            raise PlaytestError(r['exceptionDetails'].get('exception', {}).get('description')
                                or r['exceptionDetails'].get('text', 'script error'))
        return r.get('result', {}).get('value')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------- page-side helpers
RENDER_JS = r'''
window.__gbs = window.__gbs || (function () {
  const SC = window.SugarCube;
  const clone = o => { try { return structuredClone(o); } catch (e) { return JSON.parse(JSON.stringify(o)); } };
  const init = clone(SC.State.variables);
  function prepare(mode) {
    const v = clone(init);
    for (const k in v) {
      if (mode === 'high' && typeof v[k] === 'number') v[k] = 100;
      if (mode === 'zero' && typeof v[k] === 'number') v[k] = 0;
      if (mode === 'flip' && typeof v[k] === 'boolean') v[k] = !v[k];
    }
    for (const k of Object.keys(SC.State.variables)) delete SC.State.variables[k];
    Object.assign(SC.State.variables, v);
  }
  return {
    render(names, mode) {
      const out = {};
      const play = SC.Engine.play, goTo = SC.Engine.goTo, open = SC.Dialog && SC.Dialog.open;
      SC.Engine.play = () => {}; SC.Engine.goTo = () => {};
      if (SC.Dialog) SC.Dialog.open = () => {};
      try {
        for (const name of names) {
          const P = SC.Story.get(name);
          if (!P) continue;
          prepare(mode);
          const el = document.createElement('div');
          let errs = [];
          try { new SC.Wikifier(el, P.processText()); }
          catch (e) { errs.push('Script error: ' + (e && e.message ? e.message : e)); }
          el.querySelectorAll('.error-view, .error').forEach(x => {
            const t = (x.querySelector('.error') || x).textContent.trim();
            if (t) errs.push(t.slice(0, 300));
          });
          errs = [...new Set(errs)];
          if (errs.length) out[name] = errs;
        }
      } finally {
        SC.Engine.play = play; SC.Engine.goTo = goTo;
        if (SC.Dialog) SC.Dialog.open = open;
        prepare('start');
      }
      return out;
    }
  };
})();
'''

SYNTAX_JS = r'''
(async function (blocks) {
  const out = [];
  for (const b of blocks) {
    const errs = [];
    const h = e => { errs.push([e.message, e.lineno]); e.preventDefault(); };
    window.addEventListener('error', h);
    const s = document.createElement('script');
    s.textContent = 'if (false) {\n' + b.code + '\n}';
    document.head.appendChild(s);
    s.remove();
    window.removeEventListener('error', h);
    for (const [m, l] of errs) out.push({ id: b.id, message: m, line: l - 1 });
  }
  return out;
})
'''


# ---------------------------------------------------------------------- main
class Session:
    """A running game in the invisible browser (see launch())."""
    dt = srv = None
    is_sc = False
    load_events = ()

    @property
    def port(self):
        return self.srv.server_address[1]


ALERTS_JS = ("window.__gbfAlerts = [];"
             "for (const k of ['alert', 'confirm', 'prompt']) window[k] = function (m) {"
             " window.__gbfAlerts.push(String(m)); return k === 'confirm' ? true : null; };")


@contextlib.contextmanager
def launch(res, progress=None, patch=None):
    """Start the invisible browser with the game loaded; yields a Session. Cleans up afterwards."""
    progress = progress or (lambda p, m: None)
    if res.engine not in ('twine', 'html'):
        raise PlaytestError('Play-testing works for HTML games (Twine / SugarCube and other web games).')
    exe = find_browser()
    if not exe:
        raise PlaytestError('Microsoft Edge or Google Chrome is needed for play-testing, but neither was found.')
    html_path = res.html_path
    # Serve from the game folder only when the game file is inside it (commonpath fails across drives).
    game_dir, html_abs = os.path.abspath(res.game_dir), os.path.abspath(html_path)
    same_drive = os.path.splitdrive(game_dir)[0].lower() == os.path.splitdrive(html_abs)[0].lower()
    root = res.game_dir if same_drive and os.path.commonpath([game_dir, html_abs]) == game_dir \
        else os.path.dirname(html_path)
    rel_url = '/'.join(urllib.parse.quote(p) for p in os.path.relpath(html_path, root).replace('\\', '/').split('/'))
    profile = tempfile.mkdtemp(prefix='gbs-playtest-')
    srv = start_server(root, fallback=None if root == res.game_dir else res.game_dir,
                       patch=(urllib.parse.unquote('/' + rel_url), patch) if patch else None)
    proc = None
    dt = None
    try:
        progress(3, 'Starting the invisible browser')
        proc = subprocess.Popen([exe, '--headless=new', '--remote-debugging-port=0', f'--user-data-dir={profile}',
                                 '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--mute-audio',
                                 '--disable-gpu', '--remote-allow-origins=*', '--disable-background-networking',
                                 '--autoplay-policy=no-user-gesture-required', 'about:blank'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        port_file = os.path.join(profile, 'DevToolsActivePort')
        end = time.time() + 30
        while not os.path.isfile(port_file) or not open(port_file).read().strip():
            if time.time() > end or proc.poll() is not None:
                raise PlaytestError('The browser did not start.')
            time.sleep(0.1)
        port = int(open(port_file).read().split()[0])
        targets = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=10).read())
        page = next((t for t in targets if t.get('type') == 'page'), None)
        if not page:
            raise PlaytestError('The browser has no page to test in.')
        dt = DevTools(page['webSocketDebuggerUrl'])
        for d in ('Runtime.enable', 'Log.enable', 'Network.enable', 'Page.enable'):
            dt.call(d)
        # a pop-up (alert) at start would freeze the hidden page: note its text instead (window.__gbfAlerts)
        dt.call('Page.addScriptToEvaluateOnNewDocument', {'source': ALERTS_JS})
        progress(8, 'Loading the game')
        url = f'http://127.0.0.1:{srv.server_address[1]}/{rel_url}'
        dt.call('Page.navigate', {'url': url})
        dt.wait_event('Page.loadEventFired', timeout=120)
        # SugarCube starts a moment after the load event
        is_sc = False
        for _ in range(100):
            try:
                if dt.evaluate('!!(window.SugarCube && SugarCube.Story && SugarCube.State && SugarCube.Engine)', 10):
                    is_sc = True
                    break
            except PlaytestError:
                pass
            if not res.stats.get('format', '').lower().startswith('sugarcube'):
                time.sleep(1.5)
                break
            time.sleep(0.2)
        ses = Session()
        ses.dt, ses.srv, ses.is_sc, ses.load_events = dt, srv, is_sc, list(dt.events)
        yield ses
    finally:
        try:
            if dt:
                try:
                    dt.call('Browser.close', timeout=5)
                except Exception:
                    pass
                dt.close()
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            srv.shutdown()
            srv.server_close()
            shutil.rmtree(profile, ignore_errors=True)


def run(res, progress=None, modes=None, stop=None):
    """Play-test the scanned game. Returns (issues, summary dict)."""
    progress = progress or (lambda p, m: None)
    stop = stop or (lambda: False)
    issues, summary = [], {'passages': 0, 'renders': 0, 'modes': []}
    with launch(res, progress) as ses:
        dt, srv, is_sc, load_events = ses.dt, ses.srv, ses.is_sc, ses.load_events
        # syntax of every script, using the browser's own engine
        progress(14, 'Checking scripts with the browser\'s JavaScript engine')
        blocks = script_blocks(res)
        if blocks:
            payload = [{'id': n, 'code': b[2]} for n, b in enumerate(blocks)]
            for e in dt.evaluate(f'({SYNTAX_JS})({json.dumps(payload)})', 60) or []:
                e['message'] = re.sub(r"^Uncaught |Failed to execute '\w+' on '\w+': ", '', e['message'])
                where, start, _ = blocks[e['id']]
                line = start + max(0, e['line'] - 1) if start else 0
                issues.append(Issue(ERROR, 'Play-test', f'Script does not load: {e["message"]}', line, where,
                                    snippet=_line(res, line),
                                    what='The browser\'s JavaScript engine cannot read this script, so none of it runs. '
                                         'Features defined in it (macros, settings, special functions) are missing.',
                                    fix='Look at this line (and the one before it) for a missing bracket, quote or comma.'))

        # passages
        if is_sc and not stop():
            names = [n for n, (ln, tags) in res.passages.items()
                     if not set(tags) & {'widget', 'script', 'stylesheet', 'Twine.private'} and n not in ('StoryInit',)]
            summary['passages'] = len(names)
            dt.evaluate(RENDER_JS, 30)
            seen = {}
            use_modes = modes or [m for m, _ in MODES]
            summary['modes'] = use_modes
            total = len(names) * len(use_modes)
            done = 0
            for mode in use_modes:
                k = 0
                chunk = 25
                while k < len(names):
                    if stop():
                        break
                    batch = names[k:k + chunk]
                    try:
                        out = dt.evaluate(f'__gbs.render({json.dumps(batch)}, {json.dumps(mode)})', 60) or {}
                    except PlaytestError as e:
                        if chunk > 1:
                            chunk = 1
                            continue
                        out = {batch[0]: [f'The scene froze or crashed the test ({e}). It may contain an endless loop.']}
                    for name, errs in out.items():
                        for msg in errs:
                            seen.setdefault((name, msg), []).append(mode)
                    k += len(batch)
                    done += len(batch)
                    summary['renders'] = done
                    progress(15 + int(75 * done / max(1, total)), ('Playing scenes ({mode}): {k} of {n}', {'mode': mode, 'k': min(k, len(names)), 'n': len(names)}))
                    if chunk == 1 and k % 25 == 0:
                        chunk = 25
            mode_text = dict(MODES)
            for (name, msg), ms in seen.items():
                if name == 'Cheats' and '<<return>>' in msg:
                    continue  # needs real history; not a bug
                line = res.passages.get(name, (0, []))[0]
                when = ', '.join(mode_text[m] for m in ms) if len(ms) < len(use_modes) else 'in every test'
                issues.append(Issue(ERROR, 'Play-test', f'Error box when the scene is shown: {msg[:120]}', line, name,
                                    snippet=msg, what=f'When the game shows "{name}" ({when}), players see a red error box '
                                                      f'saying: {msg}',
                                    fix=playtest_fix(msg)))
        # runtime load problems
        issues += runtime_issues(dt.events, load_events, srv.server_address[1], res)
    progress(100, 'Play-test finished')
    return issues, summary


def _line(res, line):
    lines = res.lines
    return lines[line - 1].strip()[:240] if 0 < line <= len(lines) else ''


def script_blocks(res):
    """[(where, first line, code)] for every script in a Twine/HTML game."""
    out = []
    raw = '\n'.join(res.lines)
    for m in re.finditer(r'<script[^>]*id="twine-user-script"[^>]*>(.*?)</script>', raw, re.S):
        out.append(('Story JavaScript', raw.count('\n', 0, m.start(1)) + 1, m.group(1)))
    sc = res.context
    if sc is not None:
        for p in sc.passages:
            if 'script' in p.tags:
                out.append((p.name, p.line, p.raw_text))
            else:
                for m in re.finditer(r'<<script>>(.*?)<</script>>', p.text, re.S):
                    out.append((p.name, p.line + p.text.count('\n', 0, m.start(1)), m.group(1)))
        if res.engine == 'html':
            for m in re.finditer(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', raw, re.S | re.I):
                out.append(('(page)', raw.count('\n', 0, m.start(1)) + 1, m.group(1)))
    return out


def playtest_fix(msg):
    m = msg.lower()
    if 'cannot find a closing tag' in m:
        return 'A container macro (like <<if>>) in this scene is never closed. Add the missing closing tag.'
    if 'does not exist' in m and 'macro' in m:
        return 'The scene uses a macro that does not exist - usually a typo. Check the spelling of the macro named in the message.'
    if 'outside of a call to its parent' in m:
        return 'A <<else>>, <<elseif>> or closing tag is in the wrong place. Check the <<if>> structure around it.'
    if 'passage' in m and ('does not exist' in m or 'not found' in m):
        return 'The scene links to or includes a passage name that does not exist. Fix the name.'
    if 'assignment operator' in m:
        return 'A condition uses "=" instead of "is". Replace it.'
    if 'is not defined' in m or 'undefined' in m or 'null' in m:
        return 'The scene uses something that does not exist at that moment (often a misspelled variable, or a value that was never set).'
    return 'Open the scene and look for the macro or code named in the message.'


def runtime_issues(events, load_events, port, res):
    out = []
    base = f'http://127.0.0.1:{port}/'
    urls = {}
    failed = {}
    for e in events:
        m, p = e.get('method'), e.get('params', {})
        if m == 'Network.requestWillBeSent':
            urls[p.get('requestId')] = p.get('request', {}).get('url', '')
        elif m == 'Network.responseReceived':
            r = p.get('response', {})
            if r.get('status', 200) >= 400 and r.get('url', '').startswith(base):
                failed[r['url']] = r['status']
        elif m == 'Network.loadingFailed' and not p.get('canceled'):
            u = urls.get(p.get('requestId'), '')
            if u.startswith(base) and p.get('errorText') != 'net::ERR_ABORTED':
                failed.setdefault(u, p.get('errorText'))
    static_missing = {i.title.split(': ', 1)[-1].replace('\\', '/').lower() for i in res.issues
                      if i.category == 'Missing files'}
    dyn = []
    for u, status in sorted(failed.items()):
        rel = urllib.parse.unquote(u[len(base):]).split('?')[0]
        if rel.lower() in static_missing or rel.lower().endswith('favicon.ico'):
            continue
        dyn.append(rel)
    if dyn:
        out.append(Issue(ERROR, 'Play-test', f'{len(dyn)} file(s) failed to load while playing', 0, '',
                         snippet=', '.join(dyn[:10]) + (' …' if len(dyn) > 10 else ''),
                         what='These pictures/sounds were requested while the scenes were played, but do not exist. '
                              'Many of these names are built from variables, so the normal scan could not see them.',
                         fix='Add the missing files, or fix the names the game builds. Files:\n' + '\n'.join('  ' + d for d in dyn)))
    seen = set()
    for e in load_events:
        m, p = e.get('method'), e.get('params', {})
        text = None
        if m == 'Runtime.exceptionThrown':
            d = p.get('exceptionDetails', {})
            text = (d.get('exception', {}) or {}).get('description') or d.get('text')
        elif m == 'Runtime.consoleAPICalled' and p.get('type') == 'error':
            text = ' '.join(str(a.get('value', a.get('description', ''))) for a in p.get('args', []))
        elif m == 'Log.entryAdded' and p.get('entry', {}).get('level') == 'error' and p['entry'].get('source') != 'network':
            text = p['entry'].get('text')
        if text and text not in seen:
            seen.add(text)
            out.append(Issue(ERROR, 'Play-test', f'Error while the game starts: {text.splitlines()[0][:120]}', 0, '',
                             snippet=text[:400], what='The browser reported this error while loading the game. '
                                                      'Players may see a broken start screen or missing features.',
                             fix='Look at the Story JavaScript (or the script named in the message).'))
    return out
