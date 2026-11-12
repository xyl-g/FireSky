import SwiftUI

struct RegionalProfileView: View {
    @EnvironmentObject private var store: Store
    let model: String
    let coordinate: GridCoordinate

    private var section: JSONObject? {
        guard store.regionalProfileModel == model,
              store.regionalProfileCoordinate == coordinate else { return nil }
        return store.regionalProfile
    }

    private var fields: JSONObject {
        section?["fields"] as? JSONObject ?? [:]
    }

    private var distanceCount: Int {
        (section?["distances_km"] as? [Any])?.count ?? 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                Text("\(model.uppercased()) 剖面").font(.system(size: 11, weight: .semibold))
                Spacer()
                if store.regionalProfileState == "loading" {
                    ProgressView().controlSize(.mini)
                } else {
                    Button {
                        store.queryRegionalProfile(model: model, coordinate: coordinate)
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .buttonStyle(.borderless)
                    .help("重新读取剖面")
                    .disabled(store.busy)
                }
            }
            switch store.regionalProfileState {
            case "loading":
                Text("正在读取所选网格的冻结剖面…")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            case "ready":
                ready
            case "idle":
                Text("点击“读取剖面”查看该网格的太阳方向大气诊断。")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            default:
                Text(store.regionalProfileError.isEmpty ? "该网格没有可用剖面。" : store.regionalProfileError)
                    .font(.system(size: 10)).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(9)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
    }

    private var ready: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let time = section?["valid_time_local"] as? String {
                Text(time.replacingOccurrences(of: "T", with: " "))
                    .font(.system(size: 10)).monospacedDigit()
            }
            HStack(spacing: 10) {
                metric("太阳高度", section?["solar_elevation_deg"], suffix: "°")
                metric("太阳方位", section?["solar_azimuth_deg"], suffix: "°")
                Text("\(distanceCount) 个距离样本")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
            }
            let cloud = fields["cloud_fraction_percent"] as? [[Any]]
            let temperatures = fields["temperature_c"] as? [[Any]]
            HStack(spacing: 10) {
                metric("云量样本", cloud?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
                metric("温度样本", temperatures?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
            }
        }
    }

    private func metric(_ title: String, _ value: Any?, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(format(value) + suffix).font(.system(size: 10, weight: .medium)).monospacedDigit()
        }
    }

    private func format(_ value: Any?) -> String {
        if let number = value as? NSNumber, number.doubleValue.isFinite {
            return String(format: "%.1f", number.doubleValue)
        }
        if let integer = value as? Int { return "\(integer)" }
        return "—"
    }
}
