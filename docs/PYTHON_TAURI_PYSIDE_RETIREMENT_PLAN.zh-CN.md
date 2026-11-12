# Python、Tauri 与 PySide 退役方案

更新日期：2026-11-13。本文区分生产 Python 运行时、PySide6 旧桌面、Tauri 桌面壳和
Docker 正在使用的 Svelte Web 页面。它们是不同的退役对象，不能因为名称或目录相同而一起删除。

## 结论

建议分三条线推进：

1. **PySide6 桌面产品退役**：新桌面由 macOS SwiftUI/Rust FFI 承接，Web 由 Rust HTTP 托管；
   PySide 保留到功能、用户数据迁移和安装包替代均验收后，再停止分发和删除旧应用入口。
2. **Tauri 壳退役**：正式桌面产品路线已经是平台原生 UI + Rust FFI，Tauri 的 Rust 窗口壳
   不再需要；但 `apps/desktop-tauri` 中的 Svelte 页面正被 Docker 作为共享 Web 构建，
   不能直接删掉整个目录。先把页面提取为共享 Web 项目，再移除 `src-tauri`、Tauri 依赖和壳专用 CI。
3. **生产 Python 运行时退役**：默认 `make serve`、Docker 和 macOS FFI 已走 Rust；显式的
   `make serve-python`、`legacy-install`、旧 FastAPI/PySide 路径及一部分 Python 业务模块仍在。
   先逐项证明 Rust 已取代受支持能力，再停旧服务；测试、差分 oracle、基准和离线分析脚本可以继续使用 Python。

因此最终目标应是：受支持的安装包、HTTP/Web 服务和桌面产品运行时均不启动或依赖 Python；
Tauri 不再是桌面产品；共享 Web 仍可作为静态 Svelte SPA 交由 Rust HTTP 服务托管。**目标不是
删除仓库中的所有 `.py`、`.svelte` 或所有 Tauri 历史记录。**

## 当前边界

| 部分 | 当前用途 | 退役判断 |
| --- | --- | --- |
| `engine/` + `native/` | Rust HTTP、CLI、桌面 FFI、数值核心 | 正式生产主线，保留 |
| `apps/macos/` | SwiftUI/AppKit + `libfiresky_engine.dylib` | macOS 正式桌面主线，继续补真实数据与发布验收 |
| `apps/windows/`、`apps/linux/` | Rust FFI 原生客户端 | Windows/Linux 桌面开发依用户当前暂停决定，计划保留，不在本轮启动 |
| `apps/desktop-tauri/src-tauri/` | Tauri 窗口、菜单、单实例、Rust engine sidecar IPC | 不再作为正式桌面；替代并抽离共享 Web 后退役 |
| `apps/desktop-tauri/src/` | Svelte 工作台；同时支持 Tauri 和 browser transport | Docker 当前从该 package 构建 SPA；迁至明确的 shared-web package 后继续保留 |
| `frontend/` | 另一套 SvelteKit 前端；`make frontend` 构建到 `frontend/build` | 与 Docker 实际使用的 Svelte 前端并存；先盘点路由和消费者，再决定合并或退役 |
| `desktop/` | PySide6 主窗口、点/区域/实况/数据页、worker、地图和 PyInstaller spec | 旧兼容桌面；替代验收后停止发布并退役 |
| `src/firesky/api.py`、`web_server.py`、`service.py`、`desktop_rpc.py` | 旧 Python API/服务、评分装配及 PySide worker 使用 | 不进入默认 Rust HTTP 路径的能力分批退役；当前被 legacy/test 使用的代码先保留 |
| `pyproject.toml`、`make legacy-install`、`make serve-python` | Python 对照环境和显式旧服务入口 | 最终改为明确的 test/reference/legacy extras；旧产品路径关闭后移除或单独归档 |
| `tests/`、`scripts/bench_*.py`、对照/数据工具 | 回归、基准、迁移核对及上游诊断 | 保留必要工具；任何工具都不得成为受支持产品的运行时回退 |

