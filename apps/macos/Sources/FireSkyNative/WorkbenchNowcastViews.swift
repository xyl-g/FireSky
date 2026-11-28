import Charts
import SwiftUI

/// Nowcast trend view (plan items nowcast-5/6): the fixed 0–2.5 quality line plus
/// a 0–100% ratio sub-plot (burnable cloud coverage / forward burnable ratio) and an
/// independent AOD 550 sub-plot. Missing values break lines; nothing is joined to 0.
struct WorkCurveView: View {
    let report: JSONObject?
    let timezone: String
    var body: some View {
        let points = ForecastComparison.curve(report: report, model: "nowcast", timezone: timezone)
        let ratios = ForecastComparison.ratioCurve(report: report, model: "nowcast", timezone: timezone)
        let aod = ForecastComparison.aodCurve(report: report, model: "nowcast", timezone: timezone)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("霞光质量变化").font(.system(size: 12.5, weight: .semibold))
                Text("质量轴 0–2.5 · 比例轴 0–100% · 缺测断线")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Label("质量指数", systemImage: "circle.fill").font(.system(size: 9)).foregroundStyle(warm).workChip()
            }
            qualityChart(points)
            ratioChart(ratios)
            aodChart(aod)
        }.padding(11).workSurface()
    }

    private func qualityChart(_ points: [ForecastCurvePoint]) -> some View {
        Chart {
            ForEach(points) { point in
                LineMark(x: .value("时间", point.time), y: .value("质量指数", point.quality),
                         series: .value("曲线", point.series))
                    .foregroundStyle(warm).lineStyle(StrokeStyle(lineWidth: 2))
                PointMark(x: .value("时间", point.time), y: .value("质量指数", point.quality))
                    .foregroundStyle(warm).symbolSize(8)
            }
            // 原生图形规范（方案 :90）：图表需要最佳时刻标记，与点预测质量图同款菱形。
            if let bestTime = report?["best_time_local"] as? String,
               let best = points.first(where: { $0.local == bestTime }) {
                PointMark(x: .value("时间", best.time), y: .value("质量指数", best.quality))
                    .symbol(.diamond).symbolSize(46)
                    .foregroundStyle(warm)
                    .accessibilityLabel("最佳时刻 \(ObservationDate.timeLabel(best.time, timezone: timezone))，质量 \(String(format: "%.3f", best.quality))")
            }
        }
        .chartYScale(domain: 0...2.5)
        .chartYAxis {
            AxisMarks(position: .leading, values: [0, 1.25, 2.5]) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                AxisValueLabel {
                    if let number = value.as(Double.self) { Text(number.formatted(.number.precision(.fractionLength(0...2)))) }
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 5)) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let date = value.as(Date.self) {
                        Text(ObservationDate.timeLabel(date, timezone: timezone))
                    }
                }
            }
        }
        .chartYAxisLabel("质量指数 · 0–2.5")
        .frame(height: 164)
        .overlay {
            if points.isEmpty {
                Text(report == nil ? "等待实况分析" : "暂无质量曲线")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .accessibilityLabel("实况逐时质量趋势，质量轴固定 0 到 2.5，缺测时段断线")
    }

    /// 0–100% ratio series on their own axis (never mixed with the quality axis).
    /// The desktop nowcast leaves observer_return_transmission Null
    /// (nowcast_runtime.rs:786), so that series stays absent with an explicit note.
    private func ratioChart(_ ratios: [ForecastRatioPoint]) -> some View {
        let kinds = ["coverage", "ratio", "transmission"]
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                ForEach(kinds, id: \.self) { kind in
                    if ratios.contains(where: { $0.kind == kind }) {
                        HStack(spacing: 5) {
                            Rectangle().fill(ratioSeriesColor(kind)).frame(width: 12, height: 2.5)
                            Text(ratioSeriesLabel(kind))
                        }.font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if !ratios.contains(where: { $0.kind == "transmission" }) {
                    Text("回程透过率：后端未计算（桌面实况不发布该序列）")
                        .font(.system(size: 9)).foregroundStyle(.tertiary)
                }
            }
            Chart {
                ForEach(ratios) { point in
                    LineMark(x: .value("时间", point.time), y: .value("条件占比", point.value),
                             series: .value("曲线", point.series))
                        .foregroundStyle(ratioSeriesColor(point.kind))
                        .lineStyle(StrokeStyle(lineWidth: 1.8))
                }
            }
            .accessibilityLabel("卫星实况比率趋势，共 \(ratios.count) 个采样点；缺测序列不会补零。")
            .chartYScale(domain: 0...1)
            .chartYAxis {
                AxisMarks(position: .leading, values: [0, 0.5, 1]) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    AxisValueLabel {
                        if let number = value.as(Double.self) {
                            Text("\(Int((number * 100).rounded()))%")
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 5)) { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let date = value.as(Date.self) { Text(ObservationDate.timeLabel(date, timezone: timezone)) }
                    }
                }
            }
            .chartYAxisLabel("条件占比 · 0–100%")
            .frame(height: 128)
            .overlay {
                if ratios.isEmpty {
                    Text(report == nil ? "等待实况分析" : "暂无 0–100% 比例序列")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            .accessibilityLabel("可烧云占比与可烧比例趋势，占比轴固定 0 到 100%，缺测时段断线")
        }
    }

    /// Independent AOD 550 sub-plot with adaptive range, time span and source caption
    /// (plan: AOD550 独立展示取值范围、时次和来源).
    private func aodChart(_ aod: [ForecastCurvePoint]) -> some View {
        let values = aod.map(\.quality)
        let times = aod.map(\.time)
        let domain = Self.aodDomain(aod)
        let source = (report?["aerosol"] as? JSONObject)?["source"] as? String
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("AOD 550 趋势").font(.system(size: 11, weight: .semibold))
                Text(values.isEmpty ? (report == nil ? "等待实况分析" : "暂无可用 AOD550 时序")
                     : String(format: "范围 %.3f–%.3f", values.min() ?? 0, values.max() ?? 0))
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                if let first = times.min(), let last = times.max() {
                    Text("时次 \(ObservationDate.timeLabel(first, timezone: timezone))–\(ObservationDate.timeLabel(last, timezone: timezone))")
                        .font(.system(size: 9)).foregroundStyle(.secondary).monospacedDigit()
                }
                Text("来源 \(source ?? "未提供")")
                    .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1)
            }
            Chart {
                ForEach(aod) { point in
                    LineMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality),
                             series: .value("曲线", point.series))
                        .foregroundStyle(warm).lineStyle(StrokeStyle(lineWidth: 1.8))
                    PointMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality))
                        .foregroundStyle(warm).symbolSize(6)
                }
            }
            .chartYScale(domain: domain)
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 4)) { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let date = value.as(Date.self) { Text(ObservationDate.timeLabel(date, timezone: timezone)) }
                    }
                }
            }
            .chartYAxisLabel("AOD 550")
            .frame(height: 118)
            .overlay {
                if aod.isEmpty {
                    Text(report == nil ? "等待实况分析" : "暂无可用 AOD550 时序")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            .accessibilityLabel("实况 AOD 550 逐时趋势，独立量程，缺测时段断线，不与质量轴混用")
        }
    }

    private static func aodDomain(_ points: [ForecastCurvePoint]) -> ClosedRange<Double> {
        let values = points.map(\.quality)
        guard let low = values.min(), let high = values.max() else { return 0...1 }
        let padding = Swift.max(0.05, (high - low) * 0.25)
        let minLow = Swift.max(0, low - padding)
        return minLow...Swift.max(minLow + 0.1, high + padding)
    }
}

