<script lang="ts">
  import { onMount, onDestroy, createEventDispatcher } from 'svelte';
  import type * as L from 'leaflet';
  import Hand from '@lucide/svelte/icons/hand';
  import LockKeyhole from '@lucide/svelte/icons/lock-keyhole';
  import { TIANDITU_KEY, TIANDITU_ATTRIBUTION, DEFAULT_LAT, DEFAULT_LON } from '@/utils/constants';
  import { getPoint } from '@/api/location';

  export let lat: number = DEFAULT_LAT;
  export let lon: number = DEFAULT_LON;
  export let zoom: number = 9;
  export let viewportFill = false;
  let touchDevice = false;
  let interactionEnabled = true;

  function setInteraction(enabled: boolean) {
    interactionEnabled = enabled;
    for (const handler of [map?.dragging, map?.touchZoom, map?.doubleClickZoom, marker?.dragging]) {
      if (enabled) handler?.enable(); else handler?.disable();
    }
  }

  // Svelte 5 callback props (preferred over dispatched events)
  export let onMapReady: ((event: { map: L.Map }) => void) | null = null;
  export let onPointSelected: ((event: { lat: number; lon: number; name: string }) => void) | null = null;

  let mapContainer: HTMLDivElement;
  let map: L.Map | null = null;
  let marker: L.Marker | null = null;
  let activeBaseMap: L.LayerGroup | L.TileLayer | null = null;
  const baseMaps: Record<string, L.LayerGroup | L.TileLayer> = {};

  // Public layer groups for child components to add to
  export let cloudCellsLayer: L.LayerGroup | null = null;
  export let cloudHitLayer: L.LayerGroup | null = null;
  export let blockerCellsLayer: L.LayerGroup | null = null;

  const dispatch = createEventDispatcher<{
    pointSelected: { lat: number; lon: number; name: string };
    mapReady: { map: L.Map };
  }>();

  function createBaseMaps(leaflet: typeof L): Record<string, L.LayerGroup | L.TileLayer> {
    const tdLayer = (type: string, zIndex: number) =>
      leaflet.tileLayer(
        `https://t{s}.tianditu.gov.cn/DataServer?T=${type}&x={x}&y={y}&l={z}&tk=${TIANDITU_KEY}`,
        {
          subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
          maxZoom: 18,
          zIndex,
          attribution: TIANDITU_ATTRIBUTION,
        },
      );

    return {
      'tianditu-vector': leaflet.layerGroup([
        tdLayer('vec_w', 1),
        tdLayer('cva_w', 2),
      ]),
      'tianditu-imagery': leaflet.layerGroup([
        tdLayer('img_w', 1),
        tdLayer('cia_w', 2),
      ]),
      osm: leaflet.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }),
    };
  }

  function selectBaseMap(name: string) {
    if (!map) return;
    const next = baseMaps[name];
    if (!next || next === activeBaseMap) return;
    if (activeBaseMap) map.removeLayer(activeBaseMap);
    activeBaseMap = next.addTo(map);
    mapContainer.classList.toggle(
      'muted-basemap',
      name !== 'tianditu-imagery',
    );
  }

  export function selectBaseMapPublic(name: string) {
    selectBaseMap(name);
  }

  export function getMap(): L.Map | null {
    return map;
  }

  onMount(async () => {
    await import('leaflet/dist/leaflet.css');
    const leaflet = (await import('leaflet')).default;
    const bm = createBaseMaps(leaflet);
    Object.assign(baseMaps, bm);

    map = leaflet.map(mapContainer, { zoomControl: true, scrollWheelZoom: false }).setView(
      [lat, lon],
      zoom,
    );

    activeBaseMap = baseMaps['tianditu-imagery'].addTo(map);

    marker = leaflet.marker([lat, lon], { draggable: true }).addTo(map);
    touchDevice = window.matchMedia('(pointer: coarse)').matches;
    setInteraction(!touchDevice);

    cloudCellsLayer = leaflet.layerGroup().addTo(map);
    cloudHitLayer = leaflet.layerGroup().addTo(map);
    blockerCellsLayer = leaflet.layerGroup().addTo(map);

    // Click to select point
    map.on('click', (event: L.LeafletMouseEvent) => {
      if (!interactionEnabled) return;
      const { lat: clickLat, lng: clickLon } = event.latlng;
      selectPointInternal(clickLat, clickLon);
    });

    // Marker drag
    marker.on('dragend', (event: L.LeafletEvent) => {
      const point = (event.target as L.Marker).getLatLng();
      selectPointInternal(point.lat, point.lng);
    });

    // ResizeObserver for responsive map
    if ('ResizeObserver' in window) {
      const ro = new ResizeObserver(() =>
        map?.invalidateSize({ pan: false, debounceMoveend: true }),
      );
      ro.observe(mapContainer);
      // Store for cleanup
      (mapContainer as any).__resizeObserver = ro;
    }

    dispatch('mapReady', { map });
    onMapReady?.({ map });
  });

  onDestroy(() => {
    const ro = (mapContainer as any)?.__resizeObserver;
    if (ro) ro.disconnect();
    map?.remove();
    map = null;
  });

  async function selectPointInternal(
    clickLat: number,
    clickLon: number,
    name: string = '地图选点',
  ) {
    lat = clickLat;
    lon = clickLon;
    marker?.setLatLng([clickLat, clickLon]);

    try {
      const data = await getPoint(clickLat, clickLon);
      dispatch('pointSelected', { lat: clickLat, lon: clickLon, name });
      onPointSelected?.({ lat: clickLat, lon: clickLon, name });
    } catch {
      dispatch('pointSelected', { lat: clickLat, lon: clickLon, name });
      onPointSelected?.({ lat: clickLat, lon: clickLon, name });
    }
  }

  export function setView(
    newLat: number,
    newLon: number,
    newZoom?: number,
  ) {
    if (!map) return;
    lat = newLat;
    lon = newLon;
    marker?.setLatLng([newLat, newLon]);
    map.setView([newLat, newLon], newZoom ?? map.getZoom());
  }

  export function fitBounds(bounds: L.LatLngBoundsExpression, options?: L.FitBoundsOptions) {
    map?.fitBounds(bounds, options);
  }
