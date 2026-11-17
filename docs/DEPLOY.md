# 部署文档（DEPLOY）

> firesky 的部署指南。原理见 [PRINCIPLES.md](PRINCIPLES.md)，
> 使用与数据源注册见 [README.md](../README.md)。English: [DEPLOY.en.md](DEPLOY.en.md)

2026-11-11：当前默认服务为 `firesky-engine http`，不启动 Python。
已实现接口、配置和验收限制以 [原生运行时切换](archive/RUST_RUNTIME_CUTOVER.zh-CN.md)
为准。旧 Python YAML、刷新/预热任务不自动兼容新运行时；下文明确标为
legacy 的参考只能用于显式旧版部署。2026-11-14：Docker 镜像已在
`feat/win-linux-docker-parity` 修复 SPA 静态根（`FIRESKY_WEB_ROOT`）并通过
本地 compose 冒烟（首页/资产/桌面任务/令牌与任务历史持久化）；Windows/Linux
桌面与双架构镜像发布、clean-machine 验收仍待目标机，以下是构建/启动配置，
不是平台验收通过声明。

## 1. 部署形态总览

| 形态 | 适用平台 | 适用场景 | 本文档章节 |
|---|---|---|---|
| **Docker Compose**（推荐） | Linux / macOS / Windows（Docker Desktop / WSL2） | 服务器、NAS、桌面长驻，一条命令起全套 | §3 |
| **裸机 Linux** + systemd | Linux 服务器 | 生产常驻、开机自启 | §4.1 |
| **裸机 macOS** + launchd | Mac mini 等常驻 Mac | 本地设备长驻 | §4.2 |
| **裸机 Windows** | Windows 10/11 / Server | 桌面常驻、临时跑 | §4.3 |
| 开发模式 | 任意平台 | 本机调试（Rust HTTP + vite dev） | README「使用」 |

默认由一个 Rust/Axum 进程提供 API（:8765）与前端 SPA 静态托管。
任务状态在内存中，重启不恢复；缓存、报告和凭据需要持久化。

## 2. 前置条件

### 2.1 通用条件

- 机器能出网（NOMADS / ECMWF / ADS / Himawari / RainViewer）；
- **CAMS**：按 README「数据源与注册」完成 ADS 注册，把 key 写进配置；
- 磁盘预算（`df -h` / 资源管理器自查）：

| 目录 | 内容 | 量级 |
|---|---|---|
| GFS 缓存 | GRIB2 子集 + mmap 解码 | ~50–200 MB/天滚动 |
| 区域结果 | 原生独立契约结果 + 紧凑帧 | 按请求范围测量 |
| 卫星 HSD 分段 | nowcast 足迹分段（10 分钟扫描 × 多波段） | 中：~65 MB/事件窗口，务必配 prune |
| CAMS | 光学 GRIB | ~50–200 MB/天 |
| 地图帧/日志 | v25 帧 / 文本 | 小 |

### 2.2 裸机工具链（按平台）

