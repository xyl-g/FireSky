<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';
  import type { RegionalForecastCoverage, RegionalForecastPoint } from '$lib/api/types';
  import { TIANDITU_ATTRIBUTION, TIANDITU_KEY } from '$lib/utils/constants';
  import { burnColor, fieldColor, mixColor, type CloudField } from '$lib/utils/cloud-render';

  export let points: RegionalForecastPoint[] = [];
  export let coverage: RegionalForecastCoverage[] = [];
  export let selectedPoint: RegionalForecastPoint | null = null;
  export let showSamples = false;
  export let field: 'score' | CloudField = 'score';
  export let pointMode = '';
  export let pointSpacingDeg: number | null = null;
  export let onPointSelected: ((point: RegionalForecastPoint) => void) | null = null;
  export let pointForecastHref: ((point: RegionalForecastPoint) => string) | null = null;
  export let formatBestTime: ((value: string | null) => string) | null = null;

  let container: HTMLDivElement;
  let map: L.Map | null = null;
  let canvas: HTMLCanvasElement | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let animationFrame: number | null = null;
  let queued = false;
  let lastFitKey = '';
  const sampleLayer = L.layerGroup();
  const pressureFamilyLabels = {
    high: '高云压力族', middle: '中云压力族', low: '低云压力族',
  } as const;

  function fillForScore(score: number): number[] | null {
    const color = burnColor(score);
    const alpha = score <= 0.001 ? 12 : Math.round(42 + 190 * Math.sqrt(Math.min(1, score)));
    return [...color, alpha];
  }

  function quality(point: RegionalForecastPoint): number {
    return Number(point.peak_quality_index);
  }

  function element<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    className: string,
    text?: string,
  ): HTMLElementTagNameMap[K] {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function signed(value: number): string {
    return `${value >= 0 ? '+' : ''}${value.toFixed(3)}`;
  }

  function addDetailRow(list: HTMLDListElement, label: string, value: string) {
    const row = element('div', 'burn-point-popup-row');
    row.append(element('dt', '', label), element('dd', '', value));
    list.append(row);
  }

  function pointPopupWidth(): number {
    const viewportWidth = typeof window === 'undefined'
      ? 408
      : Math.min(window.innerWidth, container?.clientWidth || window.innerWidth);
    // Leave room for the popup margins and Leaflet's auto-pan padding on narrow maps.
    return Math.max(1, Math.min(360, viewportWidth - 48));
  }

  function pointPopupOptions(): L.PopupOptions {
    const width = pointPopupWidth();
    return {
      className: 'burn-point-popup-shell',
      minWidth: width,
      maxWidth: width,
      autoPanPadding: [12, 12],
    };
  }

  function createPointPopup(point: RegionalForecastPoint): HTMLElement {
    const content = element('article', 'burn-point-popup');
    const heading = element('header', 'burn-point-popup-heading');
    const isCoordinateLabel = /^(CN|KR|JP|CUSTOM)-/.test(point.name);
    heading.append(
      element(
        'strong',
        isCoordinateLabel
          ? 'burn-point-popup-location burn-point-popup-coordinate'
          : 'burn-point-popup-location',
        isCoordinateLabel
          ? `${point.lat.toFixed(1)}°N, ${point.lon.toFixed(1)}°E`
          : point.name,
      ),
      element('span', 'burn-point-popup-level', point.peak_quality_level),
    );
    content.append(heading);

    const scores = element('div', 'burn-point-popup-scores');
    const peak = element('div', 'burn-point-popup-score');
    peak.append(
      element('span', 'burn-point-popup-score-label', '峰值质量 / 2.5'),
      element('strong', '', quality(point).toFixed(3)),
    );
    const event = element('div', 'burn-point-popup-score burn-point-popup-score-secondary');
    event.append(
      element('span', 'burn-point-popup-score-label', '事件窗口质量 / 2.5'),
      element('strong', '', Number(point.quality_index).toFixed(3)),
    );
    scores.append(peak, event);
    content.append(scores);

    const details = element('dl', 'burn-point-popup-details');
    addDetailRow(
      details,
      '主导可烧压力族',
      point.dominant_burnable_pressure_family
        ? pressureFamilyLabels[point.dominant_burnable_pressure_family]
        : '无可烧目标云',
    );
    addDetailRow(details, '有效可烧云', `${(point.burnable_cloud_coverage_effective * 100).toFixed(1)}%`);
    addDetailRow(details, '可烧时长', `${point.burnable_duration_minutes.toFixed(0)} 分钟`);
    addDetailRow(details, '质量 AOD', point.quality_aod550.toFixed(3));
    addDetailRow(details, '入射修正', signed(point.incoming_aod_adjustment));
    addDetailRow(details, '观测修正', signed(point.observer_aod_adjustment));
    addDetailRow(details, '最佳时刻', formatBestTime?.(point.best_time_local) ?? point.best_time_local ?? '无明显峰值');
    addDetailRow(details, '置信度', `${Math.round(point.confidence_score * 100)}%`);
    content.append(details);

    if (pointForecastHref) {
      const link = element('a', 'burn-point-popup-command', '打开单点诊断');
      link.href = pointForecastHref(point);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      content.append(link);
    }
    return content;
  }

  function normalizedQuality(point: RegionalForecastPoint): number {
    return quality(point) / 2.5;
  }

  function cloudValue(point: RegionalForecastPoint): number {
    if (field === 'score') return 0;
    if (field === 'total') {
      return Math.max(point.high_cloud, point.mid_cloud, point.low_cloud);
    }
    return Number(point[field]);
  }

  function fillForPoint(point: RegionalForecastPoint): number[] | null {
    const score = normalizedQuality(point);
    if (field === 'score') return fillForScore(score);
    const cloud = cloudValue(point);
    if (cloud < 0.04 && score < 0.1) return null;
    const cloudColor = fieldColor(field, cloud);
    const mixed = score >= 0.1
      ? mixColor(cloudColor, burnColor(score), 0.25 + 0.45 * Math.min(1, score))
      : cloudColor;
    return [...mixed, Math.round(35 + 180 * Math.max(cloud, score))];
  }

  function minimumPositiveSpacing(values: number[]): number | null {
    if (values.length < 2) return null;
    const sorted = [...new Set(values.map((value) => Math.round(value * 10_000) / 10_000))].sort((a, b) => a - b);
    let minimum = Number.POSITIVE_INFINITY;
    for (let index = 1; index < sorted.length; index += 1) {
      const difference = sorted[index] - sorted[index - 1];
      if (difference > 1e-6) minimum = Math.min(minimum, difference);
    }
    return Number.isFinite(minimum) ? minimum : null;
  }

  function regularGridSpacing(): { lat: number; lon: number } | null {
    if (pointMode !== 'generated-regional-grid') return null;
    if (
      points.length < 4
      || pointSpacingDeg === null
      || !Number.isFinite(pointSpacingDeg)
      || pointSpacingDeg <= 0
    ) {
      throw new Error('generated-regional-grid 缺少有效的固定网格间距');
    }
    const latSpacing = minimumPositiveSpacing(points.map((point) => Number(point.lat)));
    const lonSpacing = minimumPositiveSpacing(points.map((point) => Number(point.lon)));
    if (
      latSpacing === null
      || lonSpacing === null
      || Math.abs(latSpacing - pointSpacingDeg) > 1e-6
      || Math.abs(lonSpacing - pointSpacingDeg) > 1e-6
    ) {
      throw new Error('generated-regional-grid 坐标间距与摘要精度不一致');
    }
    return { lat: pointSpacingDeg, lon: pointSpacingDeg };
  }

  function drawRegularGrid(
    context: CanvasRenderingContext2D,
    visible: { point: RegionalForecastPoint; px: number; py: number }[],
  ): boolean {
    const spacing = regularGridSpacing();
    if (!spacing) return false;
    const halfLat = spacing.lat / 2;
    const halfLon = spacing.lon / 2;
    for (const item of visible) {
      const fill = fillForPoint(item.point);
      if (!fill) continue;
      const northWest = map!.latLngToContainerPoint([
        Number(item.point.lat) + halfLat,
        Number(item.point.lon) - halfLon,
      ]);
      const southEast = map!.latLngToContainerPoint([
        Number(item.point.lat) - halfLat,
        Number(item.point.lon) + halfLon,
      ]);
      const width = southEast.x - northWest.x;
      const height = southEast.y - northWest.y;
      if (width <= 0 || height <= 0) continue;
      context.fillStyle = `rgba(${fill[0]}, ${fill[1]}, ${fill[2]}, ${fill[3] / 255})`;
      context.fillRect(northWest.x, northWest.y, width, height);
    }
    return true;
  }

  function scheduleDraw() {
    if (!map || !canvas || queued) return;
    queued = true;
    animationFrame = requestAnimationFrame(() => {
      queued = false;
      drawField();
    });
  }

  function coverageRects(): L.Bounds[] {
    if (!map) return [];
    return coverage.map((area) => {
      const [west, south, east, north] = area.bounds;
      return L.bounds(
        map!.latLngToContainerPoint([north, west]),
        map!.latLngToContainerPoint([south, east]),
      );
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
    if (!points.length) return;

    // --- Viewport culling ---
    const bounds = map.getBounds().pad(0.12);
    const visible: { point: RegionalForecastPoint; score: number; cloud: number; px: number; py: number }[] = [];
    for (const point of points) {
      if (bounds.contains([point.lat, point.lon])) {
        const pixel = map!.latLngToContainerPoint([point.lat, point.lon]);
        visible.push({ point, score: normalizedQuality(point), cloud: cloudValue(point), px: pixel.x, py: pixel.y });
      }
    }
    if (!visible.length) return;

    if (drawRegularGrid(context, visible)) return;

    // Sample grid (lower resolution for performance, upscaled with smoothing)
    const sampleWidth = Math.max(120, Math.min(260, Math.round(size.x / 4)));
    const sampleHeight = Math.max(90, Math.min(200, Math.round(size.y / 4)));
    const scaleX = size.x / sampleWidth;
    const scaleY = size.y / sampleHeight;

    // --- Precompute radial weight stamp ---
    const stampHalf = 4;
    const stampSize = stampHalf * 2 + 1;
    const stamp = new Float32Array(stampSize * stampSize);
    const smoothC = (scaleX + scaleY) * 2; // adaptive smoothing constant
    for (let dy = -stampHalf; dy <= stampHalf; dy++) {
      for (let dx = -stampHalf; dx <= stampHalf; dx++) {
        const distSq = (dx * scaleX) ** 2 + (dy * scaleY) ** 2;
        stamp[(dy + stampHalf) * stampSize + (dx + stampHalf)] = 1 / Math.pow(distSq + smoothC, 1.05);
      }
    }

    // --- Accumulate weighted scores via stamp ---
    const scoreField = new Float32Array(sampleWidth * sampleHeight);
    const cloudField = new Float32Array(sampleWidth * sampleHeight);
    const weight = new Float32Array(sampleWidth * sampleHeight);

    const pointStride = 1;
    for (let visibleIndex = 0; visibleIndex < visible.length; visibleIndex += pointStride) {
      const item = visible[visibleIndex];
      const sx = Math.round(item.px / scaleX);
      const sy = Math.round(item.py / scaleY);
      if (sx < -stampHalf || sx >= sampleWidth + stampHalf) continue;
      if (sy < -stampHalf || sy >= sampleHeight + stampHalf) continue;
      for (let dy = -stampHalf; dy <= stampHalf; dy++) {
        const gy = sy + dy;
        if (gy < 0 || gy >= sampleHeight) continue;
        const rowOff = gy * sampleWidth;
        const stampRow = (dy + stampHalf) * stampSize;
        for (let dx = -stampHalf; dx <= stampHalf; dx++) {
          const gx = sx + dx;
          if (gx < 0 || gx >= sampleWidth) continue;
          const w = stamp[stampRow + dx + stampHalf];
          const idx = rowOff + gx;
          scoreField[idx] += item.score * w;
          cloudField[idx] += item.cloud * w;
          weight[idx] += w;
        }
      }
    }

    // --- Colorize: normalize and map to RGBA ---
    const image = context.createImageData(sampleWidth, sampleHeight);
    for (let i = 0; i < scoreField.length; i++) {
      if (weight[i] <= 0) continue;
      const score = scoreField[i] / weight[i];
      const cloud = cloudField[i] / weight[i];
      let fill = fillForScore(score);
      if (field !== 'score') {
        if (cloud < 0.04 && score < 0.1) continue;
        const cloudColor = fieldColor(field, cloud);
        const mixed = score >= 0.1
          ? mixColor(cloudColor, burnColor(score), 0.25 + 0.45 * Math.min(1, score))
          : cloudColor;
        fill = [...mixed, Math.round(35 + 180 * Math.max(cloud, score))];
      }
      if (!fill) continue;
      const offset = i * 4;
      image.data[offset] = fill[0];
      image.data[offset + 1] = fill[1];
      image.data[offset + 2] = fill[2];
      image.data[offset + 3] = fill[3];
    }

    // Draw upscaled to full canvas
    const offscreen = document.createElement('canvas');
    offscreen.width = sampleWidth;
    offscreen.height = sampleHeight;
    const offCtx = offscreen.getContext('2d')!;
    offCtx.putImageData(image, 0, 0);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.drawImage(offscreen, 0, 0, size.x, size.y);
  }

  function renderSamples() {
    sampleLayer.clearLayers();
    if (!map || !points.length) return;
    if (selectedPoint) {
      const selectedMarker = L.circleMarker([selectedPoint.lat, selectedPoint.lon], {
        radius: 6,
        weight: 2,
        color: '#fff',
        fillColor: '#d84b2f',
        fillOpacity: 1,
      })
        .bindPopup(createPointPopup(selectedPoint), {
          ...pointPopupOptions(),
        })
        .addTo(sampleLayer);
      selectedMarker.openPopup();
    }
    if (!showSamples) return;
    const bounds = map.getBounds();
    const visiblePoints = points.filter((p) => bounds.contains([p.lat, p.lon]));
    // Cap markers to avoid DOM overload with dense grids
    const maxMarkers = 2000;
    const step = visiblePoints.length > maxMarkers ? Math.ceil(visiblePoints.length / maxMarkers) : 1;
    for (let i = 0; i < visiblePoints.length; i += step) {
      const point = visiblePoints[i];
      const rgb = burnColor(normalizedQuality(point));
      const color = `rgb(${rgb[0]} ${rgb[1]} ${rgb[2]})`;
      L.circleMarker([point.lat, point.lon], {
        radius: 3,
        weight: 1,
        color,
        fillColor: color,
        fillOpacity: 0.9,
      })
        .bindTooltip(
          `${point.peak_quality_level} · ${quality(point).toFixed(3)}/2.5` +
          `<br>主导可烧压力族 ${point.dominant_burnable_pressure_family ? pressureFamilyLabels[point.dominant_burnable_pressure_family] : '无'}` +
          `<br>高/中/低云 ${Math.round(point.high_cloud * 100)}/` +
          `${Math.round(point.mid_cloud * 100)}/${Math.round(point.low_cloud * 100)}%`,
        )
        .on('click', () => onPointSelected?.(point))
        .addTo(sampleLayer);
    }
  }

  function selectNearest(event: L.LeafletMouseEvent) {
    if (!map || !points.length) return;
    const click = map.latLngToContainerPoint(event.latlng);
    const bounds = map.getBounds().pad(0.05);
    let nearest = points[0];
    let nearestDistanceSq = Number.POSITIVE_INFINITY;
    for (const point of points) {
      if (!bounds.contains([point.lat, point.lon])) continue;
      const pixel = map.latLngToContainerPoint([point.lat, point.lon]);
      const dx = pixel.x - click.x;
      const dy = pixel.y - click.y;
      const distSq = dx * dx + dy * dy;
      if (distSq < nearestDistanceSq) {
        nearestDistanceSq = distSq;
        nearest = point;
      }
    }
    if (nearestDistanceSq <= 80 * 80) onPointSelected?.(nearest);
  }

  function fitCoverage() {
    if (!map || !coverage.length) return;
    const key = coverage.map((item) => item.bounds.join(',')).join('|');
    if (key === lastFitKey) return;
    lastFitKey = key;
    const bounds = L.latLngBounds([]);
    for (const item of coverage) {
      const [west, south, east, north] = item.bounds;
      bounds.extend([south, west]);
      bounds.extend([north, east]);
    }
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20], maxZoom: 6 });
  }

  $: if (points || coverage || field) {
    scheduleDraw();
    fitCoverage();
  }
  $: if (points || showSamples || selectedPoint) renderSamples();

  onMount(() => {
    map = L.map(container, { zoomControl: true, preferCanvas: true, minZoom: 3 }).setView([35, 108], 4);
    const tiled = (type: string, zIndex: number) => L.tileLayer(
      `https://t{s}.tianditu.gov.cn/DataServer?T=${type}&x={x}&y={y}&l={z}&tk=${TIANDITU_KEY}`,
      { subdomains: ['0', '1', '2', '3', '4', '5', '6', '7'], maxZoom: 18, zIndex, attribution: TIANDITU_ATTRIBUTION },
    );
    L.layerGroup([tiled('vec_w', 1), tiled('cva_w', 2)]).addTo(map);
    canvas = L.DomUtil.create('canvas', 'burn-outlook-canvas') as HTMLCanvasElement;
    map.getPanes().overlayPane.appendChild(canvas);
    sampleLayer.addTo(map);
    map.on('moveend zoomend resize', scheduleDraw);
    map.on('click', selectNearest);
    resizeObserver = new ResizeObserver(() => map?.invalidateSize({ pan: false }));
    resizeObserver.observe(container);
    fitCoverage();
    renderSamples();
    scheduleDraw();
  });

  onDestroy(() => {
    if (animationFrame !== null) cancelAnimationFrame(animationFrame);
    resizeObserver?.disconnect();
    map?.remove();
    map = null;
  });
