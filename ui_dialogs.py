"""Extra windows for Game Doctor: fix wizard, test a line, bug report writer,
version compare, lessons, glossary, guided tour, text editor picker, info window."""
import os
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import content
import fixes
import scanner
import simple
from i18n import T

APP_NAME = 'Game Doctor'


class Window:
    """A themed child window that follows the app's theme and size."""

    def __init__(self, app, title, size=None, modal=False):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(title)
        self.top.configure(bg=app.colors['bg'])
        self.top.transient(app.root)
        try:
            self.top.iconbitmap(app.icon_path)
        except tk.TclError:
            pass
        if size:
            w, h = size
            s = app.scale
            self.top.geometry(f'{int(w * s)}x{int(h * s)}')
        app.windows.append(self)
        self.top.protocol('WM_DELETE_WINDOW', self.close)
        self.top.bind('<Escape>', lambda e: self.close())
        if modal:
            self.top.grab_set()

    def text_widget(self, parent, **kw):
        t = tk.Text(parent, wrap='word', relief='flat', borderwidth=0, padx=12, pady=10, highlightthickness=1,
                    font=self.app.fonts['text'], cursor='arrow', **kw)
        self.app.style_text(t)
        t.bind('<Key>', lambda e: None if (e.state & 4 and e.keysym.lower() in ('c', 'a')) else 'break')
        return t

    def restyle(self):
        self.top.configure(bg=self.app.colors['bg'])
        for w in self._texts():
            self.app.style_text(w)

    def _texts(self):
        out = []
        stack = [self.top]
        while stack:
            w = stack.pop()
            if isinstance(w, tk.Text):
                out.append(w)
            stack.extend(w.winfo_children())
        return out

    def place_center(self):
        self.top.update_idletasks()
        r = self.app.root
        x = r.winfo_rootx() + (r.winfo_width() - self.top.winfo_width()) // 2
        y = r.winfo_rooty() + max(40, (r.winfo_height() - self.top.winfo_height()) // 3)
        self.top.geometry(f'+{max(0, x)}+{max(0, y)}')

    def close(self):
        if self in self.app.windows:
            self.app.windows.remove(self)
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()

    def exists(self):
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False


# ---------------------------------------------------------------------- info window
class InfoWindow(Window):
    def __init__(self, app, title, body, heading=None):
        super().__init__(app, title, (640, 420))
        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)
        self.text = self.text_widget(frm, height=12)
        self.text.pack(fill='both', expand=True)
        if heading:
            self.text.insert('end', heading + '\n', 'title')
        self.text.insert('end', body)
        bar = ttk.Frame(frm, padding=(0, 8, 0, 0))
        bar.pack(fill='x')
        ttk.Button(bar, text=T('Close'), command=self.close).pack(side='right')
        ttk.Button(bar, text=T('Copy'), command=lambda: app.copy_text(body)).pack(side='right', padx=(0, 6))
        self.place_center()


