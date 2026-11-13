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
