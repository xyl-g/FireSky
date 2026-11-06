# 更新日志（Changelog）

产品版本遵循语义化版本（SemVer）。算法版本（`2026.*.*.*`）独立演进，
记录在 `src/firesky/version.py` 与预测契约端点中，与产品版本互不影响。

English: product releases follow SemVer; the scoring algorithm version
(`2026.*.*.*`) evolves independently — see `src/firesky/version.py` and the
contract endpoint.

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
