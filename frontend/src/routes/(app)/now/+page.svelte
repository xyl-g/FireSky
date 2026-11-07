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
  import SatelliteVerticalProfile from '$lib/components/forecast/SatelliteVerticalProfile.svelte';
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

  import type { ForecastPayload, MapFramePayload, NationalCrossSectionPayload, Phenomenon, StepPayload, UserCity } from '$lib/api/types';
  import { getNationalCrossSection, startNowcast, runNowcast } from '$lib/api/forecast';
  import { getForecastResult } from '$lib/api/jobs';
  import { searchLocation, getPoint } from '$lib/api/location';
  import { getMapFrame } from '$lib/api/map-frame';
  import { openSSE } from '$lib/api/client';
  import { requireStrictIsoDate } from '$lib/api/date-contract';
  import { chinaDateValue, CHINA_TIME_ZONE, formatElapsed, forecastDateOptions, refreshForecastDateSelection } from '$lib/utils/format';
  import { DEFAULT_LAT, DEFAULT_LON, DEFAULT_NAME, DEFAULT_TIMEZONE, DEFAULT_ELEVATION } from '$lib/utils/constants';
  import { cn } from '$lib/utils/cn';
  import type { GeolocationState, LocalCoordinates } from '$lib/utils/geolocation';
  import Search from '@lucide/svelte/icons/search';
  import MapPin from '@lucide/svelte/icons/map-pin';
  import Layers from '@lucide/svelte/icons/layers';
  import ExternalLink from '@lucide/svelte/icons/external-link';
  import Satellite from '@lucide/svelte/icons/satellite';

  let mobileView: 'map' | 'controls' | 'results' = 'map';

  // ─── Location state ───
  let lat = DEFAULT_LAT;
  let lon = DEFAULT_LON;
  let locationName = DEFAULT_NAME;
  let timezoneName = DEFAULT_TIMEZONE;
  let elevationM = DEFAULT_ELEVATION;
  let locationReady = true;

  // ─── Form state ───
  let phenomenon: Phenomenon = 'dusk';
  let dateOptions = forecastDateOptions();
  let targetDate = dateOptions[0]?.value ?? chinaDateValue();
  let dateRefreshTimer: number | null = null;
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
  let nowcastRequestSequence = 0;
  function nowcastBody() {
    return {
      name: locationName,
      lat,
      lon,
      timezone_name: timezoneName,
      elevation_m: elevationM,
      target_date: targetDate,
      phenomenon,
      model_source: 'satellite' as const,
      satellite_source: 'auto' as const,
      forecast_mode: 'nowcast' as const,
    };
  }

  function clearNowcastResult() {
    nowcastRequestSequence += 1;
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

  function selectTargetDate(value: string) {
    let nextDate: string;
    try {
      nextDate = requireStrictIsoDate(value, '实况目标日期');
    } catch {
      errorMsg = '实况目标日期格式无效。';
      return;
    }
    if (nextDate === targetDate) {
      errorMsg = '';
      return;
    }
    errorMsg = '';
    targetDate = nextDate;
    clearNowcastResult();
  }

  function handleTargetDateInput(value: string) {
    selectTargetDate(value);
  }

  function reconcileChinaDate() {
    const refreshed = refreshForecastDateSelection(dateOptions[0]?.value, targetDate);
    const dateChanged = refreshed.targetDate !== targetDate;
    dateOptions = refreshed.options;
    targetDate = refreshed.targetDate;
    if (dateChanged) clearNowcastResult();
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
      if (data.timezone_name && Number.isFinite(Number(data.elevation_m))) {
        timezoneName = data.timezone_name;
        elevationM = data.elevation_m ?? 0;
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

  async function handlePointSelected(pLat: number, pLon: number, name: string) {
    locationReady = false;
    try {
      const data = await getPoint(pLat, pLon);
      lat = data.lat; lon = data.lon;
      timezoneName = data.timezone_name;
      elevationM = data.elevation_m;
      locationName = name;
      locationReady = true;
    } catch {
      lat = pLat; lon = pLon; locationName = name; locationReady = true;
    }
  }

  async function handleCurrentLocation(coordinates: LocalCoordinates) {
    errorMsg = '';
    const data = await getPoint(coordinates.lat, coordinates.lon);
    if (!data.timezone_name || !Number.isFinite(Number(data.elevation_m))) {
      throw new Error('无法获取当前位置的时区和海拔。');
    }
    lat = data.lat;
    lon = data.lon;
    timezoneName = data.timezone_name;
    elevationM = Number(data.elevation_m);
    locationName = '当前位置';
    searchQuery = '当前位置';
    locationReady = true;
    mapRef?.setView(data.lat, data.lon, 10);
  }

  function handleGeolocationState(state: GeolocationState) {
    if (state.status === 'loading' || state.status === 'success') {
      if (state.status === 'loading') errorMsg = '';
      return;
    }
    if (state.status !== 'idle') errorMsg = state.message;
  }

  function handleCitySelect(city: UserCity) {
    lat = city.lat;
    lon = city.lon;
    locationName = city.name;
    timezoneName = city.timezone_name || DEFAULT_TIMEZONE;
    elevationM = city.elevation_m || 0;
    searchQuery = city.name;
    locationReady = true;
    mapRef?.setView(city.lat, city.lon, 10);
  }

  function handleMapReady(event: { map: L.Map }) { leafletMap = event.map; }

  async function handleNowcast(event: Event) {
    event.preventDefault();
    if (!locationReady) { errorMsg = '坐标时区和海拔尚未就绪。'; return; }
    errorMsg = '';
    const requestSequence = ++nowcastRequestSequence;
    const requestedTargetDate = targetDate;
    loading = true;
    progressVisible = true;
    progressPercent = 0; progressMessage = '提交实况任务'; progressStage = '准备'; progressElapsed = 0; progressLogs = [];

    const body = { ...nowcastBody(), target_date: targetDate };

    try {
      let data: ForecastPayload;
      const ssePath = '/api/v1/firesky/forecasts/events';
      if (typeof EventSource !== 'undefined') {
        const job = await startNowcast(body);
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
              else if (eventName === 'failed') { settled = true; source.close(); updateProgress(d as any, requestSequence); reject(new Error(String(d.message || '实况预测失败'))); }
            },
            onError: () => { if (!settled) { settled = true; source.close(); reject(new Error('进度连接中断，请重试。')); } },
          });
        });
      } else {
        updateProgress({ stage: 'fallback', percent: 5, message: '当前浏览器不支持实时进度' }, requestSequence);
        data = await runNowcast(body);
        updateProgress({ stage: 'done', percent: 100, message: '实况预测完成' }, requestSequence);
      }
      if (requestSequence !== nowcastRequestSequence) return;
      if (targetDate !== requestedTargetDate || data.target_date !== requestedTargetDate) {
        if (targetDate === requestedTargetDate) {
          errorMsg = `实况返回日期 ${data.target_date}，与请求日期 ${requestedTargetDate} 不一致。`;
        }
        return;
      }
      forecast = data;
      mobileView = 'results';
      crossSection = data.cross_section ?? null;
      selectedTime = data.best_time_local;

      if (data.map_times?.length > 0 && data.map_token) {
        const bestIdx = Math.max(0, data.map_times.findIndex(t => t === data.best_time_local));
        try {
          const frame = await getMapFrame(data.map_token, data.map_times[bestIdx]);
          if (requestSequence === nowcastRequestSequence && targetDate === requestedTargetDate) currentFrame = frame;
        } catch (e) {
          console.warn('Initial frame load failed, Timeline will retry:', e);
        }
      }
    } catch (error: unknown) {
      if (requestSequence !== nowcastRequestSequence) return;
      errorMsg = error instanceof Error ? error.message : '实况预测失败';
    } finally {
      if (requestSequence === nowcastRequestSequence) loading = false;
    }
  }

  function updateProgress(event: { stage?: string; percent?: number; message?: string; elapsed_seconds?: number }, requestSequence = nowcastRequestSequence) {
    if (requestSequence !== nowcastRequestSequence) return;
    progressPercent = Math.max(0, Math.min(100, Number(event.percent || 0)));
    progressMessage = event.message || '实况处理中';
    progressStage = event.stage || '运行中';
    if (event.elapsed_seconds != null) progressElapsed = event.elapsed_seconds;
    progressLogs = [...progressLogs.slice(-8), event.message || event.stage || '实况处理中'];
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

  function handleFrameLoaded(frame: MapFramePayload, _index: number) {
    currentFrame = frame;
    selectedTime = frame.valid_time_local;
    const step = forecast?.steps.find((item) => item.valid_time_local === frame.valid_time_local);
    if (step?.valid) void loadCrossSection(step);
  }

  function handleBasemapChange(event: Event) {
    const target = event.target as HTMLSelectElement;
    currentBasemap = target.value;
    mapRef?.selectBaseMapPublic(currentBasemap);
  }

  onMount(() => {
    reconcileChinaDate();
    dateRefreshTimer = window.setInterval(reconcileChinaDate, 60_000);
    window.addEventListener('focus', reconcileChinaDate);
    window.addEventListener('pageshow', reconcileChinaDate);
    document.addEventListener('visibilitychange', reconcileChinaDate);
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
              <label for="now-lat" class="text-xs text-muted-foreground">纬度</label>
              <Input id="now-lat" type="number" bind:value={lat} min="-90" max="90" step="0.000001" class="h-8 text-sm font-mono-data"
                onchange={() => handlePointSelected(lat, lon, locationName)} />
            </div>
            <div>
              <label for="now-lon" class="text-xs text-muted-foreground">经度</label>
              <Input id="now-lon" type="number" bind:value={lon} min="-180" max="180" step="0.000001" class="h-8 text-sm font-mono-data"
                onchange={() => handlePointSelected(lat, lon, locationName)} />
            </div>
          </div>
          <div class="text-xs text-muted-foreground border-t border-border/50 pt-2">
            {locationName} · {timezoneName} · 海拔 {Math.round(elevationM)} m
          </div>
        </CardContent>
      </Card>

      <!-- Nowcast info -->
      <div class="flex items-start gap-2 rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-2.5 text-xs text-sky-700 dark:text-sky-300">
        <Satellite class="w-4 h-4 shrink-0 mt-0.5" />
        <div class="leading-relaxed">
          <p class="font-medium">卫星实况预测</p>
          <p class="text-sky-600/80 dark:text-sky-400/80 mt-0.5">葵花卫星反演云场，采用与单点预测一致的物理评分。</p>
        </div>
      </div>

      <!-- Nowcast Form -->
      <form class="space-y-3" onsubmit={handleNowcast}>
        <div>
          <label for="nowcast-target-date" class="text-xs text-muted-foreground mb-1.5 block">事件日期</label>
          <div class="grid grid-cols-3 gap-1 p-0.5 rounded-lg bg-muted/50 border border-border/50">
            {#each dateOptions as opt}
              <button type="button"
                class={cn("py-2 text-sm font-medium rounded-md transition-all duration-200",
                  targetDate === opt.value
                    ? "bg-card text-card-foreground shadow-sm border border-border/50"
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
              id="nowcast-target-date"
              value={targetDate}
              active={!dateOptions.some((opt) => opt.value === targetDate)}
              onchange={handleTargetDateInput}
            />
          </div>
          <p class="text-[11px] text-muted-foreground mt-1">可选择历史或未来日期，按事件时刻回取 Himawari 云图。</p>
        </div>

        <div>
          <div class="text-xs text-muted-foreground mb-1.5 block">现象</div>
          <div class="grid grid-cols-2 gap-1 p-0.5 rounded-lg bg-muted/50 border border-border/50">
            <button type="button"
              class={cn("py-2 text-sm font-medium rounded-md transition-all duration-200",
                phenomenon === 'dawn'
                  ? "bg-card text-card-foreground shadow-sm border border-border/50"
                  : "text-muted-foreground hover:text-card-foreground")}
              onclick={() => { phenomenon = 'dawn'; }}>朝霞</button>
            <button type="button"
              class={cn("py-2 text-sm font-medium rounded-md transition-all duration-200",
                phenomenon === 'dusk'
                  ? "bg-card text-card-foreground shadow-sm border border-border/50"
                  : "text-muted-foreground hover:text-card-foreground")}
              onclick={() => { phenomenon = 'dusk'; }}>晚霞</button>
          </div>
        </div>

        <Button
          type="submit"
          class="w-full h-10 font-semibold firesky-gradient-bar text-white hover:opacity-90 transition-opacity border-0"
          disabled={loading || !locationReady}
        >
          {loading ? '实况分析中...' : '获取实况预测'}
        </Button>

        {#if progressVisible}
          <div class="rounded-xl border border-border/80 bg-card p-3 space-y-2" transition:fly={{ y: 10, duration: 300 }}>
            <div class="flex justify-between items-baseline">
              <span class="text-xs font-medium truncate flex-1">{progressMessage}</span>
              <Badge variant="outline" class="text-[11px] ml-2 font-mono-data border-primary/30 text-primary">
                {`${Math.round(progressPercent)}%`}
              </Badge>
            </div>
            <div class="h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                class="h-full rounded-full firesky-gradient-bar transition-all duration-300 ease-out"
                style="width: {progressPercent}%"
              ></div>
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
              <label for="now-basemap" class="text-xs font-medium text-muted-foreground mb-1.5 block">底图</label>
              <select id="now-basemap" bind:value={currentBasemap} onchange={handleBasemapChange}
                class="w-full h-8 rounded-md border border-input bg-background px-2 text-sm">
                <option value="tianditu-vector">天地图矢量</option>
                <option value="tianditu-imagery">天地图影像</option>
                <option value="osm">OpenStreetMap</option>
              </select>
            </div>
            <Separator />
            <div>
              <label for="now-cloud-field" class="text-xs font-medium text-muted-foreground mb-1.5 block">云层显示</label>
              <select id="now-cloud-field" bind:value={cloudField} class="w-full h-8 rounded-md border border-input bg-background px-2 text-sm">
                <option value="total">综合云量</option>
                <option value="high_cloud">高云</option>
                <option value="mid_cloud">中云</option>
                <option value="low_cloud">低云</option>
              </select>
            </div>
            <div>
              <label for="now-render-mode" class="text-xs font-medium text-muted-foreground mb-1.5 block">渲染方式</label>
              <select id="now-render-mode" bind:value={renderMode} class="w-full h-8 rounded-md border border-input bg-background px-2 text-sm">
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
          </div>
        </SheetContent>
      </Sheet>

      {#if leafletMap && currentFrame}
        <SoftCloudLayer map={leafletMap} cells={currentFrame.cells} field={cloudField} {showCloud} {showBurn} {renderMode} />
        <BlockingContours map={leafletMap} cells={currentFrame.cells} show={showBlock} />
      {/if}
    </div>

    <div class="workbench-results">
    <Timeline {forecast} timeZone={CHINA_TIME_ZONE} {selectedTime} onFrameLoaded={handleFrameLoaded} />

    <!-- Results section -->
    {#if forecast}
      <section class="p-4 md:p-6 space-y-5 max-w-5xl mx-auto" transition:fly={{ y: 20, duration: 400, easing: (t) => 1 - Math.pow(1 - t, 3) }}>

        {#if forecast.nowcast}
          <div class="flex items-start gap-2 rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-2 text-xs text-sky-700 dark:text-sky-300">
            <Satellite class="w-3.5 h-3.5 shrink-0" />
            <div class="space-y-1">
              <p>
                实况源：{forecast.nowcast.source ?? '卫星'} · 通道 {forecast.nowcast.image_channel ?? '—'}
                · 近场云比 {(forecast.nowcast.near_cloud_ratio ?? 0).toFixed(2)}
                · 远场云比 {(forecast.nowcast.far_cloud_ratio ?? 0).toFixed(2)}
                · 云幕 {Math.round((forecast.nowcast.cloud_blanket_score ?? 0) * 100)}
                · 纹理 {Math.round((forecast.nowcast.cloud_texture_score ?? 0) * 100)}
                · 地平线通透 {Math.round((forecast.nowcast.horizon_clearance_score ?? 0) * 100)}
                · 几何挡光 {Math.round((forecast.nowcast.geometric_path_blocking_risk ?? 0) * 100)}
                ({forecast.nowcast.geometric_path_samples ?? 0}点)
              </p>
              {#if forecast.nowcast.cloud_type_label}
                <p class="text-sky-600/80 dark:text-sky-400/80">
                  多光谱云类 {forecast.nowcast.cloud_type_label}
                  · 置信度 {Math.round((forecast.nowcast.cloud_type_confidence ?? 0) * 100)}
                  · 高积云 {Math.round((forecast.nowcast.altocumulus_probability ?? 0) * 100)}%
                  · 高层云 {Math.round((forecast.nowcast.altostratus_probability ?? 0) * 100)}%
                  · 卷云 {Math.round((forecast.nowcast.cirrus_probability ?? 0) * 100)}%
                  · 冰云 {Math.round((forecast.nowcast.ice_cloud_probability ?? 0) * 100)}%
                </p>
              {/if}
              {#if forecast.nowcast.radar_source}
                <p class="text-sky-600/80 dark:text-sky-400/80">
                  天气雷达
                  · 最大回波 {forecast.nowcast.radar_max_dbz == null ? '无有效回波' : `${Math.round(forecast.nowcast.radar_max_dbz)} dBZ`}
                  · 本地降水 {Math.round((forecast.nowcast.radar_local_precip_ratio ?? 0) * 100)}%
                  · 光路降水 {Math.round((forecast.nowcast.radar_path_precip_ratio ?? 0) * 100)}%
                  · 区域对流风险 {Math.round((forecast.nowcast.effective_convective_risk ?? 0) * 100)}
                  · 光路阻塞风险 {Math.round((forecast.nowcast.effective_path_blocking_risk ?? 0) * 100)}
                  · 可靠度 {Math.round((forecast.nowcast.radar_reliability_score ?? 0) * 100)}
                  · 评分上限 {Math.round((forecast.nowcast.convective_score_ceiling ?? 1) * 100)}
                  · 采样覆盖率 {Math.round((forecast.nowcast.calibrated_sample_coverage_ratio ?? 0) * 100)}
                </p>
              {/if}
            </div>
          </div>
          {#if forecast.nowcast.satellite_vertical_profile}
            <SatelliteVerticalProfile profile={forecast.nowcast.satellite_vertical_profile} />
          {/if}
        {/if}

        {#if !forecast.map_token || !forecast.map_times?.length}
          <div class="rounded-xl border border-border/80 bg-muted/30 p-8 text-center">
            <p class="text-sm text-muted-foreground">当前时刻暂无云场数据</p>
            <p class="text-xs text-muted-foreground mt-1">请确认卫星数据可用后重试</p>
          </div>
        {/if}

        <ScoreCard data={forecast} />
        <details class="diagnostic-details">
          <summary>气象参数与评分明细</summary>
          <div class="space-y-4 pt-4">
            <MetricsGrid data={forecast} displayTimeZone={CHINA_TIME_ZONE} />
            <QualityBreakdown data={forecast} />
          </div>
        </details>
        <TrendChart steps={forecast.steps} timeZone={CHINA_TIME_ZONE} {selectedTime} onTimeSelected={handleTimeSelected} />
        {#if crossSection || crossSectionLoading || crossSectionError}
          <section class="min-h-[320px] overflow-hidden rounded-lg border border-border bg-card">
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
