/**
 * Cloud field rendering algorithms
 * Ported from map.html SoftCloudLayer + helper functions
 */

import type { MapCloudCell } from '@/api/types';

// ─── Color utilities ───

export function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

export function mixColor(
  left: number[],
  right: number[],
  amount: number,
): number[] {
  const t = clamp01(amount);
  return left.map((v, i) => Math.round(v + (right[i] - v) * t));
}

export type CloudField = 'total' | 'high_cloud' | 'mid_cloud' | 'low_cloud';

const COLOR_RAMPS: Record<CloudField, [number[], number[]]> = {
  total: [[72, 96, 112], [239, 244, 244]],
  high_cloud: [[70, 102, 142], [222, 235, 246]],
  mid_cloud: [[58, 111, 105], [220, 237, 231]],
  low_cloud: [[79, 88, 96], [226, 229, 231]],
};

export function fieldColor(field: CloudField, value: number): number[] {
  const ramp = COLOR_RAMPS[field] || COLOR_RAMPS.total;
  return mixColor(ramp[0], ramp[1], Math.pow(clamp01(value), 0.72));
}

export function burnColor(value: number): number[] {
  const t = clamp01(value);
  const stops: Array<[number, number[]]> = [
    [0, [253, 230, 220]],
    [0.2, [251, 176, 122]],
    [0.45, [245, 124, 102]],
    [0.7, [237, 91, 130]],
    [1, [201, 60, 145]],
  ];
  for (let index = 1; index < stops.length; index += 1) {
    const [rightAt, rightColor] = stops[index];
    if (t <= rightAt) {
      const [leftAt, leftColor] = stops[index - 1];
      return mixColor(leftColor, rightColor, (t - leftAt) / (rightAt - leftAt));
    }
  }
  return stops[stops.length - 1][1];
}

// ─── Grid sampling ───

export function sampleGrid(
  values: Float32Array,
  width: number,
  height: number,
  x: number,
  y: number,
): number {
  const px = Math.max(0, Math.min(width - 1, x));
  const py = Math.max(0, Math.min(height - 1, y));
  const x0 = Math.floor(px);
  const y0 = Math.floor(py);
  const x1 = Math.min(width - 1, x0 + 1);
  const y1 = Math.min(height - 1, y0 + 1);
  const tx = px - x0;
  const ty = py - y0;

  const a = values[y0 * width + x0] * (1 - tx) + values[y0 * width + x1] * tx;
  const b = values[y1 * width + x0] * (1 - tx) + values[y1 * width + x1] * tx;

  return a * (1 - ty) + b * ty;
}

// ─── Cloud value extraction ───

export function cloudValue(cell: MapCloudCell, field: CloudField): number {
  if (field === 'total') {
    return Math.max(cell.high_cloud, cell.mid_cloud, cell.low_cloud);
  }
  return Number((cell as unknown as Record<string, unknown>)[field] || 0);
}

// ─── Bilinear rasterized field ───

export interface FieldRaster {
  canvas: HTMLCanvasElement;
  north: number;
  south: number;
  west: number;
  east: number;
}

export function buildFieldRaster(
  cells: MapCloudCell[],
  field: CloudField,
  options: { showCloud: boolean; showBurn: boolean },
): FieldRaster | null {
  const lats = [
    ...new Set(cells.map((c) => Number(c.lat).toFixed(5))),
  ]
    .map(Number)
    .sort((a, b) => a - b);
  const lons = [
    ...new Set(cells.map((c) => Number(c.lon).toFixed(5))),
  ]
    .map(Number)
    .sort((a, b) => a - b);

  if (lats.length < 2 || lons.length < 2) return null;

  const latIndex = new Map(
    lats.map((v, i) => [v.toFixed(5), i]),
  );
  const lonIndex = new Map(
    lons.map((v, i) => [v.toFixed(5), i]),
  );

  const clouds = new Float32Array(lats.length * lons.length);
  const burns = new Float32Array(lats.length * lons.length);

  for (const cell of cells) {
    const y = latIndex.get(Number(cell.lat).toFixed(5))!;
    const x = lonIndex.get(Number(cell.lon).toFixed(5))!;
    const index = y * lons.length + x;
    clouds[index] = cloudValue(cell, field);
    burns[index] = clamp01(cell.burn_score);
  }

  const scale = 10;
  const width = lons.length * scale;
  const height = lats.length * scale;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d')!;
  const image = context.createImageData(width, height);
  const pixels = image.data;

  for (let py = 0; py < height; py++) {
    const gy = lats.length - (py + 0.5) / scale;
    for (let px = 0; px < width; px++) {
      const gx = (px + 0.5) / scale - 0.5;
      const cloud = sampleGrid(clouds, lons.length, lats.length, gx, gy);
      const burn = sampleGrid(burns, lons.length, lats.length, gx, gy);

      if (
        (!options.showCloud || cloud < 0.06) &&
        (!options.showBurn || burn < 0.1)
      )
        continue;

      let color = fieldColor(field, cloud);
      let alpha =
        options.showCloud && cloud >= 0.06
          ? 0.06 + 0.66 * clamp01((cloud - 0.06) / 0.84)
          : 0;

      if (options.showBurn && burn >= 0.1) {
        const strength = clamp01((burn - 0.1) / 0.34);
        color = mixColor(color, burnColor(burn), 0.52 + 0.4 * strength);
        alpha = Math.max(alpha, 0.22 + 0.58 * strength);
      }

      const offset = (py * width + px) * 4;
      pixels[offset] = color[0];
      pixels[offset + 1] = color[1];
      pixels[offset + 2] = color[2];
      pixels[offset + 3] = Math.round(255 * alpha);
    }
  }

  context.putImageData(image, 0, 0);

  const latStep = gridStepFromValues(lats);
  const lonStep = gridStepFromValues(lons);

  return {
    canvas,
    north: lats[lats.length - 1] + latStep / 2,
    south: lats[0] - latStep / 2,
    west: lons[0] - lonStep / 2,
    east: lons[lons.length - 1] + lonStep / 2,
  };
}

