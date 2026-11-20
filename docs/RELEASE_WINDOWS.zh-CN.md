# Windows 发布文档

日期：2026-11-21。分支 `feat/windows-release` 起 Windows 成为正式发布目标：
发布形态为**自包含 zip + Inno Setup 安装器**，CI 在 `v*` tag 时自动构建并直发
GitHub Release（与 macOS DMG 同一口径）。本地与 CI 走同一条打包路径
（`scripts/build_windows_native.ps1`），验收要求也一致。

## 1. 发布形态

| 资产 | 形态 | 说明 |
| --- | --- | --- |
| `FireSky-<版本>-windows-x64.zip` | 自包含绿色包 | 解压即用，无需安装；含引擎 DLL、ecCodes、应用资源 |
| `FireSky-<版本>-windows-arm64.zip` | 自包含绿色包 | Windows on ARM 原生引擎 |
| `FireSky-<版本>-windows-<arch>-setup.exe` | Inno Setup 安装器 | 开始菜单/卸载器/可选桌面图标；用户数据不随卸载删除 |
| `SHA256SUMS.txt` | 校验 | 全部资产 SHA256 清单（裸文件名格式） |

- 应用为 **WinUI 3 unpackaged**（`WindowsPackageType=None`）+ 自包含 .NET 8 运行时；
  正式包禁止 JSONL 后端（`FIRESKY_NATIVE_BACKEND=jsonl` 在打包安装中报错）。
- 运行时依赖 **WebView2 Evergreen Runtime**（Win11 预装；Win10 需自装）。
  安装器在安装时检测并提示，绿色包见 README 的 Windows 安装说明。
- 发布包**无签名**（无代码签名证书）：SmartScreen 首次运行会提示，
  「更多信息 → 仍要运行」放行；口径与 macOS ad-hoc 签名一致。
- 用户数据在 `%LOCALAPPDATA%\FireSky`（应用设置/会话/崩溃日志）与所选
  数据目录（`FIRESKY_WORKSPACE` 或设置页配置）；卸载器只删除应用文件。

## 2. 本地构建

前置：Windows 10 19041+ / 11；VS 2022（含 WinUI/C# 工具与 MSVC）；
.NET 8 SDK；Rust（`x86_64-pc-windows-msvc`，ARM64 需另加 target）；
Node（ecCodes bundle 脚本）；vcpkg（固定版本 `2ec0f174`）；
Inno Setup 6.3+（仅安装器需要，`winget install JRSoftware.InnoSetup`）。

```powershell
# 1) vcpkg 构建 ecCodes（首次约 20–40 分钟；固定提交与 CI 相同）
git clone --filter=blob:none --no-checkout https://github.com/microsoft/vcpkg.git "$env:LOCALAPPDATA\tools\vcpkg"
git -C "$env:LOCALAPPDATA\tools\vcpkg" checkout 2ec0f1747d8186a5c9babe276e2ad57cd2b12d2a
& "$env:LOCALAPPDATA\tools\vcpkg\bootstrap-vcpkg.bat" -disableMetrics
& "$env:LOCALAPPDATA\tools\vcpkg\vcpkg.exe" install "eccodes[aec,png]:x64-windows" --classic
node apps/shared-web/scripts/bundle-vcpkg-eccodes.mjs `
  "$env:LOCALAPPDATA\tools\vcpkg\installed" "$env:LOCALAPPDATA\tools\vcpkg\ports" `
  x64-windows "$env:TEMP\eccodes-bundle"

# 2) 一条命令完成引擎 + WinUI + 校验 + 暂存 + zip/SHA256
powershell -ExecutionPolicy Bypass -File scripts/build_windows_native.ps1 `
  -Arch x64 -EccodesRoot "$env:TEMP\eccodes-bundle"

# 3) 安装器（可选）
powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1 -Arch x64
```

产物：`dist/native-windows/FireSky-<版本>-windows-x64.zip`（+ `.sha256`、
`-setup.exe` + `.sha256`，暂存树在 `dist/native-windows/stage/`）。
ARM64 同理（`-Arch arm64`，需 ARM64 MSVC 工具链）。

## 3. CI 发布

`.github/workflows/native-desktop.yml`：

- `windows-native`（x64 + ARM64 矩阵，windows-2022 / windows-11-arm）：
  vcpkg 构建 ecCodes bundle → `scripts/build_windows_native.ps1` 单一路径完成
  引擎/WinUI 构建、载荷校验、暂存、zip → Inno Setup 构建 setup.exe →
  上传 artifact `FireSky-windows-<arch>`。
- `release`（`v*` tag 触发，`needs: [macos-native, windows-native]`）：
  下载 macOS DMG 与 Windows zip/setup 产物，校验版本化文件名，
  生成 `SHA256SUMS.txt`（`sha256sum -c` 自验），
  从 CHANGELOG 提取本版段落创建 GitHub Release。

载荷校验（脚本内固定执行）：`Resources\firesky_engine.dll`、
`Resources\firesky-engine.exe`、`Resources\app-data\RESOURCE-MANIFEST.json`、
`Resources\eccodes\bin\eccodes.dll` + definitions、`Resources\Map\map.html`，
且不得含 Python 运行时文件。

## 4. 验收口径

发布前必须在本机（或 CI Windows runner）通过：

1. **构建**：`scripts/build_windows_native.ps1` 全绿，载荷校验通过。
2. **运行冒烟**：解压 zip 直接运行 `FireSky\FireSky.exe`——引擎连接成功
   （状态栏「本地引擎已连接」）、五个页面切换正常、设置四分类渲染正常。
3. **真实数据链路**：ecCodes 在包内生效——预检 → 下载缺失数据 → 预测，
   GRIB 解码成功；下载连通性探测三源行状态如实。
4. **安装器**：setup.exe 安装 → 开始菜单启动 → 卸载后应用文件移除、
   用户数据保留。
5. **ARM64**：CI ARM64 job 绿（本机无 ARM64 时以 CI 为准）。
6. **回归**：`bash scripts/test_windows_ffi_contract.sh`（contracts job 已跑）。

## 5. 平台适配注记

- **后台同步助手**：macOS 用 launchd LaunchAgent；Windows 版本当前为应用内
  60 秒心跳驱动（应用退出即停）。任务计划程序（Task Scheduler）等价物留作
  后续平台适配项，设置页不出现 macOS 助手控件。
- **数据管理手动删除 / 目录迁移 / SRTM 独立下载**：macOS 方案阶段 B/C/D，
  任何平台都未实施；Windows 不超前实现。
- **无障碍 / 快捷键**：Ctrl+1-5 切页、Ctrl+, 设置；控件均带 ToolTip 描述，
  VoiceOver 对应的屏幕阅读器验收随基准统一推进。
