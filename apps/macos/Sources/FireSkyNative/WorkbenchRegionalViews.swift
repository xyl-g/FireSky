import AppKit
import SwiftUI

struct WorkRegionPage: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(spacing: 8) {
            controls
            WorkStaleResultBanner()
            HStack(spacing: 14) {
                summary(store.regionalModel)
                Divider().frame(height: 40).opacity(0.5)
                regionalMetadata(store.regionalModel.uppercased(),
                                 store.regionalForecast.model(store.regionalModel))
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .fireSkySurface(role: .content, cornerRadius: 12)
            HStack(spacing: 10) {
                Text(regionalGridLabel).font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer()
                Toggle(isOn: $store.showRegionalSmooth) {
                    Image(systemName: "aqi.medium").accessibilityLabel(L10n.text("平滑过渡渲染", "Smooth rendering"))
                }.toggleStyle(.button).help(L10n.text("平滑过渡渲染（关闭则显示原始 0.25° 网格方块）",
                                                      "Smooth interpolated rendering (turn off for raw 0.25° grid squares)"))
                Toggle(isOn: $store.showRegionalGrid) {
                    Image(systemName: "square.grid.3x3").accessibilityLabel("显示网格")
                }.toggleStyle(.button).help("显示网格")
                Toggle(isOn: $store.showRegionalSamples) {
                    Image(systemName: "square.stack.3d.up").accessibilityLabel("采样格点")
                }.toggleStyle(.button).help("采样格点（复用预测点，最多显示 2000 个）")
                Button { store.regionalFitVersion += 1 } label: { Image(systemName: "arrow.up.left.and.arrow.down.right") }
                    .help("适应预测范围").accessibilityLabel("适应预测范围")
                Button { store.showRegionalInspector.toggle() } label: { Image(systemName: "sidebar.right") }
                    .help("网格详情与排行").accessibilityLabel("网格详情与排行")
            }.buttonStyle(.borderless)
            HStack(alignment: .top, spacing: 12) {
                VStack(spacing: 0) {
                    map(store.regionalModel)
                    legend
                }.fireSkySurface(role: .content, cornerRadius: 12)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                if store.showRegionalInspector {
                    inspector
                        .padding(10)
                        .frame(width: 264)
                        .fireSkySurface(role: .content, cornerRadius: 12)
                }
            }.frame(maxHeight: .infinity)
            if store.busy {
                HStack {
                    ProgressView(value: store.percent, total: 100).frame(width: 130)
                    Text(store.status).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Button { store.cancel() } label: { Image(systemName: "xmark") }.help("取消任务").accessibilityLabel("取消任务")
                }
            }
            let regionalError = store.errorText(for: .regional)
            if !regionalError.isEmpty {
                // UX #17：错误全文可见、可选中复制，按页面隔离不串屏。
                HStack(alignment: .top, spacing: 8) {
                    Text(regionalError).font(.system(size: 10)).foregroundStyle(.red)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                        .help(regionalError)
                    Button { store.clearError() } label: { Image(systemName: "xmark") }
                        .buttonStyle(.borderless).font(.system(size: 10)).foregroundStyle(.secondary)
                        .help(L10n.t("关闭"))
                }
            }
        }.padding(.horizontal, 14).padding(.top, 6).padding(.bottom, 12)
            .onExitCommand { store.editingRegionOnMap = false }
    }

    private var selectedRegionMenuTitle: String {
        let byCode: [String: String] = Dictionary(uniqueKeysWithValues: store.regionDefinitions.compactMap { item in
            guard let code = item["code"] as? String else { return nil }
            return (code, item["label"] as? String ?? code)
        })
        let labels = store.selectedRegions.sorted().map { byCode[$0] ?? Store.fallbackRegionLabels[$0] ?? $0 }
        if labels.count == 1, !store.customRegion { return labels[0] }
        if !labels.isEmpty { return "\(labels[0])+\(labels.count - 1 + (store.customRegion ? 1 : 0))" }
        if store.customRegion { return L10n.text("自定义范围", "Custom") }
        return L10n.t("预测区域")
    }

    private var controls: some View {
        HStack(spacing: 9) {
            Menu {
                Section("预测区域") {
                    ForEach(store.regionDefinitions, id: \.regionCode) { item in
                        let code = item["code"] as? String ?? ""
                        Toggle(item["label"] as? String ?? code, isOn: Binding(
                            get: { store.selectedRegions.contains(code) },
                            set: {
                                if $0 { store.selectedRegions.insert(code) } else { store.selectedRegions.remove(code) }
                                store.regionalCamera = nil
                                store.regionalFitVersion += 1
                            }
                        ))
                    }
                    Toggle("自定义范围", isOn: $store.customRegion)
                }
                Divider()
                Button(L10n.text("将当前区域设为默认", "Set Current Region as Default")) {
                    store.saveCurrentRegionsAsDefault()
                }
                .disabled(store.selectedRegions.isEmpty)
                Button(L10n.text("恢复默认区域", "Reset to Default Region")) {
                    store.applyDefaultRegions()
                }
            } label: {
                Label(selectedRegionMenuTitle, systemImage: "map")
            }.frame(minWidth: 112)
            Button {
                store.editingRegionOnMap.toggle()
            } label: {
                Label(L10n.t(store.editingRegionOnMap ? "取消绘制" : "绘制范围"), systemImage: "rectangle.dashed")
                    .foregroundStyle(store.editingRegionOnMap ? coolAccent : Color.primary)
            }.help("在地图上拖拽绘制范围，Esc 取消")
            Button { store.showRegionBounds.toggle() } label: { Image(systemName: "slider.horizontal.3") }
                .help("编辑区域坐标").accessibilityLabel("编辑区域坐标")
                .popover(isPresented: $store.showRegionBounds) {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(L10n.t("自定义范围")).font(.headline)
                        Toggle("启用自定义范围", isOn: $store.customRegion).toggleStyle(.checkbox)
                        HStack { number("西界", $store.west); number("东界", $store.east) }
                        HStack { number("南界", $store.south); number("北界", $store.north) }
                        Text("各网格时区按经纬度自动计算")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        HStack { Spacer(); Button(L10n.t("确定")) { store.showRegionBounds = false } }
                    }.padding(18).frame(width: 310)
                }
            Divider().frame(height: 22)
            WorkDateButton().frame(width: 150)
            WorkSegments(options: ["dawn", "dusk"], titles: ["朝霞", "晚霞"], selection: $store.phenomenon)
                .frame(width: 112)
            WorkSegments(options: ["gfs", "ec"], titles: ["GFS", "EC"],
                         selection: $store.regionalModel).frame(width: 100)
            Spacer(minLength: 4)
            Button { store.beginCheck() } label: {
                Label(L10n.start, systemImage: "play.fill")
                    .font(.system(size: 12, weight: .semibold)).padding(.horizontal, 7).frame(height: 25)
            }.buttonStyle(.borderedProminent).disabled(store.busy || !store.online)
                .help(store.busy ? "当前有任务正在进行（\(store.status)），完成或取消后可开始新预测。"
                      : !store.online ? "引擎未连接，请先重新连接。"
                      : "先预检本地数据，确认后开始区域预测。")
        }.controlSize(.regular)
    }

    private func summary(_ model: String) -> some View {
        let report = store.regionalForecast.model(model)
        return HStack(alignment: .center, spacing: 18) {
            VStack(alignment: .leading, spacing: 5) {
                Text(model.uppercased()).font(.system(size: 13, weight: .bold))
                    .foregroundStyle(model == "gfs" ? warm : coolAccent)
                Text(report.status).font(.system(size: 10)).foregroundStyle(report.error == nil ? Color.secondary : Color.red)
            }.frame(width: 60, alignment: .leading)
            VStack(alignment: .leading, spacing: 4) {
                Text(report.peak?.quality.map { String(format: "%.2f", $0) } ?? "—")
                    .font(.system(size: 25, weight: .semibold)).monospacedDigit()
                Text(L10n.text("区域峰值", "Regional peak")).font(.system(size: 9)).foregroundStyle(.secondary)
                if let peak = report.peak {
                    Text("\(L10n.text("最佳地点", "Best location")) · \(peak.label)")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                        .lineLimit(1)
                        .help("\(peak.label) · \(peak.level)")
                }
            }
            Spacer(minLength: 2)
            VStack(alignment: .trailing, spacing: 5) {
                Text(report.available ? "\(report.validCount) 有效 · \(report.highCount) 高值" : "—")
                Text(report.missingCount > 0 ? "\(report.missingCount) 缺测" : report.available ? "无缺测" : "等待数据")
                    .foregroundStyle(report.missingCount > 0 ? Color.orange : Color.secondary)
            }.font(.system(size: 10))
        }.frame(maxWidth: .infinity)
            .help(report.error ?? "高值网格：质量指数 ≥ 1.5")
    }

    private func regionalMetadata(_ title: String, _ model: RegionalModel) -> some View {
        let count = model.pointCount > 0 ? "\(model.successCount)/\(model.pointCount) 点" : "—"
        let grid = model.precision.map { "网格 \($0)" } ?? "网格 —"
        let regions = model.regionCount > 0 ? "\(model.regionCount) 个区域" : "区域 —"
        let prefetch = model.prefetchSeconds.map { String(format: "准备 %.1fs", $0) } ?? "准备 —"
        return VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.system(size: 9, weight: .semibold))
            Text("\(grid) · \(count) · \(regions) · \(prefetch)")
                .font(.system(size: 9)).foregroundStyle(.secondary)
                .lineLimit(1).minimumScaleFactor(0.75)
            // 缓存快照可辨认（硬规则 2）：与点页「缓存状态」诊断组对齐，只标注命中。
            if model.cacheStatus == "hit", let generated = model.cacheGeneratedAt {
                Text("缓存快照 · 生成于 \(String(generated.prefix(16)).replacingOccurrences(of: "T", with: " ")) UTC")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                    .lineLimit(1).minimumScaleFactor(0.75)
                    .help("本次结果读取自引擎缓存快照，未重新计算")
            }
            if !model.nativeTimingsMS.isEmpty {
                let native = model.nativeTimingsMS
                Text("原生耗时 · " + native.keys.sorted().map {
                    "\($0) \(diagnosticValue(native[$0], digits: 1, suffix: " ms"))"
                }.joined(separator: " · "))
                .font(.system(size: 9)).foregroundStyle(.secondary)
                .lineLimit(1)
                .help(ReportDiagnosticValue.text(native))
            }
        }.frame(maxWidth: 320, alignment: .leading)
    }

    private var regionalGridLabel: String {
        let precision = store.regionalForecast.model(store.regionalModel).precision
        return precision.map { "网格精度 \($0)" } ?? "网格精度 —"
    }

    private func map(_ model: String) -> some View {
        VStack(spacing: 0) {
            HStack {
                Text(model.uppercased()).font(.system(size: 11, weight: .bold))
                    .foregroundStyle(model == "gfs" ? warm : coolAccent)
                Spacer()
                if let cycle = store.regionalForecast.model(model).cycle {
                    Text(cycle.replacingOccurrences(of: "T", with: " ").replacingOccurrences(of: "+00:00", with: "Z"))
                        .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1)
                }
            }.padding(.horizontal, 12).frame(height: 32)
            ForecastMap(store: store, model: model).frame(minWidth: 200, minHeight: 280)
                .overlay(alignment: .top) {
                    if let error = store.regionalForecast.model(model).error {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.system(size: 10)).padding(10)
                            .fireSkySurface(role: .overlay, cornerRadius: 6).padding(10)
                    }
                }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var legend: some View {
        HStack(spacing: 7) {
            Text(L10n.t("质量指数"))
            VStack(spacing: 2) {
                HStack(spacing: 0) {
                    ForEach(0..<RegionalPalette.steps, id: \.self) { index in
                        Color(nsColor: RegionalPalette.color(index))
                    }
                }.frame(width: 110, height: 6).clipShape(Capsule())
                // 六档刻度对齐 Web 图例（outlook/+page.svelte:516-523）；色带仍按
                // RegionalPalette 固定 0–2.5 映射，无渲染改动。
                HStack(spacing: 0) {
                    Text("0").fixedSize()
                    Spacer(minLength: 0)
                    Text("0.5").fixedSize()
                    Spacer(minLength: 0)
                    Text("1.0").fixedSize()
                    Spacer(minLength: 0)
                    Text("1.5").fixedSize()
                    Spacer(minLength: 0)
                    Text("2.0").fixedSize()
                    Spacer(minLength: 0)
                    Text("2.5").fixedSize()
                }.frame(width: 110)
            }
            Spacer(minLength: 4)
            Rectangle().stroke(.secondary.opacity(0.5), style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                .frame(width: 9, height: 9)
            Text(L10n.t("缺测"))
        }.font(.system(size: 9)).foregroundStyle(.secondary).padding(.horizontal, 12).frame(height: 32)
    }

    private var inspector: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let coordinate = store.selectedRegionalCoordinate {
                HStack {
                    Text(L10n.t("网格详情")).font(.system(size: 12, weight: .semibold))
                    Spacer()
                    Button { store.selectedRegionalCoordinate = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.borderless).help("关闭网格详情").accessibilityLabel("关闭网格详情")
                }
                Text(coordinate.label).font(.system(size: 11)).monospacedDigit().foregroundStyle(.secondary)
                ForEach([store.regionalModel], id: \.self) { model in
                    let cell = store.regionalForecast.model(model).cell(at: coordinate)
                    // 城市坐标不落在 0.25° 格点上（城市点由引擎单独计算），此时回退到
                    // 城市排名行携带的完整 point DTO，不得把真实值显示成「缺测」。
                    let cityCell = store.regionalForecast.model(model).cityRanking.first { $0.coordinate == coordinate }
                    let display = cell ?? cityCell
                    HStack(spacing: 6) {
                        Text(model.uppercased()).foregroundStyle(model == "gfs" ? warm : coolAccent).frame(width: 34, alignment: .leading)
                        Text(display?.quality.map { String(format: "%.2f", $0) } ?? "—").monospacedDigit().bold()
                        Spacer()
                        Text(display?.quality == nil ? "缺测" : timeLabel(display?.bestTime)).foregroundStyle(.secondary)
                        if cell?.bestTime != nil {
                            Button {
                                store.queryRegionalProfile(model: model, coordinate: coordinate)
                            } label: {
                                Image(systemName: "waveform.path.ecg")
                            }
                            .buttonStyle(.borderless)
                            .help("读取 \(model.uppercased()) 剖面")
                            .accessibilityLabel("读取 \(model.uppercased()) 剖面")
                            .disabled(store.busy)
                        }
                    }.font(.system(size: 11)).help(display?.error ?? display?.level ?? "无该模式结果")
                }
                if let cell = store.regionalForecast.model(store.regionalModel).cell(at: coordinate)
                    ?? store.regionalForecast.model(store.regionalModel).cityRanking.first(where: { $0.coordinate == coordinate }) {
                    RegionalCellDetail(model: store.regionalModel, cell: cell)
                }
                if store.regionalProfileCoordinate == coordinate,
                   !store.regionalProfileModel.isEmpty {
                    RegionalProfileView(model: store.regionalProfileModel, coordinate: coordinate)
                }
                Divider()
            }
            HStack {
                Text(L10n.t(store.regionalInspectorMode == "cities" ? "城市排名" : "高值排行"))
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                Text(store.regionalModel.uppercased()).font(.system(size: 10)).foregroundStyle(.secondary)
            }
            WorkSegments(options: ["grid", "cities"], titles: ["网格", "城市"],
                         selection: $store.regionalInspectorMode).frame(width: 124)
            if store.regionalInspectorMode == "cities" {
                TextField(L10n.t("搜索城市"), text: $store.regionalQuery).textFieldStyle(.roundedBorder)
                let rows = cityRanking
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(Array(rows.enumerated()), id: \.element.id) { index, cell in
                            Button { store.focusRegionalCell(cell.coordinate) } label: {
                                HStack(spacing: 6) {
                                    Text("\(index + 1)").font(.system(size: 10, weight: .semibold))
                                        .monospacedDigit().foregroundStyle(.secondary).frame(width: 18, alignment: .leading)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(cell.label).font(.system(size: 10, weight: .medium))
                                        Text(timeLabel(cell.bestTime)).font(.system(size: 9)).foregroundStyle(.secondary)
                                    }
                                    Spacer(minLength: 2)
                                    Text(cell.quality.map { String(format: "%.2f", $0) } ?? "—")
                                        .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                                }
                                .padding(.horizontal, 7).padding(.vertical, 5.5).contentShape(Rectangle())
                                .background(store.selectedRegionalCoordinate == cell.coordinate ? coolAccent.opacity(0.12) : Color.clear,
                                            in: RoundedRectangle(cornerRadius: 5))
                            }.buttonStyle(.plain)
                            Divider().opacity(0.35)
                        }
                    }
                    if rows.isEmpty {
                        let model = store.regionalForecast.model(store.regionalModel)
                        Text(model.cityRankingStatus == "unavailable"
                             ? (model.cityRankingReason ?? "该区域暂无城市排名")
                             : model.available ? "无匹配城市" : "等待预测")
                            .font(.system(size: 11)).foregroundStyle(.secondary).padding(.top, 24)
                    }
                }
                Text("前 \(rows.count) 个城市").font(.system(size: 9)).foregroundStyle(.tertiary)
            } else {
                TextField(L10n.t("搜索网格"), text: $store.regionalQuery).textFieldStyle(.roundedBorder)
                let rows = ranking
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(rows) { cell in
                            Button { store.focusRegionalCell(cell.coordinate) } label: {
                                HStack(spacing: 6) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(cell.coordinate.label).font(.system(size: 10)).monospacedDigit()
                                        Text(timeLabel(cell.bestTime)).font(.system(size: 9)).foregroundStyle(.secondary)
                                    }
                                    Spacer(minLength: 2)
                                    Text(cell.quality.map { String(format: "%.2f", $0) } ?? "—").font(.system(size: 12, weight: .semibold))
                                        .monospacedDigit()
                                }
                                .padding(.horizontal, 7).padding(.vertical, 5.5).contentShape(Rectangle())
                                .background(store.selectedRegionalCoordinate == cell.coordinate ? coolAccent.opacity(0.12) : Color.clear,
                                            in: RoundedRectangle(cornerRadius: 5))
                            }.buttonStyle(.plain)
                            Divider().opacity(0.35)
                        }
                    }
                    if rows.isEmpty {
                        Text(store.regionalForecast.model(store.regionalModel).available ? "无匹配网格" : "等待预测")
                            .font(.system(size: 11)).foregroundStyle(.secondary).padding(.top, 24)
                    }
                }
                Text("前 \(rows.count) 个网格").font(.system(size: 9)).foregroundStyle(.tertiary)
            }
        }.padding(.leading, 2).frame(maxHeight: .infinity)
    }

    private var ranking: [RegionalCell] {
        let query = store.regionalQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        let rows = store.regionalForecast.model(store.regionalModel).ranked
        return Array((query.isEmpty ? rows : rows.filter { $0.label.localizedCaseInsensitiveContains(query) }).prefix(2000))
    }

    private var cityRanking: [RegionalCell] {
        let query = store.regionalQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        let rows = store.regionalForecast.model(store.regionalModel).cityRanking
        return Array((query.isEmpty ? rows : rows.filter { $0.label.localizedCaseInsensitiveContains(query) }).prefix(2000))
    }

    private func number(_ title: String, _ value: Binding<Double>) -> some View {
        HStack(spacing: 6) {
            Text(title).fireSkyFont(size: 12).foregroundStyle(.secondary)
            TextField(title, value: value, format: .number).labelsHidden().textFieldStyle(.roundedBorder).frame(width: 75)
        }
    }
}

