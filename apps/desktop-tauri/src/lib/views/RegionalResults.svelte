<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import { Grid3X3, Maximize2, PanelRight, X, Search, SquareDashed, Eraser } from "@lucide/svelte";
  import MapView from "./MapView.svelte";
  import ProfileChart from "./ProfileChart.svelte";
  import WebHourlyLedger from "./WebHourlyLedger.svelte";
  import WebTrendChart from "./WebTrendChart.svelte";
  import { format, modelError, modelReport, numeric, record, regionalCells, timeLabel } from "../forecast-display";
  import { gridBounds, type GridCell } from "../map-grid";
  import type { MapOptions, ObservationSpot } from "../workbench-types";
  import type { RegionBounds } from "../map-region";
  import { regionalPointDetails, regionalReportDetails, regionalSpacing, regionalSpacingLabel } from "../regional-display";
  import { utcTime } from "../forecast-time";
  import { displayTime } from "../web-report-display";
  import { ProfileQueryController, profileRequestAt, profileRequestKey, type ProfileQueryState } from "../profile-query";
  import { rpcStart } from "../ipc";
  import type { PublishedForecast } from "../forecast-session";
  import { regionalPointSeries, regionalPointTimeSteps } from "../regional-series";

  let { result, publication = null, online = true, generation = 0, mapOptions, spot, onPick, onBounds, busy = false, selectionBounds = null }: {
    result: Record<string, unknown> | null;
    publication?: PublishedForecast | null;
    online?: boolean;
    generation?: number;
    mapOptions: MapOptions;
    spot: ObservationSpot;
    onPick?: (lat: number, lon: number) => void;
    onBounds?: (bounds: RegionBounds | null) => void;
    busy?: boolean;
    selectionBounds?: RegionBounds | null;
  } = $props();

  type Model = "gfs" | "ec";
  interface MapHandle {
    fsSetGrid(cells: unknown, spacing?: number): void;
    fsClearGrid(): void;
    fsFocusGridCell(lat: number, lon: number): void;
    fsFitBounds(west: number, south: number, east: number, north: number): void;
  }
  let display = $state<Model | "compare">("gfs");
  let rankingModel = $state<Model>("gfs");
  let showGrid = $state(true);
  let showInspector = $state(true);
  let query = $state("");
  let selected = $state<readonly [number, number] | null>(null);
  let selectedTimes = $state<Record<string, string>>({});
  let detailTab = $state("diagnostics");
  let rankingKind = $state<"grid" | "city">("grid");
  let drawing = $state(false);
  const maps = $state<Partial<Record<Model, MapHandle>>>({});
  const profileQuery = new ProfileQueryController(rpcStart, (next) => { profile = next; });
  let profile = $state<ProfileQueryState>(profileQuery.state);
  const models: Model[] = ["gfs", "ec"];
  const reports = $derived({ gfs: modelReport(result, "gfs"), ec: modelReport(result, "ec") });
  const cells = $derived({ gfs: regionalCells(reports.gfs), ec: regionalCells(reports.ec) });
  const rows = $derived({ gfs: indexRows("gfs"), ec: indexRows("ec") });
  const shownModels = $derived<Model[]>(display === "compare" ? models : [display]);
  const currentRanking = $derived(display === "compare" ? rankingModel : display);
  const selectedRow = $derived(selected ? rowAt(currentRanking, selected) ?? null : null);
  const cityItems = $derived(
    Array.isArray(record(reports[currentRanking]?.city_ranking).items)
      ? (record(reports[currentRanking]?.city_ranking).items as unknown[]).map(record)
      : [],
  );
  const hasCityRanking = $derived(
    record(reports[currentRanking]?.city_ranking).status === "ready" && cityItems.length > 0,
  );
  const selectedTimesForPoint = $derived(regionalPointTimeSteps(selectedRow, reports[currentRanking]?.frame_data));
  const selectedUtc = $derived.by(() => {
    if (selectedTimesForPoint.some((row) => row.utc === selectedTimes[currentRanking])) return selectedTimes[currentRanking];
    const best = utcTime(selectedRow?.best_time_utc ?? selectedRow?.best_time_local);
    return selectedTimesForPoint.find((row) => row.utc === best)?.utc
      ?? selectedTimesForPoint.find((row) => record(row.step).valid === true)?.utc
      ?? selectedTimesForPoint[0]?.utc
      ?? null;
  });
  const selectedSeries = $derived(
    selectedRow && (detailTab === "trend" || detailTab === "ledger")
      ? regionalPointSeries(selectedRow, reports[currentRanking]?.frame_data)
      : [],
  );
  const selectedReport = $derived(selectedRow ? {
    ...selectedRow,
    regional: true,
    best_time_utc: utcTime(selectedRow.best_time_utc ?? selectedRow.best_time_local),
    steps: selectedSeries,
  } : null);
  const selectedTimezone = $derived(
    typeof selectedRow?.timezone_name === "string" ? selectedRow.timezone_name : spot.timezone_name,
  );
  const profileRequest = $derived(selected && selectedUtc
    ? profileRequestAt(
      { profile_source: reports[currentRanking]?.profile_source },
      currentRanking,
      selected[0],
      selected[1],
      selectedUtc,
    )
    : null);
  const profileContext = $derived(
    `${publication?.requestId ?? ""}:${generation}:${currentRanking}:${selected?.join(",") ?? ""}`,
  );
  const currentProfile = $derived(
    profileRequest && profile.key === `${profileContext}:${profileRequestKey(profileRequest)}` ? profile : null,
  );

  function selectTime(utc: string) {
    selectedTimes = { ...selectedTimes, [currentRanking]: utc };
  }
  const ranking = $derived.by(() => {
    const search = query.trim().toLowerCase();
    return cells[currentRanking]
      .filter((cell) => cell[2] !== null && (!search || coordinateLabel(cell).toLowerCase().includes(search)))
      .sort((a, b) => b[2]! - a[2]! || b[0] - a[0] || a[1] - b[1])
      .slice(0, 2000);
  });

  function coordinateLabel(cell: readonly [number, number] | GridCell) {
    return `${cell[0].toFixed(4)}°, ${cell[1].toFixed(4)}°`;
  }
  function modelStatus(model: Model) {
    return modelError(result, model) ? "未完成" : reports[model] ? "预测完成" : result ? "暂无结果" : "等待预测";
  }
  function summary(model: Model) {
    const valid = cells[model].filter((cell) => cell[2] !== null);
    return {
      peak: valid.length ? valid.reduce((peak, cell) => Math.max(peak, cell[2]!), -Infinity) : null,
      valid: valid.length,
      high: valid.filter((cell) => cell[2]! >= 1.5).length,
      missing: Math.max(
        cells[model].length - valid.length,
        (numeric(reports[model]?.point_count) ?? cells[model].length) - valid.length,
        numeric(reports[model]?.error_count) ?? 0,
      ),
    };
  }
  function indexRows(model: Model) {
    const report = reports[model];
    const sourceRows = [
      ...(Array.isArray(report?.points) ? report.points : []),
      ...(Array.isArray(report?.errors) ? report.errors : []),
    ];
    const indexed = new Map<string, Record<string, unknown>>();
    for (const source of sourceRows) {
      const row = record(source);
      if (typeof row.lat === "number" && typeof row.lon === "number") indexed.set(`${row.lat},${row.lon}`, row);
    }
    return indexed;
  }
  function rowAt(model: Model, coordinate: readonly [number, number] | GridCell) {
    return rows[model].get(`${coordinate[0]},${coordinate[1]}`);
  }
  function valueAt(model: Model) {
    return selected ? cells[model].find((cell) => cell[0] === selected![0] && cell[1] === selected![1])?.[2] : null;
  }
  function fit(model: Model) {
    const bounds = gridBounds(display === "compare" ? [...cells.gfs, ...cells.ec] : cells[model], regionalSpacing(reports[model]) ?? .25);
    if (bounds) maps[model]?.fsFitBounds(bounds[0][1], bounds[0][0], bounds[1][1], bounds[1][0]);
  }
  function replay(model: Model) {
    const map = maps[model];
    if (!map) return;
    if (showGrid) map.fsSetGrid(cells[model], regionalSpacing(reports[model]) ?? .25);
    else map.fsClearGrid();
    fit(model);
    if (showGrid && selected) map.fsFocusGridCell(selected[0], selected[1]);
  }
  function select(cell: GridCell) {
    selected = [cell[0], cell[1]];
    showInspector = true;
  }
  function focus(cell: GridCell) {
    select(cell);
    if (showGrid) {
      for (const model of models) maps[model]?.fsFocusGridCell(cell[0], cell[1]);
    }
  }
  function drawnBounds(bounds: RegionBounds) {
    drawing = false;
    onBounds?.(bounds);
  }
  $effect(() => { const mode = display; const running = busy; void mode; if (running) drawing = false; });

  $effect(() => {
    const nextResult = result;
    untrack(() => {
      selected = null;
      query = "";
      selectedTimes = {};
      detailTab = "diagnostics";
      rankingKind = "grid";
      profileQuery.invalidate();
      if (!modelReport(nextResult, display === "compare" ? rankingModel : display)) {
        const available = models.find((model) => modelReport(nextResult, model));
        if (available && display !== "compare") display = available;
      }
    });
  });
  $effect(() => {
    const request = profileRequest;
    const context = profileContext;
    const active = detailTab === "profile";
    const connected = online;
    const cycle = reports[currentRanking]?.profile_source;
    const cycleUtc = record(cycle).cycle_utc;
    untrack(() => {
      if (active && connected && request) {
        void profileQuery.select(request, context, typeof cycleUtc === "string" ? cycleUtc : undefined);
      } else {
        profileQuery.invalidate();
      }
    });
  });
  onDestroy(() => profileQuery.dispose());
  $effect(() => {
    const gfs = cells.gfs, ec = cells.ec, visible = showGrid, first = maps.gfs, second = maps.ec;
    void [gfs, ec, visible, first, second];
    untrack(() => { for (const model of models) replay(model); });
  });
