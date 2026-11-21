
// this file is generated — do not edit it


/// <reference types="@sveltejs/kit" />

/**
 * This module provides access to environment variables that are injected _statically_ into your bundle at build time and are limited to _private_ access.
 * 
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 * 
 * Static environment variables are [loaded by Vite](https://vitejs.dev/guide/env-and-mode.html#env-files) from `.env` files and `process.env` at build time and then statically injected into your bundle at build time, enabling optimisations like dead code elimination.
 * 
 * **_Private_ access:**
 * 
 * - This module cannot be imported into client-side code
 * - This module only includes variables that _do not_ begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) _and do_ start with [`config.kit.env.privatePrefix`](https://svelte.dev/docs/kit/configuration#env) (if configured)
 * 
 * For example, given the following build time environment:
 * 
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://site.com
 * ```
 * 
 * With the default `publicPrefix` and `privatePrefix`:
 * 
 * ```ts
 * import { ENVIRONMENT, PUBLIC_BASE_URL } from '$env/static/private';
 * 
 * console.log(ENVIRONMENT); // => "production"
 * console.log(PUBLIC_BASE_URL); // => throws error during build
 * ```
 * 
 * The above values will be the same _even if_ different values for `ENVIRONMENT` or `PUBLIC_BASE_URL` are set at runtime, as they are statically replaced in your code with their build time values.
 */
declare module '$env/static/private' {
	export const RUST_LOG: string;
	export const LDFLAGS: string;
	export const INS_DESKTOPICON: string;
	export const CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY: string;
	export const NODE: string;
	export const INIT_CWD: string;
	export const INS_NOUPDATE: string;
	export const TERM: string;
	export const SHELL: string;
	export const INS_InstallFeature: string;
	export const BROWSER_USE_ACCESS_PIPE_PATH: string;
	export const HOMEBREW_BOTTLE_DOMAIN: string;
	export const TMPDIR: string;
	export const HOMEBREW_REPOSITORY: string;
	export const CPPFLAGS: string;
	export const npm_config_global_prefix: string;
	export const INS_NetworkKVMContentTransfer: string;
	export const CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS: string;
	export const BROWSER_USE_ACCESS_VERIFYING_IDENTITY: string;
	export const FPATH: string;
	export const MallocNanoZone: string;
	export const COLOR: string;
	export const NO_COLOR: string;
	export const INS_LOCKUPDATE: string;
	export const npm_config_noproxy: string;
	export const npm_config_registry: string;
	export const npm_config_local_prefix: string;
	export const ZSH: string;
	export const LC_ALL: string;
	export const NO_PROXY: string;
	export const http_proxy: string;
	export const USER: string;
	export const INS_NotifyUpdate: string;
	export const COMMAND_MODE: string;
	export const npm_config_globalconfig: string;
	export const INS_NetworkKVM: string;
	export const INS_DisableCA: string;
	export const INS_Autostartup: string;
	export const envDDPMStatus: string;
	export const SSH_AUTH_SOCK: string;
	export const __CF_USER_TEXT_ENCODING: string;
	export const CODEX_PERMISSION_PROFILE: string;
	export const npm_execpath: string;
	export const PAGER: string;
	export const BROWSER_USE_ACCESS_EXECUTOR_ID: string;
	export const GTPPROXY_SERVER_CONFIG_DIR: string;
	export const LSCOLORS: string;
	export const CODEX_APP_RUNTIME_CLI_PATH: string;
	export const all_proxy: string;
	export const INS_NetworkKVMContentTransferPort: string;
	export const PATH: string;
	export const ZSH_TMUX_AUTOSTART: string;
	export const npm_package_json: string;
	export const _: string;
	export const LaunchInstanceID: string;
	export const npm_config_userconfig: string;
	export const npm_config_init_module: string;
	export const INS_NetworkKVMIncomingPort: string;
	export const CODEX_THREAD_ID: string;
	export const npm_command: string;
	export const PWD: string;
	export const GTPPROXY_SERVER_DIR: string;
	export const DISABLE_AUTO_UPDATE: string;
	export const npm_lifecycle_event: string;
	export const EDITOR: string;
	export const npm_package_name: string;
	export const LANG: string;
	export const INS_NetworkKVMOutgoingPort: string;
	export const INS_NetworkKVMAutoConnect: string;
	export const npm_config_npm_version: string;
	export const XPC_FLAGS: string;
	export const CODEX_TOOL_CALL_ID: string;
	export const CODEX_CI: string;
	export const ZSH_TMUX_AUTOSTARTED: string;
	export const npm_config_node_gyp: string;
	export const https_proxy: string;
	export const npm_package_version: string;
	export const XPC_SERVICE_NAME: string;
	export const CODEX_VERSION: string;
	export const SHLVL: string;
	export const HOME: string;
	export const CODEX_SHELL: string;
	export const no_proxy: string;
	export const LOG_FORMAT: string;
	export const HOMEBREW_PREFIX: string;
	export const GH_PAGER: string;
	export const CODEX_SAGE_BACKFILL_TRACKER_TAB_REUSE: string;
	export const npm_config_cache: string;
	export const envDDPMFwStatus: string;
	export const LOGNAME: string;
	export const LESS: string;
	export const CODEX_MCP_NODE_PATH: string;
	export const npm_lifecycle_script: string;
	export const LC_CTYPE: string;
	export const CODEX_TECTONIC_PATH: string;
	export const npm_config_user_agent: string;
	export const INFOPATH: string;
	export const HOMEBREW_CELLAR: string;
	export const OSLogRateLimit: string;
	export const GIT_PAGER: string;
	export const CODEX_SESSION_ID: string;
	export const SECURITYSESSIONID: string;
	export const DDPM_INSTALL_MODE: string;
	export const CODEX_APP_TOOLS_PIPE_PATH: string;
	export const npm_node_execpath: string;
	export const npm_config_prefix: string;
	export const COLORTERM: string;
	export const CODEX_INTERNAL_ORIGINATOR_OVERRIDE: string;
	export const NODE_ENV: string;
}

