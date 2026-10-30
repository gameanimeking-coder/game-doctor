# Game Doctor

A free Windows tool that scans a game for bugs and explains, in plain words, what is wrong, what players will notice and how to fix it. It only reads the game; every fix is a suggestion you choose to make.

**Works with:** Twine (SugarCube, Harlowe, Chapbook, Snowman), other HTML games, Ren'Py, RPG Maker MV/MZ, Ink and ChoiceScript.

![Problem and fix](screenshots/2%20-%20problem%20and%20fix.png)

## What it checks
- Broken links, includes and jumps, unreachable scenes
- Missing pictures, sounds and videos, wrong capital letters in file names, unused and damaged files
- Unclosed or misspelled macros, broken conditions, variables used but never set
- HTML tags left open, duplicate ids, CSS classes with no style, JavaScript errors
- Placeholder text and common typos
- **Play-test:** plays every scene in an invisible Edge/Chrome window and reports the errors players would see

Simple-words mode, a fix wizard, a code view, bug reports for players, 27 languages, and an optional AI helper (your own API key, or LM Studio / Ollama) that suggests fixes into a copy of the game.

## Download
Get the installer from [Releases](../../releases). Windows 10/11, 64-bit.
Windows SmartScreen or Defender may warn because the installer is not code-signed; this is a known false alarm for Python apps packed into one .exe.

## Build it yourself
Needs Python 3. Run `build.bat` (makes `dist\GameDoctor.exe`), then `make_installer.bat` (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)).
Tests: `python tests\run_tests.py` and `python app.py --selftest`. More details in `README.txt`.

## License
GPL-3.0
