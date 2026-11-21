import AppKit
import Charts
import SwiftUI

// Shared brand accent; used by the workbench and the settings category rail.
let warm = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 1.00, green: 0.61, blue: 0.42, alpha: 1)
        : NSColor(calibratedRed: 0.82, green: 0.35, blue: 0.21, alpha: 1)
})
private let coolAccent = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 0.56, green: 0.72, blue: 1.00, alpha: 1)
        : NSColor(calibratedRed: 0.31, green: 0.46, blue: 0.72, alpha: 1)
})
// Trend ratio series colors: same semantics as the Web chart tokens, with macOS palette calibration.
private let coverageColor = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 0.28, green: 0.84, blue: 0.68, alpha: 1)
        : NSColor(calibratedRed: 0.04, green: 0.44, blue: 0.34, alpha: 1)
})
private let illuminationColor = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 1.00, green: 0.76, blue: 0.45, alpha: 1)
        : NSColor(calibratedRed: 0.62, green: 0.40, blue: 0.12, alpha: 1)
})
private let transmissionColor = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 0.25, green: 0.74, blue: 0.97, alpha: 1)
        : NSColor(calibratedRed: 0.04, green: 0.36, blue: 0.56, alpha: 1)
})

/// 侧栏显示状态。本机工具链无 SwiftUIMacros，使用普通 ObservableObject。
private final class SidebarVisibility: ObservableObject {
    @Published var visible = true
}

struct WorkbenchRootView: View {
    @EnvironmentObject private var store: Store
    @EnvironmentObject private var updater: Updater
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// 本机工具链无 SwiftUIMacros，使用普通 ObservableObject 代替 @State。
    @StateObject private var sidebarState = SidebarVisibility()

    var body: some View {
        HStack(spacing: 0) {
            if sidebarState.visible {
                sidebar
                Divider()
            }
            VStack(spacing: 0) {
                topbar
                Group {
                    if store.page == .data { InventoryView() }
                    else if store.page == .history { NativeTaskHistoryView() }
                    else if store.page == .settings { SettingsView() }
                    else if store.page == .regional { WorkRegionPage() }
                    else { workbench }
                }
            }
        }
        .font(.system(size: 12))
        .environment(\.timeZone, TimeZone(identifier: store.timezone) ?? .current)
        .background {
            ZStack {
                LinearGradient(
                    colors: FSDesign.pageBackground(dark: scheme == .dark),
                    startPoint: .topLeading, endPoint: .bottomTrailing)
                RadialGradient(
                    colors: [FSDesign.ambientGlow(dark: scheme == .dark), Color.clear],
                    center: .topTrailing, startRadius: 120, endRadius: 820)
                RadialGradient(
                    colors: [FSDesign.coolGlow(dark: scheme == .dark), Color.clear],
                    center: .bottomLeading, startRadius: 80, endRadius: 720)
            }
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
        .task {
            // 启动静默检查更新（默认开，可在设置 → 通用关闭）；失败不打扰。
            if updater.autoCheck {
                try? await Task.sleep(for: .seconds(8))
                if !Task.isCancelled, updater.autoCheck {
                    updater.checkForUpdates(interactive: false)
                }
            }
        }
        .onChange(of: store.page) { _, page in
            if page == .data {
                Task {
                    await store.loadInventory()
                    store.loadTerrain()
                }
            }
            if page == .history {
                store.loadTaskHistory()
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
            ForEach([Page.point, .regional, .nowcast, .data, .history]) { page in
                let selected = store.page == page
                Button {
                    // UX #5：切页不再被全局 busy 锁死；运行中的任务继续，顶栏可见进度。
                    store.page = page
                } label: {
                    HStack(spacing: 11) {
                        Image(systemName: page.symbol).symbolRenderingMode(.hierarchical)
                            .font(.system(size: 17, weight: .regular)).frame(width: 22, height: 22)
                        Text(page.title).font(.system(size: 13, weight: selected ? .semibold : .regular))
                        Spacer(minLength: 0)
                        if selected {
                            Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                        }
                    }
                    .padding(.horizontal, 12).frame(height: 44)
                    .foregroundStyle(selected ? Color.primary : Color.primary.opacity(0.76))
                    .background {
                        RoundedRectangle(cornerRadius: 8)
                            .fill(selected ? warm.opacity(0.14) : Color.clear)
                    }
                    .overlay(alignment: .leading) {
                        if selected {
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(warm)
                                .frame(width: 3, height: 22)
                                .padding(.leading, 2)
                        }
                    }
                    .contentShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.bottom, 3)
                .accessibilityLabel(selected ? "\(page.title)，当前页面" : page.title)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
            Spacer(minLength: 20)
            Button {
                store.page = .settings
            } label: {
                HStack(spacing: 11) {
                    Image(systemName: "gearshape")
                        .font(.system(size: 17, weight: .regular)).frame(width: 22, height: 22)
                    Text("设置").font(.system(size: 13, weight: store.page == .settings ? .semibold : .regular))
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 12).frame(height: 44)
                .foregroundStyle(store.page == .settings ? Color.primary : Color.primary.opacity(0.76))
                .background {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(store.page == .settings ? warm.opacity(0.14) : Color.clear)
                }
                .contentShape(RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .padding(.bottom, 8)
            .accessibilityLabel(store.page == .settings ? "设置，当前页面" : "设置")
            .accessibilityAddTraits(store.page == .settings ? .isSelected : [])
            .overlay(alignment: .topTrailing) {
                // 发现新版本时在设置入口显示绿色徽标（更新入口在设置 → 通用）。
                if updater.phase == .available {
                    Circle().fill(Color.green).frame(width: 8, height: 8)
                        .padding(.trailing, 10).padding(.top, 6)
                        .accessibilityLabel("发现新版本 \(updater.info?.version ?? "")")
                }
            }
        }
        .padding(.horizontal, 15).padding(.top, 24).padding(.bottom, 16)
        .frame(width: 196)
        .fireSkySurface(role: .navigation)
    }

    private var topbar: some View {
        HStack(alignment: .center, spacing: 12) {
            Button {
                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) {
                    sidebarState.visible.toggle()
                }
            } label: {
                Image(systemName: "sidebar.left")
                    .font(.system(size: 14, weight: .medium))
                    .frame(width: 24, height: 24)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .help(sidebarState.visible ? "隐藏侧栏" : "显示侧栏")
            .accessibilityLabel(sidebarState.visible ? "隐藏侧栏" : "显示侧栏")

            VStack(alignment: .leading, spacing: 3) {
                Text(store.page.title).font(.system(size: 20, weight: .semibold))
                // 副标题只在该页面有地点语义时显示；管理页不借用预测地点上下文。
                if store.page.showsLocationSubtitle {
                    Text(store.page == .regional ? store.regionalTitle
                         : String(format: "%@ · %.4f° N, %.4f° E", store.name, store.lat, store.lon))
                        .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer(minLength: 8)
            // UX #5：任务运行中切页后，顶栏仍能看到进度与取消入口。
            if store.busy {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(store.status).font(.system(size: 11)).foregroundStyle(.secondary)
                        .lineLimit(1).frame(maxWidth: 220)
                    Button { store.cancel() } label: { Image(systemName: "xmark.circle") }
                        .buttonStyle(.borderless)
                        .help("取消当前任务")
                        .accessibilityLabel("取消当前任务")
                }.workChip()
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 10)
        .frame(height: 72)
        .fireSkySurface(role: .navigation)
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
                    Text(L10n.text(store.page == .nowcast ? "开始实况分析" : "开始预测", store.page == .nowcast ? "Start Nowcast" : "Start Forecast"))
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(scheme == .dark ? Color(0x14, 0x14, 0x14) : Color.white)
                        .frame(maxWidth: .infinity).frame(height: 41)
                        .background(FSDesign.accentGradient(dark: scheme == .dark), in: RoundedRectangle(cornerRadius: 8))
                }.buttonStyle(.plain)
                    .opacity(store.busy || !store.online ? 0.45 : 1)
                    .disabled(store.busy || !store.online)
                    .help(store.busy ? "当前有任务正在进行（\(store.status)），完成或取消后可开始新预测。"
                          : !store.online ? "引擎未连接，请先在左下角重新连接。"
                          : "先预检本地数据，确认后开始预测。")
                if store.page == .nowcast {
                    Button("预热卫星与数据", systemImage: "arrow.down.to.line") { store.beginCheck() }
                        .buttonStyle(.borderless).font(.system(size: 10))
                        .disabled(store.busy || !store.online)
                }
                if store.busy {
                    ProgressView(value: store.percent, total: 100)
                    HStack {
                        Text(store.status).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(2)
                        Spacer()
                        Button { store.cancel() } label: { Image(systemName: "xmark") }
                            .help("取消任务")
                            .accessibilityLabel("取消任务")
                    }
                }
                if !store.online && !store.error.isEmpty {
                    Label("引擎未连接", systemImage: "exclamationmark.triangle")
                        .font(.system(size: 10)).foregroundStyle(.red)
                    Text(store.error)
                        .font(.system(size: 11))
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
                    WorkSegments(options: ["地图", "收藏"], selection: $store.locationTab).frame(width: 112)
                    if store.locationTab == "收藏" {
                        Menu {
                            if store.favorites.isEmpty {
                                Text("还没有收藏位置")
                            } else {
                                ForEach(Array(store.favorites.enumerated()), id: \.offset) { _, item in
                                    Button(role: .destructive) {
                                        store.removeFavorite(item)
                                    } label: {
                                        Label("删除 \(item["name"] as? String ?? "收藏位置")", systemImage: "trash")
                                    }
                                }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                                .font(.system(size: 13))
                                .frame(width: 22, height: 22)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.borderless)
                        .help("管理收藏位置")
                        .accessibilityLabel("管理收藏位置")
                    }
                }
            }
            HStack(spacing: 6) {
                TextField("搜索地名，如「西湖」或「杭州」", text: $store.query).onSubmit { store.search() }
                    .textFieldStyle(.roundedBorder).controlSize(.large)
                Button { store.search() } label: {
                    if store.searching { ProgressView().controlSize(.mini).frame(width: 16, height: 16) }
                    else { Image(systemName: "magnifyingglass").frame(width: 16, height: 16) }
                }
                    .help("搜索位置").controlSize(.large).disabled(store.searching)
                Button { store.locate() } label: {
                    if store.locating { ProgressView().controlSize(.mini).frame(width: 16, height: 16) }
                    else { Image(systemName: "location").frame(width: 16, height: 16) }
                }.help("获取当前位置").accessibilityLabel("获取当前位置")
                    .controlSize(.large).disabled(store.locating)
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
                // UX #7：收藏可删除（右键菜单），空收藏给出引导而不是空白。
                ScrollView {
                    if store.favorites.isEmpty {
                        VStack(spacing: 6) {
                            Image(systemName: "star").foregroundStyle(.secondary)
                            Text("还没有收藏位置").font(.system(size: 11, weight: .medium))
                            Text("搜索或在地图上选好地点后，点击下方「收藏当前点」。")
                                .font(.system(size: 11)).foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                        }.frame(maxWidth: .infinity, minHeight: 160)
                    } else {
                        VStack(spacing: 6) {
                            ForEach(Array(store.favorites.enumerated()), id: \.offset) { _, item in
                                Button(item["name"] as? String ?? "收藏位置") { store.applyLocation(item) }
                                    .buttonStyle(.borderless).frame(maxWidth: .infinity, alignment: .leading)
                                    .contextMenu {
                                        Button("删除收藏", role: .destructive) { store.removeFavorite(item) }
                                    }
                                    .help("点击切换到此位置；右键可删除收藏")
                            }
                        }.padding(8)
                    }
                }.frame(height: 176)
            }
            HStack(spacing: 9) {
                Circle().fill(store.metadataReady ? warm : Color.orange).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 4) {
                    Text(store.name).font(.system(size: 13, weight: .semibold))
                    Text(String(format: "%.4f° N, %.4f° E", store.lat, store.lon))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }
            Text(store.metadataReady
                 ? "位置已确认 · 海拔 \(Int(store.elevation)) m · \(store.timezone)"
                 : "位置待确认 · 展开「编辑坐标」获取海拔与时区")
                .font(.system(size: 11))
                .foregroundStyle(store.metadataReady ? Color.secondary : Color.orange)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .help(store.metadataReady
                      ? "海拔来源设置：\(store.elevationSourceLabel)；实际来源：\(store.elevationSourceActualLabel)"
                      : "确认海拔与时区后才能收藏该点并开始预测。")
                .accessibilityLabel(store.metadataReady
                                    ? "位置已确认，海拔 \(Int(store.elevation)) 米，时区 \(store.timezone)"
                                    : "位置待确认，请展开编辑坐标获取海拔与时区")
            HStack {
                Button("收藏当前点", systemImage: "star") { store.starLocation() }
                Spacer()
                Button("大地图", systemImage: "arrow.up.left.and.arrow.down.right") { store.showMapPicker = true }
            }.buttonStyle(.borderless).font(.system(size: 11))
        }.padding(15).workSurface()
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
                    .buttonStyle(.borderless).font(.system(size: 11))
            }
            Text("目标日期").font(.system(size: 11)).foregroundStyle(.secondary)
            WorkSegments(options: ["今天", "明天", "自定义"],
                         selection: Binding(get: { store.dateTab }, set: { store.chooseDate($0) }))
            if store.dateTab == "自定义" {
                WorkDateButton()
            }
            Text("观测现象").font(.system(size: 11)).foregroundStyle(.secondary)
            WorkSegments(options: ["dawn", "dusk"], titles: ["朝霞", "晚霞"],
                         selection: $store.phenomenon, accented: true)
            Button("编辑坐标", systemImage: "slider.horizontal.3") {
                store.editingCoordinates.toggle()
            }
            .buttonStyle(.borderless).font(.system(size: 11)).frame(maxWidth: .infinity)
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
                    Text("时区按经纬度自动计算").font(.system(size: 11)).foregroundStyle(.secondary)
                    Button("获取海拔与时区") { store.lookupMetadata() }.buttonStyle(.borderless)
                    Toggle("已确认海拔与时区", isOn: $store.metadataReady).font(.system(size: 11))
                }.textFieldStyle(.roundedBorder)
            }
            if !store.error.isEmpty {
                Text(store.error).font(.system(size: 11)).foregroundStyle(.red).textSelection(.enabled)
            }
        }.padding(15).workSurface()
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

/// UX #3：参数变更后旧结果保留，用此徽标说明结果对应旧参数，并提供一键重算。
struct WorkStaleResultBanner: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        if store.resultParametersStale {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.arrow.triangle.2.circlepath")
                    .foregroundStyle(.orange)
                Text("参数已变更 · 当前结果对应旧参数")
                    .font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
                    .help(store.resultParamsSummary)
                Spacer(minLength: 4)
                Button("按新参数重算") { store.beginCheck() }
                    .buttonStyle(.borderless).font(.system(size: 10))
                    .disabled(store.busy || !store.online)
                    .help(store.busy ? "当前有任务正在进行，完成或取消后可重算。" : store.resultParamsSummary)
            }
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Color.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 6))
            .help(store.resultParamsSummary)
        }
    }
}

