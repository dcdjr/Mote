#define AppVersion "0.2.0"
[Setup]
AppId=Mote.Desktop.Assistant
AppName=Mote
AppVersion={#AppVersion}
AppPublisher=Mote
AppPublisherURL=https://github.com/dcdjr/Mote
DefaultDirName={localappdata}\Programs\Mote
DefaultGroupName=Mote
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=MoteSetup
SetupIconFile=..\assets\mote.ico
UninstallDisplayIcon={app}\Mote.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile=INSTALL.txt
CloseApplications=yes
RestartApplications=no
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "..\dist\Mote\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Mote"; Filename: "{app}\Mote.exe"; WorkingDir: "{app}"; AppUserModelID: "Mote.Desktop.Assistant"
Name: "{autodesktop}\Mote"; Filename: "{app}\Mote.exe"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "Mote.Desktop.Assistant"
[Run]
Filename: "{app}\Mote.exe"; Description: "Launch Mote"; Flags: nowait postinstall skipifsilent
