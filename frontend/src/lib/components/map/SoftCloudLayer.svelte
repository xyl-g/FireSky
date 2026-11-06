<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';
  import type { MapCloudCell } from '@/api/types';
  import type { CloudField } from '@/utils/cloud-render';
  import { buildFieldRaster, type FieldRaster } from '@/utils/cloud-render';

  export let map: L.Map;
  export let cells: MapCloudCell[] = [];
  export let field: CloudField = 'total';
  export let showCloud: boolean = true;
  export let showBurn: boolean = true;
  export let renderMode: 'smooth' | 'grid' = 'smooth';

  let canvas: HTMLCanvasElement | null = null;
  let raster: FieldRaster | null = null;
  let queued = false;
  let animFrameId: number | null = null;

  // Grid layer for "grid" render mode
  const gridLayer = L.layerGroup();
  const hitLayer = L.layerGroup();

  function schedule() {
    if (queued || !map) return;
    queued = true;
    animFrameId = requestAnimationFrame(() => {
      queued = false;
      redraw();
    });
  }

  function redraw() {
    if (!map || !canvas) return;

    const size = map.getSize();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const topLeft = map.containerPointToLayerPoint([0, 0]);

    L.DomUtil.setPosition(canvas, topLeft);
    canvas.width = Math.round(size.x * ratio);
    canvas.height = Math.round(size.y * ratio);
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;

    const context = canvas.getContext('2d')!;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size.x, size.y);

    if (!cells || !cells.length || (!showCloud && !showBurn)) return;

    if (renderMode === 'smooth') {
      if (!raster) {
        raster = buildFieldRaster(cells, field, { showCloud, showBurn });
      }
      if (!raster) return;

      const northWest = map.latLngToContainerPoint([raster.north, raster.west]);
      const southEast = map.latLngToContainerPoint([raster.south, raster.east]);

      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = 'high';
      context.drawImage(
        raster.canvas,
        northWest.x,
        northWest.y,
        southEast.x - northWest.x,
        southEast.y - northWest.y,
      );
    }
  }

  // React to prop changes
  $: if (cells || field || showCloud || showBurn || renderMode) {
    raster = null;
    schedule();
  }

  onMount(() => {
    if (!map) return;

    canvas = L.DomUtil.create('canvas', 'cloud-field-canvas') as HTMLCanvasElement;
    map.getPanes().overlayPane.appendChild(canvas);
    map.on('move zoom resize', schedule);

    gridLayer.addTo(map);
    hitLayer.addTo(map);

    schedule();
  });

  onDestroy(() => {
    if (animFrameId) cancelAnimationFrame(animFrameId);
    map?.off('move zoom resize', schedule);
    canvas?.remove();
    gridLayer.remove();
    hitLayer.remove();
  });
</script>

<style>
  :global(.cloud-field-canvas) {
    position: absolute;
    pointer-events: none;
  }
</style>
