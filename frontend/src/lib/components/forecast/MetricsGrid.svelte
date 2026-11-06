<script lang="ts">
  import type { ForecastPayload } from '@/api/types';
  import { CHINA_TIME_ZONE, score, formatLocalDateTime, formatTime, formatUtcDateTime, modelName } from '@/utils/format';
  import Clock from '@lucide/svelte/icons/clock';
  import Eye from '@lucide/svelte/icons/eye';
  import Layers from '@lucide/svelte/icons/layers';
  import Compass from '@lucide/svelte/icons/compass';
  import Sparkles from '@lucide/svelte/icons/sparkles';
  import Activity from '@lucide/svelte/icons/activity';
  import ShieldAlert from '@lucide/svelte/icons/shield-alert';
  import CheckCircle2 from '@lucide/svelte/icons/check-circle-2';

  let {
    data,
    displayTimeZone = CHINA_TIME_ZONE,
  }: {
    data: ForecastPayload;
    displayTimeZone?: string;
  } = $props();

  let isDawn = $derived(data.phenomenon === 'dawn');
  let label = $derived(isDawn ? '朝霞' : '晚霞');
  let steps = $derived(data.steps || []);
  let firstStep = $derived(steps[0]?.valid_time_local);
  let lastStep = $derived(steps[steps.length - 1]?.valid_time_local);
  let cycleEntries = $derived(Object.entries(data.model_cycles || {}));
  let cycles = $derived(
    cycleEntries.length
      ? cycleEntries
      : [[data.model_source, data.cycle_time_utc]]
  );
  let pageTimeZone = $derived(displayTimeZone || CHINA_TIME_ZONE);
  let cycleText = $derived(
    cycles
      .map(([model, time]) => `${modelName(model)} ${formatUtcDateTime(time)}`)
      .join(' / ')
  );
  const regimeLabels: Record<string, string> = {
    local_deck: '本地云幕',
    distant_target: '远方目标云',
    remote_boundary: '远方云边界',
    cloud_hole: '云洞透光',
    convective: '对流厚云',
    horizon_blocked: '日出方向云幕遮挡',
    solar_incoming_blocked_field: '太阳入射受阻云场',
    clear: '晴空 / 无有效目标',
    invalid: '无有效场景',
    unknown: '未分类',
  };
  const colorLabels: Record<string, string> = {
    golden: '金黄',
    orange_red: '橙红',
    deep_red: '深红',
    muted_glow: '闷烧',
  };
  const phaseLabels: Record<string, string> = {
    ice: '冰相成分', mixed: '冰水混合成分', water: '液态水成分', unknown: '相态未知',
  };
  const familyLabels = { high: '高云压力族', middle: '中云压力族', low: '低云压力族' } as const;
  const genusLabels: Record<string, string> = {
    cirrus: '卷云', cirrocumulus: '卷积云', cirrostratus: '卷层云',
    altocumulus: '高积云', altostratus: '高层云', nimbostratus: '雨层云',
    stratocumulus: '层积云', stratus: '层云', cumulus: '积云', cumulonimbus: '积雨云',
  };
  // 烧霞适配度排序取自 SunsetBot 火烧云教程（章节二）的观测经验：
  // 高层云 > 卷层云 > 卷云 ≈ 高积云 > 层积云（条件性）> 层云（不成霞）。
  const genusBurnAffinity: Record<string, string> = {
    altostratus: '最佳烧霞云属（橘黄到红）',
    cirrostratus: '次佳（面积大、持续久）',
    cirrus: '常见且持久（金黄到红）',
    altocumulus: '壮观（橘黄到橘红）',
    cirrocumulus: '少见，偶伴冰晕',
    nimbostratus: '降水云 · 遮光风险高',
    stratocumulus: '条件性（云底高 + 大气极洁才有短时橘红）',
    cumulus: '对流云晚霞形态',
    cumulonimbus: '对流晚霞 / 云砧晚霞',
    stratus: '不成烧霞（可作云海陪衬）',
  };
  function topGenusCandidates(
    scores: Record<string, number> | undefined,
    limit: number,
  ): [string, number][] {
    if (!scores) return [];
    return Object.entries(scores)
      .sort(([, a], [, b]) => b - a)
      .slice(0, limit);
  }
  const trendLabels: Record<string, string> = {
    forming: '正在生成', strengthening: '正在增强', steady: '基本稳定',
    weakening: '正在减弱', dissipating: '正在消散', unknown: '演变未知',
  };

  const timeItems = (d: ForecastPayload) => [
    [
      isDawn ? '日出时刻' : '日落时刻',
      formatLocalDateTime(d.anchor_local, pageTimeZone),
    ],
    [`最佳${label}`, formatLocalDateTime(d.best_time_local, pageTimeZone)],
    [
      '预测窗口',
      d.timing_window
        ? `${formatTime(d.timing_window.start_time_local, pageTimeZone)}–${formatTime(d.timing_window.end_time_local, pageTimeZone)}`
        : d.geometry_window
        ? `${formatTime(d.geometry_window.start_time_local, pageTimeZone)}–${formatTime(d.geometry_window.end_time_local, pageTimeZone)}`
        : firstStep && lastStep
        ? `${formatTime(firstStep, pageTimeZone)}–${formatTime(lastStep, pageTimeZone)}`
        : '暂无',
    ],
    [data.forecast_mode === 'nowcast' ? '卫星观测' : '模型起报', cycleText],
  ];

  const metrics = (d: ForecastPayload) => [
    ['火烧云质量', `${Number(d.quality_index ?? 0).toFixed(3)}`, Number(d.quality_index ?? 0) / 2.5],
    ['有效可烧云', score(d.quality_components?.burnable_cloud_coverage_effective), d.quality_components?.burnable_cloud_coverage_effective ?? 0],
    ['亮度与色彩', score(d.quality_components?.brightness_color), d.quality_components?.brightness_color ?? 0],
    ['云质厚度', score(d.quality_components?.cloud_material), d.quality_components?.cloud_material ?? 0],
    ['置信度', score(d.confidence_score), d.confidence_score],
  ];

  function sceneStrength(d: ForecastPayload): number {
    return Number(d.scene_scores?.[d.selected_regime ?? ''] ?? 0);
  }

  function boundaryTimeImpact(d: ForecastPayload): string {
    const boundary = d.boundary_uncertainty;
    if (!boundary?.start_time_earliest_local || !boundary.start_time_latest_local) {
      return '对开始时刻没有可量化影响';
    }
    const earliest = formatTime(boundary.start_time_earliest_local, pageTimeZone);
    const latest = formatTime(boundary.start_time_latest_local, pageTimeZone);
    return earliest === latest ? `${earliest}（基本不变）` : `${earliest}–${latest}`;
  }
