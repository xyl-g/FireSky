import SwiftUI

/// 工作台共享样式与格式助手（自 Workbench.swift 拆出）。
/// 桌面版 NSColor 动态提供者 → UIColor 动态提供者，明暗语义不变。

let warm = Color(uiColor: UIColor { trait in
    trait.userInterfaceStyle == .dark
        ? UIColor(red: 1, green: 0.55, blue: 0.38, alpha: 1)
        : UIColor(red: 0.74, green: 0.32, blue: 0.24, alpha: 1)
})
let teal = Color(red: 0.26, green: 0.56, blue: 0.54)

struct WorkBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        LinearGradient(
            colors: scheme == .dark
                ? [Color(red: 0.07, green: 0.10, blue: 0.11), Color(red: 0.13, green: 0.12, blue: 0.14), Color(red: 0.08, green: 0.13, blue: 0.12)]
                : [Color(red: 0.89, green: 0.93, blue: 0.94), Color(red: 0.96, green: 0.92, blue: 0.90), Color(red: 0.89, green: 0.94, blue: 0.92)],
            startPoint: .topLeading, endPoint: .bottomTrailing)
    }
}

extension View {
    func workPageBackground() -> some View {
        background { WorkBackground().ignoresSafeArea() }
    }

    func workSurface(emphasized: Bool = false) -> some View { modifier(WorkSurface(emphasized: emphasized)) }

    func workChip() -> some View {
        padding(.horizontal, 8).padding(.vertical, 5)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))
            .overlay { RoundedRectangle(cornerRadius: 6).stroke(.secondary.opacity(0.16), lineWidth: 1) }
    }
}

struct WorkSurface: ViewModifier {
    var emphasized = false
    @Environment(\.colorScheme) private var scheme
    func body(content: Content) -> some View {
        content.background {
            RoundedRectangle(cornerRadius: 8)
                .fill(scheme == .dark ? Color.white.opacity(emphasized ? 0.075 : 0.045) : Color.white.opacity(emphasized ? 0.82 : 0.60))
        }.overlay {
            RoundedRectangle(cornerRadius: 8)
                .stroke(scheme == .dark ? Color.white.opacity(emphasized ? 0.16 : 0.09) : Color.white.opacity(0.80), lineWidth: 1)
        }
    }
}

struct WorkSegments: View {
    let options: [String]
    var titles: [String]? = nil
    @Binding var selection: String
    var accented = false
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        HStack(spacing: 3) {
            ForEach(Array(options.enumerated()), id: \.element) { index, value in
                let selected = selection == value
                Button { selection = value } label: {
                    Text(titles?[index] ?? value)
                        .font(.system(size: 11, weight: selected ? .semibold : .regular))
                        .foregroundStyle(selected && accented ? warm : selected ? Color.primary : Color.secondary)
                        .frame(maxWidth: .infinity).frame(height: 26)
                        .background {
                            RoundedRectangle(cornerRadius: 6)
                                .fill(selected ? (accented ? warm.opacity(0.16) : scheme == .dark ? Color.white.opacity(0.10) : .white.opacity(0.85)) : .clear)
                        }
                        .contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
        }.padding(3)
            .background(.black.opacity(scheme == .dark ? 0.12 : 0.035), in: RoundedRectangle(cornerRadius: 8))
            .overlay { RoundedRectangle(cornerRadius: 8).stroke(.secondary.opacity(0.15), lineWidth: 1) }
    }
}

func format(_ value: Any?, digits: Int = 2) -> String {
    guard let number = value as? NSNumber else { return "—" }
    return String(format: "%.*f", digits, number.doubleValue)
}

func modelColor(_ model: String) -> Color { model == "ec" ? teal : warm }

func timeLabel(_ value: String?) -> String {
    guard let value, value.count >= 16 else { return "—" }
    return String(value.dropFirst(11).prefix(5))
}

func windowLabel(_ report: JSONObject?) -> String {
    guard let value = report?["timing_window"] as? JSONObject else { return "--" }
    return "\(timeLabel(value["start_time_local"] as? String)) – \(timeLabel(value["end_time_local"] as? String))"
}

func metricValue(_ key: String, _ report: JSONObject?) -> String {
    guard let report else { return "--" }
    let components = report["quality_components"] as? JSONObject ?? [:]
    switch key {
    case "aod": return format(components["quality_aod550"] ?? report["quality_aod550"])
    case "duration": return format(report["duration_minutes"] ?? components["duration_minutes"], digits: 0)
    case "confidence": return percent(report["confidence_score"])
    case "disagreement": return format(report["model_disagreement_score"])
    case "burnable": return percent(components["burnable_cloud_coverage_effective"] ?? report["burnable_cloud_coverage_effective"])
    case "material": return format(components["cloud_material"])
    case "brightness": return format(components["brightness_color"])
    case "incoming": return format(components["incoming_aod_illumination_factor"])
    case "contrast": return format(components["observer_aod_contrast_factor"])
    default: return "--"
    }
}

func percent(_ value: Any?) -> String {
    guard let number = value as? NSNumber else { return "--" }
    return String(format: "%.0f%%", number.doubleValue * 100)
}

/// 概览页 AOD 指标卡（桌面 WorkAODValue 原样迁移）。
struct WorkAODValue: View {
    let summary: AODSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(summary.source).font(.system(size: 9, weight: .medium)).foregroundStyle(teal)
            Text(summary.value).font(.system(size: 19, weight: .semibold)).monospacedDigit()
                .foregroundStyle(summary.available ? Color.primary : Color.secondary)
                .lineLimit(1).minimumScaleFactor(0.8)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}
