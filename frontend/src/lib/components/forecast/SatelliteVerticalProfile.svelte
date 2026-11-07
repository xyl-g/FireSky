<script lang="ts">
  import type {
    SatelliteVerticalProfile,
    SatelliteVerticalProfileLevel,
    SatelliteVerticalProfileSample,
  } from '$lib/api/types';

  export let profile: SatelliteVerticalProfile | null = null;

  const width = 960;
  const height = 360;
  const left = 58;
  const right = 944;
  const top = 24;
  const bottom = 316;

  $: maxDistance = Math.max(100, ...(profile?.samples.map((sample) => sample.distance_km) ?? [100]));
  $: maxHeight = Math.max(
    10_000,
    ...(profile?.samples.flatMap((sample) => sample.levels.map((level) => level.height_m ?? 0)) ?? [10_000]),
  );
  $: pressureHeights = (profile?.pressure_levels_hpa ?? []).map((pressure, index) => {
    const heights = profile?.samples
      .flatMap((sample) => sample.levels)
      .filter((level) => level.pressure_hpa === pressure && level.height_m != null)
      .map((level) => level.height_m as number) ?? [];
    return heights[0] ?? maxHeight * (0.08 + (index / Math.max(1, (profile?.pressure_levels_hpa.length ?? 1) - 1)) * 0.84);
  });

  function x(distance: number): number {
    return left + (distance / maxDistance) * (right - left);
  }

  function y(heightM: number): number {
    return bottom - (Math.max(0, Math.min(maxHeight, heightM)) / maxHeight) * (bottom - top);
  }

  function levelColor(level: SatelliteVerticalProfileLevel | null): string {
    if (!level || level.status === 'unknown') return 'rgba(183, 121, 31, 0.05)';
    const posterior = level.hsd_posterior_percent ?? level.cloud_probability_percent;
    if (posterior == null) return 'rgba(183, 121, 31, 0.05)';
    const probability = Math.max(0, Math.min(100, posterior));
    return `rgba(86, 143, 155, ${0.12 + probability / 150})`;
  }

  function levelFor(sample: SatelliteVerticalProfileSample, pressure: number): SatelliteVerticalProfileLevel | null {
    return sample.levels.find((level) => level.pressure_hpa === pressure) ?? null;
  }

  function rowBounds(levelIndex: number): { y: number; height: number } {
    const current = pressureHeights[levelIndex] ?? 0;
    const above = levelIndex === 0 ? 0 : (pressureHeights[levelIndex - 1] + current) / 2;
    const below = levelIndex === pressureHeights.length - 1
      ? maxHeight
      : (current + pressureHeights[levelIndex + 1]) / 2;
    return { y: y(below), height: Math.max(4, y(above) - y(below)) };
  }

  function sampleCellX(index: number): number {
    const samples = profile?.samples ?? [];
    const center = x(samples[index]?.distance_km ?? 0);
    const previous = index > 0 ? x(samples[index - 1].distance_km) : left;
    const next = index + 1 < samples.length ? x(samples[index + 1].distance_km) : right;
    return index === 0 ? left : (previous + center) / 2;
  }

  function sampleCellWidth(index: number): number {
    const samples = profile?.samples ?? [];
    const center = x(samples[index]?.distance_km ?? 0);
    const next = index + 1 < samples.length ? x(samples[index + 1].distance_km) : right;
    return Math.max(8, (index + 1 === samples.length ? right : (center + next) / 2) - sampleCellX(index));
  }

  function modelFractionText(level: SatelliteVerticalProfileLevel | null): string {
    if (!level || level.model_cloud_fraction == null) return 'unknown';
    return `${Math.round(level.model_cloud_fraction * 100)}%`;
  }

  function posteriorText(level: SatelliteVerticalProfileLevel | null): string {
    if (!level) return 'unknown';
    const posterior = level.hsd_posterior_percent ?? level.cloud_probability_percent;
    return posterior == null ? 'unknown' : `${Math.round(posterior)}%`;
  }

  function statusLabel(status: string): string {
    if (status === 'available') return '完整覆盖';
    if (status === 'partial_unknown') return '部分未知';
    return '不可用';
  }

  $: isHcai = profile?.bands?.includes('CLOUD_MASK') ?? false;
  $: sourceLabel = isHcai ? 'HCAI CMSK/CPHS/CHGT' : 'HSD B07/B13/B15';
</script>

