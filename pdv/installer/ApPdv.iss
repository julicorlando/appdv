; A versão vem de pdv/app/__init__.py (build_windows.bat e o GitHub Actions passam /DAppVer=...).
#ifndef AppVer
  #define AppVer "0.3.3"
#endif

[Setup]
; Atualização pelo próprio PDV: fecha o ApPdv em execução antes de substituir os arquivos.
CloseApplications=yes
AppId={{A39E523E-6B90-4753-B524-7BF47E0BAF57}
AppName=ApPdv
AppVersion={#AppVer}
VersionInfoVersion={#AppVer}
AppPublisher=ApPdv
DefaultDirName={autopf}\ApPdv
DefaultGroupName=ApPdv
OutputDir=..\dist\installer
OutputBaseFilename=ApPdv-Setup-{#AppVer}
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

[Run]
Filename: "{app}\ApPdv.exe"; Description: "Abrir o ApPdv"; Flags: nowait postinstall

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"
