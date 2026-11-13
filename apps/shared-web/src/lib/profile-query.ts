import type { PublishedForecast } from "./forecast-session";
import type { RpcRequestHandle } from "./ipc";
import { utcTime } from "./forecast-time";

type Row = Record<string, unknown>;
const object = (value: unknown): value is Row => typeof value === "object" && value !== null && !Array.isArray(value);
const hex = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export type ProfileModel = "gfs" | "ec" | "satellite";
export interface ProfileRequest {
  result_id: string;
  source_identity: { bytes: number; sha256: string };
  model_source: ProfileModel;
  lat: number;
  lon: number;
  valid_time_utc: string;
}
export interface ProfileQueryState {
  phase: "idle" | "loading" | "ready" | "missing" | "expired" | "unavailable" | "cancelled" | "error";
  key: string | null;
  crossSection: Row | null;
  profileKind: string | null;
  message: string;
}
export const idleProfile = (): ProfileQueryState => ({ phase: "idle", key: null, crossSection: null, profileKind: null, message: "" });

export function profileRequestAt(report: Row | null, model: ProfileModel, lat: number, lon: number, time: string | null): ProfileRequest | null {
  const source = report?.profile_source;
  const utc = utcTime(time);
  if (!utc || !Number.isFinite(lat) || Math.abs(lat) > 90 || !Number.isFinite(lon) || Math.abs(lon) > 180
    || !object(source) || source.contract !== "rust-desktop-profile-source-v1"
    || source.available !== true || source.query_method !== "profile_query" || source.model_source !== model
    || source.profile_kind !== (model === "satellite" ? "satellite-step-solar-path-diagnostic" : "model-pressure-cross-section")
    || !utcTime(source.cycle_utc)
    || !hex(source.result_id) || !object(source.source_identity) || !hex(source.source_identity.sha256)
    || !Number.isSafeInteger(source.source_identity.bytes) || (source.source_identity.bytes as number) < 0) return null;
  return {
    result_id: source.result_id,
    source_identity: { bytes: source.source_identity.bytes as number, sha256: source.source_identity.sha256 },
    model_source: model, lat, lon, valid_time_utc: utc,
  };
}

export function profileRequest(published: PublishedForecast | null, report: Row | null, model: ProfileModel, time: string | null): ProfileRequest | null {
  const coordinates = published?.requestSnapshot.payload;
  if (!published || typeof coordinates?.lat !== "number" || typeof coordinates.lon !== "number") return null;
  return profileRequestAt(report, model, coordinates.lat, coordinates.lon, time);
}

export const profileRequestKey = (request: ProfileRequest): string => JSON.stringify(request);
export function profileResponseMatches(value: Row, request: ProfileRequest): boolean {
  return value.contract === "rust-desktop-profile-query-v1" && value.result_id === request.result_id
    && object(value.source_identity) && value.source_identity.bytes === request.source_identity.bytes
    && value.source_identity.sha256 === request.source_identity.sha256 && value.model_source === request.model_source
    && value.lat === request.lat && value.lon === request.lon && utcTime(value.valid_time_utc) === request.valid_time_utc;
}

/** Query cancellation never mutates a published result or starts data preparation. */
export class ProfileQueryController {
  state = idleProfile();
  private revision = 0;
  private active: RpcRequestHandle<unknown> | null = null;
  private disposed = false;
  constructor(
    private readonly start: <T>(method: string, params: Record<string, unknown>) => RpcRequestHandle<T>,
    private readonly notify: (state: ProfileQueryState) => void,
  ) {}
  private update(state: ProfileQueryState) {
    if (!this.disposed) { this.state = state; this.notify(state); }
  }
  invalidate() {
    ++this.revision;
    const active = this.active;
    this.active = null;
    if (active) {
      try { void active.cancel().catch(() => undefined); } catch { /* Cancellation is best effort after disconnect. */ }
    }
    this.update(idleProfile());
  }
  async select(request: ProfileRequest, context: string, cycleUtc?: string): Promise<void> {
    const key = `${context}:${profileRequestKey(request)}`;
    if (this.disposed || key === this.state.key) return;
    this.invalidate();
    const revision = this.revision;
    this.update({ ...idleProfile(), phase: "loading", key, message: "正在读取所选时次剖面…" });
    try {
      const handle = this.start<unknown>("profile_query", { ...request, source_identity: { ...request.source_identity } });
      this.active = handle;
      const result = await handle.result;
      if (this.disposed || revision !== this.revision) return;
      if (!object(result) || !profileResponseMatches(result, request)) throw new Error("剖面响应与当前结果、模式或时次不匹配。");
      const kind = typeof result.profile_kind === "string" ? result.profile_kind : null;
      if (result.status === "ready") {
        if (cycleUtc && utcTime(result.cycle_utc) !== utcTime(cycleUtc)) throw new Error("剖面响应的模式周期与已发布结果不匹配。");
        const expected = request.model_source === "satellite" ? "satellite-step-solar-path-diagnostic" : "model-pressure-cross-section";
        if (!object(result.cross_section) || kind !== expected) throw new Error("剖面数据类型与当前模式不匹配。");
        this.update({ phase: "ready", key, crossSection: result.cross_section, profileKind: kind, message: "" });
      } else {
        const phase = ["missing", "expired", "unavailable", "cancelled"].includes(String(result.status))
          ? result.status as "missing" | "expired" | "unavailable" | "cancelled" : "error";
        const messages = {
          missing: "所选时次缺少可用剖面，请显式重新预测。",
          expired: "该结果的剖面资源已过期，请显式重新预测。",
          unavailable: "该结果未提供所选时次剖面。",
          cancelled: "剖面查询已取消。",
          error: "剖面查询失败。",
        };
        this.update({ phase, key, crossSection: null, profileKind: kind, message: messages[phase] + (typeof result.reason === "string" ? ` ${result.reason}` : "") });
      }
    } catch (error) {
      if (!this.disposed && revision === this.revision) this.update({
        phase: "error", key, crossSection: null, profileKind: null,
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      if (revision === this.revision) this.active = null;
    }
  }
  dispose() { this.invalidate(); this.disposed = true; }
}
