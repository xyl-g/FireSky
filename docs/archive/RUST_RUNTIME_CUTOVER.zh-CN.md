# Rust 原生运行时切换与验收

更新日期：2026-11-13（当前状态与路径复核；历史验证批次保留）。

当前状态与后续顺序见[最新接续计划](NEXT_AGENT_PLAN_2026-11-13.zh-CN.md)，
旧批次收尾/暂停记录见交接记录（HANDOFF_2026-11-12，已删，git history 可查）。本文历史验证记录
不等于当前脏工作区已通过最终同版本验收；退役批次有两项区域 CLI fixture 超时待定位。

用户当前优先级：先完善并验收 macOS；macOS 是产品与 UI 基准，其他端后续向其对齐，
详见[基准规范](../MACOS_UI_BASELINE.zh-CN.md)。Docker 路径与镜像门禁属于后续 Web 部署，
不作为当前 macOS 流程/发布验收的前置条件；最终全项目验收仍逐端记录实际结果。

## 当前结论

默认构建、HTTP 服务、JSON CLI、macOS/Windows 原生桌面启动和 Docker
运行命令已切换到 `firesky-engine`，这些入口不再启动 Python，也没有失败后回调
Python 的分支。Python 源码保留为旧入口、数值差分 oracle 和测试工具。

**默认入口切换不等于全部原有功能完成退役验收。** 原生点/区域和实况主链已经实现；
仍缺的接口，以及未验证的平台见下文。遇到尚未实现的 API
返回 501，不会返回成功占位结果或偷偷启动旧后端。

本文件描述当前切换；`archive/RUST_MIGRATION_STATUS.zh-CN.md` 的 11 月 10 日表格和
`archive/RUST_MIGRATION_PLAN.md` 的阶段清单保留为历史基线，不能据其推断当前启动入口。
正式桌面方向已改为平台原生 UI + Rust FFI：先完成 macOS SwiftUI/AppKit，
再按同一 C ABI 扩展 Windows/Linux 原生客户端；Docker/Web 继续由 Rust HTTP
服务提供。2026-11-13 共享页面迁至 `apps/shared-web/`，Tauri 桌面壳已删除。桌面 FFI
合同与 macOS 门禁见 `NATIVE_UI_FFI_MIGRATION.zh-CN.md`；旧联合清单
`archive/TAURI_RUST_JOINT_MIGRATION.zh-CN.md` 仅保留历史 Web/HTTP 迁移记录。

## 运行

构建需要 Rust/Cargo、Node/npm，以及用于 GRIB 的 C ecCodes 及 definitions/samples。
服务运行不需要 Python、pip、NumPy、SatPy 或 cdsapi。

```bash
make install
make serve
# http://127.0.0.1:8765

engine/target/release/firesky-engine http --workspace "$PWD" --port 8766
engine/target/release/firesky-engine forecast --workspace "$PWD" --input request.json
engine/target/release/firesky-engine forecast --workspace "$PWD" --input regional.json --regional
engine/target/release/firesky-engine desktop --workspace "$PWD"
engine/target/release/firesky-engine call hello
```

`forecast` 使用 JSON 请求。单点例子：

```json
{
  "name": "Hangzhou",
  "lat": 30.27,
  "lon": 120.15,
  "elevation_m": 12.0,
  "timezone_name": "Asia/Shanghai",
  "target_date": "2026-11-12",
  "phenomenon": "dusk",
  "model_source": "gfs"
}
```

CLI 返回独立 `model_results` / `model_errors`，支持 `model_source: "both"`；
HTTP 请求单模式时投影为对应点/区域报告。点/区域计算前须备齐输入；可通过
桌面 check/prepare/forecast 会话或 HTTP 数据下载任务准备。普通实况请求原生
解析 HSD、GFS、CAMS；`allow_download: false` 仅使用本地输入，缺失即报错。
显式 `aerosol` 输入可用于冻结对照，但不能把这种运行记作真实 CAMS 输入验收。
`make serve-legacy-python`（原名 `make serve-python`，2026-11-13 更名）和 `make legacy-install` 是显式旧版/对照入口，不是原生服务的依赖。

