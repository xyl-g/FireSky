import Foundation

struct DownloadProbeRow: Identifiable {
    let source: String
    let title: String
    var state = "waiting"
    var detail = "尚未探测"
    var host = ""
    var bytes = 0
    var elapsedMS = 0
    var id: String { source }
    var passed: Bool { state == "passed" }
    var active: Bool { state == "testing" }
    var label: String {
        switch state {
        case "waiting": "未探测"
        case "testing": "探测中"
        case "passed": "下载验证通过"
        case "not_configured": "未配置"
        case "timeout": "超时，未验证"
        case "cancelled": "已取消"
        default: "未通过"
        }
    }
    var symbol: String {
        passed ? "checkmark.circle.fill" : state == "waiting" ? "circle.dotted" :
            state == "cancelled" ? "stop.circle" : "exclamationmark.triangle"
    }
    var measurement: String {
        let size = ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
        return "\(size) · \(String(format: "%.1f", Double(elapsedMS) / 1000)) 秒"
    }
    static var initial: [Self] { initial(aerosolSource: "cams") }
    static func initial(aerosolSource: String) -> [Self] {
        [Self(source: "gfs", title: "GFS · NOAA NOMADS"),
         Self(source: "ec", title: "EC · ECMWF Open Data"),
         Self(source: "cams", title: aerosolSource == "geos-fp"
            ? "GEOS-FP · NASA OPeNDAP"
            : "CAMS · Copernicus ADS")]
    }
    init(source: String, title: String) { self.source = source; self.title = title }
    init?(_ json: [String: Any]) {
        guard let source = json["source"] as? String,
              Self.initial.contains(where: { $0.source == source }) else { return nil }
        self.source = source
        title = json["title"] as? String ?? source.uppercased()
        state = json["state"] as? String ?? "error"
        detail = json["detail"] as? String ?? ""
        host = json["host"] as? String ?? ""
        bytes = (json["bytes_received"] as? NSNumber)?.intValue ?? 0
        elapsedMS = (json["elapsed_ms"] as? NSNumber)?.intValue ?? 0
    }
}
