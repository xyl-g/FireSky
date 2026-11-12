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
  readiness?: DataReadiness;
  ready?: boolean;
  prepared?: boolean;
}

export interface DataReadiness {
  contract: 'rust-data-readiness-v1';
  ready: boolean;
  prepared: boolean;
  frozen_plan: boolean;
  network_enabled: boolean;
  datasets: Array<{
    source: string;
    ready: boolean;
    prepared: boolean;
    required_files: number;
    verified_files: number;
    missing_count: number;
    missing: unknown[];
    errors: unknown[];
  }>;
}

// Inventory is intentionally not readiness. Accept only a checked, nonempty
// frozen request, including every requested source, after native verification.
export function verifiedFetchReadiness(value: unknown, sources: string[]): DataReadiness | null {
  if (!value || typeof value !== 'object' || sources.length === 0) return null;
  const result = value as Record<string, unknown>;
  const readiness = result.readiness as DataReadiness | undefined;
  if (result.contract !== 'rust-data-fetch-v1' || result.status !== 'done'
      || result.ready !== true || result.prepared !== true
      || !Array.isArray(result.errors) || result.errors.length !== 0
      || readiness?.contract !== 'rust-data-readiness-v1'
      || readiness.ready !== true || readiness.prepared !== true
      || readiness.frozen_plan !== true || readiness.network_enabled !== false
      || !Array.isArray(readiness.datasets) || readiness.datasets.length === 0) return null;
  if (!readiness.datasets.every((row) => row && typeof row === 'object')) return null;
  if (!sources.every((source) => readiness.datasets.some((row) => row.source === source))) return null;
  if (!readiness.datasets.every((row) =>
    sources.includes(row.source) && row.ready === true && row.prepared === true
      && Number.isSafeInteger(row.required_files) && row.required_files > 0
      && row.verified_files === row.required_files && row.missing_count === 0
      && Array.isArray(row.missing) && row.missing.length === 0
      && Array.isArray(row.errors) && row.errors.length === 0
  )) return null;
  return readiness;
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
  readiness?: DataReadiness;
  ready?: boolean;
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
