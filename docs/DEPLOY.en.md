# Deployment Guide (DEPLOY)

> Deploying the firesky burning-sunset engine. Physics: [PRINCIPLES.en.md](PRINCIPLES.en.md);
> usage & data-source registration: [README.en.md](../README.en.md). 中文版: [DEPLOY.md](DEPLOY.md)

## 1. Deployment shapes

| Shape | Platforms | Best for | Section |
|---|---|---|---|
| **Docker Compose** (recommended) | Linux / macOS / Windows (Docker Desktop / WSL2) | servers, NAS, always-on desktops — one command | §3 |
| **Bare-metal Linux** + systemd | Linux servers | production, boot-persistent | §4.1 |
| **Bare-metal macOS** + launchd | always-on Macs (Mac mini…) | local device, long-running | §4.2 |
| **Bare-metal Windows** | Windows 10/11 / Server | desktop-resident, ad-hoc runs | §4.3 |
| Development mode | any | local debugging (uvicorn + vite dev) | README "Usage" |

All shapes share the same contract: **one FastAPI process serves both the API
(:8765) and the frontend SPA**; the only stateful thing on disk is the cache
tree. Docker is the low-effort path on every platform; pick bare metal only
when you want minimal footprint or already run the host stack.

## 2. Prerequisites

### 2.1 Common

- Outbound internet (NOMADS / ECMWF / ADS / Himawari / RainViewer / AWC);
- **CAMS**: finish the ADS registration from README "Data sources &
  registration", put the key in the config;
- Disk budget (`df -h`):

| Directory | Contents | Size |
|---|---|---|
| GFS cache | GRIB2 subsets + mmap decode | ~50–200 MB/day rolling |
| Regional results | v125 results + summaries + diagnostics | ~20–100 MB/day |
| Satellite HSD/HCAI | nowcast 15-min × full-disk tiles | **large**: GBs/day — always configure pruning |
| CAMS | optical GRIB | ~50–200 MB/day |
| Map frames / logs | v25 frames / text | small |

### 2.2 Bare-metal toolchain (per platform)

