import SwiftUI

struct FrozenProfileView: View {
    @EnvironmentObject private var store: Store
    let model: String
    let reload: () -> Void

    private var section: JSONObject? {
        guard store.frozenProfileModel == model else { return nil }
        return store.frozenProfile
    }

    private var fields: JSONObject {
        section?["fields"] as? JSONObject ?? [:]
    }

    private var distanceCount: Int {
        (section?["distances_km"] as? [Any])?.count ?? 0
    }

    private var state: String {
        store.frozenProfileModel == model ? store.frozenProfileState : "idle"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                Text(model == "satellite" ? "卫星冻结剖面" : "\(model.uppercased()) 冻结剖面")
                    .font(.system(size: 11, weight: .semibold))
                Spacer()
                if state == "loading" {
                    ProgressView().controlSize(.mini)
                } else {
                    Button(action: reload) { Image(systemName: "arrow.clockwise") }
                        .buttonStyle(.borderless)
                        .help("重新读取剖面")
                        .disabled(store.busy)
                }
            }
            switch state {
            case "loading":
                Text("正在读取冻结剖面…").font(.system(size: 10)).foregroundStyle(.secondary)
            case "ready":
                if model == "satellite" { satelliteReady } else { modelReady }
            case "idle":
                Text("点击“读取冻结剖面”查看该结果的太阳方向诊断。")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            default:
                Text(store.frozenProfileError.isEmpty ? "该结果没有可用剖面。" : store.frozenProfileError)
                    .font(.system(size: 10)).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(9)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
    }

    private var modelReady: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let time = section?["valid_time_local"] as? String {
                Text(time.replacingOccurrences(of: "T", with: " "))
                    .font(.system(size: 10)).monospacedDigit()
            }
            HStack(spacing: 10) {
                metric("太阳高度", section?["solar_elevation_deg"], suffix: "°")
                metric("太阳方位", section?["solar_azimuth_deg"], suffix: "°")
                Text("\(distanceCount) 个距离样本").font(.system(size: 9)).foregroundStyle(.secondary)
            }
            let cloud = fields["cloud_fraction_percent"] as? [[Any]]
            let temperatures = fields["temperature_c"] as? [[Any]]
            HStack(spacing: 10) {
                metric("云量样本", cloud?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
                metric("温度样本", temperatures?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
            }
        }
    }

    private var satelliteReady: some View {
        let profile = section?["profile"] as? [JSONObject] ?? []
        let times = section?["source_times_utc"] as? [String] ?? []
        let distances = profile.compactMap { number($0["distance_km"]) }
        return VStack(alignment: .leading, spacing: 5) {
            Text(store.frozenProfileTime.replacingOccurrences(of: "T", with: " "))
                .font(.system(size: 10)).monospacedDigit()
            HStack(spacing: 10) {
                metric("采样数", section?["samples_count"], suffix: "")
                metric("剖面样本", profile.count, suffix: "")
                if let azimuth = number(profile.first?["solar_azimuth_deg"]) {
                    metric("太阳方位", azimuth, suffix: "°")
                }
            }
            if let low = distances.min(), let high = distances.max() {
                Text(String(format: "光路距离 %.0f–%.0f km", low, high))
                    .font(.system(size: 9)).foregroundStyle(.secondary)
            }
            if !times.isEmpty {
                Text("卫星源时次 " + times.joined(separator: " · "))
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func metric(_ title: String, _ value: Any?, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(format(value) + suffix).font(.system(size: 10, weight: .medium)).monospacedDigit()
        }
    }

    private func number(_ value: Any?) -> Double? {
        if let value = value as? NSNumber, value.doubleValue.isFinite { return value.doubleValue }
        if let value = value as? Double, value.isFinite { return value }
        if let value = value as? Int { return Double(value) }
        return nil
    }

    private func format(_ value: Any?) -> String {
        if let number = value as? NSNumber, number.doubleValue.isFinite {
            return String(format: "%.1f", number.doubleValue)
        }
        if let integer = value as? Int { return "\(integer)" }
        return "—"
    }
}