## 已实现的生产路径

| 范围 | 原生实现与边界 |
| --- | --- |
| 点/区域 | 动态事件/模式时次规划、预检、严格原始输入身份、GFS/EC 独立预测、CAMS、评分、逐时诊断、点报告、区域点与紧凑帧、理论剖面。单模式失败不覆盖另一模式结果。CAMS 默认按请求所在洲整周期下载（一次 ADS 任务覆盖该 run 全部时效，后续同洲预测走缓存零排队）；payload `cams_download_scope:"request"` 恢复按请求子集合同，跨界双矩形与实况走廊保持旧合同。 |
| HSD | S3 分页 XML 发现、完整 B07/B13/B15 扫描选择、受限下载、串联 bz2 解压、原始亮温标定、AHI `sweep=y` 椭球投影、紧凑 NPZ 原子发布和生产读取器回读。 |
| 实况 | 多帧选择、B13 运动和平流、真实 GFS 时次先验、CAMS 光路、逐观测物理评估、雷达约束、事件质量和前端报告组装。高级冻结输入路径仍可供差分测试使用。 |
| 雷达 | RainViewer 元数据、PNG、Universal Blue 调色板、覆盖/局地/路径降水和对流风险。不可用或覆盖不足保留 unknown，不当作晴空；离线实况不自动访问雷达网络。 |
| 桌面 | Rust 拥有运行时、任务、进度、取消、数据准备、单次确认令牌和结果快照恢复；原生客户端通过版本化 C FFI 调用，点/区域/实况均接入 check/prepare/forecast。macOS SwiftUI 已接入 dylib、任务历史/事件/恢复/重试；Windows WinUI/C# 已接入 DLL，Linux 已有 C++20 FFI 客户端 smoke，后两平台开发暂停。JSONL 仅保留开发诊断；共享 Web 已独立，Tauri 壳已删除。各批 bundle smoke 不替代最终同源码交付验收。 |
| 后台数据同步 | `desktop_sync` 与 macOS 设置/心跳/立即同步已实现，默认关闭；CAMS/GFS/EC 最新 verified 周期与独立状态/熔断/任务历史接线。设计 A1–A8 真实同步验收仍待完成，不包含卫星后台调度。 |
| 报告 | 点/实况报告由 Rust 渲染 HTML，按完整内容 SHA 注册到独立目录，原子发布；HTTP 读取复核文件类型、大小和内容 SHA。 |
| 实况地图 | 显式请求 `include_map_cells: true` 时，由真实评估格点发布地图帧；严格 16 字段帧/13 字段格点、范围和时序校验，内容 SHA token、gzip 原子存储。未产生格点时明确不可用。 |
| 缓存 | 独立 `rust-native-runtime-cache-v1` 命名空间，请求和源码身份、300 秒 TTL、报告/注册表原子发布、读取时重新验证冻结源 SHA 和模式周期。旧 Python 缓存不冒充新缓存。 |
| 历史恢复与保留 | `result_restore(task_id)` 读取不可变内容寻址快照，与上述计算缓存分离；周期/天数保留、快照读租约和存活地图引用保留已实现。冻结剖面源另有有效期，历史结果恢复不承诺重新计算过期剖面。 |
| 发布 | macOS 构建脚本将 Rust 引擎、ecCodes 动态库依赖闭包、definitions/samples 和许可证打包，重写 loader 路径并签名。Windows 构建加入 Cargo/预编译引擎资源与可选 ecCodes 包。Docker 运行层为 Debian + Rust + ecCodes，无 Python。 |

原生区域契约为 `rust-desktop-regional-forecast-v1` / `native-object-v1`。
前端单独验证、归一化这个契约，不伪造旧版 summary 55/cache 125/array-v6 标识。

## HTTP 范围

