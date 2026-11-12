<script lang="ts">
  import { format, numeric, percent, record, steps, type RecordValue } from "../forecast-display";
  import { displayTime, familyLabels, genusLabels, label, nowcastSections, qualitySections, range, summaryLines } from "../web-report-display";
  let { report, timezone, mode = "overview" }: { report: RecordValue | null; timezone: string; mode?: "overview" | "diagnostics" } = $props();
  const boundary = $derived(record(report?.boundary_uncertainty));
  const phase = $derived(record(report?.dominant_burnable_cloud_phase));
  const genus = $derived(record(report?.cloud_genus_proxy));
  const evolution = $derived(record(report?.cloud_evolution));
  const timing = $derived(record(report?.timing_window));
  const geometry = $derived(record(report?.geometry_window));
  const sections = $derived([...qualitySections(report), ...nowcastSections(report)]);
  const candidates = $derived(Object.entries(record(genus.candidate_scores)).filter(([, value]) => numeric(value) !== null).sort(([, a], [, b]) => Number(b) - Number(a)).slice(0, 3));
  const compatible = $derived(Array.isArray(record(report?.dominant_burnable_cloud_genus_diagnosis).compatible_genera)
    ? (record(report?.dominant_burnable_cloud_genus_diagnosis).compatible_genera as unknown[]).filter((value): value is string => typeof value === "string") : []);
  const cycles = $derived(Object.entries(record(report?.model_cycles)));
  const physicalSteps = $derived(numeric(record(report?.quality_components).physically_burnable_steps)
    ?? (steps(report).some((step) => typeof step.physically_burnable === "boolean")
      ? steps(report).filter((step) => step.physically_burnable === true).length : null));
  const noTarget = $derived(!report?.dominant_burnable_pressure_family && physicalSteps === 0);
</script>

