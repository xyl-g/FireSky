import { defineConfig, loadEnv, type PluginOption } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { visualizer } from "rollup-plugin-visualizer";

// 共享 Web SPA：dev 端口 1420，/api/v1/firesky 代理到本地 Rust HTTP 服务。
// `FIRESKY_ANALYZE=1 npm run build` 时额外产出 bundle-stats.html（产物体积分析）。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "FIRESKY_ANALYZE");
  return {
    plugins: [
      svelte(),
      ...(env.FIRESKY_ANALYZE === "1"
        ? [visualizer({ filename: "bundle-stats.html", gzipSize: true, brotliSize: true }) as PluginOption]
        : []),
    ],
    clearScreen: false,
    server: {
      port: 1420,
      strictPort: true,
      proxy: {
        "/api/v1/firesky": {
          target: loadEnv(mode, ".", "FIRESKY_").FIRESKY_DEV_API_ORIGIN || "http://127.0.0.1:8765",
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      target: "es2021",
    },
  };
});
