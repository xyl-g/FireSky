import AppKit
import Charts
import SwiftUI

private let warm = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 1, green: 0.55, blue: 0.38, alpha: 1)
        : NSColor(calibratedRed: 0.74, green: 0.32, blue: 0.24, alpha: 1)
})
private let teal = Color(red: 0.26, green: 0.56, blue: 0.54)
// Trend ratio series colors, following the Web chart tokens in frontend/src/app.css
// (--chart-coverage / --chart-illumination / --chart-transmission, light & dark pairs).
private let coverageColor = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 0.20, green: 0.83, blue: 0.60, alpha: 1)
        : NSColor(calibratedRed: 0.02, green: 0.40, blue: 0.29, alpha: 1)
})
private let illuminationColor = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 0.98, green: 0.75, blue: 0.14, alpha: 1)
        : NSColor(calibratedRed: 0.56, green: 0.33, blue: 0.02, alpha: 1)
})
private let transmissionColor = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 0.22, green: 0.74, blue: 0.97, alpha: 1)
        : NSColor(calibratedRed: 0.04, green: 0.36, blue: 0.56, alpha: 1)
})

struct WorkbenchRootView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(spacing: 0) {
                topbar
                Group {
                    if store.page == .data { InventoryView() }
                    else if store.page == .settings { SettingsView() }
                    else if store.page == .regional { WorkRegionPage() }
                    else { workbench }
                }
            }
        }
        .font(.system(size: 12))
        .environment(\.timeZone, TimeZone(identifier: store.timezone) ?? .current)
        .background {
            LinearGradient(
                colors: scheme == .dark
                    ? [Color(red: 0.07, green: 0.10, blue: 0.11), Color(red: 0.13, green: 0.12, blue: 0.14), Color(red: 0.08, green: 0.13, blue: 0.12)]
                    : [Color(red: 0.89, green: 0.93, blue: 0.94), Color(red: 0.96, green: 0.92, blue: 0.90), Color(red: 0.89, green: 0.94, blue: 0.92)],
                startPoint: .topLeading, endPoint: .bottomTrailing)
        }
        .tint(warm)
        .frame(minWidth: 1120, minHeight: 720)
        .preferredColorScheme(store.appearance == "dark" ? .dark : store.appearance == "light" ? .light : nil)
        .sheet(isPresented: $store.showPreflight, onDismiss: {
            if store.checking || store.session != nil && !store.busy { store.closePreflight() }
        }) { PreflightView().tint(warm).interactiveDismissDisabled(store.busy) }
        .sheet(isPresented: $store.showMapPicker) {
            VStack(spacing: 0) {
                HStack {
                    Text("地图选点").font(.headline)
                    Spacer()
                    Button("完成") { store.showMapPicker = false }.keyboardShortcut(.defaultAction)
                }.padding(16)
                ForecastMap(store: store)
            }.frame(width: 900, height: 620)
        }
        .alert("需要配置 CAMS Key", isPresented: $store.showCAMSReminder) {
            Button("查看教程并配置") { store.openCAMSSetup() }
            Button("稍后", role: .cancel) {}
        } message: {
            Text("\(store.camsConfigurationDescription)\n已有本地数据仍可使用。")
        }
        .task { await store.connect() }
        .onChange(of: store.page) { _, page in
            if page == .data {
                Task {
                    await store.loadInventory()
                    store.loadTaskHistory()
                }
            }
            if page == .settings { Task { await store.refreshCAMSConfiguration() } }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 11) {
                FireSkyMark().frame(width: 36, height: 36)
                Text("FireSky").font(.system(size: 17, weight: .semibold))
            }.padding(.leading, 6).padding(.bottom, 30)
            Text("观测工作台").font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary).padding(.bottom, 9)
            ForEach([Page.point, .regional, .nowcast, .data]) { page in
                Button {
                    if !store.busy { store.page = page }
                } label: {
                    HStack(spacing: 11) {
                        Image(systemName: page.symbol).symbolRenderingMode(.hierarchical)
                            .font(.system(size: 17, weight: .regular)).frame(width: 22, height: 22)
                        Text(page.title).font(.system(size: 13, weight: store.page == page ? .semibold : .regular))
                        Spacer(minLength: 0)
                        if page == .data && store.downloadProbeDate == nil {
                            Circle().fill(store.probingDownloads ? Color.orange : Color.red)
                                .frame(width: 7, height: 7).help("尚未完成 GFS / EC / CAMS 下载探测")
                        }
                    }
                    .padding(.horizontal, 12).frame(height: 46)
                    .foregroundStyle(store.page == page ? warm : Color.primary.opacity(0.72))
                    .background(store.page == page ? warm.opacity(0.12) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
                }.buttonStyle(.plain).padding(.bottom, 3)
                    .accessibilityAddTraits(store.page == page ? .isSelected : [])
            }
            Spacer(minLength: 20)
            Button("设置", systemImage: "gearshape") {
                if !store.busy { store.page = .settings }
            }.buttonStyle(.plain).frame(maxWidth: .infinity).padding(.vertical, 18)
        }
        .padding(.horizontal, 15).padding(.top, 24).padding(.bottom, 16)
        .frame(width: 196)
        .modifier(SidebarGlass())
    }

    private var topbar: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(store.page.title).font(.system(size: 22, weight: .bold))
                Text(store.page == .data ? "本地原始数据 · 周期与时效 · 按需下载"
                     : store.page == .regional ? store.regionalTitle
                     : String(format: "%@ · %.4f° N, %.4f° E", store.name, store.lat, store.lon))
                    .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Text(Date.now.formatted(.dateTime.month(.twoDigits).day(.twoDigits).weekday(.abbreviated)))
                .font(.system(size: 9)).foregroundStyle(.secondary).workChip()
        }.padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 10).frame(height: 72)
    }

    private var workbench: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(spacing: 10) {
                ScrollView {
                    VStack(spacing: 14) {
                        WorkLocationView()
                        WorkParametersView()
                    }.padding(.trailing, 2)
                }.defaultScrollAnchor(.top).scrollIndicators(.hidden)
                Button { store.beginCheck() } label: {
                    Text(store.page == .nowcast ? "开始实况分析" : "开始预测")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(scheme == .dark ? Color.black.opacity(0.86) : Color.white)
                        .frame(maxWidth: .infinity).frame(height: 41)
                        .background(warm, in: RoundedRectangle(cornerRadius: 8))
                }.buttonStyle(.plain)
                    .opacity(store.busy || !store.online ? 0.45 : 1)
                    .disabled(store.busy || !store.online)
                if store.page == .nowcast {
                    Button("预热卫星与数据", systemImage: "arrow.down.to.line") { store.beginCheck() }
                        .buttonStyle(.borderless).font(.system(size: 10))
                        .disabled(store.busy || !store.online)
                }
                if store.busy {
                    ProgressView(value: store.percent, total: 100)
                    HStack {
                        Text(store.status).font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(2)
                        Spacer()
                        Button { store.cancel() } label: { Image(systemName: "xmark") }.help("取消任务")
                    }
                }
                if !store.online && !store.error.isEmpty {
                    Label("引擎未连接", systemImage: "exclamationmark.triangle")
                        .font(.system(size: 10)).foregroundStyle(.red)
                    Text(store.error)
                        .font(.system(size: 9))
                        .foregroundStyle(.red.opacity(0.9))
                        .lineLimit(3)
                        .textSelection(.enabled)
                    Button("重新连接") { Task { await store.connect() } }.buttonStyle(.borderless)
                }
            }.frame(width: 292)
            VStack(spacing: 12) {
                if store.page == .nowcast {
                    WorkNowcastHeroView()
                    WorkNowcastResultsView()
                } else {
                    WorkHeroView()
                    WorkResultsView()
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }.padding(.leading, 18).padding(.trailing, 20).padding(.top, 14).padding(.bottom, 18)
    }
}

