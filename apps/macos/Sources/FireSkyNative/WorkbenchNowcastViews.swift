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
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("霞光质量变化").font(.system(size: 13, weight: .semibold))
                Text("质量轴固定 0–2.5 · 比例轴 0–100% · 缺测断线不补零")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Label("质量指数", systemImage: "circle.fill").font(.system(size: 9)).foregroundStyle(warm).workChip()
            }
            qualityChart(points)
            ratioChart(ratios)
            aodChart(aod)
        }.padding(16).workSurface()
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
        VStack(alignment: .leading, spacing: 14) {
            Text("\(store.resultTitle) \(store.resultPhenomenonLabel)")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            WorkStaleResultBanner()
            if report == nil || store.dateTab != "今天" {
                HStack(spacing: 8) {
                    Image(systemName: "clock.badge.exclamationmark")
                        .foregroundStyle(coolAccent)
                    Text(L10n.text(
                        "卫星实况仅适用距朝霞/晚霞 4 小时以内的当前观测窗口；远期日期请改用「单点预测」或「区域预测」。",
                        "Satellite nowcast applies within 4 hours of the target dawn/dusk window; use Point or Regional Forecast for later dates."
                    ))
                    .fireSkyFont(size: 11, relativeTo: .caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                }
                .padding(.horizontal, 10).padding(.vertical, 7)
                .background(coolAccent.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
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
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 9) {
                    HStack(spacing: 6) {
                        Circle().fill(coolAccent).frame(width: 6, height: 6)
                        Text(L10n.t("卫星实况")).font(.system(size: 12, weight: .semibold))
                        Spacer(minLength: 4)
                        Text(L10n.t(report == nil ? "等待分析" : "分析完成"))
                            .font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Text(format(report?["peak_quality_index"]))
                            .font(.system(size: 32, weight: .semibold)).monospacedDigit()
                            .foregroundStyle(report == nil ? Color.secondary : warm)
                        Text("/ 2.5").font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                    Text(report?["peak_quality_level"] as? String ?? report?["quality_level"] as? String ?? "峰值质量指数")
                        .font(.system(size: 9)).foregroundStyle(.secondary)
                    Text("推荐窗口 \(windowLabel(report))").font(.system(size: 10)).foregroundStyle(.secondary)
                    Text("\(L10n.text("卫星覆盖", "Satellite coverage"))：\(coverageSummary)")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                        .lineLimit(1)
                }.frame(maxWidth: .infinity, minHeight: 124, alignment: .topLeading)
                Divider().frame(height: 124).opacity(0.5)
                VStack(alignment: .leading, spacing: 9) {
                    Text("最佳时刻").font(.system(size: 9)).foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .font(.system(size: 22, weight: .semibold)).monospacedDigit()
                    Text("卫星观测 \(observationTime)").font(.system(size: 10)).foregroundStyle(.secondary)
                    Text("实况源：\(sourceText) · 通道 \(channelText)")
                        .font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(2)
                }.frame(maxWidth: .infinity, alignment: .topLeading)
            }
        }.padding(18).frame(maxWidth: .infinity).workSurface(emphasized: true)
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

struct WorkNowcastResultsView: View {
    @EnvironmentObject private var store: Store
    private var report: JSONObject? { store.resultKind == .nowcast ? store.result : nil }
    private var presentation: NowcastPresentation { NowcastPresentation(report: report) }
    private var nowcast: JSONObject { presentation.observation }
    // Plan row 29 grouping: 卫星云况 / 运动采样 / 光学与雷达 (nowcast-4/7/11).
    private let groups = [("卫星云况", 0..<7), ("运动采样", 7..<11), ("光学与雷达", 11..<15)]
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if report == nil {
                VStack(alignment: .leading, spacing: 8) {
                    Text(L10n.text("准备进行卫星临近实况分析", "Ready for satellite nowcast analysis"))
                        .fireSkyFont(size: 14, weight: .semibold)
                    Text(L10n.text("点击左侧「开始实况分析」检查并获取最近两帧卫星扫描后，这里会显示云况、运动采样、光学/雷达指标与概率剖面。",
                                   "Click Start Nowcast to fetch the latest paired satellite scans and view cloud coverage, motion sampling, optics, and probability profiles."))
                        .fireSkyFont(size: 12).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
                .workSurface()
            } else {
                WorkAnalysisTabs()
                ScrollView {
                    VStack(spacing: 12) {
                        if store.resultTab == "概览" {
                            ForEach(groups, id: \.0) { title, range in
                                VStack(alignment: .leading, spacing: 7) {
                                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                                        Text(L10n.t(title)).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                                        if title == "光学与雷达" {
                                            Text(radarTimeliness)
                                                .font(.system(size: 9)).foregroundStyle(.secondary)
                                                .lineLimit(1).help(radarTimeliness)
                                        }
                                        Spacer(minLength: 0)
                                    }
                                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 185), spacing: 10)], spacing: 10) {
                                        ForEach(Array(NowcastPresentation.metrics[range]), id: \.0) { spec in
                                            metric(spec)
                                        }
                                    }
                                }
                            }
                            WorkCurveView(report: report, timezone: store.timezone)
                        } else if store.resultTab == "逐时台账" {
                            WorkNowcastLedgerView(report: report, timezone: store.timezone)
                        } else if store.resultTab == "大气剖面" {
                            WorkSatelliteProfileView(presentation: presentation)
                        } else {
                            WorkDiagnosticView(report: report, profile: false, waiting: "等待实况分析")
                            diagnostics
                        }
                    }.padding(.trailing, 2)
                }.defaultScrollAnchor(.top).scrollIndicators(.hidden)
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

    private func metric(_ spec: (String, String, String)) -> some View {
        let aod = presentation.aod
        return VStack(alignment: .leading, spacing: 7) {
            Text(spec.1).font(.system(size: 12)).foregroundStyle(.primary.opacity(0.8))
            if spec.0 == "aod" {
                WorkAODValue(summary: aod)
            } else {
                Text(presentation.value(spec.0))
                    .font(.system(size: spec.0 == "satellite_low_cloud_state" || spec.0 == "satellite_motion_quality"
                                  || spec.0 == "cloud_type_label" ? 18 : 23,
                                  weight: .semibold)).monospacedDigit()
                    .foregroundStyle(report == nil ? Color.secondary : Color.primary)
                    .lineLimit(2).minimumScaleFactor(0.8)
                    .frame(maxHeight: .infinity, alignment: .leading)
            }
            Text(spec.0 == "aod" ? aod.caption : captionText(spec))
                .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(2)
        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: 112).workSurface()
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

/// Satellite posterior probability profile (plan rows nowcast-1/2/3, Web parity with
/// SatelliteVerticalProfile.svelte): header coverage stats, coolAccent retrieved-alpha scale,
/// amber dashed unknown cells, pressure-level row lines with hPa labels on a trailing
/// axis anchored to the same height scale, and the HSD posterior / GFS prior source legend.
private struct WorkSatelliteProfileView: View {
    @EnvironmentObject private var store: Store
    let presentation: NowcastPresentation

    // Web color tokens (SatelliteVerticalProfile.svelte:39,43): retrieved maps the
    // posterior onto rgba(86,143,155,·), unknown stays faint amber behind a dashed border.
    private static let retrieved = Color(red: 86 / 255, green: 143 / 255, blue: 155 / 255)
    private static let unknownAmber = Color(red: 183 / 255, green: 121 / 255, blue: 31 / 255)
    private static let unknownFill = Color(red: 183 / 255, green: 121 / 255, blue: 31 / 255).opacity(0.05)

    private static func retrievedFill(_ probability: Double) -> Color {
        retrieved.opacity(0.12 + min(100, max(0, probability)) / 150)
    }

    /// Dashed outline of one unknown cell, drawn as a closed 5-point LineMark series.
    private struct UnknownCellBorder: Identifiable {
        let id: String
        let corners: [(Double, Double)]
    }

    var body: some View {
        let cells = presentation.profileCells
        let stats = presentation.profileStats
        let hcai = stats.bands.contains("CLOUD_MASK")
        return VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("卫星冻结剖面").font(.system(size: 13, weight: .semibold))
                Spacer()
                Text("云层后验概率").font(.system(size: 10)).foregroundStyle(.secondary)
            }
            Text(presentation.profileBearingCaption).font(.system(size: 10)).foregroundStyle(.secondary)
            if stats.present {
                // nowcast-2: coverage status & sampling statistics header.
                HStack(spacing: 16) {
                    statChip("时次", stats.validTimeText ?? "—")
                    statChip("状态", stats.statusLabel)
                    statChip("采样点", stats.sampleText)
                    statChip("覆盖率", stats.coverageText)
                    statChip("压力层", "\(stats.pressureCount)")
                    statChip("未知样本", stats.unknownCount.map(String.init) ?? "—")
                }
            }
            if cells.isEmpty {
                Text(presentation.profileUnavailableReason).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 260)
            } else {
                heatmap(cells: cells, rows: stats.pressureRows)
                legend(hcai: hcai)
            }
            // nowcast-12: honest annotation — the desktop nowcast steps carry a frozen
            // model light-path cross-section, but profile samples have no terrain layer
            // and this page intentionally draws only the satellite posterior heatmap.
            Text("实况逐时结果另携带冻结太阳光路模式剖面（GFS 温湿/云分数/照明代理）；该剖面样本不含地形字段，本页仅绘卫星后验概率热图。")
                .font(.system(size: 9)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            frozenQuery
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }

    private func statChip(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 10, weight: .medium)).monospacedDigit().lineLimit(1)
        }
    }

    private func heatmap(cells: [NowcastProfileCell], rows: [NowcastPressureRow]) -> some View {
        let borders = cells.filter { $0.probability == nil }.map { cell in
            UnknownCellBorder(id: cell.id, corners: [
                (cell.startKM, cell.bottomKM), (cell.endKM, cell.bottomKM),
                (cell.endKM, cell.topKM), (cell.startKM, cell.topKM),
                (cell.startKM, cell.bottomKM),
            ])
        }
        return Chart {
            ForEach(cells) { cell in
                RectangleMark(
                    xStart: .value("距离", cell.startKM), xEnd: .value("距离", cell.endKM),
                    yStart: .value("高度", cell.bottomKM), yEnd: .value("高度", cell.topKM)
                ).foregroundStyle(cell.probability.map { Self.retrievedFill($0) } ?? Self.unknownFill)
            }
            ForEach(borders) { border in
                ForEach(Array(border.corners.enumerated()), id: \.offset) { _, corner in
                    LineMark(x: .value("距离", corner.0), y: .value("高度", corner.1),
                             series: .value("单元", border.id))
                        .foregroundStyle(Self.unknownAmber)
                        .lineStyle(StrokeStyle(lineWidth: 0.7, dash: [3, 2]))
                }
            }
        }
        .chartXAxisLabel("沿光路距离 · km")
        .chartYAxisLabel("高度 · km")
        .chartYAxis {
            // Height axis (leading) and pressure-level rows (nowcast-1): each
            // pressure level's representative GFS-prior height shares the same
            // y scale, so its dashed row line and hPa label stay aligned with km.
            AxisMarks(position: .leading) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                AxisValueLabel {
                    if let number = value.as(Double.self) {
                        Text("\(Int(number))km").font(.system(size: 8)).foregroundStyle(.secondary)
                    }
                }
            }
            AxisMarks(position: .trailing, values: rows.map(\.heightKM)) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.6, dash: [4, 3]))
                AxisValueLabel {
                    if let height = value.as(Double.self),
                       let row = rows.first(where: { abs($0.heightKM - height) < 0.001 }) {
                        Text("\(row.pressure)hPa").font(.system(size: 8))
                            .foregroundStyle(Self.retrieved)
                    }
                }
            }
        }
        .frame(height: 260)
        .accessibilityLabel("沿太阳方向卫星后验概率热图，右轴标注 GFS 先验压力层 hPa，未知样本以琥珀虚线框标出，缺测不解释为晴空")
    }

    /// nowcast-3: retrieved/unknown source legend and missing-measurement semantics,
    /// branching on bands like the Web footer (CLOUD_MASK → HCAI products, else HSD proxy).
    private func legend(hcai: Bool) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 10) {
                LinearGradient(colors: [Self.retrieved.opacity(0.12), Self.retrieved.opacity(0.79)],
                               startPoint: .leading, endPoint: .trailing)
                    .frame(width: 64, height: 6).clipShape(Capsule())
                    .overlay { Capsule().stroke(.secondary.opacity(0.35), lineWidth: 0.5) }
                Text("retrieved 后验概率 0–100%")
                Rectangle().fill(Self.unknownFill)
                    .overlay { Rectangle().stroke(Self.unknownAmber, style: StrokeStyle(lineWidth: 1, dash: [3, 2])) }
                    .frame(width: 12, height: 8)
                Text("unknown 缺失/未知")
            }
            Text(hcai
                 ? "HCAI 官方云掩膜、云相、云顶高度和 COT 产品。"
                 : "HSD B07/B13/B15 云概率代理（posterior）；HSD 分支不发布 COT、云相或官方云高真值。")
            Text("缺失/未知不解释为晴空；连续网格坐标与 model cloud fraction 来自同周期 GFS vertical prior（模式温湿度/风场先验，非观测温湿风廓线或云底真值）。")
        }.font(.system(size: 9)).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var frozenQuery: some View {
        let steps = (presentation.report?["steps"] as? [JSONObject] ?? [])
            .compactMap { $0["valid_time_local"] as? String }
        let available = (presentation.report?["profile_source"] as? JSONObject)?["available"] as? Bool == true
        return VStack(alignment: .leading, spacing: 8) {
            Divider()
            Text("按需冻结诊断").font(.system(size: 12, weight: .semibold))
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
                .padding(.horizontal, 14)
                .padding(.vertical, 14)
                Divider()
                ForEach(Array((report?["steps"] as? [JSONObject] ?? []).enumerated()), id: \.offset) { _, step in
                    let valid = step["valid"] as? Bool != false
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
                    .padding(.vertical, 12)
                    .padding(.horizontal, 14)
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
