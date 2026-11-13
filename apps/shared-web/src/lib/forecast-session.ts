import type { ProgressEvent, RpcRequestHandle } from "./ipc";
import type { DataReport, DataSessionTransport } from "./data-session";

export type ForecastKind = "point" | "regional" | "nowcast";
export interface ForecastRequestSnapshot {
  readonly kind: ForecastKind;
  readonly payload: Record<string, unknown>;
  readonly label: string;
}
export interface PublishedForecast {
  readonly kind: ForecastKind;
  readonly result: Record<string, unknown>;
  readonly requestSnapshot: ForecastRequestSnapshot;
  readonly requestId: string;
  readonly publishedAtUtc: string;
}
export type PublishedForecasts = Partial<Record<ForecastKind, PublishedForecast>>;
export interface ForecastSessionState {
  phase: "idle" | "checking" | "checked" | "preparing" | "forecasting" | "cancelling" | "error";
  session: string | null;
  report: DataReport | null;
  error: string;
  publicationPending: boolean;
  publicationError: string;
  progress: ProgressEvent | null;
  requestSnapshot: ForecastRequestSnapshot | null;
  published: PublishedForecasts;
}

interface SessionResult {
  session: string;
  report: DataReport;
}
interface Operation {
  handle: RpcRequestHandle<unknown>;
  revision: number;
  session: string | null;
  cancelled: boolean;
  done: Promise<void>;
}
const initialState = (published: PublishedForecasts = {}): ForecastSessionState => ({
  phase: "idle", session: null, report: null, error: "",
  publicationPending: false, publicationError: "", progress: null, requestSnapshot: null, published,
});
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
export function requiresCycleAgeConfirmation(report: DataReport | null | undefined): boolean {
  return report?.ready === true && report.datasets.some((row) =>
    row.ready && (row.verified !== true || row.is_latest !== true));
}
const freezeSnapshot = (value: unknown): void => {
  if (typeof value !== "object" || value === null || Object.isFrozen(value)) return;
  Object.freeze(value);
  for (const child of Object.values(value)) freezeSnapshot(child);
};

/** Owns one immutable prediction request and its single-use preparation token. */
export class ForecastSessionController {
  state: ForecastSessionState = initialState();
  private revision = 0;
  private publicationRevision = 0;
  private publicationCall: Promise<void> | null = null;
  private active: Operation | null = null;
  private disposed = false;

  constructor(
    private readonly transport: DataSessionTransport,
    private readonly notify: (state: ForecastSessionState) => void,
  ) {}

  private update(patch: Partial<ForecastSessionState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.notify(this.state);
  }

  private dismiss(session: string | null) {
    if (!session) return;
    try {
      void this.transport.request("dismiss", { session }).catch(() => undefined);
    } catch {
      // A disconnected transport must not prevent local invalidation.
    }
  }

  private invalidatePublications() {
    ++this.publicationRevision;
    this.publicationCall = null;
  }

  async check(kind: ForecastKind, payload: Record<string, unknown>, label = ""): Promise<void> {
    if (this.disposed || this.active) return;
    const snapshot: ForecastRequestSnapshot = { kind, payload: structuredClone(payload), label };
    freezeSnapshot(snapshot);
    this.dismiss(this.state.session);
    const revision = ++this.revision;
    this.invalidatePublications();
    this.update({ ...initialState(this.state.published), phase: "checking", requestSnapshot: snapshot });
    await this.run("check", { kind, payload: structuredClone(snapshot.payload) }, revision);
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

  async forecast(cycleAgeConfirmed = false): Promise<void> {
    if (this.disposed || this.active) return;
    if (!this.state.session || !this.state.report?.ready || !this.state.requestSnapshot) {
      this.update({ error: "请先检查并准备所需数据，再确认预测。" });
      return;
    }
    if (requiresCycleAgeConfirmation(this.state.report) && !cycleAgeConfirmed) {
      this.update({ error: "当前数据周期尚未确认最新状态，请先确认后再继续。" });
      return;
    }
    const session = this.state.session;
    this.invalidatePublications();
    // Forecast consumes the token even if the server ultimately rejects it.
    this.update({
      phase: "forecasting", session: null, error: "", progress: null,
      publicationPending: false,
      report: { ...this.state.report, ready: false },
    });
    await this.run("forecast", {
      session, confirmed: true,
      ...(cycleAgeConfirmed ? { cycle_age_confirmed: true } : {}),
    }, this.revision);
  }

  private async run(method: "check" | "prepare" | "forecast", params: Record<string, unknown>, revision: number) {
    const requestSnapshot = this.state.requestSnapshot;
    let handle: RpcRequestHandle<unknown>;
    try {
      handle = this.transport.start<unknown>(method, params);
    } catch (error) {
      this.dismiss(typeof params.session === "string" ? params.session : this.state.session);
      this.update({ phase: "error", session: null, report: null, requestSnapshot: null, error: errorText(error) });
      return;
    }
    const operation: Operation = {
      handle, revision, session: typeof params.session === "string" ? params.session : null,
      cancelled: false, done: Promise.resolve(),
    };
    this.active = operation;
    operation.done = (async () => {
      try {
        const result = await handle.result;
        if (this.disposed || revision !== this.revision || operation.cancelled) {
          if (method === "check") this.dismiss((result as SessionResult).session);
          return;
        }
        if (method === "forecast") {
          if (!requestSnapshot) return;
          if (typeof result !== "object" || result === null || Array.isArray(result)) {
            throw new Error("预测结果无效，上一结果保持不变。");
          }
          const published: PublishedForecast = {
            kind: requestSnapshot.kind,
            result: result as Record<string, unknown>,
            requestSnapshot,
            requestId: handle.requestId,
            publishedAtUtc: new Date().toISOString(),
          };
          this.update({
            ...initialState({ ...this.state.published, [published.kind]: published }),
          });
        } else {
          const prepared = result as SessionResult;
          this.update({ phase: "checked", session: prepared.session, report: prepared.report });
        }
      } catch (error) {
        if (!this.disposed && revision === this.revision && !operation.cancelled) {
          this.dismiss(operation.session ?? this.state.session);
          this.update({ phase: "error", session: null, report: null, requestSnapshot: null, error: errorText(error) });
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
      this.dismiss(this.state.session ?? operation.session);
      this.update({
        phase: "cancelling", session: null, report: null, requestSnapshot: null,
        publicationPending: false, error: "",
      });
      try { await operation.handle.cancel(); }
      catch (error) {
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
    this.update(initialState(this.state.published));
  }

  progress(requestId: string, event: ProgressEvent): void {
    if (this.disposed || !this.active || this.active.cancelled
      || this.active.revision !== this.revision || this.active.handle.requestId !== requestId) return;
    this.update({ progress: event });
  }

  dispose(): void {
    if (this.disposed) return;
    const operation = this.active;
    this.dismiss(this.state.session ?? operation?.session ?? null);
    this.disposed = true;
    ++this.revision;
    this.invalidatePublications();
    if (operation && !operation.cancelled) {
      operation.cancelled = true;
      try { void operation.handle.cancel().catch(() => undefined); }
      catch {
        // Teardown must remain safe if the IPC transport has already gone away.
      }
    }
  }
}
