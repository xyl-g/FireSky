import type { EngineStatus, ProgressEvent, RpcRequestHandle } from "./ipc";

const PREFIX = "/api/v1/firesky/desktop";
const METHODS = new Set([
  "hello", "search", "metadata", "inventory", "check", "prepare", "forecast",
  "publications", "dismiss", "network_probe", "cams_credentials", "profile_query",
  "retention_settings", "save_retention_settings", "task_history", "task_events", "task_forget",
]);
type FailureCode = "invalid_request" | "operation_failed" | "cancelled" | "engine_unavailable" | "engine_exited";
type Row = Record<string, unknown>;
const object = (value: unknown): value is Row => typeof value === "object" && value !== null && !Array.isArray(value);
const fail = (code: FailureCode, message: string) => Object.assign(new Error(message), { code });
const disconnected = () => fail("engine_exited", "服务连接已变化，请重新检查数据；已提交的服务端任务可能仍在执行。");

interface Connection {
  instance: string;
  generation: number;
}
interface Active {
  generation: number;
  reject(error: unknown): void;
}

/** The browser reconnects its own session; it never restarts the shared service. */
export class BrowserTransport {
  private generation = 0;
  private instance: string | null = null;
  private current: EngineStatus = {
    running: false, workspace: null, python: null, engineBinary: null, spawnError: null, generation: 0,
  };
  private statusRevision = 0;
  private pendingStatus: Promise<EngineStatus> | null = null;
  private active = new Map<string, Active>();
  private statusHandlers = new Set<(status: EngineStatus) => void>();
  private progressHandlers = new Set<(requestId: string, event: ProgressEvent) => void>();

  constructor(
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
    private readonly delay: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  ) {}

  private emitStatus() {
    for (const handler of this.statusHandlers) handler({ ...this.current });
  }

  private invalidate() {
    ++this.generation;
    for (const operation of this.active.values()) operation.reject(disconnected());
    this.active.clear();
  }

  private offline(message: string) {
    if (this.current.running) this.invalidate();
    this.instance = null;
    this.current = { ...this.current, running: false, generation: this.generation, spawnError: message };
    this.emitStatus();
  }

