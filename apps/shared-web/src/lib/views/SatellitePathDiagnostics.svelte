<script lang="ts">
  import { format, record } from "../forecast-display";
  let { section, utc }: { section: Record<string, unknown>; utc: string } = $props();
  const samples = $derived(Array.isArray(section.profile) ? section.profile.map(record) : []);
  const summary = $derived(Array.isArray(section.summary) ? section.summary.filter((line): line is string => typeof line === "string") : []);
</script>

<section class="satellite-path" aria-label="所选时次太阳光路诊断">
  <header><h2>所选时次太阳光路诊断</h2><time datetime={utc}>{utc}</time></header>
  <p>太阳方位 {format(section.azimuth_deg)}° · 光路采样 {format(section.samples_count, 0)}</p>
  {#each summary as line}<p>{line}</p>{/each}
  <div class="path-scroll">
    <table>
      <thead><tr><th>距离 km</th><th>方位 °</th><th>云底 km ASL</th><th>云顶 km ASL</th><th>目标云评分</th><th>遮挡风险</th><th>受光状态</th></tr></thead>
      <tbody>{#each samples as sample}
        <tr><td>{format(sample.distance_km)}</td><td>{format(sample.bearing_deg)}</td>
          <td>{typeof sample.cloud_base_m === "number" ? format(sample.cloud_base_m / 1000) : "--"}</td>
          <td>{typeof sample.cloud_top_m === "number" ? format(sample.cloud_top_m / 1000) : "--"}</td>
          <td>{format(sample.target_cloud_score, 3)}</td><td>{format(sample.blocker_risk, 3)}</td><td>{String(sample.illumination_status ?? "--")}</td></tr>
      {:else}<tr><td colspan="7">该时次无光路采样</td></tr>{/each}</tbody>
    </table>
  </div>
</section>

<style>
  .satellite-path { padding: 16px 0; border-bottom: 1px solid var(--fs-border); min-width: 0; }
  header { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
  h2 { margin: 0; font-size: 13px; }
  time, p { color: var(--fs-ink2); font-size: 10px; overflow-wrap: anywhere; }
  p { line-height: 1.7; margin: 7px 0; }
  .path-scroll { width: 100%; overflow: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 10px; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 9px; white-space: nowrap; border-bottom: 1px solid var(--fs-border); }
  th { color: var(--fs-ink2); font-weight: 500; }
</style>
