import Charts
import SwiftUI

/// 单点工作台（Workbench.swift 拆分迁移：单点部分；区域/实况页排除）。
/// 布局降级：桌面 HStack 双栏 → 紧凑端单列滚动，iPad（regular size class）
/// 保留双栏；「开始预测」按钮随参数列下移到底部操作区。
struct PointWorkbenchView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.horizontalSizeClass) private var sizeClass

    var body: some View {
        Group {
            if sizeClass == .regular {
                HStack(alignment: .top, spacing: 16) {
                    VStack(spacing: 12) { controls }.frame(width: 320)
                    VStack(spacing: 12) { hero; results }
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }.padding(18)
            } else {
                ScrollView {
                    VStack(spacing: 16) {
                        controls
                        hero
                        results
                    }.padding(.horizontal, 16).padding(.vertical, 12)
                }
            }
        }
        .workPageBackground()
    }

    private var controls: some View {
        Group {
            WorkLocationView()
            WorkParametersView()
            Button { store.beginCheck() } label: {
                Text("开始预测")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color.white.opacity(0.92))
                    .frame(maxWidth: .infinity).frame(height: 44)
                    .background(warm, in: RoundedRectangle(cornerRadius: 10))
            }.buttonStyle(.plain)
                .opacity(store.busy || !store.online ? 0.45 : 1)
                .disabled(store.busy || !store.online)
            if store.busy {
                VStack(spacing: 6) {
                    ProgressView(value: store.percent, total: 100)
                    HStack {
                        Text(store.status).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(2)
                        Spacer()
                        Button { store.cancel() } label: { Image(systemName: "xmark") }
                    }
                }
            }
            if !store.online && !store.error.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Label("引擎未连接", systemImage: "exclamationmark.triangle")
                        .font(.system(size: 11)).foregroundStyle(.red)
                    Button("重新连接") { Task { await store.connect() } }
                        .buttonStyle(.borderless).font(.system(size: 11))
                }
            }
        }
    }

    private var hero: some View { WorkHeroView() }
    private var results: some View { WorkResultsView() }
}

/// 观测点卡片（观测点管理 + 地图选点 + 收藏）。
struct WorkLocationView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("观测点").font(.system(size: 13, weight: .semibold))
                Spacer()
                WorkSegments(options: ["地图", "收藏"], selection: $store.locationTab).frame(width: 128)
            }
            HStack(spacing: 6) {
                TextField("搜索地名，如「西湖」或「杭州」", text: $store.query) {
                    store.search()
                }
                .textFieldStyle(.roundedBorder)
                Button { store.search() } label: { Image(systemName: "magnifyingglass") }
                Button { store.locate() } label: {
                    if store.locating { ProgressView().frame(width: 16, height: 16) }
                    else { Image(systemName: "location").frame(width: 16, height: 16) }
                }.accessibilityLabel("获取当前位置")
                    .disabled(store.locating || store.busy)
            }.controlSize(.large)
            if store.locationTab == "地图" {
                ForecastMap(store: store, mini: true).frame(height: 200)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            } else {
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(Array(store.favorites.enumerated()), id: \.offset) { _, item in
                            Button(item["name"] as? String ?? "收藏位置") { store.applyLocation(item) }
                                .buttonStyle(.borderless).frame(maxWidth: .infinity, alignment: .leading)
                        }
                        if store.favorites.isEmpty {
                            Text("暂无收藏，可在地图选点后收藏。")
                                .font(.system(size: 11)).foregroundStyle(.secondary)
                        }
                    }.padding(8)
                }.frame(height: 200)
            }
            HStack(spacing: 9) {
                Circle().fill(warm).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 4) {
                    Text(store.name).font(.system(size: 13, weight: .semibold))
                    Text(String(format: "%.4f° N, %.4f° E", store.lat, store.lon))
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 5) {
                Text(store.metadataReady ? String(format: "海拔 %.0f m", store.elevation) : "海拔未确认").workChip()
                Text(store.timezone).lineLimit(1).workChip()
            }.font(.system(size: 9)).foregroundStyle(.secondary)
            HStack {
                Button("收藏当前点", systemImage: "star") { store.starLocation() }
                Spacer()
                Button("大地图", systemImage: "arrow.up.left.and.arrow.down.right") { store.showMapPicker = true }
            }.buttonStyle(.borderless).font(.system(size: 11))
        }.padding(15).workSurface().disabled(store.busy)
    }
}