struct WorkLocationView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(store.page == .regional ? "预测区域" : "观测点").font(.system(size: 13, weight: .semibold))
                Spacer()
                if store.page != .regional {
                    WorkSegments(options: ["地图", "收藏"], selection: $store.locationTab).frame(width: 128)
                }
            }
            HStack(spacing: 6) {
                TextField("搜索地名，如「西湖」或「杭州」", text: $store.query).onSubmit { store.search() }
                    .textFieldStyle(.roundedBorder).controlSize(.large)
                Button { store.search() } label: { Image(systemName: "magnifyingglass") }
                    .help("搜索位置").controlSize(.large)
                Button { store.locate() } label: {
                    if store.locating { ProgressView().controlSize(.mini).frame(width: 16, height: 16) }
                    else { Image(systemName: "location").frame(width: 16, height: 16) }
                }.help("获取当前位置").accessibilityLabel("获取当前位置")
                    .controlSize(.large).disabled(store.locating || store.busy)
            }
            if store.page == .regional {
                VStack(spacing: 8) {
                    coordinate("西界", $store.west)
                    coordinate("东界", $store.east)
                    coordinate("南界", $store.south)
                    coordinate("北界", $store.north)
                    Text("0.25° × 0.25°").foregroundStyle(.secondary).frame(maxWidth: .infinity)
                }
            } else if store.locationTab == "地图" {
                ForecastMap(store: store, mini: true).frame(height: 176)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            } else {
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(Array(store.favorites.enumerated()), id: \.offset) { _, item in
                            Button(item["name"] as? String ?? "收藏位置") { store.applyLocation(item) }
                                .buttonStyle(.borderless).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }.padding(8)
                }.frame(height: 176)
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
            }.buttonStyle(.borderless).font(.system(size: 10))
        }.padding(15).workSurface().disabled(store.busy)
    }
    private func coordinate(_ title: String, _ value: Binding<Double>) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            TextField(title, value: value, format: .number.precision(.fractionLength(2)))
                .labelsHidden().frame(width: 110).textFieldStyle(.roundedBorder)
        }
    }
}

struct WorkParametersView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("观测参数").font(.system(size: 13, weight: .semibold))
                Spacer()
                Button("重置") { store.chooseDate("今天"); store.phenomenon = "dusk" }
                    .buttonStyle(.borderless).font(.system(size: 10))
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
                .buttonStyle(.borderless).font(.system(size: 10)).frame(maxWidth: .infinity)
            if store.editingCoordinates {
                VStack(spacing: 8) {
                    TextField("名称", text: $store.name)
                    number("纬度", $store.lat)
                    number("经度", $store.lon)
                    number("海拔 (m)", $store.elevation)
                    HStack {
                        Text("时区").foregroundStyle(.secondary)
                        Spacer()
                        Text(store.timezone).monospaced()
                    }
                    Text("时区按经纬度自动计算").font(.system(size: 9)).foregroundStyle(.secondary)
                    Button("获取海拔与时区") { store.lookupMetadata() }.buttonStyle(.borderless)
                    Toggle("已确认位置数据", isOn: $store.metadataReady).font(.system(size: 10))
                }.textFieldStyle(.roundedBorder)
            }
            if !store.error.isEmpty {
                Text(store.error).font(.system(size: 10)).foregroundStyle(.red).textSelection(.enabled)
            }
        }.padding(15).workSurface().disabled(store.busy)
    }
    private func number(_ title: String, _ value: Binding<Double>) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            TextField(title, value: value, format: .number).labelsHidden().frame(width: 130)
        }
    }
}

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
        }.buttonStyle(.plain).help("选择目标日期")
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
                }.help("上个月")
                Button { store.calendarMonth = month.moved(1) } label: {
                    Image(systemName: "chevron.right").frame(width: 24, height: 24)
                }.help("下个月")
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
                }.buttonStyle(.plain).help("关闭日历").keyboardShortcut(.cancelAction)
            }.font(.system(size: 11))
        }.padding(14).frame(width: 262)
            .tint(warm).background(.regularMaterial)
    }
    private func select(_ day: Date) {
        store.date = day
        store.showDatePicker = false
    }
}

struct WorkHeroView: View {
    @EnvironmentObject private var store: Store
    private var comparison: ForecastComparison { store.pointComparison }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(store.name) · \(store.targetDateLabel) \(store.phenomenon == "dawn" ? "朝霞" : "晚霞")")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 18) {
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
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Text(format(quality, digits: 2)).font(.system(size: 32, weight: .semibold))
                            .monospacedDigit().foregroundStyle(quality == nil ? Color.secondary : modelColor(model))
                            .lineLimit(1).minimumScaleFactor(0.8)
                        Text("/ 2.5").font(.system(size: 10)).foregroundStyle(.secondary)
                    }
                    Text(report?["peak_quality_level"] as? String ?? report?["quality_level"] as? String ?? "峰值质量指数")
                        .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 5) {
                    Text("最佳时刻").font(.system(size: 9)).foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .font(.system(size: 22, weight: .semibold)).monospacedDigit()
                }
            }
            Text("推荐窗口 \(windowLabel(report))").font(.system(size: 10)).foregroundStyle(.secondary)
            HStack(spacing: 5) {
                if let cycle = cycleLabel(model, report) {
                    Text("起报 \(cycle)").workChip()
                }
                Text(familyLabel(report?["dominant_burnable_pressure_family"])).workChip()
            }.font(.system(size: 9)).foregroundStyle(.secondary)
            Text(modelSourceNote(model))
                .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            if let message = comparison.error(model) {
                Text(message).font(.system(size: 10)).foregroundStyle(.red).lineLimit(2)
                    .help(message).textSelection(.enabled)
            }
        }.frame(maxWidth: .infinity, minHeight: 124, alignment: .topLeading)
            .accessibilityElement(children: .contain).accessibilityLabel(model.uppercased())
    }
}

private let metricSpec: [(String, String, String)] = [
    ("burnable", "有效可烧云量", "前向可烧云占视野比例"),
    ("material", "云质厚度", "云体材质负载"),
    ("brightness", "亮度色彩", "霞光亮度与色彩评分"),
    ("cloud_layer", "主要云层", "云底–云顶 AGL"),
    ("aod", "AOD 550", "气溶胶光学厚度 · 通透度"),
    ("incoming", "入射受光", "太阳入射照亮云体的因子"),
    ("contrast", "返程对比度", "观测者视线大气对比因子"),
    ("duration", "持续时长", "核心观赏窗口 · 分钟"),
    ("confidence", "输入支持度", "输入覆盖与几何支持 · 非命中概率"),
    ("convective", "对流风险", "模式对流风险指标 · 非事件概率"),
]

struct WorkResultsView: View {
    @EnvironmentObject private var store: Store
    private var comparison: ForecastComparison { store.pointComparison }
    private var report: JSONObject? { comparison.report(store.selectedModel) }
    private let groups = [("云况与色彩", 0..<4), ("大气光学", 4..<7), ("时效与可靠性", 7..<10)]
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
                                HStack(alignment: .top, spacing: 10) {
                                    ForEach(Array(metricSpec[range]), id: \.0) { spec in
                                        VStack(alignment: .leading, spacing: 7) {
                                            Text(spec.1).font(.system(size: 12)).foregroundStyle(.primary.opacity(0.8))
                                            if spec.0 == "aod" {
                                                WorkAODValue(summary: comparison.aod)
                                            } else if spec.0 == "cloud_layer" {
                                                VStack(alignment: .leading, spacing: 4) {
                                                    ForEach(ForecastComparison.models, id: \.self) { model in
                                                        HStack(spacing: 5) {
                                                            Text(model.uppercased()).font(.system(size: 9, weight: .medium))
                                                                .foregroundStyle(modelColor(model)).frame(width: 25, alignment: .leading)
                                                            Text(metricValue(spec.0, comparison.report(model)))
                                                                .font(.system(size: 14, weight: .semibold)).monospacedDigit()
                                                                .lineLimit(1).minimumScaleFactor(0.65)
                                                        }
                                                    }
                                                }.frame(maxWidth: .infinity, alignment: .leading)
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
                            }
                        }
                        WorkQualityBreakdownView(comparison: comparison)
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
                }.padding(.trailing, 2)
            }.defaultScrollAnchor(.top).scrollIndicators(.hidden)
        }
    }
}

