<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { Search, LocateFixed, Star, Maximize2, SlidersHorizontal, RotateCcw, Download, Square, MapPin, Map, ChevronDown, CalendarDays, RefreshCw, ExternalLink } from "@lucide/svelte";
  import MapView from "./MapView.svelte";
  import ForecastResults from "./ForecastResults.svelte";
  import RegionalResults from "./RegionalResults.svelte";
import { ForecastSessionController, requiresCycleAgeConfirmation, type ForecastSessionState } from "../forecast-session";
  import { desktopCapabilities, onEngineProgress, openReport, rpcRequest, rpcStart } from "../ipc";
  import { localDate, sourceTitle } from "../data-display";
  import { numeric } from "../forecast-display";
  import { displayTime, reportPaths } from "../web-report-display";
  import type { ForecastKind, MapOptions, ObservationSpot } from "../workbench-types";
  import { restoredRequest } from "../workbench-restore";
  import { validCustomRegionBounds } from "../map-region";

  let { kind, online, generation, regions = [], ecSource, mapOptions, spot = $bindable(), onOpenMap, onBusy = () => {} }: {
    kind: ForecastKind; online: boolean; generation: number; regions?: Array<Record<string, unknown>>;
    ecSource: string; mapOptions: MapOptions; spot: ObservationSpot; onOpenMap: () => void; onBusy?: (busy: boolean) => void;
  } = $props();
  const controller = new ForecastSessionController({ start: rpcStart, request: rpcRequest }, (next) => { session = next; });
  let session = $state<ForecastSessionState>(controller.state);
  let query = $state("");
  let locationTab = $state("地图");
  let dateTab = $state("今天");
  let calendarClock = $state(Date.now());
  let targetDate = $state(localDate());
  let phenomenon = $state("dusk");
  let editCoordinates = $state(false);
  let locationBusy = $state(false);
  let locating = $state(false);
  let localError = $state("");
  let favorites = $state<ObservationSpot[]>([]);
  let selectedRegions = $state(["east-asia"]);
  let customRegion = $state(false);
  let showBounds = $state(false);
  let west = $state(119.5), east = $state(120.5), south = $state(29.75), north = $state(30.75);
  let preflightOpen = $state(false);
  let cycleAgeConfirmed = $state(false);
  let preflight: HTMLDialogElement;
  let searchInput = $state<HTMLInputElement>();
  let openingReport = $state(false);
  let alive = false;
  let locationRevision = 0;
  let previousSnapshot = "";
  let previousGeneration = -1;
  const busy = $derived(["checking", "preparing", "forecasting", "cancelling"].includes(session.phase));
  const regionOptions = $derived(regions.filter((item) => typeof item.code === "string" && typeof item.label === "string"));
  const regionTitle = $derived([...regionOptions.filter((item) => selectedRegions.includes(String(item.code))).map((item) => String(item.label)), ...(customRegion ? ["自定义范围"] : [])].join("、"));
  const progress = $derived(Math.max(0, Math.min(100, session.progress?.percent ?? 0)));
  const payload = $derived({
    name: spot.name, lat: spot.lat, lon: spot.lon, elevation_m: spot.elevation_m, timezone_name: spot.timezone_name,
    target_date: targetDate, phenomenon, model_source: "both", workers: 1, forecast_mode: "model",
    satellite_source: "off", ecmwf_source: ecSource,
    ...(kind === "regional" ? { regions: [...selectedRegions].sort().concat(customRegion ? ["custom"] : []), custom_bounds: { west, east, south, north }, custom_timezone_name: spot.timezone_name, spacing_deg: .25 } : {}),
  });
  const published = $derived(session.published[kind] ?? null);
  const cycleAgeConfirmationRequired = $derived(requiresCycleAgeConfirmation(session.report));
  const cycleAgeRows = $derived(session.report?.datasets.filter((row) =>
    row.ready && (row.verified !== true || row.is_latest !== true)) ?? []);
  const cycleAgeConfirmationText = $derived(
    cycleAgeRows.some((row) => row.verified === true && row.is_latest === false)
      ? "已有数据源确认存在更新周期；我了解当前周期较旧，仍使用本次可用数据。"
      : "最新发布尚未核验；当前周期可能不是最新，我了解后仍继续。",
  );
  const result = $derived(published?.result ?? null);
  const resultPayload = $derived(published?.requestSnapshot.payload ?? payload);
  const resultSpot = $derived<ObservationSpot>(published ? {
    name: String(resultPayload.name ?? ""), lat: numeric(resultPayload.lat) ?? 0, lon: numeric(resultPayload.lon) ?? 0,
    elevation_m: numeric(resultPayload.elevation_m) ?? 0, timezone_name: String(resultPayload.timezone_name ?? "UTC"), metadataReady: true,
  } : spot);
  const resultDate = $derived(String(resultPayload.target_date ?? ""));
  const resultPhenomenon = $derived(String(resultPayload.phenomenon ?? "dusk"));
  const reports = $derived(reportPaths(result, !desktopCapabilities()));
  const status = $derived(session.phase === "checking" ? "正在检查本地数据" : session.phase === "preparing" ? "正在准备数据"
    : session.phase === "forecasting" ? kind === "nowcast" ? "正在分析实况" : "正在预测"
    : session.phase === "cancelling" ? "正在取消，等待当前阶段结束"
    : session.report ? session.report.ready ? "本次数据已下载" : "本次数据有缺失"
    : result ? "预测完成" : "");

  function chooseDate(value: string) {
    dateTab = value;
    if (value === "自定义") return;
    const parts = new Intl.DateTimeFormat("en", { timeZone: validTimezone() ? spot.timezone_name : "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
    const part = (name: string) => parts.find((item) => item.type === name)!.value;
    const day = new Date(`${part("year")}-${part("month")}-${part("day")}T12:00:00Z`);
    day.setUTCDate(day.getUTCDate() + (value === "明天" ? 1 : 0));
    targetDate = day.toISOString().slice(0, 10);
  }
  $effect(() => {
    const zone = spot.timezone_name, selected = dateTab, clock = calendarClock;
    void [zone, clock];
    if (selected !== "自定义") untrack(() => chooseDate(selected));
  });
  function validTimezone() {
    try { new Intl.DateTimeFormat("en", { timeZone: spot.timezone_name }); return true; } catch { return false; }
  }
  function invalidateLocation() { spot = { ...spot, metadataReady: false }; ++locationRevision; locationBusy = false; locating = false; }
  function editNumber(field: "lat" | "lon" | "elevation_m", raw: string) {
    invalidateLocation();
    if (raw.trim() && Number.isFinite(Number(raw))) spot = { ...spot, [field]: Number(raw) };
  }
  async function findLocation() {
    if (!online || busy || !query.trim()) return;
    const revision = ++locationRevision;
    const input = query.trim();
    locationBusy = true; localError = "";
    try {
      const point = await rpcRequest<Record<string, unknown>>("search", { query: input });
      if (!alive || revision !== locationRevision || input !== query.trim()) return;
      applyLocation(point);
    } catch (error) { if (alive && revision === locationRevision) localError = error instanceof Error ? error.message : String(error); }
    finally { if (alive && revision === locationRevision) locationBusy = false; }
  }
  function applyLocation(point: Record<string, unknown>) {
    const lat = numeric(point.lat), lon = numeric(point.lon), elevation = numeric(point.elevation_m);
    if (lat === null || lon === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) throw new Error("位置坐标无效。");
    spot = { name: typeof point.name === "string" ? point.name : spot.name, lat, lon,
      elevation_m: elevation ?? -1, timezone_name: typeof point.timezone_name === "string" ? point.timezone_name : spot.timezone_name,
      metadataReady: elevation !== null && elevation !== -1 && typeof point.timezone_name === "string" };
  }
  async function metadata() {
    if (!online || busy || !Number.isFinite(spot.lat) || !Number.isFinite(spot.lon) || Math.abs(spot.lat) > 90 || Math.abs(spot.lon) > 180) {
      localError = "请检查经纬度和引擎连接。"; return;
    }
    const revision = ++locationRevision, lat = spot.lat, lon = spot.lon;
    locationBusy = true; localError = "";
    try {
      const point = await rpcRequest<Record<string, unknown>>("metadata", { lat, lon });
      if (alive && revision === locationRevision && lat === spot.lat && lon === spot.lon) applyLocation({ ...point, name: spot.name });
    } catch (error) { if (alive && revision === locationRevision) localError = error instanceof Error ? error.message : String(error); }
    finally { if (alive && revision === locationRevision) locationBusy = false; }
  }
  function locate() {
    if (locating || busy) return;
    if (!navigator.geolocation) { localError = "系统定位不可用，请搜索地点或手动选择。"; return; }
    const revision = ++locationRevision;
    locating = true; localError = "";
    navigator.geolocation.getCurrentPosition(async (position) => {
      if (!alive || revision !== locationRevision) return;
      locating = false;
      if (Date.now() - position.timestamp > 60_000) { localError = "系统返回的位置已过期，请重试。"; return; }
      spot = { ...spot, name: "当前位置", lat: position.coords.latitude, lon: position.coords.longitude, metadataReady: false };
      await tick();
      if (alive && spot.lat === position.coords.latitude && spot.lon === position.coords.longitude) void metadata();
    }, () => {
      if (!alive || revision !== locationRevision) return;
      locating = false; localError = "未能获取系统位置，请检查定位权限或手动选择。";
    }, { enableHighAccuracy: true, timeout: 20_000, maximumAge: 0 });
  }
  function starLocation() {
    if (!spot.metadataReady) { localError = "请先确认海拔与时区。"; return; }
    favorites = [...favorites.filter((item) => item.lat !== spot.lat || item.lon !== spot.lon), { ...spot }];
    try { localStorage.setItem("firesky.favorites", JSON.stringify(favorites)); }
    catch { localError = "收藏暂时仅保留在本次运行中。"; }
  }
  function beginCheck() {
    if (!online || busy || locationBusy || locating) return;
    localError = "";
    if (!targetDate || !validTimezone()) { localError = "请检查目标日期和时区。"; return; }
    if (kind !== "regional" && (!spot.metadataReady || !Number.isFinite(spot.lat) || !Number.isFinite(spot.lon) || Math.abs(spot.lat) > 90 || Math.abs(spot.lon) > 180 || !Number.isFinite(spot.elevation_m) || spot.elevation_m === -1)) {
      editCoordinates = true; localError = "请先获取海拔与时区，或手动确认位置数据。"; return;
    }
    if (kind === "regional" && !selectedRegions.length && !customRegion) { localError = "请至少选择一个预测区域。"; return; }
    if (kind === "regional" && customRegion && !validCustomRegionBounds({ west, east, south, north })) {
      localError = "请检查区域边界：经度须在 ±180° 内、纬度须在 ±90° 内，西界不得等于东界且北界须大于南界。"; return;
    }
    preflightOpen = true;
    void controller.check(kind, payload, kind === "regional" ? regionTitle : spot.name);
  }
  async function closePreflight() {
    if (busy) await controller.cancel();
    if (!alive) return;
    preflightOpen = false;
    controller.invalidate();
  }
  function forecast() {
    if (!busy && session.report?.ready && (!cycleAgeConfirmationRequired || cycleAgeConfirmed)) {
      preflightOpen = false;
      void controller.forecast(cycleAgeConfirmed);
    }
  }
  function missingDetails(row: NonNullable<ForecastSessionState["report"]>["datasets"][number]) {
    if (row.missing_hours?.length) {
      return `缺失预报时效：${row.missing_hours.map((hour) => `F${String(hour).padStart(3, "0")}`).join("、")}`;
    }
    if (row.missing_valid_times?.length) {
      return `缺失有效时次 UTC：${row.missing_valid_times.join("、")}`;
    }
    if (row.ready !== true && (row.missing_count ?? 0) > 0) {
      return `缺少 ${row.missing_count} 项，当前未返回具体标识`;
    }
    return "";
  }
  $effect(() => {
    void session.report;
    cycleAgeConfirmed = false;
  });
  export async function cancelTask() { await controller.cancel(); }
  export function focusSearch() { searchInput?.focus(); searchInput?.select(); }
  export function restoreRequest(nextKind: ForecastKind, nextPayload: Record<string, unknown>): boolean {
    if (busy) return false;
    const restored = restoredRequest(kind, nextKind, nextPayload);
    if (!restored) return false;
    controller.invalidate();
    preflightOpen = false; ++locationRevision; locationBusy = false; locating = false; localError = "";
    dateTab = "自定义"; targetDate = restored.targetDate; phenomenon = restored.phenomenon;
    spot = restored.spot;
    if (nextKind === "regional") {
      selectedRegions = restored.regions; customRegion = restored.customRegion;
      if (restored.bounds) {
        ({ west, east, south, north } = restored.bounds);
        showBounds = true;
      }
    }
    return true;
  }
  async function showReport(path: string) {
    if (openingReport) return;
    openingReport = true; localError = "";
    try { await openReport(path); }
    catch (error) { if (alive) localError = error instanceof Error ? error.message : String(error); }
    finally { if (alive) openingReport = false; }
  }
  $effect(() => { onBusy(busy); });
  $effect(() => {
    const snapshot = JSON.stringify([kind, payload, spot.metadataReady, online]);
    const gen = generation;
    if (snapshot !== previousSnapshot || gen !== previousGeneration) {
      previousSnapshot = snapshot; previousGeneration = gen;
      untrack(() => { controller.invalidate(); preflightOpen = false; ++locationRevision; locationBusy = false; locating = false; });
    }
  });
  $effect(() => { if (preflightOpen) preflight?.showModal(); else preflight?.close(); });
  onMount(() => {
    alive = true;
    const clock = window.setInterval(() => { calendarClock = Date.now(); }, 60_000);
    let stop: (() => void) | undefined;
    void onEngineProgress((id, event) => controller.progress(id, event)).then((unlisten) => { if (alive) stop = unlisten; else unlisten(); }).catch(() => { if (alive) localError = "进度订阅失败，请重新连接。"; });
    try {
      const stored: unknown = JSON.parse(localStorage.getItem("firesky.favorites") ?? "[]");
      if (Array.isArray(stored)) favorites = stored.filter((item) => typeof item?.name === "string" && Number.isFinite(item.lat) && Number.isFinite(item.lon) && Math.abs(item.lat) <= 90 && Math.abs(item.lon) <= 180 && Number.isFinite(item.elevation_m) && typeof item.timezone_name === "string").map((item) => ({ ...item, metadataReady: item.elevation_m !== -1 }));
    } catch { /* A corrupt optional favorites list must not block the workbench. */ }
    return () => { alive = false; window.clearInterval(clock); ++locationRevision; stop?.(); controller.dispose(); onBusy(false); };
  });
</script>

{#snippet dateControls()}
  <div class="segments date-segments" role="group" aria-label="目标日期">
    {#each ["今天", "明天", "自定义"] as item}<button class:active={dateTab === item} aria-pressed={dateTab === item} onclick={() => chooseDate(item)}>{item}</button>{/each}
  </div>
  {#if dateTab === "自定义"}<label class="custom-date"><CalendarDays size={14} /><input aria-label="目标日期" type="date" bind:value={targetDate} required /></label>{/if}
{/snippet}
{#snippet eventControls()}
  <div class="segments accented" role="group" aria-label="观测现象">
    {#each [["dawn", "朝霞"], ["dusk", "晚霞"]] as [value, title]}<button class:active={phenomenon === value} aria-pressed={phenomenon === value} onclick={() => { phenomenon = value; }}>{title}</button>{/each}
  </div>
{/snippet}
{#snippet publicationActions()}
  {#if published}
    <div class="report-actions">
      <time datetime={published.publishedAtUtc}>结果发布于 {displayTime(published.publishedAtUtc, resultSpot.timezone_name, true)} · {resultSpot.timezone_name}</time>
      {#each reports as report}<button class="plain" disabled={openingReport} onclick={() => showReport(report.path)}><ExternalLink size={13} />打开{report.label}</button>{/each}
    </div>
  {/if}
{/snippet}

<div class="workbench" class:regional={kind === "regional"}>
  {#if kind === "regional"}
    <fieldset class="regional-toolbar" disabled={busy}>
      <details class="region-menu">
        <summary><Map size={14} />预测区域<ChevronDown size={12} /></summary>
        <div class="region-options">
          {#each regionOptions as item}<label><input type="checkbox" checked={selectedRegions.includes(String(item.code))} onchange={(event) => { const code = String(item.code); selectedRegions = event.currentTarget.checked ? [...selectedRegions, code] : selectedRegions.filter((value) => value !== code); }} />{String(item.label)}</label>{/each}
          <label><input type="checkbox" bind:checked={customRegion} />自定义范围</label>
        </div>
      </details>
      <button class="icon-button" title="编辑区域坐标" aria-label="编辑区域坐标" aria-expanded={showBounds} onclick={() => { showBounds = !showBounds; }}><SlidersHorizontal size={14} /></button>
      <label class="regional-date"><CalendarDays size={14} /><input aria-label="区域目标日期" type="date" bind:value={targetDate} onchange={() => { dateTab = "自定义"; }} required /></label>
      {@render eventControls()}
      <span class="spacing-label">0.25° × 0.25°</span>
      <button class="primary" disabled={!online || busy} onclick={beginCheck}>开始预测</button>
    </fieldset>
    {#if showBounds}<fieldset class="custom-bounds" disabled={busy}>
      <label><input type="checkbox" bind:checked={customRegion} />启用自定义范围</label>
      {#each ["西界", "东界", "南界", "北界"] as label, index}<label>{label}<input aria-label={label} type="number" min={index < 2 ? -180 : -90} max={index < 2 ? 180 : 90} step=".25" value={[west, east, south, north][index]} oninput={(event) => { const value = event.currentTarget.valueAsNumber; if (index === 0) west = value; else if (index === 1) east = value; else if (index === 2) south = value; else north = value; }} /></label>{/each}
      <label>时区<input aria-label="区域时区" bind:value={spot.timezone_name} /></label>
    </fieldset>{/if}
    {#if published}<p class="published-context">{published.requestSnapshot.label} · {resultDate} {resultPhenomenon === "dawn" ? "朝霞" : "晚霞"}</p>{/if}
    {@render publicationActions()}
    <RegionalResults {result} publication={published} {online} {generation} {mapOptions} spot={resultSpot} {busy}
      selectionBounds={customRegion ? { west, south, east, north } : null}
      onBounds={(bounds) => {
        if (bounds) {
          west = bounds.west; east = bounds.east; south = bounds.south; north = bounds.north;
          customRegion = true; showBounds = true;
        } else customRegion = false;
      }} />
  {:else}
    <aside class="control-rail">
      <fieldset class="location-panel" disabled={busy}>
        <div class="panel-head"><h2>观测点</h2><div class="segments" role="group" aria-label="地点视图">{#each ["地图", "收藏"] as item}<button class:active={locationTab === item} aria-pressed={locationTab === item} onclick={() => { locationTab = item; }}>{item}</button>{/each}</div></div>
        <form class="search-row" onsubmit={(event) => { event.preventDefault(); void findLocation(); }}>
          <input bind:this={searchInput} aria-label="搜索地名" placeholder="搜索地名，如「西湖」或「杭州」" bind:value={query} oninput={() => { ++locationRevision; locationBusy = false; locating = false; }} />
          <button class="icon-button" title="搜索位置" aria-label="搜索位置" disabled={!online || locationBusy || !query.trim()}><Search size={16} /></button>
          <button type="button" class="icon-button" title="获取当前位置" aria-label="获取当前位置" disabled={locating || locationBusy} onclick={locate}><LocateFixed size={16} /></button>
        </form>
        {#if locationTab === "地图"}<div class="mini-map"><MapView {...mapOptions} {spot} mini onPick={(lat, lon) => { spot = { ...spot, lat, lon, name: "地图选点", metadataReady: false }; }} /></div>
        {:else}<div class="favorites">{#each favorites as favorite}<button onclick={() => { ++locationRevision; locationBusy = false; locating = false; spot = { ...favorite }; }}><MapPin size={13} />{favorite.name}</button>{:else}<p>暂无收藏地点</p>{/each}</div>{/if}
        <div class="location-name"><i></i><div><strong>{spot.name}</strong><span class="coordinates">{spot.lat.toFixed(4)}° N, {spot.lon.toFixed(4)}° E</span></div></div>
        <div class="location-meta"><span>{spot.metadataReady ? `海拔 ${spot.elevation_m.toFixed(0)} m` : "海拔未确认"}</span><span>{spot.timezone_name}</span></div>
        <div class="location-actions"><button class="plain" onclick={starLocation}><Star size={13} />收藏当前点</button><button class="plain" onclick={onOpenMap}><Maximize2 size={13} />大地图</button></div>
      </fieldset>
      <fieldset class="parameter-panel" disabled={busy}>
        <div class="panel-head"><h2>观测参数</h2><button class="plain" onclick={() => { chooseDate("今天"); phenomenon = "dusk"; }}><RotateCcw size={12} />重置</button></div>
        <span class="field-caption">目标日期</span>
        {@render dateControls()}
        <span class="field-caption">观测现象</span>
        {@render eventControls()}
        <button class="plain edit-coordinates" aria-expanded={editCoordinates} onclick={() => { editCoordinates = !editCoordinates; }}><SlidersHorizontal size={13} />编辑坐标</button>
        {#if editCoordinates}<div class="coordinate-form">
          <label>名称<input bind:value={spot.name} /></label>
          <label>纬度<input type="number" min="-90" max="90" step=".0001" value={spot.lat} oninput={(event) => editNumber("lat", event.currentTarget.value)} /></label>
          <label>经度<input type="number" min="-180" max="180" step=".0001" value={spot.lon} oninput={(event) => editNumber("lon", event.currentTarget.value)} /></label>
          <label>海拔 (m)<input type="number" step="1" value={spot.elevation_m} oninput={(event) => editNumber("elevation_m", event.currentTarget.value)} /></label>
          <label>时区<input bind:value={spot.timezone_name} oninput={invalidateLocation} /></label>
          <button class="plain" disabled={!online || locationBusy} onclick={metadata}><RefreshCw size={12} />{locationBusy ? "正在获取…" : "获取海拔与时区"}</button>
          <label class="confirmation"><input type="checkbox" bind:checked={spot.metadataReady} />已确认位置数据</label>
        </div>{/if}
      </fieldset>
      <button class="primary start-button" disabled={!online || busy || locationBusy || locating} onclick={beginCheck}>{kind === "nowcast" ? "开始实况分析" : "开始预测"}</button>
      {#if kind === "nowcast"}<button class="plain warmup" disabled={!online || busy} onclick={beginCheck}><Download size={13} />预热卫星与数据</button>{/if}
      {#if !online}<p class="engine-warning">引擎未连接</p>{/if}
    </aside>
    <div class="results-column">
      {@render publicationActions()}
      <ForecastResults {result} publication={published} {online} {generation} nowcast={kind === "nowcast"} name={resultSpot.name} date={resultDate} phenomenon={resultPhenomenon} timezone={resultSpot.timezone_name} />
    </div>
  {/if}
  {#if (busy && !preflightOpen) || localError || session.error}
    <div class="work-status" role="status">
      {#if busy && !preflightOpen}<progress max="100" value={progress} aria-label="预测进度"></progress><span>{session.phase === "cancelling" ? status : session.progress?.message || status} · {progress}%</span><button class="plain" disabled={session.phase === "cancelling"} onclick={() => controller.cancel()}><Square size={12} />{session.phase === "cancelling" ? "取消中…" : "取消任务"}</button>{/if}
      {#if localError || session.error}<p class="error" role="alert">{localError || session.error}</p>{/if}
    </div>
  {/if}
</div>

<dialog bind:this={preflight} class="preflight" aria-labelledby="preflight-title" oncancel={(event) => { event.preventDefault(); void closePreflight(); }}>
  <header><h2 id="preflight-title">{kind === "nowcast" ? "实况准备" : "预测准备"}</h2><p>{kind === "regional" ? regionTitle : spot.name} · {targetDate} · {phenomenon === "dawn" ? "朝霞" : "晚霞"}</p></header>
  <div class="preflight-rows">
    {#each session.report?.datasets ?? [] as row}
      <div class="preflight-row"><Download size={21} /><div>
        <strong>{sourceTitle(row.source)}</strong>
        <span>所需周期 UTC：{row.request_cycle || "未解析"}</span>
        <span>{row.ready ? "本地数据完整" : `缺少 ${row.missing_count ?? "--"} 项`}</span>
        <span>{row.verified ? `最新发布 UTC：${row.latest_available_cycle ?? "未知"}${row.is_latest ? " · 当前周期" : " · 有更新周期"}` : "最新发布：未核验"}</span>
        {#if !row.ready && missingDetails(row)}<span class="preflight-missing">{missingDetails(row)}</span>{/if}
      </div><b class:ready={row.ready}>{row.ready ? "已齐" : "待下载"}</b></div>
    {:else}<p class="preflight-empty">{session.phase === "checking" ? "正在检查本地数据…" : session.error ? "数据检查未完成" : "暂无数据清单"}</p>{/each}
  </div>
  {#if cycleAgeConfirmationRequired}
    <label class="cycle-confirmation"><input type="checkbox" bind:checked={cycleAgeConfirmed} disabled={busy || session.publicationPending} /><span>{cycleAgeConfirmationText}</span></label>
  {/if}
  <p class="preflight-status" role="status">{session.phase === "cancelling" ? status : session.progress?.message || status}</p>
  {#if busy}<progress max="100" value={session.progress ? progress : undefined} aria-label="数据准备进度"></progress>{/if}
  {#if session.error}<p class="error" role="alert">{session.error}</p>{/if}
  {#if session.publicationError}<p class="error" role="alert">发布核验未完成：{session.publicationError}</p>{/if}
  <div class="preflight-secondary"><button class="plain" disabled={busy || !session.session || session.publicationPending} onclick={() => controller.publications()}><RefreshCw size={13} />{session.publicationPending ? "正在核验…" : "核验最新发布"}</button><button class="plain" disabled={busy} onclick={() => controller.check(kind, payload, kind === "regional" ? regionTitle : spot.name)}>重新检查</button></div>
  <footer><button disabled={session.phase === "cancelling"} onclick={closePreflight}>{busy ? "取消任务" : "返回"}</button><span></span>
    <button disabled={busy || !session.session} onclick={() => controller.prepare()}><Download size={14} />{session.report?.ready ? "更新数据" : "下载缺失数据"}</button>
    <button class="primary" disabled={busy || session.publicationPending || !session.report?.ready || !session.session || (cycleAgeConfirmationRequired && !cycleAgeConfirmed)} onclick={forecast}>{kind === "nowcast" ? "确认分析" : "确认预测"}</button></footer>
</dialog>

<style>
  .workbench { display: grid; grid-template-columns: 292px minmax(0, 1fr); align-items: start; gap: 16px; padding: 14px 20px 18px 18px; min-height: 100%; }
  .control-rail { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
  .results-column { min-width: 0; }
  .published-context { margin: 0; font-size: 11px; color: var(--fs-ink2); overflow-wrap: anywhere; }
  .report-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
  .report-actions time { align-self: center; margin-right: auto; color: var(--fs-ink2); font-size: 10px; overflow-wrap: anywhere; }
  fieldset { margin: 0; border: 0; min-width: 0; }
  .location-panel, .parameter-panel { display: flex; flex-direction: column; gap: 10px; padding: 15px; border: 1px solid var(--fs-border); border-radius: 8px; background: var(--fs-surface); box-shadow: inset 0 1px 0 var(--fs-edge), var(--fs-panel-shadow); }
  .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  h2 { margin: 0; font-size: 13px; font-weight: 600; }
  button, input { font: inherit; color: var(--fs-ink); }
  button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 30px; border: 1px solid var(--fs-control-border); border-radius: 6px; padding: 5px 10px; background: var(--fs-control); box-shadow: var(--fs-control-shadow); cursor: pointer; }
  button:hover:enabled { background: var(--fs-hover); }
  button:disabled { opacity: .45; cursor: default; }
  input { min-width: 0; height: 32px; padding: 5px 8px; border: 1px solid var(--fs-control-border); border-radius: 5px; background: var(--fs-control); box-shadow: inset 0 1px 2px #00000004; }
  .search-row { display: flex; gap: 6px; }
  .search-row > input { flex: 1; width: 0; font-size: 11px; }
  .icon-button { width: 32px; height: 32px; flex: 0 0 32px; padding: 0; }
  .mini-map { display: flex; height: 176px; overflow: hidden; border: 1px solid var(--fs-border); border-radius: 6px; background: var(--fs-bg-strong); }
  .favorites { display: flex; flex-direction: column; height: 176px; overflow: auto; padding: 8px; gap: 6px; }
  .favorites button { justify-content: flex-start; border: 0; background: transparent; color: var(--fs-accent); font-size: 11px; }
  .favorites p { color: var(--fs-ink2); font-size: 11px; }
  .location-name { display: flex; align-items: center; gap: 9px; }
  .location-name i { width: 8px; height: 8px; border-radius: 50%; background: var(--fs-accent); }
  .location-name > div { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .location-name strong { font-size: 13px; overflow-wrap: anywhere; }
  .coordinates { font-size: 10px; color: var(--fs-ink2); font-variant-numeric: tabular-nums; }
  .location-meta { display: flex; flex-wrap: wrap; gap: 5px; }
  .location-meta span { padding: 3px 6px; background: var(--fs-hover); border-radius: 4px; font-size: 10px; color: var(--fs-ink2); overflow-wrap: anywhere; }
  .location-actions { display: flex; justify-content: space-between; gap: 8px; }
  .plain { border: 0; background: transparent; color: var(--fs-accent); padding: 2px 0; min-height: 26px; font-size: 11px; box-shadow: none; }
  .plain:hover:enabled { background: transparent; text-decoration: underline; text-underline-offset: 3px; }
  .segments { display: flex; gap: 2px; padding: 2px; border-radius: 6px; background: var(--fs-segment); box-shadow: inset 0 1px 1px #00000004; }
  .segments button { flex: 1; min-width: 0; min-height: 26px; padding: 3px 8px; border: 0; border-radius: 4px; background: transparent; box-shadow: none; color: var(--fs-ink2); font-size: 11px; }
  .segments button.active { background: var(--fs-control); color: var(--fs-ink); box-shadow: var(--fs-segment-shadow); font-weight: 500; }
  .accented button.active { color: var(--fs-accent); background: var(--fs-control); }
  .panel-head .segments { width: 128px; }
  .field-caption { font-size: 11px; color: var(--fs-ink2); }
  .custom-date { display: flex; align-items: center; gap: 8px; color: var(--fs-accent); }
  .custom-date input { flex: 1; width: 0; }
  .edit-coordinates { align-self: center; }
  .coordinate-form { display: flex; flex-direction: column; gap: 8px; }
  .coordinate-form label { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--fs-ink2); font-size: 10px; }
  .coordinate-form input { width: 160px; }
  .coordinate-form .confirmation { justify-content: flex-start; }
  .confirmation input { width: 14px; height: 14px; accent-color: var(--fs-accent); }
  button.primary { background: var(--fs-accent); color: var(--fs-on-accent); border-color: #00000008; font-weight: 600; box-shadow: inset 0 1px 0 #ffffff24, 0 1px 2px #00000012; }
  button.primary:hover:enabled { background: var(--fs-accent); filter: brightness(1.05); }
  .start-button { min-height: 41px; width: 100%; font-size: 12px; margin-top: -4px; }
  .warmup { align-self: center; margin-top: -8px; }
  .engine-warning, .error { color: var(--fs-error); font-size: 11px; line-height: 1.7; overflow-wrap: anywhere; }
  .engine-warning { text-align: center; margin: 0; }
  .work-status { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: 12px; font-size: 10px; color: var(--fs-ink2); }
  .work-status progress { width: 130px; }
  .work-status p { margin: 0; }
  progress { accent-color: var(--fs-accent); height: 5px; }
  .regional { display: flex; flex-direction: column; align-items: stretch; gap: 12px; min-height: 100%; }
  .regional-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 0 0 12px; width: 100%; border-bottom: 1px solid var(--fs-border); }
  .regional-toolbar label { display: flex; align-items: center; gap: 8px; font-size: 10px; }
  .regional-date { display: flex; gap: 8px; }
  .regional-date input { width: 150px; }
  .region-menu { position: relative; }
  .region-menu summary { display: flex; align-items: center; gap: 8px; min-height: 32px; padding: 6px 10px; border: 1px solid var(--fs-control-border); border-radius: 5px; background: var(--fs-control); box-shadow: var(--fs-control-shadow); font-size: 11px; cursor: pointer; list-style: none; }
  .region-menu summary::-webkit-details-marker { display: none; }
  .region-options { position: absolute; z-index: 20; top: calc(100% + 6px); left: 0; min-width: 200px; max-height: 280px; overflow: auto; padding: 7px; background: var(--fs-bg-strong); border: 1px solid var(--fs-border); border-radius: 6px; box-shadow: 0 10px 30px #0002; }
  .region-options label { padding: 7px; }
  .region-options input, .custom-bounds input[type="checkbox"] { width: 14px; height: 14px; accent-color: var(--fs-accent); }
  .spacing-label { color: var(--fs-ink2); font-size: 10px; margin-left: auto; }
  .custom-bounds { display: flex; gap: 12px; flex-wrap: wrap; }
  .custom-bounds label { display: flex; align-items: center; gap: 6px; font-size: 10px; }
  .custom-bounds input { width: 85px; }
  .custom-bounds input[aria-label="区域时区"] { width: 150px; }
  .preflight { --preflight-x: 26px; --preflight-y: 26px; width: min(490px, calc(100vw - 28px)); max-height: calc(100dvh - 28px); overflow: auto; overscroll-behavior: contain; margin: auto; border: 1px solid var(--fs-control-border); border-radius: 8px; padding: var(--preflight-y) var(--preflight-x); background: var(--fs-bg-strong); color: var(--fs-ink); box-shadow: inset 0 1px 0 var(--fs-edge), 0 16px 56px #0004; }
  .preflight::backdrop { background: #0005; backdrop-filter: blur(4px); }
  .preflight header h2 { font-size: 20px; }
  .preflight header p { margin: 8px 0 20px; color: var(--fs-ink2); font-size: 11px; overflow-wrap: anywhere; }
  .preflight-row { display: flex; align-items: flex-start; gap: 12px; min-height: 72px; padding: 12px 0; border-bottom: 1px solid var(--fs-border); }
  .preflight-row > :global(svg) { color: var(--fs-ink2); flex: 0 0 21px; }
  .preflight-row > div { display: flex; flex: 1; flex-direction: column; gap: 5px; min-width: 0; }
  .preflight-row strong { font-size: 13px; }
  .preflight-row span { color: var(--fs-ink2); font-size: 10px; line-height: 1.5; overflow-wrap: anywhere; }
  .preflight-row .preflight-missing { color: var(--fs-error); }
  .preflight-row b { flex: 0 0 auto; margin-left: auto; font-size: 10px; color: var(--fs-ink2); white-space: nowrap; }
  .preflight-row b.ready { color: var(--fs-teal); }
  .cycle-confirmation { display: flex; align-items: flex-start; gap: 9px; margin-top: 12px; color: var(--fs-ink); font-size: 11px; line-height: 1.6; }
  .cycle-confirmation input { width: 15px; height: 15px; flex: 0 0 15px; margin: 2px 0 0; accent-color: var(--fs-accent); }
  .preflight-empty { min-height: 160px; display: grid; place-items: center; color: var(--fs-ink2); }
  .preflight-status { min-height: 30px; margin: 16px 0 0; font-size: 11px; color: var(--fs-ink2); }
  .preflight > progress { width: 100%; }
  .preflight-secondary { display: flex; justify-content: space-between; gap: 12px; padding: 12px 0; }
  .preflight footer { position: sticky; bottom: calc(-1 * var(--preflight-y)); z-index: 1; display: flex; flex-wrap: wrap; gap: 8px; margin: 0 calc(-1 * var(--preflight-x)) calc(-1 * var(--preflight-y)); border-top: 1px solid var(--fs-border); padding: 16px var(--preflight-x) var(--preflight-y); background: var(--fs-bg-strong); }
  .preflight footer > span { flex: 1; }
  .preflight footer button { font-size: 11px; }
  @media (max-width: 900px) { .workbench { grid-template-columns: minmax(0, 1fr); } .control-rail { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; } .start-button { grid-column: 1 / -1; } .warmup, .engine-warning { grid-column: 1 / -1; } .regional-date { flex-wrap: wrap; } }
  @media (max-width: 600px) { .workbench { padding: 12px 14px 18px; } .control-rail { display: flex; align-items: stretch; } .regional-toolbar { gap: 8px; } .spacing-label { display: none; } .regional-toolbar .primary { margin-left: auto; } .regional-date { width: 100%; } .preflight { --preflight-x: 16px; --preflight-y: 20px; } .preflight-secondary button, .preflight footer button { min-height: 44px; } .cycle-confirmation { min-height: 44px; } .preflight footer > span { display: none; } .preflight footer button { flex: 1; } }
</style>