  private async json(path: string, body?: Row, notifyDisconnect = true): Promise<Row> {
    const generation = this.generation;
    const revision = this.statusRevision;
    const notifyOffline = (message: string) => {
      if (notifyDisconnect && generation === this.generation && revision === this.statusRevision) this.offline(message);
    };
    let response: Response;
    try {
      response = await this.fetcher(`${PREFIX}${path}`, {
        method: body === undefined ? "GET" : "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      const message = "无法连接 Rust 服务；已提交的任务可能仍在执行，请重新连接后检查。";
      notifyOffline(message);
      throw fail("engine_unavailable", message);
    }
    let value: unknown;
    try { value = await response.json(); }
    catch {
      throw fail("operation_failed", "Rust 服务返回了非 JSON 响应。");
    }
    if (!response.ok) {
      const detail = object(value) ? value.error : undefined;
      const code = object(detail) && typeof detail.code === "string" ? detail.code : response.status === 400 ? "invalid_request" : "operation_failed";
      const message = object(detail) && typeof detail.message === "string"
        ? detail.message : typeof detail === "string" ? detail : `Rust 服务请求失败 (${response.status})。`;
      if (code === "engine_exited") notifyOffline(message);
      throw fail(code as FailureCode, message);
    }
    if (!object(value)) throw fail("operation_failed", "Rust 服务响应必须是对象。");
    return value;
  }

  status(): Promise<EngineStatus> {
    if (this.pendingStatus) return this.pendingStatus;
    const revision = this.statusRevision;
    const pending = (async () => {
      const raw = await this.json("/status");
      if (revision !== this.statusRevision) throw disconnected();
      if (raw.running !== true || raw.protocol !== 1 || raw.engine !== "firesky-rust"
        || typeof raw.instance_id !== "string" || !raw.instance_id || typeof raw.workspace !== "string") {
        this.offline("Rust 服务握手不匹配。");
        throw fail("engine_unavailable", this.current.spawnError!);
      }
      if (this.instance !== null && this.instance !== raw.instance_id) this.invalidate();
      if (this.generation === 0) this.generation = 1;
      this.instance = raw.instance_id;
      this.current = {
        running: true, workspace: raw.workspace, python: null, engineBinary: null,
        spawnError: null, generation: this.generation,
      };
      this.emitStatus();
      return { ...this.current };
    })();
    this.pendingStatus = pending;
    void pending.finally(() => { if (this.pendingStatus === pending) this.pendingStatus = null; }).catch(() => undefined);
    return pending;
  }

  async restart(): Promise<EngineStatus> {
    ++this.statusRevision;
    this.pendingStatus = null;
    this.invalidate();
    this.instance = null;
    this.current = { ...this.current, running: false, generation: this.generation, spawnError: null };
    this.emitStatus();
    return this.status();
  }

  private assertConnection(connection: Connection) {
    if (!this.current.running || connection.generation !== this.generation || connection.instance !== this.instance) throw disconnected();
  }

  start<T>(requestId: string, method: string, params: Row): RpcRequestHandle<T> {
    if (!METHODS.has(method)) throw fail("invalid_request", "此操作不通过浏览器远程接口提供。");
    let connection: Connection;
    const started = (async () => {
      const status = await this.status();
      connection = { instance: this.instance!, generation: status.generation };
      this.assertConnection(connection);
      const ack = await this.json("/jobs", { request_id: requestId, instance_id: connection.instance, method, params });
      this.assertConnection(connection);
      if (ack.request_id !== requestId || ack.instance_id !== connection.instance || ack.status !== "running") {
        throw fail("operation_failed", "Rust 服务任务确认不匹配。");
      }
    })();
    const result = started.then(async () => {
      let rejectStale!: (error: unknown) => void;
      const stale = new Promise<never>((_, reject) => { rejectStale = reject; });
      const operation = { generation: connection.generation, reject: rejectStale };
      this.active.set(requestId, operation);
      let lastProgress = 0;
      const release = () => {
        void this.json(`/jobs/${encodeURIComponent(requestId)}/release`, { instance_id: connection.instance }, false)
          .catch(() => undefined);
      };
      const polling = (async () => {
        for (;;) {
          this.assertConnection(connection);
          const row = await this.json(`/jobs/${encodeURIComponent(requestId)}?instance_id=${encodeURIComponent(connection.instance)}`);
          this.assertConnection(connection);
          if (row.request_id !== requestId || row.instance_id !== connection.instance
            || !Number.isSafeInteger(row.progress_seq) || (row.progress_seq as number) < lastProgress) {
            throw fail("operation_failed", "Rust 服务任务身份或进度序号不匹配。");
          }
          const seq = row.progress_seq as number;
          if (seq > lastProgress && object(row.progress)) {
            for (const handler of this.progressHandlers) handler(requestId, row.progress as ProgressEvent);
          }
          lastProgress = seq;
          if (row.status === "done") {
            release();
            if (!("result" in row)) throw fail("operation_failed", "Rust 服务任务缺少结果。");
            return row.result as T;
          }
          if (row.status === "cancelled") { release(); throw fail("cancelled", "任务已取消。"); }
          if (row.status === "failed") {
            release();
            const error = object(row.error) ? row.error : {};
            throw fail(
              typeof error.code === "string" ? error.code as FailureCode : "operation_failed",
              typeof error.message === "string" ? error.message : "Rust 服务任务失败。",
            );
          }
          if (row.status !== "running") throw fail("operation_failed", "Rust 服务任务状态无效。");
          await this.delay(300);
        }
      })();
      try { return await Promise.race([polling, stale]); }
      finally { if (this.active.get(requestId) === operation) this.active.delete(requestId); }
    });
    void result.catch(() => undefined);
    return {
      requestId, result,
      cancel: async () => {
        await started;
        this.assertConnection(connection);
        const row = await this.json(`/jobs/${encodeURIComponent(requestId)}/cancel`, { instance_id: connection.instance });
        this.assertConnection(connection);
        if (typeof row.requested !== "boolean") throw fail("operation_failed", "Rust 服务取消确认无效。");
        return { requested: row.requested };
      },
    };
  }

  onStatus(handler: (status: EngineStatus) => void): () => void {
    this.statusHandlers.add(handler);
    return () => { this.statusHandlers.delete(handler); };
  }

  onProgress(handler: (requestId: string, event: ProgressEvent) => void): () => void {
    this.progressHandlers.add(handler);
    return () => { this.progressHandlers.delete(handler); };
  }
}
