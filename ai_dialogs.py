"""Windows for the AI helper: choosing the AI service, and "Fix with AI".
Translated only into the most used languages (AI_LANGS); the other languages show these texts in English."""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import ai
import scanner
import simple
from i18n import T
from ui_dialogs import APP_NAME, InfoWindow, Window

AI_LANGS = ('de', 'es', 'fr', 'pt', 'ru', 'ja', 'zh')

# Menu entries (in the Tools menu of the main window)
MENU_FIX = 'Fix with AI…'
MENU_LOG = 'Export log for AI (text)…'
MENU_SETTINGS = 'AI helper settings…'


def export_log(app):
    if not app.result or app.scanning:
        return
    base = os.path.splitext(os.path.basename(app.result.html_path))[0] + ' - log for AI'
    f = filedialog.asksaveasfilename(title=T('Save a log of the scan for an AI assistant'),
                                     initialdir=os.path.dirname(app.result.html_path), initialfile=base,
                                     defaultextension='.txt', filetypes=[(T('Text file'), '*.txt')])
    if not f:
        return
    try:
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(app.ai_log_text())
    except OSError as e:
        messagebox.showerror(APP_NAME, T('Could not save the log:') + f'\n{e}')
        return
    app.set_status(T('Log saved: {path}. Give it to the AI together with the game file.', path=f))


def open_fix(app):
    if not app.result or app.scanning:
        messagebox.showinfo(APP_NAME, T('Open and scan a game first.'))
        return
    AIFixDialog(app)


