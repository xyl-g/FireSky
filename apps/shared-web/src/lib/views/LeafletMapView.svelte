<script lang="ts">
  /** Leaflet port of desktop/assets/map.html, including its viewport grid canvas.
   * READY means map object + configuration replay, never proof of loaded tiles.
   * Keys are supplied in memory only; tile events/URLs are never logged.
   */
  import { onDestroy, onMount, untrack } from "svelte";
  import L from "leaflet";
  import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
  import { setWorkerUrl } from "maplibre-gl";
  import mapLibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
  import "leaflet/dist/leaflet.css";
  import "maplibre-gl/dist/maplibre-gl.css";
  import "./map.css";
  import { ForecastGridLayer } from "../map-grid-layer";
  import { DEVELOPMENT_GRID, findGridCell, gridBounds, gridCellText, gridSpacing, sanitizeGrid, validCoordinates } from "../map-grid";
  import type { BaseId, GridCell, MapSpot, MapViewError, SourceId } from "../map-grid";
  import { drawnRegionBounds, splitRegionBounds, validRegionBounds, type RegionBounds } from "../map-region";
  import { loadRegionDrawing } from "../map-region-draw";

  setWorkerUrl(mapLibreWorkerUrl);

  interface MapViewProps {
    onPick?: ((lat: number, lon: number) => void) | null;
    /** Called after each successful initialization/retry, not after tile loading. */
    onReady?: (() => void) | null;
    onError?: ((error: MapViewError) => void) | null;
    onGridSelect?: ((cell: GridCell) => void) | null;
    tdtKey?: string;
    dark?: boolean;
    mini?: boolean;
    initialSource?: SourceId;
    /** Reactive desired source/base; user controls still work between prop changes. */
    source?: SourceId;
    base?: BaseId;
    /** Initial AND current desired observation point. Prop changes recenter the map. */
    spot?: MapSpot | null;
    /** Explicit no-network map-object path. Does not claim tiles loaded. */
    offline?: boolean;
    showSourceControl?: boolean;
    drawing?: boolean;
    selectionBounds?: RegionBounds | null;
    onBounds?: ((bounds: RegionBounds) => void) | null;
    onDrawEnd?: (() => void) | null;
  }

  let {
    onPick = null, onReady = null, onError = null, onGridSelect = null,
    tdtKey: keyProp = "", dark: darkProp = false, mini: miniProp = false,
    initialSource = "tianditu", source: sourceProp, base: baseProp = "imagery",
    spot = null, offline: offlineProp = false, showSourceControl = true,
    drawing = false, selectionBounds = null, onBounds = null, onDrawEnd = null,
  }: MapViewProps = $props();

  const CHINA_BBOX = { west: 73.4, east: 135.2, south: 3.5, north: 53.7 };
  const DEFAULT_SPOT = { lat: 30.2741, lon: 120.1551, name: "杭州" };
  function inChina(lat: number, lon: number): boolean {
    return lon >= CHINA_BBOX.west && lon <= CHINA_BBOX.east && lat >= CHINA_BBOX.south && lat <= CHINA_BBOX.north;
  }
  const TDT_SUBDOMAINS = ["0", "1", "2", "3", "4", "5", "6", "7"];
  const TDT_ATTR = '&copy; <a href="https://www.tianditu.gov.cn/" target="_blank">天地图</a>';
  const OFM_ATTR = '<a href="https://openfreemap.org/" target="_blank">OpenFreeMap</a> · <a href="https://openmaptiles.org/" target="_blank">OpenMapTiles</a> · &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors';
  const TDT_TYPES: Record<BaseId, Array<[string, number]>> = {
    imagery: [["img_w", 1], ["cia_w", 2]], vector: [["vec_w", 1], ["cva_w", 2]], terrain: [["ter_w", 1], ["cta_w", 2]],
  };
  const SOURCE_LABEL: Record<SourceId, string> = { openfreemap: "OpenFreeMap", tianditu: "天地图" };
  const BASE_LABEL: Record<BaseId, string> = { imagery: "影像", vector: "矢量", terrain: "地形" };
  const SOURCES: Array<{ id: SourceId; label: string }> = [{ id: "tianditu", label: "天地图" }, { id: "openfreemap", label: "OpenFreeMap" }];
  const BASES: Array<{ id: BaseId; label: string }> = [{ id: "imagery", label: "影像" }, { id: "vector", label: "矢量" }, { id: "terrain", label: "地形" }];

  let mapEl: HTMLDivElement;
  let map: L.Map | null = null;
  let marker: L.Marker | null = null;
  let activeBase: L.LayerGroup | null = null;
  let detachVectorEvents: (() => void) | null = null;
  let gridLayer: ForecastGridLayer | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let source = $state<SourceId>("tianditu");
  let baseName = $state<BaseId>("imagery");
  let dark = $state(false), mini = $state(false), tdtKey = $state("");
  let fixtureOffline = $state(false);
  let debug = $state(false);
  const offline = $derived(offlineProp || fixtureOffline);
  let ready = $state(false);
  let initError = $state("");
  let tileError = $state("");
  let gridError = $state("");
  let tileState = $state("尚未请求瓦片");
  let center = $state<{ lat: number; lng: number } | null>(null);
  let markerPos = $state<{ lat: number; lon: number } | null>(null);
  let spotName = $state("杭州");
  let statusBase = $state("单击地图放置观测点 · 可拖动标记微调");
  let toastText = $state("");
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  let lastInChina: boolean | null = null;
  let tileErrorCount = 0, tileErrorLast = 0;
  let layerGeneration = 0;
  let failNextInitialization = false;
  let gridMode = $state(false);
  let gridCells = $state<GridCell[]>([]);
  let spacing = 0.25;
  let selectedCell = $state<GridCell | null>(null);
  // Latest intent, rather than an unbounded queue. Survives failed initialization.
  type ViewIntent = { kind: "view"; lat: number; lon: number; zoom: number } |
    { kind: "bounds"; bounds: L.LatLngBoundsExpression };
  let viewIntent: ViewIntent | null = null;
  let pendingFocus: { lat: number; lon: number } | null = null;
  let drawHandler: L.Draw.Rectangle | null = null;
  let selectionLayer: L.LayerGroup | null = null;
  let drawingMode = $state(false);
  let drawError = $state("");
  let desiredDrawing = false;
  let drawGeneration = 0;
  let suppressPickUntil = 0;
  let desiredBounds: RegionBounds | null = null;

  const needsKey = $derived(!offline && source === "tianditu" && !tdtKey);
  const overseasTdt = $derived(source === "tianditu" && center !== null && !inChina(center.lat, center.lng));
  const coordText = $derived.by(() => {
    if (!markerPos) return "";
    const { lat, lon } = markerPos;
    return `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(4)}°${lon >= 0 ? "E" : "W"}`;
  });
  const layerHint = $derived(SOURCE_LABEL[source] + (source === "openfreemap" ? " · 矢量" : ` · ${BASE_LABEL[baseName]}`));
  const effectiveBase = $derived(baseName);
  const mapTintClass = $derived(source === "openfreemap" ? "" : !dark ? effectiveBase !== "imagery" ? "tint-muted" : "" : effectiveBase === "imagery" ? "tint-dark-img" : "tint-dark");

  // Track props only, not reads inside imperative setters. A local click must not
  // reapply an unchanged prop and undo the user's selection.
  $effect(() => { const value = darkProp; untrack(() => fsSetTheme(value)); });
  $effect(() => { const value = keyProp; untrack(() => fsSetKey(value)); });
  $effect(() => { const value = miniProp; untrack(() => fsSetMini(value)); });
  $effect(() => { const value = sourceProp ?? initialSource; untrack(() => fsSetSource(value)); });
  $effect(() => { const value = baseProp; untrack(() => fsSetBase(value)); });
  $effect(() => {
    const lat = spot?.lat, lon = spot?.lon, name = spot?.name;
    if (lat !== undefined && lon !== undefined) untrack(() => fsSetView(lat, lon, map?.getZoom() ?? 11, name ?? "观测点"));
  });
  $effect(() => { const value = offline; untrack(() => { if (map) selectBase(); }); });
  $effect(() => { const value = drawing; untrack(() => { void setDrawing(value); }); });
  $effect(() => {
    const value = selectionBounds;
    untrack(() => { desiredBounds = validRegionBounds(value) ? { ...value } : null; renderSelection(); });
  });

  function renderSelection() {
    selectionLayer?.remove(); selectionLayer = null;
    if (!map || !desiredBounds) return;
    const rectangles = splitRegionBounds(desiredBounds).map(({ west, south, east, north }) =>
      L.rectangle([[south, west], [north, east]], {
        color: "var(--fs-teal)", weight: 2, fillOpacity: .12, interactive: false,
        className: "firesky-region-selection",
      }));
    if (rectangles.length) selectionLayer = L.layerGroup(rectangles).addTo(map);
  }
  async function setDrawing(value: boolean) {
    desiredDrawing = value && !mini;
    const generation = ++drawGeneration;
    if (!desiredDrawing) {
      drawHandler?.disable();
      drawingMode = false;
      return;
    }
    if (!map || !ready) return;
    const owner = map;
    drawingMode = true; drawError = "";
    try {
      await loadRegionDrawing();
      if (generation !== drawGeneration || !desiredDrawing || owner !== map) return;
      // The plugin is lazy-loaded after this map's init hooks have already run.
      if (!("touchExtend" in owner)) {
        const pluginMap = L.Map as typeof L.Map & { TouchExtend: typeof L.Handler };
        owner.addHandler("touchExtend", pluginMap.TouchExtend);
      }
      drawHandler ??= new L.Draw.Rectangle(owner as L.DrawMap, {
        repeatMode: false, showArea: false,
        shapeOptions: { color: "var(--fs-teal)", weight: 2, fillOpacity: .12, interactive: false },
      });
      owner.closePopup();
      drawHandler.enable();
    } catch {
      if (generation !== drawGeneration || owner !== map) return;
      desiredDrawing = false; drawingMode = false;
      drawError = "范围绘制暂不可用，请重试或编辑区域坐标";
      onDrawEnd?.();
    }
  }
  function completeDrawing(event: L.LeafletEvent) {
    const created = event as L.DrawEvents.Created;
    if (created.layerType !== "rectangle" || !(created.layer instanceof L.Rectangle)) return;
    const rectangle = created.layer.getBounds();
    const rawBounds = {
      west: rectangle.getWest(), south: rectangle.getSouth(), east: rectangle.getEast(), north: rectangle.getNorth(),
    };
    const ambiguousWideRectangle = rawBounds.east - rawBounds.west > 180;
    const bounds = ambiguousWideRectangle ? null : drawnRegionBounds(rawBounds);
    suppressPickUntil = Date.now() + 250;
    if (!bounds) {
      drawError = ambiguousWideRectangle
        ? "绘制范围超过 180°，无法安全判断跨日界线方向；请编辑西界和东界"
        : "范围无效，请重新绘制或编辑区域坐标";
      return;
    }
    desiredBounds = bounds;
    renderSelection();
    onBounds?.(bounds);
  }
  function endDrawing() {
    desiredDrawing = false; drawingMode = false;
    suppressPickUntil = Date.now() + 250;
    onDrawEnd?.();
  }
  function cancelDrawing(event: KeyboardEvent) {
    if (event.key !== "Escape" || (!desiredDrawing && !drawingMode)) return;
    event.preventDefault(); event.stopPropagation();
    void setDrawing(false);
    onDrawEnd?.();
  }

  function buildLayers(): L.TileLayer[] {
    const c = map!.getCenter(), maxNative = inChina(c.lat, c.lng) ? 18 : 14;
    return TDT_TYPES[baseName].map(([type, zIndex]) => L.tileLayer(
      `https://t{s}.tianditu.gov.cn/DataServer?T=${type}&x={x}&y={y}&l={z}&tk=${encodeURIComponent(tdtKey)}`,
      { subdomains: TDT_SUBDOMAINS, maxZoom: 18, maxNativeZoom: maxNative, zIndex, attribution: TDT_ATTR },
    ));
  }

  function selectBase(name?: BaseId) {
    if (name && Object.hasOwn(BASE_LABEL, name)) baseName = name;
    if (!map) return;
    const generation = ++layerGeneration;
    detachVectorEvents?.(); detachVectorEvents = null;
    activeBase?.remove();
    activeBase = null;
    tileErrorCount = 0; tileErrorLast = 0; tileError = "";
    const c = map.getCenter();
    center = c;
    if (source === "tianditu" && !inChina(c.lat, c.lng)) baseName = "imagery";
    lastInChina = inChina(c.lat, c.lng);
    syncControls();
    if (offline) { tileState = "离线对象测试 · 未请求/未加载底图瓦片"; return; }
    if (needsKey) { tileState = "未配置 Key · 未请求瓦片"; return; }
    if (source === "openfreemap") {
      tileState = "正在请求 OpenFreeMap 矢量底图";
      try {
        const layer = maplibreGL({
          style: `https://tiles.openfreemap.org/styles/${dark ? "dark" : "liberty"}`,
          attributionControl: { customAttribution: OFM_ATTR },
        });
        const owner = map;
        const adapter = layer as L.MaplibreGL & {
          _transitionEnd: () => void;
          _resizeContainer: () => void;
          _zoomEnd: () => void;
        };
        // Adapter 0.1.4 schedules transition callbacks past removal, and its
        // cleanup assumes WebGL initialization succeeded. Keep both paths safe.
        adapter._transitionEnd = () => {
          L.Util.requestAnimFrame(() => {
            const vectorMap = layer.getMaplibreMap();
            if (!vectorMap || !owner.hasLayer(layer)) return;
            const zoom = owner.getZoom(), mapCenter = owner.getCenter();
            const offset = owner.latLngToContainerPoint(owner.getBounds().getNorthWest());
            adapter._resizeContainer();
            L.DomUtil.setTransform(layer.getCanvas(), offset, 1);
            vectorMap.once("moveend", () => {
              if (layer.getMaplibreMap() && owner.hasLayer(layer)) adapter._zoomEnd();
            });
            vectorMap.jumpTo({ center: [mapCenter.lng, mapCenter.lat], zoom: zoom - 1 });
          });
        };
        const remove = layer.onRemove;
        layer.onRemove = function (ownerMap: L.Map) {
          if (layer.getMaplibreMap()) remove.call(this, ownerMap);
          else layer.getContainer()?.remove();
          return this;
        };
        activeBase = L.layerGroup([layer]);
        activeBase.addTo(map);
        const currentVectorMap = layer.getMaplibreMap();
        const onVectorError = () => {
          if (generation !== layerGeneration) return;
          tileErrorCount += 1;
          tileState = "矢量底图加载失败";
          tileError = "OpenFreeMap 底图加载失败，请检查网络后重试；仍可选点";
          onError?.({ kind: "tiles", message: tileError, count: tileErrorCount });
        };
        const onVectorLoad = () => {
          if (generation === layerGeneration && !tileErrorCount) tileState = "OpenFreeMap 样式已加载";
        };
        const onVectorSource = (event: { isSourceLoaded?: boolean }) => {
          if (generation === layerGeneration && event.isSourceLoaded && !tileErrorCount) tileState = "已收到部分矢量数据（非完整覆盖保证）";
        };
        currentVectorMap.on("error", onVectorError);
        currentVectorMap.on("load", onVectorLoad);
        currentVectorMap.on("sourcedata", onVectorSource);
        detachVectorEvents = () => {
          currentVectorMap.off("error", onVectorError);
          currentVectorMap.off("load", onVectorLoad);
          currentVectorMap.off("sourcedata", onVectorSource);
        };
      } catch {
        activeBase?.remove(); activeBase = null;
        tileState = "矢量底图初始化失败";
        tileError = "无法加载 OpenFreeMap，请检查 WebGL 支持与网络后重试；仍可选点";
        onError?.({ kind: "tiles", message: tileError });
      }
      return;
    }
    tileState = "正在请求瓦片（READY 不代表瓦片加载成功）";
    const layers = buildLayers();
    // Leaflet TileLayer emits tileerror, not Map. Attach before addTo starts IO.
    for (const layer of layers) {
      layer.on("tileerror", () => {
        if (generation !== layerGeneration) return;
        tileErrorCount += 1;
        tileError = `底图瓦片加载失败（${tileErrorCount} 次）· 请检查网络或天地图 Key；仍可选点`;
        tileState = "瓦片加载失败";
        if (Date.now() - tileErrorLast >= 2000) {
          tileErrorLast = Date.now();
          onError?.({ kind: "tiles", message: "底图瓦片加载失败；请检查网络或 Key", count: tileErrorCount });
        }
      });
      layer.on("tileload", () => { if (generation === layerGeneration && !tileErrorCount) tileState = "已收到部分底图瓦片（非完整覆盖保证）"; });
    }
    activeBase = L.layerGroup(layers).addTo(map);
    layers.forEach((layer) => layer.bringToBack());
  }

  function syncControls() {
    if (source === "openfreemap") statusBase = "OpenFreeMap 矢量底图 · 单击地图放置观测点";
    else if (!tdtKey) statusBase = "未配置天地图 Key · 请在设置中配置后加载底图";
    else if (overseasTdt) statusBase = "境外天地图仅提供影像（约 9.5m）· 其他底图请在设置中选择";
    else statusBase = "单击地图放置观测点 · 可拖动标记微调";
  }
  function setSource(name: SourceId) {
    if (!Object.hasOwn(SOURCE_LABEL, name)) return;
    source = name;
    selectBase();
  }
  function markerIcon(): L.DivIcon {
    return L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 27], html: '<div class="fs-marker"><div class="halo"></div><div class="drop"></div><div class="core"></div><div class="ring"></div></div>' });
  }
  function setMarker(lat: number, lon: number) {
    if (!validCoordinates(lat, lon)) return;
    markerPos = { lat, lon };
    if (gridMode) return;
    if (!marker && map) {
      marker = L.marker([lat, lon], { icon: markerIcon(), draggable: true, zIndexOffset: 1000 }).addTo(map);
      marker.on("dragend", () => { const p = marker!.getLatLng(); emitPick(p.lat, p.lng); });
    } else marker?.setLatLng([lat, lon]);
  }
  function emitPick(lat: number, lon: number) {
    if (!validCoordinates(lat, lon)) return;
    setMarker(lat, lon);
    onPick?.(lat, lon);
    toast(`已选点 · ${lat.toFixed(3)}, ${lon.toFixed(3)}`);
  }
  function toast(text: string) {
    toastText = text;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toastText = ""), 2200);
  }

  function applyView() {
    if (!map || !viewIntent) return;
    if (viewIntent.kind === "view") {
      const { lat, lon, zoom } = viewIntent;
      map.setView([lat, lon], Math.max(0, Math.min(19, zoom)), { animate: false });
    } else map.fitBounds(viewIntent.bounds, { padding: [24, 24], animate: false });
  }
  function inspectGrid(lat: number, lon: number) {
    const cell = findGridCell(gridCells, spacing, lat, lon);
    if (!cell || !map) return;
    selectedCell = cell;
    L.popup().setLatLng([cell[0], cell[1]]).setContent(gridCellText(cell)).openOn(map);
    onGridSelect?.(cell);
  }
  function renderGrid() {
    if (!map || !gridMode) return;
    marker?.remove(); marker = null;
    gridLayer ??= new ForecastGridLayer(() => {
      if (gridError) return;
      gridError = "无法创建网格画布；请重试或在结果列表中检查单元数据";
      onError?.({ kind: "grid", message: gridError });
    });
    gridLayer.setData(gridCells, spacing);
    if (!map.hasLayer(gridLayer)) gridLayer.addTo(map);
  }

  /** All fs* setters retain latest intent even before READY. */
  export function fsSetMini(on: boolean) {
    mini = !!on;
    if (!map) return;
    if (mini) map.scrollWheelZoom.disable(); else map.scrollWheelZoom.enable();
    map.invalidateSize({ animate: false });
  }
  export function fsSetTheme(isDark: boolean) {
    const next = !!isDark;
    if (dark === next) return;
    dark = next;
    if (source === "openfreemap" && map) selectBase();
  }
  export function fsSetKey(key: string) {
    const next = (key || "").trim();
    if (next === tdtKey) return;
    tdtKey = next;
    selectBase();
  }
  export function fsSetSource(name: SourceId) { setSource(name); }
  export function fsSetBase(name: BaseId) { selectBase(name); }
  export function fsSetView(lat: number, lon: number, zoom: number, name?: string) {
    if (!validCoordinates(lat, lon)) return;
    viewIntent = { kind: "view", lat, lon, zoom: Number.isFinite(zoom) ? zoom : map?.getZoom() ?? 11 };
    pendingFocus = null;
    setMarker(lat, lon);
    if (name !== undefined) spotName = name || "观测点";
    applyView();
  }
  export function fsSetName(name: string, status?: string) {
    spotName = name || "观测点";
    if (status) statusBase = status;
  }
  export function fsFitBounds(west: number, south: number, east: number, north: number) {
    if (!validCoordinates(south, west) || !validCoordinates(north, east) || south > north || west > east) return;
    viewIntent = { kind: "bounds", bounds: [[south, west], [north, east]] };
    pendingFocus = null;
    applyView();
  }
  export function fsInvalidate() { map?.invalidateSize({ animate: false }); }
  /** Tuple cells OR {lat, lon, peak_quality_index} records; no top-N cap. */
  export function fsSetGrid(cells: unknown, cellSpacing = 0.25) {
    gridCells = sanitizeGrid(cells); spacing = gridSpacing(cellSpacing); gridMode = true;
    selectedCell = null; pendingFocus = null; gridError = "";
    map?.closePopup();
    const bounds = gridBounds(gridCells, spacing);
    if (bounds) viewIntent = { kind: "bounds", bounds };
    renderGrid(); applyView();
  }
  export function fsClearGrid() {
    gridMode = false; gridCells = []; selectedCell = null; pendingFocus = null; gridError = "";
    gridLayer?.setData([], spacing); gridLayer?.remove(); map?.closePopup();
    if (markerPos) setMarker(markerPos.lat, markerPos.lon);
  }
  export function fsFocusGridCell(lat: number, lon: number) {
    if (!validCoordinates(lat, lon)) return;
    viewIntent = { kind: "view", lat, lon, zoom: Math.max(map?.getZoom() ?? 8, 8) };
    pendingFocus = { lat, lon };
    applyView();
    if (gridMode) inspectGrid(lat, lon);
  }
  export function fsIsReady(): boolean { return ready; }
  export function fsRetry() {
    if (map && ready) { gridError = ""; selectBase(); renderGrid(); return; }
    initialize();
  }

  function disposeMap() {
    ready = false;
    ++drawGeneration;
    drawHandler?.disable(); drawHandler = null;
    drawingMode = false;
    selectionLayer = null;
    resizeObserver?.disconnect(); resizeObserver = null;
    ++layerGeneration;
    detachVectorEvents?.(); detachVectorEvents = null;
    map?.remove(); map = null; marker = null; activeBase = null;
    // A constructor that throws can leave Leaflet's stamp on its container.
    // Use a fresh inner container on retry instead of mutating private IDs.
    mapEl?.replaceChildren();
  }
  function initialize() {
    if (!mapEl) return;
    initError = "";
    try {
      disposeMap();
      if (import.meta.env.DEV && failNextInitialization) {
        failNextInitialization = false;
        throw new Error("development initialization failure fixture");
      }
      const point = markerPos ?? DEFAULT_SPOT;
      const container = L.DomUtil.create("div", "fs-leaflet-host", mapEl);
      container.style.width = "100%"; container.style.height = "100%";
      map = L.map(container, {
        center: [point.lat, point.lon], zoom: 11, minZoom: 1, maxZoom: 19,
        maxBounds: [[-85.05, -Infinity], [85.05, Infinity]], maxBoundsViscosity: 1,
        zoomControl: true, scrollWheelZoom: !mini, attributionControl: true,
        // A persistent map can be hidden mid-transition when Settings opens.
        // Instant zoom keeps its viewport settled before the container disappears.
        zoomAnimation: false,
      });
      map.attributionControl.setPrefix("");
      L.control.scale({ imperial: false, maxWidth: 110, position: "bottomright" }).addTo(map);
      map.on("click", (e: L.LeafletMouseEvent) => {
        if (drawingMode || Date.now() < suppressPickUntil) return;
        if (gridMode) inspectGrid(e.latlng.lat, e.latlng.lng);
        else emitPick(e.latlng.lat, e.latlng.lng);
      });
      map.on("draw:created", completeDrawing);
      map.on("draw:drawstop", endDrawing);
      map.on("moveend", () => {
        if (!map) return;
        center = map.getCenter();
        if (source === "tianditu" && !needsKey && lastInChina !== null && inChina(center.lat, center.lng) !== lastInChina) selectBase();
      });
      setMarker(point.lat, point.lon);
      applyView(); selectBase(); renderGrid(); renderSelection();
      if (pendingFocus && gridMode) inspectGrid(pendingFocus.lat, pendingFocus.lon);
      resizeObserver = new ResizeObserver(() => fsInvalidate());
      resizeObserver.observe(mapEl);
      ready = true;
      if (desiredDrawing) void setDrawing(true);
    } catch {
      // Do not expose raw Leaflet errors: tile URLs can contain a browser Key.
      disposeMap();
      initError = "地图对象初始化失败 · 可重试；配置、观测点和网格将保留";
      onError?.({ kind: "initialization", message: initError });
      return;
    }
    onReady?.();
  }
  function loadFixture() {
    fixtureOffline = true;
    // Remove existing remote layers synchronously, before drawing synthetic data.
    selectBase();
    fsSetGrid(DEVELOPMENT_GRID);
  }
  onMount(() => {
    // Local QA path: ?mapOffline=1 suppresses tile requests from the first build.
    const params = new URLSearchParams(window.location.search);
    fixtureOffline = import.meta.env.DEV && params.get("mapOffline") === "1";
    debug = import.meta.env.DEV && params.get("mapDebug") === "1";
    window.addEventListener("keydown", cancelDrawing);
    initialize();
  });
  onDestroy(() => { window.removeEventListener("keydown", cancelDrawing); if (toastTimer) clearTimeout(toastTimer); disposeMap(); });
