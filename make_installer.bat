@echo off
rem Builds installer\GameDoctor-2.0.1-Setup.exe with Inno Setup (run build.bat first).
cd /d "%~dp0"
if not exist dist\GameDoctor.exe call build.bat || goto :error
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
    echo Inno Setup 6 was not found. Get it free from https://jrsoftware.org/isinfo.php
    goto :error
)
"%ISCC%" /Q installer.iss || goto :error
echo.
echo Built: %~dp0installer\GameDoctor-2.0.1-Setup.exe
exit /b 0
:error
echo Installer build failed.
exit /b 1
