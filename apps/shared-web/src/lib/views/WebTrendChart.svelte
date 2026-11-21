<script lang="ts">
  import ChevronLeft from "@lucide/svelte/icons/chevron-left";
  import ChevronRight from "@lucide/svelte/icons/chevron-right";
  import UPlotChart from "./UPlotChart.svelte";
  import { TREND_SERIES, trendSamples, trendSegments, trendSummary, trendTime, trendValue, type TrendKey, type TrendValueKey } from "../forecast-trends";

  let { report, timezone, selectedUtc = null, onSelect = () => {}, aodField = "quality_aod550", aodLabel = "AOD550" }: {
    report: Record<string, unknown> | null; timezone: string; selectedUtc?: string | null; onSelect?: (utc: string) => void;
    aodField?: string; aodLabel?: string;
  } = $props();
  let enabled = $state<TrendKey[]>(TREND_SERIES.map((series) => series.key));
  const samples = $derived(trendSamples(report, aodField));
  const summary = $derived(trendSummary(samples));
  const series = $derived(TREND_SERIES.filter((item) => enabled.includes(item.key)).map((item) => ({
    ...item, segments: trendSegments(samples, item.key),
  })));
  const hasSeries = $derived(series.some((item) => item.segments.length));
  const aodSegments = $derived(trendSegments(samples, "aod"));
  const selectedIndex = $derived(samples.findIndex((row) => row.utc === selectedUtc));
  const activeIndex = $derived(Math.min(Math.max(0, selectedIndex >= 0 ? selectedIndex : summary.peakIndex ?? 0), Math.max(0, samples.length - 1)));
  const active = $derived(samples[activeIndex] ?? null);
  const low = $derived(summary.firstTime ?? 0);
  const high = $derived(summary.lastTime ?? 1);
  const aodMinimum = $derived(Math.max(0, (summary.aodMin ?? 0) - Math.max(.05, ((summary.aodMax ?? 0) - (summary.aodMin ?? 0)) * .25)));
  const aodMaximum = $derived(Math.max(aodMinimum + .1, (summary.aodMax ?? 0) + Math.max(.05, ((summary.aodMax ?? 0) - (summary.aodMin ?? 0)) * .25)));
  // uPlot 数据：x 取全量去重时点（升序），各曲线按时间对齐，缺测置 null 断线。
  const times = $derived([...new Set(samples.flatMap((sample) => sample.time === null ? [] : [sample.time]))].sort((a, b) => a - b));
  const valueAt = (key: TrendValueKey): (number | null)[] => {
    const byTime = new Map(samples.filter((sample) => sample.time !== null).map((sample) => [sample.time as number, sample]));
    return times.map((time) => {
      const sample = byTime.get(time);
      return sample ? trendValue(sample, key) : null;
    });
  };
  const ratioData = $derived.by(() => {
    const data: [number[], ...(number | null)[][]] = [times];
    for (const item of series) data.push(valueAt(item.key));
    return data;
  });
  const aodData: [number[], ...(number | null)[][]] = $derived([times, valueAt("aod")]);
  const ratio = (value: number | null | undefined) => value == null ? "--" : `${(value * 100).toFixed(0)}%`;
  const number = (value: number | null | undefined) => value == null ? "--" : value.toFixed(3);
  function select(index: number) { const utc = samples[index]?.utc; if (utc) onSelect(utc); }
  function selectAtPoint(index: number) {
    const time = times[index];
    const sample = time === undefined ? undefined : samples.find((row) => row.time === time);
    if (sample?.utc) onSelect(sample.utc);
  }
</script>