private struct WorkAnalysisTabs: View {
    @EnvironmentObject private var store: Store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var tabIndicator
    var body: some View {
        HStack(spacing: 20) {
            Spacer(minLength: 0)
            ForEach(["概览", "大气剖面", "诊断与时间窗", "逐时台账"], id: \.self) { tab in
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { store.resultTab = tab }
                } label: {
                    Text(tab).font(.system(size: 13, weight: store.resultTab == tab ? .semibold : .regular))
                        .foregroundStyle(store.resultTab == tab ? warm : Color.secondary)
                        .padding(.horizontal, 4).frame(height: 44)
                        .overlay(alignment: .bottom) {
                            if store.resultTab == tab {
                                Rectangle().fill(warm).frame(height: 3)
                                    .matchedGeometryEffect(id: "active-tab", in: tabIndicator)
                            }
                        }
                }.buttonStyle(.plain).accessibilityAddTraits(store.resultTab == tab ? .isSelected : [])
            }
            Spacer(minLength: 0)
        }
    }
}

private struct WorkAODValue: View {
    let summary: AODSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(summary.source).font(.system(size: 9, weight: .medium)).foregroundStyle(teal)
            Text(summary.value).font(.system(size: 19, weight: .semibold)).monospacedDigit()
                .foregroundStyle(summary.available ? Color.primary : Color.secondary)
                .lineLimit(1).minimumScaleFactor(0.8)
        }.frame(maxWidth: .infinity, alignment: .leading).help(summary.detail)
    }
}

/// Read-only score composition panel (plan item point-3). Every value is read from the
/// backend report (quality_components / report fields); nothing is recomputed in SwiftUI.
private struct WorkQualityBreakdownView: View {
    let comparison: ForecastComparison

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("评分组成").font(.system(size: 13, weight: .semibold))
                Text("只读展示后端计算值 · 不在客户端重算")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
            }
            ForEach(ForecastComparison.models, id: \.self) { model in
                if let report = comparison.report(model) {
                    breakdown(model: model, report: report)
                } else if let message = comparison.error(model) {
                    Text("\(model.uppercased()) 未完成：\(message)")
                        .font(.system(size: 10)).foregroundStyle(.red).lineLimit(2)
                } else {
                    Text("\(model.uppercased()) 暂无结果")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }

    private func breakdown(model: String, report: JSONObject) -> some View {
        let components = report["quality_components"] as? JSONObject ?? [:]
        let modelSource = report["model_source"] as? String ?? model
        return VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).font(.system(size: 11, weight: .semibold))
                Text("\(format(report["quality_index"], digits: 3)) / 2.5")
                    .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                    .foregroundStyle(modelColor(model))
                Text(report["quality_level"] as? String ?? "")
                    .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                if let limiting = components["quality_limiting_factor"] as? String, !limiting.isEmpty {
                    Text("当前限制因素 \(limiting)")
                        .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1).help(limiting)
                }
            }
            HStack(alignment: .top, spacing: 14) {
                VStack(spacing: 0) {
                    adjustmentRow("云量基础分",
                                  "有效可烧 \(ratioText(components["burnable_cloud_coverage_effective"])) · 可烧载荷 \(format(components["forward_burnable_cloud_load_effective"] ?? components["forward_burnable_cloud_load_peak"], digits: 2)) · 前方总云场 \(ratioText(components["forward_view_cloud_field_amount_peak"]))",
                                  signedDelta(components["coverage_base_score"] ?? components["coverage_score"]))
                    Divider().opacity(0.5)
                    adjustmentRow("可烧云比例",
                                  "可烧/总云量 \(ratioText(components["forward_burnable_cloud_ratio_peak"])) · 前方可烧云量 \(ratioText(components["forward_burnable_cloud_field_amount_peak"]))",
                                  nil)
                    Divider().opacity(0.5)
                    adjustmentRow("几何遮挡（已计入可烧量）",
                                  "最佳云入射遮挡 \(ratioText(components["solar_incoming_block_risk_at_best_cloud"] ?? components["solar_incoming_block_risk_mean"])) · 不重复扣分",
                                  nil)
                    Divider().opacity(0.5)
                    adjustmentRow("持续时间修正",
                                  "\(format(report["duration_minutes"] ?? components["duration_minutes"], digits: 0)) 分钟 · ×\(format(components["duration_factor"], digits: 3))",
                                  signedDelta(components["duration_adjustment"]))
                    Divider().opacity(0.5)
                    adjustmentRow("入射 AOD 修正",
                                  "本地 AOD \(format(components["quality_aod550"], digits: 3)) · 入射因子 ×\(format(components["incoming_aod_illumination_factor"], digits: 3))",
                                  signedDelta(components["incoming_aod_adjustment"]))
                    Divider().opacity(0.5)
                    adjustmentRow("观测 AOD 修正",
                                  "观测对比因子 ×\(format(components["observer_aod_contrast_factor"], digits: 3))",
                                  signedDelta(components["observer_aod_adjustment"]))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .leading, spacing: 6) {
                    capRow("修正后原始分", format(components["raw_quality_index"], digits: 3))
                    capRow("云量基础分", format(components["coverage_base_score"] ?? components["coverage_score"], digits: 3))
                    capRow("分析范围", valueSuffix(components["forward_field_radius_km"] ?? report["analysis_radius_km"], " km", digits: 0))
                    capRow("太阳方向扇区", valueSuffix(components["forward_sector_half_angle_deg"], "°", digits: 0, sign: "±"))
                    capRow("总云场来源", modelSource == "ec" ? "EC TCC + PL-RH 云柱代理" : "GFS 全量云场")
                    capRow("可烧量来源", text(components["quality_coverage_source"]) ?? "前方全量云场")
                    capRow("持续时间修正系数", "×\(format(components["duration_factor"], digits: 3))")
                    capRow("入射 / 观测回程因子",
                           "×\(format(components["incoming_aod_illumination_factor"], digits: 3)) / ×\(format(components["observer_aod_contrast_factor"], digits: 3))")
                    capRow("观测回程光学量",
                           "τ \(format(components["observer_return_optical_depth_mean"], digits: 3)) · T \(format(components["observer_return_transmission_mean"], digits: 3))")
                }.frame(width: 208, alignment: .leading)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("几何遮挡已包含在可烧云量中，不再重复扣分；持续时间和 AOD 各修正一次。低 AOD 小幅加分，高 AOD 有符号扣分。")
                Text("可烧量只包含既受太阳入射照亮、又能被地面观测者看到的云底面积；同一视线按距离由近到远合成，先遇到的低云会遮住其后的中高云。")
                Text("摄影焦段、Top-K、代表云和云边界不进入主评分。")
            }.font(.system(size: 9)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 4)
    }

    private func adjustmentRow(_ title: String, _ measured: String, _ delta: String?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(title).font(.system(size: 10)).foregroundStyle(.secondary)
                .frame(width: 150, alignment: .leading)
            Text(measured).font(.system(size: 10)).monospacedDigit()
                .frame(maxWidth: .infinity, alignment: .leading).lineLimit(2)
            Text(delta ?? "—")
                .font(.system(size: 11, weight: .medium)).monospacedDigit()
                .foregroundStyle(delta.map { $0.hasPrefix("-") ? Color.red : Color.green } ?? Color.secondary)
                .frame(width: 56, alignment: .trailing)
                .accessibilityLabel(delta == nil ? "\(title)，无独立修正量" : "\(title)，修正量 \(delta!)")
        }.padding(.vertical, 6)
    }

    private func capRow(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.system(size: 9)).foregroundStyle(.secondary)
            Spacer(minLength: 6)
            Text(value).font(.system(size: 9, weight: .medium)).monospacedDigit()
                .multilineTextAlignment(.trailing).lineLimit(2)
        }
    }

    private func signedDelta(_ value: Any?) -> String? {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
        if abs(number.doubleValue) < 0.0005 { return "0.000" }
        return String(format: "%+.3f", number.doubleValue)
    }

    private func valueSuffix(_ value: Any?, _ suffix: String, digits: Int, sign: String = "") -> String {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
        return "\(sign)\(String(format: "%.\(digits)f", number.doubleValue))\(suffix)"
    }

    private func text(_ value: Any?) -> String? {
        guard let value = value as? String, !value.isEmpty else { return nil }
        return value
    }
}
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
            .frame(height: 164)
            .overlay {
                if points.isEmpty {
                    Text(comparison.result == nil ? "等待预测" : "暂无质量曲线")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }.padding(16).workSurface()
    }
}