struct WorkNowcastHeroView: View {
    @EnvironmentObject private var store: Store
    private var report: JSONObject? { store.resultKind == .nowcast ? store.result : nil }
    private var nowcast: JSONObject { report?["nowcast"] as? JSONObject ?? [:] }
    private var presentation: NowcastPresentation { NowcastPresentation(report: report) }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                HStack(spacing: 5) {
                    Circle().fill(coolAccent).frame(width: 6, height: 6)
                    Text(L10n.t("卫星实况")).fireSkyFont(size: 11.5, weight: .semibold)
                }
                Text("·").foregroundStyle(.secondary)
                Text("\(store.resultTitle) \(L10n.t(store.resultPhenomenonLabel))")
                    .fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer(minLength: 4)
                Text(L10n.t(report == nil ? "等待分析" : "分析完成"))
                    .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
            }
            WorkStaleResultBanner()
            if report == nil || store.dateTab != "今天" {
                HStack(spacing: 7) {
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 11))
                        .foregroundStyle(coolAccent)
                    Text(L10n.text(
                        "卫星实况仅适用距朝霞/晚霞 4 小时以内的当前观测窗口；远期日期请改用「单点预测」或「区域预测」。",
                        "Satellite nowcast applies within 4 hours of the target dawn/dusk window; use Point or Regional Forecast for later dates."
                    ))
                    .fireSkyFont(size: 10.5, relativeTo: .caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                }
                .padding(.horizontal, 9).padding(.vertical, 5)
                .background(coolAccent.opacity(0.10), in: RoundedRectangle(cornerRadius: 6))
            }
            if let mapCapability = report?["map_capability"] as? JSONObject,
               mapCapability["available"] as? Bool == false {
                let rawReason = mapCapability["reason"] as? String ?? ""
                if rawReason != "native-nowcast-map-publication-not-requested" {
                    Label(friendlyEngineMessage(rawReason.isEmpty ? "实况地图未发布" : rawReason),
                          systemImage: "exclamationmark.triangle")
                        .font(.system(size: 10)).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel(L10n.text("实况地图不可用", "Nowcast map unavailable"))
                }
            }
            HStack(alignment: .center, spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(format(report?["peak_quality_index"]))
                            .fireSkyFont(size: 28, weight: .semibold, relativeTo: .title).monospacedDigit()
                            .foregroundStyle(report == nil ? Color.secondary : warm)
                        Text("/ 2.5").fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                    }
                    HStack(spacing: 6) {
                        Text(L10n.qualityLevel(report?["peak_quality_level"] as? String ?? report?["quality_level"] as? String ?? "峰值质量指数"))
                            .fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary)
                        Text("·").foregroundStyle(.tertiary)
                        Text("\(L10n.text("卫星覆盖", "Satellite coverage"))：\(coverageSummary)")
                            .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }.frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
                Divider().frame(height: 52).opacity(0.5)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(L10n.t("最佳时刻")).fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                        Text(timeLabel(report?["best_time_local"] as? String))
                            .fireSkyFont(size: 20, weight: .semibold, relativeTo: .title2).monospacedDigit()
                    }
                    Text("卫星观测 \(observationTime) · \(sourceText)（\(channelText)）")
                        .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary).lineLimit(1)
                }.frame(maxWidth: .infinity, alignment: .leading)
            }
            if report != nil {
                SkyTimeline(report: report, timezone: store.timezone)
            }
        }.padding(11).frame(maxWidth: .infinity).workSurface(emphasized: true)
    }
    private var observationTime: String {
        guard let value = nowcast["image_valid_time_utc"] as? String,
              let date = ISO8601DateFormatter().date(from: value) else { return "—" }
        return ObservationDate.timeLabel(date, timezone: store.timezone)
    }

    private var coverageSummary: String {
        guard report != nil else { return L10n.text("等待分析", "Waiting for analysis") }
        let stats = presentation.profileStats
        return "\(stats.statusLabel) · \(stats.coverageText)"
    }

    // nowcast-8: real source & channel from the backend (desktop_nowcast_adjustment.rs:991,994),
    // replacing the former hardcoded「GFS 先验 · CAMS 光学」caption.
    private var sourceText: String {
        guard let value = nowcast["source"] as? String, !value.isEmpty else { return "未提供" }
        return value
    }

    private var channelText: String {
        guard let value = nowcast["image_channel"] as? String, !value.isEmpty else { return "—" }
        return value
    }
}

private final class NowcastOverlayState: ObservableObject {
    @Published var showDiagnostics = false
}

