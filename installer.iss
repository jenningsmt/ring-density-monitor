; Inno Setup script for Ring Density Monitor.
;
; Builds a self-extracting, per-user installer (no admin rights required) from
; the PyInstaller onedir output. Build order:
;   pip install -r requirements-dev.txt
;   pyinstaller RingDensityMonitor.spec --clean
;   iscc installer.iss
; The resulting installer is written to dist-installer\ and is meant to be
; attached to a GitHub Release, not committed to the repo.

#define MyAppName "Ring Density Monitor"
#define MyAppVersion "1.0.1"
#define MyAppExeName "RingDensityMonitor.exe"

[Setup]
AppId={{9C1F2E4A-6B3D-4C8E-9A7F-3D2B1E5C8F6A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\RingDensityMonitor
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
OutputDir=dist-installer
OutputBaseFilename=RingDensityMonitor-Setup-{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

; Wipe the PyInstaller bundle directory before installing the new one, so an
; upgrade can't accumulate files a prior release shipped but this one no
; longer does (e.g. after a Python/PyInstaller version bump). The only user
; data this app has (%APPDATA%\RingDensityMonitor\config.json) lives entirely
; outside {app}, so this never touches it.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\RingDensityMonitor\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
