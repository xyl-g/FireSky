<script lang="ts">
  import { format, numeric, percent, steps, type RecordValue } from "../forecast-display";
  import { displayTime, familyLabels, range } from "../web-report-display";
  import { stepUtc } from "../forecast-time";
  let { report, timezone, emptyLabel = "等待预测", selectedUtc = null, onSelect = () => {} }: {
    report: RecordValue | null; timezone: string; emptyLabel?: string; selectedUtc?: string | null; onSelect?: (utc: string) => void;
  } = $props();
  const standardFields: Array<[string, string, "percent" | "number"]> = [
    ["burnable_cloud_coverage", "天空占比", "percent"], ["brightness_color", "亮度颜色", "percent"],
    ["forward_burnable_cloud_ratio", "可烧比例", "percent"], ["observer_return_transmission", "观测回程透过率", "percent"],
    ["solar_incoming_block_risk", "太阳入射阻挡", "percent"], ["quality_aod550", "有效 AOD", "number"],
  ];
  const regionalFields: Array<[string, string, "percent" | "number"]> = [
    ["burnable_cloud_coverage", "天空占比", "percent"],
    ["forward_view_cloud_field_amount", "前方总云量", "percent"],
    ["forward_burnable_cloud_field_amount", "前方可烧云量", "percent"],
    ["forward_burnable_cloud_ratio", "可烧比例", "percent"],
    ["solar_incoming_block_risk", "太阳入射阻挡", "percent"],
    ["observer_return_transmission", "观测回程透过率", "percent"],
    ["local_aod550", "本地 AOD550", "number"],
  ];
  const regional = $derived(report?.regional === true);
  const fields = $derived(regional ? regionalFields : standardFields);
  const bestUtc = $derived(stepUtc({ valid_time_utc: report?.best_time_utc, valid_time_local: report?.best_time_local }));
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard scrolling for the complete ledger) -->
<div class="ledger-scroll" role="region" aria-label="逐时质量明细" tabindex="0">
  <table>
    <thead><tr><th>时间</th><th>质量 / 2.5</th>{#if !regional}<th>档位</th><th>太阳高度</th>{/if}<th>状态</th>{#if !regional}<th>主导可烧压力族</th><th>加权云底 - 云顶</th>{/if}{#each fields as [, title]}<th>{title}</th>{/each}</tr></thead>
    <tbody>{#each steps(report) as step}
      {@const valid = step.valid !== false}
      {@const utc = stepUtc(step)}
      <tr class:selected={utc !== null && selectedUtc === utc} class:best={utc !== null && utc === bestUtc}>
        <td><button aria-label={`选择时刻 ${displayTime(step.valid_time_local ?? step.valid_time_utc, timezone)}`} aria-pressed={utc !== null && selectedUtc === utc} disabled={!utc} onclick={() => { if (utc) onSelect(utc); }}>{displayTime(step.valid_time_local ?? step.valid_time_utc, timezone)}{#if utc !== null && utc === bestUtc}<small>最佳</small>{/if}</button></td>
        <td>{valid ? format(step.instant_quality_index, 3) : "--"}</td>
        {#if !regional}<td>{String(step.instant_quality_level ?? step.quality_level ?? "--")}</td><td>{format(step.solar_elevation_deg)}</td>{/if}
        <td>{!valid ? "缺测" : numeric(step.instant_quality_index) === null ? "无数据" : "有效"}</td>
        {#if !regional}<td>{familyLabels[String(step.dominant_burnable_pressure_family)] ?? "--"}</td><td>{range(step.dominant_burnable_cloud_base_asl_mean_m, step.dominant_burnable_cloud_top_asl_mean_m, "km ASL", 1000)}</td>{/if}
        {#each fields as [key, , type]}<td>{!valid ? "--" : type === "percent" ? percent(step[key]) : format(step[key], 3)}</td>{/each}
      </tr>
    {:else}<tr><td colspan={(regional ? 3 : 7) + fields.length} class="empty">{emptyLabel}</td></tr>{/each}</tbody>
  </table>
</div>

<style>
  .ledger-scroll { max-width: 100%; overflow: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; font-variant-numeric: tabular-nums; }
  th { color: var(--fs-ink2); font-weight: 500; background: var(--fs-hover); }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--fs-border); white-space: nowrap; }
  tr:hover, tr.selected { background: var(--fs-hover); }
  tr.best td:first-child { color: var(--fs-accent); }
  button { border: 0; color: inherit; background: transparent; font: inherit; padding: 4px 0; min-height: 28px; cursor: pointer; }
  small { margin-left: 6px; font-size: 10px; color: var(--fs-accent); }
  .empty { color: var(--fs-ink2); text-align: center; padding: 40px 12px; }
</style>