struct WorkNowcastResultsView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @StateObject private var overlay = NowcastOverlayState()
    private var report: JSONObject? { store.resultKind == .nowcast ? store.result : nil }
    private var presentation: NowcastPresentation { NowcastPresentation(report: report) }
    private var nowcast: JSONObject { presentation.observation }

    private var cloudMetrics: [(String, String, String)] {
        var specs = Array(NowcastPresentation.metrics[0..<6])
        if let label = nowcast["cloud_type_label"] as? String, !label.isEmpty {
            specs.append(NowcastPresentation.metrics[6])
        }
        return specs
    }

    var body: some View {
        Group {
            if report == nil {
                ScrollView {
                    VStack(spacing: 10) {
                        WorkNowcastHeroView()
                        VStack(alignment: .leading, spacing: 8) {
                            Text(L10n.text("准备进行卫星临近实况分析", "Ready for satellite nowcast analysis"))
                                .fireSkyFont(size: 14, weight: .semibold)
                            Text(L10n.text("点击左侧「开始实况分析」检查并获取最近两帧卫星扫描后，这里会显示云况、运动采样、光学/雷达指标与概率剖面。",
                                           "Click Start Nowcast to fetch the latest paired satellite scans and view cloud coverage, motion sampling, optics, and probability profiles."))
                                .fireSkyFont(size: 12).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .workSurface()
                    }
                    .padding(.trailing, 2)
                }
                .defaultScrollAnchor(.top)
                .scrollIndicators(.hidden)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10, pinnedViews: [.sectionHeaders]) {
                        WorkNowcastHeroView()
                        Section {
                            tabBody
                        } header: {
                            pinnedTabBar
                        }
                    }
                    .padding(.trailing, 2)
                }
                .defaultScrollAnchor(.top)
                .scrollIndicators(.hidden)
            }
        }
        .sheet(isPresented: $overlay.showDiagnostics) {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Text(L10n.t("诊断与时间窗"))
                        .fireSkyFont(size: 13.5, weight: .semibold)
                    Spacer()
                    Button(L10n.close) { overlay.showDiagnostics = false }
                        .buttonStyle(.borderless)
                        .keyboardShortcut(.cancelAction)
                }
                .padding(.horizontal, 16).padding(.vertical, 10)
                Divider()
                ScrollView {
                    VStack(spacing: 12) {
                        WorkDiagnosticView(report: report, profile: false, waiting: "等待实况分析")
                        diagnostics
                    }
                    .padding(14)
                }
            }
            .frame(minWidth: 580, minHeight: 520)
            .tint(warm)
        }
    }

    private var pinnedTabBar: some View {
        HStack(alignment: .center, spacing: 10) {
            WorkAnalysisTabs()
            Spacer(minLength: 8)
            Button {
                overlay.showDiagnostics = true
            } label: {
                Label(L10n.t("诊断"), systemImage: "info.circle")
                    .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Color.primary.opacity(scheme == .dark ? 0.08 : 0.055), in: Capsule())
                    .overlay {
                        Capsule().strokeBorder(Color.primary.opacity(0.08), lineWidth: 0.5)
                    }
            }
            .buttonStyle(.plain)
            .help(L10n.t("查看技术诊断与时间窗元数据"))
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fireSkySurface(role: .navigation, cornerRadius: 12)
    }

    @ViewBuilder
    private var tabBody: some View {
        if store.resultTab == "概览" {
            VStack(spacing: 10) {
                panelSatelliteClouds
                panelMotionAndRadar
                WorkCurveView(report: report, timezone: store.timezone)
            }
        } else if store.resultTab == "逐时台账" {
            WorkNowcastLedgerView(report: report, timezone: store.timezone)
        } else if store.resultTab == "大气剖面" {
            WorkSatelliteProfileView(presentation: presentation)
        } else {
            WorkDiagnosticView(report: report, profile: false, waiting: "等待实况分析")
            diagnostics
        }
    }

    private var panelSatelliteClouds: some View {
        let specs = cloudMetrics
        return VStack(alignment: .leading, spacing: 8) {
            Text(L10n.t("卫星云况"))
                .fireSkyFont(size: 10.5, weight: .semibold, relativeTo: .caption)
                .foregroundStyle(.secondary)
            ViewThatFits(in: .horizontal) {
                structuredGrid(specs: specs, columns: 3)
                    .frame(minWidth: 640)
                structuredGrid(specs: specs, columns: 2)
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private var panelMotionAndRadar: some View {
        let motionSpecs = Array(NowcastPresentation.metrics[7..<11])
        let opticsRadarSpecs = [
            NowcastPresentation.metrics[14],
            NowcastPresentation.metrics[11],
            NowcastPresentation.metrics[12],
            NowcastPresentation.metrics[13],
        ]
        return ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 10) {
                twoByTwoPanel(title: L10n.t("运动与采样"), subtitle: nil, specs: motionSpecs)
                    .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                twoByTwoPanel(title: L10n.t("光学与雷达"), subtitle: radarTimeliness, specs: opticsRadarSpecs)
                    .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            VStack(spacing: 8) {
                twoByTwoPanel(title: L10n.t("运动与采样"), subtitle: nil, specs: motionSpecs)
                twoByTwoPanel(title: L10n.t("光学与雷达"), subtitle: radarTimeliness, specs: opticsRadarSpecs)
            }
        }
    }

    private func twoByTwoPanel(title: String, subtitle: String?, specs: [(String, String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title)
                    .fireSkyFont(size: 10.5, weight: .semibold, relativeTo: .caption)
                    .foregroundStyle(.secondary)
                if let subtitle {
                    Spacer(minLength: 4)
                    Text(subtitle)
                        .fireSkyFont(size: 9.5, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .help(subtitle)
                }
            }
            structuredGrid(specs: specs, columns: 2)
        }
        .padding(11)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .workSurface()
    }

    private func structuredGrid(specs: [(String, String, String)], columns: Int) -> some View {
        let rows = stride(from: 0, to: specs.count, by: columns).map { start in
            Array(specs[start..<min(start + columns, specs.count)])
        }
        return VStack(spacing: 7) {
            ForEach(Array(rows.enumerated()), id: \.offset) { rowIndex, rowSpecs in
                if rowIndex > 0 { Divider().opacity(0.42) }
                HStack(alignment: .top, spacing: 0) {
                    ForEach(0..<columns, id: \.self) { colIndex in
                        if colIndex > 0 { Divider().opacity(0.42) }
                        Group {
                            if colIndex < rowSpecs.count {
                                metricCell(rowSpecs[colIndex])
                            } else {
                                Color.clear.frame(height: 1)
                            }
                        }
                        .padding(.leading, colIndex == 0 ? 0 : 10)
                        .padding(.trailing, colIndex == columns - 1 ? 0 : 10)
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                }
            }
        }
    }

    /// Radar observation timeliness next to the 光学与雷达 group (nowcast-11),
    /// so radar status is visible without switching to the diagnostics tab.
    private var radarTimeliness: String {
        guard let source = nowcast["radar_source"] as? String, !source.isEmpty else {
            return "雷达：后端未接入雷达观测"
        }
        let time = timeLabel(nowcast["radar_valid_time_utc"] as? String)
        let age = number(nowcast["radar_age_minutes"]).map { String(format: "%.0f", $0) } ?? "—"
        return "雷达 \(source) · 观测 \(time) UTC · 帧龄 \(age) 分钟"
    }

    private func metricCell(_ spec: (String, String, String)) -> some View {
        let aod = presentation.aod
        let caption = spec.0 == "aod" ? aod.caption : captionText(spec)
        return VStack(alignment: .leading, spacing: 3) {
            Text(spec.1)
                .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                .foregroundStyle(.primary.opacity(0.84))
                .lineLimit(1)
            if spec.0 == "aod" {
                WorkAODValue(summary: aod)
            } else {
                Text(presentation.value(spec.0))
                    .fireSkyFont(size: 16, weight: .semibold, relativeTo: .title3).monospacedDigit()
                    .foregroundStyle(report == nil ? Color.secondary : Color.primary)
                    .lineLimit(1).minimumScaleFactor(0.78)
            }
            Text(caption)
                .fireSkyFont(size: 9.5, relativeTo: .caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.82)
                .help(caption)
        }
    }

    /// nowcast-9: the geometric path risk card carries its light-path sample count
    /// badge; missing counts stay explicit instead of implying 0 samples.
    private func captionText(_ spec: (String, String, String)) -> String {
        guard spec.0 == "geometric_path_blocking_risk" else { return spec.2 }
        guard let samples = number(nowcast["geometric_path_samples"]) else { return "光路样本数未提供" }
        return "太阳入射路径 · 基于 \(Int(samples)) 个光路样本"
    }

    private var diagnostics: some View {
        VStack(alignment: .leading, spacing: 14) {
            WorkDiagnosticGroup(title: "运行与时效") {
                diagnosticRow("计算后端", report?["compute_backend"])
                diagnosticRow("参考时刻", report?["reference_time_utc"] ?? report?["cycle_time_utc"])
                diagnosticRow("持续状态", report?["duration_status"])
                diagnosticRow("持续时长", report?["duration_minutes"], digits: 0, suffix: " 分钟")
                diagnosticRow("选中场景", sceneLabel(report?["selected_regime"]))
                if let timing = report?["timing_window"] as? JSONObject {
                    diagnosticRow("推荐窗口", "\(timeLabel(timing["start_time_local"] as? String)) – \(timeLabel(timing["end_time_local"] as? String))")
                }
            }
            if let report { WorkSharedDiagnosis(report: report) }
            nowcastScoreComponents
            Text("云体运动与雷达").font(.system(size: 13, weight: .semibold))
            ForEach([
                ("motion_correlation", "云体运动相关性"),
                ("satellite_motion_age_minutes", "卫星帧龄（分钟）"),
                ("satellite_advection_horizon_hours", "外推时长（小时）"),
                ("radar_source", "雷达数据源"), ("radar_valid_time_utc", "雷达观测时间（UTC）"),
                ("radar_max_dbz", "最大回波 dBZ"),
                ("radar_local_precip_ratio", "本地降水比例"), ("radar_path_precip_ratio", "光路降水比例"),
                ("radar_reliability_score", "雷达可靠度"),
            ], id: \.0) { key, title in
                HStack {
                    Text(title).foregroundStyle(.secondary)
                    Spacer()
                    Text(nowcast[key] as? String ?? presentation.value(key)).textSelection(.enabled).lineLimit(2)
                }.font(.system(size: 11))
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }

    /// nowcast-13: read-only score-composition rows from report.quality_components
    /// (nowcast_runtime.rs:956, event components). Nothing is recomputed here; keys
    /// the desktop nowcast does not produce simply show —.
    @ViewBuilder
    private var nowcastScoreComponents: some View {
        if let components = report?["quality_components"] as? JSONObject, !components.isEmpty {
            WorkDiagnosticGroup(title: "评分组成（后端只读 · 不在客户端重算）") {
                diagnosticRow("云量基础分", components["coverage_base_score"] ?? components["coverage_score"], digits: 3)
                diagnosticRow("修正后原始分", components["raw_quality_index"], digits: 3)
                diagnosticRow("可烧云占比（峰值）", components["forward_burnable_cloud_ratio_peak"], percentValue: true)
                diagnosticRow("可烧载荷（有效）", components["forward_burnable_cloud_load_effective"], digits: 3)
                diagnosticRow("前方总云场（峰值）", components["forward_view_cloud_field_amount_peak"], digits: 3)
                diagnosticRow("入射遮挡（均值）", components["solar_incoming_block_risk_mean"], percentValue: true)
                diagnosticRow("持续时间修正", components["duration_adjustment"], digits: 3)
                diagnosticRow("持续时间系数", components["duration_factor"], digits: 3)
                diagnosticRow("入射 AOD 修正", components["incoming_aod_adjustment"], digits: 3)
                diagnosticRow("观测 AOD 修正", components["observer_aod_adjustment"], digits: 3)
                diagnosticRow("材质修正", components["material_adjustment"], digits: 3)
                diagnosticRow("质量用 AOD 550", components["quality_aod550"], digits: 3)
            }
        }
    }
}

private final class SatelliteProfileViewState: ObservableObject {
    @Published var scale = 1.0
    @Published var showFrozenQuery = false
    @Published var showIsobars = true
    @Published var hoverDistanceKM: Double? = nil
    @Published var hoverHeightKM: Double? = nil
}

/// Satellite posterior probability profile: atmospheric Canvas with smooth horizontal
/// cloud bands, cohesive amber-veiled unknown columns (missing != clear sky),
/// optional right-axis GFS-prior pressure lines, hover crosshair readout, and
/// collapsible per-step frozen profile query.
private struct WorkSatelliteProfileView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @StateObject private var state = SatelliteProfileViewState()
    let presentation: NowcastPresentation

    private var zoomScale: Double {
        get { state.scale }
        nonmutating set { state.scale = newValue }
    }

    private let plotLeftMargin: Double = 40
    private let plotTopMargin: Double = 14
    private let plotBottomOffset: Double = 24

    // Web color tokens (SatelliteVerticalProfile.svelte:39,43): retrieved maps the
    // posterior onto rgba(86,143,155,·), unknown stays faint amber behind a dashed border.
    private static let retrieved = Color(red: 86 / 255, green: 143 / 255, blue: 155 / 255)
    private static let retrievedBright = Color(red: 98 / 255, green: 170 / 255, blue: 188 / 255)
    private static let unknownAmber = Color(red: 183 / 255, green: 121 / 255, blue: 31 / 255)
    private static let unknownFill = Color(red: 183 / 255, green: 121 / 255, blue: 31 / 255).opacity(0.11)

    private struct ColumnSlice {
        let startKM: Double
        let endKM: Double
        let cells: [NowcastProfileCell] // sorted by bottomKM ascending
        var isFullyUnknown: Bool { cells.allSatisfy { $0.probability == nil } }
    }

    private static func bandFillColor(_ probability: Double, dark: Bool) -> Color {
        let clamped = Swift.min(100, Swift.max(0, probability))
        if clamped < 15 {
            return retrieved.opacity(0.03 + clamped / 15.0 * 0.11)
        } else if clamped <= 50 {
            let t = (clamped - 15) / 35.0
            return retrieved.opacity((dark ? 0.22 : 0.18) + t * 0.32)
        } else {
            let t = (clamped - 50) / 50.0
            return retrievedBright.opacity((dark ? 0.56 : 0.52) + t * 0.30)
        }
    }

    var body: some View {
        let cells = presentation.profileCells
        let stats = presentation.profileStats
        let hcai = stats.bands.contains("CLOUD_MASK")
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(L10n.t("太阳方向卫星剖面"))
                        .fireSkyFont(size: 12.5, weight: .semibold)
                    Text(presentation.profileBearingCaption)
                        .fireSkyFont(size: 10, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                if !cells.isEmpty {
                    if !stats.pressureRows.isEmpty {
                        isobarToggle
                    }
                    zoomControls
                }
            }
            if stats.present {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), alignment: .leading)],
                          alignment: .leading, spacing: 6) {
                    statChip("时次", stats.validTimeText ?? "—")
                    statChip("状态", stats.statusLabel)
                    statChip("采样点", stats.sampleText)
                    statChip("覆盖率", stats.coverageText)
                    statChip("压力层", "\(stats.pressureCount)")
                    statChip("未知样本", stats.unknownCount.map(String.init) ?? "—")
                }
            }
            if cells.isEmpty {
                Text(presentation.profileUnavailableReason)
                    .fireSkyFont(size: 11)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 220)
            } else {
                takeawayBanner(cells: cells, stats: stats)
                profileCanvas(cells: cells, rows: stats.pressureRows)
                legend(hcai: hcai)
            }
            frozenQueryDisclosure
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private var isobarToggle: some View {
        Button {
            state.showIsobars.toggle()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "waveform.path")
                    .font(.system(size: 9.5, weight: .medium))
                Text(L10n.text("等压线", "Isobars"))
                    .font(.system(size: 10, weight: .medium))
            }
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .foregroundStyle(state.showIsobars ? Self.retrieved : Color.secondary)
            .background(
                state.showIsobars ? Self.retrieved.opacity(scheme == .dark ? 0.22 : 0.14) : Color.secondary.opacity(0.08),
                in: Capsule()
            )
            .overlay {
                Capsule().stroke(
                    state.showIsobars ? Self.retrieved.opacity(0.50) : Color.secondary.opacity(0.22),
                    lineWidth: 0.9
                )
            }
        }
        .buttonStyle(.borderless)
        .help(L10n.text("显示或隐藏右侧 GFS 先验等压线 (hPa)", "Toggle GFS-prior pressure lines (hPa)"))
    }

    private func takeawayBanner(cells: [NowcastProfileCell], stats: NowcastProfileStats) -> some View {
        let bearingText = presentation.profileBearing.map { String(format: "%.1f°", $0) } ?? "—"
        let retrievedCells = cells.compactMap { cell -> (cell: NowcastProfileCell, prob: Double)? in
            guard let p = cell.probability else { return nil }
            return (cell, p)
        }
        let peak = retrievedCells.max { $0.prob < $1.prob }
        let hasSignal = (peak?.prob ?? 0) >= 15.0
        let summaryText: String = {
            let prefix = L10n.text(
                "方位 \(bearingText) · \(stats.statusLabel)（\(stats.coverageText)）",
                "Bearing \(bearingText) · \(stats.statusLabel) (\(stats.coverageText))"
            )
            if let peak, hasSignal {
                let midKM = (peak.cell.bottomKM + peak.cell.topKM) * 0.5
                let detail = L10n.text(
                    "峰值云后验 \(Int(peak.prob.rounded()))%（约 \(String(format: "%.1f", midKM)) km）",
                    "Peak cloud posterior \(Int(peak.prob.rounded()))% (~\(String(format: "%.1f", midKM)) km)"
                )
                return "\(prefix) · \(detail)"
            }
            return "\(prefix) · \(L10n.text("暂无显著云信号", "No significant cloud signal"))"
        }()
        let tint: Color = hasSignal ? Self.retrieved : Self.unknownAmber
        return HStack(spacing: 6) {
            Image(systemName: hasSignal ? "cloud.fill" : "aqi.medium")
                .font(.system(size: 10.5, weight: .semibold))
                .foregroundStyle(tint)
            Text(summaryText)
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(.primary)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 5.5)
        .background(tint.opacity(scheme == .dark ? 0.14 : 0.09), in: RoundedRectangle(cornerRadius: 7))
        .overlay {
            RoundedRectangle(cornerRadius: 7).stroke(tint.opacity(0.32), lineWidth: 0.85)
        }
    }

    private var zoomControls: some View {
        HStack(spacing: 4) {
            Text("\(Int((zoomScale * 100).rounded()))%")
                .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 36, alignment: .trailing)
            Button {
                zoomScale = Swift.max(1, zoomScale - 0.25)
            } label: {
                Image(systemName: "minus.magnifyingglass").frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .help("缩小剖面")
            .disabled(zoomScale <= 1)
            Button {
                zoomScale = Swift.min(2.5, zoomScale + 0.25)
            } label: {
                Image(systemName: "plus.magnifyingglass").frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .help("放大剖面")
            .disabled(zoomScale >= 2.5)
            Button {
                zoomScale = 1
            } label: {
                Image(systemName: "arrow.counterclockwise").frame(width: 22, height: 22)
            }
            .buttonStyle(.borderless)
            .help("适配窗口")
            .disabled(zoomScale == 1)
        }
    }

    private func statChip(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1.5) {
            Text(title).font(.system(size: 8.5)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 10.5, weight: .medium)).monospacedDigit().lineLimit(1)
        }
    }

    private func profileCanvas(cells: [NowcastProfileCell], rows: [NowcastPressureRow]) -> some View {
        let maxDataHeight = Swift.max(cells.map(\.topKM).max() ?? 16.0, rows.map(\.heightKM).max() ?? 15.0)
        let maxHeightKM = Swift.max(16.0, ceil(maxDataHeight))
        let maxDistanceKM = Swift.max(1.0, cells.map(\.endKM).max() ?? 100.0)
        let showRightAxis = state.showIsobars && !rows.isEmpty

        return GeometryReader { geometry in
            let plotWidth = geometry.size.width * CGFloat(zoomScale)
            let plotHeight = geometry.size.height
            ZStack(alignment: .topLeading) {
                ScrollView(.horizontal) {
                    Canvas { context, size in
                        drawProfile(context: context, size: size, cells: cells, rows: rows,
                                    scale: zoomScale, showIsobars: showRightAxis,
                                    hoverDistanceKM: state.hoverDistanceKM, hoverHeightKM: state.hoverHeightKM)
                    }
                    .frame(width: plotWidth, height: plotHeight)
                    .contentShape(Rectangle())
                    .onContinuousHover { phase in
                        switch phase {
                        case .active(let location):
                            let left = plotLeftMargin
                            let right = Double(plotWidth) - (showRightAxis ? 46.0 : 14.0)
                            let top = plotTopMargin
                            let bottom = Double(plotHeight) - plotBottomOffset
                            guard right > left + 10, bottom > top + 10,
                                  Double(location.x) >= left, Double(location.x) <= right,
                                  Double(location.y) >= top, Double(location.y) <= bottom else {
                                state.hoverDistanceKM = nil
                                state.hoverHeightKM = nil
                                return
                            }
                            let xRatio = Swift.min(1.0, Swift.max(0.0, (Double(location.x) - left) / (right - left)))
                            let yRatio = Swift.min(1.0, Swift.max(0.0, 1.0 - (Double(location.y) - top) / (bottom - top)))
                            state.hoverDistanceKM = xRatio * maxDistanceKM
                            state.hoverHeightKM = yRatio * maxHeightKM
                        case .ended:
                            state.hoverDistanceKM = nil
                            state.hoverHeightKM = nil
                        @unknown default:
                            state.hoverDistanceKM = nil
                            state.hoverHeightKM = nil
                        }
                    }
                }
                .frame(height: plotHeight)
                .scrollIndicators(zoomScale > 1 ? .automatic : .hidden)

                if let hd = state.hoverDistanceKM, let hh = state.hoverHeightKM {
                    hoverReadoutCard(cells: cells, distanceKM: hd, heightKM: hh)
                        .padding(.leading, plotLeftMargin + 8)
                        .padding(.top, plotTopMargin + 8)
                        .allowsHitTesting(false)
                }
            }
        }
        .aspectRatio(2.45 / CGFloat(zoomScale), contentMode: .fit)
        .accessibilityLabel("沿太阳方向卫星后验概率剖面，右轴标注 GFS 先验压力层 hPa，未知样本以琥珀虚线幕墙标出，缺测不解释为晴空")
    }

    @ViewBuilder
    private func hoverReadoutCard(cells: [NowcastProfileCell], distanceKM: Double, heightKM: Double) -> some View {
        let exact = cells.first {
            distanceKM >= $0.startKM && distanceKM <= $0.endKM &&
            heightKM >= $0.bottomKM && heightKM <= $0.topKM
        }
        let sameColumn = exact == nil
            ? cells.filter { distanceKM >= $0.startKM && distanceKM <= $0.endKM }
            : []
        let columnUnknown = sameColumn.first(where: { $0.probability == nil }) != nil && sameColumn.allSatisfy { $0.probability == nil }

        let statusText: String = {
            if let exact {
                if let prob = exact.probability {
                    return L10n.text("后验概率 \(Int(prob.rounded()))%", "Posterior \(Int(prob.rounded()))%")
                }
                return L10n.text("未知/缺测", "Unknown / Missing")
            }
            if columnUnknown {
                return L10n.text("未知/缺测", "Unknown / Missing")
            }
            return L10n.text("无采样层", "No sample level")
        }()

        let statusColor: Color = {
            if let exact {
                return exact.probability != nil ? Self.retrieved : Self.unknownAmber
            }
            return columnUnknown ? Self.unknownAmber : .secondary
        }()

        HStack(spacing: 6) {
            Circle().fill(statusColor).frame(width: 6, height: 6)
            Text(L10n.text(
                String(format: "距离 %.0f km · 高度 %.1f km · %@", distanceKM, heightKM, statusText),
                String(format: "%.0f km · %.1f km · %@", distanceKM, heightKM, statusText)
            ))
            .font(.system(size: 10, weight: .medium))
            .monospacedDigit()
        }
        .padding(.horizontal, 8.5)
        .padding(.vertical, 5)
        .fireSkySurface(role: .overlay, cornerRadius: 8)
    }

    private func buildColumns(from cells: [NowcastProfileCell]) -> [ColumnSlice] {
        var grouped: [String: (start: Double, end: Double, cells: [NowcastProfileCell])] = [:]
        for cell in cells {
            let key = "\(Int((cell.startKM * 100).rounded()))-\(Int((cell.endKM * 100).rounded()))"
            if var existing = grouped[key] {
                existing.cells.append(cell)
                grouped[key] = existing
            } else {
                grouped[key] = (cell.startKM, cell.endKM, [cell])
            }
        }
        return grouped.values
            .sorted { $0.start < $1.start }
            .map { ColumnSlice(startKM: $0.start, endKM: $0.end, cells: $0.cells.sorted { $0.bottomKM < $1.bottomKM }) }
    }

    private func midpoint(_ a: CGPoint, _ b: CGPoint) -> CGPoint {
        CGPoint(x: (a.x + b.x) * 0.5, y: (a.y + b.y) * 0.5)
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

    private func drawProfile(context: GraphicsContext, size: CGSize, cells: [NowcastProfileCell],
                             rows: [NowcastPressureRow], scale: Double, showIsobars: Bool,
                             hoverDistanceKM: Double?, hoverHeightKM: Double?) {
        let top = plotTopMargin
        let bottom = size.height - plotBottomOffset
        guard bottom > top + 20 else { return }
        let maxDataHeight = Swift.max(cells.map(\.topKM).max() ?? 16.0, rows.map(\.heightKM).max() ?? 15.0)
        let maxHeightKM = Swift.max(16.0, ceil(maxDataHeight))
        let maxDistanceKM = Swift.max(1.0, cells.map(\.endKM).max() ?? 100.0)
        let labelSize = CGFloat(Swift.min(13, Swift.max(9.5, 9.5 * scale)))
        let left = plotLeftMargin
        let right = showIsobars ? size.width - 46.0 : size.width - 14.0
        guard right > left + 24 else { return }

        let x = { (km: Double) in left + Swift.min(Swift.max(km, 0), maxDistanceKM) / maxDistanceKM * (right - left) }
        let y = { (km: Double) in top + (1.0 - Swift.min(Swift.max(km, 0), maxHeightKM) / maxHeightKM) * (bottom - top) }

        let plotRect = CGRect(x: left, y: top, width: right - left, height: bottom - top)
        let plotPath = Path(roundedRect: plotRect, cornerRadius: 10)
        let columns = buildColumns(from: cells)

        context.drawLayer { layer in
            layer.clip(to: plotPath)

            // Atmospheric twilight sky backdrop warming toward the solar horizon on the right
            let skyGradient = Gradient(colors: scheme == .dark
                ? [Color(red: 0.06, green: 0.10, blue: 0.19),
                   Color(red: 0.12, green: 0.17, blue: 0.27),
                   Color(red: 0.22, green: 0.19, blue: 0.22)]
                : [Color(red: 0.80, green: 0.88, blue: 0.96),
                   Color(red: 0.91, green: 0.94, blue: 0.98),
                   Color(red: 0.98, green: 0.92, blue: 0.86)])
            layer.fill(plotPath, with: .linearGradient(skyGradient,
                                                        startPoint: CGPoint(x: left, y: top),
                                                        endPoint: CGPoint(x: left, y: bottom)))
            let horizonWash = Gradient(colors: [
                Color.clear,
                Color(red: 0.98, green: 0.63, blue: 0.34).opacity(scheme == .dark ? 0.16 : 0.18)
            ])
            layer.fill(plotPath, with: .linearGradient(horizonWash,
                                                        startPoint: CGPoint(x: left + (right - left) * 0.40, y: top + (bottom - top) * 0.25),
                                                        endPoint: CGPoint(x: right, y: bottom)))

            // Gridlines
            let heightStep = maxHeightKM <= 16 ? 4.0 : 5.0
            for km in stride(from: 0.0, through: maxHeightKM, by: heightStep) {
                let lineY = y(km)
                var grid = Path()
                grid.move(to: CGPoint(x: left, y: lineY))
                grid.addLine(to: CGPoint(x: right, y: lineY))
                layer.stroke(grid, with: .color(Color.secondary.opacity(0.14)), lineWidth: 0.6)
            }

            let distanceStep = maxDistanceKM <= 120 ? 20.0 : maxDistanceKM <= 300 ? 50.0 : 100.0
            for km in stride(from: 0.0, through: maxDistanceKM, by: distanceStep) where km > 0 {
                let lineX = x(km)
                var grid = Path()
                grid.move(to: CGPoint(x: lineX, y: top))
                grid.addLine(to: CGPoint(x: lineX, y: bottom))
                layer.stroke(grid, with: .color(Color.secondary.opacity(0.11)),
                             style: StrokeStyle(lineWidth: 0.6, dash: [3, 4]))
            }

            // Smooth continuous horizontal cloud probability bands across contiguous retrieved columns
            var colIdx = 0
            while colIdx < columns.count {
                if columns[colIdx].isFullyUnknown {
                    // Group contiguous fully-unknown columns into one cohesive amber-tinted column veil
                    var endIdx = colIdx
                    while endIdx + 1 < columns.count && columns[endIdx + 1].isFullyUnknown {
                        endIdx += 1
                    }
                    let veilLeft = Swift.max(left, x(columns[colIdx].startKM))
                    let veilRight = Swift.min(right, x(columns[endIdx].endKM))
                    let veilRect = CGRect(x: veilLeft, y: top + 1,
                                          width: Swift.max(1, veilRight - veilLeft),
                                          height: Swift.max(1, bottom - top - 2))
                    let veilPath = Path(roundedRect: veilRect, cornerRadius: 4)
                    layer.fill(veilPath, with: .color(Self.unknownFill))

                    layer.drawLayer { hatchLayer in
                        hatchLayer.clip(to: veilPath)
                        let span = veilRect.width + veilRect.height
                        for offset in stride(from: -veilRect.height, through: span, by: 10.0) {
                            var hatch = Path()
                            hatch.move(to: CGPoint(x: veilRect.minX + offset, y: veilRect.maxY))
                            hatch.addLine(to: CGPoint(x: veilRect.minX + offset + veilRect.height * 0.45, y: veilRect.minY))
                            hatchLayer.stroke(hatch, with: .color(Self.unknownAmber.opacity(0.14)), lineWidth: 0.8)
                        }
                    }

                    layer.stroke(veilPath, with: .color(Self.unknownAmber.opacity(0.75)),
                                 style: StrokeStyle(lineWidth: 1.0, dash: [4, 3]))

                    if veilRect.width >= 38 {
                        let badgeText = Text(L10n.text("未知/缺测", "Unknown"))
                            .font(.system(size: 8.5, weight: .semibold))
                            .foregroundStyle(Self.unknownAmber)
                        let resolved = layer.resolve(badgeText)
                        let bSize = resolved.measure(in: size)
                        if bSize.width + 10 <= veilRect.width {
                            let pillRect = CGRect(x: veilRect.midX - (bSize.width + 10) * 0.5,
                                                  y: veilRect.midY - (bSize.height + 4) * 0.5,
                                                  width: bSize.width + 10, height: bSize.height + 4)
                            layer.fill(Path(roundedRect: pillRect, cornerRadius: pillRect.height * 0.5),
                                       with: .color((scheme == .dark ? Color.black : Color.white).opacity(0.65)))
                            layer.stroke(Path(roundedRect: pillRect, cornerRadius: pillRect.height * 0.5),
                                         with: .color(Self.unknownAmber.opacity(0.55)), lineWidth: 0.7)
                            layer.draw(resolved, at: CGPoint(x: pillRect.midX, y: pillRect.midY), anchor: .center)
                        }
                    }

                    colIdx = endIdx + 1
                    continue
                }

                // Contiguous run of retrieved columns (with at least some retrieved levels)
                var endIdx = colIdx
                while endIdx + 1 < columns.count && !columns[endIdx + 1].isFullyUnknown {
                    endIdx += 1
                }
                let runCols = Array(columns[colIdx...endIdx])
                let maxLayers = runCols.map(\.cells.count).max() ?? 0

                for layerIndex in 0..<maxLayers {
                    var segStart = 0
                    while segStart < runCols.count {
                        guard runCols[segStart].cells.indices.contains(layerIndex),
                              runCols[segStart].cells[layerIndex].probability != nil else {
                            segStart += 1
                            continue
                        }
                        var segEnd = segStart
                        while segEnd + 1 < runCols.count,
                              runCols[segEnd + 1].cells.indices.contains(layerIndex),
                              runCols[segEnd + 1].cells[layerIndex].probability != nil {
                            segEnd += 1
                        }

                        let bandCells = (segStart...segEnd).map { runCols[$0].cells[layerIndex] }
                        let bandLeft = Swift.max(left, x(bandCells[0].startKM))
                        let bandRight = Swift.min(right, x(bandCells[bandCells.count - 1].endKM))
                        let bandWidth = Swift.max(1.0, bandRight - bandLeft)

                        var topPts: [CGPoint] = []
                        var botPts: [CGPoint] = []
                        for (idx, cell) in bandCells.enumerated() {
                            let cLeft = Swift.max(left, x(cell.startKM))
                            let cRight = Swift.min(right, x(cell.endKM))
                            let cMid = (cLeft + cRight) * 0.5
                            let tY = Swift.max(top + 1, y(cell.topKM))
                            let bY = Swift.min(bottom - 1, Swift.max(tY + 2, y(cell.bottomKM)))
                            if idx == 0 {
                                topPts.append(CGPoint(x: cLeft, y: tY))
                                botPts.append(CGPoint(x: cLeft, y: bY))
                            }
                            topPts.append(CGPoint(x: cMid, y: tY))
                            botPts.append(CGPoint(x: cMid, y: bY))
                            if idx == bandCells.count - 1 {
                                topPts.append(CGPoint(x: cRight, y: tY))
                                botPts.append(CGPoint(x: cRight, y: bY))
                            }
                        }

                        var bandPath = Path()
                        if bandCells.count == 1 {
                            let rect = CGRect(x: bandLeft, y: topPts[0].y,
                                              width: bandWidth, height: Swift.max(2, botPts[0].y - topPts[0].y))
                            bandPath = Path(roundedRect: rect, cornerRadius: Swift.min(4.0, rect.height * 0.35))
                        } else {
                            bandPath.move(to: topPts[0])
                            appendSmoothSegment(to: &bandPath, points: topPts)
                            let revBot = Array(botPts.reversed())
                            bandPath.addLine(to: revBot[0])
                            appendSmoothSegment(to: &bandPath, points: revBot)
                            bandPath.closeSubpath()
                        }

                        var stops: [Gradient.Stop] = []
                        for cell in bandCells {
                            let prob = cell.probability ?? 0
                            let cMid = (Swift.max(left, x(cell.startKM)) + Swift.min(right, x(cell.endKM))) * 0.5
                            let loc = Swift.min(1.0, Swift.max(0.0, (cMid - bandLeft) / bandWidth))
                            stops.append(.init(color: Self.bandFillColor(prob, dark: scheme == .dark), location: loc))
                        }
                        if stops.count == 1 {
                            layer.fill(bandPath, with: .color(stops[0].color))
                        } else {
                            layer.fill(
                                bandPath,
                                with: .linearGradient(Gradient(stops: stops),
                                                      startPoint: CGPoint(x: bandLeft, y: top),
                                                      endPoint: CGPoint(x: bandRight, y: top))
                            )
                        }

                        // Soft contour highlight on dense cores >= 45%
                        var coreStart = 0
                        while coreStart < bandCells.count {
                            guard (bandCells[coreStart].probability ?? 0) >= 45 else {
                                coreStart += 1
                                continue
                            }
                            var coreEnd = coreStart
                            while coreEnd + 1 < bandCells.count && (bandCells[coreEnd + 1].probability ?? 0) >= 45 {
                                coreEnd += 1
                            }
                            var coreTop: [CGPoint] = []
                            var coreBot: [CGPoint] = []
                            for ci in coreStart...coreEnd {
                                let c = bandCells[ci]
                                let cLeft = Swift.max(left, x(c.startKM))
                                let cRight = Swift.min(right, x(c.endKM))
                                let cMid = (cLeft + cRight) * 0.5
                                let tY = Swift.max(top + 1, y(c.topKM))
                                let bY = Swift.min(bottom - 1, Swift.max(tY + 2, y(c.bottomKM)))
                                if ci == coreStart {
                                    coreTop.append(CGPoint(x: cLeft, y: tY))
                                    coreBot.append(CGPoint(x: cLeft, y: bY))
                                }
                                coreTop.append(CGPoint(x: cMid, y: tY))
                                coreBot.append(CGPoint(x: cMid, y: bY))
                                if ci == coreEnd {
                                    coreTop.append(CGPoint(x: cRight, y: tY))
                                    coreBot.append(CGPoint(x: cRight, y: bY))
                                }
                            }
                            var corePath = Path()
                            corePath.move(to: coreTop[0])
                            appendSmoothSegment(to: &corePath, points: coreTop)
                            let revCoreBot = Array(coreBot.reversed())
                            corePath.addLine(to: revCoreBot[0])
                            appendSmoothSegment(to: &corePath, points: revCoreBot)
                            corePath.closeSubpath()
                            layer.stroke(corePath, with: .color(Self.retrievedBright.opacity(0.62)), lineWidth: 1.1)

                            coreStart = coreEnd + 1
                        }

                        segStart = segEnd + 1
                    }
                }

                // Individual unknown level cells inside an otherwise retrieved column
                for col in runCols {
                    for cell in col.cells where cell.probability == nil {
                        let cLeft = Swift.max(left, x(cell.startKM))
                        let cRight = Swift.min(right, x(cell.endKM))
                        let cTop = Swift.max(top + 1, y(cell.topKM))
                        let cBottom = Swift.min(bottom - 1, y(cell.bottomKM))
                        guard cRight > cLeft, cBottom > cTop else { continue }
                        let rect = CGRect(x: cLeft, y: cTop, width: cRight - cLeft, height: cBottom - cTop)
                        let shape = Path(roundedRect: rect, cornerRadius: 3)
                        layer.fill(shape, with: .color(Self.unknownFill))
                        layer.stroke(shape, with: .color(Self.unknownAmber.opacity(0.80)),
                                     style: StrokeStyle(lineWidth: 0.9, dash: [3, 2]))
                    }
                }

                colIdx = endIdx + 1
            }

            // GFS-prior pressure lines (toggleable)
            if showIsobars {
                for row in rows.sorted(by: { $0.heightKM > $1.heightKM }) {
                    guard (0...maxHeightKM).contains(row.heightKM) else { continue }
                    let lineY = y(row.heightKM)
                    var refLine = Path()
                    refLine.move(to: CGPoint(x: left, y: lineY))
                    refLine.addLine(to: CGPoint(x: right, y: lineY))
                    layer.stroke(refLine, with: .color(Self.retrieved.opacity(0.28)),
                                 style: StrokeStyle(lineWidth: 0.6, dash: [4, 3]))
                }
            }

            // Observer beacon & right-edge sun direction cue
            let observer = CGPoint(x: x(0), y: bottom - 5)
            let halo = CGRect(x: observer.x - 7, y: observer.y - 7, width: 14, height: 14)
            layer.fill(Path(ellipseIn: halo), with: .color(warm.opacity(0.26)))
            let dot = CGRect(x: observer.x - 4, y: observer.y - 4, width: 8, height: 8)
            layer.fill(Path(ellipseIn: dot), with: .color(warm))
            layer.stroke(Path(ellipseIn: dot), with: .color(.white.opacity(0.9)), lineWidth: 1.2)
            layer.draw(
                Text(L10n.text("观测点", "Observer")).font(.system(size: labelSize, weight: .semibold)).foregroundStyle(.primary),
                at: CGPoint(x: observer.x + 9, y: observer.y - 8),
                anchor: .leading
            )

            let sunCue: String = {
                if let bearing = presentation.profileBearing {
                    return L10n.text(String(format: "☀ 太阳方向 %.0f°", bearing), String(format: "☀ Sun %.0f°", bearing))
                }
                return L10n.text("☀ 太阳方向", "☀ Sun")
            }()
            layer.draw(
                Text(sunCue).font(.system(size: 9.5, weight: .semibold)).foregroundStyle(Color(red: 0.98, green: 0.72, blue: 0.34)),
                at: CGPoint(x: right - 10, y: top + 12),
                anchor: .trailing
            )

            // Hover crosshair
            if let hoverDistanceKM, let hoverHeightKM {
                let hx = x(hoverDistanceKM)
                let hy = y(hoverHeightKM)
                var vLine = Path()
                vLine.move(to: CGPoint(x: hx, y: top))
                vLine.addLine(to: CGPoint(x: hx, y: bottom))
                layer.stroke(vLine, with: .color(Color.primary.opacity(0.42)),
                             style: StrokeStyle(lineWidth: 0.9, dash: [3, 3]))
                var hLine = Path()
                hLine.move(to: CGPoint(x: left, y: hy))
                hLine.addLine(to: CGPoint(x: right, y: hy))
                layer.stroke(hLine, with: .color(Color.primary.opacity(0.30)),
                             style: StrokeStyle(lineWidth: 0.8, dash: [3, 3]))
            }
        }

        context.stroke(plotPath, with: .color(Color.secondary.opacity(scheme == .dark ? 0.28 : 0.22)), lineWidth: 0.9)

        // Outside axis labels: height (left)
        let heightStep = maxHeightKM <= 16 ? 4.0 : 5.0
        for km in stride(from: 0.0, through: maxHeightKM, by: heightStep) {
            context.draw(Text("\(Int(km))km").font(.system(size: labelSize)).foregroundStyle(.secondary),
                         at: CGPoint(x: left - 5, y: y(km)), anchor: .trailing)
        }

        // Outside axis labels: distance (bottom, km > 0)
        let distanceStep = maxDistanceKM <= 120 ? 20.0 : maxDistanceKM <= 300 ? 50.0 : 100.0
        for km in stride(from: 0.0, through: maxDistanceKM, by: distanceStep) where km > 0 {
            context.draw(Text("\(Int(km))km").font(.system(size: labelSize)).foregroundStyle(.secondary),
                         at: CGPoint(x: x(km), y: bottom + 10),
                         anchor: km >= maxDistanceKM - 1 ? .trailing : .center)
        }

        // Outside axis labels: pressure rows (right)
        if showIsobars {
            var lastLabelY = -Double.infinity
            for row in rows.sorted(by: { $0.heightKM > $1.heightKM }) {
                guard (0...maxHeightKM).contains(row.heightKM) else { continue }
                let lineY = y(row.heightKM)
                if abs(lineY - lastLabelY) >= 12 {
                    lastLabelY = lineY
                    context.draw(Text("\(row.pressure)hPa").font(.system(size: Swift.max(8.5, labelSize - 0.5))).foregroundStyle(Self.retrieved),
                                 at: CGPoint(x: right + 5, y: lineY), anchor: .leading)
                }
            }
        }
    }

    private func legend(hcai: Bool) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 12) {
                HStack(spacing: 5) {
                    LinearGradient(colors: [Self.retrieved.opacity(0.14), Self.retrievedBright.opacity(0.84)],
                                   startPoint: .leading, endPoint: .trailing)
                        .frame(width: 56, height: 6).clipShape(Capsule())
                        .overlay { Capsule().stroke(.secondary.opacity(0.3), lineWidth: 0.5) }
                    Text("卫星后验概率 0–100%")
                }
                HStack(spacing: 5) {
                    Rectangle().fill(Self.unknownFill)
                        .overlay { Rectangle().stroke(Self.unknownAmber, style: StrokeStyle(lineWidth: 1, dash: [3, 2])) }
                        .frame(width: 11, height: 7)
                    Text("未知/缺失（非晴空）")
                }
                HStack(spacing: 5) {
                    Rectangle().fill(Color.clear)
                        .overlay { Rectangle().stroke(Self.retrieved.opacity(0.6), style: StrokeStyle(lineWidth: 1, dash: [4, 3])) }
                        .frame(width: 12, height: 1)
                    Text("GFS 压力层先验（hPa）")
                }
            }
            Text(hcai
                 ? "HCAI 官方云掩膜/云相/云顶与 COT 产品 · 缺测不解释为晴空。"
                 : "HSD B07/B13/B15 云概率代理 · 连续高度层来自 GFS 先验（非观测真值），缺测不解释为晴空。")
        }.font(.system(size: 9)).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var frozenQueryDisclosure: some View {
        let steps = (presentation.report?["steps"] as? [JSONObject] ?? [])
            .compactMap { $0["valid_time_local"] as? String }
        let available = (presentation.report?["profile_source"] as? JSONObject)?["available"] as? Bool == true
        return VStack(alignment: .leading, spacing: 6) {
            Divider().opacity(0.45)
            Button {
                state.showFrozenQuery.toggle()
            } label: {
                HStack(spacing: 6) {
                    Text(L10n.t("按时次查询冻结剖面"))
                        .fireSkyFont(size: 11, weight: .medium)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Image(systemName: state.showFrozenQuery ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.secondary)
                }
            }
            .buttonStyle(.borderless)
            if state.showFrozenQuery {
                if !available || steps.isEmpty {
                    Text("此结果没有可查询的冻结卫星剖面。")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                } else {
                    HStack(spacing: 8) {
                        Picker("时次", selection: $store.nowcastProfileStep) {
                            ForEach(steps, id: \.self) { step in
                                Text(step.replacingOccurrences(of: "T", with: " ")).tag(step)
                            }
                        }
                        .labelsHidden()
                        .frame(maxWidth: 260)
                        Button("读取", systemImage: "waveform.path.ecg") {
                            store.queryNowcastProfile(validTimeLocal: store.nowcastProfileStep)
                        }
                        .disabled(store.busy || store.nowcastProfileStep.isEmpty)
                        .help("读取所选时次的冻结卫星剖面")
                    }
                    FrozenProfileView(model: "satellite") {
                        if !store.nowcastProfileStep.isEmpty { store.queryNowcastProfile(validTimeLocal: store.nowcastProfileStep) }
                    }
                }
            }
        }
        .onAppear {
            if store.nowcastProfileStep.isEmpty {
                store.nowcastProfileStep = (presentation.report?["best_time_local"] as? String) ?? steps.first ?? ""
            }
        }
    }
}

