# 最新状态与下一步执行计划

更新日期：2026-11-13（Asia/Shanghai）。本文为当前接续入口，取代
`archive/NEXT_AGENT_PLAN_2026-11-12.zh-CN.md` 的执行顺序；旧文中的用户决定和详细需求保留为历史依据。

> **2026-11-13 退役清理补记**：用户拍板删除 legacy Python/PySide 栈后，
> `src/firesky/`、`desktop/`、`pyproject.toml` 与全部 Python/Rust 差分测试
> （含本文引用的 `test_rust_runtime_forecast.py` 等）已物理删除；下文涉及差分
> 测试的复现命令仅作历史记录。现架构契约测试保留在 `tests/`
> （`test_rust_hsd_raw` / `test_rust_desktop_sync` / `test_native_preparation_ui`），
> 以 `make test` 运行。

## 0. 产品方向与优先级（用户本次明确）

**以 macOS 为产品与 UI 基准，其他 UI 最终向 macOS 对齐。** 当前先完善 SwiftUI/AppKit
原生端的功能、工作流程、信息层级、展示和验收，建立有证据的基准；再推进 Web 及其他端。
具体规则见[macOS 产品与 UI 基准](../MACOS_UI_BASELINE.zh-CN.md)。旧 Web/Qt 保留为历史能力
参考；macOS 当前缺陷先修复，通过验收的行为才进入跨端基准。

Docker 页面路径缺口继续保留在后续部署清单，移出首要任务；通用 HTTP/管理功能只有在
支持 macOS 当前流程或后续 Web 对齐时才进入相应工作包。Windows/Linux 开发继续暂停，
其最终对齐方向不代表本次恢复平台开发。

## 1. 本次核对范围

- 当前分支 `main`，HEAD 为 `75371d1`（后台数据同步助手实现）。共享 Web 拆分、Tauri
  删除、macOS 展示补齐等仍有未提交/未跟踪改动；HEAD 单独不能代表当前完整源码。
- 本次核对最新文档、Git 差异和相关 Rust/Swift/测试源码，只更新文档；未重新运行
  测试、构建安装包、探测网络或下载数据。下列测试和真实运行数字均来自已有记录。
- 证据有冲突时按场景、源码身份和批次分别记录，不将较早通过结果覆盖后续失败，
  不将 fixture、源码 smoke、历史数据回放或 CI 配置当作目标产物的实际验收。
- 后续修改保留当前工作区已有内容，不运行 reset/clean，不批量删除 legacy 业务模块。

## 2. 当前已做到哪里

| 工作面 | 已有实现或证据 | 仍缺的闭环 |
| --- | --- | --- |
| macOS 产品基准 | SwiftUI/AppKit + Rust FFI 是当前产品主线；用户已明确跨端对齐方向 | 主要页面/流程的同版本运行、截图、异常态和真实数据验收；形成其他端可复核的基准 |
| 生产运行时 | 默认 HTTP/CLI/macOS FFI 与 Docker 定义走 Rust，无 Python 回退 | 同一源码下的真实工作流、交付包和 clean-machine 验收 |
| 共享 Web / Tauri | 页面迁到 `apps/shared-web/`；默认构建与 HTTP 静态根已改接；Tauri 壳已删除 | 后续按 macOS 基准对齐；旧 `frontend/` 能力逐页核对；Docker 路径及双架构运行归入部署阶段 |
| Python / PySide | 旧服务改为显式 legacy 入口；部分依赖拆分、无消费者 CLI 删除；PySide 标记维护终止 | macOS/Web 替代与用户数据迁移验收；PySide 源码/spec 归档；按消费者清理 legacy 模块 |
| macOS 展示 | 71 项字段缺口盘点与本批展示实现完成；趋势、AOD、评分组成、剖面、区域详情已补 | 同 fixture 对照、不同窗口/主题/异常态截图、键盘与 VoiceOver 真机走查 |
| CAMS 路线 A | 整洲整周期 prepare、环境代理、严格 AOD550 + 11 层廓线合同；已有真实 GFS 点预测 | EC 点、区域、实况、报告/地图、取消及失败等其余场景的真实验收 |
| 后台同步 | `desktop_sync`、IPC/FFI、macOS 设置/60 秒 tick/立即同步、任务历史已实现 | 设计 A1–A8 真实验收；GFS/EC 单周期体积、去重、取消、熔断、重启与保留实测 |
| 历史恢复与保留 | 不可变快照 + `result_restore`、读租约、引用地图保留、多进程及模拟 ENOSPC 已有 | 新包 GUI 恢复、真实数据长期运行与硬件满盘验收；冻结剖面源另有有效期 |
| 跨日期变更线 | 拆分/合并、规则环形经度轴和科学回归已有 | 双侧真实 GFS/EC/CAMS 下载、连续地图和剖面的实机验收 |

