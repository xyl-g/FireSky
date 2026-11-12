/**
 * FireSky 桌面端唯一 IPC 封装（蓝图 docs/TAURI_DESKTOP_PLAN.md §3.3，M1 骨架版）。
 *
 * Rust 壳（src-tauri/src/main.rs + src/sidecar.rs）持有长命 Rust sidecar
 * `firesky-engine desktop --workspace PATH`，本模块经 Tauri
 * command 转发协议帧：
 *   rpcRequest(method, params) → 协议 result 帧 / error 帧（三码透传）
 *   rpcCancel(requestId)       → 协议级 cancel 帧 {"method":"cancel",
 *                                "params":{"request_id"}}，返回 {requested}
 *   rpcStatus()                → 壳侧进程状态（非协议帧）
 * 协议 progress 帧（type:"progress"，percent/stage/source 及可选
 * message/cache_hit/probe）由 Rust 壳转为 Tauri 事件
 * "engine-progress" 转发，用 onEngineProgress 订阅。
 *
 * 协议要点（src/firesky/desktop_rpc.py:1-20、apps/macos/.../EngineClient.swift）：
 * 私有 JSON-lines 子进程 IPC，无端口/HTTP/令牌；单行上限 1 MiB；
 * 错误码三值 invalid_request / operation_failed / cancelled；
 * 取消为协作式（progress 回调点才生效）；会话令牌是 sidecar 进程内存态
 * （1800s 过期、单次消费、≤32 个），sidecar 必须长命。
 */

import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { BrowserTransport } from "./browser-transport";

export const desktopCapabilities = () => isTauri();
let browser: BrowserTransport | undefined;
const browserClient = () => browser ??= new BrowserTransport();
const browserHandles = new Map<string, RpcRequestHandle<unknown>>();

function newRequestId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** 协议错误帧三码 + 壳侧补充码（sidecar 未启动 / 已退出）。 */
export type RpcErrorCode =
  | "invalid_request"
  | "operation_failed"
  | "cancelled"
  | "engine_unavailable"
  | "engine_exited";

export class RpcError extends Error {
  readonly code: RpcErrorCode;

  constructor(code: RpcErrorCode, message: string) {
    super(message);
    this.name = "RpcError";
    this.code = code;
  }
}

/** 服务端 safe_event 清洗后的进度帧载荷（desktop_rpc.py:444-451）。 */
export interface ProgressEvent {
  percent?: number;
  stage?: string;
  source?: string;
  message?: string;
  cache_hit?: boolean;
  /** 仅 network_probe 链携带（desktop_rpc.py:446-451）。 */
  probe?: Record<string, unknown>;
}

/** hello 握手结果（desktop_rpc.py:147-154）。 */
export interface HelloResult {
  protocol: number;
  engine: string;
  capabilities: string[];
  cams_credentials: Record<string, unknown>;
  workspace: string;
  regions: Array<Record<string, unknown>>;
}

/** 壳侧 sidecar 进程状态（rpc_status 命令，非协议帧）。 */
export interface EngineStatus {
  running: boolean;
  workspace: string | null;
  python: string | null;
  engineBinary: string | null;
  spawnError: string | null;
  /** Changes for each successfully spawned child; sessions/tokens do not survive restart. */
  generation: number;
}

/** Available synchronously, even before the first progress event. */
export interface RpcRequestHandle<T> {
  readonly requestId: string;
  readonly result: Promise<T>;
  /** Cooperative cancellation; ordered after the request registration acknowledgement. */
  cancel(): Promise<{ requested: boolean }>;
}

interface RequestTicket {
  requestId: string;
  generation: number;
}

function normalizeError(err: unknown): RpcError {
  if (err instanceof RpcError) return err;
  // Tauri command 的 Result::Err 会按序列化值 reject：RpcError → {code, message}。
  if (typeof err === "object" && err !== null && "code" in err && "message" in err) {
    const raw = err as { code: unknown; message: unknown };
    return new RpcError(
      (typeof raw.code === "string" ? raw.code : "operation_failed") as RpcErrorCode,
      String(raw.message),
    );
  }
  if (typeof err === "string") return new RpcError("operation_failed", err);
  return new RpcError("operation_failed", String(err));
}

/**
 * Start immediately and expose a stable ID. rpc_start resolves only after Rust has
 * registered the reply AND flushed the request to this generation's stdin. Both
 * wait and cancel chain from that acknowledgement, never from a progress event.
 * The helper always consumes its Rust ticket; callers should observe result errors
 * even when cancellation was requested.
 */
export function rpcStart<T = unknown>(
  method: string,
  params: Record<string, unknown> = {},
): RpcRequestHandle<T> {
  const requestId = newRequestId();
  if (!isTauri()) {
    const handle = browserClient().start<T>(requestId, method, params);
    browserHandles.set(requestId, handle);
    void handle.result.finally(() => browserHandles.delete(requestId)).catch(() => undefined);
    const result = handle.result.catch((error: unknown) => { throw normalizeError(error); });
    void result.catch(() => undefined);
    return {
      requestId,
      result,
      cancel: async () => {
        try { return await handle.cancel(); }
        catch (error) { throw normalizeError(error); }
      },
    };
  }
  const started = invoke<RequestTicket>("rpc_start", { requestId, method, params })
    .catch((err: unknown) => { throw normalizeError(err); });
  const result = started
    .then((ticket) => invoke<T>("rpc_wait", {
      requestId: ticket.requestId,
      generation: ticket.generation,
    }))
    .catch((err: unknown) => { throw normalizeError(err); });
  // Mark the original promise handled while preserving its rejection for callers
  // that await it later (for example after immediately pressing Cancel).
  void result.catch(() => undefined);
  return {
    requestId,
    result,
    cancel: async () => {
      await started;
      return rpcCancel(requestId);
    },
  };
}

