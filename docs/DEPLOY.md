# 部署文档（DEPLOY）

> firesky 的部署指南。原理见 [PRINCIPLES.md](PRINCIPLES.md)，
> 使用与数据源注册见 [README.md](../README.md)。English: [DEPLOY.en.md](DEPLOY.en.md)

## 1. 部署形态总览

| 形态 | 适用平台 | 适用场景 | 本文档章节 |
|---|---|---|---|
| **Docker Compose**（推荐） | Linux / macOS / Windows（Docker Desktop / WSL2） | 服务器、NAS、桌面长驻，一条命令起全套 | §3 |
| **裸机 Linux** + systemd | Linux 服务器 | 生产常驻、开机自启 | §4.1 |
| **裸机 macOS** + launchd | Mac mini 等常驻 Mac | 本地设备长驻 | §4.2 |
| **裸机 Windows** | Windows 10/11 / Server | 桌面常驻、临时跑 | §4.3 |
| 开发模式 | 任意平台 | 本机调试（uvicorn + vite dev） | README「使用」 |

所有形态共用同一约定：**一个 FastAPI 进程同时提供 API（:8765）与前端 SPA 静态
托管**；磁盘上唯一有状态的东西是缓存目录。Docker 是覆盖三平台的省事路径；
只在需要极致资源占用或已有主机环境时选裸机。

## 2. 前置条件

### 2.1 通用条件

- 机器能出网（NOMADS / ECMWF / ADS / Himawari / RainViewer）；
- **CAMS**：按 README「数据源与注册」完成 ADS 注册，把 key 写进配置；
- 磁盘预算（`df -h` / 资源管理器自查）：

| 目录 | 内容 | 量级 |
|---|---|---|
| GFS 缓存 | GRIB2 子集 + mmap 解码 | ~50–200 MB/天滚动 |
| 区域结果 | v125 结果 + 摘要 + 诊断 | ~20–100 MB/天 |
| 卫星 HSD 分段 | nowcast 足迹分段（10 分钟扫描 × 多波段） | 中：~65 MB/事件窗口，务必配 prune |
| CAMS | 光学 GRIB | ~50–200 MB/天 |
| 地图帧/日志 | v25 帧 / 文本 | 小 |

### 2.2 裸机工具链（按平台）

| 工具 | Linux | macOS | Windows |
|---|---|---|---|
| Python | ≥3.11（系统包/ pyenv） | ≥3.11（Homebrew / python.org） | ≥3.11（python.org，勾选 *Add to PATH*） |
| Rust | `rustup` 官方脚本 | `brew install rustup` 或 rustup 脚本 | [rustup-init.exe](https://rustup.rs)；需 VS C++ Build Tools（链接器） |
| Node | ≥20（nodesource/nvm） | `brew install node@20` | ≥20（nodejs.org MSI） |
| Make（可选） | 自带 | 自带 | 不建议；用 §4.3 的直接命令或 WSL2 |
| bash（构建脚本用） | 自带 | 自带 | 仅 WSL2/Git Bash 有；原生 Windows 走 §4.3 手动两步 |

> **原生库命名（平台差异，自动处理）**：cargo 产物在 macOS 是
> `libfiresky_native.dylib`、Linux 是 `libfiresky_native.so`、Windows 是
> `firesky_native.dll`；加载器（`src/firesky/native_core.py`）按平台自动
> 匹配，也可用 `FIRESKY_NATIVE_LIBRARY_PATH` 显式指定。
> `eccodes` 依赖走 pip 轮子（≥2.37 自带二进制），三平台无需另装 C 库。

## 3. Docker Compose（推荐，跨平台）

```bash
git clone https://github.com/xyl-g/FireSky.git && cd FireSky
FIRESKY_IMAGE_TAG=local docker compose up -d --build
curl http://127.0.0.1:8765/api/v1/firesky/health     # {"status":"ok"}
```

Windows PowerShell 下把环境变量写在前面的写法换成：

```powershell
$env:FIRESKY_IMAGE_TAG = "local"; docker compose up -d --build
```

- `docker/Dockerfile` 三阶段：Rust 编译原生评分器 → Node 构建前端 →
  python:3.13-slim 运行时（apt 装 `libeccodes0`，非 root 用户 10001 运行）；
- 持久卷：`firesky-cache`（模式/区域/帧缓存）、`firesky-cams`、
  `firesky-reports`、`firesky-logs`；
- 容器内配置 `docker/web_server.yaml` 已把缓存目录指到 `/data`；
- **Redis 可选**：只用于 `/data/latest/refresh` 的最新周期探测缓存；
  不需要就删掉 compose 里的 redis 服务并把 `FIRESKY_REDIS_ADDR` 留空——
  所有 Redis 失败都会优雅降级为实时探测。

平台注意：

| 平台 | 注意点 |
|---|---|
| Linux 服务器 | 直接跑；`systemctl enable docker` 保证开机自启 |
| macOS（Docker Desktop） | 给 Docker 分配 ≥4 GB 内存 / 2+ CPU；把仓库放进 Docker FILE SHARING 允许的路径（默认 `~/` 下即可） |
| Windows（Docker Desktop + WSL2） | 仓库放进 WSL2 文件系统（`\\wsl$\...` 或直接在 WSL 里 clone）构建快一个量级；NTFS 挂载路径构建会慢 |

端口改绑：`FIRESKY_HTTP_PORT=80 docker compose up -d`。

## 4. 裸机部署

### 4.0 三平台通用构建步骤

```bash
# 1) Python 依赖（含 eccodes 轮子）
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,cams,nowcast,ec]"

# 2) Rust 原生评分器（Linux/macOS）
./scripts/build_native_variants.sh   # 产物在 native/target/release/

# 3) 前端
cd frontend && npm ci && npm run build && cd ..

# 4) 试跑
python -m uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
curl http://127.0.0.1:8765/api/v1/firesky/health   # {"status":"ok"}
```

配置：复制 `src/firesky/config/web_server.yaml` 到 `etc/web_server.yaml`，
把 `*_cache_dir` 指到数据盘，`host: 127.0.0.1`（由反代对外），再用环境变量
`FIRESKY_CONFIG=$PWD/etc/web_server.yaml` 指向它。

### 4.1 Linux + systemd

```bash
cd /opt/firesky && python3 -m venv .venv
.venv/bin/pip install -e ".[dev,cams,nowcast,ec]"
./scripts/build_native_variants.sh
cd frontend && npm ci && npm run build && cd ..
```

`/etc/systemd/system/firesky.service`：

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

构建同 §4.0（Homebrew 装 python/rust/node；首次构建后 macOS 会对
`native/target/release/libfiresky_native_cpu.dylib` 自动 ad-hoc 重签，构建
脚本里已处理签名标识冲突问题，无需手动操作）。

`~/Library/LaunchAgents/top.firesky.server.plist`：

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
launchctl load ~/Library/LaunchAgents/top.firesky.server.plist   # 开机自启
launchctl list | grep firesky                                     # 确认在跑
```

### 4.3 Windows

**方式 A：PowerShell 直接跑（最简单）**

```powershell
git clone https://github.com/xyl-g/FireSky.git; cd FireSky
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,cams,nowcast,ec]"