/**
 * This module provides access to environment variables that are injected _statically_ into your bundle at build time and are _publicly_ accessible.
 * 
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 * 
 * Static environment variables are [loaded by Vite](https://vitejs.dev/guide/env-and-mode.html#env-files) from `.env` files and `process.env` at build time and then statically injected into your bundle at build time, enabling optimisations like dead code elimination.
 * 
 * **_Public_ access:**
 * 
 * - This module _can_ be imported into client-side code
 * - **Only** variables that begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) (which defaults to `PUBLIC_`) are included
 * 
 * For example, given the following build time environment:
 * 
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://site.com
 * ```
 * 
 * With the default `publicPrefix` and `privatePrefix`:
 * 
 * ```ts
 * import { ENVIRONMENT, PUBLIC_BASE_URL } from '$env/static/public';
 * 
 * console.log(ENVIRONMENT); // => throws error during build
 * console.log(PUBLIC_BASE_URL); // => "http://site.com"
 * ```
 * 
 * The above values will be the same _even if_ different values for `ENVIRONMENT` or `PUBLIC_BASE_URL` are set at runtime, as they are statically replaced in your code with their build time values.
 */
declare module '$env/static/public' {
	
}

/**
 * This module provides access to environment variables set _dynamically_ at runtime and that are limited to _private_ access.
 * 
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 * 
 * Dynamic environment variables are defined by the platform you're running on. For example if you're using [`adapter-node`](https://github.com/sveltejs/kit/tree/main/packages/adapter-node) (or running [`vite preview`](https://svelte.dev/docs/kit/cli)), this is equivalent to `process.env`.
 * 
 * **_Private_ access:**
 * 
 * - This module cannot be imported into client-side code
 * - This module includes variables that _do not_ begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) _and do_ start with [`config.kit.env.privatePrefix`](https://svelte.dev/docs/kit/configuration#env) (if configured)
 * 
 * > [!NOTE] In `dev`, `$env/dynamic` includes environment variables from `.env`. In `prod`, this behavior will depend on your adapter.
 * 
 * > [!NOTE] To get correct types, environment variables referenced in your code should be declared (for example in an `.env` file), even if they don't have a value until the app is deployed:
 * >
 * > ```env
 * > MY_FEATURE_FLAG=
 * > ```
 * >
 * > You can override `.env` values from the command line like so:
 * >
 * > ```sh
 * > MY_FEATURE_FLAG="enabled" npm run dev
 * > ```
 * 
 * For example, given the following runtime environment:
 * 
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://site.com
 * ```
 * 
 * With the default `publicPrefix` and `privatePrefix`:
 * 
 * ```ts
 * import { env } from '$env/dynamic/private';
 * 
 * console.log(env.ENVIRONMENT); // => "production"
 * console.log(env.PUBLIC_BASE_URL); // => undefined
 * ```
 */
