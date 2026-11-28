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

func modelSourceNote(_ model: String, usesGeosFP: Bool = false) -> String {
    if usesGeosFP {
        return model == "ec"
            ? L10n.text("EC 公共 Open Data · TCC + 压力层 RH 云柱代理 · GEOS-FP 气溶胶",
                        "EC public Open Data · TCC + pressure-level RH proxy · GEOS-FP aerosol")
            : L10n.text("GFS 0.25° 全量云场评分 · GEOS-FP 气溶胶",
                        "GFS 0.25° full cloud-field scoring · GEOS-FP aerosol")
    }
    return model == "ec"
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

/// 距离×高度剖面图层组件（v3 平滑大气剖面：暮光天空、地影区与受光走廊、平滑分层云廓与火烧云受光云底、
/// 可选等压线、平滑地形、观测点视线、触摸交互游标与一图看懂横幅）。
struct CrossSectionFigureView: View {
    let section: JSONObject
    let genusProxy: JSONObject?
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var zoomScale = 1.0
    @State private var showIsobars = false
    @State private var scrubDistanceKM: Double? = nil

    init(section: JSONObject, genusProxy: JSONObject? = nil) {
        self.section = section
        self.genusProxy = genusProxy
    }

    private var plotAspectRatio: CGFloat { sizeClass == .compact ? 1.95 : 2.45 }
    private let plotLeftMargin: Double = 40
    private let plotTopMargin: Double = 14
    private let plotBottomOffset: Double = 24
    private var plotRightMargin: Double { showIsobars ? 46 : 14 }

    private var data: CrossSectionData? { CrossSectionData(section: section) }

    private struct ColumnStratum {
        let baseASL: Double
        let topASL: Double
        let amount: Double
        let status: Int
    }

    private struct CloudRunPoint {
        let sampleIndex: Int
        let cx: Double
        let topY: Double
        let bottomY: Double
        let stratum: ColumnStratum
    }

    private struct BurnableTargetFocus {
        let distanceKM: Double
        let baseASL: Double
    }

    private struct TakeawaySummary {
        let text: String
        let status: Int // 3 = lit & visible, 2 = occluded, 0 = none
    }

    private var canvasAccessibilityLabel: String {
        guard let data else { return L10n.text("大气剖面图不可用", "Atmospheric cross-section unavailable") }
        let low = Int(data.terrain.min() ?? 0)
        let high = Int(data.terrain.max() ?? 0)
        return L10n.text(
            "大气剖面图，\(data.distances.count) 个距离采样点，\(data.levels.count) 个气压层，地形高度 \(low) 至 \(high) 米，\(data.targets.count) 个目标云层标记",
            "Atmospheric cross-section, \(data.distances.count) distance samples, \(data.levels.count) pressure levels, terrain \(low)–\(high) m, \(data.targets.count) target markers"
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let data {
                takeawayBanner(for: data)
                plotControls
                GeometryReader { geometry in
                    let plotWidth = geometry.size.width * CGFloat(zoomScale)
                    let plotHeight = geometry.size.height
                    ZStack(alignment: .topLeading) {
                        if zoomScale <= 1.01 {
                            plotCanvas(data: data, width: plotWidth, height: plotHeight)
                                .gesture(
                                    DragGesture(minimumDistance: 0)
                                        .onChanged { value in
                                            updateScrub(at: value.location, data: data, width: Double(plotWidth), height: Double(plotHeight))
                                        }
                                )
                        } else {
                            ScrollView(.horizontal) {
                                plotCanvas(data: data, width: plotWidth, height: plotHeight)
                                    .gesture(
                                        SpatialTapGesture()
                                            .onEnded { value in
                                                updateScrub(at: value.location, data: data, width: Double(plotWidth), height: Double(plotHeight))
                                            }
                                    )
                                    .simultaneousGesture(
                                        LongPressGesture(minimumDuration: 0.15)
                                            .sequenced(before: DragGesture(minimumDistance: 0))
                                            .onChanged { phase in
                                                if case .second(true, let drag?) = phase {
                                                    updateScrub(at: drag.location, data: data, width: Double(plotWidth), height: Double(plotHeight))
                                                }
                                            }
                                    )
                            }
                            .frame(height: plotHeight)
                            .scrollIndicators(.automatic)
                        }

                        if let scrubDistance = scrubDistanceKM {
                            scrubOverlayCard(data: data, distanceKM: scrubDistance)
                                .padding(.leading, plotLeftMargin + 8)
                                .padding(.top, plotTopMargin + 8)
                        }
                    }
                }
                .aspectRatio(plotAspectRatio / CGFloat(zoomScale), contentMode: .fit)
                .accessibilityLabel(canvasAccessibilityLabel)
                .accessibilityHint(L10n.text("点击或拖动查看沿程云层读数，使用缩放按钮查看细节。",
                                             "Tap or drag to inspect clouds along the path; use zoom buttons for detail."))
                .accessibilityAdjustableAction { direction in
                    switch direction {
                    case .increment: zoomScale = min(2.5, zoomScale + 0.25)
                    case .decrement: zoomScale = max(1, zoomScale - 0.25)
                    @unknown default: break
                    }
                }
                .accessibilityValue("\(Int((zoomScale * 100).rounded()))%")
                legend
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

    private func plotCanvas(data: CrossSectionData, width: CGFloat, height: CGFloat) -> some View {
        Canvas { context, size in
            draw(context: context,
                 size: size,
                 data: data,
                 scale: zoomScale,
                 showIsobars: showIsobars,
                 scrubDistanceKM: scrubDistanceKM)
        }
        .frame(width: width, height: height)
        .contentShape(Rectangle())
    }

    private func updateScrub(at location: CGPoint, data: CrossSectionData, width: Double, height: Double) {
        let left = plotLeftMargin
        let right = width - plotRightMargin
        let top = plotTopMargin
        let bottom = height - plotBottomOffset
        guard right > left + 10,
              Double(location.x) >= left, Double(location.x) <= right,
              Double(location.y) >= top, Double(location.y) <= bottom else {
            return
        }
        let ratio = Swift.min(1.0, Swift.max(0.0, (Double(location.x) - left) / (right - left)))
        let maxDist = Swift.max(1.0, data.distances.last ?? 1.0)
        scrubDistanceKM = ratio * maxDist
    }

    // MARK: - takeaway & controls

    private func takeawayBanner(for data: CrossSectionData) -> some View {
        let summary = buildTakeaway(for: data)
        let accent: Color = summary.status == 3
            ? Color(red: 0.96, green: 0.42, blue: 0.18)
            : summary.status == 2
                ? Color(red: 0.86, green: 0.56, blue: 0.20)
                : .secondary
        let iconName = summary.status == 3 ? "sun.horizon.fill"
            : summary.status == 2 ? "cloud.sun" : "sun.min"
        return HStack(spacing: 6) {
            Image(systemName: iconName)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(accent)
            Text(L10n.t("一图看懂"))
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(accent)
            Text("·")
                .foregroundStyle(.tertiary)
            Text(summary.text)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.primary)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(
            accent.opacity(summary.status == 0 ? 0.06 : (scheme == .dark ? 0.14 : 0.09)),
            in: RoundedRectangle(cornerRadius: 7)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 7)
                .stroke(accent.opacity(summary.status == 0 ? 0.18 : 0.36), lineWidth: 0.9)
        }
        .accessibilityElement(children: .combine)
    }

    private func buildTakeaway(for data: CrossSectionData) -> TakeawaySummary {
        let azText = number(section["solar_azimuth_deg"]).map { String(format: "%.0f°", $0) } ?? "—"
        let elText = number(section["solar_elevation_deg"]).map { String(format: "%.1f°", $0) } ?? "—"
        let solarPrefix = L10n.text("太阳方位 \(azText) · 仰角 \(elText)", "Sun Az \(azText) · El \(elText)")

        var litDistances: [Double] = []
        var litBasesM: [Double] = []
        var litFamilies = Set<Int>()
        var hasOccluded = false

        for i in data.distances.indices {
            for family in 1...3 {
                guard let stratum = columnStratum(data: data, sample: i, family: family) else { continue }
                if stratum.status == 3 {
                    litDistances.append(data.distances[i])
                    litBasesM.append(stratum.baseASL)
                    litFamilies.insert(family)
                } else if stratum.status == 2 {
                    hasOccluded = true
                }
            }
        }

        if !litDistances.isEmpty,
           let minDist = litDistances.min(), let maxDist = litDistances.max(),
           let minBase = litBasesM.min(), let maxBase = litBasesM.max() {
            let familyNamesZh = [3, 2, 1].filter { litFamilies.contains($0) }.map { familyShortZh($0) }.joined(separator: "/")
            let familyNamesEn = [3, 2, 1].filter { litFamilies.contains($0) }.map { familyShortEn($0) }.joined(separator: "/")
            let distText = abs(maxDist - minDist) < 1
                ? String(format: "%.0f", minDist)
                : String(format: "%.0f–%.0f", minDist, maxDist)
            let baseText = abs(maxBase - minBase) < 150
                ? String(format: "%.1f", minBase / 1000)
                : String(format: "%.1f–%.1f", minBase / 1000, maxBase / 1000)
            let detail = L10n.text(
                "约 \(distText) km 处\(familyNamesZh)云底（\(baseText) km）进入受光区且通视",
                "\(familyNamesEn) cloud base at ~\(distText) km (\(baseText) km ASL) is sunlit & visible"
            )
            return TakeawaySummary(text: "\(solarPrefix) · \(detail)", status: 3)
        } else if hasOccluded {
            let detail = L10n.text(
                "远场云层达到受光高度，但观测视线受前段云层或地形遮挡",
                "Far-field clouds reach sunlit altitude, but sightline is occluded by foreground clouds or terrain"
            )
            return TakeawaySummary(text: "\(solarPrefix) · \(detail)", status: 2)
        } else {
            let detail = L10n.text(
                "沿太阳光路暂无同时满足受光高度与通视条件的云层",
                "No cloud layers along the solar path meet both sunlit and line-of-sight criteria"
            )
            return TakeawaySummary(text: "\(solarPrefix) · \(detail)", status: 0)
        }
    }

    private var plotControls: some View {
        let elevation = diagnosticValue(section["solar_elevation_deg"], digits: 1, suffix: "°")
        let azimuth = diagnosticValue(section["solar_azimuth_deg"], digits: 1, suffix: "°")
        let radius = diagnosticValue(section["analysis_radius_km"], digits: 1, suffix: " km")
        let validTime = timeLabel(section["valid_time_local"] as? String)
        let pills = HStack(spacing: 6) {
            telemetryPill(L10n.t("仰角"), elevation)
            telemetryPill(L10n.t("方位"), azimuth)
            telemetryPill(L10n.t("范围"), radius)
            telemetryPill(L10n.t("时次"), validTime)
        }
        let controls = HStack(spacing: 8) {
            isobarToggleButton
            zoomControlButtons
        }
        return ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) {
                pills
                Spacer(minLength: 6)
                controls
            }
            VStack(alignment: .leading, spacing: 6) {
                ScrollView(.horizontal, showsIndicators: false) {
                    pills
                }
                HStack(spacing: 8) {
                    isobarToggleButton
                    Spacer(minLength: 4)
                    zoomControlButtons
                }
            }
        }
    }

    private var isobarToggleButton: some View {
        Button {
            showIsobars.toggle()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "waveform.path")
                    .font(.system(size: 10, weight: .medium))
                Text(L10n.t("等压线"))
                    .font(.system(size: 10.5, weight: .medium))
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .foregroundStyle(showIsobars ? coolAccent : Color.secondary)
            .background(
                showIsobars ? coolAccent.opacity(scheme == .dark ? 0.22 : 0.14) : Color.secondary.opacity(0.08),
                in: Capsule()
            )
            .overlay {
                Capsule().stroke(
                    showIsobars ? coolAccent.opacity(0.50) : Color.secondary.opacity(0.22),
                    lineWidth: 0.9
                )
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(L10n.t("等压线"))
        .accessibilityValue(showIsobars ? L10n.text("已开启", "On") : L10n.text("已关闭", "Off"))
    }

    private var zoomControlButtons: some View {
        HStack(spacing: 2) {
            Text("\(Int((zoomScale * 100).rounded()))%")
                .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 36, alignment: .trailing)
                .accessibilityLabel(L10n.text("剖面缩放", "Cross-section zoom"))
                .accessibilityValue("\(Int((zoomScale * 100).rounded()))%")
            Button {
                zoomScale = Swift.max(1, zoomScale - 0.25)
            } label: {
                Image(systemName: "minus.magnifyingglass").frame(width: 28, height: 28)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(L10n.text("缩小剖面", "Zoom out cross-section"))
            .disabled(zoomScale <= 1)
            Button {
                zoomScale = Swift.min(2.5, zoomScale + 0.25)
            } label: {
                Image(systemName: "plus.magnifyingglass").frame(width: 28, height: 28)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(L10n.text("放大剖面", "Zoom in cross-section"))
            .disabled(zoomScale >= 2.5)
            Button {
                zoomScale = 1
            } label: {
                Image(systemName: "arrow.counterclockwise").frame(width: 28, height: 28)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(L10n.text("适配窗口", "Reset zoom"))
            .disabled(zoomScale == 1)
        }
    }

    private func telemetryPill(_ label: String, _ value: String) -> some View {
        HStack(spacing: 3) {
            Text(label)
                .font(.system(size: 9.5))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(size: 10, weight: .medium))
                .monospacedDigit()
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3.5)
        .background(.quaternary.opacity(0.40), in: Capsule())
        .overlay { Capsule().stroke(.secondary.opacity(0.18), lineWidth: 0.7) }
        .lineLimit(1)
    }

    @ViewBuilder
    private func scrubOverlayCard(data: CrossSectionData, distanceKM: Double) -> some View {
        if let nearestIndex = data.distances.indices.min(by: {
            abs(data.distances[$0] - distanceKM) < abs(data.distances[$1] - distanceKM)
        }) {
            let sampleDist = data.distances[nearestIndex]
            let terrainText = data.terrain.indices.contains(nearestIndex)
                ? String(format: "%.0f m", data.terrain[nearestIndex]) : "—"
            let reqText = data.requiredASL.indices.contains(nearestIndex)
                ? String(format: "%.1f km", data.requiredASL[nearestIndex] / 1000) : "—"
            let presentStrata: [(Int, ColumnStratum)] = [1, 2, 3].compactMap { fam in
                guard let s = columnStratum(data: data, sample: nearestIndex, family: fam) else { return nil }
                return (fam, s)
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(String(format: "%.0f km", sampleDist))
                        .font(.system(size: 10.5, weight: .semibold))
                        .monospacedDigit()
                    Text("\(L10n.t("地形")) \(terrainText)")
                        .font(.system(size: 9.5))
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                    Text("\(L10n.t("受光基准")) \(reqText)")
                        .font(.system(size: 9.5))
                        .foregroundStyle(Color(red: 0.90, green: 0.58, blue: 0.20))
                        .monospacedDigit()
                    Button {
                        scrubDistanceKM = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
                if presentStrata.isEmpty {
                    Text(L10n.text("该列无显著模式云层（缺测或低于阈值）", "No significant clouds (missing or below threshold)"))
                        .font(.system(size: 9.5))
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(presentStrata, id: \.0) { fam, stratum in
                        let statusText = stratum.status == 3
                            ? L10n.text("受光可烧", "Sunlit & visible")
                            : stratum.status == 2
                                ? L10n.text("达到受光但被遮挡", "Lit but occluded")
                                : L10n.text("地影区内", "In Earth shadow")
                        let statusColor: Color = stratum.status == 3
                            ? Color(red: 0.96, green: 0.42, blue: 0.18)
                            : stratum.status == 2
                                ? Color(red: 0.88, green: 0.58, blue: 0.20)
                                : .secondary
                        HStack(spacing: 5) {
                            Circle().fill(statusColor).frame(width: 5.5, height: 5.5)
                            Text("\(L10n.effective == .english ? familyShortEn(fam) : familyShortZh(fam)) \(String(format: "%.1f–%.1f km", stratum.baseASL / 1000, stratum.topASL / 1000))")
                                .font(.system(size: 9.5, weight: .medium))
                                .monospacedDigit()
                            Text("· \(statusText)")
                                .font(.system(size: 9.5))
                                .foregroundStyle(statusColor)
                        }
                    }
                }
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 6)
            .fireSkySurface(role: .overlay, cornerRadius: 8)
        }
    }

    @ViewBuilder
    private var genusSummary: some View {
        if let genusProxy {
            let label = genusProxy["label"] as? String ?? "unknown"
            let compatible = (genusProxy["compatible_genera"] as? [String] ?? [])
                .map(genusLabel).joined(separator: "、")
            let candidates = genusCandidates(genusProxy["candidate_scores"])
            let confText: String = {
                guard let confidence = genusProxy["confidence"] as? NSNumber,
                      confidence.doubleValue.isFinite else { return "" }
                return " \(Int((confidence.doubleValue * 100).rounded()))%"
            }()
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: "cloud").foregroundStyle(coolAccent).font(.system(size: 10.5))
                Text(L10n.t("云属代理")).font(.system(size: 10, weight: .semibold))
                if label != "unknown" {
                    Text("\(genusLabel(label))\(confText)").font(.system(size: 10, weight: .semibold))
                } else {
                    Text(compatible.isEmpty ? L10n.text("未定", "Undetermined") : L10n.text("兼容：\(compatible)", "Compatible: \(compatible)"))
                        .font(.system(size: 10, weight: .medium))
                }
                if let candidates {
                    Text("· \(L10n.text("候选", "Candidates"))：\(candidates)")
                        .font(.system(size: 9.5))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.055), in: RoundedRectangle(cornerRadius: 6))
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

    // MARK: - stratum & geometry helpers

    private func scalar(_ grid: [[Double?]], _ sample: Int, _ level: Int) -> Double? {
        guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
        return grid[sample][level]
    }

    private func integer(_ grid: [[Int]], _ sample: Int, _ level: Int) -> Int? {
        guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
        return grid[sample][level]
    }

    private func columnStratum(data: CrossSectionData, sample: Int, family: Int) -> ColumnStratum? {
        let cloudyLevels = data.levels.indices.filter { level in
            guard integer(data.familyCodes, sample, level) == family else { return false }
            let amount = scalar(data.amounts, sample, level)
                ?? Swift.max(scalar(data.presence, sample, level) ?? 0,
                             (scalar(data.fractions, sample, level) ?? 0) / 100)
            return amount >= 0.06
        }
        guard !cloudyLevels.isEmpty else { return nil }

        let bases = cloudyLevels.compactMap { scalar(data.layerBases, sample, $0) }
        let tops = cloudyLevels.compactMap { scalar(data.layerTops, sample, $0) }
        let heights = cloudyLevels.compactMap { scalar(data.heights, sample, $0) }
        guard let layerBase = bases.min() ?? heights.min().map({ $0 - 700 }),
              let layerTop = tops.max() ?? heights.max().map({ $0 + 700 }),
              layerTop > layerBase else { return nil }

        let amountMax = cloudyLevels.compactMap {
            scalar(data.amounts, sample, $0)
                ?? Swift.max(scalar(data.presence, sample, $0) ?? 0,
                             (scalar(data.fractions, sample, $0) ?? 0) / 100)
        }.max() ?? 0.2
        let status = cloudyLevels.compactMap { integer(data.statuses, sample, $0) }.max() ?? 0
        return ColumnStratum(baseASL: layerBase, topASL: layerTop, amount: amountMax, status: status)
    }

    private func familyShortZh(_ family: Int) -> String {
        switch family {
        case 1: return "高"
        case 2: return "中"
        default: return "低"
        }
    }

    private func familyShortEn(_ family: Int) -> String {
        switch family {
        case 1: return "High"
        case 2: return "Mid"
        default: return "Low"
        }
    }

    private func midpoint(_ a: CGPoint, _ b: CGPoint) -> CGPoint {
        CGPoint(x: (a.x + b.x) * 0.5, y: (a.y + b.y) * 0.5)
    }

    private func smoothOpenCurve(points: [CGPoint]) -> Path {
        var path = Path()
        guard let first = points.first else { return path }
        path.move(to: first)
        guard points.count > 1 else { return path }
        if points.count == 2 {
            path.addLine(to: points[1])
            return path
        }
        path.addLine(to: midpoint(points[0], points[1]))
        for idx in 1 ..< (points.count - 1) {
            path.addQuadCurve(to: midpoint(points[idx], points[idx + 1]), control: points[idx])
        }
        path.addLine(to: points[points.count - 1])
        return path
    }

    private func appendSmoothSegment(to path: inout Path, points: [CGPoint]) {
        guard points.count > 1 else { return }
        if points.count == 2 {
            path.addLine(to: points[1])
            return
        }
        path.addLine(to: midpoint(points[0], points[1]))
        for idx in 1 ..< (points.count - 1) {
            path.addQuadCurve(to: midpoint(points[idx], points[idx + 1]), control: points[idx])
        }
        path.addLine(to: points[points.count - 1])
    }

    private func cloudSilhouettePath(run: [CloudRunPoint], left: Double, right: Double, columnSpan: Double) -> (closed: Path, topEdge: Path, bottomEdge: Path, leftX: Double, rightX: Double) {
        let taper = Swift.min(columnSpan * 0.45, (right - left) * 0.06)
        let first = run[0]
        let last = run[run.count - 1]
        let leftX = Swift.max(left, first.cx - taper)
        let rightX = Swift.min(right, last.cx + taper)
        let leftMidY = (first.topY + first.bottomY) * 0.5
        let rightMidY = (last.topY + last.bottomY) * 0.5

        var topPts: [CGPoint] = [CGPoint(x: leftX, y: leftMidY)]
        for p in run { topPts.append(CGPoint(x: p.cx, y: p.topY)) }
        topPts.append(CGPoint(x: rightX, y: rightMidY))

        var botPts: [CGPoint] = [CGPoint(x: rightX, y: rightMidY)]
        for p in run.reversed() { botPts.append(CGPoint(x: p.cx, y: p.bottomY)) }
        botPts.append(CGPoint(x: leftX, y: leftMidY))

        var closed = Path()
        closed.move(to: topPts[0])
        appendSmoothSegment(to: &closed, points: topPts)
        appendSmoothSegment(to: &closed, points: botPts)
        closed.closeSubpath()

        let topEdge = smoothOpenCurve(points: topPts)
        let bottomEdge = smoothOpenCurve(points: botPts.reversed())
        return (closed, topEdge, bottomEdge, leftX, rightX)
    }

    // MARK: - drawing

    private func draw(context: GraphicsContext, size: CGSize, data: CrossSectionData,
                      scale: Double, showIsobars: Bool, scrubDistanceKM: Double?) {
        let left = plotLeftMargin
        let right = size.width - (showIsobars ? 46.0 : 14.0)
        let top = plotTopMargin
        let bottom = size.height - plotBottomOffset
        guard bottom > top + 20, right > left + 20 else { return }

        let maxHeight = 18_000.0
        let strokeScale = CGFloat(scale)
        let labelSize = CGFloat(Swift.min(13, Swift.max(9.5, 9.5 * scale)))
        let maxDistance = Swift.max(1, data.distances.last ?? 1)
        let x = { (distance: Double) in left + Swift.min(Swift.max(distance, 0), maxDistance) / maxDistance * (right - left) }
        let y = { (height: Double) in top + (1 - Swift.min(Swift.max(height, 0), maxHeight) / maxHeight) * (bottom - top) }

        let plotRect = CGRect(x: left, y: top, width: right - left, height: bottom - top)
        let plotPath = Path(roundedRect: plotRect, cornerRadius: 10)
        let hasRequired = data.requiredASL.count == data.distances.count
        let reqPoints: [CGPoint] = hasRequired
            ? data.distances.indices.map { CGPoint(x: x(data.distances[$0]), y: y(data.requiredASL[$0])) }
            : []

        context.drawLayer { layer in
            layer.clip(to: plotPath)

            // Layer 1 — Twilight sky backdrop
            let verticalSky = Gradient(colors: scheme == .dark
                ? [Color(red: 0.06, green: 0.10, blue: 0.19),
                   Color(red: 0.12, green: 0.17, blue: 0.27),
                   Color(red: 0.22, green: 0.19, blue: 0.22)]
                : [Color(red: 0.80, green: 0.88, blue: 0.96),
                   Color(red: 0.91, green: 0.94, blue: 0.98),
                   Color(red: 0.98, green: 0.92, blue: 0.86)])
            layer.fill(
                plotPath,
                with: .linearGradient(verticalSky,
                                      startPoint: CGPoint(x: left, y: top),
                                      endPoint: CGPoint(x: left, y: bottom))
            )
            let horizonGlow = Gradient(colors: [
                Color.clear,
                Color(red: 0.98, green: 0.63, blue: 0.34).opacity(scheme == .dark ? 0.18 : 0.20)
            ])
            layer.fill(
                plotPath,
                with: .linearGradient(horizonGlow,
                                      startPoint: CGPoint(x: left + (right - left) * 0.38, y: top + (bottom - top) * 0.25),
                                      endPoint: CGPoint(x: right, y: bottom))
            )

            // Layer 2 — Earth Shadow (below requiredASL) & Sunlit Corridor (above requiredASL)
            if reqPoints.count > 1 {
                var shadowPath = Path()
                shadowPath.move(to: reqPoints[0])
                appendSmoothSegment(to: &shadowPath, points: reqPoints)
                shadowPath.addLine(to: CGPoint(x: right, y: bottom))
                shadowPath.addLine(to: CGPoint(x: left, y: bottom))
                shadowPath.closeSubpath()
                let shadowColor = scheme == .dark
                    ? Color(red: 0.03, green: 0.06, blue: 0.13).opacity(0.28)
                    : Color(red: 0.24, green: 0.32, blue: 0.45).opacity(0.12)
                layer.fill(shadowPath, with: .color(shadowColor))

                var sunlitPath = Path()
                sunlitPath.move(to: CGPoint(x: left, y: top))
                sunlitPath.addLine(to: CGPoint(x: right, y: top))
                sunlitPath.addLine(to: reqPoints[reqPoints.count - 1])
                appendSmoothSegment(to: &sunlitPath, points: reqPoints.reversed())
                sunlitPath.closeSubpath()

                let sunlitGrad = Gradient(colors: [
                    Color(red: 0.98, green: 0.68, blue: 0.30).opacity(0.02),
                    Color(red: 0.98, green: 0.60, blue: 0.24).opacity(scheme == .dark ? 0.16 : 0.15)
                ])
                layer.fill(
                    sunlitPath,
                    with: .linearGradient(sunlitGrad,
                                          startPoint: CGPoint(x: left, y: top),
                                          endPoint: CGPoint(x: right, y: bottom))
                )

                layer.drawLayer { rayLayer in
                    rayLayer.clip(to: sunlitPath)
                    let width = right - left
                    let height = bottom - top
                    for idx in 0..<4 {
                        let offset = Double(idx) * 0.19
                        let xTop = right - width * (0.04 + offset)
                        let rayW = width * 0.065
                        let slant = width * 0.34
                        var ray = Path()
                        ray.move(to: CGPoint(x: xTop, y: top - 4))
                        ray.addLine(to: CGPoint(x: xTop + rayW, y: top - 4))
                        ray.addLine(to: CGPoint(x: xTop + rayW - slant, y: bottom + 4))
                        ray.addLine(to: CGPoint(x: xTop - slant, y: bottom + 4))
                        ray.closeSubpath()
                        rayLayer.fill(
                            ray,
                            with: .linearGradient(
                                Gradient(colors: [
                                    Color(red: 1.0, green: 0.84, blue: 0.56).opacity(0.07),
                                    Color(red: 1.0, green: 0.72, blue: 0.38).opacity(0.0)
                                ]),
                                startPoint: CGPoint(x: right, y: top),
                                endPoint: CGPoint(x: left, y: top + height * 0.85)
                            )
                        )
                    }
                }
            }

            // Layer 3 — Altitude & dashed distance gridlines (+ optional Isobars)
            for km in stride(from: 0.0, through: 18_000.0, by: 3000.0) {
                let lineY = y(km)
                var grid = Path()
                grid.move(to: CGPoint(x: left, y: lineY))
                grid.addLine(to: CGPoint(x: right, y: lineY))
                layer.stroke(grid, with: .color(Color.secondary.opacity(km == 0 ? 0.08 : 0.15)), lineWidth: 0.6)
            }
            let distStep = maxDistance <= 120 ? 25.0 : maxDistance <= 250 ? 50.0 : 100.0
            for dist in stride(from: 0.0, through: maxDistance, by: distStep) where dist > 0 {
                let lineX = x(dist)
                var grid = Path()
                grid.move(to: CGPoint(x: lineX, y: top))
                grid.addLine(to: CGPoint(x: lineX, y: bottom))
                layer.stroke(grid, with: .color(Color.secondary.opacity(0.12)),
                             style: StrokeStyle(lineWidth: 0.6, dash: [3, 4]))
            }

            if showIsobars {
                for l in data.levels.indices {
                    var pts: [CGPoint] = []
                    var temps: [Double] = []
                    for i in data.distances.indices {
                        guard let h = scalar(data.heights, i, l) else { continue }
                        pts.append(CGPoint(x: x(data.distances[i]), y: y(h)))
                        if let temp = scalar(data.temps, i, l) { temps.append(temp) }
                    }
                    guard pts.count > 1 else { continue }
                    let isoPath = smoothOpenCurve(points: pts)
                    let avg = temps.isEmpty ? 0 : temps.reduce(0, +) / Double(temps.count)
                    let isoColor = avg < -30 ? Color(red: 0.48, green: 0.56, blue: 0.72)
                        : avg < 0 ? Color(red: 0.68, green: 0.50, blue: 0.46)
                        : Color(red: 0.80, green: 0.42, blue: 0.30)
                    layer.stroke(isoPath, with: .color(isoColor.opacity(0.35)), lineWidth: 1.0)
                }
            }

            // Layer 4 — Continuous organic cloud strata (low 3 -> mid 2 -> high 1)
            let sampleCount = Swift.max(1, data.distances.count - 1)
            let columnSpan = (right - left) / Double(sampleCount)

            for family in [3, 2, 1] {
                var runs: [[CloudRunPoint]] = []
                var currentRun: [CloudRunPoint] = []

                for i in data.distances.indices {
                    if let stratum = columnStratum(data: data, sample: i, family: family) {
                        let cx = x(data.distances[i])
                        let terrainY = data.terrain.indices.contains(i) ? y(data.terrain[i]) : bottom
                        var botY = Swift.min(terrainY - 1, y(stratum.baseASL))
                        var topY = Swift.max(top + 2, y(stratum.topASL))
                        if topY > botY - 2 {
                            let mid = Swift.min(Swift.max((topY + botY) * 0.5, top + 3), bottom - 3)
                            topY = mid - 1.5
                            botY = mid + 1.5
                        }
                        currentRun.append(CloudRunPoint(sampleIndex: i, cx: cx, topY: topY, bottomY: botY, stratum: stratum))
                    } else if !currentRun.isEmpty {
                        runs.append(currentRun)
                        currentRun = []
                    }
                }
                if !currentRun.isEmpty { runs.append(currentRun) }

                for run in runs {
                    let shape = cloudSilhouettePath(run: run, left: left, right: right, columnSpan: columnSpan)
                    let avgAmount = run.map(\.stratum.amount).reduce(0, +) / Double(run.count)
                    let alpha = Swift.min(0.86, 0.42 + avgAmount * 0.48)

                    let topColor: Color
                    let botColor: Color
                    switch family {
                    case 1:
                        topColor = (scheme == .dark ? Color(red: 0.78, green: 0.88, blue: 0.97) : Color(red: 0.92, green: 0.96, blue: 1.00)).opacity(alpha)
                        botColor = (scheme == .dark ? Color(red: 0.54, green: 0.70, blue: 0.87) : Color(red: 0.64, green: 0.78, blue: 0.92)).opacity(alpha * 0.92)
                    case 2:
                        topColor = (scheme == .dark ? Color(red: 0.56, green: 0.79, blue: 0.82) : Color(red: 0.68, green: 0.86, blue: 0.88)).opacity(alpha)
                        botColor = (scheme == .dark ? Color(red: 0.36, green: 0.60, blue: 0.65) : Color(red: 0.44, green: 0.67, blue: 0.72)).opacity(alpha * 0.94)
                    default:
                        topColor = (scheme == .dark ? Color(red: 0.50, green: 0.55, blue: 0.61) : Color(red: 0.62, green: 0.67, blue: 0.72)).opacity(alpha)
                        botColor = (scheme == .dark ? Color(red: 0.33, green: 0.38, blue: 0.44) : Color(red: 0.44, green: 0.49, blue: 0.54)).opacity(alpha * 0.96)
                    }

                    let minTopY = run.map(\.topY).min() ?? top
                    let maxBotY = run.map(\.bottomY).max() ?? bottom
                    layer.fill(
                        shape.closed,
                        with: .linearGradient(Gradient(colors: [topColor, botColor]),
                                              startPoint: CGPoint(x: shape.leftX, y: minTopY),
                                              endPoint: CGPoint(x: shape.leftX, y: maxBotY))
                    )
                    layer.stroke(shape.topEdge,
                                 with: .color(Color.white.opacity(scheme == .dark ? 0.25 : 0.45)),
                                 lineWidth: 1.0)

                    var subStart = 0
                    while subStart < run.count {
                        let st = run[subStart].stratum.status
                        var subEnd = subStart
                        while subEnd + 1 < run.count && run[subEnd + 1].stratum.status == st {
                            subEnd += 1
                        }
                        if st == 3 || st == 2 {
                            let subLeft = subStart == 0 ? shape.leftX : (run[subStart - 1].cx + run[subStart].cx) * 0.5
                            let subRight = subEnd == run.count - 1 ? shape.rightX : (run[subEnd].cx + run[subEnd + 1].cx) * 0.5
                            let subRun = Array(run[subStart...subEnd])
                            let subMinTop = subRun.map(\.topY).min() ?? minTopY
                            let subMaxBot = subRun.map(\.bottomY).max() ?? maxBotY

                            var subBotPts: [CGPoint] = []
                            if subStart == 0 {
                                subBotPts.append(CGPoint(x: shape.leftX, y: (run[0].topY + run[0].bottomY) * 0.5))
                            } else {
                                subBotPts.append(CGPoint(x: subLeft, y: (run[subStart - 1].bottomY + run[subStart].bottomY) * 0.5))
                            }
                            for p in subRun { subBotPts.append(CGPoint(x: p.cx, y: p.bottomY)) }
                            if subEnd == run.count - 1 {
                                subBotPts.append(CGPoint(x: shape.rightX, y: (run[subEnd].topY + run[subEnd].bottomY) * 0.5))
                            } else {
                                subBotPts.append(CGPoint(x: subRight, y: (run[subEnd].bottomY + run[subEnd + 1].bottomY) * 0.5))
                            }
                            let subBotCurve = smoothOpenCurve(points: subBotPts)

                            layer.drawLayer { cloudLayer in
                                cloudLayer.clip(to: shape.closed)
                                let bandRect = CGRect(x: subLeft, y: top, width: Swift.max(1, subRight - subLeft), height: bottom - top)
                                if st == 3 {
                                    let fireGlow = Gradient(stops: [
                                        .init(color: Color.clear, location: 0.0),
                                        .init(color: Color(red: 1.0, green: 0.620, blue: 0.267).opacity(0.42), location: 0.55),
                                        .init(color: Color(red: 0.961, green: 0.365, blue: 0.157).opacity(0.86), location: 1.0)
                                    ])
                                    cloudLayer.fill(
                                        Path(bandRect),
                                        with: .linearGradient(fireGlow,
                                                              startPoint: CGPoint(x: bandRect.midX, y: subMinTop),
                                                              endPoint: CGPoint(x: bandRect.midX, y: subMaxBot))
                                    )
                                } else {
                                    cloudLayer.fill(
                                        Path(bandRect),
                                        with: .color(Color(red: 0.90, green: 0.60, blue: 0.22).opacity(0.16))
                                    )
                                }
                            }

                            if st == 3 {
                                layer.stroke(subBotCurve,
                                             with: .color(Color(red: 0.98, green: 0.45, blue: 0.18).opacity(0.30)),
                                             style: StrokeStyle(lineWidth: 5.0 * Swift.min(1.3, strokeScale), lineCap: .round, lineJoin: .round))
                                layer.stroke(subBotCurve,
                                             with: .color(Color(red: 1.0, green: 0.54, blue: 0.20)),
                                             style: StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))
                            } else {
                                layer.stroke(subBotCurve,
                                             with: .color(Color(red: 0.88, green: 0.58, blue: 0.20).opacity(0.88)),
                                             style: StrokeStyle(lineWidth: 1.2, lineCap: .round, dash: [4, 3]))
                            }
                        }
                        subStart = subEnd + 1
                    }
                }
            }

            // Layer 5 — Smooth requiredASL illumination threshold curve + inline pill
            if reqPoints.count > 1 {
                let reqCurve = smoothOpenCurve(points: reqPoints)
                layer.stroke(reqCurve,
                             with: .color(Color(red: 0.96, green: 0.66, blue: 0.24).opacity(0.20)),
                             lineWidth: 5.0)
                layer.stroke(reqCurve,
                             with: .color(Color(red: 0.92, green: 0.59, blue: 0.19)),
                             style: StrokeStyle(lineWidth: 1.8, lineCap: .round, dash: [7, 4]))

                let pillIdx = Swift.min(reqPoints.count - 1, Swift.max(0, Int(Double(reqPoints.count) * 0.72)))
                let anchorPt = reqPoints[pillIdx]
                let pillText = Text(L10n.t("受光高度线"))
                    .font(.system(size: 8.5, weight: .semibold))
                    .foregroundColor(Color(red: 0.96, green: 0.72, blue: 0.34))
                let pillResolved = layer.resolve(pillText)
                let pillTextSize = pillResolved.measure(in: size)
                let pillW = Double(pillTextSize.width) + 10
                let pillH = Double(pillTextSize.height) + 4
                let anchorX = Double(anchorPt.x)
                let anchorY = Double(anchorPt.y)
                let pillX = Swift.min(right - pillW - 6, Swift.max(left + 6, anchorX - pillW * 0.5))
                let pillY = Swift.max(top + 6, Swift.min(bottom - pillH - 6, anchorY - pillH - 6))
                let pillRect = CGRect(x: pillX, y: pillY, width: pillW, height: pillH)
                layer.fill(Path(roundedRect: pillRect, cornerRadius: pillH * 0.5),
                           with: .color((scheme == .dark ? Color.black : Color.white).opacity(0.55)))
                layer.stroke(Path(roundedRect: pillRect, cornerRadius: pillH * 0.5),
                             with: .color(Color(red: 0.92, green: 0.59, blue: 0.19).opacity(0.45)),
                             lineWidth: 0.7)
                layer.draw(pillResolved, at: CGPoint(x: pillRect.midX, y: pillRect.midY), anchor: .center)
            }

            // Layer 6 — Smooth mountain/terrain silhouette
            if data.terrain.count == data.distances.count {
                let terrainPts = data.distances.indices.map { CGPoint(x: x(data.distances[$0]), y: y(data.terrain[$0])) }
                var fillPath = Path()
                if let firstT = terrainPts.first {
                    fillPath.move(to: firstT)
                    appendSmoothSegment(to: &fillPath, points: terrainPts)
                    fillPath.addLine(to: CGPoint(x: right, y: bottom))
                    fillPath.addLine(to: CGPoint(x: left, y: bottom))
                    fillPath.closeSubpath()
                }
                let terrainMinY = terrainPts.map(\.y).min() ?? CGFloat(bottom)
                let terrainGrad = Gradient(colors: scheme == .dark
                    ? [Color(red: 0.34, green: 0.40, blue: 0.42).opacity(0.76),
                       Color(red: 0.15, green: 0.18, blue: 0.22).opacity(0.92)]
                    : [Color(red: 0.44, green: 0.49, blue: 0.46).opacity(0.48),
                       Color(red: 0.28, green: 0.33, blue: 0.32).opacity(0.68)])
                layer.fill(fillPath,
                           with: .linearGradient(terrainGrad,
                                                 startPoint: CGPoint(x: left, y: terrainMinY),
                                                 endPoint: CGPoint(x: left, y: bottom)))
                let ridgeCurve = smoothOpenCurve(points: terrainPts)
                layer.stroke(ridgeCurve,
                             with: .color(Color(red: 0.45, green: 0.51, blue: 0.49)),
                             lineWidth: 1.6)
            }

            // Layer 7 — Observer beacon, right-edge sun cue, & sightline to primary burnable target
            let observerY = data.terrain.isEmpty ? bottom : y(data.terrain[0])
            let observer = CGPoint(x: x(0), y: Swift.min(bottom - 5, Swift.max(top + 10, observerY)))

            if let targetFocus = primaryBurnableFocus(data: data) {
                let targetPt = CGPoint(x: x(targetFocus.distanceKM), y: y(targetFocus.baseASL))
                var sightline = Path()
                sightline.move(to: observer)
                sightline.addLine(to: targetPt)
                layer.stroke(sightline,
                             with: .color(Color(red: 0.99, green: 0.62, blue: 0.28).opacity(0.62)),
                             style: StrokeStyle(lineWidth: 1.3, dash: [5, 4]))

                let ringOuter = CGRect(x: targetPt.x - 8, y: targetPt.y - 8, width: 16, height: 16)
                layer.fill(Path(ellipseIn: ringOuter), with: .color(Color(red: 0.98, green: 0.45, blue: 0.18).opacity(0.28)))
                let ringInner = CGRect(x: targetPt.x - 4.2, y: targetPt.y - 4.2, width: 8.4, height: 8.4)
                layer.fill(Path(ellipseIn: ringInner), with: .color(Color(red: 1.0, green: 0.56, blue: 0.22)))
                layer.stroke(Path(ellipseIn: ringInner), with: .color(.white.opacity(0.95)), lineWidth: 1.3)

                let baseKM = targetFocus.baseASL / 1000
                let targetText = Text(L10n.text(String(format: "可烧云底 %.1fkm", baseKM),
                                                String(format: "Lit base %.1fkm", baseKM)))
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.white)
                let resolvedTarget = layer.resolve(targetText)
                let tSize = resolvedTarget.measure(in: size)
                let tW = Double(tSize.width) + 10
                let tH = Double(tSize.height) + 4
                let targetX = Double(targetPt.x)
                let targetY = Double(targetPt.y)
                let tX = Swift.min(right - tW - 6, Swift.max(left + 6, targetX - tW * 0.5))
                let tY = targetY - tH - 10 >= top + 6 ? targetY - tH - 10 : Swift.min(bottom - tH - 6, targetY + 10)
                let tRect = CGRect(x: tX, y: tY, width: tW, height: tH)
                layer.fill(Path(roundedRect: tRect, cornerRadius: tH * 0.5),
                           with: .color(Color(red: 0.92, green: 0.36, blue: 0.16).opacity(0.88)))
                layer.draw(resolvedTarget, at: CGPoint(x: tRect.midX, y: tRect.midY), anchor: .center)
            }

            let haloRect = CGRect(x: observer.x - 8, y: observer.y - 8, width: 16, height: 16)
            layer.fill(Path(ellipseIn: haloRect), with: .color(Color(red: 0.96, green: 0.48, blue: 0.24).opacity(0.28)))
            let dotRect = CGRect(x: observer.x - 4.5, y: observer.y - 4.5, width: 9, height: 9)
            layer.fill(Path(ellipseIn: dotRect), with: .color(Color(red: 0.96, green: 0.48, blue: 0.24)))
            layer.stroke(Path(ellipseIn: dotRect), with: .color(.white.opacity(0.92)), lineWidth: 1.4)
            layer.draw(
                Text(L10n.text("观测点", "Observer"))
                    .font(.system(size: labelSize, weight: .semibold))
                    .foregroundColor(.primary),
                at: CGPoint(x: observer.x + 10, y: Swift.max(top + 10, observer.y - 9)),
                anchor: .leading
            )

            let sunCueText: String = {
                if let az = number(section["solar_azimuth_deg"]) {
                    return L10n.text(String(format: "☀ 太阳方向 %.0f°", az), String(format: "☀ Sun %.0f°", az))
                }
                return L10n.text("☀ 太阳方向", "☀ Sun")
            }()
            layer.draw(
                Text(sunCueText)
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundColor(Color(red: 0.98, green: 0.72, blue: 0.34)),
                at: CGPoint(x: right - 10, y: top + 12),
                anchor: .trailing
            )

            // Layer 8 — Vertical scrub crosshair
            if let scrubDistanceKM {
                let hx = x(scrubDistanceKM)
                var crosshair = Path()
                crosshair.move(to: CGPoint(x: hx, y: top))
                crosshair.addLine(to: CGPoint(x: hx, y: bottom))
                layer.stroke(crosshair,
                             with: .color(Color.primary.opacity(0.45)),
                             style: StrokeStyle(lineWidth: 1.0, dash: [3, 3]))
            }
        }

        context.stroke(plotPath,
                       with: .color(Color.secondary.opacity(scheme == .dark ? 0.28 : 0.22)),
                       lineWidth: 0.9)

        // Outside axis labels: Altitude (left)
        for km in stride(from: 0.0, through: 18_000.0, by: 3000.0) {
            context.draw(
                Text("\(Int(km / 1000))km").font(.system(size: labelSize)).foregroundColor(.secondary),
                at: CGPoint(x: left - 6, y: y(km)),
                anchor: .trailing
            )
        }

        // Outside axis labels: Distance (bottom, skip 0km)
        let distStep = maxDistance <= 120 ? 25.0 : maxDistance <= 250 ? 50.0 : 100.0
        for dist in stride(from: 0.0, through: maxDistance, by: distStep) where dist > 0 {
            let lx = x(dist)
            context.draw(
                Text("\(Int(dist))km").font(.system(size: labelSize)).foregroundColor(.secondary),
                at: CGPoint(x: lx, y: bottom + 10),
                anchor: dist >= maxDistance - distStep * 0.25 ? .trailing : .center
            )
        }

        // Outside axis labels: Isobars (right)
        if showIsobars {
            var lastLabelY = -Double.infinity
            for l in data.levels.indices {
                let heightsForLevel = data.distances.indices.compactMap { scalar(data.heights, $0, l) }
                guard let lastH = heightsForLevel.last else { continue }
                let lineY = y(lastH)
                guard lineY >= top + 4, lineY <= bottom - 4, abs(lineY - lastLabelY) >= 13 else { continue }
                lastLabelY = lineY
                context.draw(
                    Text("\(data.levels[l])hPa")
                        .font(.system(size: Swift.max(8.5, labelSize - 0.5)))
                        .foregroundColor(.secondary),
                    at: CGPoint(x: right + 5, y: lineY),
                    anchor: .leading
                )
            }
        }
    }

    private func primaryBurnableFocus(data: CrossSectionData) -> BurnableTargetFocus? {
        for target in data.targets where target["status"] as? String == "burnable" {
            if let dist = number(target["distance_km"]),
               let base = number(target["cloud_base_asl_m"]) {
                return BurnableTargetFocus(distanceKM: dist, baseASL: base)
            }
        }
        var litPoints: [(dist: Double, base: Double, amount: Double)] = []
        for i in data.distances.indices {
            for family in 1...3 {
                if let s = columnStratum(data: data, sample: i, family: family), s.status == 3 {
                    litPoints.append((data.distances[i], s.baseASL, s.amount))
                }
            }
        }
        guard !litPoints.isEmpty else { return nil }
        let sorted = litPoints.sorted { $0.dist < $1.dist }
        let mid = sorted[sorted.count / 2]
        return BurnableTargetFocus(distanceKM: mid.dist, baseASL: mid.base)
    }

    // MARK: - legend & target diagnosis

    private var legend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                legendFireBase(L10n.text("火烧云受光云底", "Lit fire-sky base"))
                legendCloudStrata(L10n.text("高/中/低云层", "High/Mid/Low clouds"))
                legendCorridor(L10n.text("受光区/地影区", "Sunlit / Shadow"))
                legendLine(L10n.text("受光高度线", "Sunlit threshold"),
                           Color(red: 0.92, green: 0.59, blue: 0.19), dashed: true)
                legendLine(L10n.text("受光被遮挡", "Lit but occluded"),
                           Color(red: 0.88, green: 0.58, blue: 0.20), dashed: true)
                legendTerrainSight(L10n.text("地形与视线", "Terrain & sightline"))
            }
            .font(.system(size: 9.5))
            .foregroundStyle(.secondary)
            .padding(.vertical, 2)
        }
    }

    private func legendFireBase(_ title: String) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 2)
                .fill(LinearGradient(colors: [Color(red: 0.72, green: 0.84, blue: 0.94),
                                              Color(red: 0.961, green: 0.365, blue: 0.157)],
                                     startPoint: .top, endPoint: .bottom))
                .frame(width: 13, height: 8)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(Color(red: 1.0, green: 0.54, blue: 0.20)).frame(height: 1.8)
                }
            Text(title)
        }
    }

    private func legendCloudStrata(_ title: String) -> some View {
        HStack(spacing: 3) {
            Capsule().fill(Color(red: 0.72, green: 0.85, blue: 0.96)).frame(width: 6, height: 7)
            Capsule().fill(Color(red: 0.48, green: 0.74, blue: 0.78)).frame(width: 6, height: 7)
            Capsule().fill(Color(red: 0.46, green: 0.51, blue: 0.57)).frame(width: 6, height: 7)
            Text(title)
        }
    }

    private func legendCorridor(_ title: String) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 2)
                .fill(LinearGradient(colors: [Color(red: 0.98, green: 0.65, blue: 0.26).opacity(0.35),
                                              Color(red: 0.12, green: 0.18, blue: 0.30).opacity(0.45)],
                                     startPoint: .top, endPoint: .bottom))
                .frame(width: 13, height: 8)
            Text(title)
        }
    }

    private func legendTerrainSight(_ title: String) -> some View {
        HStack(spacing: 4) {
            ZStack(alignment: .bottomLeading) {
                Rectangle().fill(Color(red: 0.41, green: 0.46, blue: 0.45).opacity(0.7)).frame(width: 13, height: 4)
                Path { p in
                    p.move(to: CGPoint(x: 1, y: 6))
                    p.addLine(to: CGPoint(x: 12, y: 1))
                }
                .stroke(Color(red: 0.99, green: 0.62, blue: 0.28), style: StrokeStyle(lineWidth: 1.2, dash: [2.5, 2]))
            }
            .frame(width: 13, height: 8)
            Text(title)
        }
    }

    private func legendLine(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(Color.clear).frame(width: 13, height: 0)
                .overlay { Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.5, dash: dashed ? [3.5, 2.5] : [])) }
                .frame(height: 7)
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
}

