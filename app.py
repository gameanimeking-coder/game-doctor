"""Game Doctor - main window.

Opens a game (HTML/Twine file, or a Ren'Py / RPG Maker / Ink / ChoiceScript folder),
lists problems in plain words or technical detail, shows exactly how to fix each
one, and offers hand-holding tools (fix wizard, play-test, line tester, backup,
version compare, bug report writer, lessons, tour). It never changes the game.
"""
import csv
import ctypes
import datetime
import html
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import zipfile
from tkinter import filedialog, messagebox, ttk

import ai
import ai_dialogs as AID
import content
import fixes
import i18n
import reach
import scanner
import simple
import speech
import ui_dialogs as D
from i18n import T

APP_NAME = 'Game Doctor'
APP_VERSION = '2.0.4'
SETTINGS_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'GameDoctor')
OLD_SETTINGS_DIR = os.path.join(os.path.dirname(SETTINGS_DIR), 'GameBugScanner')   # name before 2.0 shipped
SETTINGS_FILE = os.path.join(SETTINGS_DIR, 'settings.json')
CODE_LINE_LIMIT = 3000

THEMES = {
    'light': dict(bg='#f6f7f9', panel='#ffffff', fg='#1d2330', muted='#5d6675', border='#d5d9e0',
                  accent='#2f6fde', accent_fg='#ffffff', select='#dce8fd', select_fg='#10264d',
                  field='#ffffff', code_bg='#f1f3f6', code_hl='#fff2c4', group='#eef1f5',
                  error='#c62828', warning='#a86400', info='#2f6fde', clean='#2e8b46',
                  error_bg='#fde4e4', warning_bg='#fff3d1', info_bg='#e3edfd', clean_bg='#e6f4ea',
                  button='#eceff3', button_hover='#e1e6ed', link='#2f6fde'),
    'dark': dict(bg='#1b1e24', panel='#22262e', fg='#e3e6eb', muted='#9aa3b1', border='#363c47',
                 accent='#5b93f5', accent_fg='#0d1320', select='#2d4268', select_fg='#ffffff',
                 field='#2a2f38', code_bg='#171a1f', code_hl='#4a3f17', group='#2a2f38',
                 error='#ff6b6b', warning='#f0c04a', info='#7aa7ff', clean='#5fcf7f',
                 error_bg='#4a2226', warning_bg='#463a17', info_bg='#1f3354', clean_bg='#1e3a27',
                 button='#2e333d', button_hover='#3a404c', link='#8fb4ff'),
}
# Okabe-Ito colours, safe for the common kinds of colour blindness
COLORBLIND = {
    'light': dict(error='#c0392b', warning='#b06a00', info='#0072b2', clean='#007a5e',
                  error_bg='#fbe3d6', warning_bg='#fdf0d5', info_bg='#dcecf7', clean_bg='#d9f0ea'),
    'dark': dict(error='#ff8a50', warning='#f5c04a', info='#56b4e9', clean='#33c29b',
                 error_bg='#4a2a1a', warning_bg='#453a17', info_bg='#173a52', clean_bg='#143c33'),
}
SEV_KEY = {scanner.ERROR: 'error', scanner.WARNING: 'warning', scanner.INFO: 'info'}
SEV_ICON = {scanner.ERROR: '●', scanner.WARNING: '▲', scanner.INFO: '■'}
UNREACHABLE = '(unreachable)'   # filter choice: problems in scenes nobody can reach

KNOWN_EDITORS = [
    ('Notepad++', [r'%ProgramFiles%\Notepad++\notepad++.exe', r'%ProgramFiles(x86)%\Notepad++\notepad++.exe'], '-n{line} "{file}"'),
    ('Notepad3', [r'%ProgramFiles%\Notepad3\Notepad3.exe', r'%ProgramFiles(x86)%\Notepad3\Notepad3.exe'], '/g {line} "{file}"'),
    ('Sublime Text', [r'%ProgramFiles%\Sublime Text\sublime_text.exe', r'%ProgramFiles%\Sublime Text 3\sublime_text.exe'], '"{file}:{line}"'),
    ('Visual Studio Code', [r'%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe', r'%ProgramFiles%\Microsoft VS Code\Code.exe'], '-g "{file}:{line}"'),
    ('VSCodium', [r'%LOCALAPPDATA%\Programs\VSCodium\VSCodium.exe', r'%ProgramFiles%\VSCodium\VSCodium.exe'], '-g "{file}:{line}"'),
    ('EmEditor', [r'%ProgramFiles%\EmEditor\EmEditor.exe'], '/l {line} "{file}"'),
    ('UltraEdit', [r'%ProgramFiles%\IDM Computer Solutions\UltraEdit\uedit64.exe'], '"{file}/{line}"'),
    ('Notepad (Windows)', [r'%WINDIR%\System32\notepad.exe'], '"{file}"'),
]


def detect_editors():
    found = []
    for name, paths, args in KNOWN_EDITORS:
        for p in paths:
            full = os.path.expandvars(p)
            if '%' not in full and os.path.isfile(full):
                found.append(dict(name=name, path=full, args=args))
                break
    return found


def windows_prefers_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            return winreg.QueryValueEx(k, 'AppsUseLightTheme')[0] == 0
    except Exception:
        return False


