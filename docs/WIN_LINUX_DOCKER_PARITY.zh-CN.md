# Windows/Linux/Docker 对齐分支记录（feat/win-linux-docker-parity）

更新日期：2026-11-14。依据用户决定：Windows/Linux/Docker 支持，桌面与 Web 页面
均参考已验收的 macOS 那一套。本文记录该分支的范围、改动、验证证据与剩余
目标机验收；对齐口径见[跨平台基线](NATIVE_UI_CROSS_PLATFORM_BASELINE.zh-CN.md)，
产品基准见[macOS 产品与 UI 基准](MACOS_UI_BASELINE.zh-CN.md)。

## 1. 范围与结论

- 本分支把三端"源码级对齐"补完：Windows 数据页任务历史/结果恢复/参数重查、
  Linux 统一 modal 预检/任务历史/结果恢复、Docker SPA 静态根修复。
- 完成了在本机（macOS host + colima linux/aarch64 容器）可完成的全部验证；
  Windows 真机、Linux 桌面会话、双架构镜像发布的验收仍按第 4 节分列，
  不在本分支宣称完成。
- shared-web 无代码改动：展示对齐已于 2026-11-13 批次完成
  （[字段矩阵](WEB_MACOS_FIELD_MATRIX.zh-CN.md)），本分支补齐页面级走查证据。

## 2. 改动清单

| 路径 | 改动 |
| --- | --- |
| `apps/windows/FireSky/MainWindow.xaml(.cs)` | 数据页新增任务历史区：列表/事件/删除确认/恢复结果/恢复参数重查，合同与 macOS `TaskHistory.swift`+`Store.swift` 逐条一致 |
| `apps/linux/src/gtk_app.cpp` | 三个独立按钮合并为统一「预测」入口的 modal 预检 sheet（check→报告→prepare→forecast(confirmed)，周期龄确认 `cycle_age_confirmed`，取消丢令牌）；新增任务历史视图与结果恢复/参数重查 |
| `apps/linux/src/ffi_smoke.cpp` | 新增 `task_forget` 未知 id 软失败（`forgotten:false`）与 `result_restore` 缺记录硬失败断言 |
| `docker/Dockerfile`、`docker-compose.yaml` | 运行时设 `FIRESKY_WEB_ROOT=/app/frontend/build`（修复已登记的 SPA 404 缺陷，见第 3.1 节） |
| `scripts/test_docker_contract.sh` | 加 `FIRESKY_WEB_ROOT` 断言防回归 |
| `scripts/test_windows_ffi_contract.sh` | 加窗口层 `task_forget`/`result_restore` 存在性断言 |
| `apps/linux/README.md`、`apps/README.md`、`docs/DEPLOY.md`、`docs/MACOS_UI_BASELINE.zh-CN.md`、`docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md`、`docs/archive/NEXT_AGENT_PLAN_2026-11-13.zh-CN.md` | 状态行更新为"分支已接续，目标机验收分列" |

## 3. 验证证据（2026-11-14，本机）

### 3.1 Docker（colima linux/aarch64）

- `docker buildx build --load`：三阶段镜像构建成功（rust 1.91 引擎 release、
  node 22 shared-web 构建、debian slim 运行时）。
- `bash scripts/test_docker_contract.sh`：ok。
- `FIRESKY_HTTP_PORT=18765 FIRESKY_IMAGE_TAG=local docker compose up -d`：
  容器 healthy；`GET /api/v1/firesky/health` 返回
  `{"engine":"firesky-rust","python_required":false,"status":"ok"}`。
- 无令牌 `GET /api/v1/firesky/desktop/status` → 401；带 entrypoint 生成令牌 → 200。
- SPA：`/` 返回 `<title>FireSky</title>`，JS/CSS 资产 200
  （`smoke-http-runtime.mjs` 的 spa 断言通过）。
- 桌面任务生命周期：`hello` job 注册/轮询/release 合同通过，无 Python。
- 持久化：容器重建后 entrypoint 令牌逐字节一致；任务历史 receipt 经
  `--history-seed` → `--history-verify` 校验未变、未自动重启。
- **缺陷修复记录**：修复前 `/` 404——Dockerfile COPY 到 `/app/frontend/build`
  而引擎默认读 workspace 下 `apps/shared-web/dist` 且镜像未设
  `FIRESKY_WEB_ROOT`。该缺陷登记于 RUST_RUNTIME_CUTOVER「SPA」行与
  NEXT_AGENT_PLAN_2026-11-13 第 3 节；本次修复并验证。

### 3.2 shared-web

- `npm test`：149 passed。
- `svelte-check`：0 errors, 0 warnings。
- `vite build`：成功。
- Playwright UI（chromium，in-page RPC mock）：51 passed，含浅/深主题、
  窄/宽窗口、区域地图渲染与像素断言、任务/确认流程。

### 3.3 Windows（静态合同；本机无 dotnet/Windows）

- `bash scripts/test_windows_ffi_contract.sh`：ok（含新增断言）。
- `xmllint --noout MainWindow.xaml`：ok。
- 源码审查：恢复/删除互斥、busy 禁用、严格校验（64 位 hex、ISO8601 含 T、
  seq 严格递增、schema/automatic_restart 精确匹配）与 macOS 一致。

### 3.4 Linux（源码合同 + 容器编译）

- `bash scripts/test_linux_ffi.sh`：macOS 主机按设计跳过运行时（exit 0）。
- Linux 容器（rust:1-trixie，GTK 4.18.6）内：`cargo build --release --locked
  --lib`、`cmake -DFIRESKY_BUILD_GTK_UI=ON` 构建 `firesky_linux_native`、
  FFI smoke 与缺资源负例全部通过（CMAKE-BUILD-OK / GTK-NATIVE-BUILD-OK /
  FFI-SMOKE-OK / NEGATIVE-CASE-OK）。
- 编译修复：GTK 4.18 将 `gtk_drop_down_get_selected_item` 返回类型从
  `GObject*` 改为 `gpointer`（ubuntu-24.04 CI 的 4.14 仍为 `GObject*`）；
  三处调用点改用 `gpointer` 绑定，两个版本均兼容。ubuntu-24.04 CI 的
  4.14 编译仍需 PR 门禁确认。
- GTK shell 源码审查：统一预检的 check→prepare→forecast 编排、周期龄确认、
  取消丢令牌、任务历史校验与恢复流程均按 macOS 合同实现。

## 4. 仍需目标机/发布环境验收（不在本分支宣称完成）

1. Windows x64/ARM64：WinUI 实机编译、行内嵌套按钮交互、恢复参数后的预检
   对话框、DPI/WebView2/Mica、真实 CAMS 数据。
2. Linux：GTK 桌面会话交互（modal 层叠、主题、字体）、通知/文件选择器、
   发行包、x86_64/ARM64 目标构建。
3. Docker：`linux/amd64` 由 CI 双架构矩阵验证；发布（push 到 registry）与
   clean-machine 部署验收待做。
4. CI：分支推送后经 PR 触发 `native-desktop.yml`（contracts / linux-native /
   macos-native / windows-native / linux-ffi / docker 六个 job）。
5. Web：浏览器对 Docker 部署页面的真浏览器走查（本机 Playwright 走查基于
   dev server mock；HTTP 冒烟已覆盖静态资产与 API 合同）。

## 5. 环境备注

- 本机无 Docker Desktop/OrbStack，使用 Homebrew colima + docker + buildx；
  VM 为 linux/aarch64。`docker context use colima`。
- Windows 契约只能在 Windows 上编译；Linux GTK 契约用容器编译替代真机。
