GAME DOCTOR 2.0
===============

Finds problems in games and explains, in plain words, what is wrong, what
players will notice and exactly how to fix it. It ONLY READS files; it never
changes your game. The fixes are up to you.

Works with:
- Twine games: SugarCube (most checks), Harlowe, Chapbook, Snowman
- Other HTML games (general HTML / CSS / JavaScript / file checks)
- Ren'Py, RPG Maker MV/MZ, Ink and ChoiceScript (open the game folder)

The program:  dist\GameDoctor.exe   (one file, can be copied anywhere)


HOW TO USE
----------
1. Start GameDoctor.exe and click "Open game...", or drag the game's .html
   file onto GameDoctor.exe. For Ren'Py, RPG Maker, Ink or ChoiceScript use
   Tools > Open a game folder. The check starts by itself.
2. The pictures / sounds folder is the folder the game is in. Use
   Tools > Pictures and sounds folder... if the files are somewhere else.
3. Click a problem. Below the list you see where it is, what is wrong, what
   players will notice, whether you can ignore it, how hard it is to fix and
   step-by-step how to fix it, with the line "Now" and "Change to".
4. Choose how you edit at the top: "I edit in Twine" shows the line the way
   Twine shows it; "I edit the .html file" shows it the way it is really
   stored in the file (< and > written as &lt; and &gt;), ready to paste.
5. "Open in my text editor" opens the file at that line. Tools > Text editor...
   picks the editor (Notepad++, Notepad3, Sublime Text, VS Code, EmEditor and
   Notepad are found automatically, or Browse for any other program).
6. After fixing: save, then click "I fixed it - check". It tells you if the
   problem is gone and whether the fix caused a new one.
7. New here? Help > Take the tour, Help > Lessons (F1) and Help > Word list.

Red = Broken: players will run into it
Yellow = Probably broken
Blue = Worth checking, often fine


HELPERS
-------
- Fix wizard: goes through the problems one by one, most important first,
  and checks each fix.
- "Start here" list in the summary: the fixes that matter most.
- Test a line before you change it: type a line (or a whole passage) and see
  if it has problems, without touching the game.
- Back up the game file, Compare with another version (what got fixed, what
  is new), Write a bug report (plain text, Discord/Reddit or forum BBCode).
- Watch the game file: checks again every time you save.
- Mark as intentional: hides a finding you checked and want to keep (per
  game). Tools > Show problems marked as intentional brings them back.
- "How do players reach this scene?" shows the links and conditions that
  lead there.
- Player mode: a short, low-spoiler list of broken scenes and a bug report
  for the developer.
- Read aloud, Extra-large text, Colour-blind friendly colours, Simple words.


PLAY-TEST
---------
"Play-test" plays every scene in an invisible Edge (or Chrome) window, in
several ways (start values, high values, zero values, flipped yes/no), and
reports red error boxes, scripts the browser cannot read and pictures or
sounds that fail to load while playing. Needs Microsoft Edge or Google
Chrome. Nothing is sent anywhere: the game is served from your own computer
(127.0.0.1) and the browser uses a temporary profile that is deleted after.


AI HELPER (in English, German, Spanish, French, Portuguese, Russian, Japanese and Chinese)
---------
Tools > Fix with AI… sends the problems and the lines around them to an AI and
shows every change it suggests as "Now" / "Change to". Untick the ones you
don't want, then "Save fixed copy…". Your game file is never changed: the
fixes go into a copy (e.g. "game (AI fixed).html") plus a "changes.txt" list,
and the copy is scanned again to show what got fixed and whether anything new
broke. Changes far from a problem, or text that is not really on the line,
are refused.
Tools > AI helper settings… picks the AI:
- Online (needs an API key from their website, they charge per use):
  Claude, Gemini, ChatGPT, Grok, DeepSeek, Mistral, Groq, OpenRouter.
- On this computer (free, nothing leaves the computer): LM Studio, Ollama,
  or "Other" for any program that works like OpenAI.