/// 行动摘要（交接文档 P0-2）：只读取 Rust 报告字段，不在 SwiftUI 重算科学结果。
/// 双模式并列展示，不平均、不互相填补；缺测与单模式失败都有独立文案。
private struct WorkActionSummaryView: View {
    @EnvironmentObject private var store: Store
    let comparison: ForecastComparison

    private struct ModelSnapshot {
        let model: String
        let level: String
        let best: String
        let window: String
        let limit: String?
    }

    private var snapshots: [ModelSnapshot] {
        ForecastComparison.models.compactMap { model in
            guard let report = comparison.report(model) else { return nil }
            let level = report["peak_quality_level"] as? String
                ?? report["quality_level"] as? String
                ?? "指数 \(format(comparison.peak(model), digits: 2)) / 2.5"
            let rawLimit = (report["quality_components"] as? JSONObject)?["quality_limiting_factor"] as? String
            return ModelSnapshot(model: model,
                                 level: level,
                                 best: timeLabel(report["best_time_local"] as? String),
                                 window: windowLabel(report),
                                 limit: rawLimit?.isEmpty == true ? nil : rawLimit)
        }
    }

    private var failedModels: [String] {
        ForecastComparison.models.filter { comparison.error($0) != nil }
    }

    private var levelsAgree: Bool {
        snapshots.count == 2 && Set(snapshots.map(\.level)).count == 1
    }

    private var headline: String {
        if snapshots.isEmpty {
            return failedModels.isEmpty
                ? "完成一次预测后，这里会给出是否值得外出、最佳时刻和主要限制。"
                : "两个模式都未完成，暂不能给出观测建议。"
        }
        if snapshots.count == 1 {
            return "仅 \(snapshots[0].model.uppercased()) 可用：\(snapshots[0].level)"
        }
        return levelsAgree
            ? "两个模式结论一致：\(snapshots[0].level)"
            : "两个模式结论不同：GFS \(snapshots[0].level) · EC \(snapshots[1].level)"
    }

    private var timingText: String {
        guard let first = snapshots.first else { return "" }
        if levelsAgree, snapshots.allSatisfy({ $0.best == first.best && $0.window == first.window }) {
            return "最佳 \(first.best) · 推荐窗口 \(first.window)"
        }
        return snapshots.map { "\($0.model.uppercased()) 最佳 \($0.best) · 窗口 \($0.window)" }
            .joined(separator: "   ·   ")
    }

    private var notes: [String] {
        var result: [String] = []
        for model in failedModels {
            result.append("\(model.uppercased()) 未完成：\(comparison.error(model) ?? "未知原因")")
        }
        for snapshot in snapshots {
            if let limit = snapshot.limit {
                result.append("\(snapshot.model.uppercased()) 主要限制：\(limit)")
            }
        }
        if store.resultParametersStale {
            result.append("当前结果对应旧参数，可点「按新参数重算」更新。")
        }
        return result
    }

