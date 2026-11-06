<script lang="ts">
  import type { OutdoorWeatherForecast, OutdoorWeatherHour } from '$lib/api/types';
  import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
  import Cloud from '@lucide/svelte/icons/cloud';
  import CloudRain from '@lucide/svelte/icons/cloud-rain';
  import Droplets from '@lucide/svelte/icons/droplets';
  import Eye from '@lucide/svelte/icons/eye';
  import Gauge from '@lucide/svelte/icons/gauge';
  import Snowflake from '@lucide/svelte/icons/snowflake';
  import Thermometer from '@lucide/svelte/icons/thermometer';
  import Wind from '@lucide/svelte/icons/wind';

  interface Props {
    data: OutdoorWeatherForecast;
    timeZone: string;
  }

  let { data, timeZone }: Props = $props();

  function number(value: number | null | undefined, digits = 1): string {
    return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits);
  }

  function hour(value: string): string {
    return new Date(value).toLocaleTimeString('zh-CN', {
      timeZone,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }

  function visibility(value: number | null): string {
    if (value == null) return '—';
    return value >= 1000 ? `${(value / 1000).toFixed(1)} km` : `${Math.round(value)} m`;
  }

  function windDirection(value: number | null): string {
    if (value == null) return '—';
    const labels = ['北', '东北', '东', '东南', '南', '西南', '西', '西北'];
    return labels[Math.round(((value % 360) + 360) % 360 / 45) % 8];
  }

  function alertLabel(type: string): string {
    return ({
      heavy_precipitation: '强降水',
      thunderstorm: '雷暴',
      strong_wind: '强风',
      heat: '高温',
      low_visibility: '低能见度',
      snow: '降雪',
    } as Record<string, string>)[type] ?? type;
  }

  function metrics() {
    return [
      {
        label: '气温',
        value: `${number(data.daily.temperature_min_c)}～${number(data.daily.temperature_max_c)}℃`,
        detail: `体感 ${number(data.daily.apparent_temperature_min_c)}～${number(data.daily.apparent_temperature_max_c)}℃`,
        icon: Thermometer,
      },
      {
        label: '降水',
        value: `${number(data.daily.precipitation_total_mm)} mm`,
        detail: `最高概率 ${number(data.daily.precipitation_probability_max_percent, 0)}%`,
        icon: CloudRain,
      },
      {
        label: '风',
        value: `${number(data.daily.wind_speed_max_ms)} m/s`,
        detail: `阵风 ${number(data.daily.wind_gust_max_ms)} m/s`,
        icon: Wind,
      },
      {
        label: '能见度',
        value: visibility(data.daily.visibility_min_m),
        detail: '全天最低',
        icon: Eye,
      },
      {
        label: '平均云量',
        value: `${number(data.daily.cloud_cover_mean_percent, 0)}%`,
        detail: data.daily.condition,
        icon: Cloud,
      },
      {
        label: '可信度',
        value: `${Math.round(data.confidence * 100)}%`,
        detail: '逐小时数据完整度',
        icon: Gauge,
      },
    ];
  }

  function precipitation(hourly: OutdoorWeatherHour): string {
    const amount = hourly.precipitation_mm ?? 0;
    const probability = hourly.precipitation_probability_percent;
    return `${number(amount, 1)} mm${probability == null ? '' : ` · ${Math.round(probability)}%`}`;
  }
</script>

<section class="weather-view" aria-labelledby="weather-heading">
  <div class="section-heading">
    <div>
      <p>天气概况</p>
      <h2 id="weather-heading">{data.daily.condition}</h2>
    </div>
    <span>{data.summary}</span>
  </div>

  <div class="metric-grid">
    {#each metrics() as metric}
      {@const Icon = metric.icon}
      <article class="metric-item">
        <Icon class="metric-icon" aria-hidden="true" />
        <div>
          <span>{metric.label}</span>
          <strong>{metric.value}</strong>
          <small>{metric.detail}</small>
        </div>
      </article>
    {/each}
  </div>

  {#if data.alerts.length}
    <section class="alerts" aria-labelledby="alerts-heading">
      <div class="subheading">
        <AlertTriangle class="h-4 w-4" aria-hidden="true" />
        <h3 id="alerts-heading">风险信号</h3>
        <span>{data.alerts.length}</span>
      </div>
      <div class="alert-list">
        {#each data.alerts as alert}
          <div class:high={alert.severity === 'high'} class="alert-row">
            <strong>{alertLabel(alert.type)}</strong>
            <span>{alert.message}</span>
            <time>{alert.start_time_local ? hour(alert.start_time_local) : '—'}</time>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  <section class="hourly-section" aria-labelledby="hourly-weather-heading">
    <div class="subheading">
      <Droplets class="h-4 w-4" aria-hidden="true" />
      <h3 id="hourly-weather-heading">逐小时天气</h3>
      <span>{data.hourly.length} 时次</span>
    </div>

    <div class="hourly-table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">时间</th>
            <th scope="col">天气</th>
            <th scope="col">气温 / 体感</th>
            <th scope="col">降水</th>
            <th scope="col">云量 低·中·高</th>
            <th scope="col">风</th>
            <th scope="col">能见度</th>
          </tr>
        </thead>
        <tbody>
          {#each data.hourly as item}
            <tr>
              <td class="mono">{hour(item.valid_time_local)}</td>
              <td>{item.condition}</td>
              <td class="mono">{number(item.temperature_c)}℃ / {number(item.apparent_temperature_c)}℃</td>
              <td class="mono">{precipitation(item)}</td>
              <td class="mono">{number(item.cloud_cover_percent, 0)}% · {number(item.cloud_cover_low_percent, 0)} · {number(item.cloud_cover_mid_percent, 0)} · {number(item.cloud_cover_high_percent, 0)}</td>
              <td class="mono">{windDirection(item.wind_direction_deg)} {number(item.wind_speed_ms)} m/s</td>
              <td class="mono">{visibility(item.visibility_m)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <div class="mobile-hour-list">
      {#each data.hourly as item}
        <article class="mobile-hour">
          <div class="hour-main">
            <time>{hour(item.valid_time_local)}</time>
            <strong>{item.condition}</strong>
            <b>{number(item.temperature_c)}℃</b>
          </div>
          <dl>
            <div><dt>降水</dt><dd>{precipitation(item)}</dd></div>
            <div><dt>云量</dt><dd>{number(item.cloud_cover_percent, 0)}%</dd></div>
            <div><dt>风</dt><dd>{number(item.wind_speed_ms)} m/s</dd></div>
            <div><dt>能见度</dt><dd>{visibility(item.visibility_m)}</dd></div>
          </dl>
        </article>
      {/each}
    </div>
  </section>

  {#if (data.daily.snowfall_total_cm ?? 0) > 0}
    <div class="snow-note">
      <Snowflake class="h-4 w-4" aria-hidden="true" />
      累计降雪 {number(data.daily.snowfall_total_cm)} cm
    </div>
  {/if}
</section>

<style>
  .weather-view { display: grid; gap: 24px; }
  .section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; }
  .section-heading p { margin: 0 0 3px; color: var(--muted-foreground); font-size: 12px; }
  .section-heading h2 { margin: 0; font-size: 22px; font-weight: 600; letter-spacing: 0; }
  .section-heading > span { max-width: 640px; color: var(--muted-foreground); font-size: 13px; line-height: 1.6; text-align: right; }
  .metric-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
  .metric-item { min-width: 0; min-height: 112px; display: flex; align-items: flex-start; gap: 10px; padding: 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); }
  .metric-item :global(.metric-icon) { width: 16px; height: 16px; flex: none; margin-top: 2px; color: var(--primary); }
  .metric-item div { min-width: 0; display: grid; gap: 3px; }
  .metric-item span, .metric-item small { color: var(--muted-foreground); font-size: 11px; line-height: 1.35; }
  .metric-item strong { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: normal; overflow-wrap: anywhere; }
  .alerts, .hourly-section { border-top: 1px solid var(--border); padding-top: 16px; }
  .subheading { min-height: 32px; display: flex; align-items: center; gap: 8px; }
  .subheading h3 { margin: 0; font-size: 14px; font-weight: 600; }
  .subheading > span { margin-left: auto; color: var(--muted-foreground); font-size: 11px; }
  .alert-list { display: grid; margin-top: 8px; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .alert-row { min-height: 44px; display: grid; grid-template-columns: 96px minmax(0, 1fr) 56px; align-items: center; gap: 12px; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }
  .alert-row:last-child { border-bottom: 0; }
  .alert-row strong { color: var(--score-medium); font-weight: 600; }
  .alert-row.high strong { color: var(--destructive); }
  .alert-row time { color: var(--muted-foreground); text-align: right; font-variant-numeric: tabular-nums; }
  .hourly-table-wrap { margin-top: 8px; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 12px; }
  th { padding: 9px 10px; background: var(--muted); color: var(--muted-foreground); font-size: 11px; font-weight: 500; text-align: left; }
  th:nth-child(1) { width: 7%; }
  th:nth-child(2) { width: 11%; }
  th:nth-child(3) { width: 15%; }
  th:nth-child(4) { width: 13%; }
  th:nth-child(5) { width: 25%; }
  th:nth-child(6) { width: 15%; }
  th:nth-child(7) { width: 14%; }
  td { padding: 9px 10px; border-top: 1px solid var(--border); color: var(--foreground); overflow-wrap: anywhere; }
  tbody tr:hover { background: color-mix(in oklch, var(--muted) 55%, transparent); }
  .mono { font-variant-numeric: tabular-nums; }
  .mobile-hour-list { display: none; }
  .snow-note { min-height: 40px; display: flex; align-items: center; gap: 8px; padding: 0 12px; border: 1px solid var(--border); border-radius: 6px; color: var(--score-potential); font-size: 12px; }

  @media (max-width: 1180px) {
    .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  }
  @media (max-width: 760px) {
    .weather-view { gap: 20px; }
    .section-heading { display: grid; gap: 8px; }
    .section-heading > span { text-align: left; }
    .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .metric-item { min-height: 104px; padding: 12px; }
    .hourly-table-wrap { display: none; }
    .mobile-hour-list { display: grid; margin-top: 8px; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
    .mobile-hour { padding: 12px; border-bottom: 1px solid var(--border); }
    .mobile-hour:last-child { border-bottom: 0; }
    .hour-main { display: grid; grid-template-columns: 52px minmax(0, 1fr) auto; align-items: baseline; gap: 8px; }
    .hour-main time, .hour-main b { font-variant-numeric: tabular-nums; }
    .hour-main strong { font-size: 13px; font-weight: 600; }
    .hour-main b { font-size: 15px; font-weight: 600; }
    .mobile-hour dl { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; margin: 10px 0 0 60px; }
    .mobile-hour dl div { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; }
    .mobile-hour dt { color: var(--muted-foreground); }
    .mobile-hour dd { margin: 0; font-variant-numeric: tabular-nums; }
    .alert-row { grid-template-columns: 82px minmax(0, 1fr); }
    .alert-row time { display: none; }
  }
  @media (max-width: 420px) {
    .metric-grid { grid-template-columns: 1fr; }
    .metric-item { min-height: 80px; }
  }
</style>
