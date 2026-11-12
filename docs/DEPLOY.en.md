# Deployment Guide (DEPLOY)

> Deploying the firesky burning-sunset engine. Physics: [PRINCIPLES.en.md](PRINCIPLES.en.md);
> usage & data-source registration: [README.en.md](../README.en.md). 中文版: [DEPLOY.md](DEPLOY.md)

November 11, 2026: the default service is `firesky-engine http`, without Python.
The [native cutover audit](RUST_RUNTIME_CUTOVER.zh-CN.md) defines implemented
routes, configuration and acceptance limits. Legacy YAML and refresh/warmup
tasks do not automatically work with the native runtime. Sections marked legacy
apply only to an explicitly deployed Python service. Docker, Windows/Linux and
clean-machine release acceptance remain outstanding; launch configuration is
not evidence of platform acceptance.

## 1. Deployment shapes

| Shape | Platforms | Best for | Section |
|---|---|---|---|
| **Docker Compose** (recommended) | Linux / macOS / Windows (Docker Desktop / WSL2) | servers, NAS, always-on desktops — one command | §3 |
| **Bare-metal Linux** + systemd | Linux servers | production, boot-persistent | §4.1 |
| **Bare-metal macOS** + launchd | always-on Macs (Mac mini…) | local device, long-running | §4.2 |
| **Bare-metal Windows** | Windows 10/11 / Server | desktop-resident, ad-hoc runs | §4.3 |
| Development mode | any | local debugging (Rust HTTP + vite dev) | README "Usage" |

One Rust/Axum process serves the API (:8765) and frontend SPA. Jobs are held in
memory and are not restored after restart. Persist caches, reports and credentials.

## 2. Prerequisites

### 2.1 Common

- Outbound internet (NOMADS / ECMWF / ADS / Himawari / RainViewer);
- **CAMS**: finish the ADS registration from README "Data sources &
  registration", put the key in the config;
- Disk budget (`df -h`):

| Directory | Contents | Size |
|---|---|---|
| GFS cache | GRIB2 subsets + mmap decode | ~50–200 MB/day rolling |
| Regional results | native independent result contract + compact frames | measure for your workload |
| Satellite HSD segments | nowcast footprint segments (10-min scans × bands) | medium: ~65 MB per event window — always configure pruning |
| CAMS | optical GRIB | ~50–200 MB/day |
| Map frames / logs | v25 frames / text | small |

### 2.2 Bare-metal toolchain (per platform)