    private var statusSymbol: String {
        if snapshots.isEmpty { return failedModels.isEmpty ? "sparkles" : "exclamationmark.triangle" }
        return levelsAgree && failedModels.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: statusSymbol)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(warm)
                Text("行动摘要").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                Spacer(minLength: 8)
                if !snapshots.isEmpty {
                    Button {
                        store.starLocation()
                    } label: {
                        Label("收藏该地点", systemImage: "star")
                    }
                    .buttonStyle(.borderless)
                    .font(.system(size: 11))
                    .disabled(!store.metadataReady)
                    .help(store.metadataReady ? "把当前观测点加入收藏" : "请先确认海拔与时区，再收藏该地点")
                }
            }
            Text(headline)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.primary)
                .accessibilityLabel("结论：\(headline)")
            if !timingText.isEmpty {
                Text(timingText)
                    .font(.system(size: 12)).monospacedDigit()
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("时间与窗口：\(timingText)")
            }
            ForEach(notes, id: \.self) { note in
                Text(note)
                    .font(.system(size: 11))
                    .foregroundStyle(.orange)
                    .accessibilityLabel(note)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fireSkySurface(role: .content, emphasized: !snapshots.isEmpty)
    }
}

struct WorkHeroView: View {
    @EnvironmentObject private var store: Store
    private var comparison: ForecastComparison { store.pointComparison }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            WorkActionSummaryView(comparison: comparison)
            // UX #1/#3：标题跟随结果原始参数，而不是当前表单。
            Text("\(store.resultTitle) \(store.resultPhenomenonLabel)")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            WorkStaleResultBanner()
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
                Text(comparison.status(model)).font(.system(size: 11))
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
                        .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 5) {
                    Text("最佳时刻").font(.system(size: 11)).foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .font(.system(size: 22, weight: .semibold)).monospacedDigit()
                }
            }
            SkyTimeline(report: report, timezone: store.timezone)
            HStack(spacing: 5) {
                if let cycle = cycleLabel(model, report) {
                    Text("起报 \(cycle)").workChip()
                }
                Text(report == nil ? "未预测" : familyLabel(report?["dominant_burnable_pressure_family"])).workChip()
                    .help(report == nil ? "尚未运行预测；完成一次预测后显示主导可烧压力族。"
                          : "主导可烧压力族（来自本次预测结果）")
            }.font(.system(size: 11)).foregroundStyle(.secondary)
            Text(modelSourceNote(model))
                .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            if let message = comparison.error(model) {
                Text(message).font(.system(size: 11)).foregroundStyle(.red).lineLimit(2)
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
                                Text(title).font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
                                // 自适应列数：宽屏多列，窄窗口自动降列，不靠继续缩小字号。
                                LazyVGrid(columns: [GridItem(.adaptive(minimum: 165), spacing: 10)],
                                          alignment: .leading, spacing: 10) {
                                    ForEach(Array(metricSpec[range]), id: \.0) { spec in
                                        VStack(alignment: .leading, spacing: 7) {
                                            Text(spec.1).font(.system(size: 12)).foregroundStyle(.primary.opacity(0.8))
                                            if spec.0 == "aod" {
                                                WorkAODValue(summary: comparison.aod)
                                            } else if spec.0 == "cloud_layer" {
                                                VStack(alignment: .leading, spacing: 4) {
                                                    ForEach(ForecastComparison.models, id: \.self) { model in
                                                        HStack(spacing: 5) {
                                                            Text(model.uppercased()).font(.system(size: 10, weight: .medium))
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
                                                            Text(model.uppercased()).font(.system(size: 10, weight: .medium))
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
                                                .font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(3)
                                        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                                            .frame(height: 120).workSurface()
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
                        WorkLedgerView(report: report,
                                       bestTime: report?["best_time_local"] as? String,
                                       selectedTime: $store.pointSelectedTime)
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
            Text(summary.source).font(.system(size: 9, weight: .medium)).foregroundStyle(coolAccent)
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
                .foregroundStyle(deltaColor(delta))
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

    /// 加分为绿、扣分为红、零修正保持中性（对齐 Web 的 delta 着色）。
    private func deltaColor(_ delta: String?) -> Color {
        guard let delta, delta != "0.000" else { return Color.secondary }
        return delta.hasPrefix("-") ? Color.red : Color.green
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
/// 图表悬停读数的瞬态容器。本机工具链仅含 CommandLineTools（无 Xcode），
/// 没有 SwiftUIMacros 插件，SwiftUI 宏属性（@State 等）无法参与编译；
/// @StateObject/@Published 为普通属性包装器，可正常展开。
private final class ChartHoverState: ObservableObject {
    @Published var date: Date?
}

struct WorkComparisonCurveView: View {
    @EnvironmentObject private var store: Store
    let comparison: ForecastComparison
    let timezone: String
    /// 悬停时刻（原生图形规范：图表需可读 tooltip）；仅在指针悬停期间生效。
    @StateObject private var hover = ChartHoverState()

    var body: some View {
        let points = comparison.curve(timezone: timezone)
        let ratios = comparison.ratioCurve(timezone: timezone)
        let aod = comparison.aodCurve(timezone: timezone)
        let peak = points.max { $0.quality < $1.quality }
        let selectedDate = Self.time(points, matching: store.pointSelectedTime)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("霞光质量与大气趋势").font(.system(size: 13, weight: .semibold))
                Text("质量轴固定 0–2.5 · 比例轴 0–100% · 缺测断线不补零")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                HStack(spacing: 10) {
                    lineSwatch("GFS", warm, dashed: false)
                    lineSwatch("EC", coolAccent, dashed: true)
                }.font(.system(size: 10)).foregroundStyle(.secondary)
            }
            summaryChips(points: points, peak: peak)
            qualityChart(points: points, selectedDate: selectedDate)
            ratioChart(ratios)
            aodChart(aod)
            timelineStrip(peakLocal: peak?.local)
        }.padding(16).workSurface()
    }

    private func summaryChips(points: [ForecastCurvePoint], peak: ForecastCurvePoint?) -> some View {
        let aodValues = comparison.aodCurve(timezone: timezone).map(\.quality)
        let steps = comparison.report(store.selectedModel)?["steps"] as? [JSONObject] ?? []
        let validCount = steps.filter { $0["valid"] as? Bool != false }.count
        return HStack(spacing: 18) {
            summaryChip("峰值质量", peak.map { String(format: "%.3f / 2.5", $0.quality) } ?? "—")
            summaryChip("峰值时刻", peak.map { ObservationDate.timeLabel($0.time, timezone: timezone) } ?? "—")
            summaryChip("有效时次（\(store.selectedModel.uppercased())）",
                        steps.isEmpty ? "—" : "\(validCount) 有效 · 共 \(steps.count) 时次")
            summaryChip("AOD 550 范围",
                        aodValues.isEmpty ? "—" : String(format: "%.3f–%.3f", aodValues.min() ?? 0, aodValues.max() ?? 0))
        }
    }

    private func summaryChip(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 11, weight: .medium)).monospacedDigit().lineLimit(1)
        }.help(title)
    }

    private func lineSwatch(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 5) {
            Rectangle()
                .fill(dashed ? Color.clear : color)
                .frame(width: 14, height: 2)
                .overlay {
                    Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.6, dash: dashed ? [4, 3] : []))
                        .opacity(dashed ? 1 : 0)
                }
            Text(title)
        }
    }

    /// 距悬停时刻最近的有效曲线点（循环实现避免过重的类型推断表达式）。
    private static func hoveredPoint(_ points: [ForecastCurvePoint], near date: Date) -> ForecastCurvePoint? {
        var best: ForecastCurvePoint?
        var bestDelta = Double.infinity
        for point in points {
            let delta = abs(point.time.timeIntervalSince(date))
            if delta < bestDelta {
                bestDelta = delta
                best = point
            }
        }
        return best
    }

    @ChartContentBuilder
    private func seriesMarks(_ points: [ForecastCurvePoint]) -> some ChartContent {
        ForEach(points) { point in
            LineMark(x: .value("时间", point.time), y: .value("质量指数", point.quality),
                     series: .value("曲线", point.series))
                .foregroundStyle(modelColor(point.model))
                .lineStyle(StrokeStyle(lineWidth: 2, dash: point.model == "ec" ? [4, 3] : []))
            PointMark(x: .value("时间", point.time), y: .value("质量指数", point.quality))
                .foregroundStyle(modelColor(point.model)).symbolSize(8)
        }
    }

    @ChartContentBuilder
    private func bestMarks(_ points: [ForecastCurvePoint]) -> some ChartContent {
        ForEach(ForecastComparison.models, id: \.self) { model in
            if let best = bestPoint(model, in: points) {
                PointMark(x: .value("时间", best.time), y: .value("质量指数", best.quality))
                    .symbol(.diamond).symbolSize(46)
                    .foregroundStyle(modelColor(model))
                    .accessibilityLabel("\(model.uppercased()) 最佳时刻 \(ObservationDate.timeLabel(best.time, timezone: timezone))，质量 \(String(format: "%.3f", best.quality))")
            }
        }
    }

    @ChartContentBuilder
    private func hoverMarks(_ points: [ForecastCurvePoint]) -> some ChartContent {
        // 悬停读数（原生图形规范 tooltip）：取最近有效点，数值直接来自曲线数据。
        if let hoverDate = hover.date,
           let hovered = Self.hoveredPoint(points, near: hoverDate) {
            RuleMark(x: .value("悬停时刻", hovered.time))
                .foregroundStyle(Color.secondary.opacity(0.35))
                .lineStyle(StrokeStyle(lineWidth: 1))
            PointMark(x: .value("时间", hovered.time), y: .value("质量指数", hovered.quality))
                .symbol(.circle).symbolSize(30)
                .foregroundStyle(modelColor(hovered.model))
                .annotation(position: .top, spacing: 5) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(hovered.model.uppercased()) \(ObservationDate.timeLabel(hovered.time, timezone: timezone))")
                            .font(.system(size: 9, weight: .medium))
                        Text("质量 \(String(format: "%.3f", hovered.quality))")
                            .font(.system(size: 9)).monospacedDigit()
                    }.padding(4).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 4))
                        .accessibilityHidden(true)
                }
        }
    }

    private func qualityChart(points: [ForecastCurvePoint], selectedDate: Date?) -> some View {
        Chart {
            seriesMarks(points)
            bestMarks(points)
            if let selectedDate {
                RuleMark(x: .value("选中时刻", selectedDate))
                    .foregroundStyle(Color.secondary.opacity(0.55))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .accessibilityLabel("选中时刻")
            }
            hoverMarks(points)
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
        .chartYAxisLabel("质量指数 · 0–2.5")
        .frame(height: 164)
        .overlay {
            if points.isEmpty {
                Text(comparison.result == nil ? "等待预测" : "暂无质量曲线")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .accessibilityLabel("逐时质量趋势，GFS 实线，EC 虚线，菱形为各模式最佳时刻，竖虚线为选中时刻")
        .chartOverlay { proxy in
            Rectangle().fill(Color.clear).contentShape(Rectangle())
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let location):
                        hover.date = proxy.value(atX: location.x, as: Date.self)
                    case .ended:
                        hover.date = nil
                    @unknown default:
                        hover.date = nil
                    }
                }
        }
    }

    /// 0–100% aligned sub-plot for the three ratio series (plan: 右轴或对齐小图).
    private func ratioChart(_ ratios: [ForecastRatioPoint]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                ForEach(["coverage", "ratio", "transmission"], id: \.self) { kind in
                    HStack(spacing: 5) {
                        Rectangle().fill(ratioSeriesColor(kind)).frame(width: 12, height: 2.5)
                        Text(ratioSeriesLabel(kind))
                    }.font(.system(size: 9)).foregroundStyle(.secondary)
                }
                Spacer()
                Text("EC 为虚线").font(.system(size: 9)).foregroundStyle(.tertiary)
            }
            Chart {
                ForEach(ratios) { point in
                    LineMark(x: .value("时间", point.time), y: .value("条件占比", point.value),
                             series: .value("曲线", point.series))
                        .foregroundStyle(ratioSeriesColor(point.kind))
                        .lineStyle(StrokeStyle(lineWidth: 1.8, dash: point.model == "ec" ? [4, 3] : []))
                }
            }
            .chartYScale(domain: 0...1)
            .chartYAxis {
                AxisMarks(position: .trailing, values: [0, 0.5, 1]) { value in
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
                    Text("暂无 0–100% 比例序列")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            .accessibilityLabel("可烧云占比、可烧比例与回程透过率趋势，占比轴固定 0 到 100%")
        }
    }

    private func aodChart(_ aod: [ForecastCurvePoint]) -> some View {
        let values = aod.map(\.quality)
        let times = aod.map(\.time)
        let domain = Self.aodDomain(aod)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("AOD 550 趋势").font(.system(size: 11, weight: .semibold))
                Text(values.isEmpty ? "暂无可用 AOD550 时序"
                     : String(format: "范围 %.3f–%.3f · 来源 %@", values.min() ?? 0, values.max() ?? 0, comparison.aod.source))
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                if let first = times.min(), let last = times.max() {
                    Text("时次 \(ObservationDate.timeLabel(first, timezone: timezone))–\(ObservationDate.timeLabel(last, timezone: timezone))")
                        .font(.system(size: 9)).foregroundStyle(.secondary).monospacedDigit()
                }
            }
            Chart {
                ForEach(aod) { point in
                    LineMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality),
                             series: .value("曲线", point.series))
                        .foregroundStyle(modelColor(point.model))
                        .lineStyle(StrokeStyle(lineWidth: 1.8, dash: point.model == "ec" ? [4, 3] : []))
                    PointMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality))
                        .foregroundStyle(modelColor(point.model)).symbolSize(6)
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
                    Text(comparison.result == nil ? "等待预测" : "暂无可用 AOD550 时序")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            .accessibilityLabel("AOD 550 逐时趋势，自适应取值范围，缺测时段断线")
        }
    }

    /// Event timeline strip; selecting a bar updates the shared local selection only
    /// (never the map). Keyboard focusable buttons with VoiceOver labels.
    private func timelineStrip(peakLocal: String?) -> some View {
        let steps = comparison.report(store.selectedModel)?["steps"] as? [JSONObject] ?? []
        let validSteps = steps.filter { $0["valid"] as? Bool != false }
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("事件时间轴（\(store.selectedModel.uppercased())）")
                    .font(.system(size: 11, weight: .semibold))
                Text("点击选择时刻；选中态不驱动地图")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Text("深色为峰值 · 高亮为当前")
                    .font(.system(size: 9)).foregroundStyle(.tertiary)
            }
            if steps.isEmpty {
                Text(comparison.result == nil ? "等待预测" : "暂无时次")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            } else {
                HStack(alignment: .bottom, spacing: 3) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                        let valid = step["valid"] as? Bool != false
                        let local = step["valid_time_local"] as? String ?? ""
                        if valid {
                            let selected = store.pointSelectedTime == local
                            let isPeak = !local.isEmpty && local == peakLocal
                            Button {
                                store.pointSelectedTime = local
                            } label: {
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(selected ? warm : isPeak ? warm.opacity(0.62) : Color.secondary.opacity(0.32))
                                    .frame(height: selected ? 18 : 10)
                            }.buttonStyle(.plain)
                                .accessibilityLabel("查看 \(timeLabel(local))，质量 \(format(step["instant_quality_index"], digits: 3))")
                                .accessibilityAddTraits(selected ? .isSelected : [])
                                .help("\(timeLabel(local))\(isPeak ? " · 峰值" : "")")
                        } else {
                            RoundedRectangle(cornerRadius: 2)
                                .stroke(Color.secondary.opacity(0.4), style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                                .frame(height: 10)
                                .accessibilityHidden(true)
                                .help("超出有效太阳窗口")
                        }
                    }
                }
                if let first = validSteps.first?["valid_time_local"] as? String,
                   let last = validSteps.last?["valid_time_local"] as? String {
                    HStack {
                        Text(timeLabel(first)).monospacedDigit()
                        Spacer()
                        Text(timeLabel(last)).monospacedDigit()
                    }.font(.system(size: 9)).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func bestPoint(_ model: String, in points: [ForecastCurvePoint]) -> ForecastCurvePoint? {
        guard let best = comparison.report(model)?["best_time_local"] as? String, !best.isEmpty else { return nil }
        return points.first { $0.model == model && $0.local == best }
    }

    private static func time(_ points: [ForecastCurvePoint], matching local: String?) -> Date? {
        guard let local, !local.isEmpty else { return nil }
        return points.first { $0.local == local }?.time
    }

    private static func aodDomain(_ points: [ForecastCurvePoint]) -> ClosedRange<Double> {
        let values = points.map(\.quality)
        guard let low = values.min(), let high = values.max() else { return 0...1 }
        let padding = Swift.max(0.05, (high - low) * 0.25)
        let minLow = Swift.max(0, low - padding)
        return minLow...Swift.max(minLow + 0.1, high + padding)
    }
}

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