</script>

<div class="fs-map-root" class:dark class:mini data-map-state={initError ? "error" : ready ? "ready" : "loading"} data-grid-count={gridCells.length} data-drawing={drawingMode}>
  <div class="map {mapTintClass}" bind:this={mapEl}></div>
  {#if drawError}<p class="draw-error" role="alert">{drawError}</p>{/if}

  {#if showSourceControl}
  <div class="fs-panel fs-layers">
    <h4>底图 <span>{layerHint}</span></h4>
    {#if showSourceControl}<div class="fs-seg" role="group" aria-label="地图源">
      {#each SOURCES as s (s.id)}
        <button class:on={source === s.id} onclick={() => setSource(s.id)}>{s.label}</button>
      {/each}
    </div>{/if}
    {#if showSourceControl && source === "tianditu"}
      <div class="fs-seg" role="group" aria-label="底图类型">
        {#each BASES as b (b.id)}
          <button class:on={baseName === b.id} onclick={() => selectBase(b.id)} disabled={overseasTdt && b.id !== "imagery"}>{b.label}</button>
        {/each}
      </div>
    {/if}
    <p class="fs-warn" hidden={source !== "tianditu"}>需要境内合规卫星影像请选择「天地图」，由宿主注入自有 Key。</p>
  </div>
  {/if}

  <div class="fs-panel fs-status" aria-live="polite">
    {#if gridMode}
      <b>区域网格 · {gridCells.length} 个返回单元</b><br />
      <span>原始单元中心 · 无插值 / 无截断 · 灰斜线为缺测，0 为有效数值</span><br />
      <span>{selectedCell ? gridCellText(selectedCell) : "单击网格查看数值；空白不代表 0"}</span>
    {:else}
      <span class="dot">●</span> <b>{spotName}</b> · <span class="fs-coord">{coordText}</span><br />
      <span>{statusBase}</span>
    {/if}
  </div>

  <div class="fs-panel fs-health" aria-live="polite" hidden={ready && !initError && !tileError && !gridError && (!debug || mini)}>
    <span>{ready ? "READY · 地图对象已就绪" : initError ? "地图错误" : "地图对象初始化中"} · {tileState}</span>
    {#if initError || tileError || gridError}
      <p role="alert">{initError || tileError || gridError}</p>
      <button onclick={fsRetry}>重试地图 / 瓦片</button>
    {/if}
  </div>

  {#if debug && !mini}
    <details class="fs-panel fs-fixture">
      <summary>开发测试 · 合成网格（非预报）</summary>
      <p>仅开发构建可见。加载样例后不请求任何底图瓦片；正常 / 0 / null 三格。</p>
      <button onclick={loadFixture}>离线对象 + 加载三格</button>
      <button onclick={() => { fixtureOffline = !fixtureOffline; }} disabled={offlineProp}>{offline ? "退出开发离线（将请求瓦片）" : "仅离线地图对象"}</button>
      <div class="fixture-cells">
        {#each gridCells as cell, i}
          <button onclick={() => fsFocusGridCell(cell[0], cell[1])}>选择 / 聚焦 {i + 1} · {cell[2] === null ? "null 缺测" : cell[2]}</button>
        {/each}
      </div>
      <button onclick={fsClearGrid}>清除网格 / 恢复选点</button>
      <button onclick={() => { disposeMap(); initialize(); }}>重建对象（保留配置）</button>
      <button onclick={() => { failNextInitialization = true; initialize(); }}>模拟对象失败（测试重试）</button>
    </details>
  {/if}

  {#if toastText}<div class="fs-panel fs-toast">{toastText}</div>{/if}

  {#if needsKey && !initError}
    <div class="fs-key">
      <div class="fs-key-card">
        <h3>需要配置天地图 Key</h3>
        <p>请在设置中配置天地图浏览器 Key，或切换到 OpenFreeMap。</p>
        {#if debug && !mini}
          <button onclick={loadFixture}>开发测试 · 离线合成网格（非预报）</button>
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .draw-error { position: absolute; z-index: 850; top: 12px; left: 54px; right: 12px; margin: 0; padding: 9px 12px; border: 1px solid var(--fs-border); border-radius: 6px; background: var(--fs-surface-strong); color: var(--fs-error); font-size: 11px; }
  /* map.css is a plain CSS import, so its legacy :global(...) selectors are not
     Svelte-compiled. Keep the scoped Leaflet/panel essentials here. */
  .fs-map-root .fs-panel { position: absolute; z-index: 800; color: var(--fs-ink); background: var(--fs-surface-strong); backdrop-filter: none; border: 1px solid var(--fs-border); border-radius: 8px; box-shadow: 0 2px 8px rgb(0 0 0 / .08); }
  .fs-map-root .fs-key { padding: 14px; background: color-mix(in srgb, var(--fs-surface-strong) 80%, transparent); }
  .fs-map-root .fs-key-card { width: min(430px, 100%); min-width: 0; padding: 20px; background: var(--fs-surface-strong); backdrop-filter: none; border-radius: 8px; box-shadow: 0 2px 8px rgb(0 0 0 / .08); }
  .fs-map-root.mini .fs-key-card { padding: 12px; border-radius: 8px; }
  .map.tint-dark :global(.leaflet-tile-pane) { filter: var(--fs-map-tint); }
  .map.tint-dark-img :global(.leaflet-tile-pane) { filter: var(--fs-map-tint-img); }
  .map.tint-muted :global(.leaflet-tile-pane) { filter: saturate(0.62) brightness(1.04) contrast(0.94); }
  .dark .map.tint-muted :global(.leaflet-tile-pane) { filter: saturate(0.62) brightness(0.86) contrast(1.02); }
  .map :global(.fs-marker) { position: relative; width: 30px; height: 30px; }
  .map :global(.fs-marker .halo) { position: absolute; left: 50%; top: 50%; width: 34px; height: 34px; transform: translate(-50%, -50%); border-radius: 50%; background: radial-gradient(circle, rgba(242,124,66,.3), transparent 70%); }
  .map :global(.fs-marker .drop) { position: absolute; left: 50%; top: 3px; width: 18px; height: 18px; transform: translate(-50%, 0) rotate(45deg); border-radius: 50% 50% 50% 4px; background: linear-gradient(140deg, #ffc169, #e2683f); border: 2px solid rgba(255,255,255,.95); box-shadow: 0 3px 10px rgba(120,50,20,.42); }
  .map :global(.fs-marker .core) { position: absolute; left: 50%; top: 9px; width: 5px; height: 5px; transform: translate(-50%, 0); background: #fff; border-radius: 50%; }
  .map :global(.fs-marker .ring) { position: absolute; left: 50%; top: 26px; width: 9px; height: 3px; transform: translate(-50%, 0); border-radius: 50%; background: rgba(60,40,30,.28); filter: blur(1px); }
  .mini .fs-layers, .mini .fs-status, .mini .map :global(.leaflet-control-zoom), .mini .map :global(.leaflet-control-scale) { display: none; }
  .fs-health { top: 12px; left: 52px; max-width: min(440px, calc(100% - 66px)); padding: 8px 10px; font-size: 11px; }
  .fs-health p { margin: 4px 0; }
  .fs-fixture { right: 14px; bottom: 28px; width: min(300px, calc(100% - 28px)); max-height: 45%; overflow: auto; padding: 10px; font-size: 11px; }
  .fs-fixture summary { cursor: pointer; font-weight: 650; }
  .fs-fixture p { line-height: 1.6; }
  .fs-fixture button, .fs-health button, .fs-key-card > button { margin: 4px 2px; padding: 5px 8px; border: 1px solid var(--fs-border); border-radius: 7px; background: var(--fs-glass-strong); color: var(--fs-ink); cursor: pointer; font: inherit; }
  .fixture-cells { display: flex; flex-wrap: wrap; }
  .mini .fs-health { top: 4px; left: 4px; max-width: calc(100% - 28px); padding: 3px 6px; font-size: 10px; }
  @media (max-width: 640px) {
    .fs-health { top: auto; bottom: 106px; left: 14px; max-width: calc(100% - 48px); }
    .fs-fixture { bottom: 180px; max-height: 32%; }
  }
</style>
