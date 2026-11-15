# FireSky v1.0.0 发布模拟报告（2026-11-15）

本报告记录 v1.0.0 正式发布前的一轮**发布模拟**：清理残留文档/文件、
全量测试、原生打包、冒烟、离线回放与 DMG 校验。**本轮不是实际 publish，
未创建任何 git commit/tag**；工作区含大量用户未提交改动，全部保留。

## 1. 版本与产物

| 项 | 值 |
| --- | --- |
| 产品版本 | 1.0.0（`engine/Cargo.toml`、`Info.plist`、`CHANGELOG.md` 一致） |
| 平台 | macOS arm64（Apple Silicon），最低 macOS 14.0 |
| 架构 | SwiftUI/AppKit 原生 `.app` + Rust FFI 单引擎（ABI v1），无 Python |
| 签名 | ad-hoc（`codesign --sign -`），**无 Developer ID / 公证** |
| App | `dist/native-macos/FireSky.app`（259 MB） |
| DMG | `dist/v1.0.0/FireSky-1.0.0-macos-arm64.dmg`（204 MB） |

SHA256（`dist/v1.0.0/SHA256SUMS.txt`，与 DMG 同目录）：

```
3055508f23d2d00725ec6202f668b7625c26a7c681167ca21ab05380f0d92c6e  dist/v1.0.0/FireSky-1.0.0-macos-arm64.dmg
d73cd60059768e86d02dc5b70d2f04d8b9d910f3bcc4d56d5a321bab621f494e  dist/native-macos/FireSky.app/Contents/MacOS/FireSky
3675ac2c68d7bd0f7ffd9aaa9b37d63d8878321157940f7b83a0f72d97e92f80  dist/native-macos/FireSky.app/Contents/Frameworks/libfiresky_engine.dylib
```

## 2. 发布前清理

- 11 份历史/退役文档 `git mv` 至 `docs/archive/`（迁移计划、Tauri/PySide
  退役、旧基线等）；README 与现存文档链接核对，0 处断链。
- 误生成目录与副本移入废纸篓：`-q/`、副本 Markdown、空 `desktop/`、
  `dist/glass-ui` 与 `dist/ui-update`（各约 1.2 GB）、旧 `FireSky.app`/旧 DMG。
- `dist/` 现仅保留 `native-macos/`（当前 app）与 `v1.0.0/`（当前 DMG）。
- 构建残留（superseded crate artifacts、incremental、`.rcgu.o`）由
  `build_macos_native.sh` 自动移入废纸篓，共享 target 目录保持有界。
- `smoke_macos_ffi.c` 区域预检由 both 改为 gfs，对齐单模型合同。

## 3. 本轮修复（发布模拟中发现并 forward-fix）

### 3.1 compact 区域剖面回归（`engine/src/assessment.rs`）

原逻辑 `with_profiles = … && !compact_results` 与
`with_profile_bundles = !compact_results && (…)` 使 compact 区域结果不产
`cross_section_source.json` / `profiles_NNN`，导致 UI 剖面不可用、几何置信
恒为 0.55。改为：

```rust
let with_diagnostics = with_diagnostics && !input.compact_results;
let with_profile_bundles = with_profiles || with_frames;
```

剖面保留在独立有界 children，主 bundle 仍按 compact 跳过 columns/aerosol。
修复后区域 gfs flow `profile_source.available=True`，置信 0.732（不再 0.55）。

### 3.2 CAMS 精确时次回退缺陷（`engine/src/cams_data.rs`）

`single()` 的 ±3/+6 回退候选会把"请求 12Z 却拿到 15Z"的非精确文件当成功
返回，区域合同（`desktop_runtime.rs:1405`）再 fail-closed 拒绝。抽出纯函数
`fallback_candidates(run_time, lead, target, exact, eligible)`：exact 模式只
保留命中 target 的候选，空则返回 `"CAMS did not provide the exact requested
valid time"`。新增单测
`exact_fallback_candidates_never_shift_the_requested_valid_time`。

### 3.3 回放夹具单模型守卫（`apps/macos/Tests/WorkbenchSnapshot.swift`）

单模型合同下，区域回放工作区可能只含 gfs 结果。原夹具无条件强制
`selectedModel="ec"`，使 ec-wide 分支因无 ec points 而落入 point 查询失败。
改为：恢复结果中无请求模型时保留产品恢复逻辑选中的模型，并同步
`regionalDisplay`。产品代码 `Store.restoreTaskResult` 本身已按任务 payload
正确选模型，未改动。

### 3.4 EC 区域单点退化包络（`engine/src/desktop_context.rs`）

EC 区域回放最初在 prepare 阶段反复下载 CAMS 后报 "strict validation at every
eligible fallback lead"。逐层定位（手工向 ADS 提交相同 lead-12 查询拿回的
GRIB 时次精确、数值合格，引擎解码 RPC 也能成功）后确认是**产品缺陷**而非
数据缺口：

- 单个显式观测点使 EC 区域点集包络退化（north==south 且 west==east）。
  原 `domain()` 显式 EC 分支只对经度做 ±0.125° 半格补齐，纬度未补，得到
  单行 bbox。
- CAMS 模型格点覆盖合同（`cams_cache::model_grid_covers_group`）要求至少
  两条纬线/经线，单行 bbox 使每个缓存瓦片都被判"未覆盖"→ 缓存永不命中 →
  反复 ADS 下载 → 下载后仍被拒 → 报 strict validation 失败。
