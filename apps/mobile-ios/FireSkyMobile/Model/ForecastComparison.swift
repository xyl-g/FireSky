import CoreFoundation
import Foundation

enum ReportDiagnosticValue {
    static func text(_ value: Any?, digits: Int = 2, suffix: String = "",
                     percent: Bool = false, label: (String) -> String = { $0 }) -> String {
        guard let value, !(value is NSNull) else { return "—" }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                return number.boolValue ? "是" : "否"
            }
            guard number.doubleValue.isFinite else { return "—" }
            return percent ? String(format: "%.0f%%", number.doubleValue * 100)
                : String(format: "%.*f%@", digits, number.doubleValue, suffix)
        }
        if let string = value as? String {
            return suffix.isEmpty ? label(string) : "\(string)\(suffix)"
        }
        if let list = value as? [Any] {
            return list.map { text($0, label: label) }.joined(separator: "、")
        }
        if let object = value as? [String: Any] {
            return object.keys.sorted().map { "\($0)=\(text(object[$0], label: label))" }.joined(separator: " · ")
        }
        return String(describing: value)
    }
}

struct AODSummary {
    let values: [Double]
    let sources: [String]
    let detail: String
    var available: Bool { !values.isEmpty }
    var source: String {
        if sources.isEmpty { return "暂无来源" }
        if sources.allSatisfy({ $0 == "CAMS" }) { return "CAMS" }
        return sources.allSatisfy({ $0 != "CAMS" }) ? "其他来源" : "混合来源"
    }
    var value: String {
        guard let low = values.min(), let high = values.max() else { return "—" }
        let first = String(format: "%.2f", low), last = String(format: "%.2f", high)
        return first == last ? first : "\(first) – \(last)"
    }
    var caption: String {
        if values.isEmpty { return "暂无可用 AOD 观测" }
        return Set(values).count > 1 ? "事件时刻取值范围 · 未取平均" : "事件时刻 · 气溶胶光学厚度"
    }

    static func reading(reports: [[String: Any]]) -> AODSummary {
        var values: [Double] = []
        var sources: [String] = []
        var details: [String] = []
        for report in reports {
            guard let aerosol = report["aerosol"] as? [String: Any],
                  let number = aerosol["aod550"] as? NSNumber,
                  number.doubleValue.isFinite, number.doubleValue >= 0 else { continue }
            // Environmental reading, not a model's peak-quality component.
            let rawSource = aerosol["source"] as? String ?? ""
            let cams = rawSource.lowercased().hasPrefix("cams") || aerosol["quality"] as? String == "cams"
            values.append(number.doubleValue)
            sources.append(cams ? "CAMS" : "其他来源")
            let time = report["anchor_local"] as? String ?? "时刻未提供"
            details.append("\(time) · \(String(format: "%.4f", number.doubleValue)) · \(rawSource)")
        }
        return AODSummary(values: values, sources: sources,
                          detail: details.isEmpty ? "等待 CAMS 环境读取" : Array(Set(details)).sorted().joined(separator: "\n"))
    }
}

struct ForecastCurvePoint: Identifiable {
    let model: String
    let time: Date
    let quality: Double
    let segment: Int
    let local: String
    var id: String { "\(model)-\(time.timeIntervalSince1970)-\(segment)" }
    var series: String { "\(model)-\(segment)" }
}

/// One 0–1 ratio series sample (可烧云占比 / 可烧比例 / 回程透过率), kept on its own
/// percent axis rather than the fixed 0–2.5 quality axis.
struct ForecastRatioPoint: Identifiable {
    let model: String
    let time: Date
    let local: String
    let value: Double
    let kind: String
    let segment: Int
    var id: String { "\(kind)-\(model)-\(time.timeIntervalSince1970)-\(segment)" }
    var series: String { "\(kind)-\(model)-\(segment)" }
}

struct ForecastComparison {
    static let models = ["gfs", "ec"]
    let result: [String: Any]?

    func error(_ model: String) -> String? {
        (result?["model_errors"] as? [String: Any])?[model] as? String
    }

    func report(_ model: String) -> [String: Any]? {
        guard error(model) == nil else { return nil }
        return (result?["model_results"] as? [String: Any])?[model] as? [String: Any]
    }

    func status(_ model: String) -> String {
        if error(model) != nil { return "未完成" }
        if report(model) != nil { return "预测完成" }
        return result == nil ? "等待预测" : "暂无结果"
    }

    func peak(_ model: String) -> Double? {
        let report = report(model)
        guard let number = (report?["peak_quality_index"] ?? report?["quality_index"]) as? NSNumber,
              number.doubleValue.isFinite else { return nil }
        return number.doubleValue
    }