### 已有真实 CAMS 证据的边界

2026-11-13 的 `output/fullcycle-acceptance-20261113/` 保存北京/广州 prepare 与
forecast 四份 JSON；[CAMS 决定记录](CAMS_DUAL_ROUTE_PROPOSAL.zh-CN.md)记录 release
`source_id=1a05cca4…`：北京一次 ADS 任务下载亚洲 40 个 lead、122 MiB、126 秒；广州
0 次 ADS 任务、2 次缓存命中；两点 GFS 预测分别 5.0/3.7 秒。目标为 2026-11-14 dusk。
这是路线 A 的真实点预测与跨点复用证据，不代表当前脏工作区、EC、区域、实况和报告发布均已通过。

### 已有测试记录的边界

- 展示批次记录：全量 pytest 2031 项、前端合同 27 项及 Rust/Swift 构建通过；
  `/tmp/firesky-parity-macos` 新包和 C/Swift FFI、fixture、bundle smoke 通过。
- 退役批次记录：Rust 255 通过；Web Playwright 51/51、check/build 通过；pytest
  **2032 通过、1 跳过、2 失败**，两项为区域 CLI 的 GFS/EC 参数化用例 120 秒超时。
- 本次源码核对：`tests/test_rust_runtime_forecast.py::runtime_inputs` 在临时目录生成
  GFS/EC/CAMS GRIB fixture，`run_forecast` 有 120 秒超时。这是离线运行时回归测试，
  不是实时上游验收。超时根因尚未确认，不能根据改动范围直接归因于网络或断言无回归。
  根因已在 2026-11-13 基线批次确认并闭环：fixture 回归（工作区已修复，非共享 Rust
  生产回归），见下节与 `output/macos-baseline-20261113/regional-timeout-root-cause.md`。
- Docker 仅有静态/Node 契约与本地 HTTP 托管证据；文档记录当时 daemon 不可用。
  接手时重查环境，不能把这条旧环境记录当作当前状态，也不能将其写成镜像实际运行通过。
- 当前没有覆盖上述所有改动的同版本最终全绿记录；不同批次数量不直接相加。

### 2026-11-13 基线批次（第一步执行结果）

证据目录 `output/macos-baseline-20261113/`，索引 `EVIDENCE_INDEX.md`。本批在当前
脏工作区（HEAD 75371d1 + 未提交改动）实际运行构建/测试/replay，区别于上一批「仅文档
核对」；网络相关运行全部包裹断网沙箱，仅用已有缓存/fixture，未下载上游。

- 包身份：`make desktop-macos` exit 0；source_id `d6785dbd…7775`（native_abi 3）
  bundled engine/dylib/源码三方一致；arm64 codesign 通过；FFI/Swift smoke 与无 Python
  门禁通过。x86_64 未构建（第四步）。
- 区域 CLI 超时闭环：当前工作区定向复验 2/2 通过（每例约 1 秒）；HEAD 旧 fixture
  断网对照实验重现机制——区域 CAMS area/lead 上下文不匹配使 prepare 尝试真实 ADS，
  联网时挂起 >120 秒。归因为测试 fixture 回归，工作区已修复，非共享 Rust 生产回归；
  未扩大超时。