<section class="profile-shell">
  {#if !profile || profile.status === 'unavailable'}
    <div class="state">实况大气剖面不可用：校准卫星云柱或同周期 GFS vertical prior 不完整</div>
  {:else}
    <header>
      <div>
        <strong>太阳方向实况大气剖面 · 卫星代理</strong>
        <span>{profile.valid_time_utc.replace('T', ' ').slice(0, 16)} · {statusLabel(profile.status)}</span>
      </div>
      <dl>
        <div><dt>采样点</dt><dd>{profile.usable_sample_count}/{profile.sample_count}</dd></div>
        <div><dt>覆盖率</dt><dd>{Math.round(profile.coverage * 100)}%</dd></div>
        <div><dt>压力层</dt><dd>{profile.pressure_levels_hpa.length}</dd></div>
      </dl>
    </header>

    <div class="plot-scroll">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="沿太阳方向的卫星代理压力层剖面">
      <rect x={left} y={top} width={right - left} height={bottom - top} class="plot-bg" />
      {#each [0, 3000, 6000, 9000, 12000, 15000] as altitude}
        {#if altitude <= maxHeight}
          <line x1={left} x2={right} y1={y(altitude)} y2={y(altitude)} class="grid-line" />
          <text x={left - 8} y={y(altitude) + 4} text-anchor="end" class="axis-label">{altitude / 1000}km</text>
        {/if}
      {/each}
      {#each profile.pressure_levels_hpa as pressure, levelIndex}
        {@const bounds = rowBounds(levelIndex)}
        <line x1={left} x2={right} y1={bounds.y} y2={bounds.y} class="grid-line" />
        <text x={left - 8} y={bounds.y + bounds.height / 2 + 3} text-anchor="end" class="pressure-label">{pressure}hPa</text>
      {/each}
      {#each profile.samples as sample, sampleIndex}
        <line x1={x(sample.distance_km)} x2={x(sample.distance_km)} y1={top} y2={bottom} class="sample-line" />
        {#each profile.pressure_levels_hpa as pressure, levelIndex}
          {@const level = levelFor(sample, pressure)}
          {@const bounds = rowBounds(levelIndex)}
          <rect
            x={sampleCellX(sampleIndex)}
            y={bounds.y + 1}
            width={sampleCellWidth(sampleIndex)}
            height={Math.max(2, bounds.height - 2)}
            fill={levelColor(level)}
            class:unknown-cell={!level || level.status === 'unknown'}
          >
            <title>{pressure} hPa · GFS CF {modelFractionText(level)} · T {level?.temperature_c == null ? 'unknown' : `${level.temperature_c.toFixed(1)}°C`} · RH {level?.rh_percent == null ? 'unknown' : `${Math.round(level.rh_percent)}%`} · {sourceLabel} posterior {posteriorText(level)}</title>
          </rect>
        {/each}
        {#if sample.status === 'unknown'}
          <path d={`M ${x(sample.distance_km) - 5} ${top + 8} l 10 10 m -10 0 l 10 -10`} class="unknown-mark" />
        {/if}
        <text x={x(sample.distance_km)} y={bottom + 20} text-anchor="middle" class="axis-label">{Math.round(sample.distance_km)}km</text>
      {/each}
    </svg>
    </div>

    <footer>
      {#if isHcai}
        <span><i class="retrieved"></i>HCAI 官方云掩膜、云相、云顶高度和 COT 产品</span>
      {:else}
        <span><i class="retrieved"></i>HSD B07/B13/B15 云概率代理（posterior）</span>
        <span>HSD 分支不发布 COT、云相或官方云高真值</span>
      {/if}
      <span><i class="unknown"></i>{sourceLabel} 缺失/未知，不解释为晴空</span>
      <span>连续网格坐标与 model cloud fraction 来自同周期 GFS vertical prior</span>
      <span>GFS vertical prior 是模式温湿度/风场先验，不是观测温湿风廓线或云底真值</span>
    </footer>
  {/if}
</section>

<style>
  .profile-shell { border: 1px solid color-mix(in srgb, #568f9b 25%, var(--border)); background: color-mix(in srgb, #568f9b 4%, var(--card)); color: var(--foreground); overflow: hidden; }
  .state { min-height: 180px; display: grid; place-items: center; padding: 24px; color: var(--muted-foreground); font-size: 12px; text-align: center; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 14px 6px; }
  header > div { display: grid; gap: 2px; }
  header strong { font-size: 14px; font-weight: 600; }
  header span, dt { color: var(--muted-foreground); font-size: 10px; }
  dl { display: flex; gap: 18px; margin: 0; }
  dl div { display: grid; gap: 1px; }
  dd { margin: 0; font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; }
  .plot-scroll { overflow-x: auto; }
  svg { display: block; width: 100%; min-width: 760px; min-height: 280px; padding: 4px 10px 0; }
  .plot-bg { fill: color-mix(in srgb, var(--background) 94%, #d8e0e5); }
  .grid-line { stroke: color-mix(in srgb, var(--border) 78%, transparent); stroke-width: 1; }
  .sample-line { stroke: color-mix(in srgb, var(--border) 45%, transparent); stroke-dasharray: 3 5; }
  .axis-label, .pressure-label { fill: var(--muted-foreground); font-size: 10px; }
  .pressure-label { fill: #568f9b; }
  .unknown-mark { stroke: #b7791f; stroke-width: 1.5; }
  .unknown-cell { stroke: #b7791f; stroke-width: .7; stroke-dasharray: 3 2; }
  footer { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 2px 14px 10px; color: var(--muted-foreground); font-size: 10px; }
  footer span { display: inline-flex; align-items: center; gap: 4px; }
  footer i { display: inline-block; width: 15px; height: 7px; }
  footer i.retrieved { background: rgba(86, 143, 155, .65); }
  footer i.unknown { border: 1px dashed #b7791f; }
  @media (max-width: 760px) { header { padding-inline: 10px; } dl { gap: 8px; } svg { padding-inline: 2px; } footer { padding-inline: 10px; } }
</style>