declare module '$env/dynamic/private' {
	export const env: {
		RUST_LOG: string;
		LDFLAGS: string;
		INS_DESKTOPICON: string;
		CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY: string;
		NODE: string;
		INIT_CWD: string;
		INS_NOUPDATE: string;
		TERM: string;
		SHELL: string;
		INS_InstallFeature: string;
		BROWSER_USE_ACCESS_PIPE_PATH: string;
		HOMEBREW_BOTTLE_DOMAIN: string;
		TMPDIR: string;
		HOMEBREW_REPOSITORY: string;
		CPPFLAGS: string;
		npm_config_global_prefix: string;
		INS_NetworkKVMContentTransfer: string;
		CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS: string;
		BROWSER_USE_ACCESS_VERIFYING_IDENTITY: string;
		FPATH: string;
		MallocNanoZone: string;
		COLOR: string;
		NO_COLOR: string;
		INS_LOCKUPDATE: string;
		npm_config_noproxy: string;
		npm_config_registry: string;
		npm_config_local_prefix: string;
		ZSH: string;
		LC_ALL: string;
		NO_PROXY: string;
		http_proxy: string;
		USER: string;
		INS_NotifyUpdate: string;
		COMMAND_MODE: string;
		npm_config_globalconfig: string;
		INS_NetworkKVM: string;
		INS_DisableCA: string;
		INS_Autostartup: string;
		envDDPMStatus: string;
		SSH_AUTH_SOCK: string;
		__CF_USER_TEXT_ENCODING: string;
		CODEX_PERMISSION_PROFILE: string;
		npm_execpath: string;
		PAGER: string;
		BROWSER_USE_ACCESS_EXECUTOR_ID: string;
		GTPPROXY_SERVER_CONFIG_DIR: string;
		LSCOLORS: string;
		CODEX_APP_RUNTIME_CLI_PATH: string;
		all_proxy: string;
		INS_NetworkKVMContentTransferPort: string;
		PATH: string;
		ZSH_TMUX_AUTOSTART: string;
		npm_package_json: string;
		_: string;
		LaunchInstanceID: string;
		npm_config_userconfig: string;
		npm_config_init_module: string;
		INS_NetworkKVMIncomingPort: string;
		CODEX_THREAD_ID: string;
		npm_command: string;
		PWD: string;
		GTPPROXY_SERVER_DIR: string;
		DISABLE_AUTO_UPDATE: string;
		npm_lifecycle_event: string;
		EDITOR: string;
		npm_package_name: string;
		LANG: string;
		INS_NetworkKVMOutgoingPort: string;
		INS_NetworkKVMAutoConnect: string;
		npm_config_npm_version: string;
		XPC_FLAGS: string;
		CODEX_TOOL_CALL_ID: string;
		CODEX_CI: string;
		ZSH_TMUX_AUTOSTARTED: string;
		npm_config_node_gyp: string;
		https_proxy: string;
		npm_package_version: string;
		XPC_SERVICE_NAME: string;
		CODEX_VERSION: string;
		SHLVL: string;
		HOME: string;
		CODEX_SHELL: string;
		no_proxy: string;
		LOG_FORMAT: string;
		HOMEBREW_PREFIX: string;
		GH_PAGER: string;
		CODEX_SAGE_BACKFILL_TRACKER_TAB_REUSE: string;
		npm_config_cache: string;
		envDDPMFwStatus: string;
		LOGNAME: string;
		LESS: string;
		CODEX_MCP_NODE_PATH: string;
		npm_lifecycle_script: string;
		LC_CTYPE: string;
		CODEX_TECTONIC_PATH: string;
		npm_config_user_agent: string;
		INFOPATH: string;
		HOMEBREW_CELLAR: string;
		OSLogRateLimit: string;
		GIT_PAGER: string;
		CODEX_SESSION_ID: string;
		SECURITYSESSIONID: string;
		DDPM_INSTALL_MODE: string;
		CODEX_APP_TOOLS_PIPE_PATH: string;
		npm_node_execpath: string;
		npm_config_prefix: string;
		COLORTERM: string;
		CODEX_INTERNAL_ORIGINATOR_OVERRIDE: string;
		NODE_ENV: string;
		[key: `PUBLIC_${string}`]: undefined;
		[key: `${string}`]: string | undefined;
	}
}

/**
 * This module provides access to environment variables set _dynamically_ at runtime and that are _publicly_ accessible.
 * 
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 * 
 * Dynamic environment variables are defined by the platform you're running on. For example if you're using [`adapter-node`](https://github.com/sveltejs/kit/tree/main/packages/adapter-node) (or running [`vite preview`](https://svelte.dev/docs/kit/cli)), this is equivalent to `process.env`.
 * 
 * **_Public_ access:**
 * 
 * - This module _can_ be imported into client-side code
 * - **Only** variables that begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) (which defaults to `PUBLIC_`) are included
 * 
 * > [!NOTE] In `dev`, `$env/dynamic` includes environment variables from `.env`. In `prod`, this behavior will depend on your adapter.
 * 
 * > [!NOTE] To get correct types, environment variables referenced in your code should be declared (for example in an `.env` file), even if they don't have a value until the app is deployed:
 * >
 * > ```env
 * > MY_FEATURE_FLAG=
 * > ```
 * >
 * > You can override `.env` values from the command line like so:
 * >
 * > ```sh
 * > MY_FEATURE_FLAG="enabled" npm run dev
 * > ```
 * 
 * For example, given the following runtime environment:
 * 
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://example.com
 * ```
 * 
 * With the default `publicPrefix` and `privatePrefix`:
 * 
 * ```ts
 * import { env } from '$env/dynamic/public';
 * console.log(env.ENVIRONMENT); // => undefined, not public
 * console.log(env.PUBLIC_BASE_URL); // => "http://example.com"
 * ```
 * 
 * ```
 * 
 * ```
 */
declare module '$env/dynamic/public' {
	export const env: {
		[key: `PUBLIC_${string}`]: string | undefined;
	}
}
