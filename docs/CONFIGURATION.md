# 配置参考（CONFIGURATION）

> firesky 的全部配置面：YAML 文件、环境变量、优先级规则。
> 中文为主，English version: [CONFIGURATION.en.md](CONFIGURATION.en.md)

## 1. 配置文件与优先级

| 层 | 位置 | 说明 |
|---|---|---|
| 显式参数 | `load_settings(path)` / python 调用 | 最高 |
| 环境变量 | `FIRESKY_CONFIG=/path/to/your.yaml` | 次之 |
| 内置默认 | `src/firesky/config/web_server.yaml` | 兜底（缺文件也能起，全走默认值） |

容器镜像内置 `docker/web_server.yaml`（缓存目录指向持久卷 `/data`），
由 `FIRESKY_CONFIG=/app/etc/web_server.yaml` 激活。

验证当前生效配置：`make contract`（看 algorithm/cache_version）+
日志启动段。

## 2. `web_server.yaml` 全字段

```yaml
server:
  host: 127.0.0.1            # 监听地址；公网部署请保持 127.0.0.1 并走反代
  port: 8765                 # API + SPA 同端口
  workers: null              # 预测并行进程数；null=自动；预测是 CPU 密集，建议 ≤ 物理核数
  aerosol_source: cams       # auto | cams | fallback
                             #   cams=只用 CAMS 缓存（缺则报错）
                             #   fallback=缺 CAMS 时用 Open-Meteo 空气质量 AOD（精度降级，会在响应里标注）
  report_dir: reports/web    # HTML 诊断报告输出
  gfs_cache_dir: .cache/firesky/gfs                # GRIB2 + mmap 解码缓存
  ecmwf_cache_dir: .cache/firesky/ecmwf            # EC 开放数据缓存
  map_cache_dir: .cache/firesky/map-frames         # v25 地图帧
  regional_cache_dir: .cache/firesky/regional-forecasts   # v125 区域结果 + 摘要
  point_cache_dir: .cache/firesky/point-forecasts  # v121 点预测缓存
  satellite_cache_dir: .cache/firesky/himawari-hsd        # HSD 紧凑缓存（大，配 prune）
  satellite_warmup_token: null  # 卫星预热端点的本地管理令牌；公网部署必须设置
  forecast_cache_seconds: 300   # 预测响应内存缓存 TTL
  regional_refresh_payload: null  # 固化的区域刷新参数（可选 dict），cron 体可留空

logging:
  level: INFO           # DEBUG | INFO | WARNING | ERROR
  dir: logs             # 按天切割
  app_name: firesky
  when: midnight        # 轮转周期
  backup_count: 14      # 保留天数
```

## 3. 环境变量（`FIRESKY_*`）

### 3.1 核心运行时

| 变量 | 默认 | 说明 |
|---|---|---|
| `FIRESKY_CONFIG` | 内置 yaml | 配置文件路径（见 §1） |
| `FIRESKY_NATIVE_LIBRARY_PATH` | 自动搜索 | 直接指定 dylib 路径（Docker 镜像内必设） |
| `FIRESKY_NATIVE_VARIANT` | auto | `cpu` / `gpu` / `cpu-pgo` —— 变体 dylib 选择 |
| `FIRESKY_COMPUTE_BACKEND` | auto | `auto` / `cpu` / `gpu` —— 请求级后端也可在 payload 覆盖 |
| `FIRESKY_REDIS_ADDR` / `_PASSWORD` / `_DB` / `_TTL_SECONDS` | 空（禁用） | 可选 Redis：仅缓存 `/data/latest/refresh` 的周期探测；所有失败优雅降级 |

### 3.2 下载与并发

| 变量 | 默认 | 说明 |
|---|---|---|
| `FIRESKY_GFS_DOWNLOAD_WORKERS` | 4 | NOMADS 并发下载 |
| `FIRESKY_GFS_DOWNLOAD_ATTEMPTS` | 4 | 单文件重试次数 |
| `FIRESKY_GFS_DECODE_WORKERS` | 2 | GRIB2 解码线程 |
| `FIRESKY_ECMWF_DOWNLOAD_WORKERS` | 4 | EC 下载并发 |
| `FIRESKY_CAMS_DOWNLOAD_WORKERS` | 2 | CAMS/ADS 下载并发槽（1–4，批量预取已合并为单请求，此槽仅约束并发兜底下载） |
| `FIRESKY_REGIONAL_DERIVE_WORKERS` | 自动 | 区域云柱派生进程数 |
| `FIRESKY_NATIONAL_BATCH_MIN_POINTS` | 64 | 区域批切共享批量的最小点数 |
| `FIRESKY_DIAGNOSTIC_COMPRESSION_LEVEL` | 3 | 诊断包 zlib 压缩级（0–9） |

