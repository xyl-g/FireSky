import { writable } from 'svelte/store';

/**
 * Regional compute job tracked client-side.
 * The authoritative lifecycle is read from the Go forecast job endpoint;
 * this store keeps the regional result summary for the queue view.
 */
export interface RegionalJob {
  id: string;
  job_id: string;
  date: string;
  label: string;
  model_source: string;
  phenomenon: string;
  regions: string[];
  status: 'running' | 'done' | 'failed' | 'cancelled';
  progress: number;
  stage: string;
  message: string;
  elapsed: number;
  result_summary: {
    point_count: number;
    success_count: number;
    error_count: number;
    avg_score: number;
    max_score: number;
  } | null;
  error: string | null;
  created_at: string;
}

function createRegionalJobStore() {
  const { subscribe, update, set } = writable<RegionalJob[]>([]);
  return {
    subscribe,
    add(job: RegionalJob) {
      update(jobs => [job, ...jobs]);
    },
    updateJob(id: string, patch: Partial<RegionalJob>) {
      update(jobs => jobs.map(j => j.id === id ? { ...j, ...patch } : j));
    },
    clear() {
      set([]);
    },
  };
}

export const regionalJobs = createRegionalJobStore();
