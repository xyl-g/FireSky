<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import BurnOutlookMap from '$lib/components/map/BurnOutlookMap.svelte';
  import { Input } from '$lib/components/ui/input';
  import DatePickerTrigger from '$lib/components/DatePickerTrigger.svelte';
  import { ApiClientError } from '$lib/api/client';
  import { getCachedRegionalForecast } from '$lib/api/forecast';
  import type {
    Phenomenon,
    RegionalForecastArea,
    RegionalForecastCoverage,
    RegionalForecastPoint,
    RegionalForecastRequest,
    RegionalDataProvenance,
    RegionalGfsDataProvenance,
    RegionalForecastPayload,
  } from '$lib/api/types';
  import { REGION_OPTIONS_WITH_CUSTOM, REGION_BOUNDS, regionLabel } from '$lib/api/regions';
  import { CHINA_TIME_ZONE, formatTime, forecastDateOptions, isChinaDateOnOrAfterToday, refreshForecastDateSelection } from '$lib/utils/format';
  import { requireStrictIsoDate } from '$lib/api/date-contract';
  import MapIcon from '@lucide/svelte/icons/map';
  import CloudOff from '@lucide/svelte/icons/cloud-off';
  import Layers3 from '@lucide/svelte/icons/layers-3';
  import LoaderCircle from '@lucide/svelte/icons/loader-circle';
  import Calendar from '@lucide/svelte/icons/calendar';
  import Compass from '@lucide/svelte/icons/compass';
  import Sun from '@lucide/svelte/icons/sun';
  import Moon from '@lucide/svelte/icons/moon';
  import Database from '@lucide/svelte/icons/database';
  import ChevronRight from '@lucide/svelte/icons/chevron-right';

  // ─── Form state ───
  let dateOptions = forecastDateOptions();
  let targetDate = dateOptions[0]?.value ?? '';
  let dateRefreshTimer: number | null = null;
  let phenomenon: Phenomenon = 'dusk';
  let modelSource: 'gfs' | 'ec' = 'gfs';
  let cycleOverride = '';
  let selectedRegion: RegionalForecastArea = 'china';

  // Custom region
  let customLabel = '自定义区域';
  let customWest = 105;
  let customSouth = 20;
  let customEast = 125;
  let customNorth = 35;
  let customTimezone = 'Asia/Shanghai';

  // Display
  let showSamples = false;

  // ─── Result state ───
  let result: RegionalForecastPayload | null = null;
  let selectedPoint: RegionalForecastPoint | null = null;
  let loading = false;
  let errorMessage = '';
  let noData = false;
  let requestSignature = '';
  let loadSequence = 0;
  let activeRequestController: AbortController | null = null;
  const resultCache = new Map<string, RegionalForecastPayload>();

  function abortActiveRequest() {
    activeRequestController?.abort();
    activeRequestController = null;
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
      errorMessage = '';
      result = null;
      selectedPoint = null;
      noData = false;
    }
  }

  function selectTargetDate(value: string) {
    let nextDate: string;
    try {
      nextDate = requireStrictIsoDate(value, '形势图目标日期');
    } catch {
      errorMessage = '形势图目标日期格式无效。';
      return;
    }
    if (!isChinaDateOnOrAfterToday(nextDate)) {
      errorMessage = '形势图目标日期不能早于中国今天。';
      return;
    }
    if (nextDate === targetDate) {
      errorMessage = '';
      return;
    }
    errorMessage = '';
    targetDate = nextDate;
    result = null;
    selectedPoint = null;
    noData = false;
  }

  function handleTargetDateInput(value: string) {
    selectTargetDate(value);
  }

  onMount(() => {
    reconcileChinaDate();
    dateRefreshTimer = window.setInterval(reconcileChinaDate, 60_000);
    window.addEventListener('focus', reconcileChinaDate);
    window.addEventListener('pageshow', reconcileChinaDate);
    document.addEventListener('visibilitychange', reconcileChinaDate);
  });

  onDestroy(() => {
    abortActiveRequest();
    if (dateRefreshTimer) clearInterval(dateRefreshTimer);
    window.removeEventListener('focus', reconcileChinaDate);
    window.removeEventListener('pageshow', reconcileChinaDate);
    document.removeEventListener('visibilitychange', reconcileChinaDate);
  });

  const REGION_OPTIONS = REGION_OPTIONS_WITH_CUSTOM;
  const MODEL_OPTIONS = [
   { value: 'gfs', label: 'GFS', description: '全球 0.25°' },
   { value: 'ec', label: 'ECMWF IFS', description: '独立数值模式' },
  ] as const;

  function modelLabel(source: 'gfs' | 'ec'): string {
    return MODEL_OPTIONS.find((option) => option.value === source)?.label ?? source.toUpperCase();
  }

  function isGfsProvenance(provenance: RegionalDataProvenance): provenance is RegionalGfsDataProvenance {
    return 'served_gfs_cycle_utc' in provenance;
  }

  function sourceCycle(payload: RegionalForecastPayload): string {
    return isGfsProvenance(payload.data_provenance)
      ? provenanceTimes([payload.data_provenance.served_gfs_cycle_utc])
      : provenanceTimes([payload.data_provenance.cycle_utc]);
  }

  function sourceForecastHours(payload: RegionalForecastPayload): string {
    const hours = isGfsProvenance(payload.data_provenance)
      ? payload.data_provenance.gfs_forecast_hours
      : payload.data_provenance.forecast_hours;
    return hours.length ? hours.map((hour) => `${hour}h`).join(', ') : '不可证实';
  }

  function gfsValidTimes(payload: RegionalForecastPayload): string {
    return isGfsProvenance(payload.data_provenance)
      ? provenanceTimes(payload.data_provenance.gfs_valid_times_utc)
      : '不可证实';
  }

  function camsRunTimes(payload: RegionalForecastPayload): string {
    return isGfsProvenance(payload.data_provenance)
      ? provenanceTimes(payload.data_provenance.cams_run_times_utc)
      : 'CAMS 完整剖面';
  }

  function camsValidTimes(payload: RegionalForecastPayload): string {
    return isGfsProvenance(payload.data_provenance)
      ? provenanceTimes(payload.data_provenance.cams_valid_times_utc)
      : '按模式时次匹配';
  }

  function selectModelSource(source: 'gfs' | 'ec') {
    if (source === modelSource) return;
    modelSource = source;
    cycleOverride = '';
    abortActiveRequest();
    loadSequence += 1;
    loading = false;
    errorMessage = '';
    result = null;
    selectedPoint = null;
    noData = false;
  }

  $: previewCoverage = result?.coverage ?? currentCoverage();
  $: displayPoints = result?.points ?? [];

  function provenanceTimes(values: Array<string | null | undefined>): string {
    const validValues = values.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()));
    return validValues.length
      ? validValues.map((value) => value.replace('T', ' ').replace('+00:00', 'Z')).join(', ')
      : '不可证实';
  }

  function isNoDataError(error: unknown): boolean {
    if (error instanceof ApiClientError && error.status === 404) return true;
    const message = error instanceof Error ? error.message : String(error ?? '');
    return /not[\s_-]?found|暂无(?:匹配)?(?:全国)?缓存|缓存不存在|不存在(?:缓存|预测)/i.test(message);
  }

  function currentRegions(): RegionalForecastArea[] {
    return [selectedRegion];
  }

  function currentCoverage(): RegionalForecastCoverage[] {
    if (selectedRegion === 'custom') {
      return [{
        code: 'custom',
        label: customLabel || '自定义区域',
        bounds: [customWest, customSouth, customEast, customNorth],
        timezone_name: customTimezone,
        point_prefix: 'CUSTOM',
      }];
    }
    const bounds = REGION_BOUNDS[selectedRegion as Exclude<RegionalForecastArea, 'custom'>];
    return [{
      code: selectedRegion,
      label: regionLabel(selectedRegion),
      bounds,
      timezone_name: selectedRegion === 'china' ? 'Asia/Shanghai' : null,
      point_prefix: selectedRegion.toUpperCase(),
    }];
  }

  function requestBody(): RegionalForecastRequest {
    return {
      target_date: targetDate,
      phenomenon,
      model_source: modelSource,
      satellite_source: 'off',
      gfs_resolution: '0p25',
      spacing_deg: 0.25,
      regions: currentRegions(),
      ...(cycleOverride ? {
        ...(modelSource === 'gfs' ? { _gfs_cycle_override: cycleOverride } : {}),
        ...(modelSource === 'ec' ? { _ec_cycle_override: cycleOverride } : {}),
      } : {}),
      ...(selectedRegion === 'custom' ? {
        custom_bounds: { west: customWest, south: customSouth, east: customEast, north: customNorth },
        custom_label: customLabel,
        custom_timezone_name: customTimezone,
      } : {}),
    };
  }

  async function loadCache(signature: string) {
    const sequence = ++loadSequence;
    abortActiveRequest();
    const requestController = new AbortController();
    activeRequestController = requestController;
    const requestedTargetDate = targetDate;
    errorMessage = '';
    loading = true;
    result = null;
    selectedPoint = null;
    noData = false;
    try {
      const cached = resultCache.get(signature);
      const payload = cached ?? await getCachedRegionalForecast(requestBody(), requestController.signal);
      if (sequence !== loadSequence) return;
      if (targetDate !== requestedTargetDate || payload.target_date !== requestedTargetDate) {
        if (targetDate === requestedTargetDate) {
          errorMessage = `缓存返回日期 ${payload.target_date}，与请求日期 ${requestedTargetDate} 不一致。`;
        }
        return;
      }
      if (!cached) {
        resultCache.set(signature, payload);
        if (resultCache.size > 4) resultCache.delete(resultCache.keys().next().value!);
      }
      if (!payload.points?.length) {
        noData = true;
      } else {
        result = payload;
        selectedPoint = null;
      }
    } catch (error) {
      if (sequence !== loadSequence) return;
      const msg = error instanceof Error ? error.message : '读取缓存失败';
      if (isNoDataError(error)) {
        noData = true;
      } else {
        errorMessage = msg;
      }
    } finally {
      if (activeRequestController === requestController) activeRequestController = null;
      if (sequence === loadSequence) loading = false;
    }
  }

  $: requestSignature = JSON.stringify({
    targetDate,
    phenomenon,
    modelSource,
    cycleOverride,
    selectedRegion,
    customLabel,
    customWest,
    customSouth,
    customEast,
    customNorth,
    customTimezone,
  });
  $: if (requestSignature) {
    void loadCache(requestSignature);
  }

  function pointName(point: RegionalForecastPoint): string {
    return /^(CN|KR|JP|CUSTOM)-/.test(point.name)
      ? `${point.lat.toFixed(1)}°N, ${point.lon.toFixed(1)}°E`
      : point.name;
  }

  function localTime(value: string | null): string {
    if (!value) return '无明显峰值';
    return formatTime(value, CHINA_TIME_ZONE);
  }

  function pointForecastHref(point: RegionalForecastPoint): string {
    const query = new URLSearchParams({
      lat: String(point.lat),
      lon: String(point.lon),
      name: pointName(point),
      target_date: targetDate,
      phenomenon,
      model_source: modelSource,
      region: selectedRegion,
      elevation_m: String(point.observer_elevation_asl_m),
      ...(result?.cycle_time_utc ? { cycle: result.cycle_time_utc } : {}),
    });
    return `/forecast?${query.toString()}`;
  }