### 3.3 原生评分器开关（性能/调试，日常不动）

以下全部是**可选**微调，默认值即生产值；完整清单以
`grep -rhoE 'FIRESKY_[A-Z0-9_]+' engine/src | sort -u` 为准。

| 组 | 代表变量 | 作用 |
|---|---|---|
| 时间缓存模式 | `FIRESKY_NATIVE_EXACT_TEMPORAL_KEYS`（精确位级键）、`FIRESKY_NATIVE_CONSISTENT_TEMPORAL_KEYS`（自洽 120s 桶，保证 left+right==span）；均不设时默认 `Quantized120` | 共享 motion/各层缓存的时间键量化策略。默认与 consistent 均为显式近似（每侧 ≤60s）；exact 键数爆炸时共享稠密表按预算自动关闭。详见性能路线图「11-14 续轮」 |
| 共享路径缓存 | `FIRESKY_NATIVE_SHARED_SOLAR_PATH_SAMPLE_MIN_OBSERVERS`（默认 2048）、`..._MAX_BYTES`（8 GiB 上限）、`FIRESKY_NATIVE_DISABLE_SHARED_SOLAR_PATH_SAMPLE_CACHE` | 观测点 ≥ 阈值时共享太阳路径模板；内存紧张时用 DISABLE_* 逐个关 |
| 共享几何缓存 | `DISABLE_SHARED_MOTION_CACHE` / `DISABLE_SHARED_TERRAIN_CACHE` / `DISABLE_SHARED_WIND_CACHE` / `DISABLE_SHARED_AEROSOL_COLUMN_CACHE` + 对应 `*_MAX_BYTES` | 平流风/地形/气溶胶列的跨观测点共享 |
| 多方位路径 | `DISABLE_MULTI_BEARING_PATH_CACHE` / `ENABLE_MULTI_BEARING_PATH_AEROSOL_SAMPLE_CACHE` / `MULTI_BEARING_PATH_MIN_OBSERVERS` | 观测者返程多方位扩展 |
| GPU（可选 feature） | `FIRESKY_GPU_REDUCTION_MIN_TARGETS`（2048）、`GPU_MIN_FEATURES`、`DISABLE_NATIONAL_GPU_*`、`NATIONAL_GPU_*_MAX_BYTES`、`ENABLE_NATIONAL_PROFILE_GPU` | 仅 `--features gpu` 编译时有意义；CPU 最小集可忽略 |
| NEON | `FIRESKY_NATIVE_DISABLE_NEON_BLOCKING`（默认开 NEON） | aarch64 阻挡向量化开关 |
| 诊断 | `FIRESKY_NATIVE_GPU_TIMING` / `GPU_PARITY_DUMP` / `CANONICAL_PATH_DIAG` / `DISABLE_ANGULAR_FOOTPRINT_CACHE` 等 | 排查/校验用 |

> 注：上表 `*_MAX_BYTES`（如共享路径 8 GiB、共享 motion 3 GiB）是**单个缓存结构**的
> 估算上限，不是进程总内存预算。用户接受的进程峰值 physical footprint 上限约 4 GiB，
> 含 worker 缓存与输出；验收须测 physical footprint，不能只看单缓存上限或 RSS。

## 4. `pyproject.toml` 依赖组

```bash
pip install -e .              # 引擎 + 后端必需
pip install -e ".[dev]"       # + pytest
pip install -e ".[cams]"      # + cdsapi（CAMS 下载，需 ADS key）
```

## 5. 前端配置（`apps/shared-web/`）

> 2026-11-13 更新：共享 Web SPA 已迁至 `apps/shared-web/`，`make frontend`、
> Docker 与引擎默认静态根统一构建该 package。`frontend/`（旧 SvelteKit 工程）
> 仍在仓库中待合并，不再是默认前端。

- API 地址：开发模式走 vite 代理（`/api/v1/firesky` → `127.0.0.1:8765`）；生产同源托管，
  无需配置；
- 构建产物 `apps/shared-web/dist` 由后端 SPA fallback 直接服务
  （可用 `FIRESKY_WEB_ROOT` 覆盖）。