两个状态需要以代码为准：`apps/desktop-tauri/src-tauri/src/sidecar.rs` 当前选择并启动
`firesky-engine desktop`，不是 Python sidecar；`main.rs` 中提到 `desktop_rpc.py` 的注释是历史措辞。
反过来，PySide `desktop/workers.py` 仍直接调用 `firesky.web_server` 和 Python 数据/预测模块。
另有历史迁移审计文档称 SwiftUI/WinUI 会启动 Python，与当前 FFI 源码及运行时切换文档不一致；
退役基线必须重新按启动代码核实并把陈旧说明标记为历史，避免据此误删或重复迁移。

## 先解决共享 Web 与 Tauri 的边界

当前部署有两份 Svelte 工程：

- `frontend/` 由根目录 Makefile 的 `frontend`/`build` 目标构建到 `frontend/build`，Rust HTTP
  默认静态根也是 `frontend/build`。
- Dockerfile 则在 `apps/desktop-tauri` 执行 `npm ci && npm run build`，并把
  `apps/desktop-tauri/dist` 放进容器的 `frontend/build`。因此生产 Docker Web 页面不是 Tauri 二进制，
  而是由 Tauri package 名下构建的共享浏览器页面。
- `apps/desktop-tauri/src/lib/ipc.ts` 已有 browser transport；但也包含 Tauri invoke/event、
  打开本地报告/缓存和 quit 等壳专用命令。`App.svelte` 使用 Tauri window API 处理关闭行为。

推荐先将正在由 Docker 使用的 Svelte 应用搬到明确的 `apps/shared-web/`（或经确认的等价目录），
保留 browser HTTP transport 和页面能力，移除壳专用 invoke/window/event 分支；本机报告打开、
目录打开、应用退出这类能力在浏览器中显示为不支持或转换成 HTTP 可用链接。之后统一：

- Rust HTTP 默认静态目录、Makefile 前端构建、Dockerfile、CI 和部署文档都指向同一共享 Web 构建产物。
- `frontend/` 与原有 `apps/desktop-tauri/src/` 之间先做路由/交互/样式/测试消费者矩阵；合并完且同源
  页面 smoke 通过后，才移除重复工程。不要在本计划里预先假定哪一份页面功能更全。
- 移除 `apps/desktop-tauri/src-tauri/`、Tauri CLI、`@tauri-apps/api` 和 Tauri bundle CI；
  共享 Web 的 `npm run check`、`npm run build`、browser RPC 合同和 Docker HTTP smoke 继续保留。
- Tauri 专属本地命令的功能须由 macOS SwiftUI、未来 Windows/Linux 原生 UI，或共享 Web 明确的
  HTTP 等价能力承接；不得静默让浏览器端尝试读取服务器本地路径。

这样“退役 Tauri”指退役 Tauri 桌面宿主和壳专属代码，不等于退役 Docker/Web 页面。

## PySide6 退役门槛

当前 PySide 运行入口为 `python desktop/app.py`，PyInstaller 包定义在 `desktop/FireSky.spec`。
README 已把它标为 legacy；spec 仍打包 PySide6、QtWebEngine 和本地地图资源，PySide worker
调用 Python `firesky.web_server`、`firesky.desktop_data`、`firesky.location` 等模块。
所以“默认安装包无 Python”并不代表 PySide 兼容版已退役。

停止向用户分发 PySide 版之前，macOS 正式原生版及共享 Web 至少应覆盖：

1. 点预测：地名/坐标/海拔/时区、朝霞晚霞、日期、GFS/EC 双结果、预检/准备/确认、进度/取消、质量/逐时/剖面和报告。
2. 区域预测：预设/自定义区域、地图绘制、GFS/EC 质量地图、缺测、排行、城市筛选、网格详情和剖面查询。
3. 实况：卫星输入/时次、GFS 先验、CAMS、雷达约束、剖面、状态/失败信息和结果恢复；地图功能按产品合同另列。
4. 数据与设置：本地库存、下载探测、ADS/地图凭据、安全存储/脱敏、保留策略、任务历史、手动重试。
5. 用户数据：Swift `Store.swift` 已尝试从 `~/.firesky-desktop/locations.json` 导入收藏点，
   并从 `config.json.current_spot` 恢复当前点；退役前应以真实旧配置 fixture 验证并盘点地图设置、
   主题等其余用户偏好是否需迁移。不要重复实现已经有的导入，也不要自动移动/删除 `.cache`、
   ADS 凭据、报告或用户导出文件。新旧应用并存时配置导入必须幂等。
