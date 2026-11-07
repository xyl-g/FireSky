<script lang="ts">
  import { onMount } from 'svelte';
  import Activity from '@lucide/svelte/icons/activity';
  import ChevronRight from '@lucide/svelte/icons/chevron-right';
  import Cpu from '@lucide/svelte/icons/cpu';
  import Database from '@lucide/svelte/icons/database';
  import HardDrive from '@lucide/svelte/icons/hard-drive';
  import RefreshCw from '@lucide/svelte/icons/refresh-cw';
  import Server from '@lucide/svelte/icons/server';
  import { getSystemStatus, type SystemStatus } from '$lib/api/system';
  import { ApiClientError } from '$lib/api/client';

  let { embedded = false }: { embedded?: boolean } = $props();
  let status = $state<SystemStatus | null>(null);
  let loading = $state(false);
  let error = $state('');
  let autoRefresh = $state(false);
  let active = false;

  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const next = await getSystemStatus();
      if (active) { status = next; error = ''; }
    } catch (e) {
      if (active) error = e instanceof ApiClientError ? e.apiError.error : String(e);
    } finally {
      if (active) loading = false;
    }
  }

  onMount(() => {
    active = true;
    void refresh();
    const timer = setInterval(() => { if (autoRefresh) void refresh(); }, 15000);
    return () => { active = false; clearInterval(timer); };
  });

  function stateLabel(value: string) {
    return ({ ok: '正常', degraded: '降级', unavailable: '不可用', disabled: '未启用' } as Record<string, string>)[value] ?? '未知';
  }

  function uptime(seconds: number) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    return days ? `${days} 天 ${hours} 小时` : `${hours} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟`;
  }

  const ms = (value?: number) => value === undefined ? undefined : `${value.toFixed(1)} ms`;
  const mb = (value: number) => `${value.toFixed(1)} MB`;
  type Metric = [string, string | number | undefined];
  interface Service {
    id: string;
    name: string;
    icon: typeof Server;
    state: string;
    summary?: string;
    error?: string;
    metrics: Metric[];
  }

  let services = $derived.by((): Service[] => {
    if (!status) return [];
    const { go, database, redis, python } = status;
    return [
      {
        id: 'go', name: 'Go 网关', icon: Server, state: go.status,
        summary: `运行 ${uptime(go.uptime_seconds)}`,
        metrics: [['版本', go.version], ['运行时长', uptime(go.uptime_seconds)], ['Goroutines', go.goroutines],
          ['CPU 核数', go.num_cpu], ['已分配内存', mb(go.mem_alloc_mb)], ['系统内存', mb(go.mem_sys_mb)], ['GC 次数', go.gc_runs]],
      },
      {
        id: 'database', name: 'PostgreSQL', icon: Database, state: database.status,
        summary: ms(database.latency_ms), error: database.error,
        metrics: [['驱动', database.driver], ['延迟', ms(database.latency_ms)], ['连接池上限', database.max_open_connections],
          ['当前连接', database.open_connections], ['占用连接', database.in_use], ['空闲连接', database.idle],
          ['等待计数', database.wait_count], ['等待时长', ms(database.wait_duration_ms)]],
      },
      {
        id: 'redis', name: 'Redis 缓存', icon: HardDrive, state: redis.status,
        summary: ms(redis.latency_ms), error: redis.error,
        metrics: [['地址', redis.addr], ['版本', redis.version], ['延迟', ms(redis.latency_ms)], ['连接客户端', redis.connected_clients],
          ['已用内存', redis.used_memory_human], ['内存峰值', redis.used_memory_peak_human],
          ['周期缓存', redis.latest_cycles_cached === undefined ? undefined : redis.latest_cycles_cached ? '已写入' : '未写入']],
      },
      {
        id: 'python', name: 'Python 计算', icon: Cpu, state: python.status,
        summary: ms(python.latency_ms), error: python.error,
        metrics: [['健康检查延迟', ms(python.latency_ms)], ['HTTP 状态', python.http_status],
          ['返回结果', python.detail ? JSON.stringify(python.detail) : undefined]],
      },
    ];
  });

  let overall = $derived(error ? 'unavailable' : status?.status ?? 'unknown');
</script>

