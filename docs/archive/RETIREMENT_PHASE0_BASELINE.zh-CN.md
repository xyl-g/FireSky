# 退役方案 Phase 0 基线盘点（执行记录）

> **2026-11-13 复核说明**：本文件保留各批次原始记录，不是当前工作区统一通过证明。
> 当前 HEAD 为 `75371d1`，另有共享 Web、展示与退役未提交改动；当前接续见
> [最新计划](NEXT_AGENT_PLAN_2026-11-13.zh-CN.md)。下方 `d138f86`、路径/行号和环境
> 描述属于当时盘点；例如 `apps/` 当前有已跟踪文件，macOS 默认是进程内 dylib FFI，
> `firesky-engine desktop` 只属显式 JSONL 诊断路径，不能按下表推断默认启动子进程。
> 本次还发现 Docker COPY 到 `/app/frontend/build`，而 Rust 默认根解析为
> `/app/apps/shared-web/dist` 且无覆盖变量；§8–9 静态契约通过未覆盖这一问题。
> 该项归入后续 Web 部署，届时统一配置并在真实镜像中请求首页与资源。
> 用户最新决定先完善 macOS，并以其作为其他 UI 的对齐基准；见
> [macOS 基准](../MACOS_UI_BASELINE.zh-CN.md)。本记录不将 Docker 作为当前主线前置任务。
> 本次仅更新文档，未修代码或跑容器。

基线 commit：`d138f86`（工作树含 144 项既有未提交/未跟踪改动，全部保留，未做 reset/clean）。
盘点日期：2026-11-13。本文是 `PYTHON_TAURI_PYSIDE_RETIREMENT_PLAN.zh-CN.md` 的 Phase 0 交付物。

## 1. 实际启动进程（以源码为准）

| 入口 | 实际启动 | 证据 |
| --- | --- | --- |
| Tauri 壳 sidecar | `firesky-engine desktop`（Rust 二进制，非 Python） | `apps/desktop-tauri/src-tauri/src/sidecar.rs:710-712`，注释明确仅允许该固定目标 |
| macOS SwiftUI | `firesky-engine desktop`（Rust FFI 客户端直启） | `apps/macos/Sources/FireSkyNative/EngineClient.swift:128-129` |
| `make serve` / Docker CMD | `firesky-engine http`（Rust HTTP） | `Makefile:82-83`、`docker/Dockerfile:60` |
| `make serve-python` | `uvicorn firesky.api:create_app`（显式旧 Python 对照） | `Makefile:85-87` |
| PySide 桌面 | Python 进程（`desktop/workers.py` → `firesky.web_server`） | `desktop/workers.py:25` |

`src/firesky/desktop_rpc.py` 无任何 Rust/Swift 代码消费者：Rust/TS 侧仅注释引用其协议行号
（`ipc.ts:15,58,65`、`src-tauri/src/main.rs:23`）；子进程调用仅存在于 tests/bench。
`main.rs`/`ipc.ts` 中关于 `desktop_rpc.py` 的文字均为历史措辞。

## 2. legacy Python 模块消费者矩阵（结论）

- **有生产消费者（随 PySide 兼容版存亡，先保留）**：`web_server.py`（PySide workers 核心枢纽）、
  `service.py`、`desktop_data.py`（经 `desktop/data_access.py` 整模块别名）、`gfs.py`、`ecmwf*.py`、
  `aerosol.py`、`satellite*.py`、`radar.py`、`scoring.py`、`national_batch.py`、
  `forward_field_scoring.py`、`regional_diagnostics.py`、`html_report.py`、`native_core.py`、
  `rust_desktop_data.py`。
- **仅旧 API / 测试消费者（可退役候选）**：`api.py`（仅 `make serve-python` +
  `tests/test_backend_api.py`）、`desktop_rpc.py`（仅 tests/bench）、`__main__.py`（无消费者）、
  `engine.py`（仅 `tests/test_engine_api.py` + `__init__.py` re-export）。
- `uvicorn` 消费者仅 `api.py` 与 `logging_config.py`（软 import）；`httpx` 无 src 消费者；
  FastAPI 仍是 `web_server.py` 的依赖（api.py 只是薄壳）。