def load_settings():
    old = os.path.join(OLD_SETTINGS_DIR, 'settings.json')
    if not os.path.exists(SETTINGS_FILE) and os.path.exists(old):
        try:   # keep the settings (and problems marked as intentional) from when the app had its old name
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            shutil.copy2(old, SETTINGS_FILE)
        except OSError:
            pass
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def resource_path(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def health_color_key(score):
    return 'clean' if score >= 80 else 'warning' if score >= 70 else 'error'


def norm(p):
    return os.path.normcase(os.path.abspath(p)) if p else ''


class App:
    def __init__(self, root, start_path=None, settings=None):
        self.root = root
        self.settings = settings if settings is not None else load_settings()
        i18n.available()
        i18n.set_language(self.settings.get('language', 'en'))
        self.scale = float(self.settings.get('scale', 1.0))
        self.theme_choice = self.settings.get('theme', 'system')
        self.simple_on = bool(self.settings.get('simple_mode', True))
        self.audience = self.settings.get('audience', 'dev')
        self.edit_mode = self.settings.get('edit_mode', 'file')
        self.colorblind = bool(self.settings.get('colorblind', False))
        self.watch_on = bool(self.settings.get('watch', False))
        self.show_ignored = False
        self.result = None
        self.target = ''          # what was opened: a file or a folder
        self.game_dir = ''        # chosen game folder, or '' for automatic
        self.q = queue.Queue()
        self.scanning = False
        self.playtesting = False
        self._pt_stop = False
        self.fullscanning = False
        self._fs_stop = False
        self.windows = []
        self.tour = None
        self.code_built_for = None
        self.line_issues = {}
        self.problem_lines = []
        self._mtime = None
        self._mtime_before_check = None
        self._search_job = None
        self._check_cb = None
        self._scan_reason = 'scan'
        self.icon_path = resource_path('icon.ico')
        self.known_editors = KNOWN_EDITORS
        self.errors = []          # Tk callback errors (shown, and used by the self-test)

        root.title(APP_NAME)
        root.geometry(self.fit_geometry(self.settings.get('geometry', '1240x800')))
        root.minsize(860, 540)
        if self.settings.get('maximized'):
            root.state('zoomed')
        try:
            root.iconbitmap(self.icon_path)
        except tk.TclError:
            pass
        root.report_callback_exception = self.on_tk_error
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        self.fonts = {k: tkfont.nametofont(n) for k, n in (('base', 'TkDefaultFont'), ('text', 'TkTextFont'),
                                                         ('heading', 'TkHeadingFont'), ('fixed', 'TkFixedFont'))}
        family = 'Segoe UI' if 'Segoe UI' in tkfont.families() else self.fonts['base'].actual('family')
        mono = next((f for f in ('Cascadia Mono', 'Consolas', 'Courier New') if f in tkfont.families()), 'TkFixedFont')
        for k in ('base', 'text', 'heading'):
            self.fonts[k].configure(family=family)
        self.fonts['fixed'].configure(family=mono)
        self.f_bold = tkfont.Font(family=family, weight='bold')
        self.f_small = tkfont.Font(family=family)
        self.f_big = tkfont.Font(family=family, weight='bold')
        self.f_huge = tkfont.Font(family=family, weight='bold')
        self.tooltip = D.Tooltip(self)

        self.build_ui()
        self.apply_scale()
        self.apply_theme()
        self.show_welcome()
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        root.after(100, self.poll)
        root.after(1500, self.watch_tick)
        if start_path and os.path.exists(start_path):
            self.open_path(start_path)
        if not self.settings.get('tour_done') and not self.settings.get('_selftest'):
            root.after(900, self.start_tour)

    # ================================================================ basics
    def fit_geometry(self, geo):
        left, top, right, bottom = 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 60
        try:
            class RECT(ctypes.Structure):
                _fields_ = [('l', ctypes.c_long), ('t', ctypes.c_long), ('r', ctypes.c_long), ('b', ctypes.c_long)]
            rc = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0):
                left, top, right, bottom = rc.l, rc.t, rc.r, rc.b
        except Exception:
            pass
        m = re.match(r'(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?$', geo or '')
        if not m:
            return '1240x800'
        frame = 90
        w = min(int(m.group(1)), right - left)
        h = min(int(m.group(2)), bottom - top - frame)
        if m.group(3) is None:
            return f'{w}x{h}'
        x = max(left, min(int(m.group(3)), right - w))
        y = max(top, min(int(m.group(4)), bottom - h - frame))
        return f'{w}x{h}+{x}+{y}'

    def save_settings(self):
        if self.settings.get('_selftest'):
            return
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=1)
        except OSError:
            pass

    def on_tk_error(self, exc, val, tb):
        import traceback
        text = ''.join(traceback.format_exception(exc, val, tb))
        self.errors.append(text)
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(os.path.join(SETTINGS_DIR, 'error.log'), 'a', encoding='utf-8') as f:
                f.write(f'--- {datetime.datetime.now()}\n{text}\n')
        except OSError:
            pass
        if not self.settings.get('_selftest'):
            messagebox.showerror(APP_NAME, T('Something went wrong inside the app:') + f'\n\n{val}\n\n'
                                 + T('Details were saved to {path}', path=os.path.join(SETTINGS_DIR, 'error.log')))

    def set_status(self, text):
        try:
            self.status.configure(text=text)
        except tk.TclError:
            pass

    def copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(T('Copied to the clipboard'))

    def sev_icon(self, i):
        return SEV_ICON[i.severity]

    def sev_word(self, sev, plural=False):
        if self.simple_on:
            return simple.severity(sev)
        return {scanner.ERROR: T('Errors') if plural else T('Error'), scanner.WARNING: T('Warnings') if plural else T('Warning'),
                scanner.INFO: T('Notes') if plural else T('Note')}[sev]

    def cat_word(self, c):
        return simple.category(c) if self.simple_on else c

    def title_of(self, i):
        return simple.simplify(i)['title'] if self.simple_on else i.title

    @property
    def is_dev(self):
        return self.audience == 'dev'

    @property
    def is_twine(self):
        return bool(self.result) and self.result.engine == 'twine'

    # ================================================================ layout
    def build_ui(self):
        r = self.root
        for w in r.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        top = ttk.Frame(r, style='Top.TFrame', padding=(10, 8))
        top.pack(fill='x')
        top.columnconfigure(0, weight=1)
        left = ttk.Frame(top, style='Top.TFrame')
        left.grid(row=0, column=0, sticky='w')
        self.btn_open = ttk.Button(left, text=T('Open game…'), style='Accent.TButton', command=self.choose_file)
        self.btn_open.pack(side='left')
        self.btn_scan = ttk.Button(left, text=T('Quick scan'), command=self.start_scan, state='disabled')
        self.btn_scan.pack(side='left', padx=(6, 0))
        self.btn_full = ttk.Button(left, text=T('Full scan…'), command=self.toggle_fullscan, state='disabled')
        self.btn_full.pack(side='left', padx=(6, 0))
        self.btn_playtest = ttk.Button(left, text=T('▶ Play-test'), command=self.toggle_playtest, state='disabled')
        self.btn_playtest.pack(side='left', padx=(6, 0))
        self.btn_wizard = ttk.Button(left, text=T('Fix wizard'), command=self.open_wizard, state='disabled')
        self.btn_wizard.pack(side='left', padx=(6, 0))
        self.btn_tools = ttk.Menubutton(left, text=T('Tools'))
        self.btn_tools.pack(side='left', padx=(6, 0))
        self.tools_menu = tk.Menu(self.btn_tools, tearoff=False)
        self.btn_tools['menu'] = self.tools_menu
        self.btn_help = ttk.Menubutton(left, text=T('Help'))
        self.btn_help.pack(side='left', padx=(6, 0))
        self.help_menu = tk.Menu(self.btn_help, tearoff=False)
        self.btn_help['menu'] = self.help_menu
        self.fill_menus()

        # Theme, size and language move to a second line when they don't fit (longer languages).
        right = ttk.Frame(top, style='Top.TFrame')
        right.grid(row=0, column=1, sticky='e')
        self._toolbar = (top, left, right)
        self._toolbar_wrapped = False
        for w in (top, left, right):
            w.bind('<Configure>', lambda e: self.fit_toolbar())
        self.btn_audience = ttk.Button(right, command=self.toggle_audience)
        self.btn_audience.pack(side='left', padx=(0, 6))
        self.btn_simple = ttk.Button(right, command=self.toggle_simple)
        self.btn_simple.pack(side='left', padx=(0, 6))
        # Wide enough for the longest theme name, so the button doesn't jump when clicked.
        theme_w = max(len(T(n)) for n in ('System theme', 'Light theme', 'Dark theme')) + 3
        self.btn_theme = ttk.Button(right, width=theme_w, command=self.cycle_theme)
        self.btn_theme.pack(side='left')
        ttk.Label(right, text=T('Size'), style='Top.TLabel').pack(side='left', padx=(10, 4))
        ttk.Button(right, text='−', width=3, command=lambda: self.zoom(-0.1)).pack(side='left')
        self.zoom_label = ttk.Label(right, text='100%', width=5, anchor='center', style='Top.TLabel')
        self.zoom_label.pack(side='left')
        ttk.Button(right, text='+', width=3, command=lambda: self.zoom(0.1)).pack(side='left')
        self.btn_lang = ttk.Menubutton(right, text='\U0001F310 ' + i18n.LANGUAGES.get(i18n.current(), 'English'))
        self.btn_lang.pack(side='left', padx=(10, 0))
        self.lang_menu = tk.Menu(self.btn_lang, tearoff=False)
        self.btn_lang['menu'] = self.lang_menu
        self.lang_var = tk.StringVar(value=i18n.current())
        for code, name in sorted(i18n.LANGUAGES.items(), key=lambda x: x[1].lower()):
            self.lang_menu.add_radiobutton(label=name, value=code, variable=self.lang_var, command=lambda c=code: self.set_language(c))

        pathrow = ttk.Frame(r, style='Top.TFrame')
        pathrow.pack(fill='x')
        # Packed after the health score, so a long path is cut instead of the score and edit-mode switch.
        self.health_label = ttk.Label(pathrow, text='', style='Health.TLabel', padding=(12, 0, 14, 6))
        self.health_label.pack(side='right')
        self.path_label = ttk.Label(pathrow, text=T('No game opened'), style='Path.TLabel', padding=(12, 2, 12, 6))
        self.path_label.pack(side='left', fill='x', expand=True)
        self.btn_editmode = ttk.Menubutton(pathrow, style='Path.TMenubutton')
        self.editmode_menu = tk.Menu(self.btn_editmode, tearoff=False)
        self.btn_editmode['menu'] = self.editmode_menu
        self.editmode_var = tk.StringVar(value=self.edit_mode)
        self.editmode_menu.add_radiobutton(label=T('I edit in Twine'), value='twine', variable=self.editmode_var,
                                           command=lambda: self.set_edit_mode('twine'))
        self.editmode_menu.add_radiobutton(label=T('I edit the .html file (Notepad++ etc.)'), value='file',
                                           variable=self.editmode_var, command=lambda: self.set_edit_mode('file'))

        sb = ttk.Frame(r, style='Status.TFrame', padding=(10, 4))
        sb.pack(fill='x', side='bottom')
        self.status = ttk.Label(sb, text=T('Ready'), style='Status.TLabel')
        self.status.pack(side='left')
        self.progress = ttk.Progressbar(sb, mode='determinate', length=220, maximum=100)
        self.progress.pack(side='right')
        self.watch_label = ttk.Label(sb, text='', style='Status.TLabel')
        self.watch_label.pack(side='right', padx=(0, 12))

        self.nb = ttk.Notebook(r)
        self.nb.pack(fill='both', expand=True, padx=10, pady=(8, 0))
        self.nb.bind('<<NotebookTabChanged>>', lambda e: self.on_tab())
        self.build_problems_tab()
        self.build_code_tab()

        r.bind('<Control-o>', lambda e: self.choose_file())
        r.bind('<F5>', lambda e: self.start_scan())
        r.bind('<Control-e>', lambda e: self.export())
        r.bind('<Control-f>', lambda e: self.focus_search())
        r.bind('<F3>', lambda e: self.code_find_next())
        r.bind('<F1>', lambda e: self.open_lessons())
        for seq, d in (('<Control-plus>', 0.1), ('<Control-equal>', 0.1), ('<Control-minus>', -0.1),
                       ('<Control-KP_Add>', 0.1), ('<Control-KP_Subtract>', -0.1)):
            r.bind(seq, lambda e, d=d: self.zoom(d))
        r.bind('<Control-0>', lambda e: self.zoom(None))
        r.bind('<Control-MouseWheel>', lambda e: self.zoom(0.1 if e.delta > 0 else -0.1))
        self.update_mode_widgets()

    def fill_menus(self):
        m = self.tools_menu
        m.delete(0, 'end')
        m.add_command(label=T('Open a game folder (Ren\'Py, RPG Maker, Ink, ChoiceScript)…'), command=self.choose_folder_game)
        m.add_command(label=T('Pictures and sounds folder…'), command=self.choose_folder)
        m.add_separator()
        m.add_command(label=T('Back up the game file'), command=self.backup_game)
        m.add_command(label=T('Test a line before you change it…'), command=self.open_test_line)
        m.add_command(label=T('Compare with another version…'), command=self.open_compare)
        m.add_command(label=T('Write a bug report for the developer…'), command=self.open_bug_report)
        m.add_command(label=T('Export report…'), command=self.export)
        m.add_separator()
        m.add_command(label=T(AID.MENU_FIX), command=self.open_ai_fix)
        m.add_command(label=T(AID.MENU_LOG), command=self.export_ai_log)
        m.add_command(label=T(AID.MENU_SETTINGS), command=lambda: AID.AISettingsDialog(self))
        m.add_separator()
        self.watch_var = tk.BooleanVar(value=self.watch_on)
        m.add_checkbutton(label=T('Watch the game file and re-check when it is saved'), variable=self.watch_var, command=self.toggle_watch)
        self.ignored_var = tk.BooleanVar(value=self.show_ignored)
        m.add_checkbutton(label=T('Show problems marked as intentional'), variable=self.ignored_var, command=self.toggle_show_ignored)
        m.add_command(label=T('Text editor…'), command=self.choose_editor)
        h = self.help_menu
        h.delete(0, 'end')
        h.add_command(label=T('Take the tour'), command=self.start_tour)
        h.add_command(label=T('Lessons (F1)'), command=self.open_lessons)
        h.add_command(label=T('Word list'), command=self.open_glossary)
        h.add_separator()
        self.xl_var = tk.BooleanVar(value=self.scale >= 1.6)
        h.add_checkbutton(label=T('Extra-large text'), variable=self.xl_var, command=self.toggle_xl)
        self.cb_var = tk.BooleanVar(value=self.colorblind)
        h.add_checkbutton(label=T('Colour-blind friendly colours'), variable=self.cb_var, command=self.toggle_colorblind)
        h.add_separator()
        h.add_command(label=T('About'), command=self.about)

    def build_problems_tab(self):
        tab = ttk.Frame(self.nb, padding=(0, 6, 0, 0))
        self.nb.add(tab, text='  ' + T('Problems') + '  ')
        self.tab_problems = tab
        flt = ttk.Frame(tab, padding=(0, 0, 0, 6))
        flt.pack(fill='x')
        # Four groups (severities, kind, group by, search) that wrap onto a new line when narrow.
        g_sev, g_kind, g_group, g_search = (ttk.Frame(flt) for _ in range(4))
        self.sev_vars, self.sev_checks = {}, {}
        for sev in (scanner.ERROR, scanner.WARNING, scanner.INFO):
            v = tk.BooleanVar(value=self.settings.get('show_' + sev, True))
            c = ttk.Checkbutton(g_sev, text=self.sev_word(sev, True), variable=v, command=self.refresh, style=sev + '.TCheckbutton')
            c.pack(side='left', padx=(0, 12))
            self.sev_vars[sev], self.sev_checks[sev] = v, c
        self.lbl_type = ttk.Label(g_kind, text=T('Kind'))
        self.lbl_type.pack(side='left', padx=(0, 4))
        self.cat_var = tk.StringVar(value=T('All kinds'))
        self.cat_box = ttk.Combobox(g_kind, textvariable=self.cat_var, values=[T('All kinds')], width=28, state='readonly')
        self.cat_box.pack(side='left', padx=(0, 6))
        self.cat_box.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        self.cat_map = {}
        self._cat_real = None
        ttk.Label(g_group, text=T('Group by')).pack(side='left', padx=(0, 4))
        self.group_names = {'Type': T('Kind'), 'Severity': T('How bad'), 'Passage': T('Scene'), 'Nothing': T('Nothing')}
        self.group_var = tk.StringVar(value=self.group_names.get(self.settings.get('group_by', 'Type'), T('Kind')))
        gb = ttk.Combobox(g_group, textvariable=self.group_var, values=list(self.group_names.values()), width=10, state='readonly')
        gb.pack(side='left', padx=(0, 6))
        gb.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *a: self.schedule_search())
        ttk.Label(g_search, text=T('Search')).pack(side='left', padx=(0, 4))
        self.search_entry = ttk.Entry(g_search, textvariable=self.search_var, width=26)
        self.search_entry.pack(side='left')
        self.flow(flt, (g_sev, g_kind, g_group, g_search))

        self.pane = ttk.PanedWindow(tab, orient='vertical')
        self.pane.pack(fill='both', expand=True)
        tree_frame = ttk.Frame(self.pane)
        self.tree = ttk.Treeview(tree_frame, columns=('sev', 'title', 'passage', 'line', 'effort'), show='tree headings',
                                 selectmode='browse')
        self.tree.heading('#0', text='')
        self.tree.heading('sev', text=T('How bad'), command=lambda: self.sort_by('sev'))
        self.tree.heading('title', text=T('Problem'), command=lambda: self.sort_by('title'))
        self.tree.heading('passage', text=T('Scene / place'), command=lambda: self.sort_by('passage'))
        self.tree.heading('line', text=T('Line'), command=lambda: self.sort_by('line'))
        self.tree.heading('effort', text=T('Fix'), command=lambda: self.sort_by('effort'))
        vs = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        vs.pack(side='right', fill='y')   # first, so a wide list can't push the scrollbar out of sight
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_selected())
        self.tree.bind('<Control-c>', lambda e: self.copy_details())
        self.pane.add(tree_frame, weight=3)

        det = ttk.Frame(self.pane)
        self.bar1 = ttk.Frame(det, padding=(0, 6, 0, 2))
        self.bar1.pack(fill='x')
        self.btn_editor = ttk.Button(self.bar1, text=T('Open in my text editor'), command=self.open_in_editor)
        self.btn_copyfix = ttk.Button(self.bar1, text=T('Copy fixed line'), command=lambda: self.copy_fixed(self.selected_issue()))
        self.btn_search = ttk.Button(self.bar1, text=T('Copy text to search for'), command=lambda: self.copy_search(self.selected_issue()))
        self.btn_check = ttk.Button(self.bar1, text=T('I fixed it - check'), style='Accent.TButton', command=self.check_selected)
        self.btn_report1 = ttk.Button(self.bar1, text=T('Tell the developer'), command=lambda: self.open_bug_report(self.selected_issue()))
        self.bar2 = ttk.Frame(det, padding=(0, 2, 0, 4))
        self.bar2.pack(fill='x')
        self.btn_showcode = ttk.Button(self.bar2, text=T('Show in Code view'), command=self.show_in_code)
        self.btn_reach = ttk.Button(self.bar2, text=T('How do players reach this scene?'), command=self.show_reach)
        self.btn_ignore = ttk.Button(self.bar2, text=T('Mark as intentional'), command=lambda: self.toggle_ignore(self.selected_issue()))
        self.btn_read = ttk.Button(self.bar2, text=T('Read aloud'), command=self.read_aloud)
        self.btn_copy = ttk.Button(self.bar2, text=T('Copy details'), command=self.copy_details)
        txt_frame = ttk.Frame(det)
        txt_frame.pack(fill='both', expand=True)
        # A small requested height: the text still fills the space, but leaves room for the list above.
        self.text = tk.Text(txt_frame, wrap='word', relief='flat', borderwidth=0, padx=14, pady=10, cursor='arrow',
                            highlightthickness=1, font=self.fonts['text'], height=12)
        tvs = ttk.Scrollbar(txt_frame, orient='vertical', command=self.text.yview)
        self.text.configure(yscrollcommand=tvs.set)
        tvs.pack(side='right', fill='y')
        self.text.pack(side='left', fill='both', expand=True)
        self.text.bind('<Key>', lambda e: None if (e.state & 4 and e.keysym.lower() in ('c', 'a')) else 'break')
        self.pane.add(det, weight=2)

    def build_code_tab(self):
        tab = ttk.Frame(self.nb, padding=(0, 6, 0, 0))
        self.nb.add(tab, text='  ' + T('Code view') + '  ')
        self.tab_code = tab
        bar = ttk.Frame(tab, padding=(0, 0, 0, 6))
        bar.pack(fill='x')
        self.legend = {}
        for key in ('clean', 'info', 'warning', 'error'):
            lbl = ttk.Label(bar, text='', style=f'Legend{key}.TLabel')
            lbl.pack(side='left', padx=(0, 12))
            self.legend[key] = lbl
        ttk.Button(bar, text=T('◀ Previous problem'), command=lambda: self.code_jump(-1)).pack(side='left', padx=(8, 0))
        ttk.Button(bar, text=T('Next problem ▶'), command=lambda: self.code_jump(1)).pack(side='left', padx=(6, 0))
        self.goto_var = tk.StringVar()
        ttk.Button(bar, text=T('Go'), width=4, command=self.code_goto).pack(side='right')
        ge = ttk.Entry(bar, textvariable=self.goto_var, width=8)
        ge.pack(side='right', padx=(0, 4))
        ge.bind('<Return>', lambda e: self.code_goto())
        ttk.Label(bar, text=T('Line')).pack(side='right', padx=(12, 4))
        self.find_var = tk.StringVar()
        ttk.Button(bar, text=T('Find next'), command=self.code_find_next).pack(side='right')
        self.find_entry = ttk.Entry(bar, textvariable=self.find_var, width=22)
        self.find_entry.pack(side='right', padx=(0, 4))
        self.find_entry.bind('<Return>', lambda e: self.code_find_next())
        ttk.Label(bar, text=T('Find')).pack(side='right', padx=(0, 4))
        bar2 = ttk.Frame(tab, padding=(0, 0, 0, 6))
        bar2.pack(fill='x')
        ttk.Label(bar2, text=T('File')).pack(side='left', padx=(0, 4))
        self.codefile_var = tk.StringVar()
        self.codefile_box = ttk.Combobox(bar2, textvariable=self.codefile_var, state='readonly', width=60)
        self.codefile_box.pack(side='left')
        self.codefile_box.bind('<<ComboboxSelected>>', lambda e: self.build_code_view(force=True))
        self.codefile_map = {}
        self.raw_var = tk.BooleanVar(value=False)
        self.raw_check = ttk.Checkbutton(bar2, text=T('Show exactly as stored in the file (&lt; codes)'), variable=self.raw_var,
                                         command=lambda: self.build_code_view(force=True))
        self.raw_check.pack(side='left', padx=(12, 0))
        body = ttk.Frame(tab)
        body.pack(fill='both', expand=True)
        self.code = tk.Text(body, wrap='none', relief='flat', borderwidth=0, padx=6, pady=4, undo=False,
                            highlightthickness=1, font=self.fonts['fixed'], cursor='xterm')
        cvs = ttk.Scrollbar(body, orient='vertical', command=self.code.yview)
        chs = ttk.Scrollbar(tab, orient='horizontal', command=self.code.xview)
        self.overview = tk.Canvas(body, width=16, highlightthickness=0, borderwidth=0, cursor='hand2')
        self.code.configure(yscrollcommand=lambda a, b: (cvs.set(a, b), self.draw_overview_view(a, b)), xscrollcommand=chs.set)
        self.overview.pack(side='right', fill='y')
        cvs.pack(side='right', fill='y')
        self.code.pack(side='left', fill='both', expand=True)
        chs.pack(fill='x')
        self.code.bind('<Key>', lambda e: None if (e.state & 4 and e.keysym.lower() in ('c', 'a', 'f')) or
                       e.keysym in ('Up', 'Down', 'Left', 'Right', 'Prior', 'Next', 'Home', 'End') else 'break')
        self.code.bind('<ButtonRelease-1>', lambda e: self.code_clicked())
        self.code.bind('<Double-Button-1>', lambda e: self.code_open_problem())
        self.overview.bind('<Button-1>', self.overview_click)
        self.overview.bind('<B1-Motion>', self.overview_click)
        self.overview.bind('<Configure>', lambda e: self.draw_overview())
        self.code_placeholder()

    def update_mode_widgets(self):
        """Show/hide controls for Developer vs Player mode and for the kind of game."""
        dev = self.is_dev
        self.btn_audience.configure(text=('\U0001F6E0 ' + T('Developer')) if dev else ('\U0001F3AE ' + T('Player')))
        self.btn_simple.configure(text=('✔ ' + T('Simple words')) if self.simple_on else T('Simple words: off'),
                                  style='On.TButton' if self.simple_on else 'TButton')
        self.btn_wizard.pack_forget()
        if dev:
            self.btn_wizard.pack(side='left', padx=(6, 0), after=self.btn_playtest)
        try:
            if dev:
                self.nb.add(self.tab_code)
            else:
                self.nb.hide(self.tab_code)
        except tk.TclError:
            pass
        self.tree.configure(displaycolumns=('sev', 'title', 'passage', 'line', 'effort') if dev else ('sev', 'title', 'passage'))
        for b in (self.btn_editor, self.btn_copyfix, self.btn_search, self.btn_check, self.btn_report1,
                  self.btn_showcode, self.btn_reach, self.btn_ignore, self.btn_read, self.btn_copy):
            b.pack_forget()
        if dev:
            self.flow(self.bar1, (self.btn_editor, self.btn_copyfix, self.btn_search, self.btn_check))
            self.flow(self.bar2, (self.btn_showcode, self.btn_reach, self.btn_ignore, self.btn_read, self.btn_copy))
        else:
            self.flow(self.bar1, (self.btn_report1,))
            self.flow(self.bar2, (self.btn_reach, self.btn_read, self.btn_copy))
        self.btn_editmode.pack_forget()
        if dev and self.is_twine:
            self.btn_editmode.pack(side='right', padx=(0, 12), before=self.health_label)
        self.btn_editmode.configure(text=(T('I edit in Twine') if self.edit_mode == 'twine' else T('I edit the .html file')) + ' ▾')
        self.editmode_var.set(self.edit_mode)
        self.tree.heading('sev', text=T('How bad') if self.simple_on else T('Severity'))
        self.tree.heading('passage', text=T('Scene / place') if self.simple_on else T('Passage / place'))
        self.lbl_type.configure(text=T('Kind') if self.simple_on else T('Type'))
        self.watch_label.configure(text=('\U0001F441 ' + T('Watching for changes')) if self.watch_on else '')
        self.set_detail_buttons(self.selected_issue() if self.result else None)

    # ================================================================ theme and size
    def current_theme(self):
        if self.theme_choice == 'system':
            return 'dark' if windows_prefers_dark() else 'light'
        return self.theme_choice

    def cycle_theme(self):
        order = ['system', 'light', 'dark']
        self.theme_choice = order[(order.index(self.theme_choice) + 1) % 3]
        self.apply_theme()

    def apply_theme(self):
        name = self.current_theme()
        c = dict(THEMES[name])
        if self.colorblind:
            c.update(COLORBLIND[name])
        self.colors = c
        self.btn_theme.configure(text={'system': '◑ ' + T('System theme'), 'light': '☀ ' + T('Light theme'),
                                       'dark': '☾ ' + T('Dark theme')}[self.theme_choice])
        s, r = self.style, self.root
        r.configure(bg=c['bg'])
        s.configure('.', background=c['bg'], foreground=c['fg'], fieldbackground=c['field'], bordercolor=c['border'],
                    lightcolor=c['bg'], darkcolor=c['bg'], troughcolor=c['bg'], focuscolor=c['accent'],
                    selectbackground=c['select'], selectforeground=c['select_fg'], insertcolor=c['fg'], arrowcolor=c['fg'])
        s.configure('TFrame', background=c['bg'])
        s.configure('Top.TFrame', background=c['panel'])
        s.configure('Top.TLabel', background=c['panel'], foreground=c['fg'])
        s.configure('Path.TLabel', background=c['panel'], foreground=c['muted'])
        s.configure('Health.TLabel', background=c['panel'], foreground=c['muted'], font=self.f_bold)
        s.configure('Status.TFrame', background=c['panel'])
        s.configure('Status.TLabel', background=c['panel'], foreground=c['muted'])
        s.configure('TLabel', background=c['bg'], foreground=c['fg'])
        for key in ('clean', 'info', 'warning', 'error'):
            s.configure(f'Legend{key}.TLabel', background=c['bg'], foreground=c[key], font=self.f_bold)
        s.configure('TButton', background=c['button'], foreground=c['fg'], bordercolor=c['border'],
                    lightcolor=c['button'], darkcolor=c['button'])
        s.map('TButton', background=[('disabled', c['bg']), ('active', c['button_hover'])], foreground=[('disabled', c['muted'])])
        s.configure('TMenubutton', background=c['button'], foreground=c['fg'], bordercolor=c['border'],
                    lightcolor=c['button'], darkcolor=c['button'], arrowcolor=c['fg'])
        s.map('TMenubutton', background=[('active', c['button_hover'])])
        s.configure('Path.TMenubutton', background=c['panel'], foreground=c['link'], bordercolor=c['panel'],
                    lightcolor=c['panel'], darkcolor=c['panel'], arrowcolor=c['panel'], padding=(4, 0))
        s.map('Path.TMenubutton', background=[('active', c['button_hover'])])
        s.configure('Accent.TButton', background=c['accent'], foreground=c['accent_fg'], lightcolor=c['accent'],
                    darkcolor=c['accent'], bordercolor=c['accent'])
        s.map('Accent.TButton', background=[('active', c['accent']), ('disabled', c['bg'])],
              foreground=[('active', c['accent_fg']), ('disabled', c['muted'])])
        s.configure('On.TButton', background=c['select'], foreground=c['select_fg'], bordercolor=c['accent'],
                    lightcolor=c['select'], darkcolor=c['select'])
        s.map('On.TButton', background=[('active', c['select'])])
        s.configure('TCheckbutton', background=c['bg'], foreground=c['fg'], indicatorbackground=c['field'], indicatorforeground=c['fg'])
        s.map('TCheckbutton', background=[('active', c['bg'])], indicatorbackground=[('selected', c['accent'])],
              indicatorforeground=[('selected', c['accent_fg'])])
        for sev, key in SEV_KEY.items():
            s.configure(sev + '.TCheckbutton', foreground=c[key])
        s.configure('TRadiobutton', background=c['bg'], foreground=c['fg'], indicatorbackground=c['field'])
        s.map('TRadiobutton', background=[('active', c['bg'])], indicatorbackground=[('selected', c['accent'])])
        s.configure('TEntry', fieldbackground=c['field'], foreground=c['fg'], insertcolor=c['fg'])
        s.configure('TCombobox', fieldbackground=c['field'], foreground=c['fg'], background=c['button'], arrowcolor=c['fg'],
                    selectbackground=c['field'], selectforeground=c['fg'])
        s.map('TCombobox', fieldbackground=[('readonly', c['field'])], foreground=[('readonly', c['fg'])],
              selectbackground=[('readonly', c['field'])], selectforeground=[('readonly', c['fg'])])
        r.option_add('*TCombobox*Listbox.background', c['field'])
        r.option_add('*TCombobox*Listbox.foreground', c['fg'])
        r.option_add('*TCombobox*Listbox.selectBackground', c['select'])
        r.option_add('*TCombobox*Listbox.selectForeground', c['select_fg'])
        for menu in (self.tools_menu, self.help_menu, self.lang_menu, self.editmode_menu):
            menu.configure(bg=c['panel'], fg=c['fg'], activebackground=c['select'], activeforeground=c['select_fg'],
                           selectcolor=c['accent'], font=self.fonts['base'])
        s.configure('TNotebook', background=c['bg'], bordercolor=c['border'], tabmargins=(0, 0, 0, 0))
        s.configure('TNotebook.Tab', background=c['button'], foreground=c['muted'], bordercolor=c['border'],
                    lightcolor=c['button'], darkcolor=c['button'])
        s.map('TNotebook.Tab', background=[('selected', c['panel'])], foreground=[('selected', c['fg'])],
              lightcolor=[('selected', c['panel'])])
        s.configure('Treeview', background=c['panel'], fieldbackground=c['panel'], foreground=c['fg'], bordercolor=c['border'],
                    lightcolor=c['panel'], darkcolor=c['panel'])
        s.map('Treeview', background=[('selected', c['select'])], foreground=[('selected', c['select_fg'])])
        s.configure('Treeview.Heading', background=c['group'], foreground=c['fg'], bordercolor=c['border'],
                    lightcolor=c['group'], darkcolor=c['group'], relief='flat')
        s.map('Treeview.Heading', background=[('active', c['button_hover'])])
        for orient in ('Vertical', 'Horizontal'):
            s.configure(f'{orient}.TScrollbar', background=c['button'], troughcolor=c['bg'], bordercolor=c['bg'],
                        arrowcolor=c['muted'], lightcolor=c['button'], darkcolor=c['button'])
        s.configure('TProgressbar', background=c['accent'], troughcolor=c['group'], bordercolor=c['border'],
                    lightcolor=c['accent'], darkcolor=c['accent'])
        s.configure('TPanedwindow', background=c['bg'])
        s.configure('Sash', sashthickness=6, background=c['bg'], gripcount=0)
        self.tree.tag_configure('group', background=c['group'], font=self.f_bold)
        self.tree.tag_configure('ignored', foreground=c['muted'])
        for sev, key in SEV_KEY.items():
            self.tree.tag_configure(sev, foreground=c[key])
        self.style_text(self.text)
        self.code.configure(bg=c['panel'], fg=c['fg'], insertbackground=c['fg'], selectbackground=c['select'],
                            selectforeground=c['select_fg'], highlightbackground=c['border'], highlightcolor=c['border'])
        for key in ('clean', 'info', 'warning', 'error'):
            self.code.tag_configure('n_' + key, foreground=c[key], background=c[key + '_bg'])
            if key != 'clean':
                self.code.tag_configure('l_' + key, background=c[key + '_bg'])
        self.code.tag_configure('n_trunc', foreground=c['muted'])
        self.code.tag_configure('current', background=c['code_hl'])
        self.code.tag_configure('found', background=c['accent'], foreground=c['accent_fg'])
        self.code.tag_raise('found')
        self.code.tag_raise('current')
        self.overview.configure(bg=c['bg'])
        self.draw_overview()
        self.update_health_label()
        self.update_mode_widgets()
        for w in list(self.windows):
            if w.exists():
                w.restyle()
        self.dark_titlebar(name == 'dark')

    def style_text(self, t, editable=False):
        c = self.colors
        t.configure(bg=c['panel'] if not editable else c['field'], fg=c['fg'], insertbackground=c['fg'],
                    selectbackground=c['select'], selectforeground=c['select_fg'], highlightbackground=c['border'],
                    highlightcolor=c['accent'])
        t.tag_configure('title', font=self.f_big, spacing3=6)
        t.tag_configure('huge', font=self.f_huge)
        t.tag_configure('label', font=self.f_bold, foreground=c['muted'], spacing1=10, spacing3=2)
        t.tag_configure('label_inline', font=self.f_bold, foreground=c['muted'])
        t.tag_configure('muted', foreground=c['muted'])
        t.tag_configure('bold', font=self.f_bold)
        t.tag_configure('code', font=self.fonts['fixed'], background=c['code_bg'], lmargin1=8, lmargin2=8, rmargin=8)
        t.tag_configure('lineno', font=self.fonts['fixed'], foreground=c['muted'], background=c['code_bg'])
        t.tag_configure('hl', background=c['code_hl'])
        t.tag_configure('fix', lmargin1=4, lmargin2=26)
        t.tag_configure('before', font=self.fonts['fixed'], background=c['error_bg'], lmargin1=8, lmargin2=8, rmargin=8)
        t.tag_configure('after', font=self.fonts['fixed'], background=c['clean_bg'], lmargin1=8, lmargin2=8, rmargin=8)
        t.tag_configure('chg_before', foreground=c['error'], underline=True, font=self.fonts['fixed'])
        t.tag_configure('chg_after', foreground=c['clean'], underline=True, font=self.fonts['fixed'])
        t.tag_configure('banner', background=c['warning_bg'], lmargin1=8, lmargin2=8, rmargin=8, spacing1=4, spacing3=4)
        t.tag_configure('link', foreground=c['link'], underline=True)
        t.tag_configure('gloss', underline=True)
        for key in ('clean', 'info', 'warning', 'error'):
            t.tag_configure('c_' + key, foreground=c[key], font=self.f_bold)
        for sev, key in SEV_KEY.items():
            t.tag_configure(sev, foreground=c[key], font=self.f_bold)
        t.tag_raise('chg_before')
        t.tag_raise('chg_after')

    def dark_titlebar(self, on, win=None):
        if sys.platform != 'win32':
            return
        try:
            w = win or self.root
            w.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
            val = ctypes.c_int(1 if on else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x20)
        except Exception:
            pass

    def zoom(self, delta):
        self.scale = 1.0 if delta is None else min(2.5, max(0.7, round(self.scale + delta, 2)))
        self.apply_scale()
        return 'break'

    def apply_scale(self):
        sz = lambda m=1.0: max(6, round(10 * m * self.scale))
        self.fonts['base'].configure(size=sz())
        self.fonts['text'].configure(size=sz())
        self.fonts['heading'].configure(size=sz(), weight='bold')
        self.fonts['fixed'].configure(size=sz(0.95))
        self.f_bold.configure(size=sz())
        self.f_small.configure(size=sz(0.9))
        self.f_big.configure(size=sz(1.35))
        self.f_huge.configure(size=sz(2.4))
        rowh = self.fonts['base'].metrics('linespace') + max(6, round(8 * self.scale))
        self.style.configure('Treeview', rowheight=rowh, font=self.fonts['base'])
        self.style.configure('Treeview.Heading', font=self.fonts['heading'], padding=(6, round(4 * self.scale)))
        self.style.configure('TButton', padding=(round(10 * self.scale), round(4 * self.scale)))
        self.style.configure('TMenubutton', padding=(round(10 * self.scale), round(4 * self.scale)))
        self.style.configure('TNotebook.Tab', padding=(round(14 * self.scale), round(5 * self.scale)), font=self.f_bold)
        w = self.fonts['base'].measure('0')
        self.tree.column('#0', width=w * 3, minwidth=w * 2, stretch=False)
        self.tree.column('sev', width=w * 17, minwidth=w * 8, stretch=False)
        self.tree.column('title', width=w * 66, minwidth=w * 20, stretch=True)
        self.tree.column('passage', width=w * 28, minwidth=w * 10, stretch=True)
        self.tree.column('line', width=w * 8, minwidth=w * 5, stretch=False, anchor='e')
        self.tree.column('effort', width=w * 18, minwidth=w * 6, stretch=False)
        self.overview.configure(width=max(12, round(16 * self.scale)))
        self.zoom_label.configure(text=f'{round(self.scale * 100)}%')
        if hasattr(self, 'xl_var'):
            self.xl_var.set(self.scale >= 1.6)
        self.root.after_idle(self.refit_layout)   # bigger text needs more room: re-wrap the rows

    def refit_layout(self):
        for f in getattr(self, '_flows', ()):
            if f.winfo_exists():
                self._reflow(f)
        if getattr(self, '_toolbar', None) and self._toolbar[0].winfo_exists():
            self.fit_toolbar()

    def toggle_xl(self):
        if self.scale >= 1.6:
            self.scale = float(self.settings.get('scale_before_xl', 1.0))
        else:
            self.settings['scale_before_xl'] = self.scale
            self.scale = 1.6
        self.apply_scale()

    def toggle_colorblind(self):
        self.colorblind = self.cb_var.get()
        self.apply_theme()
        self.code_built_for = None
        if self.result:
            self.refresh()
            self.show_selected_or_summary()

    # ================================================================ modes
    def toggle_simple(self):
        self.simple_on = not self.simple_on
        self.after_mode_change()

    def toggle_audience(self):
        self.audience = 'player' if self.is_dev else 'dev'
        if not self.is_dev:
            self.sev_vars[scanner.INFO].set(False)
        else:
            self.sev_vars[scanner.INFO].set(True)
        self.after_mode_change()

    def set_edit_mode(self, mode):
        self.edit_mode = mode
        self.after_mode_change()

    def after_mode_change(self):
        self.update_mode_widgets()
        if self.result:
            self.fill_categories()
            self.update_counts()
            self.refresh()
            self.code_built_for = None
            if self.nb.select() == str(self.tab_code):
                self.build_code_view()
            self.show_selected_or_summary()
        else:
            self.show_welcome()

    def fit_toolbar(self):
        top, left, right = self._toolbar
        try:
            wrap = left.winfo_reqwidth() + right.winfo_reqwidth() + 30 > top.winfo_width()
        except tk.TclError:
            return
        if wrap == self._toolbar_wrapped:
            return
        self._toolbar_wrapped = wrap
        if wrap:
            right.grid(row=1, column=0, columnspan=2, sticky='e', pady=(6, 0))
        else:
            right.grid(row=0, column=1, columnspan=1, sticky='e', pady=0)

    def flow(self, frame, widgets):
        """Lay buttons out left to right, continuing on a new line when the window is too narrow."""
        frame._flow = list(widgets)
        frame._layout = None
        self._flows = [f for f in getattr(self, '_flows', []) if f.winfo_exists() and f is not frame] + [frame]
        frame.bind('<Configure>', lambda e: self._reflow(frame))
        for w in frame._flow:   # e.g. "Broken (19)" gets its count after the scan and grows
            if not getattr(w, '_flow_bound', False):
                w._flow_bound = True
                w.bind('<Configure>', lambda e, f=frame: self._schedule_reflow(f), add='+')
        self._reflow(frame)

    def _schedule_reflow(self, frame):
        if not getattr(frame, '_reflow_due', False):
            frame._reflow_due = True

            def go():
                frame._reflow_due = False
                if frame.winfo_exists():
                    self._reflow(frame)
            self.root.after_idle(go)

    def _reflow(self, frame):
        width = frame.winfo_width()
        if width <= 1:
            width = 10 ** 6   # not shown yet: one line until the real width is known
        rows, cur, x = [], [], 0
        for w in frame._flow:
            need = w.winfo_reqwidth() + 6
            if cur and x + need > width:
                rows.append(cur)
                cur, x = [], 0
            cur.append(w)
            x += need
        if cur:
            rows.append(cur)
        layout = [[str(w) for w in r] for r in rows]
        if layout == frame._layout:
            return
        frame._layout = layout
        for w in frame._flow:
            w.pack_forget()
        for rf in getattr(frame, '_rows', []):
            rf.destroy()
        frame._rows = []
        for n, r in enumerate(rows):
            rf = ttk.Frame(frame)
            rf.pack(fill='x', pady=(0 if n == 0 else 4, 0))
            frame._rows.append(rf)
            for w in r:
                w.pack(in_=rf, side='left', padx=(0, 6))
                w.lift(rf)

    def set_language(self, code):
        i18n.set_language(code)
        self.settings['language'] = code
        self.save_settings()
        self.rebuild_ui()

    def rebuild_ui(self):
        sel = self.selected_issue()
        sel_key = sel.key if sel else None
        tab_code = self.result is not None and self.nb.select() == str(self.tab_code)
        for w in list(self.windows):
            if w.exists():
                w.close()
        if self.tour:
            self.tour.hide()
            self.tour = None
        self.code_built_for = None
        self.build_ui()
        self.apply_scale()
        self.apply_theme()
        if self.result:
            self.after_result(keep_key=sel_key)
            if tab_code and self.is_dev:
                self.nb.select(self.tab_code)
            if not sel_key:
                self.show_summary()
        else:
            self.show_welcome()

    def show_selected_or_summary(self):
        if self.selected_issue():
            self.show_selected()
        elif self.result:
            self.show_summary()

    # ================================================================ open & scan
    def choose_file(self):
        if self.scanning:
            return
        start = os.path.dirname(self.target or self.settings.get('last_file', '')) or os.path.expanduser('~')
        f = filedialog.askopenfilename(title=T('Open the game'), initialdir=start,
                                       filetypes=[(T('Games'), '*.html *.htm *.rpy *.ink *.json *.txt'),
                                                  (T('HTML games'), '*.html *.htm'), (T('All files'), '*.*')])
        if f:
            self.open_path(f)

    def choose_folder_game(self):
        if self.scanning:
            return
        d = filedialog.askdirectory(title=T('Pick the game folder'), initialdir=os.path.dirname(self.target) or os.path.expanduser('~'))
        if d:
            self.open_path(d)

    def choose_folder(self):
        if self.scanning:
            return
        d = filedialog.askdirectory(title=T('Folder with the game\'s pictures, sounds and videos'),
                                    initialdir=self.game_dir or (self.result.game_dir if self.result else os.path.expanduser('~')))
        if d:
            self.game_dir = os.path.abspath(d)
            self.update_path_label()
            if self.target:
                self.start_scan()

    def open_path(self, p):
        self.target = os.path.abspath(p)
        self.game_dir = ''
        self.settings['last_file'] = self.target
        self.result = None
        self.update_path_label()
        self.start_scan()

    def update_path_label(self):
        if not self.target:
            self.path_label.configure(text=T('No game opened'))
            return
        folder = self.game_dir or (self.result.game_dir if self.result else os.path.dirname(self.target))
        self.path_label.configure(text=T('Game: {path}     Folder: {folder}', path=self.target, folder=folder))

    def start_scan(self, reason='scan', callback=None):
        if not self.target or self.scanning:
            return
        self.scanning = True
        self._scan_reason = reason
        self._check_cb = callback
        for b in (self.btn_scan, self.btn_full, self.btn_playtest, self.btn_wizard):
            b.configure(state='disabled')
        self.progress['value'] = 0
        self.set_status(T('Scanning…'))
        if reason == 'scan':
            self.tree.delete(*self.tree.get_children())
            self.set_details([(T('Scanning, please wait…'), 'title')])
            self.code_placeholder()
        target, game_dir = self.target, self.game_dir or None

        def work():
            try:
                res = scanner.scan_any(target, game_dir, progress=lambda p, m: self.q.put(('progress', p, m)))
                self.q.put(('done', res))
            except Exception as e:
                self.q.put(('error', e))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'progress':
                    self.progress['value'] = msg[1]
                    m = msg[2]
                    self.set_status(T('Working…') + ' ' + (T(m[0], **m[1]) if isinstance(m, tuple) else T(m)))
                elif kind == 'done':
                    self.scan_done(msg[1])
                elif kind == 'error':
                    self.scanning = False
                    self.update_buttons()
                    self.set_status(T('Scan failed'))
                    cb, self._check_cb = self._check_cb, None
                    if cb:
                        cb(RuntimeError(str(msg[1])))
                    else:
                        messagebox.showerror(APP_NAME, T('Could not scan:') + f'\n\n{msg[1]}')
                        if not self.result:
                            self.show_welcome()
                elif kind == 'call':
                    msg[1]()
                elif kind == 'pt_done':
                    self.playtest_done(*msg[1:])
                elif kind == 'pt_error':
                    self.playtesting = False
                    self.update_buttons()
                    self.set_status(T('Play-test stopped'))
                    messagebox.showerror(APP_NAME, T('The play-test could not run:') + f'\n\n{msg[1]}')
                elif kind == 'fs_done':
                    self.fullscan_done(*msg[1:])
                elif kind == 'fs_error':
                    self.fullscanning = False
                    self.update_buttons()
                    self.set_status(T('Full scan stopped'))
                    messagebox.showerror(APP_NAME, T('The Full scan could not run:') + f'\n\n{msg[1]}'
                                         + '\n\n' + T('What was done so far is kept. Click "Full scan…" to continue.'))
        except queue.Empty:
            pass
        try:
            want = T('Stop reading') if speech.speaking() else T('Read aloud')
            if self.btn_read.cget('text') != want:
                self.btn_read.configure(text=want)
        except tk.TclError:
            pass
        self.root.after(80, self.poll)

    def scan_done(self, res):
        self.scanning = False
        old = self.result
        reason = self._scan_reason
        sel = self.selected_issue()
        keep_key = sel.key if (sel and reason != 'scan') else None
        self.result = res
        self._mtime = self.target_mtime()
        self.saved_fullscan(res, fresh=reason == 'scan' and not self._check_cb)
        self.after_result(keep_key=keep_key)
        if reason == 'scan' or not keep_key:
            if not self.selected_issue():
                self.show_summary()
        cb, self._check_cb = self._check_cb, None
        if cb:
            cb(old)
        elif reason == 'watch' and old is not None:
            fixed, added, _ = D.compare(old, res)
            self.set_status(T('File saved - checked again: {fixed} fixed, {new} new', fixed=len(fixed), new=len(added))
                            + '  ·  ' + T('Code health: {n}%', n=self.display_health()[0]))

    def after_result(self, keep_key=None):
        self.line_issues, self.problem_lines = {}, []
        self.code_built_for = None
        self.update_path_label()
        self.fill_code_files()
        self.fill_categories()
        self.update_counts()
        self.update_health_label()
        self.update_mode_widgets()
        self.update_buttons()
        self.refresh()
        if keep_key:
            i = self.issue_by_key(keep_key)
            if i:
                self.select_issue(i)
        if self.nb.select() == str(self.tab_code):
            self.build_code_view()

    def update_buttons(self):
        has = self.result is not None and not self.scanning
        full = self.fullscanning
        self.btn_scan.configure(state='normal' if self.target and not self.scanning and not full else 'disabled')
        can_pt = has and self.result.engine in ('twine', 'html') and not full
        self.btn_playtest.configure(state='normal' if (can_pt or self.playtesting) else 'disabled',
                                    text=T('■ Stop play-test') if self.playtesting else T('▶ Play-test'))
        import fullscan
        can_full = has and fullscan.can_play(self.result) and not self.playtesting
        self.btn_full.configure(state='normal' if (can_full or full) else 'disabled',
                                text=T('❚❚ Pause Full scan') if full else T('Full scan…'))
        self.btn_wizard.configure(state='normal' if has else 'disabled')

    def target_mtime(self):
        if not self.result:
            return None
        paths = [self.result.html_path] + list(self.result.files)
        try:
            return max(os.path.getmtime(p) for p in paths if os.path.exists(p))
        except ValueError:
            return None

    # ================================================================ ignore list
    def game_key(self):
        return norm(self.result.html_path) if self.result else ''

    def ignored_keys(self):
        return set(self.settings.get('ignored', {}).get(self.game_key(), []))

    def is_ignored(self, i):
        return i.key in self.ignored_keys()

    def toggle_ignore(self, i):
        if not i:
            return
        ig = self.settings.setdefault('ignored', {}).setdefault(self.game_key(), [])
        if i.key in ig:
            ig.remove(i.key)
            self.set_status(T('No longer marked as intentional'))
        else:
            ig.append(i.key)
            self.set_status(T('Marked as intentional - hidden from the list (Tools > Show problems marked as intentional)'))
        self.save_settings()
        self.code_built_for = None
        self.update_counts()
        self.update_health_label()
        self.refresh()
        self.show_selected_or_summary()

    def toggle_show_ignored(self):
        self.show_ignored = self.ignored_var.get()
        self.refresh()

    def active_issues(self):
        """Issues that count: not marked as intentional."""
        if not self.result:
            return []
        ig = self.ignored_keys()
        return [i for i in self.result.issues if i.key not in ig]

    def display_health(self):
        return scanner.health_score(self.active_issues(), self.result.stats['passages'])

    # ================================================================ counts, health
    def fill_categories(self):
        cats = sorted({i.category for i in self.result.issues}, key=lambda c: self.cat_word(c).lower())
        self.cat_map = {self.cat_word(c): c for c in cats}
        if any(i.unreachable for i in self.result.issues):
            self.cat_map[T("In scenes nobody can reach")] = UNREACHABLE
            cats.append(UNREACHABLE)
        self.cat_box.configure(values=[T('All kinds')] + list(self.cat_map))
        real = self._cat_real
        shown = {c: w for w, c in self.cat_map.items()}
        self.cat_var.set(shown[real] if real in shown else T('All kinds'))

    def update_counts(self):
        if not self.result:
            for sev, c in self.sev_checks.items():
                c.configure(text=self.sev_word(sev, True))
            return
        act = self.active_issues()
        n = {s: sum(1 for i in act if i.severity == s) for s in (scanner.ERROR, scanner.WARNING, scanner.INFO)}
        for sev in n:
            self.sev_checks[sev].configure(text=f'{self.sev_word(sev, True)} ({n[sev]})')
        st = self.result.stats
        ig = len(self.result.issues) - len(act)
        self.set_status(T('{fmt} · {units} parts · {files} files in folder · {e} broken, {w} probably broken, {i} to check · {s} s',
                          fmt=st['format'], units=st['passages'], files=st['files_in_folder'], e=n[scanner.ERROR],
                          w=n[scanner.WARNING], i=n[scanner.INFO], s=st['seconds'])
                        + (('  ·  ' + T('{n} marked as intentional', n=ig)) if ig else ''))

    def update_health_label(self):
        if not self.result:
            self.health_label.configure(text='')
            return
        score, label = self.display_health()
        self.style.configure('Health.TLabel', foreground=self.colors[health_color_key(score)])
        self.health_label.configure(text=T('Code health: {n}% · {label}', n=score, label=T(label)))

    # ================================================================ list
    def filtered(self):
        if not self.result:
            return []
        shown = self.cat_var.get()
        cat = None if shown == T('All kinds') else self.cat_map.get(shown, shown)
        self._cat_real = cat
        q = self.search_var.get().strip().lower()
        ig = self.ignored_keys()
        out = []
        for i in self.result.issues:
            if not self.sev_vars[i.severity].get():
                continue
            if i.key in ig and not self.show_ignored:
                continue
            if cat == UNREACHABLE:
                if not i.unreachable:
                    continue
            elif cat and i.category != cat:
                continue
            if q:
                hay = ' '.join((i.title, i.passage, i.snippet, i.what, str(i.line), self.title_of(i))).lower()
                if q not in hay:
                    continue
            out.append(i)
        return out

    def schedule_search(self):
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(250, self.refresh)

    def focus_search(self):
        (self.find_entry if self.nb.select() == str(self.tab_code) else self.search_entry).focus_set()
        return 'break'

    def group_mode(self):
        rev = {v: k for k, v in self.group_names.items()}
        return rev.get(self.group_var.get(), 'Type')

    def refresh(self):
        self._search_job = None
        if not self.result:
            return
        self.visible = self.filtered()
        tree = self.tree
        keep = tree.selection()
        tree.delete(*tree.get_children())
        self.index = {id(i): n for n, i in enumerate(self.result.issues)}
        ig = self.ignored_keys()

        def row(parent, i):
            tags = (i.severity,) + (('ignored',) if i.key in ig else ())
            effort = fixes.difficulty_label(i) if self.is_dev else ''
            tree.insert(parent, 'end', iid=f'i{self.index[id(i)]}', text='',
                        values=(f'{SEV_ICON[i.severity]} {self.sev_word(i.severity)}', self.title_of(i),
                                i.passage or '—', i.line or '', effort), tags=tags)
        mode = self.group_mode()
        if mode == 'Nothing':
            for i in self.visible:
                row('', i)
        else:
            key = {'Type': lambda i: self.cat_word(i.category), 'Severity': lambda i: self.sev_word(i.severity, True),
                   'Passage': lambda i: i.passage or T('(whole game)')}[mode]
            groups = {}
            for i in self.visible:
                groups.setdefault(key(i), []).append(i)
            order = sorted(groups, key=lambda g: (min(scanner.SEVERITY_ORDER[i.severity] for i in groups[g]), g.lower()))
            for g in order:
                items = groups[g]
                worst = min(items, key=lambda i: scanner.SEVERITY_ORDER[i.severity]).severity
                gid = 'g:' + g
                tree.insert('', 'end', iid=gid, text='', open=len(order) <= 12 or len(items) <= 25,
                            values=(f'{SEV_ICON[worst]} {len(items)}', g, '', '', ''), tags=('group',))
                for i in items:
                    row(gid, i)
        if keep and keep[0].startswith('i') and tree.exists(keep[0]):
            tree.selection_set(keep[0])
            tree.see(keep[0])
        if not self.visible:
            self.set_details([(T('Nothing to show'), 'title'),
                              ('\n' + (T('No problems match the current filters.') if self.result.issues else T('No problems found. Nice work!')), None)])

    def sort_by(self, col):
        if not self.result:
            return
        keyf = {'sev': lambda i: (scanner.SEVERITY_ORDER[i.severity], i.line), 'title': lambda i: self.title_of(i).lower(),
                'passage': lambda i: (i.passage.lower(), i.line), 'line': lambda i: i.line,
                'effort': lambda i: (-i.priority, i.line)}[col]
        rev = getattr(self, '_sort', None) == col
        self._sort = None if rev else col
        sel = self.selected_issue()
        self.result.issues.sort(key=keyf, reverse=rev)
        self.code_built_for = None
        self.tree.selection_set(())
        self.refresh()
        if sel:
            self.select_issue(sel)

    def selected_issue(self):
        try:
            sel = self.tree.selection()
        except tk.TclError:
            return None
        if not sel or not sel[0].startswith('i') or not self.result:
            return None
        n = int(sel[0][1:])
        return self.result.issues[n] if n < len(self.result.issues) else None

    def issue_by_key(self, key):
        if not self.result:
            return None
        return next((i for i in self.result.issues if i.key == key), None)

    def select_issue(self, i):
        self.nb.select(self.tab_problems)
        n = self.result.issues.index(i)
        iid = f'i{n}'
        if not self.tree.exists(iid):
            for v in self.sev_vars.values():
                v.set(True)
            self.cat_var.set(T('All kinds'))
            self.search_var.set('')
            if self.is_ignored(i):
                self.show_ignored = True
                self.ignored_var.set(True)
            self.refresh()
        if self.tree.exists(iid):
            parent = self.tree.parent(iid)
            if parent:
                self.tree.item(parent, open=True)
            self.tree.selection_set(iid)
            self.tree.see(iid)
            self.show_selected()

    # ================================================================ details panel
    def set_details(self, parts):
        t = self.text
        t.delete('1.0', 'end')
        for text, tag in parts:
            t.insert('end', text, tag) if tag else t.insert('end', text)

    def set_detail_buttons(self, i):
        line_on = 'normal' if i and i.line else 'disabled'
        on = 'normal' if i else 'disabled'
        self.btn_copy.configure(state=on)
        self.btn_read.configure(state='normal')
        self.btn_editor.configure(state=line_on)
        self.btn_showcode.configure(state=line_on)
        self.btn_copyfix.configure(state='normal' if i and self.fix_form(i) is not None else 'disabled')
        self.btn_search.configure(state='normal' if i and i.search else 'disabled')
        self.btn_check.configure(state=on if not self.scanning else 'disabled')
        self.btn_report1.configure(state=on)
        self.btn_ignore.configure(state=on, text=(T('Un-mark as intentional') if i and self.is_ignored(i) else T('Mark as intentional')))
        self.btn_reach.configure(state='normal' if i and self.is_twine and i.passage in self.result.passages else 'disabled')
        for bar in (self.bar1, self.bar2):   # labels may have changed length
            if getattr(bar, '_flow', None):
                self.root.after_idle(lambda b=bar: b.winfo_exists() and self._reflow(b))

    def show_welcome(self):
        parts = [
            (f'{APP_NAME}\n', 'title'),
            (T('Checks games for mistakes and tells you in plain words what is wrong and how to fix it. Works with Twine games '
               '(SugarCube, Harlowe, Chapbook, Snowman), other HTML games, Ren\'Py, RPG Maker MV/MZ, Ink and ChoiceScript.') + '\n', None),
            (T('How to use it') + '\n', 'label'),
            (T('1. Click "Open game…" and pick the game\'s .html file (or use Tools for a game folder). The check starts by itself.') + '\n'
             + T('2. Click a problem in the list. Below it you see what is wrong, what players will notice, and exactly how to fix it.') + '\n'
             + T('3. Not sure where to start? Use the Fix wizard - it goes through the problems one by one and checks your fixes.') + '\n'
             + T('4. Help > Lessons explains the basics in a few minutes. Help > Take the tour shows you around.') + '\n', None),
            (T('Safe to use') + '\n', 'label'),
            (T('This program only reads your game. It never changes it. Use Tools > Back up the game file before you edit anything.') + '\n', None),
            (T('Shortcuts') + '\n', 'label'),
            (T('Ctrl+O open · F5 scan again · Ctrl+F search · F3 find next · Ctrl+E export · F1 lessons · Ctrl + / Ctrl − / Ctrl+0 or Ctrl+mouse wheel to resize') + '\n', 'muted'),
        ]
        self.set_details(parts)
        self.set_detail_buttons(None)

    def start_here(self, n=5):
        act = [i for i in self.active_issues() if i.severity != scanner.INFO]
        return sorted(act, key=lambda i: (-i.priority, i.line))[:n]

    def show_summary(self):
        if not self.result:
            return
        st = self.result.stats
        score, label = self.display_health()
        ck = health_color_key(score)
        act = self.active_issues()
        n = {s: sum(1 for i in act if i.severity == s) for s in (scanner.ERROR, scanner.WARNING, scanner.INFO)}
        t = self.text
        self.set_details([])
        t.insert('end', T('Check finished') + '\n', 'title')
        t.insert('end', f'{os.path.basename(self.result.html_path)} · {st["format"]} · ' +
                 T('{n} parts', n=st['passages']) + ' · ' + T('{n} file references checked', n=st['assets_referenced']) + '\n', 'muted')
        t.insert('end', T('Code health') + '\n', 'label')
        t.insert('end', f'{score}%', 'huge')
        t.insert('end', f'  {T(label)}\n', 'c_' + ck)
        t.insert('end', T('Scale: 100% excellent · 90% great · 80% good · 70% fine · below 70% needs work') + '\n', 'muted')
        t.insert('end', T('It is based on how many scenes have problems and how serious they are.') + '\n\n', 'muted')
        for sev in (scanner.ERROR, scanner.WARNING, scanner.INFO):
            t.insert('end', f'{n[sev]} {self.sev_word(sev, True).lower()}', sev)
            t.insert('end', '  ' + simple.severity_meaning(sev) + '\n', 'muted')
        top = self.start_here()
        if top and self.is_dev:
            t.insert('end', T('Start here - the most important fixes') + '\n', 'label')
            for k, i in enumerate(top, 1):
                tag = f'start{k}'
                t.insert('end', f'{k}. ')
                t.insert('end', f'{SEV_ICON[i.severity]} ', i.severity)
                t.insert('end', self.title_of(i), ('link', tag))
                t.insert('end', ('  ·  ' + i.passage if i.passage else '') + f'  ·  {fixes.difficulty_label(i)}\n', 'muted')
                t.tag_bind(tag, '<Button-1>', lambda e, i=i: self.select_issue(i))
                t.tag_bind(tag, '<Enter>', lambda e: t.configure(cursor='hand2'))
                t.tag_bind(tag, '<Leave>', lambda e: t.configure(cursor='arrow'))
            t.insert('end', T('Or click "Fix wizard" to go through them one by one.') + '\n', 'muted')
        if not self.is_dev:
            broken = sorted({i.passage for i in act if i.severity == scanner.ERROR and i.passage})
            if broken:
                t.insert('end', T('Scenes that are broken') + '\n', 'label')
                t.insert('end', T('Save your game before you visit these, so you can go back if something goes wrong:') + '\n', 'muted')
                for p in broken[:60]:
                    t.insert('end', f'• {p}\n')
            t.insert('end', '\n' + T('Use "Tell the developer" on a problem, or Tools > Write a bug report, to send them the details.') + '\n', 'muted')
        cats = {}
        for i in act:
            cats.setdefault(i.category, [0, 0, 0])[scanner.SEVERITY_ORDER[i.severity]] += 1
        t.insert('end', T('By kind') + '\n', 'label')
        sw = lambda s: self.sev_word(s, True).lower()
        for c, (e, w, nn) in sorted(cats.items(), key=lambda x: (-x[1][0], -x[1][1], x[0])):
            bits = [f'{e} {sw(scanner.ERROR)}' if e else '', f'{w} {sw(scanner.WARNING)}' if w else '', f'{nn} {sw(scanner.INFO)}' if nn else '']
            t.insert('end', f'{self.cat_word(c)}: ')
            t.insert('end', ', '.join(b for b in bits if b) + '\n', 'muted')
        t.insert('end', '\n' + T('Click a problem in the list to see the details.'), 'muted')
        self.set_detail_buttons(None)

    def show_selected(self):
        i = self.selected_issue()
        if not i:
            sel = self.tree.selection()
            if sel and sel[0].startswith('g:'):
                n = len(self.tree.get_children(sel[0]))
                self.set_details([(sel[0][2:] + '\n', 'title'),
                                  (T('{n} problem(s) in this group. Click one to see the details.', n=n), 'muted')])
            self.set_detail_buttons(None)
            return
        self.render_issue(self.text, i)
        self.text.see('1.0')
        self.set_detail_buttons(i)

    # ---------------------------------------------------------------- rendering one issue
    def fix_form(self, i):
        if not self.result or not i or not (i.old or i.new):
            return None
        mode = 'twine' if (self.is_twine and self.edit_mode == 'twine') else 'file'
        return fixes.forms(i, self.result, mode)

    def search_form(self, i):
        mode = 'twine' if (self.is_twine and self.edit_mode == 'twine') else 'file'
        return fixes.search_for(i, self.result, mode)

    def where_text(self, i):
        bits = []
        if i.passage:
            bits.append(T('Scene "{name}"', name=i.passage) if self.simple_on else T('Passage "{name}"', name=i.passage))
        if i.line:
            if self.is_twine and self.edit_mode == 'twine' and i.passage in self.result.passages:
                pl = i.line - self.result.passages[i.passage][0] + 1
                bits.append(T('line {n} of the passage', n=pl))
            else:
                bits.append(T('line {n} of {file}', n=i.line, file=os.path.basename(i.file or self.result.html_path)))
        elif i.file and norm(i.file) != norm(self.result.html_path):
            bits.append(os.path.basename(i.file))
        return ' · '.join(bits) or T('The game folder / whole game')

    def render_issue(self, t, i, wizard=False):
        res = self.result
        d = simple.simplify(i) if self.simple_on else dict(title=i.title, what=i.what, effect='', fix=i.fix)
        t.delete('1.0', 'end')
        t.insert('end', d['title'] + '\n', 'title')
        t.insert('end', f'{SEV_ICON[i.severity]} {self.sev_word(i.severity)}', i.severity)
        t.insert('end', '  ·  ' + (simple.severity_meaning(i.severity) if self.simple_on else i.category), 'muted')
        if self.is_dev and i.difficulty:
            t.insert('end', '  ·  ' + fixes.difficulty_label(i) + ' (' + fixes.effort_text(i) + ')', 'muted')
        t.insert('end', '\n')
        if self.is_ignored(i):
            t.insert('end', T('You marked this as intentional, so it does not count.') + '\n', 'banner')
        t.insert('end', T('Where') + '\n', 'label')
        t.insert('end', self.where_text(i) + '\n')
        t.insert('end', (T("What's wrong") if self.simple_on else T('What is wrong')) + '\n', 'label')
        t.insert('end', (d.get('what') or i.what or '') + '\n')
        if d.get('effect'):
            t.insert('end', T('What players will notice') + '\n', 'label')
            t.insert('end', d['effect'] + '\n')
        if not self.is_dev:
            if i.severity == scanner.ERROR and i.passage:
                t.insert('end', T('Tip') + '\n', 'label')
                t.insert('end', T('Save your game before you reach this scene, so you can go back if it breaks.') + '\n')
            self.add_glossary_tags(t)
            return
        t.insert('end', T('How to fix it') + '\n', 'label')
        ff = self.fix_form(i)
        search = self.search_form(i) if i.search else ''
        steps = []
        twine_edit = self.is_twine and self.edit_mode == 'twine'
        if twine_edit and i.passage:
            steps.append(T('Open your story in Twine and open the passage "{name}".', name=i.passage))
            if search:
                steps.append(T('Find the spot: use Twine\'s "Find and replace" (or look carefully) for this text:') + f'  {search}')
        elif i.line:
            steps.append(T('Click "Open in my text editor". It opens {file} at line {n}.', file=os.path.basename(i.file or res.html_path), n=i.line))
            if search:
                steps.append(T('If the line has moved, press Ctrl+F in your editor and search for:') + f'  {search}')
        elif res.engine == 'rpgmaker' and i.passage:
            steps.append(T('Open the project in RPG Maker and go to: {where}', where=i.passage))
        if ff is not None:
            if ff[1] == '':
                steps.append(T('Delete that line (it is shown below under "Now").'))
            else:
                steps.append(T('Change it as shown below under "Change to". "Copy fixed line" copies the whole fixed line for you to paste over the old one.'))
        else:
            steps.append(d.get('fix') or i.fix or T('See the explanation above.'))
        if i.also_lines:
            more = ', '.join(str(n) for n in i.also_lines[:15]) + (' …' if len(i.also_lines) > 15 else '')
            steps.append(T('The same name is also used on {n} other line(s): {lines}. Change it there too.', n=len(i.also_lines), lines=more))
        if twine_edit:
            steps.append(T('Publish the story again (Build > Publish to File), then reload the game to check.'))
        elif i.line or ff is not None:
            steps.append(T('Save the file, reload the game, then click "I fixed it - check".'))
        for k, s in enumerate(steps, 1):
            t.insert('end', f'{k}. ', 'bold')
            t.insert('end', s + '\n', 'fix')
        if self.is_twine and self.edit_mode == 'file' and i.line:
            t.insert('end', T('Made in Twine? Then fix it in Twine instead (switch "I edit the .html file" above to "I edit in Twine"). '
                              'Publishing from Twine overwrites changes made directly in the .html file.') + '\n', 'banner')
        if ff is not None:
            old, new = ff
            t.insert('end', T('Now') + '\n', 'label')
            self.insert_change(t, old, new, before=True)
            t.insert('end', T('Change to') + '\n', 'label')
            if new == '':
                t.insert('end', T('(delete this line)') + '\n', 'muted')
            else:
                self.insert_change(t, old, new, before=False)
        if i.advice:
            t.insert('end', T('Can I ignore this?') + '\n', 'label')
            t.insert('end', i.advice + '\n')
        if not wizard or ff is None:
            self.insert_code(t, i)
        self.add_glossary_tags(t)

    def insert_change(self, t, old, new, before):
        """One side of a before/after pair, changed characters highlighted, long lines trimmed around the change."""
        a = 0
        while a < min(len(old), len(new)) and old[a] == new[a]:
            a += 1
        b = 0
        while b < min(len(old), len(new)) - a and old[len(old) - 1 - b] == new[len(new) - 1 - b]:
            b += 1
        s = old if before else new
        mid_end = len(s) - b
        start = max(0, a - 120)
        end = min(len(s), mid_end + 120)
        tag = 'before' if before else 'after'
        t.insert('end', ('… ' if start > 0 else '') + s[start:a], tag)
        t.insert('end', s[a:mid_end], (tag, 'chg_before' if before else 'chg_after'))
        t.insert('end', s[mid_end:end] + (' …' if end < len(s) else '') + '\n', tag)

    def insert_code(self, t, i):
        lines = self.result.lines_of(i.file)
        label = T('The line in the game file (highlighted)') if self.simple_on else T('Code')
        if i.line and 0 < i.line <= len(lines):
            t.insert('end', label + '\n', 'label')
            width = len(str(min(len(lines), i.line + 3)))
            for n in range(max(1, i.line - 3), min(len(lines), i.line + 3) + 1):
                text_ = lines[n - 1].rstrip('\r')
                if len(text_) > 400:
                    text_ = text_[:400] + ' …'
                hl = ('hl',) if n == i.line else ()
                t.insert('end', f'{n:>{width}}  ', ('lineno',) + hl)
                t.insert('end', text_ + '\n', ('code',) + hl)
        elif i.snippet:
            t.insert('end', label + '\n', 'label')
            t.insert('end', i.snippet + '\n', 'code')

    def add_glossary_tags(self, t):
        if not self.simple_on:
            return
        for n, (word, expl) in enumerate(content.glossary()):
            tag = f'g{n}'
            start, count = '1.0', 0
            while count < 30:
                pos = t.search(r'\m' + re.escape(word) + r'\M', start, stopindex='end', regexp=True, nocase=True)
                if not pos:
                    break
                end = f'{pos}+{len(word)}c'
                # Not inside file names or code: "game.html", "&lt;/link&gt;", "$line", "<<widget>>".
                prev, nxt, nxt2 = t.get(f'{pos}-1c'), t.get(end), t.get(f'{end}+1c')
                inside = ((prev and prev in './\\&;<$_#:-') or (nxt and nxt in '&;>/\\_-')
                          or (nxt == '.' and nxt2.isalnum()))
                if not inside and not set(t.tag_names(pos)) & {'code', 'before', 'after', 'title', 'lineno', 'label'}:
                    t.tag_add('gloss', pos, end)
                    t.tag_add(tag, pos, end)
                start = end
                count += 1
            t.tag_bind(tag, '<Enter>', lambda e, ex=expl: self.tooltip.show(e.x_root, e.y_root, ex))
            t.tag_bind(tag, '<Leave>', lambda e: self.tooltip.hide())

    def issue_text(self, i):
        if self.simple_on:
            d = simple.simplify(i)
            out = [f'[{simple.severity(i.severity)}] {d["title"]}', T('Where: {where}', where=self.where_text(i)),
                   T("What's wrong: {text}", text=d['what'] or i.what)]
            if d.get('effect'):
                out.append(T('What players will notice: {text}', text=d['effect']))
            out.append(T('How to fix it: {text}', text=d['fix']))
        else:
            out = [f'[{i.severity}] {i.title}', i.category, self.where_text(i)]
            if i.what:
                out.append(i.what)
            if i.fix:
                out.append(i.fix)
        ff = self.fix_form(i)
        if ff:
            out.append(T('Now: {text}', text=ff[0].strip()))
            out.append(T('Change to: {text}', text=ff[1].strip() or T('(delete this line)')))
        elif i.snippet:
            out.append(T('Code: {text}', text=i.snippet))
        return '\n'.join(out)

    def copy_details(self):
        i = self.selected_issue()
        if i:
            self.copy_text(self.issue_text(i))
        return 'break'

    def copy_fixed(self, i):
        ff = self.fix_form(i) if i else None
        if not ff:
            return
        self.copy_text(ff[1])
        if self.is_twine and self.edit_mode == 'twine':
            self.set_status(T('Fixed line copied. In Twine, select the old line in passage "{name}" and paste.', name=i.passage))
        else:
            self.set_status(T('Fixed line copied. In your editor, select line {n} and paste over it.', n=i.line))

    def copy_search(self, i):
        if i and i.search:
            self.copy_text(self.search_form(i))
            self.set_status(T('Search text copied. In your editor press Ctrl+F and paste it.'))

    # ================================================================ check a fix
    def check_selected(self):
        i = self.selected_issue()
        if not i:
            return

        def done(result):
            ok, msg = result
            self.set_status(msg)
            if self.settings.get('_selftest'):
                self.last_check = result
            elif ok:
                messagebox.showinfo(APP_NAME, msg)
            else:
                messagebox.showwarning(APP_NAME, msg)
        self.check_fix(i, done)

    def check_fix(self, i, callback):
        """Rescan and tell whether issue i is gone. callback((fixed, message))."""
        lk = fixes.loose_key(i)
        before = sum(1 for x in self.result.issues if fixes.loose_key(x) == lk)
        rename, ifile = i.rename, i.file

        def after(old):
            if isinstance(old, Exception):
                callback((False, T('Could not scan:') + f' {old}'))
                return
            new_same = [x for x in self.result.issues if fixes.loose_key(x) == lk]
            if len(new_same) < before:
                callback((True, T('✔ Fixed! The problem is gone. Code health is now {n}%.', n=self.display_health()[0])))
                return
            lines = sorted({x.line for x in new_same if x.line})
            if rename:
                pat = re.compile(re.escape(rename[0]) + r'(?![\w])')
                lines = [n for n, l in enumerate(self.result.lines_of(ifile), 1) if pat.search(l)][:20]
            where = ', '.join(str(n) for n in lines[:20])
            msg = T('Not fixed yet - the problem is still there') + (' ' + T('(line {lines})', lines=where) if where else '') + '. '
            if self._mtime_before_check == self._mtime:
                msg += T('The file has not changed since the last check. Did you save it?')
            else:
                msg += T('Compare your line with the "Change to" line again.')
            callback((False, msg))
        self._mtime_before_check = self._mtime
        self.start_scan(reason='check', callback=after)

    # ================================================================ text editor
    def current_editor(self):
        ed = self.settings.get('editor')
        if ed and os.path.isfile(ed.get('path', '')):
            return ed
        found = detect_editors()
        return found[0] if found else None

    def choose_editor(self):
        D.EditorDialog(self, detect_editors(), self.current_editor())

    def open_in_editor(self, issue=None, line=None, path=None):
        i = issue or self.selected_issue()
        if i is not None:
            line = line or i.line
            path = path or i.file or self.result.html_path
        if not self.result or not line:
            return
        path = path or self.result.html_path
        ed = self.current_editor()
        if not ed:
            self.choose_editor()
            return
        args = ed.get('args') or '"{file}"'
        try:
            argv = [ed['path']] + shlex.split(args.replace('{file}', path).replace('{line}', str(line)), posix=False)
            argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
            self.last_editor_argv = argv
            if not self.settings.get('_selftest'):
                subprocess.Popen(argv)
            jump = '' if '{line}' in args else ' - ' + T('press Ctrl+G and type {n} to jump to the line', n=line)
            self.set_status(T('Opened in {name}', name=ed['name']) + jump)
        except OSError as e:
            messagebox.showerror(APP_NAME, T('Could not start {name}:', name=ed['name']) + f'\n{e}\n\n' + T('Pick another editor with Tools > Text editor…'))

    # ================================================================ tools
    def backup_game(self):
        if not self.result:
            messagebox.showinfo(APP_NAME, T('Open a game first.'))
            return
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H%M%S')
        res = self.result
        try:
            if res.engine in ('twine', 'html'):
                base, ext = os.path.splitext(res.html_path)
                dst = f'{base} - backup {stamp}{ext}'
                shutil.copy2(res.html_path, dst)
            else:
                root = res.game_dir
                dst = os.path.join(os.path.dirname(root), f'{os.path.basename(root)} - backup {stamp}.zip')
                with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
                    for p in [res.html_path] + list(res.files):
                        if os.path.isfile(p):
                            z.write(p, os.path.relpath(p, os.path.dirname(root)))
            self.last_backup = dst
            self.set_status(T('Backup saved: {path}', path=dst))
            if not self.settings.get('_selftest'):
                messagebox.showinfo(APP_NAME, T('Backup saved:') + f'\n\n{dst}')
        except OSError as e:
            messagebox.showerror(APP_NAME, T('Could not make the backup:') + f'\n{e}')

    def open_test_line(self):
        D.TestLineDialog(self)

    def test_snippet(self, text, whole):
        """Check a piece of SugarCube text against the open game. None = not supported."""
        res = self.result
        if not res or not res.context or not getattr(res.context, 'is_sugarcube', False):
            return None
        ctx = res.context
        sc = scanner.Scanner(res.html_path, res.game_dir)
        sc.format, sc.format_version = ctx.format, ctx.format_version
        sc.widgets, sc.js_macros = dict(ctx.widgets), dict(ctx.js_macros)
        sc.passage_names, sc.start_passage = set(ctx.passage_names), ctx.start_passage
        sc.css_text = ctx.css_text
        sc.raw = text
        sc.passages = [scanner.Passage('', T('(your text)'), [], scanner.COMMENT_RE.sub(scanner._blank, text), text, 1)]
        for fn in (sc.check_passages, sc.check_macros, sc.check_logic, sc.check_html, sc.check_css_classes, sc.check_text):
            try:
                fn()
            except Exception:
                pass
        skip = ('is never linked', 'is only linked', 'is empty', 'is never used', 'is never jumped', 'has no style rule')
        out = [i for i in sc.issues if not any(s in i.title for s in skip)]
        if not whole:
            out = [i for i in out if not re.search(r'is never closed|has no opening|opened more times|closed more times|is outside its', i.title)]
        known = res.known_vars
        for m in re.finditer(r'(?<![\w$])\$([A-Za-z_]\w*)', text):
            v = m.group(1)
            if v not in known:
                near = scanner._typo_of(v, sorted(known))
                out.append(scanner.Issue(scanner.WARNING if near else scanner.INFO, 'Variables', f'${v} is used but never set', 1, '',
                                         snippet=text, what=(f'${v} is not used anywhere else in the game. It looks like a misspelling of ${near}.'
                                                             if near else f'${v} is not used anywhere else in the game.'),
                                         fix=f'Rename ${v} to ${near} everywhere it appears.' if near else ''))
        tmp = scanner.ScanResult([], {'format': ctx.format}, text.split('\n'), res.html_path, res.game_dir)
        for i in out:
            fixes.enrich_issue(i, tmp)
        return out

    def open_compare(self):
        if not self.result:
            messagebox.showinfo(APP_NAME, T('Open a game first.'))
            return
        D.CompareDialog(self)

    def open_bug_report(self, only=None):
        if not self.result:
            messagebox.showinfo(APP_NAME, T('Open a game first.'))
            return
        D.BugReportDialog(self, only)

    def bug_report_text(self, items, style, with_fix):
        res = self.result
        name = os.path.splitext(os.path.basename(res.html_path))[0]
        h1 = {'plain': lambda s: s.upper(), 'discord': lambda s: f'**{s}**', 'bbcode': lambda s: f'[b]{s}[/b]'}[style]
        code = {'plain': lambda s: '    ' + s, 'discord': lambda s: f'`{s}`', 'bbcode': lambda s: f'[code]{s}[/code]'}[style]
        lines = [h1(T('Bug report for {game}', game=name)), T('Found with {app}. {n} problem(s).', app=f'{APP_NAME} {APP_VERSION}', n=len(items)), '']
        for k, i in enumerate(sorted(items, key=lambda x: (scanner.SEVERITY_ORDER[x.severity], x.passage, x.line)), 1):
            d = simple.simplify(i)
            lines.append(h1(f'{k}. {d["title"]}'))
            where = [T('Scene: {name}', name=i.passage)] if i.passage else []
            if i.line:
                where.append(T('Line {n} of {file}', n=i.line, file=os.path.basename(i.file or res.html_path)))
            if where:
                lines.append(' · '.join(where))
            if d.get('effect'):
                lines.append(T('What happens: {text}', text=d['effect']))
            lines.append(T('Technical: {text}', text=i.title))
            if with_fix:
                ff = fixes.forms(i, res, 'twine') if (i.old or i.new) else None
                if ff:
                    lines.append(T('Suggested fix (in Twine):'))
                    lines.append(code(ff[0].strip()) + '  →  ' + code(ff[1].strip() or T('(delete)')))
                elif i.fix:
                    lines.append(T('How to fix: {text}', text=i.fix.split('\n')[0]))
            lines.append('')
        return '\n'.join(lines)

    def open_wizard(self):
        if self.result:
            self.wizard = D.WizardWindow(self)

    def wizard_candidates(self):
        act = [i for i in self.active_issues() if i.severity != scanner.INFO or i.difficulty == 'easy']
        return sorted(act, key=lambda i: (-i.priority, i.line))

    def open_lessons(self):
        D.LessonsWindow(self)
        return 'break'

    def open_glossary(self):
        D.GlossaryWindow(self)

    def start_tour(self):
        if self.tour:
            self.tour.hide()
        self.nb.select(self.tab_problems)
        self.tour = D.Tour(self)

    def about(self):
        D.InfoWindow(self, T('About'), T('Version {v}. Finds problems in games and explains how to fix them. It only reads files and never '
                                         'changes your game.', v=APP_VERSION) + '\n\n' +
                     T('Translations other than English were made by an AI and may contain mistakes. Technical details stay in English, '
                       'like the game engines and their manuals.') + '\n\n' + T('Settings file: {path}', path=SETTINGS_FILE), APP_NAME)

    def read_aloud(self):
        if speech.speaking():
            speech.stop()
            return
        text = self.text.get('1.0', 'end').strip()
        if text:
            speech.speak(text)

    def toggle_watch(self):
        self.watch_on = self.watch_var.get()
        self._mtime = self.target_mtime()
        self.update_mode_widgets()
        self.set_status(T('Watching the game file. Every time you save it, it is checked again.') if self.watch_on
                        else T('Stopped watching the game file.'))

    def watch_tick(self):
        try:
            if self.watch_on and self.result and not self.scanning and not self.playtesting:
                m = self.target_mtime()
                if m and self._mtime and m != self._mtime:
                    self._mtime = m
                    self.start_scan(reason='watch')
        finally:
            self.root.after(1500, self.watch_tick)

    def show_reach(self):
        i = self.selected_issue()
        if not i or not self.is_twine:
            return
        D.InfoWindow(self, T('How do players reach this scene?'), reach.describe(self.result, i.passage, self.simple_on),
                     T('How do players reach "{name}"?', name=i.passage))

    # ================================================================ play-test
    def toggle_playtest(self):
        if self.playtesting:
            self._pt_stop = True
            self.set_status(T('Stopping the play-test…'))
            return
        if not self.result:
            return
        import playtest
        if not playtest.find_browser():
            messagebox.showinfo(APP_NAME, T('Play-testing needs Microsoft Edge or Google Chrome, and neither was found.'))
            return
        self.playtesting = True
        self._pt_stop = False
        self.update_buttons()
        res = self.result

        def work():
            try:
                issues, summary = playtest.run(res, progress=lambda p, m: self.q.put(('progress', p, m)), stop=lambda: self._pt_stop)
                self.q.put(('pt_done', res, issues, summary))
            except Exception as e:
                self.q.put(('pt_error', e))
        threading.Thread(target=work, daemon=True).start()

    def playtest_done(self, res, issues, summary):
        self.playtesting = False
        if res is not self.result:
            self.update_buttons()
            self.set_status(T('The game changed during the play-test. Run it again.'))
            return
        res.issues = [i for i in res.issues if i.category != 'Play-test']
        scanner.mark_unreachable(issues, res.unreachable)
        for i in issues:
            fixes.enrich_issue(i, res)
        res.issues += issues
        from collections import Counter
        c = Counter(i.severity for i in res.issues)
        res.stats.update(errors=c[scanner.ERROR], warnings=c[scanner.WARNING], infos=c[scanner.INFO])
        sel = self.selected_issue()
        self.after_result(keep_key=sel.key if sel else None)
        self.last_playtest = (issues, summary)
        msg = T('Play-test finished: {n} scenes played {m} ways, {k} problem(s) found while playing.',
                n=summary.get('passages', 0), m=len(summary.get('modes', [])), k=len(issues))
        if not self.selected_issue():
            self.show_summary()
        self.set_status(msg)

    # ================================================================ full scan
    def toggle_fullscan(self):
        import fullscan
        import playtest
        if self.fullscanning:
            self._fs_stop = True
            self.set_status(T('Pausing the Full scan after this group of scenes…'))
            return
        res = self.result
        if not res or not fullscan.can_play(res):
            return
        if not playtest.find_browser():
            messagebox.showinfo(APP_NAME, T('The Full scan needs Microsoft Edge or Google Chrome, and neither was found.'))
            return
        state = fullscan.load_state(res)
        if state and not state.get('finished') and messagebox.askyesno(
                APP_NAME, T('A Full scan of this game was paused at {pct}%. Continue it?',
                            pct=int(100 * fullscan.share_done(res, state)))):
            self.start_fullscan(state)
            return
        n, b, low, high = fullscan.estimate(res)
        if messagebox.askyesno(APP_NAME, T(
                'The Full scan plays every scene of the game with many different values, until every "if" part has '
                'been shown, and clicks the buttons in it. Every problem it reports is proven by playing.\n\n'
                'This game has {n} scenes with about {b} "if" parts. That should take about {low} to {high} minutes. '
                'You can pause at any time and continue later, even after closing Game Doctor. Windows will not go to '
                'sleep while it runs.\n\nStart the Full scan?', n=n, b=b, low=low, high=high)):
            fullscan.clear_state(res)
            self.start_fullscan({})

    def start_fullscan(self, state):
        import fullscan
        self.fullscanning = True
        self._fs_stop = False
        self.update_buttons()
        res = self.result

        def work():
            awake = getattr(getattr(ctypes, 'windll', None), 'kernel32', None)
            if awake:   # keep Windows awake (not the screen) while playing
                awake.SetThreadExecutionState(0x80000000 | 0x00000001)
            try:
                fullscan.run(res, progress=lambda p, m: self.q.put(('progress', p, m)), stop=lambda: self._fs_stop,
                             state=state, on_save=lambda st: fullscan.save_state(res, st))
                fullscan.save_state(res, state)
                self.q.put(('fs_done', res, state))
            except Exception as e:
                self.q.put(('fs_error', e))
            finally:
                if awake:
                    awake.SetThreadExecutionState(0x80000000)
        threading.Thread(target=work, daemon=True).start()

    def apply_fullscan(self, res, state):
        """Lay the Quick scan's findings next to what the Full scan showed (also for a paused one: what it has
        played is proven). The Quick scan's own list is kept, so this can be done again."""
        import copy
        import fullscan
        if not hasattr(res, 'quick_issues'):
            res.quick_issues = [i for i in res.issues if i.category != 'Play-test']
        issues, summary = fullscan.merge_quick(res, copy.deepcopy(res.quick_issues), state)
        played = [i for i in issues if i.category == 'Play-test']
        scanner.mark_unreachable(played, res.unreachable)
        for i in played:
            fixes.enrich_issue(i, res)
        res.issues = issues
        from collections import Counter
        c = Counter(i.severity for i in res.issues)
        res.stats.update(errors=c[scanner.ERROR], warnings=c[scanner.WARNING], infos=c[scanner.INFO])
        self.last_fullscan = summary
        return summary

    def fullscan_done(self, res, state):
        import fullscan
        self.fullscanning = False
        if res is not self.result:
            self.update_buttons()
            self.set_status(T('The game changed during the Full scan. What was done so far is kept.'))
            return
        summary = self.apply_fullscan(res, state)
        sel = self.selected_issue()
        self.after_result(keep_key=sel.key if sel else None)
        if not self.selected_issue():
            self.show_summary()
        confirmed = sum(1 for i in res.issues if i.severity == scanner.ERROR and 'Confirmed while playing' in i.what)
        if state.get('finished'):
            self.set_status(T('Full scan finished: {n} scenes, {b} of {t} "if" parts played, {c} problem(s) confirmed '
                              'while playing.', n=summary['passages'], b=summary['covered'], t=summary['branches'],
                              c=confirmed))
        else:
            self.set_status(T('Full scan paused at {pct}%. Click "Full scan…" to continue, also after closing Game Doctor.',
                              pct=int(100 * fullscan.share_done(res, state))))

    def saved_fullscan(self, res, fresh):
        """After a scan: show an earlier finished Full scan of the same unchanged game again; offer to continue a
        paused one when the game was just opened."""
        import fullscan
        if not fullscan.can_play(res):
            return
        state = fullscan.load_state(res)
        if not state:
            return
        if state.get('finished'):
            self.apply_fullscan(res, state)
        elif fresh:
            def ask():
                if res is self.result and not self.fullscanning and messagebox.askyesno(
                        APP_NAME, T('A Full scan of this game was paused at {pct}%. Continue it?',
                                    pct=int(100 * fullscan.share_done(res, state)))):
                    self.start_fullscan(state)
            self.root.after(300, ask)

    # ================================================================ code view
    def on_tab(self):
        if self.nb.select() == str(self.tab_code) and self.result and self.code_built_for != self.code_key():
            self.root.after(10, self.build_code_view)

    def fill_code_files(self):
        res = self.result
        count = lambda p: sum(1 for i in res.issues if i.line and norm(i.file or res.html_path) == norm(p))
        files = [res.html_path] + sorted(res.files, key=lambda p: (-count(p), p.lower()))
        self.codefile_map = {}
        for p in files:
            label = os.path.relpath(p, res.game_dir) if norm(p).startswith(norm(res.game_dir)) else os.path.basename(p)
            self.codefile_map[f'{label}  ({count(p)})'] = p
        self.codefile_box.configure(values=list(self.codefile_map))
        self.codefile_var.set(next(iter(self.codefile_map), ''))
        self.raw_var.set(False)
        if res.engine == 'twine':
            self.raw_check.pack(side='left', padx=(12, 0))
        else:
            self.raw_check.pack_forget()

    def code_path(self):
        return self.codefile_map.get(self.codefile_var.get(), self.result.html_path if self.result else '')

    def code_key(self):
        return (id(self.result), self.code_path(), self.raw_var.get(), self.colorblind, self.simple_on, len(self.ignored_keys()))

    def code_lines(self):
        path = self.code_path()
        if self.raw_var.get() and self.result.engine == 'twine' and norm(path) == norm(self.result.html_path):
            return self.result.raw_lines
        return self.result.lines_of(path)

    def code_placeholder(self):
        self.code_built_for = None
        self.code.delete('1.0', 'end')
        self.code.insert('end', '\n   ' + (T('Open a game to see its code here.') if not self.target else T('Scanning…')) + '\n')
        self.overview.delete('all')
        names = self.legend_names()
        for key in self.legend:
            self.legend[key].configure(text=f'■ {names[key]}')

    def legend_names(self):
        if self.simple_on:
            return {'clean': T('No problems'), 'info': T('Worth checking'), 'warning': T('Probably broken'), 'error': T('Broken')}
        return {'clean': T('Clean'), 'info': T('Notes'), 'warning': T('Warnings'), 'error': T('Errors')}

    def build_code_view(self, force=False):
        res = self.result
        if not res or (self.code_built_for == self.code_key() and not force):
            return
        self.set_status(T('Preparing the code view…'))
        self.root.config(cursor='watch')
        self.root.update_idletasks()
        path = self.code_path()
        lines = self.code_lines()
        self.line_issues = {}
        ig = self.ignored_keys()
        for n, i in enumerate(res.issues):
            if i.line and norm(i.file or res.html_path) == norm(path) and i.key not in ig:
                self.line_issues.setdefault(i.line, []).append(n)
        self.problem_lines = sorted(self.line_issues)
        width = len(str(len(lines)))
        worst = {ln: min(scanner.SEVERITY_ORDER[res.issues[n].severity] for n in idxs) for ln, idxs in self.line_issues.items()}
        out, truncated = [], []
        for n, text_ in enumerate(lines, 1):
            text_ = text_.rstrip('\r')
            if len(text_) > CODE_LINE_LIMIT:
                truncated.append((n, CODE_LINE_LIMIT))
                text_ = text_[:CODE_LINE_LIMIT] + '  … ' + T('({n} more characters on this line)', n=f'{len(text_) - CODE_LINE_LIMIT:,}')
            out.append(f'{n:>{width}} │ {text_}')
        c = self.code
        c.delete('1.0', 'end')
        c.insert('1.0', '\n'.join(out))
        keys = {0: 'error', 1: 'warning', 2: 'info'}
        groups = {'clean': [], 'error': [], 'warning': [], 'info': []}
        for n in range(1, len(lines) + 1):
            groups[keys[worst[n]] if n in worst else 'clean'].append(n)
        ncol = f'{width + 2}'
        for key, ns in groups.items():
            for k in range(0, len(ns), 800):
                ranges = []
                for n in ns[k:k + 800]:
                    ranges += [f'{n}.0', f'{n}.{ncol}']
                if ranges:
                    c.tag_add('n_' + key, *ranges)
                if key != 'clean':
                    ranges = []
                    for n in ns[k:k + 800]:
                        ranges += [f'{n}.{ncol}', f'{n}.end']
                    if ranges:
                        c.tag_add('l_' + key, *ranges)
        for n, limit in truncated:
            c.tag_add('n_trunc', f'{n}.{int(ncol) + 1 + limit}', f'{n}.end')
        names = self.legend_names()
        for key, lbl in self.legend.items():
            lbl.configure(text='■ ' + T('{name} ({n} lines)', name=names[key], n=f'{len(groups[key]):,}'))
        self._worst = worst
        self._code_total = len(lines)
        self.code_built_for = self.code_key()
        self.draw_overview()
        self.root.config(cursor='')
        self.update_counts()

    def draw_overview(self):
        cv = self.overview
        cv.delete('all')
        if not self.result or self.code_built_for != self.code_key():
            return
        h, w = max(1, cv.winfo_height()), max(1, cv.winfo_width())
        total = max(1, self._code_total)
        keys = {0: 'error', 1: 'warning', 2: 'info'}
        for level in (2, 1, 0):
            color = self.colors[keys[level]]
            for ln, wv in self._worst.items():
                if wv == level:
                    y = int((ln - 1) / total * h)
                    cv.create_rectangle(2, y, w - 2, y + 2, fill=color, outline='')
        self.draw_overview_view(*self.code.yview())

    def draw_overview_view(self, a, b):
        if not hasattr(self, 'colors'):
            return
        cv = self.overview
        h, w = max(1, cv.winfo_height()), max(1, cv.winfo_width())
        cv.delete('view')
        cv.create_rectangle(0, float(a) * h, w - 1, max(float(a) * h + 3, float(b) * h), outline=self.colors['muted'], tags=('view',))

    def overview_click(self, e):
        if self.result:
            self.code.yview_moveto(min(1, max(0, e.y / max(1, self.overview.winfo_height()))))

    def code_show_line(self, line):
        if self.code_built_for != self.code_key():
            self.build_code_view()
        c = self.code
        c.tag_remove('current', '1.0', 'end')
        c.tag_add('current', f'{line}.0', f'{line}.end')
        c.yview(f'{max(1, line - 8)}.0')
        c.mark_set('insert', f'{line}.0')
        self.describe_code_line(line)

    def describe_code_line(self, line):
        idxs = self.line_issues.get(line, [])
        if not idxs:
            self.set_status(T('Line {n}: no problems found', n=line))
            return
        titles = [self.title_of(self.result.issues[n]) for n in idxs]
        extra = f' (+{len(titles) - 2})' if len(titles) > 2 else ''
        self.set_status(T('Line {n}:', n=line) + ' ' + ' | '.join(titles[:2]) + extra + '  - ' + T('double-click to see details'))

    def code_clicked(self):
        if not self.result or self.code_built_for != self.code_key():
            return
        line = int(self.code.index('insert').split('.')[0])
        self.code.tag_remove('current', '1.0', 'end')
        self.code.tag_add('current', f'{line}.0', f'{line}.end')
        self.describe_code_line(line)

    def code_open_problem(self):
        if not self.result:
            return
        line = int(self.code.index('insert').split('.')[0])
        idxs = self.line_issues.get(line)
        if idxs:
            self.select_issue(self.result.issues[idxs[0]])
        return 'break'

    def code_jump(self, direction):
        if not self.result:
            return
        if self.code_built_for != self.code_key():
            self.build_code_view()
        if not self.problem_lines:
            return
        cur = int(self.code.index('insert').split('.')[0])
        if direction > 0:
            nxt = next((l for l in self.problem_lines if l > cur), self.problem_lines[0])
        else:
            nxt = next((l for l in reversed(self.problem_lines) if l < cur), self.problem_lines[-1])
        self.code_show_line(nxt)

    def code_goto(self):
        try:
            line = int(self.goto_var.get().strip())
        except ValueError:
            return
        if self.result:
            if self.code_built_for != self.code_key():
                self.build_code_view()
            self.code_show_line(max(1, min(line, self._code_total)))

    def code_find_next(self):
        if self.nb.select() != str(self.tab_code) or not self.result:
            return
        q = self.find_var.get()
        if not q:
            return
        c = self.code
        pos = c.search(q, c.index('insert +1c'), stopindex='end', nocase=True) or c.search(q, '1.0', stopindex='end', nocase=True)
        if not pos:
            self.set_status(T('"{text}" not found', text=q))
            return
        c.tag_remove('found', '1.0', 'end')
        c.tag_add('found', pos, f'{pos}+{len(q)}c')
        c.mark_set('insert', pos)
        c.see(pos)
        self.describe_code_line(int(pos.split('.')[0]))

    def show_in_code(self):
        i = self.selected_issue()
        if i and i.line:
            target = norm(i.file or self.result.html_path)
            for label, p in self.codefile_map.items():
                if norm(p) == target:
                    self.codefile_var.set(label)
            self.nb.select(self.tab_code)
            self.root.after(20, lambda: self.code_show_line(i.line))

    # ================================================================ export
    def export(self):
        if not self.result or self.scanning:
            return
        items = self.filtered()
        base = os.path.splitext(os.path.basename(self.result.html_path))[0] + ' - bug report'
        f = filedialog.asksaveasfilename(title=T('Export the {n} problems shown in the list', n=len(items)),
                                         initialdir=os.path.dirname(self.result.html_path), initialfile=base, defaultextension='.html',
                                         filetypes=[(T('Web page report'), '*.html'), (T('Spreadsheet (CSV)'), '*.csv'), (T('Text file'), '*.txt')])
        if not f:
            return
        try:
            write_report(f, self, items)
            self.set_status(T('Report saved: {path}', path=f))
            if not self.settings.get('_selftest') and messagebox.askyesno(APP_NAME, T('Report saved. Open it now?')):
                os.startfile(f)
        except OSError as e:
            messagebox.showerror(APP_NAME, T('Could not save the report:') + f'\n{e}')

    # ================================================================ AI helper
    def ai_config(self):
        """The chosen AI service with its key, address and model."""
        st = self.settings.get('ai', {})
        name = st.get('provider', ai.DEFAULT_PROVIDER)
        if name not in ai.PROVIDERS:
            name = ai.DEFAULT_PROVIDER
        p = ai.PROVIDERS[name]
        return dict(p, name=name, key=ai.unprotect_key(st.get('keys', {}).get(name, '')),
                    url=st.get('urls', {}).get(name, p['url']), model=st.get('models', {}).get(name, p['model']))

    def ai_log_text(self):
        act = self.active_issues()
        ig = [i for i in self.result.issues if i not in act]
        return ai.build_log(APP_VERSION, self.result, act, ig, getattr(self, 'last_playtest', None), self.display_health())

    def export_ai_log(self):
        AID.export_log(self)

    def open_ai_fix(self):
        AID.open_fix(self)

    # ================================================================ close
    def on_close(self):
        speech.stop()
        maximized = self.root.state() == 'zoomed'
        if not maximized:
            self.settings['geometry'] = self.root.geometry()
        rev = {v: k for k, v in self.group_names.items()}
        self.settings.update(scale=self.scale, theme=self.theme_choice, maximized=maximized, group_by=rev.get(self.group_var.get(), 'Type'),
                             simple_mode=self.simple_on, audience=self.audience, edit_mode=self.edit_mode, colorblind=self.colorblind,
                             watch=self.watch_on, **{'show_' + s: v.get() for s, v in self.sev_vars.items()})
        self.save_settings()
        self.root.destroy()