struct WorkCurveView: View {
    let report: JSONObject?
    let timezone: String
    var body: some View {
        let points = ForecastComparison.curve(report: report, model: "nowcast", timezone: timezone)
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("霞光质量变化").font(.system(size: 13, weight: .semibold))
                Spacer()
                Label("质量指数", systemImage: "circle.fill").font(.system(size: 9)).foregroundStyle(warm).workChip()
            }
            Chart(points) { point in
                LineMark(x: .value("时间", point.time), y: .value("质量指数", point.quality),
                         series: .value("曲线", point.series))
                    .foregroundStyle(warm).lineStyle(StrokeStyle(lineWidth: 2))
                PointMark(x: .value("时间", point.time), y: .value("质量指数", point.quality))
                    .foregroundStyle(warm).symbolSize(8)
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
            .frame(height: 164)
            .overlay {
                if points.isEmpty {
                    Text(report == nil ? "等待实况分析" : "暂无质量曲线")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }.padding(16).workSurface()
    }
}

struct WorkLedgerView: View {
    let report: JSONObject?
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(spacing: 0) {
                header
                Divider()
                ForEach(Array((report?["steps"] as? [JSONObject] ?? []).enumerated()), id: \.offset) { _, item in
                    row(item)
                    Divider().opacity(0.5)
                }
                if report == nil { Text("等待预测").foregroundStyle(.secondary).padding(40) }
            }
            .frame(minWidth: 760, alignment: .leading)
        }
        .workSurface()
    }

    private var header: some View {
        HStack(spacing: 0) {
            Text("时间").frame(width: 78, alignment: .leading)
            Text("质量").frame(width: 68, alignment: .leading)
            Text("档位").frame(width: 92, alignment: .leading)
            Text("可烧").frame(width: 52, alignment: .leading)
            Text("可烧云量").frame(width: 84, alignment: .leading)
            Text("云层 AGL").frame(width: 112, alignment: .leading)
            Text("AOD 550").frame(width: 72, alignment: .leading)
            Text("太阳仰角").frame(width: 78, alignment: .leading)
            Text("场景").frame(width: 138, alignment: .leading)
        }
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 14)
    }

    private func row(_ item: JSONObject) -> some View {
        let valid = item["valid"] as? Bool != false
        let burnable = item["physically_burnable"] as? Bool
        let base = item["dominant_burnable_cloud_base_agl_mean_m"]
        let top = item["dominant_burnable_cloud_top_agl_mean_m"]
        return HStack(spacing: 0) {
            Text(timeLabel(item["valid_time_local"] as? String))
                .frame(width: 78, alignment: .leading)
            Text(valid ? format(item["instant_quality_index"], digits: 3) : "—")
                .frame(width: 68, alignment: .leading)
            Text(valid ? item["instant_quality_level"] as? String ?? "—" : "缺测")
                .frame(width: 92, alignment: .leading)
            Image(systemName: !valid || burnable == nil ? "minus"
                  : burnable == true ? "checkmark" : "xmark")
                .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? teal : Color.orange)
                .frame(width: 52, alignment: .leading)
                .accessibilityLabel(!valid ? "缺测" : burnable == true ? "可烧" : burnable == false ? "不可烧" : "未提供")
            Text(valid ? percent(item["burnable_cloud_coverage"]) : "—")
                .frame(width: 84, alignment: .leading)
            Text(valid ? cloudLayerLabel(base: base, top: top) : "—")
                .frame(width: 112, alignment: .leading)
            Text(valid ? format(item["quality_aod550"], digits: 2) : "—")
                .frame(width: 72, alignment: .leading)
            Text(valid ? format(item["solar_elevation_deg"], digits: 1) : "—")
                .frame(width: 78, alignment: .leading)
            Text(valid ? sceneLabel(item["selected_regime"]) : "—")
                .frame(width: 138, alignment: .leading)
        }
        .font(.system(size: 11))
        .monospacedDigit()
        .padding(.vertical, 12)
        .padding(.horizontal, 14)
    }

    private func cloudLayerLabel(base: Any?, top: Any?) -> String {
        guard let base = base as? NSNumber, let top = top as? NSNumber else { return "—" }
        return "\(Int(base.doubleValue.rounded()))–\(Int(top.doubleValue.rounded())) m"
    }
}

struct WorkDiagnosticView: View {
    let report: JSONObject?
    let profile: Bool
    var waiting = "等待预测"
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(profile ? "太阳方向大气剖面" : "诊断与时间窗").font(.system(size: 13, weight: .semibold))
            if let report {
                Text(summaryText(report))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                if profile {
                    WorkCrossSectionView(section: report["cross_section"] as? JSONObject)
                    if let source = report["profile_source"] as? JSONObject,
                       source["available"] as? Bool == true {
                        let model = source["model_source"] as? String ?? "gfs"
                        Button("读取冻结剖面", systemImage: "waveform.path.ecg") {
                            store.queryPointProfile()
                        }
                        .disabled(store.busy)
                        .help("从已发布结果的冻结源读取太阳方向剖面")
                        FrozenProfileView(model: model) { store.queryPointProfile() }
                    }
                }
                WorkDiagnosticGroup(title: "运行与时间") {
                    diagnosticRow("预测周期", report["cycle_time_utc"])
                    diagnosticRow("本地锚点", report["anchor_local"])
                    diagnosticRow("时区", report["timezone_name"])
                    diagnosticRow("模式", report["forecast_mode"])
                    diagnosticRow("计算后端", report["compute_backend"])
                    diagnosticRow("分析半径", report["analysis_radius_km"], suffix: " km")
                    diagnosticRow("选中场景", sceneLabel(report["selected_regime"]))
                    diagnosticRow("持续时长", report["duration_minutes"], digits: 0, suffix: " 分钟")
                    diagnosticRow("跨模式分歧", "当前后端未计算，GFS/EC 请并列比较")
                }
                if let cache = report["cache"] as? JSONObject {
                    WorkDiagnosticGroup(title: "缓存状态") {
                        diagnosticRow("状态", cache["status"])
                        diagnosticRow("生成时间（UTC）", cache["generated_at_utc"])
                    }
                }
                WorkDiagnosticGroup(title: "云体几何与可解释性") {
                    diagnosticRow("几何代理", report["cloud_geometry_proxy_source"])
                    diagnosticRow("几何支持样本", report["cloud_geometry_support_count"])
                    diagnosticRow("几何不确定度", report["cloud_geometry_uncertainty_m"], digits: 0, suffix: " m")
                    diagnosticRow("几何支持度", report["cloud_geometry_confidence"], percentValue: true)
                    diagnosticRow("置信度语义", report["confidence_kind"] ?? report["confidence_score_semantics"])
                    diagnosticRow("质量指数语义", report["quality_index_semantics"])
                    diagnosticRow("主压力族", report["dominant_burnable_pressure_family"])
                    diagnosticRow("云相代理", report["cloud_phase_proxy"])
                    diagnosticRow("云属代理", report["cloud_genus_proxy"])
                }
                if let scores = report["scene_scores"] as? JSONObject, !scores.isEmpty {
                    WorkDiagnosticGroup(title: "场景分数") {
                        ForEach(scores.keys.sorted(), id: \.self) { key in
                            diagnosticRow(key, scores[key], percentValue: true)
                        }
                    }
                }
                if report["nowcast"] as? JSONObject == nil {
                    WorkSharedDiagnosis(report: report)
                }
                if let aerosol = report["aerosol"] as? JSONObject {
                    WorkDiagnosticGroup(title: "气溶胶来源") {
                        diagnosticRow("AOD 550", aerosol["aod550"], digits: 3)
                        diagnosticRow("来源", aerosol["source"])
                        diagnosticRow("数据质量", aerosol["quality"])
                    }
                }
                if let source = report["profile_source"] as? JSONObject {
                    WorkDiagnosticGroup(title: "剖面能力") {
                        let available = source["available"] as? Bool == true
                        diagnosticRow("冻结剖面", available ? "可查询" : "不可用")
                        if !available { diagnosticRow("原因", source["reason"]) }
                        if let expires = source["expires_at_utc"] as? String {
                            diagnosticRow("冻结源有效期", expires)
                        }
                    }
                }
                if let provenance = report["data_provenance"] as? JSONObject {
                    WorkDiagnosticGroup(title: "数据来源") {
                        ForEach(provenance.keys.sorted(), id: \.self) { key in
                            diagnosticRow(key, provenance[key])
                        }
                    }
                }
                HStack(spacing: 10) {
                    if let path = report["html_report_path"] as? String, !path.isEmpty {
                        Button("完整报告", systemImage: "doc.text") {
                            let url = URL(fileURLWithPath: path, relativeTo: URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
                            NSWorkspace.shared.open(url)
                        }
                    }
                    if let map = report["map_capability"] as? JSONObject,
                       map["available"] as? Bool == false,
                       let reason = map["reason"] as? String {
                        Label(reason, systemImage: "map")
                            .font(.system(size: 10)).foregroundStyle(.secondary)
                            .lineLimit(2).help(reason)
                    }
                }
            } else {
                Text(waiting).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 160)
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }
}

