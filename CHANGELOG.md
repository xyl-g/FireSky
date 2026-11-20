# 更新日志（Changelog）

产品版本遵循语义化版本（SemVer）。v1.0.0 起产品版本与 Rust 引擎
`engine/Cargo.toml` 的 `version` 一致；旧 Python 栈（`src/firesky/version.py`）
已于 2026-11-13 退役删除。

English: product releases follow SemVer. Since v1.0.0 the product version
matches the Rust engine crate version (`engine/Cargo.toml`); the legacy
Python stack (`src/firesky/version.py`) was retired on 2026-11-13.

## v1.1.0 (2026-11-20)

移动端与 Docker 正式版：Android/iOS 双原生客户端与 Docker 镜像首次发布。
First mobile & Docker release: native Android/iOS clients and the Docker image.

**新模块 / New modules**

- **Android**（Kotlin/Compose + JNI）：`engine-mobile` 真引擎接线（手工 C ABI
  `fsmobile.h` + JNI shim，D2 裁定路线）；ecCodes definitions 完整树随 assets
  打包、首启解包到 filesDir；默认传输 mock → ffi（设置页保留 mock 切换）；
  自签签名 release APK（arm64-v8a，minSdk 26）。本地模拟器 instrumented
  全链 + 与桌面帧级 parity + kill-restart 会话恢复验收通过（测试计数见发布
  门禁日志，证据归档 output/）。
- **iOS**（SwiftUI）：definitions 进 Bundle（`Resources/eccodes/definitions`
  文件夹引用，测试 bundle 优先，消除宿主 env 注入假阳性）；默认传输 ffi；
  模拟器 `xcodebuild test` 全绿（含新增 kill-restart 恢复测试，计数见发布
  门禁日志）；无签名 device IPA 供 AltStore/Sideloadly 免费 Apple ID 自签
  侧载（7 天有效）。地图为 MapKit 原生（D1 裁定）。
- **Docker**：三阶段镜像以 Release 资产 tarball 分发
  （`FireSky-1.1.0-docker-arm64.tar.gz`，linux/arm64 本地构建 + 冒烟通过；
  amd64 可源码构建；本版暂未发布 GHCR）。

**引擎 / Engine**

- 修复全部编译警告：去除多余 `mut`、仅测试引用函数加 `#[cfg(test)]`、serde
  `deny_unknown_fields` 契约字段标注 `#[allow(dead_code)]`；零行为变化。
  `cargo test --release` 491 passed / 0 failed / 3 ignored。

**会话恢复 / Session restore**

- 移动双端对齐 macOS `result_restore` 语义：hello → task_history → 最近可
  恢复 forecast → 恢复 result 与 location/date/phenomenon 参数（gfs 缺失回退
  ec），守 busy/generation，不覆盖进行中会话；杀 App 重启自动恢复上次结果。

**发布 / Release**

- 版本收敛 1.1.0（engine / engine-mobile / macOS / Android / iOS 一致；
  SemVer minor：新增 Android/iOS/Docker 模块）。
- 载荷：`FireSky-1.1.0-macos-arm64.dmg`、`FireSky-1.1.0-macos-x86_64.dmg`
  （沿用 v1.0.0 门禁）、`FireSky-1.1.0-android-arm64.apk`、
  `FireSky-1.1.0-ios-unsigned.ipa`、`SHA256SUMS.txt`、Docker tarball（兜底）。
  安装指引（Gatekeeper xattr / Android 未知来源+同 key 覆盖 / iOS 自签 7 天 /
  docker run）见 README「下载安装 / Releases」。
- 平台策略（2026-11-20 用户裁定）：Windows 正式包后续在 Windows 机器构建
  （源码与 CI 验证保留）；Linux 不再作为发布目标（GTK4/FFI 代码保留）。
- 已知限制：移动端 v1 仅单点预测（无区域/实况）；Android 地图延后（坐标/
  收藏输入）；iOS 真机蜂窝下载验收待硬件；definitions 约 131 MB 待裁剪；
  macOS ad-hoc 签名（无 Developer ID/公证，首次打开需手动放行）。

## v1.0.0 (2026-11-20)

首个原生正式版：macOS SwiftUI/AppKit + Rust FFI 单引擎。
First native major release: macOS SwiftUI/AppKit with a single Rust engine.

2026-11-20 正式发布（GitHub Release `v1.0.0`，双架构 DMG）；2026-11-15
完成发布模拟预演。

**架构 / Architecture**