- 页面走查（16 张截图，覆盖五页）：点 GFS/EC ×宽/窄 + 深色概览/台账 + 诊断 +
  单模式失败 + 全无效；区域 GFS/EC ×宽/窄（真实地图、色带、网格详情、剖面、高值排行）；
  实况 fixture（缺测≠0、多光谱云类「未计算」附原因）；数据（库存与就绪分离、官方源
  探测断网下如实连接失败）；设置（保留空值=停用、同步默认关、ADS Key 本机 0600
  经 UI/实现/磁盘三方核实）。
- 流程走查：check→未确认拒绝→prepare→cycle-age 门→确认 forecast→任务历史→
  result_restore（不可变快照 sha256 + 读租约），点/区域均离线 replay；失败保留旧结果
  （GFS 完成 + EC 未完成，指标 —）。
- 剩余缺口与归属：SwiftUI——实况低云状态原始枚举 `supported` 未本地化、深色区域/
  键盘/VoiceOver 未走查（第二步）；共享 Rust——点预检 GFS missing_hours [55,59,60]
  宽于预测 f056–058 的 lead 范围（第二步）、冻结剖面约 30 分钟有效期 UI 提示（第二步）；
  真实上游——EC 点/区域、≥2 帧真实 HSD 实况、冷准备、同步 A1–A8（第三步）；
  x86_64/clean-machine/公证（第四步）。

第一步完成标准达到：当前包能复核主要页面/流程；缺口有证据与模块归属。第二至五步
顺序不变。

### 2026-11-13 交付批次（第二至四步执行结果）

证据目录 `output/macos-delivery-20261113/`，索引 `EVIDENCE_INDEX.md`，结论
`DELIVERY_SUMMARY.md`。最终二进制 `dist/native-macos/FireSky.app`（arm64 thin、
adhoc 签名），source_id `d6785dbd…7775`（native_abi 3）与全部真实验收一致；
网络仅走引擎内置官方端点（NOMADS/ECMWF/Copernicus ADS/NOAA Himawari S3/AWS SRTM S3）。

- 第二步：Store.swift 冻结剖面过期/缺失本地化（regional+point 两路径）；
  WorkbenchSnapshot fixture 低云枚举修正（detected）。真实结果回放截图覆盖
  点/区域/吐鲁番/实况 ×宽窄/深色（网络禁用 sandbox，标签「历史缓存回放」）；
  负向状态留证（TTL 过期缺失、无事件网格）。键盘/VoiceOver 完整走查仍为缺口。
- 第三步：真实闭环——热/冷点（北京）、负海拔（吐鲁番 -154m）、杭州 2×2 网格区域
  （gfs_cycle_override 为产品功能）、北京无事件负向、杭州 2 帧真实 HSD 实况；
  取消/杀进程全生命周期（cancelled/interrupted→重试 done）。同步按设计第 14 节
  逐项对照：A1 冷同步四轮独立尝试均真实 fail-closed（ADS 资产读流失败，
  120s×3 内部重试；成功分支 pending 待上游带宽恢复）；A2 去重缓存命中已验；
  A3 手动先行/让路、
  A5 熔断/解锁复用既有 acceptance-sync-20261113（同一 source_id）；A6 同步中取消
  已验（run1 终态 cancelled，取消→终态延迟 441s——CAMS 取消只在 ADS 批边界检查，
  desktop_sync.rs:810，已记为实现缺口）、A8 同步中 kill -9→重启 interrupted、
  无自动续跑、凭据 grep 0 命中已验（sync-cancel-kill/evidence/synckc-*）；
  A7 滚动清理机制已验
  （cams_cycles=1：scanned=2、removed_count=2、freed 6.10MB；retention 按文件名
  解析周期，播种需改写旧周期文件名），全协议（3 周期×≥4 run）保持 pending。
  A4 GFS/EC 同步：GFS 已验（收敛轮结构化 synced/41 命中/0 下载 + 首轮真实周期
  273.55MB，官方 nomads.ncep.noaa.gov，含 A2 去重）；EC 真实下载分支已验
  （559.8MB/58 文件，官方 data.ecmwf.int，ecmwf_source=ecmwf），整批收敛在今日
  带宽下超驱动窗口（900s/1500s/2400s）保持 pending，非引擎缺陷
  （sync-a4/evidence/a4conv-gfs-run.json、a4-final-cycle-size.json）。
