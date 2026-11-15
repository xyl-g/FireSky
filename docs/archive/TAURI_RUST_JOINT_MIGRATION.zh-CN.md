# Tauri、共享 Web 与 Rust 联合迁移清单（冻结参考）

更新日期：2026-11-12。与 `RUST_REMAINING_WORK.zh-CN.md` 的 Q01–Q20 一起执行。
本文区分现有代码、待实现工作与待验收目标；不是全部移植完成的声明。

> **架构决策已变更（2026-11-12）**：正式桌面主线改为“原生 UI + Rust FFI”，先完成
> macOS，再按同一 FFI 合同扩展 Windows/Linux。本文保留 Tauri、共享 Web 和历史联合
> 测试记录，作为冻结参考，不再作为桌面产品路线或完成门槛。新的主线见
> `docs/archive/NATIVE_UI_FFI_MIGRATION.zh-CN.md`。Docker/Web 仍可复用 Rust HTTP，但不再要求
> 复用 Tauri 桌面页面。

## 1. 正式交付范围

| 交付形态 | 必需架构 | 运行方式 |
| --- | --- | --- |
| macOS | ARM64、x86_64 | Tauri + 随包 Rust 引擎及原生依赖 |
| Windows | x64、ARM64 | Tauri + 随包 Rust 引擎及原生依赖 |
| Docker | amd64、arm64 | 同一份 Web 业务界面 + Rust HTTP 服务 |

- 保留当前 Tauri 的布局、导航、视觉及 Swift 设计原稿，不复制旧 Web 的布局。
- 共用一套业务页面和 Rust 核心，不另建 Docker 专用业务前端。
- 桌面使用 IPC，浏览器使用同源 HTTP；差异限于连接、凭据和系统能力适配。
- 所有预测均执行检查、准备缺失数据、确认预测，不因浏览器运行而绕过预检。
- 面向开源、本人及约 2–3 人轻量自托管，不做公网运营、多租户或集群架构。
- 不重新引入 Python sidecar、Qt 产品或独立 SwiftUI/WinUI 产品交付。

## 2. 首批实施边界

本次先落地 T01 的共享连接基础，以及 Docker 构建同一份 Web 产物：

1. Rust 增加桌面会话专用 HTTP 任务通道，复用现有 Runtime，不走绕过确认的普通预测入口。
2. 前端保留 `ipc.ts` 门面，按运行环境选择 Tauri IPC 或 HTTP。
3. 浏览器获得真实进度、实例失效与协作取消；取消请求被接收不等于计算已经停止。
4. 浏览器不调用打开服务器目录、退出桌面进程或重启共享服务等本机能力。
5. Docker 构建 `apps/desktop-tauri`，不再构建旧 `frontend` 页面；
   `/app/frontend/build` 仅为 Rust 静态托管目录，不代表继续维护旧前端。

上述基础落地后，T02–T08 仍须按下列清单逐项实现或验收。
当前批次的具体测试结果应在文末记录，不能用历史 fixture 结果替代。

## 3. 逐项工作

### T01 共享页面与连接适配

关联：R08、R10、R11、V03。

- 已有：Tauri 长命 Rust sidecar、请求句柄、事件订阅、generation 隔离及协作取消。
- 首批已实现：HTTP 桌面任务注册、状态/结果/真实进度查询及取消请求；
  按服务实例识别旧连接，拒绝跨实例请求，不把旧会话带到新服务。
- 首批已实现：浏览器连接使用与桌面相同的业务 DTO，库存也走相同 Runtime 方法，
  不直接拿不同结构的 `/data/status` 替代。
- 首批已实现：浏览器“重新连接”仅重建客户端连接，不重启所有人的共享服务；
  不将断网、停止轮询或取消 fetch 当作 Rust 任务终止。
