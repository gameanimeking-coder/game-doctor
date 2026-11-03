"""Full scan: play every scene of the game with many sets of values, until every branch
(<<if>>/<<elseif>>/<<else>>, <<switch>>/<<case>>) has been shown at least once, and record
what really goes wrong while playing.

- Branch tracking: before a scene is shown, a tiny invisible marker is put right after each
  branch's opening tag (only in the browser's copy; the game file is never changed). When a
  branch is shown, its marker reports it.
- Value sets: start values first, then the old play-test's three ways (numbers high, numbers 0,
  yes/no flipped), then values taken from the conditions themselves ($money gt 50 -> 49, 50, 51;
  $gender is "f" -> "f" and the other values the game gives $gender). Changes that opened a branch
  are kept as the starting point for the branches inside it.
- Every picture/sound/video the scene asks for is recorded and checked by the browser itself.

Results carry the values that caused them, so every problem found here is proven by playing.
SugarCube, Harlowe 3 and X-Lowe (Harlowe: hooks after (if:)/(else-if:)/(else:)/(unless:) and a game's own
condition macros). merge_quick() lays the Quick scan's findings next to what playing showed.
"""
import collections
import contextlib
import hashlib
import itertools
import json
import os
import re
import time
import urllib.parse

import playtest
from playtest import PlaytestError
from scanner import ERROR, INFO, WARNING, Issue

MAX_BUDGET = 400       # most ways to play one scene (start values and the three old ways included)
PER_ROUND = 6          # ways per scene sent to the browser at once
BATCH = 20             # scenes played side by side
MODES = [('high', 'all numbers set high (100)'), ('zero', 'all numbers set to 0'), ('flip', 'every yes/no value flipped')]

# shared by the SugarCube and Harlowe players: dice, the files a scene asks for, checking files
_JS_RECORD = r'''
  let rec = null, hits = null, curKey = 0, seq = 0;
  // Dice: while testing, random numbers repeat exactly for the same test. A test can fix the first roll;
  // later rolls still vary, so "roll until different" loops end.
  let playing = false, firstRoll = null, seed = 1;
  const mathRandom = Math.random;
  Math.random = () => {
    if (!playing) return mathRandom();
    if (firstRoll !== null) { const r = firstRoll; firstRoll = null; return r; }
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  window.GBF_HIT = (k, id) => { if (k === curKey && hits) hits.add(id); };

  // ---- record every file the scene asks for
  const note = v => {
    if (!rec || v == null) return;
    const s = String(v).trim();
    if (s && !/^(data:|blob:|about:|javascript:|#)/i.test(s)) rec.add(s);
  };
  for (const C of [HTMLImageElement, HTMLMediaElement, HTMLSourceElement]) {
    const d = Object.getOwnPropertyDescriptor(C.prototype, 'src');
    if (d && d.set) Object.defineProperty(C.prototype, 'src', { configurable: true, enumerable: d.enumerable,
      get: d.get, set(v) { note(v); d.set.call(this, v); } });
  }
  const setAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (n, v) {
    if (rec && /^(src|poster)$/i.test(n) && /^(img|audio|video|source|image|embed|track|input)$/i.test(this.tagName)) note(v);
    return setAttr.call(this, n, v);
  };
  const OrigAudio = window.Audio;
  window.Audio = function (src) { if (src !== undefined) note(src); return src === undefined ? new OrigAudio() : new OrigAudio(src); };
  window.Audio.prototype = OrigAudio.prototype;
  const fromDom = el => {
    el.querySelectorAll('[src],[poster]').forEach(x => { note(x.getAttribute('src')); note(x.getAttribute('poster')); });
    el.querySelectorAll('[style]').forEach(x => {
      for (const m of x.getAttribute('style').matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/g)) note(m[2]);
    });
  };

'''

_JS_CHECK = r'''
    async check(urls) {
      const out = {};
      await Promise.all(urls.map(async u => {
        try {
          const abs = new URL(u, document.baseURI);
          if (abs.origin !== location.origin) { out[u] = [abs.href, 0]; return; }
          const r = await fetch(abs.href, { method: 'HEAD', cache: 'no-store' });
          out[u] = [abs.pathname, r.status];
        } catch (e) { out[u] = ['', 0]; }
      }));
      return out;
    }
'''