function gridStepFromValues(values: number[]): number {
  const steps: number[] = [];
  for (let i = 1; i < values.length; i++) {
    const step = values[i] - values[i - 1];
    if (step > 0.00001) steps.push(step);
  }
  steps.sort((a, b) => a - b);
  return steps.length ? steps[Math.floor(steps.length / 2)] : 0.25;
}

// ─── Marching squares contours ───

export interface ContourLevel {
  value: number;
  color: string;
  weight: number;
  opacity: number;
  dashArray?: string;
}

export function contourSegments(
  cells: MapCloudCell[],
  key: string,
  threshold: number,
): [number, number][][] {
  const lats = [
    ...new Set(cells.map((c) => Number(c.lat).toFixed(5))),
  ]
    .map(Number)
    .sort((a, b) => a - b);
  const lons = [
    ...new Set(cells.map((c) => Number(c.lon).toFixed(5))),
  ]
    .map(Number)
    .sort((a, b) => a - b);

  const values = new Map(
    cells.map((c) => [
      `${Number(c.lat).toFixed(5)},${Number(c.lon).toFixed(5)}`,
      Number((c as unknown as Record<string, unknown>)[key] || 0),
    ]),
  );

  const valueAt = (lat: number, lon: number) =>
    values.get(`${lat.toFixed(5)},${lon.toFixed(5)}`) || 0;

  const crossing = (
    a: { lat: number; lon: number; value: number },
    b: { lat: number; lon: number; value: number },
  ): [number, number] | null => {
    if ((a.value >= threshold) === (b.value >= threshold)) return null;
    const ratio = (threshold - a.value) / (b.value - a.value);
    return [
      a.lat + (b.lat - a.lat) * ratio,
      a.lon + (b.lon - a.lon) * ratio,
    ];
  };

  const segments: [number, number][][] = [];

  for (let i = 0; i < lats.length - 1; i++) {
    for (let j = 0; j < lons.length - 1; j++) {
      const corners = [
        { lat: lats[i], lon: lons[j], value: valueAt(lats[i], lons[j]) },
        {
          lat: lats[i + 1],
          lon: lons[j],
          value: valueAt(lats[i + 1], lons[j]),
        },
        {
          lat: lats[i + 1],
          lon: lons[j + 1],
          value: valueAt(lats[i + 1], lons[j + 1]),
        },
        {
          lat: lats[i],
          lon: lons[j + 1],
          value: valueAt(lats[i], lons[j + 1]),
        },
      ];

      const points: [number, number][] = [];
      for (let edge = 0; edge < 4; edge++) {
        const point = crossing(corners[edge], corners[(edge + 1) % 4]);
        if (point) points.push(point);
      }

      if (points.length === 2) {
        segments.push([points[0], points[1]]);
      } else if (points.length === 4) {
        segments.push([points[0], points[1]], [points[2], points[3]]);
      }
    }
  }

  return segments;
}

// ─── Format helpers ───

export function cellPopupHtml(cell: MapCloudCell, label: string): string {
  const s = (v: number) => Math.round(Number(v || 0) * 100);
  const esc = (v: unknown) =>
    String(v ?? '').replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!,
    );
  return `<strong>${esc(label)}</strong><br>` +
    `云证据 ${s(cell.cloud_evidence)} · 格点可烧信号 ${s(cell.burn_score)}<br>` +
    `高/中/低 ${s(cell.high_cloud)}/${s(cell.mid_cloud)}/${s(cell.low_cloud)}<br>` +
    `受光 ${s(cell.illumination_score)} · 光路 ${s(cell.path_clearance_score)} · 挡光 ${s(cell.blocking_risk)}`;
}