struct WorkLedgerView: View {
    @EnvironmentObject private var store: Store
    let report: JSONObject?
    let bestTime: String?
    @Binding var selectedTime: String?
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
                Text("云底–云顶为海拔 ASL 口径，「云层 AGL」为离地口径；— 表示缺测，无效时次不展示数值；点击行仅切换本地选中时刻，不驱动地图。")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(minWidth: 1284, alignment: .leading)
        }
        .workSurface()
    }

    private var header: some View {
        HStack(spacing: 0) {
            Text("时间").frame(width: 88, alignment: .leading)
            Text("质量").frame(width: 68, alignment: .leading)
            Text("档位").frame(width: 92, alignment: .leading)
            Text("可烧").frame(width: 52, alignment: .leading)
            Text("可烧云量").frame(width: 84, alignment: .leading)
            Text("主导压力族").frame(width: 96, alignment: .leading)
            Text("云底–云顶 ASL").frame(width: 120, alignment: .leading)
            Text("云层 AGL").frame(width: 112, alignment: .leading)
            Text("亮度颜色").frame(width: 68, alignment: .leading)
            Text("可烧比例").frame(width: 68, alignment: .leading)
            Text("回程透过率").frame(width: 76, alignment: .leading)
            Text("入射阻挡").frame(width: 72, alignment: .leading)
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
        let baseASL = item["dominant_burnable_cloud_base_asl_mean_m"]
        let topASL = item["dominant_burnable_cloud_top_asl_mean_m"]
        let local = item["valid_time_local"] as? String ?? ""
        let selected = !local.isEmpty && selectedTime == local
        let isBest = !local.isEmpty && local == bestTime
        return Button {
            guard valid, !local.isEmpty else { return }
            selectedTime = local
        } label: {
            HStack(spacing: 0) {
                HStack(spacing: 4) {
                    Text(timeLabel(local))
                    if isBest {
                        Image(systemName: "star.fill")
                            .font(.system(size: 9))
                            .foregroundStyle(warm)
                    }
                }
                .frame(width: 88, alignment: .leading)
                Text(valid ? format(item["instant_quality_index"], digits: 3) : "—")
                    .frame(width: 68, alignment: .leading)
                Text(valid ? item["instant_quality_level"] as? String ?? "—" : "缺测")
                    .frame(width: 92, alignment: .leading)
                Image(systemName: !valid || burnable == nil ? "minus"
                      : burnable == true ? "checkmark" : "xmark")
                    .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? coolAccent : Color.orange)
                    .frame(width: 52, alignment: .leading)
                    .accessibilityLabel(!valid ? "缺测" : burnable == true ? "可烧" : burnable == false ? "不可烧" : "未提供")
                Text(valid ? percent(item["burnable_cloud_coverage"]) : "—")
                    .frame(width: 84, alignment: .leading)
                Text(valid ? familyLabel(item["dominant_burnable_pressure_family"]) : "—")
                    .frame(width: 96, alignment: .leading)
                    .foregroundStyle(item["dominant_burnable_pressure_family"] == nil ? Color.secondary : Color.primary)
                Text(valid ? aslRangeLabel(base: baseASL, top: topASL) : "—")
                    .frame(width: 120, alignment: .leading)
                Text(valid ? cloudLayerLabel(base: base, top: top) : "—")
                    .frame(width: 112, alignment: .leading)
                Text(valid ? ratioText(item["brightness_color"]) : "—")
                    .frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["forward_burnable_cloud_ratio"]) : "—")
                    .frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["observer_return_transmission"]) : "—")
                    .frame(width: 76, alignment: .leading)
                Text(valid ? ratioText(item["solar_incoming_block_risk"]) : "—")
                    .frame(width: 72, alignment: .leading)
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
            .contentShape(Rectangle())
            .background(selected ? warm.opacity(0.10) : Color.clear, in: RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
        .disabled(!valid || local.isEmpty)
        .accessibilityLabel(valid ? "时次 \(timeLabel(local))，质量 \(format(item["instant_quality_index"], digits: 3))\(isBest ? "，最佳时刻" : "")" : "缺测时次")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    /// ASL datum column, matching the Web「加权云底–云顶」column (km ASL).
    private func aslRangeLabel(base: Any?, top: Any?) -> String {
        guard let base = base as? NSNumber, base.doubleValue.isFinite,
              let top = top as? NSNumber, top.doubleValue.isFinite else { return "—" }
        return String(format: "%.1f–%.1f km", base.doubleValue / 1000, top.doubleValue / 1000)
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
                    diagnosticRow("主压力族", familyLabel(report["dominant_burnable_pressure_family"]))
                }
                WorkGenusPhaseDiagnosis(report: report)
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
                        Label(friendlyEngineMessage(reason), systemImage: "map")
                            .font(.system(size: 10)).foregroundStyle(.secondary)
                            .lineLimit(2).help(friendlyEngineMessage(reason))
                    }
                }
            } else {
                Text(waiting).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 160)
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }
}

