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
        VStack(alignment: .leading, spacing: 6) {
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
                    .accessibilityLabel("重新读取 \(model.uppercased()) 剖面")
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
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
    }

    private var ready: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if let time = section?["valid_time_local"] as? String {
                    Text(time.replacingOccurrences(of: "T", with: " "))
                        .font(.system(size: 9.5)).monospacedDigit().foregroundStyle(.secondary)
                }
                Spacer(minLength: 4)
                Text("\(distanceCount) \(L10n.text("距离样本", "samples"))")
                    .font(.system(size: 9)).foregroundStyle(.secondary).monospacedDigit()
            }
            let cloud = fields["cloud_fraction_percent"] as? [[Any]]
            let temperatures = fields["temperature_c"] as? [[Any]]
            HStack(spacing: 8) {
                metric("太阳高度", section?["solar_elevation_deg"], suffix: "°")
                metric("太阳方位", section?["solar_azimuth_deg"], suffix: "°")
                metric("云量", cloud?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
                metric("温度", temperatures?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
            }
            figureButton
        }
    }

    /// regional-7：区域网格距离×高度剖面图层（与单点共用 CrossSectionFigureView）。
    /// 图层与图例来自 profile_query 的 cross_section.fields（温度/RH/云量/地形/照明），
    /// 侧栏宽度不足以可读绘制，展开到弹出窗口查看。
    private var figureButton: some View {
        Button {
            store.showRegionalProfileFigure = true
        } label: {
            Label("展开剖面图层", systemImage: "square.split.bottomrightquarter")
                .font(.system(size: 10, weight: .medium))
        }
        .buttonStyle(.borderless)
        .help("查看温度、湿度、云层、地形与照明剖面图层")
        .accessibilityLabel("展开 \(model.uppercased()) 网格剖面图层")
        .popover(isPresented: $store.showRegionalProfileFigure, arrowEdge: .trailing) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline) {
                    Text("\(model.uppercased()) 网格剖面").font(.system(size: 12, weight: .semibold))
                    Spacer()
                    Text(coordinate.label).font(.system(size: 10)).monospacedDigit().foregroundStyle(.secondary)
                }
                if let section {
                    CrossSectionFigureView(section: section)
                }
                Text("图层由 \(model.uppercased()) 冻结 profile_query 的剖面字段绘制；温度/RH/云量/地形/照明为该模式自身的场，缺测层不绘制、不解释为晴空。")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(12)
            .frame(width: 740, alignment: .topLeading)
        }
    }

    private func metric(_ title: String, _ value: Any?, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary).lineLimit(1)
            Text(format(value) + suffix).font(.system(size: 10, weight: .medium)).monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func format(_ value: Any?) -> String {
        if let number = value as? NSNumber, number.doubleValue.isFinite {
            return String(format: "%.1f", number.doubleValue)
        }
        if let integer = value as? Int { return "\(integer)" }
        return "—"
    }
}