| 工具 | Linux | macOS | Windows |
|---|---|---|---|
| Python | 原生构建/运行不需要 | 原生构建/运行不需要 | 原生构建/运行不需要 |
| Rust | `rustup` 官方脚本 | `brew install rustup` 或 rustup 脚本 | [rustup-init.exe](https://rustup.rs)；需 VS C++ Build Tools（链接器） |
| Node | ≥20（nodesource/nvm） | `brew install node@20` | ≥20（nodejs.org MSI） |
| Make（可选） | 自带 | 自带 | 不建议；用 §4.3 的直接命令或 WSL2 |
| bash（构建脚本用） | 自带 | 自带 | 仅 WSL2/Git Bash 有；原生 Windows 走 §4.3 手动两步 |

Rust 引擎通过 C ABI 加载 ecCodes，需安装相应平台的动态库及 definitions/samples；
可设置 `FIRESKY_ECCODES_LIBRARY`、`ECCODES_DEFINITION_PATH` 和
`ECCODES_SAMPLES_PATH`。不依赖 Python eccodes 轮子或 `native_core.py`。
macOS 原生包构建会收集 ecCodes 资源；Windows 的资源打包仍需显式配置。

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

- 本地无 Docker daemon 时可先运行 `make docker-contract`，它会校验
  Dockerfile、资源清单、Compose 持久卷和双架构 CI 契约，但不会伪造镜像运行通过；
- 单架构本地加载使用
  `make docker-buildx FIRESKY_DOCKER_PLATFORMS=linux/amd64`（或
  `linux/arm64`）；双架构发布使用
  `FIRESKY_IMAGE_TAG=registry.example/firesky:tag FIRESKY_PUSH=1 make docker-buildx`，
  因为 Docker 的 `--load` 不能导入多架构 manifest list；
- `docker/Dockerfile` 三阶段：Rust 编译独立引擎 → Node 构建
  `apps/shared-web` 的共享 Web →
  Debian slim 运行时（apt 装 `libeccodes0`，非 root 用户 10001 运行，无 Python）；
- 镜像显式使用 `libeccodes.so.0`、`/usr/share/eccodes/definitions` 和
  `/usr/share/eccodes/samples`；这些路径由 Debian `libeccodes-data` 依赖提供；
- Docker 与 Tauri 使用同一份业务页面；浏览器通过同源 HTTP 桌面任务接口连接，
  不启动 Tauri 壳，也不暴露服务器目录打开或 CAMS 凭据写入；
- 持久卷：`firesky-cache`（模式/区域/帧缓存）、`firesky-cams`、
  `firesky-reports`、`firesky-logs`；
- Dockerfile/Compose 的环境变量把缓存与报告指到 `/data`，不读取旧 Python YAML；
- 默认仅映射 `127.0.0.1:8765`。容器首次启动会在持久状态卷生成
  `/data/state/http-token`（权限 `0600`），浏览器访问时使用用户名 `firesky`、
  该文件内容作为密码；执行 `docker compose exec firesky cat /data/state/http-token`
  可在本机读取，不要把输出粘贴到日志或公开讨论中。
  重建容器保留密码；所有页面、业务 API、报告和地图都受保护，只有健康检查免登录。
  API 也接受 `Authorization: Bearer <token>`，不接受 URL 查询参数中的令牌；
- 改为局域网发布（例如 `FIRESKY_HTTP_HOST=0.0.0.0`）后仍保留内建鉴权。
  Basic/Bearer 都不是传输加密，跨主机访问应放在可信 TLS 反向代理或私有加密隧道后，
  不应直接暴露公网；代理不能擅自改写请求 Host/Origin，转发头不会被当作授权；
- Redis 仅在 `--profile legacy-cache` 下启动，默认不运行；原生 `/data/latest/refresh`
  尚未接入，不能据此安排周期刷新。

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
# 1) 独立 Rust 引擎（系统中另需 ecCodes）
cargo build --manifest-path engine/Cargo.toml --release --locked

# 2) 前端
cd apps/shared-web && npm ci && npm run build && cd ..

# 3) 试跑
engine/target/release/firesky-engine http --workspace "$PWD" --host 127.0.0.1 --port 8765
# 另一个终端：
curl http://127.0.0.1:8765/api/v1/firesky/health   # {"status":"ok"}
```

配置使用 [原生环境变量](archive/RUST_RUNTIME_CUTOVER.zh-CN.md#配置)，例如
`FIRESKY_GFS_CACHE_DIR`、`FIRESKY_CAMS_CACHE_DIR` 和 `FIRESKY_REPORT_DIR`。
旧 `FIRESKY_CONFIG` / `web_server.yaml` 不控制原生进程。
ADS 凭据可通过受权限保护的 `~/.cdsapirc` 或 `CDSAPI_RC` 指定文件提供，
生产服务账户必须有读取权限；不要把密钥写进公开配置或命令日志。

HTTP 默认仅允许 loopback 监听，并校验本机 Host 及同源请求，防止网页跨来源触发下载、
清理或计算。非 loopback 监听必须同时设置 `FIRESKY_HTTP_ALLOW_REMOTE=1` 与
`FIRESKY_HTTP_TOKEN_FILE`（建议使用服务账户独占的 `0600` 文件），或
`FIRESKY_HTTP_TOKEN`；两种令牌配置不能同时使用。令牌要求 32–4096 个
ASCII 字母、数字或 `-_.~`，应使用随机生成值。错误消息不包含令牌内容。
即使监听 loopback，只要配置令牌也会开启登录保护。远程模式不建立账户体系，
共享令牌的持有者拥有该实例全部权限；凭据写入仍不向浏览器开放。

### 4.1 Linux + systemd

```bash
cd /opt/firesky
cargo build --manifest-path engine/Cargo.toml --release --locked
cd apps/shared-web && npm ci && npm run build && cd ..
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

启动前创建 `/var/lib/firesky` 并交给 `www-data` 写入，安装 ecCodes，
为服务账户配置 ADS 凭据。健康成功不表示预测所需数据已准备齐全。

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now firesky
```

### 4.2 macOS + launchd

构建同 §4.0，需要 Rust、Node 和 C ecCodes，不需要 Python。
如需原生桌面安装包，使用 `bash scripts/build_macos_native.sh`；
本节 launchd 启动的是 HTTP 服务而不是桌面客户端。

`~/Library/LaunchAgents/top.firesky.server.plist`：

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

将 ecCodes 占位路径替换成真实安装路径；非系统资源还需配置 definitions/samples。
launchd 不会自动读取交互式 shell 的环境变量。

```bash
mkdir -p logs
launchctl load ~/Library/LaunchAgents/top.firesky.server.plist   # 开机自启
launchctl list | grep firesky                                     # 确认在跑
```

### 4.3 Windows

**方式 A：PowerShell 直接跑（最简单）**

```powershell
git clone https://github.com/xyl-g/FireSky.git; cd FireSky
cargo build --manifest-path engine/Cargo.toml --release --locked

cd apps/shared-web; npm ci; npm run build; cd ..

$env:FIRESKY_ECCODES_LIBRARY = "C:\ecCodes\bin\eccodes.dll"
$env:ECCODES_DEFINITION_PATH = "C:\ecCodes\share\eccodes\definitions"
$env:ECCODES_SAMPLES_PATH = "C:\ecCodes\share\eccodes\samples"
$env:PATH = "C:\ecCodes\bin;$env:PATH"
.\engine\target\release\firesky-engine.exe http --workspace "$PWD" --host 127.0.0.1 --port 8765
```

将 ecCodes 示例路径替换为实际路径；缓存目录使用 §4.0 的环境变量。
浏览器开 `http://127.0.0.1:8765`。Windows 构建/运行尚需目标平台实测。

**方式 B：开机自启**——用「任务计划程序」建一个"登录时触发"的任务，
程序填引擎绝对路径 `...\engine\target\release\firesky-engine.exe`，参数
`http --workspace C:\FireSky --host 127.0.0.1 --port 8765`，
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
    client_max_body_size 1m;

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

### 旧版自动刷新参考（legacy，仅 Python 服务）

本小节不是原生部署操作：`data/latest/refresh` 和 national refresh 尚未接入
原生运行时，以下旧调度不得直接用于新服务。原生任务请按已实现 HTTP 契约显式提交，
先准备输入，再轮询 job；任务在重启后不会恢复。

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
（操作：启动系统 `curl.exe`，参数同上 URL；Windows 10+ 自带
curl.exe），或在 WSL2 里用 cron。

### 旧版卫星缓存预热参考（legacy，仅 Python 服务）

以下预热路由/YAML token 仅属于旧版；原生服务尚未接入该管理任务。
原生实况可直接执行 HSD 准备，`allow_download: false` 才限定缓存读取。

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
2. 若 `engine/` 或 `native/` 有改动：执行
   `cargo build --manifest-path engine/Cargo.toml --release --locked`
   重编独立引擎（容器构建自动做）；
3. 若前端改动：`cd apps/shared-web && npm ci && npm run build`（容器构建自动做）；
4. 重启进程；原生缓存校验源码/请求/输入身份，旧 Python 缓存不作为兼容命中。
   重启会丢失内存 job/桌面会话，先等任务结束或明确取消。

## 7. 安全清单

- 对公网暴露时务必走反代 + HTTPS；后端只监听 127.0.0.1；
- 原生服务没有新增远程鉴权；公网必须保护下载、清理、凭据与桌面准备能力，
  不能用旧 `satellite_warmup_token` 代替网关鉴权；
- CORS/限流：默认无鉴权（内网产品定位）；公网建议网关层加 basic auth/限流；
- 保护 ADS 凭据、报告和缓存目录；不要把密钥或带令牌的 URL 写入日志。

## 8. 健康检查与监控

| 项 | 方法 |
|---|---|
| 存活 | `GET /api/v1/firesky/health` → `{"status":"ok"}` |
| 版本契约 | `GET /api/v1/firesky/runtime` 与 `/forecasts/national/contract`；原生独立契约不冒充旧版本号 |
| 日志 | 容器 stdout/stderr、systemd journal 或 launchd 指定文件；旧 `logging.dir` 不控制原生进程 |
| 缓存水位 | `du -sh /data/cache/firesky/* /data/cams`（Windows 用资源管理器看目录大小） |

## 9. 排障

| 症状 | 处理 |
|---|---|
| 容器启动即退 / ecCodes 加载失败 | 检查引擎构建和 `libeccodes0`；裸机确认 ecCodes 动态库及 definitions/samples 路径 |
| Windows `cargo build` 报链接器错误 | 安装 Visual Studio C++ Build Tools（MSVC link.exe），重开终端再试 |
| Windows ecCodes 加载失败 | 检查 DLL 架构、依赖 DLL 搜索路径和 definitions/samples；原生运行不依赖 pip eccodes |
| macOS launchd 未自启 | `launchctl list \| grep firesky` 看状态；路径含空格需在 plist 里保持原样；`logs/` 目录必须先建好 |
| `/` 404 | 前端 build 缺失（容器内已内置；裸机跑 `npm run build`） |
| 预测报 GFS 周期不可用 | NOMADS 延迟 ~5h45m；等或换旧周期 |
| 内核日志 SIGSEGV (mmap) | 不要在运行中删除/替换 mmap 解码缓存目录；升级时先停服 |
