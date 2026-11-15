# Configuration Reference

> Every configuration surface of firesky: YAML files, environment variables,
> precedence rules. 中文版：[CONFIGURATION.md](CONFIGURATION.md)

## 1. Files & precedence

| Layer | Location | Notes |
|---|---|---|
| Explicit | `load_settings(path)` / python call | highest |
| Env var | `FIRESKY_CONFIG=/path/to/your.yaml` | next |
| Bundled default | `src/firesky/config/web_server.yaml` | fallback (server boots even if missing) |

The container image ships `docker/web_server.yaml` (cache dirs under the
`/data` volume), activated via `FIRESKY_CONFIG=/app/etc/web_server.yaml`.

Verify the effective config: `make contract` (algorithm/cache_version) plus
the startup log lines.

## 2. `web_server.yaml` — every field

```yaml
server:
  host: 127.0.0.1            # bind address; keep 127.0.0.1 behind a reverse proxy
  port: 8765                 # API + SPA on one port
  workers: null              # parallel prediction processes; null = auto (≤ physical cores recommended)
  aerosol_source: cams       # auto | cams | fallback
                             #   cams = CAMS cache only (clear error when missing)
                             #   fallback = Open-Meteo air-quality AOD when CAMS cache is missing (degraded, flagged in responses)
  report_dir: reports/web    # HTML diagnostic reports
  gfs_cache_dir: .cache/firesky/gfs                # GRIB2 + mmap decode cache
  ecmwf_cache_dir: .cache/firesky/ecmwf            # EC open-data cache
  map_cache_dir: .cache/firesky/map-frames         # v25 map frames
  regional_cache_dir: .cache/firesky/regional-forecasts   # v125 regional results + summaries
  point_cache_dir: .cache/firesky/point-forecasts  # point-forecast cache
  satellite_cache_dir: .cache/firesky/himawari-hsd        # HSD compact cache (large — configure pruning)
  satellite_warmup_token: null  # local admin token for satellite warmup endpoints; required for public deploys
  forecast_cache_seconds: 300   # in-memory forecast response TTL
  regional_refresh_payload: null  # optional fixed regional-refresh args

logging:
  level: INFO           # DEBUG | INFO | WARNING | ERROR
  dir: logs             # daily rotation
  app_name: firesky
  when: midnight        # rotation trigger
  backup_count: 14      # retained days
```

## 3. Environment variables (`FIRESKY_*`)

### 3.1 Core runtime

| Variable | Default | Meaning |
|---|---|---|
| `FIRESKY_CONFIG` | bundled yaml | config file path (§1) |
| `FIRESKY_NATIVE_LIBRARY_PATH` | auto-search | explicit dylib path (required inside the Docker image) |
| `FIRESKY_NATIVE_VARIANT` | auto | `cpu` / `gpu` / `cpu-pgo` — variant dylib selection |
| `FIRESKY_COMPUTE_BACKEND` | auto | `auto` / `cpu` / `gpu` — requests may also override per payload |
| `FIRESKY_REDIS_ADDR` / `_PASSWORD` / `_DB` / `_TTL_SECONDS` | empty (off) | optional Redis: only caches the `/data/latest/refresh` cycle probe; every failure degrades gracefully |

### 3.2 Downloads & concurrency

| Variable | Default | Meaning |
|---|---|---|
| `FIRESKY_GFS_DOWNLOAD_WORKERS` | 4 | NOMADS concurrent downloads |
| `FIRESKY_GFS_DOWNLOAD_ATTEMPTS` | 4 | retries per file |
| `FIRESKY_GFS_DECODE_WORKERS` | 2 | GRIB2 decode threads |
| `FIRESKY_ECMWF_DOWNLOAD_WORKERS` | 4 | EC download concurrency |
| `FIRESKY_REGIONAL_DERIVE_WORKERS` | auto | regional column-derivation processes |
| `FIRESKY_NATIONAL_BATCH_MIN_POINTS` | 64 | minimum points for the shared regional batch |
| `FIRESKY_DIAGNOSTIC_COMPRESSION_LEVEL` | 3 | diagnostic bundle zlib level (0–9) |