# ---------------------------------------------------------------------- text editor picker
class EditorDialog(Window):
    def __init__(self, app, found, current):
        super().__init__(app, T('Choose your text editor'), modal=True)
        c = app.colors
        frm = ttk.Frame(self.top, padding=16)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text=T('Which program should open the game file?'), font=app.f_bold).pack(anchor='w')
        ttk.Label(frm, text=T('These text editors were found on this computer:'), foreground=c['muted']).pack(anchor='w', pady=(2, 8))
        self.choice = tk.StringVar()
        self.found = found
        cur_path = (current or {}).get('path', '')
        for n, ed in enumerate(found):
            ttk.Radiobutton(frm, text=f'{ed["name"]}    {ed["path"]}', value=str(n), variable=self.choice,
                            command=self.pick_found).pack(anchor='w', pady=1)
            if os.path.normcase(ed['path']) == os.path.normcase(cur_path):
                self.choice.set(str(n))
        ttk.Radiobutton(frm, text=T('Another program…'), value='other', variable=self.choice).pack(anchor='w', pady=(6, 1))
        grid = ttk.Frame(frm)
        grid.pack(fill='x', pady=(10, 0))
        ttk.Label(grid, text=T('Program')).grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.path_var = tk.StringVar(value=cur_path)
        ttk.Entry(grid, textvariable=self.path_var, width=60).grid(row=0, column=1, sticky='we')
        ttk.Button(grid, text=T('Browse…'), command=self.browse).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(grid, text=T('Options')).grid(row=1, column=0, sticky='w', padx=(0, 8), pady=(6, 0))
        self.args_var = tk.StringVar(value=(current or {}).get('args', '"{file}"'))
        ttk.Entry(grid, textvariable=self.args_var, width=60).grid(row=1, column=1, sticky='we', pady=(6, 0))
        grid.columnconfigure(1, weight=1)
        ttk.Label(frm, text=T('{file} is replaced by the game file and {line} by the line number. Notepad++ uses: -n{line} "{file}"'),
                  foreground=c['muted'], wraplength=int(620 * app.scale)).pack(anchor='w', pady=(6, 0))
        if not self.choice.get():
            self.choice.set('other' if cur_path else ('0' if found else 'other'))
            if found and not cur_path:
                self.pick_found()
        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=(14, 0))
        ttk.Button(btns, text=T('Cancel'), command=self.close).pack(side='right')
        ttk.Button(btns, text=T('Save'), style='Accent.TButton', command=self.save).pack(side='right', padx=(0, 6))
        self.place_center()
        self.top.focus_set()

    def pick_found(self):
        ed = self.found[int(self.choice.get())]
        self.path_var.set(ed['path'])
        self.args_var.set(ed['args'])

    def browse(self):
        f = filedialog.askopenfilename(parent=self.top, title=T('Choose the text editor program'),
                                       filetypes=[(T('Programs'), '*.exe'), (T('All files'), '*.*')],
                                       initialdir=os.environ.get('ProgramFiles', 'C:\\'))
        if f:
            self.path_var.set(os.path.normpath(f))
            self.choice.set('other')
            name = os.path.basename(f).lower()
            for ename, paths, args in self.app.known_editors:
                if any(os.path.basename(os.path.expandvars(p)).lower() == name for p in paths):
                    self.args_var.set(args)
                    break

    def save(self):
        path = self.path_var.get().strip().strip('"')
        if not os.path.isfile(path):
            messagebox.showerror(APP_NAME, T('That program could not be found. Use "Browse…" to pick it.'), parent=self.top)
            return
        ch = self.choice.get()
        name = self.found[int(ch)]['name'] if ch.isdigit() else os.path.splitext(os.path.basename(path))[0]
        self.app.settings['editor'] = dict(name=name, path=path, args=self.args_var.get().strip() or '"{file}"')
        self.app.save_settings()
        self.app.set_status(T('Text editor set to {name}', name=name))
        self.close()