- 首批已实现：报告使用引擎返回的受限 URL；本机报告路径不转换成任意服务器文件访问。
- 首批已实现：打开缓存目录、退出应用等仅在桌面出现；浏览器不暴露未受保护的凭据写入接口。
- 已实现：桌面、浏览器和通用 HTTP point/regional/nowcast 任务共享持久历史、
  终态与事件查询；服务退出后的任务在确认 owner 锁释放后记为“已中断”，不自动重启。
 结果仅在运行期保留完整值，重启后返回受限结果引用。
- 未覆盖：旧 CLI 路径、手动重试入口、历史事件分页/按时间淘汰和真实目标机强杀验收；
  完整范围见 R08。
- 验收：同一套页面在 Tauri 与普通浏览器都能检查、准备、确认、查看结果；
  快速取消、迟到回包、服务换代和页面离开均不会误报成功或串进度。

### T02 已有业务内容核对与结果发布

关联：R01、R03、R08、V01、V03。

- 已有：双模型摘要、九指标、质量曲线、物理趋势/AOD、逐时台账、
  时间和周期、云场/相态/演变、评分修正、光路气溶胶及卫星/雷达约束展示。
- 已实现：发布结果与草稿检查/准备分离；按结果类型分别保留完整发布快照，检查、准备、失败、取消、参数变化及重连不会清空旧结果；新结果校验成功后才原子替换并记录发布时间。
- 已核对：前端字段保留数值零、缺测、无目标云、部分模式失败和实际 UTC/本地时间语义；动态查询错误不覆盖已发布结果。
- 待验收：仍需真实点/区域/实况结果逐项核对 Rust 字段、单位及来源；fixture 不能证明下载、算法或数据来源已验收。

### T03 动态剖面与统一选中时次

关联：R01、R10、V03。

- 已实现：单点预测与实况结果共享精确 UTC 选择，趋势、逐时台账、气象剖面共用同一时间身份；保留亚秒精度，不因显示时间舍入而合并样本。
- 已实现：Rust 查询绑定结果、来源 SHA、模式、授权观测坐标和选中 UTC；Tauri IPC 与浏览器 HTTP 走同一 DTO。查询使用结果冻结张量，不触发下载或换周期，源过期明确返回。
- 已实现：加载、缺失、不可用、失败、过期、取消、跨 generation 及快速切时次的迟到响应隔离；卫星逐步光路诊断与原始固定垂直剖面分离，不重标或拼接异方位样本。
- 已接入区域结果视图：选中网格后，趋势、逐时台账和动态剖面共用按该格点锚点及压缩帧偏移派生的 UTC 时次；保留亚秒精度。剖面仅在用户打开剖面页时，按所选网格坐标和时次请求冻结来源；本地 AOD 与质量 AOD 使用不同字段和标签。
- 尚未完成：区域 UI 当前仅有 fixture 验收，未由真实区域/CAMS 结果证明。动态区域剖面保留上限为 100,000 个格点，超过上限时区域预测仍可完成，但剖面明确不可用；上限扩展、真实数据、跨平台时区和长期保留仍需验收。

### T04 区域诊断、城市排名与跨日界线

关联：R01、R03、R07、R13。

- 已有：GFS/EC/双地图、完整网格、矩形拖绘、选中网格摘要和来源/精度展示。
- 部分实现：网格选中详情可查看已发布的逐点诊断字段、UTC 趋势、逐时台账和模型剖面；GFS/EC 仍分别展示。详情仍受现有逐点结果字段限制，不代表独立区域诊断 API 或完整有利/不利因素归因已完成。
- 已实现：区域任务从安装包内置的 695 城市目录筛选地点，复用同一模式、周期、
  事件与输入快照执行 GFS/EC 二次评估，按固定排序合同发布独立城市排名；
  Tauri 页面支持网格/城市切换、搜索及双模型分开查看。
- 未完成：区域刷新身份比较、缓存复用和独立管理刷新 API；失败保留旧结果的管理
  流程仍需完整接线和验收。
