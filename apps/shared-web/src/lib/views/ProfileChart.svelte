<script lang="ts">
  import { profileCellLabel, profileData, type ProfileCell } from "../profile-data";
  import type { RecordValue } from "../forecast-display";

  let { report, satellite = false }: { report: RecordValue | null; satellite?: boolean } = $props();
  const patternId = $props.id();
  const data = $derived(profileData(report, satellite));
  let focused = $state<ProfileCell | null>(null);
  let activeIndex = $state(0);
  let svg = $state<SVGSVGElement>();
  $effect(() => { data; focused = null; activeIndex = 0; });
  const width = 760, height = 300, left = 44, right = 744, top = 16, bottom = 254;
  const x = (distance: number) => left + distance / data.maxDistance * (right - left);
  const y = (altitude: number) => bottom - Math.max(0, altitude) / data.maxHeight * (bottom - top);
  const line = (points: { distance: number; height: number }[]) =>
    points.map((point, index) => `${index ? "L" : "M"}${x(point.distance)},${y(point.height)}`).join(" ");
  const axes = $derived(Array.from({ length: 5 }, (_, index) => index / 4));
  const fill = (cell: ProfileCell) => cell.probability === null ? `url(#${patternId})`
    : `color-mix(in srgb, var(--profile-color) ${8 + cell.probability * .85}%, var(--fs-surface-strong))`;
  const temperatureColor = (value: number | null) => value === null ? "var(--fs-ink2)" : value < -30 ? "#9b7299" : value < 0 ? "#ba7468" : "#c45142";
  const kilometers = (value: number | null) => value === null ? "--" : (value / 1000).toFixed(1);
  function inspectKey(event: KeyboardEvent, index: number) {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); focused = data.cells[index]; return; }
    const direction = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
    if (!direction && !["Home", "End"].includes(event.key)) return;
    event.preventDefault();
    activeIndex = event.key === "Home" ? 0 : event.key === "End" ? data.cells.length - 1 : Math.max(0, Math.min(data.cells.length - 1, index + direction));
    svg?.querySelector<SVGRectElement>(`[data-cell-index="${activeIndex}"]`)?.focus();
  }
</script>