# ---------------------------------------------------------------------- fix wizard
class WizardWindow(Window):
    """One problem at a time: explanation, before/after, buttons, and a check."""

    def __init__(self, app):
        super().__init__(app, T('Fix wizard'), (860, 660))
        self.keys = [i.key for i in app.wizard_candidates()]
        self.fixed = set()
        self.pos = 0
        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)
        head = ttk.Frame(frm)
        head.pack(fill='x')
        self.step_label = ttk.Label(head, text='', font=app.f_bold)
        self.step_label.pack(side='left')
        self.fixed_label = ttk.Label(head, text='')
        self.fixed_label.pack(side='right')
        self.bar = ttk.Progressbar(frm, maximum=max(1, len(self.keys)))
        self.bar.pack(fill='x', pady=(6, 8))
        self.text = self.text_widget(frm)
        self.text.pack(fill='both', expand=True)
        row1 = ttk.Frame(frm, padding=(0, 8, 0, 0))
        row1.pack(fill='x')
        self.btn_edit = ttk.Button(row1, text=T('Open in my text editor'), command=lambda: app.open_in_editor(issue=self.issue()))
        self.btn_edit.pack(side='left')
        self.btn_copyfix = ttk.Button(row1, text=T('Copy fixed line'), command=lambda: app.copy_fixed(self.issue()))
        self.btn_copyfix.pack(side='left', padx=(6, 0))
        self.btn_search = ttk.Button(row1, text=T('Copy text to search for'), command=lambda: app.copy_search(self.issue()))
        self.btn_search.pack(side='left', padx=(6, 0))
        self.btn_check = ttk.Button(row1, text=T('I fixed it - check'), style='Accent.TButton', command=self.check)
        self.btn_check.pack(side='right')
        row2 = ttk.Frame(frm, padding=(0, 6, 0, 0))
        row2.pack(fill='x')
        self.btn_back = ttk.Button(row2, text=T('◀ Back'), command=lambda: self.go(-1))
        self.btn_back.pack(side='left')
        self.btn_skip = ttk.Button(row2, text=T('Skip ▶'), command=lambda: self.go(1))
        self.btn_skip.pack(side='left', padx=(6, 0))
        self.btn_ignore = ttk.Button(row2, text=T('Mark as intentional'), command=self.ignore)
        self.btn_ignore.pack(side='left', padx=(6, 0))
        ttk.Button(row2, text=T('Close'), command=self.close).pack(side='right')
        self.show()
        self.place_center()

    def issue(self):
        if not self.keys:
            return None
        return self.app.issue_by_key(self.keys[self.pos])

    def show(self, note=None):
        t = self.text
        t.delete('1.0', 'end')
        n = len(self.keys)
        self.bar.configure(maximum=max(1, n), value=len(self.fixed))
        self.fixed_label.configure(text=T('{done} of {total} fixed', done=len(self.fixed), total=n))
        if not n:
            self.step_label.configure(text=T('Nothing to fix'))
            t.insert('end', T('There are no problems left that need fixing. Well done!') + '\n', 'title')
            for b in (self.btn_edit, self.btn_copyfix, self.btn_search, self.btn_check, self.btn_back, self.btn_skip, self.btn_ignore):
                b.configure(state='disabled')
            return
        self.step_label.configure(text=T('Problem {n} of {total}', n=self.pos + 1, total=n))
        i = self.issue()
        if i is None:
            t.insert('end', T('This problem is gone - it was fixed or the file changed. Click Skip to go on.') + '\n', 'c_clean')
            for b in (self.btn_edit, self.btn_copyfix, self.btn_search, self.btn_check, self.btn_ignore):
                b.configure(state='disabled')
        else:
            self.app.render_issue(t, i, wizard=True)   # clears the text first, so the note goes in afterwards
            has_fix = self.app.fix_form(i) is not None
            self.btn_copyfix.configure(state='normal' if has_fix else 'disabled')
            self.btn_edit.configure(state='normal' if i.line else 'disabled')
            self.btn_search.configure(state='normal' if i.search else 'disabled')
            self.btn_check.configure(state='normal')
            self.btn_ignore.configure(state='normal')
        self.btn_back.configure(state='normal' if self.pos > 0 else 'disabled')
        self.btn_skip.configure(state='normal' if self.pos < n - 1 else 'disabled')
        if note:
            t.insert('1.0', note[0] + '\n\n', note[1])
        self.last_note = note
        t.see('1.0')

    def go(self, d):
        self.pos = max(0, min(len(self.keys) - 1, self.pos + d))
        self.show()

    def ignore(self):
        i = self.issue()
        if i:
            self.app.toggle_ignore(i)
            self.fixed.add(self.keys[self.pos])
            self.go(1)

    def check(self):
        i = self.issue()
        if not i:
            return
        self.btn_check.configure(state='disabled', text=T('Checking…'))

        def done(result):
            if not self.exists():
                return
            self.btn_check.configure(state='normal', text=T('I fixed it - check'))
            fixed, detail = result
            if fixed:
                self.fixed.add(self.keys[self.pos])
                if self.pos < len(self.keys) - 1:
                    self.pos += 1
                self.show((T('✔ Fixed! Well done. Here is the next one.'), 'c_clean'))
            else:
                self.show((detail, 'c_warning'))
        self.app.check_fix(i, done)