- 部分实现：Rust 已保留 wrapped bbox 的 west/east 顺序，GFS 缓存覆盖判断、
  EC 解码裁剪和上下文经度联合不会把窄区域扩大到全球；GFS/EC 事件计划在
  CAMS 单矩形边界处明确失败。待实现：CAMS 双区域下载/合并、nowcast 的 wrapped
  CAMS，以及选区、表单校验、请求、拼接、地图范围与选点的完整跨日界线链路，
  不只在后端放开 east/west 校验。
- 验收：区域结果、排名和诊断来源一致；边界两侧无重复列、错位或近全球范围误下载。

### T05 统一预检、进度与恢复

关联：R04、R05、R06、R08、R12。

- 已有：三类预测的 check → prepare → 再次确认 forecast，
  参数/连接变化会作废令牌；数据页库存、下载探测与预测会话分开。
- 已实现并通过 fixture 验收：预检展示各数据源所需 UTC 周期、最新发布状态和缺失时效/有效时次；
  数据完整但周期已确认较旧，或最新发布未核验时，须单独勾选确认。
  前端控制器与 Rust 会话端都拒绝未确认的预测请求；弹窗错误可播报，短屏操作区保持可见。
- 尚未真实验收：CAMS/上游返回的周期与缺失项、下载失败/取消/过期令牌等完整服务场景。
- 待核对：准备完成后使用同一冻结请求检查完整性，不能凭文件数或空缺项数组报 ready。
- 已实现：Rust 持久任务收据、进度/事件重放、终态筛选、owner 锁与进程退出恢复；桌面和浏览器任务页可查看、手动恢复参数及删除终态收据。恢复只填表，不启动检查、准备或预测；用户仍须显式检查/准备/确认。
- 已实现：Docker 将任务历史放入 `/data/state/tasks` 独立卷；脚本烟测定义非法点检查、读取失败历史、重建容器后校验记录未改变且没有自动重启。该跨容器脚本尚未真实运行。
- 已覆盖：通用 HTTP point/regional/nowcast 启动、详情、列表、取消和 SSE 事件均
  使用同一 `TaskHistory`，重启后不自动重新计算。CLI 入口和真实容器重建验收仍未完成。
- 待清理：把容易误解为独立预热的入口统一到按需准备，
  不新增后台定时卫星下载和手动 CAMS run pin。
- 验收：真实 CAMS 和缺时次、坏文件、下载失败、取消、过期令牌等场景；
  保留旧有效结果，取消后等真实任务终态再解除忙碌。

### T06 设置、缓存与历史报告

关联：R09、R11、R13。

- 已有：主题、EC 来源、地图来源/底图、内存地图 Key、CAMS 配置检查、
  桌面重连及受限目录打开；浏览器能力需要单独适配。
- 已实现核心：Rust `retention` 合同支持 GFS、EC、CAMS 分别按周期数清理，
  卫星按扫描时间滚动天数清理，报告/地图按天清理并保护仍被引用的地图；
  只在请求成功后执行，失败下载不会淘汰旧数据，删除会真实释放磁盘。
- 已实现：Rust `retention_settings` 合同、workspace 原子配置文件、Tauri/浏览器
  设置页读写；空值表示停用该类自动清理。点/区域/实况准备成功及 `/data/fetch`
  成功后会读取配置并执行清理。
- 未完成：默认保留值仍由用户设置，任务引用生命周期与跨平台并发/崩溃验收仍缺。
- 已保持：报告保留期间不依赖原始天气缓存，用户导出到其他路径的文件不在清理范围。
- 待核对：城市/时区资源随包内置，浏览器显示地点当地时间，内部使用 UTC。
- 实施参数待定：缓存默认值、失败下载残留策略及轻量远程鉴权方式；
  不把旧配置自动迁移或任意引擎路径编辑擅自列为必须功能。

### T07 桌面壳、地图与能力保护

关联：R10、R11、V03。

- 已有：单实例、菜单、忙碌退出确认、EOF 后有限等待及直接子进程回收；
  不等于所有原生 GUI 与后代进程场景已验收。
