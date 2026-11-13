<script lang="ts">
  import { onMount, untrack } from "svelte";
  import LeafletMapView from "./LeafletMapView.svelte";
  import { DEVELOPMENT_GRID, sanitizeGrid, type BaseId, type GridCell, type MapSpot, type MapViewError, type SourceId } from "../map-grid";
  import type { RegionBounds } from "../map-region";

  let {
    spot = null, onPick = null, onReady = null, onError = null, onGridSelect = null,
    provider = "tianditu", tdtKey = "", dark = false, base = "imagery", mini = false,
    drawing = false, selectionBounds = null, onBounds = null, onDrawEnd = null,
  }: {
    spot?: MapSpot | null;
    onPick?: ((lat: number, lon: number) => void) | null;
    onReady?: (() => void) | null;
    onError?: ((error: MapViewError) => void) | null;
    onGridSelect?: ((cell: GridCell) => void) | null;
    provider?: SourceId; tdtKey?: string; dark?: boolean; base?: BaseId;
    mini?: boolean;
    drawing?: boolean;
    selectionBounds?: RegionBounds | null;
    onBounds?: ((bounds: RegionBounds) => void) | null;
    onDrawEnd?: (() => void) | null;
  } = $props();

  interface MapMethods {
    fsSetGrid(cells: unknown, spacing?: number): void;
    fsClearGrid(): void;
    fsFocusGridCell(lat: number, lon: number): void;
    fsSetView(lat: number, lon: number, zoom?: number, name?: string): void;
    fsFitBounds(west: number, south: number, east: number, north: number): void;
    fsRetry(): void;
    fsIsReady(): boolean;
  }
  let child = $state<MapMethods>();
  let cells = $state<GridCell[]>([]);
  let hasGrid = $state(false);
  let spacing = .25;
  let focus: [number, number] | null = null;
  let view: [number, number, number, string?] | null = null;
  let bounds: [number, number, number, number] | null = null;
  let selected = $state<GridCell | null>(null);
  let debug = $state(false);
  onMount(() => { debug = import.meta.env.DEV && new URLSearchParams(window.location.search).get("mapDebug") === "1"; });

  function gridSelect(cell: GridCell) { selected = cell; focus = [cell[0], cell[1]]; onGridSelect?.(cell); }
  function replay() {
    if (!child) return;
    if (view) child.fsSetView(...view);
    if (hasGrid) child.fsSetGrid(cells, spacing);
    if (bounds) child.fsFitBounds(...bounds);
    if (focus) child.fsFocusGridCell(...focus);
  }
  $effect(() => { const instance = child; if (instance) untrack(replay); });
  function mapReady() { replay(); onReady?.(); }

  export function fsSetGrid(input: unknown, cellSpacing = .25) {
    cells = sanitizeGrid(input); spacing = cellSpacing; hasGrid = true; selected = null;
    focus = null; bounds = null; child?.fsSetGrid(cells, spacing);
  }
  export function fsClearGrid() { cells = []; hasGrid = false; selected = null; focus = null; bounds = null; child?.fsClearGrid(); }
  export function fsFocusGridCell(lat: number, lon: number) { focus = [lat, lon]; child?.fsFocusGridCell(lat, lon); }
  export function fsSetView(lat: number, lon: number, zoom = 11, name?: string) { view = [lat, lon, zoom, name]; bounds = null; focus = null; child?.fsSetView(...view); }
  export function fsFitBounds(west: number, south: number, east: number, north: number) { bounds = [west, south, east, north]; focus = null; child?.fsFitBounds(...bounds); }
  export function fsRetry() { child?.fsRetry(); }
  export function fsIsReady() { return child?.fsIsReady() ?? false; }
</script>

<div class="map-switcher" class:mini>
  <div class="provider-map">
    {#key provider}
      <LeafletMapView bind:this={child} source={provider} initialSource={provider} {base} {tdtKey} {spot} {dark} {mini} {onPick} {onError} {drawing} {selectionBounds} {onBounds} {onDrawEnd} onGridSelect={gridSelect} onReady={mapReady} showSourceControl={false} />
    {/key}
  </div>
  {#if debug && !mini}
    <details class="grid-tools">
      <summary>开发网格（合成，非预测）</summary>
      <button onclick={() => fsSetGrid(DEVELOPMENT_GRID)}>加载三格</button>
      {#each cells as cell, index}<button onclick={() => fsFocusGridCell(cell[0], cell[1])}>聚焦 {index + 1} · {cell[2] === null ? "缺测" : cell[2]}</button>{/each}
      <button onclick={fsClearGrid}>清除网格</button>
      {#if selected}<span>{selected[2] === null ? "缺测 / 计算失败" : `峰值 ${selected[2].toFixed(3)}`}</span>{/if}
    </details>
  {/if}
</div>

<style>
  .map-switcher { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  button { color: var(--fs-ink); background: var(--fs-surface-strong); border: 1px solid var(--fs-border); border-radius: 5px; padding: 6px 8px; font: inherit; min-width: 0; }
  button { cursor: pointer; }
  .provider-map { display: flex; flex: 1; min-height: 0; position: relative; }
  .grid-tools { flex-shrink: 0; padding: 8px 12px; font-size: 10px; max-height: 100px; overflow: auto; }
  .grid-tools button { margin: 6px 6px 0 0; font-size: 10px; }
</style>