# ---------------------------------------------------------------------- test a line
class TestLineDialog(Window):
    def __init__(self, app):
        super().__init__(app, T('Test a line before you change it'), (780, 560))
        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text=T('Paste or type the line (or the whole passage) you want to use:'), font=app.f_bold).pack(anchor='w')
        self.input = tk.Text(frm, height=6, wrap='word', relief='flat', highlightthickness=1, font=app.fonts['fixed'], undo=True)
        app.style_text(self.input, editable=True)
        self.input.pack(fill='x', pady=(6, 6))
        opts = ttk.Frame(frm)
        opts.pack(fill='x')
        self.whole = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text=T('This is a whole passage (also check that every tag is closed)'), variable=self.whole).pack(side='left')
        ttk.Button(opts, text=T('Check'), style='Accent.TButton', command=self.run).pack(side='right')
        self.out = self.text_widget(frm)
        self.out.pack(fill='both', expand=True, pady=(8, 0))
        bar = ttk.Frame(frm, padding=(0, 8, 0, 0))
        bar.pack(fill='x')
        ttk.Button(bar, text=T('Close'), command=self.close).pack(side='right')
        self.out.insert('end', T('Nothing is changed in your game. This only checks the text you type here.'), 'muted')
        self.place_center()
        self.input.focus_set()

    def run(self):
        text = self.input.get('1.0', 'end').rstrip('\n')
        found = self.app.test_snippet(text, self.whole.get())
        o = self.out
        o.delete('1.0', 'end')
        if found is None:
            o.insert('end', T('Testing single lines works for Twine (SugarCube) games. Open one first.') + '\n', 'c_warning')
            return
        if not found:
            o.insert('end', '✔ ' + T('No problems found in this text.') + '\n', 'c_clean')
            return
        o.insert('end', T('{n} problem(s) found:', n=len(found)) + '\n\n', 'bold')
        for i in found:
            d = simple.simplify(i) if self.app.simple_on else dict(title=i.title, what=i.what)
            o.insert('end', f'{self.app.sev_icon(i)} ', i.severity)
            o.insert('end', d['title'] + '\n', 'bold')
            o.insert('end', (d.get('what') or '') + '\n', 'muted')
            if i.old or i.new:
                o.insert('end', T('Suggested:') + ' ', 'label_inline')
                o.insert('end', (i.new if i.new else T('(delete this line)')) + '\n', 'after')
            o.insert('end', '\n')


# ---------------------------------------------------------------------- bug report writer
class BugReportDialog(Window):
    def __init__(self, app, only=None):
        super().__init__(app, T('Write a bug report for the developer'), (820, 640))
        self.only = only
        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)
        opts = ttk.Frame(frm)
        opts.pack(fill='x')
        ttk.Label(opts, text=T('Style:')).pack(side='left')
        self.style_var = tk.StringVar(value='plain')
        for val, label in (('plain', T('Plain text')), ('discord', T('Discord / Reddit')), ('bbcode', T('Forum (BBCode)'))):
            ttk.Radiobutton(opts, text=label, value=val, variable=self.style_var, command=self.update).pack(side='left', padx=(8, 0))
        opts2 = ttk.Frame(frm, padding=(0, 6, 0, 0))
        opts2.pack(fill='x')
        ttk.Label(opts2, text=T('Include:')).pack(side='left')
        self.scope = tk.StringVar(value='selected' if only else 'errors')
        for val, label in (('errors', simple.severity(scanner.ERROR)), ('both', T('Broken + probably broken')),
                           ('selected', T('Only the selected problem'))):
            rb = ttk.Radiobutton(opts2, text=label, value=val, variable=self.scope, command=self.update)
            rb.pack(side='left', padx=(8, 0))
            if val == 'selected' and not only:
                rb.configure(state='disabled')
        self.with_fix = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts2, text=T('Include how to fix'), variable=self.with_fix, command=self.update).pack(side='left', padx=(16, 0))
        self.text = self.text_widget(frm)
        self.text.configure(font=app.fonts['fixed'])
        self.text.pack(fill='both', expand=True, pady=(8, 0))
        bar = ttk.Frame(frm, padding=(0, 8, 0, 0))
        bar.pack(fill='x')
        ttk.Button(bar, text=T('Close'), command=self.close).pack(side='right')
        ttk.Button(bar, text=T('Save as…'), command=self.save).pack(side='right', padx=(0, 6))
        ttk.Button(bar, text=T('Copy'), style='Accent.TButton', command=lambda: app.copy_text(self.report)).pack(side='right', padx=(0, 6))
        self.update()
        self.place_center()

    def items(self):
        s = self.scope.get()
        if s == 'selected' and self.only:
            return [self.only]
        sev = {scanner.ERROR} if s == 'errors' else {scanner.ERROR, scanner.WARNING}
        return [i for i in self.app.active_issues() if i.severity in sev]

    def update(self):
        self.report = self.app.bug_report_text(self.items(), self.style_var.get(), self.with_fix.get())
        self.text.delete('1.0', 'end')
        self.text.insert('end', self.report)

    def save(self):
        f = filedialog.asksaveasfilename(parent=self.top, title=T('Save the bug report'), defaultextension='.txt',
                                         initialfile='bug report.txt', filetypes=[(T('Text file'), '*.txt')])
        if f:
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(self.report)
            self.app.set_status(T('Bug report saved: {path}', path=f))


