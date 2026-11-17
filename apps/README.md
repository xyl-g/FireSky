# FireSky Native Desktop

> 桌面正式路线（2026-11-12）：原生 UI + Rust FFI，先 macOS，后 Windows/Linux。
> Tauri 桌面壳已退役（2026-11-13）：共享 Web 迁至 `apps/shared-web/`（Docker 与
> Rust HTTP 托管的浏览器页面），Tauri package 已删除。
> 退役记录见 `docs/archive/PYTHON_TAURI_PYSIDE_RETIREMENT_PLAN.zh-CN.md` 与
> `docs/archive/RETIREMENT_PHASE0_BASELINE.zh-CN.md`。
> 产品与 UI 基准（2026-11-13，用户明确）：先完善并验收 macOS，其他 UI 最终向
> macOS 对齐。见[基准规范](../docs/MACOS_UI_BASELINE.zh-CN.md)与
> [当前计划](../docs/archive/NEXT_AGENT_PLAN_2026-11-13.zh-CN.md)。
> Windows/Linux/Docker 对齐已在 `feat/win-linux-docker-parity` 分支接续
> （2026-11-14）：Windows 数据页任务历史/结果恢复/重查、Linux 统一预检/
> 任务历史/结果恢复均已按 macOS 合同接入源码，Docker/Web 页面完成本地
> 构建与运行冒烟；目标机（Windows x64/ARM64、Linux 桌面会话、双架构镜像
> 发布）验收仍待。状态见[跨平台基线](../docs/NATIVE_UI_CROSS_PLATFORM_BASELINE.zh-CN.md)
> 与 [分支记录](../docs/WIN_LINUX_DOCKER_PARITY.zh-CN.md)。

## Architecture

```
macOS: SwiftUI / AppKit / MapKit  ─┐
Windows: WinUI 3 / C# / WebView2   ├─ versioned Rust FFI ─ Rust Runtime
Linux: native toolkit              ┘
Docker/Web                         ─ Rust HTTP ────────────┘
```

The native frontends use platform-native navigation, controls and modal dialogs.
The product and UI baseline is the validated macOS application. Complete its
point, regional, nowcast, data/settings and history workflows first, then align
the shared Web and future Windows/Linux interfaces to its capabilities,
information hierarchy, result semantics and state feedback. Each platform keeps
its native controls and system integrations. Existing Qt and Web screens are
historical capability references; their dimensions and layouts do not dictate
the Mac interface. Unverified Mac behavior must be corrected before it becomes
part of the cross-platform baseline.
The Mac navigation surface uses `glassEffect` on macOS 26+, with a system material
fallback on macOS 14/15. Windows uses WinUI `MicaBackdrop`; the forecast map is a
local Leaflet document hosted by WebView2, not a web-rendered application shell.
MapKit and OpenStreetMap tiles need network access. Their map imagery is not
satellite nowcast data.

The Mac point overview presents GFS and EC summaries side by side, paired
model values within the existing metric cells, and two curves aligned to actual
timestamps. Profile, diagnostics and ledger retain a model selector. Results
are never averaged or substituted across models; an unavailable model remains
explicitly unavailable. AOD 550 is instead a shared environmental reading from
the report's `aerosol.aod550`, labelled CAMS only when the actual source is CAMS.
Identical readings appear once; different event readings show a range, not
an average or a model-specific peak-quality component.
Data preparation lives in the prediction sheet and
the separate data page, without a redundant input-rail data card or persistent
engine-status badges. Connection failures still offer a reconnect action.

The legacy PySide6 desktop (`desktop/`) and the Python reference stack
(`src/firesky/`) were removed on 2026-11-13. Native apps are the only desktop
clients; production native clients never start a Python backend.

## macOS

Prerequisites: macOS 14+, Swift 6 SDK with macOS 26 APIs, Cargo and ecCodes.
No Python environment is needed to build or run the native application.

```sh
bash scripts/build_macos_native.sh
open dist/native-macos/FireSky.app
```

This produces an ad-hoc signed app bundling the Rust engine, ecCodes dependency
closure, definitions, samples and licenses. Its default data workspace is
`~/Library/Application Support/FireSky/Data`, not this checkout.
An explicit workspace setting or `FIRESKY_NATIVE_WORKSPACE` build override is
supported. Notarization and clean-machine release acceptance remain separate.
The sidebar contains only a Settings entry at the bottom; appearance selection
is in Settings. The app icon and sidebar mark share the same native vector
source, and custom dates use a calendar popover rather than a stepper field.