</script>

<div bind:this={container} class="map" aria-label="区域火烧云质量地图"></div>

<style>
  .map {
    width: 100%;
    height: 100%;
    min-height: 460px;
    background: #dce5e8;
  }

  :global(.burn-outlook-canvas) {
    position: absolute;
    pointer-events: none;
  }

  :global(.burn-point-popup-shell .leaflet-popup-content-wrapper) {
    box-sizing: border-box;
    max-width: calc(100vw - 16px);
    border: 1px solid var(--glass-border);
    border-radius: 6px;
    background: var(--glass-specular), var(--glass-strong);
    -webkit-backdrop-filter: blur(var(--glass-blur-strong)) saturate(var(--glass-saturation));
    backdrop-filter: blur(var(--glass-blur-strong)) saturate(var(--glass-saturation));
    color: var(--foreground);
    box-shadow: var(--glass-shadow-raised), var(--glass-highlight);
  }

  :global(.burn-point-popup-shell .leaflet-popup-content) {
    box-sizing: border-box;
    min-width: 0;
    margin: 14px;
  }

  :global(.burn-point-popup-shell .leaflet-popup-tip) {
    background: var(--glass-strong);
  }

  :global(.burn-point-popup) {
    width: 100%;
    min-width: 0;
  }

  :global(.burn-point-popup-heading) {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 10px;
    min-width: 0;
    padding-bottom: 10px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
  }

  :global(.burn-point-popup-location) {
    flex: 1 1 180px;
    min-width: 0;
    color: var(--foreground);
    font-size: 13px;
    font-weight: 600;
    line-height: 1.35;
    overflow-wrap: break-word;
    word-break: normal;
  }

  :global(.burn-point-popup-coordinate) {
    flex: 0 0 auto;
    white-space: nowrap;
    overflow-wrap: normal;
    word-break: normal;
  }

  :global(.burn-point-popup-level) {
    flex: 0 0 auto;
    max-width: 100%;
    padding: 3px 6px;
    border-radius: 4px;
    background: var(--glass-well);
    border: 1px solid var(--glass-border);
    color: var(--muted-foreground);
    font-size: 11px;
    line-height: 1.35;
    overflow-wrap: break-word;
    word-break: normal;
  }

  :global(.burn-point-popup-scores) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin: 14px 0;
  }

  :global(.burn-point-popup-score) {
    min-width: 0;
    display: grid;
    gap: 5px;
    padding: 9px 10px 10px;
    border: 1px solid var(--border);
    border-top: 2px solid var(--score-high);
    border-radius: 5px;
    background: color-mix(in srgb, var(--card) 92%, var(--muted));
  }

  :global(.burn-point-popup-score-secondary) {
    border-top-color: var(--score-potential);
  }

  :global(.burn-point-popup-score-label) {
    min-width: 0;
    color: var(--muted-foreground);
    font-size: 10px;
    line-height: 1.35;
    overflow-wrap: break-word;
    word-break: normal;
  }

  :global(.burn-point-popup-score strong) {
    font-size: 23px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  :global(.burn-point-popup-details) {
    display: grid;
    gap: 0;
    margin: 0;
    padding-top: 4px;
    border-top: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
  }

  :global(.burn-point-popup-row) {
    display: grid;
    grid-template-columns: minmax(96px, 1fr) minmax(0, 1.35fr);
    align-items: baseline;
    column-gap: 12px;
    min-width: 0;
    padding: 5px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
    font-size: 11px;
    line-height: 1.4;
  }

  :global(.burn-point-popup-row:nth-child(4)),
  :global(.burn-point-popup-row:nth-child(7)) {
    margin-top: 8px;
    padding-top: 10px;
    border-top: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
  }

  :global(.burn-point-popup-row dt) {
    min-width: 0;
    color: var(--muted-foreground);
  }

  :global(.burn-point-popup-row dd) {
    min-width: 0;
    margin: 0;
    text-align: right;
    font-variant-numeric: tabular-nums;
    overflow-wrap: break-word;
    word-break: normal;
  }

  :global(.burn-point-popup-command) {
    box-sizing: border-box;
    width: 100%;
    min-height: 36px;
    margin-top: 16px;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--foreground) !important;
    text-decoration: none !important;
    font-size: 12px;
    font-weight: 500;
    line-height: 1.35;
    overflow-wrap: break-word;
    text-align: center;
  }

  :global(.burn-point-popup-command:hover) {
    background: var(--glass-well);
  }

  @media (max-width: 760px) {
    .map {
      min-height: 520px;
    }

    :global(.burn-point-popup-shell .leaflet-popup-content-wrapper) {
      max-width: calc(100vw - 16px);
    }

    :global(.burn-point-popup-shell .leaflet-popup-content) {
      margin: 12px;
    }

    :global(.burn-point-popup-row) {
      grid-template-columns: minmax(84px, 0.9fr) minmax(0, 1.1fr);
      column-gap: 8px;
    }
  }
</style>
