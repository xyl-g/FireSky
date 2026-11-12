type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
const number = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const text = (value: unknown): string => typeof value === "string" ? value : "";
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const percentage = (value: unknown): number | null => {
  const result = number(value);
  return result !== null && result >= 0 && result <= 100 ? result : null;
};

export interface ProfilePoint { distance: number; height: number }
export interface ProfileCell {
  id: string;
  distance: number;
  height: number;
  pressure: number | null;
  start: number;
  end: number;
  bottom: number;
  top: number;
  probability: number | null;
  temperature: number | null;
  humidity: number | null;
  status: number | null;
  family: "high" | "mid" | "low";
}
interface ProfileLevel {
  height: number;
  pressure: number | null;
  probability: number | null;
  temperature: number | null;
  humidity: number | null;
  status: number | null;
}
interface ProfileSample { distance: number; levels: ProfileLevel[] }
export interface ProfileData {
  satellite: boolean;
  title: string;
  source: string;
  timestamp: string;
  bearing: number | null;
  coverage: number | null;
  solarElevation: number | null;
  offsetMinutes: number | null;
  terrainSource: string;
  sourceNote: string;
  pressureLines: Array<{ pressure: number; temperature: number | null; segments: ProfilePoint[][] }>;
  targets: ProfileTarget[];
  cells: ProfileCell[];
  terrain: ProfilePoint[][];
  illumination: ProfilePoint[][];
  maxDistance: number;
  maxHeight: number;
  empty: string;
}
export interface ProfileTarget {
  distance: number | null;
  family: string;
  phase: string;
  status: string;
  reason: string;
  base: number | null;
  top: number | null;
  baseAGL: number | null;
  topAGL: number | null;
}

function family(pressure: number | null): ProfileCell["family"] {
  return pressure !== null && pressure <= 400 ? "high" : pressure !== null && pressure <= 700 ? "mid" : "low";
}

function cellsFor(samples: ProfileSample[]): ProfileCell[] {
  const sorted = [...samples].sort((a, b) => a.distance - b.distance);
  return sorted.flatMap((sample, column) => {
    const previous = sorted[column - 1]?.distance ?? sample.distance - 10;
    const next = sorted[column + 1]?.distance ?? sample.distance + 10;
    const start = Math.max(0, (previous + sample.distance) / 2), end = (sample.distance + next) / 2;
    const levels = [...sample.levels].sort((a, b) => a.height - b.height);
    return levels.map((level, row) => ({
      ...level,
      id: `${column}-${row}`,
      distance: sample.distance,
      start,
      end,
      bottom: Math.max(0, ((levels[row - 1]?.height ?? 0) + level.height) / 2),
      top: (level.height + (levels[row + 1]?.height ?? level.height + 1000)) / 2,
      family: family(level.pressure),
    })).filter((cell) => cell.end > cell.start && cell.top > cell.bottom);
  });
}

/** Missing heights break a line; they must not be joined across an unknown column. */
export function profileLineSegments(distances: unknown, heights: unknown): ProfilePoint[][] {
  const rows = list(heights), segments: ProfilePoint[][] = [[]];
  list(distances).forEach((rawDistance, index) => {
    const distance = number(rawDistance), height = number(rows[index]);
    if (distance === null || distance < 0 || height === null) {
      if (segments.at(-1)!.length) segments.push([]);
      return;
    }
    segments.at(-1)!.push({ distance, height });
  });
  return segments.filter((segment) => segment.length);
}

/** Use only the solar bearing, not all columns of the satellite azimuth fan. */
function satelliteSamples(profile: RecordValue, bearing: number | null): ProfileSample[] {
  const samples = list(profile.samples).map(record);
  const bearings = new Set(samples.map((sample) => number(sample.bearing_deg)).filter((value) => value !== null));
  if (bearing === null && bearings.size > 1) return [];
  const alongPath = new Map<number, { difference: number; sample: RecordValue }>();
  for (const sample of samples) {
    const distance = number(sample.distance_km), sampleBearing = number(sample.bearing_deg);
    if (distance === null || distance < 0) continue;
    const difference = bearing === null ? 0 : sampleBearing === null ? Infinity : Math.abs(((sampleBearing - bearing) % 360 + 540) % 360 - 180);
    if (difference >= .5) continue;
    if (!alongPath.has(distance) || difference < alongPath.get(distance)!.difference) alongPath.set(distance, { difference, sample });
  }
  return [...alongPath].map(([distance, { sample }]) => ({
    distance,
    levels: list(sample.levels).map(record).flatMap((level) => {
      const height = number(level.height_m);
      if (height === null || height < 0) return [];
      return [{
        height,
        pressure: number(level.pressure_hpa),
        probability: sample.status === "retrieved" && level.status !== "unknown"
          ? percentage(level.hsd_posterior_percent ?? level.cloud_probability_percent) : null,
        temperature: number(level.temperature_c),
        humidity: percentage(level.rh_percent),
        status: null,
      }];
    }),
  }));
}

