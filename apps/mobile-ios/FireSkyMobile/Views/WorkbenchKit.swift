import SwiftUI

/// 工作台共享样式与格式助手（与 macOS Workbench.swift / DesignSystem.swift 对齐）。
let warm = Color(uiColor: UIColor { trait in
    trait.userInterfaceStyle == .dark
        ? UIColor(red: 1, green: 155 / 255.0, blue: 106 / 255.0, alpha: 1)
        : UIColor(red: 210 / 255.0, green: 90 / 255.0, blue: 54 / 255.0, alpha: 1)
})

let coolAccent = Color(uiColor: UIColor { trait in
    trait.userInterfaceStyle == .dark
        ? UIColor(red: 0.56, green: 0.72, blue: 1.00, alpha: 1)
        : UIColor(red: 0.31, green: 0.46, blue: 0.72, alpha: 1)
})

let teal = Color(red: 0.26, green: 0.56, blue: 0.54)

let coverageColor = Color(uiColor: UIColor { trait in
    trait.userInterfaceStyle == .dark
        ? UIColor(red: 0.28, green: 0.84, blue: 0.68, alpha: 1)
        : UIColor(red: 0.04, green: 0.44, blue: 0.34, alpha: 1)
})

let illuminationColor = Color(uiColor: UIColor { trait in
    trait.userInterfaceStyle == .dark
        ? UIColor(red: 1.00, green: 0.76, blue: 0.45, alpha: 1)
        : UIColor(red: 0.62, green: 0.40, blue: 0.12, alpha: 1)
})

let transmissionColor = Color(uiColor: UIColor { trait in
    trait.userInterfaceStyle == .dark
        ? UIColor(red: 0.25, green: 0.74, blue: 0.97, alpha: 1)
        : UIColor(red: 0.04, green: 0.36, blue: 0.56, alpha: 1)
})

struct WorkBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        ZStack {
            LinearGradient(
                colors: FSDesign.pageBackground(dark: scheme == .dark),
                startPoint: .topLeading, endPoint: .bottomTrailing)
            RadialGradient(
                colors: [FSDesign.ambientGlow(dark: scheme == .dark), Color.clear],
                center: .topTrailing, startRadius: 80, endRadius: 620)
            RadialGradient(
                colors: [FSDesign.coolGlow(dark: scheme == .dark), Color.clear],
                center: .bottomLeading, startRadius: 60, endRadius: 560)
        }
    }
}

extension View {
    func workPageBackground() -> some View {
        background { WorkBackground().ignoresSafeArea() }
    }

    func workSurface(emphasized: Bool = false) -> some View {
        fireSkySurface(role: .content, emphasized: emphasized, cornerRadius: 14)
    }

    func workChip() -> some View {
        modifier(WorkChipModifier())
    }

    func workInputField(cornerRadius: CGFloat = 10) -> some View {
        modifier(WorkInputFieldModifier(cornerRadius: cornerRadius))
    }
}

private struct WorkChipModifier: ViewModifier {
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        let dark = scheme == .dark
        content
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(
                FSDesign.surfaceFill(role: .grouped, dark: dark),
                in: RoundedRectangle(cornerRadius: 6)
            )
            .overlay {
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
            }
    }
}