</script>

<div class="map-shell" class:viewport-fill={viewportFill}>
<div
  bind:this={mapContainer}
  class="map-container"
  class:touch-locked={touchDevice && !interactionEnabled}
  aria-label="观测点地图"
></div>
{#if touchDevice}
  <button class="map-interaction glass-panel-strong" onclick={() => setInteraction(!interactionEnabled)}
    aria-label={interactionEnabled ? '锁定地图交互' : '启用地图交互'} title={interactionEnabled ? '锁定地图交互' : '启用地图交互'} aria-pressed={interactionEnabled}>
    {#if interactionEnabled}<Hand size={18} />{:else}<LockKeyhole size={18} />{/if}
  </button>
{/if}
</div>

<style>
  .map-shell { position: relative; }
  .viewport-fill .map-container { height: calc(100dvh - 100px); min-height: 320px; }
  .map-interaction { position: absolute; left: 12px; bottom: 30px; z-index: 700; width: 44px; height: 44px; display: grid; place-items: center; border: 1px solid var(--glass-border); border-radius: 8px; cursor: pointer; }
  .map-container.touch-locked { touch-action: pan-y; }
  @media (max-width: 1023px) { .viewport-fill .map-container { height: calc(100dvh - 190px); min-height: 320px; } }
  .map-container {
    width: 100%;
    height: min(60vh, 620px);
    min-height: 430px;
    overflow: hidden;
    contain: layout paint;
    background: var(--muted);
    border-bottom: 1px solid var(--border);
  }

  :global(.muted-basemap .leaflet-tile-pane) {
    filter: saturate(0.58) brightness(1.05) contrast(0.91);
  }

  @media (max-width: 700px) {
    .map-container {
      height: min(60vh, 620px);
      min-height: 400px;
    }
  }
</style>