前缀为 `/api/v1/firesky`。

| 接口 | 行为 |
| --- | --- |
| `GET /health`、`/runtime` | 原生健康、源码身份、能力。健康成功只证明进程运行，不证明数据准备齐全。 |
| `GET /locations/search`、`/points/metadata` | 原生地名/时区/海拔服务。 |
| `POST /forecasts[/compute]` | 点任务或同步点预测。 |
| `POST /forecasts/national[/compute]` | 区域任务或同步区域预测。 |
| `POST /forecasts/nowcast[/compute]` | 实况任务或同步实况预测。 |
| `GET /forecasts/events` | SSE 终态重放和 keepalive。当前进度为阶段级，不提供 Python 全部细分进度。 |
| `GET /forecasts/jobs[/ID]`、`/forecasts/results/ID` | 内存任务清单、状态、结果；进程重启不恢复这些任务。 |
| `POST /forecasts/ID/cancel` | 协作取消；worker 退出前不释放并发槽位，不发布取消后的成功结果。 |
| `GET /forecasts/national/contract` | 原生区域独立契约。 |
| `GET /forecasts/national/cache`、`/summary` | 严格原生缓存读取。推荐使用返回结果中的 SHA key；按请求字段查找时，字段不同可明确 cache miss，不重算。 |
| `GET /forecasts/national/cross-section` | 原生单点剖面计算；支持明确观测海拔和 `offset_minutes`。 |
| `GET /runtime/cache` | 用 `key`、`regional` 读取原生缓存 envelope。 |
| `GET /reports/ID.html` | 原生点/实况 HTML 报告；ID 为完整 SHA256，拒绝路径穿越与被修改的报告。 |
| `GET /map-frames?token=...&time=...` | 原生实况地图帧；兼容前端帧字段，独立 `native-map-v1` 缓存，读取时校验内容 SHA 和完整合同。 |
| `POST /desktop/METHOD` | check/prepare/forecast/publications/dismiss/network_probe 桌面会话桥。 |
| `GET /desktop/status` | 共享 Web 握手，返回服务实例、protocol 和 Rust 引擎身份。 |
| `POST /desktop/jobs`、`GET /desktop/jobs/ID` | 异步桌面业务任务，复用同一 Runtime、预检/确认及并发限制；请求绑定服务实例，查询返回进度序号和终态。 |
| `POST /desktop/jobs/ID/cancel` | 仅请求协作取消，worker 退出前状态保持 running、占用槽位，不发布迟到成功。 |
| `POST /desktop/jobs/ID/release` | 消费终态后释放注册项；运行中拒绝，已释放时幂等。浏览器不提供 `save_cams_key`。 |
| `GET /data/status` | 本地数据清单；不把文件数量伪装成某请求 ready，也不声称上游周期已探测。 |
| `POST /data/requirements`、`/data/fetch`；`GET /data/fetch/status` | 原生事件计划、GFS/EC/CAMS 下载任务和状态。范围为 1–7 天、dawn/dusk、非跨日界线 bbox；显式 CAMS run pin 尚不支持。 |
| `POST /data/prune` | 仅允许配置的缓存目录，移动可重建缓存到 `.trash`，不 unlink；数据库、锁、符号链接和已有回收区不处理。`freed_mb` 表示移出活动缓存量，`reclaimed_disk_mb` 为 0。 |
| SPA | 默认托管 `apps/shared-web/dist`，可用 `FIRESKY_WEB_ROOT` 覆盖；Makefile 与 Docker 构建 shared-web。Docker COPY 到 `/app/frontend/build` 且无覆盖变量的问题已于 2026-11-14 在 `feat/win-linux-docker-parity` 修复：镜像与 Compose 均设 `FIRESKY_WEB_ROOT=/app/frontend/build`，`test_docker_contract.sh` 加断言防回归，本地 compose 冒烟已验证首页与 JS/CSS 资产。Web UI 后续按 macOS 基准对齐，旧 `frontend/` 能力逐页核对。 |