private struct WorkInputFieldModifier: ViewModifier {
    let cornerRadius: CGFloat
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        let dark = scheme == .dark
        content
            .textFieldStyle(.plain)
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .background(
                FSDesign.surfaceFill(role: .grouped, dark: dark),
                in: RoundedRectangle(cornerRadius: cornerRadius)
            )
            .overlay {
                RoundedRectangle(cornerRadius: cornerRadius)
                    .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
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

func number(_ value: Any?) -> Double? {
    guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
    return number.doubleValue
}

func modelColor(_ model: String) -> Color { model == "ec" ? coolAccent : warm }

func timeLabel(_ value: String?) -> String {
    guard let value, value.count >= 16 else { return "—" }
    return String(value.dropFirst(11).prefix(5))
}

func windowLabel(_ report: JSONObject?) -> String {
    guard let value = report?["timing_window"] as? JSONObject else { return "—" }
    return "\(timeLabel(value["start_time_local"] as? String)) – \(timeLabel(value["end_time_local"] as? String))"
}

func percent(_ value: Any?) -> String {
    guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
    return String(format: "%.0f%%", number.doubleValue * 100)
}

func ratioText(_ value: Any?) -> String {
    guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
    return String(format: "%.0f%%", number.doubleValue * 100)
}

func cloudLayerMetricValue(_ report: JSONObject?) -> String {
    guard let report,
          let base = number(report["dominant_burnable_cloud_base_agl_mean_m"]),
          let top = number(report["dominant_burnable_cloud_top_agl_mean_m"]),
          top >= base else {
        return "—"
    }
    return String(format: "%.1f–%.1f km", base / 1000, top / 1000)
}

func metricValue(_ key: String, _ report: JSONObject?) -> String {
    guard let report else { return "—" }
    let components = report["quality_components"] as? JSONObject ?? [:]
    switch key {
    case "aod": return format(components["quality_aod550"] ?? report["quality_aod550"])
    case "cloud_layer": return cloudLayerMetricValue(report)
    case "duration": return format(report["duration_minutes"] ?? components["duration_minutes"], digits: 0)
    case "confidence": return percent(report["confidence_score"])
    case "convective": return format(report["convective_risk"])
    case "burnable": return percent(components["burnable_cloud_coverage_effective"] ?? report["burnable_cloud_coverage_effective"])
    case "material": return ratioText(components["cloud_material"])
    case "brightness": return ratioText(components["brightness_color"])
    case "incoming": return format(components["incoming_aod_illumination_factor"])
    case "contrast": return format(components["observer_aod_contrast_factor"])
    default: return "—"
    }
}

func familyLabel(_ value: Any?) -> String {
    switch value as? String {
    case "high": return "高云压力族"
    case "middle": return "中云压力族"
    case "low": return "低云压力族"
    default: return "无可烧目标云"
    }
}

func genusLabel(_ value: String) -> String {
    switch value {
    case "cirrus": return L10n.text("卷云", "Cirrus")
    case "cirrocumulus": return L10n.text("卷积云", "Cirrocumulus")
    case "cirrostratus": return L10n.text("卷层云", "Cirrostratus")
    case "altocumulus": return L10n.text("高积云", "Altocumulus")
    case "altostratus": return L10n.text("高层云", "Altostratus")
    case "nimbostratus": return L10n.text("雨层云", "Nimbostratus")
    case "stratocumulus": return L10n.text("层积云", "Stratocumulus")
    case "stratus": return L10n.text("层云", "Stratus")
    case "cumulus": return L10n.text("积云", "Cumulus")
    case "cumulonimbus": return L10n.text("积雨云", "Cumulonimbus")
    default: return value.replacingOccurrences(of: "_", with: " ")
    }
}

func cloudGenusBrief(_ proxy: JSONObject?) -> String? {
    guard let proxy else { return nil }
    let label = proxy["label"] as? String ?? "unknown"
    let pct: String = {
        guard let conf = proxy["confidence"] as? NSNumber, conf.doubleValue.isFinite else { return "" }
        return " \(Int((conf.doubleValue * 100).rounded()))%"
    }()
    if label != "unknown", !label.isEmpty {
        return L10n.text("\(genusLabel(label))（代理\(pct)）", "\(genusLabel(label)) (proxy\(pct))")
    }
    if let compatible = proxy["compatible_genera"] as? [String], let first = compatible.first, !first.isEmpty {
        return L10n.text("\(genusLabel(first))等（代理）", "\(genusLabel(first)) etc. (proxy)")
    }
    return nil
}

func phaseLabel(_ value: String) -> String {
    switch value {
    case "ice": return L10n.text("冰相成分", "Ice")
    case "water": return L10n.text("液态水成分", "Water")
    case "mixed": return L10n.text("冰水混合成分", "Mixed")
    default: return L10n.text("相态未知", "Unknown phase")
    }
}

func sceneLabel(_ value: Any?) -> String {
    guard let value = value as? String, !value.isEmpty else { return "—" }
    switch value {
    case "clear": return "晴空"
    case "forward_cloud_field": return "前向云场"
    case "burnable_cloud_field": return "可烧云场"
    case "solar_incoming_blocked_field": return "入射受阻"
    case "local_deck": return "近场云层"
    case "distant_target": return "远场目标"
    case "remote_boundary": return "远端边界"
    case "cloud_hole": return "云隙"
    case "convective_risk": return "对流风险"
    case "invalid": return "缺测"
    case "unknown": return "未知"
    case "forming": return "形成中"
    case "strengthening": return "增强"
    case "dissipating": return "消散中"
    case "weakening": return "减弱"
    case "steady": return "稳定"
    case "golden": return "金黄"
    case "orange_red": return "橙红"
    case "deep_red": return "深红"
    case "muted_glow": return "暗淡霞光"
    case "water": return "水云"
    case "ice": return "冰云"
    case "mixed": return "混合相"
    case "numerical_model": return "数值模式"
    case "satellite_observation": return "卫星观测"
    default: return value.replacingOccurrences(of: "_", with: " ")
    }
}

func cycleLabel(_ model: String, _ report: JSONObject?) -> String? {
    let cycles = report?["model_cycles"] as? JSONObject
    let value = cycles?[model] as? String ?? report?["cycle_time_utc"] as? String
    guard let value, value.count >= 16 else { return nil }
    let text = value.prefix(16).replacingOccurrences(of: "T", with: " ")
    return value.contains("+00:00") || value.hasSuffix("Z") ? text + "Z" : text
}

func modelSourceNote(_ model: String) -> String {
    model == "ec"
        ? L10n.text("EC 公共 Open Data · TCC + 压力层 RH 云柱代理 · CAMS 气溶胶",
                    "EC public Open Data · TCC + pressure-level RH proxy · CAMS aerosol")
        : L10n.text("GFS 0.25° 全量云场评分 · CAMS 气溶胶",
                    "GFS 0.25° full cloud-field scoring · CAMS aerosol")
}

func ratioSeriesColor(_ kind: String) -> Color {
    switch kind {
    case "coverage": return coverageColor
    case "ratio": return illuminationColor
    default: return transmissionColor
    }
}

func ratioSeriesLabel(_ kind: String) -> String {
    switch kind {
    case "coverage": return L10n.text("可烧云占比", "Burnable coverage")
    case "ratio": return L10n.text("可烧比例", "Burnable ratio")
    default: return L10n.text("回程透过率", "Return trans.")
    }
}

func diagnosticValue(_ value: Any?, digits: Int = 2, suffix: String = "",
                     percentValue: Bool = false) -> String {
    ReportDiagnosticValue.text(value, digits: digits, suffix: suffix, percent: percentValue) { sceneLabel($0) }
}

func summaryText(_ report: JSONObject) -> String {
    if let text = report["summary"] as? String, !text.isEmpty { return text }
    if let values = report["summary"] as? [Any] {
        let lines = values.compactMap { value -> String? in
            guard let text = value as? String, !text.isEmpty else { return nil }
            return "• \(text)"
        }
        if !lines.isEmpty { return lines.joined(separator: "\n") }
    }
    return L10n.text("暂无文字摘要", "No text summary")
}

/// 概览页 AOD 指标卡。
struct WorkAODValue: View {
    let summary: AODSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(summary.source).font(.system(size: 10, weight: .medium)).foregroundStyle(coolAccent)
            Text(summary.value).font(.system(size: 19, weight: .semibold)).monospacedDigit()
                .foregroundStyle(summary.available ? Color.primary : Color.secondary)
                .lineLimit(1).minimumScaleFactor(0.8)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// 观测窗口时段条（对齐 macOS SkyTimeline）。
struct SkyTimeline: View {
    let report: JSONObject?
    let timezone: String
    @Environment(\.colorScheme) private var scheme

    private func minutes(_ hhmm: String) -> Int? {
        let parts = hhmm.split(separator: ":")
        guard parts.count == 2, let h = Int(parts[0]), let m = Int(parts[1]),
              (0...23).contains(h), (0...59).contains(m) else { return nil }
        return h * 60 + m
    }

    var body: some View {
        if let window = report?["timing_window"] as? JSONObject,
           let startRaw = window["start_time_local"] as? String,
           let endRaw = window["end_time_local"] as? String {
            let startLabel = timeLabel(startRaw)
            let endLabel = timeLabel(endRaw)
            let bestRaw = report?["best_time_local"] as? String
            let bestLabel = timeLabel(bestRaw)
            let startMin = minutes(startLabel)
            let endMin = minutes(endLabel)
            let bestMin = minutes(bestLabel)
            VStack(alignment: .leading, spacing: 5) {
                if let startMin, let endMin {
                    let span = (endMin - startMin <= 0) ? endMin - startMin + 1440 : endMin - startMin
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(.quaternary.opacity(0.5))
                            Capsule()
                                .fill(FSDesign.accentGradient(dark: scheme == .dark))
                                .frame(width: geo.size.width)
                            if let bestMin {
                                let offsetRatio = CGFloat((bestMin - startMin <= 0 ? bestMin + 1440 : bestMin) - startMin) / CGFloat(max(span, 1))
                                Circle()
                                    .fill(Color.white)
                                    .frame(width: 9, height: 9)
                                    .overlay { Circle().strokeBorder(FSDesign.chipForeground("done", dark: scheme == .dark), lineWidth: 1.5) }
                                    .offset(x: min(max(geo.size.width * offsetRatio - 4.5, 0), max(geo.size.width - 9, 0)))
                            }
                        }
                    }
                    .frame(height: 10)
                }
                HStack {
                    Text(startLabel).monospacedDigit()
                    Spacer()
                    Text("\(L10n.t("最佳时刻")) \(bestLabel)")
                        .foregroundStyle(FSDesign.chipForeground("done", dark: scheme == .dark))
                    Spacer()
                    Text(endLabel).monospacedDigit()
                }
                .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
            }
        } else {
            Text(L10n.tf("推荐窗口 %@", windowLabel(report)))
                .font(.caption2).foregroundStyle(.secondary)
        }
    }
}

/// 参数变更提示条（仅在当前页结果参数与表单不一致时显示）。
struct WorkStaleResultBanner: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        if store.resultParametersStale {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.arrow.triangle.2.circlepath")
                    .foregroundStyle(.orange)
                Text(L10n.t("参数已变更 · 当前结果对应旧参数"))
                    .font(.caption.weight(.medium)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                Button(L10n.t("按新参数重算")) { store.beginCheck() }
                    .buttonStyle(.borderless).font(.caption.weight(.medium))
                    .disabled(store.busy || !store.online)
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(Color.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

/// 剖面网格数据（对齐 macOS CrossSectionData）。
private struct CrossSectionData {
    let distances: [Double]
    let levels: [Int]
    let heights: [[Double?]]
    let temps: [[Double?]]
    let rh: [[Double?]]
    let amounts: [[Double?]]
    let presence: [[Double?]]
    let fractions: [[Double?]]
    let familyCodes: [[Int]]
    let statuses: [[Int]]
    let layerBases: [[Double?]]
    let layerTops: [[Double?]]
    let terrain: [Double]
    let requiredASL: [Double]
    let targets: [JSONObject]

    init?(section: JSONObject) {
        let distances = (section["distances_km"] as? [Any])?.compactMap(number) ?? []
        guard !distances.isEmpty else { return nil }
        let fields = section["fields"] as? JSONObject ?? [:]
        let grid: (String) -> [[Double?]] = { key in
            ((fields[key] as? [[Any]]) ?? []).map { (row: [Any]) in
                row.map { ($0 as? NSNumber)?.doubleValue }
            }
        }
        let ints: (String) -> [[Int]] = { key in
            ((fields[key] as? [[Any]]) ?? []).map { (row: [Any]) in
                row.map { ($0 as? NSNumber)?.intValue ?? 0 }
            }
        }
        self.distances = distances
        self.levels = (section["pressure_levels_hpa"] as? [Any])?.compactMap { ($0 as? NSNumber)?.intValue } ?? []
        self.heights = grid("geopotential_height_asl_m")
        self.temps = grid("temperature_c")
        self.rh = grid("rh_percent")
        self.amounts = grid("cloud_amount")
        self.presence = grid("cloud_presence")
        self.fractions = grid("cloud_fraction_percent")
        self.familyCodes = ints("pressure_family_code")
        self.statuses = ints("cloud_layer_status")
        self.layerBases = grid("cloud_layer_base_asl_m")
        self.layerTops = grid("cloud_layer_top_asl_m")
        self.terrain = (section["terrain_elevation_asl_m"] as? [Any])?.compactMap(number) ?? []
        self.requiredASL = (section["required_illumination_base_asl_m"] as? [Any])?.compactMap(number) ?? []
        self.targets = section["theoretical_targets"] as? [JSONObject] ?? []
    }
}

struct WorkCrossSectionView: View {
    let section: JSONObject?
    let genusProxy: JSONObject?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let section {
                CrossSectionFigureView(section: section, genusProxy: genusProxy)
            } else {
                Text(L10n.text("此结果没有可查询的冻结模式剖面。", "No model cross-section available for this result."))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(L10n.text("太阳方向大气剖面", "Sun-direction atmospheric profile"))
    }
}

/// 距离×高度剖面图层组件（与 macOS v1.9.10 CrossSectionFigureView 同构）。
struct CrossSectionFigureView: View {
    let section: JSONObject
    let genusProxy: JSONObject?
    @Environment(\.colorScheme) private var scheme
    @State private var zoomScale = 1.0

    init(section: JSONObject, genusProxy: JSONObject? = nil) {
        self.section = section
        self.genusProxy = genusProxy
    }

    private let plotAspectRatio: CGFloat = 1.95

    private var data: CrossSectionData? { CrossSectionData(section: section) }

    private var canvasAccessibilityLabel: String {
        guard let data else { return L10n.text("大气剖面图不可用", "Atmospheric cross-section unavailable") }
        let low = Int(data.terrain.min() ?? 0)
        let high = Int(data.terrain.max() ?? 0)
        return "大气剖面图，\(data.distances.count) 个距离采样点，\(data.levels.count) 个气压层，地形高度 \(low) 至 \(high) 米，\(data.targets.count) 个目标云层标记"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let data {
                plotControls
                GeometryReader { geometry in
                    let plotWidth = geometry.size.width * CGFloat(zoomScale)
                    ScrollView(.horizontal) {
                        Canvas { context, size in
                            draw(context: context, size: size, data: data, scale: zoomScale)
                        }
                        .frame(width: plotWidth, height: geometry.size.height)
                    }
                    .frame(height: geometry.size.height)
                    .scrollIndicators(zoomScale > 1 ? .automatic : .hidden)
                }
                .aspectRatio(plotAspectRatio / CGFloat(zoomScale), contentMode: .fit)
                .accessibilityLabel(canvasAccessibilityLabel)
                .accessibilityAdjustableAction { direction in
                    switch direction {
                    case .increment: zoomScale = min(2.5, zoomScale + 0.25)
                    case .decrement: zoomScale = max(1, zoomScale - 0.25)
                    @unknown default: break
                    }
                }
                .accessibilityValue("\(Int((zoomScale * 100).rounded()))%")
                legend
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), alignment: .leading)],
                          alignment: .leading, spacing: 8) {
                    diagnosticChip(L10n.text("太阳高度", "Solar elev."), section["solar_elevation_deg"], suffix: "°")
                    diagnosticChip(L10n.text("太阳方位", "Solar azim."), section["solar_azimuth_deg"], suffix: "°")
                    diagnosticChip(L10n.text("剖面范围", "Range"), section["analysis_radius_km"], suffix: " km")
                    diagnosticChip(L10n.text("相对时刻", "Offset"), offsetLabel(section["offset_minutes"]))
                    diagnosticChip(L10n.text("有效时次", "Valid time"), timeLabel(section["valid_time_local"] as? String))
                    diagnosticChip(L10n.text("采样", "Sampling"), section["spatial_sampling"])
                }
                genusSummary
                targetStrip(data.targets)
            } else {
                Text(L10n.text("后端未返回可绘制的剖面字段；可在诊断中查看理论目标与照明基准。",
                               "No drawable cross-section fields returned."))
                    .font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120, alignment: .center)
            }
        }
    }

    private var plotControls: some View {
        HStack(spacing: 6) {
            Text(L10n.text("沿光路高度 · 海拔口径", "Altitude along sun ray · ASL"))
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            Spacer(minLength: 4)
            Text("\(Int((zoomScale * 100).rounded()))%")
                .font(.caption.weight(.medium)).monospacedDigit()
                .foregroundStyle(.secondary)
            Button {
                zoomScale = max(1, zoomScale - 0.25)
            } label: {
                Image(systemName: "minus.magnifyingglass").frame(minWidth: 36, minHeight: 36)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(L10n.text("缩小剖面", "Zoom out cross-section"))
            .disabled(zoomScale <= 1)
            Button {
                zoomScale = min(2.5, zoomScale + 0.25)
            } label: {
                Image(systemName: "plus.magnifyingglass").frame(minWidth: 36, minHeight: 36)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(L10n.text("放大剖面", "Zoom in cross-section"))
            .disabled(zoomScale >= 2.5)
            Button {
                zoomScale = 1
            } label: {
                Image(systemName: "arrow.counterclockwise").frame(minWidth: 36, minHeight: 36)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(L10n.text("适配窗口", "Reset zoom"))
            .disabled(zoomScale == 1)
        }
    }

    @ViewBuilder
    private var genusSummary: some View {
        if let genusProxy {
            let label = genusProxy["label"] as? String ?? "unknown"
            let compatible = (genusProxy["compatible_genera"] as? [String] ?? [])
                .map(genusLabel).joined(separator: "、")
            let candidates = genusCandidates(genusProxy["candidate_scores"])
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "cloud").foregroundStyle(coolAccent)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(L10n.text("云属候选（代理）", "Cloud genus (proxy)")).font(.caption.weight(.semibold))
                        if label != "unknown" {
                            Text(genusLabel(label)).font(.caption.weight(.semibold))
                        } else {
                            Text(compatible.isEmpty ? L10n.text("未定", "Undetermined")
                                 : L10n.text("兼容：\(compatible)", "Compatible: \(compatible)"))
                                .font(.caption.weight(.medium))
                        }
                        Spacer(minLength: 4)
                        if let confidence = genusProxy["confidence"] as? NSNumber,
                           confidence.doubleValue.isFinite {
                            Text("\(Int((confidence.doubleValue * 100).rounded()))%")
                                .font(.caption2.weight(.medium)).monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                    }
                    if let candidates {
                        Text(L10n.text("候选：\(candidates)", "Candidates: \(candidates)"))
                            .font(.caption2).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text(L10n.text("依据压力层、相态与云层几何的模型代理，不代表卫星或人工云属观测。",
                                   "Model proxy from pressure, phase, and geometry; not a satellite or human observation."))
                        .font(.caption2).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 10).padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            .accessibilityElement(children: .combine)
        }
    }

    private func genusCandidates(_ scores: Any?) -> String? {
        guard let scores = scores as? JSONObject else { return nil }
        let ranked = scores.sorted {
            (($0.value as? NSNumber)?.doubleValue ?? -1) > (($1.value as? NSNumber)?.doubleValue ?? -1)
        }
        let labels = ranked.prefix(3).compactMap { key, value -> String? in
            guard let score = value as? NSNumber, score.doubleValue.isFinite else { return nil }
            return "\(genusLabel(key)) \(Int((score.doubleValue * 100).rounded()))%"
        }
        return labels.isEmpty ? nil : labels.joined(separator: " · ")
    }

    // MARK: drawing

    private func draw(context: GraphicsContext, size: CGSize, data: CrossSectionData, scale: Double) {
        let top = 10.0, bottom = size.height - 24
        guard bottom > top + 20 else { return }
        let maxHeight = 18_000.0
        let labelSize = CGFloat(min(14, max(10, 10 * scale)))
        let strokeScale = CGFloat(scale)
        let targetAltitudeStep = maxHeight * 34 / Double(bottom - top)
        let altitudeStep = [1000.0, 1500, 2000, 3000, 5000, 7500, 10_000]
            .first(where: { $0 >= targetAltitudeStep }) ?? maxHeight
        let altitudes = Array(stride(from: 0.0, through: maxHeight, by: altitudeStep))
        func altitudeLabel(_ altitude: Double) -> Text {
            let label = altitude.truncatingRemainder(dividingBy: 1000) == 0
                ? "\(Int(altitude / 1000))km" : String(format: "%.1fkm", altitude / 1000)
            return Text(label).font(.system(size: labelSize)).foregroundColor(.secondary)
        }
        let labelWidth = altitudes.map {
            context.resolve(altitudeLabel($0)).measure(in: size).width
        }.max() ?? 0
        let left = max(40, ceil(labelWidth) + 8), right = size.width - 12
        guard right > left + 20 else { return }
        let maxDistance = max(1, data.distances.last ?? 1)
        let x = { (distance: Double) in left + distance / maxDistance * (right - left) }
        let y = { (height: Double) in top + (1 - min(max(height, 0), maxHeight) / maxHeight) * (bottom - top) }

        // Atmospheric sky gradient backdrop inside the plot frame
        let plotRect = CGRect(x: left, y: top, width: right - left, height: bottom - top)
        let plotPath = Path(roundedRect: plotRect, cornerRadius: 8 * strokeScale)
        let skyGradient = Gradient(colors: scheme == .dark
            ? [Color(red: 0.09, green: 0.13, blue: 0.22).opacity(0.75),
               Color(red: 0.14, green: 0.19, blue: 0.28).opacity(0.45),
               Color(red: 0.28, green: 0.20, blue: 0.19).opacity(0.32)]
            : [Color(red: 0.85, green: 0.91, blue: 0.97).opacity(0.72),
               Color(red: 0.94, green: 0.95, blue: 0.98).opacity(0.52),
               Color(red: 0.99, green: 0.93, blue: 0.87).opacity(0.55)])
        context.fill(
            plotPath,
            with: .linearGradient(skyGradient,
                                  startPoint: CGPoint(x: left, y: top),
                                  endPoint: CGPoint(x: left, y: bottom))
        )
        context.stroke(plotPath, with: .color(Color.secondary.opacity(scheme == .dark ? 0.28 : 0.22)),
                       lineWidth: 0.9 * strokeScale)

        // Subtle golden-amber solar illumination corridor above requiredASL
        if data.requiredASL.count == data.distances.count {
            var sunlit = Path()
            sunlit.move(to: CGPoint(x: left, y: top))
            sunlit.addLine(to: CGPoint(x: right, y: top))
            for index in data.distances.indices.reversed() {
                sunlit.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.requiredASL[index])))
            }
            sunlit.closeSubpath()
            let sunGrad = Gradient(colors: [
                Color(red: 0.98, green: 0.69, blue: 0.30).opacity(scheme == .dark ? 0.03 : 0.04),
                Color(red: 0.96, green: 0.57, blue: 0.22).opacity(scheme == .dark ? 0.14 : 0.13),
            ])
            context.fill(
                sunlit,
                with: .linearGradient(sunGrad,
                                      startPoint: CGPoint(x: left, y: top),
                                      endPoint: CGPoint(x: left, y: bottom))
            )
        }

        for altitude in altitudes {
            let lineY = y(altitude)
            var grid = Path()
            grid.move(to: CGPoint(x: left, y: lineY))
            grid.addLine(to: CGPoint(x: right, y: lineY))
            context.stroke(grid, with: .color(Color.secondary.opacity(0.18)), lineWidth: 0.6 * strokeScale)
            context.draw(altitudeLabel(altitude),
                         at: CGPoint(x: left - 5, y: lineY), anchor: .trailing)
        }
        let step = max(1, data.distances.count / 5)
        for (index, distance) in data.distances.enumerated() where index % step == 0 || index == data.distances.count - 1 {
            let lineX = x(distance)
            var grid = Path()
            grid.move(to: CGPoint(x: lineX, y: top))
            grid.addLine(to: CGPoint(x: lineX, y: bottom))
            context.stroke(grid, with: .color(Color.secondary.opacity(0.14)),
                           style: StrokeStyle(lineWidth: 0.6 * strokeScale, dash: [3 * strokeScale, 4 * strokeScale]))
            context.draw(Text("\(Int(distance))km").font(.system(size: labelSize)).foregroundColor(.secondary),
                         at: CGPoint(x: lineX, y: bottom + 10),
                         anchor: distance == maxDistance ? .trailing : .center)
        }

        // terrain fill (vertical earth-slate gradient) + crisp ridge line + observer badge
        if data.terrain.count == data.distances.count {
            var fill = Path()
            fill.move(to: CGPoint(x: x(data.distances[0]), y: y(data.terrain[0])))
            for index in data.distances.indices.dropFirst() {
                fill.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.terrain[index])))
            }
            fill.addLine(to: CGPoint(x: right, y: bottom))
            fill.addLine(to: CGPoint(x: left, y: bottom))
            fill.closeSubpath()
            let terrainMinY = data.terrain.map { y($0) }.min() ?? bottom
            let terrainGrad = Gradient(colors: scheme == .dark
                ? [Color(red: 0.36, green: 0.42, blue: 0.44).opacity(0.62),
                   Color(red: 0.20, green: 0.24, blue: 0.28).opacity(0.85)]
                : [Color(red: 0.46, green: 0.51, blue: 0.49).opacity(0.38),
                   Color(red: 0.32, green: 0.37, blue: 0.36).opacity(0.58)])
            context.fill(
                fill,
                with: .linearGradient(terrainGrad,
                                      startPoint: CGPoint(x: left, y: terrainMinY),
                                      endPoint: CGPoint(x: left, y: bottom))
            )
            var line = Path()
            line.move(to: CGPoint(x: x(data.distances[0]), y: y(data.terrain[0])))
            for index in data.distances.indices.dropFirst() {
                line.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.terrain[index])))
            }
            context.stroke(line, with: .color(Color(red: 0.41, green: 0.46, blue: 0.45)),
                           lineWidth: 1.8 * strokeScale)
            let observer = CGPoint(x: x(data.distances[0]), y: y(data.terrain[0]))
            let haloSize = 15 * strokeScale
            let halo = CGRect(x: observer.x - haloSize / 2, y: observer.y - haloSize / 2,
                              width: haloSize, height: haloSize)
            context.fill(Path(ellipseIn: halo), with: .color(Color(red: 0.95, green: 0.49, blue: 0.26).opacity(0.24)))
            let dotSize = 9 * strokeScale
            let dot = CGRect(x: observer.x - dotSize / 2, y: observer.y - dotSize / 2,
                             width: dotSize, height: dotSize)
            context.fill(Path(ellipseIn: dot), with: .color(Color(red: 0.95, green: 0.49, blue: 0.26)))
            context.stroke(Path(ellipseIn: dot), with: .color(.white.opacity(0.9)), lineWidth: 1.4 * strokeScale)
            context.draw(Text(L10n.text("观测点", "Observer")).font(.system(size: labelSize, weight: .semibold)).foregroundColor(.primary),
                         at: CGPoint(x: observer.x + dotSize * 1.1, y: observer.y - 9 * strokeScale), anchor: .leading)
        }

        // Rounded layered cloud envelopes with lit burnable undersides
        let sampleCount = Swift.max(1, data.distances.count - 1)
        let columnSpan = (right - left) / Double(sampleCount)
        let cellWidth = Swift.max(6.0, columnSpan * 0.92)
        func scalar(_ grid: [[Double?]], _ sample: Int, _ level: Int) -> Double? {
            guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
            return grid[sample][level]
        }
        func integer(_ grid: [[Int]], _ sample: Int, _ level: Int) -> Int? {
            guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
            return grid[sample][level]
        }
        for i in data.distances.indices {
            for family in 1...3 {
                let cloudyLevels = data.levels.indices.filter { level in
                    guard integer(data.familyCodes, i, level) == family else { return false }
                    let amount = scalar(data.amounts, i, level)
                        ?? max(scalar(data.presence, i, level) ?? 0,
                               (scalar(data.fractions, i, level) ?? 0) / 100)
                    return amount >= 0.06
                }
                guard !cloudyLevels.isEmpty else { continue }

                let bases = cloudyLevels.compactMap { scalar(data.layerBases, i, $0) }
                let tops = cloudyLevels.compactMap { scalar(data.layerTops, i, $0) }
                let heights = cloudyLevels.compactMap { scalar(data.heights, i, $0) }
                let layerBase = bases.min() ?? heights.min().map { $0 - 700 }
                let layerTop = tops.max() ?? heights.max().map { $0 + 700 }
                guard let layerBase, let layerTop, layerTop > layerBase else { continue }

                let amounts = cloudyLevels.compactMap {
                    scalar(data.amounts, i, $0)
                        ?? max(scalar(data.presence, i, $0) ?? 0,
                               (scalar(data.fractions, i, $0) ?? 0) / 100)
                }
                let presence = cloudyLevels.compactMap { scalar(data.presence, i, $0) }.max()
                let humidity = cloudyLevels.compactMap { scalar(data.rh, i, $0) }.max()
                let fraction = cloudyLevels.compactMap { scalar(data.fractions, i, $0) }.max()
                let status = cloudyLevels.compactMap { integer(data.statuses, i, $0) }.max() ?? 0

                let centerX = x(data.distances[i])
                let rectLeft = max(left, centerX - cellWidth / 2)
                let rectRight = min(right, centerX + cellWidth / 2)
                let terrainY = data.terrain.indices.contains(i) ? y(data.terrain[i]) : bottom
                let rectTop = max(top, y(layerTop))
                let rectBottom = min(terrainY, y(layerBase))
                guard rectRight > rectLeft, rectBottom > rectTop else { continue }
                let rect = CGRect(x: rectLeft, y: rectTop,
                                  width: rectRight - rectLeft, height: rectBottom - rectTop)
                let radius = min(4.5 * strokeScale, min(rect.width, rect.height) * 0.35)
                let shape = Path(roundedRect: rect, cornerRadius: radius)
                if let fill = cloudCellFill(amount: amounts.max(), presence: presence,
                                            rh: humidity, fraction: fraction, family: family) {
                    let topColor = fill.opacity(0.82)
                    let bottomColor = status == 3
                        ? Color(red: 0.97, green: 0.52, blue: 0.28).opacity(0.62)
                        : fill
                    context.fill(
                        shape,
                        with: .linearGradient(Gradient(colors: [topColor, bottomColor]),
                                              startPoint: CGPoint(x: rect.midX, y: rect.minY),
                                              endPoint: CGPoint(x: rect.midX, y: rect.maxY))
                    )
                }
                if status == 3 {
                    context.stroke(shape, with: .color(Color(red: 0.96, green: 0.43, blue: 0.20)),
                                   lineWidth: 1.9 * strokeScale)
                } else if status == 2 {
                    context.stroke(shape, with: .color(Color(red: 0.85, green: 0.56, blue: 0.22)),
                                   style: StrokeStyle(lineWidth: 1.4 * strokeScale,
                                                      dash: [4 * strokeScale, 3 * strokeScale]))
                }
            }
        }

        // temperature layer lines
        for l in data.levels.indices {
            var points: [CGPoint] = []
            var temps: [Double] = []
            for i in data.distances.indices {
                guard data.heights.indices.contains(i), data.heights[i].indices.contains(l),
                      let height = data.heights[i][l] else { continue }
                points.append(CGPoint(x: x(data.distances[i]), y: y(height)))
                if data.temps.indices.contains(i), data.temps[i].indices.contains(l),
                   let temp = data.temps[i][l] {
                    temps.append(temp)
                }
            }
            guard points.count > 1 else { continue }
            var line = Path()
            line.move(to: points[0])
            points.dropFirst().forEach { line.addLine(to: $0) }
            let average = temps.isEmpty ? 0 : temps.reduce(0, +) / Double(temps.count)
            let color = average < -30 ? Color(red: 0.55, green: 0.40, blue: 0.45)
                : average < 0 ? Color(red: 0.69, green: 0.42, blue: 0.36)
                : Color(red: 0.78, green: 0.31, blue: 0.22)
            context.stroke(line, with: .color(color.opacity(0.62)), lineWidth: 1.1 * strokeScale)
            if l % 2 == 0, l < data.levels.count, let last = points.last {
                context.draw(Text("\(data.levels[l])hPa").font(.system(size: labelSize)).foregroundColor(.secondary),
                             at: CGPoint(x: last.x - 5 * strokeScale, y: last.y - 6 * strokeScale),
                             anchor: .trailing)
            }
        }

        // required illumination threshold (ASL), dashed amber
        if data.requiredASL.count == data.distances.count {
            var line = Path()
            line.move(to: CGPoint(x: x(data.distances[0]), y: y(data.requiredASL[0])))
            for index in data.distances.indices.dropFirst() {
                line.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.requiredASL[index])))
            }
            context.stroke(line, with: .color(Color(red: 0.83, green: 0.55, blue: 0.22)),
                           style: StrokeStyle(lineWidth: 2.2 * strokeScale,
                                              dash: [7 * strokeScale, 5 * strokeScale]))
        }

        // theoretical target cloud base / top markers
        for target in data.targets {
            guard let distance = number(target["distance_km"]),
                  let base = number(target["cloud_base_asl_m"]) else { continue }
            let basePoint = CGPoint(x: x(distance), y: y(base))
            let markerSize = 8 * strokeScale
            context.fill(Path(ellipseIn: CGRect(x: basePoint.x - markerSize / 2,
                                                y: basePoint.y - markerSize / 2,
                                                width: markerSize, height: markerSize)),
                         with: .color(warm))
            if let top = number(target["cloud_top_asl_m"]) {
                let topPoint = CGPoint(x: x(distance), y: y(top))
                context.fill(Path(ellipseIn: CGRect(x: topPoint.x - markerSize / 2,
                                                    y: topPoint.y - markerSize / 2,
                                                    width: markerSize, height: markerSize)),
                             with: .color(coolAccent))
            }
        }
    }

    private func cloudCellFill(amount: Double?, presence: Double?, rh: Double?,
                               fraction: Double?, family: Int) -> Color? {
        let cloudAmount = amount ?? Swift.max(presence ?? 0, (fraction ?? 0) / 100)
        let humidity = Swift.max(0, (rh ?? 0) - 55) / 45
        if cloudAmount < 0.06 {
            if let fraction, fraction <= 0 { return nil }
            if humidity < 0.06 { return nil }
            return Color(red: 112 / 255, green: 151 / 255, blue: 126 / 255)
                .opacity(min(0.34, 0.04 + humidity * 0.26))
        }
        let alpha = min(0.82, 0.10 + cloudAmount * 0.68)
        switch family {
        case 1: return Color(red: 116 / 255, green: 157 / 255, blue: 194 / 255).opacity(alpha)
        case 2: return Color(red: 105 / 255, green: 154 / 255, blue: 142 / 255).opacity(alpha)
        default: return Color(red: 126 / 255, green: 136 / 255, blue: 145 / 255).opacity(alpha)
        }
    }

    private func offsetLabel(_ value: Any?) -> String {
        guard let value = value as? NSNumber, value.doubleValue.isFinite else { return "—" }
        let minutes = Int(value.doubleValue)
        return "\(minutes > 0 ? "+" : "")\(minutes) 分"
    }

    private var legend: some View {
        VStack(alignment: .leading, spacing: 5) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), alignment: .leading)],
                      alignment: .leading, spacing: 6) {
                legendChip(L10n.t("高云压力族"), Color(red: 116 / 255, green: 157 / 255, blue: 194 / 255))
                legendChip(L10n.t("中云压力族"), Color(red: 105 / 255, green: 154 / 255, blue: 142 / 255))
                legendChip(L10n.t("低云压力族"), Color(red: 126 / 255, green: 136 / 255, blue: 145 / 255))
                legendChip(L10n.text("湿润未饱和", "Moist unsaturated"), Color(red: 112 / 255, green: 151 / 255, blue: 126 / 255))
                legendLine(L10n.text("温度层线", "Isotherms"), Color(red: 0.69, green: 0.42, blue: 0.36), dashed: false)
                legendLine(L10n.text("地形", "Terrain"), Color(red: 0.41, green: 0.45, blue: 0.44), dashed: false)
                legendDot(L10n.text("云底", "Cloud base"), warm)
                legendDot(L10n.text("云顶", "Cloud top"), coolAccent)
                legendLine(L10n.text("理论照明基准（ASL）", "Illumination base (ASL)"), Color(red: 0.83, green: 0.55, blue: 0.22), dashed: true)
                legendFrame(L10n.text("受光且可见候选", "Lit visible candidate"), Color(red: 0.94, green: 0.42, blue: 0.21), dashed: false)
                legendFrame(L10n.text("受光但不可见", "Lit but obscured"), Color(red: 0.83, green: 0.55, blue: 0.22), dashed: true)
            }
            Text(L10n.text("图层数值为对应模式各自的场；未着色不代表晴空，橙色状态未评估云体遮挡。",
                           "Field values reflect each model; unshaded cells are not necessarily clear sky."))
                .font(.caption2).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.caption2).foregroundStyle(.secondary)
    }

    private func legendChip(_ title: String, _ color: Color) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(color).frame(width: 12, height: 7).cornerRadius(1.5)
            Text(title)
        }
    }

    private func legendLine(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(Color.clear).frame(width: 12, height: 0)
                .overlay { Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.6, dash: dashed ? [4, 3] : [])) }
                .frame(height: 7)
            Text(title)
        }
    }

    private func legendDot(_ title: String, _ color: Color) -> some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(title)
        }
    }

    private func legendFrame(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(Color.clear)
                .overlay { Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.4, dash: dashed ? [4, 3] : [])) }
                .frame(width: 11, height: 7)
            Text(title)
        }
    }

    private func targetStrip(_ targets: [JSONObject]) -> some View {
        let burnable = targets.filter { $0["status"] as? String == "burnable" }
        return Group {
            if !targets.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L10n.text("受光与几何/地形可见候选（未评估云遮挡）", "Lit & terrain-visible candidates"))
                        .font(.caption.weight(.medium)).foregroundStyle(.primary)
                    if !burnable.isEmpty {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), spacing: 6)], spacing: 6) {
                            ForEach(Array(burnable.prefix(8).enumerated()), id: \.offset) { _, target in
                                targetChip(target)
                            }
                        }
                    } else if let diagnostic = targets.first(where: { $0["status"] as? String != "burnable" }) {
                        Text("当前太阳方向没有可烧目标云：\(targetFamily(diagnostic))，\(diagnostic["status_reason"] as? String ?? "状态原因未提供")")
                            .font(.caption2).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func targetChip(_ target: JSONObject) -> some View {
        let family = targetFamily(target)
        let phase = (target["cloud_phase"] as? JSONObject)?["label"] as? String ?? "unknown"
        let phaseText = phaseLabel(phase)
        let distance = number(target["distance_km"]).map { String(format: "%.0f", $0) } ?? "—"
        let base = number(target["cloud_base_asl_m"]).map { String(format: "%.1f", $0 / 1000) } ?? "—"
        let top = number(target["cloud_top_asl_m"]).map { String(format: "%.1f", $0 / 1000) } ?? "—"
        let text = "\(family) · \(phaseText) · \(distance) km · ASL \(base)–\(top) km"
        return Text(text)
            .font(.caption2).monospacedDigit()
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Color(red: 0.94, green: 0.42, blue: 0.21).opacity(0.08),
                        in: RoundedRectangle(cornerRadius: 6))
            .overlay { RoundedRectangle(cornerRadius: 6).stroke(Color(red: 0.94, green: 0.42, blue: 0.21).opacity(0.42), lineWidth: 1) }
    }

    private func targetFamily(_ target: JSONObject) -> String {
        switch target["pressure_family"] as? String {
        case "high": return L10n.t("高云压力族")
        case "middle": return L10n.t("中云压力族")
        case "low": return L10n.t("低云压力族")
        default: return target["pressure_family_label"] as? String ?? L10n.text("云层", "Cloud layer")
        }
    }

    private func diagnosticChip(_ title: String, _ value: Any?, suffix: String = "") -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(title == L10n.text("有效时次", "Valid time") ? (value as? String ?? "—")
                 : diagnosticValue(value, digits: 1, suffix: suffix))
                .font(.caption.weight(.medium)).monospacedDigit()
        }
    }
}

