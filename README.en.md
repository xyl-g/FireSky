# firesky

An independent, self-contained burning-sunset (sunset/sunrise glow) prediction
stack: **native Rust prediction engine and HTTP service + SvelteKit frontend +
native desktop clients**. The physics follows the methodology of the
[SunsetBot tutorial](https://www.sunsetbot.top/halo/).

> 中文文档：[README.md](README.md)

---

## Contents

1. [Features](#features)
2. [Architecture & physics](#architecture--physics)
3. [Requirements & installation](#requirements--installation)
4. [Data sources & registration (important)](#data-sources--registration)
5. [Configuration](#configuration)
6. [Usage: backend / frontend / CLI](#usage)
7. [API endpoints](#api-endpoints)
8. [Testing & invariant regression](#testing--invariant-regression)
9. [Repository layout](#repository-layout)
10. [Troubleshooting](#troubleshooting)
11. [Principles (deep dive)](docs/PRINCIPLES.en.md)
12. [Deployment guide](docs/DEPLOY.en.md)
13. [Configuration reference](docs/CONFIGURATION.en.md)
14. [Acknowledgments](#acknowledgments)

## Features

- **Point forecasts**: per-five-minute burning-sky quality ledger for one
  observer (index + level labels: from "no glow" to "once-in-a-lifetime burn").
- **Regional forecasts**: batch observer scoring over China/East-Asia regions
  with map frames and atmospheric cross-sections.
- **Nowcast**: Himawari-9 satellite (HSD segments) cloud-column retrieval + weather
  radar convective correction.
- **Backend service**: Rust/Axum jobs + SSE + gzip + checked input caches.
- **Minimal frontend**: `/forecast`, `/now`, `/outlook`, `/admin`.
- **Native desktop**: macOS SwiftUI/AppKit and Windows WinUI 3, directly launching Rust. See [build instructions and migration status](apps/README.md). The old PySide6 app was retired (2026-11-13); user data (~/.firesky-desktop favorites and current spot) imports automatically into the native app.

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

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/firesky/health` | GET | health check |
| `/api/v1/firesky/forecasts/national/contract` | GET | regional algorithm/cache-version contract |
| `/api/v1/firesky/forecasts` | POST | start a point-forecast job (async) |
| `/api/v1/firesky/forecasts/results/{job_id}` | GET | fetch job result (failures surface as 400/500) |
| `/api/v1/firesky/forecasts/national` | POST | start a regional forecast job |
| `/api/v1/firesky/forecasts/nowcast` | POST | start a nowcast job (satellite + radar) |
| `/api/v1/firesky/map-frames` | GET | map frames (token + time) |
| `/api/v1/firesky/forecasts/national/summary` | POST | regional summary (strict field contract) |
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
- After changing `native/src/*.rs`, rebuild with
  `./scripts/build_native_variants.sh` before running tests — otherwise they
  load the stale dylib and report misleading results.

## Desktop app (local, single-machine)

Current native clients launch the Rust engine directly:
[macOS SwiftUI / Windows WinUI](apps/README.md). The Tauri desktop shell and
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

## Repository layout

```
firesky/
├── engine/                # Rust engine: HTTP service / CLI / desktop FFI / data sync
├── native/                # Rust ray-traced scorer (cdylib)
├── apps/
│   ├── macos/             # SwiftUI/AppKit + Rust FFI (primary desktop)
│   ├── windows/           # WinUI 3 + Rust FFI
│   ├── linux/             # GTK4/C++ + Rust FFI
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
| `national CPU scorer library not found` | run `./scripts/build_native_variants.sh` first |
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