/// Cross-section grid parsed from the already-returned cross_section.fields payload
/// (engine/src/cross_section.rs). Nulls stay nil so missing cells are never drawn as clear sky.
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
        self.terrain = (section["terrain_elevation_asl_m"] as? [Any])?.compactMap(number) ?? []
        self.requiredASL = (section["required_illumination_base_asl_m"] as? [Any])?.compactMap(number) ?? []
        self.targets = section["theoretical_targets"] as? [JSONObject] ?? []
    }
}

private struct WorkCrossSectionView: View {
    let section: JSONObject?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("太阳方向剖面").font(.system(size: 12, weight: .semibold))
                Spacer()
                Text(section == nil ? "暂无冻结剖面" : "沿光路高度 · 海拔口径")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
            }
            if let section {
                CrossSectionFigureView(section: section)
            } else {
                Text("此结果没有可查询的冻结模式剖面。")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
        .accessibilityLabel("太阳方向大气剖面：温度层线、相对湿度与云量色块、地形填充、云层状态描边和理论照明基准，高度轴为海拔 0 到 18 公里")
    }
}

/// 距离×高度剖面图层组件（regional-7，区域/单点共用，方案 :123-124）。
/// 图层：温度层线、RH/云量色块（压力族配色）、地形填充、云层状态描边、
/// 理论照明基准与理论目标云底/云顶；缺测层不绘制、不解释成晴空。
struct CrossSectionFigureView: View {
    let section: JSONObject
    @Environment(\.colorScheme) private var scheme

