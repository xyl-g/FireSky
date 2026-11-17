import Foundation

struct AODSummary {
    let values: [Double]
    let sources: [String]
    let detail: String
    var available: Bool { !values.isEmpty }
    var source: String {
        if sources.isEmpty || sources.allSatisfy({ $0 == "CAMS" }) { return "CAMS" }
        return sources.allSatisfy({ $0 != "CAMS" }) ? "其他来源" : "混合来源"
    }
    var value: String {
        guard let low = values.min(), let high = values.max() else { return "--" }
        let first = String(format: "%.2f", low), last = String(format: "%.2f", high)
        return first == last ? first : "\(first) – \(last)"
    }
    var caption: String {
        Set(values).count > 1 ? "事件时刻取值范围 · 未取平均" : "事件时刻 · 气溶胶光学厚度"
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
    var id: String { "\(model)-\(time.timeIntervalSince1970)-\(segment)" }
    var series: String { "\(model)-\(segment)" }
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

    static func curve(report: [String: Any]?, model: String, timezone: String) -> [ForecastCurvePoint] {
        let iso = ISO8601DateFormatter()
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let local = DateFormatter()
        local.calendar = Calendar(identifier: .gregorian)
        local.locale = Locale(identifier: "en_US_POSIX")
        local.timeZone = TimeZone(identifier: timezone) ?? .current
        local.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        var points: [ForecastCurvePoint] = []
        var segment = 0
        var seen: Set<Date> = []
        for step in report?["steps"] as? [[String: Any]] ?? [] {
            let utc = step["valid_time_utc"] as? String
            let label = utc ?? step["valid_time_local"] as? String ?? ""
            let time = iso.date(from: label) ?? fractional.date(from: label)
                ?? (utc == nil ? local.date(from: label) : nil)
            guard let time, step["valid"] as? Bool != false,
                  let quality = step["instant_quality_index"] as? NSNumber,
                  quality.doubleValue.isFinite else {
                // Keep invalid samples as breaks rather than joining across them.
                segment += 1
                continue
            }
            guard seen.insert(time).inserted else { continue }
            points.append(ForecastCurvePoint(model: model, time: time, quality: quality.doubleValue, segment: segment))
        }
        return points.sorted { $0.time < $1.time }
    }
}