private struct CrossSectionPoint: Identifiable {
    let id: String
    let distance: Double
    let height: Double
    let kind: String
}

private struct WorkCrossSectionView: View {
    let section: JSONObject?

    private var points: [CrossSectionPoint] {
        let targets = section?["theoretical_targets"] as? [JSONObject] ?? []
        var values: [CrossSectionPoint] = []
        for (index, target) in targets.enumerated() {
            guard let distance = number(target["distance_km"]) else { continue }
            if let base = number(target["cloud_base_agl_m"]) {
                values.append(CrossSectionPoint(id: "\(index)-base", distance: distance, height: base / 1000, kind: "云底"))
            }
            if let top = number(target["cloud_top_agl_m"]) {
                values.append(CrossSectionPoint(id: "\(index)-top", distance: distance, height: top / 1000, kind: "云顶"))
            }
        }
        let distances = (section?["distances_km"] as? [Any])?.compactMap(number) ?? []
        let required = (section?["required_illumination_base_agl_m"] as? [Any])?.compactMap(number) ?? []
        for (index, pair) in zip(distances.indices, zip(distances, required)) {
            values.append(CrossSectionPoint(id: "\(index)-required", distance: pair.0, height: pair.1 / 1000, kind: "理论照明基准"))
        }
        return values
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("太阳方向剖面").font(.system(size: 12, weight: .semibold))
                Spacer()
                Text(section == nil ? "暂无冻结剖面" : "沿光路高度")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
            }
            if let section {
                HStack(spacing: 12) {
                    diagnosticChip("太阳高度", section["solar_elevation_deg"], suffix: "°")
                    diagnosticChip("太阳方位", section["solar_azimuth_deg"], suffix: "°")
                    diagnosticChip("采样", section["spatial_sampling"])
                }
                if points.isEmpty {
                    Text("后端未返回可绘制的云层目标；可在完整报告中查看原始剖面。")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                } else {
                    Chart(points) { point in
                        LineMark(
                            x: .value("距离", point.distance),
                            y: .value("高度", point.height),
                            series: .value("曲线", point.kind)
                        )
                        .foregroundStyle(point.kind == "云底" ? warm : point.kind == "云顶" ? teal : Color.secondary)
                        .lineStyle(StrokeStyle(lineWidth: point.kind == "理论照明基准" ? 1 : 2,
                                               dash: point.kind == "理论照明基准" ? [4, 3] : []))
                        PointMark(
                            x: .value("距离", point.distance),
                            y: .value("高度", point.height)
                        )
                        .foregroundStyle(point.kind == "云底" ? warm : point.kind == "云顶" ? teal : Color.secondary)
                        .symbolSize(point.kind == "理论照明基准" ? 4 : 8)
                    }
                    .chartXAxisLabel("沿光路距离 · km")
                    .chartYAxisLabel("高度 · km")
                    .frame(height: 230)
                    HStack(spacing: 12) {
                        legend("云底", warm)
                        legend("云顶", teal)
                        legend("理论照明基准", .secondary, dashed: true)
                    }
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                }
            } else {
                Text("此结果没有可查询的冻结模式剖面。")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private func diagnosticChip(_ title: String, _ value: Any?, suffix: String = "") -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(diagnosticValue(value, digits: 1, suffix: suffix))
                .font(.system(size: 10, weight: .medium)).monospacedDigit()
        }
    }

    private func legend(_ title: String, _ color: Color, dashed: Bool = false) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(color).frame(width: 14, height: dashed ? 1 : 2)
            Text(title)
        }
    }
}

private struct WorkSharedDiagnosis: View {
    let report: JSONObject

    var body: some View {
        if let evolution = report["cloud_evolution"] as? JSONObject {
            WorkDiagnosticGroup(title: "云体演化") {
                diagnosticRow("趋势", evolution["trend"])
                diagnosticRow("目标分数变化", evolution["target_score_change"], digits: 3)
                diagnosticRow("云隙闭合风险", evolution["cloud_hole_closure_risk"], percentValue: true)
                diagnosticRow("比较起点", evolution["comparison_start_local"])
                diagnosticRow("比较终点", evolution["comparison_end_local"])
            }
        }
        if let color = report["color_diagnosis"] as? JSONObject, !color.isEmpty {
            WorkDiagnosticGroup(title: "色彩诊断") {
                diagnosticRow("色彩类型", color["type"])
                diagnosticRow("诊断支持度", color["confidence"], percentValue: true)
                diagnosticRow("云相", color["cloud_phase"])
                diagnosticRow("诊断来源", color["source"])
            }
        }
        if let roles = report["source_roles"] as? JSONObject {
            WorkDiagnosticGroup(title: "数据角色") {
                diagnosticRow("主要来源", roles["primary"])
                diagnosticRow("辅助来源", roles["auxiliary"])
                if let provenance = report["data_provenance"] as? JSONObject {
                    diagnosticRow("本次使用观测", provenance["observation_used"])
                }
            }
        }
    }
}

private struct WorkDiagnosticGroup<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 6, content: content)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.18), in: RoundedRectangle(cornerRadius: 6))
    }
}

private func diagnosticRow(_ title: String, _ value: Any?, digits: Int = 2,
                          suffix: String = "", percentValue: Bool = false) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 10) {
        Text(title).foregroundStyle(.secondary)
        Spacer(minLength: 10)
        Text(diagnosticValue(value, digits: digits, suffix: suffix, percentValue: percentValue))
            .multilineTextAlignment(.trailing)
            .textSelection(.enabled)
            .lineLimit(2)
    }.font(.system(size: 10))
}

