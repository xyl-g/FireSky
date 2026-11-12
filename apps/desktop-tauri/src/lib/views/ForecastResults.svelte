<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import { curveSegments, format, metricValue, metrics, modelError, modelReport, nowcastMetrics, record, timeLabel, windowLabel, type RecordValue } from "../forecast-display";
  import ProfileChart from "./ProfileChart.svelte";
  import WebReportDetails from "./WebReportDetails.svelte";
  import WebHourlyLedger from "./WebHourlyLedger.svelte";
  import WebTrendChart from "./WebTrendChart.svelte";
  import SatellitePathDiagnostics from "./SatellitePathDiagnostics.svelte";
  import { defaultForecastTime, forecastTimes } from "../forecast-time";
  import { displayTime } from "../web-report-display";
  import { ProfileQueryController, profileRequest, profileRequestKey, type ProfileQueryState } from "../profile-query";
  import { rpcStart } from "../ipc";
  import type { PublishedForecast } from "../forecast-session";
  let { result, nowcast = false, name, date, phenomenon, timezone, publication = null, online = true, generation = 0 }: {
    result: RecordValue | null; nowcast?: boolean; name: string; date: string; phenomenon: string; timezone: string;
    publication?: PublishedForecast | null; online?: boolean; generation?: number;
  } = $props();
  const tabs = ["概览", "大气剖面", "诊断与时间窗", "逐时台账"];
  let tab = $state("概览");
  let model = $state("gfs");
  let selectedTimes = $state<Record<string, string>>({});
  const query = new ProfileQueryController(rpcStart, (next) => { profile = next; });
  let profile = $state<ProfileQueryState>(query.state);
  const selectedModel = $derived(nowcast ? "satellite" : model === "ec" ? "ec" : "gfs");
  const models = $derived(nowcast ? ["satellite"] : ["gfs", "ec"]);
  const reportFor = (id: string) => nowcast ? result : modelReport(result, id);
  const selectedReport = $derived(reportFor(model));
  const resultTimes = $derived(forecastTimes(selectedReport));
  const selectedUtc = $derived(resultTimes.some((row) => row.utc === selectedTimes[selectedModel]) ? selectedTimes[selectedModel] : defaultForecastTime(selectedReport));
  const request = $derived(profileRequest(publication, selectedReport, selectedModel, selectedUtc));
  const profileContext = $derived(`${publication?.requestId ?? ""}:${generation}:${selectedModel}`);
  const currentProfile = $derived(request && profile.key === `${profileContext}:${profileRequestKey(request)}` ? profile : null);
  function selectTime(utc: string) { selectedTimes = { ...selectedTimes, [selectedModel]: utc }; }
  $effect(() => {
    const identity = publication?.requestId;
    const kind = nowcast;
    void [identity, kind];
    untrack(() => { selectedTimes = {}; query.invalidate(); });
  });
  $effect(() => {
    const selectedRequest = request, context = profileContext, active = tab === "大气剖面", connected = online;
    const cycle = record(selectedReport?.profile_source).cycle_utc;
    untrack(() => {
      if (active && connected && selectedRequest) void query.select(selectedRequest, context, typeof cycle === "string" ? cycle : undefined);
      else query.invalidate();
    });
  });
  onDestroy(() => query.dispose());
  const specs = $derived(nowcast ? nowcastMetrics : metrics);
  const groups = $derived(nowcast ? ["卫星云况", "运动与约束", "采样与光学"] : ["云况与色彩", "大气光学", "时效与可靠性"]);
  const curves = $derived(models.flatMap((id) => curveSegments(reportFor(id)).map((points) => ({ id, points }))));
  const times = $derived(curves.flatMap((line) => line.points.map((point) => point.time)));
  const low = $derived(times.length ? Math.min(...times) : 0);
  const high = $derived(times.length ? Math.max(...times) : 1);
  const x = (time: number) => 42 + (time - low) / Math.max(1, high - low) * 620;
  const y = (value: number) => 160 - Math.min(2.5, Math.max(0, value)) / 2.5 * 140;
  const chartTime = (value: number) => {
    try { return new Intl.DateTimeFormat("zh-CN", { timeZone: timezone, hour: "2-digit", minute: "2-digit", hour12: false }).format(value); }
    catch { return "--"; }
  };
