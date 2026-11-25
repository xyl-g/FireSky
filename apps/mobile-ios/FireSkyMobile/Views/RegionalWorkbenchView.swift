import SwiftUI

/// 区域预测工作台（中国 / 东亚等区域 preset + Canvas 墨卡托热力图 + 城市排行 + 网格详情）。
struct RegionalWorkbenchView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize

    private var model: RegionalModel { store.regionalForecast.model(store.regionalModel) }

    var body: some View {
        VStack(spacing: 14) {
            controlsCard
            WorkStaleResultBanner()
            heroSummaryCard
            heatmapCard
            if let coordinate = store.selectedRegionalCoordinate,
               let cell = model.cell(at: coordinate) ?? model.cityRanking.first(where: { $0.coordinate == coordinate }) {
                RegionalCellDetailCard(model: store.regionalModel, cell: cell)
            }
            rankingCard
        }
    }

    // MARK: - 控制面板

    private var controlsCard: some View {
        let dark = scheme == .dark
        return VStack(alignment: .leading, spacing: 11) {
            HStack {
                Text(L10n.text("区域预测参数", "Regional Parameters")).font(.headline)
                Spacer()
                Menu {
                    ForEach(store.availableRegions, id: \.code) { item in
                        Button {
                            store.selectRegionalPreset(item.code)
                        } label: {
                            HStack {
                                Text(item.label)
                                if store.selectedRegions.contains(item.code) {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "map")
                        Text(store.regionalTitle)
                        Image(systemName: "chevron.down").font(.caption2)
                    }
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(FSDesign.surfaceFill(role: .grouped, dark: dark), in: Capsule())
                    .overlay {
                        Capsule().strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
                    }
                }
            }

            Text(L10n.t("目标日期")).font(.caption2).foregroundStyle(.secondary)
            WorkSegments(options: ["今天", "明天", "自定义"],
                         titles: [L10n.t("今天"), L10n.t("明天"), L10n.t("自定义")],
                         selection: Binding(get: { store.dateTab }, set: { store.chooseDate($0) }))
            if store.dateTab == "自定义" {
                WorkDateButton()
            }

            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(L10n.t("观测现象")).font(.caption2).foregroundStyle(.secondary)
                    WorkSegments(options: ["dawn", "dusk"],
                                 titles: [L10n.t("朝霞"), L10n.t("晚霞")],
                                 selection: $store.phenomenon, accented: true)
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text(L10n.text("气象模式", "Model")).font(.caption2).foregroundStyle(.secondary)
                    WorkSegments(options: ["gfs", "ec"],
                                 titles: ["GFS", "EC"],
                                 selection: $store.regionalModel)
                }
            }

            AccentButton(
                title: L10n.text("开始区域预测", "Start Regional Forecast"),
                systemImage: "map.fill",
                disabled: store.busy || !store.online
            ) {
                store.beginCheck(for: .regional)
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

    // MARK: - 区域 Hero 摘要

    private var heroSummaryCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle().fill(modelColor(store.regionalModel)).frame(width: 7, height: 7)
                Text("\(store.regionalModel.uppercased()) · \(store.regionalTitle)")
                    .font(.caption.weight(.semibold))
                Spacer()
                Text(L10n.status(model.status))
                    .font(.caption2)
                    .foregroundStyle(model.error == nil ? Color.secondary : Color.red)
            }

            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(model.peak?.quality.map { String(format: "%.2f", $0) } ?? "—")
                            .font(.system(size: 30, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(model.peak?.quality == nil ? Color.secondary : modelColor(store.regionalModel))
                        Text("/ 2.5").font(.caption2).foregroundStyle(.secondary)
                    }
                    Text(L10n.text("区域峰值", "Regional peak"))
                        .font(.caption2).foregroundStyle(.secondary)
                }

                Spacer(minLength: 8)

                VStack(alignment: .trailing, spacing: 4) {
                    if let peak = model.peak {
                        Text("\(L10n.text("最佳地点", "Best")) · \(peak.label)")
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                    } else {
                        Text(L10n.text("等待区域数据", "Waiting for data"))
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    Text(model.available
                         ? L10n.text("\(model.validCount) 有效 · \(model.highCount) 高值 · \(model.missingCount) 缺测",
                                     "\(model.validCount) valid · \(model.highCount) high · \(model.missingCount) missing")
                         : L10n.text("高值格点 ≥ 1.50", "High quality ≥ 1.50"))
                        .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                }
            }

            if model.available {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 6) { regionalMetaChips }
                    VStack(alignment: .leading, spacing: 6) { regionalMetaChips }
                }
                .font(.caption2).foregroundStyle(.secondary)
            }

            if let error = model.error {
                Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface(emphasized: model.available)
    }

    @ViewBuilder
    private var regionalMetaChips: some View {
        if let precision = model.precision {
            Text(precision).lineLimit(1).workChip()
        }
        if model.pointCount > 0 {
            Text("\(model.successCount)/\(model.pointCount) 点").lineLimit(1).monospacedDigit().workChip()
        }
        if let cycle = model.cycle {
            Text(cycle.prefix(16).replacingOccurrences(of: "T", with: " ") + "Z")
                .lineLimit(1).monospacedDigit().workChip()
        }
        if model.cacheStatus == "hit" {
            Text(L10n.text("缓存快照", "Cache hit")).lineLimit(1).workChip()
        }
    }

    // MARK: - Mercator Canvas 热力图

    private var heatmapCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L10n.text("区域分布热图", "Regional Heatmap")).font(.subheadline.weight(.semibold))
                Spacer()
                Text(L10n.text("点击格点查看详情", "Tap cell to inspect"))
                    .font(.caption2).foregroundStyle(.secondary)
            }

            if model.cells.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "map").font(.title2).foregroundStyle(.secondary)
                    Text(model.available
                         ? L10n.text("该区域暂无可绘制格点", "No grid cells available")
                         : L10n.text("运行区域预测后，这里将渲染 0.25° 墨卡托热力分布图。", "Run a regional forecast to render the Mercator heatmap."))
                        .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, minHeight: 200)
                .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
            } else {
                RegionalMercatorHeatmap(
                    cells: model.cells,
                    cities: Array(model.cityRanking.prefix(15)),
                    bounds: store.selectedRegionBounds,
                    selected: store.selectedRegionalCoordinate
                ) { lat, lon in
                    if let hit = model.hit(lat: lat, lon: lon, maxDistanceDeg: max(1.5, model.pointSpacingDeg * 1.5)) {
                        store.selectedRegionalCoordinate = hit
                    } else if let nearestCity = model.cityRanking.min(by: {
                        hypot($0.coordinate.lat - lat, $0.coordinate.lon - lon) <
                        hypot($1.coordinate.lat - lat, $1.coordinate.lon - lon)
                    }), hypot(nearestCity.coordinate.lat - lat, nearestCity.coordinate.lon - lon) <= 3.0 {
                        store.selectedRegionalCoordinate = nearestCity.coordinate
                    }
                }
                .frame(height: sizeClass == .regular ? 340 : 250)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay {
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(Color.secondary.opacity(0.22), lineWidth: 1)
                }

                heatmapLegend
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private var heatmapLegend: some View {
        HStack(spacing: 8) {
            Text(L10n.t("质量指数"))
            VStack(spacing: 2) {
                HStack(spacing: 0) {
                    ForEach(0..<RegionalPalette.steps, id: \.self) { index in
                        RegionalPalette.color(index)
                    }
                }
                .frame(width: 140, height: 7)
                .clipShape(Capsule())
                HStack(spacing: 0) {
                    Text("0")
                    Spacer(minLength: 0)
                    Text("0.5")
                    Spacer(minLength: 0)
                    Text("1.0")
                    Spacer(minLength: 0)
                    Text("1.5")
                    Spacer(minLength: 0)
                    Text("2.0")
                    Spacer(minLength: 0)
                    Text("2.5")
                }
                .frame(width: 140)
            }
            Spacer(minLength: 4)
            Rectangle()
                .stroke(.secondary.opacity(0.5), style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                .frame(width: 10, height: 10)
            Text(L10n.t("缺测"))
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
    }

    // MARK: - 城市与高值排行

    private var rankingCard: some View {
        let query = store.regionalQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        let isCities = store.regionalInspectorMode == "cities"
        let sourceRows = isCities ? model.cityRanking : model.ranked
        let filteredRows = Array((query.isEmpty ? sourceRows : sourceRows.filter {
            $0.label.localizedCaseInsensitiveContains(query)
        }).prefix(100))

        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(isCities ? L10n.text("城市 Top 排行", "City Top Ranking") : L10n.text("高值格点排行", "Top Grid Cells"))
                    .font(.subheadline.weight(.semibold))
                Spacer()
                WorkSegments(
                    options: ["cities", "grid"],
                    titles: [L10n.text("城市", "Cities"), L10n.text("网格", "Grid")],
                    selection: $store.regionalInspectorMode
                )
                .frame(width: 132)
            }

            if !sourceRows.isEmpty {
                HStack(spacing: 7) {
                    Image(systemName: "magnifyingglass").font(.caption).foregroundStyle(.secondary)
                    TextField(isCities ? L10n.text("搜索城市", "Search city") : L10n.text("搜索格点", "Search cell"),
                              text: $store.regionalQuery)
                        .textFieldStyle(.plain)
                    if !store.regionalQuery.isEmpty {
                        Button { store.regionalQuery = "" } label: {
                            Image(systemName: "xmark.circle.fill").font(.caption).foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }

            if filteredRows.isEmpty {
                Text(isCities && model.cityRankingStatus == "unavailable"
                     ? (model.cityRankingReason ?? L10n.text("该区域暂无城市排行", "City ranking unavailable"))
                     : (model.available ? L10n.text("无匹配结果", "No matches") : L10n.text("等待区域预测结果", "Waiting for regional forecast")))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.vertical, 20)
                    .frame(maxWidth: .infinity, alignment: .center)
            } else {
                LazyVStack(spacing: 0) {
                    ForEach(Array(filteredRows.enumerated()), id: \.element.id) { index, cell in
                        let isSelected = store.selectedRegionalCoordinate == cell.coordinate
                        Button {
                            store.selectedRegionalCoordinate = cell.coordinate
                        } label: {
                            HStack(spacing: 10) {
                                Text("\(index + 1)")
                                    .font(.caption.weight(.bold))
                                    .monospacedDigit()
                                    .foregroundStyle(index < 3 ? warm : Color.secondary)
                                    .frame(width: 24, alignment: .leading)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(cell.label).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                                    HStack(spacing: 6) {
                                        Text(L10n.qualityLevel(cell.level))
                                        Text("·")
                                        Text(timeLabel(cell.bestTime))
                                    }
                                    .font(.caption2).foregroundStyle(.secondary)
                                }
                                Spacer(minLength: 4)
                                Text(cell.quality.map { String(format: "%.2f", $0) } ?? "—")
                                    .font(.headline.weight(.semibold))
                                    .monospacedDigit()
                                    .foregroundStyle(cell.quality.map { RegionalPalette.fill($0).swiftUIColor } ?? Color.secondary)
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 9)
                            .background(isSelected ? coolAccent.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        if index < filteredRows.count - 1 {
                            Divider().opacity(0.4)
                        }
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }
}

/// Web Mercator 投影交互式 Canvas 热力图。
private struct RegionalMercatorHeatmap: View {
    @Environment(\.colorScheme) private var scheme
    let cells: [RegionalCell]
    let cities: [RegionalCell]
    let bounds: [Double]?
    let selected: GridCoordinate?
    let onTapCoordinate: (Double, Double) -> Void

    private struct Viewport {
        let minX: Double
        let maxX: Double
        let minY: Double
        let maxY: Double
        let stepLon: Double
        let stepLat: Double

        init(cells: [RegionalCell], bounds: [Double]?) {
            let lats = cells.map(\.coordinate.lat)
            let lons = cells.map(\.coordinate.lon)
            let west = bounds?[0] ?? lons.min() ?? 73.0
            let south = bounds?[1] ?? lats.min() ?? 18.0
            let east = bounds?[2] ?? lons.max() ?? 135.0
            let north = bounds?[3] ?? lats.max() ?? 54.0

            let sortedLons = Array(Set(lons)).sorted()
            let sortedLats = Array(Set(lats)).sorted()
            let dLon = zip(sortedLons, sortedLons.dropFirst()).map { $1 - $0 }.filter { $0 > 0.05 }.min() ?? 0.25
            let dLat = zip(sortedLats, sortedLats.dropFirst()).map { $1 - $0 }.filter { $0 > 0.05 }.min() ?? 0.25
            self.stepLon = min(5.0, max(0.25, dLon))
            self.stepLat = min(5.0, max(0.25, dLat))

            self.minX = Self.mercX(west - stepLon * 0.5)
            self.maxX = Self.mercX(east + stepLon * 0.5)
            self.minY = Self.mercY(south - stepLat * 0.5)
            self.maxY = Self.mercY(north + stepLat * 0.5)
        }

        static func mercX(_ lon: Double) -> Double {
            lon * .pi / 180.0
        }

        static func mercY(_ lat: Double) -> Double {
            let clamped = min(85.0, max(-85.0, lat))
            let rad = clamped * .pi / 180.0
            return log(tan(.pi / 4.0 + rad / 2.0))
        }

        static func lon(fromMercX x: Double) -> Double {
            x * 180.0 / .pi
        }

        static func lat(fromMercY y: Double) -> Double {
            (2.0 * atan(exp(y)) - .pi / 2.0) * 180.0 / .pi
        }

        func rect(for coordinate: GridCoordinate, in size: CGSize) -> CGRect {
            let x0 = Self.mercX(coordinate.lon - stepLon * 0.5)
            let x1 = Self.mercX(coordinate.lon + stepLon * 0.5)
            let y0 = Self.mercY(coordinate.lat + stepLat * 0.5)
            let y1 = Self.mercY(coordinate.lat - stepLat * 0.5)

            let spanX = max(1e-6, maxX - minX)
            let spanY = max(1e-6, maxY - minY)

            let left = (x0 - minX) / spanX * size.width
            let right = (x1 - minX) / spanX * size.width
            let top = (maxY - y0) / spanY * size.height
            let bottom = (maxY - y1) / spanY * size.height
            return CGRect(x: left, y: top, width: max(2, right - left), height: max(2, bottom - top))
        }

        func point(for coordinate: GridCoordinate, in size: CGSize) -> CGPoint {
            let spanX = max(1e-6, maxX - minX)
            let spanY = max(1e-6, maxY - minY)
            let px = (Self.mercX(coordinate.lon) - minX) / spanX * size.width
            let py = (maxY - Self.mercY(coordinate.lat)) / spanY * size.height
            return CGPoint(x: px, y: py)
        }

        func coordinate(at point: CGPoint, in size: CGSize) -> (lat: Double, lon: Double) {
            let spanX = max(1e-6, maxX - minX)
            let spanY = max(1e-6, maxY - minY)
            let mx = minX + (point.x / max(1, size.width)) * spanX
            let my = maxY - (point.y / max(1, size.height)) * spanY
            return (Self.lat(fromMercY: my), Self.lon(fromMercX: mx))
        }
    }

    var body: some View {
        let viewport = Viewport(cells: cells, bounds: bounds)
        GeometryReader { geo in
            Canvas { context, size in
                let bg = scheme == .dark
                    ? Color(red: 0.08, green: 0.11, blue: 0.18)
                    : Color(red: 0.93, green: 0.95, blue: 0.98)
                context.fill(Path(CGRect(origin: .zero, size: size)), with: .color(bg))

                // 经纬参考线
                for lon in stride(from: 80.0, through: 130.0, by: 10.0) {
                    let pt = viewport.point(for: GridCoordinate(lat: 35, lon: lon)!, in: size)
                    var path = Path()
                    path.move(to: CGPoint(x: pt.x, y: 0))
                    path.addLine(to: CGPoint(x: pt.x, y: size.height))
                    context.stroke(path, with: .color(Color.secondary.opacity(0.14)), lineWidth: 0.6)
                }
                for lat in stride(from: 20.0, through: 50.0, by: 10.0) {
                    let pt = viewport.point(for: GridCoordinate(lat: lat, lon: 105)!, in: size)
                    var path = Path()
                    path.move(to: CGPoint(x: 0, y: pt.y))
                    path.addLine(to: CGPoint(x: size.width, y: pt.y))
                    context.stroke(path, with: .color(Color.secondary.opacity(0.14)), lineWidth: 0.6)
                }

                // 格点热力块
                for cell in cells {
                    let rect = viewport.rect(for: cell.coordinate, in: size)
                    let path = Path(rect.insetBy(dx: -0.3, dy: -0.3))
                    if let quality = cell.quality {
                        context.fill(path, with: .color(RegionalPalette.fill(quality).swiftUIColor))
                    } else {
                        context.stroke(path, with: .color(Color.secondary.opacity(0.28)),
                                       style: StrokeStyle(lineWidth: 0.6, dash: [2, 2]))
                    }
                }

                // 城市锚点
                for city in cities {
                    let pt = viewport.point(for: city.coordinate, in: size)
                    let dot = CGRect(x: pt.x - 3, y: pt.y - 3, width: 6, height: 6)
                    context.fill(Path(ellipseIn: dot), with: .color(.white.opacity(0.9)))
                    context.stroke(Path(ellipseIn: dot), with: .color(Color.black.opacity(0.5)), lineWidth: 0.8)
                }

                // 选中高亮环
                if let selected {
                    let pt = viewport.point(for: selected, in: size)
                    let ring = CGRect(x: pt.x - 7, y: pt.y - 7, width: 14, height: 14)
                    context.stroke(Path(ellipseIn: ring), with: .color(.white), lineWidth: 2.2)
                    context.stroke(Path(ellipseIn: ring.insetBy(dx: -1.5, dy: -1.5)), with: .color(warm), lineWidth: 1.5)
                }
            }
            .contentShape(Rectangle())
            .gesture(
                SpatialTapGesture()
                    .onEnded { value in
                        let coord = viewport.coordinate(at: value.location, in: geo.size)
                        onTapCoordinate(coord.lat, coord.lon)
                    }
            )
        }
    }
}

/// 选中网格/城市详情卡。
private struct RegionalCellDetailCard: View {
    @EnvironmentObject private var store: Store
    let model: String
    let cell: RegionalCell

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(cell.label).font(.subheadline.weight(.semibold))
                if let peakLevel = cell.peakLevel, !peakLevel.isEmpty {
                    Text(L10n.qualityLevel(peakLevel))
                        .font(.caption2.weight(.medium))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(warm.opacity(0.14), in: Capsule())
                }
                Spacer()
                Button { store.selectedRegionalCoordinate = nil } label: {
                    Image(systemName: "xmark").font(.caption).foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }

            HStack(spacing: 10) {
                scoreBox(L10n.text("峰值质量 / 2.5", "Peak / 2.5"), cell.peakQuality)
                scoreBox(L10n.text("窗口质量 / 2.5", "Window / 2.5"), cell.eventQuality)
            }

            VStack(spacing: 4) {
                detailRow(L10n.t("坐标"), cell.coordinate.label)
                detailRow(L10n.t("主导压力族"), cell.pressureFamily.map { familyLabel($0) } ?? L10n.text("未提供", "N/A"))
                detailRow(L10n.t("有效可烧云量"), cell.cloudCoverageEffective.map { String(format: "%.1f%%", $0 * 100) } ?? L10n.text("未计算", "N/A"))
                detailRow(L10n.t("持续时长"), cell.durationMinutes.map { String(format: "%.0f 分钟", $0) } ?? L10n.text("未计算", "N/A"))
                detailRow("AOD 550", cell.qualityAod.map { String(format: "%.3f", $0) } ?? L10n.text("未计算", "N/A"))
                detailRow(L10n.t("最佳时刻"), cell.bestTime.map { timeLabel($0) } ?? L10n.text("无明显峰值", "N/A"))
                detailRow(L10n.text("置信度", "Confidence"), cell.confidenceScore.map { "\(Int(($0 * 100).rounded()))%" } ?? L10n.text("未提供", "N/A"))
            }

            Button {
                store.openRegionalCellInPointForecast(cell, model: model)
            } label: {
                Label(L10n.text("在单点模式打开", "Open in Point Forecast"), systemImage: "arrow.up.forward.app")
                    .font(.caption.weight(.semibold))
                    .frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.bordered)
            .tint(warm)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface(emphasized: true)
    }

    private func scoreBox(_ title: String, _ value: Double?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(value.map { String(format: "%.3f", $0) } ?? L10n.text("未计算", "N/A"))
                .font(.title3.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(value == nil ? Color.secondary : Color.primary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 8))
    }

    private func detailRow(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            Text(value).monospacedDigit()
        }
        .font(.caption)
    }
}