struct WorkRegionPage: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(spacing: 12) {
            controls
            HStack(spacing: 16) {
                summary("gfs")
                Divider().frame(height: 54)
                summary("ec")
            }.padding(.vertical, 6)
            HStack(spacing: 12) {
                regionalMetadata("GFS", store.regionalForecast.model("gfs"))
                Divider().frame(height: 28)
                regionalMetadata("EC", store.regionalForecast.model("ec"))
                Spacer(minLength: 0)
            }
            HStack(spacing: 10) {
                WorkSegments(options: ["gfs", "ec", "compare"], titles: ["GFS", "EC", "双图对照"],
                             selection: $store.regionalDisplay).frame(width: 248)
                Spacer()
                Text(regionalGridLabel).font(.system(size: 10)).foregroundStyle(.secondary)
                Toggle(isOn: $store.showRegionalGrid) {
                    Image(systemName: "square.grid.3x3").accessibilityLabel("显示网格")
                }.toggleStyle(.button).help("显示网格")
                Button { store.regionalFitVersion += 1 } label: { Image(systemName: "arrow.up.left.and.arrow.down.right") }
                    .help("适应预测范围").accessibilityLabel("适应预测范围")
                Button { store.showRegionalInspector.toggle() } label: { Image(systemName: "sidebar.right") }
                    .help("网格详情与排行").accessibilityLabel("网格详情与排行")
            }.buttonStyle(.borderless)
            HStack(alignment: .top, spacing: 12) {
                VStack(spacing: 0) {
                    HStack(spacing: 1) {
                        map(store.regionalDisplay == "compare" ? "gfs" : store.regionalDisplay)
                        if store.regionalDisplay == "compare" { map("ec") }
                    }
                    legend
                }.background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                if store.showRegionalInspector { inspector.frame(width: 236) }
            }.frame(maxHeight: .infinity)
            if store.busy {
                HStack {
                    ProgressView(value: store.percent, total: 100).frame(width: 130)
                    Text(store.status).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Button { store.cancel() } label: { Image(systemName: "xmark") }.help("取消任务")
                }
            }
            if !store.error.isEmpty {
                Text(store.error).font(.system(size: 10)).foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading).lineLimit(2)
            }
        }.padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 16)
            .onExitCommand { store.editingRegionOnMap = false }
    }

    private var controls: some View {
        HStack(spacing: 9) {
            Menu {
                Section("预测区域") {
                    ForEach(store.regionDefinitions, id: \.regionCode) { item in
                        let code = item["code"] as? String ?? ""
                        Toggle(item["label"] as? String ?? code, isOn: Binding(
                            get: { store.selectedRegions.contains(code) },
                            set: { if $0 { store.selectedRegions.insert(code) } else { store.selectedRegions.remove(code) } }
                        ))
                    }
                    Toggle("自定义范围", isOn: $store.customRegion)
                }
            } label: {
                Label("预测区域", systemImage: "map")
            }.frame(width: 112)
            Button {
                store.editingRegionOnMap.toggle()
            } label: {
                Label(store.editingRegionOnMap ? "取消绘制" : "绘制范围", systemImage: "rectangle.dashed")
                    .foregroundStyle(store.editingRegionOnMap ? teal : Color.primary)
            }.help("在地图上拖拽绘制范围，Esc 取消")
            Button { store.showRegionBounds.toggle() } label: { Image(systemName: "slider.horizontal.3") }
                .help("编辑区域坐标").accessibilityLabel("编辑区域坐标")
                .popover(isPresented: $store.showRegionBounds) {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("自定义范围").font(.headline)
                        Toggle("启用自定义范围", isOn: $store.customRegion).toggleStyle(.checkbox)
                        HStack { number("西界", $store.west); number("东界", $store.east) }
                        HStack { number("南界", $store.south); number("北界", $store.north) }
                        Text("各网格时区按经纬度自动计算")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        HStack { Spacer(); Button("完成") { store.showRegionBounds = false } }
                    }.padding(18).frame(width: 310).disabled(store.busy)
                }
            Divider().frame(height: 22)
            WorkDateButton().frame(width: 150)
            WorkSegments(options: ["dawn", "dusk"], titles: ["朝霞", "晚霞"], selection: $store.phenomenon)
                .frame(width: 112)
            Spacer(minLength: 4)
            Button { store.beginCheck() } label: {
                Label("开始预测", systemImage: "play.fill")
                    .font(.system(size: 12, weight: .semibold)).padding(.horizontal, 7).frame(height: 25)
            }.buttonStyle(.borderedProminent).disabled(!store.online)
        }.controlSize(.regular).disabled(store.busy)
    }

    private func summary(_ model: String) -> some View {
        let report = store.regionalForecast.model(model)
        return HStack(alignment: .center, spacing: 18) {
            VStack(alignment: .leading, spacing: 5) {
                Text(model.uppercased()).font(.system(size: 13, weight: .bold))
                    .foregroundStyle(model == "gfs" ? warm : teal)
                Text(report.status).font(.system(size: 10)).foregroundStyle(report.error == nil ? Color.secondary : Color.red)
            }.frame(width: 60, alignment: .leading)
            VStack(alignment: .leading, spacing: 4) {
                Text(report.peak?.quality.map { String(format: "%.2f", $0) } ?? "—")
                    .font(.system(size: 25, weight: .semibold)).monospacedDigit()
                Text("区域峰值").font(.system(size: 9)).foregroundStyle(.secondary)
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
        let models = store.regionalDisplay == "compare" ? ["gfs", "ec"] : [store.regionalDisplay]
        let labels = models.compactMap { store.regionalForecast.model($0).precision }
        return labels.isEmpty ? "网格精度 —" : labels.joined(separator: " / ")
    }

    private func map(_ model: String) -> some View {
        VStack(spacing: 0) {
            HStack {
                Text(model.uppercased()).font(.system(size: 11, weight: .bold))
                    .foregroundStyle(model == "gfs" ? warm : teal)
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
                            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6)).padding(10)
                    }
                }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var legend: some View {
        HStack(spacing: 7) {
            Text("质量指数")
            Text("0")
            HStack(spacing: 0) {
                ForEach(0..<RegionalPalette.steps, id: \.self) { index in
                    Color(nsColor: RegionalPalette.color(index))
                }
            }.frame(width: 110, height: 6).clipShape(Capsule())
            Text("2.5")
            Spacer(minLength: 4)
            Rectangle().stroke(.secondary.opacity(0.5), style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                .frame(width: 9, height: 9)
            Text("缺测")
        }.font(.system(size: 9)).foregroundStyle(.secondary).padding(.horizontal, 12).frame(height: 32)
    }

    private var inspector: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let coordinate = store.selectedRegionalCoordinate {
                HStack {
                    Text("网格详情").font(.system(size: 12, weight: .semibold))
                    Spacer()
                    Button { store.selectedRegionalCoordinate = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.borderless).help("关闭网格详情")
                }
                Text(coordinate.label).font(.system(size: 11)).monospacedDigit().foregroundStyle(.secondary)
                ForEach(["gfs", "ec"], id: \.self) { model in
                    let cell = store.regionalForecast.model(model).cell(at: coordinate)
                    HStack(spacing: 6) {
                        Text(model.uppercased()).foregroundStyle(model == "gfs" ? warm : teal).frame(width: 34, alignment: .leading)
                        Text(cell?.quality.map { String(format: "%.2f", $0) } ?? "—").monospacedDigit().bold()
                        Spacer()
                        Text(cell?.quality == nil ? "缺测" : timeLabel(cell?.bestTime)).foregroundStyle(.secondary)
                        if cell?.bestTime != nil {
                            Button {
                                store.queryRegionalProfile(model: model, coordinate: coordinate)
                            } label: {
                                Image(systemName: "waveform.path.ecg")
                            }
                            .buttonStyle(.borderless)
                            .help("读取 \(model.uppercased()) 剖面")
                            .disabled(store.busy)
                        }
                    }.font(.system(size: 11)).help(cell?.error ?? cell?.level ?? "无该模式结果")
                }
                if store.regionalProfileCoordinate == coordinate,
                   !store.regionalProfileModel.isEmpty {
                    RegionalProfileView(model: store.regionalProfileModel, coordinate: coordinate)
                }
                Divider()
            }
            HStack {
                Text(store.regionalInspectorMode == "cities" ? "城市排名" : "高值排行")
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                if store.regionalDisplay == "compare" {
                    WorkSegments(options: ["gfs", "ec"], titles: ["GFS", "EC"], selection: $store.selectedModel).frame(width: 100)
                } else {
                    Text(store.regionalDisplay.uppercased()).font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            WorkSegments(options: ["grid", "cities"], titles: ["网格", "城市"],
                         selection: $store.regionalInspectorMode).frame(width: 124)
            if store.regionalInspectorMode == "cities" {
                TextField("搜索城市", text: $store.regionalQuery).textFieldStyle(.roundedBorder)
                let rows = cityRanking
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(Array(rows.enumerated()), id: \.element.id) { index, cell in
                            Button { store.focusRegionalCell(cell.coordinate) } label: {
                                HStack(spacing: 6) {
                                    Text("\(index + 1)").font(.system(size: 10, weight: .semibold))
                                        .monospacedDigit().foregroundStyle(.secondary).frame(width: 18, alignment: .leading)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(cell.label).font(.system(size: 10, weight: .medium))
                                        Text(timeLabel(cell.bestTime)).font(.system(size: 9)).foregroundStyle(.secondary)
                                    }
                                    Spacer(minLength: 2)
                                    Text(String(format: "%.2f", cell.quality ?? 0))
                                        .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                                }
                                .padding(.horizontal, 7).padding(.vertical, 9).contentShape(Rectangle())
                                .background(store.selectedRegionalCoordinate == cell.coordinate ? teal.opacity(0.12) : Color.clear,
                                            in: RoundedRectangle(cornerRadius: 5))
                            }.buttonStyle(.plain)
                            Divider().opacity(0.35)
                        }
                    }
                    if rows.isEmpty {
                        let model = store.regionalForecast.model(store.selectedModel)
                        Text(model.cityRankingStatus == "unavailable"
                             ? (model.cityRankingReason ?? "该区域暂无城市排名")
                             : model.available ? "无匹配城市" : "等待预测")
                            .font(.system(size: 11)).foregroundStyle(.secondary).padding(.top, 32)
                    }
                }
                Text("前 \(rows.count) 个城市").font(.system(size: 9)).foregroundStyle(.tertiary)
            } else {
                TextField("搜索网格", text: $store.regionalQuery).textFieldStyle(.roundedBorder)
                let rows = ranking
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(rows) { cell in
                            Button { store.focusRegionalCell(cell.coordinate) } label: {
                                HStack(spacing: 6) {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(cell.coordinate.label).font(.system(size: 10)).monospacedDigit()
                                        Text(timeLabel(cell.bestTime)).font(.system(size: 9)).foregroundStyle(.secondary)
                                    }
                                    Spacer(minLength: 2)
                                    Text(String(format: "%.2f", cell.quality ?? 0)).font(.system(size: 12, weight: .semibold))
                                        .monospacedDigit()
                                }
                                .padding(.horizontal, 7).padding(.vertical, 9).contentShape(Rectangle())
                                .background(store.selectedRegionalCoordinate == cell.coordinate ? teal.opacity(0.12) : Color.clear,
                                            in: RoundedRectangle(cornerRadius: 5))
                            }.buttonStyle(.plain)
                            Divider().opacity(0.35)
                        }
                    }
                    if rows.isEmpty {
                        Text(store.regionalForecast.model(store.selectedModel).available ? "无匹配网格" : "等待预测")
                            .font(.system(size: 11)).foregroundStyle(.secondary).padding(.top, 32)
                    }
                }
                Text("前 \(rows.count) 个网格").font(.system(size: 9)).foregroundStyle(.tertiary)
            }
        }.padding(.leading, 2).frame(maxHeight: .infinity)
    }

    private var ranking: [RegionalCell] {
        let query = store.regionalQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        let rows = store.regionalForecast.model(store.selectedModel).ranked
        return Array((query.isEmpty ? rows : rows.filter { $0.label.localizedCaseInsensitiveContains(query) }).prefix(2000))
    }

    private var cityRanking: [RegionalCell] {
        let query = store.regionalQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        let rows = store.regionalForecast.model(store.selectedModel).cityRanking
        return Array((query.isEmpty ? rows : rows.filter { $0.label.localizedCaseInsensitiveContains(query) }).prefix(2000))
    }

    private func number(_ title: String, _ value: Binding<Double>) -> some View {
        HStack(spacing: 6) {
            Text(title).foregroundStyle(.secondary)
            TextField(title, value: value, format: .number).labelsHidden().textFieldStyle(.roundedBorder).frame(width: 75)
        }
    }
}