FULL_JS = r'''
window.__gbf = window.__gbf || (function () {
  const SC = window.SugarCube;
  const clone = o => { try { return structuredClone(o); } catch (e) { return JSON.parse(JSON.stringify(o)); } };
  const init = clone(SC.State.variables);
''' + _JS_RECORD + r'''  // ---- find the branches of a scene
  const MASK = /\/\*[\s\S]*?\*\/|<!--[\s\S]*?-->|\{\{\{[\s\S]*?\}\}\}|"""[\s\S]*?"""|<<script\b[\s\S]*?<<\/script>>|<nowiki>[\s\S]*?<\/nowiki>|<script\b[\s\S]*?<\/script>/gi;
  const TAG = /<<(\/?)(if|elseif|else|switch|case|default)\b((?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[^>"'`]|>(?!>))*)>>/g;
  function tokens(text) {
    const masked = text.replace(MASK, m => m.replace(/[^\n]/g, ' '));
    const out = [], stack = [];
    const add = (blk, kind, cond, at, after) => {
      if (blk.last) { blk.last.end = at; if (blk.last.cond) blk.prior.push(blk.last.cond); }
      const b = { id: out.length, kind, cond: (kind === 'else' || kind === 'default') ? '' : cond,
                  prior: blk.prior.slice(), parent: blk.parent, sw: blk.type === 'switch' ? blk.expr : '',
                  at, after, end: -1 };
      out.push(b);
      blk.last = b;
    };
    TAG.lastIndex = 0;
    let m;
    while ((m = TAG.exec(masked))) {
      const close = m[1] === '/', kind = m[2], args = m[3].trim(), at = m.index, after = m.index + m[0].length;
      if (close) {
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i].type === kind) {
            for (const b of stack.splice(i)) if (b.last && b.last.end < 0) b.last.end = at;
            break;
          }
        }
      } else if (kind === 'if' || kind === 'switch') {
        const top = stack[stack.length - 1];
        const blk = { type: kind, expr: args, prior: [], last: null, parent: top && top.last ? top.last.id : -1 };
        stack.push(blk);
        if (kind === 'if') add(blk, 'if', args, at, after);
      } else {
        const want = (kind === 'case' || kind === 'default') ? 'switch' : 'if';
        const blk = stack.length && stack[stack.length - 1].type === want ? stack[stack.length - 1] : null;
        if (!blk) continue;
        if (kind === 'else' && /^if\b/.test(args)) add(blk, 'elseif', args.slice(2).trim(), at, after);
        else add(blk, kind, args, at, after);
      }
    }
    return out;
  }
  function instrument(text, key) {
    let out = '', pos = 0;
    for (const b of tokens(text)) {
      out += text.slice(pos, b.after) + '<<run window.GBF_HIT(' + key + ',' + b.id + ')>>';
      pos = b.after;
    }
    return out + text.slice(pos);
  }

  // ---- values
  function prepare(mode, set) {
    const v = clone(init);
    for (const k in v) {
      if (mode === 'high' && typeof v[k] === 'number') v[k] = 100;
      if (mode === 'zero' && typeof v[k] === 'number') v[k] = 0;
      if (mode === 'flip' && typeof v[k] === 'boolean') v[k] = !v[k];
    }
    let applied = 0;
    firstRoll = null;
    seed = 12345;
    for (const [path, val] of set || []) {
      if (path[0] === '#random') { firstRoll = val; seed = Math.floor(val * 1e6) + 7; applied++; continue; }
      let o = v, ok = true;
      for (let i = 0; i < path.length - 1; i++) {
        if (o[path[i]] === undefined || o[path[i]] === null) o[path[i]] = {};
        o = o[path[i]];
        if (typeof o !== 'object') { ok = false; break; }
      }
      const last = path[path.length - 1], cur = ok ? o[last] : undefined;
      let val2 = val;
      if (ok && typeof cur === 'number' && typeof val === 'boolean') val2 = val ? 1 : 0;   // "<<if $n>>" on a number
      if (ok && typeof cur === 'string' && typeof val === 'boolean') { if (val) continue; val2 = ''; }
      if (!ok || (cur !== undefined && cur !== null && (typeof cur === 'object' || typeof cur !== typeof val2))) continue;
      o[last] = val2;
      applied++;
    }
    for (const k of Object.keys(SC.State.variables)) delete SC.State.variables[k];
    Object.assign(SC.State.variables, v);
    if (SC.State.temporary) for (const k of Object.keys(SC.State.temporary)) delete SC.State.temporary[k];
    return applied;
  }

  return {
    structure(names) {
      const out = {};
      for (const name of names) {
        const P = SC.Story.get(name);
        if (!P) continue;
        const text = P.text;
        out[name] = tokens(text).map(b => Object.assign(b, { nl: text.slice(0, b.at).split('\n').length - 1 }));
      }
      return out;
    },
    play(jobs) {
      const res = [];
      // nothing may leave the scene, open windows or restart the game while it is tested
      const saved = [];
      const stub = (o, k) => { if (o && typeof o[k] === 'function') { saved.push([o, k, o[k]]); o[k] = () => {}; } };
      for (const k of ['play', 'goTo', 'go', 'show', 'restart', 'backward', 'forward']) stub(SC.Engine, k);
      stub(SC.Dialog, 'open'); stub(SC.UI, 'restart'); stub(SC.UI, 'alert');
      for (const k of ['alert', 'confirm', 'prompt', 'open', 'print']) stub(window, k);
      const onerror = window.onerror;
      let clickErrs = null;
      const catcher = e => { if (clickErrs) { clickErrs.push(String(e.message || e.error || 'error')); e.preventDefault(); } };
      window.onerror = null;
      window.addEventListener('error', catcher);
      playing = true;
      let stage = document.getElementById('gbf-stage');
      if (!stage) {
        stage = document.createElement('div');
        stage.id = 'gbf-stage';
        stage.style.display = 'none';
        document.body.appendChild(stage);
      }
      const errorsIn = (el, label, out) => el.querySelectorAll('.error-view, .error').forEach(x => {
        if (x.dataset.gbfSeen) return;
        x.dataset.gbfSeen = 1;
        const t = (x.querySelector('.error') || x).textContent.replace(/<<run window\.GBF_HIT\(\d+,\d+\)>>/g, '').trim();
        if (t) out.push([t.slice(0, 300), label]);
      });
      try {
        for (const j of jobs) {
          const P = SC.Story.get(j.name);
          if (!P) { res.push({ skipped: true }); continue; }
          const applied = prepare(j.mode || 'start', j.set);
          if (j.set && j.set.length && !applied) { res.push({ skipped: true }); continue; }
          curKey = ++seq; hits = new Set(); rec = new Set();
          stage.textContent = '';
          const el = document.createElement('div');
          stage.appendChild(el);
          const errs = [];
          try { new SC.Wikifier(el, instrument(P.processText(), curKey)); }
          catch (e) { errs.push(['Script error: ' + (e && e.message ? e.message : e), null]); }
          errorsIn(el, null, errs);
          // click every button the scene made (links that run code: <<link>>, <<button>>, <<linkreplace>> ...)
          const clicked = new Set();
          for (let n = 0; n < 40; n++) {
            const x = [...el.querySelectorAll('.macro-link, .macro-button, .macro-linkappend, .macro-linkprepend, .macro-linkreplace, .macro-click, button')]
              .find(b => !clicked.has(b) && !b.disabled);
            if (!x) break;
            clicked.add(x);
            prepare(j.mode || 'start', j.set);   // every button starts from the same values
            const label = x.textContent.trim().slice(0, 60) || '(button)';
            clickErrs = [];
            try { x.click(); } catch (e) { clickErrs.push(String(e && e.message ? e.message : e)); }
            for (const m of clickErrs) errs.push([m.replace(/^Uncaught (Error: )?/, '').slice(0, 300), label]);
            clickErrs = null;
            errorsIn(el, label, errs);
          }
          fromDom(el);
          const seen = new Set();
          res.push({ taken: [...hits], errs: errs.filter(e => !seen.has(e[0]) && seen.add(e[0])), urls: [...rec] });
          curKey = 0; hits = null; rec = null;
        }
      } finally {
        curKey = 0; hits = null; rec = null; clickErrs = null; playing = false;
        stage.textContent = '';
        window.removeEventListener('error', catcher);
        window.onerror = onerror;
        for (const [o, k, f] of saved) o[k] = f;
        prepare('start');
      }
      return res;
    },
''' + _JS_CHECK + r'''  };
})();
'''

