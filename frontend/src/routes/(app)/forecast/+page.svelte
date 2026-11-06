<script lang="ts">
  import MapView from '$lib/components/map/MapView.svelte';
  import SoftCloudLayer from '$lib/components/map/SoftCloudLayer.svelte';
  import BlockingContours from '$lib/components/map/BlockingContours.svelte';
  import Timeline from '$lib/components/map/Timeline.svelte';
  import ScoreCard from '$lib/components/forecast/ScoreCard.svelte';
  import MetricsGrid from '$lib/components/forecast/MetricsGrid.svelte';
  import QualityBreakdown from '$lib/components/forecast/QualityBreakdown.svelte';
  import TrendChart from '$lib/components/forecast/TrendChart.svelte';
  import HourlyTable from '$lib/components/forecast/HourlyTable.svelte';
  import NationalCrossSection from '$lib/components/forecast/NationalCrossSection.svelte';
  import CitySelector from '$lib/components/CitySelector.svelte';
  import CurrentLocationButton from '$lib/components/CurrentLocationButton.svelte';
  import DatePickerTrigger from '$lib/components/DatePickerTrigger.svelte';

  import { Button } from '$lib/components/ui/button';
  import { Card, CardHeader, CardContent } from '$lib/components/ui/card';
  import { Input } from '$lib/components/ui/input';
  import { Badge } from '$lib/components/ui/badge';
  import { Separator } from '$lib/components/ui/separator';
  import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '$lib/components/ui/sheet';
  import { fade, fly } from 'svelte/transition';
  import { onDestroy, onMount } from 'svelte';

  import type { ForecastPayload, ForecastRequest, MapFramePayload, ModelSource, NationalCrossSectionPayload, Phenomenon, StepPayload, UserCity } from '$lib/api/types';
  import { getNationalCrossSection, startForecast, runForecast } from '$lib/api/forecast';
  import { getForecastResult } from '$lib/api/jobs';
  import { searchLocation, getPoint } from '$lib/api/location';
  import { getMapFrame } from '$lib/api/map-frame';
  import { openSSE } from '$lib/api/client';
  import { requireStrictIsoDate, requireStrictUtcIsoTime } from '$lib/api/date-contract';
  import { CHINA_TIME_ZONE, ecmwfForecastMaxDate, formatElapsed, forecastDateOptions, gfsForecastMaxDate, isChinaDateOnOrAfterToday, refreshForecastDateSelection } from '$lib/utils/format';
  import {
    DEFAULT_LAT, DEFAULT_LON, DEFAULT_NAME, DEFAULT_TIMEZONE, DEFAULT_ELEVATION,
  } from '$lib/utils/constants';
  import { cn } from '$lib/utils/cn';
  import type { GeolocationState, LocalCoordinates } from '$lib/utils/geolocation';
  import Search from '@lucide/svelte/icons/search';
  import MapPin from '@lucide/svelte/icons/map-pin';
  import Layers from '@lucide/svelte/icons/layers';
  import ExternalLink from '@lucide/svelte/icons/external-link';

  let mobileView: 'map' | 'controls' | 'results' = 'map';

  // ─── Location state ───
  let lat = DEFAULT_LAT;
  let lon = DEFAULT_LON;
  let locationName = DEFAULT_NAME;
  let timezoneName = DEFAULT_TIMEZONE;
  let elevationM = DEFAULT_ELEVATION;
  let locationReady = true;

  // ─── Form state ───
  let dateOptions = forecastDateOptions();
  let targetDate = dateOptions[0]?.value ?? '';
  let dateRefreshTimer: number | null = null;
  let phenomenon: Phenomenon = 'dawn';
  let modelSource: ModelSource = 'gfs';
  let diagnosticRegion = '';
  let cycleOverride = '';
  let searchQuery = DEFAULT_NAME;

  // ─── Map state ───
  let mapRef: MapView | null = null;
  let currentBasemap = 'tianditu-imagery';
  let cloudField: 'total' | 'high_cloud' | 'mid_cloud' | 'low_cloud' = 'total';
  let renderMode: 'smooth' | 'grid' = 'smooth';
  let showCloud = true;
  let showBurn = true;
  let showBlock = false;
  let leafletMap: L.Map | null = null;

  // ─── Forecast state ───
  let forecast: ForecastPayload | null = null;
  let currentFrame: MapFramePayload | null = null;
  let crossSection: NationalCrossSectionPayload | null = null;
  let crossSectionLoading = false;
  let crossSectionError = '';
  let selectedTime: string | null = null;
  let crossSectionRequestSequence = 0;
  let loading = false;
  let errorMsg = '';
  let progressVisible = false;
  let progressPercent = 0;
  let progressMessage = '';
  let progressStage = '';
  let progressElapsed = 0;
  let progressLogs: string[] = [];
  let searchLoading = false;
  let forecastRequestSequence = 0;

  function isValidIanaTimezone(value: unknown): value is string {
    if (typeof value !== 'string' || value.trim() !== value || value.length === 0) return false;
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: value }).format(new Date(0));
      return true;
    } catch {
      return false;
    }
  }

  function clearForecastResult() {
    forecastRequestSequence += 1;
    crossSectionRequestSequence += 1;
    errorMsg = '';
    forecast = null;
    if (mobileView === 'results') mobileView = 'controls';
    currentFrame = null;
    crossSection = null;
    crossSectionError = '';
    crossSectionLoading = false;
    selectedTime = null;
    loading = false;
    progressVisible = false;
    progressPercent = 0;
    progressMessage = '';
    progressStage = '';
    progressElapsed = 0;
    progressLogs = [];
  }

  function maxDateForModel(source: ModelSource): string {
    if (source === 'ec') return ecmwfForecastMaxDate();
    return gfsForecastMaxDate();
  }

  function selectTargetDate(value: string) {
    let nextDate: string;
    try {
      nextDate = requireStrictIsoDate(value, '预测目标日期');
    } catch {
      errorMsg = '预测目标日期格式无效。';
      return;
    }
    if (!isChinaDateOnOrAfterToday(nextDate)) {
      errorMsg = '预测目标日期不能早于中国今天。';
      return;
    }
    const maxDate = maxDateForModel(modelSource);
    if (nextDate > maxDate) {
      errorMsg = `${sourceLabel(modelSource)} 当前可选日期至 ${maxDate}；更晚日期超出数据发布的时效范围。`;
      return;
    }
    if (nextDate === targetDate) {
      errorMsg = '';
      return;
    }
    errorMsg = '';
    targetDate = nextDate;
    cycleOverride = '';
    clearForecastResult();
  }

  function handleTargetDateInput(value: string) {
    selectTargetDate(value);
  }

  function selectModelSource(source: ModelSource) {
    if (source === modelSource) return;
    const maxDate = maxDateForModel(source);
    if (targetDate > maxDate) {
      // 切换数值模式时超出上限：自动收敛到该模式可用的最新日期
      targetDate = maxDate;
      errorMsg = `${sourceLabel(source)} 数据上限为 ${maxDate}，已自动调整目标日期。`;
    }
    modelSource = source;
    cycleOverride = '';
    clearForecastResult();
  }

  function sourceLabel(source: ModelSource): string {
    return source === 'ec' ? 'ECMWF IFS' : 'GFS';
  }

  function sourceDescription(source: ModelSource): string {
    return source === 'ec'
      ? 'EC 公共 Open Data · TCC + 压力层 RH 云柱代理 · CAMS 气溶胶'
      : 'GFS 0.25° 独立数值模式 · CAMS 气溶胶';
  }

  // ─── Handlers ───
  async function handleSearch(event: Event) {
    event.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;
    errorMsg = '';
    searchLoading = true;
    try {
      const data = await searchLocation(query);
      lat = data.lat;
      lon = data.lon;
      locationName = data.name;
      if (
        isValidIanaTimezone(data.timezone_name)
        && Number.isFinite(Number(data.elevation_m))
        && Number(data.elevation_m) >= -500
        && Number(data.elevation_m) <= 9000
      ) {
        timezoneName = diagnosticRegion === 'china' ? CHINA_TIME_ZONE : data.timezone_name;
        elevationM = Number(data.elevation_m);
        locationReady = true;
      } else {
        await handlePointSelected(data.lat, data.lon, data.name);
      }
      mapRef?.setView(data.lat, data.lon, 10);
    } catch (error: unknown) {
      errorMsg = error instanceof Error ? error.message : '搜索失败';
    } finally {
      searchLoading = false;
    }
  }

  async function handlePointSelected(
    pLat: number,
    pLon: number,
    name: string,
    authoritativeElevationM: number | null = null,
  ) {
    locationReady = false;
    try {
      const data = await getPoint(pLat, pLon);
      if (!isValidIanaTimezone(data.timezone_name)) {
        throw new Error('观测点时区无效，无法执行太阳几何预测。');
      }
      lat = data.lat; lon = data.lon;
      timezoneName = diagnosticRegion === 'china' ? CHINA_TIME_ZONE : data.timezone_name;
      elevationM = authoritativeElevationM ?? data.elevation_m;
      if (!Number.isFinite(elevationM) || elevationM < -500 || elevationM > 9000) {
        throw new Error('观测点海拔无效，无法执行地形敏感预测。');
      }
      locationName = name;
      locationReady = true;
    } catch (error: unknown) {
      locationReady = false;
      errorMsg = error instanceof Error ? error.message : '无法获取观测点时区和海拔。';
    }
  }

  async function handleCurrentLocation(coordinates: LocalCoordinates) {
    errorMsg = '';
    await handlePointSelected(coordinates.lat, coordinates.lon, '当前位置');
    if (!locationReady) throw new Error(errorMsg || '无法解析当前位置的时区和海拔。');
    searchQuery = '当前位置';
    mapRef?.setView(lat, lon, 10);
  }

  function handleGeolocationState(state: GeolocationState) {
    if (state.status === 'loading' || state.status === 'success') {
      if (state.status === 'loading') errorMsg = '';
      return;
    }
    if (state.status !== 'idle') errorMsg = state.message;
  }

  function handleCitySelect(city: UserCity) {
    if (!Number.isFinite(city.elevation_m) || city.elevation_m < -500 || city.elevation_m > 9000) {
      locationReady = false;
      errorMsg = '城市海拔无效，无法执行地形敏感预测。';
      return;
    }
    if (!isValidIanaTimezone(city.timezone_name)) {
      locationReady = false;
      errorMsg = '城市时区无效，无法执行太阳几何预测。';
      return;
    }
    lat = city.lat;
    lon = city.lon;
    locationName = city.name;
    timezoneName = diagnosticRegion === 'china' ? CHINA_TIME_ZONE : city.timezone_name;
    elevationM = city.elevation_m;
    searchQuery = city.name;
    locationReady = true;
    mapRef?.setView(city.lat, city.lon, 10);
  }

  function handleMapReady(event: { map: L.Map }) { leafletMap = event.map; }

  async function handleForecast(event: Event) {
    event.preventDefault();
    if (!locationReady) { errorMsg = '坐标时区和海拔尚未就绪。'; return; }
    errorMsg = '';
    const requestSequence = ++forecastRequestSequence;
    loading = true;
    progressVisible = true;
    progressPercent = 0; progressMessage = '提交预测任务'; progressStage = '准备'; progressElapsed = 0; progressLogs = [];

    const requestedTargetDate = targetDate;
    const body: ForecastRequest = {
      name: locationName,
      lat,
      lon,
      timezone_name: timezoneName,
      elevation_m: elevationM,
      target_date: requestedTargetDate,
      phenomenon,
      model_source: modelSource,
      satellite_source: 'off',
      forecast_mode: 'model',
      ...(modelSource === 'gfs' ? { gfs_resolution: '0p25' as const } : {}),
      ...(diagnosticRegion ? { _diagnostic_region: diagnosticRegion } : {}),
      ...(cycleOverride
        ? modelSource === 'gfs'
          ? { _gfs_cycle_override: cycleOverride }
          : modelSource === 'ec'
          ? { _ec_cycle_override: cycleOverride }
          : {}
        : {}),
    };

    try {
      let data: ForecastPayload;
      const ssePath = '/api/v1/firesky/forecasts/events';
      if (typeof EventSource !== 'undefined') {
        const job = await startForecast(body);
        updateProgress({ stage: 'connected', percent: 3, message: '已连接实时进度' }, requestSequence);
        data = await new Promise<ForecastPayload>((resolve, reject) => {
          let settled = false;
          const source = openSSE(ssePath, { job_id: job.job_id }, {
            onEvent: async (eventName, eventData: unknown) => {
              const d = eventData as Record<string, unknown>;
              if (eventName === 'progress') updateProgress(d as any, requestSequence);
              else if (eventName === 'done') {
                settled = true;
                source.close();
                updateProgress(d as any, requestSequence);
                try {
                  const result = d.result as ForecastPayload | undefined;
                  resolve(result ?? await getForecastResult<ForecastPayload>(job.job_id));
                } catch (error) {
                  reject(error);
                }
              }
              else if (eventName === 'failed') { settled = true; source.close(); updateProgress(d as any, requestSequence); reject(new Error(String(d.message || '预测失败'))); }
              else if (eventName === 'cancelled') { settled = true; source.close(); updateProgress(d as any, requestSequence); reject(new Error(String(d.message || '计算已取消'))); }
            },
            onError: () => { if (!settled) { settled = true; source.close(); reject(new Error('进度连接中断，请重试。')); } },
          });
        });
      } else {
        updateProgress({ stage: 'fallback', percent: 5, message: '当前浏览器不支持实时进度' }, requestSequence);
        data = await runForecast(body);
        updateProgress({ stage: 'done', percent: 100, message: '预测完成' }, requestSequence);
      }
      // A suspended tab can finish an older request after China-local midnight.
      // Never attach that response to the new date's trend or point view.
      if (requestSequence !== forecastRequestSequence) return;
      if (targetDate !== requestedTargetDate || data.target_date !== requestedTargetDate) {
        if (targetDate === requestedTargetDate) {
          errorMsg = `预测返回日期 ${data.target_date}，与请求日期 ${requestedTargetDate} 不一致。`;
        }
        return;
      }
      forecast = data;
      mobileView = 'results';
      currentFrame = null;
      crossSection = data.cross_section ?? null;
      if (data.cross_section_capability && !data.cross_section_capability.available) {
        crossSectionError = data.cross_section_capability.reason ?? '当前模型没有可用的太阳方向压力层剖面';
      }
      selectedTime = data.best_time_local;

      // Load initial map frame directly to ensure overlays render immediately
      if (data.map_times?.length > 0 && data.map_token) {
        const bestIdx = Math.max(0, data.map_times.findIndex(t => t === data.best_time_local));
        try {
          const frame = await getMapFrame(data.map_token, data.map_times[bestIdx]);
          if (requestSequence === forecastRequestSequence && targetDate === requestedTargetDate) currentFrame = frame;
        } catch (e) {
          console.warn('Initial frame load failed, Timeline will retry:', e);
        }
      }
    } catch (error: unknown) {
      if (requestSequence !== forecastRequestSequence) return;
      errorMsg = error instanceof Error ? error.message : '预测失败';
    } finally {
      if (requestSequence === forecastRequestSequence) loading = false;
    }
  }

  function updateProgress(
    event: { stage?: string; percent?: number; message?: string; elapsed_seconds?: number },
    requestSequence = forecastRequestSequence,
  ) {
    if (requestSequence !== forecastRequestSequence) return;
    progressPercent = Math.max(0, Math.min(100, Number(event.percent || 0)));
    progressMessage = event.message || '预测处理中';
    progressStage = event.stage || '运行中';
    if (event.elapsed_seconds != null) progressElapsed = event.elapsed_seconds;
    progressLogs = [...progressLogs.slice(-8), event.message || event.stage || '预测处理中'];
  }

  async function loadCrossSection(step: StepPayload) {
    if (!forecast?.cross_section_request) return;
    const requestSequence = ++crossSectionRequestSequence;
    crossSectionLoading = true;
    crossSectionError = '';
    const offsetMinutes = (new Date(step.valid_time_local).getTime() - new Date(forecast.anchor_local).getTime()) / 60_000;
    try {
      const section = await getNationalCrossSection(forecast.cross_section_request, lat, lon, offsetMinutes);
      if (requestSequence === crossSectionRequestSequence) crossSection = section;
    } catch (error) {
      if (requestSequence === crossSectionRequestSequence) {
        crossSectionError = error instanceof Error ? error.message : '剖面加载失败';
      }
    } finally {
      if (requestSequence === crossSectionRequestSequence) crossSectionLoading = false;
    }
  }

  async function handleTimeSelected(step: StepPayload) {
    if (!step.valid) return;
    selectedTime = step.valid_time_local;
    await loadCrossSection(step);
  }

  function handleFrameLoaded(frame: MapFramePayload, _index: number, mapToken: string) {
    if (!forecast?.map_token || forecast.map_token !== mapToken) return;
    if (!forecast.map_times.includes(frame.valid_time_local)) return;
    currentFrame = frame;
    selectedTime = frame.valid_time_local;
    const step = forecast?.steps.find((item) => item.valid_time_local === frame.valid_time_local);
    if (step?.valid) void loadCrossSection(step);
  }

  function reconcileChinaDate() {
    const refreshed = refreshForecastDateSelection(dateOptions[0]?.value, targetDate);
    const nextTargetDate = isChinaDateOnOrAfterToday(refreshed.targetDate)
      ? refreshed.targetDate
      : refreshed.options[0]?.value ?? refreshed.targetDate;
    const dateChanged = nextTargetDate !== targetDate;
    dateOptions = refreshed.options;
    targetDate = nextTargetDate;
    if (dateChanged) {
      cycleOverride = '';
      clearForecastResult();
    }
  }

  function handleBasemapChange(event: Event) {
    const target = event.target as HTMLSelectElement;
    currentBasemap = target.value;
    mapRef?.selectBaseMapPublic(currentBasemap);
  }

  onMount(() => {
    // SSR/build output can carry yesterday's calendar value across midnight.
    // Reconcile it immediately in the browser before handling deep-link dates.
    reconcileChinaDate();
    dateRefreshTimer = window.setInterval(reconcileChinaDate, 60_000);
    window.addEventListener('focus', reconcileChinaDate);
    window.addEventListener('pageshow', reconcileChinaDate);
    document.addEventListener('visibilitychange', reconcileChinaDate);
    const params = new URLSearchParams(window.location.search);
    const queryLat = Number(params.get('lat'));
    const queryLon = Number(params.get('lon'));
    // Treat the zero coordinate pair as an empty/default point, not a real
    // location, so deep links cannot replace Hangzhou with 0,0.
    if (
      !Number.isFinite(queryLat) ||
      !Number.isFinite(queryLon) ||
      (queryLat === 0 && queryLon === 0)
    ) return;
    lat = queryLat;
    lon = queryLon;
    locationName = params.get('name') || `${queryLat.toFixed(2)}, ${queryLon.toFixed(2)}`;
    searchQuery = locationName;
    const queryDate = params.get('target_date');
    const queryPhenomenon = params.get('phenomenon');
    const queryModel = params.get('model_source');
    diagnosticRegion = params.get('region') || '';
    const queryCycle = params.get('cycle');
    if (queryModel === 'gfs' || queryModel === 'ec') modelSource = queryModel;
    let queryDateAccepted = false;
    if (queryDate !== null) {
      try {
        requireStrictIsoDate(queryDate, '趋势图目标日期');
        queryDateAccepted = isChinaDateOnOrAfterToday(queryDate);
      } catch {
        queryDateAccepted = false;
      }
    }
    const refreshedDate = refreshForecastDateSelection(
      dateOptions[0]?.value,
      queryDateAccepted ? queryDate! : targetDate,
    );
    queryDateAccepted = queryDateAccepted && refreshedDate.targetDate === queryDate;
    dateOptions = refreshedDate.options;
    targetDate = refreshedDate.targetDate;
    cycleOverride = '';
    if (queryDateAccepted && queryCycle !== null) {
      try {
        cycleOverride = requireStrictUtcIsoTime(queryCycle, '趋势图模式周期');
      } catch {
        cycleOverride = '';
      }
    }
    if (queryPhenomenon === 'dawn' || queryPhenomenon === 'dusk') phenomenon = queryPhenomenon;
    const rawElevation = params.get('elevation_m');
    let queryElevation: number | null = null;
    if (rawElevation !== null) {
      const parsedElevation = Number(rawElevation);
      if (!Number.isFinite(parsedElevation) || parsedElevation < -500 || parsedElevation > 9000) {
        locationReady = false;
        errorMsg = '趋势图观测点海拔无效。';
        return;
      }
      queryElevation = parsedElevation;
    }
    void handlePointSelected(queryLat, queryLon, locationName, queryElevation);
    setTimeout(() => mapRef?.setView(queryLat, queryLon, 8), 0);
  });

  onDestroy(() => {
    if (dateRefreshTimer) clearInterval(dateRefreshTimer);
    window.removeEventListener('focus', reconcileChinaDate);
    window.removeEventListener('pageshow', reconcileChinaDate);
    document.removeEventListener('visibilitychange', reconcileChinaDate);
  });
