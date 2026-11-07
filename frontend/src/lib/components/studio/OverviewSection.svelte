<script lang="ts">
  import { onMount } from 'svelte';
  import ArrowRight from '@lucide/svelte/icons/arrow-right';
  import Cpu from '@lucide/svelte/icons/cpu';
  import Inbox from '@lucide/svelte/icons/inbox';
  import Database from '@lucide/svelte/icons/database';
  import Activity from '@lucide/svelte/icons/activity';
  import { getDataStatus, type DataStatus } from '$lib/api/data';
  import { getRegionalContract, type RegionalContract } from '$lib/api/admin';
  import type { SectionId } from './AdminConsole.svelte';

  let { onNavigate }: { onNavigate: (id: SectionId) => void } = $props();

  let data: DataStatus | null = $state(null);
  let contract: RegionalContract | null = $state(null);
  let loading = $state(true);

  const actions: { id: SectionId; icon: typeof Cpu; label: string; hint: string }[] = [
    { id: 'compute', icon: Cpu, label: '提交区域计算', hint: '选择区域与日期，起一个区域预测任务' },
    { id: 'queue', icon: Inbox, label: '查看运行队列', hint: '跟踪正在运行与已完成的任务' },
    { id: 'data', icon: Database, label: '管理数据资产', hint: '检查 GFS / ECMWF / CAMS 缓存与缺数' },
  ];

  onMount(async () => {
    try {
      data = await getDataStatus();
    } catch {
      data = null;
    }
    try {
      contract = await getRegionalContract();
    } catch {
      contract = null;
    }
    loading = false;
  });

  function formatMb(mb: number | undefined): string {
    if (mb === undefined || !Number.isFinite(mb)) return '—';
    return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`;
  }
</script>

<div class="space-y-6">
  <!-- Engine status -->
  <section>
    <h2 class="mb-3 flex items-center gap-2 text-[13px] font-semibold text-foreground">
      <Activity class="h-4 w-4 text-primary" /> 引擎状态
    </h2>
    <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div class="glass-panel rounded-xl border p-3">
        <p class="text-[10px] uppercase tracking-wide text-muted-foreground">数据状态</p>
        <p class="mt-1 text-lg font-semibold text-foreground">
          {#if loading}…{:else if !data}不可用{:else if data.overall_status === 'current'}已就绪{:else if data.overall_status === 'update_available'}可更新{:else}缺数据{/if}
        </p>
      </div>
      <div class="glass-panel rounded-xl border p-3">
        <p class="text-[10px] uppercase tracking-wide text-muted-foreground">缓存占用</p>
        <p class="mt-1 text-lg font-semibold text-foreground font-mono-data">
          {loading ? '…' : formatMb(data?.total_cache_size_mb)}
        </p>
      </div>
      <div class="glass-panel rounded-xl border p-3">
        <p class="text-[10px] uppercase tracking-wide text-muted-foreground">区域摘要版本</p>
        <p class="mt-1 text-lg font-semibold text-foreground font-mono-data">
          v{contract?.summary_version ?? '—'}
        </p>
      </div>
      <div class="glass-panel rounded-xl border p-3">
        <p class="text-[10px] uppercase tracking-wide text-muted-foreground">区域缓存版本</p>
        <p class="mt-1 text-lg font-semibold text-foreground font-mono-data">
          {contract?.cache_version ?? '—'}
        </p>
      </div>
    </div>
  </section>

  <!-- Dataset readiness -->
  {#if data?.datasets?.length}
    <section>
      <h2 class="mb-3 text-[13px] font-semibold text-foreground">数据源就绪情况</h2>
      <div class="grid gap-2 sm:grid-cols-3">
        {#each data.datasets as dataset}
          <div class="rounded-lg border border-border/60 bg-muted/20 p-3">
            <div class="flex items-center justify-between">
              <span class="font-mono-data text-xs font-semibold uppercase">{dataset.source}</span>
              <span class="text-[10px] {dataset.status === 'current' ? 'text-emerald-500' : dataset.status === 'missing' ? 'text-destructive' : 'text-amber-500'}">
                {dataset.status === 'current' ? '就绪' : dataset.status === 'update_available' ? '可更新' : dataset.status === 'missing' ? '缺失' : '不可用'}
              </span>
            </div>
            <p class="mt-1.5 text-[11px] text-muted-foreground">
              本地 {dataset.latest_local_cycle || '—'}
            </p>
            <p class="text-[11px] text-muted-foreground">
              文件 {dataset.local_files} / {dataset.required_files}
            </p>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  <!-- Quick actions -->
  <section>
    <h2 class="mb-3 text-[13px] font-semibold text-foreground">常用操作</h2>
    <div class="grid gap-2">
      {#each actions as action}
        <button
          class="glass-control-well flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition hover:text-foreground"
          onclick={() => onNavigate(action.id)}
        >
          <action.icon class="h-4 w-4 shrink-0 text-primary" />
          <span class="min-w-0 flex-1">
            <span class="block text-xs font-medium text-foreground">{action.label}</span>
            <span class="block text-[10px] text-muted-foreground">{action.hint}</span>
          </span>
          <ArrowRight class="h-3.5 w-3.5 shrink-0" />
        </button>
      {/each}
    </div>
  </section>
</div>
