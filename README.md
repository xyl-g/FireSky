# FireSky

火烧云（朝晚霞）物理预测栈：**Rust 原生预测引擎与 HTTP 服务 + SvelteKit 前端 + 原生桌面**。
物理与算法基于 [SunsetBot 教程](https://www.sunsetbot.top/halo/) 的方法论独立实现。

A burning-sunset prediction stack: **native Rust prediction engine and HTTP service + SvelteKit frontend + native desktop clients**.

> English documentation: [README.en.md](README.en.md)

> 当前版本 **v2.0.3**（见[下载安装](#下载安装releases)）：macOS DMG（双架构）、
> Windows zip + 安装器（x64/ARM64）、Android 通用 APK 与 iOS 未签名 IPA
> 全部随 CI `v*` tag 自动直发 Release（移动端亦支持 `mobile-v*` 独立热修）。v2.0.3 修复
> macOS 更新/退出时 8765 端口未释放问题并精修 16 方位雷达 Top-3 排版；v2.0.2 重构了
> macOS 光谱云色与 16 方位观测雷达双栏排版（修复 `0..1` 雷达比例尺与步进时间对齐）并完善 AltStore v2 源元数据；v2.0.1 将
> CAMS ADS 同指纹队列保留恢复至 24 小时（孤儿仍 4 小时清理）并新增 macOS 升级自动重载后台同步助手；v2.0.0 新增
> **CIE 1931 物理光谱云色预测**、**16 方位火烧云观测雷达**、**高分预警推送（Bark / Server酱 / Webhook / macOS 横幅）**、
> **移动端二维码一键配对（支持局域网与 Cloudflare Tunnel）**、**macOS 追霞 PNG 海报卡导出**、
> 同步进度消歧与 **AltStore 自动更新订阅源**；v1.10.5 修复了
> macOS 最小化/关窗后无法重开主窗口、窗口重开秒出不闪断连接及后台助手工作区校验，
> 并将 CAMS ADS 孤儿任务清理周期缩紧至 4 小时（升级 CI 至 Node 24）；
> v1.10.4 为 macOS 新增**关窗驻留菜单栏后台**（`NSStatusItem` 与 `launchd` 助手调度协同，不再阻塞退出），
> 加固 **CAMS ADS 队列轮询抗瞬时 500/429 抖动**，补齐 iOS IPA 图标与 Ad-Hoc 签名，并按路径精准门控 CI；
> v1.10.3 为 Android 补齐**嵌入式交互地图**
> （浅/深色双主题与全屏选点），将 iOS / Android 收口为**严格只读瘦客户端**
> （远端不暴露写/删配置入口，避免手机误改桌面数据），并新增 **CAMS ADS 任务持久接回**
> 与大快照冷启动防 OOM；v1.10.2 优化了**桌面端应用内更新下载速度与默认系统代理兼容**，
> 以及**后台数据同步按数据源独立锁与指数退避自动恢复**（CAMS ADS 长时间排队不再阻塞 GFS/EC）；
> v1.10.1 起 iOS / Android 全面转型纯原生瘦客户端（直连 Mac / Windows / Docker 服务端，
> 支持区域预测热力图/城市排行与卫星实况、实时 SSE 进度推送）。UI 设计系统 v2
> （毛玻璃卡片 / 状态徽标 / 强调渐变 / 夜空蓝暗色）token 见 [DESIGN.md](DESIGN.md)。
> UI 以 [macOS 产品与 UI 基准](docs/MACOS_UI_BASELINE.zh-CN.md)为参照，移动端蓝图与验收状态见
> [移动端双原生计划](docs/MOBILE_NATIVE_PLAN.md)。

---

## 目录 / Contents

- [FireSky](#firesky)
  - [目录 / Contents](#目录--contents)
  - [功能](#功能)
  - [下载安装（Releases）](#下载安装releases)
    - [macOS](#macos)
    - [Windows](#windows)
    - [Android](#android)
    - [iOS](#ios)
    - [Docker 自托管](#docker-自托管)
    - [平台策略](#平台策略)
  - [架构与物理原理](#架构与物理原理)
  - [系统要求与安装](#系统要求与安装)
  - [数据源与注册要求](#数据源与注册要求)
  - [配置说明](#配置说明)
  - [使用](#使用)
    - [后端 + 前端（推荐）](#后端--前端推荐)
    - [CLI（引擎直用，不起服务）](#cli引擎直用不起服务)
  - [API 端点清单](#api-端点清单)
  - [测试与不变量回归](#测试与不变量回归)
  - [桌面应用（本地单机）](#桌面应用本地单机)
    - [地图底图与天地图 Key](#地图底图与天地图-key)
  - [移动应用（v1 范围）](#移动应用v1-范围)
  - [目录结构](#目录结构)
  - [常见问题排查](#常见问题排查)
  - [来源](#来源)
  - [致谢](#致谢)

## 功能

- **点预测**：单观测点朝/晚霞逐五分钟质量台账（指数 + 档位：无明显火烧云 → 世纪大烧）
- **区域预测**：中国/东亚等区域批量观测点评分 + 地图帧 + 大气剖面
- **实况 nowcast**：向日葵 9 号卫星（HSD 分段）云柱反演 + 天气雷达对流修正
- **后端服务**：Rust/Axum 异步 job + SSE + gzip + 严格输入身份缓存
- **最小前端**：`/forecast`（点/区域预测）、`/now`（实况）、`/outlook`（形势图）、`/admin`（管理控制台）
- **原生桌面**：macOS SwiftUI/AppKit + Rust FFI 为产品与 UI 基准；Windows WinUI 3
  对齐同基准信息架构（历史独立页/设置分类/数据管理/应用内更新等）并在真实
  Windows 机器完成构建与运行验收，发布形态为自包含 zip + Inno Setup 安装器；
  Linux 原生 UI 代码保留（不再作为发布目标，见[平台策略](#平台策略)）；
  [构建与迁移状态](apps/README.md)。
- **移动应用**：iOS SwiftUI + Android Kotlin/Compose 纯原生瘦客户端，通过局域网
  HTTP 或 Cloudflare Tunnel HTTPS + SSE 实时流直连 Mac / Windows / Docker 服务端，
  支持单点预测、区域预测（中国热力图与城市排行）、卫星实况、数据管理与任务历史；
  Android APK 与 iOS IPA 均随 Release 自动发布。

## 下载安装（Releases）

正式版统一从 [GitHub Releases](https://github.com/xyl-g/FireSky/releases) 下载
（当前 **v2.0.3**）。下载后先校验完整性（`SHA256SUMS.txt` 为裸文件名格式，
与各资产放在同一目录执行）：

```bash
shasum -a 256 -c SHA256SUMS.txt
```

标准资产清单（`<版本>` 通配，全部由 CI `v*` tag 自动构建直发）：

| 资产 | 平台 | 说明 |
|---|---|---|
| `FireSky-<版本>-macos-arm64.dmg` | macOS 14+（Apple Silicon） | 原生桌面 `.app`，ad-hoc 签名 |
| `FireSky-<版本>-macos-x86_64.dmg` | macOS 14+（Intel） | CI Intel 构建 + 冒烟；Apple Silicon 可经 Rosetta 2 运行 |
| `FireSky-<版本>-windows-x64.zip` / `-x64-setup.exe` | Windows 10 19041+（x64） | 绿色 zip / Inno Setup 安装器 |
| `FireSky-<版本>-windows-arm64.zip` / `-arm64-setup.exe` | Windows（ARM64） | 同上 |
| `FireSky-<版本>-android-arm64.apk` | Android 8.0+（通用纯瘦客户端） | 纯 Kotlin/Compose 瘦客户端 APK（无本地大引擎） |
| `FireSky-<版本>-ios-arm64.ipa` | iOS 16+（arm64 纯瘦客户端） | 未签名 `.ipa`，可用 AltStore / SideStore / Sideloadly / TrollStore / 开发者重签安装 |
| `SHA256SUMS.txt` | — | 全部资产 SHA256 清单 |

> Android APK 与 iOS IPA 均随 `v*` tag 自动构建直发 Release（iOS IPA 为未签名包，
> 需通过侧载工具或自行签名安装）；Docker 镜像请从源码构建
> （见 [Docker 自托管](#docker-自托管)）。

### macOS

DMG 拖入「应用程序」即可。发布为 **ad-hoc 签名（无 Developer ID / 公证）**，
Gatekeeper 默认拦截首次打开：

- **macOS 14**：右键 `FireSky.app` →「打开」；或系统设置 → 隐私与安全性 →「仍要打开」。
- **macOS 15 及以上**：右键打开已不再绕过 Gatekeeper。首次双击被拦截后，到
  系统设置 → 隐私与安全性 →「仍要打开」；或终端执行：

  ```bash
  xattr -dr com.apple.quarantine /Applications/FireSky.app
  ```

仅从官方 Release 页下载并先核对 SHA256。完整发布流程与门禁见
[macOS 发布文档](docs/RELEASE_MACOS.zh-CN.md)。

### Windows

自 **feat/windows-release** 起为正式发布目标，发布形态为**自包含 zip + 安装器**
（首个 Windows 正式包随下一版本发布，见[平台策略](#平台策略)）：

- **绿色 zip**：解压到任意目录，运行 `FireSky\FireSky.exe` 即用；适合免安装
  与便携使用。
- **安装器（setup.exe）**：向导安装、开始菜单快捷方式与卸载器；卸载只移除
  应用文件，用户数据（`%LOCALAPPDATA%\FireSky` 与所选数据目录）保留。
- 系统要求：Windows 10 19041+ / 11；**WebView2 Runtime**（Win11 预装，Win10
  需[自行安装](https://developer.microsoft.com/microsoft-edge/webview2/)，
  安装器会在安装时检测提示）。
- 发布包**无代码签名**：SmartScreen 首次运行提示「更多信息 → 仍要运行」；
  仅从官方 Release 页下载并先核对 SHA256（`SHA256SUMS.txt` 含 zip 与 setup）。
- 安装器安装目录下的 `Resources\` 内置 Rust 引擎 DLL、ecCodes 与全部应用
  资源；GRIB 解码在包内完成，无需额外依赖。

完整发布流程与验收口径见 [Windows 发布文档](docs/RELEASE_WINDOWS.zh-CN.md)。

### Android

自签签名 release APK（不上架应用商店；仅 arm64-v8a，minSdk 26 / Android 8.0+）：

1. 下载 APK 后点击安装，按提示允许浏览器/文件管理器「安装未知应用」。
2. **覆盖更新必须使用同一签名 key 的官方 APK**：直接安装新版本即可保留应用
   数据；请勿混装其他来源的重新签名包（签名不一致会被系统拒绝）。
3. 命令行安装/更新：`adb install -r FireSky-<版本>-android-arm64.apk`。

### iOS

发布为**无签名 device IPA**，不能直接安装。用免费 Apple ID 通过
[AltStore](https://altstore.io) 或 [Sideloadly](https://sideloadly.io) 重新签名
后侧载到真机：

- 免费账号签名 **7 天有效**，到期需重签（AltStore 在同一 Wi-Fi 下可自动刷新）；
  免费账号同时最多 3 个自签 App。
- **AltStore / SideStore 自动更新订阅源**：在 AltStore 中添加软件源
  `https://github.com/xyl-g/FireSky/releases/latest/download/altstore-source.json`
  即可在新版本发布后直接检测并侧载更新；也可在 FireSky 桌面端设置中扫描配对二维码一键连接。
- 首次启动前：设置 → 通用 → VPN 与设备管理 → 信任对应开发者证书。
- 质量验收以模拟器 `xcodebuild test` 全链为准，IPA 只是分发载体。

### Docker 自托管

镜像从源码构建（CI 仅做构建冒烟，不随 Release 分发 tarball）。构建后运行，
容器内服务监听 8765：

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
# 首次启动生成访问令牌（用户名 firesky，密码为下面命令的输出，勿公开粘贴）：
docker exec firesky cat /data/state/http-token
# 浏览器打开 http://127.0.0.1:8765
```

- 也可用 compose：`docker compose up -d --build`（compose 默认同样仅绑定
  `127.0.0.1:8765`）。
- 持久卷、鉴权与局域网发布注意见[部署文档](docs/DEPLOY.md)。

### 平台策略

- **macOS / Windows**：正式发布目标，CI `v*` tag 自动构建直发 Release
  （macOS DMG 双架构；Windows zip + 安装器 x64/ARM64）。
- **Android**：正式发布目标，mobile-release job 随 tag 构建 APK 直发。
- **iOS**：源码构建（Xcode），IPA 分发后续接入。
- **Docker**：自托管方案，镜像从源码构建（不再随 Release 分发 tarball）。
- **Linux**：自 2026-11-20 起**不再作为发布目标**（用户裁定）；GTK4 客户端与
  FFI 代码保留，可自行源码构建；服务器部署推荐 Docker。

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

   算法版本 `2026.11.04.87`。评分实现位于 `native/` 和 `engine/`。完整推导见[原理文档](docs/PRINCIPLES.md)。
   The empirical layer: amount anchors $q_{\rm cov}$ → material factor $f_m=m/0.72$ → inflow AOD illumination
   $\iota$ → return-leg AOD contrast $c$ → best-continuous-20-minute window integral with duration factor $f_D$
   → calibrated response curve → level labels, as in the formula above. Algorithm version `2026.11.04.87`.
   Full derivation: [PRINCIPLES](docs/PRINCIPLES.en.md).

## 系统要求与安装

服务/CLI/原生桌面运行时均为 Rust，无 Python 运行时依赖。
已实现范围与验收缺口见 [原生运行时切换](docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md)。

| 依赖 | 版本 | 用途 | 必需? |
|---|---|---|---|
| Rust (cargo) | 当前稳定版 | 独立原生引擎编译 | 构建需要 |
| Node.js + npm | ≥ 20 | 前端构建 | 前端需要 |
| eccodes 二进制库 | ecCodes ≥ 2.33 | Rust 通过 C ABI 解码 GRIB；需 definitions/samples | ✅ |
| Python + pytest/numpy | ≥ 3.11 | 仅现架构契约测试与资源/数据工具（`scripts/`） | 运行不需要 |

```bash
# 构建引擎与前端，不安装 Python 包
make install
# Rust HTTP + SPA，默认 127.0.0.1:8765
make serve
# 浏览器打开 http://127.0.0.1:8765
```

## 数据源与注册要求

**唯一需要注册的是 CAMS（哥白尼大气数据服务）**；其余全部公开、免注册。

| 数据源 | 用途 | 注册/密钥 | 免费额度 |
|---|---|---|---|
| **CAMS 全球大气成分预报**（ADS） | 气溶胶 AOD550 + 11 层消光（决定霞色与通透度） | ⚠️ **必须注册**：① 在 [ADS](https://ads.atmosphere.copernicus.eu) 注册账号；② 接受数据集许可；③ 在桌面设置中保存 API key，或配置 `~/.cdsapirc`。Rust 直接访问 ADS，不需要 cdsapi | 免费，有排队 |
| GFS（NOAA NOMADS） | 主数值模式（云/湿/温/风/边界） | 无需注册，公开 GRIB2 | 免费，请保留 User-Agent |
| ECMWF open data | 第二模式（`model_source: ec`） | 无需注册（公开 S3/GCS 桶） | 免费 |
| 向日葵 9 号（NOAA S3 + NICT + JMA） | nowcast 卫星云柱（HSD 分段足迹） | 无需注册，公开桶 | 免费 |
| RainViewer | nowcast 天气雷达 | 无需注册（公开 API） | 免费（非商业） |
| Open-Meteo | 地名搜索 / 空气质量 AOD 回退 | 无需注册（非商业） | 免费 |
| OSM Nominatim | 地名搜索 | 无需注册，需 User-Agent（1 req/s 限速） | 免费 |
| AWS terrarium 高程瓦片 | 观测点海拔 | 无需注册 | 免费 |

CAMS API key 配置（`~/.cdsapirc`）示例：

```yaml
url: https://ads.atmosphere.copernicus.eu/api
key: 你的APIKEY
```

拿到 CAMS 权限后，预热缓存的两种方式：

```bash
# 方式 A：Rust 数据下载计划器（异步 job；需要 ADS key，不需要 cdsapi）
curl -X POST http://127.0.0.1:8765/api/v1/firesky/data/fetch -H 'Content-Type: application/json' \
  -d '{"target_date": "2026-11-12", "phenomenon": "dusk", "region": "china", "timezone": "Asia/Shanghai", "sources": ["cams"]}'
# 轮询：GET /api/v1/firesky/data/fetch/status?job_id=...

# 方式 B：从其他部署复制已下载好的 CAMS 缓存目录（文件格式一致）
#   直接拷贝或软链到本项目 .cache/cams
```

> 评分读取严格校验的 CAMS 缓存，缺少完整时空覆盖时明确报错，不虚构 AOD。
> 数据准备由 Rust 直接访问 ADS，仍需账号和许可。

## 配置说明

原生运行时使用 [环境变量](docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md#配置) 和命令行参数；
Docker 部署的目录约定见 `docker/Dockerfile` 与 [部署文档](docs/DEPLOY.md)。

## 使用

### 后端 + 前端（推荐）

```bash
make serve
# 前端已构建时直接访问 http://127.0.0.1:8765/forecast
# 开发模式前端：cd apps/shared-web && npm run dev -- --host 5173（API 走 /api 代理到 8765）
```

### CLI（引擎直用，不起服务）

```bash
engine/target/release/firesky-engine forecast --input request.json --workspace "$PWD"
# JSON 请求示例及字段见原生运行时文档
```

## API 端点清单

原生已注册接口及未完成清单见 [原生运行时切换](docs/archive/RUST_RUNTIME_CUTOVER.zh-CN.md#http-范围)。
参数、返回字段与调用示例见 [预测接口说明](docs/FORECAST_API.md)。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v1/firesky/health` | GET | 健康检查 |
| `/api/v1/firesky/forecasts/national/contract` | GET | 区域预测算法/缓存版本契约 |
| `/api/v1/firesky/forecasts` | POST | 启动单点预测 job（异步） |
| `/api/v1/firesky/forecasts/results/{job_id}` | GET | 取 job 结果（未完成为 404；一般任务失败为 500，GFS 输入不可用为 503） |
| `/api/v1/firesky/forecasts/national` | POST | 启动区域预测 job |
| `/api/v1/firesky/forecasts/nowcast` | POST | 启动 nowcast job（卫星+雷达） |
| `/api/v1/firesky/map-frames` | GET | 地图帧（token + time） |
| `/api/v1/firesky/forecasts/national/summary` | GET | 区域摘要（严格字段契约） |
| `/api/v1/firesky/points/metadata`、`/locations/search` | GET | 观测点元数据 / 地名搜索 |
| `/api/v1/firesky/data/*` | POST/GET | 数据下载计划器（`data/fetch` 异步 job，`sources` 含 `gfs`/`ecmwf`/`cams`）+ 状态/清理 |
| `/api/v1/firesky/cache/warmup` | POST | 热门城市预测缓存预热 |
| `/{full_path}` | GET | SPA fallback（前端客户端路由） |

## 测试与不变量回归

```bash
make test-rust
make test                                  # 现架构契约测试，需 pytest/numpy
cd apps/shared-web && npm run check && npm run build # 0 errors
```

- `tests/test_rust_hsd_raw.py`：HSD 标定原生契约（Python 仅作测试 oracle）。
- `tests/test_rust_desktop_sync.py`：后台数据同步助手 JSONL 桥契约（设置持久化、
  到期门禁、熔断与解锁、fail-closed）。
- `tests/test_native_preparation_ui.py`：macOS Swift 准备/展示逻辑编译检查。
- 评分核心已并入引擎 crate（`engine/src/native/`）；改动后直接重建引擎
  （`make engine` / `make desktop-macos`）再跑测试。

## 桌面应用（本地单机）

当前正式桌面先交付 macOS SwiftUI/AppKit + Rust FFI；Windows WinUI 和 Linux
原生 UI 只按 macOS 的 ABI、预检和任务生命周期合同扩展。[构建与迁移状态](apps/README.md)
记录各平台的实际验收边界。Tauri 桌面壳与旧 PySide6 桌面均已退役（2026-11-13）：
共享 Web 迁至 `apps/shared-web/`，由 Rust HTTP 与 Docker 托管；旧版用户数据
（`~/.firesky-desktop/` 收藏点与当前点）由 macOS 新版启动时自动导入。退役记录见
`docs/archive/PYTHON_TAURI_PYSIDE_RETIREMENT_PLAN.zh-CN.md`。

### 地图底图与天地图 Key

共享 Web 的选点地图双底图源，**地图只用于选点，不叠加任何预报结果**：

| 底图源 | 适用区域 | 底图类型 | Key |
|---|---|---|---|
| 天地图（默认） | 中国境内 | 影像 / 矢量 / 地形 | 需自备 |
| OpenFreeMap | 全球 | 影像 / 街道 | 免 Key |

天地图 Key 在 [console.tianditu.gov.cn](https://console.tianditu.gov.cn) 申请，
应用类型选「浏览器端」，在 Web 设置页「地图服务」中填入（保存在浏览器本地）。

**仓库与发布产物都不内置可用 Key。** 未配置时地图显示申请引导，并可一键切到
OpenFreeMap 底图继续选点。境内公开使用须用天地图（《地图管理条例》要求有审图号的
底图），OpenFreeMap 仅限中国境外。

## 移动应用（v1 范围）

双原生：iOS SwiftUI（`apps/mobile-ios/`）+ Android Kotlin/Compose
（`apps/mobile-android/`）。预测引擎内嵌 App 进程内（`engine-mobile/`，与桌面
`desktop` 子命令同源，JSON-lines 帧协议逐帧一致），ecCodes definitions 随包
打包（约 131 MB，裁剪为后续优化），离线预测依赖预置/已下载缓存。

- **v1 做**：单点预测（GFS/EC 双模型）、数据会话 check→prepare→confirm（进度/
  取消）、观测点管理、设置（CAMS Key 写沙箱）、上次结果离线查看与**杀 App 重启
  自动恢复**（`result_restore`，与 macOS 语义一致）。
- **v1 不做**：区域预测、实况 nowcast、HTML 报告、后台预取。
- **地图**：iOS 已用 MapKit 原生实现；Android 地图延后，v1 以坐标/收藏输入选点。
- **验收**：Android 模拟器 instrumented 全链 + 与桌面帧级 parity + kill-restart
  恢复；iOS 模拟器 `xcodebuild test` 全绿（bundle definitions 真实生效，不靠
  宿主环境注入）。接线与验收记录见[移动端双原生计划](docs/MOBILE_NATIVE_PLAN.md)。

## 目录结构

```
firesky/
├── engine/                # Rust 引擎：HTTP 服务 / CLI / 桌面 FFI / 数据同步
├── engine-mobile/         # 移动端进程内引擎（C ABI fsmobile.h + JNI shim）
├── native/                # Rust 光线追踪评分器（cdylib）
├── apps/
│   ├── macos/             # SwiftUI/AppKit + Rust FFI（正式桌面主线）
│   ├── mobile-android/    # Kotlin/Compose + JNI（APK 随 Release 自动发布）
│   ├── mobile-ios/        # SwiftUI + 静态链接引擎（Xcode 源码构建）
│   ├── windows/           # WinUI 3 + Rust FFI（正式包后续在 Windows 机器构建）
│   ├── linux/             # GTK4/C++ + Rust FFI（代码保留，不再作为发布目标）
│   └── shared-web/        # 共享 Web SPA（Rust HTTP 与 Docker 同源托管）
├── frontend/              # 旧 SvelteKit 前端（待合并，契约测试保留）
├── resources/app-data/    # 内置城市目录与时区数据集（sha256 清单审计）
├── docker/                # 三阶段镜像（Rust 构建 + Web 构建 + 无 Python 运行时）
├── scripts/               # 构建/冒烟脚本与资源、数据准备工具
└── tests/                 # 现架构契约测试（Rust IPC / Swift UI）
```

## 常见问题排查

| 症状 | 原因与处理 |
|---|---|
| `national CPU scorer library not found` | 旧版独立评分器残留报错；评分核心已并入引擎，重建引擎（`make engine`）即可 |
| `No cached CAMS optical profile covers ...` | CAMS 缓存缺该周期文件——预热端点下载（需 ADS key）或从其他部署拷贝 `.cache/cams` |
| `CAMS full profile does not cover every model cell` | CAMS 水平范围不够——扩大预热 `area` |
| 预测报"GFS 周期不可用" | NOMADS 发布延迟 ~5h45m；用更早的 cycle 或稍后重试 |
| 前端空白/404 | `cd apps/shared-web && npm run build` 后重启后端 |
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
- **开源软件栈**：Rust 生态（rayon、Axum 等）、ecCodes、Svelte/SvelteKit、
  Leaflet、shadcn-svelte、lucide 图标；astral（太阳公式来源，见 `third_party/astral/NOTICE`）。
- 以及每一个抬头看过晚霞、并愿意把观测反馈给数据的人。