- 第四步：rust 260/0；pytest 2035 passed/1 skipped；shared-web 149 通过、
  svelte-check 0/0、build 8.72s；原生冒烟全绿 exit=0（gate-native-smoke.log：
  资源清单/C FFI/Swift FFI/27 项 fixture 预检+location client/应用冷启动/包冒烟；
  SRTM 官方瓦片因上游带宽退化用同一官方 URL 断点续传预置缓存后复跑，
  未用 clean-machine 模式）；x86_64 交叉构建按设计 exit 2（缺 x86_64 eccodes，
  第一版仅 arm64）；性能样本见各 evidence/timings.json 与 real-point/evidence/
  perf-sample.json（热 prepare 0.65–0.98s，冷 209–440s，forecast 3.25–15.3s，
  含 CPU/峰值 RSS 采样）。
- 如实缺口：x86_64、Developer ID 签名/公证、键盘/VoiceOver 走查、A1 成功分支
  （受 ADS 上游稳定性影响）、A4 EC 整批收敛（GFS 已验、EC 下载分支已验）、
  A7 全协议、Web 对齐（第五步，不在本次 macOS 交付范围）。

## 3. 下一步顺序

### 第一步：从当前 macOS 应用建立可运行基线

1. 记录 HEAD、工作区差异和必要新文件清单/哈希，从当前源码构建 macOS app，核对包内
   dylib、引擎 `source_id` 与源码身份；旧包或单独 HEAD 不能代表完整工作区。
2. 先用已有缓存/fixture 走查点预测、区域、实况、数据/设置和历史任务。按
   [macOS 基准覆盖面](../MACOS_UI_BASELINE.zh-CN.md)记录实际操作、截图、字段与缺口。
3. 验证预检、准备、确认、进度、取消、失败保留旧结果、重试和重启恢复；明确哪些缺口
   位于 SwiftUI、FFI 或共享 Rust，再按影响 macOS 日常使用的程度修复。
4. 两项区域 CLI 超时作为共享 Rust 回归待办，在区域流程排查中重现；检查 fixture
   上下文、模式时次、CAMS 区域身份/覆盖、缓存及阶段耗时，不直接扩大超时或归因于上游。

定向复验入口：

```bash
pytest tests/test_rust_runtime_forecast.py -q -k native_cli_regional_has_real_points_frames_and_separate_registry
```

完成标准：当前包能复核主要页面/流程；缺口有证据与模块归属；共享核心失败如实记录并处理，
不以完成 Web、Docker 或通用 HTTP 管理接口作为开始 macOS 验收的前置条件。

状态（2026-11-13 基线批次）：已执行并达到，见 §2「2026-11-13 基线批次」；剩余缺口
转入第二至四步。

### 第二步：完善 macOS 功能与 UI，形成跨端基准

1. 以 macOS 产品需求和实际 Rust 字段完善摘要、趋势、评分组成、逐时台账、剖面、区域
   地图/详情、实况诊断。旧 Web 中的额外字段先评估用途，不自动复制全部页面与布局。
2. 验证 1440×900、1120×720、窄窗口，浅色/深色，长标签、负高度、全无效和单模式失败；
   检查读数、单位、时间、缺测断线、图层、色带和焦点，保存截图，实测键盘与 VoiceOver。
3. R04/R12 优先核对 macOS 使用的卫星按需准备、科学字段/空间/时间完整性与取消；
   R08/R09 优先核对历史结果恢复与周期/天数保留。已有城市排名、发布探测、同步与快照直接复用。
4. 点预测任意时次剖面按 macOS 流程需要补齐，优先复用 `profile_query.valid_time_utc`；
   先确认实际接线缺口，再考虑扩展 RPC。
5. 将通过的页面/操作/状态写入基准证据表，保留未通过项；SwiftUI 当前缺陷不能固化为其他端的目标。

完成标准：macOS 主要功能、界面和状态语义有清晰的已验收/待验收记录；其他端能据此逐项对齐。

