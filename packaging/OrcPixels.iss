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
; Only our own .exe files count as "in use" for Restart Manager. A browser started from
; OrcPixels used to inherit our DLL folder (PyInstaller SetDllDirectory), and its helpers
; (e.g. a Chrome extension's native host) kept _internal\VCRUNTIME140.dll loaded. With the
; default filter (*.exe,*.dll,*.chm) Setup then had to close those foreign processes, could
; not, and the silent update rolled back. The shared runtime DLLs are handled in [Files].
CloseApplications=force
CloseApplicationsFilter=*.exe
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "&Start OrcPixels when Windows starts"; Flags: unchecked

[Files]
#define RuntimeDlls "VCRUNTIME140*.dll,MSVCP140*.dll,ucrtbase.dll,api-ms-win-*.dll,concrt140.dll"
Source: "{#SrcDir}\*"; Excludes: "{#RuntimeDlls}"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Microsoft runtime DLLs: other programs may have them loaded (see CloseApplicationsFilter).
; Version-checked (same version = skipped, so a locked copy doesn't matter); a newer one that
; is locked is replaced at the next restart instead of failing the update.
Source: "{#SrcDir}\VCRUNTIME140*.dll"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs restartreplace uninsrestartdelete skipifsourcedoesntexist
Source: "{#SrcDir}\MSVCP140*.dll"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs restartreplace uninsrestartdelete skipifsourcedoesntexist
Source: "{#SrcDir}\ucrtbase.dll"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs restartreplace uninsrestartdelete skipifsourcedoesntexist
Source: "{#SrcDir}\api-ms-win-*.dll"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs restartreplace uninsrestartdelete skipifsourcedoesntexist
Source: "{#SrcDir}\concrt140.dll"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs restartreplace uninsrestartdelete skipifsourcedoesntexist

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
  InstallDone: Boolean;

const
  SYNCHRONIZE = $00100000;
  WAIT_OBJECT_0 = 0;

function OpenProcess(dwDesiredAccess: Cardinal; bInheritHandle: LongBool; dwProcessId: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): LongBool;
  external 'CloseHandle@kernel32.dll stdcall';

function ConfigDir(): String;
begin
  // /CONFIGDIR=<folder> (testing): write install.json there instead of %APPDATA%\OrcPixels
  Result := ExpandConstant('{param:CONFIGDIR|{userappdata}\OrcPixels}') + '\config';
end;

function IsRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

// Number of running OrcPixels.exe processes started from Dir ('' = from any folder).
function RunningInstances(Dir: String): Integer;
var
  Locator, Svc, Procs, P: Variant;
  I: Integer;
begin
  Result := 0;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Svc := Locator.ConnectServer('.', 'root\CIMV2');
    Procs := Svc.ExecQuery('SELECT ExecutablePath FROM Win32_Process WHERE Name = ''OrcPixels.exe''');
    for I := 0 to Procs.Count - 1 do
    begin
      P := Procs.ItemIndex(I);
      if (Dir = '') or VarIsNull(P.ExecutablePath) or
         (CompareText(ExtractFileDir(P.ExecutablePath), RemoveBackslashUnlessRoot(Dir)) = 0) then
        Result := Result + 1;
    end;
  except
    Log('Could not list running OrcPixels processes: ' + GetExceptionMessage);
  end;
end;

// OTA update: the app starts Setup, then exits. Wait until it is gone *before* Setup checks
// for files in use; otherwise Setup races the shutdown, finds OrcPixels.exe still running
// and, being silent with /SUPPRESSMSGBOXES, rolls back.
procedure WaitForApp();
var
  Pid, Waited: Integer;
  H: THandle;
  Dir: String;
begin
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid > 0 then
  begin
    H := OpenProcess(SYNCHRONIZE, False, Pid);
    if H <> 0 then
    begin
      Log(Format('Waiting for OrcPixels (PID %d) to exit...', [Pid]));
      if WaitForSingleObject(H, 60000) = WAIT_OBJECT_0 then
        Log('OrcPixels exited.')
      else
        Log('OrcPixels is still running after 60 s; Restart Manager will close it.');
      CloseHandle(H);
    end else
      Log(Format('OrcPixels (PID %d) is not running.', [Pid]));
  end;
  if IsRelaunch() then
  begin
    // Older apps don't pass /WAITPID: wait for OrcPixels.exe in the target folder by name.
    Dir := ExpandConstant('{param:DIR|}');
    Waited := 0;
    while (RunningInstances(Dir) > 0) and (Waited < 30000) do
    begin
      if Waited = 0 then
        Log('Waiting for OrcPixels.exe in ' + Dir + ' to exit...');
      Sleep(500);
      Waited := Waited + 500;
    end;
    if Waited > 0 then
      Log(Format('Waited %d ms for OrcPixels.exe.', [Waited]));
  end;
end;

function InitializeSetup(): Boolean;
begin
  InstallDone := False;
  WaitForApp();
  Result := True;
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
  if CurStep = ssDone then
    InstallDone := True;
end;

// OTA update that failed or rolled back: start the old version again so the sign isn't left
// dark. The app reads the /LOG file at startup and shows why the update didn't install.
procedure DeinitializeSetup();
var
  Exe: String;
  Rc: Integer;
begin
  if IsRelaunch() and not InstallDone then
  begin
    Exe := AddBackslash(ExpandConstant('{param:DIR|}')) + 'OrcPixels.exe';
    if FileExists(Exe) and (RunningInstances(ExtractFileDir(Exe)) = 0) then
    begin
      Log('Update did not complete; restarting the installed OrcPixels: ' + Exe);
      ExecAsOriginalUser(Exe, '--no-browser', '', SW_SHOWNORMAL, ewNoWait, Rc);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'OrcPixels');
  // user data (%APPDATA%\OrcPixels, the Library) is kept on purpose
end;