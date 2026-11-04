; Inno Setup script for the Game Doctor installer.
; Build it with make_installer.bat (after build.bat has made dist\GameDoctor.exe).

#define AppName "Game Doctor"
#define AppVersion "3.5.0"
#define AppExe "GameDoctor.exe"

[Setup]
; The AppId identifies this app for updates and uninstalling. Never change it.
AppId={{8DCB3FC2-87C6-4B24-8DFF-2833B023EADA}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppComments=Finds bugs in Twine, HTML, Ren'Py, RPG Maker, Ink and ChoiceScript games and explains how to fix them.
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Ask "only for me" (no admin needed) or "for all users"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer
OutputBaseFilename=GameDoctor-{#AppVersion}-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra
SolidCompression=yes
CloseApplications=yes
InfoBeforeFile=installer_info.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.txt"; DestDir: "{app}"; DestName: "Read me.txt"; Flags: ignoreversion isreadme

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName} - Read me"; Filename: "{app}\Read me.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