private struct WorkNowcastHeroView: View {
    @EnvironmentObject private var store: Store
    private var report: JSONObject? { store.resultKind == .nowcast ? store.result : nil }
    private var nowcast: JSONObject { report?["nowcast"] as? JSONObject ?? [:] }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(store.name) · \(store.targetDateLabel) \(store.phenomenon == "dawn" ? "朝霞" : "晚霞")")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 9) {
                    HStack(spacing: 6) {
                        Circle().fill(teal).frame(width: 6, height: 6)
                        Text("卫星实况").font(.system(size: 12, weight: .semibold))
                        Spacer(minLength: 4)
                        Text(report == nil ? "等待分析" : "分析完成")
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
                }.frame(maxWidth: .infinity, minHeight: 124, alignment: .topLeading)
                Divider().frame(height: 124).opacity(0.5)
                VStack(alignment: .leading, spacing: 9) {
                    Text("最佳时刻").font(.system(size: 9)).foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .font(.system(size: 22, weight: .semibold)).monospacedDigit()
                    Text("卫星观测 \(observationTime)").font(.system(size: 10)).foregroundStyle(.secondary)
                    Text("GFS 先验 · CAMS 光学").font(.system(size: 10)).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .topLeading)
            }
        }.padding(18).frame(maxWidth: .infinity).workSurface(emphasized: true)
    }
    private var observationTime: String {
        guard let value = nowcast["image_valid_time_utc"] as? String,
              let date = ISO8601DateFormatter().date(from: value) else { return "—" }
        return ObservationDate.timeLabel(date, timezone: store.timezone)
    }
}

