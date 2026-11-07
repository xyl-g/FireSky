<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import {
    Root as DropdownMenuRoot,
    Trigger as DropdownMenuTrigger,
    Content as DropdownMenuContent,
    Item as DropdownMenuItem,
    Label as DropdownMenuLabel,
    Separator as DropdownMenuSeparator,
  } from '$lib/components/ui/dropdown-menu';
  import { toggleMode, setMode } from 'mode-watcher';
  import Sun from '@lucide/svelte/icons/sun';
  import Moon from '@lucide/svelte/icons/moon';
  import SunMoon from '@lucide/svelte/icons/sun-moon';
  import CloudSun from '@lucide/svelte/icons/cloud-sun';
  import Settings from '@lucide/svelte/icons/settings';
  import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
  import Satellite from '@lucide/svelte/icons/satellite';

  let { children } = $props();

  const navItems = [
    { href: '/forecast', label: '预测', icon: SunMoon },
    { href: '/now', label: '实况', icon: Satellite },
    { href: '/outlook', label: '形势图', icon: CloudSun },
  ];

  function isActive(href: string): boolean {
    return page.url?.pathname.startsWith(href) ?? false;
  }

  function navigate(href: string) {
    goto(href);
  }

</script>

  <div class="flex flex-col h-screen">
    <a href="#main-content" class="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:text-foreground focus:shadow-lg">跳转到主要内容</a>

    <!-- 悬浮玻璃胶囊导航（与展示站首页同款） -->
    <header class="px-3 pt-3 shrink-0 relative z-40">
      <div class="glass glass-nav rounded-2xl h-14 px-3 flex items-center justify-between">
        <div class="flex items-center w-full gap-3 min-w-0">
          <!-- Brand -->
          <a
            class="flex min-h-10 items-center gap-2 rounded-xl px-1.5 shrink-0 hover:bg-white/40 dark:hover:bg-white/[0.08] transition-colors duration-200"
            href="/forecast"
          >
            <div class="w-7 h-7 rounded-lg firesky-gradient-bar flex items-center justify-center shadow-sm">
              <Sun class="w-3.5 h-3.5 text-white" />
            </div>
            <span class="firesky-wordmark hidden sm:inline">
              FireSky
            </span>
          </a>

          <div class="w-px h-4 bg-border/70 dark:bg-white/10"></div>

          <!-- Nav -->
          <nav class="flex items-center gap-1 flex-1 overflow-x-auto scrollbar-none" aria-label="主导航">
            {#each navItems as item}
              {@const Icon = item.icon}
              <a
                class="relative flex min-h-9 min-w-11 shrink-0 whitespace-nowrap items-center justify-center gap-1.5 rounded-full px-3 text-[11px] font-medium transition-all duration-200
                  {isActive(item.href)
                    ? 'bg-white/90 text-foreground shadow-[0_1px_3px_rgba(92,58,30,0.10)] ring-1 ring-black/[0.04] dark:bg-white/[0.14] dark:text-foreground dark:ring-white/[0.06] dark:shadow-none'
                    : 'text-muted-foreground hover:text-foreground hover:bg-white/50 dark:hover:bg-white/[0.08]'}"
                href={item.href}
                aria-label={item.label}
                aria-current={isActive(item.href) ? 'page' : undefined}
              >
                <Icon class="w-3.5 h-3.5" />
                <span class="hidden md:inline">{item.label}</span>
              </a>
            {/each}
          </nav>

          <!-- Right actions -->
          <div class="flex items-center gap-1 shrink-0">
            <!-- Theme toggle -->
            <DropdownMenuRoot>
              <DropdownMenuTrigger>
                {#snippet child({ props })}
                  <button {...props} class="inline-flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground hover:bg-white/50 dark:hover:bg-white/[0.08] transition-colors cursor-pointer" aria-label="切换主题">
                    <Sun class="w-3.5 h-3.5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
                    <Moon class="absolute w-3.5 h-3.5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
                    <span class="sr-only">切换主题</span>
                  </button>
                {/snippet}
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onclick={() => setMode('light')}>
                  <Sun class="mr-2 h-4 w-4" /> 亮色
                </DropdownMenuItem>
                <DropdownMenuItem onclick={() => setMode('dark')}>
                  <Moon class="mr-2 h-4 w-4" /> 暗色
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onclick={toggleMode}>
                  切换主题
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenuRoot>

            <!-- Account menu: firesky runs without user accounts -->
            <DropdownMenuRoot>
              <DropdownMenuTrigger>
                {#snippet child({ props })}
                  <button {...props} class="h-9 w-9 rounded-full bg-gradient-to-br from-[var(--firesky-1)] to-[var(--firesky-3)] flex items-center justify-center text-white hover:ring-2 hover:ring-white/40 transition-all cursor-pointer" aria-label="打开菜单">
                    <Settings class="h-4 w-4" />
                  </button>
                {/snippet}
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" class="w-56">
                <DropdownMenuLabel class="font-normal">
                  <div class="flex flex-col space-y-0.5">
                    <p class="text-sm font-semibold">FireSky</p>
                    <p class="text-xs text-muted-foreground">本地部署 · 无需登录</p>
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onclick={() => navigate('/admin')}>
                  <SlidersHorizontal class="mr-2 h-4 w-4" /> 管理控制台
                </DropdownMenuItem>
                <DropdownMenuItem onclick={() => navigate('/forecast')}>
                  <SunMoon class="mr-2 h-4 w-4" /> 预测工作台
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onclick={() => toggleMode()}>
                  <Sun class="mr-2 h-4 w-4 dark:hidden" />
                  <Moon class="mr-2 hidden h-4 w-4 dark:block" /> 切换明暗主题
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenuRoot>
          </div>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main id="main-content" class="flex-1 min-h-0 overflow-y-auto px-3 pb-3" tabindex="-1">
      {@render children()}
    </main>
  </div>