### CAMS Credentials

At startup the Mac app checks local ADS configuration and, if missing or
incomplete, offers a reminder linking to Settings and the configuration tutorial.
This check never accesses ADS, starts a download, or declares a key authenticated.
Existing complete local data can still be used without a key.

Settings provides a secure input for a replacement ADS Personal Access Token,
official ADS/API/dataset links, and an explicit save action. Saved credentials
are plaintext in `CDSAPI_RC` or `~/.cdsapirc`, atomically replaced with permissions
`0600`. Other flat configuration fields are retained. Existing values are not
returned over IPC, shown in the interface, or written to the checkout.
`CDSAPI_KEY` / `CDSAPI_URL` override the file; the interface reports that override
and refuses a misleading file save when it would not take effect. Token validity
and dataset licence acceptance are checked by ADS during actual downloads.

### Download Probes

The Mac data page presents an explicit GFS / EC / CAMS download check before the
local inventory, with a warning dot in the sidebar until a check has completed.
Nothing is probed automatically at startup. GFS downloads one tiny NOMADS subset;
EC reads the official ECMWF index and downloads one complete GRIB message using
Range; CAMS submits one single-variable, single-time, small-area ADS job and
downloads its result. Only complete GRIB messages successfully decoded by ecCodes
pass. Samples remain in memory and do not populate or change prediction caches.
Each row reports endpoint, received bytes, elapsed time and sanitized failures.
The copy action exports those results, never tokens or signed asset URLs.

EC supports three official open-data endpoints: ECMWF, AWS Europe and Google
Cloud. The selected endpoint is stored locally and is passed to both the
download probe and subsequent desktop forecast requests. GFS uses the NOAA
NOMADS endpoint and CAMS uses Copernicus ADS; those services do not expose a
second official mirror in this workflow, so their entries are intentionally
fixed.

GFS/EC have a 40-second operation budget and CAMS a 90-second budget, with
8-second connection/read timeouts. Cancellation is cooperative between reads
and polling steps; cancelling or timing out a queued CAMS job attempts to dismiss
it without retrying submissions. A queued job, reachable API or locally saved key
is never reported as proof of successful data download. Results describe this
network at the check time, not complete forecast coverage or future availability.
The probe uses the current production endpoints, including the official EC host;
it does not select or switch mirrors. ADS bypasses environment proxies, matching
the existing production ADS downloader.

## Windows

Build on Windows 11 with Visual Studio 2022's WinUI/C# tooling, .NET 8 SDK,
Windows SDK and WebView2 Runtime installed:

```powershell
dotnet restore apps/windows/FireSky/FireSky.csproj
dotnet build apps/windows/FireSky/FireSky.csproj -c Release -p:Platform=x64 -r win-x64
dotnet build apps/windows/FireSky/FireSky.csproj -c Release -p:Platform=ARM64 -r win-arm64
```

Run the resulting unpackaged `FireSky.exe`. The project copies
`firesky_engine.dll` beside the native resources and loads the same versioned
C ABI as macOS; `FIRESKY_RUST_ENGINE_BIN` is retained only for explicit JSONL
development diagnostics. Configure a writable data workspace with
`FIRESKY_WORKSPACE`.
GRIB decoding requires an ecCodes DLL with its definitions/samples;
`FIRESKY_ECCODES_ROOT` can include these resources during packaging. No Python
interpreter is launched.

The Windows FFI adapter is wired but has not been built or interaction-tested
on this Mac. Windows engine parity, GRIB native dependencies, Rust DLL
packaging, system location permission, Mica fallback, DPI and WebView2 must be
validated on a Windows machine before claiming a working Windows release.
The host-independent adapter contract can be checked with
`bash scripts/test_windows_ffi_contract.sh`; this does not replace either
Windows architecture build or runtime acceptance.

## Linux

The Linux port reuses the same `engine/include/firesky_ffi.h` ABI. A C++20
RAII client is available in `apps/linux`; it loads `libfiresky_engine.so`,
copies progress events before returning from the Rust callback, and keeps the
runtime/library alive until all task workers have joined:

```sh
./scripts/test_linux_ffi.sh
```

This is the Linux contract gate, not the finished desktop shell. GTK/Qt window
navigation, system location permissions, map integration, packaging and
target-machine real-data acceptance remain after the shared FFI contract is
stable. No separate JSONL production protocol is introduced.

## Workflow Contract

