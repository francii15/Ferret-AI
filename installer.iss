#define MyAppName "Ferret"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Ferret AI"
#define MyAppExeName "Ferret.exe"

[Setup]
AppId={{B9BEF125-6F04-4DA7-BD84-1CF78AA44B14}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Ferret
DefaultGroupName=Ferret
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=Ferret_Setup_1.0.0
SetupIconFile=ferret.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\Ferret.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Ferret"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Ferret"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Ferret"; Flags: nowait postinstall skipifsilent