- `scripts/build_app_resources.py` 是**构建期工具**：`build_macos_native.sh:85`、
  `test_macos_native.sh:26`、CI `--check`（native-desktop.yml:43）。引擎侧校验是纯 Rust
  （`engine/src/app_resources.rs`）。生产用户不需要 Python 运行它。

## 3. 两套 Svelte 前端（盘点完成，合并决策另列）

- `frontend/`：SvelteKit 2（adapter-static，`frontend/build`），路由 `/forecast` `/now` `/outlook`
  `/admin`；独有 admin 管理台、鉴权、SSE 队列、全国剖面组件；无 Tauri 依赖。`make frontend`/
  引擎 `web_root` 默认/CI frontend job 指向它。
- `apps/desktop-tauri/src/`：Vite + Svelte 5 纯 SPA（构建到 `dist/`），Docker 生产页面来源；
  双态 transport（浏览器 HTTP `BrowserTransport` / Tauri invoke）；独有工作台
  （点/区域/实况/数据/设置）、任务历史、桌面 RPC 全链、地图选点/拖绘。
- 两套功能互有独占、组件零复用；完整合并是产品级决策，按方案不在本轮预判，本轮只完成
  「共享 Web 与 Tauri 壳解耦」（Phase 1/2）。
- `apps/desktop-tauri/scripts/` 下 node 脚本有**壳外消费者**：
  `scripts/bundle_macos_eccodes.sh:6`（macOS 原生打包）、native-desktop.yml（vcpkg eccodes、
  资源清单校验、Docker 冒烟、单测）、Dockerfile（npm ci 构建页面）。拆分时必须随共享 Web 一起迁移。

## 4. 陈旧文档标注（本轮已打历史标记）

- `docs/archive/RUST_MIGRATION_STATUS.zh-CN.md:37,53`：「SwiftUI/WinUI 启动 Python desktop_rpc」
  与现源码不符 → 历史标记。
- `docs/archive/TAURI_DESKTOP_PLAN.md`：整体为历史蓝图（Tauri 已决定退役）→ 顶部历史标记。
- `docs/FORECAST_API.md:838,886`：`python -u -m firesky.desktop_rpc` 启动配方为旧桌面 IPC → 历史标记。

## 5. 用户数据路径（PySide 迁移盘点）

`desktop/widgets.py:79-81`：`~/.firesky-desktop/`（`locations.json` 收藏点、`config.json`
含 `tianditu_key`/`current_spot`）；`desktop/app.py:606-608` 缓存 fallback。Swift `Store.swift`
已实现导入。退役前需真实 fixture 验证（本轮未改动用户数据）。

## 6. 本轮门禁执行环境说明

- 本机 Docker daemon 不可用（`docker version` 连接失败）→ Docker 门禁以
  `scripts/test_docker_contract.sh`（静态 + Node 清单校验，CI 保留真实双架构冒烟）+ 本地
  `vite build` + `firesky-engine http` 托管构建产物冒烟替代。
- `apps/` 整目录当前未被 git 跟踪；新增 ignore 规则覆盖 node_modules/dist/test-results。

## 7. 门禁确认

未确认消费者前未删任何文件、未删依赖、未停止构建（本盘点完成即为 Phase 1 的前置门禁）。

## 8. 执行结果（2026-11-13，Phase 1–5）

- **Phase 1（共享 Web 拆分）**：`apps/shared-web/` 新建（Svelte 源码 + Playwright 测试 +
  壳外脚本 eccodes/smoke/native-engine/native-target）；`src/lib/ipc.ts` 重写为
  browser-only（删 Tauri invoke/event、openCache/quitDesktop 显式不支持、报告仅 HTTP
  契约路径）；Makefile `frontend`/`check`/`serve-frontend`、Dockerfile stage 2、
  `.dockerignore`、engine `web_root` 默认（`apps/shared-web/dist`）统一到同一构建。
  门禁：svelte-check 0 错、`npm run build` 成功、**Playwright 51/51 全绿**（12 个 spec
  的 Tauri invoke mock 全部转写为 HTTP 任务通道 mock：`tests/helpers/in-page-rpc.mjs`）、
  engine `http.rs` 默认根冒烟（health ok + index/asset 200，`python_required:false`）、
  `scripts/test_docker_contract.sh` ok。