| Tool | Linux | macOS | Windows |
|---|---|---|---|
| Python | ≥3.11 (distro / pyenv) | ≥3.11 (Homebrew / python.org) | ≥3.11 (python.org, tick *Add to PATH*) |
| Rust | rustup script | `brew install rustup` or rustup script | [rustup-init.exe](https://rustup.rs); needs VS C++ Build Tools (linker) |
| Node | ≥20 (nodesource/nvm) | `brew install node@20` | ≥20 (nodejs.org MSI) |
| Make (optional) | built-in | built-in | not recommended; use §4.3 direct commands or WSL2 |
| bash (build script) | built-in | built-in | WSL2/Git Bash only; native Windows uses the two manual steps in §4.3 |

> **Native library naming (per platform, handled automatically)**: the cargo
> artifact is `libfiresky_native.dylib` on macOS, `libfiresky_native.so` on
> Linux, and `firesky_native.dll` on Windows; the loader
> (`src/firesky/native_core.py`) matches per platform, or set
> `FIRESKY_NATIVE_LIBRARY_PATH` explicitly.
> The `eccodes` dependency ships as pip wheels (≥2.37 bundle the binaries) —
> no separate C library needed on any of the three OSes.

## 3. Docker Compose (recommended, cross-platform)

```bash
git clone https://github.com/xyl-g/FireSky.git && cd FireSky
FIRESKY_IMAGE_TAG=local docker compose up -d --build
curl http://127.0.0.1:8765/api/v1/firesky/health     # {"status":"ok"}
```

Windows PowerShell:

```powershell
$env:FIRESKY_IMAGE_TAG = "local"; docker compose up -d --build
```

- `docker/Dockerfile` is three-stage: Rust builds the native scorer → Node
  builds the frontend → python:3.13-slim runtime (apt `libeccodes0`, non-root
  uid 10001);
- Persistent volumes: `firesky-cache`, `firesky-cams`, `firesky-reports`,
  `firesky-logs`;
- The in-container config `docker/web_server.yaml` already points all cache
  dirs to `/data`;
- **Redis is optional**: it only caches the latest-cycle probe for
  `/data/latest/refresh`. Remove the redis service and leave
  `FIRESKY_REDIS_ADDR` empty — every Redis failure degrades gracefully.

Platform notes:

| Platform | Notes |
|---|---|
| Linux server | run as-is; `systemctl enable docker` for boot persistence |
| macOS (Docker Desktop) | give Docker ≥4 GB RAM / 2+ CPUs; keep the repo under a FILE SHARING path (default `~/` works) |
| Windows (Docker Desktop + WSL2) | clone the repo inside the WSL2 filesystem (`\\wsl$\...`); building from an NTFS mount is an order of magnitude slower |

Rebind the port: `FIRESKY_HTTP_PORT=80 docker compose up -d`.

## 4. Bare metal

### 4.0 Common build steps (all OSes)

```bash
# 1) Python deps (eccodes ships as a wheel)
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,cams]"

# 2) Native scorer (Linux/macOS)
./scripts/build_native_variants.sh   # artifacts in native/target/release/

# 3) Frontend
cd frontend && npm ci && npm run build && cd ..

# 4) Smoke run
python -m uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
curl http://127.0.0.1:8765/api/v1/firesky/health   # {"status":"ok"}
```

Config: copy `src/firesky/config/web_server.yaml` to `etc/web_server.yaml`,
point the `*_cache_dir` entries at a data disk and keep `host: 127.0.0.1`
(expose via the reverse proxy); point `FIRESKY_CONFIG=$PWD/etc/web_server.yaml`
at it.

### 4.1 Linux + systemd

```bash
cd /opt/firesky && python3 -m venv .venv
.venv/bin/pip install -e ".[dev,cams]"
./scripts/build_native_variants.sh
cd frontend && npm ci && npm run build && cd ..
```

`/etc/systemd/system/firesky.service`:

```ini
[Unit]
Description=firesky burning-sunset engine
After=network-online.target
Wants=network-online.target

[Service]
User=www-data
WorkingDirectory=/opt/firesky
Environment=FIRESKY_CONFIG=/opt/firesky/etc/web_server.yaml
Environment=FIRESKY_NATIVE_VARIANT=cpu
ExecStart=/opt/firesky/.venv/bin/uvicorn firesky.api:create_app \
    --factory --host 127.0.0.1 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now firesky
```

### 4.2 macOS + launchd

Build as in §4.0 (Homebrew for python/rust/node; the ad-hoc signing
identifier conflict for `libfiresky_native_cpu.dylib` is already handled
inside the build script — no manual codesign needed).

`~/Library/LaunchAgents/top.firesky.server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>top.firesky.server</string>
  <key>WorkingDirectory</key><string>/Users/YOU/firesky</string>
  <key>ProgramArguments</key><array>
    <string>/Users/YOU/firesky/.venv/bin/uvicorn</string>
    <string>firesky.api:create_app</string>
    <string>--factory</string><string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8765</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>FIRESKY_CONFIG</key><string>/Users/YOU/firesky/etc/web_server.yaml</string>
    <key>FIRESKY_NATIVE_VARIANT</key><string>cpu</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/YOU/firesky/logs/uvicorn.log</string>
  <key>StandardErrorPath</key><string>/Users/YOU/firesky/logs/uvicorn.err.log</string>
</dict></plist>
```

```bash
mkdir -p logs
launchctl load ~/Library/LaunchAgents/top.firesky.server.plist   # start + boot persistence
launchctl list | grep firesky                                     # verify
```

### 4.3 Windows

**Option A: run directly in PowerShell (simplest)**

```powershell
git clone https://github.com/xyl-g/FireSky.git; cd FireSky
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,cams]"

# Native scorer: the build script needs bash; on native Windows use the
# equivalent two commands
cd native; cargo build --release
Copy-Item target\release\firesky_native.dll target\release\firesky_native_cpu.dll
cd ..

cd frontend; npm ci; npm run build; cd ..

$env:FIRESKY_CONFIG = "$PWD\etc\web_server.yaml"
python -m uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
```

Copy `src\firesky\config\web_server.yaml` to `etc\web_server.yaml` and point
the cache dirs at a data disk (use raw strings or forward slashes). Open
`http://127.0.0.1:8765` in a browser.

**Option B: auto-start** — Task Scheduler with an "at log on" trigger:
program `.venv\Scripts\python.exe`, arguments
`-m uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765`,
start-in the repo root; or register a Windows service via
[NSSM](https://nssm.cc) (`nssm install firesky ...`) with restart-on-crash.

**Option C: WSL2** — for the Linux toolchain (`make`, the build script,
systemd targets), `wsl --install` then follow §4.1 inside WSL Ubuntu;
Windows browsers reach it at `localhost:8765` (WSL2 port forwarding).

**Windows firewall**: binding to 127.0.0.1 never triggers a firewall prompt;
if you rebind 0.0.0.0 for LAN access, allow it on the private profile on
first start.

## 5. Reverse proxy & scheduled refresh

### nginx (80/443 → 8765)

```nginx
server {
    listen 80;
    server_name firesky.example.com;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_read_timeout 300s;      # job-result polling / SSE
        proxy_buffering off;          # required for SSE
    }
}
```

> SSE: job event streams are long-lived connections —
> `proxy_buffering off` + a 300 s read timeout are required. Terminate TLS
> with certbot or your gateway. Home deployments on macOS/Windows that stay
> off the public internet can skip this section entirely.

### Scheduled refresh after each GFS cycle (cron / schtasks)

NOMADS publishes each cycle with a **~5h45m** lag. Cron examples:

```cron
# 1) every 10 minutes: refresh the latest-published-cycle probe cache
#    (optional; makes /data/status instant)
*/10 * * * *  curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/data/latest/refresh -H 'Content-Type: application/json' -d '{}' >> /var/log/firesky-cycles.log 2>&1

# 2) after each 00/06/12/18Z cycle (+6h and +7h): start the day's regional job.
#    NOTE: /forecasts/national/refresh is a read-only inspection (returns the
#    missing plan, creates no job) — run forecasts via POST /forecasts/national.
0 6,12,18,0 * * *  curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/forecasts/national -H 'Content-Type: application/json' -d "{\"target_date\": \"$(TZ=Asia/Shanghai date +\%F)\", \"phenomenon\": \"dusk\"}" >> /var/log/firesky-refresh.log 2>&1
30 6,12,18,0 * * * curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/forecasts/national -H 'Content-Type: application/json' -d "{\"target_date\": \"$(TZ=Asia/Shanghai date +\%F)\", \"phenomenon\": \"dusk\"}" >> /var/log/firesky-refresh.log 2>&1
```

> `data/latest/refresh` caches into Redis (optional); `forecasts/national` is
> an async job — to avoid concurrent recomputation, poll an existing job with
> `GET /api/v1/firesky/forecasts/results/{job_id}` first.

**Windows**: there is no cron — create two basic tasks in Task Scheduler at
the same times (action: run `.venv\Scripts\curl.exe` with the URLs above;
Windows 10+ ships curl.exe), or use cron inside WSL2.

### Disk hygiene

```cron
30 4 * * * curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/data/prune \
  -H 'Content-Type: application/json' -d '{}' >> /var/log/firesky-prune.log 2>&1
```

(On Windows trigger the same request daily via Task Scheduler.)

## 6. Upgrade flow

1. `git pull`;
2. If `native/src/**` changed: rebuild the native scorer
   (Linux/macOS `./scripts/build_native_variants.sh`; Windows the two cargo
   commands from §4.3 Option A; automatic in container builds);
3. If the frontend changed: `cd frontend && npm ci && npm run build`
   (automatic in container builds);
4. Restart the process; **algorithm/cache-version bumps invalidate old caches
   automatically** (keys embed the algorithm version) — no manual cleanup;
   regional/point results recompute on the next request.

## 7. Security checklist

- Public exposure only via a reverse proxy + HTTPS; keep the backend on
  127.0.0.1;
- `satellite_warmup_token`: satellite/HCAI warmup is expensive — **always**
  set the token for public deployments;
- CORS/rate limiting: no auth by default (intranet product); add gateway-level
  basic auth / rate limits for public use;
- No database, no user system (history features removed) — the only sensitive
  surface is the cache tree content.

## 8. Health checks & monitoring

| Item | Method |
|---|---|
| Liveness | `GET /api/v1/firesky/health` → `{"status":"ok"}` |
| Version contract | `GET /api/v1/firesky/forecasts/national/contract` → algorithm/summary_version/cache_version |
| Logs | container stdout / daily-rotated files under `logging.dir` |
| Cache footprint | `du -sh /data/cache/firesky/* /data/cams` (Explorer folder sizes on Windows) |

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| container exits immediately / `libfiresky_native not found` | check stage-1 build logs; verify `FIRESKY_NATIVE_LIBRARY_PATH` points to an existing library (platform names in §2.2) |
| Windows `cargo build` linker error | install Visual Studio C++ Build Tools (MSVC link.exe), reopen the terminal |
| Windows `pip install eccodes` fails | upgrade pip (`python -m pip install -U pip`) to get the binary-bundled eccodes wheel |
| macOS launchd job not starting | `launchctl list \| grep firesky`; keep literal spaces in plist paths; the `logs/` directory must exist first |
| `/` returns 404 | frontend build missing (bundled in the container; run `npm run build` on bare metal) |
| forecasts say "GFS cycle unavailable" | NOMADS lag ~5h45m — wait or use an older cycle |
| SIGSEGV in kernel logs (mmap) | never delete/replace the mmap decode-cache directory while running; stop the service before upgrades |