# Harlowe 3 and X-Lowe. X-Lowe offers window.require('state') etc.; for classic Harlowe the browser's copy of the
# game gets the same small door (harlowe_door), the game file itself is never changed.
HARLOWE_JS = r'''
window.__gbf = window.__gbf || (function () {
  const State = require('state'), Engine = require('engine'), Passages = require('passages');
  const clone = o => { try { return structuredClone(o); } catch (e) { return o; } };
  const own = () => Object.keys(State.variables).filter(k => !k.startsWith('TwineScript_'));
  const init = {};
  for (const k of own()) init[k] = clone(State.variables[k]);
''' + _JS_RECORD + r'''
  // ---- find the branches of a scene: hooks after (if:), (else-if:), (else:), (unless:), and after a game's own
  // macro when an (else:) follows it, e.g. (is_fem:)[...](else:)[...]
  const MASK = /<!--[\s\S]*?-->|<script\b[\s\S]*?<\/script>|<style\b[\s\S]*?<\/style>|(`+)[\s\S]*?\1/gi;
  const COND = { if: 'if', elseif: 'elseif', else: 'else', unless: 'unless' };
  const norm = n => n.toLowerCase().replace(/[-_]/g, '');
  function macroEnd(t, i) {                    // t[i] is "(": index after the matching ")"
    let d = 0;
    for (let j = i; j < t.length; j++) {
      const c = t[j];
      if (c === '"' || c === "'") {
        for (j++; j < t.length && t[j] !== c; j++) if (t[j] === '\\') j++;
        continue;
      }
      if (c === '(') d++;
      else if (c === ')' && --d === 0) return j + 1;
    }
    return -1;
  }
  function hookEnd(t, i) {                     // t[i] is "[": index of the matching "]"
    let d = 1;
    for (let j = i + 1; j < t.length; j++) {
      if (t[j] === '[' && t[j + 1] === '[') {  // a [[link]]
        const e = t.indexOf(']]', j + 2);
        if (e < 0) return -1;
        j = e + 1;
        continue;
      }
      if (t[j] === '(' && /^\([\w-]+:/.test(t.slice(j, j + 40))) {
        const e = macroEnd(t, j);
        if (e > 0) { j = e - 1; continue; }
      }
      if (t[j] === '[') d++;
      else if (t[j] === ']' && --d === 0) return j;
    }
    return -1;
  }
  function tokens(text) {
    const t = text.replace(MASK, m => m.replace(/[^\n]/g, ' '));
    const out = [], open = [], chains = {};
    const MACRO = /\((\$?[\w-]+):/g;          // (if:), a game's own (is_fem:), or ($is_fem:) made with (macro:)
    let m;
    while ((m = MACRO.exec(t))) {
      const at = m.index;
      while (open.length && open[open.length - 1].end < at) open.pop();
      const e = macroEnd(t, at);
      if (e < 0) continue;
      let j = e, plus;
      while ((plus = /^\s*\+\s*\(/.exec(t.slice(j, j + 40)))) {      // (if: $x)+(text-colour: red)[...]
        const e2 = macroEnd(t, j + plus[0].length - 1);
        if (e2 < 0) break;
        j = e2;
      }
      const tag = /^\|[\w-]+>/.exec(t.slice(j, j + 60));
      if (tag) j += tag[0].length;
      if (t[j] !== '[') continue;
      const end = hookEnd(t, j);
      if (end < 0) continue;
      let kind = COND[norm(m[1])];
      if (!kind) {
        if (!/^\s*\((?:else|else-?if):/i.test(t.slice(end + 1, end + 200))) continue;
        kind = 'custom';
      }
      const depth = open.length;
      const args = text.slice(at + m[0].length, e - 1).trim();
      let chain = chains[depth];
      if (kind === 'if' || kind === 'unless' || kind === 'custom') chain = chains[depth] = { prior: [] };
      else if (!chain) continue;
      const cond = kind === 'else' ? '' : kind === 'unless' ? '!(' + args + ')' : kind === 'custom' ? text.slice(at, e) : args;
      const b = { id: out.length, kind: kind === 'elseif' || kind === 'else' ? kind : 'if', cond, prior: chain.prior.slice(),
                  parent: depth ? open[depth - 1].id : -1, sw: '', at, after: j + 1, end };
      if (cond) chain.prior.push(cond);
      out.push(b);
      open.push({ end, id: b.id });
    }
    return out;
  }
  function instrument(text, key) {
    let out = '', pos = 0;
    for (const b of tokens(text).sort((x, y) => x.after - y.after)) {
      out += text.slice(pos, b.after) + '<script>window.GBF_HIT(' + key + ',' + b.id + ')</script>';
      pos = b.after;
    }
    return out + text.slice(pos);
  }

  // ---- values (datamaps are Maps, datasets are Sets)
  const get = (o, k) => o instanceof Map ? o.get(k) : o[k];
  const put = (o, k, v) => { if (o instanceof Map) o.set(k, v); else o[k] = v; };
  function prepare(mode, set) {
    const v = {};
    for (const k in init) {
      v[k] = clone(init[k]);
      if (mode === 'high' && typeof v[k] === 'number') v[k] = 100;
      if (mode === 'zero' && typeof v[k] === 'number') v[k] = 0;
      if (mode === 'flip' && typeof v[k] === 'boolean') v[k] = !v[k];
    }
    let applied = 0;
    firstRoll = null;
    seed = 12345;
    for (const [path, val] of set || []) {
      if (path[0] === '#random') { firstRoll = val; seed = Math.floor(val * 1e6) + 7; applied++; continue; }
      let o = v, ok = true;
      for (let i = 0; i < path.length - 1; i++) {
        let n = get(o, path[i]);
        if (n === undefined || n === null) { n = new Map(); put(o, path[i], n); }
        o = n;
        if (typeof o !== 'object') { ok = false; break; }
      }
      const last = path[path.length - 1], cur = ok ? get(o, last) : undefined;
      let val2 = val;
      if (ok && typeof cur === 'number' && typeof val === 'boolean') val2 = val ? 1 : 0;
      if (ok && typeof cur === 'string' && typeof val === 'boolean') { if (val) continue; val2 = ''; }
      if (!ok || (cur !== undefined && cur !== null && (typeof cur === 'object' || typeof cur !== typeof val2))) continue;
      put(o, last, val2);
      applied++;
    }
    for (const k of own()) if (!(k in v)) { try { delete State.variables[k]; } catch (e) {} }
    for (const k in v) State.variables[k] = v[k];
    return applied;
  }

  return {
    structure(names) {
      const out = {};
      for (const name of names) {
        const P = Passages.get(name);
        if (!P) continue;
        const text = P.get('source');
        out[name] = tokens(text).map(b => Object.assign(b, { nl: text.slice(0, b.at).split('\n').length - 1 }));
      }
      return out;
    },
    play(jobs) {
      const res = [];
      const show = Engine.goToPassage;
      // nothing may leave the scene or open windows while it is tested
      const saved = [];
      const stub = (o, k) => { if (o && typeof o[k] === 'function') { saved.push([o, k, o[k]]); o[k] = () => {}; } };
      for (const k of ['goToPassage', 'goBack', 'goForward', 'redirect', 'toggleFullscreen']) stub(Engine, k);
      // (go-to:) travels a moment later, after the test: Harlowe's own debug switch turns it off
      const options = require('utils').options || {}, ignoreGotos = options.ignoreGotos;
      options.ignoreGotos = true;
      for (const k of ['alert', 'confirm', 'prompt', 'open', 'print']) stub(window, k);
      const onerror = window.onerror;
      let clickErrs = null;
      const catcher = e => { if (clickErrs) { clickErrs.push(String(e.message || e.error || 'error')); e.preventDefault(); } };
      window.onerror = null;
      window.addEventListener('error', catcher);
      const errorsIn = (el, label, out) => el.querySelectorAll('tw-error').forEach(x => {
        if (x.dataset.gbfSeen) return;
        x.dataset.gbfSeen = 1;
        const t = (x.firstChild && x.firstChild.nodeType === 3 ? x.firstChild.textContent : x.textContent).trim();
        const part = x.closest('tw-include');      // a header, footer or (display:)ed passage
        if (t) out.push([t.slice(0, 300), label, part ? part.getAttribute('name') : null]);
      });
      try {
        for (const j of jobs) {
          const P = Passages.get(j.name);
          if (!P) { res.push({ skipped: true }); continue; }
          const applied = prepare(j.mode || 'start', j.set);
          if (j.set && j.set.length && !applied) { res.push({ skipped: true }); continue; }
          curKey = ++seq; hits = new Set(); rec = new Set();
          const errs = [];
          const source = P.get('source');
          P.set('source', instrument(source, curKey));
          Passages.clearTreeCache();       // Harlowe keeps scenes it has read: make it read the marked copy
          playing = true;
          clickErrs = [];
          try { show.call(Engine, j.name, { transition: 'instant' }); }
          catch (e) { errs.push(['Script error: ' + (e && e.message ? e.message : e), null]); }
          finally { P.set('source', source); Passages.clearTreeCache(); }
          for (const m of clickErrs) errs.push([m.replace(/^Uncaught (Error: )?/, '').slice(0, 300), null]);
          clickErrs = null;
          const ps = document.querySelectorAll('tw-story > tw-passage');
          const el = ps[ps.length - 1] || document.body;
          errorsIn(el, null, errs);
          // click every link that opens more of the scene ((link:), (click:) ...), not links to other scenes
          const clicked = new Set();
          for (let n = 0; n < 40; n++) {
            const x = [...el.querySelectorAll('tw-link:not([passage-name]), .enchantment-link')]
              .find(b => !clicked.has(b) && b.isConnected);
            if (!x) break;
            clicked.add(x);
            prepare(j.mode || 'start', j.set);
            const label = x.textContent.trim().slice(0, 60) || '(link)';
            clickErrs = [];
            try { x.click(); } catch (e) { clickErrs.push(String(e && e.message ? e.message : e)); }
            for (const m of clickErrs) errs.push([m.replace(/^Uncaught (Error: )?/, '').slice(0, 300), label]);
            clickErrs = null;
            errorsIn(el, label, errs);
          }
          fromDom(el);
          playing = false;
          const seen = new Set();
          res.push({ taken: [...hits], errs: errs.filter(e => !seen.has(e[0]) && seen.add(e[0])), urls: [...rec] });
          curKey = 0; hits = null; rec = null;
          if (State.pastLength > 50 && State.forgetUndos) { try { State.forgetUndos(-1); } catch (e) {} }
        }
      } finally {
        curKey = 0; hits = null; rec = null; clickErrs = null; playing = false;
        window.removeEventListener('error', catcher);
        window.onerror = onerror;
        for (const [o, k, f] of saved) o[k] = f;
        options.ignoreGotos = ignoreGotos;
        prepare('start');
      }
      return res;
    },
''' + _JS_CHECK + r'''
  };
})();
'''


def harlowe_door(html):
    """Classic Harlowe keeps its parts private; give the browser's copy the same window.require door X-Lowe has."""
    if 'window.require=function' in html:
        return html
    m = re.search(r'define\("harlowe",\[([^\]]*)\]', html)
    if not m:
        return html
    door = ('define("gbf_door",["state","engine","passages","section","macros","utils"],function(a,b,c,d,e,f){'
            'var M={state:a,engine:b,passages:c,section:d,macros:e,utils:f};'
            'window.require=window.require||function(n){return M[n]};});')
    return html[:m.start()] + door + f'define("harlowe",[{m.group(1)},"gbf_door"]' + html[m.end():]


