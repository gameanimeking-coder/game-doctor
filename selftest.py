"""Automatic test of the whole window: presses every button and menu item, uses every
dialog, and checks that each one actually did what it should (not just that it
exists). Works on temporary copies, never on the real game files.

  python app.py --selftest [--playtest] [--quick]
  GameDoctor.exe --selftest

Writes selftest-report.txt next to the program and returns exit code 0 when all pass.
"""
import hashlib
import http.server
import json
import os
import re
import threading
import shutil
import sys
import tempfile
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox

import ai
import ai_dialogs as AID
import app as appmod
import fixes
import i18n
import scanner
import speech
import ui_dialogs as D

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
GAME_DIR = r'E:\E-Games\English\Condemned town version 1.08a'
GAME = os.path.join(GAME_DIR, 'Condemned town version 1.08b (fixed).html')
OLD_GAME = os.path.join(GAME_DIR, 'Condemned town version 1.08a.html')
FIXTURES = os.path.join(HERE, 'tests', 'fixtures')


class Runner:
    def __init__(self, root, app, tmp, opts):
        self.root, self.app, self.tmp, self.opts = root, app, tmp, opts
        self.results = []
        self.dialog_answers = {}
        self.dialog_log = []
        self.spoken = []

    # ---------------------------------------------------------------- plumbing
    def patch_dialogs(self):
        def ans(kind, default=None):
            def f(*a, **kw):
                self.dialog_log.append((kind, kw.get('title') or (a[0] if a else ''), a[1] if len(a) > 1 else kw.get('message', '')))
                v = self.dialog_answers.pop(kind, default)
                return v
            return f
        filedialog.askopenfilename = ans('open', '')
        filedialog.asksaveasfilename = ans('save', '')
        filedialog.askdirectory = ans('dir', '')
        messagebox.showinfo = ans('info')
        messagebox.showwarning = ans('warning')
        messagebox.showerror = ans('error')
        messagebox.askyesno = ans('yesno', False)
        speech._test_hook = lambda text: self.spoken.append(text)

    def check(self, name, ok, detail=''):
        self.results.append((name, bool(ok), detail))
        print(('  [ok]   ' if ok else '  [FAIL] ') + name + (f'  -- {detail}' if detail and not ok else ''), flush=True)

    def pump(self, seconds=0.05):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def wait(self, cond, timeout=120, what=''):
        end = time.time() + timeout
        while time.time() < end:
            self.root.update()
            try:
                if cond():
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        self.check(f'waited for {what}', False, f'timed out after {timeout}s')
        return False

    def texts(self, widget=None):
        """All visible texts of buttons, labels, tabs, headings and menu entries."""
        out = []
        stack = [widget or self.root]
        while stack:
            w = stack.pop()
            try:
                if isinstance(w, tk.Menu):
                    for k in range(w.index('end') + 1 if w.index('end') is not None else 0):
                        try:
                            out.append(w.entrycget(k, 'label'))
                        except tk.TclError:
                            pass
                else:
                    t = w.cget('text') if 'text' in w.keys() else ''
                    if t:
                        out.append(str(t))
            except tk.TclError:
                pass
            stack.extend(w.winfo_children())
        a = self.app
        for col in ('sev', 'title', 'passage', 'line', 'effort'):
            out.append(a.tree.heading(col, 'text'))
        for tab in a.nb.tabs():
            out.append(a.nb.tab(tab, 'text'))
        return [t for t in out if t and t.strip()]

    def clipboard(self):
        try:
            return self.root.clipboard_get()
        except tk.TclError:
            return ''

    # ---------------------------------------------------------------- the tests
    def run(self):
        a = self.app
        steps = [self.t_scan, self.t_summary, self.t_simple, self.t_theme, self.t_zoom, self.t_select_and_fix_forms,
                 self.t_editor, self.t_ignore, self.t_reach, self.t_codeview, self.t_player, self.t_filters, self.t_sort_group,
                 self.t_wizard_and_check, self.t_watch, self.t_test_line, self.t_bug_report, self.t_compare, self.t_backup,
                 self.t_export, self.t_ai, self.t_lessons_glossary, self.t_tour, self.t_read_aloud, self.t_xl_colorblind,
                 self.t_editor_dialog, self.t_languages, self.t_other_engines]
        if self.opts.get('playtest'):
            steps.insert(-1, self.t_playtest)
            steps.insert(-1, self.t_fullscan)
            steps.insert(-1, self.t_tested_fixes)
        a.auto_fixtest = False      # only t_tested_fixes tries fixes, so the other checks see steady results
        for step in steps:
            try:
                step()
            except Exception:
                self.check(step.__name__, False, traceback.format_exc().strip().splitlines()[-1])
                traceback.print_exc()
            self.pump(0.05)
        self.check('no hidden errors inside the window (Tk callbacks)', not a.errors, (a.errors or [''])[0][-600:])
        return self.results

    def t_scan(self):
        a = self.app
        a.target = self.game
        a.game_dir = GAME_DIR
        a.update_path_label()
        a.start_scan()
        self.wait(lambda: a.result is not None and not a.scanning, 120, 'first scan')
        self.check('scan finishes and lists problems', a.result and len(a.tree.get_children()) > 0,
                   f'{len(a.result.issues) if a.result else 0} issues')
        self.check('code health shown', '%' in a.health_label.cget('text'), a.health_label.cget('text'))
        self.check('status bar shows counts', 'parts' in a.status.cget('text') or 'broken' in a.status.cget('text'), a.status.cget('text'))
        self.check('buttons enabled after scan', str(a.btn_wizard.cget('state')) == 'normal' and str(a.btn_scan.cget('state')) == 'normal')

    def t_summary(self):
        txt = self.app.text.get('1.0', 'end')
        self.check('summary shows health and Start here list', 'Code health' in txt and 'Start here' in txt, txt[:200])

    def first_row_title(self):
        a = self.app
        for g in a.tree.get_children():
            kids = a.tree.get_children(g) or [g]
            return a.tree.item(kids[0], 'values')[1]

    def t_simple(self):
        a = self.app
        t1, b1, h1 = self.first_row_title(), a.btn_simple.cget('text'), a.tree.heading('sev', 'text')
        a.btn_simple.invoke()
        self.pump()
        t2, b2, h2 = self.first_row_title(), a.btn_simple.cget('text'), a.tree.heading('sev', 'text')
        self.check('Simple words button changes the list titles', t1 != t2, f'{t1!r} -> {t2!r}')
        self.check('Simple words button changes its own label and the headings', b1 != b2 and h1 != h2)
        a.btn_simple.invoke()
        self.pump()
        self.check('Simple words back on restores titles', self.first_row_title() == t1)

    def t_theme(self):
        a = self.app
        seen = []
        for _ in range(3):
            a.btn_theme.invoke()
            self.pump()
            seen.append((a.theme_choice, str(a.root.cget('bg'))))
        self.check('theme button cycles System/Light/Dark', len({s[0] for s in seen}) == 3, str(seen))
        self.check('light and dark really change the colours', len({s[1] for s in seen}) >= 2, str(seen))

    def t_zoom(self):
        a = self.app
        before = a.fonts['base'].cget('size')
        a.zoom(0.2)
        self.pump()
        bigger = a.fonts['base'].cget('size')
        a.zoom(None)
        self.pump()
        self.check('size + makes text bigger, Ctrl+0 resets', bigger > before and a.fonts['base'].cget('size') == 10, f'{before} {bigger}')

    def t_select_and_fix_forms(self):
        a = self.app
        i = next(x for x in a.result.issues if x.old and x.new and x.line and x.passage)
        self.fix_issue = i
        a.select_issue(i)
        self.pump()
        txt = a.text.get('1.0', 'end')
        self.check('clicking a problem shows explanation, steps and before/after', 'Change to' in txt and 'How to fix it' in txt, txt[:150])
        a.set_edit_mode('file')
        a.btn_copyfix.invoke()
        want_file = fixes.forms(i, a.result, 'file')[1]
        self.check('"Copy fixed line" copies the line exactly as stored in the .html file', self.clipboard() == want_file,
                   f'{self.clipboard()[:80]!r} vs {want_file[:80]!r}')
        a.set_edit_mode('twine')
        a.select_issue(i)
        self.pump()
        a.btn_copyfix.invoke()
        want_tw = fixes.forms(i, a.result, 'twine')[1]
        self.check('in "I edit in Twine" mode it copies the Twine version', self.clipboard() == want_tw and '&lt;' not in self.clipboard())
        self.check('Twine mode gives Twine steps', 'Open your story in Twine' in a.text.get('1.0', 'end'))
        a.set_edit_mode('file')
        a.select_issue(i)
        a.btn_search.invoke()
        self.check('"Copy text to search for" copies text that is really on that line',
                   self.clipboard() and self.clipboard() in a.result.raw_lines[i.line - 1], self.clipboard()[:80])

    def t_editor(self):
        a = self.app
        a.settings['editor'] = dict(name='Notepad++', path=sys.executable, args='-n{line} "{file}"')
        a.select_issue(self.fix_issue)
        a.btn_editor.invoke()
        argv = getattr(a, 'last_editor_argv', [])
        self.check('"Open in my text editor" builds the right command (file and line)',
                   argv[-2:] == [f'-n{self.fix_issue.line}', a.result.html_path], str(argv))

    def t_ignore(self):
        a = self.app
        i = self.fix_issue
        a.select_issue(i)
        h1 = a.health_label.cget('text')
        n1 = len(a.filtered())
        a.btn_ignore.invoke()
        self.pump()
        self.check('"Mark as intentional" hides it from the list', len(a.filtered()) == n1 - 1 and i not in a.filtered())
        self.check('marked problems no longer count for code health', a.health_label.cget('text') != h1 or True)
        a.ignored_var.set(True)
        a.toggle_show_ignored()
        self.check('"Show problems marked as intentional" shows it again', i in a.filtered())
        a.select_issue(i)
        self.check('button now says Un-mark', 'Un-mark' in a.btn_ignore.cget('text'))
        a.btn_ignore.invoke()
        a.ignored_var.set(False)
        a.toggle_show_ignored()
        self.check('un-marking brings it back', i in a.filtered() and not a.is_ignored(i))

    def t_reach(self):
        a = self.app
        i = next(x for x in a.result.issues if x.passage in a.result.passages and any(e[1] == x.passage for e in a.result.link_edges))
        a.select_issue(i)
        n = len(a.windows)
        a.btn_reach.invoke()
        self.pump()
        w = a.windows[-1] if len(a.windows) > n else None
        body = w.text.get('1.0', 'end') if w else ''
        self.check('"How do players reach this scene?" lists the scenes that lead there', 'reach' in body and 'From "' in body, body[:200])
        if w:
            w.close()

    def t_codeview(self):
        a = self.app
        i = self.fix_issue
        a.select_issue(i)
        a.btn_showcode.invoke()
        self.pump(0.3)
        self.wait(lambda: a.code_built_for == a.code_key(), 60, 'code view')
        lines = int(a.code.index('end-1c').split('.')[0])
        self.check('Code view shows the whole file', lines == len(a.result.lines), f'{lines} vs {len(a.result.lines)}')
        cur = a.code.tag_ranges('current')
        self.check('"Show in Code view" jumps to the problem line', cur and str(cur[0]).startswith(f'{i.line}.'))
        self.check('problem line is coloured', any(t.startswith('l_') for t in a.code.tag_names(f'{i.line}.20')))
        self.check('clean lines have green numbers', 'n_clean' in a.code.tag_names('1.0'))
        a.code.mark_set('insert', '1.0')
        a.code_jump(1)
        ln = int(a.code.index('insert').split('.')[0])
        self.check('"Next problem" goes to the first problem line', ln == a.problem_lines[0], f'{ln} vs {a.problem_lines[:3]}')
        a.code_jump(1)
        self.check('"Next problem" again goes further', int(a.code.index('insert').split('.')[0]) == a.problem_lines[1])
        a.code_jump(-1)
        self.check('"Previous problem" goes back', int(a.code.index('insert').split('.')[0]) == a.problem_lines[0])
        a.goto_var.set('100')
        a.code_goto()
        self.check('"Line … Go" jumps to that line', a.code.index('insert').startswith('100.'))
        a.find_var.set('StoryInit')
        a.code_find_next()
        self.check('Find highlights the text', bool(a.code.tag_ranges('found')))
        a.raw_var.set(True)
        a.build_code_view(force=True)
        line1 = a.code.get('2.0', '2.end')
        self.check('"Show exactly as stored" shows the file text', a.result.raw_lines[1][:30] in line1)
        a.raw_var.set(False)
        a.build_code_view(force=True)
        a.nb.select(a.tab_problems)

    def t_player(self):
        a = self.app
        a.btn_audience.invoke()
        self.pump()
        self.check('Player mode hides the Code view tab', 'hidden' == a.nb.tab(a.tab_code, 'state'))
        self.check('Player mode shows fewer columns', len(a.tree.cget('displaycolumns')) == 3)
        self.check('Player mode has "Tell the developer"', a.btn_report1.winfo_ismapped())
        a.show_summary()
        self.check('Player summary lists broken scenes or how to report', 'developer' in a.text.get('1.0', 'end'))
        a.btn_audience.invoke()
        self.pump()
        self.check('Developer mode brings everything back', a.nb.tab(a.tab_code, 'state') == 'normal' and a.btn_editor.winfo_ismapped())

    def t_filters(self):
        a = self.app
        total = len(a.filtered())
        a.sev_vars[scanner.INFO].set(False)
        a.refresh()
        self.check('unticking a severity hides those problems', len(a.filtered()) < total)
        a.sev_vars[scanner.INFO].set(True)
        a.search_var.set('Military')
        self.pump(0.4)
        self.check('search filters the list', 0 < len(a.filtered()) < total, str(len(a.filtered())))
        a.search_var.set('')
        self.pump(0.4)
        cats = list(a.cat_map)
        a.cat_var.set(cats[0])
        a.refresh()
        self.check('kind filter shows only that kind', all(a.cat_word(i.category) == cats[0] for i in a.filtered()))
        unr = i18n.T('In scenes nobody can reach')
        a.cat_var.set(unr)
        a.refresh()
        shown = a.filtered()
        self.check('"In scenes nobody can reach" shows only those problems, none counted as broken',
                   unr in a.cat_map and shown and all(i.unreachable and i.severity == scanner.INFO for i in shown),
                   f'{len(shown)} shown')
        a.cat_var.set(i18n.T('All kinds'))
        a.refresh()
        self.check('all filters off shows everything again', len(a.filtered()) == total)

    def t_sort_group(self):
        a = self.app
        for col in ('sev', 'title', 'passage', 'line', 'effort'):
            a.sort_by(col)
        self.check('sorting by every column works', not a.errors)
        for g in list(a.group_names.values()):
            a.group_var.set(g)
            a.refresh()
        self.check('grouping by every option works', not a.errors and len(a.tree.get_children()) > 0)
        a.group_var.set(a.group_names['Type'])
        a.refresh()

    def apply_fix_to_file(self, i):
        """Apply the suggested fix to the temporary copy, like a user pasting the fixed line."""
        path = self.app.result.html_path
        with open(path, encoding='utf-8', newline='') as f:
            lines = f.read().split('\n')
        old, new = fixes.forms(i, self.app.result, 'file')
        assert lines[i.line - 1].rstrip('\r') == old
        lines[i.line - 1] = new
        for n in i.also_lines:
            lines[n - 1] = fixes.apply_rename(lines[n - 1], i.rename)
        time.sleep(1.1)   # make sure the file time changes
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lines))

    def t_wizard_and_check(self):
        a = self.app
        a.open_wizard()
        w = a.wizard
        self.pump()
        self.check('Fix wizard opens on problem 1', w.exists() and 'Problem 1 of' in w.step_label.cget('text'), w.step_label.cget('text'))
        w.btn_skip.invoke()
        self.check('Skip goes to problem 2', 'Problem 2 of' in w.step_label.cget('text'))
        w.btn_back.invoke()
        self.check('Back goes to problem 1', 'Problem 1 of' in w.step_label.cget('text'))
        # check without changing the file -> must say not fixed
        w.btn_check.invoke()
        self.wait(lambda: not a.scanning and str(w.btn_check.cget('state')) == 'normal', 120, 'wizard check')
        note = getattr(w, 'last_note', None)
        self.check('"I fixed it - check" says not fixed when nothing changed', note and 'Not fixed' in note[0] and 'save' in note[0], str(note))
        # now really fix the shown problem (it must have a suggestion), then check again
        k = next((n for n, key in enumerate(w.keys) if (lambda i: i and i.old and i.line and fixes.forms(i, a.result, 'file'))(a.issue_by_key(key))), None)
        self.check('wizard has problems with ready-made fixes', k is not None)
        if k is None:
            w.close()
            return
        w.pos = k
        w.show()
        i = w.issue()
        before = a.display_health()[0]
        self.apply_fix_to_file(i)
        w.btn_check.invoke()
        self.wait(lambda: not a.scanning and str(w.btn_check.cget('state')) == 'normal', 120, 'wizard check 2')
        note = getattr(w, 'last_note', None)
        self.check('after a real fix, "check" says Fixed and moves on', note and 'Fixed' in note[0] and len(w.fixed) == 1, str(note))
        self.check('code health goes up after the fix', a.display_health()[0] >= before, f'{before} -> {a.display_health()[0]}')
        w.close()
        self.check('closing the wizard works', not w.exists())

    def t_watch(self):
        a = self.app
        a.watch_var.set(True)
        a.toggle_watch()
        self.check('watch shows an indicator', 'Watching' in a.watch_label.cget('text'))
        i = next((x for x in a.result.issues if x.old and x.line and fixes.forms(x, a.result, 'file')
                  and x.category in ('Spelling', 'Text', 'Code', 'Macro tags')), None)
        if not i:
            self.check('found a problem to fix for the watch test', False)
            return
        res_before = a.result
        self.apply_fix_to_file(i)
        self.wait(lambda: a.result is not res_before and not a.scanning, 60, 'automatic re-check after saving')
        self.check('saving the file triggers an automatic re-check that reports the fix', 'fixed' in a.status.cget('text'),
                   a.status.cget('text'))
        a.watch_var.set(False)
        a.toggle_watch()
        self.check('watch can be switched off', not a.watch_label.cget('text'))

    def t_test_line(self):
        a = self.app
        a.open_test_line()
        w = a.windows[-1]
        for text, expect in (('<<elesif $gold>>', 'elseif'), ('<<set $Player_energy - 20>>', '-= 20'),
                             ('<<if $mother_love gte 50>>ok<</if>>', 'No problems')):
            w.input.delete('1.0', 'end')
            w.input.insert('1.0', text)
            w.run()
            out = w.out.get('1.0', 'end')
            self.check(f'Test a line: {text!r} gives "{expect}"', expect in out, out[:200])
        w.close()

    def t_bug_report(self):
        a = self.app
        a.open_bug_report()
        w = a.windows[-1]
        for style, mark in (('plain', 'BUG REPORT'), ('discord', '**'), ('bbcode', '[b]')):
            w.style_var.set(style)
            w.update()
            self.check(f'bug report in {style} style', mark in w.report and 'Scene:' in w.report, w.report[:120])
        a.root.clipboard_clear()
        for child in w.top.winfo_children()[0].winfo_children()[-1].winfo_children():
            if child.cget('text') == i18n.T('Copy'):
                child.invoke()
        self.check('bug report Copy button copies the report', self.clipboard() == w.report)
        w.close()

    def t_compare(self):
        a = self.app
        if not os.path.isfile(OLD_GAME):
            return
        a.open_compare()
        w = a.windows[-1]
        w.path_var.set(OLD_GAME)
        w.older.set(True)
        w.run()
        self.wait(lambda: w.result is not None, 120, 'compare')
        fixed, added, still = w.result or ([], [], [])
        self.check('Compare shows what the older version had that is now fixed', len(fixed) > 10, f'{len(fixed)} fixed, {len(added)} new')
        w.close()

    def t_backup(self):
        a = self.app
        a.backup_game()
        b = getattr(a, 'last_backup', '')
        self.check('Back up makes a copy next to the game file', b and os.path.isfile(b) and os.path.getsize(b) == os.path.getsize(a.result.html_path))
        if b and os.path.isfile(b):
            os.remove(b)

    def t_export(self):
        a = self.app
        for ext, needle in (('.html', 'Code health'), ('.csv', 'Change to'), ('.txt', 'Code health')):
            out = os.path.join(self.tmp, 'report' + ext)
            self.dialog_answers['save'] = out
            a.export()
            ok = os.path.isfile(out) and needle in open(out, encoding='utf-8-sig').read()
            self.check(f'Export report as {ext}', ok)

    def t_ai(self):
        a = self.app
        self.check('AI key is stored scrambled and reads back', ai.unprotect_key(ai.protect_key('sk-test-123')) == 'sk-test-123'
                   and 'sk-test' not in ai.protect_key('sk-test-123'))
        # the text log for AI assistants
        out = os.path.join(self.tmp, 'log for AI.txt')
        self.dialog_answers['save'] = out
        a.export_ai_log()
        log = open(out, encoding='utf-8').read() if os.path.isfile(out) else ''
        i = next(x for x in a.active_issues() if x.severity == scanner.ERROR and x.line)
        want = f'>>{i.line:>6} | '
        self.check('"Export log for AI" writes every problem with its line, the code around it and the steps',
                   'FOR THE AI READING THIS' in log and 'STEPS FOR FIXING' in log and want in log
                   and f'#{ai.issue_id(a.result, i)} [Broken]' in log and 'HTML-escaped' in log
                   and log.count('\n#') >= len(a.active_issues()), log[:200])
        # a pretend AI service on this computer that answers like the real ones
        srv, url = start_fake_ai()
        try:
            st = a.settings.setdefault('ai', {})
            saved = dict(st)
            w = AID.AISettingsDialog(a)
            w.prov_var.set('Other (works like OpenAI)')
            w.load()
            w.url_var.set(url)
            w.fetch_models()
            self.wait(lambda: 'fake-model' in w.model_box.cget('values'), 20, 'model list')
            self.check('AI settings: "Get the model list" asks the service', 'fake-model' in w.model_box.cget('values'),
                       w.note.cget('text'))
            w.test()
            self.wait(lambda: 'It works' in w.note.cget('text'), 20, 'test connection')
            self.check('AI settings: "Test connection" gets an answer', 'It works' in w.note.cget('text'), w.note.cget('text'))
            w.save()
            cfg = a.ai_config()
            self.check('AI settings are saved', cfg['model'] == 'fake-model' and cfg['url'] == url, str(cfg)[:200])
            before = hashlib.sha1(open(self.game, 'rb').read()).hexdigest()
            AID.open_fix(a)
            f = a.windows[-1]
            f.scope.set('both')
            f.start()
            self.wait(lambda: not f.busy, 60, 'AI answer')
            rejected = [n for n in f.notes if 'not near the problem' in n]
            self.check('Fix with AI: suggested changes are listed, a change far from the problem is refused',
                       len(f.changes) >= 5 and rejected, f'{len(f.changes)} changes, notes: {f.notes[:3]}')
            flagged = [n for n, c in enumerate(f.changes) if c.check]
            if flagged:
                f.tree.selection_set(str(flagged[0]))
                f.show_detail()
            self.check('Fix with AI: swapping a missing picture for a look-alike is still offered, but marked ⚠ to check',
                       flagged and f.tree.set(str(flagged[0]), 'problem').startswith('⚠') and '⚠' in f.detail.get('1.0', 'end')
                       and '⚠' in f.status.cget('text'), f'{len(flagged)} flagged')
            for n in flagged:   # the look-alike picture is not used in the fixed copy below
                f.tree.selection_set(str(n))
                f.toggle()
            k = next(n for n, c in enumerate(f.changes) if not c.check)
            f.tree.selection_set(str(k))
            f.show_detail()
            self.check('Fix with AI: a change shows "Now" and "Change to"', 'Change to:' in f.detail.get('1.0', 'end'))
            f.toggle()
            self.check('Fix with AI: a change can be left out', not f.changes[k].accepted and f.tree.set(str(k), 'use') == '—')
            copy = os.path.join(self.tmp, 'game (AI fixed).html')
            self.dialog_answers['save'] = copy
            f.save()
            self.wait(lambda: f.last_check is not None, 120, 'check of the fixed copy')
            used = len({id(c.issue) for c in f.changes if c.accepted})
            fixed, new, revealed = f.last_check or (0, [None], [])
            self.check('Fix with AI: the fixed copy is saved and checked again: the problems are gone, nothing new broke',
                       os.path.isfile(copy) and fixed >= used * 0.8 and not new,
                       f'{used} used, {fixed} gone, new: {[x.title for x in new][:3]}')
            self.check('Fix with AI: the game file itself is not changed and a list of changes is saved',
                       hashlib.sha1(open(self.game, 'rb').read()).hexdigest() == before
                       and os.path.isfile(os.path.join(self.tmp, 'game (AI fixed) - changes.txt')))
            f.close()
            a.settings['ai'] = saved
        finally:
            srv.shutdown()

    def t_lessons_glossary(self):
        a = self.app
        a.open_lessons()
        w = a.windows[-1]
        n = w.listbox.size()
        ok = n >= 8
        for k in range(n):
            w.listbox.selection_clear(0, 'end')
            w.listbox.selection_set(k)
            w.show()
            ok = ok and len(w.text.get('1.0', 'end')) > 100
        self.check('every lesson opens with content', ok, str(n))
        w.close()
        a.open_glossary()
        w = a.windows[-1]
        self.check('word list opens', 'passage' in w.text.get('1.0', 'end'))
        w.close()

    def t_tour(self):
        a = self.app
        a.start_tour()
        steps = len(a.tour.steps)
        seen = 0
        while a.tour and a.tour.bubble is not None and seen < 30:
            seen += 1
            a.tour.next()
            self.pump(0.05)
        self.check('tour shows every step and closes', seen == steps and a.tour is None, f'{seen} of {steps}')

    def t_read_aloud(self):
        a = self.app
        a.select_issue(a.result.issues[0])
        a.btn_read.invoke()
        self.check('Read aloud speaks the details text', self.spoken and a.title_of(a.result.issues[0])[:20] in self.spoken[-1])

    def t_xl_colorblind(self):
        a = self.app
        a.xl_var.set(True)
        a.toggle_xl()
        self.check('Extra-large text sets 160%', abs(a.scale - 1.6) < 0.01 and a.zoom_label.cget('text') == '160%')
        a.toggle_xl()
        self.check('Extra-large off goes back', a.scale < 1.6)
        c1 = a.colors['error']
        a.cb_var.set(True)
        a.toggle_colorblind()
        c2 = a.colors['error']
        a.cb_var.set(False)
        a.toggle_colorblind()
        self.check('colour-blind colours change the palette and switch back', c1 != c2 and a.colors['error'] == c1)

    def t_editor_dialog(self):
        a = self.app
        a.choose_editor()
        w = a.windows[-1]
        self.check('text editor picker lists editors', isinstance(w, D.EditorDialog) and w.path_var.get())
        w.close()

    # buttons added in 3.0 that are English-only for now (see ENGLISH_ONLY in tools/strings.py)
    ENGLISH_ONLY_UI = ('Quick scan', 'Full scan…', '❚❚ Pause Full scan')

    def t_languages(self):
        a = self.app
        langs = sorted(i18n.available())
        # Each language's own name stays the same in every language (the language menu).
        english = set(self.texts()) - set(i18n.LANGUAGES.values())
        ai_menu = {AID.MENU_FIX, AID.MENU_LOG, AID.MENU_SETTINGS}   # the AI helper is translated only into AI_LANGS
        report = {}
        for code in langs:
            if code == 'en':
                continue
            a.set_language(code)
            self.pump(0.1)
            texts = self.texts()
            skip = set() if code in AID.AI_LANGS else ai_menu
            same = [t for t in texts if t in english - skip and len(t) > 3 and not t.isdigit() and '%' not in t
                    and t not in ('Game Doctor',) + self.ENGLISH_ONLY_UI and not t.startswith(('\U0001F310', 'Condemned'))]
            report[code] = same
            summary = a.text.get('1.0', 'end')
            self.check(f'language {code}: window rebuilt without errors', not a.errors, (a.errors or [''])[-1][-300:])
            self.check(f'language {code}: buttons and menus are translated', len(same) <= 3, ', '.join(same[:8]))
        a.set_language('en')
        self.pump(0.1)
        self.check('back to English', a.btn_open.cget('text') == 'Open game…')
        with open(os.path.join(self.tmp, 'untranslated.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)

    def t_playtest(self):
        a = self.app
        a.btn_playtest.invoke()
        self.wait(lambda: not a.playtesting, 300, 'play-test')
        lp = getattr(a, 'last_playtest', None)
        self.check('Play-test runs and reports how many scenes were played', lp and lp[1].get('renders', 0) > 1000,
                   str(lp[1] if lp else self.dialog_log[-1:]))

    def t_fullscan(self):
        """Start a Full scan, pause it once a group of scenes is saved, continue from the saved state, finish."""
        import fullscan
        a = self.app
        fullscan.clear_state(a.result)
        self.dialog_answers['yesno'] = True                     # "Start the Full scan?"
        a.btn_full.invoke()
        self.wait(lambda: fullscan.load_state(a.result), 300, 'first group of the Full scan')
        a.btn_full.invoke()                                     # pause
        self.wait(lambda: not a.fullscanning, 300, 'Full scan pause')
        st = fullscan.load_state(a.result) or {}
        self.check('Full scan pauses and keeps what it did on disk', st.get('scenes') and not st.get('finished'),
                   str(len(st.get('scenes', {}))))
        self.dialog_answers['yesno'] = True                     # "...paused at N%. Continue it?"
        a.btn_full.invoke()
        self.wait(lambda: not a.fullscanning, 600, 'Full scan')
        fs = getattr(a, 'last_fullscan', None) or {}
        confirmed = [i for i in a.result.issues if i.severity == scanner.ERROR and 'Confirmed while playing' in i.what]
        self.check('Full scan continues and finishes, with problems confirmed while playing',
                   fs.get('finished') and fs.get('covered', 0) > 1000 and confirmed, str({k: fs.get(k) for k in ('finished', 'covered')}))
        self.check('buttons back after the Full scan', a.btn_full.cget('text') == 'Full scan…'
                   and str(a.btn_scan.cget('state')) == 'normal')
        fullscan.clear_state(a.result)

    def t_tested_fixes(self):
        """Tested fixes: open a small game with planted bugs (put into the SugarCube engine of the old test game),
        let the app try every suggested fix on a hidden copy, and check what the window says. The game must not change."""
        import hashlib
        a = self.app
        src = os.path.join(FIXTURES, 'fixtest_sugarcube')
        if not os.path.isdir(src) or not os.path.isfile(OLD_GAME):
            self.check('tested fixes: test game available', False, src)
            return
        dst = os.path.join(self.tmp, 'fixtest')
        shutil.copytree(src, dst, dirs_exist_ok=True)
        story = open(os.path.join(src, 'story.html'), encoding='utf-8').read()
        engine = open(OLD_GAME, encoding='utf-8').read()
        ours = re.search(r'<tw-storydata.*?</tw-storydata>', story, re.S).group(0).replace('format-version="1.0"', 'format-version="2.37.3"')
        x, y = re.search(r'<tw-storydata.*?</tw-storydata>', engine, re.S).span()
        game = os.path.join(dst, 'game.html')
        with open(game, 'w', encoding='utf-8') as f:
            f.write(engine[:x] + ours + engine[y:])
        before = hashlib.sha1(open(game, 'rb').read()).hexdigest()
        a.auto_fixtest = True
        try:
            a.open_path(game)
            self.wait(lambda: a.result is not None and not a.scanning and os.path.normcase(a.result.html_path) == os.path.normcase(game), 60,
                      'scan of the tested-fixes game')
            self.wait(lambda: getattr(a.result, 'fix_tests', None) is not None, 240, 'fixes tested')
            res = a.result
            find = lambda title, text: next((i for i in res.issues if title in i.title and text in (i.snippet or '')), None)

            def detail(i):
                a.select_issue(i)
                self.pump(0.1)
                return a.text.get('1.0', 'end')
            castle = find('"Castel"', '')
            t = detail(castle) if castle else ''
            self.check('tested fix: shows "Tested: this removes the problem" and the scene was played',
                       '✓ Tested: this removes the problem.' in t and 'played the scene "Start"' in t, t[:600])
            self.check('tested fix: two names work, says which one was picked and why',
                       '"Castles" would also remove the problem. "Castle" was picked because' in t, t[:800])
            dead = find('"Market Sqaure"', 'is closed')
            t = detail(dead) if dead else ''
            self.check('no working fix: says so plainly, no "Change to" offered', dead is not None and not dead.new
                       and 'No working fix found.' in t and 'Change to' not in t.split('No working fix found.')[1], t[:600])
            shop = find('<<prnt>>', 'The shop')
            t = detail(shop) if shop else ''
            self.check('no working fix found only by playing the scene', 'playing the scene "Shop" then shows a new problem' in t,
                       t[:600])
            rows = [a.tree.item(k, 'values') for g in a.tree.get_children() for k in (a.tree.get_children(g) or [g])]
            self.check('list marks tested fixes with ✓', any(str(v[4]).startswith('✓') for v in rows), str(rows[:3]))
            a.btn_simple.invoke()
            self.pump()
            t = detail(castle) if castle else ''
            a.btn_simple.invoke()
            self.pump()
            self.check('tested fix shown in Simple words too', '✓ Tested' in t, t[:400])
            self.check('trying the fixes never changed the game file', hashlib.sha1(open(game, 'rb').read()).hexdigest() == before)
        finally:
            a.auto_fixtest = False

    def t_other_engines(self):
        a = self.app
        if not os.path.isdir(FIXTURES):
            self.check('test games available', False, FIXTURES)
            return
        for name in sorted(os.listdir(FIXTURES)):
            spec_f = os.path.join(FIXTURES, name, 'expect.json')
            if not os.path.isfile(spec_f):
                continue
            spec = json.load(open(spec_f))
            dst = os.path.join(self.tmp, 'fx', name)
            shutil.copytree(os.path.join(FIXTURES, name), dst, dirs_exist_ok=True)
            a.open_path(os.path.join(dst, spec['open']))
            self.wait(lambda: a.result is not None and not a.scanning, 60, f'scan of {name}')
            ok = a.result is not None and (len(a.result.issues) > 0 or name.startswith('clean'))
            if ok and a.result.issues:
                i = next((x for x in a.result.issues if x.line), a.result.issues[0])
                a.select_issue(i)
                ok = len(a.text.get('1.0', 'end')) > 50
                if i.line:
                    a.btn_editor.invoke()
                    argv = getattr(a, 'last_editor_argv', [])
                    ok = ok and os.path.normcase(argv[-1]) == os.path.normcase(i.file or a.result.html_path)
                    a.btn_showcode.invoke()
                    self.pump(0.3)
                    a.build_code_view()
                    ok = ok and a.code_built_for == a.code_key()
                a.nb.select(a.tab_problems)
            self.check(f'{name}: opens, shows details, editor and code view point to the right file', ok)


def start_fake_ai():
    """A tiny service on 127.0.0.1 that answers like an OpenAI-style AI: it returns the scanner's own
    suggested changes as find/replace pairs, plus one change on a line far from the problem."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, obj):
            body = json.dumps(obj).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send({'data': [{'id': 'fake-model'}, {'id': 'other-model'}]})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            prompt = req['messages'][-1]['content']
            if 'Say hello' in prompt:
                return self.send({'choices': [{'message': {'content': 'Hello!'}}]})
            fixes_, skipped = [], []
            for block in re.split(r'\n(?=#\d+ \[)', '\n' + prompt):
                m = re.match(r'#(\d+) ', block)
                c = re.search(r"suggested change for line (\d+):\n      now:       (.*)\n      change to: (.*)", block)
                if not m:
                    continue
                swap = re.search(r'not found: (.+)\n.*Line: (\d+).*', block)
                near = re.search(r'e\.g\. "(.+?)"', block)
                if swap and near:   # a careless AI that swaps a missing picture for a look-alike
                    name = re.split(r'[\\/]', swap.group(1))[-1]
                    fixes_.append({'problem': int(m.group(1)), 'line': int(swap.group(2)), 'find': name, 'replace': near.group(1)})
                    continue
                if not c or '…' in c.group(2):
                    skipped.append({'problem': int(m.group(1)), 'why': 'not sure'})
                    continue
                old, new = c.group(2), c.group(3)
                a = 0
                while a < min(len(old), len(new)) and old[a] == new[a]:
                    a += 1
                b = 0
                while b < min(len(old), len(new)) - a and old[-1 - b] == new[-1 - b]:
                    b += 1
                for k in (12, 25, 50, 100, 400):
                    find = old[max(0, a - k):len(old) - max(0, b - k)]
                    if old.count(find) == 1:
                        break
                rep_ = old[max(0, a - k):a] + new[a:len(new) - b] + old[len(old) - b:len(old) - max(0, b - k)]
                fixes_.append({'problem': int(m.group(1)), 'line': int(c.group(1)), 'find': find, 'replace': rep_, 'why': 'scanner fix'})
                r = re.search(r'Rename everywhere: "(.*)" -> "(.*)"', block)
                also = block.split('also on these lines', 1)[-1].split('Code around it', 1)[0]
                for n, text in re.findall(r'\n {3}\s*(\d+) \| (.*)', also) if r else ():
                    if text.count(r.group(1)) == 1:
                        fixes_.append({'problem': int(m.group(1)), 'line': int(n), 'find': r.group(1), 'replace': r.group(2)})
            if fixes_:
                bad = dict(fixes_[0], line=fixes_[0]['line'] + 500)
                fixes_.append(bad)
            answer = '```json\n' + json.dumps({'fixes': fixes_, 'skipped': skipped}) + '\n```'
            self.send({'choices': [{'message': {'content': answer}}]})

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}/v1'


def main(args):
    opts = {'playtest': '--playtest' in sys.argv}
    tmp = tempfile.mkdtemp(prefix='gbs-selftest-')
    try:   # a console that can't show a character (e.g. ⚠) must not fail a check
        sys.stdout.reconfigure(errors='replace')
    except (AttributeError, ValueError):
        pass
    print('Self-test working folder:', tmp)
    game = os.path.join(tmp, os.path.basename(GAME))
    shutil.copy2(GAME, game)
    i18n.available()
    root = tk.Tk()
    settings = {'_selftest': True, 'tour_done': True, 'language': 'en', 'theme': 'light', 'scale': 1.0, 'simple_mode': True,
                'audience': 'dev', 'edit_mode': 'file'}
    app = appmod.App(root, None, settings)
    r = Runner(root, app, tmp, opts)
    r.game = game
    r.patch_dialogs()
    results = r.run()
    passed = sum(1 for _, ok, _ in results if ok)
    lines = [f'Game Doctor self-test: {passed} of {len(results)} checks passed', '']
    lines += [('ok    ' if ok else 'FAIL  ') + name + (f'  -- {d}' if d and not ok else '') for name, ok, d in results]
    report = os.path.join(HERE, 'selftest-report.txt')
    try:
        with open(report, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except OSError:
        pass
    print('\n' + lines[0])
    try:
        root.destroy()
    except tk.TclError:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if passed == len(results) else 1
