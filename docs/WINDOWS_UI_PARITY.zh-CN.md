# Windows UI 与 macOS 对齐矩阵

日期：2026-11-21。基准：v1.5.0 macOS（`apps/macos`）。本文回答两个问题：
哪些能力已经一致（及证据位置）；哪些**不能**逐项一致、原因是什么、
Windows 采用了什么等价口径。逐像素或逐控件复制不是目标（见
[macOS UI 基准](MACOS_UI_BASELINE.zh-CN.md) §1：各端用各平台原生控件实现
同一套能力与语义）。

## 1. 已一致（能力与合同同层对齐）

| 页面/能力 | Windows 位置 | 对齐口径 |
| --- | --- | --- |
| 导航五页 + Ctrl+1-5/Ctrl+, | `MainWindow.xaml` | macOS 侧栏 + ⌘1-5/⌘, |
| 任务历史独立页：双栏/筛选/恢复/重试/删除/事件时间线/本地时区 | `MainWindow.History.cs` | `NativeTaskHistoryView` |
| 设置四分类（切类保留草稿） | `MainWindow.Settings.cs` | `SettingsView` |
| CAMS 凭据（状态文案/保存/教程/环境覆盖提示） | 同上 | `camsBlock` |
| EC 下载源三选 + 下载连通性探测（行级状态/取消/复制报告） | 同上 + `ProbeRow` | `dataSourcesBlock` + `DownloadProbeView` |
| 后台同步（开关/源/间隔/时效/锚定区域/立即同步/每源状态行/熔断解锁/60s 心跳） | 同上 | `dataSyncBlock` + Store.data_sync_* |
| 保留策略 / 数据目录选择 | 同上 | `retentionBlock`/`dataDirectoryBlock` |
| 海拔来源三档（metadata/payload 透传 `elevation_source`） | `MainWindow.xaml.cs` | 位置与海拔分类 |
| 数据管理：库存 + 每周期/每源删除（preview→确认→execute，范围/文件数/释放/跳过/失败） | `MainWindow.Data.cs` | v1.3.0 `InventoryView` |
| 地形 SRTM 面板：区域预设估算确认 → 区域/全球/点下载 → 批量进度（百分比/计数/当前瓦片）→ 取消续传 | 同上 | v1.3/v1.4 `TerrainPanelView` |
| 软件更新：检查/发布说明/下载进度/SHA256SUMS 校验/自动安装/静默检查/侧栏徽标 | `Updater.cs` + `MainWindow.Updater.cs` | v1.5.0 `Updater` + `updaterBlock` |
| 点预测：双摘要卡/指标分组/评分组成/对比曲线（断线+悬停）/15 列台账/剖面图层/冻结剖面/诊断分组 | `MainWindow.Results.cs` | `Workbench` 各视图 |
| 实况：卫星帧 hero/指标卡/后验概率剖面热图/冻结卫星剖面 | `MainWindow.Nowcast.cs` | `WorkNowcast*` |
| 区域：预设多选/拖拽绘制（跨 180° 短弧）/固定色带+缺测图例/采样点/网格详情/单点打开 | `MainWindow.Regional.cs` + `apps/shared/map.html` | `RegionalForecast` 等 |
| 周期龄确认/会话持久化/收藏/今天明天 Tab/退出确认/stale 徽标 | `PreflightDialog.cs` 等 | Store 对应合同 |
| 错误中文映射（friendlyEngineMessage 全量） | `FriendlyMessages.cs` | Store 同名函数 |

## 2. 有意采用 Windows 等价（不逐项复刻）

| macOS 形态 | Windows 等价 | 原因 |
| --- | --- | --- |
| SwiftUI 侧栏 + glassEffect 导航 | WinUI `NavigationView` + `MicaBackdrop` | 各端用平台原生导航壳；基准明确不要求逐像素复制 |
| NSAlert/confirmationDialog 弹层 | WinUI `ContentDialog` | 平台模态控件不同；周期龄确认复用预检对话框（WinUI 不允许叠开两个 ContentDialog） |
| `SecureField`/`Picker`/`DisclosureGroup` | `PasswordBox`/`RadioButtons`/`Expander` | 平台原生控件映射 |
| 底部状态栏（WinUI） | macOS 为顶栏进度 chip | Windows 桌面惯例状态栏；任务运行中进度与取消全局可见（同一 UX 目标） |
| 质量曲线 SwiftUI Charts LineMark | WinUI `Canvas`+`Polyline` 自绘（断段/悬停/最佳标记） | WinUI 无 Charts；语义（缺测断线、0–2.5 固定轴）逐条对齐 |
| DMG 拖拽安装 / 安装引导 / hdiutil 安装 | Inno Setup 安装器 + zip；更新自替换走批处理 | Windows 无 DMG/hdiutil；安装引导职责由安装器承担（v1.4.0 的 macOS-only 部分） |
| URLSession 更新下载 | `HttpClient`（系统代理 + 环境代理变量同口径） | 平台网络栈；SHA256SUMS 校验与失败口径一致 |
| launchd 后台同步助手 | **无等价**：应用内 60 秒心跳 | Windows 计划任务等价物未实现（后续平台适配项）；设置页不展示 macOS 助手控件 |

## 3. 暂不可一致（原因明确，登记为后续项）

| 差异 | 原因 | 状态 |
| --- | --- | --- |
| 后台同步助手（关闭应用后仍同步） | 需要任务计划程序集成 + 提权安装/卸载 + 卸载终止在途同步（macOS 为 launchd + 专用 helper 二进制） | 平台适配项，登记后续 |
| 数据管理「手动删除」的跨进程占用保护粒度 | 引擎 flock 语义在 Windows 为 LockFileEx，跳过口径一致；但 Defender/索引器会瞬时锁住新写文件，fsync 已降级为非致命（stderr 记录） | 已缓解；观察真实环境 |
| `metadata` 响应 `source` 字段（实际海拔来源展示） | 引擎 JSONL/FFI 桌面响应当前不含 `source` 字段（共享 Rust 契约缺口，非 Windows 独有） | 待共享契约补充；UI 已按"未获取"降级显示 |
| 实况页 Windows 视觉细节（heatmap 色带/图例密度） | 自绘 Canvas 与 SwiftUI Canvas 绘制细节非逐像素 | 语义一致（unknown 琥珀、0–100% 暖色）；视觉验收随基准统一推进 |
| VoiceOver/VoiceAccess 无障碍逐项 | macOS 侧已有 accessibilityLabel 全量；Windows AutomationProperties 部分接入 | 后续按基准统一补齐 |
| 快照/夹具测试钩子（isSnapshotRun） | macOS 专用启动模式；Windows 未建同套夹具 | 纯函数层（更新器/剖面解析）以单元测试替代；UI 夹具后续项 |

## 4. 结论

- 能力与合同层：Windows 已对齐 v1.5.0 macOS 基准（v1.2.0 信息架构 + v1.3.0
  删除/地形 + v1.5.0 更新），共享 Rust 契约同一份。
- 形态层：导航壳、弹窗、控件、安装器按 Windows 原生等价实现——这是基准
  （§1"跨端对齐不要求逐像素复制"）允许且有意的差异。
- 剩余真实差距集中在两处：**后台同步助手等价物**（需要任务计划程序集成）
  与**无障碍/夹具补齐**；均已登记，不阻塞 Windows 版本发布。