- Point and regional forecasts always run GFS and EC independently.
- Regional spacing is forced to 0.25 degrees; values are actual engine output,
  never fabricated or interpolated. Outlines represent unscored grid cells.
- Point/nowcast support explicit system location permission, age/accuracy checks,
  map selection, place search, elevation/timezone lookup and manual confirmation.
- `check` returns a short local checklist plus a server-owned request token.
- `publications` verifies upstream asynchronously without holding the operation
  lock or blocking the local checklist. Unverified does not mean latest.
- `prepare` downloads the pinned plan (or verified newer applicable cycle),
  validates it strictly, and returns to the checklist. It never starts scoring.
- `forecast` requires `confirmed: true`, a ready, unexpired token, and another
  strict integrity/coverage check. Tokens are single-use and hold frozen inputs.
- Cancelling a sheet dismisses the token; cancellation of running work is
  cooperative at engine progress callbacks, not immediate interruption of an
  in-flight external transfer.
- Inventory shows GFS / EC / CAMS sizes, file counts and expandable cycles.
  Downloads for the selected location go through the same preflight.
- Data and download navigation is separate from settings.

## FFI 与开发 IPC

正式桌面客户端加载 `libfiresky_engine`（macOS `.dylib`、Windows `.dll`、Linux
`.so`），通过不透明 runtime/task 句柄调用版本化 C ABI。同步/异步结果分别使用
`firesky-ffi-call-v1` 和 `firesky-ffi-error-v1` UTF-8 JSON envelope，进度事件使用
`firesky-ffi-event-v1`；所有 Rust 返回的 `char *` 必须调用
`firesky_ffi_string_free` 释放，UI 回调必须切回平台主线程。

macOS 的 JSONL desktop IPC 仍可用于开发诊断和动态库问题定位：

Launch `firesky-engine desktop --workspace PATH`. No HTTP listener,
port, API token or external server is involved. One JSON object per line:

```json
{"id":"request-1","method":"check","params":{"kind":"point","payload":{}}}
{"id":"request-1","type":"result","result":{"session":"opaque-token","report":{}}}
```

Methods: `hello`, `search`, `metadata`, `inventory`, `check`, `publications`,
`prepare`, `forecast`, `dismiss`, `cancel`, `cams_credentials`, `save_cams_key`,
`network_probe`.
`hello` includes only sanitized credential availability. `save_cams_key` accepts
a key over the private pipe but never echoes it. Cancellation parameters contain
`request_id`. Progress exposes source/stage/percent and, optionally, an engine
status `message` (a string capped at 1,024 characters) and explicit boolean
`cache_hit`. These additive fields keep protocol version 1; clients must accept
their absence and ignore unknown fields. Treat a cache hit only as
`cache_hit == true`, never infer it from stage/percent: paired GFS/EC forecasts
map a native 99% hit to 49%/99% respectively. Only cache-hit producers set the
flag; numeric/string lookalikes are not forwarded. Messages are user-visible
status text, so producers must not include credentials or other secrets;
diagnostic engine output still goes to stderr. Swift/Windows clients use a
nonempty message when available and fall back to their previous status text.
`network_probe` additionally streams
sanitized per-source probe rows and returns the three final dataset results;
no forecast session is created. Clients store stderr under the
platform's FireSky application-support directory.

### Development IPC overrides

The following section documents the JSONL development bridge used for
diagnosis and contract testing. It is not the macOS production path: the
bundled app loads `libfiresky_engine.dylib` directly through FFI, and a missing
FFI library is a hard error in a packaged app.

Set `FIRESKY_LOCATION_ENGINE_BIN` to an absolute `firesky-engine` executable
path before launching the native app to route `search`, `metadata` and `inventory` directly
to a separate Rust child. `FIRESKY_LOCATION_CONFIG` optionally names a bounded
startup JSON file:

```json
{
  "location": {
    "timezone_manifest": "/absolute/resources/timezones/manifest.json",
    "location_cache_path": ".cache/locations.json",
    "srtm_cache_dir": ".cache/srtm"
  },
  "inventory": {
    "gfs_cache_dir": ".cache/gfs",
    "ecmwf_cache_dir": ".cache/ecmwf",
    "cams_cache_dir": ".cache/cams"
  },
  "allow_download": true,
  "workers": 4,
  "max_active_requests": 16
}
```

