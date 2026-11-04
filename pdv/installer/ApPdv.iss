[Setup]
AppId={{A39E523E-6B90-4753-B524-7BF47E0BAF57}
AppName=ApPdv
AppVersion=0.2.0
AppPublisher=ApPdv
DefaultDirName={autopf}\ApPdv
DefaultGroupName=ApPdv
OutputDir=..\dist\installer
OutputBaseFilename=ApPdv-Setup
SetupIconFile=..\assets\appdv-icon.ico
UninstallDisplayIcon={app}\ApPdv.exe
ArchitecturesInstallIn64BitMode=x64
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\ApPdv\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ApPdv"; Filename: "{app}\ApPdv.exe"
Name: "{autodesktop}\ApPdv"; Filename: "{app}\ApPdv.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"
