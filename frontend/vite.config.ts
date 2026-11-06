import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit()
  ],
  server: {
    port: 5173,
    proxy: {
      // Keep profile exploration usable while the Go gateway is being
      // developed or restarted; production serves the same route via Go.
      '/api/forecast/national/cross-section': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true
      },
      // Phase 1: ALL API traffic goes through Go gateway on :8080
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true
      },
      '/reports': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true
      }
    }
  },
  build: {
    target: 'es2022'
  }
});
