import { defineConfig, loadEnv } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// 共享 Web SPA：dev 端口 1420，/api/v1/firesky 代理到本地 Rust HTTP 服务。
export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
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
}));