<section class="system-status glass-panel border {embedded ? '' : 'standalone'}" aria-labelledby="system-status-heading" aria-busy={loading}>
  <header class="panel-heading">
    <h2 id="system-status-heading"><Activity size={16} />运行状态</h2>
    <div class="refresh-controls">
      <label title="每 15 秒刷新系统状态"><input type="checkbox" bind:checked={autoRefresh} />自动刷新</label>
      <button onclick={refresh} disabled={loading} aria-label="刷新系统状态" title="刷新系统状态">
        <RefreshCw size={14} class={loading ? 'animate-spin' : ''} />
      </button>
    </div>
  </header>

  <div class="health-summary">
    <span class="health-label" data-state={overall}><span class="status-dot"></span>
      {error ? '状态更新失败' : status ? status.status === 'ok' ? '服务运行正常' : `服务${stateLabel(status.status)}` : '正在检查服务'}
    </span>
    <span class="check-time">{status ? `${error ? '上次成功' : '更新于'} ${new Date(status.checked_at).toLocaleTimeString('zh-CN', { hour12: false })}` : '等待响应'}</span>
  </div>

  {#if error}<p class="error-message" role="alert">{error}{status ? '（以下为上次成功检查的数据）' : ''}</p>{/if}

  <div class="service-list">
    {#each services as service (service.id)}
      <details class="service" open={service.state === 'unavailable' || service.state === 'degraded'}>
        <summary>
          <service.icon size={16} class="service-icon" />
          <span class="service-name">{service.name}</span>
          <span class="service-metric">{service.summary ?? '暂无指标'}</span>
          <span class="service-state" data-state={service.state}><span class="status-dot"></span>{stateLabel(service.state)}</span>
          <ChevronRight size={14} class="disclosure-icon" />
        </summary>
        <div class="service-detail">
          {#if service.error}<p class="error-message">{service.error}</p>{/if}
          <dl>
            {#each service.metrics.filter(([, value]) => value !== undefined) as [label, value] (label)}
              <div><dt>{label}</dt><dd>{value}</dd></div>
            {/each}
          </dl>
        </div>
      </details>
    {:else}
      <div class="status-placeholder">{error ? '暂时无法获取服务状态' : '正在获取 Go、数据库、缓存与计算服务状态…'}</div>
    {/each}
  </div>
</section>

<style>
  .system-status { min-width: 0; border-radius: 16px; align-self: start; overflow: hidden; color: var(--foreground); }
  .standalone { max-width: 72rem; margin: 1.5rem auto; }
  .panel-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 14px 20px; border-bottom: 1px solid var(--glass-border); }
  h2 { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; }
  h2 :global(svg) { color: var(--muted-foreground); }
  .refresh-controls { display: flex; align-items: center; gap: 12px; }
  .refresh-controls label { display: flex; align-items: center; gap: 6px; color: var(--muted-foreground); font-size: 11px; cursor: pointer; }
  input { accent-color: var(--foreground); }
  .refresh-controls button { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 8px; color: var(--muted-foreground); cursor: pointer; }
  .refresh-controls button:hover { background: var(--glass-subtle); color: var(--foreground); }
  .refresh-controls button:disabled { opacity: .5; cursor: wait; }
  .health-summary { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; padding: 16px 20px 10px; }
  .health-label { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 500; }
  .check-time { color: var(--muted-foreground); font-size: 11px; font-variant-numeric: tabular-nums; }
  .status-dot { width: 6px; height: 6px; flex-shrink: 0; border-radius: 50%; background: currentColor; }
  [data-state] { color: var(--muted-foreground); }
  [data-state="ok"] { color: #16805c; }
  [data-state="degraded"] { color: #a16207; }
  [data-state="unavailable"] { color: #dc3654; }
  :global(.dark) [data-state="ok"] { color: #63d4ac; }
  :global(.dark) [data-state="degraded"] { color: #f1c36b; }
  :global(.dark) [data-state="unavailable"] { color: #fb8ba1; }
  .service-list { padding: 0 20px 8px; }
  .service + .service { border-top: 1px solid var(--glass-border); }
  summary { display: grid; grid-template-columns: 16px minmax(0, 1fr) auto auto 14px; align-items: center; gap: 10px; min-height: 51px; font-size: 12px; cursor: pointer; list-style: none; border-radius: 6px; }
  summary::-webkit-details-marker { display: none; }
  summary:hover { background: var(--glass-subtle); }
  summary:focus-visible, button:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
  :global(.service-icon), :global(.disclosure-icon) { color: var(--muted-foreground); }
  .service-name { font-weight: 500; }
  .service-metric { color: var(--muted-foreground); font-size: 11px; font-variant-numeric: tabular-nums; }
  .service-state { display: flex; align-items: center; gap: 6px; font-size: 11px; }
  details[open] :global(.disclosure-icon) { transform: rotate(90deg); }
  .service-detail { padding: 0 0 16px 26px; }
  dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; font-size: 11px; }
  dt { color: var(--muted-foreground); margin-bottom: 3px; }
  dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  .error-message { margin: 0; padding: 10px 20px; color: var(--destructive); font-size: 12px; overflow-wrap: anywhere; }
  .service-detail .error-message { padding: 0 0 12px; }
  .status-placeholder { min-height: 204px; display: grid; place-items: center; color: var(--muted-foreground); font-size: 12px; }
  @media (max-width: 640px) {
    summary { grid-template-columns: 16px minmax(0, 1fr) auto 14px; gap: 8px; padding: 10px 0; }
    .service-metric { grid-column: 2; grid-row: 2; }
    .service-state { grid-column: 3; grid-row: 1 / 3; }
    summary :global(.disclosure-icon) { grid-column: 4; grid-row: 1 / 3; }
  }
</style>