四个传输 worker；每个 Runtime 同时只允许一个重计算/准备操作，忙时拒绝，
不能把四 worker 写成四个大范围预测的并行吞吐。任务注册上限 32、
已结束任务 TTL 30 分钟。输入正文上限 1 MiB。
当前默认仅监听 loopback；远程监听须显式设置 `FIRESKY_HTTP_ALLOW_REMOTE=1` 并配置
`FIRESKY_HTTP_TOKEN` 或 `FIRESKY_HTTP_TOKEN_FILE`（二者互斥）。
页面、API、报告及地图使用 Basic/Bearer 保护，health 除外；同时检查 Host/Origin。
Docker 可持久化生成的私有令牌，详见部署文档。这是轻量私有部署，不承诺公网服务安全。
不要将 `/health` 的 `python_required: false` 解读为所有原有功能已验收。

## 配置

| 环境变量 | 默认/说明 |
| --- | --- |
| `FIRESKY_WORKSPACE` | 当前目录；也可用 `--workspace`。 |
| `FIRESKY_WEB_ROOT` | 默认 `frontend/build`；绝对路径直接使用，相对路径按 workspace 解析。冻结的共享 Web 页面本地可设为 `apps/desktop-tauri/dist`。 |
| `FIRESKY_GFS_CACHE_DIR` | `.cache/gfs` |
| `FIRESKY_ECMWF_CACHE_DIR` | `.cache/ecmwf` |
| `FIRESKY_CAMS_CACHE_DIR` | `.cache/cams` |
| `FIRESKY_HSD_CACHE_DIR` | `.cache/himawari-hsd`；实况兼容 `FIRESKY_SATELLITE_CACHE_DIR` 别名。 |
| `FIRESKY_CACHE_ROOT` | `.cache`；默认清理根。单独配置的 CAMS 缓存可通过 `cache_root` 显式选择。 |
| `FIRESKY_REPORT_DIR` | `reports/web` |
| `FIRESKY_POINT_CACHE_DIR` | `.cache/point-forecasts` |
| `FIRESKY_REGIONAL_CACHE_DIR` | `.cache/regional-forecasts` |
| `FIRESKY_MAP_CACHE_DIR` | `.cache/map-frames` |
| `FIRESKY_TIMEZONE_MANIFEST` | 可选的已验证地理时区数据清单；不是 Python timezonefinder。 |
| `FIRESKY_ECCODES_LIBRARY` | 可选 ecCodes 动态库路径；同时支持 `ECCODES_DEFINITION_PATH` / `ECCODES_SAMPLES_PATH`。 |
| `FIRESKY_RUST_ENGINE_BIN` | 原生客户端/差分测试的明确引擎路径。 |

旧 Python YAML 并不会自动控制新运行时。CAMS 继续需要 ADS 账号和数据集许可；
Rust 自行访问 ADS，不需要安装 cdsapi。ADS 请求遵循环境代理（`HTTP(S)_PROXY`/
`ALL_PROXY`/`NO_PROXY`，2026-11-12 用户指令，与 GFS/EC 客户端一致）；
ecCodes、上游网络、平台权限依然是外部依赖。

## 原报告问题处理

用户提供的 `report-845e9183/v1/report.md` 属于阶段 54 评估段报告，不能证明整个
应用退役。其四项发现的当前处理情况：

1. `detail=model` 不再克隆/序列化只供 full 诊断的全量 columns 和信号列表；
   相同立体角覆盖计算复用。
2. AOD 的 Python `or` 真值语义统一，显式零值回退有回归测试。
3. 部分完成：调整和评估提供进程内 `compute(Value)`，原生编排不再通过临时
   JSON 文件和自身 CLI 子进程往返，复用一次事件质量输出；前端默认不携带
   巨量内部评估数组。内部仍有 `json!`/`from_value` 构造 quality 参数及
   `Value` 结果转换，尚未改成端到端 typed API，不能宣称零序列化开销。