private struct WorkNowcastResultsView: View {
    @EnvironmentObject private var store: Store
    private var report: JSONObject? { store.resultKind == .nowcast ? store.result : nil }
    private var presentation: NowcastPresentation { NowcastPresentation(report: report) }
    private var nowcast: JSONObject { presentation.observation }
    private let groups = [("卫星云况", 0..<3), ("运动与约束", 3..<6), ("采样与光学", 6..<9)]
    var body: some View {
        VStack(spacing: 12) {
            WorkAnalysisTabs()
            ScrollView {
                VStack(spacing: 12) {
                    if store.resultTab == "概览" {
                        ForEach(groups, id: \.0) { title, range in
                            VStack(alignment: .leading, spacing: 7) {
                                Text(title).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                                HStack(alignment: .top, spacing: 10) {
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

    private func metric(_ spec: (String, String, String)) -> some View {
        let aod = presentation.aod
        return VStack(alignment: .leading, spacing: 7) {
            Text(spec.1).font(.system(size: 12)).foregroundStyle(.primary.opacity(0.8))
            if spec.0 == "aod" {
                WorkAODValue(summary: aod)
            } else {
                Text(presentation.value(spec.0))
                    .font(.system(size: spec.0 == "satellite_low_cloud_state" || spec.0 == "satellite_motion_quality" ? 18 : 23,
                                  weight: .semibold)).monospacedDigit()
                    .foregroundStyle(report == nil ? Color.secondary : Color.primary)
                    .lineLimit(2).minimumScaleFactor(0.8)
                    .frame(maxHeight: .infinity, alignment: .leading)
            }
            Text(spec.0 == "aod" ? aod.caption : spec.2)
                .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(2)
        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: 112).workSurface()
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
            if let map = report?["map_capability"] as? JSONObject,
               map["available"] as? Bool == false {
                Label(map["reason"] as? String ?? "实况地图未发布", systemImage: "map")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .lineLimit(2).help(map["reason"] as? String ?? "")
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }
}

private struct WorkSatelliteProfileView: View {
    @EnvironmentObject private var store: Store
    let presentation: NowcastPresentation
    var body: some View {
        let cells = presentation.profileCells
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("卫星冻结剖面").font(.system(size: 13, weight: .semibold))
                Spacer()
                Text("云层后验概率").font(.system(size: 10)).foregroundStyle(.secondary)
            }
            Text(presentation.profileBearingCaption).font(.system(size: 10)).foregroundStyle(.secondary)
            if cells.isEmpty {
                Text(presentation.profileUnavailableReason).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 260)
            } else {
                Chart(cells) { cell in
                    RectangleMark(
                        xStart: .value("距离", cell.startKM), xEnd: .value("距离", cell.endKM),
                        yStart: .value("高度", cell.bottomKM), yEnd: .value("高度", cell.topKM)
                    ).foregroundStyle(cell.probability.map { warm.opacity(0.08 + 0.85 * $0 / 100) } ?? Color.secondary.opacity(0.16))
                }
                .chartXAxisLabel("沿光路距离 · km")
                .chartYAxisLabel("高度 · km")
                .frame(height: 260)
                HStack(spacing: 6) {
                    Rectangle().fill(.secondary.opacity(0.16)).frame(width: 10, height: 10)
                    Text("缺测")
                    Spacer()
                    Text("0%")
                    LinearGradient(colors: [warm.opacity(0.08), warm.opacity(0.93)],
                                   startPoint: .leading, endPoint: .trailing).frame(width: 72, height: 5)
                    Text("100%")
                }.font(.system(size: 9)).foregroundStyle(.secondary)
            }
            frozenQuery
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
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
                            .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? teal : Color.orange)
                            .frame(width: 52, alignment: .leading)
                            .accessibilityLabel(!valid ? "缺测" : burnable == true ? "可烧" : burnable == false ? "不可烧" : "未提供")
                        Text(valid ? percent(step["burnable_cloud_coverage"]) : "—")
                            .frame(width: 84, alignment: .leading)
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
            }
            .frame(minWidth: 538, alignment: .leading)
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

private extension Dictionary where Key == String, Value == Any {
    var regionCode: String { self["code"] as? String ?? "" }
}

private struct WorkSurface: ViewModifier {
    var emphasized = false
    @Environment(\.colorScheme) private var scheme
    func body(content: Content) -> some View {
        content.background {
            RoundedRectangle(cornerRadius: 8)
                .fill(scheme == .dark ? Color.white.opacity(emphasized ? 0.075 : 0.045) : Color.white.opacity(emphasized ? 0.82 : 0.60))
        }.overlay {
            RoundedRectangle(cornerRadius: 8)
                .stroke(scheme == .dark ? Color.white.opacity(emphasized ? 0.16 : 0.09) : Color.white.opacity(0.80), lineWidth: 1)
        }
    }
}

private struct SidebarGlass: ViewModifier {
    @ViewBuilder func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            content.background { Color.clear.glassEffect(.regular, in: .rect(cornerRadius: 0)) }
        } else {
            content.background(.regularMaterial)
        }
    }
}

private struct WorkSegments: View {
    let options: [String]
    var titles: [String]? = nil
    @Binding var selection: String
    var accented = false
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        HStack(spacing: 3) {
            ForEach(Array(options.enumerated()), id: \.element) { index, value in
                let selected = selection == value
                Button { selection = value } label: {
                    Text(titles?[index] ?? value)
                        .font(.system(size: 11, weight: selected ? .semibold : .regular))
                        .foregroundStyle(selected && accented ? warm : selected ? Color.primary : Color.secondary)
                        .frame(maxWidth: .infinity).frame(height: 26)
                        .background {
                            RoundedRectangle(cornerRadius: 6)
                                .fill(selected ? (accented ? warm.opacity(0.16) : scheme == .dark ? Color.white.opacity(0.10) : .white.opacity(0.85)) : .clear)
                        }
                        .contentShape(Rectangle())
                }.buttonStyle(.plain)
            }
        }.padding(3)
            .background(.black.opacity(scheme == .dark ? 0.12 : 0.035), in: RoundedRectangle(cornerRadius: 8))
            .overlay { RoundedRectangle(cornerRadius: 8).stroke(.secondary.opacity(0.15), lineWidth: 1) }
    }
}

extension View {
    fileprivate func workSurface(emphasized: Bool = false) -> some View { modifier(WorkSurface(emphasized: emphasized)) }
    fileprivate func workChip() -> some View {
        padding(.horizontal, 8).padding(.vertical, 5)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))
            .overlay { RoundedRectangle(cornerRadius: 6).stroke(.secondary.opacity(0.16), lineWidth: 1) }
    }
}

private func format(_ value: Any?, digits: Int = 2) -> String {
    guard let number = value as? NSNumber else { return "—" }
    return String(format: "%.*f", digits, number.doubleValue)
}

private func number(_ value: Any?) -> Double? {
    guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
    return number.doubleValue
}

private func diagnosticValue(_ value: Any?, digits: Int = 2, suffix: String = "",
                             percentValue: Bool = false) -> String {
    ReportDiagnosticValue.text(value, digits: digits, suffix: suffix, percent: percentValue) { sceneLabel($0) }
}

private func summaryText(_ report: JSONObject) -> String {
    if let text = report["summary"] as? String, !text.isEmpty { return text }
    if let values = report["summary"] as? [Any] {
        let lines = values.compactMap { value -> String? in
            guard let text = value as? String, !text.isEmpty else { return nil }
            return "• \(text)"
        }
        if !lines.isEmpty { return lines.joined(separator: "\n") }
    }
    return "暂无文字摘要"
}

private func sceneLabel(_ value: Any?) -> String {
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

private func modelColor(_ model: String) -> Color { model == "ec" ? teal : warm }

private func timeLabel(_ value: String?) -> String {
    guard let value, value.count >= 16 else { return "—" }
    return String(value.dropFirst(11).prefix(5))
}

private func windowLabel(_ report: JSONObject?) -> String {
    guard let value = report?["timing_window"] as? JSONObject else { return "--" }
    return "\(timeLabel(value["start_time_local"] as? String)) – \(timeLabel(value["end_time_local"] as? String))"
}

private func metricValue(_ key: String, _ report: JSONObject?) -> String {
    guard let report else { return "--" }
    let components = report["quality_components"] as? JSONObject ?? [:]
    switch key {
    case "aod": return format(components["quality_aod550"] ?? report["quality_aod550"])
    case "cloud_layer": return cloudLayerMetricValue(report)
    case "duration": return format(report["duration_minutes"] ?? components["duration_minutes"], digits: 0)
    case "confidence": return percent(report["confidence_score"])
    case "convective": return format(report["convective_risk"])
    case "burnable": return percent(components["burnable_cloud_coverage_effective"] ?? report["burnable_cloud_coverage_effective"])
    case "material": return format(components["cloud_material"])
    case "brightness": return format(components["brightness_color"])
    case "incoming": return format(components["incoming_aod_illumination_factor"])
    case "contrast": return format(components["observer_aod_contrast_factor"])
    default: return "--"
    }
}

private func cloudLayerMetricValue(_ report: JSONObject?) -> String {
    guard let report,
          let base = number(report["dominant_burnable_cloud_base_agl_mean_m"]),
          let top = number(report["dominant_burnable_cloud_top_agl_mean_m"]),
          top >= base else {
        return "—"
    }
    return String(format: "%.1f–%.1f km", base / 1000, top / 1000)
}

private func percent(_ value: Any?) -> String {
    guard let number = value as? NSNumber else { return "--" }
    return String(format: "%.0f%%", number.doubleValue * 100)
}

private func familyLabel(_ value: Any?) -> String {
    switch value as? String {
    case "high": return "高云压力族"
    case "middle": return "中云压力族"
    case "low": return "低云压力族"
    default: return "无可烧目标云"
    }
}

private func genusLabel(_ value: String) -> String {
    switch value {
    case "cirrus": return "卷云"
    case "cirrocumulus": return "卷积云"
    case "cirrostratus": return "卷层云"
    case "altocumulus": return "高积云"
    case "altostratus": return "高层云"
    case "nimbostratus": return "雨层云"
    case "stratocumulus": return "层积云"
    case "stratus": return "层云"
    case "cumulus": return "积云"
    case "cumulonimbus": return "积雨云"
    default: return value.replacingOccurrences(of: "_", with: " ")
    }
}

private func phaseLabel(_ value: String) -> String {
    switch value {
    case "ice": return "冰相成分"
    case "water": return "液态水成分"
    case "mixed": return "冰水混合成分"
    default: return "相态未知"
    }
}

/// Percent text for 0–1 ratio fields; missing values show the missing mark, never 0%.
private func ratioText(_ value: Any?) -> String {
    guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
    return String(format: "%.0f%%", number.doubleValue * 100)
}

private func ratioSeriesColor(_ kind: String) -> Color {
    switch kind {
    case "coverage": return coverageColor
    case "ratio": return illuminationColor
    default: return transmissionColor
    }
}

private func ratioSeriesLabel(_ kind: String) -> String {
    switch kind {
    case "coverage": return "可烧云占比"
    case "ratio": return "可烧比例"
    default: return "回程透过率"
    }
}

/// Cycle chip text, e.g. "2026-11-12 06:00Z"; model_cycles values are UTC ISO strings.
private func cycleLabel(_ model: String, _ report: JSONObject?) -> String? {
    let cycles = report?["model_cycles"] as? JSONObject
    let value = cycles?[model] as? String ?? report?["cycle_time_utc"] as? String
    guard let value, value.count >= 16 else { return nil }
    let text = value.prefix(16).replacingOccurrences(of: "T", with: " ")
    return value.contains("+00:00") || value.hasSuffix("Z") ? text + "Z" : text
}

/// One-line model source note, mirroring the Web source description.
private func modelSourceNote(_ model: String) -> String {
    model == "ec"
        ? "EC 公共 Open Data · TCC + 压力层 RH 云柱代理 · 不含 GFS 云场 · CAMS 气溶胶"
        : "GFS 0.25° 全量云场评分 · CAMS 气溶胶"
}
