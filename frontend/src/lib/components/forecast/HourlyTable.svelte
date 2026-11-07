<script lang="ts">
  import type { StepPayload } from '@/api/types';
  import { formatTime } from '@/utils/format';

  export let steps: StepPayload[];
  export let bestTime: string | null = null;
  export let selectedTime: string | null = null;
  export let timeZone: string = 'Asia/Shanghai';
  export let onTimeSelected: ((step: StepPayload) => void) | undefined = undefined;

  function percent(value: number | undefined): string {
    return `${Math.round(Number(value ?? 0) * 100)}%`;
  }

  function optionalPercent(value: number | null | undefined): string {
    return value == null ? '—' : percent(value);
  }

  function heightRange(step: StepPayload): string {
    if (
      step.dominant_burnable_cloud_base_asl_mean_m == null
      || step.dominant_burnable_cloud_top_asl_mean_m == null
    ) return '—';
    return `${(step.dominant_burnable_cloud_base_asl_mean_m / 1000).toFixed(1)}–${(step.dominant_burnable_cloud_top_asl_mean_m / 1000).toFixed(1)} km ASL`;
  }

  const familyLabels = { high: '高云压力族', middle: '中云压力族', low: '低云压力族' } as const;
  $: validSteps = (Array.isArray(steps) ? steps : []).filter((step) => step.valid);
</script>

<div class="glass-panel rounded-lg border overflow-hidden">
  <div class="px-5 pt-4 pb-2">
    <h3 class="font-display text-base text-card-foreground">逐时质量明细</h3>
  </div>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[960px] text-sm font-mono-data">
      <thead>
        <tr class="border-b border-border bg-muted/50">
          <th class="head text-left">时间</th>
          <th class="head text-right">质量 / 2.5</th>
          <th class="head text-left">主导可烧压力族</th>
          <th class="head text-right">加权云底–云顶</th>
          <th class="head text-right">天空占比</th>
          <th class="head text-right">亮度颜色</th>
          <th class="head text-right">可烧比例</th>
          <th class="head text-right">观测回程透过率</th>
          <th class="head text-right">太阳入射阻挡</th>
          <th class="head text-right">有效 AOD</th>
        </tr>
      </thead>
      <tbody>
        {#each validSteps as step, index}
          <tr
            class="border-b border-border/50 cursor-pointer transition-colors hover:bg-accent/50 {step.valid_time_local === selectedTime ? 'bg-primary/10' : index % 2 ? 'bg-muted/20' : ''}"
            on:click={() => onTimeSelected?.(step)}
          >
            <td class="py-2.5 px-4 text-left font-medium text-card-foreground whitespace-nowrap">
              {formatTime(step.valid_time_local, timeZone)}
              {#if step.valid_time_local === bestTime}<span class="ml-1 text-[10px] text-primary">最佳</span>{/if}
            </td>
            <td class="py-2.5 px-3 text-right font-semibold text-card-foreground">{Number(step.instant_quality_index ?? 0).toFixed(3)}</td>
            <td class="py-2.5 px-3 text-left">
              {#if step.dominant_burnable_pressure_family}
                <strong class="font-medium text-card-foreground">{familyLabels[step.dominant_burnable_pressure_family]}</strong>
              {:else}
                <span class="text-muted-foreground">无可烧目标云</span>
              {/if}
            </td>
            <td class="py-2.5 px-3 text-right text-muted-foreground whitespace-nowrap">{heightRange(step)}</td>
            <td class="py-2.5 px-3 text-right text-card-foreground">{percent(step.burnable_cloud_coverage)}</td>
            <td class="py-2.5 px-3 text-right text-card-foreground">{percent(step.brightness_color)}</td>
            <td class="py-2.5 px-3 text-right text-card-foreground">{percent(step.forward_burnable_cloud_ratio)}</td>
            <td class="py-2.5 px-3 text-right text-card-foreground">{optionalPercent(step.observer_return_transmission)}</td>
            <td class="py-2.5 px-3 text-right text-muted-foreground">{percent(step.solar_incoming_block_risk)}</td>
            <td class="py-2.5 px-4 text-right text-muted-foreground">{step.quality_aod550 == null ? '—' : step.quality_aod550.toFixed(3)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>

<style>
  .head { padding: 10px 12px; color: var(--muted-foreground); font-size: 11px; font-weight: 500; white-space: nowrap; }
</style>