# ---------------------------------------------------------------------- reading conditions
_PATH = r'(?:\$|\bState\.variables\.|\bV\.)([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*|\[\s*(?:"[^"\n]*"|\'[^\'\n]*\'|\d+)\s*\])*)'
PATH_RE = re.compile(_PATH)
_LIT = r'(-?\d+(?:\.\d+)?|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|\btrue\b|\bfalse\b)'
LIT_RE = re.compile(_LIT)
_OP = r'\s*(?:isnot|is not|is|eq|neq|gte|gt|lte|lt|to|into|===|!==|==|!=|>=|<=|>|<|=)\s*'
CMP_RE = re.compile(_PATH + _OP + _LIT)
CMP_REV_RE = re.compile(_LIT + _OP + _PATH)
SEG_RE = re.compile(r'\.([A-Za-z_]\w*)|\[\s*(?:"([^"\n]*)"|\'([^\'\n]*)\'|(\d+))\s*\]')


def parse_path(name, rest):
    """'$a', '.b["c d"]' -> ('$a.b.c d', ['a', 'b', 'c d'])  (methods and .length left out)"""
    keys = [name]
    for m in SEG_RE.finditer(rest):
        keys.append(next(g for g in m.groups() if g is not None))
    return keys


def paths_in(expr):
    """Variables an expression reads, as key lists."""
    out = []
    for m in PATH_RE.finditer(expr):
        keys = parse_path(m.group(1), m.group(2))
        if expr[m.end():m.end() + 1] == '(' and len(keys) > 1:
            keys = keys[:-1]                         # $list.includes("x") -> $list
        while len(keys) > 1 and keys[-1] in ('length', 'size'):
            keys = keys[:-1]
        if keys not in out:
            out.append(keys)
    return out


def lit_value(s):
    if s in ('true', 'false'):
        return s == 'true'
    if s[0] in '"\'':
        try:
            return json.loads('"' + s[1:-1].replace('"', '\\"') + '"') if s[0] == '\'' else json.loads(s)
        except ValueError:
            return s[1:-1]
    return float(s) if '.' in s else int(s)


def comparisons(expr):
    """[(key list, value)] for every '$var op literal' (either way round)."""
    out = []
    for m in CMP_RE.finditer(expr):
        out.append((parse_path(m.group(1), m.group(2)), lit_value(m.group(3))))
    for m in CMP_REV_RE.finditer(expr):
        out.append((parse_path(m.group(2), m.group(3)), lit_value(m.group(1))))
    return out


def _has(vals, x):
    return any(type(y) is type(x) and y == x for y in vals)   # 1 and True are different values here


def _near(v):
    return [v - 1, v, v + 1] if isinstance(v, int) and not isinstance(v, bool) else [v]


def _add(dom, keys, v, near=False):
    vals = dom.setdefault(tuple(keys), [])
    for x in (_near(v) if near else [v]):
        if not _has(vals, x):
            vals.append(x)


def domains(texts):
    """Every value the game gives or compares each variable with: {key tuple: [values]}."""
    dom = {}
    for text in texts:
        for m in re.finditer(r'<<set\b(.*?)>>', text, re.S):
            for keys, v in comparisons(m.group(1)):
                _add(dom, keys, v)
        for m in re.finditer(r'<<(?:else)?if\b(.*?)>>', text, re.S):
            for keys, v in comparisons(m.group(1)):
                _add(dom, keys, v, near=True)
        if '(set:' in text or '(if:' in text or '(put:' in text:      # Harlowe
            text = harlowe_cond(text)
            for m in re.finditer(r'\((?:set|put):(.*?)\)', text, re.S):
                for keys, v in comparisons(m.group(1)):
                    _add(dom, keys, v)
            for m in re.finditer(r'\((?:if|else-?if|unless):(.*?)\)', text, re.S | re.I):
                for keys, v in comparisons(m.group(1)):
                    _add(dom, keys, v, near=True)
    return dom


POSS_RE = re.compile(r"(\$[A-Za-z_]\w*(?:\.\w+|\[[^\]]*\])*)'s\s+(?:([A-Za-z_]\w*)|(\"[^\"\n]*\"|'[^'\n]*'))")


def harlowe_cond(text, made=None):
    """Harlowe's $a's b and $a's "b c" written the way the solver reads them: $a.b, $a["b c"]. A call of a
    macro the game made itself, ($is_fem:), is replaced by the condition it gives back ($gender is "f")."""
    prev = None
    while prev != text:
        prev = text
        text = POSS_RE.sub(lambda m: f'{m.group(1)}.{m.group(2)}' if m.group(2) else f'{m.group(1)}[{m.group(3)}]', text)
        if made:
            text = MADE_CALL_RE.sub(lambda m: f'({made[m.group(1)]})' if m.group(1) in made else m.group(0), text)
    return text


MADE_RE = re.compile(r'\(set:\s*\$([A-Za-z_]\w*)\s+to\s+\(macro:[^\[\]]*\[\s*\(output-data:\s*([^()\[\]]+?)\s*\)\s*\]\s*\)\s*\)')
MADE_CALL_RE = re.compile(r'\(\$([A-Za-z_]\w*):\s*\)')


def made_macros(texts):
    """Macros a Harlowe game makes from a plain condition, without values passed in: {name: condition}."""
    return {m.group(1): m.group(2) for t in texts if '(macro:' in t for m in MADE_RE.finditer(t)}


def candidates(branch, dom, limit=8):
    """{key tuple: [values]} worth trying to open this branch."""
    out = {}
    exprs = [branch['cond'], *branch['prior']]
    if branch['sw']:
        exprs.append(branch['sw'])
    for e in exprs:
        for keys in paths_in(e):
            vals = out.setdefault(tuple(keys), [])
            local = [v for k, v in comparisons(e) if tuple(k) == tuple(keys)]
            for v in local:
                for x in _near(v):
                    if not _has(vals, x):
                        vals.append(x)
    if branch['kind'] == 'case' and branch['sw']:
        for keys in paths_in(branch['sw']):
            vals = out.setdefault(tuple(keys), [])
            for m in LIT_RE.finditer(branch['cond']):
                v = lit_value(m.group(1))
                if not _has(vals, v):
                    vals.insert(0, v)
    for keys, vals in out.items():
        for v in dom.get(keys, []):
            if not _has(vals, v):
                vals.append(v)
        if not vals or all(isinstance(v, bool) for v in vals):
            vals += [v for v in (True, False) if not _has(vals, v)]
        elif any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals) and not _has(vals, 0):
            vals.append(0)
        if any(isinstance(v, str) for v in vals) and not _has(vals, ''):
            vals.append('')
        del vals[limit:]
    return out


def show_values(job):
    mode, sets = job
    parts = [dict(MODES)[mode]] if mode != 'start' else []
    for keys, v in sets:
        if keys == RANDOM:
            parts.append(f'random numbers coming out at {int(v * 100)}%')
        else:
            parts.append(f'${".".join(keys)} = {json.dumps(v)}')
    return ', '.join(parts) if parts else 'the starting values'


# ---------------------------------------------------------------------- making a condition true or false
OR_RE = re.compile(r'\|\||or\b')
AND_RE = re.compile(r'&&|and\b')
NOT_RE = re.compile(r'(?:!(?!=)|not\b)\s*')
_OPG = r'\s*(isnot|is not|is|eq|neq|gte|gt|lte|lt|to|===|!==|==|!=|>=|<=|>|<|=)\s*'
ATOM_RE = re.compile(_PATH + _OPG + _LIT)
ATOM_REV_RE = re.compile(_LIT + _OPG + _PATH)
ATOM_VV_RE = re.compile(_PATH + _OPG + _PATH)
EITHER_RE = re.compile(r'\beither\(\s*' + _LIT + r'(?:\s*,[^()]*)?\)')
ORDER = {'gt': True, '>': True, 'gte': True, '>=': True, 'lt': False, '<': False, 'lte': False, '<=': False}
EQ, NE = {'is', 'eq', '==', '===', 'to', '='}, {'isnot', 'is not', 'neq', '!=', '!=='}
FLIP = {'gt': 'lt', 'lt': 'gt', 'gte': 'lte', 'lte': 'gte', '>': '<', '<': '>', '>=': '<=', '<=': '>='}


def split_top(expr, sep):
    """Split at `sep` outside brackets and quotes (word separators only at a word start)."""
    parts, depth, q, start, i = [], 0, None, 0, 0
    while i < len(expr):
        c = expr[i]
        if q:
            if c == '\\':
                i += 2
                continue
            if c == q:
                q = None
        elif c in '"\'`':
            q = c
        elif c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif depth == 0 and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] in '_$')):
            m = sep.match(expr, i)
            if m:
                parts.append(expr[start:i])
                start = i = m.end()
                continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts if p.strip()]


