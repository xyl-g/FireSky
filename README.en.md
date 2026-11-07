# firesky

An independent, self-contained burning-sunset (sunset/sunrise glow) prediction
stack: **prediction engine (Python + Rust ray tracing) + FastAPI backend +
minimal SvelteKit frontend**. The physics follows the methodology of the
[SunsetBot tutorial](https://www.sunsetbot.top/halo/).

> 中文文档：[README.md](README.md)

---

## Contents

1. [Features](#features)
2. [Architecture & physics](#architecture--physics)
3. [Requirements & installation](#requirements--installation)
4. [Data sources & registration (important)](#data-sources--registration)
5. [Configuration](#configuration)
6. [Usage: backend / frontend / CLI / Python API](#usage)
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
- **Backend service**: FastAPI async jobs + SSE + gzip + persistent caches
  (point / regional / map frames / satellite).
- **Minimal frontend**: `/forecast`, `/now`, `/outlook`, `/admin`.

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

   Algorithm version `2026.11.04.87` (`src/firesky/version.py`). Full derivation:
   [PRINCIPLES](docs/PRINCIPLES.en.md).

## Requirements & installation

| Dependency | Version | Purpose | Required? |
|---|---|---|---|
| Python | ≥ 3.11 | engine + backend | ✅ |
| Rust (cargo) | stable ≥ 1.75 | native scorer build | ✅ |
| Node.js + npm | ≥ 20 | frontend build | frontend only |
| numpy / requests / astral / eccodes | see `pyproject.toml` | numerics / downloads / solar geometry / GRIB2 decode | ✅ |
| fastapi / uvicorn / pyyaml / httpx | same | backend service | ✅ |
| pillow / h5py | same | radar PNG / satellite HDF5 | ✅ |
| cdsapi | ≥ 0.7 | **CAMS downloads** (registration required, see below) | only to auto-download CAMS |
| ecCodes binary library | ≥ 2.33 | native library behind the eccodes Python package (`brew install eccodes` / apt `libeccodes-data`) | ✅ |

```bash
# 1) Engine + backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,cams,nowcast,ec]"
./scripts/build_native_variants.sh        # builds libfiresky_native(_cpu) dylib

# 2) Frontend
cd frontend && npm install && npm run build && cd ..

# 3) Start the backend (serves the frontend SPA, default 127.0.0.1:8765)
uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
# open http://127.0.0.1:8765
```

## Data sources & registration

**CAMS (Copernicus Atmosphere Data Store) is the only source that requires
registration.** Everything else is public and key-free.

| Source | Used for | Registration / key | Free tier |
|---|---|---|---|
| **CAMS global atmospheric composition forecasts** (ADS) | aerosol AOD550 + 11-level extinction (drives glow colour and clarity) | ⚠️ **Registration required**: ① register at [ADS](https://ads.atmosphere.copernicus.eu); ② accept the `cams-global-atmospheric-composition-forecasts` dataset licence on your profile page; ③ create an API key and write it to `~/.cdsapirc` (standard CDS format: `url: https://ads.atmosphere.copernicus.eu/api` + `key: <YOUR-KEY>`); ④ `pip install cdsapi` | free, queued |
| GFS (NOAA NOMADS) | primary NWP model (cloud/RH/temperature/wind/boundaries) | none (public GRIB2); keep a polite User-Agent | free |
| ECMWF open data | second model (`model_source: ec`) | none (public S3/GCS buckets) | free |
| Himawari-9 (NOAA S3 + NICT + JMA) | nowcast satellite cloud columns (HSD segments) | none (public buckets) | free |
| RainViewer | nowcast weather radar | none (public API) | free (non-commercial) |
| Open-Meteo | place-name search / air-quality AOD fallback | none (non-commercial) | free |
| OSM Nominatim | place-name search | none; a User-Agent is required (1 req/s) | free |
| AWS terrarium elevation tiles | observer elevation | none | free |

`~/.cdsapirc` example:

```ini
url = https://ads.atmosphere.copernicus.eu/api
key = YOUR-API-KEY
```

Once CAMS access works, warm the cache either way:

```bash
# Option A: the data-download planner (async job; the cams source uses
# cdsapi and requires an ADS key)
curl -X POST http://127.0.0.1:8765/api/v1/firesky/data/fetch \
  -H 'Content-Type: application/json' \
  -d '{"target_date": "2026-11-05", "phenomenon": "dusk", "region": "china", "sources": ["cams"]}'
# poll: GET /api/v1/firesky/data/fetch/status?job_id=...

# Option B: reuse an already-downloaded cache (the file format is
# identical): copy/symlink an existing CAMS cache into this project.
```

> The engine itself only **reads** the CAMS cache (fail-closed — it raises a
> clear error instead of inventing AOD when the cache is missing). Downloads
> happen through the backend warmup endpoint (cdsapi) or whatever you prepare.
> `satellite_warmup_token` is a local admin token configured in the YAML — it
> is unrelated to any third-party registration.

## Configuration

Config file: `src/firesky/config/web_server.yaml`; point the env var
`FIRESKY_CONFIG` at your own copy to override.

| Field | Default | Meaning |
|---|---|---|
| `server.host` / `server.port` | 127.0.0.1 / 8765 | backend bind |
| `server.workers` | null | parallel prediction processes (null = auto) |
| `server.aerosol_source` | cams | `auto` / `cams` / `fallback` (fallback = Open-Meteo air-quality AOD when CAMS cache is missing) |
| `server.*_cache_dir` | `.cache/firesky/*` | GFS / ECMWF / CAMS / map-frame / regional / point / satellite cache dirs |
| `server.satellite_warmup_token` | null | local token guarding satellite warmup admin endpoints |
| `server.forecast_cache_seconds` | 300 | in-memory forecast response cache TTL |
| `logging.*` | INFO / logs / firesky | log level / dir / app name |

## Usage

### Backend + frontend (recommended)

```bash
uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
# with the frontend built: open http://127.0.0.1:8765/forecast
# frontend dev mode: cd frontend && npm run dev -- --host 5173 (API proxied to 8765)
```

### CLI (engine only, no server)

```bash
python -m firesky predict --lat 30.27 --lon 120.15 --date 2026-11-05 \
    --phenomenon dusk --cams-cache-dir .cache/cams
```

### Python API

```python
from datetime import datetime, timezone
from firesky import predict_burn_sky

result = predict_burn_sky(
    [{"name": "Hangzhou", "lat": 30.27, "lon": 120.15, "elevation_m": 12.0,
      "timezone_name": "Asia/Shanghai"}],
    "2026-11-05",
    "dusk",
    datetime(2026, 11, 4, 12, tzinfo=timezone.utc),   # GFS cycle
    cams_cache_dir=".cache/cams",
)
print(result["points"][0]["peak"]["quality_level"])
```

## API endpoints

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
| `/api/v1/firesky/satellite/cache/warmup` | POST | satellite cache warmup (requires `satellite_warmup_token`, header `x-firesky-warmup-token`) |
| `/{full_path}` | GET | SPA fallback (frontend client routing) |

## Testing & invariant regression

```bash
.venv/bin/python -m pytest tests/ -q          # 29 passed
cd frontend && npm run check && npm run build # 0 errors
```

- `tests/test_engine_api.py`: `predict_burn_sky` input validation and the exposed
  algorithm version.
- `tests/test_scoring_invariants.py`: unified 20-minute window (short-run
  equality / boundary continuity), LCL cloud base, NaN level guard, monotonic
  response curve.
- `tests/test_backend_api.py`: service contract (health / contract / async-job
  failure semantics).
- After changing `native/src/*.rs`, rebuild with
  `./scripts/build_native_variants.sh` before running pytest — otherwise pytest
  loads the stale dylib and reports misleading results.

## Repository layout

```
firesky/
├── src/firesky/           # engine + backend (Python)
│   ├── engine.py          # high-level API predict_burn_sky
│   ├── api.py             # FastAPI production entry
│   ├── web_server.py      # service core (jobs / caches / payload assembly)
│   ├── national_batch.py  # regional grid derivation + native calls + quality
│   ├── satellite*.py / radar.py                  # nowcast data & retrieval
│   ├── gfs.py / ecmwf*.py / aerosol.py           # data ingestion
│   ├── scoring.py / forward_field_scoring.py     # physics + empirical scoring
│   ├── native_core.py     # ctypes bridge (libfiresky_native)
│   └── config/web_server.yaml                    # service config
├── native/                # Rust ray-traced scorer (cdylib)
├── frontend/              # minimal SvelteKit frontend (served by the backend)
├── scripts/build_native_variants.sh
└── tests/
```

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `national CPU scorer library not found` | run `./scripts/build_native_variants.sh` first |
| `No cached CAMS optical profile covers ...` | the CAMS cache lacks this cycle's file — warm via the endpoint (needs an ADS key) or copy an existing `.cache/cams` |
| `CAMS full profile does not cover every model cell` | CAMS horizontal area too small — widen the warmup `area` |
| forecast says "GFS cycle unavailable" | NOMADS publish lag is ~5h45m; use an earlier cycle or retry later |
| blank frontend / 404 at `/` | `cd frontend && npm run build`, then restart the backend |
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
- **Open-source stack**: the Rust ecosystem (rayon and friends), numpy,
  astral, ecCodes, FastAPI, uvicorn, SvelteKit, Leaflet, shadcn-svelte and the
  lucide icon set.
- And everyone who has ever looked up at a burning sunset and been willing to
  feed observations back into the data.