    var aod: AODSummary {
        AODSummary.reading(reports: Self.models.compactMap { report($0) })
    }

    func curve(timezone: String) -> [ForecastCurvePoint] {
        Self.models.flatMap { Self.curve(report: report($0), model: $0, timezone: timezone) }
            .sorted { $0.time < $1.time }
    }

    func ratioCurve(timezone: String) -> [ForecastRatioPoint] {
        Self.models.flatMap { Self.ratioCurve(report: report($0), model: $0, timezone: timezone) }
            .sorted { $0.time < $1.time }
    }

    func aodCurve(timezone: String) -> [ForecastCurvePoint] {
        Self.models.flatMap { Self.aodCurve(report: report($0), model: $0, timezone: timezone) }
            .sorted { $0.time < $1.time }
    }

    static func curve(report: [String: Any]?, model: String, timezone: String) -> [ForecastCurvePoint] {
        let iso = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let local = stepLocalFormatter(timezone)
        var points: [ForecastCurvePoint] = []
        var segment = 0
        var seen: Set<Date> = []
        for step in report?["steps"] as? [[String: Any]] ?? [] {
            let time = stepDate(step, iso: iso, fractional: fractional, local: local)
            guard let time, step["valid"] as? Bool != false,
                  let quality = step["instant_quality_index"] as? NSNumber,
                  quality.doubleValue.isFinite else {
                // Keep invalid samples as breaks rather than joining across them.
                segment += 1
                continue
            }
            guard seen.insert(time).inserted else { continue }
            points.append(ForecastCurvePoint(model: model, time: time, quality: quality.doubleValue,
                                             segment: segment, local: step["valid_time_local"] as? String ?? ""))
        }
        return points.sorted { $0.time < $1.time }
    }

    static func ratioCurve(report: [String: Any]?, model: String, timezone: String) -> [ForecastRatioPoint] {
        let kinds: [(String, String)] = [
            ("coverage", "burnable_cloud_coverage"),
            ("ratio", "forward_burnable_cloud_ratio"),
            ("transmission", "observer_return_transmission"),
        ]
        let iso = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let local = stepLocalFormatter(timezone)
        var points: [ForecastRatioPoint] = []
        for (kind, key) in kinds {
            var segment = 0
            var seen: Set<Date> = []
            for step in report?["steps"] as? [[String: Any]] ?? [] {
                let time = stepDate(step, iso: iso, fractional: fractional, local: local)
                guard let time, step["valid"] as? Bool != false,
                      let value = step[key] as? NSNumber, value.doubleValue.isFinite else {
                    segment += 1
                    continue
                }
                guard seen.insert(time).inserted else { continue }
                points.append(ForecastRatioPoint(model: model, time: time,
                                                 local: step["valid_time_local"] as? String ?? "",
                                                 value: min(max(value.doubleValue, 0), 1),
                                                 kind: kind, segment: segment))
            }
        }
        return points
    }

    static func aodCurve(report: [String: Any]?, model: String, timezone: String) -> [ForecastCurvePoint] {
        let iso = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let local = stepLocalFormatter(timezone)
        var points: [ForecastCurvePoint] = []
        var segment = 0
        var seen: Set<Date> = []
        for step in report?["steps"] as? [[String: Any]] ?? [] {
            let time = stepDate(step, iso: iso, fractional: fractional, local: local)
            guard let time, step["valid"] as? Bool != false,
                  let aod = step["quality_aod550"] as? NSNumber,
                  aod.doubleValue.isFinite, aod.doubleValue >= 0 else {
                segment += 1
                continue
            }
            guard seen.insert(time).inserted else { continue }
            points.append(ForecastCurvePoint(model: model, time: time, quality: aod.doubleValue,
                                             segment: segment, local: step["valid_time_local"] as? String ?? ""))
        }
        return points
    }

    static func stepDate(_ step: [String: Any], iso: ISO8601DateFormatter,
                         fractional: ISO8601DateFormatter, local: DateFormatter) -> Date? {
        let utc = step["valid_time_utc"] as? String
        let label = utc ?? step["valid_time_local"] as? String ?? ""
        return iso.date(from: label) ?? fractional.date(from: label)
            ?? (utc == nil ? local.date(from: label) : nil)
    }

    static func stepLocalFormatter(_ timezone: String) -> DateFormatter {
        let local = DateFormatter()
        local.calendar = Calendar(identifier: .gregorian)
        local.locale = Locale(identifier: "en_US_POSIX")
        local.timeZone = TimeZone(identifier: timezone) ?? .current
        local.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return local
    }
}