6. 安装体验：macOS 无开发工具 clean-machine 启动，权限和 Keychain/配置行为、更新/卸载后的用户数据保留；
   文档和下载渠道明确标记旧 PySide 安装包停止维护。

验收前继续保留 PySide 代码和独立构建说明，但不把它作为正式版 CI/release 门禁以外的默认入口；
验收后删除或归档 PyInstaller spec、旧 app 入口及仅由其依赖的 UI 模块。若仍需要开源社区的 Python
兼容版，应将其作为单独 legacy 发行物和依赖组，写明不受新版本支持，不能混入正式原生安装包。

## Python 生产代码与依赖退役

### 运行时分层

- **必须离开产品进程**：Python 子进程、`desktop_rpc.py` sidecar、PySide app、FastAPI/uvicorn
  旧 API、Python fallback、生产镜像/安装包中的 Python runtime。最终验证时从 PATH 移除 Python，
  冷启动服务/桌面并完成受支持的本地/真实数据流程。
- **可以暂留为开发/验收**：pytest 测试、Python/Rust 差分、离线 replay、基准和上游探测脚本。
  这些进入 `test`/`dev`/`analysis` extras 或独立工具环境；生产安装不得 import、spawn 或依赖它们。
- **需单独判定**：`scripts/build_app_resources.py` 参与开发者打包的 resource manifest 生成/检查。
  若要求生产用户运行不需要 Python，它可作为构建期依赖明确文档化；若要求发布工程完全 Python-free，
  再将这段简单 manifest/hash 工作迁到 Rust/Node 并做字节一致性测试。

### 仍需逐项核对的业务代码

不能只删 `api.py` 或把依赖从 `pyproject.toml` 移掉。按当前源代码与迁移清单，需为以下 legacy
Python 模块建立“仍有生产消费者 / 仅测试或旧 API / 可退役”的逐模块矩阵：

- 服务和请求编排：`src/firesky/api.py`、`web_server.py`、`service.py`、`desktop_rpc.py`、`desktop_data.py`。
- 数据接入和产品算法：`gfs.py`、`ecmwf*.py`、`aerosol.py`、`satellite.py`、
  `satellite_cloud_column.py`、`radar.py`、`scoring.py`、`national_batch.py`、
  `forward_field_scoring.py`、`regional_diagnostics.py`、`html_report.py`。
- 兼容入口和桥：`__main__.py`、`engine.py`、`native_core.py`、`rust_desktop_data.py` 及旧 CLI/API DTO。

Rust 默认 HTTP/CLI、macOS FFI 与 Docker 路径已存在；但需要用实际运行证据逐项确认请求路由、错误码、
缓存、源 provenance、事件选择、区域结果、HSD 标定和 nowcast 字段都没有回到 Python。尤其保留
`horizontal_cloud_probability` 与 `cloud_probability_given_cloud` 等实况字段的语义；native ledger 或
helper 测试通过不等于全部 Python 服务编排已被替代。

### Python 依赖切分

目前 `pyproject.toml` 主 dependencies 仍声明 numpy/requests/astral/eccodes/FastAPI/uvicorn/PyYAML/
Pillow/httpx/h5py/Redis；同时有 `dev`、`cams`、`nowcast`、`ec` extras。`make legacy-install`
安装 Python 参考栈，`make serve-python` 显式启动 `firesky.api`，`make test` 用 Python pytest 做
差分/回归。建议：