    private var data: CrossSectionData? { CrossSectionData(section: section) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                diagnosticChip("太阳高度", section["solar_elevation_deg"], suffix: "°")
                diagnosticChip("太阳方位", section["solar_azimuth_deg"], suffix: "°")
                diagnosticChip("剖面范围", section["analysis_radius_km"], suffix: " km")
                diagnosticChip("相对时刻", offsetLabel(section["offset_minutes"]))
                diagnosticChip("有效时次", timeLabel(section["valid_time_local"] as? String))
                diagnosticChip("采样", section["spatial_sampling"])
            }
            if let data {
                Canvas { context, size in
                    draw(context: context, size: size, data: data)
                }
                .frame(height: 252)
                legend
                targetStrip(data.targets)
            } else {
                Text("后端未返回可绘制的剖面字段；可在诊断中查看理论目标与照明基准。")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120, alignment: .center)
            }
        }
    }

    // MARK: drawing

    private func draw(context: GraphicsContext, size: CGSize, data: CrossSectionData) {
        let left = 40.0, right = size.width - 12, top = 10.0, bottom = size.height - 24
        guard right > left + 20, bottom > top + 20 else { return }
        let maxDistance = max(1, data.distances.last ?? 1)
        let maxHeight = 18_000.0
        let x = { (distance: Double) in left + distance / maxDistance * (right - left) }
        let y = { (height: Double) in top + (1 - min(max(height, 0), maxHeight) / maxHeight) * (bottom - top) }

        // height grid + labels (0 / 3 / 6 / ... / 18 km ASL)
        for altitude in stride(from: 0.0, through: maxHeight, by: 3000.0) {
            let lineY = y(altitude)
            var grid = Path()
            grid.move(to: CGPoint(x: left, y: lineY))
            grid.addLine(to: CGPoint(x: right, y: lineY))
            context.stroke(grid, with: .color(Color.secondary.opacity(0.22)), lineWidth: 0.6)
            context.draw(Text("\(Int(altitude / 1000))km").font(.system(size: 8)).foregroundStyle(.secondary),
                         at: CGPoint(x: left - 4, y: lineY), anchor: .trailing)
        }
        // distance grid every 4th sample
        for (index, distance) in data.distances.enumerated() where index % 4 == 0 {
            let lineX = x(distance)
            var grid = Path()
            grid.move(to: CGPoint(x: lineX, y: top))
            grid.addLine(to: CGPoint(x: lineX, y: bottom))
            context.stroke(grid, with: .color(Color.secondary.opacity(0.15)),
                           style: StrokeStyle(lineWidth: 0.6, dash: [3, 4]))
            context.draw(Text("\(Int(distance))km").font(.system(size: 8)).foregroundStyle(.secondary),
                         at: CGPoint(x: lineX, y: bottom + 10), anchor: .center)
        }

        // terrain fill + line + observer dot
        if data.terrain.count == data.distances.count {
            var fill = Path()
            fill.move(to: CGPoint(x: x(data.distances[0]), y: y(data.terrain[0])))
            for index in data.distances.indices.dropFirst() {
                fill.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.terrain[index])))
            }
            fill.addLine(to: CGPoint(x: right, y: bottom))
            fill.addLine(to: CGPoint(x: left, y: bottom))
            fill.closeSubpath()
            context.fill(fill, with: .color(Color(red: 0.41, green: 0.45, blue: 0.44).opacity(0.18)))
            var line = Path()
            line.move(to: CGPoint(x: x(data.distances[0]), y: y(data.terrain[0])))
            for index in data.distances.indices.dropFirst() {
                line.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.terrain[index])))
            }
            context.stroke(line, with: .color(Color(red: 0.41, green: 0.45, blue: 0.44)), lineWidth: 1.6)
            let observer = CGPoint(x: x(data.distances[0]), y: y(data.terrain[0]))
            let dot = CGRect(x: observer.x - 5, y: observer.y - 5, width: 10, height: 10)
            context.fill(Path(ellipseIn: dot), with: .color(Color(red: 0.95, green: 0.49, blue: 0.26)))
            context.draw(Text("观测点").font(.system(size: 8)).foregroundStyle(.secondary),
                         at: CGPoint(x: observer.x + 10, y: observer.y - 8), anchor: .leading)
        }

        // RH / cloud amount cells with cloud-layer status strokes
        let sampleCount = Swift.max(1, data.distances.count - 1)
        let columnSpan = (right - left) / Double(sampleCount)
        let cellWidth = Swift.max(6.0, columnSpan * 0.72)
        for i in data.distances.indices {
            for l in data.levels.indices {
                guard let height = data.heights[i][l] else { continue } // 缺测层不绘制，不解释成晴空
                let center = CGPoint(x: x(data.distances[i]), y: y(height))
                let topY = y(height + 700)
                let cellHeight = max(5, y(height - 700) - topY)
                let rect = CGRect(x: center.x - cellWidth / 2, y: topY,
                                  width: cellWidth, height: cellHeight)
                if let fill = cloudCellFill(amount: data.amounts[i][l], presence: data.presence[i][l],
                                            rh: data.rh[i][l], fraction: data.fractions[i][l],
                                            family: data.familyCodes[i][l]) {
                    context.fill(Path(rect), with: .color(fill))
                }
                switch data.statuses[i][l] {
                case 3:
                    context.stroke(Path(rect), with: .color(Color(red: 0.94, green: 0.42, blue: 0.21)), lineWidth: 1.8)
                case 2:
                    context.stroke(Path(rect), with: .color(Color(red: 0.83, green: 0.55, blue: 0.22)),
                                   style: StrokeStyle(lineWidth: 1.4, dash: [4, 3]))
                default:
                    break
                }
            }
        }

        // temperature layer lines colored by level average temperature
        for l in data.levels.indices {
            var points: [CGPoint] = []
            var temps: [Double] = []
            for i in data.distances.indices {
                guard let height = data.heights[i][l] else { continue }
                points.append(CGPoint(x: x(data.distances[i]), y: y(height)))
                if let temp = data.temps[i][l] { temps.append(temp) }
            }
            guard points.count > 1 else { continue }
            var line = Path()
            line.move(to: points[0])
            points.dropFirst().forEach { line.addLine(to: $0) }
            let average = temps.isEmpty ? 0 : temps.reduce(0, +) / Double(temps.count)
            let color = average < -30 ? Color(red: 0.55, green: 0.40, blue: 0.45)
                : average < 0 ? Color(red: 0.69, green: 0.42, blue: 0.36)
                : Color(red: 0.78, green: 0.31, blue: 0.22)
            context.stroke(line, with: .color(color.opacity(0.62)), lineWidth: 1.1)
            if l % 2 == 0, l < data.levels.count, let last = points.last {
                context.draw(Text("\(data.levels[l])hPa").font(.system(size: 8)).foregroundStyle(.secondary),
                             at: CGPoint(x: last.x - 5, y: last.y - 6), anchor: .trailing)
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
                           style: StrokeStyle(lineWidth: 2.2, dash: [7, 5]))
        }

        // theoretical target cloud base / top markers
        for target in data.targets {
            guard let distance = number(target["distance_km"]),
                  let base = number(target["cloud_base_asl_m"]) else { continue }
            let basePoint = CGPoint(x: x(distance), y: y(base))
            context.fill(Path(ellipseIn: CGRect(x: basePoint.x - 4, y: basePoint.y - 4, width: 8, height: 8)),
                         with: .color(warm))
            if let top = number(target["cloud_top_asl_m"]) {
                let topPoint = CGPoint(x: x(distance), y: y(top))
                context.fill(Path(ellipseIn: CGRect(x: topPoint.x - 4, y: topPoint.y - 4, width: 8, height: 8)),
                             with: .color(coolAccent))
            }
        }
    }

    /// Port of the Web cloudFill (NationalCrossSection.svelte): cloud amount over
    /// presence/fraction fallback, humidity tint below the cloud threshold, family colors.
    private func cloudCellFill(amount: Double?, presence: Double?, rh: Double?,
                               fraction: Double?, family: Int) -> Color? {
        let cloudAmount = amount ?? Swift.max(presence ?? 0, (fraction ?? 0) / 100)
        let humidity = Swift.max(0, (rh ?? 0) - 55) / 45
        if cloudAmount < 0.18 {
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

    // MARK: legend & target diagnosis

    private var legend: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 12) {
                legendChip("高云压力族", Color(red: 116 / 255, green: 157 / 255, blue: 194 / 255))
                legendChip("中云压力族", Color(red: 105 / 255, green: 154 / 255, blue: 142 / 255))
                legendChip("低云压力族", Color(red: 126 / 255, green: 136 / 255, blue: 145 / 255))
                legendChip("湿润未饱和", Color(red: 112 / 255, green: 151 / 255, blue: 126 / 255))
                legendLine("温度层线", Color(red: 0.69, green: 0.42, blue: 0.36), dashed: false)
                legendLine("地形", Color(red: 0.41, green: 0.45, blue: 0.44), dashed: false)
            }
            HStack(spacing: 12) {
                legendDot("云底", warm)
                legendDot("云顶", coolAccent)
                legendLine("理论照明基准（ASL）", Color(red: 0.83, green: 0.55, blue: 0.22), dashed: true)
                legendFrame("受光且可见候选（未评估云遮挡）", Color(red: 0.94, green: 0.42, blue: 0.21), dashed: false)
                legendFrame("达到受光阈值但不可见", Color(red: 0.83, green: 0.55, blue: 0.22), dashed: true)
            }
            Text("图层数值为对应模式（GFS/EC）各自的场；未着色不代表晴空（缺测或低于阈值），低云压力族仅在近场距离门限内排除；橙色状态未评估云体遮挡。")
                .font(.system(size: 9)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 9)).foregroundStyle(.secondary)
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

    /// Theoretical target status summary (Web target-strip parity).
    private func targetStrip(_ targets: [JSONObject]) -> some View {
        let burnable = targets.filter { $0["status"] as? String == "burnable" }
        return Group {
            if !targets.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("受光与几何/地形可见候选（未评估云遮挡）")
                        .font(.system(size: 10, weight: .medium)).foregroundStyle(.primary)
                    if !burnable.isEmpty {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 232), spacing: 6)], spacing: 6) {
                            ForEach(Array(burnable.prefix(8).enumerated()), id: \.offset) { _, target in
                                targetChip(target)
                            }
                        }
                    } else if let diagnostic = targets.first(where: { $0["status"] as? String != "burnable" }) {
                        Text("当前太阳方向没有可烧目标云：\(targetFamily(diagnostic))，\(diagnostic["status_reason"] as? String ?? "状态原因未提供")")
                            .font(.system(size: 9)).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func targetChip(_ target: JSONObject) -> some View {
        let family = targetFamily(target)
        let phase = (target["cloud_phase"] as? JSONObject)?["label"] as? String ?? "unknown"
        let phaseText = phase == "ice" ? "冰相" : phase == "water" ? "水相" : phase == "mixed" ? "混合相" : "未知"
        let distance = number(target["distance_km"]).map { String(format: "%.0f", $0) } ?? "—"
        let base = number(target["cloud_base_asl_m"]).map { String(format: "%.1f", $0 / 1000) } ?? "—"
        let top = number(target["cloud_top_asl_m"]).map { String(format: "%.1f", $0 / 1000) } ?? "—"
        let text = "\(family) · \(phaseText) · \(distance) km · ASL \(base)–\(top) km"
        return Text(text)
            .font(.system(size: 9)).monospacedDigit()
            .padding(.horizontal, 6).padding(.vertical, 3)
            .background(Color(red: 0.94, green: 0.42, blue: 0.21).opacity(0.08),
                        in: RoundedRectangle(cornerRadius: 4))
            .overlay { RoundedRectangle(cornerRadius: 4).stroke(Color(red: 0.94, green: 0.42, blue: 0.21).opacity(0.42), lineWidth: 1) }
            .help(target["status_reason"] as? String ?? text)
    }

    private func targetFamily(_ target: JSONObject) -> String {
        switch target["pressure_family"] as? String {
        case "high": return "高云压力族"
        case "middle": return "中云压力族"
        case "low": return "低云压力族"
        default: return target["pressure_family_label"] as? String ?? "云层"
        }
    }

    private func diagnosticChip(_ title: String, _ value: Any?, suffix: String = "") -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(title == "有效时次" ? (value as? String ?? "—")
                 : diagnosticValue(value, digits: 1, suffix: suffix))
                .font(.system(size: 10, weight: .medium)).monospacedDigit()
        }
    }
}

