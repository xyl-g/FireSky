import { get } from './client';

export interface GoStatus {
  status: string;
  version: string;
  uptime_seconds: number;
  goroutines: number;
  num_cpu: number;
  mem_alloc_mb: number;
  mem_sys_mb: number;
  gc_runs: number;
}

export interface DatabaseStatus {
  status: string; // ok | unavailable | disabled
  driver: string;
  latency_ms?: number;
  max_open_connections?: number;
  open_connections?: number;
  in_use?: number;
  idle?: number;
  wait_count?: number;
  wait_duration_ms?: number;
  error?: string;
}

export interface RedisStatus {
  status: string; // ok | unavailable | disabled
  addr?: string;
  latency_ms?: number;
  version?: string;
  connected_clients?: string;
  used_memory_human?: string;
  used_memory_peak_human?: string;
  latest_cycles_cached?: boolean;
  error?: string;
}

export interface PythonStatus {
  status: string; // ok | unavailable
  latency_ms?: number;
  detail?: Record<string, unknown>;
  error?: string;
  http_status?: number;
}

export interface SystemStatus {
  status: string; // ok | degraded
  go: GoStatus;
  database: DatabaseStatus;
  redis: RedisStatus;
  python: PythonStatus;
  checked_at: string;
}

export function getSystemStatus(): Promise<SystemStatus> {
  return get<SystemStatus>('/api/v1/firesky/admin/system');
}