</script>

<div class="outlook-shell">
  <!-- Left Control Rail with Observatory Precision Design -->
  <aside class="control-rail glass-rail" aria-label="预报条件">
    <div class="rail-heading">
      <div class="min-w-0">
        <h1 class="font-display">全国形势分布图</h1>
        <p>{phenomenon === 'dusk' ? '各地本地日落窗口火烧云质量峰值' : '各地本地日出窗口火烧云质量峰值'}</p>
      </div>
      <div class="w-8 h-8 rounded-xl bg-primary/10 flex items-center justify-center text-primary shrink-0">
        <MapIcon class="h-4 w-4" />
      </div>
    </div>

    <div class="forecast-form">
      <!-- Date -->
      <div class="field date-field">
        <div class="field-label flex items-center gap-1.5">
          <Calendar class="w-3.5 h-3.5 text-primary" />
          <span>目标预报日期</span>
        </div>
        <div class="segment three-cols">
          {#each dateOptions as opt}
            <button type="button" class:active={targetDate === opt.value} onclick={() => selectTargetDate(opt.value)}>
              <span>{opt.label}</span>
              <small>{opt.value.slice(5)}</small>
            </button>
          {/each}
        </div>
        <div class="date-picker">
          <span>更多日期</span>
          <DatePickerTrigger
            id="outlook-target-date"
            value={targetDate}
            min={dateOptions[0]?.value}
            active={!dateOptions.some((opt) => opt.value === targetDate)}
            onchange={handleTargetDateInput}
          />
        </div>
      </div>

      <!-- Phenomenon -->
      <fieldset>
        <legend class="flex items-center gap-1.5">
          <Compass class="w-3.5 h-3.5 text-primary" />
          <span>观测现象</span>
        </legend>
        <div class="segment">
          <button type="button" class:active={phenomenon === 'dawn'} onclick={() => phenomenon = 'dawn'} class="flex items-center justify-center gap-1.5">
            <Sun class="w-3.5 h-3.5 {phenomenon === 'dawn' ? 'text-amber-500' : ''}" />
            <span>朝霞 (日出)</span>
          </button>
          <button type="button" class:active={phenomenon === 'dusk'} onclick={() => phenomenon = 'dusk'} class="flex items-center justify-center gap-1.5">
            <Moon class="w-3.5 h-3.5 {phenomenon === 'dusk' ? 'text-rose-500' : ''}" />
            <span>晚霞 (日落)</span>
          </button>
        </div>
      </fieldset>

      <!-- Numerical model -->
      <fieldset>
        <legend class="flex items-center gap-1.5">
          <Database class="w-3.5 h-3.5 text-primary" />
          <span>数值模式</span>
        </legend>
        <div class="segment model-segment">
          {#each MODEL_OPTIONS as option}
            <button
              type="button"
              class:active={modelSource === option.value}
              aria-pressed={modelSource === option.value}
              onclick={() => selectModelSource(option.value)}
            >
              <span>{option.label}</span>
              <small>{option.description}</small>
            </button>
          {/each}
        </div>
      </fieldset>

      <!-- Region dropdown -->
      <div class="field">
        <label for="outlook-region" class="flex items-center gap-1.5">
          <Layers3 class="w-3.5 h-3.5 text-primary" />
          <span>观测区域</span>
        </label>
        <select id="outlook-region" bind:value={selectedRegion}>
          {#each REGION_OPTIONS as opt}
            <option value={opt.value}>{opt.label}</option>
          {/each}
        </select>
      </div>

      {#if selectedRegion === 'custom'}
        <div class="custom-fields">
          <div class="field wide">
            <label for="outlook-custom-label">区域名称</label>
            <Input id="outlook-custom-label" bind:value={customLabel} />
          </div>
          <div class="field"><label for="outlook-west">西界</label><Input id="outlook-west" type="number" bind:value={customWest} min="-180" max="180" step="0.5" /></div>
          <div class="field"><label for="outlook-east">东界</label><Input id="outlook-east" type="number" bind:value={customEast} min="-180" max="180" step="0.5" /></div>
          <div class="field"><label for="outlook-south">南界</label><Input id="outlook-south" type="number" bind:value={customSouth} min="-90" max="90" step="0.5" /></div>
          <div class="field"><label for="outlook-north">北界</label><Input id="outlook-north" type="number" bind:value={customNorth} min="-90" max="90" step="0.5" /></div>
          <div class="field wide">
            <label for="outlook-timezone">时区</label>
            <Input id="outlook-timezone" bind:value={customTimezone} placeholder="Asia/Shanghai" />
          </div>
        </div>
      {/if}

      {#if loading}
        <div class="loading-indicator">
          <LoaderCircle class="h-4 w-4 animate-spin text-primary" />
          <span>读取全国云场与物理仿真...</span>
        </div>
      {/if}

      {#if errorMessage}<p class="error" role="alert">{errorMessage}</p>{/if}
    </div>

    <!-- Data Provenance Card -->
    {#if result}
      <section class="data-source" aria-labelledby="data-source-title">
        <div class="source-heading">
          <div>
            <span class="source-kicker">数据来源</span>
            <h2 id="data-source-title">{modelLabel(result.model_source)} / CAMS 气溶胶</h2>
          </div>
          <span class:stale={isGfsProvenance(result.data_provenance) && result.data_provenance.stale} class="source-state font-mono">
            {isGfsProvenance(result.data_provenance) && result.data_provenance.stale ? '上一份快照' : '当前模型周期'}
          </span>
        </div>
        <dl class="source-list font-mono-data">
          <div><dt>{modelLabel(result.model_source)} 起报</dt><dd>{sourceCycle(result)}</dd></div>
          <div><dt>{modelLabel(result.model_source)} 时次</dt><dd>{isGfsProvenance(result.data_provenance) ? gfsValidTimes(result) : sourceForecastHours(result)}</dd></div>
          {#if isGfsProvenance(result.data_provenance)}
            <div><dt>CAMS 起报</dt><dd>{camsRunTimes(result)}</dd></div>
            <div><dt>CAMS 时次</dt><dd>{camsValidTimes(result)}</dd></div>
          {:else}
            <div><dt>气溶胶</dt><dd>CAMS 完整剖面</dd></div>
            <div><dt>模式结果</dt><dd>独立评分结果</dd></div>
          {/if}
        </dl>
      </section>
    {/if}
  </aside>

  <!-- Right Map Viewport -->
  <section class="map-stage">
    <BurnOutlookMap
      points={displayPoints}
      coverage={previewCoverage}
      pointMode={result ? result.point_mode : ''}
      pointSpacingDeg={result ? result.precision.point_spacing_deg : null}
      {selectedPoint}
      {showSamples}
      field="score"
      {pointForecastHref}
      formatBestTime={localTime}
      onPointSelected={(point) => selectedPoint = point}
    />

    {#if noData}
      <div class="no-data-overlay">
        <CloudOff class="h-8 w-8 text-muted-foreground/60" />
        <p class="font-display">No Data</p>
        <span>该日期 / 区域组合暂无全国缓存数据</span>
      </div>
    {:else}
      <label class="sample-toggle">
        <input type="checkbox" bind:checked={showSamples} />
        <Layers3 class="h-3.5 w-3.5 text-primary" />
        <span>采样格点</span>
      </label>

      <!-- Professional Quality Ramp Legend -->
      <div class="legend" aria-label="火烧云质量连续色带">
        <div class="legend-header flex items-center justify-between mb-1.5">
          <span class="legend-title">火烧云质量 / 2.5</span>
          <span class="legend-sub font-mono">越红越壮丽</span>
        </div>
        <i class="quality-ramp"></i>
        <div class="legend-values font-mono-data">
          <span>0</span>
          <span>0.5</span>
          <span>1.0</span>
          <span>1.5</span>
          <span>2.0</span>
          <span>2.5</span>
        </div>
      </div>
    {/if}
  </section>
</div>

<style>
  .outlook-shell {
    display: grid;
    grid-template-columns: 320px minmax(0, 1fr);
    height: 100%;
    min-height: 0;
    border-radius: 1rem;
    overflow: hidden;
    border: 1px solid var(--glass-border);
    box-shadow: var(--glass-shadow);
  }
  /* glass-rail 提供半透明填充 + 背景 blur + 右侧分隔高光；此处只管布局 */
  .control-rail { overflow-y: auto; padding: 22px 18px 20px; }
  .rail-heading { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--glass-border); }
  .rail-heading h1 { margin: 0; font-size: 17px; font-weight: 600; line-height: 1.3; }
  .rail-heading p { margin: 4px 0 0; color: var(--muted-foreground); font-size: 11px; line-height: 1.5; }
  .forecast-form { display: grid; gap: 18px; }
  .field { display: grid; gap: 6px; min-width: 0; }
  .field-label { font-size: 12px; font-weight: 600; color: var(--foreground); }
  .field label, legend { color: var(--foreground); font-size: 12px; font-weight: 600; }
  fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
  legend { margin-bottom: 6px; }

  /* 嵌入式分段选择：glass-control-well 配方（内凹井面，不再叠加 backdrop 层） */
  .segment { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 3px; background: var(--glass-well); border: 1px solid var(--glass-border); border-radius: 8px; box-shadow: inset 0 1px 2px color-mix(in oklch, var(--foreground) 6%, transparent), 0 1px 0 var(--glass-reflection); }
  .segment.three-cols { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  /* The model picker holds one button per MODEL_OPTIONS entry, so the column
     count must follow the data: a hard-coded 3 left an empty third column and
     squeezed the GFS/ECMWF labels into two thirds of the rail width. */
  .segment.model-segment { grid-template-columns: repeat(auto-fit, minmax(0, 1fr)); }
  .segment button { min-height: 38px; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; padding: 0 4px; border: 0; border-radius: 6px; color: var(--muted-foreground); background: transparent; cursor: pointer; font-size: 12px; font-weight: 500; line-height: 1.25; text-align: center; transition: all 0.15s ease; }
  .segment button span { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .segment button small { max-width: 100%; color: inherit; font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 10px; font-variant-numeric: tabular-nums; opacity: 0.85; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* 选中态 = glass-control-active 配方；用 ring shadow 代替 border，避免选中时布局抖动 */
  .segment button.active { background: var(--glass-specular), var(--glass-strong); color: var(--card-foreground); font-weight: 600; box-shadow: 0 0 0 1px color-mix(in oklch, var(--ring) 26%, var(--glass-border)), var(--glass-shadow-contact), var(--glass-highlight); }

  .date-picker { display: grid; gap: 6px; margin-top: 10px; }
  .date-picker > span { color: var(--muted-foreground); font-size: 11px; font-weight: 500; }

  /* 蒙层用暖 card 底而非 background，避免磨砂后地图的灰蓝底斑驳透出 */
  .no-data-overlay { position: absolute; inset: 0; z-index: 600; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; background: color-mix(in srgb, var(--card) 90%, transparent); backdrop-filter: blur(6px) saturate(var(--glass-saturation)); -webkit-backdrop-filter: blur(6px) saturate(var(--glass-saturation)); }
  .no-data-overlay p { margin: 0; font-size: 18px; font-weight: 600; color: var(--muted-foreground); }
  .no-data-overlay span { font-size: 12px; color: var(--muted-foreground); }

  .custom-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding-left: 12px; border-left: 2px solid var(--glass-border); }
  .custom-fields .wide { grid-column: 1 / -1; }

  /* glass-input 配方：内凹井面，聚焦时升为 strong 面 */
  select { width: 100%; height: 38px; border: 1px solid var(--glass-border); border-radius: 8px; padding: 0 10px; background: var(--glass-well); color: var(--foreground); font-size: 13px; outline: none; box-shadow: inset 0 1px 2px color-mix(in oklch, var(--foreground) 6%, transparent), 0 1px 0 var(--glass-reflection); transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease; }
  select:focus { border-color: var(--primary); background: var(--glass-strong); box-shadow: 0 0 0 3px color-mix(in oklch, var(--ring) 16%, transparent), var(--glass-highlight); }

  .loading-indicator { display: flex; align-items: center; gap: 8px; color: var(--muted-foreground); font-size: 12px; }
  .error { margin: 0; color: var(--destructive); font-size: 12px; }

  dl { display: grid; gap: 6px; margin: 0; }
  dl div { display: flex; justify-content: space-between; gap: 12px; font-size: 11px; }
  dt { color: var(--muted-foreground); }
  dd { margin: 0; font-variant-numeric: tabular-nums; }

  .data-source { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--glass-border); }
  .source-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
  .source-kicker { display: block; color: var(--muted-foreground); font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
  .source-heading h2 { margin: 2px 0 0; font-size: 13px; font-weight: 600; }
  .source-state { flex: none; padding: 2px 6px; border-radius: 4px; background: var(--glass-well); border: 1px solid var(--glass-border); color: var(--muted-foreground); font-size: 10px; font-weight: 500; }
  .source-state.stale { color: var(--destructive); }
  .source-list { gap: 6px; margin-top: 10px; }
  .source-list div { align-items: flex-start; }
  .source-list dd { max-width: 180px; text-align: right; overflow-wrap: anywhere; }

  .map-stage { position: relative; min-width: 0; min-height: 0; overflow: hidden; }
  .sample-toggle, .legend { position: absolute; z-index: 500; border: 1px solid var(--glass-border); background: var(--glass-specular), var(--glass-strong); backdrop-filter: blur(12px) saturate(var(--glass-saturation)); -webkit-backdrop-filter: blur(12px) saturate(var(--glass-saturation)); box-shadow: var(--glass-shadow), var(--glass-highlight); }
  .sample-toggle { top: 14px; right: 14px; display: flex; align-items: center; gap: 6px; min-height: 32px; padding: 4px 10px; border-radius: 8px; font-size: 12px; cursor: pointer; }

  .legend { left: 14px; bottom: 24px; width: min(380px, calc(100% - 28px)); padding: 10px 12px; border-radius: 10px; font-size: 11px; }
  .legend-title { font-weight: 600; color: var(--card-foreground); font-size: 11px; }
  .legend-sub { font-size: 10px; color: var(--muted-foreground); }

  /* 🌅 雅致高级火烧云色梯 (冷灰 -> 柔金 -> 珊瑚橙 -> 晚霞赭红 -> 暮色醇红) 避免大红过于刺眼 */
  .quality-ramp { display: block; width: 100%; height: 9px; border-radius: 3px; background: linear-gradient(90deg, #9e9e9e 0%, #eab308 20%, #f97316 45%, #ea580c 70%, #be123c 88%, #881337 100%); }
  .legend-values { display: flex; justify-content: space-between; margin-top: 4px; color: var(--muted-foreground); font-size: 10px; font-variant-numeric: tabular-nums; }

  @media (max-width: 760px) {
    .outlook-shell { display: block; height: 100%; overflow-y: auto; }
    .control-rail { overflow: visible; border-bottom: 1px solid var(--glass-border); box-shadow: none; }
    .map-stage { height: 560px; }
    .sample-toggle { top: auto; bottom: 18px; }
    .legend { display: none; }
  }
</style>