### 3.3 Native scorer knobs (performance / debugging — leave defaults)

All optional; defaults are production values. The authoritative list:
`grep -rhoE 'FIRESKY_[A-Z0-9_]+' engine/src | sort -u`.

| Group | Representative variables | Purpose |
|---|---|---|
| Temporal cache mode | `FIRESKY_NATIVE_EXACT_TEMPORAL_KEYS` (exact bit-level keys), `FIRESKY_NATIVE_CONSISTENT_TEMPORAL_KEYS` (self-consistent 120 s buckets, guarantees left+right==span); default `Quantized120` when neither is set | temporal-key quantization for the shared motion / layer caches. Default and consistent are explicit approximations (<=60 s per side); exact keys auto-disable the shared dense table when the key count exceeds its budget. See the performance roadmap "11-14 follow-up" section |
| Shared path caches | `FIRESKY_NATIVE_SHARED_SOLAR_PATH_SAMPLE_MIN_OBSERVERS` (2048), `..._MAX_BYTES` (8 GiB cap), `DISABLE_SHARED_SOLAR_PATH_SAMPLE_CACHE` | solar-path templates shared above the observer threshold; use DISABLE_* to shed memory |
| Shared geometry caches | `DISABLE_SHARED_MOTION_CACHE` / `DISABLE_SHARED_TERRAIN_CACHE` / `DISABLE_SHARED_WIND_CACHE` / `DISABLE_SHARED_AEROSOL_COLUMN_CACHE` + `*_MAX_BYTES` | cross-observer advection wind / terrain / aerosol columns |
| Multi-bearing legs | `DISABLE_MULTI_BEARING_PATH_CACHE` / `ENABLE_MULTI_BEARING_PATH_AEROSOL_SAMPLE_CACHE` / `MULTI_BEARING_PATH_MIN_OBSERVERS` | observer-return multi-bearing expansion |
| GPU (optional feature) | `FIRESKY_GPU_REDUCTION_MIN_TARGETS` (2048), `GPU_MIN_FEATURES`, `DISABLE_NATIONAL_GPU_*`, `NATIONAL_GPU_*_MAX_BYTES`, `ENABLE_NATIONAL_PROFILE_GPU` | only meaningful with `--features gpu`; ignore in the CPU minimal set |
| NEON | `FIRESKY_NATIVE_DISABLE_NEON_BLOCKING` (NEON on by default) | aarch64 blocking vectorisation switch |
| Diagnostics | `FIRESKY_NATIVE_GPU_TIMING` / `GPU_PARITY_DUMP` / `CANONICAL_PATH_DIAG` / `DISABLE_ANGULAR_FOOTPRINT_CACHE` … | debugging / validation |

> Note: the `*_MAX_BYTES` caps above (e.g. shared path 8 GiB, shared motion 3 GiB) bound a
> **single cache structure**, not total process memory. The accepted process peak physical
> footprint is ~4 GiB including worker caches and outputs; acceptance must measure physical
> footprint, not a single cache cap or RSS.

## 4. `pyproject.toml` extras

```bash
pip install -e .              # engine + backend
pip install -e ".[dev]"       # + pytest
pip install -e ".[cams]"      # + cdsapi (CAMS downloads, needs an ADS key)
```

## 5. Frontend configuration (`apps/shared-web/`)

> Updated 2026-11-13: the shared Web SPA now lives in `apps/shared-web/`;
> `make frontend`, Docker and the engine's default static root all build that
> package. The old `frontend/` (SvelteKit) remains in the tree pending a merge
> and is no longer the default frontend.

- API base: dev mode uses the vite proxy (`/api/v1/firesky` → `127.0.0.1:8765`);
  production is same-origin served — nothing to configure;
- The build output `apps/shared-web/dist` is served by the backend's SPA
  fallback (override with `FIRESKY_WEB_ROOT`).