# ---------------------------------------------------------------------- compare versions
class CompareDialog(Window):
    def __init__(self, app, other_path=None):
        super().__init__(app, T('Compare two versions'), (860, 640))
        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text=T('Current game:') + ' ' + os.path.basename(app.result.html_path), font=app.f_bold).pack(anchor='w')
        row = ttk.Frame(frm, padding=(0, 6, 0, 0))
        row.pack(fill='x')
        ttk.Label(row, text=T('Other version:')).pack(side='left')
        self.path_var = tk.StringVar(value=other_path or '')
        ttk.Entry(row, textvariable=self.path_var).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(row, text=T('Browse…'), command=self.browse).pack(side='left')
        row2 = ttk.Frame(frm, padding=(0, 6, 0, 0))
        row2.pack(fill='x')
        ttk.Label(row2, text=T('The other version is:')).pack(side='left')
        self.older = tk.BooleanVar(value=True)
        ttk.Radiobutton(row2, text=T('older'), value=True, variable=self.older).pack(side='left', padx=(8, 0))
        ttk.Radiobutton(row2, text=T('newer'), value=False, variable=self.older).pack(side='left', padx=(8, 0))
        self.btn_run = ttk.Button(row2, text=T('Compare'), style='Accent.TButton', command=self.run)
        self.btn_run.pack(side='right')
        self.text = self.text_widget(frm)
        self.text.pack(fill='both', expand=True, pady=(8, 0))
        bar = ttk.Frame(frm, padding=(0, 8, 0, 0))
        bar.pack(fill='x')
        ttk.Button(bar, text=T('Close'), command=self.close).pack(side='right')
        self.text.insert('end', T('Pick the other version of the game and click Compare. Both are only read, never changed.'), 'muted')
        self.place_center()
        self.result = None

    def browse(self):
        f = filedialog.askopenfilename(parent=self.top, title=T('Pick the other version'),
                                       initialdir=os.path.dirname(self.app.result.html_path),
                                       filetypes=[(T('HTML games'), '*.html *.htm'), (T('All files'), '*.*')])
        if f:
            self.path_var.set(f)

    def run(self):
        path = self.path_var.get().strip().strip('"')
        if not os.path.isfile(path) and not os.path.isdir(path):
            messagebox.showerror(APP_NAME, T('Pick the other version first.'), parent=self.top)
            return
        self.btn_run.configure(state='disabled', text=T('Comparing…'))
        self.text.delete('1.0', 'end')
        self.text.insert('end', T('Scanning the other version…'), 'muted')
        cur = self.app.result

        def work():
            # results go through the app's queue: only the main thread may touch the window
            try:
                other = scanner.scan_any(path, None)
                self.app.q.put(('call', lambda: self.show(cur, other)))
            except Exception as e:
                msg = str(e)
                self.app.q.put(('call', lambda: self.fail(msg)))
        threading.Thread(target=work, daemon=True).start()

    def fail(self, msg):
        if self.exists():
            self.btn_run.configure(state='normal', text=T('Compare'))
            self.text.delete('1.0', 'end')
            self.text.insert('end', msg, 'c_error')

    def show(self, cur, other):
        if not self.exists():
            return
        self.btn_run.configure(state='normal', text=T('Compare'))
        old, new = (other, cur) if self.older.get() else (cur, other)
        fixed, added, still = compare(old, new)
        self.result = (fixed, added, still)
        t = self.text
        t.delete('1.0', 'end')
        t.insert('end', f'{os.path.basename(old.html_path)}  →  {os.path.basename(new.html_path)}\n', 'title')
        t.insert('end', T('Code health: {a}% → {b}%', a=old.stats['health'], b=new.stats['health']) + '\n\n', 'bold')
        for title, items, tag in ((T('Fixed in the newer version ({n})', n=len(fixed)), fixed, 'c_clean'),
                                  (T('New problems in the newer version ({n})', n=len(added)), added, 'c_error'),
                                  (T('Still there ({n})', n=len(still)), still, 'muted')):
            t.insert('end', title + '\n', tag)
            for i in sorted(items, key=lambda x: scanner.SEVERITY_ORDER[x.severity])[:300]:
                name = simple.simplify(i)['title'] if self.app.simple_on else i.title
                t.insert('end', f'  {self.app.sev_icon(i)} ', i.severity)
                t.insert('end', f'{name}' + (f'  ·  {i.passage}' if i.passage else '') + '\n')
            if len(items) > 300:
                t.insert('end', '  …\n', 'muted')
            t.insert('end', '\n')