/// Structured cloud-genus / cloud-phase diagnosis (plan item point-9). Reads the backend
/// proxy objects as-is; GFS point reports carry the fixed "not resolved" phase placeholder,
/// which is shown as 未解析 instead of a guessed phase (plan item point-15).
private struct WorkGenusPhaseDiagnosis: View {
    let report: JSONObject

    var body: some View {
        WorkDiagnosticGroup(title: "云属与相态（模型代理 · 不参与评分）") {
            if let genus = report["cloud_genus_proxy"] as? JSONObject {
                let label = genus["label"] as? String ?? "unknown"
                if label != "unknown" {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("云属代理").foregroundStyle(.secondary)
                        Spacer(minLength: 10)
                        Text(genusLabel(label))
                            .multilineTextAlignment(.trailing)
                        Text(percent(genus["confidence"])).monospacedDigit().foregroundStyle(.secondary)
                    }.font(.system(size: 10))
                    if let candidates = topCandidates(genus["candidate_scores"], limit: 3) {
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text("候选 Top3").foregroundStyle(.secondary)
                            Spacer(minLength: 10)
                            Text(candidates).multilineTextAlignment(.trailing).lineLimit(2)
                        }.font(.system(size: 10))
                    }
                } else if let compatible = compatibleGenera(report) {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("云属未定").foregroundStyle(.secondary)
                        Spacer(minLength: 10)
                        Text("压力族仅兼容 \(compatible)").multilineTextAlignment(.trailing).lineLimit(2)
                    }.font(.system(size: 10))
                }
                diagnosticRow("推断依据", genus["source"])
                diagnosticRow("评分影响", (genus["scoring_effect"] as? Bool) == false ? "不参与评分" : "—")
            } else {
                diagnosticRow("云属代理", "—")
            }
            phaseRows(report["cloud_phase_proxy"])
        }
    }

    @ViewBuilder
    private func phaseRows(_ phase: Any?) -> some View {
        if let phase = phase as? JSONObject {
            let label = phase["label"] as? String ?? "unknown"
            if label == "unknown", phase["source"] as? String == "not-resolved-for-native-burnable-target" {
                diagnosticRow("相态", "未解析（GFS 桌面点预测不解析目标云相态）")
            } else {
                let ice = phase["ice_fraction"] as? NSNumber
                let iceText = (ice != nil && ice!.doubleValue.isFinite)
                    ? " · 冰含量占比 \(Int((ice!.doubleValue * 100).rounded()))%" : ""
                diagnosticRow("相态", "\(phaseLabel(label))\(iceText)")
                diagnosticRow("相态置信度", phase["confidence"], percentValue: true)
            }
        } else if phase == nil {
            diagnosticRow("相态", "—")
        } else {
            diagnosticRow("相态", phase)
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
            WorkStaleResultBanner()
            HStack(spacing: 16) {
                summary(store.regionalModel)
                Spacer(minLength: 0)
                regionalMetadata(store.regionalModel.uppercased(),
                                 store.regionalForecast.model(store.regionalModel))
            }.padding(.vertical, 6)
            HStack(spacing: 10) {
                Text(regionalGridLabel).font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer()
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
                // UX #17：错误全文可见、可选中复制，不再截断成两行。
                Text(store.error).font(.system(size: 10)).foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                    .help(store.error)
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
                    .foregroundStyle(store.editingRegionOnMap ? coolAccent : Color.primary)
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
                Label("开始预测", systemImage: "play.fill")
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
                            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6)).padding(10)
                    }
                }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var legend: some View {
        HStack(spacing: 7) {
            Text("质量指数")
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
                            .disabled(store.busy)
                        }
                    }.font(.system(size: 11)).help(display?.error ?? display?.level ?? "无该模式结果")
                }
                if let cell = store.regionalForecast.model(store.selectedModel).cell(at: coordinate)
                    ?? store.regionalForecast.model(store.selectedModel).cityRanking.first(where: { $0.coordinate == coordinate }) {
                    RegionalCellDetail(model: store.selectedModel, cell: cell)
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
                Text(store.regionalModel.uppercased()).font(.system(size: 10)).foregroundStyle(.secondary)
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
                                    Text(cell.quality.map { String(format: "%.2f", $0) } ?? "—")
                                        .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                                }
                                .padding(.horizontal, 7).padding(.vertical, 9).contentShape(Rectangle())
                                .background(store.selectedRegionalCoordinate == cell.coordinate ? coolAccent.opacity(0.12) : Color.clear,
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
                                    Text(cell.quality.map { String(format: "%.2f", $0) } ?? "—").font(.system(size: 12, weight: .semibold))
                                        .monospacedDigit()
                                }
                                .padding(.horizontal, 7).padding(.vertical, 9).contentShape(Rectangle())
                                .background(store.selectedRegionalCoordinate == cell.coordinate ? coolAccent.opacity(0.12) : Color.clear,
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

/// 区域网格弹窗明细（regional-1～4，Web BurnOutlookMap.svelte 弹窗的原生对应）。
/// 字段全部来自区域 points[] DTO 顶层与 quality_components；键缺失显示「未计算/未提供」，
/// 不在 SwiftUI 重算或猜值（方案 :33、:83-84）。置信度缺值显示「未提供」而非 0%。
private struct RegionalCellDetail: View {
    @EnvironmentObject private var store: Store
    let model: String
    let cell: RegionalCell

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Text(model.uppercased()).font(.system(size: 10, weight: .bold)).foregroundStyle(modelColor(model))
                if let peakLevel = cell.peakLevel, !peakLevel.isEmpty {
                    Text(peakLevel).font(.system(size: 9, weight: .medium))
                        .padding(.horizontal, 5).padding(.vertical, 1.5)
                        .background(warm.opacity(0.12), in: Capsule())
                }
                Spacer()
            }
            HStack(alignment: .top, spacing: 8) {
                scoreCard("峰值质量 / 2.5", cell.peakQuality)
                scoreCard("事件窗口质量 / 2.5", cell.eventQuality)
            }
            VStack(alignment: .leading, spacing: 3) {
                row("主导可烧压力族", cell.pressureFamily.map { familyLabel($0) } ?? "未提供")
                    .help("结果未包含该格点的主导可烧压力族字段；通常表示无可烧目标云。")
                row("有效可烧云", cell.cloudCoverageEffective.map { String(format: "%.1f%%", $0 * 100) } ?? "未计算")
                row("可烧时长", cell.durationMinutes.map { String(format: "%.0f 分钟", $0) } ?? "未计算")
                row("质量 AOD", cell.qualityAod.map { String(format: "%.3f", $0) } ?? "未计算")
                row("入射修正", signedText(cell.incomingAdjustment))
                row("观测修正", signedText(cell.observerAdjustment))
                row("最佳时刻", cell.bestTime.map { timeLabel($0) } ?? "无明显峰值")
                row("置信度", cell.confidenceScore.map { "\(Int(($0 * 100).rounded()))%" } ?? "未提供")
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
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(value.map { String(format: "%.3f", $0) } ?? "未计算")
                .font(.system(size: 13, weight: .semibold)).monospacedDigit()
                .foregroundStyle(value == nil ? Color.secondary : Color.primary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(6)
        .background(Color.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 5))
        .accessibilityElement(children: .combine)
    }

    private func row(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.system(size: 9)).foregroundStyle(.secondary)
            Spacer(minLength: 6)
            Text(value).font(.system(size: 10)).monospacedDigit()
        }
        .accessibilityElement(children: .combine)
    }

    /// 入射/观测 AOD 修正：Web 弹窗的 ±0.3f 语义。
    private func signedText(_ value: Double?) -> String {
        guard let value else { return "未计算" }
        return String(format: "%+.3f", value)
    }
}