- 待加固：CSP、资源/网络允许范围、系统定位用途声明与权限流程。
- 待验收：天地图/OpenFreeMap、真实瓦片/授权、切源重放、地图选择/拖绘、
  原生菜单、取消后退出及引擎故障恢复。
- 不做：云层地图叠加、可烧云/挡光云叠加及逐帧播放；
  后端已有地图服务不因此被无差别删除。
- 不做：复制旧 Web 管理后台、多租户账户体系或公网运营面板。

### T08 构建、架构闭包与联合验收

关联：V01–V05、O01、O02。

- 待实现：按目标架构构建正确 Rust 引擎及 ecCodes 动态库/definitions/samples，
  缺必需依赖时发布构建失败，不能只告警后继续。
- 已实现首版：`resources/app-data/` 提供城市目录、完整地理时区数据和
  `RESOURCE-MANIFEST.json`；Tauri/Docker 通过 `FIRESKY_RESOURCE_DIR` 注入只读
  资源根，缓存/报告/任务历史仍使用独立可写目录。仍待各架构 clean-machine、
  无网络城市模式和真实排名验收。
- 待完成：图标、签名/公证、Windows 安装与 WebView2、
  macOS/Windows 各架构 clean-machine 验收、Docker 两架构真实运行。
- 待验收：相同源码版本的 Rust、Web/桌面合同、UI、真实 CAMS 点/区域/实况及报告全链路。
- 待测量：冷/热缓存、结果复用、P50/P95、峰值内存及 2–3 人提交请求；
  用实测决定有界队列与重计算并发，不承诺未经测量的加速倍数。
- 待清退：替代验收后移除旧 Python 产品入口，仅保留隔离测试/数值对照；
  旧 SwiftUI/WinUI 源码只作参考。
- 最终门槛：所有实现、优化和清退完成后冻结源码，再执行 V04；
  任一必需目标未通过，就明确标记未完成。

## 4. 联合执行顺序

1. T01 连接适配与同源 Docker Web，先取得不依赖 Tauri API 的真实 Rust 握手。
2. R12/R13 + T05：统一预检与必需资源；T02：保留上一份有效结果。
3. T03/T04：统一时次、动态剖面、区域逐点诊断、城市排名与跨日界线。
4. R08/R09/R11 + T06/T07：任务恢复、缓存/历史报告、轻量权限及桌面能力加固。
5. V01/V02 + T08：真实数据、六类目标产物、原生 GUI 和浏览器分别验收。
6. V05/O01：测量优化；O02：替代后清退；最后执行 V04 最终统一门禁。

## 5. 本次首批验证记录

本节只记录本批次重新执行的结果，不复用以前 80 项 Node / 40 项 UI 等历史结果。

- Rust HTTP 聚焦测试 **12/12**，debug 引擎构建通过。覆盖任务实例隔离、进度、
  worker 退出后取消终态、运行中禁止 release、40 次短任务释放及静态根路径解析。
- Tauri 壳 Rust 测试 **18/18**；包含原生引擎握手、取消注册顺序、重启隔离、
  EOF 回收与受限报告打开，不等于原生 GUI/安装包验收。
- 前端 Node 测试 **102/102**，Svelte **0 errors / 0 warnings**，随后生产构建通过。
  新增 HTTP 合同、release、进度与重连测试；旧 status/start/poll/cancel 请求的
  两类迟到失败共 8 组回归，确认不会断开重连后的新连接。
- 完整 Chrome Playwright fixture **43/43**（单轮 26.3 秒，原 40 + HTTP 3），
  覆盖桌面 IPC 及浏览器检查/准备/显式确认、协作取消和只读能力。
  原生 fixture 补齐官方 Tauri 环境标记及窗口元数据，不通过修改生产分流绕过测试。
- 真实 Rust HTTP 服务使用隔离空缓存工作区，通过 **40 次**
  hello → poll → release，以及旧实例 409 拒绝；共享 SPA 由 Rust 直接托管。
