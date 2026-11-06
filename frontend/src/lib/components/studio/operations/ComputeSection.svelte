<script lang="ts">
  import { onDestroy } from 'svelte';
  import { startRegionalForecast } from '$lib/api/forecast';
  import {
    cancelForecastJob,
    getForecastJob,
    getForecastResult,
    isTerminalForecastJobStatus,
  } from '$lib/api/jobs';
  import { openSSE } from '$lib/api/client';
  import { ApiClientError } from '$lib/api/client';
  import type {
    Phenomenon,
    RegionalForecastArea,
    RegionalForecastPayload,
    RegionalForecastPoint,
    ForecastJob,
  } from '$lib/api/types';
  import Cpu from '@lucide/svelte/icons/cpu';
  import Play from '@lucide/svelte/icons/play';
  import Loader2 from '@lucide/svelte/icons/loader-2';
  import CheckCircle from '@lucide/svelte/icons/check-circle';
  import XCircle from '@lucide/svelte/icons/x-circle';
  import Sun from '@lucide/svelte/icons/sun';
  import Sunrise from '@lucide/svelte/icons/sunrise';
  import Sunset from '@lucide/svelte/icons/sunset';
  import Database from '@lucide/svelte/icons/database';
  import Map from '@lucide/svelte/icons/map';
  import Clock from '@lucide/svelte/icons/clock';
  import CalendarDays from '@lucide/svelte/icons/calendar-days';
  import AlertCircle from '@lucide/svelte/icons/alert-circle';
  import { regionalJobs } from '$lib/stores/compute-jobs.svelte';
  import { REGION_OPTIONS } from '$lib/api/regions';
  import { chinaDateValue, chinaHour } from '$lib/utils/format';

  // ══════════════════════════════════════
  // Form state
  // ══════════════════════════════════════

  let modelSource = $state<'gfs' | 'ec'>('gfs');
  let phenomenon = $state<Phenomenon>('dusk');
  let regions = $state<RegionalForecastArea[]>(['china']);

  const MODEL_OPTIONS: { value: 'gfs' | 'ec'; label: string; desc: string }[] = [
    { value: 'gfs', label: 'GFS', desc: '全球预报系统 · 逐小时' },
    { value: 'ec', label: 'ECMWF IFS', desc: '独立数值模式 · 3 小时' },
  ];

  const PHENOMENON_OPTIONS: { value: Phenomenon; label: string; icon: typeof Sun }[] = [
    { value: 'dusk', label: '晚霞', icon: Sunset },
    { value: 'dawn', label: '朝霞', icon: Sunrise },
  ];

  // REGION_OPTIONS is imported from $lib/api/regions.ts (the 9 operational
  // regions; this page has no custom-bounds UI).

  // ══════════════════════════════════════
  // Computation state
  // ══════════════════════════════════════

  type JobStatus = 'idle' | 'starting' | 'running' | 'done' | 'failed' | 'cancelled';

  type DayJob = {
    date: string;
    label: string;
    status: JobStatus;
    job_id: string | null;
    progress: number;
    stage: string;
    message: string;
    elapsed: number;
    result: RegionalForecastPayload | null;
    error: string | null;
    source: EventSource | null;
  };

  let jobs: DayJob[] = $state([]);
  let computing = $state(false);
  let runGeneration = 0;
  let activeJobId: string | null = null;
  let activeStoreId: string | null = null;
  const cancelRequestedGenerations = new Set<number>();
  let statusPollTimer: ReturnType<typeof setTimeout> | null = null;
  let stopActiveWatch: (() => void) | null = null;
  const STATUS_POLL_INTERVAL_MS = 1500;
  const DAWN_TODAY_CUTOFF_HOUR = 10;

  function localDateValue(value: Date): string {
    return chinaDateValue(0, value);
  }

  function minimumDateValue(selectedPhenomenon: Phenomenon, now = new Date()): string {
    const offset = selectedPhenomenon === 'dawn' && chinaHour(now) >= DAWN_TODAY_CUTOFF_HOUR ? 1 : 0;
    return chinaDateValue(offset, now);
  }

  function dateLabel(value: string): string {
    if (!value) return '';
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      month: 'long',
      day: 'numeric',
      weekday: 'short',
    }).format(new Date(`${value}T00:00:00Z`));
  }

  let targetDate = $state(localDateValue(new Date()));
  let minimumTargetDate = $derived(minimumDateValue(phenomenon));

  $effect(() => {
    if (!targetDate || targetDate < minimumTargetDate) {
      targetDate = minimumTargetDate;
    }
  });

  function toggleRegion(r: RegionalForecastArea) {
    if (regions.includes(r)) {
      if (regions.length > 1) {
        regions = regions.filter(x => x !== r);
      }
    } else {
      regions = [...regions, r];
    }
  }

  // ══════════════════════════════════════
  // Start computation
  // ══════════════════════════════════════

  function clearStatusPoll() {
    if (statusPollTimer !== null) {
      clearTimeout(statusPollTimer);
      statusPollTimer = null;
    }
  }

  function abortActiveTracking() {
    clearStatusPoll();
    for (const job of jobs) {
      if (job.source) {
        job.source.close();
        job.source = null;
      }
    }
    activeJobId = null;
    activeStoreId = null;
    const release = stopActiveWatch;
    stopActiveWatch = null;
    release?.();
  }

  async function startCompute() {
    if (computing || !targetDate) return;
    const generation = ++runGeneration;
    abortActiveTracking();
    computing = true;

    const newJobs: DayJob[] = [{
      date: targetDate,
      label: dateLabel(targetDate),
      status: 'idle' as JobStatus,
      job_id: null,
      progress: 0,
      stage: '',
      message: '',
      elapsed: 0,
      result: null,
      error: null,
      source: null,
    }];
    jobs = newJobs;

    try {
      await launchJob(0, generation);
    } finally {
      if (generation === runGeneration) computing = false;
    }
  }

  async function launchJob(idx: number, generation: number) {
    const job = jobs[idx];
    if (!job || generation !== runGeneration) return;
    job.status = 'starting';
    job.message = '正在提交任务…';
    jobs = jobs;

    try {
      const resp = await startRegionalForecast({
        target_date: job.date,
        phenomenon,
        model_source: modelSource,
        forecast_mode: 'model',
        satellite_source: 'off',
        model_cadence_hours: modelSource === 'ec' ? 3 : 1,
        spacing_deg: 0.25,
        regions,
      });

      const cancellationRequested = cancelRequestedGenerations.has(generation);
      if (generation !== runGeneration && !cancellationRequested) return;
      job.job_id = resp.job_id;

      if (cancellationRequested) {
        let cancellationSettled = false;
        try {
          await cancelForecastJob(resp.job_id);
          job.status = 'cancelled';
          job.progress = 100;
          job.message = '已取消';
          job.error = '已取消';
          cancellationSettled = true;
        } catch (error) {
          // 后端对「不存在或已结束」的取消请求统一返回 404（api.py cancel → FileNotFoundError）。
          if (error instanceof ApiClientError && error.status === 404) {
            try {
              await loadDetachedTerminalJob(job, resp.job_id);
              cancellationSettled = true;
            } catch {
              // The authoritative status read can be retried by the watcher below.
            }
          }
        } finally {
          cancelRequestedGenerations.delete(generation);
          jobs = jobs;
        }
        if (!cancellationSettled) {
          job.message = '取消未确认，继续跟踪任务状态…';
          await attachSubmittedJob(job, resp.job_id, runGeneration);
        }
        computing = false;
        return;
      }
      await attachSubmittedJob(job, resp.job_id, generation);
    } catch (e) {
      if (generation !== runGeneration) return;
      const failedStoreId = activeStoreId;
      abortActiveTracking();
      job.status = 'failed';
      if (e instanceof ApiClientError) {
        job.error = e.apiError?.error || '提交失败';
      } else {
        job.error = e instanceof Error ? e.message : '未知错误';
      }
      job.message = job.error;
      if (failedStoreId) {
        regionalJobs.updateJob(failedStoreId, {
          status: 'failed',
          message: job.error,
          error: job.error,
        });
      }
      jobs = jobs;
    }
  }

  async function attachSubmittedJob(job: DayJob, jobId: string, generation: number): Promise<void> {
    job.status = 'running';
    if (!job.message || job.message === '正在提交任务…') job.message = '已连接，等待计算…';
    jobs = jobs;
    const storeId = `${modelSource}-${phenomenon}-${job.date}-${Date.now()}`;
    activeJobId = jobId;
    activeStoreId = storeId;
    regionalJobs.add({
      id: storeId,
      job_id: jobId,
      date: job.date,
      label: job.label,
      model_source: modelSource,
      phenomenon,
      regions: [...regions],
      status: 'running',
      progress: 0,
      stage: '',
      message: '已连接，等待计算…',
      elapsed: 0,
      result_summary: null,
      error: null,
      created_at: new Date().toISOString(),
    });
    await monitorJob(job, storeId, jobId, generation);
  }

  async function loadDetachedTerminalJob(job: DayJob, jobId: string): Promise<void> {
    const remote = await getForecastJob(jobId);
    if (!isTerminalForecastJobStatus(remote.status)) {
      throw new Error('取消冲突后任务仍未结束');
    }
    job.progress = 100;
    job.stage = remote.progress_stage || remote.status;
    if (remote.status === 'done') {
      job.status = 'done';
      job.message = remote.progress_message || '计算完成';
      job.error = null;
      job.result = await getForecastResult<RegionalForecastPayload>(jobId);
      return;
    }
    const message = remote.status === 'cancelled'
      ? (remote.error_message || '计算已取消')
      : (remote.error_message || remote.progress_message || '计算失败');
    job.status = remote.status === 'cancelled' ? 'cancelled' : 'failed';
    job.message = message;
    job.error = message;
  }

  async function monitorJob(
    job: DayJob,
    storeId: string,
    jobId: string,
    generation: number,
  ): Promise<void> {
    let settled = false;
    let pollInFlight = false;
    let source: EventSource | null = null;
    let resolveWait: (() => void) | null = null;
    const wait = new Promise<void>((resolve) => { resolveWait = resolve; });
    const ownsRun = () => generation === runGeneration && activeJobId === jobId;
    const releaseWatch = () => resolveWait?.();
    stopActiveWatch = releaseWatch;

    const updateFromStatus = (remote: ForecastJob) => {
      job.progress = Math.max(0, Math.min(100, Number(remote.progress_percent) || 0));
      job.stage = remote.progress_stage || '';
      job.message = remote.progress_message || '';
      const startedAt = remote.started_at || remote.created_at;
      const startedMs = Date.parse(startedAt);
      const endMs = remote.completed_at ? Date.parse(remote.completed_at) : Date.now();
      if (Number.isFinite(startedMs) && Number.isFinite(endMs)) {
        job.elapsed = Math.max(0, (endMs - startedMs) / 1000);
      }
      regionalJobs.updateJob(storeId, {
        progress: job.progress,
        stage: job.stage,
        message: job.message,
        elapsed: job.elapsed,
      });
      jobs = jobs;
    };

    const finish = async (remote: ForecastJob) => {
      if (settled || !ownsRun() || !isTerminalForecastJobStatus(remote.status)) return;
      settled = true;
      clearStatusPoll();
      source?.close();
      source = null;
      job.source = null;

      if (remote.status === 'done') {
        try {
          const result = await getForecastResult<RegionalForecastPayload>(jobId);
          if (!ownsRun()) {
            resolveWait?.();
            return;
          }
          job.status = 'done';
          job.progress = 100;
          job.stage = remote.progress_stage || 'done';
          job.message = remote.progress_message || '计算完成';
          job.result = result;
          const pts = result.points || [];
          regionalJobs.updateJob(storeId, {
            status: 'done',
            progress: 100,
            stage: job.stage,
            message: job.message,
            elapsed: job.elapsed,
            result_summary: {
              point_count: result.point_count,
              success_count: result.success_count,
              error_count: result.error_count,
              avg_score: pts.length ? pts.reduce((s, p) => s + Number(p.quality_index) / 2.5, 0) / pts.length : 0,
              max_score: pts.length ? Math.max(...pts.map(p => Number(p.quality_index) / 2.5)) : 0,
            },
          });
        } catch (error) {
          if (ownsRun()) {
            job.status = 'failed';
            job.error = error instanceof Error ? error.message : '读取计算结果失败';
            job.message = job.error;
            regionalJobs.updateJob(storeId, {
              status: 'failed', error: job.error, message: job.error,
            });
          }
        }
      } else if (ownsRun()) {
        const message = remote.status === 'cancelled'
          ? (remote.error_message || '计算已取消')
          : (remote.error_message || remote.progress_message || '计算失败');
        job.status = remote.status === 'cancelled' ? 'cancelled' : 'failed';
        job.progress = 100;
        job.stage = remote.progress_stage || remote.status;
        job.message = message;
        job.error = message;
        regionalJobs.updateJob(storeId, {
          status: remote.status === 'cancelled' ? 'cancelled' : 'failed',
          progress: 100,
          stage: job.stage,
          message,
          error: message,
          elapsed: job.elapsed,
        });
      }

      if (ownsRun()) {
        jobs = jobs;
        activeJobId = null;
        activeStoreId = null;
      }
      if (stopActiveWatch === releaseWatch) stopActiveWatch = null;
      resolveWait?.();
    };

    const pollStatus = async () => {
      if (settled || !ownsRun() || pollInFlight) return;
      pollInFlight = true;
      try {
        const remote = await getForecastJob(jobId);
        if (!ownsRun()) return;
        updateFromStatus(remote);
        if (isTerminalForecastJobStatus(remote.status)) await finish(remote);
      } catch {
        // Status polling is the authoritative watcher; transient failures retry.
      } finally {
        pollInFlight = false;
        if (!settled && ownsRun()) {
          statusPollTimer = setTimeout(() => { void pollStatus(); }, STATUS_POLL_INTERVAL_MS);
        }
      }
    };

    source = openSSE('/api/v1/firesky/forecasts/events', { job_id: jobId }, {
      onEvent: (eventName, eventData: unknown) => {
        if (!ownsRun() || settled) return;
        const d = eventData as Record<string, unknown>;
        if (eventName === 'progress') {
          job.stage = (d.stage as string) || '';
          job.progress = (d.percent as number) || 0;
          job.message = (d.message as string) || '';
          job.elapsed = (d.elapsed_seconds as number) || 0;
          jobs = jobs;
          regionalJobs.updateJob(storeId, {
            stage: job.stage, progress: job.progress, message: job.message, elapsed: job.elapsed,
          });
        } else if (eventName === 'done' || eventName === 'failed' || eventName === 'cancelled') {
          // SSE is only a progress signal. Read the Go job row for the terminal state.
          void pollStatus();
        }
      },
      onError: () => {
        // The status poll remains active when the SSE connection closes or retries.
      },
    });
    job.source = source;
    void pollStatus();
    await wait;
  }

  async function cancelAll() {
    const cancelledStoreId = activeStoreId;
    const cancelledJobId = activeJobId;
    const pendingGeneration = jobs.some(job => job.status === 'starting') ? runGeneration : null;
    if (pendingGeneration !== null) {
      if (cancelRequestedGenerations.size > 0) return;
      cancelRequestedGenerations.add(pendingGeneration);
      runGeneration += 1;
      abortActiveTracking();
      return;
    }
    if (!cancelledJobId) return;
    const activeJob = jobs.find(job => job.job_id === cancelledJobId);
    if (activeJob) activeJob.message = '正在取消…';
    if (cancelledStoreId) regionalJobs.updateJob(cancelledStoreId, { message: '正在取消…' });
    jobs = jobs;
    try {
      await cancelForecastJob(cancelledJobId);
    } catch (error) {
      // 404 = 任务不存在或已先结束（后端 cancel 约定），此时由状态轮询读取终态。
      const message = error instanceof ApiClientError && error.status === 404
        ? '任务已先结束，正在读取最终结果…'
        : '取消请求失败，继续跟踪任务状态';
      if (activeJob) activeJob.message = message;
      if (cancelledStoreId) regionalJobs.updateJob(cancelledStoreId, { message });
      jobs = jobs;
    }
  }

  // ══════════════════════════════════════
  // Results helpers
  // ══════════════════════════════════════

  function avgScore(points: RegionalForecastPoint[]): number {
    if (!points.length) return 0;
    return points.reduce((sum, p) => sum + Number(p.quality_index) / 2.5, 0) / points.length;
  }

  function maxScore(points: RegionalForecastPoint[]): number {
    if (!points.length) return 0;
    return Math.max(...points.map(p => Number(p.quality_index) / 2.5));
  }

  function scoreColor(score: number): string {
    if (score >= 0.7) return '#dc2626';
    if (score >= 0.5) return '#ea580c';
    if (score >= 0.3) return '#d97706';
    if (score >= 0.1) return '#65a30d';
    return '#6b7280';
  }

  function scoreLabel(score: number): string {
    if (score >= 0.7) return '极佳';
    if (score >= 0.5) return '良好';
    if (score >= 0.3) return '一般';
    if (score >= 0.1) return '较差';
    return '无';
  }

  function formatElapsed(s: number): string {
    if (s < 1) return '<1s';
    if (s < 60) return `${Math.round(s)}s`;
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}m${sec}s`;
  }

  function stageLabel(stage: string): string {
    const map: Record<string, string> = {
      queued: '排队中',
      validate: '验证参数',
      service: '初始化服务',
      cache: '加载缓存',
      prefetch_gfs: '预取 GFS',
      prefetch_ec: '预取 ECMWF',
      decode_gfs: '解析 GFS',
      decode_ec: '解析 ECMWF',
      tensor_gfs: 'GFS 云柱张量',
      tensor_ec: 'ECMWF 云柱张量',
      national: '区域计算',
      batch_compute: '全格点评分',
      target_path: '目标路径精修',
      frames: '形势帧编码',
      calibration: '校准',
      history: '历史记录',
      report: '生成报告',
      map_frames: '地图帧',
      finalize: '完成中',
      connected: '已连接',
    };
    return map[stage] || stage;
  }

  // Top points for each result
  function topPoints(result: RegionalForecastPayload, n: number = 10): RegionalForecastPoint[] {
    return [...result.points]
      .sort((a, b) => Number(b.quality_index) - Number(a.quality_index))
      .slice(0, n);
  }

  // ══════════════════════════════════════
  // Result summary
  // ══════════════════════════════════════

  let allResults = $derived(jobs.filter(j => j.status === 'done' && j.result));
  let hasAnyResult = $derived(allResults.length > 0);
  let totalPoints = $derived(allResults.reduce((sum, j) => sum + (j.result?.point_count || 0), 0));
  let totalErrors = $derived(allResults.reduce((sum, j) => sum + (j.result?.error_count || 0), 0));
  let avgBurn = $derived(
    allResults.length > 0
      ? allResults.reduce((sum, j) => sum + avgScore(j.result!.points), 0) / allResults.length
      : 0
  );

  onDestroy(() => {
    runGeneration += 1;
    abortActiveTracking();
  });
</script>

<div class="max-w-5xl mx-auto p-6 space-y-6">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <div>
      <h1 class="text-xl font-bold flex items-center gap-2">
        <Cpu class="w-5 h-5 text-primary" />
        区域网格实时计算
      </h1>
      <p class="text-sm text-muted-foreground mt-0.5">计算指定日期的区域朝晚霞评分网格</p>
    </div>
  </div>

  <!-- Form -->
  <section class="rounded-xl border p-5 space-y-5">
    <!-- Model source -->
    <div class="space-y-2">
      <div class="text-xs font-medium text-muted-foreground">数值模型</div>
      <div class="grid grid-cols-2 gap-2">
        {#each MODEL_OPTIONS as opt}
          <button
            class="px-4 py-2.5 rounded-lg border text-sm font-medium transition-all
              {modelSource === opt.value
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border hover:bg-muted'}"
            onclick={() => modelSource = opt.value}
          >
            <span class="block">{opt.label}</span>
            <span class="block text-[11px] text-muted-foreground font-normal mt-0.5">{opt.desc}</span>
          </button>
        {/each}
      </div>
    </div>

    <!-- Phenomenon -->
    <div class="space-y-2">
      <div class="text-xs font-medium text-muted-foreground">现象</div>
      <div class="grid grid-cols-2 gap-2">
        {#each PHENOMENON_OPTIONS as opt}
          {@const Icon = opt.icon}
          <button
            class="flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border text-sm font-medium transition-all
              {phenomenon === opt.value
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border hover:bg-muted'}"
            onclick={() => phenomenon = opt.value}
          >
            <Icon class="w-4 h-4" />
            {opt.label}
          </button>
        {/each}
      </div>
    </div>

    <!-- Target date -->
    <div class="space-y-2">
      <label for="compute-target-date" class="text-xs font-medium text-muted-foreground">计算日期</label>
      <div class="relative max-w-xs">
        <CalendarDays class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          id="compute-target-date"
          type="date"
          bind:value={targetDate}
          min={minimumTargetDate}
          disabled={computing}
          class="glass-input h-10 w-full rounded-lg pl-10 pr-3 text-sm outline-none transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>
    </div>

    <!-- Regions -->
    <div class="space-y-2">
      <div class="text-xs font-medium text-muted-foreground">计算区域（可多选）</div>
      <div class="flex flex-wrap gap-2">
        {#each REGION_OPTIONS as opt}
          <button
            class="px-3 py-1.5 rounded-lg border text-sm font-medium transition-all
              {regions.includes(opt.value)
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border hover:bg-muted'}"
            onclick={() => toggleRegion(opt.value)}
          >
            {opt.label}
          </button>
        {/each}
      </div>
    </div>

    <!-- Submit -->
    <div class="flex items-center gap-3 pt-2">
      {#if !computing}
        <button
          class="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          onclick={startCompute}
          disabled={!targetDate}
        >
          <Play class="w-4 h-4" />
          开始计算
        </button>
      {:else}
        <button
          class="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium border border-destructive/30 text-destructive hover:bg-destructive/10 transition-colors"
          onclick={cancelAll}
        >
          <XCircle class="w-4 h-4" />
          取消计算
        </button>
      {/if}
      <span class="text-xs text-muted-foreground">
        {dateLabel(targetDate)} · {modelSource.toUpperCase()} · {phenomenon === 'dusk' ? '晚霞' : '朝霞'} ·
        {regions.map(r => REGION_OPTIONS.find(o => o.value === r)?.label).join('+')}
      </span>
    </div>
  </section>

  <!-- Progress / Results -->
  {#if jobs.length > 0}
    <!-- Overall summary -->
    {#if hasAnyResult}
      <section class="rounded-xl border p-4 flex items-center gap-6 text-sm">
        <div class="flex items-center gap-1.5">
          <Database class="w-4 h-4 text-muted-foreground" />
          <span class="text-muted-foreground">总计</span>
          <span class="font-semibold">{totalPoints}</span>
          <span class="text-muted-foreground">个网格点</span>
        </div>
        {#if totalErrors > 0}
          <div class="flex items-center gap-1.5 text-destructive">
            <AlertCircle class="w-4 h-4" />
            <span class="font-semibold">{totalErrors}</span>
            <span>个错误</span>
          </div>
        {/if}
        <div class="flex items-center gap-1.5">
          <Sun class="w-4 h-4" style="color: {scoreColor(avgBurn)}" />
          <span class="text-muted-foreground">平均评分</span>
          <span class="font-semibold" style="color: {scoreColor(avgBurn)}">{(avgBurn * 100).toFixed(1)}</span>
          <span class="text-xs" style="color: {scoreColor(avgBurn)}">{scoreLabel(avgBurn)}</span>
        </div>
      </section>
    {/if}

    <!-- Selected-day result -->
    <div class="grid gap-4">
      {#each jobs as job}
        <div class="rounded-xl border overflow-hidden">
          <!-- Day header -->
          <div class="px-4 py-3 border-b bg-muted/30 flex items-center justify-between">
            <div class="flex items-center gap-2">
              <Clock class="w-4 h-4 text-muted-foreground" />
              <span class="font-medium text-sm">{job.label}</span>
              <span class="text-xs text-muted-foreground font-mono">{job.date}</span>
            </div>
            {#if job.status === 'done'}
              <CheckCircle class="w-4 h-4 text-emerald-500" />
            {:else if job.status === 'failed' || job.status === 'cancelled'}
              <XCircle class="w-4 h-4 text-destructive" />
            {:else if job.status === 'running' || job.status === 'starting'}
              <Loader2 class="w-4 h-4 text-primary animate-spin" />
            {/if}
          </div>

          <!-- Progress -->
          <div class="px-4 py-3 space-y-2">
            {#if job.status === 'idle'}
              <div class="text-xs text-muted-foreground py-4 text-center">等待中…</div>
            {:else if job.status === 'starting'}
              <div class="flex items-center gap-2 text-xs text-muted-foreground py-2">
                <Loader2 class="w-3.5 h-3.5 animate-spin" />
                {job.message}
              </div>
            {:else if job.status === 'running'}
              <div class="space-y-1.5">
                <div class="flex items-center justify-between text-xs">
                  <span class="text-muted-foreground">{stageLabel(job.stage)}</span>
                  <span class="font-mono text-muted-foreground">{job.progress}%</span>
                </div>
                <div class="h-1.5 rounded-full bg-muted overflow-hidden">
                  <div
                    class="h-full rounded-full bg-primary transition-all duration-300"
                    style="width: {job.progress}%"
                  ></div>
                </div>
                <p class="text-[11px] text-muted-foreground truncate">{job.message}</p>
                <p class="text-[11px] text-muted-foreground/60">已耗时 {formatElapsed(job.elapsed)}</p>
              </div>
            {:else if job.status === 'done' && job.result}
              {@const r = job.result}
              {@const top10 = topPoints(r)}
              <div class="space-y-3">
                <!-- Stats -->
                <div class="grid grid-cols-3 gap-2 text-center">
                  <div class="rounded-lg bg-muted/50 p-2">
                    <div class="text-sm font-semibold">{r.point_count}</div>
                    <div class="text-[10px] text-muted-foreground">网格点</div>
                  </div>
                  <div class="rounded-lg bg-muted/50 p-2">
                    <div class="text-sm font-semibold" style="color: {scoreColor(avgScore(r.points))}">
                      {(avgScore(r.points) * 100).toFixed(1)}
                    </div>
                    <div class="text-[10px] text-muted-foreground">平均分</div>
                  </div>
                  <div class="rounded-lg bg-muted/50 p-2">
                    <div class="text-sm font-semibold" style="color: {scoreColor(maxScore(r.points))}">
                      {(maxScore(r.points) * 100).toFixed(1)}
                    </div>
                    <div class="text-[10px] text-muted-foreground">最高分</div>
                  </div>
                </div>
                <div class="text-[11px] text-muted-foreground">
                  成功 {r.success_count} · 失败 {r.error_count} ·
                  {r.model_source === 'gfs' ? r.compute_backend.toUpperCase() : `${r.model_source.toUpperCase()} 独立模式`} ·
                  耗时 {formatElapsed(job.elapsed)}
                </div>
                <div class="text-[11px] text-muted-foreground">
                  {r.precision.mode} · {r.precision.point_spacing_deg != null ? `${r.precision.point_spacing_deg.toFixed(2)}°` : '显式点位'} ·
                  {r.timing.frame_points_per_second.toFixed(0)} 点/秒 ·
                  评分 {formatElapsed(r.timing.frame_instant_scoring_seconds)}
                </div>

                <!-- Top points -->
                <div class="space-y-1">
                  <div class="text-[11px] font-medium text-muted-foreground flex items-center gap-1">
                    <Map class="w-3 h-3" />
                    最佳点位 Top 10
                  </div>
                  <div class="space-y-0.5 max-h-60 overflow-y-auto">
                    {#each top10 as p, pi}
                      <div class="flex items-center gap-2 text-[11px] py-1 px-2 rounded bg-muted/30">
                        <span class="w-4 text-right text-muted-foreground/50">{pi + 1}</span>
                        <div
                          class="w-2 h-2 rounded-full shrink-0"
                          style="background: {scoreColor(Number(p.quality_index) / 2.5)}"
                        ></div>
                        <span class="font-semibold tabular-nums" style="color: {scoreColor(Number(p.quality_index) / 2.5)}">
                          {Number(p.quality_index).toFixed(3)}
                        </span>
                        <span class="text-muted-foreground truncate flex-1">{p.name}</span>
                        <span class="text-muted-foreground/50 tabular-nums shrink-0">
                          {p.lat.toFixed(1)}°N {p.lon.toFixed(1)}°E
                        </span>
                      </div>
                    {/each}
                  </div>
                </div>
              </div>
            {:else if job.status === 'failed' || job.status === 'cancelled'}
              <div class="rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-xs text-destructive">
                {job.error || '计算失败'}
              </div>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Empty state -->
  {#if jobs.length === 0}
    <div class="rounded-xl border border-dashed py-16 text-center">
      <Cpu class="w-8 h-8 mx-auto text-muted-foreground/20 mb-3" />
      <p class="text-sm text-muted-foreground">选择日期和参数后点击「开始计算」，系统将计算当天的区域网格评分</p>
      <p class="text-xs text-muted-foreground/60 mt-1">计算可能需要几分钟，取决于网格密度和区域大小</p>
    </div>
  {/if}
</div>
