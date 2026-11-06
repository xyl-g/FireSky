<script lang="ts">
  import type { ForecastPayload } from '@/api/types';

  let { data }: { data: ForecastPayload } = $props();

  let quality = $derived(data.quality_components);
  let analysisRadiusKm = $derived(data.analysis_radius_km ?? quality?.forward_field_radius_km);
  let viewAmount = $derived(quality?.forward_view_cloud_field_amount_peak ?? 0);
  let burnableAmount = $derived(quality?.forward_burnable_cloud_field_amount_peak ?? 0);
  let burnableEffective = $derived(quality?.forward_burnable_cloud_field_amount_effective ?? 0);
  let burnableRatio = $derived(quality?.forward_burnable_cloud_ratio_peak ?? 0);
  let burnableLoad = $derived(
    quality?.forward_burnable_cloud_load_effective
    ?? quality?.forward_burnable_cloud_load_peak
    ?? burnableEffective
  );
  let localViewAmount = $derived(quality?.observer_local_cloud_field_amount_peak ?? 0);
  let localBurnableAmount = $derived(quality?.observer_local_burnable_cloud_field_amount_peak ?? 0);
  let localBurnableLoad = $derived(quality?.observer_local_burnable_cloud_load_peak ?? 0);
  let localColumnScore = $derived(quality?.observer_column_quality_index ?? 0);
  let localColumnPeakScore = $derived(quality?.observer_column_quality_peak ?? 0);
  let qualityAod = $derived(quality?.quality_aod550 ?? 0);
  let incomingAodFactor = $derived(quality?.incoming_aod_illumination_factor ?? 1);
  let observerAodFactor = $derived(quality?.observer_aod_contrast_factor ?? 1);
  let aodAdjustment = $derived((quality?.incoming_aod_adjustment ?? 0) + (quality?.observer_aod_adjustment ?? 0));
  let levelValue = $derived(
    quality?.coverage_score
    ?? quality?.coverage_base_score
    ?? data.quality_index
    ?? 0
  );
  let modelLabel = $derived(
    data.model_source === 'ec'
      ? 'EC TCC + PL-RH 云柱代理'
      : data.model_source === 'satellite'
      ? '卫星校准云柱'
      : 'GFS 全量云场'
  );
  let modelExplanation = $derived(
    data.model_source === 'ec'
      ? 'EC 公共 Open Data 的总云量（TCC）与压力层相对湿度（PL-RH）代理；不含 GFS 云场。'
      : data.model_source === 'satellite'
      ? '基于校准卫星云柱的实况/近实时输入。'
      : '评分统计观测点朝太阳方向、分析范围内的全部 GFS 网格。'
  );

  let adjustments = $derived(
    quality ? [
      ['云场绝对量', `${(burnableEffective * 100).toFixed(1)}% · 可烧载荷 ${burnableLoad.toFixed(2)} · 云量基础分 ${(quality.coverage_score ?? quality.coverage_base_score ?? 0).toFixed(3)}`, quality.coverage_base_score ?? 0],
      ['可烧云比例', `${(burnableRatio * 100).toFixed(1)}% · 总云场 ${(viewAmount * 100).toFixed(1)}%`, 0],
      ['几何遮挡（已计入可烧量）', `最佳云入射遮挡 ${(quality.solar_incoming_block_risk_at_best_cloud ?? quality.solar_incoming_block_risk_mean ?? 0).toFixed(3)} · 不重复扣分`, 0],
      ['持续时间修正', `${(quality.duration_minutes ?? 0).toFixed(0)} 分钟 · ×${(quality.duration_factor ?? 1).toFixed(3)}`, quality.duration_adjustment ?? 0],
      ['本地 AOD 来源 + 双段因子', `本地 ${qualityAod.toFixed(3)} · 入射 ×${incomingAodFactor.toFixed(3)} · 观测 ×${observerAodFactor.toFixed(3)}`, aodAdjustment],
    ] as [string, string, number][] : []
  );

  function coverageLevel(value: number): string {
    if (value < 0.03) return '微微烧';
    if (value < 0.12) return '小烧';
    if (value < 0.25) return '小烧到中烧';
    if (value < 0.40) return '中烧';
    if (value < 0.55) return '中烧到大烧';
    if (value < 0.70) return '不完美大烧';
    if (value < 0.85) return '典型大烧';
    if (value < 0.95) return '优质大烧';
    return '世纪大烧候选';
  }

  function adjustment(value: number): string {
    if (Math.abs(value) < 0.0005) return '0.000';
    return `${value > 0 ? '+' : ''}${value.toFixed(3)}`;
  }
</script>