- 真实 Chrome 无 Tauri mock，检查握手、库存、只读 CAMS 设置和单点页面，
  覆盖 390/768/1024/1440px、明暗、三个页面共 **24 组**布局检查；
  最终生产产物再次复验上述布局及浏览器重新连接。
  未见页面错误或文档/工作区横向溢出；仅调用 hello/inventory/cams_credentials，
  没有下载、预测或凭据修改。天地图未提供 Key，不算真实瓦片验收。
- `docker compose config --quiet` 和结构化断言通过：默认 loopback、
  Redis 默认不运行、显式 legacy profile 才出现。
- 本机 Docker daemon 不可用，未构建或运行镜像；未验收 Docker amd64/arm64、
  四种桌面架构安装包、原生 GUI、真实 CAMS 及端到端预测。
- 当前前端构建仍提示主包和 MapLibre worker 较大；本批未声称完成拆包或性能优化。
- 通用 HTTP 注册表的取消句柄和完整结果仍为进程内态，但任务收据与事件已持久化，
  重启后列表/详情可恢复且运行中任务不会自动重启。未执行全量科学计算回归、
  性能测量或 Python 产品入口清退，不将本批连接适配写作全量迁移完成。

## 6. T08 本轮落地与发布边界

记录日期：2026-11-11。以下是构建合同与本地验证结果，不是六类产物已经发布或验收通过。

### 目标与构建合同

- 必需桌面目标为 `aarch64-apple-darwin`、`x86_64-apple-darwin`、
  `x86_64-pc-windows-msvc`、`aarch64-pc-windows-msvc`；
  Docker 为 `linux/amd64`、`linux/arm64`。Windows ARM64 与 x64 同为必需目标。
- 准备脚本显式执行 `cargo build --locked --release --target <目标>`，尊重
  `CARGO_TARGET_DIR`，只读取该目标目录中的引擎；不复用宿主机旧产物，
  不把 universal 或 GNU 目标冒充这四个目标。
- 用真实 PE/Mach-O 头检查引擎及全部打包动态库架构、依赖闭包；
  ecCodes 库、非空 definitions/samples、依赖版本、许可证声明及实际许可证文件缺一即失败。
- 先在相邻临时目录构建并复核，再整体替换资源目录；
  不与旧目录合并，避免残留另一架构的 DLL，验证失败前保留旧资源。
- `RESOURCE-MANIFEST.json` 记录每个文件大小、SHA-256、二进制架构/导入、
  所属包版本和许可证，以及尚未验证的签名、clean-machine、真实数据状态。
  `BUNDLE-METADATA.json` 使用 `firesky-native-dependencies-v1` schema；
  每个包包含 `name/version/license/files/license_files`。
- macOS 正常 Homebrew 前缀自动收集实际已安装 Cellar 版本、递归动态库及许可证；
  不以当前 formula 的最新版本冒充本机二进制版本。
  Windows vcpkg 收集指定 triplet 的 release DLL、数据和 copyright，
  并要求已安装包版本与 pinned ports 的许可证元数据版本一致。
- macOS 仍以 Tauri `minimumSystemVersion: 12.0` 为门槛；
  Rust 引擎构建设置同一 `MACOSX_DEPLOYMENT_TARGET`。
  每个 Mach-O 缺少最低部署版本或要求更高系统时均明确失败，不能靠改文件名或忽略检查宣称兼容。
- `FIRESKY_NATIVE_TARGET` 与 Tauri 目标不一致即失败；
  外置引擎 `FIRESKY_RUST_ENGINE_BIN` 必须配套
  `FIRESKY_RUST_ENGINE_MANIFEST`，验证目标、源码版本和实际 SHA-256。
  独立收据 schema 为 `firesky-native-engine-v1`，字段为 `target/version/sha256`；
  也接受先前完整资源 manifest。
- 自备 decoder 使用 `FIRESKY_ECCODES_ROOT` 和随包元数据，或显式
  `FIRESKY_NATIVE_DEPENDENCIES_MANIFEST`；未知第三方包不得猜测版本和许可证。

