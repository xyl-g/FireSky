import { get, post } from './client';

export interface CycleInfo {
  cycle: string;
  file_count: number;
  total_size_mb: number;
  forecast_hours: number[];
}

export interface DatasetStatus {
  source: 'gfs' | 'ecmwf' | 'cams' | string;
  status: 'current' | 'update_available' | 'missing' | 'unavailable';
  latest_available_cycle: string;
  latest_local_cycle: string;
  required_files: number;
  required_hours_count: number;
  region_count: number;
  regions: string[];
  local_files: number;
  missing_hours: number[];
  cycles: CycleInfo[];
  prepared?: boolean;
  prepared_message?: string;
  capability_status?: 'available' | 'partial' | 'unavailable';
  capability_message?: string;
  unavailable_sample_count?: number | null;
}

export interface DataPlanOptions {
  region: string;
  region_name: string;
  bbox: number[];
  timezone: string;
  days: number;
  phenomena: string[];
}

export interface DerivedCacheInfo {
  name: string;
  label: string;
  file_count: number;
  total_size_mb: number;
  latest_file: string;
}

export interface DataStatus {
  checked_at: string;
  overall_status: 'current' | 'update_available' | 'missing';
  total_cache_size_mb: number;
  datasets: DatasetStatus[];
  derived: DerivedCacheInfo[];
}

export function getDataStatus(options?: DataPlanOptions): Promise<DataStatus> {
  const params = options ? {
    region: options.region,
    region_name: options.region_name,
    bbox: options.bbox.join(','),
    timezone: options.timezone,
    days: String(options.days),
    phenomena: options.phenomena.join(','),
  } : undefined;
  return get('/api/v1/firesky/data/status', params) as Promise<DataStatus>;
}

// ── Async data-download planner ────────────────────────────────────────────

export interface DataFetchRequest {
  target_date?: string;
  phenomenon?: string;
  region?: string;
  sources?: Array<'gfs' | 'ecmwf' | 'cams'>;
  lead_hours?: number[];
}

export interface DataFetchJob {
  job_id: string;
  status: string;
  sources?: string[];
}

export interface DataFetchProgress {
  job_id: string;
  status: 'running' | 'done' | 'failed' | string;
  sources?: string[];
  completed?: number;
  total?: number;
  current?: string;
  results?: Array<Record<string, unknown>>;
  errors?: string[];
  elapsed_seconds?: number;
}

export function startDataFetch(body: DataFetchRequest): Promise<DataFetchJob> {
  return post('/api/v1/firesky/data/fetch', body) as Promise<DataFetchJob>;
}

export function getDataFetchStatus(job_id: string): Promise<DataFetchProgress> {
  return get('/api/v1/firesky/data/fetch/status', { job_id }) as Promise<DataFetchProgress>;
}

export interface DataRequirementDataset {
  source: string;
  required_cycles?: string[];
  required_lead_hours?: number[];
  [key: string]: unknown;
}

export interface DataRequirementPlan {
  datasets: DataRequirementDataset[];
  [key: string]: unknown;
}

export function getDataRequirements(body: {
  region?: string;
  bbox?: number[];
  days?: number;
  phenomena?: string[];
  sources?: string[];
}): Promise<DataRequirementPlan> {
  return post('/api/v1/firesky/data/requirements', body) as Promise<DataRequirementPlan>;
}

export interface PruneResult {
  message: string;
  scanned?: number;
  removed?: number;
  freed_mb?: number;
  [key: string]: unknown;
}

export function pruneDataCache(body: {
  cache_root?: string;
  max_age_days?: number;
} = {}): Promise<PruneResult> {
  return post('/api/v1/firesky/data/prune', body) as Promise<PruneResult>;
}