private struct WorkNowcastHeroView: View {
    @EnvironmentObject private var store: Store
    private var report: JSONObject? { store.resultKind == .nowcast ? store.result : nil }
    private var nowcast: JSONObject { report?["nowcast"] as? JSONObject ?? [:] }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(store.resultTitle) \(store.resultPhenomenonLabel)")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            WorkStaleResultBanner()
            HStack(alignment: .top, spacing: 18) {
                VStack(alignment: .leading, spacing: 9) {
                    HStack(spacing: 6) {
                        Circle().fill(coolAccent).frame(width: 6, height: 6)
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

private struct WorkNowcastResultsView: View {
    @EnvironmentObject private var store: Store
    private var report: JSONObject? { store.resultKind == .nowcast ? store.result : nil }
    private var presentation: NowcastPresentation { NowcastPresentation(report: report) }
    private var nowcast: JSONObject { presentation.observation }
    // Plan row 29 grouping: 卫星云况 / 运动采样 / 光学与雷达 (nowcast-4/7/11).
    private let groups = [("卫星云况", 0..<7), ("运动采样", 7..<11), ("光学与雷达", 11..<15)]
    var body: some View {
        VStack(spacing: 12) {
            WorkAnalysisTabs()
            ScrollView {
                VStack(spacing: 12) {
                    if store.resultTab == "概览" {
                        ForEach(groups, id: \.0) { title, range in
                            VStack(alignment: .leading, spacing: 7) {
                                HStack(alignment: .firstTextBaseline, spacing: 10) {
                                    Text(title).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
                                    if title == "光学与雷达" {
                                        Text(radarTimeliness)
                                            .font(.system(size: 9)).foregroundStyle(.secondary)
                                            .lineLimit(1).help(radarTimeliness)
                                    }
                                    Spacer(minLength: 0)
                                }
                                LazyVGrid(columns: [GridItem(.flexible(), spacing: 10),
                                                    GridItem(.flexible(), spacing: 10),
                                                    GridItem(.flexible())], spacing: 10) {
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
            if let map = report?["map_capability"] as? JSONObject,
               map["available"] as? Bool == false {
                Label(friendlyEngineMessage(map["reason"] as? String ?? "实况地图未发布"), systemImage: "map")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .lineLimit(2).help(friendlyEngineMessage(map["reason"] as? String ?? ""))
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

private extension Dictionary where Key == String, Value == Any {
    var regionCode: String { self["code"] as? String ?? "" }
}

private struct WorkSurface: ViewModifier {
    var emphasized = false
    func body(content: Content) -> some View {
        // 内容表面退出通用玻璃：正文、指标和图表保持稳定底色与清晰边界。
        content.fireSkySurface(role: .content, emphasized: emphasized)
    }
}

private struct WorkSegments: View {
    let options: [String]
    var titles: [String]? = nil
    @Binding var selection: String
    var accented = false
    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast
    var body: some View {
        HStack(spacing: 3) {
            ForEach(Array(options.enumerated()), id: \.element) { index, value in
                let selected = selection == value
                let label = titles?[index] ?? value
                Button { selection = value } label: {
                    Text(label)
                        .font(.system(size: 11, weight: selected ? .semibold : .regular))
                        .foregroundStyle(selected && accented
                                          ? (scheme == .dark ? warm : Color(0xB3, 0x3F, 0x23))
                                          : selected ? Color.primary : Color.secondary)
                        .frame(maxWidth: .infinity).frame(height: 28)
                        .background {
                            RoundedRectangle(cornerRadius: 6)
                                .fill(selected
                                      ? (accented ? warm.opacity(0.14)
                                         : scheme == .dark ? Color.white.opacity(0.12) : Color.white.opacity(0.88))
                                      : Color.clear)
                        }
                        .overlay {
                            RoundedRectangle(cornerRadius: 6)
                                .strokeBorder(
                                    selected ? warm.opacity(0.35)
                                             : Color.secondary.opacity(contrast == .increased ? 0.45 : 0.16),
                                    lineWidth: selected || contrast == .increased ? 1 : 0.5
                                )
                        }
                        .contentShape(Rectangle())
                }.buttonStyle(.plain)
                    .accessibilityLabel("\(label)\(selected ? "，已选中" : "")")
                    .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }.padding(3)
            .background(Color.black.opacity(scheme == .dark ? 0.12 : 0.035), in: RoundedRectangle(cornerRadius: 8))
            .overlay { RoundedRectangle(cornerRadius: 8).stroke(.secondary.opacity(0.15), lineWidth: 1) }
    }
}

extension View {
    fileprivate func workSurface(emphasized: Bool = false) -> some View { modifier(WorkSurface(emphasized: emphasized)) }
    fileprivate func workChip() -> some View {
        padding(.horizontal, 8).padding(.vertical, 5)
            .background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 6))
            .overlay { RoundedRectangle(cornerRadius: 6).stroke(.secondary.opacity(0.22), lineWidth: 1) }
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

private func modelColor(_ model: String) -> Color { model == "ec" ? coolAccent : warm }

private func timeLabel(_ value: String?) -> String {
    guard let value, value.count >= 16 else { return "—" }
    return String(value.dropFirst(11).prefix(5))
}

private func windowLabel(_ report: JSONObject?) -> String {
    guard let value = report?["timing_window"] as? JSONObject else { return "—" }
    return "\(timeLabel(value["start_time_local"] as? String)) – \(timeLabel(value["end_time_local"] as? String))"
}

/// B1 天空语义：观测窗口时段条（方案 UI_REFRESH B1）——渐变窗口区间 +
/// 最佳时刻点 + 观测点时区倒计时；无窗口数据时回退文字行。
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

    /// 观测点时区下 HH:mm 对应的时刻；早于窗口起点视为次日凌晨（跨午夜）。
    private func pointDate(_ minutesValue: Int, start: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        var components = calendar.dateComponents([.year, .month, .day], from: Date())
        components.hour = minutesValue / 60
        components.minute = minutesValue % 60
        let today = calendar.date(from: components) ?? Date()
        if minutesValue >= start { return today }
        return calendar.date(byAdding: .day, value: 1, to: today) ?? today
    }

    private func countdown(_ interval: TimeInterval) -> String {
        let total = max(Int(interval) / 60, 0)
        let hours = total / 60
        let minutesValue = total % 60
        return hours > 0 ? "\(hours) 小时 \(minutesValue) 分" : "\(minutesValue) 分钟"
    }

    var body: some View {
        if let window = report?["timing_window"] as? JSONObject,
           let startRaw = window["start_time_local"] as? String,
           let endRaw = window["end_time_local"] as? String,
           let start = minutes(startRaw), let end = minutes(endRaw) {
            let bestRaw = report?["best_time_local"] as? String
            let best = bestRaw.flatMap(minutes)
            let span = (end - start <= 0) ? end - start + 1440 : end - start
            let now = Date()
            let startDate = pointDate(start, start: start)
            let endDate = pointDate(end % 1440, start: start)
            let inWindow = now >= startDate && now <= endDate
            let bestDate = best.map { pointDate($0, start: start) }
            VStack(alignment: .leading, spacing: 5) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(.quaternary.opacity(0.5))
                        // 窗口区间：按窗口跨度在轴上定位（跨午夜已归一化）。
                        Capsule()
                            .fill(FSDesign.accentGradient(dark: scheme == .dark))
                            .frame(width: geo.size.width * CGFloat(span) / CGFloat(max(span, 1)))
                        if let best {
                            Circle()
                                .fill(Color.white)
                                .frame(width: 9, height: 9)
                                .overlay { Circle().strokeBorder(FSDesign.chipForeground("done", dark: scheme == .dark), lineWidth: 1.5) }
                                .offset(x: geo.size.width * CGFloat((best - start <= 0 ? best + 1440 : best) - start) / CGFloat(span) - 4.5)
                        }
                    }
                }
                .frame(height: 10)
                HStack {
                    Text(startRaw).monospacedDigit()
                    Spacer()
                    if let bestRaw, let bestDate {
                        Text(inWindow
                             ? "最佳时刻 \(bestRaw) · \(countdown(bestDate.timeIntervalSinceNow))后"
                             : "最佳时刻 \(bestRaw)")
                            .foregroundStyle(FSDesign.chipForeground("done", dark: scheme == .dark))
                    }
                    Spacer()
                    Text(endRaw).monospacedDigit()
                }
                .font(.system(size: 9)).monospacedDigit().foregroundStyle(.secondary)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("观测窗口 \(startRaw) 至 \(endRaw)\(bestRaw.map { "，最佳时刻 \($0)" } ?? "")")
            }
        } else {
            Text("推荐窗口 \(windowLabel(report))").font(.system(size: 10)).foregroundStyle(.secondary)
        }
    }
}

private func metricValue(_ key: String, _ report: JSONObject?) -> String {
    guard let report else { return "—" }
    let components = report["quality_components"] as? JSONObject ?? [:]
    switch key {
    case "aod": return format(components["quality_aod550"] ?? report["quality_aod550"])
    case "cloud_layer": return cloudLayerMetricValue(report)
    case "duration": return format(report["duration_minutes"] ?? components["duration_minutes"], digits: 0)
    case "confidence": return percent(report["confidence_score"])
    case "convective": return format(report["convective_risk"])
    case "burnable": return percent(components["burnable_cloud_coverage_effective"] ?? report["burnable_cloud_coverage_effective"])
    // 0–1 比例字段统一百分比口径，与逐时台账 ratioText、Web MetricsGrid score() 一致。
    case "material": return ratioText(components["cloud_material"])
    case "brightness": return ratioText(components["brightness_color"])
    case "incoming": return format(components["incoming_aod_illumination_factor"])
    case "contrast": return format(components["observer_aod_contrast_factor"])
    default: return "—"
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
    guard let number = value as? NSNumber else { return "—" }
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