### 第三步：完成 macOS 真实数据与后台同步闭环

1. 在原生 UI 中补 EC 点、GFS/EC 区域、至少两帧真实 HSD 实况及受支持的报告读取；
   记录来源、run/lead/valid、AOD/廓线与文件哈希。跨线区域和实况地图按各自范围单列，
   不把尚未接入 UI 的独立地图能力写成已验收功能。
2. 复用有效缓存验证热路径，用隔离缓存验证冷准备；核对预检反馈、下载进度、取消、失败
   与重新执行的完整用户流程，保留旧有效结果和用户缓存。
3. 按[同步设计 A1–A8](../DATA_SYNC_HELPER_DESIGN.zh-CN.md)从 macOS 设置、立即同步和任务历史
   验证冷同步、去重、周期完整性/体积、熔断/解锁、取消、保留与重启；多 run 长期验收单列。
   `test_rust_desktop_sync.py` 是离线合同证据，手动 prepare 也不能代替后台同步实测。
4. CAMS 排队期间的组锁等待、上游失败、缺凭据与覆盖不足需在 UI 如实呈现；分别记录
   实现问题与认证/排队/网络原因，未通过项保持 pending。

完成标准：macOS 受支持工作流有真实输入和 UI 证据，能区分冷/热路径与失败状态。

### 第四步：macOS 发布与性能验收

- 同一源码下完成 Rust、必要 Python 差分、C/Swift FFI、macOS fixture/流程门禁；
  报告 passed/failed/ignored/skipped，保留受影响 Web 合同检查以发现共享核心回归。
- 逐架构验证 macOS ARM64/x86_64 bundle、资源完整性、无源码/无 Python 启动、旧配置导入、
  clean-machine、签名/公证与用户数据保留；各架构实际缺项单独记录。
- 测量 macOS 点/区域/实况的冷输入、热输入、结果缓存与少量并发：端到端 P50/P95、
  CPU、峰值内存、下载/解码/评分/序列化耗时，再按瓶颈优化。
- macOS 验收包、截图、操作状态及 Rust 数据语义共同形成可追溯的跨端基准版本；
  macOS 发布与完整多平台项目验收分别报告。

#### 2026-11-14 性能优化接续

当前性能权威记录见
[性能优化路线图](PERFORMANCE_OPTIMIZATION_ROADMAP_2026-11-14.zh-CN.md) 末尾
「PendingSolarCell 紧凑化与验证」。本轮只改 `engine/src/native/national.rs` 的
pending 工作记录布局：实测 288 B → 152 B，保留两遍顺序、路径缓存和浮点表达式；
未新增公开性能开关。缩减 256 点三轮 A/B 的 raw assessment/samples 逐位一致，
峰值均值约少 9.81 MB，但 CPU 均值无收益，忙机 wall 约 -1.43% 不能当稳定加速。
全国 36,685 点单轮峰值 3,920,613,240 B（约 3.651 GiB），12 个张量逐位一致，
最佳时刻/排名零变化；wall 相对旧权威约 -9.85% 只是不同源码/负载下的观察。

全国 compare 已闭环（11-14 第三轮）：仅改验证工具，识别旧 f64 回退合同并如实
计数（14,072 点、max 2.93e-9），raw 张量/评分/排名零位差；验证器独立构建于
`output/performance-20261114/verify/`，原 A/B 二进制未动，验证器单测 4/4。
桌面 CLI 产品链路已闭环（11-14 第四轮）：当前源码 `forecast --regional
force_refresh=true` 全国 36,685 点成功，结果与冻结 bench 逐位一致（仅产品层
补注 timezone_name），城市排行 694 城 ready，缓存命中轮 0.763 s 返回逐位一致；
峰值 3.7166 GiB < 4 GiB，忙机 wall 504.7 s。证据与边界见路线图「第四轮」。
IPC 会话层已闭环（11-14/15 第五轮）：hello→check→prepare→forecast(confirmed)
→progress→task_history→result_restore 全链通过，restore 与 forecast 逐位一致，
forecast 418.8 s（忙机）。精确稀疏 motion 缓存模拟证伪（Exact 365,660 键、
99.96% 单次使用、LRU 0% 命中），不实现；Metal 单 kernel 原型位级一致
（严格编译 0 位差）且 12× 单核吞吐，但只覆盖 <0.5% 热路径，不接线。
GUI Swift 层点击与真实下载路径仍未验收，不能写成通过。完整 lib
测试 455 passed / 3 ignored。下一步优先在空闲受控负载下
重复当前 A/B，并补 GUI 点击与下载路径验收；SkyBuffers 块化需先设计。
线程 8 vs 10 的忙机观察已记录：8 线程峰值约少
105.8 MiB，但 wall/CPU 无收益；`min_len=8` 忙机 wall 略低但峰值更高，均未达到
改默认门槛。保持 macOS 主线、0.25° 单模型和 4 GiB 上限，
不恢复 Windows/Linux 工作，不覆盖现有未提交迁移/同步/UI 改动。