/// 冻结剖面状态展示卡片（点预测与卫星实况共用）。
struct FrozenProfileView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
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
                Text(model == "satellite"
                     ? L10n.text("卫星冻结剖面", "Frozen Satellite Profile")
                     : "\(model.uppercased()) \(L10n.text("冻结剖面", "Frozen Profile"))")
                    .font(.caption.weight(.semibold))
                Spacer()
                if state == "loading" {
                    ProgressView().controlSize(.small)
                } else {
                    Button(action: reload) {
                        Image(systemName: "arrow.clockwise")
                    }
                    .buttonStyle(.borderless)
                    .disabled(store.busy)
                }
            }
            switch state {
            case "loading":
                Text(L10n.text("正在读取冻结剖面…", "Loading frozen profile…"))
                    .font(.caption2).foregroundStyle(.secondary)
            case "ready":
                if model == "satellite" { satelliteReady } else { modelReady }
            case "idle":
                Text(L10n.text("点击刷新按钮从冻结源读取太阳方向剖面。", "Tap reload to inspect the frozen solar-path profile."))
                    .font(.caption2).foregroundStyle(.secondary)
            default:
                Text(store.frozenProfileError.isEmpty ? L10n.text("该结果没有可用剖面。", "No profile available.") : store.frozenProfileError)
                    .font(.caption2).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(FSDesign.surfaceFill(role: .grouped, dark: scheme == .dark), in: RoundedRectangle(cornerRadius: 8))
    }

    private var modelReady: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let time = section?["valid_time_local"] as? String {
                Text(time.replacingOccurrences(of: "T", with: " "))
                    .font(.caption2).monospacedDigit()
            }
            HStack(spacing: 10) {
                metric(L10n.text("太阳高度", "Solar elev."), section?["solar_elevation_deg"], suffix: "°")
                metric(L10n.t("方位"), section?["solar_azimuth_deg"], suffix: "°")
                Text(L10n.text("\(distanceCount) 个距离样本", "\(distanceCount) samples"))
                    .font(.caption2).foregroundStyle(.secondary)
            }
            let cloud = fields["cloud_fraction_percent"] as? [[Any]]
            let temperatures = fields["temperature_c"] as? [[Any]]
            HStack(spacing: 10) {
                metric(L10n.text("云量样本", "Cloud samples"), cloud?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
                metric(L10n.text("温度样本", "Temp samples"), temperatures?.reduce(0) { $0 + $1.count } ?? 0, suffix: "")
            }
        }
    }

    private var satelliteReady: some View {
        let profile = section?["profile"] as? [JSONObject] ?? []
        let times = section?["source_times_utc"] as? [String] ?? []
        let distances = profile.compactMap { number($0["distance_km"]) }
        return VStack(alignment: .leading, spacing: 5) {
            Text(store.frozenProfileTime.replacingOccurrences(of: "T", with: " "))
                .font(.caption2).monospacedDigit()
            HStack(spacing: 10) {
                metric(L10n.text("采样数", "Samples"), section?["samples_count"], suffix: "")
                metric(L10n.text("剖面样本", "Profile pts"), profile.count, suffix: "")
                if let azimuth = number(profile.first?["solar_azimuth_deg"]) {
                    metric(L10n.t("方位"), azimuth, suffix: "°")
                }
            }
            if let low = distances.min(), let high = distances.max() {
                Text(String(format: L10n.text("光路距离 %.0f–%.0f km", "Path %.0f–%.0f km"), low, high))
                    .font(.caption2).foregroundStyle(.secondary)
            }
            if !times.isEmpty {
                Text(L10n.text("卫星源时次 ", "Satellite scans ") + times.joined(separator: " · "))
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func metric(_ title: String, _ value: Any?, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.system(size: 9)).foregroundStyle(.secondary)
            Text(formatMetric(value) + suffix).font(.caption2.weight(.medium)).monospacedDigit()
        }
    }

    private func formatMetric(_ value: Any?) -> String {
        if let n = value as? NSNumber, n.doubleValue.isFinite {
            return String(format: "%.1f", n.doubleValue)
        }
        return "—"
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
