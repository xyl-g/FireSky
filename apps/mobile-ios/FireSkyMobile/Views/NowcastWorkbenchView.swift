import Charts
import SwiftUI

/// 卫星临近实况工作台（4小时窗提示 + Hero 评分卡 + 5分钟质量曲线 + 拉格朗日趋势 / 地平线隙 / 阴天阴影 + 垂直剖面）。
struct NowcastWorkbenchView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize

    private var report: JSONObject? { store.nowcastReport }
    private var presentation: NowcastPresentation { NowcastPresentation(report: report) }
    private var nowcast: JSONObject { presentation.observation }

    var body: some View {
        VStack(spacing: 14) {
            WorkLocationView()
            nowcastControlsCard
            WorkStaleResultBanner()
            nowcastHeroCard
            nowcastResultsSection
        }
    }

    // MARK: - 控制卡

    private var nowcastControlsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L10n.text("实况参数", "Nowcast Parameters")).font(.headline)
                Spacer()
                Text(store.targetDateLabel)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "clock.badge.exclamationmark")
                    .foregroundStyle(coolAccent)
                Text(L10n.text(
                    "卫星实况仅适用距朝霞/晚霞 4 小时以内的当前观测窗口；远期日期请改用「单点预测」或「区域预测」。",
                    "Satellite nowcast applies within 4 hours of the target dawn/dusk window; use Point or Regional Forecast for later dates."
                ))
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(coolAccent.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))

            Text(L10n.t("观测现象")).font(.caption2).foregroundStyle(.secondary)
            WorkSegments(options: ["dawn", "dusk"],
                         titles: [L10n.t("朝霞"), L10n.t("晚霞")],
                         selection: $store.phenomenon, accented: true)

            AccentButton(
                title: L10n.text("开始实况分析", "Start Nowcast"),
                systemImage: "antenna.radiowaves.left.and.right",
                disabled: store.busy || !store.online
            ) {
                store.beginCheck(for: .nowcast)
            }

            if store.busy {
                VStack(spacing: 6) {
                    ProgressView(value: store.percent, total: 100)
                    HStack {
                        Text(store.status).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                        Spacer()
                        Button { store.cancel() } label: {
                            Image(systemName: "xmark").frame(minWidth: 32, minHeight: 32)
                        }
                        .accessibilityLabel(L10n.cancel)
                    }
                }
            }

            let pageError = store.errorText(for: .point)
            if !pageError.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Text(pageError).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    Button { store.clearError() } label: {
                        Image(systemName: "xmark").font(.caption).foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(L10n.close)
                }
                .padding(10)
                .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(15)
        .workSurface()
        .disabled(store.busy)
    }

    // MARK: - 实况 Hero 评分卡

    private var nowcastHeroCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Circle().fill(coolAccent).frame(width: 7, height: 7)
                Text(L10n.text("卫星实况", "Satellite Nowcast")).font(.caption.weight(.semibold))
                Spacer()
                Text(report == nil ? L10n.text("等待分析", "Waiting") : L10n.text("分析完成", "Complete"))
                    .font(.caption2).foregroundStyle(.secondary)
            }

            if let mapCapability = report?["map_capability"] as? JSONObject,
               mapCapability["available"] as? Bool == false {
                let rawReason = mapCapability["reason"] as? String ?? ""
                if rawReason != "native-nowcast-map-publication-not-requested" {
                    let message = friendlyEngineMessage(rawReason)
                    if !message.isEmpty {
                        Label(message, systemImage: "exclamationmark.triangle")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
            }

            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(format(report?["peak_quality_index"]))
                            .font(.system(size: 32, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(report == nil ? Color.secondary : warm)
                        Text("/ 2.5").font(.caption2).foregroundStyle(.secondary)
                    }
                    Text(L10n.qualityLevel(
                        report?["peak_quality_level"] as? String
                        ?? report?["quality_level"] as? String
                        ?? L10n.text("峰值质量指数", "Peak quality index")
                    ))
                    .font(.caption).foregroundStyle(.secondary)
                }

                Spacer(minLength: 8)

                VStack(alignment: .trailing, spacing: 4) {
                    Text(L10n.t("最佳时刻")).font(.caption2).foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .font(.title2.weight(.semibold)).monospacedDigit()
                    Text(L10n.tf("推荐窗口 %@", windowLabel(report)))
                        .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                }
            }

            if report != nil {
                SkyTimeline(report: report, timezone: store.timezone)
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 6) { nowcastChips }
                    VStack(alignment: .leading, spacing: 6) { nowcastChips }
                }
                .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface(emphasized: report != nil)
    }

    @ViewBuilder
    private var nowcastChips: some View {
        let stats = presentation.profileStats
        Text("\(L10n.text("卫星覆盖", "Coverage")) \(stats.statusLabel) · \(stats.coverageText)")
            .lineLimit(1).workChip()
        if let source = nowcast["source"] as? String, !source.isEmpty {
            let channel = nowcast["image_channel"] as? String ?? "—"
            Text("\(source) · \(channel)").lineLimit(1).workChip()
        }
        if let obsTime = observationTime {
            Text(L10n.text("观测 \(obsTime)", "Obs \(obsTime)")).lineLimit(1).monospacedDigit().workChip()
        }
    }

    private var observationTime: String? {
        guard let value = nowcast["image_valid_time_utc"] as? String,
              let date = ObservationDate.timestamp(value) else { return nil }
        return ObservationDate.timeLabel(date, timezone: store.timezone)
    }

    // MARK: - 实况结果区

    private var cloudMetrics: [(String, String, String)] {
        var specs = Array(NowcastPresentation.metrics[0..<6])
        if let label = nowcast["cloud_type_label"] as? String, !label.isEmpty {
            specs.append(NowcastPresentation.metrics[6])
        }
        return specs
    }

    @ViewBuilder
    private var nowcastResultsSection: some View {
        if let report {
            VStack(spacing: 12) {
                HStack(alignment: .center, spacing: 8) {
                    nowcastTabs
                    Spacer(minLength: 4)
                    Button {
                        store.showNowcastDiagnostics = true
                    } label: {
                        Label(L10n.t("诊断"), systemImage: "info.circle")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Color.primary.opacity(scheme == .dark ? 0.07 : 0.05), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("nowcast-diagnostics-button")
                }

                if store.nowcastResultTab == "概览" {
                    panelSatelliteClouds
                    panelMotionAndRadar
                    if presentation.lagrangianTrendKPerHour != nil || presentation.horizonGapScore != nil || presentation.overcastShadowFactor != nil {
                        nowcastHighlightsCard
                    }
                    nowcastCurveCard(report: report)
                } else if store.nowcastResultTab == "大气剖面" {
                    WorkSatelliteProfileView(presentation: presentation)
                } else if store.nowcastResultTab == "逐时台账" {
                    WorkNowcastLedgerCard(report: report, timezone: store.timezone)
                } else {
                    WorkDiagnosticView(report: report, profile: false, waiting: L10n.text("等待实况分析", "Waiting for analysis"))
                    nowcastExtraDiagnosticsCard(report: report)
                }
            }
            .sheet(isPresented: $store.showNowcastDiagnostics) {
                NavigationStack {
                    ScrollView {
                        VStack(spacing: 12) {
                            WorkDiagnosticView(report: report, profile: false, waiting: L10n.text("等待实况分析", "Waiting for analysis"), showHeader: false)
                            nowcastExtraDiagnosticsCard(report: report)
                        }
                        .padding(16)
                    }
                    .workPageBackground()
                    .navigationTitle(L10n.t("诊断与时间窗"))
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button(L10n.close) { store.showNowcastDiagnostics = false }
                        }
                    }
                }
                .tint(warm)
            }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    FireSkyMarkScene(size: 46)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L10n.text("准备进行卫星临近实况分析", "Ready for satellite nowcast"))
                            .font(.subheadline.weight(.semibold))
                        Text(L10n.text(
                            "将拉取最近卫星多通道扫描与太阳光路反演，输出 5 分钟实况曲线、拉格朗日云温趋势与垂直剖面。",
                            "Fetches recent satellite scans and sun-ray retrievals for 5-min quality curves and vertical profiles."
                        ))
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .workSurface()
        }
    }

    private var nowcastTabs: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(["概览", "大气剖面", "逐时台账"], id: \.self) { tab in
                    let selected = store.nowcastResultTab == tab
                    Button {
                        store.nowcastResultTab = tab
                    } label: {
                        Text(L10n.t(tab))
                            .font(.caption.weight(selected ? .semibold : .regular))
                            .foregroundStyle(selected ? Color.primary : Color.secondary)
                            .padding(.horizontal, 12).padding(.vertical, 8)
                            .frame(minHeight: 36)
                            .background {
                                if selected {
                                    Capsule()
                                        .fill(scheme == .dark ? Color.white.opacity(0.11) : Color.white.opacity(0.95))
                                }
                            }
                            .overlay(alignment: .bottom) {
                                if selected {
                                    Capsule().fill(warm).frame(width: 20, height: 2.5).offset(y: -2)
                                }
                            }
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            .padding(3)
            .background(
                Capsule().fill(scheme == .dark ? Color(0x16, 0x1E, 0x2E) : Color(0xEA, 0xF0, 0xF6))
            )
            .overlay {
                Capsule().strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: scheme == .dark, emphasized: false), lineWidth: 1)
            }
        }
    }

    private var panelSatelliteClouds: some View {
        let specs = cloudMetrics
        let cols = (sizeClass == .regular && !typeSize.isAccessibilitySize) ? 3 : 2
        return VStack(alignment: .leading, spacing: 9) {
            Text(L10n.t("卫星云况"))
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            structuredGrid(specs: specs, columns: cols)
        }
        .padding(14)
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
                    .frame(minWidth: 340, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                twoByTwoPanel(title: L10n.t("光学与雷达"), subtitle: radarTimeliness, specs: opticsRadarSpecs)
                    .frame(minWidth: 340, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            VStack(spacing: 10) {
                twoByTwoPanel(title: L10n.t("运动与采样"), subtitle: nil, specs: motionSpecs)
                twoByTwoPanel(title: L10n.t("光学与雷达"), subtitle: radarTimeliness, specs: opticsRadarSpecs)
            }
        }
    }

    private func twoByTwoPanel(title: String, subtitle: String?, specs: [(String, String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                if let subtitle {
                    Spacer(minLength: 4)
                    Text(subtitle)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
            }
            structuredGrid(specs: specs, columns: 2)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .workSurface()
    }

    private func structuredGrid(specs: [(String, String, String)], columns: Int) -> some View {
        let rows = stride(from: 0, to: specs.count, by: columns).map { start in
            Array(specs[start..<min(start + columns, specs.count)])
        }
        return VStack(spacing: 8) {
            ForEach(Array(rows.enumerated()), id: \.offset) { rowIndex, rowSpecs in
                if rowIndex > 0 { Divider().opacity(0.42) }
                HStack(alignment: .top, spacing: 0) {
                    ForEach(0..<rowSpecs.count, id: \.self) { colIndex in
                        if colIndex > 0 { Divider().opacity(0.42) }
                        nowcastMetricCell(rowSpecs[colIndex])
                            .padding(.leading, colIndex == 0 ? 0 : 10)
                            .padding(.trailing, colIndex == rowSpecs.count - 1 ? 0 : 10)
                            .frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                }
            }
        }
    }

    private var radarTimeliness: String {
        guard let source = nowcast["radar_source"] as? String, !source.isEmpty else {
            return L10n.text("雷达：未接入雷达观测", "Radar: not connected")
        }
        let time = timeLabel(nowcast["radar_valid_time_utc"] as? String)
        let age = number(nowcast["radar_age_minutes"]).map { String(format: "%.0f", $0) } ?? "—"
        return L10n.text("雷达 \(source) · \(time) UTC · \(age) 分钟", "Radar \(source) · \(time) UTC · \(age)m")
    }

    private func nowcastMetricCell(_ spec: (String, String, String)) -> some View {
        let aod = presentation.aod
        let caption = spec.0 == "aod" ? aod.caption : captionText(spec)
        return VStack(alignment: .leading, spacing: 3) {
            Text(spec.1)
                .font(.caption.weight(.medium))
                .foregroundStyle(.primary.opacity(0.84))
                .lineLimit(1)
            if spec.0 == "aod" {
                WorkAODValue(summary: aod)
            } else {
                Text(presentation.value(spec.0))
                    .font(.title3.weight(.semibold)).monospacedDigit()
                    .foregroundStyle(report == nil ? Color.secondary : Color.primary)
                    .lineLimit(1).minimumScaleFactor(0.78)
            }
            Text(caption)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
    }

    private func captionText(_ spec: (String, String, String)) -> String {
        guard spec.0 == "geometric_path_blocking_risk" else { return spec.2 }
        guard let samples = number(nowcast["geometric_path_samples"]) else { return L10n.text("光路样本数未提供", "Samples unavailable") }
        return L10n.text("太阳入射路径 · \(Int(samples)) 个光路样本", "Solar path · \(Int(samples)) samples")
    }

    private var nowcastHighlightsCard: some View {
        let stats = presentation.profileStats
        return VStack(alignment: .leading, spacing: 10) {
            Text(L10n.text("实况关键诊断", "Key Nowcast Diagnostics"))
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                highlightCell(
                    title: L10n.text("卫星采样覆盖", "Satellite Coverage"),
                    value: presentation.value("calibrated_sample_coverage_ratio"),
                    detail: "\(stats.statusLabel) · \(stats.sampleText)"
                )
                highlightCell(
                    title: L10n.text("拉格朗日 B13 趋势", "Lagrangian B13 Trend"),
                    value: presentation.lagrangianTrendText,
                    detail: L10n.text("云顶亮温随时间变化率", "Cloud-top brightness temp rate")
                )
                if let gap = presentation.horizonGapScore {
                    highlightCell(
                        title: L10n.text("地平线缝隙评分", "Horizon Gap Score"),
                        value: String(format: "%.0f%%", gap * 100),
                        detail: "horizon_gap_score"
                    )
                }
                if let shadow = presentation.overcastShadowFactor {
                    highlightCell(
                        title: L10n.text("阴天阴影因子", "Overcast Shadow Factor"),
                        value: String(format: "%.0f%%", shadow * 100),
                        detail: "overcast_shadow_factor"
                    )
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private func highlightCell(title: String, value: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).monospacedDigit()
            Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 8))
    }

    private func nowcastCurveCard(report: JSONObject) -> some View {
        let points = ForecastComparison.curve(report: report, model: "nowcast", timezone: store.timezone)
        let ratios = ForecastComparison.ratioCurve(report: report, model: "nowcast", timezone: store.timezone)
        let aod = ForecastComparison.aodCurve(report: report, model: "nowcast", timezone: store.timezone)
        let bestTime = report["best_time_local"] as? String
        let bestPoint = bestTime.flatMap { t in points.first { $0.local == t } }

        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L10n.text("5 分钟实况质量曲线", "5-Min Nowcast Quality Curve"))
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text("0–2.5").font(.caption2).foregroundStyle(.secondary)
            }
            Chart {
                ForEach(points) { point in
                    LineMark(x: .value("时间", point.time), y: .value("质量指数", point.quality),
                             series: .value("曲线", point.series))
                        .foregroundStyle(warm)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                    PointMark(x: .value("时间", point.time), y: .value("质量指数", point.quality))
                        .foregroundStyle(warm)
                        .symbolSize(10)
                }
                if let bestPoint {
                    PointMark(x: .value("时间", bestPoint.time), y: .value("质量指数", bestPoint.quality))
                        .symbol(.diamond)
                        .symbolSize(54)
                        .foregroundStyle(warm)
                }
            }
            .chartYScale(domain: 0...2.5)
            .chartYAxis {
                AxisMarks(position: .leading, values: [0, 1.25, 2.5]) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    AxisValueLabel {
                        if let n = value.as(Double.self) {
                            Text(n.formatted(.number.precision(.fractionLength(0...2))))
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 5)) { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(ObservationDate.timeLabel(date, timezone: store.timezone))
                        }
                    }
                }
            }
            .frame(height: 170)

            if !ratios.isEmpty {
                Chart(ratios) { point in
                    LineMark(x: .value("时间", point.time), y: .value("占比", point.value),
                             series: .value("曲线", point.series))
                        .foregroundStyle(ratioSeriesColor(point.kind))
                        .lineStyle(StrokeStyle(lineWidth: 1.7))
                }
                .chartYScale(domain: 0...1)
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 5)) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let date = value.as(Date.self) {
                                Text(ObservationDate.timeLabel(date, timezone: store.timezone))
                            }
                        }
                    }
                }
                .frame(height: 110)
            }

            if !aod.isEmpty {
                Chart(aod) { point in
                    LineMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality),
                             series: .value("曲线", point.series))
                        .foregroundStyle(coolAccent)
                        .lineStyle(StrokeStyle(lineWidth: 1.7))
                }
                .frame(height: 92)
            }
        }
        .padding(14)
        .workSurface()
    }

    private func nowcastExtraDiagnosticsCard(report: JSONObject) -> some View {
        let components = report["quality_components"] as? JSONObject ?? [:]
        return VStack(alignment: .leading, spacing: 12) {
            if !components.isEmpty {
                WorkDiagnosticGroup(title: L10n.t("评分组成（后端只读 · 不在客户端重算）")) {
                    diagnosticRow(L10n.t("云量基础分"), components["coverage_base_score"] ?? components["coverage_score"], digits: 3)
                    diagnosticRow(L10n.text("修正后原始分", "Raw quality"), components["raw_quality_index"], digits: 3)
                    diagnosticRow(L10n.text("可烧云占比（峰值）", "Peak burnable ratio"), components["forward_burnable_cloud_ratio_peak"], percentValue: true)
                    diagnosticRow(L10n.text("可烧载荷（有效）", "Effective burnable load"), components["forward_burnable_cloud_load_effective"], digits: 3)
                    diagnosticRow(L10n.text("前方总云场（峰值）", "Forward total cloud"), components["forward_view_cloud_field_amount_peak"], digits: 3)
                    diagnosticRow(L10n.text("入射遮挡（均值）", "Solar block mean"), components["solar_incoming_block_risk_mean"], percentValue: true)
                    diagnosticRow(L10n.t("持续时间修正"), components["duration_adjustment"], digits: 3)
                    diagnosticRow(L10n.text("持续时间系数", "Duration factor"), components["duration_factor"], digits: 3)
                    diagnosticRow(L10n.t("入射 AOD 修正"), components["incoming_aod_adjustment"], digits: 3)
                    diagnosticRow(L10n.t("观测 AOD 修正"), components["observer_aod_adjustment"], digits: 3)
                    diagnosticRow(L10n.text("材质修正", "Material adj."), components["material_adjustment"], digits: 3)
                    diagnosticRow("AOD 550", components["quality_aod550"], digits: 3)
                }
            }
            WorkDiagnosticGroup(title: L10n.text("卫星运动与光学诊断", "Motion & Optical Diagnostics")) {
                diagnosticRow(L10n.text("拉格朗日 B13 趋势", "Lagrangian B13 trend"), presentation.lagrangianTrendText)
                if let gap = presentation.horizonGapScore {
                    diagnosticRow("horizon_gap_score", gap, percentValue: true)
                }
                if let shadow = presentation.overcastShadowFactor {
                    diagnosticRow("overcast_shadow_factor", shadow, percentValue: true)
                }
                diagnosticRow(L10n.text("运动质量", "Motion quality"), presentation.value("satellite_motion_quality"))
                diagnosticRow(L10n.text("光路样本数", "Light-path samples"), nowcast["geometric_path_samples"], digits: 0)
                diagnosticRow(L10n.text("雷达数据源", "Radar source"), nowcast["radar_source"])
            }
        }
        .padding(14)
        .workSurface()
    }
}