### 第五步：其他 UI 向 macOS 对齐，再完成 Web 部署

1. 以 macOS 基准建立逐页/操作/字段/状态差异矩阵，核对 `apps/shared-web/`、旧
   `frontend/` 和未来 Windows/Linux 的行为。先对齐日常预测、数据/任务和结果展示。
2. Web 沿用同一 Rust 能力，通过 HTTP 适配 macOS 已验证流程。R01 通用区域诊断、
   R03/R05/R10 管理/刷新与旧接口，仅按实际 Web 消费者补齐；保留源身份和失败语义。
3. Web 独占 admin、鉴权、SSE 等部署能力单独决定保留方式；系统文件/目录、应用退出等
   行为按平台适配。完成矩阵和消费者核对后再处理重复前端，平台控件保留原生惯例。
4. Web 交付阶段修复 Docker 静态根、完成镜像 amd64/arm64 build/run、首页/资源/鉴权/报告
   与真实工作流验收。Docker 缺口不阻塞当前 macOS 主线。
5. Windows/Linux 维持暂停，待用户恢复后参照同一 macOS 基准实现与目标机验收。
6. 按实际产品/测试消费者及替代验收结果归档 PySide/spec、清理 legacy 模块，保留必要 oracle。

后续 Docker 待办的具体证据：Dockerfile COPY 到 `/app/frontend/build`，而 workspace
`/app` 下 Rust 默认读取 `/app/apps/shared-web/dist`；Dockerfile、Compose 和 entrypoint
未设置 `FIRESKY_WEB_ROOT`。应在部署阶段统一 COPY/静态根配置，并验证首页和 JS/CSS，
health 成功不能证明 SPA 可用。此项已于 2026-11-14 在 `feat/win-linux-docker-parity` 修复并经本地 compose 冒烟验证（首页、JS/CSS、桌面任务生命周期、令牌与任务历史持久化）。

## 4. 继续保留的范围决定

- macOS 为产品与 UI 基准；Web/Windows/Linux 最终向其对齐。旧 Web/Qt 差异只作历史
  能力参考，当前工作按 macOS 使用体验、共享核心及原生交付需要排序。
- Windows/Linux 原生 UI 开发暂停；保留未来目标，不自动启动平台开发。
- GEE 路线 B 整体暂缓；恢复时必须覆盖 nowcast，不先加仅 UI 生效的开关。
- 模式单点预测地图不做；区域地图必须渲染；地图与图表不要求联动；实况地图单独立项。
- 保留策略空值仍为停用，不把示例周期数或天数设成默认值。
- `frontend/` 有 admin、鉴权、SSE 等独占能力，`apps/shared-web/` 是当前默认构建入口；
  两工程合并先做逐页/路由/消费者矩阵，不整体删除 `frontend/`。
- 本次仅分析并更新文档，不自动开始上述实现、下载、提交或发布。

## 5. 接续时应交付什么

每个工作包记录：实际改动与受支持范围、源码/二进制/安装包身份、测试命令与结果、
fixture 或真实上游的区别、输入与时间来源、证据目录、剩余缺项。旧批次原始记录继续
保存在交接、退役、展示和同步设计文档，本文只负责当前状态与执行顺序。
