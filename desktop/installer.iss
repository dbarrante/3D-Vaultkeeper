[Setup]
AppName=3D Vaultkeeper
AppVersion=0.1.0
AppPublisher=3D Vaultkeeper
DefaultDirName={autopf}\3D Vaultkeeper
DefaultGroupName=3D Vaultkeeper
UninstallDisplayIcon={app}\3D Vaultkeeper.exe
OutputDir=installer_output
OutputBaseFilename=3DVaultkeeper-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "dist\3D Vaultkeeper\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
Source: "..\THIRD-PARTY-LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\3D Vaultkeeper"; Filename: "{app}\3D Vaultkeeper.exe"
Name: "{group}\Uninstall 3D Vaultkeeper"; Filename: "{uninstallexe}"
Name: "{autodesktop}\3D Vaultkeeper"; Filename: "{app}\3D Vaultkeeper.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
