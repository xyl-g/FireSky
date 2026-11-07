/**
 * 根路径入口：直接进入预测工作台。
 *
 * 服务端所有未命中静态资源的路径都回退到 SPA 壳（200.html），因此 `/`
 * 必须在前端显式重定向 —— 否则客户端路由找不到与 `/` 匹配的页面，
 * 页面会渲染成空白/404。使用 replaceState 语义（redirect）避免用户按返回
 * 键又回到空根路径。
 */
import { redirect } from '@sveltejs/kit';

export const prerender = false;

export function load() {
  redirect(307, '/forecast');
}