function modelSamples(section: RecordValue): ProfileSample[] {
  const fields = record(section.fields), pressures = list(section.pressure_levels_hpa);
  const field = (name: string, column: number, row: number) => list(list(fields[name])[column])[row];
  return list(section.distances_km).flatMap((rawDistance, column) => {
    const distance = number(rawDistance);
    if (distance === null || distance < 0) return [];
    return [{
      distance,
      levels: pressures.flatMap((rawPressure, row) => {
        const height = number(field("geopotential_height_asl_m", column, row));
        if (height === null || height < 0) return [];
        const fraction = percentage(field("cloud_fraction_percent", column, row));
        const amount = number(field("cloud_amount", column, row));
        return [{
          height,
          pressure: number(rawPressure),
          probability: fraction ?? (amount !== null && amount >= 0 && amount <= 1 ? amount * 100 : null),
          temperature: number(field("temperature_c", column, row)),
          humidity: percentage(field("rh_percent", column, row)),
          status: number(field("cloud_layer_status", column, row)),
        }];
      }),
    }];
  });
}

export function profileData(report: RecordValue | null, satellite = false): ProfileData {
  const section = record(report?.cross_section);
  const profile = record(record(report?.nowcast).satellite_vertical_profile);
  const bearing = number(section.solar_azimuth_deg);
  const available = !satellite || profile.status !== "unavailable";
  const samples = (!report || !available ? [] : satellite ? satelliteSamples(profile, bearing) : modelSamples(section))
    .sort((a, b) => a.distance - b.distance);
  const cells = cellsFor(samples);
  const terrain = profileLineSegments(section.distances_km, section.terrain_elevation_asl_m);
  const illumination = profileLineSegments(section.distances_km, section.required_illumination_base_asl_m);
  const satelliteBands = list(profile.bands);
  const source = satellite ? satelliteBands.includes("CLOUD_MASK") ? "HCAI 云产品 · GFS 高度先验" : "卫星云层证据 · GFS 高度先验"
    : section.model_source === "ec" ? "ECMWF IFS · 压力层云量" : "GFS · 压力层云量";
  const sourceNote = satellite
    ? satelliteBands.includes("CLOUD_MASK")
      ? "HCAI 官方云掩膜、云相、云顶高度和 COT 产品；GFS 温湿度与高度为模式先验。缺测不代表晴空。"
      : "HSD B07/B13/B15 云概率代理，不发布 COT、云相或官方云高真值；GFS 温湿度与高度为模式先验。缺测不代表晴空。"
    : "云层橙色状态只表示受光与几何/地形可见候选，未评估云体遮挡；低云压力族仅在近场距离门限内排除。";
  const pressureLines = [...new Set(cells.map((cell) => cell.pressure).filter((value): value is number => value !== null))]
    .sort((a, b) => b - a).map((pressure) => {
      const levels = samples.map((sample) => sample.levels.find((level) => level.pressure === pressure));
      const temperatures = levels.flatMap((level) => level?.temperature === null || level?.temperature === undefined ? [] : [level.temperature]);
      return {
        pressure,
        temperature: temperatures.length ? temperatures.reduce((sum, value) => sum + value, 0) / temperatures.length : null,
        segments: profileLineSegments(samples.map((sample) => sample.distance), levels.map((level) => level?.height)),
      };
    });
  const targets = list(section.theoretical_targets).map(record).map((target) => ({
    distance: number(target.distance_km),
    family: text(target.pressure_family_label),
    phase: ({ ice: "冰相", water: "水相", mixed: "混合相", unknown: "未知" } as Record<string, string>)[text(record(target.cloud_phase).label)] ?? "未知",
    status: text(target.status),
    reason: text(target.status_reason),
    base: number(target.cloud_base_asl_m),
    top: number(target.cloud_top_asl_m),
    baseAGL: number(target.cloud_base_agl_m),
    topAGL: number(target.cloud_top_agl_m),
  }));
  const extent = [...terrain, ...illumination].flat().reduce((result, point) => ({
    distance: Math.max(result.distance, point.distance), height: Math.max(result.height, point.height),
  }), { distance: 1, height: 1000 });
  for (const cell of cells) { extent.distance = Math.max(extent.distance, cell.end); extent.height = Math.max(extent.height, cell.top); }
  return {
    satellite,
    title: satellite ? "太阳方向卫星剖面" : "太阳方向大气剖面",
    source,
    timestamp: text(satellite ? profile.valid_time_utc : section.valid_time_local),
    bearing,
    coverage: satellite ? percentage(number(profile.coverage) === null ? null : Number(profile.coverage) * 100) : null,
    solarElevation: number(section.solar_elevation_deg),
    offsetMinutes: number(section.offset_minutes),
    terrainSource: text(section.terrain_source),
    sourceNote,
    pressureLines,
    targets,
    cells,
    terrain,
    illumination,
    maxDistance: extent.distance,
    maxHeight: extent.height,
    empty: !report ? satellite ? "等待实况分析" : "等待预测" : satellite ? "暂无可用卫星剖面采样" : "暂无可用模式剖面采样",
  };
}

export function profileCellLabel(cell: ProfileCell, satellite: boolean): string {
  const format = (value: number | null, digits = 0) => value === null ? "--" : value.toFixed(digits);
  return `${cell.distance.toFixed(1)} km · 高度 ${(cell.height / 1000).toFixed(2)} km · ${format(cell.pressure)} hPa · ${satellite ? "云层后验概率" : "模式云量"} ${cell.probability === null ? "缺测" : `${format(cell.probability)}%`} · 温度 ${format(cell.temperature, 1)}°C · 相对湿度 ${format(cell.humidity)}%`;
}