/// 观测参数卡片（目标日期 + 现象 + 手动坐标编辑）。
struct WorkParametersView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("观测参数").font(.system(size: 13, weight: .semibold))
                Spacer()
                Button("重置") { store.chooseDate("今天"); store.phenomenon = "dusk" }
                    .buttonStyle(.borderless).font(.system(size: 11))
            }
            Text("目标日期").font(.system(size: 10)).foregroundStyle(.secondary)
            WorkSegments(options: ["今天", "明天", "自定义"],
                         selection: Binding(get: { store.dateTab }, set: { store.chooseDate($0) }))
            if store.dateTab == "自定义" {
                WorkDateButton()
            }
            Text("观测现象").font(.system(size: 10)).foregroundStyle(.secondary)
            WorkSegments(options: ["dawn", "dusk"], titles: ["朝霞", "晚霞"],
                         selection: $store.phenomenon, accented: true)
            Button("编辑坐标", systemImage: "slider.horizontal.3") { store.editingCoordinates.toggle() }
                .buttonStyle(.borderless).font(.system(size: 11)).frame(maxWidth: .infinity)
            if store.editingCoordinates {
                VStack(spacing: 8) {
                    TextField("名称", text: $store.name)
                    number("纬度", $store.lat)
                    number("经度", $store.lon)
                    number("海拔 (m)", $store.elevation)
                    TextField("时区", text: $store.timezone)
                    Button("获取海拔与时区") { store.lookupMetadata() }.buttonStyle(.borderless)
                    Toggle("已确认位置数据", isOn: $store.metadataReady).font(.system(size: 11))
                }.textFieldStyle(.roundedBorder)
            }
            if !store.error.isEmpty {
                Text(store.error).font(.system(size: 11)).foregroundStyle(.red).textSelection(.enabled)
            }
        }.padding(15).workSurface().disabled(store.busy)
    }

    private func number(_ title: String, _ value: Binding<Double>) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            TextField(title, value: value, format: .number).labelsHidden().frame(width: 130)
                .keyboardType(.numbersAndPunctuation)
        }
    }
}

/// 自定义日期按钮 + 月历弹层（桌面 popover 语义，iPhone 上自动降级为 sheet）。
private struct WorkDateButton: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        Button {
            store.calendarMonth = store.date
            store.showDatePicker.toggle()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "calendar").foregroundStyle(warm)
                Text(store.targetDateLabel).monospacedDigit()
                Spacer(minLength: 4)
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .medium)).foregroundStyle(.secondary)
            }.font(.system(size: 12)).padding(.horizontal, 11).frame(height: 34)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
        }.buttonStyle(.plain)
            .accessibilityLabel("目标日期 \(store.targetDateLabel)")
            .popover(isPresented: $store.showDatePicker, arrowEdge: .bottom) { WorkCalendarView() }
    }
}

