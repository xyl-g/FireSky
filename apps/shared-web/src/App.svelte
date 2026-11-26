<script lang="ts">
  import { onMount, tick } from "svelte";
  import MapView from "./lib/views/MapView.svelte";
  import WorkbenchView from "./lib/views/WorkbenchView.svelte";
  import SettingsView from "./lib/views/SettingsView.svelte";
  import DataView from "./lib/views/DataView.svelte";
  import TaskHistoryView from "./lib/views/TaskHistoryView.svelte";
  import { Activity, Database, Map, MapPin, Settings, RefreshCw, History } from "@lucide/svelte";
  import type { ForecastKind, MapOptions, ObservationSpot } from "./lib/workbench-types";
  import type { BaseId, SourceId } from "./lib/map-grid";
  import type { RetryRequest } from "./lib/task-history";
  import { parseRestoredRequest } from "./lib/workbench-restore";
  import { loadElevationSource, saveElevationSource, loadAerosolSource, saveAerosolSource, parseAerosolSource, type AerosolSource, type ElevationSource } from "./lib/settings-state";
  import {
    hello, onDesktopAction, onEngineStatus, quitDesktop, rpcRestart, rpcStatus, RpcError, type DesktopAction, type EngineStatus,
  } from "./lib/ipc";

  type ViewId = ForecastKind | "data" | "history" | "settings";
  function preference<T extends string>(key: string, choices: readonly T[], fallback: T): T {
    try { const value = localStorage.getItem(key); return choices.includes(value as T) ? value as T : fallback; }
    catch { return fallback; }
  }

  const nav = [
    { id: "point", label: "单点预测", icon: MapPin },
    { id: "regional", label: "区域预测", icon: Map },
    { id: "nowcast", label: "实况观测", icon: Activity },
    { id: "data", label: "数据与下载", icon: Database },
    { id: "history", label: "任务历史", icon: History },
    { id: "settings", label: "设置", icon: Settings },
  ] as const;
  let activePage = $derived(nav.find((item) => item.id === current)!);

  let current: ViewId = $state("point");
  let workKind = $state<ForecastKind>("point");
  let forecastBusy = $state(false);
  let dataBusy = $state(false);
  let workbench = $state<WorkbenchView>();
  let dataView = $state<DataView>();
  let historyView = $state<TaskHistoryView>();
  let exitRequested = $state(false);
  let exiting = $state(false);
  let exitError = $state("");
  let exitDialog: HTMLDialogElement;
  let appearance = $state<"system" | "light" | "dark">(preference("firesky.appearance", ["system", "light", "dark"], "system"));
  let systemDark = $state(false);
  let ecSource = $state(preference("firesky.ecSource", ["ecmwf", "aws", "google"], "ecmwf") as string);
  let aerosolSource = $state<AerosolSource>(loadAerosolSource(localStorage));
  let elevationSource = $state<ElevationSource>(loadElevationSource(localStorage));
  let engineWorkspace = $state("");
  let engineBinary = $state("");
  let showMap = $state(false);
  let mapVisited = $state(false);
  let mapDialog: HTMLDialogElement;
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", weekday: "short" });
  let dateLabel = $state(dateFormat.format(new Date()));
  let engineOk = $state<boolean | null>(null); // null = 检测中
  let engineDetail = $state("正在连接引擎…");
  let reconnecting = $state(false);
  let subscriptionError = $state("");
  let regions = $state<Array<Record<string, unknown>>>([]);
  let engineGeneration = $state(0);
  let dataVisited = $state(false);
  let historyVisited = $state(false);
  let spot = $state<ObservationSpot>({ lat: 30.2741, lon: 120.1551, name: "杭州", elevation_m: 8, timezone_name: "Asia/Shanghai", metadataReady: true });
  let mapState = $state("地图正在加载");
  let mapError = $state("");
  let mapProvider = $state<SourceId>(preference<SourceId>("firesky.mapProvider", ["tianditu", "openfreemap"], "tianditu"));
  let tdtKey = $state("");
  let mapBase = $state<BaseId>(preference<BaseId>("firesky.mapBase", ["vector", "imagery", "terrain"], "imagery"));
  const dark = $derived(appearance === "dark" || appearance === "system" && systemDark);
  const mapOptions = $derived<MapOptions>({ provider: mapProvider, tdtKey, base: mapBase, dark });
  const subtitle = $derived.by(() => current === "data" ? "原始数据 · 周期与时效 · 按需下载" : current === "history" ? "最近任务 · 状态与事件 · 恢复参数" : current === "settings" ? "外观、数据源与 Rust 服务" : current === "regional" ? "区域质量分布 · GFS / EC 双模式" : `${spot.name} · ${spot.lat.toFixed(4)}° N, ${spot.lon.toFixed(4)}° E`);
  let alive = false;
  let connectionAttempt = 0;
  let generation = -1;

  function describeError(error: unknown) {
    return error instanceof RpcError ? `${error.code}: ${error.message}` : String(error);
  }

  async function verifyEngine(status: EngineStatus) {
    const attempt = ++connectionAttempt;
    generation = Math.max(generation, status.generation);
    engineGeneration = status.generation;
    engineWorkspace = status.workspace ?? "";
    engineBinary = status.engineBinary ?? "";
    if (!status.running) {
      engineOk = false;
      engineDetail = status.spawnError || "引擎已退出，请重新连接。";
      return;
    }
    engineOk = null;
    engineDetail = "正在校验引擎协议…";
    try {
      const result = await hello();
      const latest = await rpcStatus();
      if (!alive || attempt !== connectionAttempt) return;
      if (!latest.running || latest.generation !== status.generation) {
        engineOk = false;
        engineDetail = "连接已变化，请重新连接。";
        return;
      }
      engineOk = result.protocol === 1 && result.engine === "firesky-rust";
      engineWorkspace = result.workspace;
      regions = engineOk ? result.regions : [];
      engineDetail = engineOk
        ? `protocol ${result.protocol} · workspace ${result.workspace}`
        : "协议不匹配";
    } catch (error) {
      if (!alive || attempt !== connectionAttempt) return;
      engineOk = false;
      engineDetail = describeError(error);
    }
  }

  async function reconnect() {
    if (reconnecting) return;
    reconnecting = true;
    engineOk = null;
    engineDetail = "正在重连引擎…";
    try {
      const status = await rpcRestart();
      if (alive) await verifyEngine(status);
    } catch (error) {
      if (alive) {
        engineOk = false;
        engineDetail = describeError(error);
      }
    } finally {
      if (alive) reconnecting = false;
    }
  }

  /** 浏览器无推送通道：定期探测状态，引擎退出/服务失联时降级徽章，恢复后走完整重连。 */
  async function heartbeat() {
    if (!alive || exiting || exitRequested || reconnecting) return;
    try {
      const status = await rpcStatus();
      if (!alive || reconnecting) return;
      if (!status.running) {
        if (engineOk === true) { ++connectionAttempt; engineOk = false; }
        if (engineOk === false) engineDetail = status.spawnError || "引擎已退出，请重新连接。";
        return;
      }
      if (status.generation > generation) { generation = status.generation; engineGeneration = status.generation; }
      if (engineOk === false) void reconnect();
    } catch {
      if (!alive || engineOk !== true || reconnecting) return;
      ++connectionAttempt;
      engineOk = false;
      engineDetail = "引擎心跳失败，请检查服务。";
    }
  }

  async function restoreTask(request: RetryRequest): Promise<boolean> {
    if (forecastBusy || dataBusy || engineOk !== true) return false;
    if (!parseRestoredRequest(request.kind, request.payload)) return false;
    const previousKind = workKind;
    const previousSource = ecSource;
    const previousAerosol = aerosolSource;
    workKind = request.kind;
    await tick();
    if (forecastBusy || dataBusy || engineOk !== true) {
      workKind = previousKind;
      return false;
    }
    if (["ecmwf", "aws", "google"].includes(String(request.payload.ecmwf_source))) {
      ecSource = String(request.payload.ecmwf_source);
    }
    if (["cams", "geos-fp"].includes(String(request.payload.aerosol_source))) {
      aerosolSource = parseAerosolSource(request.payload.aerosol_source);
    }
    if (workbench?.restoreRequest(request.kind, request.payload)) {
      current = request.kind;
      return true;
    }
    ecSource = previousSource;
    aerosolSource = previousAerosol;
    workKind = previousKind;
    return false;
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.target instanceof HTMLElement && event.target.closest("dialog[open]")) return;
    if (event.key === "F5") { event.preventDefault(); desktopAction("refresh"); return; }
    if (!(event.ctrlKey || event.metaKey)) return;
    if (event.key.toLowerCase() === "k") { event.preventDefault(); desktopAction("search"); return; }
    if (forecastBusy) return;
    if (event.key === ",") { event.preventDefault(); current = "settings"; return; }
    const index = Number(event.key) - 1;
    if (index >= 0 && index < nav.length) {
      event.preventDefault();
      current = nav[index].id;
    }
  }

  async function exitNow() {
    if (exiting) return;
    exiting = true; exitError = "";
    try { await quitDesktop(); }
    catch (error) { exitError = describeError(error); exiting = false; exitRequested = true; }
  }
  function requestExit() {
    if (exiting) return;
    if (forecastBusy || dataBusy) { exitRequested = true; exitError = ""; }
    else void exitNow();
  }
  async function cancelAndExit() {
    if (exiting) return;
    exiting = true; exitError = "";
    try {
      await Promise.all([workbench?.cancelTask(), dataView?.cancelTask()]);
      await quitDesktop();
    } catch (error) { exitError = describeError(error); exiting = false; }
  }
  function desktopAction(action: DesktopAction) {
    if (action === "quit") { requestExit(); return; }
    if (exiting || exitRequested) return;
    if (action === "cancel") {
      if (showMap) { showMap = false; return; }
      if (forecastBusy) void workbench?.cancelTask();
      if (dataBusy) void dataView?.cancelTask();
      return;
    }
    if (action === "refresh") {
      if (current === "data") dataView?.refresh();
      else if (current === "history") historyView?.refresh();
      return;
    }
    if (forecastBusy) return;
    if (action === "search") {
      current = "point";
      requestAnimationFrame(() => workbench?.focusSearch());
    } else if (nav.some((item) => item.id === action)) current = action as ViewId;
  }

  $effect(() => { if (current === "data") dataVisited = true; });
  $effect(() => { if (current === "history") historyVisited = true; });
  $effect(() => { if (current === "point" || current === "regional" || current === "nowcast") workKind = current; });
  $effect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try { localStorage.setItem("firesky.appearance", appearance); } catch { /* Optional preference storage. */ }
  });
  $effect(() => { try { localStorage.setItem("firesky.ecSource", ecSource); } catch { /* Optional preference storage. */ } });
  $effect(() => { saveAerosolSource(localStorage, aerosolSource); });
  $effect(() => { saveElevationSource(localStorage, elevationSource); });
  $effect(() => { try { localStorage.setItem("firesky.mapProvider", mapProvider); localStorage.setItem("firesky.mapBase", mapBase); } catch { /* Credentials remain in memory. */ } });
  $effect(() => { if (showMap) { mapVisited = true; mapDialog?.showModal(); } else mapDialog?.close(); });
  $effect(() => { if (exitRequested) exitDialog?.showModal(); else exitDialog?.close(); });
  $effect(() => {
    void mapProvider;
    void tdtKey;
    mapError = "";
  });

  onMount(() => {
    alive = true;
    const clock = window.setInterval(() => { dateLabel = dateFormat.format(new Date()); }, 30_000);
    const pulse = window.setInterval(() => { void heartbeat(); }, 4000);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    systemDark = media.matches;
    const updateSystemTheme = () => { systemDark = media.matches; };
    media.addEventListener("change", updateSystemTheme);
    try {
      const value = localStorage.getItem("firesky.appearance");
      if (value === "system" || value === "light" || value === "dark") appearance = value;
    } catch { /* Keep the system appearance when storage is unavailable. */ }
    const listeners: Array<() => void> = [];
    const subscribe = async (subscription: Promise<() => void>) => {
      try {
        const unlisten = await subscription;
        if (alive) listeners.push(unlisten);
        else unlisten();
      } catch (error) {
        if (alive) subscriptionError = `事件订阅失败：${describeError(error)}`;
      }
    };
    void (async () => {
      await Promise.all([
        subscribe(onDesktopAction(desktopAction)),
        subscribe(onEngineStatus((status) => {
          if (!alive || status.generation < generation) return;
          generation = status.generation;
          engineGeneration = status.generation;
          if (!status.running) {
            ++connectionAttempt;
            engineOk = false;
            engineDetail = status.spawnError || "引擎已退出，请重新连接。";
          }
        })),
      ]);
      try {
        const status = await rpcStatus();
        if (alive) await verifyEngine(status);
      } catch (error) {
        if (alive) {
          engineOk = false;
          engineDetail = describeError(error);
        }
      }
    })();
    return () => {
      alive = false;
      window.clearInterval(clock);
      window.clearInterval(pulse);
      media.removeEventListener("change", updateSystemTheme);
      ++connectionAttempt;
      for (const unlisten of listeners) unlisten();
    };
  });
