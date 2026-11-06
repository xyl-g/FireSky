export const CHINA_PRODUCTION_POINT_COUNT = 36_685;

export function requireProductionChinaSummary(
  summary: Record<string, unknown>,
  precision: Record<string, unknown>,
): void {
  const regions = summary.regions;
  if (!Array.isArray(regions) || regions.length !== 1 || regions[0] !== 'china') {
    return;
  }
  if (
    summary.point_mode !== 'generated-regional-grid'
    || summary.model_source !== 'gfs'
    || precision.point_spacing_deg !== 0.25
    || precision.model_grid_spacing_deg !== 0.25
    || summary.point_count !== CHINA_PRODUCTION_POINT_COUNT
    || summary.success_count !== CHINA_PRODUCTION_POINT_COUNT
    || !Array.isArray(summary.points)
    || summary.points.length !== CHINA_PRODUCTION_POINT_COUNT
    || summary.error_count !== 0
    || precision.full_point_coverage !== true
  ) {
    throw new Error(
      'China production summary requires a complete 0.25-degree 36,685-point grid',
    );
  }
}