- 即便补齐纬度，±0.125° 经 canonical 0.25° 取整后仍塌成单格，EC 代理网格
  无法从单一格点推断间距（"EC proxy requires at least one non-singleton
  axis"）。

修法：显式 EC 点集包络若退化（任一轴零跨度），回退到与 GFS 一致的缓冲区域
域（`union_boxes(points, 1000.)` + canonical 0.25°），保证区域 EC 始终是二维
网格；真正的多点二维点集仍保留紧贴包络。新增回归测试
`ec_regional_single_explicit_point_bbox_is_two_dimensional`。修复后 EC 区域
离线 flow 与 UI 回放均通过，forecast 产出真实质量指数与可用剖面源。

## 4. 测试与验证结果

| 项 | 结果 | 证据 |
| --- | --- | --- |
| Rust 全量（release，含 CAMS 与 EC 包络修复） | 457 passed / 0 failed / 3 ignored；集成 4+2+4 passed | `cargo-test-release.log` |
| pytest 契约（.venv，release 引擎） | 38 passed / 1 skipped | `pytest-contract.log` |
| macOS 原生构建 | rc=0 | `build-macos-native.log` |
| native smoke（clean-machine + app 冷启动 + arm64） | 全过 | `macos-native-smoke.log` |
| point 离线 flow（both） | FLOW OK，restored [ec, gfs] | `replay-flow-point.log` |
| regional gfs 离线 flow | FLOW OK，restored [gfs] | `replay-flow-regional.log` |
| regional ec 离线 flow | FLOW OK，restored [ec]，真实质量指数 + 剖面源可用 | `replay-flow-regional-ec.log` |
| point UI 回放（gfs/ec × wide/narrow） | 全 ready | `replay-ui-point.log` |
| regional UI 回放（gfs/ec × wide/narrow） | 全 ready（ec 分支按单模型合同回落 gfs） | `replay-ui-regional.log` |
| regional ec UI 回放（gfs/ec × wide/narrow） | 全 ready（实际选中 ec） | `replay-ui-regional-ec.log` |
| DMG create / verify | rc=0 / checksum VALID | `dmg-create.log` / `dmg-verify.log` |
| DMG 挂载 codesign + 版本 + arch | ok / 1.0.0 / arm64；dylib SHA 与构建一致 | 本报告 §1 |

所有离线 flow/UI 均在 `sandbox-exec (deny network*)` 下运行，仅回放本地缓存。

## 5. 已知限制

- **EC 区域单点退化包络已修复**（§3.4）：EC 区域离线 flow 与 UI 回放本轮均
  通过。修复前缺的精确 12Z CAMS 瓦片已按引擎相同的 ADS 查询补齐入缓存
  （`cams-optics-v2-2026-11-14-0000-012-43adcedca8e4.grib`，时次/数值经引擎
  解码校验），离线回放不再触发下载。
- **城市排行在独立回放引擎下不可用**（gfs/ec 区域一致）：standalone
  `firesky-engine desktop` 解析的 city-catalog 路径在该上下文不存在，
  `city_ranking` 优雅降级为 `unavailable`（"No such file"），不影响 forecast
  主流程（flow 仍 FLOW OK）。打包 app 内资源路径正常，非本轮回归。
- ad-hoc 签名，无 Developer ID / 公证；首次打开需用户手动放行。
- Windows / Linux 原生打包暂停；Docker / Web 验收本轮未做。
- 忙机（load 8+）下编译/测试耗时为观察值，非空闲基准；性能数据不作为验收。
- 未 commit / tag；正式发布由用户决定。

## 6. 复现命令

```bash
cargo test --manifest-path engine/Cargo.toml --locked --release
FIRESKY_REQUIRE_RUST_TESTS=1 \
  FIRESKY_RUST_ENGINE_BIN="$PWD/engine/target/release/firesky-engine" \
  .venv/bin/python -m pytest tests/ -q
bash scripts/build_macos_native.sh
FIRESKY_CLEAN_MACHINE_SMOKE=1 FIRESKY_APP_SMOKE=1 FIRESKY_EXPECTED_ARCH=arm64 \
  bash scripts/test_macos_native.sh
bash scripts/test_macos_replay_ui.sh "$PWD/output/release-v1-20261115/replay-point" point
bash scripts/test_macos_replay_ui.sh "$PWD/output/release-v1-20261115/replay-regional" regional
bash scripts/test_macos_replay_ui.sh "$PWD/output/release-v1-20261115/replay-regional-ec" regional
hdiutil create -volname "FireSky 1.0.0" -srcfolder dist/native-macos/FireSky.app \
  -ov -format UDZO dist/v1.0.0/FireSky-1.0.0-macos-arm64.dmg
hdiutil verify dist/v1.0.0/FireSky-1.0.0-macos-arm64.dmg
```

离线 flow（`sandbox-exec (deny network*)` 包装，仅回放本地缓存）：

```bash
export FIRESKY_RUST_ENGINE_BIN="$PWD/dist/native-macos/FireSky.app/Contents/Resources/firesky-engine"
for ws in replay-point replay-regional replay-regional-ec; do
  /usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' \
    .venv/bin/python "output/release-v1-20261115/$ws/desktop_flow.py"
done
```
