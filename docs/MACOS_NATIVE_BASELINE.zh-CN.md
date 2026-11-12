# FireSky macOS 原生端基线

更新日期：2026-11-12

本轮交付、已知缺口及用户要求暂停的接续入口见
[交接记录](HANDOFF_2026-11-12.zh-CN.md)。Windows/Linux 当前暂停，先验收 macOS。

本文定义 macOS 正式桌面端的完成基线。Windows/Linux 后续只复制平台壳和
FFI 适配层，不复制预测、下载、预检、缓存或报告业务逻辑。

## 1. 交付形态

- UI：SwiftUI/AppKit。
- 引擎：`libfiresky_engine.dylib`，通过 `engine/include/firesky_ffi.h`
  的 ABI v1 加载。
- 资源：`.app/Contents/Resources/app-data`、ecCodes 动态库、
  `definitions`/`samples`、城市目录、时区清单和许可证。
- 用户数据：默认位于 `~/Library/Application Support/FireSky/Data`，
  与只读安装资源分离。
- 正式 `.app` 不启动 Python，不依赖 Tauri，也不因 FFI 缺失静默回退 JSONL。
  JSONL 仅允许在显式 `FIRESKY_NATIVE_BACKEND=jsonl` 的开发诊断模式使用。

## 2. macOS 已完成实现

### FFI 与生命周期

- 同步和异步 C ABI v1：`hello`、位置、清单、网络探测、预检、准备、预测、
  报告和任务事件。
- 回调事件只在回调期间有效；Swift 立即复制后切回主 actor。
- 关闭顺序固定为取消任务、等待 `join/free`、释放 runtime、最后卸载 dylib。
- 错误、取消、重复 join、非法 JSON、NUL/UTF-8 和非 ASCII 工作区均有合同检查。

### 业务工作流

- 点、区域、实况统一执行：
  `check -> publications -> prepare -> forecast(confirmed=true)`。
- 预检弹窗显示缺失、过期和待核验数据；准备成功前不能确认预测。
- 区域计算自动生成 GFS/EC 独立城市排名。
- 点击区域网格后，可按冻结结果的 `result_id`、输入 SHA、模式、坐标和 UTC
  时次读取独立剖面，不会重新下载或混用周期。
- 数据页提供持久任务历史、事件回放、终态记录删除、已发布结果恢复，以及
  恢复参数后重新进入统一预检；恢复结果仍由 Rust 重新校验缓存和报告身份。
- 数据页提供 GFS/EC/CAMS 清单和下载连通性探测。
- 设置页提供 CAMS Key、EC 下载源、GFS/EC/CAMS 周期保留数，以及卫星/报告/地图
  滚动保留天数。清理只在新输入校验成功后执行。

### 发布门禁

- 构建前校验 `RESOURCE-MANIFEST.json`，过期或篡改资源直接失败。
- `.app` 缺少 `app-data` 或资源清单时硬失败，不从源码目录补资源。
- `arm64` 和 `x86_64` 通过同一脚本构建；跨架构构建必须显式提供匹配架构的
  ecCodes 库。
- `FIRESKY_CODESIGN_IDENTITY` 可启用 Developer ID、hardened runtime 和 timestamp，
  `FIRESKY_CODESIGN_ENTITLEMENTS` 可附加经过审查的 entitlements；未配置时本地
  smoke 使用 ad-hoc 签名。
- bundle smoke 覆盖 C/Swift FFI、资源缺失失败、无 Python 动态依赖、非 ASCII
  工作区和可选冷启动。

## 3. 本机验证命令

```bash
swift build --package-path apps/macos -c release
bash scripts/test_macos_swift_fixtures.sh
bash scripts/test_macos_native.sh
FIRESKY_CLEAN_MACHINE_SMOKE=1 \
FIRESKY_APP_SMOKE=1 \
FIRESKY_EXPECTED_ARCH=arm64 \
bash scripts/test_macos_native.sh
```

Rust 引擎合同和运行时测试：

```bash
cargo test --manifest-path engine/Cargo.toml --locked
```

本机 ARM64 最后一次验证（2026-11-12）：

```text
Rust: 174 passed, 2 ignored
Runtime process tests: 4 passed
Swift release build: passed
macOS FFI/Swift fixture/smoke: passed
Application cold-start and native bundle smoke: passed
```

`swift test` 当前不作为门禁：`Package.swift` 没有声明 SwiftPM test target，
Swift 回归由 `scripts/test_macos_swift_fixtures.sh` 和
`scripts/test_macos_native.sh` 显式编译、执行现有 fixture。

## 4. 尚未能在当前主机完成的门禁

这些不是 macOS FFI 接线缺失，但在发布前必须单独记录：

1. 真实 CAMS 账号、数据集授权和上游网络下的点/区域/实况/报告冷缓存与热缓存。
2. Intel macOS 主机上的 `x86_64` 构建、启动和真实数据运行。
3. 无开发工具、无源码、无网络条件下的 clean-machine 安装/升级/卸载保留数据。
4. Developer ID 签名、公证和 staple；本地 smoke 使用 ad-hoc 签名。
5. 真实权限、代理、下载失败、重启恢复和历史报告查看。

未通过上述门禁时，只能标记为“macOS 实现完成，发布验收待完成”，不能声称三端
交付完成。

## 5. Windows/Linux 复制规则

Windows 和 Linux 必须保持以下合同不变：

- 同一 ABI 版本、方法名、JSON envelope、错误码、任务状态和确认令牌语义。
- 同一资源清单字段、缓存身份、报告引用和 retention 合同。
- 同一预检弹窗逻辑：平台 UI 只展示和确认，Rust 决定 ready/missing/old。
- 平台差异只允许出现在窗口、文件选择、定位、通知、钥匙串和动态库加载：
  Windows 使用 `firesky_engine.dll`，Linux 使用 `libfiresky_engine.so`。
- 每个平台完成后分别执行自己的真实架构和 clean-machine 门禁，不能用 macOS
  结果替代 Windows/Linux 运行验收。
