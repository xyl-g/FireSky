<script lang="ts">
  import type { SolarPathProfileSample } from '$lib/api/types';

  export let profile: SolarPathProfileSample[] = [];

  const width = 920;
  const height = 300;
  const left = 54;
  const right = 900;
  const top = 24;
  const bottom = 254;
  const maxHeight = 12_000;

  $: maxDistance = Math.max(100, ...profile.map((sample) => sample.distance_km));

  function x(distance: number): number {
    return left + (distance / maxDistance) * (right - left);
  }

  function y(heightM: number): number {
    return bottom - (Math.max(0, Math.min(maxHeight, heightM)) / maxHeight) * (bottom - top);
  }

  function cloudFill(sample: SolarPathProfileSample): string {
    if (sample.cloud_top_m <= 0) return 'transparent';
    const alpha = Math.min(0.82, 0.16 + sample.blocker_risk * 0.62);
    if (sample.high_presence >= Math.max(sample.mid_presence, sample.low_presence)) {
      return `rgba(116, 157, 194, ${alpha})`;
    }
    if (sample.mid_presence >= sample.low_presence) {
      return `rgba(105, 154, 142, ${alpha})`;
    }
    return `rgba(126, 136, 145, ${alpha})`;
  }

  function statusLabel(status: string): string {
    if (status === 'sunlit_cloud') return '受光';
    if (status === 'ray_intersects_cloud') return '光线穿云';
    if (status === 'below_illumination_threshold') return '低于受光阈值';
    return '晴空';
  }
</script>

<section class="profile-shell">
  <header>
    <div>
      <strong>太阳方向实况云柱剖面</strong>
      <span>卫星云层代理 · 沿太阳方位采样</span>
    </div>
    <span>{profile.length} 个距离点</span>
  </header>

  <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="沿太阳方向的实况云柱剖面">
    <rect x={left} y={top} width={right - left} height={bottom - top} class="plot-bg" />
    {#each [0, 3000, 6000, 9000, 12000] as altitude}
      <line x1={left} x2={right} y1={y(altitude)} y2={y(altitude)} class="grid-line" />
      <text x={left - 8} y={y(altitude) + 4} text-anchor="end" class="axis-label">{altitude / 1000}km</text>
    {/each}

    {#each profile as sample}
      <line
        x1={x(sample.distance_km)}
        x2={x(sample.distance_km)}
        y1={y(sample.required_illumination_height_m)}
        y2={bottom}
        class="sun-ray"
      />
      {#if sample.cloud_top_m > 0}
        <rect
          x={x(sample.distance_km) - 15}
          y={y(sample.cloud_top_m)}
          width="30"
          height={Math.max(5, y(sample.cloud_base_m) - y(sample.cloud_top_m))}
          fill={cloudFill(sample)}
          class="cloud-column"
        />
      {/if}
      {#each sample.levels as level}
        {@const fraction = Math.max(0, Math.min(1, Number(level.cloud_fraction_percent ?? 0) / 100))}
        {#if fraction > 0}
          <circle cx={x(sample.distance_km)} cy={y(level.height_m)} r={2 + fraction * 3} fill={cloudFill(sample)} opacity={0.55 + fraction * 0.35} />
        {/if}
      {/each}
      <circle cx={x(sample.distance_km)} cy={y(0)} r="2.5" class="sample-point" />
      <text x={x(sample.distance_km)} y={bottom + 18} text-anchor="middle" class="axis-label">{Math.round(sample.distance_km)}km</text>
    {/each}
  </svg>

  <div class="profile-summary">
    {#each profile as sample}
      <span title={`${statusLabel(sample.illumination_status)} · 光路挡光 ${Math.round(sample.blocker_risk * 100)}/100`}>
        {Math.round(sample.distance_km)}km · {statusLabel(sample.illumination_status)} · 挡光 {Math.round(sample.blocker_risk * 100)}
      </span>
    {/each}
  </div>

  <footer>
    <span><i class="cloud"></i>云柱代理</span>
    <span><i class="sun-ray-key"></i>太阳入射阈值</span>
    <span><i class="pressure"></i>压力层云量代理</span>
    <span>剖面用于实况挡光判断，不等同于完整探空</span>
  </footer>
</section>

<style>
  .profile-shell { border: 1px solid color-mix(in srgb, #3b82f6 20%, var(--border)); background: color-mix(in srgb, #3b82f6 4%, var(--card)); color: var(--foreground); overflow: hidden; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px 4px; font-size: 11px; color: var(--muted-foreground); }
  header > div { display: grid; gap: 2px; }
  header strong { color: var(--foreground); font-size: 13px; font-weight: 600; }
  svg { display: block; width: 100%; min-height: 240px; padding: 4px 10px 0; }
  .plot-bg { fill: color-mix(in srgb, var(--background) 94%, #d8e0e5); }
  .grid-line { stroke: color-mix(in srgb, var(--border) 78%, transparent); stroke-width: 1; }
  .axis-label { fill: var(--muted-foreground); font-size: 10px; }
  .cloud-column { stroke: color-mix(in srgb, #5f8990 70%, var(--border)); stroke-width: 1; }
  .sun-ray { stroke: #d38b39; stroke-width: 1.4; stroke-dasharray: 5 4; opacity: .85; }
  .sample-point { fill: #ef6c35; stroke: var(--background); stroke-width: 1.5; }
  .profile-summary { display: flex; gap: 6px 8px; flex-wrap: wrap; padding: 0 14px 8px; color: var(--muted-foreground); font-size: 10px; }
  .profile-summary span { border: 1px solid color-mix(in srgb, var(--border) 80%, transparent); padding: 2px 5px; }
  footer { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 2px 14px 10px; color: var(--muted-foreground); font-size: 10px; }
  footer span { display: inline-flex; align-items: center; gap: 4px; }
  footer i { display: inline-block; width: 15px; height: 7px; }
  footer i.cloud { background: rgba(105, 154, 142, .62); }
  footer i.sun-ray-key { border-top: 2px dashed #d38b39; }
  footer i.pressure { width: 8px; height: 8px; border-radius: 50%; background: #6d9b9a; }
  @media (max-width: 760px) { header { padding-inline: 10px; } svg { padding-inline: 2px; } .profile-summary, footer { padding-inline: 10px; } }
</style>
