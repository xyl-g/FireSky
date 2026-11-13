# FireSky · 共享 Web SPA

Vite 6 + Svelte 5 纯浏览器单页应用，是 Docker 与本地 Rust HTTP 服务共同托管的
唯一产品 Web 页面（退役方案 Phase 1/2：
`docs/PYTHON_TAURI_PYSIDE_RETIREMENT_PLAN.zh-CN.md`）。前身是
`apps/desktop-tauri` 的 Svelte 应用；Tauri 窗口壳已退役，浏览器分支成为唯一形态。

## 页面能力

- 单点预测 / 区域预测 / 实况观测工作台（准备确认、协作取消、双模型结果、剖面与台账）。
- 数据与下载（库存、检查、下载探测、任务历史与恢复）。
- 设置（外观、EC 下载源、缓存保留策略、地图来源）。
- 地图选点与区域拖绘（天地图栅格 / OpenFreeMap 矢量）。

桌面专属能力（打开宿主缓存目录、退出共享服务、CAMS Key 本机写入、宿主菜单/窗口
生命周期）由原生桌面（macOS SwiftUI + Rust FFI）提供，浏览器端显式不支持。

## IPC

`src/lib/ipc.ts` 是唯一 IPC 封装，browser-only：全部经同源 HTTP 任务通道
`/api/v1/firesky/desktop/*`（`engine/src/http/desktop.rs`）。协议错误码与取消语义
见 `src/lib/browser-transport.ts`。

## 开发与验证

前置：Node ≥ 20、npm；运行与构建不需要 Python。生产服务端是 Rust
（`make serve`，默认静态根即本 package 的 `dist/`）。

```bash
cd apps/shared-web
npm ci                 # 首次准备依赖
npm run dev            # Vite 1420，/api/v1/firesky 代理到 127.0.0.1:8765
npm run check          # svelte-check
npm test               # Node 内置测试：IPC helper / 显示逻辑 / 打包脚本
npm run test:ui        # Playwright（vite dev + HTTP fixture mock）
npm run build          # 生产构建 → dist/
npm run smoke:http     # 对真实 Rust HTTP 运行时的免下载冒烟
```

`make frontend` / `make check` / Docker 三阶段构建均已指向本 package。