/// 区域网格弹窗明细（regional-1～4，Web BurnOutlookMap.svelte 弹窗的原生对应）。
/// 字段全部来自区域 points[] DTO 顶层与 quality_components；键缺失显示「未计算/未提供」，
/// 不在 SwiftUI 重算或猜值（方案 :33、:83-84）。置信度缺值显示「未提供」而非 0%。
private struct RegionalCellDetail: View {
    @EnvironmentObject private var store: Store
    let model: String
    let cell: RegionalCell

    private let metricColumns = [
        GridItem(.flexible(), spacing: 5),
        GridItem(.flexible(), spacing: 5),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(model.uppercased()).font(.system(size: 10, weight: .bold)).foregroundStyle(modelColor(model))
                if let peakLevel = cell.peakLevel, !peakLevel.isEmpty {
                    Text(peakLevel).font(.system(size: 9, weight: .medium))
                        .padding(.horizontal, 5).padding(.vertical, 1.5)
                        .background(warm.opacity(0.12), in: Capsule())
                }
                Spacer()
            }
            HStack(alignment: .top, spacing: 6) {
                scoreCard("峰值质量 / 2.5", cell.peakQuality)
                scoreCard("事件窗口质量 / 2.5", cell.eventQuality)
            }
            LazyVGrid(columns: metricColumns, alignment: .leading, spacing: 4) {
                gridMetric("压力族", cell.pressureFamily.map { familyLabel($0) } ?? "未提供")
                    .help("结果未包含该格点的主导可烧压力族字段；通常表示无可烧目标云。")
                gridMetric("可烧云", cell.cloudCoverageEffective.map { String(format: "%.1f%%", $0 * 100) } ?? "未计算")
                gridMetric("时长", cell.durationMinutes.map { String(format: "%.0f 分钟", $0) } ?? "未计算")
                gridMetric("质量 AOD", cell.qualityAod.map { String(format: "%.3f", $0) } ?? "未计算")
                gridMetric("入射修正", signedText(cell.incomingAdjustment))
                gridMetric("观测修正", signedText(cell.observerAdjustment))
                gridMetric("最佳时刻", cell.bestTime.map { timeLabel($0) } ?? "无明显峰值")
                gridMetric("置信度", cell.confidenceScore.map { "\(Int(($0 * 100).rounded()))%" } ?? "未提供")
            }
            Button {
                store.openRegionalCellInPointForecast(cell, model: model)
            } label: {
                Label("在单点模式打开", systemImage: "arrow.up.forward.app")
                    .font(.system(size: 10, weight: .medium))
            }
            .buttonStyle(.borderless)
            .help("用该网格的坐标、模式、日期与现象预填单点预测")
            .accessibilityLabel("在单点模式打开 \(cell.label)")
        }
        .padding(8)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
    }

    private func scoreCard(_ title: String, _ value: Double?) -> some View {
        VStack(alignment: .leading, spacing: 1.5) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary).lineLimit(1)
            Text(value.map { String(format: "%.3f", $0) } ?? "未计算")
                .font(.system(size: 12.5, weight: .semibold)).monospacedDigit()
                .foregroundStyle(value == nil ? Color.secondary : Color.primary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(5)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 5))
        .accessibilityElement(children: .combine)
    }

    private func gridMetric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary).lineLimit(1)
            Text(value).font(.system(size: 10, weight: .medium)).monospacedDigit()
                .lineLimit(1).minimumScaleFactor(0.85)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 6)
        .padding(.vertical, 3.5)
        .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 4))
        .accessibilityElement(children: .combine)
    }

    /// 入射/观测 AOD 修正：Web 弹窗的 ±0.3f 语义。
    private func signedText(_ value: Double?) -> String {
        guard let value else { return "未计算" }
        return String(format: "%+.3f", value)
    }
}

private extension Dictionary where Key == String, Value == Any {
    var regionCode: String { self["code"] as? String ?? "" }
}