def compare(old, new):
    """(fixed in new, new in new, still there) using loose keys with counts."""
    def counts(res):
        c = {}
        for i in res.issues:
            c.setdefault(fixes.loose_key(i), []).append(i)
        return c
    a, b = counts(old), counts(new)
    fixed, added, still = [], [], []
    for k, items in a.items():
        n_new = len(b.get(k, []))
        fixed += items[n_new:]
        still += items[:n_new]
    for k, items in b.items():
        added += items[len(a.get(k, [])):]
    return fixed, added, still


# ---------------------------------------------------------------------- lessons and glossary
class LessonsWindow(Window):
    def __init__(self, app):
        super().__init__(app, T('Lessons'), (900, 620))
        self.lessons = content.lessons()
        pane = ttk.PanedWindow(self.top, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=10, pady=10)
        left = ttk.Frame(pane)
        self.listbox = tk.Listbox(left, activestyle='none', relief='flat', highlightthickness=1, font=app.fonts['base'],
                                  exportselection=False)
        self.listbox.pack(fill='both', expand=True)
        for title, _ in self.lessons:
            self.listbox.insert('end', title)
        self.listbox.bind('<<ListboxSelect>>', lambda e: self.show())
        pane.add(left, weight=1)
        right = ttk.Frame(pane)
        self.text = self.text_widget(right)
        self.text.pack(fill='both', expand=True)
        pane.add(right, weight=3)
        self.style_list()
        self.listbox.selection_set(0)
        self.show()
        self.place_center()

    def style_list(self):
        c = self.app.colors
        self.listbox.configure(bg=c['panel'], fg=c['fg'], selectbackground=c['select'], selectforeground=c['select_fg'],
                               highlightbackground=c['border'])

    def restyle(self):
        super().restyle()
        self.style_list()

    def show(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        title, parts = self.lessons[sel[0]]
        t = self.text
        t.delete('1.0', 'end')
        t.insert('end', title + '\n\n', 'title')
        for kind, s in parts:
            if kind == 'p':
                t.insert('end', s + '\n\n')
            elif kind == 'code':
                t.insert('end', s + '\n', 'code')
                t.insert('end', '\n')
            elif kind == 'bad':
                t.insert('end', '✘ ' + T('Wrong:') + '\n', 'c_error')
                t.insert('end', s + '\n', 'before')
                t.insert('end', '\n')
            elif kind == 'good':
                t.insert('end', '✔ ' + T('Right:') + '\n', 'c_clean')
                t.insert('end', s + '\n', 'after')
                t.insert('end', '\n')


class GlossaryWindow(Window):
    def __init__(self, app):
        super().__init__(app, T('Word list'), (700, 600))
        frm = ttk.Frame(self.top, padding=10)
        frm.pack(fill='both', expand=True)
        self.text = self.text_widget(frm)
        self.text.pack(fill='both', expand=True)
        self.text.insert('end', T('Words used in this app') + '\n\n', 'title')
        for word, expl in content.glossary():
            self.text.insert('end', word + '\n', 'bold')
            self.text.insert('end', expl + '\n\n')
        self.place_center()


# ---------------------------------------------------------------------- tooltip and tour
class Tooltip:
    def __init__(self, app):
        self.app = app
        self.tip = None

    def show(self, x, y, text):
        self.hide()
        c = self.app.colors
        self.tip = tk.Toplevel(self.app.root)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes('-topmost', True)
        lbl = tk.Label(self.tip, text=text, justify='left', wraplength=int(360 * self.app.scale), bg=c['panel'], fg=c['fg'],
                       relief='solid', borderwidth=1, padx=8, pady=6, font=self.app.fonts['base'])
        lbl.pack()
        self.tip.geometry(f'+{x + 14}+{y + 18}')

    def hide(self):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


class Tour:
    """Speech-bubble tour that points at the main controls one by one."""

    def __init__(self, app):
        self.app = app
        self.steps = [(w, txt) for w, txt in content.tour_steps() if getattr(app, w, None) is not None]
        self.n = 0
        self.bubble = None
        self.show()

    def show(self):
        self.hide()
        if self.n >= len(self.steps):
            self.app.settings['tour_done'] = True
            self.app.save_settings()
            self.app.tour = None
            return
        app = self.app
        c = app.colors
        attr, text = self.steps[self.n]
        widget = getattr(app, attr)
        try:
            widget.update_idletasks()
            x = widget.winfo_rootx()
            top, height = widget.winfo_rooty(), widget.winfo_height()
            y = top + height + 6
        except tk.TclError:
            self.n += 1
            return self.show()
        b = self.bubble = tk.Toplevel(app.root)
        b.wm_overrideredirect(True)
        b.attributes('-topmost', True)
        b.configure(bg=c['accent'])
        inner = tk.Frame(b, bg=c['panel'], padx=12, pady=10)
        inner.pack(padx=2, pady=2)
        tk.Label(inner, text=T('Step {n} of {total}', n=self.n + 1, total=len(self.steps)), bg=c['panel'], fg=c['muted'],
                 font=app.f_small).pack(anchor='w')
        tk.Label(inner, text=text, bg=c['panel'], fg=c['fg'], justify='left', wraplength=int(380 * app.scale),
                 font=app.fonts['base']).pack(anchor='w', pady=(4, 8))
        row = tk.Frame(inner, bg=c['panel'])
        row.pack(fill='x')
        ttk.Button(row, text=T('Next') if self.n < len(self.steps) - 1 else T('Done'), style='Accent.TButton',
                   command=self.next).pack(side='right')
        ttk.Button(row, text=T('Skip tour'), command=self.end).pack(side='right', padx=(0, 6))
        b.update_idletasks()
        sw = app.root.winfo_screenwidth()
        x = min(x, sw - b.winfo_width() - 10)
        # Keep the bubble on screen (and off the taskbar): below the control if it fits,
        # otherwise inside a big control (lists, text boxes), otherwise above it.
        bh = b.winfo_height()
        bottom = min(app.root.winfo_screenheight() - 60, app.root.winfo_rooty() + app.root.winfo_height())
        if y + bh > bottom:
            y = top + 10 if height > bh + 20 else top - bh - 6
        y = max(0, min(y, bottom - bh))
        b.geometry(f'+{max(0, x)}+{y}')
        try:
            widget.focus_set()
        except tk.TclError:
            pass

    def next(self):
        self.n += 1
        self.show()

    def end(self):
        self.n = len(self.steps)
        self.show()

    def hide(self):
        if self.bubble is not None:
            try:
                self.bubble.destroy()
            except tk.TclError:
                pass
            self.bubble = None

