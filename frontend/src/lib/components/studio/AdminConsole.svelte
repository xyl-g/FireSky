<script module lang="ts">
  export type SectionId = 'overview' | 'compute' | 'queue' | 'data';
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import AlertCircle from '@lucide/svelte/icons/alert-circle';
  import CheckCircle2 from '@lucide/svelte/icons/check-circle-2';
  import ExternalLink from '@lucide/svelte/icons/external-link';
  import Info from '@lucide/svelte/icons/info';
  import LoaderCircle from '@lucide/svelte/icons/loader-circle';
  import Cpu from '@lucide/svelte/icons/cpu';
  import Inbox from '@lucide/svelte/icons/inbox';
  import Database from '@lucide/svelte/icons/database';
  import Monitor from '@lucide/svelte/icons/monitor';
  import Moon from '@lucide/svelte/icons/moon';
  import Sun from '@lucide/svelte/icons/sun';
  import {
    Root as DropdownMenuRoot,
    Trigger as DropdownMenuTrigger,
    Content as DropdownMenuContent,
    Item as DropdownMenuItem,
    Separator as DropdownMenuSeparator,
  } from '$lib/components/ui/dropdown-menu';
  import { setMode } from 'mode-watcher';
  import { TOAST_EVENT, type ToastType } from './ui';
  import OverviewSection from './OverviewSection.svelte';
  import ComputeSection from './operations/ComputeSection.svelte';
  import QueueSection from './operations/QueueSection.svelte';
  import DataSection from './operations/DataSection.svelte';

  const NAV_GROUPS = [
    {
      label: '运行管理',
      items: [
        { id: 'overview', label: '总览', icon: Monitor },
        { id: 'compute', label: '实时计算', icon: Cpu },
        { id: 'queue', label: '运行队列', icon: Inbox },
        { id: 'data', label: '数据资产', icon: Database },
      ],
    },
  ] as const;

  const SECTION_META: Record<SectionId, { title: string; desc: string }> = {
    overview: { title: '总览', desc: '引擎运行状态与数据概况' },
    compute: { title: '实时计算', desc: '提交并跟踪区域晚霞与朝霞计算任务' },
    queue: { title: '运行队列', desc: '查看任务队列、进度与历史结果' },
    data: { title: '数据资产', desc: '检查 GFS、ECMWF、CAMS 与派生缓存' },
  };

  const ALL_IDS: SectionId[] = ['overview', 'compute', 'queue', 'data'];

  function readHash(): SectionId {
    const h = window.location.hash.replace(/^#\/?/, '');
    if (h === 'system') return 'overview';
    return (ALL_IDS.find((id) => id === h) ?? 'overview') as SectionId;
  }

  interface Toast {
    id: number;
    message: string;
    type: ToastType;
  }

  let section = $state<SectionId>(readHash());
  let toasts = $state<Toast[]>([]);

  // firesky runs without accounts: the console is a local operations surface.
  $effect(() => {
    const onHashChange = () => (section = readHash());
    window.addEventListener('hashchange', onHashChange);
    const onToast = (e: Event) => {
      const { message, type } = (e as CustomEvent<{ message: string; type: ToastType }>).detail;
      const id = Date.now() + Math.random();
      toasts = [...toasts.slice(-3), { id, message, type }];
      setTimeout(() => (toasts = toasts.filter((t) => t.id !== id)), 3200);
    };
    window.addEventListener(TOAST_EVENT, onToast);
    return () => {
      window.removeEventListener('hashchange', onHashChange);
      window.removeEventListener(TOAST_EVENT, onToast);
    };
  });

  function navigate(id: SectionId) {
    window.location.hash = `#/${id}`;
    section = id;
  }

</script>


  <!-- This route does not inherit the public-site layout, so it owns the shared refraction filter. -->
  <div class="studio-console min-h-screen flex glass-workspace text-foreground">
    <!-- Sidebar -->
    <aside class="console-sidebar glass-rail w-60 shrink-0 text-muted-foreground flex flex-col sticky top-0 h-screen z-20">
      <div class="px-4 py-4">
        <a href="/forecast" class="group flex items-center gap-2.5 transition hover:opacity-80" title="返回预测台">
          <div
            class="w-7 h-7 shrink-0 rounded-lg firesky-gradient-bar flex items-center justify-center shadow-sm"
          >
            <Sun class="h-3.5 w-3.5 text-white" />
          </div>
          <span class="firesky-wordmark min-w-0 truncate">
            FireSky
          </span>
          <span class="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium tracking-wide text-muted-foreground/70">OPS</span>
        </a>
      </div>

      <nav class="flex-1 px-3 pb-4 flex flex-col gap-5 overflow-y-auto admin-scroll-dark">
        {#each NAV_GROUPS as group (group.label)}
          <div>
            <p class="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              {group.label}
            </p>
            <div class="flex flex-col gap-0.5">
              {#each group.items as item (item.id)}
                {@const active = section === item.id}
                <button
                  onclick={() => navigate(item.id)}
                  class="relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors cursor-pointer {active
                    ? 'bg-white/90 text-foreground font-semibold shadow-[0_1px_2px_rgba(92,58,30,0.08)] dark:bg-white/[0.10] dark:shadow-none'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/40'}"
                >
                  {#if active}
                    <span class="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-orange-500/80 dark:bg-amber-400/80"></span>
                  {/if}
                  <span class={active ? 'text-orange-600 dark:text-amber-400' : ''}>
                    <item.icon class="w-4 h-4" />
                  </span>
                  <span>{item.label}</span>

                </button>
              {/each}
            </div>
          </div>
        {/each}
      </nav>

      <div class="console-account px-5 py-4 border-t border-border">
        <p class="font-mono text-[10px] tracking-wide text-muted-foreground">LOCAL · NO AUTH</p>
      </div>
    </aside>

    <!-- Main -->
    <main class="flex-1 min-w-0 flex flex-col">
      <header
        class="glass-strong glass-nav sticky top-0 z-10 rounded-none border-x-0 border-t-0 px-4 md:px-8 py-3.5 flex flex-wrap gap-3 items-center justify-between"
      >
        <div>
          <h1 class="text-[15px] font-semibold text-foreground tracking-tight">{SECTION_META[section].title}</h1>
          <p class="text-[11px] text-muted-foreground mt-0.5">{SECTION_META[section].desc}</p>
        </div>
        <div class="flex items-center gap-2">
          <DropdownMenuRoot>
            <DropdownMenuTrigger>
              {#snippet child({ props })}
                <button
                  {...props}
                  class="glass-control-well inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition hover:text-foreground cursor-pointer"
                  aria-label="切换界面模式"
                  title="切换界面模式"
                >
                  <Sun class="w-3.5 h-3.5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
                  <Moon class="absolute w-3.5 h-3.5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
                </button>
              {/snippet}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" class="glass-strong w-36">
              <DropdownMenuItem onclick={() => setMode('light')}>
                <Sun class="mr-2 h-4 w-4" /> 浅色模式
              </DropdownMenuItem>
              <DropdownMenuItem onclick={() => setMode('dark')}>
                <Moon class="mr-2 h-4 w-4" /> 深色模式
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onclick={() => setMode('system')}>
                <Monitor class="mr-2 h-4 w-4" /> 跟随系统
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenuRoot>

          <a
            href="/forecast"
            target="_blank"
            rel="noopener noreferrer"
            class="glass-control-well inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground transition hover:text-foreground cursor-pointer"
          >
            返回预测台
            <ExternalLink class="w-3 h-3" />
          </a>
        </div>
      </header>

      <div class="p-4 md:p-8">
        <div class="max-w-6xl mx-auto">
          {#key section}
            <div class="admin-view-in">
              {#if section === 'overview'}
                <OverviewSection onNavigate={navigate} />
              {:else if section === 'compute'}
                <ComputeSection />
              {:else if section === 'queue'}
                <QueueSection />
              {:else if section === 'data'}
                <DataSection />
              {/if}
            </div>
          {/key}
        </div>
      </div>
    </main>

    <!-- Toasts -->
    <div class="fixed bottom-6 right-6 z-50 flex flex-col gap-2 items-end">
      {#each toasts as t (t.id)}
        <div
          class="admin-toast-in flex items-center gap-2.5 rounded-xl bg-foreground py-2.5 pl-3.5 pr-4 text-[13px] text-background shadow-xl ring-1 ring-border"
        >
          {#if t.type === 'success'}<CheckCircle2 class="w-4 h-4 text-emerald-400 shrink-0" />{/if}
          {#if t.type === 'error'}<AlertCircle class="w-4 h-4 text-rose-400 shrink-0" />{/if}
          {#if t.type === 'info'}<Info class="w-4 h-4 text-sky-400 shrink-0" />{/if}
          <span>{t.message}</span>
        </div>
      {/each}
    </div>
  </div>

<style>
  @media (max-width: 640px) {
    .studio-console { flex-direction: column; }
    .console-sidebar { width: 100%; height: auto; position: relative; }
    .console-sidebar nav { flex-direction: row; gap: 4px; overflow-x: auto; padding-bottom: 12px; }
    .console-sidebar nav > div, .console-sidebar nav > div > div { display: contents; }
    .console-sidebar nav > div > p { display: none; }
    .console-sidebar nav button { flex-shrink: 0; white-space: nowrap; }
    /* 「LOCAL · NO AUTH」是纯装饰文本（无账号体系）；移动端隐藏，避免绝对定位
       压到品牌行右端的 OPS 角标上。 */
    .console-account { display: none; }
  }
</style>