# Python 到 Rust 迁移收尾审计

审计日期：2026-11-10

## 结论

Rust 已覆盖大量计算、数据解码、缓存格式和独立工作流；Rust 引擎可作为独立进程运行，不启动 Python。但 FireSky 的生产入口仍依赖 Python，迁移目标尚未完成。当前状态应描述为“Rust 核心持续扩展、生产服务仍由 Python 统筹”，而不是“应用已 Rust 化”。

自有业务算法没有被证明存在不能用 Rust 实现的部分。以下“不可移植”专指不应把外部系统、平台能力或现有 UI 框架误当作 Python 算法重写；有些依赖可以替换，但须另行实现并通过完整行为验证。

## 已有 Rust 覆盖

| 范围 | 当前覆盖 | 仍不能据此宣称 |
| --- | --- | --- |
| 数值与云场 | 几何、太阳时、云体/云柱推导、经验评分、质量诊断、部分点预测物化和区域帧计算已有 Rust 实现。 | 全部生产点/区域工作流已切换；所有时间平局、失败和诊断行为均已验收。 |
| GFS、EC、CAMS | ecCodes 解码、部分 GFS/EC/CAMS 输入规划、下载、缓存校验和文件化张量路径已有 Rust 实现。 | 全部数据源及产品都已走生产 Rust 路径；上游实时服务、账号、更新策略和缓存生命周期已验收。 |
| HSD 实况 | 已有紧凑缓存读取、B13 运动、部分平流、HSD 校准样本检索、GFS 先验批处理、多帧调整组装，以及 Rust 事件/瞬时质量 ledger。 | HSD 拉取与原始校准、雷达约束、每观测物理评分和最终报告已整体迁移。 |
| 桌面与缓存 | 位置、清单、预检、会话令牌、部分准备/预测 RPC 及多个有校验的缓存/张量格式已有 Rust 路径。 | 桌面所有任务生命周期、HTTP/CLI、会话进度、取消、结果缓存和安装包均已脱离 Python。 |
| 性能 | 独立组件及固定缓存路径已有多组对照基准和局部优化。 | 端到端、冷缓存、并发或其他操作系统的性能已证明。 |

详细阶段、接口和已有验证见 [RUST_MIGRATION.md](RUST_MIGRATION.md)；执行顺序及验收门槛见 [RUST_MIGRATION_PLAN.md](RUST_MIGRATION_PLAN.md)；性能数据见 [RUST_PERFORMANCE.md](RUST_PERFORMANCE.md)。

## 生产入口核对

- HTTP 入口是 `uvicorn firesky.api:create_app`，路由及预测服务由 `src/firesky/api.py`、`src/firesky/web_server.py` 承担；当前 `docker/Dockerfile` 的运行镜像也是 Python。
- CLI 入口仍是 `python -m firesky`（`src/firesky/__main__.py`）。
- SwiftUI 和 WinUI 客户端的主桌面 IPC 仍启动 Python `firesky.desktop_rpc`；Rust 子进程目前承接的是部分 opt-in 能力。Qt 桌面本身运行于 Python。
- `engine/` 的 `firesky-engine` 是独立 Rust JSON-lines 进程，可不启动 Python，但并不因此自动接管以上 HTTP、CLI、桌面及任务生命周期。
- `pyproject.toml` 仍用 setuptools 构建 Python 包，生产依赖包含 NumPy、requests、ecCodes 等。

关键路由开关也反映了“局部 Rust”与“整体退役”的差异：`FIRESKY_GFS_PRIOR_BACKEND` 和 `FIRESKY_NOWCAST_SCORING_BACKEND` 当前默认 Rust；`FIRESKY_DESKTOP_DATA_BACKEND`、`FIRESKY_GFS_DECODE_BACKEND`、`FIRESKY_CLOUD_BACKEND`、`FIRESKY_ECMWF_DATA_BACKEND`、`FIRESKY_CAMS_DATA_BACKEND`、`FIRESKY_REGIONAL_DATA_BACKEND` 等默认 Python。启用单个 Rust 开关不表示某个完整业务流程或整个进程已 Python-free。

## 尚未完成

优先级按阻止“生产运行不需要 Python”的程度排序。

