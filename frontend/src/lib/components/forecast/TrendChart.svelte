<script lang="ts">
  import type { ModelSource, StepPayload } from '@/api/types';
  import Activity from '@lucide/svelte/icons/activity';
  import Clock3 from '@lucide/svelte/icons/clock-3';
  import Database from '@lucide/svelte/icons/database';
  import { CHINA_TIME_ZONE, formatTime, formatUtcDateTime } from '@/utils/format';

  let {
    steps = [],
    timeZone = CHINA_TIME_ZONE,
    targetDate = null,
    cycleTimeUtc = null,
    modelSource = null,
    selectedTime = null,
    onTimeSelected = undefined,
  }: {
    steps?: StepPayload[];
    timeZone?: string;
    targetDate?: string | null;
    cycleTimeUtc?: string | null;
    modelSource?: ModelSource | null;
    selectedTime?: string | null;
    onTimeSelected?: ((step: StepPayload) => void) | undefined;
  } = $props();

  type TrendKey = 'quality' | 'coverage' | 'illumination' | 'returnTransmission';

  const W = 1000;
  const H = 300;
  const L = 54;
  const R = 42;
  const T = 22;
  const B = 42;
  const PW = W - L - R;
  const PH = H - T - B;
  const AOD_W = 360;
  const AOD_H = 156;
  const AOD_L = 38;
  const AOD_R = 16;
  const AOD_T = 18;
  const AOD_B = 30;
  const AOD_PW = AOD_W - AOD_L - AOD_R;
  const AOD_PH = AOD_H - AOD_T - AOD_B;

  const series: Array<{ key: TrendKey; color: string; label: string; dash: string }> = [
    { key: 'quality', color: 'var(--chart-quality)', label: '火烧云质量', dash: '' },
    { key: 'coverage', color: 'var(--chart-coverage)', label: '可烧云占比', dash: '7 4' },
    { key: 'illumination', color: 'var(--chart-illumination)', label: '可烧比例', dash: '3 4' },
    { key: 'returnTransmission', color: 'var(--chart-transmission)', label: '回程透过率', dash: '3 4' },
  ];
  const qualityGrid = [0, 0.625, 1.25, 1.875, 2.5];

  function finite(value: unknown, fallback = 0): number {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value: number, minimum: number, maximum: number): number {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function sourceLabel(source: ModelSource): string {
    return source === 'ec' ? 'ECMWF IFS' : source === 'satellite' ? '实况' : 'GFS';
  }

  function ratio(value: unknown): number {
    return clamp(finite(value), 0, 1);
  }

  function quality(step: StepPayload): number {
    return clamp(finite(step.instant_quality_index), 0, 2.5);
  }

  function aod(step: StepPayload): number | null {
    const value = Number(step.quality_aod550);
    return Number.isFinite(value) && value >= 0 ? value : null;
  }

  function value(step: StepPayload, key: TrendKey): number {
    if (key === 'quality') return quality(step);
    if (key === 'coverage') return ratio(step.burnable_cloud_coverage);
    if (key === 'illumination') return ratio(step.forward_burnable_cloud_ratio);
    return ratio(step.observer_return_transmission);
  }

  function x(index: number): number {
    return L + (PW * index) / Math.max(1, safeSteps.length - 1);
  }

  function yQuality(valueToPlot: number): number {
    return T + PH * (1 - clamp(valueToPlot, 0, 2.5) / 2.5);
  }

  function yRatio(valueToPlot: number): number {
    return T + PH * (1 - clamp(valueToPlot, 0, 1));
  }

  function plotY(step: StepPayload, key: TrendKey): number {
    return key === 'quality' ? yQuality(value(step, key)) : yRatio(value(step, key));
  }

  function segments(key: TrendKey): string[] {
    const result: string[] = [];
    let current: string[] = [];
    safeSteps.forEach((step, index) => {
      if (!step.valid) {
        if (current.length) result.push(current.join(' '));
        current = [];
        return;
      }
      current.push(`${x(index)},${plotY(step, key)}`);
    });
    if (current.length) result.push(current.join(' '));
    return result;
  }

  function aodX(index: number): number {
    return AOD_L + (AOD_PW * index) / Math.max(1, safeSteps.length - 1);
  }

  function yAod(valueToPlot: number): number {
    return AOD_T + AOD_PH * (1 - (valueToPlot - aodDomainMin) / Math.max(0.1, aodDomainMax - aodDomainMin));
  }

  function aodSegments(): string[] {
    const result: string[] = [];
    let current: string[] = [];
    safeSteps.forEach((step, index) => {
      const valueToPlot = step.valid ? aod(step) : null;
      if (valueToPlot == null) {
        if (current.length) result.push(current.join(' '));
        current = [];
        return;
      }
      current.push(`${aodX(index)},${yAod(valueToPlot)}`);
    });
    if (current.length) result.push(current.join(' '));
    return result;
  }

  function labelInterval(): number {
    if (safeSteps.length <= 6) return 1;
    if (safeSteps.length <= 12) return 2;
    return 3;
  }

  function rawQuality(step: StepPayload): number {
    return quality(step);
  }

  let allSteps = $derived(Array.isArray(steps) ? steps : []);
  let validSteps = $derived(allSteps.filter((step) => step.valid));
  let safeSteps = $derived(validSteps);
  let validCount = $derived(validSteps.length);
  let firstStep = $derived(safeSteps[0]?.valid_time_local ?? null);
  let lastStep = $derived(safeSteps[safeSteps.length - 1]?.valid_time_local ?? null);
  let peakStep = $derived(
    validSteps.reduce<StepPayload | null>((best, step) => {
      if (!best || quality(step) > quality(best)) return step;
      return best;
    }, null)
  );
  let aodValues = $derived(validSteps.map(aod).filter((valueToPlot): valueToPlot is number => valueToPlot != null));
  let aodMin = $derived(aodValues.length ? Math.min(...aodValues) : 0);
  let aodMax = $derived(aodValues.length ? Math.max(...aodValues) : 0);
  let aodPadding = $derived(Math.max(0.05, (aodMax - aodMin) * 0.25));
  let aodDomainMin = $derived(Math.max(0, aodMin - aodPadding));
  let aodDomainMax = $derived(Math.max(aodDomainMin + 0.1, aodMax + aodPadding));
  let selectedStep = $derived(safeSteps.find((step) => step.valid_time_local === selectedTime) ?? null);
  let activeStep = $derived(selectedStep ?? peakStep);
  let effectiveModelSource = $derived(modelSource ?? (cycleTimeUtc ? 'gfs' : 'satellite'));
  let modelLabel = $derived(sourceLabel(effectiveModelSource));
</script>

<section class="trend-shell glass-panel" aria-labelledby="trend-title">
  <header class="trend-header">
    <div class="trend-title-block">
      <div class="eyebrow"><Activity class="h-3.5 w-3.5" /> 单点逐时核心</div>
      <h3 id="trend-title">质量与大气条件趋势</h3>
      <p>
        {#if cycleTimeUtc}{modelLabel} 起报 {formatUtcDateTime(cycleTimeUtc)} · {:else}{modelLabel} · {/if}
        精确单点结果
      </p>
    </div>
    <div class="trend-meta" aria-label="趋势数据来源">
      {#if targetDate}<span class="meta-pill">{targetDate}</span>{/if}
      <span class="meta-pill"><Database class="h-3 w-3" />精确单点</span>
      <span class="meta-pill">{modelLabel}</span>
    </div>
  </header>

  {#if validCount > 0}
    <div class="trend-summary" aria-label="趋势摘要">
      <div class="summary-item summary-primary">
        <span>峰值质量</span>
        <strong>{peakStep ? rawQuality(peakStep).toFixed(3) : '—'}</strong>
        <small>/ 2.5</small>
      </div>
      <div class="summary-item">
        <span>峰值时刻</span>
        <strong>{peakStep ? formatTime(peakStep.valid_time_local, timeZone) : '—'}</strong>
        <small>{peakStep?.physically_burnable ? '物理条件通过' : '无可烧目标云'}</small>
      </div>
      <div class="summary-item">
        <span>有效时次</span>
        <strong>{validCount}</strong>
        <small>{allSteps.length > validCount ? `共 ${allSteps.length} 个时次，已隐藏无效时刻` : '仅展示有效太阳窗口'}</small>
      </div>
      <div class="summary-item">
        <span>AOD550 范围</span>
        <strong>{aodValues.length ? `${aodMin.toFixed(3)}–${aodMax.toFixed(3)}` : '—'}</strong>
        <small>{aodValues.length ? '质量评分使用值' : '当前时段无有效值'}</small>
      </div>
    </div>

    <div class="trend-layout">
      <div class="main-plot">
        <div class="plot-heading">
          <span>质量主线</span>
          <span class="plot-unit">左轴质量分 · 右轴占比</span>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} class="trend-svg" role="img" aria-label="逐时质量和物理条件趋势">
          {#each qualityGrid as grid}
            <line x1={L} y1={yQuality(grid)} x2={W - R} y2={yQuality(grid)} class="grid-line" />
            <text x={L - 10} y={yQuality(grid) + 4} text-anchor="end" class="axis-label">{grid.toFixed(grid === 0 || grid === 2.5 ? 1 : 2)}</text>
            <text x={W - R + 10} y={yQuality(grid) + 4} text-anchor="start" class="axis-label">{Math.round((grid / 2.5) * 100)}%</text>
          {/each}
          {#each series as item}
            {#each segments(item.key) as segment}
              <polyline
                points={segment}
                fill="none"
                stroke={item.color}
                stroke-width={item.key === 'quality' ? 3.5 : 2}
                stroke-dasharray={item.dash}
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            {/each}
          {/each}
          {#each safeSteps as step, index}
            {#if step.valid_time_local === selectedTime}
              <line x1={x(index)} x2={x(index)} y1={T} y2={T + PH} class="selected-line" />
            {/if}
            {#if step.valid}
              <circle
                cx={x(index)}
                cy={plotY(step, 'quality')}
                r={step.valid_time_local === selectedTime ? 6 : 4}
                fill={step.physically_burnable ? 'var(--chart-quality)' : 'var(--muted-foreground)'}
                stroke="var(--card)"
                stroke-width="2"
                role="button"
                tabindex="0"
                aria-label={`选择 ${formatTime(step.valid_time_local, timeZone)}，质量 ${rawQuality(step).toFixed(3)} / 2.5`}
                onclick={() => onTimeSelected?.(step)}
                onkeydown={(event) => (event.key === 'Enter' || event.key === ' ') && onTimeSelected?.(step)}
              />
            {/if}
            {#if index % labelInterval() === 0 || index === safeSteps.length - 1}
              <text x={x(index)} y={H - 12} text-anchor="middle" class="axis-label time-label">{formatTime(step.valid_time_local, timeZone)}</text>
            {/if}
          {/each}
          <text x={L} y={T - 8} text-anchor="start" class="axis-caption">质量分</text>
          <text x={W - R} y={T - 8} text-anchor="end" class="axis-caption">条件占比</text>
        </svg>
        <div class="legend" aria-label="趋势图例">
          {#each series as item}
            <span><i style={`background:${item.color}`}></i>{item.label}</span>
          {/each}
        </div>
      </div>

      <aside class="trend-aside">
        <section class="aside-section aod-section" aria-labelledby="aod-title">
          <div class="aside-heading">
            <div>
              <h4 id="aod-title">AOD550</h4>
              <p>质量评分使用的本地气溶胶光学厚度</p>
            </div>
            <strong>{aodValues.length ? aodMax.toFixed(3) : '—'}</strong>
          </div>
          {#if aodValues.length}
            <svg viewBox={`0 0 ${AOD_W} ${AOD_H}`} class="aod-svg" role="img" aria-label="AOD550 逐时趋势">
              <line x1={AOD_L} y1={AOD_T} x2={AOD_L} y2={AOD_H - AOD_B} class="axis-line" />
              <line x1={AOD_L} y1={AOD_H - AOD_B} x2={AOD_W - AOD_R} y2={AOD_H - AOD_B} class="axis-line" />
              <line x1={AOD_L} y1={yAod(aodMax)} x2={AOD_W - AOD_R} y2={yAod(aodMax)} class="grid-line" />
              <line x1={AOD_L} y1={yAod(aodMin)} x2={AOD_W - AOD_R} y2={yAod(aodMin)} class="grid-line" />
              <text x={AOD_L - 6} y={yAod(aodMax) + 4} text-anchor="end" class="axis-label">{aodMax.toFixed(2)}</text>
              <text x={AOD_L - 6} y={yAod(aodMin) + 4} text-anchor="end" class="axis-label">{aodMin.toFixed(2)}</text>
              {#each aodSegments() as segment}
                <polyline points={segment} fill="none" stroke="var(--chart-illumination)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
              {/each}
              {#each safeSteps as step, index}
                {#if step.valid && aod(step) != null}
                  <circle cx={aodX(index)} cy={yAod(aod(step) ?? 0)} r="2.5" fill="var(--chart-illumination)" />
                {/if}
              {/each}
              <text x={AOD_L} y={AOD_H - 8} text-anchor="start" class="axis-label">{firstStep ? formatTime(firstStep, timeZone) : '—'}</text>
              <text x={AOD_W - AOD_R} y={AOD_H - 8} text-anchor="end" class="axis-label">{lastStep ? formatTime(lastStep, timeZone) : '—'}</text>
            </svg>
          {:else}
            <div class="aside-empty">暂无可用 AOD550 时序</div>
          {/if}
        </section>

        <section class="aside-section timeline-section" aria-labelledby="timeline-title">
          <div class="aside-heading">
            <div>
              <h4 id="timeline-title"><Clock3 class="h-3.5 w-3.5" />事件时间轴</h4>
              <p>{firstStep ? formatTime(firstStep, timeZone) : '—'}–{lastStep ? formatTime(lastStep, timeZone) : '—'}</p>
            </div>
            <strong>{activeStep ? formatTime(activeStep.valid_time_local, timeZone) : '—'}</strong>
          </div>
          <div class="timeline-track" aria-label="选择逐时时刻">
            {#each safeSteps as step}
              {#if step.valid}
                <button
                  type="button"
                  class:active={step.valid_time_local === selectedTime}
                  class:peak={step.valid_time_local === peakStep?.valid_time_local}
                  aria-label={`查看 ${formatTime(step.valid_time_local, timeZone)}`}
                  aria-pressed={step.valid_time_local === selectedTime}
                  title={formatTime(step.valid_time_local, timeZone)}
                  onclick={() => onTimeSelected?.(step)}
                ></button>
              {:else}
                <span class="timeline-gap" title="超出有效太阳窗口"></span>
              {/if}
            {/each}
          </div>
          <div class="timeline-labels">
            <span>{firstStep ? formatTime(firstStep, timeZone) : '—'}</span>
            <span>{lastStep ? formatTime(lastStep, timeZone) : '—'}</span>
          </div>
        </section>
      </aside>
    </div>

    {#if activeStep}
      <footer class="trend-footer">
        <strong>当前 {formatTime(activeStep.valid_time_local, timeZone)} · 质量 {rawQuality(activeStep).toFixed(3)}</strong>
      </footer>
    {/if}
  {:else}
    <div class="trend-empty" role="status">
      <Activity class="h-6 w-6" />
      <strong>{allSteps.length ? '当前事件窗口暂无有效时刻' : '暂无逐时质量结果'}</strong>
      <p>
        {allSteps.length
          ? '太阳几何或模式输入未在该窗口形成可评分时次，AOD 与趋势曲线暂不绘制。'
          : '预测完成后，单点逐时质量、AOD550 和起报来源会显示在这里。'}
      </p>
      {#if targetDate || cycleTimeUtc}
        <div class="empty-meta">{targetDate ?? '未指定日期'} · {modelLabel}{#if cycleTimeUtc} {formatUtcDateTime(cycleTimeUtc)}{/if}</div>
      {/if}
    </div>
  {/if}
</section>

<style>
  .trend-shell { overflow: hidden; border: 1px solid var(--glass-border); border-radius: 10px; }
  .trend-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 17px 20px 14px; border-bottom: 1px solid var(--border); }
  .trend-title-block { min-width: 0; }
  .eyebrow { display: flex; align-items: center; gap: 5px; color: var(--primary); font-size: 10px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
  h3, h4, p { margin: 0; }
  h3 { margin-top: 4px; color: var(--card-foreground); font-size: 16px; font-weight: 600; }
  .trend-header p { margin-top: 4px; color: var(--muted-foreground); font-size: 11px; line-height: 1.5; }
  .trend-meta { display: flex; flex: none; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
  .meta-pill { display: inline-flex; align-items: center; gap: 4px; min-height: 24px; padding: 3px 8px; border: 1px solid var(--border); border-radius: 5px; color: var(--muted-foreground); font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 10px; font-variant-numeric: tabular-nums; }
  .trend-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--border); }
  .summary-item { min-width: 0; padding: 12px 16px; border-right: 1px solid var(--border); }
  .summary-item:last-child { border-right: 0; }
  .summary-item span, .summary-item small { display: block; color: var(--muted-foreground); font-size: 10px; line-height: 1.4; }
  .summary-item strong { display: inline-block; margin-top: 3px; color: var(--card-foreground); font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 17px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  .summary-item small { margin-top: 2px; }
  .summary-primary strong { color: var(--primary); }
  .trend-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, 31%); min-width: 0; }
  .main-plot { min-width: 0; padding: 16px 18px 12px 20px; }
  .plot-heading, .aside-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
  .plot-heading { color: var(--card-foreground); font-size: 12px; font-weight: 600; }
  .plot-unit { color: var(--muted-foreground); font-size: 10px; font-weight: 400; }
  .trend-svg { display: block; width: 100%; height: auto; min-height: 220px; margin-top: 5px; }
  .grid-line { stroke: var(--border); stroke-width: 1; }
  .selected-line { stroke: var(--foreground); stroke-width: 1; stroke-dasharray: 3 3; opacity: .45; }
  .axis-label { fill: var(--muted-foreground); font-size: 10px; }
  .axis-caption { fill: var(--muted-foreground); font-size: 9px; }
  .legend { display: flex; flex-wrap: wrap; gap: 7px 14px; padding: 0 8px 2px; color: var(--muted-foreground); font-size: 10px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
  .legend i { display: inline-block; width: 13px; height: 3px; border-radius: 2px; }
  .trend-aside { min-width: 0; border-left: 1px solid var(--border); }
  .aside-section { min-width: 0; padding: 16px; }
  .aside-section + .aside-section { border-top: 1px solid var(--border); }
  .aside-heading h4 { display: flex; align-items: center; gap: 5px; color: var(--card-foreground); font-size: 12px; font-weight: 600; }
  .aside-heading p { margin-top: 3px; color: var(--muted-foreground); font-size: 10px; line-height: 1.4; }
  .aside-heading > strong { flex: none; color: var(--card-foreground); font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 15px; font-variant-numeric: tabular-nums; }
  .aod-svg { display: block; width: 100%; height: auto; margin-top: 8px; }
  .axis-line { stroke: var(--border); stroke-width: 1; }
  .aside-empty { display: grid; min-height: 80px; place-items: center; margin-top: 8px; border: 1px dashed var(--border); color: var(--muted-foreground); font-size: 11px; }
  .timeline-track { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(4px, 1fr); align-items: center; gap: 3px; min-height: 34px; margin-top: 12px; padding: 0 2px; border-bottom: 1px solid var(--border); }
  .timeline-track button, .timeline-gap { width: 100%; min-width: 4px; height: 10px; border: 0; border-radius: 3px 3px 0 0; background: var(--muted); }
  .timeline-track button { cursor: pointer; }
  .timeline-track button:hover, .timeline-track button:focus-visible { background: var(--primary); }
  .timeline-track button.peak { background: color-mix(in srgb, var(--primary) 62%, var(--muted)); }
  .timeline-track button.active { height: 18px; background: var(--primary); }
  .timeline-gap { background: transparent; border: 1px dashed var(--border); }
  .timeline-labels { display: flex; justify-content: space-between; gap: 8px; margin-top: 6px; color: var(--muted-foreground); font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 10px; font-variant-numeric: tabular-nums; }
  .trend-footer { display: flex; justify-content: flex-end; padding: 10px 20px; border-top: 1px solid var(--border); }
  .trend-footer strong { color: var(--card-foreground); font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 10px; font-variant-numeric: tabular-nums; }
  .trend-empty { display: grid; place-items: center; gap: 8px; min-height: 220px; padding: 24px; color: var(--muted-foreground); text-align: center; }
  .trend-empty strong { color: var(--card-foreground); font-size: 14px; }
  .trend-empty p { max-width: 420px; font-size: 11px; line-height: 1.6; }
  .empty-meta { padding: 5px 8px; border: 1px solid var(--border); border-radius: 5px; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 10px; font-variant-numeric: tabular-nums; }
  @media (max-width: 760px) {
    .trend-header { display: block; padding: 14px 15px 12px; }
    .trend-meta { justify-content: flex-start; margin-top: 10px; }
    .trend-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .summary-item { border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
    .summary-item:nth-child(2n) { border-right: 0; }
    .summary-item:nth-last-child(-n + 2) { border-bottom: 0; }
    .trend-layout { display: block; }
    .main-plot { padding: 14px 10px 12px 12px; }
    .trend-svg { min-height: 190px; }
    .trend-aside { border-top: 1px solid var(--border); border-left: 0; }
    .trend-footer { padding: 10px 14px; }
  }
  @media (max-width: 420px) {
    .summary-item { padding: 10px 11px; }
    .summary-item strong { font-size: 15px; }
    .plot-unit { display: none; }
    .time-label { font-size: 9px; }
  }
</style>