- 正式产品为 macOS 原生 `.app`（SwiftUI/AppKit + `libfiresky_engine.dylib`
  ABI v1）；Python/PySide/Tauri 旧栈已全部退役删除，正式包不含 Python，
  不因 FFI 缺失静默回退。
- 点、区域、实况统一 `check -> publications -> prepare -> forecast` 工作流；
  区域独立城市排名、冻结结果剖面、任务历史/回放/恢复、后台同步助手。
- 内置资源包（695 城市目录 + 时区数据，sha256 清单审计）；资源缺失硬失败。

**数值与性能 / Numerics & performance**

- 时间缓存单位/键一致性修复；新增 opt-in consistent/exact 时间键模式
  （默认 quantized-120s 不变）。
- 全国 0.25° 批量（36,685 点 / 365,794 样本）：共享 motion 缓存、单层快
  路径、NEON blocking、统一投影排序等已落地；4 核忙机 wall ≈ 551s、峰值
  ≈ 3.25 GiB（< 4 GiB 门槛）。位级一致门槛下精确复用已审计穷尽
  （见 docs/archive/ALGORITHM_AUDIT_2026-11-15.zh-CN.md）。
- 近似光路模板缓存保持 opt-in（实测 4 核 CPU -18.4%，但改变 10.6% 点的
  最佳时刻步，未获容差审批，默认关闭）。

**跨平台与移动 / Cross-platform & mobile**

- Native Desktop CI 首次全绿（run 44571959210 @ f341ef8，10 jobs）：
  macOS arm64/x86_64（DMG + hdiutil 校验 + FFI bundle 冒烟）、Windows
  WinUI x64/ARM64（编译 + 产物验证：exe/引擎 DLL/资源清单/无 Python）、
  Linux GTK4（构建 + FFI smoke）、Linux FFI amd64/arm64、Docker
  amd64/arm64 构建 + 运行时冒烟、原生资源契约。
- 区域单模型契约三端对齐 macOS：Linux GTK/WinUI/Web 区域页改为 GFS/EC
  单选（引擎拒绝区域 `model_source:"both"`），回放恢复同步对齐。
- MN1 移动里程碑：`engine-mobile` 承载桌面同源 `serve_runtime`；iOS
  模拟器全链路 GFS+EC 真实解码、预置缓存零网络、与桌面帧级 parity；
  Android `libengine_mobile.so` 仅依赖系统库。MN2/MN3（Android 全链路、
  真机与 definitions 打包）进行中，见 `docs/MOBILE_NATIVE_PLAN.md`。

**正式发布 / Release**

- 发布流程与证据：`docs/RELEASE_MACOS.zh-CN.md`；模拟预演记录
  `docs/RELEASE_V1_SIMULATION_2026-11-15.zh-CN.md`。
- 载荷：`FireSky-1.0.0-macos-arm64.dmg`（本地构建，全门禁通过）与
  `FireSky-1.0.0-macos-x86_64.dmg`（CI Intel runner 构建 + 冒烟），同源码
  f341ef8；`SHA256SUMS.txt` 随 Release 发布。
- 门禁（2026-11-20）：Rust 491 passed / 0 failed / 3 ignored + 集成
  4+2+4；pytest 契约 38 passed / 1 skipped；clean-machine/冷启动/bundle
  冒烟 4/4；离线 flow ×3 + UI 回放 ×3（deny network*）全过；双 DMG
  hdiutil verify + 挂载校验（版本/架构/codesign/资源清单）。
- 已知限制：ad-hoc 签名（无 Developer ID/公证，首次打开需手动放行，见
  发布文档）；Windows/Linux 真机交互式 UI 验收与正式安装包未完成；
  macOS Intel 真机验收未完成（CI Intel 构建+冒烟已过）；空闲受控性能
  窗口未获得。

**清理 / Cleanup**

- 历史计划/迁移/退役文档移入 `docs/archive/`；删除误生成 `-q/` 目录、
  `desktop/` 空壳与重复文档副本；旧 UI 实验产物移出 `dist/`。
- 发布前精简（2026-11-16）：24 个历史 output 目录（约 10 GB）与
  `engine/target-profile`（1.7 GB）移入废纸篓；15 个开发期 bench/audit
  examples 删除（不参与 release 构建，CI 无引用）；归档文档链接修正。
  测试套件（cargo test / pytest / Swift 夹具 / CI 冒烟脚本）保留为
  质量门禁，不进入发布包。

