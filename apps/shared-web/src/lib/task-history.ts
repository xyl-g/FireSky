import type { ForecastKind } from "./workbench-types";

type Row = Record<string, unknown>;
export type TaskStatus = "running" | "done" | "failed" | "cancelled" | "interrupted";
export interface RetryRequest {
  kind: ForecastKind;
  payload: Row;
}
export interface HistoryTask {
  task_id: string;
  method: string;
  status: TaskStatus;
  created_at_utc: string;
  updated_at_utc: string;
  cancel_requested: boolean;
  event_seq: number;
  retry_request: RetryRequest | null;
  progress: { stage?: string; source?: string; percent?: number };
}
export interface HistoryEvent {
  seq: number;
  at_utc: string;
  event: string;
  data: Row;
}
export interface HistoryState {
  tasks: HistoryTask[];
  total: number;
  issues: number;
  loaded: boolean;
  loading: boolean;
  error: string;
  selectedId: string | null;
  events: HistoryEvent[];
  eventsLoading: boolean;
  eventsError: string;
  truncated: boolean;
  forgetting: string | null;
}
interface Transport {
  request<T>(method: string, params: Row): Promise<T>;
}
const row = (value: unknown): Row =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const text = (value: unknown, max = 256): string => typeof value === "string" ? value.slice(0, max) : "";
const id = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const date = (value: unknown) => {
  const result = text(value, 64);
  return Number.isFinite(Date.parse(result)) ? result : "";
};
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const statuses = new Set(["running", "done", "failed", "cancelled", "interrupted"]);
const initial = (): HistoryState => ({
  tasks: [], total: 0, issues: 0, loaded: false, loading: false, error: "",
  selectedId: null, events: [], eventsLoading: false, eventsError: "", truncated: false, forgetting: null,
});

function task(value: unknown): HistoryTask {
  const item = row(value);
  if (!id(item.task_id) || !statuses.has(String(item.status)) || !date(item.created_at_utc) || !date(item.updated_at_utc)
    || !Number.isSafeInteger(item.event_seq) || Number(item.event_seq) < 1) {
    throw new Error("任务记录格式无效，请刷新后重试。");
  }
  const retry = row(item.retry_request);
  const kind = retry.kind;
  const payload = row(retry.payload);
  const progress = row(item.progress);
  const percent = progress.percent;
  return {
    task_id: item.task_id, method: text(item.method, 64), status: item.status as TaskStatus,
    created_at_utc: date(item.created_at_utc), updated_at_utc: date(item.updated_at_utc),
    cancel_requested: item.cancel_requested === true,
    event_seq: Number(item.event_seq),
    retry_request: ["point", "regional", "nowcast"].includes(String(kind)) && retry.payload === payload
      ? { kind: kind as ForecastKind, payload: structuredClone(payload) } : null,
    progress: {
      stage: text(progress.stage, 128), source: text(progress.source, 128),
      ...(typeof percent === "number" && Number.isFinite(percent) && percent >= 0 && percent <= 100 ? { percent } : {}),
    },
  };
}

/** A read-only journal client. Restoring parameters is always a separate user action. */
export class TaskHistoryController {
  state = initial();
  private revision = 0;
  private listRevision = 0;
  private eventRevision = 0;
  private loadedEventsSeq = 0;
  private refreshing = false;
  private disposed = false;
  constructor(private readonly transport: Transport, private readonly notify: (state: HistoryState) => void) {}

  private update(patch: Partial<HistoryState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.notify(this.state);
  }

  reset() {
    ++this.revision;
    ++this.listRevision;
    ++this.eventRevision;
    this.loadedEventsSeq = 0;
    this.refreshing = false; // 作废旧代请求后不得阻塞下一代读取。
    this.update(initial());
  }