private struct WorkCalendarView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    private var month: ObservationMonth { ObservationMonth(containing: store.calendarMonth, timezone: store.timezone) }
    private let columns = Array(repeating: GridItem(.fixed(30), spacing: 4), count: 7)
    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text(month.title).font(.system(size: 13, weight: .semibold))
                Spacer()
                Button { store.calendarMonth = month.moved(-1) } label: {
                    Image(systemName: "chevron.left").frame(width: 24, height: 24)
                }
                Button { store.calendarMonth = month.moved(1) } label: {
                    Image(systemName: "chevron.right").frame(width: 24, height: 24)
                }
            }.buttonStyle(.plain)
            LazyVGrid(columns: columns, spacing: 4) {
                ForEach(["一", "二", "三", "四", "五", "六", "日"], id: \.self) { day in
                    Text(day).font(.system(size: 10)).foregroundStyle(.secondary).frame(width: 30, height: 22)
                }
                ForEach(month.days, id: \.self) { day in
                    let selected = month.calendar.isDate(day, inSameDayAs: store.date)
                    let current = month.calendar.isDate(day, equalTo: month.start, toGranularity: .month)
                    let today = month.calendar.isDate(day, inSameDayAs: .now)
                    Button { select(day) } label: {
                        Text("\(month.calendar.component(.day, from: day))")
                            .font(.system(size: 11, weight: selected ? .semibold : .regular)).monospacedDigit()
                            .foregroundStyle(selected ? (scheme == .dark ? Color.black : Color.white)
                                             : current ? Color.primary : Color.secondary.opacity(0.55))
                            .frame(width: 30, height: 30)
                            .background(selected ? warm : .clear, in: Circle())
                            .overlay { Circle().stroke(today && !selected ? warm.opacity(0.7) : .clear, lineWidth: 1) }
                            .contentShape(Circle())
                    }.buttonStyle(.plain).accessibilityLabel(ObservationDate.label(day, timezone: store.timezone))
                        .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            Divider()
            HStack {
                Button("今天") { select(.now) }.buttonStyle(.plain).foregroundStyle(warm)
                Spacer()
                Button { store.showDatePicker = false } label: {
                    Image(systemName: "xmark").frame(width: 24, height: 24)
                }.buttonStyle(.plain)
            }.font(.system(size: 11))
        }.padding(14).frame(width: 262)
            .tint(warm).background(.regularMaterial)
    }

    private func select(_ day: Date) {
        store.date = day
        store.showDatePicker = false
    }
}

/// 结果概览 hero（GFS/EC 双模型摘要）。
struct WorkHeroView: View {
    @EnvironmentObject private var store: Store
    private var comparison: ForecastComparison { store.pointComparison }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(store.name) · \(store.targetDateLabel) \(store.phenomenon == "dawn" ? "朝霞" : "晚霞")")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 14) {
                summary("gfs")
                Divider().frame(height: 124).opacity(0.5)
                summary("ec")
            }
        }.padding(18).frame(maxWidth: .infinity).workSurface(emphasized: true)
    }

    private func summary(_ model: String) -> some View {
        let report = comparison.report(model)
        let quality = comparison.peak(model)
        return VStack(alignment: .leading, spacing: 9) {
            HStack {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).font(.system(size: 12, weight: .semibold))
                Spacer(minLength: 4)
                Text(comparison.status(model)).font(.system(size: 9))
                    .foregroundStyle(comparison.error(model) == nil ? Color.secondary : Color.red)
            }
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Text(format(quality, digits: 2)).font(.system(size: 30, weight: .semibold))
                            .monospacedDigit().foregroundStyle(quality == nil ? Color.secondary : modelColor(model))
                            .lineLimit(1).minimumScaleFactor(0.8)
                        Text("/ 2.5").font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                    Text(report?["quality_level"] as? String ?? "峰值质量指数")
                        .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 5) {
                    Text("最佳时刻").font(.system(size: 9)).foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .font(.system(size: 20, weight: .semibold)).monospacedDigit()
                }
            }
            Text("推荐窗口 \(windowLabel(report))").font(.system(size: 10)).foregroundStyle(.secondary)
            if let message = comparison.error(model) {
                Text(message).font(.system(size: 10)).foregroundStyle(.red).lineLimit(2).textSelection(.enabled)
            }
        }.frame(maxWidth: .infinity, minHeight: 124, alignment: .topLeading)
            .accessibilityElement(children: .contain).accessibilityLabel(model.uppercased())
    }
}

