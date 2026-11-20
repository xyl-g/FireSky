; FireSky Windows 安装器（Inno Setup 6.3+）。
; 载荷来自 scripts/build_windows_native.ps1 的干净暂存树（dist/native-windows/stage），
; 由 scripts/build_windows_installer.ps1 以 /DAppVersion /DArch /DStageDir 调用。
; 安装器不打包用户数据（%LOCALAPPDATA%\FireSky），卸载只移除应用文件。

#define AppName "FireSky"
#define AppPublisher "FireSky"
#define AppURL "https://github.com/xyl-g/FireSky"
#define AppExeName "FireSky.exe"

[Setup]
AppId={{7E6F0A84-3C42-4C0E-9A5B-2F1D8A0BF1C4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir={#StageDir}
OutputBaseFilename=FireSky-{#AppVersion}-windows-{#Arch}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible arm64
ArchitecturesAllowed=x64compatible arm64
MinVersion=10.0.19041
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#StageDir}\FireSky\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载后清理安装目录本身；用户数据（%LOCALAPPDATA%\FireSky 与所选数据目录）保留。

[Code]
// WebView2 Runtime 是地图 WebView 的硬依赖；Win11 预装，Win10 需要自装。
// 只检查并提示，不静默下载——离线安装包保持明确行为。
const
  WebView2RuntimeKey = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed(): Boolean;
begin
  Result := RegKeyExists(HKEY_LOCAL_MACHINE, WebView2RuntimeKey)
    or RegKeyExists(HKEY_CURRENT_USER, WebView2RuntimeKey);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
  begin
    MsgBox(
      '未检测到 WebView2 Runtime（FireSky 地图组件依赖）。' #13#10 #13#10 +
      '可以从微软官网安装 Evergreen Runtime 后继续：' #13#10 +
      'https://developer.microsoft.com/microsoft-edge/webview2/' #13#10 #13#10 +
      '仍可继续安装，但首次运行地图将无法加载。',
      '缺少 WebView2 Runtime', MB_OK, 0);
  end;
end;