</script>

<svelte:window onkeydown={onKeydown} />

<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <svg class="brand-mark" viewBox="0 0 64 64" fill="none" aria-hidden="true">
        <rect x="1" y="1" width="62" height="62" rx="14" fill="#212b2e" />
        <path d="M16 33V29a16 16 0 0 1 32 0v4Z" fill="#ff9466" />
        <path d="M32 6v2m-19 5 2 2m36-2-2 2M23 46h18" stroke="#ff9466" stroke-width="3" stroke-linecap="round" />
        <path d="M12 37h40" stroke="white" stroke-width="3" stroke-linecap="round" />
      </svg>
      <div>FireSky</div>
    </div>
    <div class="nav-caption">观测工作台</div>
    <nav aria-label="主要导航">
      {#each nav as item (item.id)}
        <button
          class="nav-item"
          class:active={current === item.id}
          class:settings={item.id === "settings"}
          aria-current={current === item.id ? "page" : undefined}
          aria-label={item.label}
          title={item.label}
          disabled={forecastBusy}
          onclick={() => (current = item.id)}
        >
          <item.icon size={17} />
          <span>{item.label}</span>
        </button>
      {/each}
    </nav>
    <div class="engine-badge" class:ok={engineOk === true} class:bad={engineOk === false}>
      <span class="dot">●</span>
      <span>
        {engineOk === null ? "连接中" : engineOk ? "引擎已连接" : "引擎未连接"}
        {#if engineOk === false}<small title={engineDetail}>{engineDetail}</small><button class="reconnect" disabled={reconnecting || forecastBusy} onclick={reconnect}><RefreshCw size={11} />{reconnecting ? "重连中…" : "重新连接"}</button>{/if}
        {#if subscriptionError}<small title={subscriptionError}>{subscriptionError}</small>{/if}
      </span>
    </div>
  </aside>

  <main class="content">
    <header class="topbar">
      <div><h1>{activePage.label}</h1><p>{subtitle}</p></div>
      <time class="date-chip">{dateLabel}</time>
    </header>
    <div class="workspace">
    <div hidden={current === "data" || current === "settings"}>
      <WorkbenchView bind:this={workbench} kind={workKind} online={engineOk === true} generation={engineGeneration} {regions} {ecSource} {aerosolSource} {elevationSource} {mapOptions} bind:spot onOpenMap={() => { showMap = true; }} onBusy={(value) => { forecastBusy = value; }} />
    </div>
    {#if dataVisited}
      <div hidden={current !== "data"}>
        <DataView bind:this={dataView} online={engineOk === true} generation={engineGeneration} {regions} bind:ecSource bind:aerosolSource visible={current === "data"} onBusy={(value) => { dataBusy = value; }} />
      </div>
    {/if}
    {#if historyVisited}
      <div hidden={current !== "history"}>
        <TaskHistoryView bind:this={historyView} online={engineOk === true} generation={engineGeneration} visible={current === "history"} retryDisabled={forecastBusy || dataBusy} onRestore={restoreTask} />
      </div>
    {/if}
    {#if current === "settings"}
      <SettingsView bind:provider={mapProvider} bind:tdtKey bind:base={mapBase} bind:appearance bind:ecSource bind:aerosolSource bind:elevationSource online={engineOk === true} generation={engineGeneration} {engineWorkspace} {engineBinary} onReconnect={reconnect} {reconnecting} />
    {/if}
    </div>
  </main>
</div>

<dialog bind:this={mapDialog} class="map-dialog" aria-labelledby="map-picker-title" oncancel={() => { showMap = false; }} onclose={() => { showMap = false; }}>
  <header><div><h2 id="map-picker-title">地图选点</h2><span class="coordinates">{spot.lat.toFixed(5)}°, {spot.lon.toFixed(5)}°</span></div><button onclick={() => { showMap = false; }}>完成</button></header>
  {#if mapVisited}<div class="persistent-map"><MapView {...mapOptions} {spot}
    onPick={(lat, lon) => { spot = { ...spot, lat, lon, name: "地图选点", metadataReady: false, metadataSource: undefined }; }}
    onReady={() => { mapState = ""; mapError = ""; }}
    onError={(error) => { if (error.kind === "initialization") mapState = "地图初始化失败"; if (error.kind !== "tiles") mapError = error.message; }} /></div>{/if}
  {#if mapState || mapError}<footer aria-live="polite">{mapState}{mapError ? ` · ${mapError}` : ""}</footer>{/if}
</dialog>

<dialog bind:this={exitDialog} class="exit-dialog" aria-labelledby="exit-title" oncancel={(event) => { event.preventDefault(); if (!exiting) exitRequested = false; }}>
  <h2 id="exit-title">退出 FireSky？</h2>
  <p>{exiting ? "正在等待任务结束并退出…" : "仍有下载或计算任务正在运行。取消任务需等待当前阶段结束；强制退出会中断未完成的工作。"}</p>
  {#if exitError}<p class="error" role="alert">{exitError}</p>{/if}
  <footer>
    <button disabled={exiting} onclick={() => { exitRequested = false; }}>继续运行</button>
    <button disabled={exiting} onclick={cancelAndExit}>取消任务并退出</button>
    <button class="destructive" disabled={exiting} onclick={exitNow}>强制退出</button>
  </footer>
</dialog>

<style>
  .shell { display: flex; width: 100%; height: 100%; }
  .sidebar {
    display: flex;
    flex-direction: column;
    flex: 0 0 196px;
    border-right: 1px solid var(--fs-border);
    background: var(--fs-sidebar);
    backdrop-filter: blur(24px) saturate(125%);
    box-shadow: inset -1px 0 0 var(--fs-edge);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 22px 21px 28px;
    font-size: 17px;
    font-weight: 600;
    color: var(--fs-ink);
  }
  .brand-mark { width: 36px; height: 36px; color: var(--fs-accent); }
  .nav-caption { padding: 0 21px 10px; font-size: 11px; font-weight: 500; color: var(--fs-ink2); }
  nav { display: flex; flex-direction: column; flex: 1; gap: 4px; padding: 0 12px 12px; }
  .nav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 40px;
    padding: 0 10px;
    border: 0;
    border-radius: 6px;
    cursor: pointer;
    text-align: left;
    font: inherit;
    font-size: 13px;
    color: var(--fs-ink2);
    background: transparent;
    transition: background 0.15s, color 0.15s, box-shadow 0.15s;
  }
  .nav-item :global(svg) { width: 18px; height: 18px; flex: 0 0 18px; }
  .nav-item.settings { margin-top: auto; }
  .nav-item:disabled { opacity: .5; cursor: default; }
  .nav-item:hover { background: var(--fs-hover); color: var(--fs-ink); }
  .nav-item.active { background: var(--fs-selection); color: var(--fs-ink); font-weight: 600; box-shadow: inset 0 1px 0 var(--fs-edge), 0 1px 2px #00000005; }
  .nav-item.active :global(svg) { color: var(--fs-accent); }
  .engine-badge { display: flex; gap: 8px; margin: 0 14px; padding: 12px 6px 16px; border-top: 1px solid var(--fs-border); font-size: 11px; line-height: 1.6; color: var(--fs-ink2); }
  .engine-badge .dot { color: var(--fs-ink2); font-size: 9px; }
  .engine-badge.ok .dot { color: var(--fs-teal); }
  .engine-badge.bad .dot { color: var(--fs-accent); }
  .engine-badge small { display: block; max-width: 143px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; }
  .reconnect { display: flex; align-items: center; gap: 6px; margin-top: 7px; padding: 4px 8px; border: 1px solid var(--fs-border); border-radius: 6px; color: var(--fs-ink2); background: var(--fs-surface); cursor: pointer; font: inherit; font-size: 10px; }
  .reconnect:disabled { cursor: wait; opacity: 0.6; }
  .content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .topbar { display: flex; flex: 0 0 72px; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 22px; border-bottom: 1px solid var(--fs-border); background: var(--fs-toolbar); }
  .topbar > div { min-width: 0; }
  .topbar h1 { margin: 0; font-size: 20px; line-height: 1.3; font-weight: 650; }
  .topbar p { margin: 4px 0 0; font-size: 11px; color: var(--fs-ink2); overflow-wrap: anywhere; }
  .date-chip { padding: 5px 8px; border-radius: 5px; border: 1px solid var(--fs-border); background: var(--fs-surface); font-size: 11px; color: var(--fs-ink2); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .coordinates { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-variant-numeric: tabular-nums; }
  .workspace { flex: 1; min-height: 0; overflow: auto; }
  .workspace > div:not([hidden]) { min-height: 100%; }
  .map-dialog { width: min(900px, calc(100vw - 28px)); height: min(620px, calc(100dvh - 28px)); max-width: none; max-height: none; margin: auto; padding: 0; border: 1px solid var(--fs-border); border-radius: 8px; color: var(--fs-ink); background: var(--fs-bg-strong); box-shadow: 0 24px 80px #0003, 0 0 0 1px #00000008; overflow: hidden; }
  .map-dialog[open] { display: flex; flex-direction: column; }
  .map-dialog::backdrop { background: #0005; backdrop-filter: blur(4px); }
  .map-dialog header { display: flex; flex: 0 0 auto; justify-content: space-between; align-items: center; padding: 14px 16px; gap: 12px; border-bottom: 1px solid var(--fs-border); }
  .map-dialog h2 { font-size: 14px; margin: 0 0 4px; }
  .map-dialog .coordinates { font-size: 10px; color: var(--fs-ink2); }
  .map-dialog header button { min-height: 30px; padding: 5px 12px; color: var(--fs-ink); border: 1px solid var(--fs-border); border-radius: 5px; background: var(--fs-surface-strong); font: inherit; cursor: pointer; }
  .persistent-map { display: flex; flex: 1; min-height: 0; }
  .map-dialog footer { flex: 0 0 auto; padding: 6px 12px; font-size: 10px; color: var(--fs-ink2); overflow-wrap: anywhere; }
  .exit-dialog { width: min(480px, calc(100vw - 28px)); padding: 24px; margin: auto; border: 1px solid var(--fs-control-border); border-radius: 8px; background: var(--fs-bg-strong); color: var(--fs-ink); box-shadow: 0 16px 56px #0004; }
  .exit-dialog::backdrop { background: #0005; backdrop-filter: blur(4px); }
  .exit-dialog h2 { margin: 0 0 12px; font-size: 18px; }
  .exit-dialog p { font-size: 12px; line-height: 1.7; }
  .exit-dialog footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
  .exit-dialog button { min-height: 32px; border: 1px solid var(--fs-control-border); border-radius: 5px; padding: 6px 10px; background: var(--fs-control); color: var(--fs-ink); font: inherit; cursor: pointer; }
  .exit-dialog button:disabled { opacity: .5; cursor: wait; }
  .exit-dialog .destructive, .exit-dialog .error { color: var(--fs-error); }
  @media (max-width: 700px) {
    .sidebar { flex-basis: 56px; }
    .brand { padding: 14px 10px; }
    .brand > div, .nav-caption, .engine-badge { display: none; }
    nav { padding: 0 6px 12px; }
    .nav-item { padding: 0 12px; gap: 20px; width: 44px; overflow: hidden; white-space: nowrap; }
    .nav-item span { display: none; }
    .topbar { padding: 10px 14px; }
    .topbar p { line-height: 1.5; }
    .topbar h1 { font-size: 19px; }
    .date-chip { display: none; }
  }
  @media (pointer: coarse) {
    .nav-item { min-height: 44px; }
  }
</style>
