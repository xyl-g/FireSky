import { get, post } from './client';
import { normalizeRegionalForecastPayload } from './forecast';
import type {
  ForecastJob,
  ForecastJobListResponse,
  ForecastJobStatus,
  RegionalForecastPayload,
} from './types';

/** Terminal values reported by the firesky job endpoints. */
export const TERMINAL_FORECAST_JOB_STATUSES: ReadonlySet<ForecastJobStatus> = new Set([
  'done',
  'failed',
  'cancelled',
  'interrupted',
]);

export function isTerminalForecastJobStatus(status: ForecastJobStatus): boolean {
  return TERMINAL_FORECAST_JOB_STATUSES.has(status);
}

/** List durable forecast jobs, paginated and filterable. */
export function listForecastJobs(params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<ForecastJobListResponse> {
  const p: Record<string, string> = {};
  if (params?.status) p.status = params.status;
  if (params?.limit) p.limit = String(params.limit);
  if (params?.offset) p.offset = String(params.offset);
  return get<ForecastJobListResponse>('/api/v1/firesky/forecasts/jobs', p);
}

/** Get a single forecast job by ID. */
export function getForecastJob(id: string): Promise<ForecastJob> {
  return get<ForecastJob>(`/api/v1/firesky/forecasts/jobs/${id}`);
}

/** Acknowledges cancellation; poll the job until its worker reaches a terminal state. */
export function cancelForecastJob(id: string): Promise<{
  job_id: string;
  status: 'running';
  cancel_requested: true;
}> {
  return post(`/api/v1/firesky/forecasts/${id}/cancel`, {});
}

export function getForecastResult<T>(jobId: string): Promise<T> {
  return get<T>(`/api/v1/firesky/forecasts/results/${jobId}`).then((payload) => {
    if (payload && typeof payload === 'object' && 'points' in payload) {
      return normalizeRegionalForecastPayload(payload as unknown as RegionalForecastPayload) as unknown as T;
    }
    return payload;
  });
}