private struct WorkNowcastLedgerView: View {
    let report: JSONObject?
    let timezone: String
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(spacing: 0) {
                HStack(spacing: 0) {
                    Text("时间").frame(width: 78, alignment: .leading)
                    Text("质量").frame(width: 68, alignment: .leading)
                    Text("档位").frame(width: 92, alignment: .leading)
                    Text("可烧").frame(width: 52, alignment: .leading)
                    Text("可烧云量").frame(width: 84, alignment: .leading)
                    Text("亮度颜色").frame(width: 68, alignment: .leading)
                    Text("可烧比例").frame(width: 68, alignment: .leading)
                    Text("太阳入射阻挡").frame(width: 88, alignment: .leading)
                    Text("AOD 550").frame(width: 72, alignment: .leading)
                    Text("太阳仰角").frame(width: 78, alignment: .leading)
                }
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                Divider()
                ForEach(Array((report?["steps"] as? [JSONObject] ?? []).enumerated()), id: \.offset) { _, step in
                    let valid = ForecastComparison.isStepValid(step)
                    let burnable = step["physically_burnable"] as? Bool
                    HStack(spacing: 0) {
                        Text(timestamp(step)).frame(width: 78, alignment: .leading)
                        Text(valid ? format(step["instant_quality_index"], digits: 3) : "—")
                            .frame(width: 68, alignment: .leading)
                        Text(valid ? step["instant_quality_level"] as? String ?? "—" : "缺测")
                            .frame(width: 92, alignment: .leading)
                        Image(systemName: !valid || burnable == nil ? "minus"
                              : burnable == true ? "checkmark" : "xmark")
                            .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? coolAccent : Color.orange)
                            .frame(width: 52, alignment: .leading)
                            .accessibilityLabel(!valid ? "缺测" : burnable == true ? "可烧" : burnable == false ? "不可烧" : "未提供")
                        Text(valid ? percent(step["burnable_cloud_coverage"]) : "—")
                            .frame(width: 84, alignment: .leading)
                        Text(valid ? ratioText(step["brightness_color"]) : "—")
                            .frame(width: 68, alignment: .leading)
                        Text(valid ? ratioText(step["forward_burnable_cloud_ratio"]) : "—")
                            .frame(width: 68, alignment: .leading)
                        Text(valid ? ratioText(step["solar_incoming_block_risk"]) : "—")
                            .frame(width: 88, alignment: .leading)
                        Text(valid ? format(step["quality_aod550"], digits: 2) : "—")
                            .frame(width: 72, alignment: .leading)
                        Text(valid ? format(step["solar_elevation_deg"], digits: 1) : "—")
                            .frame(width: 78, alignment: .leading)
                    }
                    .font(.system(size: 11))
                    .monospacedDigit()
                    .padding(.vertical, 7)
                    .padding(.horizontal, 10)
                    Divider().opacity(0.5)
                }
                if report == nil {
                    Text("等待实况分析").foregroundStyle(.secondary).padding(40)
                }
                Text("亮度颜色 / 可烧比例 / 太阳入射阻挡为后端 0–1 比例字段，— 表示缺测；回程透过率与逐时云底–云顶桌面实况未计算，故不设该两列（不伪造）。点击行不驱动地图。")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(minWidth: 756, alignment: .leading)
        }
        .workSurface()
    }
    private func timestamp(_ step: JSONObject) -> String {
        if let local = step["valid_time_local"] as? String { return timeLabel(local) }
        guard let utc = step["valid_time_utc"] as? String,
              let date = ISO8601DateFormatter().date(from: utc) else { return "—" }
        return ObservationDate.timeLabel(date, timezone: timezone)
    }
}