def strip_parens(e):
    """'(($a > 1))' -> '$a > 1', but '($a) && ($b)' stays."""
    e = e.strip()
    while e.startswith('(') and e.endswith(')'):
        depth = 0
        for i, c in enumerate(e):
            depth += (c == '(') - (c == ')')
            if depth == 0 and i < len(e) - 1:
                return e
        e = e[1:-1].strip()
    return e


def _merge(alt_lists):
    out = {}
    for alts in alt_lists:
        if alts:
            for k, v in alts[0].items():
                out.setdefault(k, v)
    return [out] if out else []


def solve(expr, want, dom):
    """Ways to make a condition true (want=True) or false: a list of {key tuple: value}. Parts of the
    condition that are not simple comparisons of variables are left out."""
    e = strip_parens(expr)
    ors = split_top(e, OR_RE)
    if len(ors) > 1:
        return [a for d in ors for a in solve(d, True, dom)][:6] if want else _merge([solve(d, False, dom) for d in ors])
    ands = split_top(e, AND_RE)
    if len(ands) > 1:
        return _merge([solve(a, True, dom) for a in ands]) if want else [a for x in ands for a in solve(x, False, dom)][:6]
    m = NOT_RE.match(e)
    if m and e[m.end():].strip():
        return solve(e[m.end():], not want, dom)
    if want:
        e = EITHER_RE.sub(lambda m: m.group(1), e)       # $x == either(2, 3) -> $x == 2
    m = ATOM_VV_RE.fullmatch(e)
    if m and m.group(3) in ORDER:
        hi, lo = (100, 0) if ORDER[m.group(3)] == want else (0, 100)
        return [{tuple(parse_path(m.group(1), m.group(2))): hi, tuple(parse_path(m.group(4), m.group(5))): lo}]
    m = ATOM_RE.fullmatch(e)
    if m:
        keys, op, lit = tuple(parse_path(m.group(1), m.group(2))), m.group(3), lit_value(m.group(4))
    else:
        m = ATOM_REV_RE.fullmatch(e)
        if m:
            keys, op, lit = tuple(parse_path(m.group(3), m.group(4))), m.group(2), lit_value(m.group(1))
            op = FLIP.get(op, op)
        elif PATH_RE.fullmatch(e):
            return [{tuple(paths_in(e)[0]): bool(want)}]
        else:
            return []
    v = _value_for(op, lit, want, dom.get(keys, []))
    return [] if v is None else [{keys: v}]


def _value_for(op, lit, want, known):
    if op in NE:
        op, want = 'is', not want
    if op in EQ:
        if want:
            return lit
        other = [v for v in known if type(v) is type(lit) and v != lit]
        if other:
            return other[0]
        if isinstance(lit, bool):
            return not lit
        if isinstance(lit, (int, float)):
            return lit + 1
        return '' if lit else 'x'
    if not isinstance(lit, (int, float)) or isinstance(lit, bool):
        return None
    above, below = lit + 1, lit - 1
    return {'gt': (above, lit), '>': (above, lit), 'gte': (lit, below), '>=': (lit, below),
            'lt': (below, lit), '<': (below, lit), 'lte': (lit, above), '<=': (lit, above)}[op][0 if want else 1]


# ---------------------------------------------------------------------- one scene
def _job_of(base, changes):
    mode, sets = base
    d = dict(sets)
    d.update(changes)
    return mode, tuple(sorted(d.items(), key=lambda kv: kv[0]))


RANDOM = ('#random',)
DICE = [round((i + 0.5) / 12, 3) for i in range(12)]
NEEDS_HISTORY = re.compile(r'<<(?:return|back)>>: cannot find passage')
DICE_RE = re.compile(r'\brandom\w*\s*\(|\beither\s*\(|Math\.random|\.(?:random|pluck|shuffle)\s*\(')
RANDOM_RE = re.compile(r'(?<![\w$])_\w|\brandom\w*\s*\(|\beither\s*\(|Math\.random')


class Scene:
    """Plays one scene until every branch has run or its budget is used up. Each branch gets a plan:
    values that make its condition true (and the earlier branches' conditions false), then single
    changes and pairs of changes of the variables it reads. Branches take turns."""

    def __init__(self, name, branches, dom, budget=None, dice=False):
        self.name, self.branches, self.dom = name, branches, dom
        self.budget = budget or min(MAX_BUDGET, 4 + 8 * len(branches))
        self.covered = {}          # branch id -> job that opened it
        self.tried = collections.Counter()
        self.first = collections.deque([('start', ()), *((m, ()) for m, _ in MODES)])
        if dice:
            self.first += [('start', ((RANDOM, r),)) for r in DICE]
        self.plans = {}            # branch id -> deque of jobs
        self.turn = 0
        self.renders = 0
        self.frozen = False
        self.errors = {}           # message -> [first job, ways it happened, happened with start values, button clicked]
        self.files = {}            # url -> first job that asked for it

    def frontier(self):
        return [b for b in self.branches if b['id'] not in self.covered
                and (b['parent'] < 0 or b['parent'] in self.covered)]

    def plan(self, b):
        base = self.covered.get(b['parent'], ('start', ()))
        dom, jobs = self.dom, []
        if b['kind'] == 'case':
            own = [{tuple(k): lit_value(m.group(1))} for m in LIT_RE.finditer(b['cond']) for k in paths_in(b['sw'])]
        elif b['kind'] in ('else', 'default'):
            own = [{}]
        else:
            own = solve(b['cond'], True, dom) or [{}]
        for o in own or [{}]:
            neg = {}
            if b['kind'] != 'case':
                for p in b['prior']:
                    alts = solve(p, False, dom)
                    pick = next((a for a in alts if not set(a) & set(o)), alts[0] if alts else {})
                    for k, v in pick.items():
                        neg.setdefault(k, v)
            neg.update(o)
            jobs.append(_job_of(base, neg))
        cands = candidates(b, dom)
        for k, vals in cands.items():
            for v in vals:
                jobs.append(_job_of(base, {k: v}))
        for (k1, v1s), (k2, v2s) in itertools.combinations(list(cands.items())[:4], 2):
            for v1, v2 in itertools.product(v1s[:3], v2s[:3]):
                jobs.append(_job_of(base, {k1: v1, k2: v2}))
        if RANDOM_RE.search(' '.join([b['cond'], *b['prior']])) or not cands:
            # dice or values the scene makes itself: fix the dice to each result in turn
            jobs += [_job_of(jobs[0], {RANDOM: r}) for r in DICE]
        return collections.deque(jobs)

    def next_jobs(self, n):
        out = []
        while self.first and len(out) < n:
            out.append(self.first.popleft())
        front = self.frontier()
        while front and len(out) < n and self.renders + len(out) < self.budget:
            progress = False
            k = self.turn % len(front)
            for b in front[k:] + front[:k]:
                if len(out) >= n or self.renders + len(out) >= self.budget:
                    break
                q = self.plans.get(b['id'])
                if q is None:
                    q = self.plans[b['id']] = self.plan(b)
                while q:
                    job = q.popleft()
                    repeats = q.count(job)          # planned repeats are allowed to run again
                    if self.tried[job] <= repeats and job not in out:
                        self.tried[job] += 1
                        out.append(job)
                        progress = True
                        break
            self.turn += 1
            if not progress:
                break
        return out

    def feed(self, job, r):
        if r.get('skipped'):
            return
        self.renders += 1
        for i in r.get('taken', []):
            self.covered.setdefault(i, job)
        for msg, label, *where in r.get('errs', []):
            e = self.errors.setdefault(msg, [job, 0, False, label, where[0] if where else None])
            e[1] += 1
            if job == ('start', ()):
                e[2] = True
        for u in r.get('urls', []):
            self.files.setdefault(u, job)

    @property
    def finished(self):
        if self.frozen:
            return True
        if self.first:
            return False
        if self.renders >= self.budget:
            return True
        return not any(self.plans.get(b['id']) is None or self.plans[b['id']] for b in self.frontier())

    def result(self):
        return dict(branches=self.branches, covered={str(k): v for k, v in self.covered.items()}, renders=self.renders,
                    errors=self.errors, files=self.files)