4. 云柱索引半径显式取 `max(1000, include_map_cells ? radius_km : 0)`，
   加入大半径回归，避免 1200 km 请求发生后端分叉。

原报告的 warm-cache p50 78.75 ms 对 33.77 ms 仅是检索后评估/约束/事件质量段，
约 2.33 倍；不包含下载、标定、完整输入规划、HTTP 或打包，不能写作整站提速。
本次最终复测记录见 `RUST_PERFORMANCE.md`。

最终二进制 7 轮同 fixture 复测：Python 检索后评估段 p50 **73.10 ms**，
Rust **33.31 ms**，约 **2.19 倍**，耗时降低 **54.4%**；计时前验证评分步骤
结构相等。这是固定 AOD 和历史 GFS 先验选择的双帧组件测试，不是普通在线请求，
不包括检索本身及下载到报告的全链路，也不代表真实 CAMS 验收。

## 明确未完成

逐项涉及模块、建议设计、完成标准与执行顺序见
[Rust 迁移剩余工作与设计清单](RUST_REMAINING_WORK.zh-CN.md)。

以下不是“Rust 无法移植”，而是当前代码/验收尚未覆盖，不能宣称完成：

- 模式单点完整云场地图未接入；用户在后续 Q04 确认不再需要，已从迁移实现范围
  排除，后续仅清理对应废弃入口/协议。点报告不携带地图格点。
  实况 token/time 地图、区域地图和点报告内联剖面保留，不受该决定影响。
  地图已随报告/存活快照引用及保留策略回收；真实长期磁盘释放仍需验收，不采用容量配额代替用户确认的周期/天数规则。
- 通用 HTTP 区域独立诊断和仍有消费者的管理刷新 API 尚需核对/接线。
  城市排名已并入区域任务；卫星按需准备，不新增常驻后台预热；发布探测与模式后台
  同步已有实现，不机械复刻旧 Redis/预热接口。未注册路由明确 501，保留/退役依消费者矩阵决定。
- Qt/PySide 旧界面、旧 Python 包 API/脚本继续需要 Python；它们不属于新的原生
  启动入口。尚未删除或承诺兼容所有旧 Python 入口。
- macOS 原生 UI 已默认加载 `libfiresky_engine.dylib`，正式 bundle 不启动 Python；
  FFI 生命周期、动态库卸载、资源路径、Swift/C ABI 和预检冷启动已通过本机 smoke，
  CI 已加入 ARM64/x86_64 矩阵。真实 CAMS 冷/热链路、clean-machine、Developer ID
  签名/公证仍未验收。11 月 13 日已有北京/广州真实 GFS 点冷准备与缓存复用，详见
  `CAMS_DUAL_ROUTE_PROPOSAL.zh-CN.md`；不能替代 EC/区域/实况和当前源码统一验收。
  Windows 已按同一合同接入 `firesky_engine.dll`，Linux 已有
  `libfiresky_engine.so` C++20 适配和 smoke；两端目标机 UI、安装包和真实数据仍未验收，
  不能用冻结 Tauri 页面替代。
- Windows/Linux 原生 UI 的任务历史、结果恢复和手动重试入口尚未全部接入；
  跨平台强杀恢复、缓存配额/定时回收、完整日志/运维配置兼容、所有旧异常码和
  导出字段的逐项差分尚未全部验收。
- Windows/Linux 构建和无 Python clean-machine 验收、本机 Docker 实际镜像构建、
  跨平台资源/签名/升级验收尚未完成。本机没有 dotnet，Docker daemon 不可用；
  配置校验不能替代真实运行。
- 上游 GFS/EC/CAMS/RainViewer 各产品的实时长时间可靠性、完整冷/热预测并发
  P50/P95、CPU 和峰值内存尚未完成测量。

没有已知自有天气算法在语言层面不能迁移。ecCodes 的 C ABI、NOAA/ECMWF/ADS
服务与数据、SwiftUI/WinUI/Svelte 和 OS 权限不是 Python 业务算法，不应为了
“全 Rust”口号重写或伪造。测试 oracle 可以继续使用 Python，但生产引擎不能调用它。