| 优先级 | 未完成边界 | 下一步与验收 |
| --- | --- | --- |
| P0 | 实况逐观测评分。引擎已有 opt-in 原生批量评估操作（`desktop_nowcast_assessment`，v2 27 列特征行含 `horizontal_cloud_probability`/`cloud_probability_given_cloud`，单次调用覆盖列构建、候选、三族光路信号、剖面、雷达约束与 18 值 ledger rows，`FIRESKY_NOWCAST_ASSESSMENT_BACKEND=rust` 启用），但生产默认仍走 Python 逐扫描路径；事件 best_index/窗口规则仍在 Python。 | 在真实服务流量上验证双后端输出相等并启用 rust 后端为默认；补齐 HSD/CAMS/GFS 供应编排与主扫描选择的原生路径后，才可移除 Python 逐扫描评分。 |
| P0 | 生产点/区域/EC 工作流与服务编排。Rust 有冻结输入及独立工作流，但正常服务仍负责动态输入选择、下载/刷新、完整报告和多个缓存边界。 | 将真实 API/桌面请求接入 Rust 输入规划、GFS/EC/CAMS 解析、评分、诊断、报告和缓存；保留当前 schema、失败语义及 GFS/EC 独立结果。验证完整请求，而非单独 helper。 |
| P1 | 实况数据生命周期。远端 HSD 发现/Range 下载、原始标定、紧凑缓存写入，以及完整 HSD/CAMS/GFS 和雷达供应编排未形成 Python-free 生产路径；部分 HCAI 解析仍在 Python。 | 逐项迁移或用等价原生库替换，保留校准、投影、掩码、单位、时间和来源语义；用固定原始样本及线上探测验证。不要以已缓存 HSD 读取性能代表下载到报告的性能。 |
| P1 | HTTP、CLI 与长期任务。FastAPI 路由、SSE、鉴权/错误处理、任务队列、缓存刷新/裁剪以及 `python -m firesky` 仍是 Python 应用入口。 | Rust 实现兼容服务/CLI 与任务生命周期；测试路由、SSE、错误、并发、取消、重试及旧客户端协议。 |
| P1 | 桌面完整运行时。macOS/Windows 客户端目前启动 `firesky.desktop_rpc` Python 子进程；Qt 桌面界面本身也依赖 Python。若继续支持 Qt，它仍会要求 Python。 | 原生客户端改为直接启动 Rust 服务并覆盖所有 IPC 方法。决定 Qt 是继续作为 Python 兼容版，还是退役；不要将其算作 Rust 引擎迁移已完成。 |
| P2 | 发布与跨平台。ecCodes、tz/SRTM 等资源的安装、定位和许可证/来源交付，以及 macOS、Windows、Linux 的 clean-machine 构建尚未整体验收。 | 提供各平台 Rust 引擎和依赖资源包；在无 Python 环境执行完整点/区域/实况/桌面烟测，验证非 ASCII 路径、权限、崩溃恢复和升级。 |

另外，迁移前仍须解决或明确复现事件窗口在数值相同/近似相同时的诊断选择规则。已通过 helper 的数值相等测试不能替代完整响应对照。

## 不应直接移植的边界

| 内容 | 判断 | 处理方式 |
| --- | --- | --- |
| NOAA NOMADS、ECMWF Open Data、Copernicus ADS、Himawari/HCAI、RainViewer 等上游及其天气产品 | 上游服务、账号、数据和发布时效不属于本仓库，不能搬进 Rust。 | Rust 可以重写客户端、认证、下载、校验、重试和缓存策略；数据仍由服务提供。 |
| ecCodes、SatPy、pyresample 及系统库 | 这些是第三方原生/科学计算实现，不是 FireSky 自有 Python 业务逻辑。Rust 引擎当前通过 C ecCodes 解 GRIB，仍需要 ecCodes 动态库及定义文件。SatPy/pyresample 参与 HSD 原始数据处理。 | ecCodes 可继续作为 C ABI 依赖；若要消除此依赖，必须另选/实现 GRIB 解码器并证明消息、位图、缺测和扫描顺序一致。HSD 相关库可由 Rust 或其他原生库替换，但目前没有等价性证据，不能称为“已不可移植”或“已迁移”。 |
| macOS SwiftUI、Windows WinUI/C#、Svelte/TypeScript 前端 | 这些现有客户端不是 Python。改写成 Rust UI 不是 Python 迁移的必要条件，且会扩大平台适配和交互回归范围。 | 保持 UI 为平台原生/现有前端，通过稳定 IPC/HTTP 调用 Rust 引擎。OS 定位权限、文件选择等仍须使用平台 API，可由 Rust 包装但不能用通用 Rust 代码消除平台差异。 |
| PySide/Qt 兼容桌面界面 | 它是 Python 前端，不属于计算核心；但只要保留并运行此 UI，就不能宣称整个桌面产品完全不依赖 Python。 | 最低成本是将其作为单独的兼容版；严格 Python-free 产品应由已存在的 macOS/Windows 原生前端替代它，而不是把 UI 代码硬翻译成 Rust。 |
| Python 测试、差分 oracle、离线参考实现 | 不属于生产运行时要求，保留它们有利于防止数值和 schema 回归。 | Rust 生产程序不得调用它们；发布验收应在 Python 不可用时通过。 |

因此，目前没有“因为语言而无法移植”的自有核心算法清单。真正的约束是外部依赖、上游数据边界、平台 UI 决策，以及迁移后必须补齐的等价性、部署和生产集成证据。

## 近期性能结果

2026-11-10 在固定本地 HSD/GFS 缓存、双观测时次、7 次 warm trials 上，服务改为仅对首个时次构造 nowcast 诊断对象。每时次评分对照用例确认该优化不改变受约束 `TimeStepAssessment`。

| 指标 | 优化前 p50 | 优化后 p50 |
| --- | ---: | ---: |
| 两个逐观测物理评分 | 66.54 ms | 66.03 ms |
| 两次观测约束 | 17.66 ms | 8.31 ms |
| Rust 事件质量包装 | 0.624 ms | 0.797 ms |
| 评分、约束与事件质量合计 | 84.56 ms | 75.53 ms |

该固定缓存路径总耗时下降约 10.7%；这不是完整预测延迟。下载、缓存预热、原始 HSD 标定、HTTP/报告序列化、冷启动和跨平台性能均不在此测量范围内。

## 完成定义

只有当生产服务、桌面和 CLI 在 Python 不可用时仍能完成原有流程；完整结果、错误、缓存和会话协议通过差分及端到端测试；所需外部库/资源可安装；目标操作系统通过 clean-machine 验收；并提供真实点、区域、实况 workload 的冷/热延迟、P50/P95、CPU、内存和并发数据后，才能宣告 Python 运行时退役。纯 Rust benchmark、某个 backend 开关或单个成功的预测样例均不足以通过此门槛。
