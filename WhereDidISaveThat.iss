#define MyAppName "Where Did I Save That?"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Where Did I Save That contributors"
#define MyAppExeName "WhereDidISaveThat.exe"

[Setup]
AppId={{7A48BB79-1F7A-4AE4-A795-95D2B15942CA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Where Did I Save That
DefaultGroupName=Where Did I Save That
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=WhereDidISaveThat-Setup
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopappicon"; Description: "Create an app shortcut on the desktop"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Dirs]
Name: "{code:GetPhotosFolder}"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Where Did I Save That"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Where Did I Save That"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopappicon
Name: "{autodesktop}\Photos - Where Did I Save That"; Filename: "{code:GetPhotosFolder}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent

[Code]
function GetPhotosFolder(Param: string): string;
var
  PicturesPath: string;
begin
  PicturesPath := GetShellFolderByCSIDL($0027, True);
  if PicturesPath = '' then
    PicturesPath := ExpandConstant('{userprofile}\Pictures');
  Result := AddBackslash(PicturesPath) + 'Photos';
end;