## 本次验证

以下为本次共享 HTTP/Web 实施前的切换验证快照，二进制身份不代表新增连接层后的
当前源码。新增批次的测试记录见 `archive/TAURI_RUST_JOINT_MIGRATION.zh-CN.md` 第 5 节；
不以旧二进制或仅成功编译作为当前通过依据。

- `native` release 测试：150 passed；现存重复 `#[test]` / 未注册测试函数警告仍在。
- `engine` 最终 debug 单元测试：118 passed、2 ignored；真实进程测试 4 passed，
  包含无 Python PATH 的 CLI、desktop 和 HTTP 启动。ignored 的本地数据验收
  需显式运行，不计入上述通过数。
- 前端严格原生区域/实况/地图/报告合同测试 17 passed，分别以实际 GFS 和 EC
  区域响应复验；`npm run check` 无错误/警告，随后 `npm run build` 通过。
- 最终 release 真实运行时测试 5 passed：点双模式与缓存、区域双模式、桌面点确认/
  单次消费，以及桌面实况缺 CAMS 拒绝与成功生命周期。成功实况使用手动 aerosol
  和独立合成 CAMS GRIB 预检，不能记作真实 CAMS 全链路验收。
- 原生逐观测评分差分测试 46 passed；最终 release SHA256：
  `df88af4cc525210d12d48f4f09c98ae5568f1b47a606e437c75fcbfe2ad28aa7`；
  `source_id`：
  `6b62145a51032e44b7ba07f02a3b1a358aafca04a551903f99d4071074e5fbe6`。
- 真实 HSD 对照 SatPy：三通道 NaN mask 相同；最大亮温差
  `3.0517578e-5 K`，纬度差 `3.8147e-6°`，经度差 `7.6294e-6°`。
- 空缓存 NOAA 历史扫描实跑：2026-11-08 05:50Z 请求、杭州 100 km、1 帧，
  下载 3 个原始文件共 7,638,414 bytes，原生标定/NPZ 发布/回读成功；
  本机 debug 单次约 14.267 s，不是生产吞吐基准。
- macOS Swift release 编译通过；最终 `dist/native-macos/FireSky.app` 含 ecCodes
  依赖闭包、definitions/samples 和许可证；loader 路径及严格签名检查通过。
  包内 engine 的 `source_id` 与上述最终 release 一致；重定位/签名后 SHA256 为
  `b8220392467d80357eaba60da610c37fd62a2d56ddf9404a3b6d2171a1f75829`，
  不应与构建目录未重定位的二进制 SHA 混淆。
- `docker compose config --quiet` 通过；未运行 Docker 构建。
- 全量 Python/Rust 回归曾得到 1997 passed、10 failed。8 项是仍要求旧 Python
  sidecar 的 Swift fixture，已改为真实 Rust 主进程和禁止 Python 的 trap；
  位置/清单整组复跑 124 passed。另 2 项发现 HTML 可选计数字段校验过宽，
  已修复为只对缺测显示占位，拒绝非法负数/类型，接受 `3.0` 等整数值浮点；
  原 12 个非法输入用例保留，新增 9 组可选计数/时长回归。
  最终 release 联合复验：EC 服务/报告 78、真实运行时 5、评分差分 46，
  合计 129 passed（38.77 秒）。
  不将该全量运行记录成“全量通过”。
- Tauri Rust 测试 14 passed；整套 UI 曾 28/29，地图缩放项在当前源码上独立
  复跑通过。期间地图测试存在其他并发修改，尚未取得最终整套 29/29 的证据。
- 中英文 README/DEPLOY 的正式启动示例均改为 Rust；旧 Python API、PySide/
  PyInstaller 和旧预热/刷新调度显式标记 legacy。部署示例的语法检查不代表
  Linux/Windows 平台运行验收。
