<script lang="ts">
  import type { NationalCrossSectionPayload } from '$lib/api/types';
  import LoaderCircle from '@lucide/svelte/icons/loader-circle';

  export let section: NationalCrossSectionPayload | null = null;
  export let loading = false;
  export let error = '';

  const width = 960;
  const height = 360;
  const left = 58;
  const right = 944;
  const top = 22;
  const bottom = 318;
  const maxHeight = 18_000;
  const phaseLabels: Record<string, string> = {
    unknown: '未知',
    ice: '冰相',
    water: '水相',
    mixed: '混合相',
  };

  function x(distance: number): number {
    const maxDistance = section?.distances_km.at(-1) ?? section?.analysis_radius_km ?? 1;
    return left + (distance / Math.max(1, maxDistance)) * (right - left);
  }

  function y(value: number): number {
    return bottom - (Math.max(0, Math.min(maxHeight, value)) / maxHeight) * (bottom - top);
  }

  function isFiniteHeight(value: number | null | undefined): value is number {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function cloudFill(
    amount: number | null,
    presence: number | null,
    rh: number | null,
    fraction: number | null,
    pressureFamilyCode: number,
  ): string {
    const cloudAmount = amount != null
      ? Math.max(0, Number(amount))
      : Math.max(Number(presence ?? 0), Number(fraction ?? 0) / 100);
    const humidityAmount = Math.max(0, Number(rh ?? 0) - 55) / 45;
    if (cloudAmount < 0.18) {
      if (fraction != null && Number(fraction) <= 0.0) return 'transparent';
      if (humidityAmount < 0.06) return 'transparent';
      return `rgba(112, 151, 126, ${Math.min(0.34, 0.04 + humidityAmount * 0.26)})`;
    }
    const alpha = Math.min(0.82, 0.10 + cloudAmount * 0.68);
    if (pressureFamilyCode === 1) return `rgba(116, 157, 194, ${alpha})`;
    if (pressureFamilyCode === 2) return `rgba(105, 154, 142, ${alpha})`;
    return `rgba(126, 136, 145, ${alpha})`;
  }

  function levelPath(levelIndex: number): string {
    if (!section) return '';
    const commands: string[] = [];
    let connected = false;
    section.distances_km.forEach((distance, distanceIndex) => {
      const altitude = section!.fields.geopotential_height_asl_m[distanceIndex][levelIndex];
      if (!isFiniteHeight(altitude)) {
        connected = false;
        return;
      }
      commands.push(`${connected ? 'L' : 'M'} ${x(distance).toFixed(1)} ${y(altitude).toFixed(1)}`);
      connected = true;
    });
    return commands.join(' ');
  }

  function pressureLabelPoint(levelIndex: number): { distance: number; altitude: number } | null {
    if (!section) return null;
    for (let distanceIndex = section.distances_km.length - 1; distanceIndex >= 0; distanceIndex -= 1) {
      const altitude = section.fields.geopotential_height_asl_m[distanceIndex][levelIndex];
      if (isFiniteHeight(altitude)) {
        return { distance: section.distances_km[distanceIndex], altitude };
      }
    }
    return null;
  }

  function shadowPath(): string {
    if (!section) return '';
    return section.distances_km.map((distance, index) =>
      `${index === 0 ? 'M' : 'L'} ${x(distance).toFixed(1)} ${y(section!.required_illumination_base_asl_m[index]).toFixed(1)}`,
    ).join(' ');
  }

  function terrainHeight(distanceIndex: number): number {
    return section!.terrain_elevation_asl_m[distanceIndex];
  }

  function terrainFillPath(): string {
    if (!section || !section.distances_km.length) return '';
    const topPath = section.distances_km.map((distance, index) =>
      `${index === 0 ? 'M' : 'L'} ${x(distance).toFixed(1)} ${y(terrainHeight(index)).toFixed(1)}`,
    ).join(' ');
    return `${topPath} L ${x(section.distances_km.at(-1) ?? 0).toFixed(1)} ${bottom} L ${left} ${bottom} Z`;
  }

  function terrainPath(): string {
    if (!section) return '';
    return section.distances_km.map((distance, index) =>
      `${index === 0 ? 'M' : 'L'} ${x(distance).toFixed(1)} ${y(terrainHeight(index)).toFixed(1)}`,
    ).join(' ');
  }

  function temperatureColor(levelIndex: number): string {
    if (!section) return '#ad624f';
    const values = section.fields.temperature_c
      .map((row) => row[levelIndex])
      .filter((value): value is number => value !== null);
    const average = values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
    return average < -30 ? '#8b6574' : average < 0 ? '#b06b5c' : '#c64f37';
  }

  function layerStatus(distanceIndex: number, levelIndex: number): number {
    return Number(section!.fields.cloud_layer_status[distanceIndex][levelIndex]);
  }

  function statusStroke(status: number): string {
    if (status === 3) return '#ef6c35';
    if (status === 2) return '#d38b39';
    return 'none';
  }

  function statusDash(status: number): string | undefined {
    return status === 2 ? '4 3' : undefined;
  }

  $: modelLabel = section?.model_source === 'ec' ? 'ECMWF IFS' : 'GFS';
  $: satelliteSection = section?.spatial_sampling === 'satellite-along-path-retrievals';
  $: sectionLabel = satelliteSection ? '卫星实况剖面（校准卫星 + 同周期 GFS 先验）' : modelLabel;
  $: terrainLabel = section?.terrain_source ?? `${modelLabel} 地形海拔`;
</script>

<div class="section-shell">
  {#if loading}
    <div class="state"><LoaderCircle class="h-5 w-5 animate-spin" /><span>抽取压力层剖面...</span></div>
  {:else if error}
    <div class="state error">{error}</div>
  {:else if section}
    <header>
      <div>
        <strong>太阳方向大气剖面 · {sectionLabel}</strong>
        <span>{section.valid_time_local.replace('T', ' ').slice(0, 16)}</span>
      </div>
      <dl>
        <div><dt>方位</dt><dd>{section.solar_azimuth_deg.toFixed(1)}°</dd></div>
        <div><dt>太阳高度</dt><dd>{section.solar_elevation_deg.toFixed(1)}°</dd></div>
        <div><dt>剖面范围</dt><dd>{section.analysis_radius_km.toFixed(0)} km</dd></div>
        <div><dt>相对时刻</dt><dd>{section.offset_minutes > 0 ? '+' : ''}{section.offset_minutes} 分</dd></div>
      </dl>
    </header>

    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="沿太阳方向的温湿度和云层垂直剖面">
      <rect x={left} y={top} width={right - left} height={bottom - top} class="plot-bg" />
      {#each [0, 3000, 6000, 9000, 12000, 15000, 18000] as altitude}
        <line x1={left} x2={right} y1={y(altitude)} y2={y(altitude)} class="grid-line" />
        <text x={left - 8} y={y(altitude) + 4} text-anchor="end" class="axis-label">{altitude / 1000}km</text>
      {/each}
      {#each section.distances_km.filter((_, index) => index % 4 === 0) as distance}
        <line x1={x(distance)} x2={x(distance)} y1={top} y2={bottom} class="grid-line vertical" />
        <text x={x(distance)} y={bottom + 20} text-anchor="middle" class="axis-label">{distance}km</text>
      {/each}

      <path d={terrainFillPath()} class="terrain-fill" />

      {#each section.distances_km as distance, distanceIndex}
        {#each section.pressure_levels_hpa as _pressure, levelIndex}
          {@const altitude = section.fields.geopotential_height_asl_m[distanceIndex][levelIndex]}
          {#if isFiniteHeight(altitude)}
            {@const pressureFamilyCode = section.fields.pressure_family_code[distanceIndex][levelIndex]}
            {@const presence = section.fields.cloud_presence[distanceIndex][levelIndex]}
            {@const amount = section.fields.cloud_amount[distanceIndex][levelIndex]}
            {@const humidity = section.fields.rh_percent[distanceIndex][levelIndex]}
            {@const fraction = section.fields.cloud_fraction_percent[distanceIndex][levelIndex]}
            <rect
              x={x(distance) - 18}
              y={y(altitude + 700)}
              width="36"
              height={Math.max(5, y(altitude - 700) - y(altitude + 700))}
              fill={cloudFill(amount, presence, humidity, fraction, pressureFamilyCode)}
              stroke={statusStroke(layerStatus(distanceIndex, levelIndex))}
              stroke-width={layerStatus(distanceIndex, levelIndex) === 3 ? 2.4 : 1.6}
              stroke-dasharray={statusDash(layerStatus(distanceIndex, levelIndex))}
            />
          {/if}
        {/each}
      {/each}

      {#each section.pressure_levels_hpa as pressure, levelIndex}
        <path d={levelPath(levelIndex)} fill="none" stroke={temperatureColor(levelIndex)} stroke-width="1.1" opacity="0.62" />
        {@const labelPoint = pressureLabelPoint(levelIndex)}
        {#if levelIndex % 2 === 0 && labelPoint}
          <text x={x(labelPoint.distance) - 5} y={y(labelPoint.altitude) - 3} text-anchor="end" class="pressure-label">{pressure}hPa</text>
        {/if}
      {/each}

      <path d={shadowPath()} fill="none" class="shadow-line" />
      <path d={terrainPath()} fill="none" class="terrain-line" />
      <circle cx={left} cy={y(terrainHeight(0))} r="6" class="observer" />
      <text x={left + 12} y={y(terrainHeight(0)) - 10} class="annotation">观测点</text>
      <text x={left + 155} y={top + 20} class="annotation warm">黄色虚线：云底理论受光阈值（ASL）</text>
    </svg>

    <footer>
      <span><i class="cloud high"></i>高云压力族</span>
      <span><i class="cloud mid"></i>中云压力族</span>
      <span><i class="cloud low"></i>低云压力族</span>
      <span><i class="status burnable"></i>橙色实线：受光且几何/地形可见候选（未评估云遮挡）</span>
      <span><i class="status lit"></i>橙色虚线：达到受光阈值但几何/地形不可见</span>
      <span><i class="humidity"></i>湿润未饱和</span>
      <span><i class="temperature"></i>温度层线</span>
      <span><i class="terrain"></i>{terrainLabel}</span>
      <span>低云压力族仅在近场距离门限内排除；橙色状态未评估云体遮挡。</span>
    </footer>
    {#if section.theoretical_targets.length}
      <div class="target-strip">
        <strong>受光与几何/地形可见候选（未评估云遮挡）</strong>
        {#each section.theoretical_targets.filter((target) => target.status === 'burnable').slice(0, 8) as target}
          <span class="target-chip" title={target.status_reason}>
            {target.pressure_family_label} · {phaseLabels[target.cloud_phase.label] ?? '未知'} · {target.distance_km.toFixed(0)} km ·
            ASL {(target.cloud_base_asl_m / 1000).toFixed(1)}–{(target.cloud_top_asl_m / 1000).toFixed(1)} km ·
            AGL {(target.cloud_base_agl_m / 1000).toFixed(1)}–{(target.cloud_top_agl_m / 1000).toFixed(1)} km
          </span>
        {:else}
          {@const diagnostic = section.theoretical_targets.find((target) => target.status !== 'burnable')}
          <span class="target-empty">
            当前太阳方向没有可烧目标云{diagnostic ? `：${diagnostic.pressure_family_label}，${diagnostic.status_reason}` : ''}
          </span>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="state">选择地图格点查看当前时刻剖面</div>
  {/if}
</div>

<style>
  .section-shell { height: 100%; min-height: 0; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; color: var(--foreground); }
  header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 14px 6px; }
  header > div { display: grid; gap: 2px; }
  header strong { font-size: 14px; font-weight: 600; }
  header span { color: var(--muted-foreground); font-size: 11px; }
  dl { display: flex; gap: 18px; margin: 0; }
  dl div { display: grid; gap: 1px; }
  dt { color: var(--muted-foreground); font-size: 10px; }
  dd { margin: 0; font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; }
  svg { width: 100%; height: 100%; min-height: 210px; }
  .plot-bg { fill: color-mix(in srgb, var(--background) 93%, #d8e0e5); }
  .grid-line { stroke: color-mix(in srgb, var(--border) 78%, transparent); stroke-width: 1; }
  .grid-line.vertical { stroke-dasharray: 3 4; }
  .axis-label, .pressure-label, .annotation { fill: var(--muted-foreground); font-size: 10px; }
  .pressure-label { font-size: 9px; }
  .annotation.warm { fill: #b97836; }
  .shadow-line { stroke: #d38b39; stroke-width: 2.2; stroke-dasharray: 7 5; }
  .terrain-fill { fill: color-mix(in srgb, #68736f 17%, transparent); }
  .terrain-line { stroke: #68736f; stroke-width: 1.8; }
  .observer { fill: #f37d42; stroke: var(--background); stroke-width: 2; }
  footer { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 2px 14px 10px; color: var(--muted-foreground); font-size: 10px; }
  footer span { display: inline-flex; align-items: center; gap: 4px; }
  footer i { width: 15px; height: 7px; display: inline-block; }
  footer i.status { height: 9px; background: transparent; border: 2px solid #ef6c35; }
  footer i.status.lit { border-style: dashed; border-color: #d38b39; }
  .cloud.high { background: rgba(116, 157, 194, .75); }
  .cloud.mid { background: rgba(105, 154, 142, .75); }
  .cloud.low { background: rgba(126, 136, 145, .75); }
  .humidity { background: rgba(112, 151, 126, .42); }
  .temperature { border-top: 2px solid #b55f4c; }
  .terrain { border-top: 2px solid #68736f; }
  .target-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px; padding: 0 14px 10px; font-size: 10px; color: var(--muted-foreground); }
  .target-strip strong { color: var(--foreground); font-size: 11px; }
  .target-chip { border: 1px solid color-mix(in srgb, #ef6c35 42%, var(--border)); background: color-mix(in srgb, #ef6c35 8%, transparent); color: var(--foreground); padding: 2px 6px; }
  .target-empty { color: var(--muted-foreground); }
  .state { height: 100%; min-height: 220px; display: flex; align-items: center; justify-content: center; gap: 8px; color: var(--muted-foreground); font-size: 12px; }
  .state.error { color: var(--destructive); padding: 24px; text-align: center; }
  @media (max-width: 760px) { dl { display: none; } header { padding-inline: 10px; } }
</style>