<section class="profile" class:satellite aria-label={data.title}>
  <header>
    <div><h2>{data.title}</h2><p>{data.source}</p></div>
    <div class="metadata">
      {#if data.timestamp}<span>{data.timestamp.replace("T", " ").slice(0, 16)}{satellite ? " UTC" : ""}</span>{/if}
      {#if data.bearing !== null}<span>方位 {data.bearing.toFixed(1)}°</span>{/if}
      {#if data.solarElevation !== null}<span>太阳高度 {data.solarElevation.toFixed(1)}°</span>{/if}
      {#if data.offsetMinutes !== null}<span>相对时刻 {data.offsetMinutes > 0 ? "+" : ""}{data.offsetMinutes} 分</span>{/if}
      {#if data.coverage !== null}<span>覆盖 {data.coverage.toFixed(0)}%</span>{/if}
    </div>
  </header>
  {#if data.cells.length}
    <div class="plot-scroll">
      <svg bind:this={svg} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={satellite ? "沿太阳方向的卫星云层后验概率剖面" : "沿太阳方向的模式云量剖面"} data-profile-cells={data.cells.length}>
        <defs>
          <pattern id={patternId} width="6" height="6" patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="var(--fs-hover)" />
            <path d="M-1,1 L1,-1 M0,6 L6,0 M5,7 L7,5" stroke="var(--fs-ink2)" stroke-opacity=".32" stroke-width=".8" />
          </pattern>
        </defs>
        <rect x={left} y={top} width={right - left} height={bottom - top} class="plot-background" />
        {#each axes as ratio}
          <line x1={left} x2={right} y1={y(data.maxHeight * ratio)} y2={y(data.maxHeight * ratio)} class="guide" />
          <text x={left - 7} y={y(data.maxHeight * ratio) + 3} text-anchor="end">{(data.maxHeight * ratio / 1000).toFixed(1)}</text>
          <text x={x(data.maxDistance * ratio)} y={bottom + 16} text-anchor={ratio === 0 ? "start" : ratio === 1 ? "end" : "middle"}>{(data.maxDistance * ratio).toFixed(0)}</text>
        {/each}
        {#each data.cells as cell, index (cell.id)}
          <rect class="profile-cell" class:missing={cell.probability === null}
            x={x(cell.start)} y={y(cell.top)}
            width={Math.max(.2, x(cell.end) - x(cell.start))}
            height={Math.max(.2, y(cell.bottom) - y(cell.top))}
            fill={fill(cell)} stroke={focused?.id === cell.id ? "var(--fs-ink)" : cell.status === 3 || cell.status === 2 ? "var(--fs-accent)" : "var(--fs-surface-strong)"}
            stroke-width={focused?.id === cell.id || cell.status === 3 || cell.status === 2 ? 1.5 : .4}
            stroke-dasharray={cell.status === 2 ? "4 3" : undefined}
            tabindex={index === activeIndex ? 0 : -1} role="button" aria-label={profileCellLabel(cell, satellite)}
            data-cell-index={index} onclick={() => { focused = cell; activeIndex = index; }} onkeydown={(event) => inspectKey(event, index)}
            onmouseenter={() => { focused = cell; }} onmouseleave={() => { focused = null; }}
            onfocus={() => { focused = cell; activeIndex = index; }} onblur={() => { focused = null; }}>
            <title>{profileCellLabel(cell, satellite)}</title>
          </rect>
        {/each}
        {#each data.pressureLines as pressureLine, index}
          {#each pressureLine.segments as points}<path d={line(points)} class="pressure-line" stroke={temperatureColor(pressureLine.temperature)} />{/each}
          {@const last = pressureLine.segments.at(-1)?.at(-1)}
          {#if index % 2 === 0 && last}
            <text x={x(last.distance) - 4} y={y(last.height) - 3} text-anchor="end" class="pressure-label">{pressureLine.pressure} hPa</text>
          {/if}
        {/each}
        {#each data.terrain as points}
          {#if points.length > 1}<path d={`${line(points)} L${x(points.at(-1)!.distance)},${bottom} L${x(points[0].distance)},${bottom} Z`} class="terrain-fill" />{/if}
          <path d={line(points)} class="terrain-line" />
        {/each}
        {#each data.illumination as points}<path d={line(points)} class="illumination" />{/each}
        <text x={left} y={top - 5}>高度 · km ASL</text>
        <text x={(left + right) / 2} y={height - 5} text-anchor="middle">沿太阳方向距离 · km</text>
      </svg>
    </div>
    <div class="readout" aria-live="polite">{focused ? profileCellLabel(focused, satellite) : "\u00a0"}</div>
    <footer>
      <span><i class="missing-key"></i>缺测</span>
      <span>{satellite ? "云层后验概率" : "模式云量"}</span><span>0%</span><i class="scale"></i><span>100%</span>
      {#if data.illumination.length}<span><i class="illumination-key"></i>理论受光阈值</span>{/if}
      {#if data.terrain.length}<span title={data.terrainSource}><i class="terrain-key"></i>地形</span>{/if}
      <span><i class="temperature-key"></i>压力 / 温度层线</span>
      {#if data.cells.some((cell) => cell.status === 3 || cell.status === 2)}
        <span><i class="candidate-key"></i>受光且几何可见候选</span>
        <span><i class="candidate-key hidden-target"></i>受光但几何不可见</span>
      {/if}
    </footer>
    {#if data.targets.length}
      <div class="targets">
        <h3>受光与几何 / 地形可见候选</h3>
        {#each data.targets.filter((target) => target.status === "burnable").slice(0, 8) as target}
          <p title={target.reason}>{target.family} · {target.phase} · {target.distance?.toFixed(0) ?? "--"} km · ASL {kilometers(target.base)}–{kilometers(target.top)} km · AGL {kilometers(target.baseAGL)}–{kilometers(target.topAGL)} km</p>
        {:else}<p>{data.targets.find((target) => target.reason)?.reason || "当前太阳方向没有受光且几何可见的目标云"}</p>{/each}
      </div>
    {/if}
    <p class="source-note">{data.sourceNote}</p>
  {:else}<p class="empty">{data.empty}</p>{/if}
</section>

<style>
  .profile { --profile-color: var(--fs-teal); min-width: 0; padding: 18px 0; }
  .profile.satellite { --profile-color: var(--fs-accent); }
  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  h2 { margin: 0; font-size: 13px; font-weight: 600; }
  p { margin: 7px 0 0; color: var(--fs-ink2); font-size: 10px; line-height: 1.6; }
  .metadata { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px 12px; color: var(--fs-ink2); font-size: 10px; font-variant-numeric: tabular-nums; }
  .plot-scroll { overflow-x: auto; margin-top: 18px; }
  svg { display: block; width: 100%; min-width: 620px; min-height: 210px; }
  svg text { fill: var(--fs-ink2); font-size: 12px; font-variant-numeric: tabular-nums; }
  .plot-background { fill: var(--fs-surface-strong); }
  .guide { stroke: var(--fs-border); stroke-width: .8; }
  .profile-cell { outline: none; }
  .profile-cell:focus-visible { stroke: var(--fs-ink); stroke-width: 2; }
  .terrain-fill { fill: color-mix(in srgb, var(--fs-ink2) 20%, transparent); pointer-events: none; }
  .terrain-line { fill: none; stroke: var(--fs-ink2); stroke-width: 1.4; pointer-events: none; }
  .illumination { fill: none; stroke: var(--fs-accent); stroke-width: 1.6; stroke-dasharray: 6 4; pointer-events: none; }
  .pressure-line { fill: none; stroke-width: .9; opacity: .6; pointer-events: none; }
  .pressure-label { font-size: 10px; pointer-events: none; }
  .readout { min-height: 30px; padding-top: 5px; color: var(--fs-ink2); font-size: 10px; line-height: 1.5; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  footer { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font-size: 9px; color: var(--fs-ink2); }
  footer span { display: inline-flex; align-items: center; gap: 5px; }
  footer i { display: inline-block; width: 12px; height: 8px; flex-shrink: 0; }
  .missing-key { border: 1px solid var(--fs-border); background: repeating-linear-gradient(135deg, var(--fs-hover), var(--fs-hover) 2px, var(--fs-ink2) 2px, var(--fs-ink2) 3px); }
  footer .scale { width: 72px; height: 5px; background: linear-gradient(to right, color-mix(in srgb, var(--profile-color) 8%, var(--fs-surface-strong)), color-mix(in srgb, var(--profile-color) 93%, var(--fs-surface-strong))); }
  .illumination-key { border-top: 2px dashed var(--fs-accent); height: 0; margin-left: 8px; }
  .terrain-key { border-top: 2px solid var(--fs-ink2); height: 0; margin-left: 8px; }
  .temperature-key { border-top: 2px solid #ba7468; height: 0; margin-left: 8px; }
  .candidate-key { border: 1.5px solid var(--fs-accent); margin-left: 8px; }
  .candidate-key.hidden-target { border-style: dashed; }
  .targets { margin-top: 14px; border-top: 1px solid var(--fs-border); padding-top: 12px; }
  h3 { margin: 0; font-size: 11px; font-weight: 500; }
  .empty { min-height: 260px; display: grid; place-items: center; margin: 0; text-align: center; font-size: 12px; }
  @media (max-width: 600px) { header { flex-direction: column; } .metadata { justify-content: flex-start; } .profile { padding-top: 14px; } }
</style>