{#if quality && data.quality_index !== undefined}
  <section class="glass-panel border rounded-lg overflow-hidden">
    <header class="flex flex-wrap items-start justify-between gap-3 px-4 py-3 border-b border-border">
      <div>
        <h3 class="text-sm font-medium text-card-foreground">火烧云质量计算</h3>
        <p class="mt-0.5 text-xs text-muted-foreground">
          {coverageLevel(levelValue)} · {modelLabel}
        </p>
      </div>
      <strong class="font-mono-data text-lg text-card-foreground tabular-nums">
        {data.quality_index.toFixed(3)} / 2.5
      </strong>
    </header>

    <div class="grid grid-cols-2 md:grid-cols-6 divide-x divide-y md:divide-y-0 divide-border border-b border-border">
      <div class="metric"><span>前方总云场量</span><strong>{(viewAmount * 100).toFixed(1)}%</strong></div>
      <div class="metric"><span>前方可烧云量</span><strong>{(burnableAmount * 100).toFixed(1)}%</strong></div>
      <div class="metric"><span>可烧 / 总云量</span><strong>{(burnableRatio * 100).toFixed(1)}%</strong></div>
      <div class="metric"><span>有效可烧云量</span><strong>{(burnableEffective * 100).toFixed(1)}%</strong></div>
      <div class="metric"><span>可烧云载荷</span><strong>{burnableLoad.toFixed(2)}</strong></div>
      <div class="metric"><span>观测点本地可烧柱</span><strong>{(localBurnableAmount * 100).toFixed(1)}%</strong><small>云柱 {(localViewAmount * 100).toFixed(1)}% · 载荷 {localBurnableLoad.toFixed(2)}</small></div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 divide-x divide-y lg:divide-y-0 divide-border">
      <div class="metric"><span>总云场时段均值</span><strong>{((quality.forward_view_cloud_field_amount_mean ?? 0) * 100).toFixed(1)}%</strong></div>
      <div class="metric"><span>可烧云场时段均值</span><strong>{((quality.forward_burnable_cloud_field_amount_mean ?? 0) * 100).toFixed(1)}%</strong></div>
      <div class="metric"><span>云量基础分</span><strong>{(quality.coverage_score ?? quality.coverage_base_score ?? 0).toFixed(3)}</strong></div>
      <div class="metric"><span>修正后原始分</span><strong>{(quality.raw_quality_index ?? 0).toFixed(3)}</strong></div>
      <div class="metric"><span>最终质量分</span><strong>{data.quality_index.toFixed(3)}</strong></div>
      <div class="metric"><span>本地云柱诊断分</span><strong>{localColumnScore.toFixed(3)}</strong><small>峰值 {localColumnPeakScore.toFixed(3)} · 不替换主分</small></div>
      <div class="metric"><span>当前限制因素</span><strong>{quality.quality_limiting_factor ?? '—'}</strong></div>
    </div>

    <div class="grid lg:grid-cols-[1fr_280px] border-t border-border">
      <div class="divide-y divide-border">
        {#each adjustments as [label, measured, delta]}
          <div class="grid grid-cols-[minmax(0,1fr)_64px] sm:grid-cols-[minmax(0,1fr)_minmax(0,180px)_64px] items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
            <span class="col-span-2 sm:col-span-1 text-muted-foreground">{label}</span>
            <span class="font-mono-data sm:text-right text-card-foreground tabular-nums break-words">{measured}</span>
            <strong class:text-emerald-600={delta > 0} class:text-red-600={delta < 0} class="font-mono-data text-right font-medium tabular-nums">
              {adjustment(delta)}
            </strong>
          </div>
        {/each}
      </div>
      <div class="border-t lg:border-t-0 lg:border-l border-border divide-y divide-border">
        <div class="cap-row"><span>分析范围</span><strong>{analysisRadiusKm == null ? '—' : `${analysisRadiusKm.toFixed(0)} km`}</strong></div>
        <div class="cap-row"><span>太阳方向扇区</span><strong>±{(quality.forward_sector_half_angle_deg ?? 90).toFixed(0)}°</strong></div>
        <div class="cap-row"><span>总云场来源</span><strong>{modelLabel}</strong></div>
        <div class="cap-row"><span>可烧量来源</span><strong>{quality.quality_coverage_source ?? '前方全量云场'}</strong></div>
        <div class="cap-row"><span>最佳云入射遮挡</span><strong>{((quality.solar_incoming_block_risk_at_best_cloud ?? quality.solar_incoming_block_risk_mean ?? 0) * 100).toFixed(1)}%</strong></div>
        <div class="cap-row"><span>持续时间修正系数</span><strong>×{(quality.duration_factor ?? 1).toFixed(3)}</strong></div>
        <div class="cap-row"><span>入射 / 观测回程因子</span><strong>×{incomingAodFactor.toFixed(3)} / ×{observerAodFactor.toFixed(3)}</strong></div>
        <div class="cap-row"><span>观测回程光学量</span><strong>τ {(quality.observer_return_optical_depth_mean ?? 0).toFixed(3)} · T {(quality.observer_return_transmission_mean ?? 1).toFixed(3)}</strong></div>
      </div>
    </div>

    <div class="border-t border-border px-4 py-2.5 text-[11px] leading-5 text-muted-foreground">
      <p>{modelExplanation} 每个高、中、低云体使用各自云底和云顶；同一视线按距离由近到远合成，先遇到的低云会遮住其后的中高云。</p>
      <p>每个可见云底都单独追踪朝太阳方向的入射射线。射线穿过其他云体或地球曲面时降低照明透过率；最终可烧量只包含既受光、又能被地面观测者看到的云底面积。</p>
      <p>绝对可烧云面积先决定 0-2.5 基础分。几何遮挡已包含在可烧量中，不再重复扣分；持续时间和 AOD 各修正一次。低 AOD 小幅加分，高 AOD 有符号扣分。</p>
      <p>摄影焦段、Top-K、代表云和云边界不进入主评分。</p>
    </div>
  </section>
{/if}

<style>
  .metric { padding: 14px 16px; min-width: 0; }
  .metric span { display: block; font-size: 11px; color: var(--muted-foreground); }
  .metric strong { display: block; margin-top: 5px; font-size: 15px; font-weight: 500; color: var(--card-foreground); font-variant-numeric: tabular-nums; }
  .metric small { display: block; margin-top: 3px; font-size: 10px; color: var(--muted-foreground); white-space: normal; }
  .cap-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 16px; font-size: 12px; }
  .cap-row span { color: var(--muted-foreground); }
  .cap-row strong { color: var(--card-foreground); font-family: var(--font-mono, monospace); font-weight: 500; font-variant-numeric: tabular-nums; }
</style>
