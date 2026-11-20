/** 海拔来源三档（v1.1.1 引擎契约：metadata 可选参数 elevation_source，缺省 auto）。 */
export type ElevationSource = "auto" | "http" | "srtm";
export const ELEVATION_SOURCE_CHOICES: readonly ElevationSource[] = ["auto", "http", "srtm"];
export const ELEVATION_SOURCE_STORAGE_KEY = "firesky.elevationSource";

/** 非法/缺省值一律回落 auto，与引擎缺省语义一致。 */
export function parseElevationSource(value: unknown): ElevationSource {
  return ELEVATION_SOURCE_CHOICES.includes(value as ElevationSource) ? value as ElevationSource : "auto";
}

export function loadElevationSource(storage: Pick<Storage, "getItem">): ElevationSource {
  try { return parseElevationSource(storage.getItem(ELEVATION_SOURCE_STORAGE_KEY)); }
  catch { return "auto"; }
}

export function saveElevationSource(storage: Pick<Storage, "setItem">, value: ElevationSource): boolean {
  try { storage.setItem(ELEVATION_SOURCE_STORAGE_KEY, parseElevationSource(value)); return true; }
  catch { return false; }
}

/** metadata / search（search 内部同样触发 point metadata）请求统一透传海拔来源。 */
export function withElevationSource(params: Record<string, unknown>, source: ElevationSource): Record<string, unknown> {
  return { ...params, elevation_source: parseElevationSource(source) };
}

export function elevationSourceDescription(source: ElevationSource): string {
  if (source === "http") return "HTTP：仅通过 open-meteo 在线查询海拔与时区；查询失败直接报错，不回退本地地形。";
  if (source === "srtm") return "SRTM：下载地形瓦片在本地计算海拔（时区用本地数据）；瓦片缓存后离线可用，缺瓦片时需要允许下载。";
  return "自动：优先 open-meteo 在线查询，失败时自动回退本地时区 + SRTM 地形瓦片。";
}

/** 按引擎响应 source 字段展示海拔来源；未知取值不展示。 */
export function metadataSourceTitle(source: unknown): string {
  if (source === "open-meteo-point-metadata") return "海拔来源：open-meteo 在线";
  if (source === "local-timezone+srtm") return "海拔来源：本地时区 + SRTM";
  return "";
}

export interface CamsConfiguration {
  configured: boolean;
  state: "missing" | "configured" | "wrong_endpoint" | "tls_disabled" | "unreadable";
  path: string;
  environmentKey: boolean;
  canSave: boolean;
}

/** Only the public status contract is retained; unexpected credential fields are discarded. */
export function camsConfiguration(value: unknown): CamsConfiguration {
  if (!value || typeof value !== "object") throw new Error("Invalid CAMS status");
  const status = value as Record<string, unknown>;
  const states = ["missing", "configured", "wrong_endpoint", "tls_disabled", "unreadable"];
  if (typeof status.state !== "string" || !states.includes(status.state)
    || typeof status.configured !== "boolean" || typeof status.can_save !== "boolean"
    || typeof status.environment_key !== "boolean" || typeof status.path !== "string") {
    throw new Error("Invalid CAMS status");
  }
  return {
    configured: status.configured && status.state === "configured",
    state: status.state as CamsConfiguration["state"],
    path: status.path,
    environmentKey: status.environment_key,
    canSave: status.can_save,
  };
}

export function camsDescription(status: CamsConfiguration | null): string {
  if (!status) return "尚未检查本地 CAMS 配置。";
  if (status.configured) {
    return status.environmentKey ? "已配置，使用环境变量中的 CAMS 凭据。" : "已配置本地 CAMS 凭据。";
  }
  if (status.state === "wrong_endpoint") return "CAMS API 地址不正确，需要 Copernicus ADS 地址。";
  if (status.state === "tls_disabled") return "CAMS 配置关闭了 TLS 校验，需修正后再使用。";
  if (status.state === "unreadable") return "无法读取 CAMS 配置，请检查文件格式与访问权限。";
  return "尚未配置 CAMS Personal Access Token；已有本地数据仍可使用。";
}
