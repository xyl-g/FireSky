<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { goto } from '$app/navigation';
  import { listForecastJobs, getForecastJob } from '$lib/api/jobs';
  import { ApiClientError } from '$lib/api/client';
  import { openSSE } from '$lib/api/client';
  import { regionalJobs } from '$lib/stores/compute-jobs.svelte';
  import type { ForecastJob, ForecastJobStatus } from '$lib/api/types';
  import type { RegionalJob } from '$lib/stores/compute-jobs.svelte';
  import Inbox from '@lucide/svelte/icons/inbox';
  import RefreshCw from '@lucide/svelte/icons/refresh-cw';
  import Loader2 from '@lucide/svelte/icons/loader-2';
  import CheckCircle from '@lucide/svelte/icons/check-circle';
  import XCircle from '@lucide/svelte/icons/x-circle';
  import Clock from '@lucide/svelte/icons/clock';
  import Cpu from '@lucide/svelte/icons/cpu';
  import ChevronRight from '@lucide/svelte/icons/chevron-right';
  import Map from '@lucide/svelte/icons/map';
  import Sun from '@lucide/svelte/icons/sun';
  import Sunrise from '@lucide/svelte/icons/sunrise';
  import Sunset from '@lucide/svelte/icons/sunset';
  import ArrowLeft from '@lucide/svelte/icons/arrow-left';
  import Filter from '@lucide/svelte/icons/filter';

  // ══════════════════════════════════════
  // State
  // ══════════════════════════════════════

  // Go dispatcher jobs
  let goJobs: ForecastJob[] = $state([]);
  let goJobsLoading = $state(true);
  let goJobsError: string | null = $state(null);
  let goJobsTotal = $state(0);

  // Regional jobs from store
  let rJobs: RegionalJob[] = $state([]);
  const unsub = regionalJobs.subscribe(v => { rJobs = v; });

  // Detail view
  let selectedJob: ForecastJob | null = $state(null);
  let selectedRegional: RegionalJob | null = $state(null);
  let detailLoading = $state(false);

  // SSE connection for live job tracking
  let liveSource: EventSource | null = null;
  let liveProgress = $state({ percent: 0, stage: '', message: '', elapsed: 0 });
  // SSE is the low-latency path, but a proxy/browser reconnect can lose the
  // only event that moved a long-running job forward. Keep the detail view
  // reconciled with the durable job row while it is active.
  let selectedPollTimer: ReturnType<typeof setInterval> | null = null;
  let selectedPollInFlight = false;

  // Filter
  let statusFilter = $state<string>('');

  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const STATUS_LABELS: Record<ForecastJobStatus, string> = {
    running: '计算中',
    done: '完成',
    failed: '失败',
    cancelled: '已取消',
  };

  const STATUS_COLORS: Record<ForecastJobStatus, string> = {
    running: 'bg-primary/10 text-primary dark:text-primary',
    done: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    failed: 'bg-red-500/10 text-red-600 dark:text-red-400',
    cancelled: 'bg-muted text-muted-foreground',
  };

  const REGIONAL_STATUS_COLORS: Record<string, string> = {
    running: 'bg-primary/10 text-primary dark:text-primary',
    done: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    failed: 'bg-red-500/10 text-red-600 dark:text-red-400',
    cancelled: 'bg-muted text-muted-foreground',
  };

  // ══════════════════════════════════════
  // Data loading
  // ══════════════════════════════════════

  async function loadGoJobs() {
    try {
      const resp = await listForecastJobs({ limit: 50, offset: 0, status: statusFilter || undefined });
      goJobs = resp.items || [];
      goJobsTotal = resp.total;
      goJobsError = null;
    } catch (e) {
      if (e instanceof ApiClientError) {
        goJobsError = e.apiError?.error || '加载任务列表失败';
      } else {
        goJobsError = '网络错误';
      }
    } finally {
      goJobsLoading = false;
    }
  }

  async function selectGoJob(job: ForecastJob) {
    selectedRegional = null;
    selectedJob = null;
    detailLoading = true;
    stopLive();
    stopSelectedPolling();
    try {
      const detail = await getForecastJob(job.id);
      selectedJob = detail;
      // If job is active, subscribe to SSE
      if (isActive(detail.status)) {
        startLive(detail.id);
        startSelectedPolling(detail.id);
      }
    } catch (e) {
      goJobsError = e instanceof Error ? e.message : '加载详情失败';
    } finally {
      detailLoading = false;
    }
  }

  function selectRegionalJob(job: RegionalJob) {
    selectedJob = null;
    selectedRegional = job;
    stopLive();
    stopSelectedPolling();
  }

  function backToList() {
    selectedJob = null;
    selectedRegional = null;
    stopLive();
    stopSelectedPolling();
  }

  // ══════════════════════════════════════
  // Live SSE tracking
  // ══════════════════════════════════════

  function startLive(jobId: string) {
    stopLive();
    liveProgress = { percent: selectedJob?.progress_percent || 0, stage: selectedJob?.progress_stage || '', message: selectedJob?.progress_message || '', elapsed: 0 };
    liveSource = openSSE('/api/v1/firesky/forecasts/events', { job_id: jobId }, {
      onEvent: (eventName, eventData: unknown) => {
        const d = eventData as Record<string, unknown>;
        if (eventName === 'progress') {
          liveProgress = {
            percent: (d.percent as number) || 0,
            stage: (d.stage as string) || '',
            message: (d.message as string) || '',
            elapsed: (d.elapsed_seconds as number) || 0,
          };
        } else if (eventName === 'done') {
          liveProgress = { ...liveProgress, percent: 100, stage: 'done', message: '计算完成' };
          stopLive();
          stopSelectedPolling();
          loadGoJobs();
          if (selectedJob) {
            selectedJob = { ...selectedJob, status: 'done', progress_percent: 100 };
          }
          void refreshSelectedJob(jobId);
        } else if (eventName === 'failed') {
          liveProgress = { ...liveProgress, percent: 100, stage: 'failed', message: (d.message as string) || '失败' };
          stopLive();
          stopSelectedPolling();
          loadGoJobs();
          if (selectedJob) {
            selectedJob = { ...selectedJob, status: 'failed', error_message: (d.message as string) || '失败' };
          }
        }
      },
      onError: () => {
        // EventSource retries by itself. Reconcile immediately as well so a
        // dropped stream cannot leave the detail card on an old percentage.
        void refreshSelectedJob(jobId);
      },
    });
  }

  function startSelectedPolling(jobId: string) {
    stopSelectedPolling();
    selectedPollTimer = setInterval(() => {
      void refreshSelectedJob(jobId);
    }, 2000);
  }

  function stopSelectedPolling() {
    if (selectedPollTimer) {
      clearInterval(selectedPollTimer);
      selectedPollTimer = null;
    }
  }

  async function refreshSelectedJob(jobId: string) {
    if (selectedPollInFlight) return;
    selectedPollInFlight = true;
    try {
      const detail = await getForecastJob(jobId);
      if (selectedJob?.id === jobId) {
        selectedJob = detail;
        liveProgress = {
          ...liveProgress,
          percent: isTerminalStatus(detail.status) ? 100 : detail.progress_percent,
          stage: detail.progress_stage || 'done',
          message: detail.progress_message || '计算完成',
        };
        if (isTerminalStatus(detail.status)) {
          stopSelectedPolling();
          stopLive();
          loadGoJobs();
        }
      }
    } catch {
      // The terminal SSE event already provides a usable local state. Keep it
      // visible if the detail request races with persistence.
    } finally {
      selectedPollInFlight = false;
    }
  }

  function isTerminalStatus(status: ForecastJobStatus): boolean {
    return status === 'done' || status === 'failed' || status === 'cancelled';
  }

  function stopLive() {
    if (liveSource) {
      liveSource.close();
      liveSource = null;
    }
  }

  // ══════════════════════════════════════
  // Helpers
  // ══════════════════════════════════════

  function formatTime(iso: string | null): string {
    if (!iso) return '-';
    try {
      return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return iso; }
  }

  function formatElapsed(s: number): string {
    if (!s || s < 1) return '-';
    if (s < 60) return `${Math.round(s)}s`;
    return `${Math.floor(s / 60)}m${Math.floor(s % 60)}s`;
  }

  function timeDiff(start: string | null, end: string | null): string {
    if (!start) return '-';
    const s = new Date(start).getTime();
    const e = end ? new Date(end).getTime() : Date.now();
    return formatElapsed((e - s) / 1000);
  }

  function parsePayload(payload: string): Record<string, unknown> | null {
    try { return JSON.parse(payload); } catch { return null; }
  }

  function jobLabel(job: ForecastJob): string {
    const p = parsePayload(job.request_payload);
    if (!p) return job.id.slice(0, 8);
    const date = p.target_date || '';
    const model = (p.model_source as string)?.toUpperCase() || '';
    const ph = p.phenomenon === 'dawn' ? '朝霞' : '晚霞';
    return `${model} ${ph} ${date}`;
  }

  function scoreColor(score: number): string {
    if (score >= 0.7) return '#dc2626';
    if (score >= 0.5) return '#ea580c';
    if (score >= 0.3) return '#d97706';
    if (score >= 0.1) return '#65a30d';
    return '#6b7280';
  }

  function isActive(status: string): boolean {
    return status === 'running';
  }

  let activeGoJobs = $derived(goJobs.filter(j => isActive(j.status)));
  let activeRegionalJobs = $derived(rJobs.filter(j => j.status === 'running'));

  onMount(() => {
    loadGoJobs();
    pollTimer = setInterval(loadGoJobs, 10000);
  });

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer);
    stopLive();
    stopSelectedPolling();
    unsub();
  });