private let metricSpec: [(String, String, String)] = [
    ("burnable", "有效可烧云量", "前向可烧云占视野比例"),
    ("material", "云质厚度", "云体材质负载"),
    ("brightness", "亮度色彩", "霞光亮度与色彩评分"),
    ("aod", "AOD 550", "气溶胶光学厚度 · 通透度"),
    ("incoming", "入射受光", "太阳入射照亮云体的因子"),
    ("contrast", "返程对比度", "观测者视线大气对比因子"),
    ("duration", "持续时长", "核心观赏窗口 · 分钟"),
    ("confidence", "置信度", "引擎结果置信评分"),
    ("disagreement", "模式分歧", "多模式结论分歧度"),
]

/// 结果区：分析标签页 + 指标卡组 + 质量曲线（紧湊端指标卡自适应换行）。
struct WorkResultsView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.horizontalSizeClass) private var sizeClass
    private var comparison: ForecastComparison { store.pointComparison }
    private var report: JSONObject? { comparison.report(store.selectedModel) }
    private let groups = [("云况与色彩", 0..<3), ("大气光学", 3..<6), ("时效与可靠性", 6..<9)]
    var body: some View {
        VStack(spacing: 12) {
            WorkAnalysisTabs()
            if store.resultTab != "概览" {
                HStack {
                    WorkSegments(options: ForecastComparison.models, titles: ["GFS", "EC"], selection: $store.selectedModel)
                        .frame(width: 150)
                    Spacer()
                    Text(comparison.status(store.selectedModel)).font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            ScrollView {
                VStack(spacing: 12) {
                    if store.resultTab == "概览" {
                        ForEach(groups, id: \.0) { title, range in
                            VStack(alignment: .leading, spacing: 7) {
                                Text(title).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                                LazyVGrid(columns: columns, spacing: 10) {
                                    ForEach(Array(metricSpec[range]), id: \.0) { spec in
                                        metricCard(spec)
                                    }
                                }
                            }
                        }
                        WorkComparisonCurveView(comparison: comparison, timezone: store.timezone)
                    } else if let message = comparison.error(store.selectedModel) {
                        Text("\(store.selectedModel.uppercased()) 未完成：\(message)")
                            .font(.system(size: 12)).foregroundStyle(.red).textSelection(.enabled)
                            .padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
                    } else if report == nil, comparison.result != nil {
                        Text("\(store.selectedModel.uppercased()) 暂无结果")
                            .foregroundStyle(.secondary).padding(30)
                    } else if store.resultTab == "逐时台账" {
                        WorkLedgerView(report: report)
                    } else {
                        WorkDiagnosticView(report: report, profile: store.resultTab == "大气剖面")
                    }
                }
            }
        }
    }

    private var columns: [GridItem] {
        sizeClass == .regular
            ? [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())]
            : [GridItem(.adaptive(minimum: 158), spacing: 10)]
    }

    private func metricCard(_ spec: (String, String, String)) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(spec.1).font(.system(size: 12)).foregroundStyle(.primary.opacity(0.8))
            if spec.0 == "aod" {
                WorkAODValue(summary: comparison.aod)
            } else {
                HStack(spacing: 10) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(model.uppercased()).font(.system(size: 9, weight: .medium))
                                .foregroundStyle(modelColor(model))
                            Text(metricValue(spec.0, comparison.report(model)))
                                .font(.system(size: 19, weight: .semibold)).monospacedDigit()
                                .foregroundStyle(comparison.report(model) == nil ? Color.secondary : Color.primary)
                                .lineLimit(1).minimumScaleFactor(0.8)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            Text(spec.0 == "aod" ? comparison.aod.caption : spec.2)
                .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(2)
        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: 112).workSurface()
    }
}

