@echo off
rem Builds dist\GameDoctor.exe (one file, no console window).
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || goto :error
    .venv\Scripts\python -m pip install pyinstaller || goto :error
)
if not exist icon.ico .venv\Scripts\python make_icon.py
rem http and email stay in: the play-test runs a small local web server.
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name GameDoctor --icon icon.ico --add-data "icon.ico;." --add-data "lang;lang" ^
    --exclude-module unittest --exclude-module pydoc --exclude-module xml --exclude-module pdb ^
    app.py || goto :error
echo.
echo Built: %~dp0dist\GameDoctor.exe
exit /b 0
:error
echo Build failed.
exit /b 1