def _job_json(name, job):
    mode, sets = job
    return {'name': name, 'mode': mode, 'set': [[list(k), v] for k, v in sets]}


# ---------------------------------------------------------------------- main
def scene_names(res):
    return [n for n, (ln, tags) in res.passages.items()
            if not set(tags) & {'widget', 'script', 'stylesheet', 'Twine.private'} and n != 'StoryInit']


def run(res, progress=None, stop=None, state=None, on_save=None, budget=None, only=None):
    """Full scan of a scanned SugarCube game. `state` (a dict, may be from an earlier, paused run) is
    filled with each finished scene, and on_save(state) is called after every batch.
    Returns (issues, summary)."""
    progress = progress or (lambda p, m: None)
    stop = stop or (lambda: False)
    state = state if state is not None else {}
    done = state.setdefault('scenes', {})
    file_status = state.setdefault('file_status', {})
    t0 = time.time()
    with Player(res, progress) as pl:
        names = scene_names(res)
        todo = [n for n in names if n not in done and (only is None or n in only)]
        texts = [p.text for p in res.context.passages] if res.context is not None else []
        dom = domains(texts)
        made = made_macros(texts)
        dice = {p.name for p in res.context.passages if DICE_RE.search(p.text)} if res.context is not None else set()
        for k in range(0, len(todo), BATCH):
            if stop():
                break
            batch = todo[k:k + BATCH]
            struct = pl.evaluate(f'__gbf.structure({json.dumps(batch)})', 60, retry=True) or {}
            if pl.engine == 'harlowe':
                for b in itertools.chain.from_iterable(struct.values()):
                    b['cond'], b['prior'] = harlowe_cond(b['cond'], made), [harlowe_cond(c, made) for c in b['prior']]
            scenes = [Scene(n, struct.get(n, []), dom, budget, n in dice) for n in batch if n in struct]
            while not stop():
                jobs = [(s, j) for s in scenes if not s.finished for j in s.next_jobs(PER_ROUND)]
                if not jobs:
                    break
                for s, j, r in _play(pl, jobs):
                    s.feed(j, r)
            if stop():
                break
            new_urls = sorted({u for s in scenes for u in s.files} - set(file_status))
            for i in range(0, len(new_urls), 50):
                file_status.update(pl.evaluate(f'__gbf.check({json.dumps(new_urls[i:i + 50])})', 120, retry=True) or {})
            for s in scenes:
                done[s.name] = s.result()
            state['seconds'] = state.get('seconds', 0) + (time.time() - t0)
            t0 = time.time()
            if on_save:
                on_save(state)
            n_done = sum(1 for n in names if n in done)
            left = state['seconds'] / max(1, len(done)) * (len(names) - n_done)
            progress(10 + int(88 * n_done / max(1, len(names))),
                     ('Playing every branch: {k} of {n} scenes, about {left} left',
                      {'k': n_done, 'n': len(names), 'left': f'{max(1, round(left / 60))} min'}))
        # only start-up problems from the browser's own log: files are checked one by one above
        # (pictures cut off when a scene is cleared would look like failures there)
        load_events = playtest.runtime_issues(*pl.first_load, res)
    state['seconds'] = state.get('seconds', 0) + (time.time() - t0)
    state['finished'] = all(n in done for n in names)
    issues, summary = make_issues(res, state)
    have = {(i.title, i.passage) for i in issues}
    issues += [i for i in load_events if (i.title, i.passage) not in have]
    progress(100, 'Full scan finished')
    return issues, summary


class Lost(Exception):
    """The game froze the browser or left the page; the browser was started again."""
    def __init__(self, msg, froze):
        super().__init__(msg)
        self.froze = froze


class Player:
    """The hidden browser with the game loaded. When a scene freezes it (endless loop) or the game
    leaves the page (reload, link to another file), the browser is started again and Lost is raised."""

    def __init__(self, res, progress):
        self.res, self.progress = res, progress
        self.stack = self.ses = self.first_load = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        self.close()
        self.stack = contextlib.ExitStack()
        quiet = self.first_load is not None     # a restart does not show 'Loading the game' again
        self.ses = self.stack.enter_context(playtest.launch(self.res, (lambda p, m: None) if quiet else self.progress,
                                                            patch=harlowe_door))
        self.engine = 'sugarcube' if self.ses.is_sc else None
        if not self.engine:
            for _ in range(50):     # Harlowe shows its first scene a moment after loading
                try:
                    if self.ses.dt.evaluate("typeof require === 'function' && !!require('state') && "
                                            "!!document.querySelector('tw-passage')", 10):
                        self.engine = 'harlowe'
                        break
                except PlaytestError:
                    pass
                time.sleep(0.2)
        if not self.engine:
            raise PlaytestError('Full scan can play SugarCube, Harlowe and X-Lowe games.')
        if self.first_load is None:
            self.first_load = (self.ses.load_events, self.ses.load_events, self.ses.port)
        self.ses.dt.evaluate(FULL_JS if self.engine == 'sugarcube' else HARLOWE_JS, 30)

    def close(self):
        if self.stack:
            try:
                self.stack.close()
            except Exception:
                pass
            self.stack = None

    def _alive(self):
        try:
            return self.ses.dt.evaluate('typeof __gbf', 10) == 'object'
        except (PlaytestError, OSError):
            return False

    def evaluate(self, expr, timeout, retry=False):
        try:
            return self.ses.dt.evaluate(expr, timeout)
        except (PlaytestError, OSError) as e:      # OSError: socket timed out
            froze = isinstance(e, OSError) or 'in time' in str(e)
            if not froze and self._alive():
                raise
            self.start()
            if retry:
                return self.ses.dt.evaluate(expr, timeout)
            raise Lost(str(e), froze) from None


def _play(pl, jobs):
    """Play jobs [(scene, job)] in the browser; yields (scene, job, result). When a batch freezes the
    browser or leaves the page, its jobs are played again one by one: a scene that freezes alone is
    reported once and not played further; a job that leaves the page is skipped."""
    payload = [_job_json(s.name, j) for s, j in jobs]
    try:
        out = pl.evaluate(f'__gbf.play({json.dumps(payload)})', 90 if len(jobs) > 1 else 30) or []
        for (s, j), r in zip(jobs, out):
            yield s, j, r or {'skipped': True}
        return
    except Lost as e:
        if len(jobs) == 1:
            s, j = jobs[0]
            if e.froze:
                s.frozen = True
                yield s, j, {'errs': [['The scene froze the game while it was tested. It may contain an endless loop.', None]]}
            else:
                yield s, j, {'skipped': True}
            return
    for s, j in jobs:
        if not s.frozen:
            yield from _play(pl, [(s, j)])


def _job(raw):
    mode, sets = raw
    return mode, tuple((tuple(k), v) for k, v in sets)


# Error boxes that come from how the scene is written, whatever the values: these are confirmed.
# Any other error box may only happen because the scene was shown on its own (an earlier scene
# normally sets the values it reads), so it is "worth checking", not confirmed.
WRITTEN_RE = re.compile(
    r'cannot find a closing tag|was found outside of a call to its parent|macro <</?[\w-]+>> does not exist'
    r'|bad (?:evaluation|conditional expression)[^:]*: (?:Unexpected (?:identifier|token|number|string|end of input)'
    r'|Invalid or unexpected token|missing \S+ (?:after|before)|Unterminated|Invalid (?:left-hand|regular))'
    r'|\bno [\w ]{1,30} specified|\b(?!State\b|setup\b)[A-Za-z]\w* is not defined\b'
    # Harlowe / X-Lowe
    r"|isn't valid Harlowe syntax|I can't run the macro '[^']+' because it doesn't exist"
    r"|too many values were given to|doesn't have a matching (?:opening|closing)|unclosed|unmatched")
PASSAGE_RE = re.compile(r'''passage ["'](.+?)["'] (?:does not exist|because it doesn't exist)'''
                        r'''|no passage named ["'](.+?)["']''')
UNSET_FILE_RE = re.compile(r'undefined|\bnull\b|\bNaN\b|\[object|^(?:true|false|\d*)$', re.I)