# 原生评分器：构建脚本依赖 bash，Windows 原生环境用等价的两条命令
cd native; cargo build --release
Copy-Item target\release\firesky_native.dll target\release\firesky_native_cpu.dll
cd ..

cd frontend; npm ci; npm run build; cd ..

$env:FIRESKY_CONFIG = "$PWD\etc\web_server.yaml"
python -m uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
```

复制 `src\firesky\config\web_server.yaml` 到 `etc\web_server.yaml` 并把
缓存目录改到数据盘（路径用原始字符串或正斜杠）。浏览器开
`http://127.0.0.1:8765`。

**方式 B：开机自启**——用「任务计划程序」建一个"登录时触发"的任务，
程序填 `.venv\Scripts\python.exe`，参数
`-m uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765`，
起始于仓库根目录；或用 [NSSM](https://nssm.cc) 注册为 Windows 服务
（`nssm install firesky ...`），带崩溃自动重启。

**方式 C：WSL2**——想要 Linux 工具链（`make`、构建脚本、systemd 目标）
就 `wsl --install` 后在 WSL Ubuntu 里按 §4.1 走；对 Windows 浏览器暴露
`localhost:8765`（WSL2 自动端口转发）。

**Windows 防火墙**：只监听 127.0.0.1 时不会弹防火墙询问；若改绑
0.0.0.0 对局域网服务，首次启动放行专用网络即可。

## 5. 反向代理与定时刷新

### nginx（对外 80/443 → 8765）

```nginx
server {
    listen 80;
    server_name firesky.example.com;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_read_timeout 300s;      # 预测 job 结果轮询/SSE
        proxy_buffering off;          # SSE 必须关缓冲
    }
}
```

> SSE：预测 job 事件流是长连接，`proxy_buffering off` + 300s 读超时是必须的；
> HTTPS 证书用 certbot 或你的网关。Windows/macOS 家庭部署不对外时，可跳过
> 本节，直接内网访问。

### GFS 周期后自动刷新（cron / schtasks）

NOMADS 每个周期约延迟 **5h45m** 发布。用 cron 在发布后触发区域刷新
（可选 redis 让 `/data/status` 秒回）：

```cron
# 1) 每 10 分钟：刷新"最新已发布周期"探测缓存（可选，让 /data/status 秒回）
*/10 * * * *  curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/data/latest/refresh -H 'Content-Type: application/json' -d '{}' >> /var/log/firesky-cycles.log 2>&1

# 2) 每天 00/06/12/18Z 周期发布后（+6h 与 +7h 各试一次）启动当日区域预测 job
#    注意：/forecasts/national/refresh 是只读巡检（返回缺失计划，不创建 job）；
#    真正跑预测用 POST /forecasts/national。
0 6,12,18,0 * * *  curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/forecasts/national -H 'Content-Type: application/json' -d "{\"target_date\": \"$(TZ=Asia/Shanghai date +\%F)\", \"phenomenon\": \"dusk\"}" >> /var/log/firesky-refresh.log 2>&1
30 6,12,18,0 * * * curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/forecasts/national -H 'Content-Type: application/json' -d "{\"target_date\": \"$(TZ=Asia/Shanghai date +\%F)\", \"phenomenon\": \"dusk\"}" >> /var/log/firesky-refresh.log 2>&1
```

> `data/latest/refresh` 的结果缓存进 Redis（可选）；`forecasts/national` 是
> 异步 job——想串行避免并发重算，可在 cron 里先查
> `GET /api/v1/firesky/forecasts/results/{job_id}` 的既有 job 状态。

**Windows**：没有 cron，用「任务计划程序」按同样时刻建两个基本任务
（操作：启动 `.venv\Scripts\curl.exe`，参数同上 URL；Windows 10+ 自带
curl.exe），或在 WSL2 里用 cron。

### 实况（nowcast）卫星缓存预热

实况请求**只读缓存**（fail-closed：无新鲜帧直接报"卫星观测缺失"，不阻塞
下载）。线上必须用调度器持续预热：

1. 配置 `satellite_warmup_token`（yaml；公网部署**必须**设强随机值）；
2. cron 每 10 分钟预热一帧（与 Himawari 扫描节奏一致）：

```cron
*/10 * * * * curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/satellite/cache/warmup \
  -H 'Content-Type: application/json' -H "x-firesky-warmup-token: $TOKEN" \
  -d '{"lat": 30.27, "lon": 120.15, "radius_km": 900, "reference_time_utc": "'"$(
    TZ=UTC date -u +%Y-%m-%dT%H:%M:00Z)"'"}' >> /var/log/firesky-warmup.log 2>&1
```

（lat/lon 换成你的观测点；多点位各自一条。）预热一帧约 20–40s /
20MB（HSD 分段足迹）；实况自身只在事件 ±4h 内可请求。

### 磁盘卫生

```cron
30 4 * * * curl -fsS -X POST http://127.0.0.1:8765/api/v1/firesky/data/prune \
  -H 'Content-Type: application/json' -d '{}' >> /var/log/firesky-prune.log 2>&1
```

（Windows 同上用任务计划程序每天触发一次。）

## 6. 升级流程

1. `git pull`；
2. 若 `native/src/**` 有改动：重新编译原生库
   （Linux/macOS `./scripts/build_native_variants.sh`；Windows 按 §4.3
   方式 A 的两条 cargo 命令；容器构建自动做）；
3. 若前端改动：`cd frontend && npm ci && npm run build`（容器构建自动做）；
4. 重启进程；**算法/缓存版本 bump 时旧缓存自动失效**（key 含算法版本），
   无需手动清理；区域/点结果会在下次请求时按新版本重算。

## 7. 安全清单

- 对公网暴露时务必走反代 + HTTPS；后端只监听 127.0.0.1；
- `satellite_warmup_token`：卫星预热是重操作，公网部署**必须**设置令牌；
- CORS/限流：默认无鉴权（内网产品定位）；公网建议网关层加 basic auth/限流；
- 无数据库、无用户系统——泄露面只有缓存目录内容。

## 8. 健康检查与监控

| 项 | 方法 |
|---|---|
| 存活 | `GET /api/v1/firesky/health` → `{"status":"ok"}` |
| 版本契约 | `GET /api/v1/firesky/forecasts/national/contract` → algorithm/summary_version/cache_version |
| 日志 | 容器 stdout / `logging.dir` 下按天切割 |
| 缓存水位 | `du -sh /data/cache/firesky/* /data/cams`（Windows 用资源管理器看目录大小） |

## 9. 排障

| 症状 | 处理 |
|---|---|
| 容器启动即退 / `libfiresky_native not found` | 检查构建日志 stage1 是否成功；`FIRESKY_NATIVE_LIBRARY_PATH` 是否指向存在的库文件（平台名见 §2.2） |
| Windows `cargo build` 报链接器错误 | 安装 Visual Studio C++ Build Tools（MSVC link.exe），重开终端再试 |
| Windows `pip install eccodes` 失败 | 升级 pip（`python -m pip install -U pip`）以获得自带二进制的 eccodes 轮子 |
| macOS launchd 未自启 | `launchctl list \| grep firesky` 看状态；路径含空格需在 plist 里保持原样；`logs/` 目录必须先建好 |
| `/` 404 | 前端 build 缺失（容器内已内置；裸机跑 `npm run build`） |
| 预测报 GFS 周期不可用 | NOMADS 延迟 ~5h45m；等或换旧周期 |
| 内核日志 SIGSEGV (mmap) | 不要在运行中删除/替换 mmap 解码缓存目录；升级时先停服 |