/**
 * 调用 desktop_rpc 方法。params 必须是对象（服务端校验
 * desktop_rpc.py:481-482，非对象 → invalid_request）。
 * 取消用 rpcCancel(requestId) 发协议级 cancel 帧——注意取消是协作式的，
 * 不会中断进行中的外部下载（apps/README.md:141-143）。
 */
export async function rpcRequest<T = unknown>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  if (!isTauri()) return rpcStart<T>(method, params).result;
  try {
    return await invoke<T>("rpc_request", { method, params });
  } catch (err) {
    throw normalizeError(err);
  }
}

/** 发送协议级取消帧，返回 {requested}；目标未知/已完成时 requested=false。 */
export async function rpcCancel(requestId: string): Promise<{ requested: boolean }> {
  if (!isTauri()) {
    const handle = browserHandles.get(requestId);
    if (!handle) return { requested: false };
    try { return await handle.cancel(); }
    catch (error) { throw normalizeError(error); }
  }
  try {
    return await invoke<{ requested: boolean }>("rpc_cancel", { requestId });
  } catch (err) {
    throw normalizeError(err);
  }
}

/** 读取壳侧 sidecar 进程状态（是否在跑 / 解析出的 workspace / 启动失败原因）。 */
export async function rpcStatus(): Promise<EngineStatus> {
  if (!isTauri()) {
    try { return await browserClient().status(); }
    catch (error) { throw normalizeError(error); }
  }
  try {
    const raw = await invoke<Record<string, unknown>>("rpc_status");
    return {
      running: Boolean(raw["running"]),
      workspace: (raw["workspace"] as string | null) ?? null,
      python: (raw["python"] as string | null) ?? null,
      engineBinary: (raw["engineBinary"] as string | null) ?? null,
      spawnError: (raw["spawnError"] as string | null) ?? null,
      generation: typeof raw["generation"] === "number" ? raw["generation"] : 0,
    };
  } catch (err) {
    throw normalizeError(err);
  }
}

/** Explicit, serialized reconnect. Invalidates all previous generation session tokens. */
export async function rpcRestart(): Promise<EngineStatus> {
  if (!isTauri()) {
    try { return await browserClient().restart(); }
    catch (error) { throw normalizeError(error); }
  }
  try {
    return await invoke<EngineStatus>("rpc_restart");
  } catch (err) {
    throw normalizeError(err);
  }
}

/** Subscribe before reading rpcStatus so a fast engine exit cannot leave a stale badge. */
export function onEngineStatus(handler: (status: EngineStatus) => void): Promise<UnlistenFn> {
  if (!isTauri()) return Promise.resolve(browserClient().onStatus(handler));
  return listen<EngineStatus>("engine-status", (e) => handler(e.payload));
}

export type DesktopAction = "point" | "regional" | "nowcast" | "data" | "settings" | "refresh" | "cancel" | "search" | "quit";
export type CacheSource = "gfs" | "ecmwf" | "cams" | "satellite" | "map" | "point" | "regional";
export function onDesktopAction(handler: (action: DesktopAction) => void): Promise<UnlistenFn> {
  if (!isTauri()) return Promise.resolve(() => {});
  return listen<{ action: DesktopAction }>("desktop-action", (event) => handler(event.payload.action));
}
export async function openReport(path: string): Promise<void> {
  if (!isTauri()) {
    if (!/^\/api\/v1\/firesky\/reports\/[a-f0-9]{64}(?:\.html)?$/.test(path)) {
      throw new RpcError("invalid_request", "只能打开当前 Rust 服务发布的报告。");
    }
    window.open(path, "_blank", "noopener,noreferrer");
    return;
  }
  try { await invoke("desktop_open_report", { path }); } catch (error) { throw normalizeError(error); }
}
export async function openCache(source: CacheSource): Promise<void> {
  if (!isTauri()) throw new RpcError("invalid_request", "浏览器不能打开服务端缓存目录。");
  try { await invoke("desktop_open_cache", { source }); } catch (error) { throw normalizeError(error); }
}
export async function quitDesktop(): Promise<void> {
  if (!isTauri()) throw new RpcError("invalid_request", "浏览器不能退出共享 Rust 服务。");
  try { await invoke("desktop_quit"); } catch (error) { throw normalizeError(error); }
}

/** 握手：connect() 语义的第一步。调用方须校验 protocol === 1（两端客户端同款）。 */
export function hello(): Promise<HelloResult> {
  return rpcRequest<HelloResult>("hello");
}

/**
 * 订阅引擎进度事件（Rust 侧 emit "engine-progress"，载荷 {requestId, event}）。
 * 进度语义按链区分（蓝图 §3.4）：point/regional 直读服务端 pair 映射值
 * 0–100 连续推进（GFS 相 0–50、EC 相 50–100），不得二次映射；
 * nowcast 为引擎原始 stage/percent（前端复刻 Qt 两段式映射）；
 * network_probe 的 percent 无意义，走 probe 字段。
 */
export function onEngineProgress(
  handler: (requestId: string, event: ProgressEvent) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return Promise.resolve(browserClient().onProgress(handler));
  return listen<{ requestId: string; event: ProgressEvent }>(
    "engine-progress",
    (e) => handler(e.payload.requestId, e.payload.event),
  );
}
