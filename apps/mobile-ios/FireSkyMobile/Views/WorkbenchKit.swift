import SwiftUI

/// 工作台共享样式与格式助手（自 Workbench.swift 拆出）。
/// 桌面版 NSColor 动态提供者 → UIColor 动态提供者，明暗语义不变。

let warm = Color(uiColor: UIColor { trait in
    trait.userInterfaceStyle == .dark
        ? UIColor(red: 1, green: 155 / 255.0, blue: 106 / 255.0, alpha: 1)
        : UIColor(red: 210 / 255.0, green: 90 / 255.0, blue: 54 / 255.0, alpha: 1)
})
let teal = Color(red: 0.26, green: 0.56, blue: 0.54)

struct WorkBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        LinearGradient(
            colors: FSDesign.pageBackground(dark: scheme == .dark),
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
    @Environment(\.colorSchemeContrast) private var contrast
    func body(content: Content) -> some View {
        content.background {
            RoundedRectangle(cornerRadius: 8)
                .fill(FSDesign.surface(dark: scheme == .dark))
        }.overlay {
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(emphasized ? warm : FSDesign.border(dark: scheme == .dark),
                              lineWidth: contrast == .increased ? 2 : 1)
        }
    }
}

struct WorkSegments: View {
    let options: [String]
    var titles: [String]? = nil
    @Binding var selection: String
    var accented = false
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(spacing: 3)) : AnyLayout(HStackLayout(spacing: 3))
        layout {
            ForEach(Array(options.enumerated()), id: \.element) { index, value in
                let selected = selection == value
                Button { selection = value } label: {
                    Text(titles?[index] ?? value)
                        .font(.subheadline.weight(selected ? .semibold : .regular))
                        .foregroundStyle(selected && accented ? warm : selected ? Color.primary : Color.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 4).padding(.vertical, 8)
                        .frame(maxWidth: .infinity).frame(minHeight: 44)
                        .background {
                            RoundedRectangle(cornerRadius: 6)
                                .fill(selected ? (accented ? warm.opacity(0.16) : scheme == .dark ? Color.white.opacity(0.10) : .white.opacity(0.85)) : .clear)
                        }
                        .contentShape(Rectangle())
                }.buttonStyle(.plain)
                    .accessibilityAddTraits(selected ? .isSelected : [])
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
