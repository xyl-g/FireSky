# 退役方案 Phase 0 基线盘点（执行记录）

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

- `docs/RUST_MIGRATION_STATUS.zh-CN.md:37,53`：「SwiftUI/WinUI 启动 Python desktop_rpc」
  与现源码不符 → 历史标记。
- `docs/TAURI_DESKTOP_PLAN.md`：整体为历史蓝图（Tauri 已决定退役）→ 顶部历史标记。
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