struct WorkGenusPhaseDiagnosis: View {
    let report: JSONObject

    var body: some View {
        WorkDiagnosticGroup(title: L10n.text("云属与相态（模型代理 · 不参与评分）", "Cloud Genus & Phase (Model Proxy)")) {
            if let genus = report["cloud_genus_proxy"] as? JSONObject {
                let label = genus["label"] as? String ?? "unknown"
                if label != "unknown" {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(L10n.text("云属代理", "Genus proxy")).foregroundStyle(.secondary)
                        Spacer(minLength: 10)
                        Text(genusLabel(label)).multilineTextAlignment(.trailing)
                        Text(percent(genus["confidence"])).monospacedDigit().foregroundStyle(.secondary)
                    }.font(.caption)
                    if let candidates = topCandidates(genus["candidate_scores"], limit: 3) {
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text(L10n.text("候选 Top3", "Top candidates")).foregroundStyle(.secondary)
                            Spacer(minLength: 10)
                            Text(candidates).multilineTextAlignment(.trailing).lineLimit(2)
                        }.font(.caption)
                    }
                } else if let compatible = compatibleGenera(report) {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(L10n.text("云属未定", "Genus undetermined")).foregroundStyle(.secondary)
                        Spacer(minLength: 10)
                        Text(L10n.text("压力族仅兼容 \(compatible)", "Compatible: \(compatible)"))
                            .multilineTextAlignment(.trailing).lineLimit(2)
                    }.font(.caption)
                }
                diagnosticRow(L10n.text("推断依据", "Proxy basis"), genus["source"])
                diagnosticRow(L10n.text("评分影响", "Scoring effect"),
                              (genus["scoring_effect"] as? Bool) == false
                              ? L10n.text("不参与评分（非卫星/人工观测）", "Not scored (not observation)")
                              : "—")
            } else {
                diagnosticRow(L10n.text("云属代理", "Genus proxy"), "—")
            }
            phaseRows(report["cloud_phase_proxy"])
        }
    }

    @ViewBuilder
    private func phaseRows(_ phase: Any?) -> some View {
        if let phase = phase as? JSONObject {
            let label = phase["label"] as? String ?? "unknown"
            if label == "unknown", phase["source"] as? String == "not-resolved-for-native-burnable-target" {
                diagnosticRow(L10n.text("相态", "Phase"), L10n.text("未解析（点预测不解析目标云相态）", "Not resolved"))
            } else {
                let ice = phase["ice_fraction"] as? NSNumber
                let iceText = (ice != nil && ice!.doubleValue.isFinite)
                    ? " · \(L10n.text("冰含量占比", "Ice")) \(Int((ice!.doubleValue * 100).rounded()))%" : ""
                diagnosticRow(L10n.text("相态", "Phase"), "\(phaseLabel(label))\(iceText)")
                diagnosticRow(L10n.text("相态置信度", "Phase confidence"), phase["confidence"], percentValue: true)
            }
        } else if phase == nil {
            diagnosticRow(L10n.text("相态", "Phase"), "—")
        } else {
            diagnosticRow(L10n.text("相态", "Phase"), phase)
        }
    }

    private func topCandidates(_ scores: Any?, limit: Int) -> String? {
        guard let scores = scores as? JSONObject, !scores.isEmpty else { return nil }
        let ranked = scores.sorted { ($0.value as? NSNumber)?.doubleValue ?? -1 > ($1.value as? NSNumber)?.doubleValue ?? -1 }
        let top = ranked.prefix(limit).compactMap { key, value -> String? in
            guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
            return "\(genusLabel(key)) \(Int((number.doubleValue * 100).rounded()))%"
        }
        return top.isEmpty ? nil : top.joined(separator: " · ")
    }

    private func compatibleGenera(_ report: JSONObject) -> String? {
        guard let diagnosis = report["dominant_burnable_cloud_genus_diagnosis"] as? JSONObject,
              let genera = diagnosis["compatible_genera"] as? [String], !genera.isEmpty else { return nil }
        return genera.map(genusLabel).joined(separator: " / ")
    }
}