def written_error(msg, text):
    """True when an error box comes from how the scene is written, not from the values it was shown with."""
    m = PASSAGE_RE.search(msg)
    if m:   # a missing passage named in the scene itself (not worked out from a value)
        gone = m.group(1) or m.group(2)
        return any(f'{q}{gone}{q2}' in text for q, q2 in (('"', '"'), ("'", "'"), ('[[', ']]'), ('|', ']]'), ('->', ']]'), ('[[', '<-')))
    return bool(WRITTEN_RE.search(msg))


def realistic(job):
    """Starting values, maybe with a dice result: values a player can really have."""
    return all(k == RANDOM for k, v in job[1]) and job[0] == 'start'


HISTORY_RE = re.compile(r'\b(?:visited|hasVisited|lastVisited|visitedTags|previous|turns|tags|time)\s*\('
                        r'|\((?:history|visited|passage|passages):|\bvisits?\b')
TEMP_RE = re.compile(r'(?<![\w$.])_[A-Za-z]\w*')


def why_missed(b, covered):
    """A short reason why a branch never ran."""
    if b['parent'] >= 0 and str(b['parent']) not in covered:
        return 'inside a part that never ran'
    text = ' '.join([b['cond'], *b['prior'], b['sw']])
    if HISTORY_RE.search(text):
        return 'depends on scenes seen before'
    if TEMP_RE.search(text):
        return 'depends on a value the scene works out itself'
    return 'no values found that open it'


ON_ITS_OWN = (('Error box when the scene is shown on its own', 'error boxes'),
              ('File failed to load when the scene is shown on its own', 'files failed to load'))


def _group_worth_checking(issues):
    """One "worth checking" finding per scene and kind, not one per message: big games give thousands."""
    out, groups = [], {}
    for i in issues:
        kind = next((k for k in ON_ITS_OWN if i.severity == INFO and i.title.startswith(k[0])), None)
        if kind:
            groups.setdefault((i.passage, kind), []).append(i)
        else:
            out.append(i)
    for (name, (prefix, plural)), items in groups.items():
        if len(items) == 1:
            out.append(items[0])
            continue
        first = items[0]
        cut = len('Worth checking: ')
        parts = [f'({n}) {i.what[cut:]}' for n, i in enumerate(items[:6], 1)]
        more = f' …and {len(items) - 6} more.' if len(items) > 6 else ''
        out.append(Issue(INFO, 'Play-test', f'{len(items)} {plural} when the scene is shown on its own', first.line,
                         name, snippet='\n'.join(i.snippet for i in items[:10]),
                         what='Worth checking: ' + ' '.join(parts) + more, fix=first.fix))
    return out


def make_issues(res, state):
    """Turn the recorded play results into findings. Every finding names the values that caused it."""
    issues = []
    done = state.get('scenes', {})
    file_status = state.get('file_status', {})
    total_b = covered_b = renders = 0
    missed = []
    bad_files = {}
    boxes = {}                 # (scene or part that shows it, message) -> [(scene played, job, button)]
    texts = {p.name: p.text for p in res.context.passages} if res.context is not None else {}
    all_text = '\n'.join(texts.values())
    for name, r in done.items():
        start = res.passages.get(name, (0, []))[0]
        renders += r['renders']
        total_b += len(r['branches'])
        covered_b += len(r['covered'])
        for b in r['branches']:
            if str(b['id']) not in r['covered']:
                missed.append((name, b, start + b.get('nl', 0), why_missed(b, r['covered'])))
        for msg, (job, count, at_start, label, *rest) in r['errors'].items():
            if NEEDS_HISTORY.search(msg):
                continue  # going back needs the scenes played before; a scene played on its own has none
            owner = rest[0] if rest and rest[0] else name     # a header/footer or included part shows the box
            boxes.setdefault((owner, msg), []).append((name, _job(job), label))
        for u, job in r['files'].items():
            path, status = file_status.get(u, ('', 0))
            if status >= 400:
                bad_files.setdefault(urllib.parse.unquote(path).lstrip('/'), []).append((name, start, _job(job), status))
    # a box that depends on values and shows up in many scenes is one finding, not one per scene
    merged, first_owner = {}, {}
    for (owner, msg), where in boxes.items():
        if msg.startswith('The scene froze') or written_error(msg, texts.get(owner, '')):
            merged[(owner, msg)] = where
        else:
            merged.setdefault((first_owner.setdefault(msg, owner), msg), []).extend(where)
    for (owner, msg), where in merged.items():
        where.sort(key=lambda w: (w[0] != owner, not realistic(w[1])))
        name, job, label = where[0]
        start = res.passages.get(owner, (0, []))[0]
        how = show_values(job) + (f' and the player clicks "{label}"' if label else '')
        shows = f'"{name}"' if owner == name else f'"{name}" (in the part "{owner}" it includes)'
        others = sorted({w[0] for w in where} - {name})
        also = f' The same happens in {len(others)} other scene(s): {", ".join(others[:8])}.' if others else ''
        if msg.startswith('The scene froze'):
            issues.append(Issue(WARNING, 'Play-test', 'The scene froze the game while it was tested', start, owner,
                                what=f'Worth checking: when the game shows {shows} with {how}, it stops '
                                     'answering. It may contain an endless loop, or it only works after an '
                                     'earlier scene has set the values it uses.',
                                fix='Play to this scene and see if the game freezes. If it does, look for a loop '
                                    'that never ends.'))
        elif written_error(msg, texts.get(owner, '')):
            issues.append(Issue(ERROR, 'Play-test', f'Error box when the scene is shown: {msg[:120]}', start, owner,
                                snippet=msg,
                                what=f'Confirmed while playing: when the game shows {shows} with {how}, players '
                                     f'see a red error box saying: {msg}{also}',
                                fix=playtest.playtest_fix(msg)))
        else:
            issues.append(Issue(INFO, 'Play-test', f'Error box when the scene is shown on its own: {msg[:120]}',
                                start, owner, snippet=msg,
                                what=f'Worth checking: when the game shows {shows} on its own with {how}, '
                                     f'players see a red error box saying: {msg}. In the real game an earlier '
                                     'scene may set the values this scene needs first, so players may never '
                                     f'see it.{also}',
                                fix='Play to this scene the normal way. If the red box shows up: ' + playtest.playtest_fix(msg)))
    for rel, where in sorted(bad_files.items()):
        # a name written in the game, or worked out from values a player really has, is confirmed;
        # one worked out from unusual values (or from a value the scene did not have) is worth checking
        where.sort(key=lambda w: not realistic(w[2]))
        name, start, job, status = where[0]
        others = sorted({w[0] for w in where[1:]})
        also = f' It is also asked for in {len(others)} other scene(s): {", ".join(others[:8])}.' if others else ''
        base = rel.rsplit('/', 1)[-1]
        unset = UNSET_FILE_RE.search(base.rsplit('.', 1)[0]) or UNSET_FILE_RE.search(rel)
        written = '.' in base and len(rel) > 4 and rel in all_text
        if not unset and (written or realistic(job)):
            issues.append(Issue(ERROR, 'Play-test', f'File failed to load while playing: {rel}', start, name, snippet=rel,
                                what=f'Confirmed while playing: when the game shows "{name}" with {show_values(job)}, '
                                     f'it asks for "{rel}", but the file is not there (error {status}). Players see a '
                                     f'broken picture or hear nothing.{also}',
                                fix='Add the missing file, or correct the name the scene uses.'))
        else:
            why = ('the name is made from a value the scene did not have' if unset
                   else 'the name is made from values that players may never have')
            issues.append(Issue(INFO, 'Play-test', f'File failed to load when the scene is shown on its own: {rel}',
                                start, name, snippet=rel,
                                what=f'Worth checking: when the game shows "{name}" on its own with {show_values(job)}, '
                                     f'it asks for "{rel}", but the file is not there (error {status}). Here {why}, '
                                     f'so players may never see this.{also}',
                                fix='Play to this scene the normal way and see if a picture or sound is missing.'))
    issues = _group_worth_checking(issues)
    if missed:
        why = collections.Counter(w for n, b, ln, w in missed)
        lines = [f'  "{n}" line {ln}: <<{b["kind"]}{(" " + b["cond"]) if b["cond"] else ""}>> ({w})'
                 for n, b, ln, w in missed[:60]]
        more = f'\n  …and {len(missed) - 60} more.' if len(missed) > 60 else ''
        issues.append(Issue(INFO, 'Play-test', f'{len(missed)} branch(es) never ran with any values tried', 0, '',
                            snippet='\n'.join(lines[:10]),
                            what='The Full scan tried many values but these parts of scenes were never shown, so '
                                 'problems inside them could not be checked by playing. Why: '
                                 + '; '.join(f'{w}: {c}' for w, c in why.most_common()) + '.',
                            fix='Nothing to fix unless you expect players to see these parts:\n' + '\n'.join(lines) + more))
    summary = dict(passages=len(done), renders=renders, branches=total_b, covered=covered_b,
                   files=len(file_status), missing_files=len(bad_files), seconds=int(state.get('seconds', 0)),
                   finished=state.get('finished', False), missed=[(n, b['id'], ln, w) for n, b, ln, w in missed])
    return issues, summary


