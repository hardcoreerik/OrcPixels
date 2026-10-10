; OrcPixels Windows installer (Inno Setup 6). Built by tools\build_installer.ps1:
;   ISCC /DAppVersion=x.y.z /DSrcDir=<dist\OrcPixels> packaging\OrcPixels.iss
; Program files -> {autopf}\OrcPixels (Program Files; /CURRENTUSER -> per-user Programs).
; User config + data -> %APPDATA%\OrcPixels (never Program Files). Library -> wizard page.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SrcDir
  #define SrcDir "..\dist\OrcPixels"
#endif

[Setup]
AppId={{6C1E0B7A-2F4D-4E8B-9C1A-0A7E5D3B9F21}
AppName=OrcPixels
AppVersion={#AppVersion}
AppPublisher=Oregon Radio Company LLC
AppPublisherURL=https://github.com/hardcoreerik/OrcPixels
DefaultDirName={autopf}\OrcPixels
DefaultGroupName=OrcPixels
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist
OutputBaseFilename=OrcPixels-Setup-{#AppVersion}
SetupIconFile=orcpixels.ico
UninstallDisplayIcon={app}\OrcPixels.exe
WizardImageFile=wizard_large.bmp
WizardSmallImageFile=wizard_small.bmp
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "&Start OrcPixels when Windows starts"; Flags: unchecked

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OrcPixels"; Filename: "{app}\OrcPixels.exe"
Name: "{autoprograms}\Uninstall OrcPixels"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OrcPixels"; Filename: "{app}\OrcPixels.exe"; Tasks: desktopicon

[Registry]
; per-user autostart (the tray menu toggles the same value; uninstall always removes it)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OrcPixels"; ValueData: """{app}\OrcPixels.exe"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\OrcPixels.exe"; Description: "Start OrcPixels now"; Flags: nowait postinstall skipifsilent
; OTA update (/SILENT /RELAUNCH=1 from the running app): start it again, as the normal user
Filename: "{app}\OrcPixels.exe"; Parameters: "--no-browser"; Flags: nowait runasoriginaluser; Check: IsRelaunch

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM OrcPixels.exe"; Flags: runhidden; RunOnceId: "StopOrcPixels"

[Code]
var
  LibPage: TInputDirWizardPage;

function ConfigDir(): String;
begin
  // /CONFIGDIR=<folder> (testing): write install.json there instead of %APPDATA%\OrcPixels
  Result := ExpandConstant('{param:CONFIGDIR|{userappdata}\OrcPixels}') + '\config';
end;

procedure InitializeWizard;
begin
  LibPage := CreateInputDirPage(wpSelectDir, 'Library folder',
    'Where should OrcPixels keep your imported media?',
    'GIFs and images you import (GIPHY, Tenor, your own files) and the display-sized presets made from them are stored here. Settings stay in ' + ConfigDir() + '.',
    False, 'OrcPixels Library');
  LibPage.Add('');
  LibPage.Values[0] := ExpandConstant('{param:LIBRARYDIR|{userdocs}\OrcPixels Library}');
end;

function IsRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function JsonEscape(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := S;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(ConfigDir());
    ForceDirectories(LibPage.Values[0]);
    SaveStringToFile(ConfigDir() + '\install.json',
      '{"library_dir": "' + JsonEscape(LibPage.Values[0]) + '", "installed_version": "{#AppVersion}"}' + #13#10, False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'OrcPixels');
  // user data (%APPDATA%\OrcPixels, the Library) is kept on purpose
end;
