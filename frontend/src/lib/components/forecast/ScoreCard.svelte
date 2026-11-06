<script lang="ts">
  import type { ForecastPayload } from '@/api/types';
  import Sparkles from '@lucide/svelte/icons/sparkles';
  import Sun from '@lucide/svelte/icons/sun';
  import Moon from '@lucide/svelte/icons/moon';
  import MapPin from '@lucide/svelte/icons/map-pin';
  import Gauge from '@lucide/svelte/icons/gauge';
  import { CHINA_TIME_ZONE, formatLocalDateTime } from '@/utils/format';

  let { data }: { data: ForecastPayload } = $props();

  let quality = $derived(
    data.quality_index == null || !Number.isFinite(Number(data.quality_index))
      ? null
      : Number(data.quality_index)
  );

  let drivers = $derived(
    [
      { label: '前方可烧云量', value: data.quality_components?.forward_burnable_cloud_field_amount_peak },
      { label: '有效可烧云量', value: data.quality_components?.forward_burnable_cloud_field_amount_effective },
      { label: '最佳云入射遮挡', value: data.quality_components?.solar_incoming_block_risk_at_best_cloud },
    ].filter((driver) => driver.value != null && Number.isFinite(driver.value))
  );

  let isDawn = $derived(data.phenomenon === 'dawn');
  let hasPhysicalBurnableCloud = $derived(
    Number(data.quality_components?.physically_burnable_steps ?? 0) > 0
    || Boolean(data.steps?.some((step) => step.physically_burnable))
  );
  const familyLabels = { high: '高云压力族', middle: '中云压力族', low: '低云压力族' } as const;
</script>

<div class="glass-panel-strong relative rounded-xl border p-4 md:p-5">
  <div class="relative z-10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-6">
    <!-- Left Column: Location, Phenomenon & Model -->
    <div class="min-w-0 flex-1">
      <div class="flex items-center gap-2.5 flex-wrap mb-1.5">
        <h2 class="text-lg font-semibold text-card-foreground flex items-start gap-2 min-w-0">
          <MapPin class="w-5 h-5 text-primary shrink-0" />
          <span class="break-words min-w-0">{data.observer_name}</span>
        </h2>

        <!-- Phenomenon Badge (朝霞 / 晚霞胶囊) -->
        <span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold tracking-wide {isDawn ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20' : 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20'}">
          {#if isDawn}
            <Sun class="w-3.5 h-3.5" />
            <span>朝霞日出</span>
          {:else}
            <Moon class="w-3.5 h-3.5" />
            <span>晚霞日落</span>
          {/if}
        </span>

        <!-- Quality Level Badge -->
        {#if data.quality_level}
          <span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-mono bg-secondary text-secondary-foreground border border-border">
            <Sparkles class="w-3 h-3 text-amber-500" />
            <span>{data.quality_level}</span>
          </span>
        {/if}
      </div>

      <!-- Target Date & Source -->
      <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground font-mono-data">
        <span>{data.target_date}</span>
        <span class="text-border">·</span>
        <span class="px-2 py-0.5 rounded-md bg-secondary text-secondary-foreground text-xs font-semibold">
          {{ gfs: 'GFS 数值模式', ec: 'ECMWF IFS 数值模式', satellite: '风云卫星实况' }[data.model_source] || data.model_source.toUpperCase()}
        </span>
      </div>

      <!-- Cloud Structure Meta -->
      <div class="mt-3 text-xs text-muted-foreground flex items-center gap-1.5 flex-wrap">
        <Gauge class="w-3.5 h-3.5 text-primary/80" />
        {#if data.dominant_burnable_pressure_family}
          <span class="font-medium text-card-foreground">{familyLabels[data.dominant_burnable_pressure_family]}</span>
        {:else if hasPhysicalBurnableCloud}
          <span>存在通过物理条件的可烧云 · 主导可烧压力族未解析</span>
        {:else}
          <span class="text-muted-foreground">当前时段光路受阻，缺乏物理可烧云</span>
        {/if}
      </div>
    </div>

    <!-- Right Column: Precision Score -->
    <div class="flex flex-row sm:flex-col items-baseline sm:items-end justify-between w-full sm:w-auto shrink-0 border-t sm:border-t-0 pt-4 sm:pt-0 border-border/40">
      <div class="flex flex-col sm:items-end">
        <div
          class="font-mono-data text-4xl font-semibold leading-none text-foreground"
        >
          {quality === null ? '—' : quality.toFixed(3)}
        </div>
        <span class="mt-1 text-[11px] text-muted-foreground font-mono tracking-wider">
          {quality === null ? '质量分暂不可用' : '火烧云质量 / 2.5'}
        </span>
      </div>
    </div>
  </div>
  {#if data.best_time_local}
    <p class="mt-4 text-xs text-muted-foreground">最佳时刻 <strong class="font-mono-data font-medium text-foreground">{formatLocalDateTime(data.best_time_local, CHINA_TIME_ZONE)}</strong></p>
  {/if}
  {#if drivers.length}
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4 pt-4 border-t border-border">
      {#each drivers as driver (driver.label)}
        <div class="min-w-0">
          <div class="flex justify-between gap-2 text-xs"><span class="text-muted-foreground">{driver.label}</span><span class="font-mono-data">{((driver.value ?? 0) * 100).toFixed(1)}%</span></div>
          <div class="h-1 mt-2 rounded-full bg-muted overflow-hidden" aria-hidden="true"><div class="h-full bg-primary" style:width={`${Math.max(0, Math.min(100, (driver.value ?? 0) * 100))}%`}></div></div>
        </div>
      {/each}
    </div>
  {/if}
</div>