### 本轮验证

- `node --test apps/desktop-tauri/scripts/*.test.mjs`：**30/30**。
  覆盖四目标、错误架构/平台、资源缺失、闭包与许可证、SHA 篡改、
  原子替换、外置引擎收据、最低 macOS 门槛、HTTP smoke 失败分支、
  历史记录重建验证及 CI state volume/参数结构。
  测试中的二进制头 fixture 不是可执行程序验收。
- `bash -n scripts/bundle_macos_eccodes.sh`、workflow YAML 解析、
  本批文件 `git diff --check` 通过。
- 在自动清理的临时目录实际组装了本机 ARM64 decoder，收集 **28,540** 个常规文件，
  检查真实动态库架构与重定位后的导入闭包，收集 ecCodes `2.50.0`、
  openjpeg `2.5.4_1`、libpng `1.6.59`、libaec `1.1.8` 的实际许可证。
  未覆盖现有 `dist` 或应用资源目录，也未进行完整 Tauri 发布构建。
- CI 已定义四个原生 Tauri 构建任务和两个原生 Docker 构建/运行任务，
  不是仅测合同的占位矩阵；Docker smoke 检查共享 SPA、真实 Rust hello、
  实例身份、Python-free 标记和 release；另用无上游请求的非法点预检
  （`lat: 91`、非法日期）生成失败任务，读取持久历史。
  CI 显式检查 `FIRESKY_TASK_HISTORY_DIR=/data/state/tasks` 及可写目录，
  以命名 volume 绑定 `/data/state`，删除并重建容器后再次查询，
  要求实例身份已变化、原失败记录的身份/事件序列/更新时间/任务总数不变、
  `automatic_restart=false`，不触发准备或预测。
  **本轮仅通过对应 Node fixture/结构测试，没有执行这些远程 CI 任务**。
- 现有 Windows `offlineInstaller` 保留，未以删除 WebView2 离线依赖简化 ARM64 构建。

### 明确未完成

- 本机 Homebrew 动态库实际最低系统为 ecCodes/openjpeg/libpng **27.0**、
  libaec **27.2**；`otool -l` 独立确认 libaec 的 `LC_BUILD_VERSION minos 27.2`。
  这套资源不支持声明的 macOS 12，严格门槛会拒绝发布；
  需要使用满足 macOS 12 的兼容工具链重建 decoder 及其闭包，不静默提高支持下限。
- Windows vcpkg ecCodes 的 OpenMP/MSVC 可再分发运行库闭包尚未通过真实构建验证。
  若导入 `vcomp140.dll` 或非系统 MSVC DLL，必须补齐对应架构、
  版本与实际许可证的可再分发包，或采用另外经验证的构建方案；
  不能把这些 DLL 加入系统白名单掩盖缺失。
- 新增 Docker 历史持久化 smoke 仍需在两种真实架构运行；
  Node fixture 和 workflow 结构测试不能替代真实容器重建验收，
  仅检查目录存在也不等于恢复验收。
- 图标、发布签名/公证、四种桌面 clean-machine 安装/启动、两个 Docker
  架构真实运行、真实 CAMS 与完整预测、最终统一门禁仍待执行。
  本轮不声称已有发布包、生产可用性、完全移除 Python 或性能收益。

### 源码清单

- `apps/desktop-tauri/scripts/native-target.mjs`：目标、精确 Cargo 输出路径、
  PE/Mach-O 解析及外置引擎收据。
- `apps/desktop-tauri/scripts/native-resources.mjs`：版本/许可证/SHA 清单、
  架构/导入闭包及 macOS 最低版本门槛。
- `apps/desktop-tauri/scripts/prepare-engine.mjs`：目标化构建、临时资源准备及整体替换。
- `apps/desktop-tauri/scripts/bundle-macos-eccodes.mjs`、
  `scripts/bundle_macos_eccodes.sh`：macOS 依赖重定位与 Homebrew 元数据。
