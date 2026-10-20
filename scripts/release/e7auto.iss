#if VER != 0x06070300
  #error Inno Setup 6.7.3 is required
#endif
; Inno Setup 6.7.3. The payload is prepared and sealed by release.workflow.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef SetupOutputDir
  #error SetupOutputDir is required
#endif
#define ProductId "{438C8C44-EB0D-48EA-BA98-6B4589FE35B2}"
#define ModelId "E7auto.Installed"

[Setup]
AppId={{438C8C44-EB0D-48EA-BA98-6B4589FE35B2}
AppName=E7auto
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=E7auto
AppPublisherURL=https://github.com/Ricardo1503449/E7auto
AppSupportURL=https://github.com/Ricardo1503449/E7auto/issues
AppUpdatesURL=https://github.com/Ricardo1503449/E7auto/releases/latest
DefaultDirName={autopf}\E7auto
DisableDirPage=no
UsePreviousAppDir=yes
AlwaysShowDirOnReadyPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
OutputDir={#SetupOutputDir}
OutputBaseFilename=E7auto_v{#AppVersion}_Setup_x64
SetupIconFile={#PayloadDir}\assets\ui\e7auto.ico
UninstallDisplayIcon={app}\E7auto.exe
UninstallDisplayName=E7auto
CreateUninstallRegKey=yes
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupMutex=Global\E7auto.Installed.Setup

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\install-files.txt"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\E7auto"; Filename: "{app}\E7auto.exe"; WorkingDir: "{app}"; AppUserModelID: "{#ModelId}"
Name: "{autodesktop}\E7auto"; Filename: "{app}\E7auto.exe"; WorkingDir: "{app}"; AppUserModelID: "{#ModelId}"; Tasks: desktopicon

[Registry]
Root: HKLM64; Subkey: "Software\Classes\AppUserModelId\{#ModelId}"; ValueType: string; ValueName: "DisplayName"; ValueData: "E7auto"; Flags: uninsdeletekey
Root: HKLM64; Subkey: "Software\Classes\AppUserModelId\{#ModelId}"; ValueType: string; ValueName: "IconUri"; ValueData: "{app}\assets\ui\e7auto-icon-64.png"
Root: HKLM64; Subkey: "Software\Classes\AppUserModelId\{#ModelId}"; ValueType: string; ValueName: "CustomActivator"; ValueData: "{{5914C5E5-CC54-4A2B-A664-DDF892B3D861}"

[Run]
Filename: "{app}\E7auto.exe"; Parameters: "--after-update={param:E7AUTOUPDATEID|}"; WorkingDir: "{app}"; Flags: nowait; Check: IsUpdateMode

[UninstallDelete]
; Only application-owned runtime data. Never delete {app}\*.
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\state"
Type: dirifempty; Name: "{app}"

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#ProductId}_is1';
  RunningMutex = 'Global\E7auto.Installed.Running';
var
  PreviousFiles, IncomingFiles: TArrayOfString;

function ValidUpdateJob(): Boolean;
var Job, Prefix: String; I: Integer;
begin
  Job := ExpandConstant('{param:E7AUTOUPDATEID|}');
  Prefix := 'v{#AppVersion}-';
  Result := False;
  if (Copy(Job, 1, Length(Prefix)) <> Prefix) or
     (Length(Job) <> Length(Prefix) + 32) then Exit;
  for I := Length(Prefix) + 1 to Length(Job) do
    if Pos(Copy(Job, I, 1), '0123456789abcdef') = 0 then Exit;
  Result := True;
end;

function IsUpdateMode(): Boolean;
begin
  Result := (ExpandConstant('{param:E7AUTOUPDATE|}') = '1') and ValidUpdateJob();
end;

function InitializeSetup(): Boolean;
begin
  Result := (ExpandConstant('{param:E7AUTOUPDATE|}') <> '1') or ValidUpdateJob();
  if not Result then MsgBox('更新任务参数无效，请重新检查更新。', mbError, MB_OK);
end;

function FileAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function SameDirectory(A, B: String): Boolean;
begin
  Result := CompareText(RemoveBackslashUnlessRoot(ExpandFileName(A)),
                        RemoveBackslashUnlessRoot(ExpandFileName(B))) = 0;
end;

function SafeRelative(Name: String): Boolean;
begin
  Result := (Name <> '') and (Pos(':', Name) = 0) and (Pos('..', Name) = 0)
    and (Pos('*', Name) = 0) and (Pos('?', Name) = 0) and (Pos('/', Name) = 0)
    and (Copy(Name, 1, 1) <> '\') and (Copy(Name, Length(Name), 1) <> '\')
    and (CompareText(Copy(ExtractFileName(Name), 1, 5), 'unins') <> 0)
    and (CompareText(Name, 'logs') <> 0) and (CompareText(Name, 'state') <> 0)
    and (CompareText(Copy(Name, 1, 5), 'logs\') <> 0)
    and (CompareText(Copy(Name, 1, 6), 'state\') <> 0);
end;

function LoadManifest(Name: String; var Files: TArrayOfString): Boolean;
var I: Integer;
begin
  Result := False;
  if not LoadStringsFromFile(Name, Files) then Exit;
  if GetArrayLength(Files) < 2 then Exit;
  if Files[0] <> '{#ProductId}' then Exit;
  for I := 1 to GetArrayLength(Files) - 1 do
    if not SafeRelative(Files[I]) then Exit;
  Result := True;
end;

function SafeTree(Directory: String): Boolean;
var Item: TFindRec;
begin
  Result := False;
  if (FileAttributes(Directory) <> $FFFFFFFF) and
     ((FileAttributes(Directory) and $400) <> 0) then Exit;
  Result := True;
  if FindFirst(AddBackslash(Directory) + '*', Item) then begin
    try
      repeat
        if (Item.Name <> '.') and (Item.Name <> '..') then begin
          if (Item.Attributes and $400) <> 0 then begin Result := False; Exit; end;
          if (Item.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            if not SafeTree(AddBackslash(Directory) + Item.Name) then begin Result := False; Exit; end;
        end;
      until not FindNext(Item);
    finally FindClose(Item); end;
  end;
end;

function SafeParents(Directory: String): Boolean;
var Parent: String;
begin
  Result := False;
  repeat
    if (FileAttributes(Directory) <> $FFFFFFFF) and
       ((FileAttributes(Directory) and $400) <> 0) then Exit;
    Parent := ExtractFileDir(RemoveBackslashUnlessRoot(Directory));
    if SameDirectory(Parent, Directory) then Break;
    Directory := Parent;
  until Directory = '';
  Result := True;
end;

function EmptyDirectory(Directory: String): Boolean;
var Item: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Directory) + '*', Item) then begin
    try
      repeat
        if (Item.Name <> '.') and (Item.Name <> '..') then begin Result := False; Exit; end;
      until not FindNext(Item);
    finally FindClose(Item); end;
  end;
end;

function DestinationError(Directory: String): String;
var Existing, OldVersion: String; OldPacked, NewPacked: Int64;
begin
  Result := '';
  if (Copy(Directory, 1, 2) = '\\') or
     SameDirectory(Directory, AddBackslash(ExtractFileDrive(Directory))) then begin
    Result := '请选择本地磁盘中的 E7auto 专用文件夹，不能选择磁盘根目录。'; Exit;
  end;
  if not SafeParents(Directory) or not SafeTree(Directory) then begin
    Result := '安装目录不能包含符号链接或目录联接，请选择普通文件夹。'; Exit;
  end;
  if RegQueryStringValue(HKLM64, UninstallKey, 'InstallLocation', Existing) then begin
    if not SameDirectory(Directory, Existing) then begin
      Result := '覆盖升级请使用原安装目录。若要更换位置，请先卸载旧版（日志和状态也会删除）。'; Exit;
    end;
    if RegQueryStringValue(HKLM64, UninstallKey, 'DisplayVersion', OldVersion) and
       StrToVersion(OldVersion, OldPacked) and StrToVersion('{#AppVersion}', NewPacked) then
      if ComparePackedVersion(OldPacked, NewPacked) > 0 then begin
        Result := '已安装更新版本，不能用旧版覆盖。'; Exit;
      end;
  end else if not EmptyDirectory(Directory) then begin
    Result := '首次安装请选择专用空文件夹，不接管未登记目录。'; Exit;
  end;
  if not EmptyDirectory(Directory) and
     not LoadManifest(AddBackslash(Directory) + 'install-files.txt', PreviousFiles) then
    Result := '请选择空文件夹，或已有安装版 E7auto 的目录。请勿覆盖源码、旧免安装目录或其他软件。';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Problem: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    Problem := DestinationError(WizardDirValue);
    Result := Problem = '';
    if not Result then MsgBox(Problem, mbError, MB_OK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var I: Integer; Existing: String;
begin
  Result := DestinationError(ExpandConstant('{app}'));
  if Result <> '' then Exit;
  if IsUpdateMode() then begin
    if not RegQueryStringValue(HKLM64, UninstallKey, 'InstallLocation', Existing) then begin
      Result := '自动更新只适用于已有安装，请重新运行安装向导。'; Exit;
    end;
    for I := 1 to 600 do begin
      if not CheckForMutexes(RunningMutex) then Break;
      Sleep(100);
    end;
  end;
  if CheckForMutexes(RunningMutex) then begin
    Result := 'E7auto 正在运行。请先停止脚本并关闭程序，再继续安装。'; Exit;
  end;
  SetArrayLength(PreviousFiles, 0);
  if FileExists(ExpandConstant('{app}\install-files.txt')) then
    if not LoadManifest(ExpandConstant('{app}\install-files.txt'), PreviousFiles) then begin
      Result := '原安装文件清单无效，无法安全覆盖。'; Exit;
    end;
  ExtractTemporaryFile('install-files.txt');
  if not LoadManifest(ExpandConstant('{tmp}\install-files.txt'), IncomingFiles) then
    Result := '安装包文件清单无效。';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var I, J: Integer; Retained: Boolean; Path: String;
begin
  if CurStep = ssPostInstall then begin
    for I := 1 to GetArrayLength(PreviousFiles) - 1 do begin
      Retained := False;
      for J := 1 to GetArrayLength(IncomingFiles) - 1 do
        if CompareText(PreviousFiles[I], IncomingFiles[J]) = 0 then Retained := True;
      if not Retained then begin
        Path := ExpandConstant('{app}\') + PreviousFiles[I];
        if FileExists(Path) and not DeleteFile(Path) then
          RaiseException('无法删除已停用的程序文件：' + Path);
      end;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
var Files: TArrayOfString;
begin
  Result := False;
  CreateMutex('Global\E7auto.Installed.Setup');
  if CheckForMutexes(RunningMutex) then begin
    SuppressibleMsgBox('请先停止脚本并关闭 E7auto，再卸载。', mbError, MB_OK, IDOK); Exit;
  end;
  if not SafeParents(ExpandConstant('{app}')) or not SafeTree(ExpandConstant('{app}')) or
     not LoadManifest(ExpandConstant('{app}\install-files.txt'), Files) then begin
    SuppressibleMsgBox('安装目录或文件清单异常，已停止卸载以保护其他文件。', mbError, MB_OK, IDOK); Exit;
  end;
  Result := True;
end;