| Tool | Linux | macOS | Windows |
|---|---|---|---|
| Python | not needed for native build/run | not needed for native build/run | not needed for native build/run |
| Rust | rustup script | `brew install rustup` or rustup script | [rustup-init.exe](https://rustup.rs); needs VS C++ Build Tools (linker) |
| Node | ≥20 (nodesource/nvm) | `brew install node@20` | ≥20 (nodejs.org MSI) |
| Make (optional) | built-in | built-in | not recommended; use §4.3 direct commands or WSL2 |
| bash (build script) | built-in | built-in | WSL2/Git Bash only; native Windows uses the two manual steps in §4.3 |

The Rust engine loads C ecCodes and needs its shared library plus
definitions/samples. Use `FIRESKY_ECCODES_LIBRARY`, `ECCODES_DEFINITION_PATH`
and `ECCODES_SAMPLES_PATH` when required. It does not use the Python eccodes
wheel or `native_core.py`. macOS app packaging collects decoder resources;
Windows packaging still needs explicit resources and target-platform acceptance.

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

- Without a local Docker daemon, run `make docker-contract` first. It checks the
  Dockerfile, immutable resource manifest, Compose volumes and dual-architecture
  CI contract without claiming that an image actually ran;
- For a local single-platform image use
  `make docker-buildx FIRESKY_DOCKER_PLATFORMS=linux/amd64` (or `linux/arm64`);
  publish both platforms with
  `FIRESKY_IMAGE_TAG=registry.example/firesky:tag FIRESKY_PUSH=1 make docker-buildx`.
  Docker `--load` cannot import a multi-platform manifest list;
- `docker/Dockerfile` is three-stage: Rust builds the standalone engine → Node
  builds the shared Web UI in `apps/desktop-tauri` → Debian slim runtime (apt `libeccodes0`, non-root
  uid 10001, no Python);
- The image explicitly uses `libeccodes.so.0`, `/usr/share/eccodes/definitions`
  and `/usr/share/eccodes/samples`; Debian's `libeccodes-data` dependency provides
  those data paths;
- Docker and Tauri use the same business pages. Browsers connect through same-origin
  desktop HTTP jobs, without a Tauri shell, server-directory opening or CAMS key writes;
- Persistent volumes: `firesky-cache`, `firesky-cams`, `firesky-reports`,
  `firesky-logs`;
- Dockerfile/Compose environment variables point caches and reports at `/data`;
  legacy Python YAML is not loaded;
- The published port defaults to `127.0.0.1:8765`. On first startup the container
  creates `/data/state/http-token` (`0600`) in the persistent state volume.
  Sign in using username `firesky` and the file contents as the password.
  Read it locally with `docker compose exec firesky cat /data/state/http-token`;
  never paste the output into logs or public discussions. Container recreation
  preserves this password. Pages, business APIs, reports and maps require
  authentication; health checks remain public. API clients can instead send
  `Authorization: Bearer <token>`; query-string tokens are not accepted;
- LAN publishing through `FIRESKY_HTTP_HOST=0.0.0.0` keeps authentication enabled.
  Basic/Bearer authentication does not encrypt transport: use a trusted TLS proxy
  or a private encrypted tunnel across hosts. Do not expose it publicly.
  Preserve Host/Origin at the proxy; forwarded headers never grant authority;
- Redis starts only with `--profile legacy-cache`, not by default. Native `/data/latest/refresh`
  is not wired; do not schedule that endpoint as a native capability.

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
# 1) Standalone engine (also install system ecCodes)
cargo build --manifest-path engine/Cargo.toml --release --locked

# 2) Frontend
cd frontend && npm ci && npm run build && cd ..

# 3) Smoke run
engine/target/release/firesky-engine http --workspace "$PWD" --host 127.0.0.1 --port 8765
# In another terminal:
curl http://127.0.0.1:8765/api/v1/firesky/health   # {"status":"ok"}
```

Use [native environment variables](RUST_RUNTIME_CUTOVER.zh-CN.md#配置), such as
`FIRESKY_GFS_CACHE_DIR`, `FIRESKY_CAMS_CACHE_DIR` and `FIRESKY_REPORT_DIR`.
`FIRESKY_CONFIG` and Python YAML do not configure the native process.
Provide ADS credentials via a permission-protected `~/.cdsapirc` or a file
selected by `CDSAPI_RC`, readable by the service account. Never put keys in
public configuration or command logs.

HTTP listens on loopback by default and checks local Host and same-origin requests
to prevent unrelated websites from triggering downloads, cleanup or computation.
A non-loopback bind requires both `FIRESKY_HTTP_ALLOW_REMOTE=1` and
`FIRESKY_HTTP_TOKEN_FILE` (a service-account-owned `0600` file is recommended), or
`FIRESKY_HTTP_TOKEN`; configuring both token sources is an error. Tokens must
contain 32–4096 ASCII letters, digits or `-_.~` and should be randomly generated.
Errors never include token contents. A configured token also protects loopback
listeners. This is a shared-instance secret, not an account system: its holders
have full instance permissions. Browser-side CAMS credential writes remain disabled.

### 4.1 Linux + systemd

```bash
cd /opt/firesky
cargo build --manifest-path engine/Cargo.toml --release --locked
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
Environment=FIRESKY_CACHE_ROOT=/var/lib/firesky
Environment=FIRESKY_GFS_CACHE_DIR=/var/lib/firesky/gfs
Environment=FIRESKY_ECMWF_CACHE_DIR=/var/lib/firesky/ecmwf
Environment=FIRESKY_CAMS_CACHE_DIR=/var/lib/firesky/cams
Environment=FIRESKY_HSD_CACHE_DIR=/var/lib/firesky/himawari-hsd
Environment=FIRESKY_POINT_CACHE_DIR=/var/lib/firesky/point-forecasts
Environment=FIRESKY_REGIONAL_CACHE_DIR=/var/lib/firesky/regional-forecasts
Environment=FIRESKY_MAP_CACHE_DIR=/var/lib/firesky/map-frames
Environment=FIRESKY_REPORT_DIR=/var/lib/firesky/reports
ExecStart=/opt/firesky/engine/target/release/firesky-engine http --workspace /opt/firesky --host 127.0.0.1 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create `/var/lib/firesky` with write access for `www-data`, install ecCodes and
provide ADS credentials to that account before starting. Health does not prove
forecast inputs are ready.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now firesky
```

### 4.2 macOS + launchd

Build as in §4.0 with Rust, Node and C ecCodes; Python is not needed.
For the native desktop app use `bash scripts/build_macos_native.sh`.
This launchd example runs the HTTP service, not the desktop application.

`~/Library/LaunchAgents/top.firesky.server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>top.firesky.server</string>
  <key>WorkingDirectory</key><string>/Users/YOU/firesky</string>
  <key>ProgramArguments</key><array>
    <string>/Users/YOU/firesky/engine/target/release/firesky-engine</string>
    <string>http</string>
    <string>--workspace</string><string>/Users/YOU/firesky</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8765</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>FIRESKY_ECCODES_LIBRARY</key><string>/ABSOLUTE/PATH/libeccodes.dylib</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/YOU/firesky/logs/engine.log</string>
  <key>StandardErrorPath</key><string>/Users/YOU/firesky/logs/engine.err.log</string>
</dict></plist>
```

Replace the ecCodes placeholder with its actual path and configure
definitions/samples if necessary. launchd does not inherit interactive shell settings.

```bash
mkdir -p logs
launchctl load ~/Library/LaunchAgents/top.firesky.server.plist   # start + boot persistence
launchctl list | grep firesky                                     # verify
```

### 4.3 Windows

**Option A: run directly in PowerShell (simplest)**

```powershell
git clone https://github.com/xyl-g/FireSky.git; cd FireSky
cargo build --manifest-path engine/Cargo.toml --release --locked

cd frontend; npm ci; npm run build; cd ..

$env:FIRESKY_ECCODES_LIBRARY = "C:\ecCodes\bin\eccodes.dll"
$env:ECCODES_DEFINITION_PATH = "C:\ecCodes\share\eccodes\definitions"
$env:ECCODES_SAMPLES_PATH = "C:\ecCodes\share\eccodes\samples"
$env:PATH = "C:\ecCodes\bin;$env:PATH"
.\engine\target\release\firesky-engine.exe http --workspace "$PWD" --host 127.0.0.1 --port 8765
```

Replace ecCodes paths with the actual installation and use §4.0 environment
variables for caches. Open `http://127.0.0.1:8765`. Windows build/run acceptance
still requires testing on the target platform.

**Option B: auto-start** — Task Scheduler with an "at log on" trigger:
program `...\engine\target\release\firesky-engine.exe`, arguments
`http --workspace C:\FireSky --host 127.0.0.1 --port 8765`,
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
    client_max_body_size 1m;

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

### Legacy scheduled refresh reference (Python service only)

This is not a native deployment procedure: `data/latest/refresh` and national
refresh are not wired in the native runtime. Do not copy these old schedules to
the new service. Submit native jobs against the implemented contract after input
preparation and poll their status; restart does not restore jobs.

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
the same times (action: run system `curl.exe` with the URLs above;
Windows 10+ ships curl.exe), or use cron inside WSL2.

### Disk hygiene

```cron
30 4 * * * curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/data/prune \
  -H 'Content-Type: application/json' -d '{}' >> /var/log/firesky-prune.log 2>&1
```

(On Windows trigger the same request daily via Task Scheduler.)

## 6. Upgrade flow

1. `git pull`;
2. If `engine/` or `native/` changed, rebuild the standalone engine with
   `cargo build --manifest-path engine/Cargo.toml --release --locked`
   (automatic in container builds);
3. If the frontend changed: `cd frontend && npm ci && npm run build`
   (automatic in container builds);
4. Restart the process. Native caches validate source/request/input identities;
   legacy Python caches cannot substitute for them. Restart loses jobs and desktop
   sessions; wait for completion or cancel work first.

## 7. Security checklist

- Public exposure only via a reverse proxy + HTTPS; keep the backend on
  127.0.0.1;
- The native runtime has no new remote authentication. Protect download, prune,
  credential and desktop preparation APIs at the gateway; the legacy
  `satellite_warmup_token` does not protect native endpoints;
- CORS/rate limiting: no auth by default (intranet product); add gateway-level
  basic auth / rate limits for public use;
- Protect ADS credentials, reports and cache directories; do not log keys or
  token-bearing URLs.

## 8. Health checks & monitoring

| Item | Method |
|---|---|
| Liveness | `GET /api/v1/firesky/health` → `{"status":"ok"}` |
| Version contract | `GET /api/v1/firesky/runtime` and `/forecasts/national/contract`; native contracts do not impersonate legacy versions |
| Logs | container stdout/stderr, systemd journal or launchd files; legacy `logging.dir` does not configure native logs |
| Cache footprint | `du -sh /data/cache/firesky/* /data/cams` (Explorer folder sizes on Windows) |

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| container exits immediately / ecCodes loading fails | check engine build and `libeccodes0`; on bare metal check the shared library and definitions/samples paths |
| Windows `cargo build` linker error | install Visual Studio C++ Build Tools (MSVC link.exe), reopen the terminal |
| Windows ecCodes loading fails | check DLL architecture, dependent DLL search paths and definitions/samples; native runtime does not use pip eccodes |
| macOS launchd job not starting | `launchctl list \| grep firesky`; keep literal spaces in plist paths; the `logs/` directory must exist first |
| `/` returns 404 | frontend build missing (bundled in the container; run `npm run build` on bare metal) |
| forecasts say "GFS cycle unavailable" | NOMADS lag ~5h45m — wait or use an older cycle |
| SIGSEGV in kernel logs (mmap) | never delete/replace the mmap decode-cache directory while running; stop the service before upgrades |
