<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';
  import type { MapCloudCell } from '@/api/types';
  import { contourSegments, type ContourLevel } from '@/utils/cloud-render';

  export let map: L.Map;
  export let cells: MapCloudCell[] = [];
  export let show: boolean = false;
  export let key: string = 'blocking_risk';
  export let levels: ContourLevel[] = [
    { value: 0.45, color: '#6d4055', weight: 1.5, opacity: 0.72, dashArray: '4 4' },
    { value: 0.65, color: '#572c43', weight: 2.1, opacity: 0.92, dashArray: '5 3' },
  ];

  const layer = L.layerGroup();
  const canvasRenderer = L.canvas({ padding: 0.5 });

  function render() {
    layer.clearLayers();
    if (!show || !cells.length || !map) return;

    for (const level of levels) {
      const segments = contourSegments(cells, key, level.value);
      for (const segment of segments) {
        L.polyline(segment, {
          renderer: canvasRenderer,
          color: level.color,
          weight: level.weight,
          opacity: level.opacity,
          dashArray: level.dashArray || undefined,
          interactive: false,
          lineCap: 'round',
        }).addTo(layer);
      }
    }
  }

  $: if (cells || show || levels) {
    render();
  }

  onMount(() => {
    layer.addTo(map);
    render();
  });

  onDestroy(() => {
    layer.remove();
  });
</script>