</script>

<div class="max-w-5xl mx-auto p-4 sm:p-6 space-y-6">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <div>
      <h1 class="text-xl font-bold flex items-center gap-2">
        <Inbox class="w-5 h-5 text-primary" />
        计算队列
      </h1>
      <p class="text-sm text-muted-foreground mt-0.5">
        {#if activeGoJobs.length > 0 || activeRegionalJobs.length > 0}
          {activeGoJobs.length + activeRegionalJobs.length} 个任务进行中
        {:else}
          所有任务已完成或暂无任务
        {/if}
      </p>
    </div>
    {#if !selectedJob && !selectedRegional}
      <button
        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border hover:bg-muted transition-colors"
        onclick={loadGoJobs}
        disabled={goJobsLoading}
      >
        <RefreshCw class="w-3.5 h-3.5 {goJobsLoading ? 'animate-spin' : ''}" />
        刷新
      </button>
    {/if}
  </div>

  <!-- Error -->
  {#if goJobsError}
    <div class="rounded-lg border border-destructive/30 bg-destructive/5 p-3 flex items-center justify-between">
      <p class="text-sm text-destructive">{goJobsError}</p>
      <button class="text-destructive/60 hover:text-destructive text-xs" onclick={() => goJobsError = null}>关闭</button>
    </div>
  {/if}

  <!-- ═══════════════════════════════ -->
  <!-- Detail view                     -->
  <!-- ═══════════════════════════════ -->
  {#if selectedJob}
    <button class="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors" onclick={backToList}>
      <ArrowLeft class="w-4 h-4" />
      返回列表
    </button>

    <div class="glass-panel rounded-xl border overflow-hidden">
      <!-- Job header -->
      <div class="px-5 py-4 border-b bg-muted/30">
        <div class="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 class="font-semibold">{jobLabel(selectedJob)}</h2>
            <p class="text-xs text-muted-foreground font-mono mt-0.5">{selectedJob.id}</p>
          </div>
          <span class="inline-flex px-2 py-1 rounded-md text-xs font-medium {STATUS_COLORS[selectedJob.status]}">
            {STATUS_LABELS[selectedJob.status]}
          </span>
        </div>
      </div>

      <!-- Progress (live if active) -->
      {#if isActive(selectedJob.status) || selectedJob.status === 'done'}
        <div class="px-5 py-4 border-b space-y-3">
          <div class="flex items-center justify-between text-sm">
            <span class="text-muted-foreground">{liveProgress.stage || selectedJob.progress_stage || '-'}</span>
            <span class="font-mono">{liveProgress.percent || selectedJob.progress_percent}%</span>
          </div>
          <div class="h-2 rounded-full bg-muted overflow-hidden">
            <div
              class="h-full rounded-full transition-all duration-500 {selectedJob.status === 'done' ? 'bg-emerald-500' : 'bg-primary'}"
              style="width: {liveProgress.percent || selectedJob.progress_percent}%"
            ></div>
          </div>
          <p class="text-xs text-muted-foreground">{liveProgress.message || selectedJob.progress_message || ''}</p>
          {#if liveProgress.elapsed > 0}
            <p class="text-xs text-muted-foreground/60">已耗时 {formatElapsed(liveProgress.elapsed)}</p>
          {/if}
        </div>
      {/if}

      <!-- Error -->
      {#if selectedJob.error_message}
        <div class="px-5 py-3 border-b bg-destructive/5">
          <p class="text-sm text-destructive">{selectedJob.error_message}</p>
        </div>
      {/if}

      <!-- Metadata -->
      <div class="px-5 py-4 grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
        <div>
          <span class="text-muted-foreground">创建时间</span>
          <p class="font-mono text-xs mt-0.5">{formatTime(selectedJob.created_at)}</p>
        </div>
        <div>
          <span class="text-muted-foreground">开始时间</span>
          <p class="font-mono text-xs mt-0.5">{formatTime(selectedJob.started_at)}</p>
        </div>
        <div>
          <span class="text-muted-foreground">完成时间</span>
          <p class="font-mono text-xs mt-0.5">{formatTime(selectedJob.completed_at)}</p>
        </div>
        <div>
          <span class="text-muted-foreground">耗时</span>
          <p class="font-mono text-xs mt-0.5">{timeDiff(selectedJob.started_at, selectedJob.completed_at)}</p>
        </div>
        <div>
          <span class="text-muted-foreground">算法版本</span>
          <p class="font-mono text-xs mt-0.5">{selectedJob.algorithm_version || '-'}</p>
        </div>
        <div>
          <span class="text-muted-foreground">缓存键</span>
          <p class="font-mono text-xs mt-0.5 truncate">{selectedJob.cache_key || '-'}</p>
        </div>
      </div>

      <!-- Request payload -->
      {#if selectedJob.request_payload}
        {@const p = parsePayload(selectedJob.request_payload)}
        {#if p}
          <div class="px-5 py-4 border-t">
            <h3 class="text-xs font-medium text-muted-foreground mb-2">请求参数</h3>
            <div class="rounded-lg bg-muted/50 p-3 font-mono text-xs space-y-1 [overflow-wrap:anywhere]">
              {#each Object.entries(p) as [key, val]}
                <div><span class="text-muted-foreground">{key}:</span> <span>{typeof val === 'object' ? JSON.stringify(val) : String(val)}</span></div>
              {/each}
            </div>
          </div>
        {/if}
      {/if}
    </div>

  <!-- ═══════════════════════════════ -->
  <!-- Regional job detail             -->
  <!-- ═══════════════════════════════ -->
  {:else if selectedRegional}
    <button class="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors" onclick={backToList}>
      <ArrowLeft class="w-4 h-4" />
      返回列表
    </button>

    <div class="glass-panel rounded-xl border overflow-hidden">
      <div class="px-5 py-4 border-b bg-muted/30">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="font-semibold">
              {selectedRegional.model_source.toUpperCase()}
              {selectedRegional.phenomenon === 'dawn' ? '朝霞' : '晚霞'}
              {selectedRegional.label}
            </h2>
            <p class="text-xs text-muted-foreground mt-0.5">
              {selectedRegional.regions.join(', ')} · job_id: {selectedRegional.job_id}
            </p>
          </div>
          <span class="inline-flex px-2 py-1 rounded-md text-xs font-medium {REGIONAL_STATUS_COLORS[selectedRegional.status] || ''}">
            {selectedRegional.status === 'running' ? '计算中' : selectedRegional.status === 'done' ? '完成' : selectedRegional.status === 'failed' ? '失败' : selectedRegional.status === 'cancelled' ? '已取消' : '排队中'}
          </span>
        </div>
      </div>

      <div class="px-5 py-4 border-b space-y-3">
        <div class="flex items-center justify-between text-sm">
          <span class="text-muted-foreground">{selectedRegional.stage || '-'}</span>
          <span class="font-mono">{selectedRegional.progress}%</span>
        </div>
        <div class="h-2 rounded-full bg-muted overflow-hidden">
          <div
            class="h-full rounded-full transition-all duration-500 {selectedRegional.status === 'done' ? 'bg-emerald-500' : 'bg-primary'}"
            style="width: {selectedRegional.progress}%"
          ></div>
        </div>
        <p class="text-xs text-muted-foreground">{selectedRegional.message}</p>
        <p class="text-xs text-muted-foreground/60">耗时 {formatElapsed(selectedRegional.elapsed)}</p>
      </div>

      {#if selectedRegional.result_summary}
        {@const s = selectedRegional.result_summary}
        <div class="px-5 py-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div class="text-center rounded-lg bg-muted/50 p-3">
            <div class="text-lg font-bold">{s.point_count}</div>
            <div class="text-[11px] text-muted-foreground">网格点</div>
          </div>
          <div class="text-center rounded-lg bg-muted/50 p-3">
            <div class="text-lg font-bold" style="color: {scoreColor(s.avg_score)}">{(s.avg_score * 100).toFixed(1)}</div>
            <div class="text-[11px] text-muted-foreground">平均分</div>
          </div>
          <div class="text-center rounded-lg bg-muted/50 p-3">
            <div class="text-lg font-bold" style="color: {scoreColor(s.max_score)}">{(s.max_score * 100).toFixed(1)}</div>
            <div class="text-[11px] text-muted-foreground">最高分</div>
          </div>
        </div>
        <div class="px-5 pb-4 text-xs text-muted-foreground">
          成功 {s.success_count} · 失败 {s.error_count}
        </div>
      {/if}

      {#if selectedRegional.error}
        <div class="px-5 py-3 border-t bg-destructive/5">
          <p class="text-sm text-destructive">{selectedRegional.error}</p>
        </div>
      {/if}
    </div>

  <!-- ═══════════════════════════════ -->
  <!-- List view                       -->
  <!-- ═══════════════════════════════ -->
  {:else}
    <!-- Filter -->
    <div class="flex flex-wrap items-center gap-2">
      <Filter class="w-3.5 h-3.5 text-muted-foreground" />
      {#each ['', 'running', 'done', 'failed'] as s}
        <button
          class="px-2.5 py-1 rounded-md text-xs font-medium transition-colors
            {statusFilter === s ? 'bg-primary text-primary-foreground' : 'border hover:bg-muted'}"
          onclick={() => { statusFilter = s; loadGoJobs(); }}
        >
          {s === '' ? '全部' : STATUS_LABELS[s as ForecastJobStatus]}
        </button>
      {/each}
    </div>

    <!-- Regional compute jobs (from store) -->
    {#if rJobs.length > 0}
      <section class="space-y-3">
        <h2 class="text-sm font-semibold flex items-center gap-2">
          <Map class="w-4 h-4 text-amber-500" />
          区域网格计算
          <span class="text-xs font-normal text-muted-foreground">（当前会话）</span>
        </h2>
        <div class="glass-panel rounded-xl border overflow-hidden divide-y">
          {#each rJobs as job}
            <button
              class="w-full px-4 py-3 flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:gap-3 hover:bg-muted/30 transition-colors text-left"
              onclick={() => selectRegionalJob(job)}
            >
              <!-- Status icon -->
              {#if job.status === 'running'}
                <Loader2 class="w-4 h-4 text-primary animate-spin shrink-0" />
              {:else if job.status === 'done'}
                <CheckCircle class="w-4 h-4 text-emerald-500 shrink-0" />
              {:else if job.status === 'failed'}
                <XCircle class="w-4 h-4 text-destructive shrink-0" />
              {:else if job.status === 'cancelled'}
                <XCircle class="w-4 h-4 text-muted-foreground shrink-0" />
              {:else}
                <Clock class="w-4 h-4 text-muted-foreground shrink-0" />
              {/if}

              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium">
                    {job.model_source.toUpperCase()}
                    {job.phenomenon === 'dawn' ? '朝霞' : '晚霞'}
                    {job.label}
                  </span>
                  <span class="text-[11px] text-muted-foreground font-mono">{job.date}</span>
                </div>
                <div class="flex items-center gap-2 mt-0.5">
                  <span class="text-xs text-muted-foreground">{job.regions.join(', ')}</span>
                  {#if job.result_summary}
                    <span class="text-xs text-muted-foreground/60">·</span>
                    <span class="text-xs" style="color: {scoreColor(job.result_summary.avg_score)}">
                      均分 {(job.result_summary.avg_score * 100).toFixed(1)}
                    </span>
                    <span class="text-xs text-muted-foreground/60">·</span>
                    <span class="text-xs text-muted-foreground">{job.result_summary.point_count} 点</span>
                  {/if}
                </div>
              </div>

              <!-- Progress / status -->
              <div class="w-full sm:w-auto sm:shrink-0 text-left sm:text-right">
                {#if job.status === 'running'}
                  <div class="text-xs font-mono text-primary">{job.progress}%</div>
                  <div class="w-16 h-1 rounded-full bg-muted mt-1 overflow-hidden">
                    <div class="h-full rounded-full bg-primary" style="width: {job.progress}%"></div>
                  </div>
                {:else}
                  <span class="inline-flex px-2 py-0.5 rounded text-[10px] font-medium {REGIONAL_STATUS_COLORS[job.status] || ''}">
                    {job.status === 'done' ? '完成' : job.status === 'failed' ? '失败' : job.status === 'cancelled' ? '已取消' : '排队中'}
                  </span>
                {/if}
              </div>

              <ChevronRight class="w-4 h-4 text-muted-foreground/30 shrink-0" />
            </button>
          {/each}
        </div>
      </section>
    {/if}

    <!-- Go dispatcher jobs -->
    <section class="space-y-3">
      <h2 class="text-sm font-semibold flex items-center gap-2">
        <Cpu class="w-4 h-4 text-violet-500" />
        预测任务
        <span class="text-xs font-normal text-muted-foreground">（共 {goJobsTotal} 个）</span>
      </h2>

      {#if goJobsLoading && goJobs.length === 0}
        <div class="text-center py-8 text-muted-foreground text-sm">加载中...</div>
      {:else if goJobs.length === 0}
        <div class="rounded-xl border border-dashed py-12 text-center">
          <Inbox class="w-6 h-6 mx-auto text-muted-foreground/20 mb-2" />
          <p class="text-sm text-muted-foreground">暂无预测任务</p>
          <p class="text-xs text-muted-foreground/60 mt-1">从预测页面或实时计算页面提交任务后将显示在此处</p>
        </div>
      {:else}
        <div class="glass-panel rounded-xl border overflow-hidden divide-y">
          {#each goJobs as job}
            <button
              class="w-full px-4 py-3 flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:gap-3 hover:bg-muted/30 transition-colors text-left"
              onclick={() => selectGoJob(job)}
            >
              <!-- Status icon -->
              {#if isActive(job.status)}
                <Loader2 class="w-4 h-4 text-primary animate-spin shrink-0" />
              {:else if job.status === 'done'}
                <CheckCircle class="w-4 h-4 text-emerald-500 shrink-0" />
              {:else if job.status === 'failed'}
                <XCircle class="w-4 h-4 text-destructive shrink-0" />
              {:else}
                <Clock class="w-4 h-4 text-muted-foreground shrink-0" />
              {/if}

              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium">{jobLabel(job)}</div>
                <div class="flex items-center gap-2 mt-0.5">
                  <span class="text-xs text-muted-foreground">{formatTime(job.created_at)}</span>
                  <span class="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium {STATUS_COLORS[job.status]}">
                    {STATUS_LABELS[job.status]}
                  </span>
                  {#if job.progress_message}
                    <span class="text-xs text-muted-foreground/60 truncate">{job.progress_message}</span>
                  {/if}
                </div>
              </div>

              <!-- Progress -->
              <div class="w-full sm:w-auto sm:shrink-0 text-left sm:text-right">
                {#if isActive(job.status)}
                  <div class="text-xs font-mono text-primary">{job.progress_percent}%</div>
                  <div class="w-16 h-1 rounded-full bg-muted mt-1 overflow-hidden">
                    <div class="h-full rounded-full bg-primary" style="width: {job.progress_percent}%"></div>
                  </div>
                {:else if job.status === 'done'}
                  <span class="text-xs text-muted-foreground">{timeDiff(job.started_at, job.completed_at)}</span>
                {:else if job.error_message}
                  <span class="text-xs text-destructive truncate max-w-32 block">{job.error_message}</span>
                {/if}
              </div>

              <ChevronRight class="w-4 h-4 text-muted-foreground/30 shrink-0" />
            </button>
          {/each}
        </div>
      {/if}
    </section>
  {/if}
</div>