struct WorkSharedDiagnosis: View {
    let report: JSONObject

    var body: some View {
        if let evolution = report["cloud_evolution"] as? JSONObject {
            WorkDiagnosticGroup(title: L10n.text("云体演化", "Cloud evolution")) {
                diagnosticRow(L10n.text("趋势", "Trend"), evolution["trend"])
                diagnosticRow(L10n.text("目标分数变化", "Target score delta"), evolution["target_score_change"], digits: 3)
                diagnosticRow(L10n.text("云隙闭合风险", "Cloud hole risk"), evolution["cloud_hole_closure_risk"], percentValue: true)
                diagnosticRow(L10n.text("比较起点", "Start"), evolution["comparison_start_local"])
                diagnosticRow(L10n.text("比较终点", "End"), evolution["comparison_end_local"])
            }
        }
        if let color = report["color_diagnosis"] as? JSONObject, !color.isEmpty {
            WorkDiagnosticGroup(title: L10n.text("色彩诊断", "Color diagnosis")) {
                diagnosticRow(L10n.text("色彩类型", "Color type"), color["type"])
                diagnosticRow(L10n.text("诊断支持度", "Support"), color["confidence"], percentValue: true)
                diagnosticRow(L10n.text("云相", "Cloud phase"), color["cloud_phase"])
                diagnosticRow(L10n.text("诊断来源", "Source"), color["source"])
            }
        }
        if let roles = report["source_roles"] as? JSONObject {
            WorkDiagnosticGroup(title: L10n.text("数据角色", "Source roles")) {
                diagnosticRow(L10n.text("主要来源", "Primary"), roles["primary"])
                diagnosticRow(L10n.text("辅助来源", "Auxiliary"), roles["auxiliary"])
                if let provenance = report["data_provenance"] as? JSONObject {
                    diagnosticRow(L10n.text("本次使用观测", "Observation"), provenance["observation_used"])
                }
            }
        }
    }
}

struct WorkDiagnosticGroup<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 6, content: content)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.18), in: RoundedRectangle(cornerRadius: 8))
    }
}

func diagnosticRow(_ title: String, _ value: Any?, digits: Int = 2,
                   suffix: String = "", percentValue: Bool = false) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 10) {
        Text(title).foregroundStyle(.secondary)
        Spacer(minLength: 10)
        Text(diagnosticValue(value, digits: digits, suffix: suffix, percentValue: percentValue))
            .multilineTextAlignment(.trailing)
            .textSelection(.enabled)
            .lineLimit(2)
    }.font(.caption)
}
