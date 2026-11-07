import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      // SPA 壳与预渲染首页分离：index.html 是预渲染首页，200.html 是 SPA fallback。
      // Go 网关（router.go serveSPA/serveEmbeddedSPA）按此约定路由。
      fallback: '200.html',
      precompress: false,
      strict: false
    }),
    alias: {
      '@': 'src/lib'
    },
    paths: {
      base: ''
    }
  }
};

export default config;
