# 晓洋站点 · 前端（单 SvelteKit 应用）

个人展示站（摄影/日志/软件工具箱 + 管理中心）与朝晚霞预测站在**同一个 SvelteKit 应用**中。

## 设计规范

UI 开发和 review 先读取 [DESIGN.md](DESIGN.md) 与 [AGENTS.md](AGENTS.md)。
规范按 Awesome DESIGN.md 的思路组织，记录本项目的液态玻璃、主题、组件、响应式及验收要求，
具体样式以 `src/app.css` 和现有组件为准。它是开发约定，不是运行时主题插件。

仓库分析、参考品牌和使用示例见 [接入说明](docs/design-md-integration.md)。

## 路由地图

| 分组 | 路径 | 说明 |
|---|---|---|
| (site) | `/` `/photos` `/logs` `/logs/post?slug=` `/software` `/software/app?id=` `/about` | 公开展示站（液态玻璃风格） |
| (studio) | `/studio`（登录）`/studio/console` | 统一管理中心：内容/评论/账号/计算/队列/数据/定时任务 |
| (app) | `/forecast` `/now` `/outdoor` `/outlook` `/windy` `/history` `/feedback` `/settings` | 朝晚霞预测站（登录后使用） |
| (admin) | `/admin` `/admin/users` `/admin/approvals` `/admin/compute` `/admin/queue` `/admin/tasks` `/admin/data` | 兼容路径，跳转到 `/studio/console` 对应区域 |
| (public) | `/login` `/register` | 预测站账号登录/注册 |

> 认证统一：一处登录（`/login` 或 `/studio`），三端通用（JWT 双 token + 自动刷新）。

## 命令

```bash
npm install        # 安装依赖
npm run dev        # 开发（http://localhost:5173，/api 代理到 Go 网关 :8080）
npm run build      # 静态产物 → build/（SPA fallback）
npm run check      # svelte-check 类型检查
```

## 后端

- 开发：Go 网关 `:8080`（vite 代理 `/api` 与 `/reports`），Python 计算 `:8765`（截面接口代理）
- 生产：整站 `build/` 可由 Go 网关 embed 或任意静态托管 + 反代
