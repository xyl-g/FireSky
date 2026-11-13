/**
 * FireSky 共享 Web 唯一 IPC 封装（browser-only）。
 *
 * 由 apps/desktop-tauri/src/lib/ipc.ts 迁移（退役方案 Phase 1）：桌面宿主
 * （SwiftUI/WinUI）走各自原生 FFI/IPC，共享 Web 一律经同源 HTTP 任务通道
 * （engine/src/http/desktop.rs 的 /desktop/status|jobs|cancel|release）。
 * 移除了 Tauri invoke/event 分支与桌面专属能力（打开宿主缓存目录、退出服务）。
 *
 * 协议要点（engine/src/rpc.rs，原 desktop_rpc.py 历史措辞已替换）：
 * 私有 JSON-lines 子进程 IPC，无端口/HTTP/令牌；单行上限 1 MiB；
 * 错误码三值 invalid_request / operation_failed / cancelled；
 * 取消为协作式；HTTP 层另有 engine_unavailable / engine_exited 连接语义。
 */

import { BrowserTransport } from "./browser-transport";

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

/** 协议错误帧三码 + HTTP 连接补充码（服务不可达 / 已变化）。 */
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

/** 服务端 safe_event 清洗后的进度帧载荷。 */
export interface ProgressEvent {
  percent?: number;
  stage?: string;
  source?: string;
  message?: string;
  cache_hit?: boolean;
  /** 仅 network_probe 链携带。 */
  probe?: Record<string, unknown>;
}

/** hello 握手结果。 */
export interface HelloResult {
  protocol: number;
  engine: string;
  capabilities: string[];
  cams_credentials: Record<string, unknown>;
  workspace: string;
  regions: Array<Record<string, unknown>>;
}

/** 服务端共享引擎状态（/desktop/status 握手）。 */
export interface EngineStatus {
  running: boolean;
  workspace: string | null;
  python: null;
  engineBinary: null;
  spawnError: string | null;
  /** 每次成功重连后变化；会话不跨重连存活。 */
  generation: number;
}

/** Available synchronously, even before the first progress event. */
export interface RpcRequestHandle<T> {
  readonly requestId: string;
  readonly result: Promise<T>;
  /** Cooperative cancellation; ordered after the request registration acknowledgement. */
  cancel(): Promise<{ requested: boolean }>;
}

function normalizeError(err: unknown): RpcError {
  if (err instanceof RpcError) return err;
  // HTTP 任务的错误按 {code, message} 反序列化。
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
 * Start immediately and expose a stable ID. The browser handle resolves only
 * after the HTTP acknowledgement registers the job; cancel chains from that
 * acknowledgement. Callers should observe result errors even when cancellation
 * was requested.
 */
export function rpcStart<T = unknown>(
  method: string,
  params: Record<string, unknown> = {},
): RpcRequestHandle<T> {
  const requestId = newRequestId();
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

/** 发起一个 RPC 方法调用。params 必须是对象（服务端校验非对象 → invalid_request）。 */
export async function rpcRequest<T = unknown>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  return rpcStart<T>(method, params).result;
}

/** 发送协议级取消帧，返回 {requested}；目标未知/已完成时 requested=false。 */
export async function rpcCancel(requestId: string): Promise<{ requested: boolean }> {
  const handle = browserHandles.get(requestId);
  if (!handle) return { requested: false };
  try { return await handle.cancel(); }
  catch (error) { throw normalizeError(error); }
}

/** 读取服务端共享引擎状态（是否在跑 / 工作区 / 上次失败原因）。 */
export async function rpcStatus(): Promise<EngineStatus> {
  try { return await browserClient().status(); }
  catch (error) { throw normalizeError(error); }
}

/** Explicit, serialized reconnect. Invalidates all previous generation session tokens. */
export async function rpcRestart(): Promise<EngineStatus> {
  try { return await browserClient().restart(); }
  catch (error) { throw normalizeError(error); }
}

/** Subscribe before reading rpcStatus so a fast engine exit cannot leave a stale badge. */
export function onEngineStatus(handler: (status: EngineStatus) => void): Promise<() => void> {
  return Promise.resolve(browserClient().onStatus(handler));
}

export type DesktopAction = "point" | "regional" | "nowcast" | "data" | "settings" | "refresh" | "cancel" | "search" | "quit";
export type CacheSource = "gfs" | "ecmwf" | "cams" | "satellite" | "map" | "point" | "regional";

/** 浏览器没有宿主菜单事件源；保留 no-op 订阅兼容既有调用方。 */
export function onDesktopAction(_handler: (action: DesktopAction) => void): Promise<() => void> {
  return Promise.resolve(() => {});
}

/**
 * 打开服务端发布的报告。浏览器只能打开当前 Rust 服务发布的
 * /api/v1/firesky/reports/{sha256}.html；不接受任意路径。
 */
export async function openReport(path: string): Promise<void> {
  if (!/^\/api\/v1\/firesky\/reports\/[a-f0-9]{64}(?:\.html)?$/.test(path)) {
    throw new RpcError("invalid_request", "只能打开当前 Rust 服务发布的报告。");
  }
  window.open(path, "_blank", "noopener,noreferrer");
}

/** 浏览器不能打开服务端本地缓存目录（桌面宿主能力，由原生 UI 提供）。 */
export async function openCache(_source: CacheSource): Promise<never> {
  throw new RpcError("invalid_request", "浏览器不能打开服务端缓存目录。");
}

/** 浏览器不能退出共享 Rust 服务（桌面宿主能力，由原生 UI 提供）。 */
export async function quitDesktop(): Promise<never> {
  throw new RpcError("invalid_request", "浏览器不能退出共享 Rust 服务。");
}

/** 握手：connect() 语义的第一步。调用方须校验 protocol === 1（两端客户端同款）。 */
export function hello(): Promise<HelloResult> {
  return rpcRequest<HelloResult>("hello");
}

/**
 * 订阅引擎进度事件。进度语义按链区分：point/regional 直读服务端 pair 映射值
 * 0–100 连续推进（GFS 相 0–50、EC 相 50–100），不得二次映射；
 * nowcast 为引擎原始 stage/percent（前端复刻两段式映射）；
 * network_probe 的 percent 无意义，走 probe 字段。
 */
export function onEngineProgress(
  handler: (requestId: string, event: ProgressEvent) => void,
): Promise<() => void> {
  return Promise.resolve(browserClient().onProgress(handler));
}
