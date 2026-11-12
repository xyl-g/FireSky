import Foundation

struct NowcastProfileCell: Identifiable {
    let id: String
    let startKM: Double
    let endKM: Double
    let bottomKM: Double
    let topKM: Double
    let probability: Double?
}

struct NowcastPresentation {
    let report: [String: Any]?
    var observation: [String: Any] { report?["nowcast"] as? [String: Any] ?? [:] }
    var aod: AODSummary { AODSummary.reading(reports: report.map { [$0] } ?? []) }

    static let metrics = [
        ("near_cloud_ratio", "近场云量", "观测点附近卫星云覆盖"),
        ("far_cloud_ratio", "远场云量", "太阳方向远场云覆盖"),
        ("satellite_low_cloud_state", "低云状态", "卫星校准与垂直先验"),
        ("satellite_motion_quality", "运动质量", "卫星云体运动状态"),
        ("convective_score_ceiling", "对流顶棚", "对流约束评分上限"),
        ("geometric_path_blocking_risk", "光路阻塞风险", "太阳入射路径"),
        ("calibrated_sample_coverage_ratio", "采样覆盖率", "卫星有效采样比例"),
        ("confidence", "实况支持度", "卫星反演支持 · 非命中概率"),
        ("aod", "AOD 550", "气溶胶光学厚度"),
    ]

    func value(_ key: String) -> String {
        if key == "aod" { return aod.value }
        if key == "satellite_motion_quality" {
            guard let value = observation[key] as? String else { return "--" }
            return ["observation": "原始观测", "strong": "可靠", "decayed": "已衰减",
                    "unknown": "未知", "diagnostic-only": "仅供诊断"][value] ?? value
        }
        if key == "satellite_low_cloud_state" {
            guard let value = observation[key] as? String else { return "--" }
            return ["supported": "支持低云", "not_supported": "不支持低云", "unknown": "未知"][value] ?? value
        }
        if key == "cloud_type_label" {
            let label = observation[key] as? String ?? ""
            return label.isEmpty ? "--" : label
        }
        guard let value = Self.number(observation[key]) else { return "--" }
        switch key {
        case "near_cloud_ratio", "far_cloud_ratio", "geometric_path_blocking_risk",
             "calibrated_sample_coverage_ratio", "confidence", "convective_score_ceiling",
             "radar_reliability_score", "radar_local_precip_ratio", "radar_path_precip_ratio":
            return String(format: "%.0f%%", value * 100)
        default: return String(format: "%.2f", value)
        }
    }

    var profileBearing: Double? {
        let profile = observation["satellite_vertical_profile"] as? [String: Any]
        let samples = profile?["samples"] as? [[String: Any]] ?? []
        let bearings = Set(samples.compactMap { Self.number($0["bearing_deg"]) }).sorted()
        let section = report?["cross_section"] as? [String: Any]
        let steps = report?["steps"] as? [[String: Any]] ?? []
        let bestTime = report?["best_time_local"] as? String
        let best = bestTime.flatMap { time in steps.first { $0["valid_time_local"] as? String == time } }
        let diagnostic = steps.first {
            $0["valid"] as? Bool != false && Self.number($0["solar_azimuth_deg"]) != nil
        }
        let target = Self.number(section?["solar_azimuth_deg"]) ?? Self.number(best?["solar_azimuth_deg"])
            ?? Self.number(diagnostic?["solar_azimuth_deg"])
        if let target {
            return bearings.min { Self.angularDistance($0, target) < Self.angularDistance($1, target) }
        }
        return bearings.count == 1 ? bearings.first : nil
    }

    var profileBearingCaption: String {
        guard let bearing = profileBearing else { return "卫星云层证据 · GFS 高度先验" }
        let basis = report?["best_time_local"] as? String == nil ? "诊断采样方位" : "峰值附近采样方位"
        return String(format: "%@ %.1f° · 卫星证据 / GFS 高度先验", basis, bearing)
    }

    var profileUnavailableReason: String {
        let profile = observation["satellite_vertical_profile"] as? [String: Any]
        let samples = profile?["samples"] as? [[String: Any]] ?? []
        if !samples.isEmpty && profileBearing == nil {
            return "已有多方位卫星样本，但缺少太阳方位，无法选定剖面"
        }
        return profile?["reason"] as? String ?? "暂无可绘制的冻结卫星剖面"
    }

    var profileCells: [NowcastProfileCell] {
        guard let profile = observation["satellite_vertical_profile"] as? [String: Any],
              profile["status"] as? String != "unavailable" else { return [] }
        let rawSamples = profile["samples"] as? [[String: Any]] ?? []
        let bearing = profileBearing
        let bearings = Set(rawSamples.compactMap { Self.number($0["bearing_deg"]) })
        // The retrieval covers an azimuth fan; a section must not overlay every bearing.
        guard bearing != nil || bearings.count <= 1 else { return [] }
        let samples = rawSamples
            .filter { Self.number($0["distance_km"]) != nil }
            .filter { sample in
                guard let bearing else { return true }
                guard let value = Self.number(sample["bearing_deg"]) else { return false }
                return Self.angularDistance(value, bearing) < 0.5
            }
            .sorted { Self.number($0["distance_km"])! < Self.number($1["distance_km"])! }
        var cells: [NowcastProfileCell] = []
        for (index, sample) in samples.enumerated() {
            let distance = Self.number(sample["distance_km"])!
            let before = index > 0 ? Self.number(samples[index - 1]["distance_km"])! : distance - 10
            let after = index + 1 < samples.count ? Self.number(samples[index + 1]["distance_km"])! : distance + 10
            let levels = (sample["levels"] as? [[String: Any]] ?? [])
                .filter { (Self.number($0["height_m"]) ?? -1) >= 0 }
                .sorted { Self.number($0["height_m"])! < Self.number($1["height_m"])! }
            for (layer, level) in levels.enumerated() {
                let height = Self.number(level["height_m"])!
                let lower = layer > 0 ? Self.number(levels[layer - 1]["height_m"])! : 0
                let upper = layer + 1 < levels.count ? Self.number(levels[layer + 1]["height_m"])! : height + 1000
                let retrieved = sample["status"] as? String == "retrieved" && level["status"] as? String != "unknown"
                let probability = retrieved ? Self.number(level["hsd_posterior_percent"] ?? level["cloud_probability_percent"]) : nil
                cells.append(NowcastProfileCell(
                    id: "\(index)-\(layer)", startKM: max(0, (before + distance) / 2), endKM: (distance + after) / 2,
                    bottomKM: max(0, (lower + height) / 2000), topKM: (height + upper) / 2000,
                    probability: probability.map { min(100, max(0, $0)) }
                ))
            }
        }
        return cells
    }

    private static func number(_ value: Any?) -> Double? {
        guard let value = value as? NSNumber, value.doubleValue.isFinite else { return nil }
        return value.doubleValue
    }

    private static func angularDistance(_ left: Double, _ right: Double) -> Double {
        let difference = abs((left - right).truncatingRemainder(dividingBy: 360))
        return min(difference, 360 - difference)
    }
}