/// 卫星后验概率剖面（v3 平滑气层带 + 琥珀缺测幕墙 + 一图看懂 + GFS 先验等压线 + 触摸游标 + 折叠冻结剖面查询）。
private struct WorkSatelliteProfileView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @State private var zoomScale = 1.0
    @State private var showIsobars = true
    @State private var showFrozenQuery = false
    @State private var scrubDistanceKM: Double? = nil
    @State private var scrubHeightKM: Double? = nil

    let presentation: NowcastPresentation

    private let plotLeftMargin: Double = 38
    private let plotTopMargin: Double = 14
    private let plotBottomOffset: Double = 24
    private var plotAspectRatio: CGFloat { sizeClass == .compact ? 1.95 : 2.45 }

    private static let retrieved = Color(red: 86 / 255, green: 143 / 255, blue: 155 / 255)
    private static let retrievedBright = Color(red: 98 / 255, green: 170 / 255, blue: 188 / 255)
    private static let unknownAmber = Color(red: 183 / 255, green: 121 / 255, blue: 31 / 255)
    private static let unknownFill = Color(red: 183 / 255, green: 121 / 255, blue: 31 / 255).opacity(0.11)

    private struct ColumnSlice {
        let startKM: Double
        let endKM: Double
        let cells: [NowcastProfileCell]
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
        return VStack(alignment: .leading, spacing: 10) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 8) {
                    headerText(stats: stats)
                    Spacer(minLength: 6)
                    if !cells.isEmpty {
                        headerActions(stats: stats)
                    }
                }
                VStack(alignment: .leading, spacing: 6) {
                    headerText(stats: stats)
                    if !cells.isEmpty {
                        HStack(spacing: 8) {
                            if !stats.pressureRows.isEmpty { isobarToggle }
                            Spacer(minLength: 4)
                            zoomControls
                        }
                    }
                }
            }

            if stats.present {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 112), alignment: .leading)],
                          alignment: .leading, spacing: 6) {
                    statChip(L10n.t("时次"), stats.validTimeText ?? "—")
                    statChip(L10n.text("状态", "Status"), stats.statusLabel)
                    statChip(L10n.text("采样点", "Samples"), stats.sampleText)
                    statChip(L10n.text("覆盖率", "Coverage"), stats.coverageText)
                    statChip(L10n.text("压力层", "Levels"), "\(stats.pressureCount)")
                    statChip(L10n.text("未知样本", "Unknown"), stats.unknownCount.map(String.init) ?? "—")
                }
            }

            if cells.isEmpty {
                Text(presentation.profileUnavailableReason)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 180)
            } else {
                takeawayBanner(cells: cells, stats: stats)
                profileCanvas(cells: cells, rows: stats.pressureRows)
                legend(hcai: hcai)
            }

            frozenQueryDisclosure
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private func headerText(stats: NowcastProfileStats) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(L10n.t("太阳方向卫星剖面"))
                .font(.subheadline.weight(.semibold))
            Text(presentation.profileBearingCaption)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private func headerActions(stats: NowcastProfileStats) -> some View {
        HStack(spacing: 8) {
            if !stats.pressureRows.isEmpty {
                isobarToggle
            }
            zoomControls
        }
    }

    private var isobarToggle: some View {
        Button {
            showIsobars.toggle()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "waveform.path").font(.system(size: 10, weight: .medium))
                Text(L10n.t("等压线")).font(.system(size: 10.5, weight: .medium))
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .foregroundStyle(showIsobars ? Self.retrieved : Color.secondary)
            .background(
                showIsobars ? Self.retrieved.opacity(scheme == .dark ? 0.22 : 0.14) : Color.secondary.opacity(0.08),
                in: Capsule()
            )
            .overlay {
                Capsule().stroke(
                    showIsobars ? Self.retrieved.opacity(0.50) : Color.secondary.opacity(0.22),
                    lineWidth: 0.9
                )
            }
        }
        .buttonStyle(.plain)
    }

    private var zoomControls: some View {
        HStack(spacing: 2) {
            Text("\(Int((zoomScale * 100).rounded()))%")
                .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 36, alignment: .trailing)
            Button {
                zoomScale = Swift.max(1, zoomScale - 0.25)
            } label: {
                Image(systemName: "minus.magnifyingglass").frame(width: 28, height: 28)
            }
            .buttonStyle(.borderless)
            .disabled(zoomScale <= 1)
            Button {
                zoomScale = Swift.min(2.5, zoomScale + 0.25)
            } label: {
                Image(systemName: "plus.magnifyingglass").frame(width: 28, height: 28)
            }
            .buttonStyle(.borderless)
            .disabled(zoomScale >= 2.5)
            Button {
                zoomScale = 1
            } label: {
                Image(systemName: "arrow.counterclockwise").frame(width: 28, height: 28)
            }
            .buttonStyle(.borderless)
            .disabled(zoomScale == 1)
        }
    }

    private func statChip(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1.5) {
            Text(title).font(.system(size: 9)).foregroundStyle(.secondary)
            Text(value).font(.caption2.weight(.medium)).monospacedDigit().lineLimit(1)
        }
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
            Text(L10n.t("一图看懂"))
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(tint)
            Text("·")
                .foregroundStyle(.tertiary)
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

    private func profileCanvas(cells: [NowcastProfileCell], rows: [NowcastPressureRow]) -> some View {
        let maxDataHeight = Swift.max(cells.map(\.topKM).max() ?? 16.0, rows.map(\.heightKM).max() ?? 15.0)
        let maxHeightKM = Swift.max(16.0, ceil(maxDataHeight))
        let maxDistanceKM = Swift.max(1.0, cells.map(\.endKM).max() ?? 100.0)
        let showRightAxis = showIsobars && !rows.isEmpty

        return GeometryReader { geometry in
            let plotWidth = geometry.size.width * CGFloat(zoomScale)
            let plotHeight = geometry.size.height
            ZStack(alignment: .topLeading) {
                if zoomScale <= 1.01 {
                    satCanvas(cells: cells, rows: rows, width: plotWidth, height: plotHeight, showRightAxis: showRightAxis)
                        .gesture(
                            DragGesture(minimumDistance: 0)
                                .onChanged { value in
                                    updateScrub(at: value.location, width: Double(plotWidth), height: Double(plotHeight),
                                                showRightAxis: showRightAxis, maxDistanceKM: maxDistanceKM, maxHeightKM: maxHeightKM)
                                }
                        )
                } else {
                    ScrollView(.horizontal) {
                        satCanvas(cells: cells, rows: rows, width: plotWidth, height: plotHeight, showRightAxis: showRightAxis)
                            .gesture(
                                SpatialTapGesture()
                                    .onEnded { value in
                                        updateScrub(at: value.location, width: Double(plotWidth), height: Double(plotHeight),
                                                    showRightAxis: showRightAxis, maxDistanceKM: maxDistanceKM, maxHeightKM: maxHeightKM)
                                    }
                            )
                            .simultaneousGesture(
                                LongPressGesture(minimumDuration: 0.15)
                                    .sequenced(before: DragGesture(minimumDistance: 0))
                                    .onChanged { phase in
                                        if case .second(true, let drag?) = phase {
                                            updateScrub(at: drag.location, width: Double(plotWidth), height: Double(plotHeight),
                                                        showRightAxis: showRightAxis, maxDistanceKM: maxDistanceKM, maxHeightKM: maxHeightKM)
                                        }
                                    }
                            )
                    }
                    .frame(height: plotHeight)
                    .scrollIndicators(.automatic)
                }

                if let hd = scrubDistanceKM, let hh = scrubHeightKM {
                    scrubReadoutCard(cells: cells, distanceKM: hd, heightKM: hh)
                        .padding(.leading, plotLeftMargin + 8)
                        .padding(.top, plotTopMargin + 8)
                }
            }
        }
        .aspectRatio(plotAspectRatio / CGFloat(zoomScale), contentMode: .fit)
        .accessibilityLabel(L10n.text("沿太阳方向卫星后验概率剖面", "Satellite posterior probability profile along sun ray"))
    }

    private func satCanvas(cells: [NowcastProfileCell], rows: [NowcastPressureRow],
                           width: CGFloat, height: CGFloat, showRightAxis: Bool) -> some View {
        Canvas { context, size in
            drawProfile(context: context, size: size, cells: cells, rows: rows,
                        scale: zoomScale, showIsobars: showRightAxis,
                        scrubDistanceKM: scrubDistanceKM, scrubHeightKM: scrubHeightKM)
        }
        .frame(width: width, height: height)
        .contentShape(Rectangle())
    }

    private func updateScrub(at location: CGPoint, width: Double, height: Double,
                             showRightAxis: Bool, maxDistanceKM: Double, maxHeightKM: Double) {
        let left = plotLeftMargin
        let right = width - (showRightAxis ? 46.0 : 14.0)
        let top = plotTopMargin
        let bottom = height - plotBottomOffset
        guard right > left + 10, bottom > top + 10,
              Double(location.x) >= left, Double(location.x) <= right,
              Double(location.y) >= top, Double(location.y) <= bottom else {
            return
        }
        let xRatio = Swift.min(1.0, Swift.max(0.0, (Double(location.x) - left) / (right - left)))
        let yRatio = Swift.min(1.0, Swift.max(0.0, 1.0 - (Double(location.y) - top) / (bottom - top)))
        scrubDistanceKM = xRatio * maxDistanceKM
        scrubHeightKM = yRatio * maxHeightKM
    }

    @ViewBuilder
    private func scrubReadoutCard(cells: [NowcastProfileCell], distanceKM: Double, heightKM: Double) -> some View {
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
                return L10n.t("未知/缺测")
            }
            if columnUnknown { return L10n.t("未知/缺测") }
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
                String(format: "%.0f km · %.1f km · %@", distanceKM, heightKM, statusText),
                String(format: "%.0f km · %.1f km · %@", distanceKM, heightKM, statusText)
            ))
            .font(.system(size: 10, weight: .medium))
            .monospacedDigit()
            Button {
                scrubDistanceKM = nil
                scrubHeightKM = nil
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
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
                             scrubDistanceKM: Double?, scrubHeightKM: Double?) {
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

            // Twilight sky backdrop
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

            var colIdx = 0
            while colIdx < columns.count {
                if columns[colIdx].isFullyUnknown {
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
                        let badgeText = Text(L10n.t("未知/缺测"))
                            .font(.system(size: 8.5, weight: .semibold))
                            .foregroundColor(Self.unknownAmber)
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

                // Individual unknown level cells inside retrieved columns
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

            // Observer beacon & sun direction cue
            let observer = CGPoint(x: x(0), y: bottom - 5)
            let halo = CGRect(x: observer.x - 7, y: observer.y - 7, width: 14, height: 14)
            layer.fill(Path(ellipseIn: halo), with: .color(warm.opacity(0.26)))
            let dot = CGRect(x: observer.x - 4, y: observer.y - 4, width: 8, height: 8)
            layer.fill(Path(ellipseIn: dot), with: .color(warm))
            layer.stroke(Path(ellipseIn: dot), with: .color(.white.opacity(0.9)), lineWidth: 1.2)
            layer.draw(
                Text(L10n.text("观测点", "Observer")).font(.system(size: labelSize, weight: .semibold)).foregroundColor(.primary),
                at: CGPoint(x: observer.x + 9, y: observer.y - 8),
                anchor: .leading
            )

            let sunCue: String = {
                if let bearing = presentation.profileBearing {
                    return L10n.text(String(format: "☀ 太阳方向 %.0f°", bearing), String(format: "☀ Sun %.0f°", bearing))
                }
                return L10n.text("☀ 太阳方向", "☀ Sun")
            }()
            let sunTextColor = scheme == .dark
                ? Color(red: 0.98, green: 0.72, blue: 0.34)
                : Color(red: 0.85, green: 0.45, blue: 0.12)
            let sunResolved = layer.resolve(
                Text(sunCue)
                    .font(.system(size: 9.0, weight: .semibold))
                    .foregroundColor(sunTextColor)
            )
            let sunTextSize = sunResolved.measure(in: size)
            let sunPillW: CGFloat = sunTextSize.width + 12
            let sunPillH: CGFloat = sunTextSize.height + 5
            let sunPillX: CGFloat = CGFloat(right) - sunPillW - 8
            let sunPillY: CGFloat = CGFloat(top) + 7
            let sunPillRect = CGRect(x: sunPillX, y: sunPillY, width: sunPillW, height: sunPillH)
            layer.fill(Path(roundedRect: sunPillRect, cornerRadius: sunPillH * 0.5),
                       with: .color((scheme == .dark ? Color.black : Color.white).opacity(0.70)))
            layer.stroke(Path(roundedRect: sunPillRect, cornerRadius: sunPillH * 0.5),
                         with: .color(sunTextColor.opacity(0.40)),
                         lineWidth: 0.7)
            layer.draw(sunResolved, at: CGPoint(x: sunPillRect.midX, y: sunPillRect.midY), anchor: .center)

            if let scrubDistanceKM, let scrubHeightKM {
                let hx = x(scrubDistanceKM)
                let hy = y(scrubHeightKM)
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

        let heightStep = maxHeightKM <= 16 ? 4.0 : 5.0
        for km in stride(from: 0.0, through: maxHeightKM, by: heightStep) {
            context.draw(Text("\(Int(km))km").font(.system(size: labelSize)).foregroundColor(.secondary),
                         at: CGPoint(x: left - 5, y: y(km)), anchor: .trailing)
        }

        let distanceStep = maxDistanceKM <= 120 ? 20.0 : maxDistanceKM <= 300 ? 50.0 : 100.0
        for km in stride(from: 0.0, through: maxDistanceKM, by: distanceStep) where km > 0 {
            context.draw(Text("\(Int(km))km").font(.system(size: labelSize)).foregroundColor(.secondary),
                         at: CGPoint(x: x(km), y: bottom + 10),
                         anchor: km >= maxDistanceKM - 1 ? .trailing : .center)
        }

        if showIsobars {
            var lastLabelY = -Double.infinity
            for row in rows.sorted(by: { $0.heightKM > $1.heightKM }) {
                guard (0...maxHeightKM).contains(row.heightKM) else { continue }
                let lineY = y(row.heightKM)
                if abs(lineY - lastLabelY) >= 12 {
                    lastLabelY = lineY
                    context.draw(Text("\(row.pressure)hPa").font(.system(size: Swift.max(8.5, labelSize - 0.5))).foregroundColor(Self.retrieved),
                                 at: CGPoint(x: right + 5, y: lineY), anchor: .leading)
                }
            }
        }
    }

    private func legend(hcai: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    HStack(spacing: 5) {
                        LinearGradient(colors: [Self.retrieved.opacity(0.14), Self.retrievedBright.opacity(0.84)],
                                       startPoint: .leading, endPoint: .trailing)
                            .frame(width: 48, height: 6).clipShape(Capsule())
                            .overlay { Capsule().stroke(.secondary.opacity(0.3), lineWidth: 0.5) }
                        Text(L10n.text("卫星后验概率 0–100%", "Cloud posterior 0–100%"))
                    }
                    HStack(spacing: 5) {
                        Rectangle().fill(Self.unknownFill)
                            .overlay { Rectangle().stroke(Self.unknownAmber, style: StrokeStyle(lineWidth: 1, dash: [3, 2])) }
                            .frame(width: 11, height: 7)
                        Text(L10n.text("未知/缺失（非晴空）", "Unknown/missing (not clear)"))
                    }
                    HStack(spacing: 5) {
                        Rectangle().fill(Color.clear)
                            .overlay { Rectangle().stroke(Self.retrieved.opacity(0.6), style: StrokeStyle(lineWidth: 1, dash: [4, 3])) }
                            .frame(width: 12, height: 1)
                        Text(L10n.text("GFS 压力层先验（hPa）", "GFS prior levels (hPa)"))
                    }
                }
                .font(.system(size: 9.5))
                .foregroundStyle(.secondary)
            }
            Text(hcai
                 ? L10n.text("HCAI 官方云掩膜/云相/云顶与 COT 产品 · 缺测不解释为晴空。", "HCAI cloud mask/phase/top & COT; missing is not clear sky.")
                 : L10n.text("HSD B07/B13/B15 云概率代理 · 连续高度层来自 GFS 先验（非观测真值），缺测不解释为晴空。",
                             "HSD B07/B13/B15 cloud proxy · continuous levels from GFS prior; missing is not clear sky."))
                .font(.system(size: 9.5)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var frozenQueryDisclosure: some View {
        let steps = (presentation.report?["steps"] as? [JSONObject] ?? [])
            .compactMap { $0["valid_time_local"] as? String }
        let available = (presentation.report?["profile_source"] as? JSONObject)?["available"] as? Bool == true
        return VStack(alignment: .leading, spacing: 6) {
            Divider().opacity(0.45)
            DisclosureGroup(isExpanded: $showFrozenQuery) {
                VStack(alignment: .leading, spacing: 8) {
                    if !available || steps.isEmpty {
                        Text(L10n.text("此结果没有可查询的冻结卫星剖面。", "No frozen satellite profile available for this result."))
                            .font(.caption2).foregroundStyle(.secondary)
                    } else {
                        HStack(spacing: 8) {
                            Picker(L10n.t("时次"), selection: $store.nowcastProfileStep) {
                                ForEach(steps, id: \.self) { step in
                                    Text(step.replacingOccurrences(of: "T", with: " ")).tag(step)
                                }
                            }
                            .labelsHidden()
                            .pickerStyle(.menu)
                            Button {
                                store.queryNowcastProfile(validTimeLocal: store.nowcastProfileStep)
                            } label: {
                                Label(L10n.text("读取", "Load"), systemImage: "waveform.path.ecg")
                                    .font(.caption.weight(.medium))
                            }
                            .buttonStyle(.bordered)
                            .disabled(store.busy || store.nowcastProfileStep.isEmpty)
                        }
                        FrozenProfileView(model: "satellite") {
                            if !store.nowcastProfileStep.isEmpty {
                                store.queryNowcastProfile(validTimeLocal: store.nowcastProfileStep)
                            }
                        }
                    }
                }
                .padding(.top, 6)
            } label: {
                Text(L10n.t("按时次查询冻结剖面"))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
            }
        }
        .onAppear {
            if store.nowcastProfileStep.isEmpty {
                store.nowcastProfileStep = (presentation.report?["best_time_local"] as? String) ?? steps.first ?? ""
            }
        }
    }
}

private struct WorkNowcastLedgerCard: View {
    let report: JSONObject
    let timezone: String
    @State private var showInvalid = false

    private var allSteps: [JSONObject] { report["steps"] as? [JSONObject] ?? [] }
    private var visibleSteps: [JSONObject] {
        showInvalid ? allSteps : allSteps.filter(ForecastComparison.isStepValid)
    }
    private var hiddenInvalidCount: Int {
        allSteps.filter { !ForecastComparison.isStepValid($0) }.count
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(spacing: 0) {
                    HStack(spacing: 0) {
                        Text(L10n.t("时间")).frame(width: 72, alignment: .leading)
                        Text(L10n.t("质量")).frame(width: 64, alignment: .leading)
                        Text(L10n.t("档位")).frame(width: 96, alignment: .leading)
                        Text(L10n.t("可烧")).frame(width: 48, alignment: .leading)
                        Text(L10n.t("可烧云量")).frame(width: 78, alignment: .leading)
                        Text(L10n.t("亮度颜色")).frame(width: 72, alignment: .leading)
                        Text(L10n.t("可烧比例")).frame(width: 68, alignment: .leading)
                        Text(L10n.t("入射阻挡")).frame(width: 76, alignment: .leading)
                        Text("AOD").frame(width: 64, alignment: .leading)
                        Text(L10n.t("太阳仰角")).frame(width: 76, alignment: .leading)
                    }
                    .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    Divider()
                    ForEach(Array(visibleSteps.enumerated()), id: \.offset) { _, step in
                        let valid = ForecastComparison.isStepValid(step)
                        let burnable = step["physically_burnable"] as? Bool
                        HStack(spacing: 0) {
                            Text(timestamp(step)).frame(width: 72, alignment: .leading)
                            Text(valid ? format(step["instant_quality_index"], digits: 3) : "—").frame(width: 64, alignment: .leading)
                            Text(valid ? (step["instant_quality_level"] as? String ?? "—") : L10n.t("外推窗外/未判断"))
                                .foregroundStyle(valid ? Color.primary : Color.secondary)
                                .frame(width: 96, alignment: .leading)
                            Image(systemName: !valid || burnable == nil ? "minus" : burnable == true ? "checkmark" : "xmark")
                                .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? coolAccent : Color.orange)
                                .frame(width: 48, alignment: .leading)
                            Text(valid ? percent(step["burnable_cloud_coverage"]) : "—").frame(width: 78, alignment: .leading)
                            Text(valid ? ratioText(step["brightness_color"]) : "—").frame(width: 72, alignment: .leading)
                            Text(valid ? ratioText(step["forward_burnable_cloud_ratio"]) : "—").frame(width: 68, alignment: .leading)
                            Text(valid ? ratioText(step["solar_incoming_block_risk"]) : "—").frame(width: 76, alignment: .leading)
                            Text(valid ? format(step["quality_aod550"], digits: 2) : "—").frame(width: 64, alignment: .leading)
                            Text(valid ? format(step["solar_elevation_deg"], digits: 1) : "—").frame(width: 76, alignment: .leading)
                        }
                        .font(.caption2).monospacedDigit()
                        .padding(.horizontal, 14).padding(.vertical, 10)
                        Divider().opacity(0.4)
                    }
                    if visibleSteps.isEmpty {
                        Text(L10n.text("当前时段内没有有效判断时次。", "No valid evaluated time steps in this window."))
                            .font(.callout).foregroundStyle(.secondary).padding(28)
                    }
                }
                .frame(minWidth: 736, alignment: .leading)
            }
            Divider().opacity(0.45)
            HStack(spacing: 10) {
                Text(L10n.text("默认仅展示有效判断时次；— 表示缺测。", "Unevaluated rows hidden by default; — indicates missing."))
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if hiddenInvalidCount > 0 {
                    Button {
                        showInvalid.toggle()
                    } label: {
                        Label(
                            showInvalid
                                ? L10n.text("隐藏未判断时次", "Hide unevaluated")
                                : L10n.text("显示 \(hiddenInvalidCount) 个未判断时次", "Show \(hiddenInvalidCount) unevaluated"),
                            systemImage: showInvalid ? "eye.slash" : "eye"
                        )
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.secondary.opacity(0.10), in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
        }
        .workSurface()
    }

    private func timestamp(_ step: JSONObject) -> String {
        if let local = step["valid_time_local"] as? String { return timeLabel(local) }
        guard let utc = step["valid_time_utc"] as? String,
              let date = ObservationDate.timestamp(utc) else { return "—" }
        return ObservationDate.timeLabel(date, timezone: timezone)
    }
}