- 把仍需的旧服务依赖迁到一个明确的 `[legacy]` extra；pytest 和差分依赖留在 `[test]`/`[dev]`。
- 分开提供 `make test-rust` 与 `make test-differential`；后者明确要求 Python，不是安装/启动生产服务的前置条件。
- 将 `make serve-python` 改名/文档化为 `serve-legacy-python`，记录移除版本；退役周期后删除 target。
- 清点 `cdsapi`、SatPy、pyresample、ecmwf-opendata 等 optional groups 是否仍有用户支持的 Python
  工具消费；Rust 产品路径无需因此安装这些包。
- 最终生产 wheel/container/app 的依赖 SBOM 中不含 Python；测试环境和本地转换脚本另行安装。

## 分阶段执行与门禁

### Phase 0：冻结基线与准确盘点

- 以同一 commit/source_id 记录 `make serve`、Rust HTTP、macOS FFI、Docker HTTP 的实际启动进程；在无 Python
  PATH 下复验。确认不存在隐式子进程或 Python fallback。
- 生成 Python 模块、入口、依赖、命令、CI job、发布物和测试消费者矩阵；每项标记 owner、替代品、风险、门禁。
- 对齐 `RUST_MIGRATION_STATUS.zh-CN.md`、`RUST_RUNTIME_CUTOVER.zh-CN.md`、README 和 CI 的时效口径；
  将“macOS/Windows 仍运行 Python desktop_rpc”等已过期记录标注为历史，不删历史测试证据。
- 记录 PySide 旧用户数据路径、桌面用户流程和 Tauri/browser 两种运行能力。

**门禁**：未确认目录/接口消费者前，不删文件、不删依赖、不停止构建。

### Phase 1：先从 Tauri package 拆共享 Web

- 将 Docker 当前构建的 Svelte 页面及 browser transport 移到共享 Web 工程；保留 HTTP browser 路径。
- 分离 Tauri 专用文件系统/窗口/菜单操作；网页不提供打开宿主/服务器本地目录和退出服务等能力。
- 统一 Makefile、Rust HTTP `FIRESKY_WEB_ROOT` 默认、Dockerfile、CI、部署文档到一个 Web build。
- `frontend/` 与 `apps/desktop-tauri/src` 做逐页对照，确认最终只维护一套产品 Web UI；已发布 URL/静态资源兼容后再清理重复工程。

**门禁**：桌面 Tauri 仍可构建作为过渡；Docker 与本地 Rust HTTP 使用同一共享 Web SHA 完成点/区域/实况、任务、登录和报告 smoke。

### Phase 2：退役 Tauri 壳，不退役 Svelte Web

- 新功能冻结，保留一个明确弃用周期，确认 macOS SwiftUI 为桌面主线，Web 由 Rust HTTP 提供。
- 在共享 Web 可独立 build/run 后，移除 Tauri Rust crate、`src-tauri` sidecar/window/menu/capabilities、
  Tauri npm/CLI/自动 bundle 和只覆盖 Tauri 壳的 CI。
- 保留浏览器 HTTP contract、SPA check/build、Docker smoke、区域地图与 Web UI regression tests。
- 处理平台菜单/窗口 close/重启/文件路径能力：桌面功能由 SwiftUI 负责，浏览器能力由 Web/HTTP 合同负责，
  不用浏览器模拟系统文件 API。

**门禁**：从发布矩阵移除 Tauri 安装包；确认 Docker 页面未因删壳而丢失，Rust 服务仍可托管 SPA。

### Phase 3：停止 PySide 分发

- 完成前述 macOS 原生与 Web feature parity 以及真实 CAMS/HSD 上游验收。
- 给 PySide 旧入口和发布包标记清楚的维护终止版本；在兼容周期提供迁移说明及用户数据导入。
- 从正式 release workflow 移除 `desktop/FireSky.spec` 构建；PySide 仅在单独 legacy/test workflow 可选构建。
- 新安装包、源码冷启动和升级路径均使用 SwiftUI + Rust FFI；PySide 用户 config/import 路径不覆盖新设置。

**门禁**：无 PySide 的 macOS bundle 做 clean-machine 点/区域/nowcast/数据准备与结果恢复走查。