## v0.1.1 (2026-11-07)

首次发布后的修正与改进。Fixes and improvements since v0.1.0.

**修复**

- EC 单点：`ecmwf-opendata` 依赖未声明（新增 `ec` extra）；下载清单补
  2t/2d 地表温/露点（部分时效公开数据不发布，按可选增强处理，缺失时
  LCL 云底自动回退），EC 剖面输出字段集与 GFS 对齐
- 实况：移除机场 METAR 修正链路与 HCAI 下载路径（下载过慢），校准帧
  改为 HSD 分段独占；satpy/pyresample/aiohttp 等实况依赖此前未声明，
  新增 `nowcast` extra
- 实况发布门：覆盖率按文档契约逐采样点计算；语义改为**有可用采样即
  发布**，覆盖率随结果披露（`calibrated_sample_coverage_ratio`），
  仅零覆盖 fail-closed
- 旧原生 dylib 残留已废弃符号：重建后 EC 路径回归稳定
- 根路径 `/` 自动跳转预测工作台；文档（PRINCIPLES/README 中英）
  GitHub 数学渲染全面修复（转义、列表结构、lt/gt）

**改进**

- CAMS/ADS 批量预取：事件窗口多个时刻合并为一次 ADS 请求，冷启动
  CAMS 阶段时延约减半
- GFS/EC 单点报告字段集对齐（70 vs 70，69 共有；仅剩源特有字段）
- 精简代码库：移除未接入服务路径的模块与前端组件，收敛公开 payload
  字段到实际产出范围
- 新增 GitHub Actions CI（pytest / cargo test / svelte-check / docker
  build / macOS+Windows 桌面打包）；DEPLOY 补实况预热 cron 示例与
  token 步骤
- **PySide6 桌面应用**（`desktop/app.py`）：本地单机形态，与 Web 服务
  共用同一预测核心路径（数值一致）；观测点表单/常用城市/进度日志/
  峰值档位卡片/逐五分钟质量曲线与台账；CI 自动出 macOS dmg 与
  Windows exe 目录

## v0.1.0 (2026-11-07)

首个公开发布版本。First public release.

**引擎（Python + Rust）**

- 完整预测链路：GFS 0.25° 子集下载（fail-closed 位图掩膜 + mmap 解码缓存）、
  三族云柱派生、ECMWF IFS 第二模式源、CAMS 11 层 532nm 气溶胶剖面（含
  lead 回退）
- Rust 光追评分器（CPU）：逐观测点 × 逐五分钟步长的太阳入射 + 观测者返程
  双向追踪，含地球曲率、地形/海面下界、半拉格朗日风场平流（RK2 中点风）、
  36 方位 × 90 仰角视角球带积分、目标甲板自遮蔽距离衰减与连接迟滞、时间
  括号占空比混合
- 全国区域批处理（共享张量批量，90 个 Rust 契约测试）与单点精算
- 云属诊断：压力族兼容判定 + 证据驱动云属代理 v2（相态 + 教程经验厚度
  阈值，显示专用、不参与评分），前端附烧霞适配度排序
- 实况 nowcast：向日葵 9 HSD + HCAI 校准卫星云柱（多观测 + 落地判定）

**服务**

- FastAPI 单进程同时提供 API（`/api/v1/firesky/*`）与前端 SPA 静态托管
- 异步预测 job（SSE 进度 / 取消 / 相同请求去重）、全国摘要 / 截面 / 诊断
  端点、数据资产状态与 prune
- CAMS/ADS 下载有界并发（默认 2，`FIRESKY_CAMS_DOWNLOAD_WORKERS` 可调 1–4），
  缓解冷缓存首跑的 ADS 排队等待

**前端（SvelteKit + Tailwind 4）**

- 预测 / 实况 / 形势图三页 + 管理控制台（总览 / 实时计算 / 运行队列 / 数据资产）
- 全站液态玻璃材质体系（明暗双主题、亮色 Warm Sand 色板统一）、地图图层
  控制、云属卡片

**部署**

- Docker Compose 三阶段构建（Rust → Node → python:3.13-slim，非 root 运行）
- 裸机部署文档覆盖 Linux systemd / macOS launchd / Windows（PowerShell、
  任务计划、NSSM、WSL2）三平台，含反代 / cron 定时 / 安全清单 / 排障
- GPL-3.0-only 许可证；致谢 SunsetBot 火烧云教程
  （<https://www.sunsetbot.top/halo/>）