</script>

<div class="observation-workbench glass-workspace" data-mobile-view={mobileView}>
  <nav class="workbench-views glass-strong" aria-label="工作台视图">
    {#each [{ id: 'map', label: '地图' }, { id: 'controls', label: '参数' }, { id: 'results', label: '结果' }] as view}
      <button type="button" aria-pressed={mobileView === view.id} disabled={view.id === 'results' && !forecast}
        onclick={() => mobileView = view.id as typeof mobileView}>{view.label}</button>
    {/each}
  </nav>
  <!-- ═══ Sidebar ═══ -->
  <aside class="workbench-rail glass-rail" aria-label="观测参数">
    <div class="p-4 space-y-4">
      <!-- Location Search -->
      <form class="flex w-full min-w-0 max-w-full items-center gap-2" onsubmit={handleSearch}>
        <Input bind:value={searchQuery} placeholder="输入地点搜索..." class="h-9 min-w-0 flex-1 text-sm" />
        <div class="flex shrink-0 items-center gap-2" aria-label="地点操作">
          <Button type="submit" size="icon-lg" variant="outline" disabled={searchLoading} class="h-9 w-9 shrink-0 p-0" aria-label="搜索地点" title="搜索地点">
            <Search class="w-4 h-4" />
          </Button>
          <CurrentLocationButton class="h-9 w-9 shrink-0" onlocate={handleCurrentLocation} onstatechange={handleGeolocationState} />
        </div>
      </form>

      <!-- Saved Cities -->
      <CitySelector {lat} {lon} name={locationName} timezoneName={timezoneName} elevationM={elevationM} onselect={handleCitySelect} />

      <!-- Coordinates -->
      <Card class="border-border/80">
        <CardHeader class="py-3 px-4">
          <div class="flex items-center gap-2 text-sm font-medium">
            <MapPin class="w-4 h-4 text-primary" />
            观测点
          </div>
        </CardHeader>
        <CardContent class="px-4 pb-4 pt-0 space-y-3">
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label for="forecast-lat" class="text-xs text-muted-foreground">纬度</label>
              <Input id="forecast-lat" type="number" bind:value={lat} min="-90" max="90" step="0.000001" class="h-8 text-sm font-mono-data"
                onchange={() => handlePointSelected(lat, lon, locationName)} />
            </div>
            <div>
              <label for="forecast-lon" class="text-xs text-muted-foreground">经度</label>
              <Input id="forecast-lon" type="number" bind:value={lon} min="-180" max="180" step="0.000001" class="h-8 text-sm font-mono-data"
                onchange={() => handlePointSelected(lat, lon, locationName)} />
            </div>
          </div>
          <div class="text-xs text-muted-foreground border-t border-border/50 pt-2">
            {locationName} · {timezoneName} · 海拔 {Math.round(elevationM)} m
          </div>
        </CardContent>
      </Card>

      <!-- Forecast Form -->
      <form class="space-y-3" onsubmit={handleForecast}>
        <div>
          <div class="text-xs text-muted-foreground mb-1.5 block">目标日期</div>
          <div class="glass-control-well grid grid-cols-3 gap-1 p-0.5 rounded-lg border">
            {#each dateOptions as opt}
              <button type="button"
                class={cn("py-2 text-sm font-medium rounded-md transition-all duration-200",
                  targetDate === opt.value
                    ? "glass-control-active text-card-foreground border"
                    : "text-muted-foreground hover:text-card-foreground")}
                onclick={() => selectTargetDate(opt.value)}>
                <span class="block text-xs text-muted-foreground">{opt.label}</span>
                <span class="block font-mono-data text-[11px]">{opt.value.slice(5)}</span>
              </button>
            {/each}
          </div>
          <div class="mt-2 grid grid-cols-[auto_minmax(0,1fr)] items-center gap-2">
            <span class="shrink-0 text-xs text-muted-foreground">更多日期</span>
            <DatePickerTrigger
              id="forecast-target-date"
              value={targetDate}
              min={dateOptions[0]?.value}
              max={maxDateForModel(modelSource)}
              active={!dateOptions.some((opt) => opt.value === targetDate)}
              onchange={handleTargetDateInput}
            />
          </div>
        </div>

        <div>
          <div class="text-xs text-muted-foreground mb-1.5 block">现象</div>
          <div class="glass-control-well grid grid-cols-2 gap-1 p-0.5 rounded-lg border">
            <button type="button"
              class={cn("py-2 text-sm font-medium rounded-md transition-all duration-200",
                phenomenon === 'dawn'
                  ? "glass-control-active text-card-foreground border"
                  : "text-muted-foreground hover:text-card-foreground")}
              onclick={() => { phenomenon = 'dawn'; cycleOverride = ''; }}>朝霞</button>
            <button type="button"
              class={cn("py-2 text-sm font-medium rounded-md transition-all duration-200",
                phenomenon === 'dusk'
                  ? "glass-control-active text-card-foreground border"
                  : "text-muted-foreground hover:text-card-foreground")}
              onclick={() => { phenomenon = 'dusk'; cycleOverride = ''; }}>晚霞</button>
          </div>
        </div>

        <div>
          <div class="text-xs text-muted-foreground mb-1.5 block">数值模式</div>
          <div class="glass-control-well grid grid-cols-2 gap-1 p-0.5 rounded-lg border">
            {#each [{ value: 'gfs', label: 'GFS' }, { value: 'ec', label: 'ECMWF' }] as option}
              <button type="button"
                class={cn("py-2 text-sm font-medium rounded-md transition-all duration-200",
                  modelSource === option.value
                    ? "glass-control-active text-card-foreground border"
                    : "text-muted-foreground hover:text-card-foreground")}
                onclick={() => selectModelSource(option.value as ModelSource)}>{option.label}</button>
            {/each}
          </div>
        </div>

        <!-- Submit button with sunset gradient -->
        <Button type="submit" class="w-full h-10 font-semibold firesky-gradient-bar text-white hover:opacity-90 transition-opacity border-0" disabled={loading}>
          {loading ? '预测中...' : '获取预测'}
        </Button>

        {#if progressVisible}
          <div class="glass-panel rounded-xl border p-3 space-y-2" transition:fly={{ y: 10, duration: 300 }}>
            <div class="flex justify-between items-baseline">
              <span class="text-xs font-medium truncate flex-1">{progressMessage}</span>
              <Badge variant="outline" class="text-[11px] ml-2 font-mono-data border-primary/30 text-primary">{Math.round(progressPercent)}%</Badge>
            </div>
            <div class="h-1.5 rounded-full bg-muted overflow-hidden">
              <div class="h-full rounded-full firesky-gradient-bar transition-all duration-300 ease-out"
                style="width: {progressPercent}%"></div>
            </div>
            <div class="flex justify-between text-[10px] text-muted-foreground">
              <span>{progressStage}</span>
              <span class="font-mono-data">{formatElapsed(progressElapsed)}</span>
            </div>
            {#if progressLogs.length > 0}
              <div class="border-t border-border/50 pt-1.5 max-h-20 overflow-y-auto text-[10px] text-muted-foreground space-y-0.5">
                {#each progressLogs as log}
                  <div class="truncate">{log}</div>
                {/each}
              </div>
            {/if}
          </div>
        {/if}

        {#if modelSource !== 'gfs' && !loading}
          <div class="rounded-lg border border-border/70 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground" aria-label="独立数值模式来源">
            <strong class="text-card-foreground">{sourceLabel(modelSource)} 独立预测</strong>
            <span class="block mt-0.5">{sourceDescription(modelSource)}</span>
          </div>
        {/if}
      </form>

      {#if errorMsg}
        <div class="rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" transition:fade={{ duration: 200 }}>
          {errorMsg}
        </div>
      {/if}
    </div>
  </aside>

  <!-- ═══ Main Area ═══ -->
  <div class="workbench-main">
    <div class="workbench-map relative">
      <MapView
        viewportFill
        bind:this={mapRef}
        {lat} {lon}
        onPointSelected={(e) => handlePointSelected(e.lat, e.lon, e.name)}
        onMapReady={(e) => handleMapReady(e)}
      />

      <!-- Map layer control -->
      <Sheet>
        <SheetTrigger>
          {#snippet child({ props })}
            <button {...props} aria-label="图层控制" title="图层控制" class="glass-panel-strong inline-flex items-center justify-center absolute top-3 right-3 z-[700] h-8 w-8 rounded-lg border hover:border-primary/30 transition-colors cursor-pointer">
              <Layers class="w-4 h-4" />
            </button>
          {/snippet}
        </SheetTrigger>
        <SheetContent side="right" class="w-64 p-4">
          <SheetHeader><SheetTitle>图层控制</SheetTitle></SheetHeader>
          <div class="space-y-4 mt-4">
            <div>
              <label for="forecast-basemap" class="text-xs font-medium text-muted-foreground mb-1.5 block">底图</label>
              <select id="forecast-basemap" bind:value={currentBasemap} onchange={handleBasemapChange}
                class="w-full h-8 rounded-md border border-input bg-background px-2 text-sm">
                <option value="tianditu-vector">天地图矢量</option>
                <option value="tianditu-imagery">天地图影像</option>
                <option value="osm">OpenStreetMap</option>
              </select>
            </div>
            {#if forecast?.map_token && forecast.map_times?.length}
              <Separator />
              <div>
                <label for="forecast-cloud-field" class="text-xs font-medium text-muted-foreground mb-1.5 block">云层显示</label>
                <select id="forecast-cloud-field" bind:value={cloudField} class="w-full h-8 rounded-md border border-input bg-background px-2 text-sm">
                  <option value="total">综合云量</option>
                  <option value="high_cloud">高云</option>
                  <option value="mid_cloud">中云</option>
                  <option value="low_cloud">低云</option>
                </select>
              </div>
              <div>
                <label for="forecast-render-mode" class="text-xs font-medium text-muted-foreground mb-1.5 block">渲染方式</label>
                <select id="forecast-render-mode" bind:value={renderMode} class="w-full h-8 rounded-md border border-input bg-background px-2 text-sm">
                  <option value="smooth">连续云场</option>
                  <option value="grid">原始格点</option>
                </select>
              </div>
              <Separator />
              <div class="grid grid-cols-2 gap-3 text-sm">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" bind:checked={showCloud} class="accent-primary w-3.5 h-3.5" /> 云图
                </label>
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" bind:checked={showBurn} class="accent-primary w-3.5 h-3.5" /> 可烧云
                </label>
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" bind:checked={showBlock} class="accent-primary w-3.5 h-3.5" /> 挡光云
                </label>
              </div>
            {/if}
          </div>
        </SheetContent>
      </Sheet>

      {#if leafletMap && currentFrame}
        <SoftCloudLayer map={leafletMap} cells={currentFrame.cells} field={cloudField} {showCloud} {showBurn} {renderMode} />
        <BlockingContours map={leafletMap} cells={currentFrame.cells} show={showBlock} />
      {/if}

      {#if forecast && forecast.model_source !== 'gfs' && !forecast.map_token}
        <div class="mx-3 mb-3 rounded-lg border border-border/70 bg-card/85 px-3 py-2 text-xs text-muted-foreground backdrop-blur-sm" role="status">
          {forecast.map_capability?.reason ?? `${sourceLabel(forecast.model_source)} 当前没有可用的区域云场地图。`}
        </div>
      {/if}
    </div>

    <div class="workbench-results">
    {#if forecast?.map_token && forecast.map_times?.length}
      <Timeline {forecast} timeZone={CHINA_TIME_ZONE} {selectedTime} onFrameLoaded={handleFrameLoaded} onTimeSelected={handleTimeSelected} />
    {:else if forecast}
      <Timeline {forecast} timeZone={CHINA_TIME_ZONE} {selectedTime} onTimeSelected={handleTimeSelected} />
    {/if}

    <!-- Results section with entrance animation -->
    {#if forecast}
      <section class="p-4 md:p-6 space-y-5 max-w-5xl mx-auto" transition:fly={{ y: 20, duration: 400, easing: (t) => 1 - Math.pow(1 - t, 3) }}>

        <ScoreCard data={forecast} />
        <details class="diagnostic-details">
          <summary>气象参数与评分明细</summary>
          <div class="space-y-4 pt-4">
            <MetricsGrid data={forecast} displayTimeZone={CHINA_TIME_ZONE} />
            <QualityBreakdown data={forecast} />
          </div>
        </details>
        <TrendChart steps={forecast.steps} timeZone={CHINA_TIME_ZONE} targetDate={forecast.target_date} cycleTimeUtc={forecast.cycle_time_utc} modelSource={forecast.model_source} {selectedTime} onTimeSelected={handleTimeSelected} />
        {#if crossSection || crossSectionLoading || crossSectionError}
          <section class="glass-panel min-h-[320px] overflow-hidden rounded-lg border">
            <NationalCrossSection section={crossSection} loading={crossSectionLoading} error={crossSectionError} />
          </section>
        {/if}
        <HourlyTable steps={forecast.steps} bestTime={forecast.best_time_local} {selectedTime} timeZone={CHINA_TIME_ZONE} onTimeSelected={handleTimeSelected} />
        {#if forecast.html_report_url}
          <Button variant="outline" href={forecast.html_report_url} target="_blank" rel="noopener" class="gap-2 border-primary/20 hover:border-primary/50 transition-colors">
            打开完整诊断报告 <ExternalLink class="w-3.5 h-3.5" />
          </Button>
        {/if}
      </section>
    {/if}
    </div>
  </div>
</div>
