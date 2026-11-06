<script lang="ts">
  import type { OutdoorOpportunityForecast, OutdoorProduct } from '$lib/api/types';
  import Clock3 from '@lucide/svelte/icons/clock-3';
  import Gauge from '@lucide/svelte/icons/gauge';
  import Layers3 from '@lucide/svelte/icons/layers-3';
  import ListChecks from '@lucide/svelte/icons/list-checks';
  import Mountain from '@lucide/svelte/icons/mountain';
  import Rainbow from '@lucide/svelte/icons/rainbow';
  import Snowflake from '@lucide/svelte/icons/snowflake';
  import Telescope from '@lucide/svelte/icons/telescope';

  interface Props {
    data: OutdoorOpportunityForecast;
    product: Exclude<OutdoorProduct, 'weather'>;
    timeZone: string;
  }

  let { data, product, timeZone }: Props = $props();

  const productLabels = {
    stargazing: '星空观测',
    cloud_sea: '云海',
    rainbow: '彩虹',
    rime: '雾凇',
  } as const;

  const componentLabels: Record<string, string> = {
    darkness: '夜空黑度',
    cloud_clearance: '云层通透',
    visibility: '能见度',
    dryness: '空气干燥度',
    precipitation_clearance: '无降水支持',
    wind_comfort: '风力舒适度',
    moon_darkness: '低月光干扰',
    low_cloud_deck: '低云成层',
    temperature_inversion: '逆温支持',
    wind_stability: '风场稳定',
    upper_sky_clearance: '上层天空通透',
    viewpoint_above_cloud: '观景点高于云顶',
    timing: '时段适宜度',
    solar_geometry: '太阳几何',
    rain_evidence: '降水证据',
    sunlight_break: '云隙透光',
    shower_contrast: '阵雨反差',
    supercooled_temperature: '过冷温度',
    moisture_saturation: '水汽饱和',
    fog: '雾支持',
    low_cloud: '低云支持',
    wind_supply: '风输送',
    liquid_water: '液态水支持',
    freezing_layer_support: '冻结层支持',
    duration_support: '持续时长',
  };

  const diagnosticLabels: Record<string, [string, string]> = {
    solar_elevation_deg: ['太阳高度', '°'],
    solar_azimuth_deg: ['太阳方位', '°'],
    moon_elevation_deg: ['月亮高度', '°'],
    moon_illumination_percent: ['月面照明', '%'],
    cloud_cover_percent: ['总云量', '%'],
    low_cloud_cover_percent: ['低云量', '%'],
    visibility_m: ['能见度', 'm'],
    estimated_cloud_base_m: ['估算云底', 'm'],
    estimated_cloud_top_m: ['估算云顶', 'm'],
    viewpoint_clearance_m: ['云顶净空', 'm'],
    inversion_anomaly_c: ['逆温异常', '℃'],
    rainbow_bearing_deg: ['彩虹方位', '°'],
    rain_mm: ['小时雨量', 'mm'],
    precipitation_probability_percent: ['降水概率', '%'],
    hazardous_convection: ['强对流风险', ''],
    temperature_c: ['气温', '℃'],
    relative_humidity_percent: ['相对湿度', '%'],
    dew_point_spread_c: ['温露差', '℃'],
    freezing_level_height_m: ['冻结层高度', 'm'],
    continuous_support_hours: ['连续支持', 'h'],
  };

  function time(value: string | null): string {
    if (!value) return '—';
    return new Date(value).toLocaleTimeString('zh-CN', {
      timeZone,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }

  function diagnosticValue(key: string, value: string | number | boolean | null): string {
    if (value == null) return '—';
    if (typeof value === 'boolean') return value ? '是' : '否';
    const unit = diagnosticLabels[key]?.[1] ?? '';
    if (typeof value === 'number') {
      const digits = Math.abs(value) >= 100 ? 0 : 1;
      return `${value.toFixed(digits)}${unit}`;
    }
    return String(value);
  }

  function scoreClass(score: number): string {
    if (score >= 65) return 'good';
    if (score >= 45) return 'fair';
    if (score >= 25) return 'low';
    return 'poor';
  }
</script>

<section class="opportunity-view" aria-labelledby="opportunity-heading">
  <div class="score-band {scoreClass(data.score)}">
    <div class="product-mark" aria-hidden="true">
      {#if product === 'stargazing'}<Telescope />
      {:else if product === 'cloud_sea'}<Mountain />
      {:else if product === 'rainbow'}<Rainbow />
      {:else}<Snowflake />{/if}
    </div>
    <div class="score-copy">
      <p>{productLabels[product]}</p>
      <h2 id="opportunity-heading">{data.level}</h2>
      <span>{data.summary}</span>
    </div>
    <div class="score-value">
      <strong>{data.score.toFixed(1)}</strong>
      <span>潜势 / 100</span>
    </div>
    <dl class="score-meta">
      <div><dt>最佳时刻</dt><dd>{time(data.best_time_local)}</dd></div>
      <div><dt>可信度</dt><dd>{Math.round(data.confidence * 100)}%</dd></div>
      <div><dt>机会窗口</dt><dd>{data.windows.length}</dd></div>
    </dl>
  </div>

  <div class="analysis-grid">
    <section aria-labelledby="components-heading">
      <div class="subheading">
        <Layers3 class="h-4 w-4" aria-hidden="true" />
        <h3 id="components-heading">评分分量</h3>
      </div>
      <dl class="component-list">
        {#each Object.entries(data.components) as [key, value]}
          <div>
            <dt><span>{componentLabels[key] ?? key}</span><b>{Math.round(value * 100)}</b></dt>
            <dd>
              <span class="component-track" role="progressbar" aria-label={componentLabels[key] ?? key} aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(value * 100)}>
                <i style:width={`${Math.max(0, Math.min(100, value * 100))}%`}></i>
              </span>
            </dd>
          </div>
        {/each}
      </dl>
    </section>

    <section aria-labelledby="diagnostics-heading">
      <div class="subheading">
        <Gauge class="h-4 w-4" aria-hidden="true" />
        <h3 id="diagnostics-heading">物理诊断</h3>
      </div>
      <dl class="diagnostic-grid">
        {#each Object.entries(data.diagnostics) as [key, value]}
          <div>
            <dt>{diagnosticLabels[key]?.[0] ?? key}</dt>
            <dd>{diagnosticValue(key, value)}</dd>
          </div>
        {/each}
      </dl>
    </section>
  </div>

  <section class="windows-section" aria-labelledby="windows-heading">
    <div class="subheading">
      <Clock3 class="h-4 w-4" aria-hidden="true" />
      <h3 id="windows-heading">机会窗口</h3>
      <span>{data.windows.length || '无'}</span>
    </div>
    {#if data.windows.length}
      <div class="window-list">
        {#each data.windows as window, index}
          <article>
            <span>{String(index + 1).padStart(2, '0')}</span>
            <div><strong>{time(window.start_time_local)}–{time(window.end_time_local)}</strong><small>峰值 {time(window.best_time_local)}</small></div>
            <b>{window.peak_score.toFixed(1)}</b>
            <small>{window.duration_hours.toFixed(1)} h</small>
          </article>
        {/each}
      </div>
    {:else}
      <p class="empty-window">没有达到一般等级的连续时段。</p>
    {/if}
  </section>

  <section class="hourly-score-section" aria-labelledby="hourly-score-heading">
    <div class="subheading">
      <ListChecks class="h-4 w-4" aria-hidden="true" />
      <h3 id="hourly-score-heading">逐小时潜势</h3>
      <span>0–100</span>
    </div>
    <div class="hourly-score-grid">
      {#each data.hourly as item}
        <div
          class="hour-score {scoreClass(item.score)}"
          aria-label={`${time(item.valid_time_local)}，${item.level}，${item.score.toFixed(1)} 分`}
          title={`${time(item.valid_time_local)} · ${item.level} · ${item.score.toFixed(1)}`}
        >
          <time>{time(item.valid_time_local)}</time>
          <strong>{Math.round(item.score)}</strong>
          <i style:height={`${Math.max(3, Math.min(100, item.score))}%`}></i>
        </div>
      {/each}
    </div>
  </section>

  {#if data.assumptions.length}
    <div class="assumption-list">
      {#each data.assumptions as assumption}<p>{assumption}</p>{/each}
    </div>
  {/if}
</section>

<style>
  .opportunity-view { display: grid; gap: 24px; }
  .score-band { display: grid; grid-template-columns: 48px minmax(220px, 1fr) auto minmax(220px, auto); align-items: center; gap: 16px; padding: 18px 0; border-top: 2px solid var(--score-weak); border-bottom: 1px solid var(--border); }
  .score-band.good { border-top-color: var(--score-high); }
  .score-band.fair { border-top-color: var(--score-medium); }
  .score-band.low { border-top-color: var(--score-potential); }
  .product-mark { width: 44px; height: 44px; display: grid; place-items: center; border: 1px solid var(--border); border-radius: 6px; color: var(--primary); background: var(--muted); }
  .product-mark :global(svg) { width: 20px; height: 20px; }
  .score-copy { min-width: 0; }
  .score-copy p { margin: 0 0 2px; color: var(--muted-foreground); font-size: 12px; }
  .score-copy h2 { margin: 0; font-size: 22px; font-weight: 600; letter-spacing: 0; }
  .score-copy span { display: block; margin-top: 4px; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
  .score-value { min-width: 110px; text-align: right; }
  .score-value strong { display: block; font-size: 42px; line-height: 1; font-weight: 600; font-variant-numeric: tabular-nums; }
  .score-value span { color: var(--muted-foreground); font-size: 10px; }
  .score-meta { min-width: 220px; display: grid; gap: 5px; margin: 0; padding-left: 18px; border-left: 1px solid var(--border); }
  .score-meta div { display: flex; justify-content: space-between; gap: 16px; font-size: 12px; }
  .score-meta dt { color: var(--muted-foreground); }
  .score-meta dd { margin: 0; font-variant-numeric: tabular-nums; }
  .analysis-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 32px; }
  .analysis-grid > section, .windows-section, .hourly-score-section { min-width: 0; border-top: 1px solid var(--border); padding-top: 14px; }
  .subheading { min-height: 32px; display: flex; align-items: center; gap: 8px; }
  .subheading h3 { margin: 0; font-size: 14px; font-weight: 600; }
  .subheading > span { margin-left: auto; color: var(--muted-foreground); font-size: 11px; }
  .component-list { display: grid; gap: 10px; margin: 8px 0 0; }
  .component-list > div { display: grid; grid-template-columns: minmax(116px, .7fr) minmax(140px, 1.3fr); align-items: center; gap: 12px; }
  .component-list dt { display: flex; justify-content: space-between; gap: 12px; color: var(--muted-foreground); font-size: 11px; }
  .component-list dt b { color: var(--foreground); font-weight: 500; font-variant-numeric: tabular-nums; }
  .component-list dd { margin: 0; }
  .component-track { height: 7px; display: block; overflow: hidden; border-radius: 2px; background: var(--muted); }
  .component-track i { height: 100%; display: block; background: var(--primary); }
  .diagnostic-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 8px 0 0; background: var(--border); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .diagnostic-grid div { min-height: 60px; display: grid; align-content: center; gap: 3px; padding: 9px 11px; background: var(--card); }
  .diagnostic-grid dt { color: var(--muted-foreground); font-size: 10px; }
  .diagnostic-grid dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  .window-list { display: grid; margin-top: 8px; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .window-list article { min-height: 52px; display: grid; grid-template-columns: 32px minmax(0, 1fr) 64px 54px; align-items: center; gap: 12px; padding: 7px 12px; border-bottom: 1px solid var(--border); }
  .window-list article:last-child { border-bottom: 0; }
  .window-list article > span { color: var(--muted-foreground); font-size: 10px; }
  .window-list article div { display: grid; gap: 2px; }
  .window-list strong { font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .window-list small { color: var(--muted-foreground); font-size: 10px; }
  .window-list article > b { text-align: right; font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .window-list article > small { text-align: right; }
  .empty-window { margin: 8px 0 0; padding: 16px; border: 1px solid var(--border); border-radius: 6px; color: var(--muted-foreground); font-size: 12px; }
  .hourly-score-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 4px; margin-top: 8px; }
  .hour-score { position: relative; min-width: 0; height: 76px; display: grid; grid-template-rows: auto 1fr; align-items: start; justify-items: center; gap: 6px; padding: 7px 3px 4px; overflow: hidden; border: 1px solid var(--border); border-radius: 4px; background: var(--card); }
  .hour-score time { z-index: 1; color: var(--muted-foreground); font-size: 9px; font-variant-numeric: tabular-nums; }
  .hour-score strong { z-index: 1; align-self: center; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .hour-score i { position: absolute; inset: auto 0 0; min-height: 3px; background: color-mix(in oklch, var(--score-weak) 25%, transparent); }
  .hour-score.low i { background: color-mix(in oklch, var(--score-potential) 28%, transparent); }
  .hour-score.fair i { background: color-mix(in oklch, var(--score-medium) 32%, transparent); }
  .hour-score.good i { background: color-mix(in oklch, var(--score-high) 30%, transparent); }
  .assumption-list { display: grid; gap: 4px; padding: 10px 12px; border-left: 2px solid var(--border); color: var(--muted-foreground); }
  .assumption-list p { margin: 0; font-size: 11px; line-height: 1.5; }

  @media (max-width: 980px) {
    .score-band { grid-template-columns: 44px minmax(180px, 1fr) auto; }
    .score-meta { grid-column: 2 / -1; min-width: 0; padding: 10px 0 0; border-left: 0; border-top: 1px solid var(--border); }
    .analysis-grid { grid-template-columns: 1fr; gap: 24px; }
  }
  @media (max-width: 620px) {
    .score-band { grid-template-columns: 44px minmax(0, 1fr) auto; gap: 10px; }
    .score-copy span { grid-column: 1 / -1; }
    .score-value { min-width: 76px; }
    .score-value strong { font-size: 32px; }
    .score-meta { grid-column: 1 / -1; }
    .component-list > div { grid-template-columns: 116px minmax(0, 1fr); }
    .hourly-score-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); }
    .window-list article { grid-template-columns: 24px minmax(0, 1fr) 48px; }
    .window-list article > small { display: none; }
  }
  @media (max-width: 390px) {
    .diagnostic-grid { grid-template-columns: 1fr; }
    .score-copy h2 { font-size: 19px; }
  }
</style>
