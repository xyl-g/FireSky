import Foundation

/// 海拔来源三档设置（v1.1.1 任务书 §1 契约）：
/// auto=HTTP 优先、SRTM 兜底（默认）；http=仅 open-meteo，失败显式报错；
/// srtm=跳过 HTTP，本地时区+SRTM 瓦片（缓存优先，缺瓦片按下载开关决定）。
///
/// 持久化走 UserDefaults（同 appearance/ecmwfSource）；非法/缺省值一律归一为
/// auto。search/metadata 请求参数在此统一构造，保证所有调用点透传
/// elevation_source（引擎侧 desktop_dispatch 对非法值报 invalid elevation_source）。
enum ElevationSourceSetting {
    static let key = "elevationSource"

    static func normalize(_ value: String?) -> String {
        switch value {
        case "http": return "http"
        case "srtm": return "srtm"
        default: return "auto"
        }
    }

    static func load(_ defaults: UserDefaults = .standard) -> String {
        normalize(defaults.string(forKey: key))
    }

    /// 持久化并返回归一后的取值（UI 状态以此为准）。
    @discardableResult
    static func save(_ value: String, defaults: UserDefaults = .standard) -> String {
        let normalized = normalize(value)
        defaults.set(normalized, forKey: key)
        return normalized
    }

    static func searchParams(query: String, source: String) -> JSONObject {
        ["query": query, "elevation_source": normalize(source)]
    }

    static func metadataParams(lat: Double, lon: Double, source: String) -> JSONObject {
        ["lat": lat, "lon": lon, "elevation_source": normalize(source)]
    }
}