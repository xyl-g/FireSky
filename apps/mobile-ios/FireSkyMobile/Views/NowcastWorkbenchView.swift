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

    @ViewBuilder
    private var nowcastResultsSection: some View {
        if let report {
            VStack(spacing: 12) {
                WorkSegments(
                    options: ["概览", "大气剖面", "诊断与时间窗", "逐时台账"],
                    titles: [L10n.t("概览"), L10n.t("大气剖面"), L10n.t("诊断与时间窗"), L10n.t("逐时台账")],
                    selection: $store.nowcastResultTab
                )

                if store.nowcastResultTab == "概览" {
                    nowcastHighlightsCard
                    nowcastCurveCard(report: report)
                    nowcastMetricsGrid
                } else if store.nowcastResultTab == "大气剖面" {
                    nowcastProfileCard
                    if let section = presentation.effectiveCrossSection {
                        WorkCrossSectionView(section: section, genusProxy: report["cloud_genus_proxy"] as? JSONObject)
                            .padding(14)
                            .workSurface()
                    }
                } else if store.nowcastResultTab == "逐时台账" {
                    nowcastLedgerCard(report: report)
                } else {
                    WorkDiagnosticView(report: report, profile: false, waiting: L10n.text("等待实况分析", "Waiting for analysis"))
                    nowcastExtraDiagnosticsCard
                }
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

    private var nowcastHighlightsCard: some View {
        let stats = presentation.profileStats
        return VStack(alignment: .leading, spacing: 10) {
            Text(L10n.text("实况关键诊断", "Key Nowcast Diagnostics"))
                .font(.subheadline.weight(.semibold))
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

    private var nowcastMetricsGrid: some View {
        let groups = [("卫星云况", 0..<7), ("运动采样", 7..<11), ("光学与雷达", 11..<15)]
        let columns = (sizeClass == .regular && !typeSize.isAccessibilitySize)
            ? [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
            : [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
        return ForEach(groups, id: \.0) { title, range in
            VStack(alignment: .leading, spacing: 8) {
                Text(L10n.t(title)).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                LazyVGrid(columns: columns, spacing: 10) {
                    ForEach(Array(NowcastPresentation.metrics[range]), id: \.0) { spec in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(spec.1).font(.caption).foregroundStyle(.secondary)
                            if spec.0 == "aod" {
                                WorkAODValue(summary: presentation.aod)
                            } else {
                                Text(presentation.value(spec.0))
                                    .font(.title3.weight(.semibold))
                                    .monospacedDigit()
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.75)
                            }
                            Text(spec.0 == "aod" ? presentation.aod.caption : spec.2)
                                .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, minHeight: 96, alignment: .topLeading)
                        .workSurface()
                    }
                }
            }
        }
    }

    private var nowcastProfileCard: some View {
        let cells = presentation.profileCells
        let stats = presentation.profileStats
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L10n.text("卫星垂直后验剖面", "Satellite Vertical Profile")).font(.subheadline.weight(.semibold))
                Spacer()
                Text(stats.statusLabel).font(.caption2).foregroundStyle(.secondary)
            }
            Text(presentation.profileBearingCaption).font(.caption2).foregroundStyle(.secondary)
            if cells.isEmpty {
                Text(presentation.profileUnavailableReason)
                    .font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 140)
            } else {
                Chart {
                    ForEach(cells) { cell in
                        RectangleMark(
                            xStart: .value("距离", cell.startKM), xEnd: .value("距离", cell.endKM),
                            yStart: .value("高度", cell.bottomKM), yEnd: .value("高度", cell.topKM)
                        )
                        .foregroundStyle(
                            cell.probability.map {
                                Color(red: 86 / 255, green: 143 / 255, blue: 155 / 255)
                                    .opacity(0.14 + min(100, max(0, $0)) / 140)
                            } ?? Color.orange.opacity(0.10)
                        )
                    }
                }
                .chartXAxisLabel(L10n.text("沿光路距离 · km", "Distance along ray · km"))
                .chartYAxisLabel(L10n.text("高度 · km", "Altitude · km"))
                .frame(height: 220)
            }
        }
        .padding(14)
        .workSurface()
    }

    private var nowcastExtraDiagnosticsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
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

    private func nowcastLedgerCard(report: JSONObject) -> some View {
        let steps = report["steps"] as? [JSONObject] ?? []
        return ScrollView(.horizontal, showsIndicators: false) {
            VStack(spacing: 0) {
                HStack(spacing: 0) {
                    Text(L10n.t("时间")).frame(width: 72, alignment: .leading)
                    Text(L10n.t("质量")).frame(width: 64, alignment: .leading)
                    Text(L10n.t("档位")).frame(width: 90, alignment: .leading)
                    Text(L10n.t("可烧")).frame(width: 48, alignment: .leading)
                    Text(L10n.t("可烧云量")).frame(width: 78, alignment: .leading)
                    Text(L10n.t("亮度颜色")).frame(width: 72, alignment: .leading)
                    Text(L10n.t("入射阻挡")).frame(width: 76, alignment: .leading)
                    Text("AOD").frame(width: 64, alignment: .leading)
                    Text(L10n.t("太阳仰角")).frame(width: 76, alignment: .leading)
                }
                .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                .padding(.horizontal, 14).padding(.vertical, 10)
                Divider()
                ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                    let valid = step["valid"] as? Bool != false
                    let burnable = step["physically_burnable"] as? Bool
                    HStack(spacing: 0) {
                        Text(timeLabel(step["valid_time_local"] as? String)).frame(width: 72, alignment: .leading)
                        Text(valid ? format(step["instant_quality_index"], digits: 3) : "—").frame(width: 64, alignment: .leading)
                        Text(valid ? (step["instant_quality_level"] as? String ?? "—") : L10n.t("缺测")).frame(width: 90, alignment: .leading)
                        Image(systemName: !valid || burnable == nil ? "minus" : burnable == true ? "checkmark" : "xmark")
                            .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? coolAccent : Color.orange)
                            .frame(width: 48, alignment: .leading)
                        Text(valid ? percent(step["burnable_cloud_coverage"]) : "—").frame(width: 78, alignment: .leading)
                        Text(valid ? ratioText(step["brightness_color"]) : "—").frame(width: 72, alignment: .leading)
                        Text(valid ? ratioText(step["solar_incoming_block_risk"]) : "—").frame(width: 76, alignment: .leading)
                        Text(valid ? format(step["quality_aod550"], digits: 2) : "—").frame(width: 64, alignment: .leading)
                        Text(valid ? format(step["solar_elevation_deg"], digits: 1) : "—").frame(width: 76, alignment: .leading)
                    }
                    .font(.caption2).monospacedDigit()
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    Divider().opacity(0.4)
                }
            }
            .frame(minWidth: 660, alignment: .leading)
        }
        .workSurface()
    }
}