private struct WorkAnalysisTabs: View {
    @EnvironmentObject private var store: Store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var tabIndicator
    var body: some View {
        HStack(spacing: 14) {
            ForEach(["概览", "大气剖面", "诊断与时间窗", "逐时台账"], id: \.self) { tab in
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { store.resultTab = tab }
                } label: {
                    Text(tab).font(.system(size: 13, weight: store.resultTab == tab ? .semibold : .regular))
                        .foregroundStyle(store.resultTab == tab ? warm : Color.secondary)
                        .padding(.horizontal, 4).frame(height: 40)
                        .overlay(alignment: .bottom) {
                            if store.resultTab == tab {
                                Rectangle().fill(warm).frame(height: 3)
                                    .matchedGeometryEffect(id: "active-tab", in: tabIndicator)
                            }
                        }
                }.buttonStyle(.plain).accessibilityAddTraits(store.resultTab == tab ? .isSelected : [])
            }
        }
    }
}

struct WorkComparisonCurveView: View {
    let comparison: ForecastComparison
    let timezone: String
    var body: some View {
        let points = comparison.curve(timezone: timezone)
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("霞光质量变化").font(.system(size: 13, weight: .semibold))
                Spacer()
                ForEach(ForecastComparison.models, id: \.self) { model in
                    HStack(spacing: 5) {
                        Rectangle().fill(modelColor(model)).frame(width: 14, height: 2)
                        Text(model.uppercased())
                    }.font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            Chart(points) { point in
                LineMark(x: .value("时间", point.time), y: .value("质量指数", point.quality),
                         series: .value("曲线", point.series))
                    .foregroundStyle(modelColor(point.model))
                    .lineStyle(StrokeStyle(lineWidth: 2, dash: point.model == "ec" ? [4, 3] : []))
                PointMark(x: .value("时间", point.time), y: .value("质量指数", point.quality))
                    .foregroundStyle(modelColor(point.model)).symbolSize(8)
            }
            .chartYScale(domain: 0...2.5)
            .chartYAxis {
                AxisMarks(position: .leading, values: [0, 1.25, 2.5]) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    AxisValueLabel {
                        if let number = value.as(Double.self) {
                            Text(number.formatted(.number.precision(.fractionLength(0...2))))
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
            .frame(height: 184)
            .overlay {
                if points.isEmpty {
                    Text(comparison.result == nil ? "等待预测" : "暂无质量曲线")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }.padding(16).workSurface()
    }
}

/// 逐时台账。
struct WorkLedgerView: View {
    let report: JSONObject?
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("时间").frame(maxWidth: .infinity, alignment: .leading)
                Text("质量指数").frame(maxWidth: .infinity)
                Text("太阳高度").frame(maxWidth: .infinity)
            }.font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary).padding(14)
            Divider()
            ForEach(Array((report?["steps"] as? [JSONObject] ?? []).enumerated()), id: \.offset) { _, item in
                HStack {
                    Text(timeLabel(item["valid_time_local"] as? String)).frame(maxWidth: .infinity, alignment: .leading)
                    Text(format(item["instant_quality_index"], digits: 3)).frame(maxWidth: .infinity)
                    Text(format(item["solar_elevation_deg"], digits: 2)).frame(maxWidth: .infinity)
                }.font(.system(size: 11)).monospacedDigit().padding(12)
                Divider().opacity(0.5)
            }
            if report == nil { Text("等待预测").foregroundStyle(.secondary).padding(40) }
        }.workSurface()
    }
}

/// 诊断与时间窗 / 大气剖面摘要。桌面版的 NSWorkspace 打开 HTML 报告按钮
/// 未迁移（v1 不做 HTML 报告）。
struct WorkDiagnosticView: View {
    let report: JSONObject?
    let profile: Bool
    var waiting = "等待预测"
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(profile ? "太阳方向大气剖面" : "诊断与时间窗").font(.system(size: 13, weight: .semibold))
            if let report {
                Text(report["summary"] as? String ?? "").textSelection(.enabled)
                Text("周期 \(report["cycle_time_utc"] as? String ?? "--")").foregroundStyle(.secondary)
            } else {
                Text(waiting).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 120)
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }
}