# ---------------------------------------------------------------------- Quick scan results, checked by playing
QUICK_FILE_RE = re.compile(r'(?:not found|missing|failed)[^:]*:\s*(\S[^|]*?\.\w{2,5})\s*$', re.I)
QUICK_NAME_RE = re.compile(r'"([^"]+)"|<<([\w-]+)>>|\(([\w-]+):\)')
SET_RE = r'(?:<<set\b[^>]*|\((?:set|put|move):[^)]*|State\.variables\.)'


def _branch_at(branches, rel_line, text):
    """The innermost branch of a scene that holds this line of the scene (None when outside every branch)."""
    best = None
    for b in branches:
        last = b['nl'] + text.count('\n', b['at'], b['end']) if b.get('end', -1) >= 0 else b['nl']
        if b['nl'] <= rel_line <= last and (best is None or b['nl'] >= best['nl']):
            best = b
    return best


def _needs(branches, line, text):
    """The condition a link on this line needs (only a plain (if:)/<<if>> right around it), or ''."""
    b = _branch_at(branches, line, text)
    return ' '.join(b['cond'].split()) if b and b['kind'] == 'if' else ''


def merge_quick(res, quick, state):
    """Lay the Quick scan's findings next to what playing showed. Returns (quick findings, changed in place,
    then the Full scan's own findings that the Quick scan did not have).
    - A Quick Error seen again while playing gets "Confirmed while playing" (and the Full copy is dropped).
    - Moved to "worth checking" (Info) only with a reason found while playing: the file loaded fine, or the
      players-never-see-this rule (the scene opens only behind a condition, the problem sits in the branch for
      the opposite). Everything else stays as the Quick scan said; a part never shown gets a note."""
    full, summary = make_issues(res, state)
    done = state.get('scenes', {})
    file_ok = {urllib.parse.unquote(p).lstrip('/').lower() for p, status in state.get('file_status', {}).values()
               if 200 <= status < 400}
    texts = {p.name: p.text for p in res.context.passages} if res.context is not None else {}
    marked = res.passage_texts or texts
    incoming = collections.defaultdict(list)
    for f, t, off, *_ in res.link_edges:
        incoming[t].append((f, off))
    used = set()
    for q in quick:
        if q.severity != ERROR or q.passage not in done:
            continue
        scene = done[q.passage]
        names = [a or b or c for a, b, c in QUICK_NAME_RE.findall(q.title)]
        fm = QUICK_FILE_RE.search(q.title)
        same = None
        for n, f in enumerate(full):
            if n in used or f.severity != ERROR:
                continue
            if fm and f.title.startswith('File failed') and f.snippet.lower().endswith(fm.group(1).strip().lower()):
                same = n
            elif not fm and f.passage == q.passage and any(x and x in f.snippet for x in names):
                same = n
            if same is not None:
                break
        if same is not None:
            used.add(same)
            q.what = f'{q.what}\n\n{full[same].what}'
            continue
        if fm and fm.group(1).strip().lower() in file_ok:
            q.severity = INFO
            q.what = (f'{q.what}\n\nWorth checking: while playing, "{fm.group(1).strip()}" loaded fine, so players '
                      'probably see it.')
            continue
        start = res.passages.get(q.passage, (0, []))[0]
        text = texts.get(q.passage, '')
        b = _branch_at(scene['branches'], q.line - start, text)
        if b is None:
            continue
        # players never see this: every link into the scene needs condition C, and this part needs C false
        ways_in = incoming.get(q.passage, [])
        needs = {_needs(done[f]['branches'], marked.get(f, '').count('\n', 0, off), texts.get(f, ''))
                 if f in done else '' for f, off in ways_in}
        c = next(iter(needs)) if len(needs) == 1 else ''
        prior = [' '.join(p.split()) for p in b['prior']]
        changed = any(re.search(SET_RE + r'[^\n]*' + re.escape('$' + k[0]), text[:b['at']])
                      for k in paths_in(c)) if c else True
        if c and b['kind'] in ('else', 'elseif') and c in prior and not changed:
            q.severity = INFO
            q.what = (f'{q.what}\n\nWorth checking: players never see this. "{q.passage}" only opens from links '
                      f'that need {c}, and this part only shows when that is not true.')
        elif str(b['id']) not in scene['covered']:
            q.what = (f'{q.what}\n\nNot checked by playing: this part of "{q.passage}" never showed with any values '
                      f'the Full scan tried ({why_missed(b, scene["covered"])}).')
    return quick + [f for n, f in enumerate(full) if n not in used], summary



# ---------------------------------------------------------------------- long runs: estimate, saved progress
STATE_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'GameDoctor', 'fullscan')
BRANCH_RE = re.compile(r'<<(?:if|elseif|else|case|default)\b|\((?:if|else-?if|else|unless):', re.I)
MS_PER_PLAY = (10, 120)   # fastest and slowest seen on the test games (Condemned Town ... Family Ties)


def can_play(res):
    fmt = res.stats.get('format', '').lower()
    return res.engine == 'twine' and fmt.startswith(('sugarcube', 'harlowe', 'x-lowe'))


def estimate(res):
    """(scenes, branches, fewest minutes, most minutes) before starting, counted from the text alone."""
    texts = {p.name: p.text for p in res.context.passages} if res.context is not None else {}
    names = scene_names(res)
    branches = [len(BRANCH_RE.findall(texts.get(n, ''))) for n in names]
    plays = sum(min(MAX_BUDGET, 4 + 2 * b) for b in branches)
    return len(names), sum(branches), *(max(1, round(plays * ms / 60000)) for ms in MS_PER_PLAY)


def _stamp(res):
    path = os.path.abspath(res.html_path)
    return {'path': path, 'size': os.path.getsize(path), 'mtime': int(os.path.getmtime(path))}


def state_file(res):
    return os.path.join(STATE_DIR, hashlib.sha1(os.path.abspath(res.html_path).lower().encode()).hexdigest()[:16] + '.json')


def load_state(res):
    """The saved Full scan of this game, or None (none saved, or the game file changed since)."""
    try:
        with open(state_file(res), encoding='utf-8') as f:
            saved = json.load(f)
        return saved['state'] if saved.get('stamp') == _stamp(res) else None
    except (OSError, ValueError, KeyError):
        return None


def save_state(res, state):
    """Written after every group of scenes, so a closed app or a crash loses little."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = state_file(res) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'stamp': _stamp(res), 'state': state}, f)
    os.replace(tmp, state_file(res))


def clear_state(res):
    try:
        os.remove(state_file(res))
    except OSError:
        pass


def share_done(res, state):
    names = scene_names(res)
    return sum(1 for n in names if n in state.get('scenes', {})) / max(1, len(names))


if __name__ == '__main__':
    import sys
    import scanner
    res = scanner.scan_any(sys.argv[1])
    t = time.time()
    issues, summary = run(res, progress=lambda p, m: print(f'  {p}% {m}', flush=True) if p % 10 == 0 else None)
    print(json.dumps({k: v for k, v in summary.items() if k != 'missed'}, indent=1))
    for i in issues:
        print(f'{i.severity} | {i.passage} | {i.title}\n    {i.what[:300]}')
    print(f'{time.time() - t:.0f}s')
