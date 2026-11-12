import { defineConfig, loadEnv } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Tauri 约定：dev 端口固定且必须可用（tauri.conf.json build.devUrl 对应 1420）。
export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  envPrefix: ["VITE_", "TAURI_ENV_"],
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
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    outDir: "dist",
    // dist/index.html 里的占位页会被真实构建产物覆盖（占位页仅用于
    // tauri-build 的 frontendDist 存在性校验，见 README「当前限制」）。
    emptyOutDir: true,
    target: "es2021",
  },
}));
