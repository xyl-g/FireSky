# firesky

独立自足的火烧云（朝晚霞）物理预测栈：**预测引擎（Python + Rust 光线追踪）+ FastAPI 后端 + SvelteKit 最小前端**。
物理与算法基于 [SunsetBot 教程](https://www.sunsetbot.top/halo/) 的方法论独立实现。

An independent, self-contained burning-sunset prediction stack: **prediction engine (Python + Rust ray tracing) + FastAPI backend + minimal SvelteKit frontend**.

> English documentation: [README.en.md](README.en.md)

---

## 目录 / Contents

1. [功能](#功能)
2. [架构与物理原理](#架构与物理原理)
3. [系统要求与安装](#系统要求与安装)
4. [数据源与注册要求（重要）](#数据源与注册要求)
5. [配置说明](#配置说明)
6. [使用：后端 / 前端 / CLI / Python API](#使用)
7. [API 端点清单](#api-端点清单)
8. [测试与不变量回归](#测试与不变量回归)
9. [目录结构](#目录结构)
10. [常见问题排查](#常见问题排查)
11. [原理文档（深入）](docs/PRINCIPLES.md)
12. [部署文档](docs/DEPLOY.md)
13. [配置参考](docs/CONFIGURATION.md)
14. [致谢](#致谢)

## 功能

- **点预测**：单观测点朝/晚霞逐五分钟质量台账（指数 + 档位：无明显火烧云 → 世纪大烧）
- **区域预测**：中国/东亚等区域批量观测点评分 + 地图帧 + 大气剖面
- **实况 nowcast**：向日葵 9 号卫星（HSD 分段）云柱反演 + 天气雷达对流修正
- **后端服务**：FastAPI 异步 job + SSE + gzip + 持久缓存（点/区域/地图帧/卫星）
- **最小前端**：`/forecast`（点/区域预测）、`/now`（实况）、`/outlook`（形势图）、`/admin`（管理控制台）

## 架构与物理原理

```
GFS/EC 公开数据 ──► 云柱派生（三族云量/透明度/几何/深云风险）──► Rust 光线追踪评分器 ──► 经验评分层 ──► 档位
CAMS 气溶胶(缓存) ─────────────────────────────────────────────┘        │
卫星/雷达(实况) ─────────────────────────────────────────────────┴─► nowcast 修正
```

1. **数据层**：GFS 子集（压层云量/凝结物/湿度/温度/风 + 云层边界）下载、GRIB2 解码（bitmap 掩膜 fail-closed）、
   mmap 解码缓存；派生高/中/低三族云。ECMWF 开放数据为第二模式来源。CAMS 11 层 532nm 消光廓线从缓存只读接入。
   GFS subset download + GRIB2 decode (fail-closed bitmap masking) with an mmap decode cache; three-family cloud
   derivation. ECMWF open-data is the second model source. CAMS 11-level 532 nm extinction profiles are read from cache.
2. **光线追踪层（Rust，`native/`）**：逐观测点 × 逐五分钟步长，太阳入射光线 + 观测者返程视线双向追踪：
   地球曲率（入射腿用纯几何 $R=6371$ km，视线仰角用 $7/6$ 有效半径）、地形/海面下界、
   半拉格朗日风场平流（RK2 中点风 + 出域钳制）、36 方位 × 90 仰角视角球带积分、
   目标甲板自遮蔽距离衰减（60→150 km 回归全阻挡）与 3 格连接迟滞、
   时间括号占空比二值化（云到达瞬间保持完整 deck 属性）。云光学深度沿光路累积为

$$\Delta\tau_c=b^{1.35}\frac{\Delta s}{140\ \mathrm{km}},\qquad
  T_c=e^{-\tau_c}\left(1-0.72 b_{\max}\right)$$

   其中 $b$ 是节点阻挡度（云量 × 不透明度 × 垂直相交 × 自遮蔽系数）。
   Per observer × five-minute step, the native scorer traces the solar incoming ray and the observer return sight
   line: Earth curvature (pure $R=6371$ km on the inflow leg, $7/6$ effective radius for sight-line elevation),
   terrain/sea floors, semi-Lagrangian wind advection (RK2 midpoint wind + edge clamping), a 36×90 view
   solid-angle projection, distance-decayed target self-shielding with 3-cell connection hysteresis, and
   duty-cycle bracket blending. Cloud optical depth accumulates along the path as above.

3. **经验评分层**：云量锚点映射 $q_{\rm cov}$ → 材质因子 $f_m=m/0.72$ → 入射 AOD 受光 $\iota$ →
   返程 AOD 对比度 $c$ → 最佳连续 20 分钟窗口积分 + 时长因子 $f_D$ → 校准响应曲线（饱和点
   pre=1.812）→ 档位文案：

$$\mathrm{pre}=q_{\rm cov} f_m \iota c f_D,\qquad
  q=\min\left(2.5, 1.225 \mathrm{pre}^{1.2}\right)$$

   算法版本 `2026.11.04.87`（`src/firesky/version.py`）。完整推导见[原理文档](docs/PRINCIPLES.md)。
   The empirical layer: amount anchors $q_{\rm cov}$ → material factor $f_m=m/0.72$ → inflow AOD illumination
   $\iota$ → return-leg AOD contrast $c$ → best-continuous-20-minute window integral with duration factor $f_D$
   → calibrated response curve → level labels, as in the formula above. Algorithm version `2026.11.04.87`.
   Full derivation: [PRINCIPLES](docs/PRINCIPLES.en.md).

## 系统要求与安装

| 依赖 | 版本 | 用途 | 必需? |
|---|---|---|---|
| Python | ≥ 3.11 | 引擎 + 后端 | ✅ |
| Rust (cargo) | 稳定版 ≥ 1.75 | 原生评分器编译 | ✅ |
| Node.js + npm | ≥ 20 | 前端构建 | 前端需要 |
| numpy / requests / astral / eccodes | 见 `pyproject.toml` | 数值 / 下载 / 太阳几何 / GRIB2 解码 | ✅ |
| fastapi / uvicorn / pyyaml / httpx | 同上 | 后端服务 | ✅ |
| pillow / h5py | 同上 | 雷达 PNG / 卫星 HDF5 | ✅ |
| cdsapi | ≥ 0.7 | **CAMS 下载**（需注册，见下） | 想自动下载 CAMS 时 |
| eccodes 二进制库 | ecCodes ≥ 2.33 | eccodes Python 包依赖的原生库（`brew install eccodes` / apt `libeccodes-data`） | ✅ |

```bash
# 1) 引擎与后端
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,cams,nowcast,ec]"
./scripts/build_native_variants.sh        # 构建 libfiresky_native(_cpu) dylib

# 2) 前端
cd frontend && npm install && npm run build && cd ..

# 3) 启动后端（托管前端 SPA，默认 127.0.0.1:8765）
uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

## 数据源与注册要求

**唯一需要注册的是 CAMS（哥白尼大气数据服务）**；其余全部公开、免注册。

| 数据源 | 用途 | 注册/密钥 | 免费额度 |
|---|---|---|---|
| **CAMS 全球大气成分预报**（ADS） | 气溶胶 AOD550 + 11 层消光（决定霞色与通透度） | ⚠️ **必须注册**：① 在 [ADS](https://ads.atmosphere.copernicus.eu) 注册账号；② 在个人页面接受 `cams-global-atmospheric-composition-forecasts` 数据集许可；③ 生成 API key，写入 `~/.cdsapirc`（标准 CDS 格式：`url: https://ads.atmosphere.copernicus.eu/api` + `key: 你的UID:你的KEY`——新版 ADS 只有一串 key，填 `key: <KEY>` 即可）；④ `pip install cdsapi` | 免费，有排队 |
| GFS（NOAA NOMADS） | 主数值模式（云/湿/温/风/边界） | 无需注册，公开 GRIB2 | 免费，请保留 User-Agent |
| ECMWF open data | 第二模式（`model_source: ec`） | 无需注册（公开 S3/GCS 桶） | 免费 |
| 向日葵 9 号（NOAA S3 + NICT + JMA） | nowcast 卫星云柱（HSD 分段足迹） | 无需注册，公开桶 | 免费 |
| RainViewer | nowcast 天气雷达 | 无需注册（公开 API） | 免费（非商业） |
| Open-Meteo | 地名搜索 / 空气质量 AOD 回退 | 无需注册（非商业） | 免费 |
| OSM Nominatim | 地名搜索 | 无需注册，需 User-Agent（1 req/s 限速） | 免费 |
| AWS terrarium 高程瓦片 | 观测点海拔 | 无需注册 | 免费 |

CAMS API key 配置（`~/.cdsapirc`）示例：

```ini
url = https://ads.atmosphere.copernicus.eu/api
key = 你的APIKEY
```

拿到 CAMS 权限后，预热缓存的两种方式：

```bash
# 方式 A：数据下载计划器（异步 job；cams 源走 cdsapi，需要 ADS key）
curl -X POST http://127.0.0.1:8765/api/v1/firesky/data/fetch -H 'Content-Type: application/json' \
  -d '{"target_date": "2026-11-05", "phenomenon": "dusk", "region": "china", "sources": ["cams"]}'
# 轮询：GET /api/v1/firesky/data/fetch/status?job_id=...

# 方式 B：从其他部署复制已下载好的 CAMS 缓存目录（文件格式一致）
#   直接拷贝或软链到本项目 .cache/cams
```

> 注意：引擎本身**只读** CAMS 缓存（fail-closed，缺缓存时明确报错而不虚构 AOD）；
> 下载由后端预热端点（cdsapi）或你自己准备。`satellite_warmup_token` 是本地管理
> 令牌（yaml 配置），与任何第三方注册无关。

## 配置说明

配置文件：`src/firesky/config/web_server.yaml`，可用环境变量 `FIRESKY_CONFIG` 指向自定义副本。

| 字段 | 默认 | 说明 |
|---|---|---|
| `server.host` / `server.port` | 127.0.0.1 / 8765 | 后端监听 |
| `server.workers` | null | 预测并行进程数（null=自动） |
| `server.aerosol_source` | cams | `auto` / `cams` / `fallback`（fallback=无 CAMS 时用 Open-Meteo 空气质量 AOD） |
| `server.*_cache_dir` | `.cache/firesky/*` | GFS / ECMWF / CAMS / 地图帧 / 区域 / 单点 / 卫星缓存目录 |
| `server.satellite_warmup_token` | null | 卫星预热管理端点的本地令牌（设了才开网络预热） |
| `server.forecast_cache_seconds` | 300 | 预测响应内存缓存秒数 |
| `logging.*` | INFO / logs / firesky | 日志级别 / 目录 / 应用名 |

## 使用

### 后端 + 前端（推荐）

```bash
uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
# 前端已构建时直接访问 http://127.0.0.1:8765/forecast
# 开发模式前端：cd frontend && npm run dev -- --host 5173（API 走 /api 代理到 8765）
```

### CLI（引擎直用，不起服务）

```bash
python -m firesky predict --lat 30.27 --lon 120.15 --date 2026-11-05 \
    --phenomenon dusk --cams-cache-dir .cache/cams
```

### Python API

```python
from datetime import datetime, timezone
from firesky import predict_burn_sky

result = predict_burn_sky(
    [{"name": "杭州", "lat": 30.27, "lon": 120.15, "elevation_m": 12.0,
      "timezone_name": "Asia/Shanghai"}],
    "2026-11-05",
    "dusk",
    datetime(2026, 11, 4, 12, tzinfo=timezone.utc),   # GFS 周期
    cams_cache_dir=".cache/cams",
)
print(result["points"][0]["peak"]["quality_level"])
```

## API 端点清单

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v1/firesky/health` | GET | 健康检查 |
| `/api/v1/firesky/forecasts/national/contract` | GET | 区域预测算法/缓存版本契约 |
| `/api/v1/firesky/forecasts` | POST | 启动单点预测 job（异步） |
| `/api/v1/firesky/forecasts/results/{job_id}` | GET | 取 job 结果（失败时以 400/500 返回错误） |
| `/api/v1/firesky/forecasts/national` | POST | 启动区域预测 job |
| `/api/v1/firesky/forecasts/nowcast` | POST | 启动 nowcast job（卫星+雷达） |
| `/api/v1/firesky/map-frames` | GET | 地图帧（token + time） |
| `/api/v1/firesky/forecasts/national/summary` | POST | 区域摘要（严格字段契约） |
| `/api/v1/firesky/points/metadata`、`/locations/search` | GET | 观测点元数据 / 地名搜索 |
| `/api/v1/firesky/data/*` | POST/GET | 数据下载计划器（`data/fetch` 异步 job，`sources` 含 `gfs`/`ecmwf`/`cams`）+ 状态/清理 |
| `/api/v1/firesky/cache/warmup` | POST | 热门城市预测缓存预热 |
| `/api/v1/firesky/satellite/cache/warmup` | POST | 卫星缓存预热（需 `satellite_warmup_token`，请求头 `x-firesky-warmup-token`） |
| `/{full_path}` | GET | SPA fallback（前端客户端路由） |

## 测试与不变量回归

```bash
.venv/bin/python -m pytest tests/ -q          # 29 passed
cd frontend && npm run check && npm run build # 0 errors
```

- `tests/test_scoring_invariants.py`：评分不变量回归（统一 20 分钟窗口：短 run 等值 /
  边界连续；LCL 云底；NaN 档位防护；响应曲线单调性）。
- `tests/test_backend_api.py`：服务契约（health / contract / 异步 job 失败语义）。
- 改动 `native/src/*.rs` 后先 `./scripts/build_native_variants.sh` 再跑 pytest
  （否则 pytest 用的是旧 dylib，会误判）。

## 桌面应用（本地单机）

不想部署服务的话，可以直接用 PySide6 桌面应用（与 Web 服务共用同一条
预测核心路径，数值完全一致）：

```bash
# 开发运行
.venv/bin/python desktop/app.py

# 打包（macOS 出 .app 目录 / Windows 出 exe 目录）
pip install pyinstaller
pyinstaller desktop/FireSky.spec --noconfirm
# macOS：cd dist && hdiutil create -volname FireSky -srcfolder FireSky -ov -format UDZO FireSky.dmg
```

界面：观测点表单（含常用城市）、日期/现象/模式选择、进度日志、峰值
档位卡片、逐五分钟质量曲线与台账表。CI 的 `desktop` job 在
macOS/Windows 上自动打包并上传产物（未签名：macOS 首开需右键→打开，
Windows SmartScreen 提示后选「仍要运行」）。

## 目录结构

```
firesky/
├── src/firesky/           # 引擎 + 后端（Python）
│   ├── engine.py          # 高层 API predict_burn_sky
│   ├── api.py             # FastAPI 生产入口
│   ├── web_server.py      # 服务核心（job/缓存/payload 装配）
│   ├── national_batch.py  # 区域网格派生 + 原生调用 + 质量装配
│   ├── satellite*.py / radar.py                  # nowcast 数据与反演
│   ├── gfs.py / ecmwf*.py / aerosol.py           # 数据摄取
│   ├── scoring.py / forward_field_scoring.py     # 物理 + 经验评分
│   ├── native_core.py     # ctypes 桥（libfiresky_native）
│   └── config/web_server.yaml                    # 服务配置
├── native/                # Rust 光线追踪评分器（cdylib）
├── frontend/              # SvelteKit 最小前端（build 由后端托管）
├── scripts/build_native_variants.sh
└── tests/
```

## 常见问题排查

| 症状 | 原因与处理 |
|---|---|
| `national CPU scorer library not found` | 先跑 `./scripts/build_native_variants.sh` |
| `No cached CAMS optical profile covers ...` | CAMS 缓存缺该周期文件——预热端点下载（需 ADS key）或从其他部署拷贝 `.cache/cams` |
| `CAMS full profile does not cover every model cell` | CAMS 水平范围不够——扩大预热 `area` |
| 预测报"GFS 周期不可用" | NOMADS 发布延迟 ~5h45m；用更早的 cycle 或稍后重试 |
| 前端空白/404 | `cd frontend && npm run build` 后重启后端 |
| 405 Method Not Allowed | 未知路径的 POST 落在 GET-only SPA catch-all 上——检查端点拼写 |

## 来源

物理与算法基于 [SunsetBot](https://www.sunsetbot.top/about/) 教程的方法论独立实现；
评分常数与结构参考了社区对朝晚霞形成的公开讨论。

## 致谢

- **[SunsetBot](https://sunsetbot.top/about/)** —— 本项目的方法论源自其教程
  （[halo 篇](https://www.sunsetbot.top/halo/)）：大气光学、气溶胶散射与火烧云
  成色原理的讲解，是评分模型的出发点。
- **开源数据与服务**：NOAA NOMADS（GFS）、ECMWF（open data）、Copernicus
  CAMS / ADS（气溶胶）、JMA / NICT / NOAA（向日葵 9 号）、RainViewer（雷达）、
  Open-Meteo、OpenStreetMap Nominatim
  （地名）、AWS Terrain Tiles（高程）、MapLibre / Leaflet（地图）。
- **开源软件栈**：Rust 生态（rayon 等）、numpy、astral、ecCodes、FastAPI、
  uvicorn、SvelteKit、Leaflet、shadcn-svelte、lucide 图标。
- 以及每一个抬头看过晚霞、并愿意把观测反馈给数据的人。
