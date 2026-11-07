# 更新日志（Changelog）

产品版本遵循语义化版本（SemVer）。算法版本（`2026.*.*.*`）独立演进，
记录在 `src/firesky/version.py` 与预测契约端点中，与产品版本互不影响。

English: product releases follow SemVer; the scoring algorithm version
(`2026.*.*.*`) evolves independently — see `src/firesky/version.py` and the
contract endpoint.

## v0.1.1 (2026-11-07)

首次发布后的修正与改进。Fixes and improvements since v0.1.0.

**修复**

- EC 单点：`ecmwf-opendata` 依赖未声明（新增 `ec` extra）；下载清单补
  2t/2d 地表温/露点（部分时效公开数据不发布，按可选增强处理，缺失时
  LCL 云底自动回退），EC 剖面输出字段集与 GFS 对齐
- 实况：移除机场 METAR 修正链路与 HCAI 下载路径（下载过慢），校准帧
  改为 HSD 分段独占；satpy/pyresample/aiohttp 等实况依赖此前未声明，
  新增 `nowcast` extra
- 实况发布门：覆盖率按文档契约逐采样点计算；语义改为**有可用采样即
  发布**，覆盖率随结果披露（`calibrated_sample_coverage_ratio`），
  仅零覆盖 fail-closed
- 旧原生 dylib 残留已废弃符号：重建后 EC 路径回归稳定
- 根路径 `/` 自动跳转预测工作台；文档（PRINCIPLES/README 中英）
  GitHub 数学渲染全面修复（转义、列表结构、lt/gt）

**改进**

- CAMS/ADS 批量预取：事件窗口多个时刻合并为一次 ADS 请求，冷启动
  CAMS 阶段时延约减半
- GFS/EC 单点报告字段集对齐（70 vs 70，69 共有；仅剩源特有字段）
- 精简代码库：移除未接入服务路径的模块与前端组件，收敛公开 payload
  字段到实际产出范围
- 新增 GitHub Actions CI（pytest / cargo test / svelte-check / docker
  build / macOS+Windows 桌面打包）；DEPLOY 补实况预热 cron 示例与
  token 步骤
- **PySide6 桌面应用**（`desktop/app.py`）：本地单机形态，与 Web 服务
  共用同一预测核心路径（数值一致）；观测点表单/常用城市/进度日志/
  峰值档位卡片/逐五分钟质量曲线与台账；CI 自动出 macOS dmg 与
  Windows exe 目录

## v0.1.0 (2026-11-07)

首个公开发布版本。First public release.

**引擎（Python + Rust）**

- 完整预测链路：GFS 0.25° 子集下载（fail-closed 位图掩膜 + mmap 解码缓存）、
  三族云柱派生、ECMWF IFS 第二模式源、CAMS 11 层 532nm 气溶胶剖面（含
  lead 回退）
- Rust 光追评分器（CPU）：逐观测点 × 逐五分钟步长的太阳入射 + 观测者返程
  双向追踪，含地球曲率、地形/海面下界、半拉格朗日风场平流（RK2 中点风）、
  36 方位 × 90 仰角视角球带积分、目标甲板自遮蔽距离衰减与连接迟滞、时间
  括号占空比混合
- 全国区域批处理（共享张量批量，90 个 Rust 契约测试）与单点精算
- 云属诊断：压力族兼容判定 + 证据驱动云属代理 v2（相态 + 教程经验厚度
  阈值，显示专用、不参与评分），前端附烧霞适配度排序
- 实况 nowcast：向日葵 9 HSD + HCAI 校准卫星云柱（多观测 + 落地判定）

**服务**

- FastAPI 单进程同时提供 API（`/api/v1/firesky/*`）与前端 SPA 静态托管
- 异步预测 job（SSE 进度 / 取消 / 相同请求去重）、全国摘要 / 截面 / 诊断
  端点、数据资产状态与 prune
- CAMS/ADS 下载有界并发（默认 2，`FIRESKY_CAMS_DOWNLOAD_WORKERS` 可调 1–4），
  缓解冷缓存首跑的 ADS 排队等待

**前端（SvelteKit + Tailwind 4）**

- 预测 / 实况 / 形势图三页 + 管理控制台（总览 / 实时计算 / 运行队列 / 数据资产）
- 全站液态玻璃材质体系（明暗双主题、亮色 Warm Sand 色板统一）、地图图层
  控制、云属卡片

**部署**

- Docker Compose 三阶段构建（Rust → Node → python:3.13-slim，非 root 运行）
- 裸机部署文档覆盖 Linux systemd / macOS launchd / Windows（PowerShell、
  任务计划、NSSM、WSL2）三平台，含反代 / cron 定时 / 安全清单 / 排障
- GPL-3.0-only 许可证；致谢 SunsetBot 火烧云教程
  （<https://www.sunsetbot.top/halo/>）