The key is stored scrambled by Windows (only your Windows account can read it).
Tools > Export log for AI (text)… saves a plain-text log of the scan with every
problem, its line, the code around it and the steps to fix it, written for an
AI to read. Give it to any AI chat together with the game file or folder.


CODE HEALTH
-----------
  100% excellent · 90% great · 80% good · 70% fine · below 70% needs work
Based on how many scenes have problems and how serious they are. 100% means
nothing was found. Problems marked as intentional do not count.


CODE VIEW
---------
Shows the whole game file with coloured line numbers (green = no problems,
blue = note, yellow = warning, red = error). The strip on the right shows
where problems are; click it to jump. Previous / Next problem, Find (Ctrl+F,
F3) and "Line ... Go". "Show exactly as stored in the file" switches between
the readable text and the real escaped text.


WHAT IT CHECKS
--------------
- Links / includes / gotos to scenes that do not exist (with "did you mean"),
  duplicate, empty and unreachable scenes
- Pictures, sounds, videos, fonts and files that are missing, have wrong
  capital letters, use "C:\..." paths or "\" instead of "/"; unused, empty,
  very large, cut-off, fake (a web page saved as .jpg) or wrongly-named files
- Macro tags: unclosed <<if>>, extra closing tags, <<else>> outside <<if>>,
  missing or extra ">" / "<", unclosed quotes and brackets, unknown and
  misspelled macros, outdated macros
- Conditions: "=" instead of "is", "=>" / "=<", "is not", "$x is 1 or 2",
  "and" and "or" mixed without brackets
- <<set>> that compares instead of setting, or changes nothing
- Variables used but never set, set but never used, near-duplicate names
- Widgets, HTML tags, duplicate ids, CSS class names, JavaScript brackets
- Text: PLACEHOLDER / TODO left in, common spelling mistakes, doubled words
- Harlowe / Chapbook / Snowman, Ren'Py, RPG Maker, Ink and ChoiceScript have
  their own rules (labels, jumps, indentation, event targets, switches...)


LANGUAGES
---------
The window can be shown in 27 languages (the globe menu at the top right):
English, Spanish, French, German, Italian, Portuguese, Dutch, Swedish,
Norwegian, Danish, Finnish, Polish, Czech, Hungarian, Romanian, Russian,
Ukrainian, Turkish, Greek, Simplified and Traditional Chinese, Japanese,
Korean, Vietnamese, Indonesian, Thai and Hindi.
Translations other than English were made by an AI and may contain
mistakes. Technical names stay in English. Right-to-left languages (Arabic,
Hebrew, Persian) are not included because the window toolkit cannot lay
them out. Language files are plain text in the "lang" folder of the source.


SETTINGS
--------
Theme (System / Light / Dark), language and size are on the top right.
Ctrl + / Ctrl - / Ctrl+0 or Ctrl + mouse wheel change the size.
Ctrl+O open, F5 scan again, Ctrl+F search, F3 find next, Ctrl+E export,
F1 lessons.
Settings (including problems marked as intentional) are saved in
%APPDATA%\GameDoctor\settings.json


COMMAND LINE
------------
GameDoctor.exe "game.html" "report.html" --report [--simple]
writes a report without opening the window (--simple = plain words).


REBUILDING THE .EXE
-------------------
Needs Python 3. Double-click build.bat (makes dist\GameDoctor.exe).
Then make_installer.bat makes installer\GameDoctor-2.0-Setup.exe (needs the free
Inno Setup 6 from https://jrsoftware.org; the installer script is installer.iss).
Main source files: app.py (window), ui_dialogs.py (other windows),
scanner.py (HTML / Twine checks), twine_formats.py, formats.py (other
engines), media.py (damaged files), playtest.py, fixes.py (fix lines),
reach.py, simple.py and content.py (plain-words texts, lessons),
ai.py + ai_dialogs.py (AI helper),
i18n.py + lang\ (translations), knowledge.py (macro lists, misspellings).
Tests: python tests\run_tests.py   ·   python app.py --selftest
Translation check: python tools\strings.py check