  /** Background polls keep `loading` untouched so the spinner only tracks user-visible reads. */
  async refresh(options: { background?: boolean } = {}) {
    const background = options.background === true;
    if (this.disposed || this.state.loading || this.state.forgetting || this.refreshing) return;
    const revision = this.revision;
    const listing = ++this.listRevision;
    const current = () => !this.disposed && revision === this.revision && listing === this.listRevision;
    this.refreshing = true;
    this.update(background ? { error: "" } : { loading: true, error: "" });
    try {
      const response = row(await this.transport.request("task_history", { limit: 100 }));
      if (!current()) return;
      if (response.schema !== "firesky-task-history-v1" || response.automatic_restart !== false
        || !Array.isArray(response.tasks) || response.tasks.length > 100
        || !Number.isSafeInteger(response.total) || Number(response.total) < response.tasks.length
        || !Array.isArray(response.issues)) throw new Error("任务历史协议不匹配，请重新连接引擎。");
      const tasks = response.tasks.map(task);
      if (new Set(tasks.map((item) => item.task_id)).size !== tasks.length) throw new Error("任务历史包含重复记录。");
      this.update({ tasks, total: Number(response.total), issues: response.issues.length, loaded: true, error: "" });
      if (this.state.selectedId) {
        const selected = tasks.find((item) => item.task_id === this.state.selectedId);
        if (!selected) this.closeEvents();
        else if (!this.state.eventsLoading && selected.event_seq > this.loadedEventsSeq) void this.select(selected.task_id);
      }
    } catch (error) {
      if (current()) this.update({ error: errorText(error) });
    } finally {
      this.refreshing = false;
      if (current() && !background) this.update({ loading: false });
    }
  }

  async select(taskId: string) {
    if (this.disposed || !this.state.tasks.some((item) => item.task_id === taskId)) return;
    const revision = this.revision;
    const eventRevision = ++this.eventRevision;
    const current = () => !this.disposed && revision === this.revision && eventRevision === this.eventRevision;
    const same = this.state.selectedId === taskId;
    this.update({ selectedId: taskId, events: same ? this.state.events : [], eventsLoading: true, eventsError: "", truncated: same && this.state.truncated });
    try {
      const response = row(await this.transport.request("task_events", { task_id: taskId, after_seq: 0 }));
      if (!current()) return;
      if (response.task_id !== taskId || !Array.isArray(response.events) || response.events.length > 256
        || !Number.isSafeInteger(response.next_seq) || Number(response.next_seq) < 0
        || typeof response.truncated !== "boolean") throw new Error("任务事件协议不匹配。");
      let previous = 0;
      const events = response.events.map((value): HistoryEvent => {
        const event = row(value);
        if (!Number.isSafeInteger(event.seq) || Number(event.seq) <= previous
          || Number(event.seq) > Number(response.next_seq) || !date(event.at_utc)) throw new Error("任务事件顺序无效。");
        previous = Number(event.seq);
        return { seq: previous, at_utc: date(event.at_utc), event: text(event.event, 64), data: row(event.data) };
      });
      this.loadedEventsSeq = Number(response.next_seq);
      this.update({ events, truncated: response.truncated });
    } catch (error) {
      if (current()) this.update({ eventsError: errorText(error) });
    } finally {
      if (current()) this.update({ eventsLoading: false });
    }
  }

  closeEvents() {
    ++this.eventRevision;
    this.loadedEventsSeq = 0;
    this.update({ selectedId: null, events: [], eventsLoading: false, eventsError: "", truncated: false });
  }

  retry(taskId: string): RetryRequest | null {
    const item = this.state.tasks.find((item) => item.task_id === taskId);
    return !this.disposed && item && item.status !== "running" && item.retry_request
      ? structuredClone(item.retry_request) : null;
  }

  async forget(taskId: string) {
    const item = this.state.tasks.find((item) => item.task_id === taskId);
    if (this.disposed || this.state.forgetting || !item || item.status === "running") return false;
    const revision = this.revision;
    const current = () => !this.disposed && revision === this.revision;
    ++this.listRevision;
    this.refreshing = false; // 清单已被作废，放行下一次刷新。
    this.update({ forgetting: taskId, loading: false, error: "" });
    try {
      const response = row(await this.transport.request("task_forget", { task_id: taskId }));
      if (!current()) return false;
      if (typeof response.forgotten !== "boolean") throw new Error("删除任务记录的响应无效。");
      if (this.state.selectedId === taskId) this.closeEvents();
      this.update({ tasks: this.state.tasks.filter((entry) => entry.task_id !== taskId), total: Math.max(0, this.state.total - 1) });
      return true;
    } catch (error) {
      if (current()) this.update({ error: errorText(error) });
      return false;
    } finally {
      if (current()) this.update({ forgetting: null });
    }
  }

  dispose() {
    this.disposed = true;
    ++this.revision;
    ++this.listRevision;
    ++this.eventRevision;
  }
}

export const taskStatus = (value: TaskStatus, cancelling = false) =>
  value === "running" ? cancelling ? "取消中" : "运行中"
  : value === "done" ? "已完成" : value === "failed" ? "失败" : value === "cancelled" ? "已取消" : "已中断";
export const taskMethod = (method: string) =>
  ({ check: "数据检查", prepare: "数据准备", forecast: "预测计算", publications: "发布核验", network_probe: "下载探测" } as Record<string, string>)[method] ?? "任务";