- `apps/desktop-tauri/scripts/bundle-vcpkg-eccodes.mjs`：Windows release 资源和版本/许可证转换。
- `apps/desktop-tauri/scripts/smoke-http-runtime.mjs`：同源 SPA 与真实 Rust HTTP hello smoke。
- `apps/desktop-tauri/scripts/native-resources.test.mjs`、
  `bundle-vcpkg-eccodes.test.mjs`、`smoke-http-runtime.test.mjs`：新增构建合同测试。
- `.github/workflows/native-desktop.yml`：四原生桌面与两原生 Docker 构建/运行矩阵。

## 7. T02/T03/R08/T05 本轮实现与验证

记录日期：2026-11-11。只陈述本次实际执行的源码及测试证据；不代表本项目完成迁移或跨平台验收。

- T02 发布结果快照此前已在本轮前端批次落地：每类结果独立保存结果、原始请求和结果 ID，只有有效成功才替换。失败、重连和编辑表单均保留上一份结果。单测覆盖第二次检查/准备/预测失败、取消及迟到成功回包；浏览器实际天气预测未运行。
- T03 新增 UTC 时间身份、共用时次选择、动态剖面请求控制器、Rust 冻结来源查询，以及 IPC/HTTP 前端适配。结果回包校验结果/来源/模式/坐标/时次身份，卫星路径诊断不冒充动态垂直剖面。单点、实况与区域选中网格均已接入；区域仍受 100,000 格点冻结剖面上限约束。
- R08/T01 新增 JSON 原子落盘的任务历史。只保存白名单重试参数和受限结果引用，不保留令牌、地图 Key、ADS 凭据或绝对报告路径；列表最多读 100 条，单记录上限 1 MiB，事件环保留最近 256 条，超出以截断标记呈现。owner 状态不明确或记录损坏时作为 issue 报告，不将未知状态误判为中断。
- 同一 Runtime 内，JSONL desktop 与共享 HTTP desktop jobs 在任务注册、进度、取消、worker 终态时写入历史；完成/失败/取消状态由真正 worker 终态决定。删除只移除终态任务记录，不删除数据、报告或已发布结果。Web 历史页只读轮询，支持查看事件、恢复参数和删除终态记录。
- **Rust**：`cargo test --locked --manifest-path engine/Cargo.toml`，库 **148 passed / 2 ignored**，进程集成 **4 passed**，doc tests 0；现有两项 dead-code warning。覆盖真实 owner 子进程崩溃锁、重开恢复、软链接/未知 owner、结果引用白名单、删除落盘与 HTTP 重启历史。
- **前端单测**：`npm test` **177/177**，包含任务历史并发/代次隔离、事件续读、恢复不启动计算和删除边界；T03 IPC/HTTP 动态剖面测试覆盖原生与 HTTP 两通道。Svelte 检查 0 errors/0 warnings；生产 Vite build 成功，但主 JS 约 1.51 MB、MapLibre worker 约 518 KB，未做拆包优化。
- **浏览器 UI**：Playwright **55/55**，覆盖 HTTP/Tauri fixture、单点/区域/实况预检、旧周期及未核验周期的额外确认、预测/地图/设置、动态剖面、任务历史及 390/768/1024/1440px 布局。新增短屏长地名/密集缺失详情、操作区可见性、44px 触控目标、弹窗错误播报和 Escape 焦点返回检查；目视检查 390×520 亮/暗、滚动到底与 1440×720 预检截图。区域回归仍验证选中网格、精确 UTC、趋势、台账最佳时刻、本地 AOD 标签及 profile_query 坐标/时次绑定。全部使用隔离 fixture，无真实下载或计算。
- **尚未通过最终门禁**：真实下载/预测/CAMS 未跑；CLI 任务、手动重试入口、
  历史事件分页、按时间淘汰尚未实现。macOS/Windows/Docker 真实矩阵、旧 Python
  路径清退及端到端性能也未完成。