### Phase 4：切断旧 Python 产品服务

- 每迁移一个产品路径就做旧 Python 与 Rust 的同输入差分、错误/缺测差分、缓存和 provenance 检查。
- Rust 替代真实服务路径并至少跨一个正式版本稳定后，依消费者逐项退役 Python route/API/CLI/module。
- `serve-python`、旧 `python -m firesky api`/旧命令不再对外作为默认/推荐方式；如保留兼容，必须是显式 legacy extra，
  不允许作为 Rust 失败回退。
- 生产路径的真实 GFS/EC/CAMS/HSD 下载、点/区域/nowcast、报告、任务/恢复能力全部由 Rust 拥有。

**门禁**：无 Python 环境下通过从全新工作区启动的 Rust HTTP/CLI/Docker/macOS native 测试；失败路径只报原生错误。

### Phase 5：依赖和源文件收尾

- 从主 `[project.dependencies]` 移除仅旧产品路径需要的 FastAPI/uvicorn/PyYAML 等，改放 `[legacy]` 或测试 extras；
  不在没有消费者调查前修改数组科学测试的安装方式。
- 先删除已无产品消费者且测试 oracle 替代充分的 PySide/app adapter 和旧 service entrypoint；保留最小差分工具。
- 清理过期 Tauri 计划及文档链接，保留一份架构决策和最终退役记录；`apps/desktop-tauri` 若只剩 shared Web，
  改成与实际用途一致的 package 名，而非整体删除。
- 检查正式 Docker image、macOS `.app`、正式桌面安装包和 SBOM 不含 Python runtime 或 PySide/Qt framework；
  如仍发布 Python 参考/测试包，须与产品安装包区分，且不能成为生产运行依赖。

**门禁**：全量 Rust tests、必要 Python differential suite、Web check/build/HTTP contracts、发布产物扫描和
真实数据工作流在同一冻结 commit 完成；旧应用已不能被文档误认为支持版本。

## 顺序与禁止项

1. 先完成 Phase 0 基线。当前仓库有大范围既有未提交/未跟踪改动；新接手者先核对 `git status`，不得 `reset/clean`。
2. Phase 1 和 Phase 2 可先行，因为它们的核心是拆分 Tauri 壳与共享 Web，并不依赖删除 Python 服务。
3. Phase 3 要等正式 macOS FFI 与 Web 能力完整替代 PySide；Windows/Linux 开发按用户当前暂停范围不自动启动。
4. Phase 4/5 依赖真实源服务、算法字段合同和全量数据链路验收；不能用旧缓存单次成功宣称 Python 业务模块可删。
5. 不要同时删除 `frontend/`、`apps/desktop-tauri/`、`desktop/` 或 `src/firesky/` 整目录；每个目录有不同产品/测试/共享 Web 消费者。
6. 不删除用户 `dist/` 安装包、缓存、报告、`.firesky-desktop` 配置或 ADS 凭据作为“代码退役”步骤。
7. 不把测试/数值 oracle 中的 Python 等同为生产 Python 依赖；也不因“仓库有 Python”就否认 Rust 默认生产路径已可运行。

## 最终完成定义

- **Tauri 桌面壳已退役**：无 Tauri desktop bundle、sidecar host 或其 CI release；共享 Web 独立构建并继续由 Rust HTTP/Docker 提供。
- **PySide 桌面已退役**：无正式 PyInstaller/PySide 版；macOS 原生桌面覆盖受支持工作流，升级和旧用户数据迁移已验收。
- **Python 运行时已退役**：受支持生产入口/镜像/安装包不含 Python runtime，不 spawn Python，无 Python 环境可工作。
- **Python 源码清理已完成（可选更晚目标）**：只移除已没有受支持产品/测试消费者的模块；必要差分、基准、数据调查工具可留在独立工具环境。

上述四条应分别报告状态；不能用“默认服务是 Rust”代替 PySide/Tauri 退役，也不能用“仓库仍有 `.py`”否认生产运行时已无 Python。
