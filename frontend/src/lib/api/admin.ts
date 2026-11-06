import { get, post } from './client';

// ── Regional forecast (national) jobs ──────────────────────────────────────

export interface RegionalForecastRequest {
  target_date: string;
  phenomenon: 'dawn' | 'dusk';
  region?: string;
  spacing_deg?: number;
  model_source?: 'gfs' | 'ec';
  compute_backend?: 'cpu' | 'gpu';
  workers?: number;
}

export interface RegionalJobRef {
  job_id: string;
  scope?: string;
}

export interface RegionalJobStatus {
  scope?: string;
  algorithm?: string;
  target_date?: string;
  phenomenon?: string;
  point_count?: number;
  success_count?: number;
  error_count?: number;
  points?: Array<Record<string, unknown>>;
  errors?: Array<Record<string, unknown>>;
  timing?: Record<string, number>;
  cache?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RegionOption {
  code: string;
  label: string;
  bounds: number[];
  timezone: string;
}

export function startRegionalForecast(body: RegionalForecastRequest): Promise<RegionalJobRef> {
  return post('/api/v1/firesky/forecasts/national', body) as Promise<RegionalJobRef>;
}

export function getRegionalForecastResult(job_id: string): Promise<RegionalJobStatus> {
  return get(`/api/v1/firesky/forecasts/results/${job_id}`) as Promise<RegionalJobStatus>;
}

export function cancelJob(job_id: string): Promise<{ job_id: string; status: string }> {
  return post(`/api/v1/firesky/forecasts/${job_id}/cancel`, {}) as Promise<{
    job_id: string;
    status: string;
  }>;
}

export interface RegionalContract {
  scope: string;
  algorithm: string;
  summary_version: number;
  cache_version: number;
  point_format: string;
}

export function getRegionalContract(): Promise<RegionalContract> {
  return get('/api/v1/firesky/forecasts/national/contract') as Promise<RegionalContract>;
}
