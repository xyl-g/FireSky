import type { ProgressEvent, RpcRequestHandle } from "./ipc";

export interface DataRow {
  source: string;
  ready: boolean;
  request_cycle?: string | null;
  latest_local_cycle?: string | null;
  latest_available_cycle?: string | null;
  verified?: boolean;
  is_latest?: boolean;
  missing_count?: number;
  missing_hours?: number[];
  missing_valid_times?: string[];
  [key: string]: unknown;
}

export interface DataReport {
  ready: boolean;
  datasets: DataRow[];
  notes?: string[];
  [key: string]: unknown;
}

export interface DataSessionState {
  phase: "idle" | "checking" | "checked" | "preparing" | "cancelling" | "error";
  session: string | null;
  report: DataReport | null;
  error: string;
  publicationPending: boolean;
  publicationError: string;
  progress: ProgressEvent | null;
}

interface SessionResult {
  session: string;
  report: DataReport;
}

export interface DataSessionTransport {
  start<T>(method: string, params: Record<string, unknown>): RpcRequestHandle<T>;
  request<T>(method: string, params: Record<string, unknown>): Promise<T>;
}

interface Operation {
  handle: RpcRequestHandle<SessionResult>;
  revision: number;
  cancelled: boolean;
  done: Promise<void>;
}

const initialState = (): DataSessionState => ({
  phase: "idle", session: null, report: null, error: "",
  publicationPending: false, publicationError: "", progress: null,
});
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Owns one check snapshot. No request from this controller runs a forecast. */
export class DataSessionController {
  state: DataSessionState = initialState();
  private revision = 0;
  private publicationRevision = 0;
  private active: Operation | null = null;
  private publicationCall: Promise<void> | null = null;
  private disposed = false;

  constructor(
    private readonly transport: DataSessionTransport,
    private readonly notify: (state: DataSessionState) => void,
  ) {}

  private update(patch: Partial<DataSessionState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.notify(this.state);
  }

  private dismiss(session: string | null) {
    if (!session) return;
    try {
      void this.transport.request("dismiss", { session }).catch(() => undefined);
    } catch {
      // Teardown must still finish if the transport has already been disposed.
    }
  }

  private invalidatePublications() {
    ++this.publicationRevision;
    this.publicationCall = null;
  }

  async check(payload: Record<string, unknown>): Promise<void> {
    if (this.disposed || this.active) return;
    const snapshot = structuredClone(payload);
    this.dismiss(this.state.session);
    const revision = ++this.revision;
    this.invalidatePublications();
    this.update({ ...initialState(), phase: "checking" });
    await this.run("check", { kind: "regional", payload: snapshot }, revision);
  }

  async publications(): Promise<void> {
    if (this.disposed || this.active || !this.state.session) return;
    if (this.publicationCall) return this.publicationCall;
    const session = this.state.session;
    const revision = this.revision;
    const publicationRevision = ++this.publicationRevision;
    const current = () => !this.disposed && revision === this.revision
      && publicationRevision === this.publicationRevision && session === this.state.session;
    this.update({ publicationPending: true, publicationError: "" });
    const call = (async () => {
      try {
        const result = await this.transport.request<SessionResult>("publications", { session });
        if (current()) this.update({ report: result.report });
      } catch (error) {
        if (current()) this.update({ publicationError: errorText(error) });
      } finally {
        if (current()) this.update({ publicationPending: false });
      }
    })();
    this.publicationCall = call;
    await call;
    if (this.publicationCall === call) this.publicationCall = null;
  }

  async prepare(): Promise<void> {
    if (this.disposed || this.active || !this.state.session) return;
    this.invalidatePublications();
    this.update({
      phase: "preparing", error: "", progress: null, publicationPending: false,
      report: this.state.report ? { ...this.state.report, ready: false } : null,
    });
    await this.run("prepare", { session: this.state.session }, this.revision);
  }

  private async run(method: string, params: Record<string, unknown>, revision: number) {
    let handle: RpcRequestHandle<SessionResult>;
    try {
      handle = this.transport.start<SessionResult>(method, params);
    } catch (error) {
      this.update({ phase: "error", error: errorText(error) });
      return;
    }
    const operation: Operation = { handle, revision, cancelled: false, done: Promise.resolve() };
    this.active = operation;
    operation.done = (async () => {
      try {
        const result = await handle.result;
        if (this.disposed || revision !== this.revision || operation.cancelled) {
          if (method === "check") this.dismiss(result.session);
          return;
        }
        this.update({ phase: "checked", session: result.session, report: result.report });
      } catch (error) {
        if (!this.disposed && revision === this.revision && !operation.cancelled) {
          this.update({ phase: "error", error: errorText(error) });
        }
      } finally {
        if (this.active === operation) {
          this.active = null;
          if (!this.disposed && (operation.cancelled || revision !== this.revision)) {
            this.update({ phase: "idle", progress: null });
          }
        }
      }
    })();
    await operation.done;
  }

  async cancel(): Promise<void> {
    if (this.disposed || !this.active) return;
    const operation = this.active;
    if (!operation.cancelled) {
      operation.cancelled = true;
      ++this.revision;
      this.invalidatePublications();
      this.dismiss(this.state.session);
      this.update({
        phase: "cancelling", session: null, report: null,
        publicationPending: false, error: "",
      });
      try {
        await operation.handle.cancel();
      } catch (error) {
        if (!this.disposed && this.active === operation) this.update({ error: errorText(error) });
      }
    }
    await operation.done;
  }

  invalidate(): void {
    if (this.disposed) return;
    if (this.active) {
      void this.cancel();
      return;
    }
    ++this.revision;
    this.invalidatePublications();
    this.dismiss(this.state.session);
    this.update(initialState());
  }

  progress(requestId: string, event: ProgressEvent): void {
    if (this.disposed || !this.active || this.active.cancelled
      || this.active.revision !== this.revision || this.active.handle.requestId !== requestId) return;
    this.update({ progress: event });
  }

  dispose(): void {
    if (this.disposed) return;
    this.dismiss(this.state.session);
    const operation = this.active;
    this.disposed = true;
    ++this.revision;
    this.invalidatePublications();
    if (operation && !operation.cancelled) {
      operation.cancelled = true;
      try {
        void operation.handle.cancel().catch(() => undefined);
      } catch {
        // An unavailable transport must not prevent view teardown.
      }
    }
  }
}
