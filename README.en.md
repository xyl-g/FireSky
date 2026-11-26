# FireSky

burning-sunset (sunset/sunrise glow) prediction
stack: **native Rust prediction engine and HTTP service + SvelteKit frontend +
native desktop clients**. The physics follows the methodology of the
[SunsetBot tutorial](https://www.sunsetbot.top/halo/).

> 中文文档：[README.md](README.md)

> Current release **v2.0.3** (see [Download & install](#download--install-releases)):
> macOS DMGs (dual arch), Windows zip + installer (x64/ARM64), universal Android
> APK, and unsigned sideloadable iOS `.ipa` are all built and attached to GitHub
> Releases automatically on `v*` tags (with independent `mobile-v*` mobile releases).
> v2.0.3 fixes port 8765 persistence across macOS in-app updates/quits and polishes
> Top-3 azimuth radar bars; v2.0.2 redesigns the macOS Spectral Glow & Azimuth Radar dual-column cards (fixing
> the `0..1` radar scale and step timestamps) and AltStore v2 metadata; v2.0.1 extends CAMS ADS matching-fingerprint queue retention back to 24 hours
> (keeping 4-hour orphan cleanup) and auto-reloads the macOS background sync helper
> after app upgrades; v2.0.0 brings **CIE 1931 spectral glow color prediction**, **16-direction azimuth
> glow radar**, **high-score glow alerts (Bark / ServerChan / JSON Webhook / macOS banners)**,
> **mobile QR code pairing**, **PNG glow poster export on macOS**, per-source sync
> progress clarity, and an **auto-updating AltStore source**; v1.10.5 fixes macOS window restoration after minimize/close, makes window reopen
> connection-idempotent, fixes background helper workspace checks, tightens CAMS ADS
> orphan cleanup to 4 hours, and upgrades GitHub Actions to Node 24;
> v1.10.4 adds **macOS close-to-menu-bar background mode** (yielding scheduled syncs
> to the `launchd` helper so quitting isn't blocked), **resilient CAMS ADS queue
> polling** across transient `5xx`/`429` errors, iOS IPA `AppIcon` + Ad-Hoc signature
> compatibility for AltStore/SideStore, and path-gated CI; v1.10.3 brings **embedded interactive maps & UI
> polish** on Android, hardens iOS & Android into **strictly read-only thin clients**,
> adds **CAMS ADS job persistence & reattach**, and prevents startup OOM on large
> regional snapshots. v1.10.2 dramatically speeds up **in-app
> updater downloads with proper default system-proxy routing**, and adds
> **per-source locks & exponential-backoff auto-recovery** for background data sync
> (so long CAMS ADS queues never block GFS/EC). Since v1.10.1, iOS & Android run as
> pure native thin clients (connecting to Mac/Windows/Docker servers over real-time
> SSE with Regional Forecast & Satellite Nowcast).
> Design tokens in [DESIGN.md](DESIGN.md).
> The UI reference is the [macOS product & UI baseline](docs/MACOS_UI_BASELINE.zh-CN.md);
> mobile blueprint and acceptance status:
> [mobile native plan](docs/MOBILE_NATIVE_PLAN.md).

---

## Contents

- [FireSky](#firesky)
  - [Contents](#contents)
  - [Features](#features)
  - [Download \& install (Releases)](#download--install-releases)
    - [macOS](#macos)
    - [Windows](#windows)
    - [Android](#android)
    - [iOS](#ios)
    - [Docker (self-hosting)](#docker-self-hosting)
    - [Platform strategy](#platform-strategy)
  - [Architecture \& physics](#architecture--physics)
  - [Requirements \& installation](#requirements--installation)
  - [Data sources \& registration](#data-sources--registration)
  - [Configuration](#configuration)
  - [Usage](#usage)
    - [Backend + frontend (recommended)](#backend--frontend-recommended)
    - [CLI (engine only, no server)](#cli-engine-only-no-server)
  - [API endpoints](#api-endpoints)
  - [Testing \& invariant regression](#testing--invariant-regression)
  - [Desktop app (local, single-machine)](#desktop-app-local-single-machine)
    - [Map basemaps and the Tianditu key](#map-basemaps-and-the-tianditu-key)
  - [Mobile apps (v1 scope)](#mobile-apps-v1-scope)
  - [Repository layout](#repository-layout)
  - [Troubleshooting](#troubleshooting)
  - [Origin](#origin)
  - [Acknowledgments](#acknowledgments)

## Features

- **Point forecasts**: per-five-minute burning-sky quality ledger for one
  observer (index + level labels: from "no glow" to "once-in-a-lifetime burn").
- **Regional forecasts**: batch observer scoring over China/East-Asia regions
  with map frames and atmospheric cross-sections.
- **Nowcast**: Himawari-9 satellite (HSD segments) cloud-column retrieval + weather
  radar convective correction.
- **Backend service**: Rust/Axum jobs + SSE + gzip + checked input caches.
- **Minimal frontend**: `/forecast`, `/now`, `/outlook`, `/admin`.
- **Native desktop**: macOS SwiftUI/AppKit + Rust FFI is the product & UI
  baseline; the Windows WinUI 3 client matches the same information
  architecture (standalone history page, categorized settings, data
  management, in-app update) and was build/run validated on a real
  Windows machine, shipping as a self-contained zip plus an Inno Setup
  installer attached to Releases. The Linux native UI code is kept but no
  longer a release target (see [Platform strategy](#platform-strategy)). See
  [build instructions and migration status](apps/README.md). The old PySide6 app was retired (2026-11-13); user data (~/.firesky-desktop favorites and current spot) imports automatically into the native app.
- **Mobile apps**: pure native iOS SwiftUI + Android Kotlin/Compose thin clients
  connecting to your Mac / Windows / Docker FireSky server over LAN HTTP or
  Cloudflare Tunnel HTTPS with real-time SSE streaming; supports Point Forecast,
  Regional Forecast (China heatmap & city ranking), Satellite Nowcast, Data
  Management, and Task History, with both Android APK and unsigned iOS `.ipa`
  published with Releases.

## Download & install (Releases)

Official builds are published on
[GitHub Releases](https://github.com/xyl-g/FireSky/releases) (current:
**v2.0.3**). Verify integrity after downloading (`SHA256SUMS.txt` uses bare
file names; run it in the directory holding the assets):

```bash
shasum -a 256 -c SHA256SUMS.txt
```

Standard assets (`<version>` is a wildcard; all built and published
automatically by CI on `v*` tags):

| Asset | Platform | Notes |
|---|---|---|
| `FireSky-<version>-macos-arm64.dmg` | macOS 14+ (Apple Silicon) | native desktop `.app`, ad-hoc signed |
| `FireSky-<version>-macos-x86_64.dmg` | macOS 14+ (Intel) | CI Intel build + smoke; runs via Rosetta 2 on Apple Silicon |
| `FireSky-<version>-windows-x64.zip` / `-x64-setup.exe` | Windows 10 19041+ (x64) | portable zip / Inno Setup installer |
| `FireSky-<version>-windows-arm64.zip` / `-arm64-setup.exe` | Windows (ARM64) | same |
| `FireSky-<version>-android-arm64.apk` | Android 8.0+ (universal thin client) | pure Kotlin/Compose thin-client APK (no embedded engine) |
| `FireSky-<version>-ios-arm64.ipa` | iOS 16+ (arm64 thin client) | unsigned `.ipa` for AltStore / SideStore / Sideloadly / TrollStore / developer re-signing |
| `SHA256SUMS.txt` | — | SHA256 manifest for all assets |

> Both Android APK and unsigned iOS `.ipa` ship automatically with `v*` tag
> Releases (sideload or re-sign the `.ipa` on iOS); Docker images are built from
> source (see [Docker (self-hosting)](#docker-self-hosting)).

### macOS

Drag the app from the DMG into Applications. Releases are **ad-hoc signed
(no Developer ID / notarization)**, so Gatekeeper blocks the first launch:

- **macOS 14**: right-click `FireSky.app` → Open; or System Settings →
  Privacy & Security → Open Anyway.
- **macOS 15 and later**: right-click → Open no longer bypasses Gatekeeper.
  After the first blocked launch, use System Settings → Privacy & Security →
  Open Anyway, or run:

  ```bash
  xattr -dr com.apple.quarantine /Applications/FireSky.app
  ```

Download only from the official Releases page and check SHA256 first. Full
release runbook and gates: [macOS release doc](docs/RELEASE_MACOS.zh-CN.md).

### Windows

An official release target since `feat/windows-release`, shipping as a
**self-contained zip plus an installer** (the first official Windows package
follows with the next release):

- **Portable zip**: unzip anywhere and run `FireSky\FireSky.exe`; no install
  step required.
- **Installer (setup.exe)**: guided install with a Start Menu shortcut and
  an uninstaller; uninstalling removes application files only and keeps user
  data (`%LOCALAPPDATA%\FireSky` and the chosen data directory).
- Requirements: Windows 10 19041+ / 11 and the **WebView2 Runtime**
  (preinstalled on Win11; [install on Win10](https://developer.microsoft.com/microsoft-edge/webview2/) —
  the installer checks and warns).
- Packages are **unsigned**: SmartScreen shows "More info → Run anyway" on
  first launch; download only from the official Releases page and verify
  SHA256 first (`SHA256SUMS.txt` covers zips and setups).
- The engine DLL, ecCodes and all application resources ship inside the
  package's `Resources\`; GRIB decoding needs no extra dependencies.

Full runbook and acceptance criteria: [Windows release doc](docs/RELEASE_WINDOWS.zh-CN.md).

### Android

Self-signed release APK (not distributed via app stores; arm64-v8a only,
minSdk 26 / Android 8.0+):

1. Open the downloaded APK and allow "install unknown apps" for your
   browser/file manager when prompted.
2. **Updates must come from the official APK signed with the same key**:
   install the new version over the old one to keep app data; do not mix in
   re-signed builds from other sources (signature mismatch is rejected).
3. Command line: `adb install -r FireSky-<version>-android-arm64.apk`.

### iOS

The release is an **unsigned device IPA** and cannot be installed directly.
Re-sign it with a free Apple ID via [AltStore](https://altstore.io) or
[Sideloadly](https://sideloadly.io) and sideload onto a device:

- Free-account signatures are **valid for 7 days** and must be refreshed
  (AltStore auto-refreshes on the same Wi-Fi); free accounts allow at most
  3 sideloaded apps at a time.
- **AltStore / SideStore auto-update feed**: add source URL
  `https://github.com/xyl-g/FireSky/releases/latest/download/altstore-source.json`
  in AltStore/SideStore for one-tap updates, and scan the desktop QR pairing code
  to connect instantly.
- Before first launch: Settings → General → VPN & Device Management → trust
  the developer certificate.
- Quality acceptance is the simulator `xcodebuild test` chain; the IPA is
  only the distribution vehicle.

### Docker (self-hosting)

Images are built from source (CI only runs a build smoke test; no Release
tarballs are attached). Build, then run — the service listens on 8765 inside
the container:

```bash
docker build -f docker/Dockerfile -t firesky:local .
docker run -d --name firesky \
  -p 127.0.0.1:8765:8765 \
  -v firesky-cache:/data/cache \
  -v firesky-cams:/data/cams \
  -v firesky-reports:/data/reports \
  -v firesky-logs:/data/logs \
  -v firesky-state:/data/state \
  firesky:local
# First start generates the access token (user: firesky; password is the
# output below — do not paste it into logs or public discussions):
docker exec firesky cat /data/state/http-token
# open http://127.0.0.1:8765
```

- Compose alternative: `docker compose up -d --build` (compose also binds
  `127.0.0.1:8765` only by default).
- Persistent volumes, authentication and LAN-exposure notes:
  [deployment guide](docs/DEPLOY.en.md).

### Platform strategy

- **macOS / Windows**: official release targets; CI builds and publishes
  assets on `v*` tags (macOS dual-arch DMGs; Windows x64/ARM64 zip +
  installer).
- **Android**: official release target; the mobile-release job builds the
  APK on tags and attaches it to Releases.
- **iOS**: build from source (Xcode); IPA distribution to follow.
- **Docker**: self-hosting option; images are built from source (no Release
  tarballs).
- **Linux**: **no longer a release target since 2026-11-20** (user decision);
  the GTK4 client and FFI code are kept and can be built from source. For
  servers, use Docker.

## Architecture & physics

```
GFS/EC public data ──► cloud-column derivation (3-family amount/opacity/geometry/deep risk) ──► Rust ray-traced scorer ──► empirical scoring ──► levels
CAMS aerosol (cache) ──────────────────────────────────────────────────────────────────────────┘        │
satellite/radar (live) ─────────────────────────────────────────────────────────────────────────┴─► nowcast correction
```

1. **Data layer**: GFS subset download + GRIB2 decode (fail-closed bitmap
   masking) with an mmap decode cache; three-family cloud derivation. ECMWF
   open-data is the second model source. CAMS 11-level 532 nm extinction
   profiles are read read-only from a prepared cache.
2. **Ray-tracing layer (Rust, `native/`)**: per observer × five-minute step,
   the scorer traces both the solar incoming ray and the observer return sight
   line: Earth curvature (pure $R=6371$ km on the inflow leg, $7/6$ effective
   radius for sight-line elevation), terrain and sea-surface floors,
   semi-Lagrangian wind advection (RK2 midpoint wind + edge clamping), a 36×90
   azimuth/elevation view solid-angle projection, distance-decayed target
   self-shielding (60→150 km ramp back to full blocking) with 3-cell connection
   hysteresis, and duty-cycle bracket blending. Cloud optical depth accumulates
   along the path as

$$\Delta\tau_c=b^{1.35}\frac{\Delta s}{140\ \mathrm{km}},\qquad
  T_c=e^{-\tau_c}\left(1-0.72 b_{\max}\right)$$

   where $b$ is the node blocking factor (amount × opacity × vertical
   intersection × self-shield factor).

3. **Empirical scoring layer**: cloud-amount anchors $q_{\rm cov}$ → material
   factor $f_m=m/0.72$ → inflow AOD illumination $\iota$ → return-leg AOD
   contrast $c$ → best-continuous-20-minute window integral with duration factor
   $f_D$ → calibrated response curve (saturates at pre=1.812) → level labels:

$$\mathrm{pre}=q_{\rm cov} f_m \iota c f_D,\qquad
  q=\min\left(2.5, 1.225 \mathrm{pre}^{1.2}\right)$$

   Algorithm version `2026.11.04.87`. Full derivation:
   [PRINCIPLES](docs/PRINCIPLES.en.md).

## Requirements & installation

The HTTP/CLI and native desktop runtimes are Rust and do not require Python.
The [cutover audit](docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md) records implemented
paths and validation limits.

| Dependency | Version | Purpose | Required? |
|---|---|---|---|
| Rust (cargo) | current stable | engine build | build only |
| Node.js + npm | ≥ 20 | frontend build | frontend only |
| ecCodes binary library | ≥ 2.33 | direct GRIB C ABI with definitions/samples | yes |
| Python + pytest/numpy | ≥ 3.11 | architecture contract tests and resource/data tools (`scripts/`) only | not for runtime |

```bash
# Build Rust engine and frontend
make install
# Serve native HTTP and SPA
make serve
# open http://127.0.0.1:8765
```

## Data sources & registration

**CAMS (Copernicus Atmosphere Data Store) is the only source that requires
registration.** Everything else is public and key-free.

| Source | Used for | Registration / key | Free tier |
|---|---|---|---|
| **CAMS global atmospheric composition forecasts** (ADS) | aerosol AOD550 + 11-level extinction (drives glow colour and clarity) | ⚠️ **Registration required**: register at [ADS](https://ads.atmosphere.copernicus.eu), accept the dataset licence, then save the key in desktop settings or `~/.cdsapirc`. Rust accesses ADS directly; cdsapi is not required | free, queued |
| GFS (NOAA NOMADS) | primary NWP model (cloud/RH/temperature/wind/boundaries) | none (public GRIB2); keep a polite User-Agent | free |
| ECMWF open data | second model (`model_source: ec`) | none (public S3/GCS buckets) | free |
| Himawari-9 (NOAA S3 + NICT + JMA) | nowcast satellite cloud columns (HSD segments) | none (public buckets) | free |
| RainViewer | nowcast weather radar | none (public API) | free (non-commercial) |
| Open-Meteo | place-name search / air-quality AOD fallback | none (non-commercial) | free |
| OSM Nominatim | place-name search | none; a User-Agent is required (1 req/s) | free |
| AWS terrarium elevation tiles | observer elevation | none | free |

`~/.cdsapirc` example:

```yaml
url: https://ads.atmosphere.copernicus.eu/api
key: YOUR-API-KEY
```

Once CAMS access works, warm the cache either way:

```bash
# Option A: native Rust data planner (async job; ADS key required, no cdsapi)
curl -X POST http://127.0.0.1:8765/api/v1/firesky/data/fetch \
  -H 'Content-Type: application/json' \
  -d '{"target_date": "2026-11-12", "phenomenon": "dusk", "region": "china", "timezone": "Asia/Shanghai", "sources": ["cams"]}'
# poll: GET /api/v1/firesky/data/fetch/status?job_id=...

# Option B: reuse an already-downloaded cache (the file format is
# identical): copy/symlink an existing CAMS cache into this project.
```

> Scoring reads strictly validated CAMS caches and fails when required
> space/time coverage is missing instead of inventing AOD. Rust preparation
> accesses ADS directly and still requires an account and accepted licence.

## Configuration

The native runtime uses [environment variables and CLI arguments](docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md#配置).
Docker directory conventions are in `docker/Dockerfile` and the [deployment guide](docs/DEPLOY.en.md).

## Usage

### Backend + frontend (recommended)

```bash
make serve
# with the frontend built: open http://127.0.0.1:8765/forecast
# frontend dev mode: cd apps/shared-web && npm run dev -- --host 5173 (API proxied to 8765)
```

### CLI (engine only, no server)

```bash
engine/target/release/firesky-engine forecast --input request.json --workspace "$PWD"
# JSON request examples and fields are in the native runtime audit
```

## API endpoints

See the [native HTTP contract and outstanding routes](docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md#http-范围).
Parameters, response fields and call examples: [forecast API doc](docs/FORECAST_API.md).

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/firesky/health` | GET | health check |
| `/api/v1/firesky/forecasts/national/contract` | GET | regional algorithm/cache-version contract |
| `/api/v1/firesky/forecasts` | POST | start a point-forecast job (async) |
| `/api/v1/firesky/forecasts/results/{job_id}` | GET | fetch job result (404 while unfinished; task failures are 500, unavailable GFS input is 503) |
| `/api/v1/firesky/forecasts/national` | POST | start a regional forecast job |
| `/api/v1/firesky/forecasts/nowcast` | POST | start a nowcast job (satellite + radar) |
| `/api/v1/firesky/map-frames` | GET | map frames (token + time) |
| `/api/v1/firesky/forecasts/national/summary` | GET | regional summary (strict field contract) |
| `/api/v1/firesky/points/metadata`, `/locations/search` | GET | observer metadata / place-name search |
| `/api/v1/firesky/data/*` | POST/GET | data-download planner (`data/fetch` async job, `sources`: `gfs`/`ecmwf`/`cams`) + status / prune |
| `/api/v1/firesky/cache/warmup` | POST | hot-city forecast cache warmup |
| `/{full_path}` | GET | SPA fallback (frontend client routing) |

## Testing & invariant regression

```bash
make test-rust
make test                                  # architecture contract tests; needs pytest/numpy
cd apps/shared-web && npm run check && npm run build # 0 errors
```

- `tests/test_rust_hsd_raw.py`: native HSD calibration contracts (Python is
  only the test oracle).
- `tests/test_rust_desktop_sync.py`: background data-sync helper JSONL bridge
  contracts (settings persistence, due gates, circuit breaker, fail-closed).
- `tests/test_native_preparation_ui.py`: macOS Swift preparation/display logic
  compile checks.
- The scoring core has been merged into the engine crate
  (`engine/src/native/`); after changing it, rebuild the engine
  (`make engine` / `make desktop-macos`) before running tests.

## Desktop app (local, single-machine)

The primary desktop delivery is macOS SwiftUI/AppKit + Rust FFI; the Windows
WinUI and Linux native UIs extend only along the macOS ABI, preflight and
task-lifecycle contracts ([build and migration status](apps/README.md) records
the actual acceptance boundary per platform). The Tauri desktop shell and
the legacy PySide6 app are retired (2026-11-13); the shared browser web now
lives in `apps/shared-web/` and is served by the Rust HTTP service and Docker.
Old user data (~/.firesky-desktop favorites and current spot) imports
automatically into the macOS native app. Retirement record:
`docs/archive/PYTHON_TAURI_PYSIDE_RETIREMENT_PLAN.zh-CN.md`.

### Map basemaps and the Tianditu key

The shared-web picker offers two basemap sources, **used for point picking
only — no forecast layers are drawn on the map**:

| Source | Valid area | Basemaps | Key |
|---|---|---|---|
| Tianditu (default) | Mainland China | imagery / vector / terrain | required |
| OpenFreeMap | Worldwide | imagery / streets | keyless |

Get a Tianditu key at [console.tianditu.gov.cn](https://console.tianditu.gov.cn)
(application type: browser) and enter it in the web settings page under
**Map service** (stored in browser-local preferences).

**Neither the repository nor release artifacts ship a working key.** Without
one the map shows a setup card and offers a one-click switch to OpenFreeMap.
Public use inside China must stay on Tianditu (Chinese map regulations require
a licensed basemap); OpenFreeMap is for locations outside China.

## Mobile apps (v1 scope)

Dual-native: iOS SwiftUI (`apps/mobile-ios/`) + Android Kotlin/Compose
(`apps/mobile-android/`). The prediction engine runs embedded in the app
process (`engine-mobile/`, same source as the desktop `desktop` subcommand;
the JSON-lines frame protocol is frame-for-frame identical). ecCodes
definitions ship inside the package (~131 MB; trimming is a follow-up
optimization); offline forecasts rely on seeded/downloaded caches.

- **In v1**: point forecasts (independent GFS/EC results), the data session
  check→prepare→confirm (progress/cancel), observer management, settings
  (CAMS key written to the sandbox), offline viewing of the last result and
  **automatic restore after kill-restart** (`result_restore`, same semantics
  as macOS).
- **Not in v1**: regional forecasts, live nowcast, HTML reports, background
  prefetch.
- **Maps**: iOS already uses native MapKit; the Android map is deferred — v1
  picks locations via coordinates/favorites.
- **Acceptance**: Android emulator instrumented full chain + frame-level
  parity against the desktop + kill-restart restore; iOS simulator
  `xcodebuild test` all green (bundle definitions genuinely in effect, no
  host-environment injection). Wiring and acceptance records:
  [mobile native plan](docs/MOBILE_NATIVE_PLAN.md).

## Repository layout

```
firesky/
├── engine/                # Rust engine: HTTP service / CLI / desktop FFI / data sync
├── engine-mobile/         # in-process mobile engine (C ABI fsmobile.h + JNI shim)
├── native/                # Rust ray-traced scorer (cdylib)
├── apps/
│   ├── macos/             # SwiftUI/AppKit + Rust FFI (primary desktop)
│   ├── mobile-android/    # Kotlin/Compose + JNI (APK published with Releases)
│   ├── mobile-ios/        # SwiftUI + statically linked engine (build from Xcode source)
│   ├── windows/           # WinUI 3 + Rust FFI (official installer built on a Windows machine later)
│   ├── linux/             # GTK4/C++ + Rust FFI (code kept, no longer a release target)
│   └── shared-web/        # shared web SPA (served by Rust HTTP and Docker)
├── frontend/              # legacy SvelteKit frontend (pending merge, contract tests kept)
├── resources/app-data/    # bundled city catalog & timezone dataset (sha256 manifest)
├── docker/                # three-stage image (Rust build + web build + Python-free runtime)
├── scripts/               # build/smoke scripts and resource/data preparation tools
└── tests/                 # architecture contract tests (Rust IPC / Swift UI)
```

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `national CPU scorer library not found` | stale standalone-scorer error; the scoring core is merged into the engine — rebuild it (`make engine`) |
| `No cached CAMS optical profile covers ...` | the CAMS cache lacks this cycle's file — warm via the endpoint (needs an ADS key) or copy an existing `.cache/cams` |
| `CAMS full profile does not cover every model cell` | CAMS horizontal area too small — widen the warmup `area` |
| forecast says "GFS cycle unavailable" | NOMADS publish lag is ~5h45m; use an earlier cycle or retry later |
| blank frontend / 404 at `/` | `cd apps/shared-web && npm run build`, then restart the backend |
| 405 Method Not Allowed | a POST to an unknown path hits the GET-only SPA catch-all — check the endpoint spelling |

## Origin

The physics and algorithms follow the methodology of the
[SunsetBot tutorial](https://www.sunsetbot.top/halo/); scoring constants and
structure draw on public discussion of how burning-sunset colours form.

## Acknowledgments

- **[SunsetBot](https://sunsetbot.top/about/)** — the methodology of this
  project comes from its tutorial ([halo chapter](https://www.sunsetbot.top/halo/)):
  atmospheric optics, aerosol scattering and the colour formation of burning
  sunsets, which the scoring model is built upon.
- **Open data & services**: NOAA NOMADS (GFS), ECMWF (open data), Copernicus
  CAMS / ADS (aerosol), JMA / NICT / NOAA (Himawari-9), RainViewer (radar),
  Open-Meteo, OpenStreetMap Nominatim
  (geocoding), AWS Terrain Tiles (elevation), MapLibre / Leaflet (maps).
- **Open-source stack**: the Rust ecosystem (rayon, Axum and friends),
  ecCodes, Svelte/SvelteKit, Leaflet, shadcn-svelte and the lucide icon set;
  astral (solar-formula source, see `third_party/astral/NOTICE`).
- And everyone who has ever looked up at a burning sunset and been willing to
  feed observations back into the data.