</script>

<!-- Time Context Bar -->
<div class="glass-panel rounded-2xl border overflow-hidden">
  <div class="grid grid-cols-2 lg:grid-cols-4 divide-x divide-y lg:divide-y-0 divide-border">
    {#each timeItems(data) as [name, value], i}
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium mb-1">{name}</span>
        <strong class="block text-sm font-semibold text-card-foreground font-mono-data leading-relaxed break-words">{value}</strong>
      </div>
    {/each}
  </div>
</div>

<!-- Diagnostics Group -->
{#if data.selected_regime}
  <div class="glass-panel border overflow-hidden rounded-2xl">
    <div class="grid grid-cols-2 lg:grid-cols-4 divide-x divide-y lg:divide-y-0 divide-border">
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">主导云场</span>
        <strong class="block mt-1 text-sm font-semibold text-card-foreground [overflow-wrap:anywhere]">
          {data.dominant_burnable_pressure_family ? regimeLabels[data.selected_regime] || data.selected_regime : '无可烧目标云'}
        </strong>
        <small class="diagnostic-source">最佳时刻空间结构 · 信号 {score(sceneStrength(data))}</small>
      </div>
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">预期霞光颜色</span>
        <strong class="block mt-1 text-sm font-semibold text-card-foreground">
          {data.dominant_burnable_pressure_family ? colorLabels[data.color_diagnosis?.type || ''] || '暂无' : '不适用'}
          {#if data.dominant_burnable_pressure_family && data.color_diagnosis}<small class="font-normal text-muted-foreground"> · 可信度 {score(data.color_diagnosis.confidence)}</small>{/if}
        </strong>
        <small class="diagnostic-source">逐时质量、太阳高度、AOD、光路通视联合判断</small>
      </div>
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">主导压力族 / 相态</span>
        <strong class="block mt-1 text-sm font-semibold text-card-foreground">
          {data.dominant_burnable_pressure_family ? familyLabels[data.dominant_burnable_pressure_family] : '无可烧目标云'}
        </strong>
        <small class="diagnostic-source">
          {phaseLabels[data.dominant_burnable_cloud_phase?.label || 'unknown'] || '相态未知'}
          {#if data.dominant_burnable_cloud_phase?.ice_fraction != null}
            · 冰含量占比 {Math.round(data.dominant_burnable_cloud_phase.ice_fraction * 100)}%
          {/if}
        </small>
        {#if data.cloud_genus_proxy?.label && data.cloud_genus_proxy.label !== 'unknown'}
          <strong class="block mt-1 text-sm font-semibold text-card-foreground">
            云属（模型代理）· {genusLabels[data.cloud_genus_proxy.label] || data.cloud_genus_proxy.label}
            <small class="font-normal text-muted-foreground"> · 置信 {score(data.cloud_genus_proxy.confidence)}</small>
          </strong>
          {#if genusBurnAffinity[data.cloud_genus_proxy.label]}
            <small class="diagnostic-source block">{genusBurnAffinity[data.cloud_genus_proxy.label]}</small>
          {/if}
          <small class="diagnostic-source block mt-1">
            {#each topGenusCandidates(data.cloud_genus_proxy.candidate_scores, 3) as [genus, candidateScore], i}
              {#if i > 0} · {/if}{genusLabels[genus] || genus} {Math.round(candidateScore * 100)}%
            {/each}
            · 由压力族+相态+云体几何推断，不参与评分
          </small>
        {:else if data.dominant_burnable_cloud_genus_diagnosis}
          <small class="diagnostic-source block mt-1">
            云属未定 · 压力族仅兼容
            {data.dominant_burnable_cloud_genus_diagnosis.compatible_genera.map((genus) => genusLabels[genus] || genus).join(' / ')}
            · 不参与评分
          </small>
        {/if}
      </div>
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">目标云演变趋势</span>
        <strong class="block mt-1 text-sm font-semibold text-card-foreground">
          {data.dominant_burnable_pressure_family ? trendLabels[data.cloud_evolution?.trend || 'unknown'] || '演变未知' : '不适用'}
          {#if data.dominant_burnable_pressure_family && (data.cloud_evolution?.cloud_hole_closure_risk ?? 0) > 0.35}
            <small class="font-normal text-amber-600"> · 云洞封闭风险 {score(data.cloud_evolution?.cloud_hole_closure_risk ?? 0)}</small>
          {/if}
        </strong>
        <small class="diagnostic-source">最佳时刻前后 {Math.round(data.cloud_evolution?.comparison_minutes ?? 20)} 分钟目标云场变化</small>
      </div>
    </div>
  </div>
{/if}

{#if data.boundary_uncertainty}
  {@const boundary = data.boundary_uncertainty}
  <div class="glass-panel border overflow-hidden rounded-2xl">
    <div class="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-border bg-muted/20">
      <div>
        <h3 class="text-sm font-semibold text-card-foreground">目标云边界位置敏感度</h3>
        <p class="mt-0.5 text-xs text-muted-foreground">
          {boundary.source === 'target-candidate-envelope' ? '多个候选目标给出的边界位置范围' : '模式从有云到晴空的过渡位置范围'}；不直接计入质量分
        </p>
      </div>
      <strong class:text-emerald-600={boundary.classification_stable} class:text-amber-600={!boundary.classification_stable} class="text-xs font-semibold px-2.5 py-1 rounded-full bg-secondary border border-border whitespace-nowrap">
        {boundary.classification_stable ? '可烧判断稳定' : '可烧判断敏感'}
      </strong>
    </div>
    <div class="grid grid-cols-2 lg:grid-cols-4 divide-x divide-y lg:divide-y-0 divide-border">
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">可能位置</span>
        <strong class="block mt-1 text-sm font-mono-data font-semibold text-card-foreground">
          {boundary.lower_distance_km.toFixed(0)}–{boundary.upper_distance_km.toFixed(0)} km
        </strong>
      </div>
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">位置跨度</span>
        <strong class="block mt-1 text-sm font-mono-data font-semibold text-card-foreground">
          {boundary.width_km.toFixed(0)} km
        </strong>
      </div>
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">判断影响</span>
        <strong class="block mt-1 text-sm font-mono-data font-semibold text-card-foreground">
          {boundary.classification_stable
            ? '边界移动不改变结论'
            : boundary.nearest_flip_displacement_km == null
            ? '边界移动可能改变结论'
            : `移动约 ${boundary.nearest_flip_displacement_km.toFixed(0)} km 可改变结论`}
        </strong>
      </div>
      <div class="p-4 min-w-0">
        <span class="block text-[11px] text-muted-foreground font-medium">开始时刻影响</span>
        <strong class="block mt-1 text-sm font-mono-data font-semibold text-card-foreground break-words">
          {boundaryTimeImpact(data)}
        </strong>
      </div>
    </div>
  </div>
{/if}

<!-- Metrics Grid -->
<div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3.5">
  {#each metrics(data) as [label, displayValue, rawValue]}
    <div class="glass-panel rounded-2xl border p-4 hover:border-primary/40 transition-all">
      <span class="block text-[11px] font-medium text-muted-foreground">{label}</span>
      <strong class="block mt-1.5 font-display text-2xl text-card-foreground font-mono-data">{displayValue}</strong>
      <!-- Mini progress indicator with warm gradient -->
      <div class="mt-3 h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          class="h-full rounded-full transition-all duration-500"
          style="width: {Math.round(Number(rawValue) * 100)}%; background: linear-gradient(90deg, #f59e0b, #f97316)"
        ></div>
      </div>
    </div>
  {/each}
</div>

<!-- Summary -->
{#if data.summary.length > 0}
  <div class="glass-panel rounded-2xl border p-5">
    <h3 class="text-sm font-semibold text-card-foreground mb-3 flex items-center gap-1.5">
      <CheckCircle2 class="w-4 h-4 text-primary" />
      <span>物理仿真与气象分析结论</span>
    </h3>
    <ul class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2.5">
      {#each data.summary as item}
        <li class="flex items-start gap-2.5 text-xs text-muted-foreground leading-relaxed">
          <span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 bg-primary"></span>
          <span>{item}</span>
        </li>
      {/each}
    </ul>
  </div>
{/if}

<style>
  .diagnostic-source {
    display: block;
    margin-top: 5px;
    color: var(--muted-foreground);
    font-size: 10px;
    line-height: 1.45;
  }
</style>