<section class="physical-trends" aria-label="云况与光学趋势">
  <header>
    <h2>云况与光学趋势</h2>
    <div class="summary">
      <span>有效时次 <strong>{summary.validCount} / {samples.length}</strong></span>
      <span>峰值时刻 <strong>{trendTime(summary.peakIndex === null ? null : samples[summary.peakIndex].time, timezone)}</strong></span>
      <span>{aodLabel} <strong>{summary.aodMin === null ? "--" : `${summary.aodMin.toFixed(3)} - ${summary.aodMax!.toFixed(3)}`}</strong></span>
    </div>
  </header>
  {#if samples.length}
    <fieldset class="series-controls">
      <legend>云况曲线</legend>
      {#each TREND_SERIES as item (item.key)}
        <label>
          <input type="checkbox" value={item.key} bind:group={enabled} />
          <i style:border-color={item.color} style:border-top-style={item.dash ? "dashed" : "solid"}></i>
          <span>{item.label}</span>
        </label>
      {/each}
    </fieldset>
    <div class="plots">
      <div class="ratio-plot">
        <h3>云况与光路占比</h3>
        <UPlotChart
          ariaLabel="逐时云量、可烧比例与光路占比曲线"
          data={ratioData}
          series={series.map((item) => ({ label: item.label, color: item.color, dash: item.dash }))}
          xRange={[low, high]}
          yRange={[0, 1]}
          yTicks={[0, .25, .5, .75, 1]}
          formatX={(time) => trendTime(time, timezone)}
          formatY={(value) => `${value * 100}%`}
          selection={active?.time ?? null}
          onSelect={selectAtPoint}
          dataSegments={series.map((item) => item.segments.length).join(",")}
        />
        {#if !hasSeries}<p class="plot-empty">{enabled.length ? "暂无有效云况时序" : "未选择曲线"}</p>{/if}
      </div>
      <div class="aod-plot">
        <h3>{aodLabel}</h3>
        <UPlotChart
          ariaLabel={`${aodLabel} 逐时光学厚度曲线`}
          data={aodData}
          series={[{ label: aodLabel, color: "var(--trend-burnable)", width: 2, pointSize: 5 }]}
          xRange={[low, high]}
          yRange={[aodMinimum, aodMaximum]}
          yTicks={[aodMinimum, (aodMinimum + aodMaximum) / 2, aodMaximum]}
          formatX={(time) => trendTime(time, timezone)}
          formatY={(value) => value.toFixed(2)}
          selection={active?.time ?? null}
          onSelect={selectAtPoint}
          dataSegments={String(aodSegments.length)}
          dataPoints={String(aodSegments.reduce((count, segment) => count + segment.length, 0))}
        />
        {#if !aodSegments.length}<p class="plot-empty">暂无有效 {aodLabel} 时序</p>{/if}
      </div>
    </div>
    <div class="timeline">
      <button type="button" aria-label="上一时次" title="上一时次" disabled={activeIndex <= 0} onclick={() => select(activeIndex - 1)}><ChevronLeft size={16} /></button>
      <input type="range" min="0" max={Math.max(0, samples.length - 1)} step="1" value={activeIndex}
        aria-label="逐时时刻" aria-valuetext={`${trendTime(active?.time ?? null, timezone)}${active?.valid ? "" : " 缺测"}`}
        disabled={samples.length < 2} oninput={(event) => select(Number(event.currentTarget.value))} />
      <button type="button" aria-label="下一时次" title="下一时次" disabled={activeIndex >= samples.length - 1} onclick={() => select(activeIndex + 1)}><ChevronRight size={16} /></button>
      <output aria-live="polite">{trendTime(active?.time ?? null, timezone)} · {active?.valid ? `质量 ${number(active.quality)}` : "缺测"}</output>
    </div>
    <dl class="readings" aria-label="当前时次物理量">
      {#each TREND_SERIES as item (item.key)}<div><dt>{item.label}</dt><dd>{ratio(active?.values[item.key])}</dd></div>{/each}
      <div><dt>{aodLabel}</dt><dd>{number(active?.aod)}</dd></div>
    </dl>
  {:else}<p class="empty">暂无逐时云况与光学结果</p>{/if}
</section>

<style>
  .physical-trends { --trend-cloud: #2563a5; --trend-burnable: #ae6a17; --trend-ratio: #8052a4; --trend-transmission: #367c8c; border-top: 1px solid var(--fs-border); padding-top: 18px; min-width: 0; }
  :global(:root[data-theme="dark"]) .physical-trends { --trend-cloud: #84b7f2; --trend-burnable: #e8be70; --trend-ratio: #c5a0e8; --trend-transmission: #8ec8df; }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  h2, h3, p { margin: 0; }
  h2 { font-size: 13px; font-weight: 600; }
  h3 { font-size: 11px; font-weight: 500; color: var(--fs-ink2); }
  .summary { display: flex; gap: 8px 16px; flex-wrap: wrap; font-size: 10px; color: var(--fs-ink2); }
  .summary strong { font-weight: 500; color: var(--fs-ink); font-variant-numeric: tabular-nums; }
  .series-controls { display: flex; flex-wrap: wrap; gap: 2px 14px; min-width: 0; padding: 0; margin: 10px 0 4px; border: 0; }
  legend { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  label { display: inline-flex; align-items: center; gap: 5px; min-height: 34px; cursor: pointer; font-size: 10px; color: var(--fs-ink2); }
  label span { overflow-wrap: anywhere; }
  label input { accent-color: var(--fs-accent); width: 13px; height: 13px; margin: 0; }
  label i { display: block; width: 14px; border-top: 2px solid; flex: none; }
  .plots { display: grid; grid-template-columns: minmax(0, 1fr) minmax(170px, 28%); gap: 18px; min-width: 0; }
  .ratio-plot, .aod-plot { position: relative; min-width: 0; }
  .plot-empty { position: absolute; inset: 60px 10px 50px 40px; display: flex; align-items: center; justify-content: center; font-size: 11px; color: var(--fs-ink2); text-align: center; background: var(--fs-bg); }
  .timeline { display: grid; grid-template-columns: 30px minmax(0, 1fr) 30px auto; align-items: center; gap: 6px; margin: 2px 0 14px; }
  .timeline button { display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 1px solid var(--fs-control-border); border-radius: 4px; background: var(--fs-control); color: var(--fs-ink); cursor: pointer; }
  .timeline button:disabled { cursor: default; opacity: .4; }
  .timeline input { width: 100%; min-width: 0; margin: 0; accent-color: var(--fs-accent); }
  output { min-width: 115px; text-align: right; font-size: 10px; color: var(--fs-ink2); font-variant-numeric: tabular-nums; }
  .readings { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; margin: 0; }
  .readings div { min-width: 0; }
  dt { margin-bottom: 4px; color: var(--fs-ink2); font-size: 10px; overflow-wrap: anywhere; }
  dd { margin: 0; font-size: 12px; font-variant-numeric: tabular-nums; }
  .empty { padding: 36px 12px; text-align: center; color: var(--fs-ink2); font-size: 12px; }
  @media (max-width: 600px) {
    .plots { grid-template-columns: minmax(0, 1fr); gap: 12px; }
    .series-controls { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 10px; }
    label { min-height: 44px; }
    .timeline { grid-template-columns: 44px minmax(0, 1fr) 44px; }
    .timeline button { width: 44px; height: 44px; }
    .timeline input { min-height: 44px; }
    output { grid-column: 1 / -1; text-align: center; }
  }
</style>