</script>

<div class="regional-results">
  <div class="regional-summaries" aria-label="区域双模型摘要">
    {#each models as model (model)}
      {@const values = summary(model)}
      <section class="model-summary" aria-label={`${model.toUpperCase()} 区域摘要`}>
        <div class="model-status">
          <strong class:ec={model === "ec"}>{model.toUpperCase()}</strong>
          <span class:error={!!modelError(result, model)}>{modelStatus(model)}</span>
        </div>
        <div class="model-peak"><strong>{format(values.peak)}</strong><span>区域峰值</span></div>
        <div class="model-count">
          <span>{reports[model] ? `${values.valid} 有效 · ${values.high} 高值` : "--"}</span>
          <span class:missing={values.missing > 0}>{values.missing > 0 ? `${values.missing} 缺测` : reports[model] ? "无缺测" : "等待数据"}</span>
        </div>
      </section>
    {/each}
  </div>

  <div class="map-controls">
    <div class="segments" role="group" aria-label="区域地图模式">
      {#each [...models, "compare"] as mode}
        <button class:active={display === mode} type="button" aria-pressed={display === mode} onclick={() => { display = mode as Model | "compare"; }}>
          {mode === "compare" ? "双图对照" : mode.toUpperCase()}
        </button>
      {/each}
    </div>
    <span class="map-spacing">{regionalSpacingLabel(reports[display === "compare" ? currentRanking : display])}</span>
    {#if onBounds}
      <button class="icon-button" class:active={drawing} type="button" title={drawing ? "取消绘制范围" : "绘制范围"} aria-label={drawing ? "取消绘制范围" : "绘制范围"} aria-pressed={drawing} disabled={busy} onclick={() => { drawing = !drawing; }}><SquareDashed size={16} /></button>
      <button class="icon-button" type="button" title="清除绘制范围" aria-label="清除绘制范围" disabled={busy || !selectionBounds} onclick={() => { drawing = false; onBounds?.(null); }}><Eraser size={16} /></button>
    {/if}
    <label class="icon-toggle" class:active={showGrid} title="显示网格">
      <input type="checkbox" bind:checked={showGrid} aria-label="显示网格" /><Grid3X3 size={16} />
    </label>
    <button class="icon-button" type="button" title="适应预测范围" aria-label="适应预测范围" disabled={!cells.gfs.length && !cells.ec.length} onclick={() => { for (const model of models) fit(model); }}><Maximize2 size={16} /></button>
    <button class="icon-button" class:active={showInspector} type="button" title="网格详情与排行" aria-label="网格详情与排行" aria-expanded={showInspector} onclick={() => { showInspector = !showInspector; }}><PanelRight size={16} /></button>
  </div>

  <div class="map-and-inspector" class:with-inspector={showInspector}>
    <div class="map-area">
      <div class="model-maps" class:comparison={display === "compare"}>
        {#each shownModels as model (model)}
          <section class="model-map" aria-label={`${model.toUpperCase()} 区域地图`}>
            <header><strong class:ec={model === "ec"}>{model.toUpperCase()}</strong><span>{typeof reports[model]?.cycle_time_utc === "string" ? String(reports[model]?.cycle_time_utc).replace("T", " ") : ""}</span></header>
            <div class="map-surface">
              <MapView bind:this={maps[model]} {...mapOptions} {spot} {onPick} {selectionBounds} drawing={drawing && model === shownModels[0]} onBounds={drawnBounds} onDrawEnd={() => { drawing = false; }} onReady={() => replay(model)} onGridSelect={select} />
              {#if modelError(result, model)}
                <p class="map-message error" role="alert">{modelError(result, model)}</p>
              {:else if !cells[model].length}
                <p class="map-message">{result ? "暂无区域网格" : "等待区域预测"}</p>
              {/if}
            </div>
          </section>
        {/each}
      </div>
      <div class="legend" aria-label="区域质量图例"><span>质量指数</span><span>0</span><span class="color-scale"></span><span>2.5</span><span class="legend-spacer"></span><span class="no-data"></span><span>缺测</span></div>
    </div>

    {#if showInspector}
      <aside class="inspector" aria-label="网格详情与排行">
        {#if selected}
          <section class="cell-inspection">
            <div class="inspector-heading"><h3>网格详情</h3><button class="icon-button" type="button" title="关闭网格详情" aria-label="关闭网格详情" onclick={() => { selected = null; }}><X size={14} /></button></div>
            <p class="coordinate">{coordinateLabel(selected)}</p>
            {#each models as model (model)}
              {@const row = rowAt(model, selected)}
              {@const value = valueAt(model)}
              <div class="cell-reading" title={typeof row?.error === "string" ? row.error : typeof row?.quality_level === "string" ? row.quality_level : ""}>
                <strong class:ec={model === "ec"}>{model.toUpperCase()}</strong>
                <b>{format(value)}</b>
                <span>{value == null ? "缺测" : timeLabel(row?.best_time_local)}</span>
              </div>
            {/each}
            <div class="detail-tabs" role="tablist" aria-label="网格分析">
              {#each [["diagnostics", "诊断"], ["trend", "趋势"], ["profile", "剖面"], ["ledger", "逐时"]] as [id, label] (id)}
                <button type="button" role="tab" aria-selected={detailTab === id} class:active={detailTab === id} onclick={() => { detailTab = id; }}>{label}</button>
              {/each}
            </div>
            {#if selectedTimesForPoint.length}
              <label class="detail-time">时次
                <select aria-label="网格结果时次" value={selectedUtc ?? ""} onchange={(event) => selectTime(event.currentTarget.value)}>
                  {#each selectedTimesForPoint as item (item.utc)}
                    <option value={item.utc}>{displayTime(item.utc, selectedTimezone, true)} · UTC {item.utc.slice(11, 16)}{item.step.valid === false ? " · 缺测" : ""}</option>
                  {/each}
                </select>
              </label>
            {/if}
            <div class="detail-panel" role="tabpanel" aria-label={detailTab}>
              {#if detailTab === "diagnostics"}
                {#each models as model (model)}
                  {@const row = rowAt(model, selected)}
                  {@const value = valueAt(model)}
                  <dl class="detail-list" aria-label={`${model.toUpperCase()} 网格诊断`}>
                    {#each regionalPointDetails(row, value != null) as detail}
                      <div><dt>{detail.label}</dt><dd>{detail.value}</dd></div>
                    {/each}
                  </dl>
                {/each}
              {:else if detailTab === "trend"}
                <WebTrendChart report={selectedReport} timezone={selectedTimezone} {selectedUtc} onSelect={selectTime} aodField="local_aod550" aodLabel="本地 AOD550" />
              {:else if detailTab === "ledger"}
                <WebHourlyLedger report={selectedReport} timezone={selectedTimezone} {selectedUtc} onSelect={selectTime} emptyLabel="该网格没有逐时结果" />
              {:else if !online}
                <p class="profile-status" role="status">服务未连接，动态剖面暂不可用。</p>
              {:else if !profileRequest}
                <p class="profile-status" role="status">该网格没有可查询的冻结剖面或 UTC 时次。</p>
              {:else if currentProfile?.phase === "ready" && currentProfile.crossSection}
                <ProfileChart report={{ ...selectedReport, cross_section: currentProfile.crossSection }} />
              {:else}
                <p class="profile-status" class:error={currentProfile?.phase === "error"} role="status">{currentProfile?.message || "正在读取所选时次剖面…"}</p>
              {/if}
            </div>
          </section>
        {/if}
        <div class="inspector-heading">
          <h3>{rankingKind === "city" ? "城市排行" : "高值排行"}</h3>
          <div class="inspector-heading-actions">
            {#if hasCityRanking}
              <div class="segments compact" role="group" aria-label="排行类型">
                <button class:active={rankingKind === "grid"} type="button" aria-pressed={rankingKind === "grid"} onclick={() => { rankingKind = "grid"; }}>网格</button>
                <button class:active={rankingKind === "city"} type="button" aria-pressed={rankingKind === "city"} onclick={() => { rankingKind = "city"; }}>城市</button>
              </div>
            {/if}
            {#if display === "compare"}
              <div class="segments compact" role="group" aria-label={selected ? "详情与排行模式" : "排行模式"}>
                {#each models as model (model)}<button class:active={rankingModel === model} type="button" aria-pressed={rankingModel === model} onclick={() => { rankingModel = model; }}>{model.toUpperCase()}</button>{/each}
              </div>
            {:else}<span>{display.toUpperCase()}</span>{/if}
          </div>
        </div>
        <label class="search"><Search size={14} /><input type="search" bind:value={query} placeholder={rankingKind === "city" ? "搜索城市" : "搜索网格"} aria-label={rankingKind === "city" ? "搜索城市" : "搜索网格"} /></label>
        {#if rankingKind === "city"}
          {@const filteredCities = cityItems.filter((item) => !query.trim() || String(item.name ?? "").toLowerCase().includes(query.trim().toLowerCase())).slice(0, 2000)}
          <div class="ranking-list" aria-label="城市排行">
            {#each filteredCities as city (String(city.name ?? city.rank))}
              <div class="ranking-row city-ranking-row">
                <span><span class="coordinate">{String(city.name ?? "未命名城市")}</span><small>{timeLabel(city.best_time_local)} · {String(city.lat ?? "--")}°, {String(city.lon ?? "--")}°</small></span>
                <strong>{format(city.peak_quality_index ?? city.quality_index)}</strong>
              </div>
            {:else}<p class="ranking-empty">{hasCityRanking ? "无匹配城市" : "当前区域没有城市排名"}</p>{/each}
          </div>
          <p class="ranking-count">前 {filteredCities.length} 个城市</p>
        {:else}
          <div class="ranking-list">
            {#each ranking as cell (`${cell[0]},${cell[1]}`)}
              {@const row = rowAt(currentRanking, cell)}
              <button class="ranking-row" class:selected={selected?.[0] === cell[0] && selected?.[1] === cell[1]} type="button" onclick={() => focus(cell)}>
                <span><span class="coordinate">{coordinateLabel(cell)}</span><small>{timeLabel(row?.best_time_local)}</small></span>
                <strong>{format(cell[2])}</strong>
              </button>
            {:else}<p class="ranking-empty">{reports[currentRanking] ? "无匹配网格" : "等待预测"}</p>{/each}
          </div>
          <p class="ranking-count">前 {ranking.length} 个网格</p>
        {/if}
        {#each models as model (model)}
          {#if reports[model]}
            <details class="report-details">
              <summary>{model.toUpperCase()} 数据来源与精度</summary>
              <dl class="detail-list" aria-label={`${model.toUpperCase()} 数据来源与精度`}>
                {#each regionalReportDetails(reports[model]) as detail}
                  <div><dt>{detail.label}</dt><dd>{detail.value}</dd></div>
                {/each}
              </dl>
            </details>
          {/if}
        {/each}
      </aside>
    {/if}
  </div>
</div>

<style>
  .regional-results { display: flex; flex-direction: column; gap: 12px; width: 100%; min-width: 0; min-height: 0; height: 100%; }
  .regional-summaries { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 8px 0 12px; border-bottom: 1px solid var(--fs-border); }
  .model-summary { display: flex; align-items: center; gap: 18px; min-width: 0; padding-right: 16px; }
  .model-summary + .model-summary { padding-right: 0; padding-left: 16px; border-left: 1px solid var(--fs-border); }
  .model-status { display: flex; flex-direction: column; flex: 0 0 60px; gap: 5px; }
  .model-status strong { font-size: 13px; }
  .model-status span, .model-count { font-size: 11px; color: var(--fs-ink2); }
  .model-status strong, .model-map header strong, .cell-reading strong { color: var(--fs-accent); }
  .model-status strong.ec, .model-map header strong.ec, .cell-reading strong.ec { color: var(--fs-teal); }
  .model-peak { display: flex; flex-direction: column; gap: 4px; }
  .model-peak strong { font-size: 25px; font-variant-numeric: tabular-nums; line-height: 1.1; }
  .model-peak span { font-size: 10px; color: var(--fs-ink2); }
  .model-count { display: flex; flex: 1; flex-direction: column; gap: 5px; align-items: flex-end; }
  .missing { color: var(--fs-accent); }
  .map-controls { display: flex; align-items: center; gap: 7px; min-width: 0; }
  button, input { font: inherit; }
  button { cursor: pointer; }
  .segments { display: flex; flex: 0 0 248px; min-height: 30px; gap: 2px; padding: 2px; border-radius: 6px; background: var(--fs-segment); }
  .segments button { flex: 1; min-width: 0; padding: 4px 8px; border: 0; border-radius: 4px; background: transparent; color: var(--fs-ink2); font-size: 11px; white-space: nowrap; }
  .segments button.active { color: var(--fs-ink); background: var(--fs-control); box-shadow: var(--fs-segment-shadow); font-weight: 500; }
  .map-spacing { flex: 1; text-align: right; font-size: 10px; color: var(--fs-ink2); white-space: nowrap; }
  .icon-button, .icon-toggle { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 30px; width: 30px; height: 30px; padding: 0; border: 0; border-radius: 5px; background: transparent; color: var(--fs-ink2); cursor: pointer; }
  .icon-button:hover, .icon-toggle:hover { background: var(--fs-hover); color: var(--fs-ink); }
  .icon-button.active, .icon-toggle.active { color: var(--fs-accent); background: var(--fs-accent-soft); }
  .icon-button:disabled { cursor: default; opacity: .4; }
  .icon-toggle { position: relative; }
  .icon-toggle input { position: absolute; inset: 0; margin: 0; opacity: 0; cursor: pointer; }
  .icon-toggle:has(input:focus-visible) { outline: 2px solid var(--fs-accent); outline-offset: 2px; }
  .map-and-inspector { display: grid; grid-template-columns: minmax(0, 1fr); flex: 1; gap: 12px; min-width: 0; min-height: 420px; }
  .map-and-inspector.with-inspector { grid-template-columns: minmax(0, 1fr) 236px; }
  .map-area { display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; border: 1px solid var(--fs-control-border); border-radius: 7px; background: var(--fs-surface); box-shadow: var(--fs-panel-shadow); }
  .model-maps { display: grid; grid-template-columns: minmax(0, 1fr); flex: 1; min-height: 0; }
  .model-maps.comparison { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .model-map { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .model-map + .model-map { border-left: 1px solid var(--fs-border); }
  .model-map header { display: flex; align-items: center; justify-content: space-between; gap: 10px; height: 32px; flex: 0 0 32px; padding: 0 12px; font-size: 11px; background: var(--fs-toolbar); box-shadow: inset 0 1px 0 var(--fs-edge); }
  .model-map header span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; color: var(--fs-ink2); }
  .map-surface { display: flex; position: relative; flex: 1; min-height: 280px; }
  .map-surface :global(.grid-tools), .map-surface :global(.fs-fixture) { display: none; }
  .map-surface :global(.fs-layers) { display: none; }
  .map-surface :global(.fs-status) { display: none; }
  .map-surface :global(.fs-health) { top: auto; bottom: 12px; left: 12px; max-width: calc(100% - 24px); }
  .map-message { position: absolute; z-index: 1000; top: 8px; right: 8px; left: 8px; width: fit-content; max-width: calc(100% - 16px); margin: 0 auto; padding: 7px 10px; border-radius: 6px; background: var(--fs-bg-strong); font-size: 10px; line-height: 1.6; overflow-wrap: anywhere; pointer-events: none; color: var(--fs-ink2); }
  .error, .model-status .error { color: var(--fs-error); }
  .legend { display: flex; align-items: center; gap: 7px; flex: 0 0 32px; padding: 0 12px; font-size: 10px; color: var(--fs-ink2); background: var(--fs-toolbar); border-top: 1px solid var(--fs-border); }
  .color-scale { width: 110px; height: 6px; border-radius: 3px; background: linear-gradient(90deg, #87929e 0% 2%, #d6b56a 2% 16%, #ea7c2c 16% 40%, #c0261b 40% 100%); }
  .legend-spacer { flex: 1; }
  .no-data { width: 9px; height: 9px; border: 1px dashed var(--fs-ink2); }
  .inspector { display: flex; flex-direction: column; gap: 12px; min-width: 0; min-height: 0; padding-left: 2px; }
  .inspector-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 24px; }
  h3 { margin: 0; font-size: 12px; font-weight: 600; }
  .inspector-heading-actions > span { font-size: 10px; color: var(--fs-ink2); }
  .cell-inspection { display: flex; flex-direction: column; gap: 10px; padding-bottom: 12px; border-bottom: 1px solid var(--fs-border); }
  .detail-tabs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 2px; padding: 2px; border-radius: 5px; background: var(--fs-segment); }
  .detail-tabs button { min-width: 0; min-height: 28px; padding: 4px 2px; border: 0; border-radius: 3px; background: transparent; color: var(--fs-ink2); font-size: 10px; }
  .detail-tabs button.active { background: var(--fs-control); color: var(--fs-ink); box-shadow: var(--fs-segment-shadow); font-weight: 500; }
  .detail-time { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--fs-ink2); font-size: 10px; }
  .detail-time select { flex: 1; min-width: 0; height: 30px; padding: 4px 6px; border: 1px solid var(--fs-control-border); border-radius: 4px; background: var(--fs-control); color: var(--fs-ink); font: inherit; }
  .detail-panel { min-width: 0; overflow: hidden; }
  .detail-panel :global(.physical-trends) { border-top: 0; padding-top: 2px; }
  .detail-panel :global(.plots) { grid-template-columns: minmax(0, 1fr); gap: 8px; }
  .detail-panel :global(.series-controls) { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .detail-panel :global(.profile) { padding: 2px 0; }
  .detail-panel :global(.profile header) { flex-direction: column; }
  .detail-panel :global(.profile .metadata) { justify-content: flex-start; }
  .detail-panel :global(.ledger-scroll) { max-width: 100%; }
  .profile-status { margin: 0; padding: 14px 0; color: var(--fs-ink2); font-size: 10px; line-height: 1.6; overflow-wrap: anywhere; }
  .profile-status.error { color: var(--fs-error); }
  .coordinate { font-variant-numeric: tabular-nums; font-size: 10px; color: var(--fs-ink2); }
  p.coordinate { margin: 0; font-size: 11px; }
  .cell-reading { display: flex; align-items: center; gap: 12px; font-size: 11px; }
  .cell-reading strong { width: 34px; }
  .cell-reading b { font-variant-numeric: tabular-nums; }
  .cell-reading > span { margin-left: auto; color: var(--fs-ink2); }
  .detail-list { display: flex; flex-direction: column; gap: 5px; margin: 0; font-size: 10px; }
  .detail-list > div { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; min-width: 0; }
  .detail-list dt { flex: 0 0 auto; color: var(--fs-ink2); }
  .detail-list dd { min-width: 0; margin: 0; text-align: right; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  .report-details { border-top: 1px solid var(--fs-border); padding-top: 8px; }
  .report-details summary { font-size: 10px; color: var(--fs-ink2); cursor: pointer; }
  .report-details .detail-list { margin-top: 9px; }
  .compact { flex: 0 0 100px; min-height: 26px; }
  .compact button { padding: 2px 6px; font-size: 10px; }
  .search { display: flex; align-items: center; gap: 7px; min-width: 0; padding: 7px 8px; border: 1px solid var(--fs-control-border); border-radius: 5px; background: var(--fs-control); box-shadow: inset 0 1px 2px #00000004; color: var(--fs-ink2); }
  .search input { width: 100%; min-width: 0; padding: 0; border: 0; outline: 0; background: transparent; color: var(--fs-ink); font-size: 11px; }
  .search:focus-within { outline: 2px solid var(--fs-accent); outline-offset: 2px; }
  .ranking-list { flex: 1; min-height: 120px; max-height: 440px; overflow: auto; }
  .ranking-row { display: flex; align-items: center; justify-content: space-between; gap: 6px; width: 100%; min-height: 48px; padding: 9px 7px; border: 0; border-bottom: 1px solid var(--fs-border); border-radius: 0; background: transparent; color: var(--fs-ink); text-align: left; }
  .ranking-row:hover { background: var(--fs-hover); }
  .ranking-row.selected { border-radius: 5px; background: var(--fs-selection); }
  .ranking-row > span { display: flex; flex-direction: column; gap: 4px; }
  .ranking-row small { font-size: 10px; color: var(--fs-ink2); }
  .ranking-row strong { font-size: 12px; font-variant-numeric: tabular-nums; }
  .inspector-heading-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; }
  .city-ranking-row { cursor: default; }
  .ranking-empty { margin: 32px 0; text-align: center; font-size: 11px; color: var(--fs-ink2); }
  .ranking-count { margin: 0; font-size: 9px; color: var(--fs-ink2); }
  @media (max-width: 1150px) {
    .map-and-inspector.with-inspector { grid-template-columns: minmax(0, 1fr) 210px; }
    .model-summary { gap: 10px; }
    .model-status { flex-basis: 48px; }
    .model-count { gap: 4px; }
  }
  @media (max-width: 850px) {
    .map-and-inspector.with-inspector { grid-template-columns: minmax(0, 1fr); }
    .inspector { padding: 12px 0 0; border-top: 1px solid var(--fs-border); }
    .ranking-list { max-height: 240px; flex: none; }
    .model-maps.comparison { grid-template-columns: minmax(0, 1fr); }
    .model-map + .model-map { border-left: 0; border-top: 1px solid var(--fs-border); }
    .map-surface { min-height: 340px; }
    .map-area { min-height: 400px; }
  }
  @media (max-width: 600px) {
    .regional-results { gap: 10px; height: auto; }
    .regional-summaries { gap: 10px; }
    .model-summary { flex-wrap: wrap; gap: 8px; padding-right: 0; }
    .model-summary + .model-summary { padding-left: 10px; }
    .model-status { flex-basis: 100%; }
    .model-peak strong { font-size: 23px; }
    .model-count { align-items: flex-start; flex-basis: 100%; }
    .map-controls { flex-wrap: wrap; }
    .segments { flex: 1 0 100%; min-height: 44px; }
    .map-spacing { text-align: left; }
    .compact { flex: 0 0 100px; }
    .icon-button, .icon-toggle { width: 44px; height: 44px; flex-basis: 44px; }
    .legend { gap: 5px; padding: 0 8px; }
    .color-scale { width: 85px; }
  }
</style>
