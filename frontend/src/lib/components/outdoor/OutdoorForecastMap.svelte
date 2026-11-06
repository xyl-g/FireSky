<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';
  import type {
    OutdoorMapPayload,
    OutdoorMapPoint,
    OutdoorProduct,
    OutdoorWeatherMetric,
  } from '$lib/api/types';
  import { TIANDITU_ATTRIBUTION, TIANDITU_KEY } from '$lib/utils/constants';

  interface Props {
    points?: OutdoorMapPoint[];
    request?: OutdoorMapPayload['request'] | null;
    scale?: OutdoorMapPayload['scale'] | null;
    product: OutdoorProduct;
    weatherMetric: OutdoorWeatherMetric;
    selectedPoint?: OutdoorMapPoint | null;
    onViewportChanged?: (bounds: { west: number; south: number; east: number; north: number }) => void;
    onPointSelected?: (point: OutdoorMapPoint) => void;
  }

  let {
    points = [],
    request = null,
    scale = null,
    product,
    weatherMetric,
    selectedPoint = null,
    onViewportChanged,
    onPointSelected,
  }: Props = $props();

  let container: HTMLDivElement;
  let map: L.Map | null = null;
  let canvas: HTMLCanvasElement | null = null;
  let animationFrame: number | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let selectedLayer: L.CircleMarker | null = null;
  let drawQueued = false;

  const opportunityStops: Array<[number, [number, number, number]]> = [
    [0, [67, 78, 84]],
    [0.25, [151, 113, 46]],
    [0.45, [36, 139, 132]],
    [0.65, [50, 116, 166]],
    [0.8, [109, 76, 153]],
    [1, [181, 74, 109]],
  ];

  function weatherStops(): Array<[number, [number, number, number]]> {
    if (weatherMetric === 'temperature') {
      return [
        [0, [67, 108, 173]],
        [0.28, [51, 163, 166]],
        [0.55, [117, 175, 95]],
        [0.75, [226, 169, 58]],
        [1, [196, 61, 53]],
      ];
    }
    if (weatherMetric === 'precipitation') {
      return [
        [0, [91, 105, 112]],
        [0.25, [48, 156, 174]],
        [0.5, [45, 111, 181]],
        [0.75, [100, 75, 167]],
        [1, [172, 58, 126]],
      ];
    }
    if (weatherMetric === 'cloud_cover') {
      return [
        [0, [61, 83, 79]],
        [0.35, [102, 128, 130]],
        [0.65, [157, 170, 170]],
        [1, [232, 232, 224]],
      ];
    }
    return [
      [0, [43, 131, 115]],
      [0.35, [110, 166, 74]],
      [0.6, [224, 177, 51]],
      [0.8, [219, 111, 46]],
      [1, [182, 53, 52]],
    ];
  }

  function colorAt(value: number): [number, number, number] {
    const stops = product === 'weather' ? weatherStops() : opportunityStops;
    const normalized = Math.max(0, Math.min(1, value));
    for (let index = 1; index < stops.length; index++) {
      const [rightStop, rightColor] = stops[index];
      if (normalized > rightStop) continue;
      const [leftStop, leftColor] = stops[index - 1];
      const ratio = (normalized - leftStop) / Math.max(0.0001, rightStop - leftStop);
      return [0, 1, 2].map((channel) =>
        Math.round(leftColor[channel] + (rightColor[channel] - leftColor[channel]) * ratio),
      ) as [number, number, number];
    }
    return stops[stops.length - 1][1];
  }

  function scheduleDraw() {
    if (!map || !canvas || drawQueued) return;
    drawQueued = true;
    animationFrame = requestAnimationFrame(() => {
      drawQueued = false;
      drawField();
    });
  }

  function drawField() {
    if (!map || !canvas) return;
    const size = map.getSize();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);
    canvas.width = Math.round(size.x * ratio);
    canvas.height = Math.round(size.y * ratio);
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size.x, size.y);
    if (!request || points.length !== request.rows * request.columns) return;

    const sampleWidth = Math.max(140, Math.min(300, Math.round(size.x / 3.5)));
    const sampleHeight = Math.max(100, Math.min(220, Math.round(size.y / 3.5)));
    const xScale = size.x / sampleWidth;
    const yScale = size.y / sampleHeight;
    const longitudeByColumn = new Float32Array(sampleWidth);
    const latitudeByRow = new Float32Array(sampleHeight);
    for (let x = 0; x < sampleWidth; x++) {
      longitudeByColumn[x] = map.containerPointToLatLng([x * xScale, size.y / 2]).lng;
    }
    for (let y = 0; y < sampleHeight; y++) {
      latitudeByRow[y] = map.containerPointToLatLng([size.x / 2, y * yScale]).lat;
    }

    const image = context.createImageData(sampleWidth, sampleHeight);
    const { west, east, south, north } = request.bounds;
    for (let y = 0; y < sampleHeight; y++) {
      const lat = latitudeByRow[y];
      if (lat < south || lat > north) continue;
      const rowPosition = (north - lat) / Math.max(0.0001, north - south) * (request.rows - 1);
      const row0 = Math.max(0, Math.min(request.rows - 1, Math.floor(rowPosition)));
      const row1 = Math.min(request.rows - 1, row0 + 1);
      const rowWeight = rowPosition - row0;
      for (let x = 0; x < sampleWidth; x++) {
        const lon = longitudeByColumn[x];
        if (lon < west || lon > east) continue;
        const columnPosition = (lon - west) / Math.max(0.0001, east - west) * (request.columns - 1);
        const column0 = Math.max(0, Math.min(request.columns - 1, Math.floor(columnPosition)));
        const column1 = Math.min(request.columns - 1, column0 + 1);
        const columnWeight = columnPosition - column0;
        const northWest = points[row0 * request.columns + column0]?.score ?? 0;
        const northEast = points[row0 * request.columns + column1]?.score ?? northWest;
        const southWest = points[row1 * request.columns + column0]?.score ?? northWest;
        const southEast = points[row1 * request.columns + column1]?.score ?? southWest;
        const northValue = northWest + (northEast - northWest) * columnWeight;
        const southValue = southWest + (southEast - southWest) * columnWeight;
        const value = (northValue + (southValue - northValue) * rowWeight) / 100;
        const color = colorAt(value);
        const offset = (y * sampleWidth + x) * 4;
        image.data[offset] = color[0];
        image.data[offset + 1] = color[1];
        image.data[offset + 2] = color[2];
        image.data[offset + 3] = Math.round(product === 'weather' ? 160 : 60 + 155 * Math.sqrt(Math.max(0, value)));
      }
    }
    const offscreen = document.createElement('canvas');
    offscreen.width = sampleWidth;
    offscreen.height = sampleHeight;
    offscreen.getContext('2d')?.putImageData(image, 0, 0);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.drawImage(offscreen, 0, 0, size.x, size.y);
  }

  function selectNearest(event: L.LeafletMouseEvent) {
    if (!request || !points.length) return;
    const { lat, lng } = event.latlng;
    const row = Math.round((request.bounds.north - lat) / (request.bounds.north - request.bounds.south) * (request.rows - 1));
    const column = Math.round((lng - request.bounds.west) / (request.bounds.east - request.bounds.west) * (request.columns - 1));
    if (row < 0 || row >= request.rows || column < 0 || column >= request.columns) return;
    const point = points[row * request.columns + column];
    if (point) onPointSelected?.(point);
  }

  function emitViewport() {
    if (!map) return;
    const bounds = map.getBounds();
    const longitudeSpan = bounds.getEast() - bounds.getWest();
    const latitudeSpan = bounds.getNorth() - bounds.getSouth();
    if (longitudeSpan > 175 || latitudeSpan > 95) {
      const zoomDelta = Math.ceil(Math.log2(Math.max(longitudeSpan / 175, latitudeSpan / 95)));
      map.setZoom(map.getZoom() + Math.max(1, zoomDelta), { animate: false });
      return;
    }
    onViewportChanged?.({
      west: bounds.getWest(),
      south: bounds.getSouth(),
      east: bounds.getEast(),
      north: bounds.getNorth(),
    });
  }

  function renderSelection() {
    selectedLayer?.remove();
    selectedLayer = null;
    if (!map || !selectedPoint) return;
    selectedLayer = L.circleMarker([selectedPoint.lat, selectedPoint.lon], {
      radius: 6,
      weight: 2,
      color: '#ffffff',
      fillColor: '#20282c',
      fillOpacity: 1,
    }).addTo(map);
  }

  export function setView(lat: number, lon: number, zoom = 7) {
    map?.setView([lat, lon], zoom);
  }

  onMount(() => {
    map = L.map(container, {
      zoomControl: false,
      preferCanvas: true,
      minZoom: 3,
      maxBounds: [[-85, -180], [85, 180]],
      maxBoundsViscosity: 1,
    }).setView([30.2741, 120.1552], 6);
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    const tile = (type: string, zIndex: number) => L.tileLayer(
      `https://t{s}.tianditu.gov.cn/DataServer?T=${type}&x={x}&y={y}&l={z}&tk=${TIANDITU_KEY}`,
      {
        subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'],
        maxZoom: 18,
        noWrap: true,
        zIndex,
        attribution: TIANDITU_ATTRIBUTION,
      },
    );
    L.layerGroup([tile('vec_w', 1), tile('cva_w', 2)]).addTo(map);
    canvas = L.DomUtil.create('canvas', 'outdoor-forecast-canvas') as HTMLCanvasElement;
    map.getPanes().overlayPane.appendChild(canvas);
    map.on('moveend', emitViewport);
    map.on('move zoom resize', scheduleDraw);
    map.on('click', selectNearest);
    resizeObserver = new ResizeObserver(() => map?.invalidateSize({ pan: false }));
    resizeObserver.observe(container);
    scheduleDraw();
    emitViewport();
  });

  onDestroy(() => {
    if (animationFrame !== null) cancelAnimationFrame(animationFrame);
    resizeObserver?.disconnect();
    map?.remove();
    map = null;
  });

  $effect(() => {
    points;
    request;
    product;
    weatherMetric;
    scheduleDraw();
  });

  $effect(() => {
    selectedPoint;
    renderSelection();
  });
</script>

<div bind:this={container} class="map" aria-label={`${scale?.label ?? '户外预测'}整图图层`}></div>

<style>
  .map {
    width: 100%;
    height: 100%;
    min-height: 0;
    background: #dce5e8;
  }

  :global(.outdoor-forecast-canvas) {
    position: absolute;
    pointer-events: none;
  }

  :global(.leaflet-control-attribution) {
    max-width: min(70vw, 620px);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

</style>