{#if report}
  <div class="web-details">
    {#if mode === "overview"}
      <section aria-label="时间与数据周期">
        <h2>时间与数据周期</h2>
        <dl class="context-grid">
          <div><dt>{report.phenomenon === "dawn" ? "日出时刻" : "日落时刻"}</dt><dd>{displayTime(report.anchor_local, timezone, true)}</dd></div>
          <div><dt>最佳时刻</dt><dd>{displayTime(report.best_time_local, timezone, true)}</dd></div>
          <div><dt>预测窗口</dt><dd>{displayTime(timing.start_time_local ?? geometry.start_time_local, timezone)} - {displayTime(timing.end_time_local ?? geometry.end_time_local, timezone)}</dd></div>
          <div><dt>数据周期 UTC</dt><dd>{#if cycles.length}{#each cycles as [id, time]}<span>{label(id)} {displayTime(time, "UTC", true)}</span>{/each}{:else}{displayTime(report.cycle_time_utc, "UTC", true)}{/if}</dd></div>
        </dl>
      </section>
      <section aria-label="云场与色彩诊断">
        <h2>云场与色彩诊断</h2>
        <dl class="context-grid">
          <div><dt>主导云场</dt><dd>{noTarget ? "无可烧目标云" : label(report.selected_regime)}<small>信号 {percent(record(report.scene_scores)[String(report.selected_regime)])}</small></dd></div>
          <div><dt>预期霞光颜色</dt><dd>{noTarget ? "不适用" : label(record(report.color_diagnosis).type)}{#if !noTarget}<small>可信度 {percent(record(report.color_diagnosis).confidence)}</small>{/if}</dd></div>
          <div><dt>主导可烧压力族 / 相态</dt><dd>{familyLabels[String(report.dominant_burnable_pressure_family)] ?? "--"}<small>{label(phase.label)} · 冰比例 {percent(phase.ice_fraction)}</small></dd></div>
          <div><dt>物理可烧采样</dt><dd>{format(physicalSteps, 0)}</dd></div>
          <div><dt>云属（模型代理）</dt><dd>{genusLabels[String(genus.label)] ?? label(genus.label)}<small>置信度 {percent(genus.confidence)}</small>{#if candidates.length}<small>{candidates.map(([id, value]) => `${genusLabels[id] ?? id} ${percent(value)}`).join(" · ")}</small>{/if}{#if (!genus.label || genus.label === "unknown") && compatible.length}<small>压力族兼容 {compatible.map((id) => genusLabels[id] ?? id).join(" / ")}</small>{/if}<small>压力族、相态与几何代理，不参与评分</small></dd></div>
          <div><dt>目标云演变</dt><dd>{noTarget ? "不适用" : label(evolution.trend)}{#if !noTarget}<small>云洞封闭风险 {percent(evolution.cloud_hole_closure_risk)} · 对比 {format(evolution.comparison_minutes, 0)} 分钟</small>{/if}</dd></div>
          <div><dt>加权云底 - 云顶</dt><dd>{range(report.dominant_burnable_cloud_base_asl_mean_m, report.dominant_burnable_cloud_top_asl_mean_m, "km ASL", 1000)}</dd></div>
        </dl>
      </section>
      {#if summaryLines(report).length}
        <section aria-label="物理与气象分析结论"><h2>物理与气象分析结论</h2><ul>{#each summaryLines(report) as item}<li>{item}</li>{/each}</ul></section>
      {/if}
    {:else}
      {#if Object.keys(boundary).length}
        <section aria-label="目标云边界位置敏感度">
          <h2>目标云边界位置敏感度</h2>
          <dl class="context-grid">
            <div><dt>可能位置</dt><dd>{range(boundary.lower_distance_km, boundary.upper_distance_km, "km", 1, 0)}</dd></div>
            <div><dt>位置跨度</dt><dd>{numeric(boundary.width_km) === null ? "--" : `${format(boundary.width_km, 0)} km`}</dd></div>
            <div><dt>可烧判断</dt><dd>{boundary.classification_stable === true ? "稳定" : boundary.classification_stable === false ? "敏感" : "--"}</dd></div>
            <div><dt>改变结论所需位移</dt><dd>{numeric(boundary.nearest_flip_displacement_km) === null ? "--" : `${format(boundary.nearest_flip_displacement_km, 0)} km`}</dd></div>
            <div><dt>开始时刻影响</dt><dd>{displayTime(boundary.start_time_earliest_local, timezone)} - {displayTime(boundary.start_time_latest_local, timezone)}</dd></div>
            <div><dt>持续时间范围</dt><dd>{range(boundary.duration_min_minutes, boundary.duration_max_minutes, "分钟", 1, 0)}</dd></div>
          </dl>
        </section>
      {/if}
      {#if Object.keys(geometry).length}
        <section aria-label="几何时间窗"><h2>几何时间窗</h2><dl class="context-grid">
          <div><dt>开始 / 中点 / 结束</dt><dd>{displayTime(geometry.start_time_local, timezone)} / {displayTime(geometry.midpoint_time_local, timezone)} / {displayTime(geometry.end_time_local, timezone)}</dd></div>
          <div><dt>阴影移动速度</dt><dd>{format(geometry.shadow_speed_km_per_min)} km/min</dd></div>
          <div><dt>云边界距离</dt><dd>{format(geometry.edge_distance_km)} km</dd></div>
        </dl></section>
      {/if}
      {#each sections as section}
        <section aria-label={section.title}>
          <h2>{section.title}</h2>
          <dl class="detail-grid">{#each section.rows as row}<div><dt>{row.label}</dt><dd>{row.value}</dd></div>{/each}</dl>
        </section>
      {/each}
      <section aria-label="评分物理口径">
        <h2>评分物理口径</h2>
        <p class="methodology">太阳方向全量云场逐视线合成，近处低云会遮挡后方云体；可烧量只统计同时受光且能被地面观测者看见的云底面积。绝对可烧面积决定 0–2.5 基础分，几何遮挡已计入，不重复扣分；持续时间与 AOD 各修正一次。摄影焦段、Top-K、代表云和云边界不进入主评分。</p>
      </section>
      {#if Object.keys(record(report.data_provenance ?? report.ecmwf_provenance)).length}
        <section aria-label="数据来源"><h2>数据来源</h2><dl class="detail-grid">
          {#each Object.entries(record(report.data_provenance ?? report.ecmwf_provenance)).filter(([, value]) => typeof value === "string" || typeof value === "boolean") as [key, value]}
            <div><dt>{key}</dt><dd>{typeof value === "boolean" ? value ? "是" : "否" : String(value)}</dd></div>
          {/each}
        </dl></section>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .web-details { min-width: 0; }
  section { border-top: 1px solid var(--fs-border); padding: 16px 0; }
  h2 { margin: 0 0 12px; font-size: 13px; font-weight: 600; }
  dl { margin: 0; display: grid; gap: 0 18px; }
  .context-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  dl > div { min-width: 0; padding: 9px 0; }
  dt { color: var(--fs-ink2); font-size: 11px; line-height: 1.5; }
  dd { margin: 5px 0 0; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  dd span, small { display: block; } small { font-size: 10px; color: var(--fs-ink2); }
  .detail-grid > div { display: flex; justify-content: space-between; gap: 14px; border-bottom: 1px solid var(--fs-border); }
  .detail-grid dd { text-align: right; margin: 0; }
  ul { margin: 0; padding-left: 18px; color: var(--fs-ink2); font-size: 12px; line-height: 1.8; }
  li + li { margin-top: 6px; }
  .methodology { margin: 0; font-size: 11px; line-height: 1.8; color: var(--fs-ink2); }
  @media (max-width: 700px) { .context-grid, .detail-grid { grid-template-columns: 1fr; } }
</style>