class AISettingsDialog(Window):
    """Pick an AI service, paste the key, pick a model."""

    def __init__(self, app, then=None):
        super().__init__(app, T('AI helper'), modal=True)
        self.then = then
        c = app.colors
        st = app.settings.setdefault('ai', {})
        frm = ttk.Frame(self.top, padding=16)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text=T('Which AI should suggest fixes?'), font=app.f_bold).pack(anchor='w')
        ttk.Label(frm, text=T('Online services need an API key from their website and charge you for use. '
                              'LM Studio and Ollama run models on this computer for free; start them and load a model first.'),
                  foreground=c['muted'], wraplength=int(640 * app.scale), justify='left').pack(anchor='w', pady=(2, 10))
        grid = ttk.Frame(frm)
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text=T('Service')).grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.prov_var = tk.StringVar(value=st.get('provider', ai.DEFAULT_PROVIDER))
        self.prov_box = ttk.Combobox(grid, textvariable=self.prov_var, values=list(ai.PROVIDERS), state='readonly', width=40)
        self.prov_box.grid(row=0, column=1, sticky='w')
        self.prov_box.bind('<<ComboboxSelected>>', lambda e: self.load())
        ttk.Label(grid, text=T('API key')).grid(row=1, column=0, sticky='w', padx=(0, 8), pady=(8, 0))
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(grid, textvariable=self.key_var, show='•', width=60)
        self.key_entry.grid(row=1, column=1, sticky='we', pady=(8, 0))
        ttk.Label(grid, text=T('Address')).grid(row=2, column=0, sticky='w', padx=(0, 8), pady=(8, 0))
        self.url_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.url_var, width=60).grid(row=2, column=1, sticky='we', pady=(8, 0))
        ttk.Label(grid, text=T('Model')).grid(row=3, column=0, sticky='w', padx=(0, 8), pady=(8, 0))
        mrow = ttk.Frame(grid)
        mrow.grid(row=3, column=1, sticky='we', pady=(8, 0))
        self.model_var = tk.StringVar()
        self.model_box = ttk.Combobox(mrow, textvariable=self.model_var, width=44)
        self.model_box.pack(side='left')
        ttk.Button(mrow, text=T('Get the model list'), command=self.fetch_models).pack(side='left', padx=(6, 0))
        self.note = ttk.Label(frm, text='', foreground=c['muted'], wraplength=int(640 * app.scale), justify='left')
        self.note.pack(anchor='w', pady=(10, 0))
        ttk.Label(frm, text=T('The key is stored scrambled by Windows, so only your Windows account can read it. '
                              'With an online service, the problems and the lines around them are sent to that company.'),
                  foreground=c['muted'], wraplength=int(640 * app.scale), justify='left').pack(anchor='w', pady=(8, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=(14, 0))
        ttk.Button(btns, text=T('Cancel'), command=self.close).pack(side='right')
        ttk.Button(btns, text=T('Save'), style='Accent.TButton', command=self.save).pack(side='right', padx=(0, 6))
        ttk.Button(btns, text=T('Test connection'), command=self.test).pack(side='left')
        self.load()
        self.place_center()

    def load(self):
        st = self.app.settings.get('ai', {})
        name = self.prov_var.get()
        p = ai.PROVIDERS[name]
        self.key_var.set(ai.unprotect_key(st.get('keys', {}).get(name, '')))
        self.url_var.set(st.get('urls', {}).get(name, p['url']))
        self.model_var.set(st.get('models', {}).get(name, p['model']))
        self.model_box.configure(values=[])
        self.key_entry.configure(state='normal' if p['key'] or name.startswith('Other') else 'disabled')
        self.note.configure(text=T('No key needed: it runs on this computer.') if not p['key'] else '')

    def cfg(self):
        name = self.prov_var.get()
        return dict(ai.PROVIDERS[name], name=name, key=self.key_var.get().strip(), url=self.url_var.get().strip(),
                    model=self.model_var.get().strip())

    def _run(self, fn, done):
        self.note.configure(text=T('Connecting…'))
        q = queue.Queue()
        threading.Thread(target=lambda: q.put(self._try(fn)), daemon=True).start()

        def poll():
            try:
                ok, val = q.get_nowait()
            except queue.Empty:
                self.top.after(100, poll)
                return
            if self.top.winfo_exists():
                done(ok, val)
        self.top.after(100, poll)

    @staticmethod
    def _try(fn):
        try:
            return True, fn()
        except ai.AIError as e:
            return False, str(e)
        except Exception as e:
            return False, f'{type(e).__name__}: {e}'

    def fetch_models(self):
        cfg = self.cfg()

        def done(ok, val):
            if ok:
                self.model_box.configure(values=val)
                if val and self.model_var.get() not in val:
                    self.model_var.set(val[0])
                self.note.configure(text=T('{n} models found. Pick one from the list.', n=len(val)))
            else:
                self.note.configure(text=val)
        self._run(lambda: ai.list_models(cfg), done)

    def test(self):
        cfg = self.cfg()

        def done(ok, val):
            self.note.configure(text=T('It works. The AI answered: {text}', text=str(val)[:120]) if ok else val)
        self._run(lambda: ai.chat(cfg, 'Answer in one short sentence.', 'Say hello.'), done)

    def save(self):
        cfg = self.cfg()
        if not cfg['model']:
            self.note.configure(text=T('Pick a model first. "Get the model list" shows what this service has.'))
            return
        st = self.app.settings.setdefault('ai', {})
        name = cfg['name']
        st['provider'] = name
        st.setdefault('urls', {})[name] = cfg['url']
        st.setdefault('models', {})[name] = cfg['model']
        if cfg['key']:
            st.setdefault('keys', {})[name] = ai.protect_key(cfg['key'])
        else:
            st.get('keys', {}).pop(name, None)
        self.app.save_settings()
        self.app.set_status(T('AI helper set to {name} · {model}', name=name, model=cfg['model']))
        then = self.then
        self.close()
        if then:
            then()


class AIFixDialog(Window):
    """Ask the AI for fixes, review each one, and save a fixed copy of the game."""

    def __init__(self, app):
        super().__init__(app, T('Fix with AI'), (1000, 760))
        c = app.colors
        self.res = app.result
        self.changes, self.notes = [], []
        self.q = queue.Queue()
        self.busy = self.stopping = False
        self.copy_path = self.last_check = None
        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)
        top = ttk.Frame(frm)
        top.pack(fill='x')
        self.using = ttk.Label(top, text='')
        self.using.pack(side='left')
        ttk.Button(top, text=T('Change AI…'), command=lambda: AISettingsDialog(app, then=self.show_using)).pack(side='left', padx=(8, 0))
        scope = ttk.Frame(frm, padding=(0, 8, 0, 0))
        scope.pack(fill='x')
        ttk.Label(scope, text=T('Fix:')).pack(side='left')
        sel = app.selected_issue()
        self.scope = tk.StringVar(value='both')
        for val, label in (('selected', T('Only the selected problem')), ('both', T('Broken + probably broken')),
                           ('all', T('Every problem that has a line'))):
            rb = ttk.Radiobutton(scope, text=label, value=val, variable=self.scope, command=self.show_count)
            rb.pack(side='left', padx=(8, 0))
            if val == 'selected' and not (sel and sel.line):
                rb.configure(state='disabled')
        self.count = ttk.Label(scope, text='', foreground=c['muted'])
        self.count.pack(side='left', padx=(12, 0))
        run = ttk.Frame(frm, padding=(0, 8, 0, 0))
        run.pack(fill='x')
        self.btn_ask = ttk.Button(run, text=T('Ask the AI'), style='Accent.TButton', command=self.start)
        self.btn_ask.pack(side='left')
        self.btn_stop = ttk.Button(run, text=T('Stop'), command=self.stop, state='disabled')
        self.btn_stop.pack(side='left', padx=(6, 0))
        self.status = ttk.Label(run, text=T('Nothing changes until you save, and your game file itself is never changed: '
                                            'accepted fixes go into a copy.'), foreground=c['muted'],
                                wraplength=int(720 * app.scale), justify='left')
        self.status.pack(side='left', padx=(12, 0))
        lst = ttk.Frame(frm, padding=(0, 8, 0, 0))
        lst.pack(fill='both', expand=True)
        cols = ('use', 'problem', 'line', 'change')
        self.tree = ttk.Treeview(lst, columns=cols, show='headings', height=10, selectmode='browse')
        for col, txt, w, st in (('use', T('Use'), 60, False), ('problem', T('Problem'), 380, True),
                                ('line', T('Line'), 70, False), ('change', T('Change'), 420, True)):
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=int(w * app.scale), stretch=st, anchor='center' if col in ('use', 'line') else 'w')
        sb = ttk.Scrollbar(lst, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_detail())
        self.tree.bind('<Double-1>', lambda e: self.toggle())
        self.tree.bind('<space>', lambda e: self.toggle())
        self.detail = self.text_widget(frm, height=9)
        self.detail.configure(font=app.fonts['fixed'])
        self.detail.pack(fill='x', pady=(8, 0))
        bar = ttk.Frame(frm, padding=(0, 8, 0, 0))
        bar.pack(fill='x')
        self.btn_toggle = ttk.Button(bar, text=T('Use / don\'t use (double-click)'), command=self.toggle, state='disabled')
        self.btn_toggle.pack(side='left')
        self.btn_notes = ttk.Button(bar, text=T('Notes from the AI'), command=self.show_notes, state='disabled')
        self.btn_notes.pack(side='left', padx=(6, 0))
        ttk.Button(bar, text=T('Close'), command=self.close).pack(side='right')
        self.btn_save = ttk.Button(bar, text=T('Save fixed copy…'), style='Accent.TButton', command=self.save, state='disabled')
        self.btn_save.pack(side='right', padx=(0, 6))
        self.btn_open = ttk.Button(bar, text=T('Open the fixed copy'), command=self.open_copy)
        self.show_using()
        self.show_count()
        self.place_center()

    def cfg(self):
        return self.app.ai_config()

    def show_using(self):
        cfg = self.cfg()
        self.using.configure(text=T('AI: {name} · {model}', name=cfg['name'], model=cfg['model'] or T('(no model picked)')))

    def items(self):
        a = self.app
        s = self.scope.get()
        if s == 'selected':
            i = a.selected_issue()
            return [i] if i and i.line else []
        sev = {scanner.ERROR, scanner.WARNING} if s == 'both' else {scanner.ERROR, scanner.WARNING, scanner.INFO}
        return [i for i in a.active_issues() if i.line and i.severity in sev]

    def show_count(self):
        n = len(self.items())
        self.count.configure(text=T('{n} problems, {k} request(s) to the AI', n=n, k=-(-n // ai.BATCH)))

    # ---- asking
    def start(self):
        cfg = self.cfg()
        if not cfg['model'] or (ai.PROVIDERS[cfg['name']]['key'] and not cfg['key']):
            AISettingsDialog(self.app, then=self.show_using)
            return
        items = self.items()
        if not items:
            self.status.configure(text=T('There are no problems with a line number to send.'))
            return
        self.busy, self.stopping = True, False
        self.btn_ask.configure(state='disabled')
        self.btn_stop.configure(state='normal')
        self.btn_save.configure(state='disabled')
        self.status.configure(text=T('Asking the AI…'))
        res = self.res

        def work():
            try:
                out = ai.ask(cfg, res, items, progress=lambda k, n: self.q.put(('progress', k, n)),
                             stop=lambda: self.stopping)
                self.q.put(('done', out))
            except Exception as e:
                self.q.put(('done', ([], [f'{type(e).__name__}: {e}'])))
        threading.Thread(target=work, daemon=True).start()
        self.top.after(100, self.poll)

    def stop(self):
        self.stopping = True
        self.status.configure(text=T('Stopping after the current request…'))

    def poll(self):
        if not self.top.winfo_exists():
            return
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == 'progress':
                    self.status.configure(text=T('Asking the AI… request {k} of {n}', k=msg[1], n=msg[2]))
                elif msg[0] == 'done':
                    self.finish(*msg[1])
                    return
                elif msg[0] == 'checked':
                    self.checked(*msg[1:])
                    return
        except queue.Empty:
            pass
        self.top.after(100, self.poll)

    def finish(self, changes, notes):
        self.busy = False
        self.changes, self.notes = changes, notes
        self.btn_ask.configure(state='normal')
        self.btn_stop.configure(state='disabled')
        self.btn_notes.configure(state='normal' if notes else 'disabled')
        self.btn_toggle.configure(state='normal' if changes else 'disabled')
        self.btn_save.configure(state='normal' if changes else 'disabled')
        self.fill()
        flagged = sum(1 for c in changes if c.check)
        self.status.configure(text=(T('{n} changes suggested. Check each one, untick the ones you don\'t want, then save a fixed copy.',
                                      n=len(changes)) + ('  ' + T('{k} marked ⚠ swap a missing picture or sound for another file: '
                                                                  'check those yourself.', k=flagged) if flagged else ''))
                              if changes else
                              T('The AI suggested no usable changes. See "Notes from the AI".'))
        if changes:
            self.tree.selection_set(self.tree.get_children()[0])
        elif notes and not self.app.settings.get('_selftest'):
            self.show_notes()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for n, ch in enumerate(self.changes):
            title = simple.simplify(ch.issue)['title'] if self.app.simple_on else ch.issue.title
            self.tree.insert('', 'end', iid=str(n), values=(self.mark(ch), ('⚠ ' if ch.check else '') + title, ch.line,
                                                             f'{ch.find}  →  {ch.replace}'), tags=('check',) if ch.check else ())
        self.tree.tag_configure('check', foreground=self.app.colors['warning'])

    @staticmethod
    def mark(ch):
        return '✔' if ch.accepted else '—'

    def current(self):
        sel = self.tree.selection()
        return self.changes[int(sel[0])] if sel else None

    def toggle(self):
        ch = self.current()
        if not ch:
            return 'break'
        ch.accepted = not ch.accepted
        self.tree.set(str(self.changes.index(ch)), 'use', self.mark(ch))
        self.btn_save.configure(state='normal' if any(c.accepted for c in self.changes) else 'disabled')
        return 'break'

    def show_detail(self):
        ch = self.current()
        t = self.detail
        t.delete('1.0', 'end')
        if not ch:
            return
        line = ai.readable_lines(self.res, ch.file)[ch.line - 1].rstrip('\r')
        pos = line.find(ch.find)
        a = max(0, pos - 80)
        now = line[a:pos + len(ch.find) + 80]
        new = now.replace(ch.find, ch.replace, 1)
        t.insert('end', f'#{ai.issue_id(self.res, ch.issue)} {ch.issue.title}\n')
        t.insert('end', T('Line {n}', n=ch.line) + (f' · {ch.issue.passage}' if ch.issue.passage else '') + '\n\n')
        t.insert('end', T('Now:') + '\n   ' + now.strip() + '\n')
        t.insert('end', T('Change to:') + '\n   ' + new.strip() + '\n')
        if ch.why:
            t.insert('end', '\n' + T('Why:') + ' ' + ch.why + '\n')
        if ch.check:
            t.insert('end', '\n⚠ ' + T(ch.check) + '\n')

    def show_notes(self):
        InfoWindow(self.app, T('Notes from the AI'), '\n'.join('• ' + n for n in self.notes), heading=T('Notes from the AI'))

    # ---- saving
    def save(self):
        res = self.res
        accepted = [c for c in self.changes if c.accepted]
        one = {os.path.normcase(c.file or res.html_path) for c in accepted} == {os.path.normcase(res.html_path)}
        if one:
            base, ext = os.path.splitext(os.path.basename(res.html_path))
            dest = filedialog.asksaveasfilename(parent=self.top, title=T('Save the fixed copy'), initialdir=os.path.dirname(res.html_path),
                                                initialfile=f'{base} (AI fixed){ext}', defaultextension=ext,
                                                filetypes=[(T('Game file'), '*' + ext)])
        else:
            dest = filedialog.askdirectory(parent=self.top, title=T('Choose an empty folder for the fixed files'))
        if not dest:
            return
        cfg = self.cfg()
        try:
            written = ai.write_copy(res, self.changes, dest)
            log = (os.path.splitext(dest)[0] if one else os.path.join(dest, 'AI')) + ' - changes.txt'
            with open(log, 'w', encoding='utf-8') as fh:
                fh.write(ai.change_list(res, self.changes, self.notes, f'{cfg["name"]} · {cfg["model"]}'))
        except (ai.AIError, OSError) as e:
            messagebox.showerror(APP_NAME, T('Could not save the fixed copy:') + f'\n{e}', parent=self.top)
            return
        self.app.set_status(T('Fixed copy saved: {path}', path=written[0] if one else dest))
        if not one:
            self.status.configure(text=T('Saved {n} fixed files and a list of the changes in {path}. Back up the game, '
                                         'then copy them over the game\'s files.', n=len(written), path=dest))
            return
        self.copy_path = written[0]
        self.status.configure(text=T('Saved. Checking the fixed copy again…'))
        game_dir = res.game_dir

        def work():
            try:
                self.q.put(('checked', ai.compare(res, scanner.scan_any(written[0], game_dir), self.changes), written[0]))
            except Exception as e:
                self.q.put(('checked', None, f'{e}'))
        threading.Thread(target=work, daemon=True).start()
        self.top.after(100, self.poll)

    def checked(self, result, info):
        if result is None:
            self.status.configure(text=T('Saved, but the fixed copy could not be checked: {error}', error=info))
            return
        fixed, new, revealed = result
        self.last_check = result
        self.status.configure(text=T('Saved and checked: {fixed} problems gone, {new} new problems.', fixed=fixed, new=len(new))
                              + ('  ' + T('{n} more problems were found on the changed lines (often a second problem on the same line).',
                                          n=len(revealed)) if revealed else '')
                              + ('  ' + T('Open the fixed copy to see them.') if new or revealed else ''))
        self.btn_open.pack(side='right', padx=(0, 6))

    def open_copy(self):
        if self.copy_path:
            self.app.open_path(self.copy_path)
            self.close()
