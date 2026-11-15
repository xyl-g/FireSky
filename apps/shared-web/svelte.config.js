import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

// 纯 SPA（非 SvelteKit）——见 docs/archive/TAURI_DESKTOP_PLAN.md §2.2 选型。
export default {
  preprocess: vitePreprocess(),
};