Prepare the complete timezone resource with `scripts/export_timezone_data.py`;
see Stage 26 in `docs/archive/RUST_MIGRATION.md` for pinned versions and licensing.
The app workspace is the child's working directory. Without a config file,
online provider defaults work, but offline timezone fallback needs its explicit
prepared resource. No resource is downloaded or synthesized automatically.

The historical command `desktop-location` and environment variable name
remain compatible; the same process now also serves read-only inventory.
The native client validates all required methods in its handshake during connection. It
retains the original compact desktop payloads: search always resolves metadata
after locating a candidate, and metadata retains the caller's coordinates.
Inventory returns the full original datasets/cycles/coverage/paths/area
payload. Counts and filename indexing are not GRIB integrity, publication
or forecast-readiness checks. No files are downloaded or modified by inventory.
Cancellation is sent to the child that owns the request. A configured Rust
failure is reported, never silently retried through Python. Reconnecting or
stopping the client disposes both children. Diagnostics use separate
`engine.log` and `location-engine.log` files.

Without `FIRESKY_LOCATION_ENGINE_BIN`, the historical bridge is not used by the
bundled macOS app. It remains an explicit development-only diagnostic path;
the main desktop service, forecasts, preparation and credentials in this section
still describe the legacy Python client. Qt/HTTP are not routed through this
optional child. The Windows source path still requires a Windows/.NET build and
runtime acceptance.

Independently, set `FIRESKY_DESKTOP_DATA_BACKEND=rust` and
`FIRESKY_RUST_ENGINE_BIN` to the engine's absolute path to delegate desktop
model context planning and model-plan cache checks from the existing Python
desktop service to Rust. The raw planner covers point normalization, regional
grids, source domains, cycles, solar windows and terrain-dependent replanning;
the checker covers GFS/EC availability and fast/strict CAMS brackets while
preserving complete paired reports. It applies to both native clients' main
service and the Qt panel; it does not route HTTP endpoints. Publication probes,
confirmation sessions, preparation and forecast lifecycle remain in Python.
The same selector now routes the production `search`, `metadata` and
`inventory` methods through Rust RPCs; their original payloads and cache/path
semantics are preserved, and native failures are surfaced without Python
location or inventory fallbacks. The Python desktop service still owns the
IPC session and therefore remains present.
The bridge reuses one bounded native connection, rescans on every check and
has no Python checking fallback. Non-China generated grids need the verified
timezone manifest configured through `FIRESKY_TIMEZONE_MANIFEST`; uncovered
coordinates use the existing `Etc/GMT` fallback. See Stage 30 of
`docs/archive/RUST_MIGRATION.md`. Nowcast checks use the same native GFS/CAMS cache
gate and native event-window context. Preparation uses the native
`desktop_nowcast_prepare` boundary for compact-HSD validation, GFS hours/bbox
and CAMS bracket downloads after the Python HSD provider has discovered and
written the remote compact frames. Remote HSD listing, range download,
calibration and cache writes remain Python; native failures do not fall back
to Python GFS/CAMS providers.

For an opt-in read/decode migration of already-written compact HSD frames, set
`FIRESKY_HSD_BACKEND=rust` together with `FIRESKY_RUST_ENGINE_BIN`. The native
provider validates the NPZ in Rust, exports a source-bound
`hsd-compact-frame-v1` tensor bundle and returns calibrated arrays to the
existing scorer. Paired frames also use the native B13 phase-correlation
motion diagnostic and its bounded manifest-version cache. The default remains
Python; remote HSD discovery, range downloads, calibration and cache writes
still use the Python provider.

When the Rust selector is active, the same provider also performs the
continuous B07/B13/B15 advection step in Rust and stores a source-bound
derived tensor bundle. Repeated identical projections use a bounded
manifest-version cache; HCAI categorical retrieval and cloud-column inference
remain Python-owned. For HSD-only frames with a validated native manifest,
`calibrated_satellite_cloud_retrieval` now batches the deterministic
B07/B13/B15 cloud posterior through `desktop_nowcast_hsd_retrieval`; the
existing `SatelliteCloudColumn` and feature contracts are reconstructed in
Python. HCAI categorical columns stay on the Python path. This retrieval
boundary is correctness-focused and does not claim an end-to-end speedup over
the in-process Python posterior; see `docs/archive/RUST_PERFORMANCE.md` Stage 37.

The native first version presents scores, summaries, the point quality curve,
ledger, model-specific failures and actual regional cells. The legacy Qt profile canvas,
five-minute ledger and detailed diagnostic views have not all been ported.
Keep Qt available until those views and Windows validation are complete.