# ---------------------------------------------------------------------- reports
def write_report(f, app, items):
    ext = os.path.splitext(f)[1].lower()
    res = app.result
    score, label = app.display_health()
    if ext == '.csv':
        with open(f, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow([T('Severity'), T('Kind'), T('Problem'), T('Problem (simple words)'), T('Scene / place'), T('File'), T('Line'),
                        T('Code'), T('What is wrong'), T('What players will notice'), T('How to fix'), T('Change to')])
            for i in items:
                d = simple.simplify(i)
                ff = fixes.forms(i, res, 'file') if (i.old or i.new) else None
                w.writerow([i.severity, i.category, i.title, d['title'], i.passage, os.path.basename(i.file or res.html_path),
                            i.line or '', i.snippet, i.what, d.get('effect', ''), i.fix, ff[1] if ff else ''])
    elif ext == '.txt':
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(f'{APP_NAME} - {res.html_path}\n' + T('Code health: {n}% ({label})', n=score, label=T(label)) + '\n\n')
            fh.write('\n\n'.join(app.issue_text(i) for i in items))
    else:
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(html_report(res, items, app.simple_on, (score, label)))


def html_report(res, items, simple_on=False, health=None):
    st = res.stats
    e = html.escape
    score, label = health or (st['health'], st['health_label'])
    rows = []
    groups = {}
    for i in items:
        groups.setdefault(simple.category(i.category) if simple_on else i.category, []).append(i)
    order = sorted(groups, key=lambda g: (min(scanner.SEVERITY_ORDER[i.severity] for i in groups[g]), g))
    sev_word = (lambda s: simple.severity(s)) if simple_on else (lambda s: T({scanner.ERROR: 'Error', scanner.WARNING: 'Warning', scanner.INFO: 'Note'}[s]))
    for g in order:
        rows.append(f'<h2>{e(g)} <span class="n">{len(groups[g])}</span></h2>')
        for i in groups[g]:
            d = simple.simplify(i)
            where = ' · '.join(x for x in [f'“{e(i.passage)}”' if i.passage else '', T('line {n}', n=i.line) if i.line else ''] if x) or T('Whole game')
            title = d['title'] if simple_on else i.title
            what = (d['what'] or i.what) if simple_on else i.what
            body = (f'<pre>{e(i.snippet)}</pre>' if i.snippet else '') + (f'<p><b>{e(T("What is wrong"))}:</b> {e(what)}</p>' if what else '')
            if simple_on and d.get('effect'):
                body += f'<p><b>{e(T("What players will notice"))}:</b> {e(d["effect"])}</p>'
            fix = d['fix'] if simple_on else i.fix
            if fix:
                body += f'<p><b>{e(T("How to fix"))}:</b> {e(fix).replace(chr(10), "<br>")}</p>'
            ff = fixes.forms(i, res, 'file') if (i.old or i.new) else None
            if ff:
                body += f'<p><b>{e(T("Change to"))}:</b></p><pre class="after">{e(ff[1]) or e(T("(delete this line)"))}</pre>'
            rows.append(f'<details class="{i.severity.lower()}"><summary><span class="sev">{e(sev_word(i.severity))}</span>'
                        f'<span class="t">{e(title)}</span><span class="w">{where}</span></summary>{body}</details>')
    hvar = {'clean': '--ok', 'warning': '--warn', 'error': '--err'}[health_color_key(score)]
    count = lambda s: sum(1 for i in items if i.severity == s)
    return f'''<!doctype html><html lang="{i18n.current()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(T("Bug report"))} - {e(os.path.basename(res.html_path))}</title>
<style>
:root{{--bg:#f6f7f9;--panel:#fff;--fg:#1d2330;--muted:#5d6675;--border:#d5d9e0;--err:#c62828;--warn:#a86400;--info:#2f6fde;--ok:#2e8b46;--code:#f1f3f6;--okbg:#e6f4ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1e24;--panel:#22262e;--fg:#e3e6eb;--muted:#9aa3b1;--border:#363c47;--err:#ff6b6b;--warn:#f0c04a;--info:#7aa7ff;--ok:#5fcf7f;--code:#171a1f;--okbg:#1e3a27}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "Segoe UI",system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}} h1{{margin:0 0 4px}} .meta{{color:var(--muted)}}
.counts{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}} .counts div{{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 16px}}
.counts b{{font-size:22px;display:block}} h2{{margin:28px 0 8px;font-size:18px}} .n{{color:var(--muted);font-weight:normal}}
details{{background:var(--panel);border:1px solid var(--border);border-left:4px solid var(--info);border-radius:6px;margin:6px 0;padding:6px 12px}}
details.error{{border-left-color:var(--err)}} details.warning{{border-left-color:var(--warn)}}
summary{{cursor:pointer;display:flex;gap:10px;flex-wrap:wrap;align-items:baseline}} .sev{{font-weight:600;min-width:64px}}
.error .sev{{color:var(--err)}} .warning .sev{{color:var(--warn)}} .info .sev{{color:var(--info)}} .t{{flex:1;min-width:200px}} .w{{color:var(--muted);font-size:13px}}
pre{{background:var(--code);padding:8px;border-radius:4px;white-space:pre-wrap;word-break:break-word;font:13px Consolas,monospace}} pre.after{{background:var(--okbg)}}
</style></head><body><main>
<h1>{e(T("Bug report"))}</h1><div class="meta">{e(res.html_path)}<br>{e(st["format"])} · {APP_NAME} {APP_VERSION}</div>
<div class="counts"><div><b style="color:var({hvar})">{score}%</b>{e(T("Code health"))} · {e(T(label))}</div>
<div><b style="color:var(--err)">{count(scanner.ERROR)}</b>{e(sev_word(scanner.ERROR))}</div>
<div><b style="color:var(--warn)">{count(scanner.WARNING)}</b>{e(sev_word(scanner.WARNING))}</div>
<div><b style="color:var(--info)">{count(scanner.INFO)}</b>{e(sev_word(scanner.INFO))}</div></div>
{''.join(rows)}
</main></body></html>'''


def main():
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GameDoctor.App')
        except Exception:
            pass
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--selftest' in sys.argv:
        import selftest
        sys.exit(selftest.main(args))
    if '--report' in sys.argv:
        src = args[0]
        out = args[1] if len(args) > 1 else os.path.splitext(src)[0] + ' - bug report.html'
        res = scanner.scan_any(src)
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(html_report(res, res.issues, '--simple' in sys.argv))
        return
    root = tk.Tk()
    App(root, args[0] if args else None)
    root.mainloop()


if __name__ == '__main__':
    main()