- **Phase 2（Tauri 壳退役）**：`native-desktop.yml` 删除 Tauri `desktop` job（4 目标
  安装包矩阵）与陈旧注释；`apps/desktop-tauri/` 整目录删除（3.2GB，含 src-tauri 壳、
  旧测试产物）；`apps/README.md`、`README(.en).md`、`docs/DEPLOY(.en).md` 更新为退役
  状态；`apps/shared-web/README.md` 新建。门禁复跑全绿（engine 255/0、playwright 51/51、
  docker 契约 ok）。
- **Phase 3（PySide 维护终止）**：README 中英双语标记 EOL（不再修复、不再随正式发布
  打包，用户数据 `~/.firesky-desktop` 由原生版自动导入）；确认无任何 workflow 构建
  `desktop/FireSky.spec`。
- **Phase 4（旧服务入口更名）**：`make serve-python` → `make serve-legacy-python`；
  `legacy-install` 增装 `[legacy]` extra；`docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md` 口径更新。
- **Phase 5（依赖与源文件收尾）**：`pyproject.toml` 主 dependencies 移除
  uvicorn/httpx/redis（→ 新 `[legacy]` extra），description 标注 legacy 参考栈定位；
  `redis_cache.py` 改软 import（缺失时静默降级，原生路径不装 redis）；删除无消费者的
  `src/firesky/__main__.py`、`src/firesky/engine.py`（及 `tests/test_engine_api.py`），
  `__init__.py` 去除 re-export。差分/基准工具全部保留。
- **明确未做（后续目标，非本轮）**：`frontend/`（SvelteKit）与 `apps/shared-web` 的合并
  （互有独占功能，属产品级决策，方案要求先做逐页对照）；`desktop/` PySide 源码与
  `desktop/FireSky.spec` 的物理删除（等替代验收后按方案归档）；`src/firesky/web_server.py`
  等 legacy 业务模块（仍有 PySide/test 消费者，按矩阵保留）。

## 9. 最终差分门禁结果（2026-11-13）

- `pytest tests/ -q`（Rust 重建后严格差分）：**2032 通过 / 1 跳过 / 2 失败**。
  失败项 `test_rust_runtime_forecast.py::test_native_cli_regional_has_real_points_frames_and_separate_registry[gfs|ec]`
  为引擎 CLI `forecast --regional` 测试，子进程 120 秒超时。2026-11-13 源码复核：
  `runtime_inputs` 在临时目录生成 GFS/EC/CAMS GRIB fixture，属于离线运行时回归，
  **不是真实上游验收**。此前按改动文件范围判断“与退役改动正交”并不能证明根因；
  目前应记录为待重现/定位，不能直接归因于上游、直接放宽超时或计入全绿门禁。
- Rust：`cargo test --manifest-path engine/Cargo.toml --locked` 255 通过 0 失败。
- Web：playwright 51/51、svelte-check 0 错、`npm run build` 成功。
- Docker 契约：`scripts/test_docker_contract.sh` ok（daemon 不可用，静态 + Node 清单校验）。
- 引擎默认根冒烟：`firesky-engine http`（无 FIRESKY_WEB_ROOT）托管
  `apps/shared-web/dist`（health `python_required:false`、index/asset 200）。

## 10. 文档同步（2026-11-13 补充）

- 退役方案主文档：边界表与 Phase 0–5 标题标注完成状态；「顺序与禁止项」注明
  `apps/desktop-tauri/` 为唯一整体删除例外；「最终完成定义」四条分别标记 ✅/◐。
- 活跃文档统一到 shared-web：README(.en)（EOL 标记、构建/排障命令）、
  DEPLOY(.en)（构建路径）、CONFIGURATION(.en)（前端配置段）、ci.yml（frontend job
  改为 shared-web check+build+test；旧 `frontend/` 契约测试拆为独立轻量 job 保留，
  标注 pending merge）。
