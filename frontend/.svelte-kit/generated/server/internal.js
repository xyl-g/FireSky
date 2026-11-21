
import root from '../root.js';
import { set_building, set_prerendering } from '$app/env/internal';
import { set_assets } from '$app/paths/internal/server';
import { set_manifest, set_read_implementation } from '__sveltekit/server';
import { set_private_env, set_public_env } from '../../../node_modules/@sveltejs/kit/src/runtime/shared-server.js';
import error from '../shared/error-template.js';

export const options = {
	app_template_contains_nonce: false,
	async: false,
	csp: {"mode":"auto","directives":{"upgrade-insecure-requests":false,"block-all-mixed-content":false},"reportOnly":{"upgrade-insecure-requests":false,"block-all-mixed-content":false}},
	csrf_check_origin: true,
	csrf_trusted_origins: [],
	embedded: false,
	env_public_prefix: 'PUBLIC_',
	env_private_prefix: '',
	hash_routing: false,
	hooks: null, // added lazily, via `get_hooks`
	preload_strategy: "modulepreload",
	root,
	service_worker: false,
	service_worker_options: undefined,
	server_error_boundaries: false,
	templates: {
		app: ({ head, body, assets, nonce, env }) => "<!doctype html>\n<html lang=\"zh-CN\">\n  <head>\n    <meta charset=\"utf-8\" />\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n    <meta name=\"description\" content=\"FireSky：朝霞晚霞物理预测、卫星实况与形势图。\" />\n    <link rel=\"icon\" href=\"/favicon.svg\" />\n    <meta name=\"theme-color\" content=\"#f6f2ea\" />\n    <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\" />\n    <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin />\n    <link href=\"https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap\" rel=\"stylesheet\" />\n    <!-- leaflet.css 随地图组件按需加载（见 MapView 等），不再全局阻塞首屏 -->\n    <title>FireSky</title>\n    <script>\n      // 主题初始化 + 液态玻璃折射检测（仅 Chromium 支持 backdrop-filter: url(#svg)）\n      const storedTheme = localStorage.getItem('mode-watcher-mode') || localStorage.getItem('theme') || localStorage.getItem('mode');\n      if (!localStorage.getItem('mode-watcher-mode') && (storedTheme === 'dark' || storedTheme === 'light')) {\n        localStorage.setItem('mode-watcher-mode', storedTheme);\n      }\n      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;\n      const mode = storedTheme === 'dark' || storedTheme === 'light' ? storedTheme : (prefersDark ? 'dark' : 'light');\n      document.documentElement.classList.toggle('dark', mode === 'dark');\n      if (/\\s(Chrome|Chromium|Edg)\\/\\d+/.test(navigator.userAgent) && !/iPhone|iPad/.test(navigator.userAgent)) {\n        document.documentElement.classList.add('chromium-glass');\n      }\n    </script>\n    " + head + "\n  </head>\n  <body data-sveltekit-preload-data=\"hover\">\n    <div style=\"display: contents\">" + body + "</div>\n  </body>\n</html>\n",
		error
	},
	version_hash: "6467sm"
};

export async function get_hooks() {
	let handle;
	let handleFetch;
	let handleError;
	let handleValidationError;
	let init;
	

	let reroute;
	let transport;
	

	return {
		handle,
		handleFetch,
		handleError,
		handleValidationError,
		init,
		reroute,
		transport
	};
}

export { set_assets, set_building, set_manifest, set_prerendering, set_private_env, set_public_env, set_read_implementation };