</script>

<div class="forecast-results">
  <section class="forecast-hero" aria-label={nowcast ? "实况摘要" : "双模型摘要"}>
    <p class="context">{name} · {date} {phenomenon === "dawn" ? "朝霞" : "晚霞"}</p>
    <div class="model-summaries">
      {#each models as id}
        {@const report = reportFor(id)}
        {@const error = nowcast ? "" : modelError(result, id)}
        <div class="model-summary" class:ec={id === "ec"} class:satellite={nowcast}>
          <div class="model-heading"><strong><i></i>{nowcast ? "卫星实况" : id.toUpperCase()}</strong><span class:error={!!error}>{error ? "未完成" : report ? nowcast ? "分析完成" : "预测完成" : nowcast ? "等待分析" : "等待预测"}</span></div>
          <div class="score-row">
            <div><div class="score" class:missing={!report}><b>{format(report?.peak_quality_index ?? report?.quality_index)}</b><span>/ 2.5</span></div><p>{String(report?.quality_level ?? "峰值质量指数")}</p></div>
            <div class="best-time"><span>最佳时刻</span><strong>{timeLabel(report?.best_time_local)}</strong></div>
          </div>
          <p>推荐窗口 {windowLabel(report)}</p>
          {#if error}<p class="error">{error}</p>{/if}
          {#if nowcast}<p>卫星观测 {timeLabel(record(report?.nowcast).image_valid_time_utc)} UTC · GFS 先验 · CAMS 光学</p>{/if}
        </div>
      {/each}
    </div>
  </section>
  <div class="analysis-tabs" role="tablist" aria-label="分析视图">
    {#each tabs as item}<button role="tab" aria-selected={tab === item} class:active={tab === item} onclick={() => { tab = item; }}>{item}</button>{/each}
  </div>
  <div class="result-body" role="tabpanel" aria-label={tab}>
    {#if tab !== "概览" && !nowcast}
      <div class="model-selector" role="group" aria-label="结果模式">
        {#each ["gfs", "ec"] as id}<button class:active={model === id} aria-pressed={model === id} onclick={() => { model = id; }}>{id.toUpperCase()}</button>{/each}
      </div>
    {/if}
    {#if tab !== "概览" && resultTimes.length}
      <div class="result-time">
        <label>时次<select aria-label="结果时次" value={selectedUtc ?? ""} onchange={(event) => selectTime(event.currentTarget.value)}>
          {#each resultTimes as row}<option value={row.utc}>{displayTime(row.utc, timezone)}{row.step.valid === false ? " · 缺测" : ""}</option>{/each}
        </select></label>
        {#if selectedUtc}<time datetime={selectedUtc}>{selectedUtc}</time>{/if}
      </div>
    {/if}
    {#if tab === "概览"}
      {#each groups as group, index}
        <section class="metric-group" aria-label={group}>
          <h2>{group}</h2>
          <div class="metrics">
            {#each specs.slice(index * 3, index * 3 + 3) as spec}
              <article class="metric">
                <h3>{spec[1]}</h3>
                <div class="metric-values">
                  {#each models as id}<div><span class:ec={id === "ec"}>{nowcast ? "" : id.toUpperCase()}</span><strong>{metricValue(spec[0], reportFor(id), nowcast)}</strong></div>{/each}
                </div>
                <p>{spec[2]}</p>
              </article>
            {/each}
          </div>
        </section>
      {/each}
      <section class="curve">
        <div class="curve-title"><h2>质量指数趋势</h2><span>GFS <i></i>{#if !nowcast} EC <i class="ec"></i>{/if}</span></div>
        {#if curves.length}
          <svg viewBox="0 0 690 190" role="img" aria-label="质量指数时间曲线">
            {#each [0, .5, 1, 1.5, 2, 2.5] as value}
              <line x1="42" x2="662" y1={y(value)} y2={y(value)} class="guide" />
              <text x="30" y={y(value) + 4} text-anchor="end">{value.toFixed(1)}</text>
            {/each}
            {#each curves as line}
              <polyline points={line.points.map((point) => `${x(point.time)},${y(point.value)}`).join(" ")} class:ec={line.id === "ec"} />
              {#each line.points as point}<circle cx={x(point.time)} cy={y(point.value)} r="2.5" class:ec={line.id === "ec"}><title>{chartTime(point.time)} · {point.value.toFixed(3)}</title></circle>{/each}
            {/each}
            <text x="42" y="183">{chartTime(low)}</text><text x="662" y="183" text-anchor="end">{chartTime(high)}</text>
          </svg>
        {:else}<p class="empty">{nowcast ? "等待实况分析" : "等待预测"}</p>{/if}
      </section>
      {#if !nowcast}
        <div class="model-selector" role="group" aria-label="概览细节模式">
          {#each ["gfs", "ec"] as id}<button class:active={model === id} aria-pressed={model === id} onclick={() => { model = id; }}>{id.toUpperCase()}</button>{/each}
        </div>
      {/if}
      <WebTrendChart report={selectedReport} {timezone} {selectedUtc} onSelect={selectTime} />
      <WebReportDetails report={selectedReport} {timezone} mode="overview" />
    {:else if !nowcast && modelError(result, model)}
      <p class="error result-notice">{model.toUpperCase()} 未完成：{modelError(result, model)}</p>
    {:else if tab === "逐时台账"}
      <WebHourlyLedger report={selectedReport} {timezone} {selectedUtc} onSelect={selectTime} emptyLabel={nowcast ? "等待实况分析" : "等待预测"} />
    {:else if tab === "大气剖面"}
      {#if request}
        {#if !online}<p class="profile-status" role="status">服务未连接，动态剖面暂不可用。</p>
        {:else if currentProfile?.phase === "ready" && currentProfile.crossSection}
          {#if nowcast}<SatellitePathDiagnostics section={currentProfile.crossSection} utc={selectedUtc!} />
          {:else}<ProfileChart report={{ ...selectedReport, cross_section: currentProfile.crossSection }} />{/if}
        {:else}<p class="profile-status" class:error={currentProfile?.phase === "error"} role="status">{currentProfile?.message || "正在读取所选时次剖面…"}</p>{/if}
      {:else if selectedReport}
        <p class="profile-status" role="status">{resultTimes.length ? "报告内嵌剖面 · 无动态资源" : "报告内嵌剖面 · 无可查询的 UTC 时次"}</p>
      {/if}
      {#if nowcast || !request}<ProfileChart report={selectedReport} satellite={nowcast} />{/if}
    {:else}
      <WebReportDetails report={selectedReport} {timezone} mode="diagnostics" />
    {/if}
  </div>
</div>

<style>
  .forecast-results { display: flex; flex-direction: column; min-width: 0; gap: 12px; }
  .result-time { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 0; color: var(--fs-ink2); font-size: 10px; }
  .result-time label { display: flex; align-items: center; gap: 8px; }
  .result-time select { min-height: 30px; max-width: 100%; border: 1px solid var(--fs-control-border); border-radius: 5px; padding: 4px 8px; color: var(--fs-ink); background: var(--fs-control); font: inherit; }
  .result-time time { overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  .profile-status { padding: 14px 0; color: var(--fs-ink2); font-size: 11px; overflow-wrap: anywhere; }
  .forecast-hero { padding: 18px; background: var(--fs-surface); border: 1px solid var(--fs-border); border-radius: 8px; box-shadow: inset 0 1px 0 var(--fs-edge), var(--fs-panel-shadow); }
  p { margin: 0; line-height: 1.6; }
  .context { margin-bottom: 14px; font-size: 11px; color: var(--fs-ink2); }
  .model-summaries { display: flex; gap: 18px; }
  .model-summary { flex: 1; min-width: 0; }
  .model-summary + .model-summary { padding-left: 18px; border-left: 1px solid var(--fs-border); }
  .model-heading { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 12px; }
  .model-heading strong { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .model-heading span, .score-row p, .model-summary > p { font-size: 11px; color: var(--fs-ink2); }
  i { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--fs-accent); }
  .ec i, i.ec, .satellite i { background: var(--fs-teal); }
  .score-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
  .score { display: flex; align-items: baseline; gap: 5px; white-space: nowrap; }
  .score b { font-size: 30px; font-weight: 600; line-height: 1.15; color: var(--fs-accent); font-variant-numeric: tabular-nums; }
  .ec .score b { color: var(--fs-teal); }
  .score span { font-size: 10px; color: var(--fs-ink2); }
  .missing b { color: var(--fs-ink2) !important; }
  .best-time { display: flex; flex-direction: column; gap: 7px; }
  .best-time span { font-size: 10px; color: var(--fs-ink2); }
  .best-time strong { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .analysis-tabs { display: flex; justify-content: center; gap: 20px; border-bottom: 1px solid var(--fs-border); }
  button { color: var(--fs-ink2); font: inherit; border: 0; background: transparent; cursor: pointer; }
  .analysis-tabs button { min-height: 40px; padding: 0 4px; border-bottom: 2px solid transparent; font-size: 12px; white-space: nowrap; }
  .analysis-tabs button:hover { color: var(--fs-ink); }
  .analysis-tabs button.active { color: var(--fs-accent); border-bottom-color: var(--fs-accent); font-weight: 600; }
  .result-body { display: flex; flex-direction: column; gap: 12px; }
  h2 { margin: 0; font-size: 13px; font-weight: 600; }
  .metric-group > h2 { margin-bottom: 8px; color: var(--fs-ink2); font-size: 11px; font-weight: 500; }
  .metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .metric { min-height: 108px; padding: 12px; background: var(--fs-surface); border: 1px solid var(--fs-border); border-radius: 7px; box-shadow: inset 0 1px 0 var(--fs-edge); }
  h3 { margin: 0 0 8px; font-size: 12px; font-weight: 500; }
  .metric-values { display: flex; gap: 10px; margin-bottom: 8px; }
  .metric-values > div { display: flex; flex: 1; flex-direction: column; min-width: 0; gap: 3px; }
  .metric-values span { color: var(--fs-accent); font-size: 10px; }
  .metric-values span.ec { color: var(--fs-teal); }
  .metric-values strong { font-size: 19px; line-height: 1.25; font-weight: 600; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  .metric p { font-size: 10px; color: var(--fs-ink2); }
  .curve { border-top: 1px solid var(--fs-border); padding: 18px 0 0; }
  .curve-title { display: flex; justify-content: space-between; gap: 12px; }
  .curve-title span { font-size: 10px; color: var(--fs-ink2); }
  svg { display: block; width: 100%; max-height: 210px; margin-top: 12px; }
  svg text { font-size: 10px; fill: var(--fs-ink2); }
  .guide { stroke: var(--fs-border); }
  polyline { fill: none; stroke: var(--fs-accent); stroke-width: 2; }
  polyline.ec { stroke: var(--fs-teal); }
  circle { fill: var(--fs-accent); } circle.ec { fill: var(--fs-teal); }
  .empty { text-align: center; color: var(--fs-ink2); padding: 46px 12px; font-size: 12px; }
  .model-selector { align-self: flex-start; display: flex; gap: 2px; padding: 2px; border-radius: 6px; background: var(--fs-segment); }
  .model-selector button { min-width: 66px; padding: 6px 12px; border-radius: 4px; font-size: 11px; }
  .model-selector button.active { color: var(--fs-ink); background: var(--fs-control); box-shadow: var(--fs-segment-shadow); }
  .error, .model-heading span.error, .model-summary > p.error { color: var(--fs-error); }
  .result-notice { padding: 20px 0; }
  @media (max-width: 1200px) and (min-width: 901px) { .score-row { flex-direction: column; } .best-time { flex-direction: row; align-items: center; gap: 8px; } }
  @media (max-width: 600px) { .forecast-hero { padding: 14px; } .model-summaries { gap: 12px; } .model-summary + .model-summary { padding-left: 12px; } .score-row { flex-direction: column; } .best-time { gap: 3px; } .analysis-tabs { gap: 4px; justify-content: space-between; } .analysis-tabs button { font-size: 11px; } .metrics { grid-template-columns: 1fr; gap: 8px; } .metric { min-height: 102px; } .metric-values strong { font-size: 21px; } }
</style>
