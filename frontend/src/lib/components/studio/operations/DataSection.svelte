<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import type { DataStatus, DataFetchProgress } from '$lib/api/data';
  import { getDataStatus, verifiedFetchReadiness } from '$lib/api/data';
  import { REGION_OPTIONS, REGION_BOUNDS } from '$lib/api/regions';
  import { post, get } from '$lib/api/client';
  import RefreshCw from '@lucide/svelte/icons/refresh-cw';
  import Database from '@lucide/svelte/icons/database';
  import HardDrive from '@lucide/svelte/icons/hard-drive';
  import Clock from '@lucide/svelte/icons/clock';
  import DownloadCloud from '@lucide/svelte/icons/download-cloud';
  import Info from '@lucide/svelte/icons/info';
  import Globe from '@lucide/svelte/icons/globe';
  import ChevronRight from '@lucide/svelte/icons/chevron-right';
  import ChevronDown from '@lucide/svelte/icons/chevron-down';
  import Trash2 from '@lucide/svelte/icons/trash-2';

  let status: DataStatus | null = null;
  let loading = true;
  let error = '';
  let expandedCycle: Record<string, boolean> = {};

  // ── Region selector ──
  type Region = {
    code: string;
    name: string;
    west: number;
    south: number;
    east: number;
    north: number;
    timezone: string;
    parent_code: string;
    is_builtin: boolean;
    enabled: boolean;
  };
  let regions: Region[] = [];
  let selectedRegion = 'china';
  let revision = 0;
  let inventoryRequest = 0;
  let destroyed = false;
  onDestroy(() => { destroyed = true; revision += 1; });

  // firesky has no region-management service (that lived in the old Go
  // gateway). Build the list locally from the shared region table so the
  // download planner always has a usable set.
  const REGION_TIMEZONES: Record<string, string> = {
    china: 'Asia/Shanghai',
    'east-asia': 'Asia/Shanghai',
    'southeast-asia': 'Asia/Singapore',
    'south-asia': 'Asia/Kolkata',
    europe: 'Europe/Berlin',
    'north-america': 'America/Denver',
    'south-america': 'America/Sao_Paulo',
    oceania: 'Australia/Sydney',
    'africa-middle-east': 'Africa/Cairo',
    'north-asia': 'Asia/Tokyo',
  };

  function loadRegions() {
    regions = REGION_OPTIONS.map((option) => {
      const [west, south, east, north] = REGION_BOUNDS[option.value as keyof typeof REGION_BOUNDS];
      return {
        code: option.value,
        name: option.label,
        west,
        south,
        east,
        north,
        timezone: REGION_TIMEZONES[option.value] ?? 'UTC',
        parent_code: '',
        is_builtin: true,
        enabled: true,
      };
    });
    if (!regions.some((region) => region.code === selectedRegion) && regions.length > 0) {
      selectedRegion = regions[0].code;
    }
  }

  function planOptions() {
    const r = regions.find((region) => region.code === selectedRegion);
    if (!r) throw new Error('没有可用的下载区域');
    return {
      region: r.code,
      region_name: r.name,
      bbox: [r.west, r.east, r.north, r.south],
      timezone: r.timezone,
      days: 3,
      phenomena: ['dawn', 'dusk'],
    };
  }

  // ── Download ──
  type DownloadStatus = {
    loading: boolean;
    message: string;
    kind: 'info' | 'success' | 'error';
    completed: number;
    total: number;
  };
  let dlGfs: DownloadStatus = { loading: false, message: '', kind: 'info', completed: 0, total: 0 };
  let dlEc: DownloadStatus = { loading: false, message: '', kind: 'info', completed: 0, total: 0 };
  let dlCams: DownloadStatus = { loading: false, message: '', kind: 'info', completed: 0, total: 0 };

  async function startDownload(sources: string[], setStatus: (s: DownloadStatus) => void, label: string, cycle?: string) {
    const requestRevision = revision;
    const current = () => !destroyed && requestRevision === revision;
    const update = (value: DownloadStatus) => { if (current()) setStatus(value); };
    update({ loading: true, message: `${label} 下载中…`, kind: 'info', completed: 0, total: 0 });
    let job_id = '';
    try {
      const options = planOptions();
      const body: {
        sources: string[];
        region: string;
        region_name: string;
        bbox: number[];
        timezone: string;
        days: number;
        phenomena: string[];
        cycle?: string;
      } = { sources, ...options };
      // Pin the download to the exact cycle shown on the card so we never re-query
      // and end up fetching a different (stale) cycle than the one the user sees.
      if (cycle && !sources.includes('cams')) body.cycle = cycle;
      const start = await post<{ job_id: string; status: string; sources?: string[] }>(
        '/api/v1/firesky/data/fetch',
        body,
      );
      job_id = start.job_id;
    } catch (e) {
      update({ loading: false, message: `下载失败: ${e instanceof Error ? e.message : '未知错误'}`, kind: 'error', completed: 0, total: 0 });
      return;
    }

    const POLL_INTERVAL = 1500;
    let pollFailures = 0;
    while (current()) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL));
      if (!current()) return;
      try {
        const s = await get<DataFetchProgress>(`/api/v1/firesky/data/fetch/status`, { job_id });
        if (!current()) return;
        pollFailures = 0;

        if (s.status === 'done') {
          const readiness = verifiedFetchReadiness(s, sources);
          const verifiedFiles = readiness?.datasets.reduce((sum, row) => sum + row.verified_files, 0) ?? 0;
          await loadStatus();
          update({
            loading: false,
            message: readiness ? `请求输入已验证：${verifiedFiles} 个文件` : '下载结束，但冻结请求完整性未通过验证',
            kind: readiness ? 'success' : 'error',
            completed: s.completed ?? 0,
            total: s.total ?? 0,
          });
          return;
        }
        if (['failed', 'cancelled', 'interrupted'].includes(s.status)) {
          const errMsg = (s.errors && s.errors.length > 0) ? s.errors[0] : '下载失败';
          update({ loading: false, message: `下载失败: ${errMsg}`, kind: 'error', completed: s.completed ?? 0, total: s.total ?? 0 });
          return;
        }
        // running
        const pct = (s.total ?? 0) > 0 ? Math.round(((s.completed ?? 0) / s.total!) * 100) : 0;
        update({
          loading: true,
          message: s.current ? `${s.current} ${pct}% (${s.completed ?? 0}/${s.total ?? 0})` : `${label} 下载中…`,
          kind: 'info',
          completed: s.completed ?? 0,
          total: s.total ?? 0,
        });
      } catch (e) {
        if (++pollFailures >= 10) {
          update({ loading: false, message: '下载状态连接中断，请刷新库存并重新检查请求', kind: 'error', completed: 0, total: 0 });
          return;
        }
      }
    }
  }

  function toggleCycle(key: string) {
    expandedCycle = { ...expandedCycle, [key]: !expandedCycle[key] };
  }

  function formatHours(hours: number[]): string {
    if (!hours?.length) return '无';
    if (hours.length <= 8) return hours.join(', ');
    return `${hours[0]}–${hours[hours.length - 1]} (${hours.length}个)`;
  }

  function cycleLabel(iso: string): string {
    if (!iso) return '无数据';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}Z`;
  }

  function downloadFor(source: string) {
    const ds = status?.datasets?.find(d => d.source === source);
    const cycle = ds?.latest_available_cycle || undefined;
    let setStatus: (s: DownloadStatus) => void;
    switch (source) {
      case 'gfs': setStatus = s => dlGfs = s; break;
      case 'ecmwf': setStatus = s => dlEc = s; break;
      case 'cams': setStatus = s => dlCams = s; break;
      default: setStatus = s => dlGfs = s; break;
    }
    startDownload([source], setStatus, datasetLabel(source), cycle);
  }

  async function loadStatus(): Promise<DataStatus | null> {
    const requestId = ++inventoryRequest;
    const requestRevision = revision;
    loading = true;
    error = '';
    try {
      const result = await getDataStatus(planOptions());
      if (destroyed || requestId !== inventoryRequest || requestRevision !== revision) return null;
      status = result;
      return status;
    } catch (e) {
      if (!destroyed && requestId === inventoryRequest && requestRevision === revision) {
        error = e instanceof Error ? e.message : '加载数据状态失败';
      }
      return null;
    } finally {
      if (!destroyed && requestId === inventoryRequest && requestRevision === revision) loading = false;
    }
  }

  function changeRegion() {
    revision += 1;
    status = null;
    dlGfs = { loading: false, message: '', kind: 'info', completed: 0, total: 0 };
    dlEc = { ...dlGfs };
    dlCams = { ...dlGfs };
    void loadStatus();
  }

  let pruning = false;
  let pruneMsg = '';

  async function pruneCache() {
    if (!confirm('确认清理过期缓存文件（默认清理 2 天前的）？此操作不可撤销。')) return;
    pruning = true;
    pruneMsg = '';
    try {
      const res = await post<{ message: string; removed?: string[]; failed?: string[] }>('/api/v1/firesky/data/prune', { max_age_days: 2 });
      const removed = res.removed?.length ?? 0;
      const failed = res.failed?.length ?? 0;
      pruneMsg = `清理完成：删除 ${removed} 个文件` + (failed ? `，${failed} 个失败` : '');
      await loadStatus();
    } catch (e) {
      pruneMsg = '清理失败：' + (e instanceof Error ? e.message : '网络错误');
    } finally {
      pruning = false;
    }
  }

  onMount(async () => {
    await loadRegions();
    await loadStatus();
  });

  function datasetLabel(source: string): string {
    switch (source) {
      case 'gfs': return 'GFS';
      case 'ecmwf': return 'ECMWF';
      case 'cams': return 'CAMS';
      default: return source;
    }
  }
</script>

<div class="data-page">
  <div class="page-header">
    <div class="header-left">
      <Database class="w-5 h-5 text-muted-foreground" />
      <h1>数据资产</h1>
    </div>
    <div class="header-actions">
      <button class="refresh-btn" onclick={pruneCache} disabled={pruning} title="删除 2 天前的过期缓存文件（.grib/.json/.gz 等），清理后请点刷新">
        <Trash2 class="w-4 h-4" />
        {pruning ? '清理中…' : '清理过期缓存'}
      </button>
      <button class="refresh-btn" onclick={loadStatus} disabled={loading}>
        <span class:spinning={loading}><RefreshCw class="w-4 h-4" /></span>
        {loading ? '加载中…' : '刷新'}
      </button>
    </div>
  </div>

  {#if error}
    <div class="error-banner">{error}</div>
  {/if}

  {#if pruneMsg}
    <div class="error-banner" style="border-color:#10b981; background:#ecfdf5; color:#065f46">{pruneMsg}</div>
  {/if}

  <!-- Download section -->
  <div class="download-section">
    <h2 class="download-title">
      <DownloadCloud class="w-4 h-4" />
      下载最新数据
    </h2>

    <!-- Region selector -->
    <div class="region-section">
      <div class="region-label">
        <Globe class="w-3.5 h-3.5" />
        下载区域
      </div>
      <div class="region-row">
        <select class="region-select" bind:value={selectedRegion} onchange={changeRegion}>
          {#each regions as r}
            <option value={r.code}>{r.name}</option>
          {/each}
        </select>
        {#if selectedRegion}
          {@const r = regions.find((x) => x.code === selectedRegion)}
          {#if r}
            <span class="region-note">
              GFS/CAMS 矩形裁剪 · [{r.west}, {r.south}, {r.east}, {r.north}] · {r.timezone}
            </span>
          {/if}
        {/if}
      </div>
    </div>

    <div class="download-grid">
      {#each [['gfs', '#3b82f6', dlGfs], ['ecmwf', '#8b5cf6', dlEc], ['cams', '#f59e0b', dlCams]] as [source, color, dl]}
        {@const ds = status?.datasets?.find(d => d.source === (source as string))}
        {@const isUnavailable = ds?.capability_status === 'unavailable'}
        {@const hasLocal = !!ds?.latest_local_cycle}
        {@const hasUpdate = hasLocal && !!ds?.latest_available_cycle && cycleLabel(ds.latest_available_cycle) !== cycleLabel(ds?.latest_local_cycle || '')}
        {@const hasMissing = hasLocal && !hasUpdate && ds?.missing_hours?.length > 0}
        {@const hasNoData = !hasLocal && !isUnavailable}
        <div class="dl-card glass-panel">
          <div class="dl-head">
            <div class="dl-badge" style="background: {color}20; color: {color};">
              <span class="dl-letter">{(source as string)[0].toUpperCase()}</span>
            </div>
            <div class="dl-info">
              <span class="dl-name">{datasetLabel(source as string)}</span>
              <span class="dl-cycle">最新: {cycleLabel(ds?.latest_available_cycle || '')}</span>
            </div>
              <button class="dl-btn" style="--dl-color: {color}" onclick={() => downloadFor(source as string)} disabled={(dl as DownloadStatus).loading || isUnavailable}>
                {#if (dl as DownloadStatus).loading}<span class="spinning"><RefreshCw class="w-3.5 h-3.5" /></span>{/if}
                {(dl as DownloadStatus).loading ? '下载中' : isUnavailable ? '不可用' : hasNoData ? '下载' : hasUpdate ? '更新' : '检查并补全'}
              </button>
          </div>
          {#if !(dl as DownloadStatus).loading && ds}
            {#if isUnavailable || ds.capability_status === 'partial'}
              <div class="dl-hint">
                <Info class="w-3 h-3" />
                {ds.capability_message || '当前周期不覆盖所选事件'}
              </div>
            {:else if hasNoData}
              <div class="dl-hint">
                <Info class="w-3 h-3" />
                无数据
              </div>
            {:else if hasUpdate}
              <div class="dl-hint">
                <Info class="w-3 h-3" />
                本地: {cycleLabel(ds.latest_local_cycle)}
                {#if ds.missing_hours?.length > 0}
                  · 缺 {ds.missing_hours.length} 个时效
                {/if}
              </div>
            {:else if hasMissing}
              <div class="dl-hint">
                <Info class="w-3 h-3" />
                {#if ds.prepared === false && ds.prepared_message}
                  {ds.prepared_message}
                {:else}
                  缺 {ds.missing_hours.length} 个时效
                {/if}
              </div>
            {:else if ds.prepared === false}
              <div class="dl-hint"><Info class="w-3 h-3" />库存不代表预测输入完整</div>
            {/if}
          {/if}
          {#if (dl as DownloadStatus).loading && (dl as DownloadStatus).total > 0}
            <div class="dl-progress">
              <div class="dl-progress-bar" style="width: {(dl as DownloadStatus).total > 0 ? (dl as DownloadStatus).completed / (dl as DownloadStatus).total * 100 : 0}%; background: {color};"></div>
            </div>
          {/if}
          {#if (dl as DownloadStatus).message}
            <div class="dl-status" class:success={(dl as DownloadStatus).kind === 'success'} class:error={(dl as DownloadStatus).kind === 'error'}>
              {(dl as DownloadStatus).message}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  </div>

  {#if status}
    <div class="scan-time">
      <Clock class="w-3.5 h-3.5" />
      检查时间: {new Date(status.checked_at).toLocaleString('zh-CN')}
      {#if status.total_cache_size_mb > 0}
        · 共 {status.total_cache_size_mb.toFixed(1)} MB
      {/if}
      <span class="region-pill">
        <Globe class="w-3 h-3" />
        {regions.find((r) => r.code === selectedRegion)?.name || selectedRegion} 区域
      </span>
    </div>

    <!-- Data inventory cards -->
    <div class="dataset-grid">
      {#each [['gfs', '#3b82f6', 'GFS', 'NOAA GFS 0.25° 全球预报'], ['ecmwf', '#8b5cf6', 'ECMWF', 'ECMWF IFS 0.25° 全球预报'], ['cams', '#f59e0b', 'CAMS', 'Copernicus 气溶胶 AOD550']] as [source, color, label, desc]}
        {@const ds = status.datasets?.find(d => d.source === (source as string))}
        {#if ds}
          <div class="dataset-card glass-panel">
            <div class="card-header">
              <div class="card-icon" style="background: {color}20; color: {color};">
                <span class="icon-letter">{(source as string)[0].toUpperCase()}</span>
              </div>
              <div class="card-title-area">
                <h2>{label as string}</h2>
                <p class="description">{desc as string}</p>
              </div>
              {#if ds.latest_local_cycle}
                <div class="latest-badge" style="border-color: {color};">
                  {ds.latest_local_cycle}
                </div>
              {:else}
                <div class="latest-badge empty">无数据</div>
              {/if}
            </div>

            <div class="card-stats">
              <div class="stat">
                <span class="stat-value">{ds.local_files}</span>
                <span class="stat-label">文件</span>
              </div>
              <div class="stat">
                <span class="stat-value">{ds.cycles?.reduce((sum, c) => sum + c.total_size_mb, 0).toFixed(1) || 0}</span>
                <span class="stat-label">MB</span>
              </div>
              <div class="stat">
                <span class="stat-value">{ds.cycles?.length || 0}</span>
                <span class="stat-label">时次</span>
              </div>
            </div>

            {#if ds.cycles?.length}
              <div class="cycles-section">
                {#each ds.cycles as ci}
                  {@const key = `${source}-${ci.cycle}`}
                  <button class="cycle-row" onclick={() => toggleCycle(key)}>
                    <div class="cycle-main">
                      {#if expandedCycle[key]}
                        <ChevronDown class="w-3.5 h-3.5 text-muted-foreground" />
                      {:else}
                        <ChevronRight class="w-3.5 h-3.5 text-muted-foreground" />
                      {/if}
                      <span class="cycle-name">{ci.cycle}</span>
                      <span class="cycle-meta">{ci.file_count} 个文件 · {ci.total_size_mb} MB</span>
                    </div>
                    {#if expandedCycle[key]}
                      <div class="cycle-detail">
                        <span class="hours-label">预报时效:</span>
                        <span class="hours-list">{formatHours(ci.forecast_hours)}</span>
                      </div>
                    {/if}
                  </button>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      {/each}
    </div>

    {#if status.derived?.some(d => d.file_count > 0)}
      <h3 class="section-title">
        <HardDrive class="w-4 h-4" />
        派生缓存
      </h3>
      <div class="derived-grid">
        {#each status.derived as d}
          <div class="derived-card glass-panel">
            <div class="derived-header">
              <span class="derived-label">{d.label}</span>
              {#if d.latest_file}
                <span class="derived-time">{d.latest_file}</span>
              {/if}
            </div>
            <div class="derived-stats">
              <span>{d.file_count} 文件</span>
              <span>{d.total_size_mb} MB</span>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  {:else if loading}
    <div class="loading-state">
      <span class="spinning"><RefreshCw class="w-5 h-5" /></span>
      <span>加载数据状态…</span>
    </div>
  {:else}
    <div class="loading-state">
      <span>暂无数据</span>
    </div>
  {/if}
</div>

<style>
  .data-page {
    padding: 16px 20px;
    max-width: 960px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .header-left h1 {
    font-size: 18px;
    font-weight: 600;
    margin: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .refresh-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--background);
    color: var(--foreground);
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
  }

  .refresh-btn:hover { background: var(--accent); }
  .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .spinning { animation: spin 1s linear infinite; display: inline-flex; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .error-banner {
    padding: 10px 14px;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 6px;
    color: #dc2626;
    font-size: 13px;
  }

  .loading-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 60px 0;
    color: var(--muted-foreground);
    font-size: 14px;
  }

  /* ── Download section ── */

  .download-section {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .download-title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 14px;
    font-weight: 600;
    margin: 0;
    color: var(--muted-foreground);
  }

  .download-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
  }

  .dl-card {
    border: 1px solid var(--glass-border);
    border-radius: 10px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .dl-head {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .dl-badge {
    width: 34px;
    height: 34px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    font-weight: 700;
  }

  .dl-letter { font-size: 16px; }

  .dl-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .dl-name {
    font-size: 13px;
    font-weight: 600;
  }

  .dl-cycle {
    font-size: 11px;
    color: var(--muted-foreground);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .dl-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 5px 12px;
    border: 1px solid var(--dl-color, #3b82f6);
    border-radius: 6px;
    background: var(--dl-color, #3b82f6);
    color: #fff;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
    transition: opacity 0.15s;
  }

  .dl-btn:hover { opacity: 0.85; }
  .dl-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .dl-hint {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: #d97706;
    margin-top: -4px;
    padding-left: 44px;
  }

  .dl-progress {
    height: 4px;
    border-radius: 2px;
    background: var(--border);
    overflow: hidden;
  }

  .dl-progress-bar {
    height: 100%;
    border-radius: 2px;
    transition: width 0.4s ease;
  }

  .dl-status {
    font-size: 12px;
    padding: 5px 8px;
    border-radius: 4px;
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    color: #1d4ed8;
  }

  .dl-status.success {
    background: #f0fdf4;
    border-color: #bbf7d0;
    color: #15803d;
  }

  .dl-status.error {
    background: #fef2f2;
    border-color: #fecaca;
    color: #dc2626;
  }

  /* ── Inventory ── */

  .dataset-grid {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .dataset-card {
    border: 1px solid var(--glass-border);
    border-radius: 10px;
    overflow: hidden;
  }

  .card-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
  }

  .card-icon {
    width: 40px;
    height: 40px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    font-weight: 700;
  }

  .icon-letter { font-size: 18px; }

  .card-title-area {
    flex: 1;
    min-width: 0;
  }

  .card-title-area h2 {
    font-size: 15px;
    font-weight: 600;
    margin: 0;
  }

  .description {
    font-size: 12px;
    color: var(--muted-foreground);
    margin: 2px 0 0;
  }

  .latest-badge {
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid;
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .latest-badge.empty {
    border-color: var(--border);
    color: var(--muted-foreground);
  }

  .card-stats {
    display: flex;
    gap: 0;
    border-top: 1px solid var(--border);
  }

  .stat {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 10px 0;
    border-right: 1px solid var(--border);
  }

  .stat:last-child { border-right: none; }

  .stat-value {
    font-size: 16px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .stat-label {
    font-size: 11px;
    color: var(--muted-foreground);
    margin-top: 2px;
  }

  .cycles-section {
    border-top: 1px solid var(--border);
    max-height: 300px;
    overflow-y: auto;
  }

  .cycle-row {
    display: block;
    width: 100%;
    text-align: left;
    padding: 8px 16px;
    border: none;
    border-bottom: 1px solid var(--border);
    background: none;
    cursor: pointer;
    transition: background 0.1s;
    font: inherit;
    color: inherit;
  }

  .cycle-row:last-child { border-bottom: none; }
  .cycle-row:hover { background: var(--accent); }

  .cycle-main {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .cycle-name {
    font-size: 13px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }

  .cycle-meta {
    font-size: 12px;
    color: var(--muted-foreground);
    margin-left: auto;
  }

  .cycle-detail {
    padding: 6px 0 2px 24px;
    font-size: 12px;
    color: var(--muted-foreground);
  }

  .hours-label { flex-shrink: 0; }

  .hours-list {
    word-break: break-all;
    font-variant-numeric: tabular-nums;
  }

  /* ── Status ── */

  .scan-time {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--muted-foreground);
    flex-wrap: wrap;
  }

  .region-pill {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    padding: 1px 8px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--accent);
    font-size: 11px;
  }

  .section-title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 14px;
    font-weight: 600;
    margin: 4px 0 0;
  }

  .derived-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 10px;
  }

  .derived-card {
    border: 1px solid var(--glass-border);
    border-radius: 8px;
    padding: 12px 14px;
  }

  .derived-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 6px;
  }

  .derived-label {
    font-size: 13px;
    font-weight: 500;
  }

  .derived-time {
    font-size: 11px;
    color: var(--muted-foreground);
  }

  .derived-stats {
    display: flex;
    gap: 12px;
    font-size: 12px;
    color: var(--muted-foreground);
    font-variant-numeric: tabular-nums;
  }

  /* ── Region selector ── */

  .region-section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .region-label {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted-foreground);
  }

  .region-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .region-select {
    padding: 6px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--background);
    color: var(--foreground);
    font-size: 13px;
    cursor: pointer;
    min-width: 200px;
  }

  .region-note {
    font-size: 12px;
    color: var(--muted-foreground);
    font-variant-numeric: tabular-nums;
  }

  @media (max-width: 1023px) {
    .data-page {
      padding: 12px 16px;
    }

    .page-header {
      align-items: flex-start;
      gap: 10px;
    }

    .header-actions {
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .download-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .card-header {
      flex-wrap: wrap;
    }

    .card-title-area {
      min-width: 0;
      flex: 1 1 calc(100% - 96px);
    }

    .latest-badge {
      margin-left: auto;
    }

    .cycle-main {
      flex-wrap: wrap;
    }

    .cycle-meta {
      margin-left: 24px;
      width: calc(100% - 24px);
    }

    .derived-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 640px) {
    .data-page {
      padding: 10px 12px;
    }

    .page-header {
      flex-direction: column;
    }

    .header-actions {
      width: 100%;
      justify-content: stretch;
    }

    .header-actions .refresh-btn {
      flex: 1;
      justify-content: center;
    }

    .download-grid { grid-template-columns: 1fr; }

    .region-select {
      min-width: 0;
      width: 100%;
    }

    .region-note {
      width: 100%;
      word-break: break-word;
    }

    .derived-grid { grid-template-columns: 1fr; }

    .card-header {
      align-items: flex-start;
    }

    .card-title-area {
      min-width: 0;
      flex: 1 1 calc(100% - 96px);
    }

    .latest-badge {
      margin-left: 0;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .cycle-meta {
      margin-left: 24px;
    }
  }
</style>
