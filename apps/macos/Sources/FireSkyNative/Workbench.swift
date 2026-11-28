import AppKit
import SwiftUI

// Shared brand accent; used by the workbench and the settings category rail.
let warm = Color(nsColor: NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(calibratedRed: 1.00, green: 0.61, blue: 0.42, alpha: 1)
        : NSColor(calibratedRed: 0.82, green: 0.35, blue: 0.21, alpha: 1)
})
let coolAccent = Color(nsColor: NSColor(name: nil) { appearance in
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

/// 侧栏显示状态。语义与 NavigationSplitViewVisibility 对齐；本机工具链下
/// 直接替换根容器会让 1120×720 窗口裁掉品牌行和顶栏，因此继续由自定根布局消费。
private final class SidebarVisibility: ObservableObject {
    @Published var column: NavigationSplitViewVisibility = .all

    var visible: Bool { column != .detailOnly }
}

struct WorkbenchRootView: View {
    @EnvironmentObject private var store: Store
    @EnvironmentObject private var updater: Updater
    @EnvironmentObject private var reminders: ReminderCenter
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    /// 本机工具链无 SwiftUIMacros，使用普通 ObservableObject 代替 @State。
    @StateObject private var sidebarState = SidebarVisibility()

    var body: some View {
        HStack(spacing: 0) {
            if sidebarState.visible {
                sidebar
                Divider().opacity(0.6)
            }
            VStack(spacing: 0) {
                topbar
                Divider().opacity(0.45)
                Group {
                    if store.page == .data { InventoryView() }
                    else if store.page == .history { NativeTaskHistoryView() }
                    else if store.page == .settings { SettingsView() }
                    else if store.page == .regional { WorkRegionPage() }
                    else { workbench }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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
            .ignoresSafeArea()
        }
        .tint(warm)
        .frame(minWidth: 1120, minHeight: 720)
        .preferredColorScheme(store.appearance == "dark" ? .dark : store.appearance == "light" ? .light : nil)
        .sheet(isPresented: $store.showPreflight, onDismiss: {
            if store.checking || store.session != nil && !store.busy { store.closePreflight() }
        }) { PreflightView().tint(warm).interactiveDismissDisabled(store.busy) }
        .sheet(isPresented: $store.showUpdateSheet) {
            UpdateSheetView().tint(warm)
        }
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
        .onAppear {
            store.onRecentAlertsReceived = { alerts in
                guard store.alertNotifyDesktop else { return }
                let key = "firesky.lastDeliveredAlertID"
                let lastSeen = UserDefaults.standard.string(forKey: key) ?? ""
                for alert in alerts {
                    guard let id = alert["id"] as? String, !id.isEmpty else { continue }
                    if id == lastSeen { break }
                    let title = alert["title"] as? String ?? "FireSky 火烧云预警"
                    let body = alert["body"] as? String ?? ""
                    reminders.deliverImmediate(id: id, title: title, body: body)
                }
                if let newest = alerts.first?["id"] as? String, !newest.isEmpty {
                    UserDefaults.standard.set(newest, forKey: key)
                }
            }
        }
        .task {
            // 启动静默检查更新（默认开，可在设置 → 通用关闭）；仅首次启动检查，关窗重开不重复触发。
            if updater.autoCheck && !store.didScheduleAutoUpdateCheck {
                store.didScheduleAutoUpdateCheck = true
                try? await Task.sleep(for: .seconds(8))
                if !Task.isCancelled, updater.autoCheck {
                    updater.checkForUpdates(interactive: false)
                }
            }
        }
        .onChange(of: store.page) { _, page in
            if page == .regional, store.regionalCamera == nil {
                store.regionalFitVersion += 1
            }
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
            HStack(spacing: 8) {
                FireSkyMark().frame(width: 28, height: 28)
                VStack(alignment: .leading, spacing: 1) {
                    Text("FireSky").fireSkyFont(size: 15, weight: .semibold, relativeTo: .title3)
                    Text(L10n.text("朝晚霞观测台", "Twilight Workbench"))
                        .fireSkyFont(size: 10, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                }
            }.padding(.leading, 4).padding(.bottom, 14)
            Text(L10n.t("观测工作台")).fireSkyFont(size: 10, weight: .semibold, relativeTo: .caption)
                .foregroundStyle(.secondary).padding(.leading, 4).padding(.bottom, 6)
            ForEach([Page.point, .regional, .nowcast, .data, .history]) { page in
                let selected = store.page == page
                Button {
                    // UX #5：切页不再被全局 busy 锁死；运行中的任务继续，顶栏可见进度。
                    store.page = page
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: page.symbol).symbolRenderingMode(.hierarchical)
                            .font(.system(size: 14, weight: selected ? .medium : .regular))
                            .foregroundStyle(selected ? warm : Color.primary.opacity(0.68))
                            .frame(width: 20, height: 20)
                        Text(page.title)
                            .fireSkyFont(size: 12.5, weight: selected ? .semibold : .regular)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                        if selected {
                            Image(systemName: "chevron.right").font(.system(size: 9.5, weight: .semibold))
                                .foregroundStyle(warm.opacity(0.85))
                        }
                    }
                    .padding(.horizontal, 9).frame(minHeight: 34)
                    .foregroundStyle(selected ? Color.primary : Color.primary.opacity(0.76))
                    .background {
                        RoundedRectangle(cornerRadius: 8)
                            .fill(selected ? warm.opacity(scheme == .dark ? 0.16 : 0.12) : Color.clear)
                    }
                    .overlay(alignment: .leading) {
                        if selected {
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(warm)
                                .frame(width: 3, height: 16)
                                .padding(.leading, 2)
                        }
                    }
                    .contentShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.bottom, 2)
                .accessibilityLabel(selected ? "\(page.title)，当前页面" : page.title)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
            Spacer(minLength: 12)
            Divider().opacity(0.45).padding(.bottom, 8)
            Button {
                store.page = .settings
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "gearshape")
                        .font(.system(size: 14, weight: store.page == .settings ? .medium : .regular))
                        .foregroundStyle(store.page == .settings ? warm : Color.primary.opacity(0.68))
                        .frame(width: 20, height: 20)
                    Text(L10n.navSettings)
                        .fireSkyFont(size: 12.5, weight: store.page == .settings ? .semibold : .regular)
                    Spacer(minLength: 0)
                    if updater.phase == .available {
                        Circle().fill(Color.green).frame(width: 7, height: 7)
                            .accessibilityLabel("发现新版本 \(updater.info?.version ?? "")")
                    }
                }
                .padding(.horizontal, 9).frame(minHeight: 34)
                .foregroundStyle(store.page == .settings ? Color.primary : Color.primary.opacity(0.76))
                .background {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(store.page == .settings ? warm.opacity(scheme == .dark ? 0.16 : 0.12) : Color.clear)
                }
                .overlay(alignment: .leading) {
                    if store.page == .settings {
                        RoundedRectangle(cornerRadius: 1.5)
                            .fill(warm)
                            .frame(width: 3, height: 16)
                            .padding(.leading, 2)
                    }
                }
                .contentShape(RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .accessibilityLabel(store.page == .settings
                                ? L10n.text("设置，当前页面", "Settings, current page")
                                : L10n.navSettings)
            .accessibilityAddTraits(store.page == .settings ? .isSelected : [])
        }
        .padding(.horizontal, 10).padding(.top, 34).padding(.bottom, 12)
        .frame(width: dynamicTypeSize.isAccessibilitySize ? 236 : 176)
        .fireSkySurface(role: .navigation)
    }

    private var appearanceIcon: String {
        switch store.appearance {
        case "light": return "sun.max"
        case "dark": return "moon.fill"
        default: return "circle.lefthalf.filled"
        }
    }

    private var appearanceHelpText: String {
        switch store.appearance {
        case "light": return L10n.text("当前：浅色外观（点击切换深色）", "Light appearance (click for Dark)")
        case "dark": return L10n.text("当前：深色外观（点击跟随系统）", "Dark appearance (click for System)")
        default: return L10n.text("当前：跟随系统外观（点击切换浅色）", "System appearance (click for Light)")
        }
    }

    private var topbar: some View {
        HStack(alignment: .center, spacing: 10) {
            Button {
                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) {
                    sidebarState.column = sidebarState.visible ? .detailOnly : .all
                }
            } label: {
                Image(systemName: "sidebar.left")
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: 26, height: 26)
                    .background(Color.primary.opacity(scheme == .dark ? 0.06 : 0.045),
                                in: RoundedRectangle(cornerRadius: 6))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help(L10n.t(sidebarState.visible ? "隐藏侧栏" : "显示侧栏"))
            .accessibilityLabel(L10n.t(sidebarState.visible ? "隐藏侧栏" : "显示侧栏"))

            VStack(alignment: .leading, spacing: 1) {
                Text(store.page.title).fireSkyFont(size: 16, weight: .semibold, relativeTo: .title2)
                // 副标题只在该页面有地点语义时显示；管理页不借用预测地点上下文。
                if store.page.showsLocationSubtitle {
                    Text(store.page == .regional ? store.regionalTitle
                         : String(format: "%@ · %.4f° N, %.4f° E", store.name, store.lat, store.lon))
                        .fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer(minLength: 8)
            // UX #5：任务运行中切页后，顶栏仍能看到进度与取消入口。
            if store.busy {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(store.status).fireSkyFont(size: 11, relativeTo: .caption)
                        .foregroundStyle(.secondary).lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true).frame(maxWidth: 220)
                    Button { store.cancel() } label: { Image(systemName: "xmark.circle") }
                        .buttonStyle(.borderless)
                        .help(L10n.t("取消当前任务"))
                        .accessibilityLabel(L10n.t("取消当前任务"))
                }.workChip()
            }
            if updater.phase == .available, let version = updater.info?.version {
                Button {
                    store.showUpdateSheet = true
                } label: {
                    HStack(spacing: 5) {
                        Circle().fill(Color.green).frame(width: 6, height: 6)
                        Text(L10n.text("新版本 \(version)", "Update \(version)"))
                            .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                    }
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(Color.green.opacity(scheme == .dark ? 0.16 : 0.12), in: Capsule())
                    .overlay { Capsule().strokeBorder(Color.green.opacity(0.32), lineWidth: 1) }
                }
                .buttonStyle(.plain)
                .help(L10n.text("查看新版本详情并更新", "View update details"))
                .accessibilityLabel(L10n.text("发现新版本 \(version)，打开更新说明", "New version \(version) available, open update details"))
            }
            if !store.online {
                Button {
                    Task { await store.connect() }
                } label: {
                    HStack(spacing: 5) {
                        Circle().fill(Color.orange).frame(width: 6, height: 6)
                        Text(L10n.t("引擎未连接"))
                            .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                    }
                    .padding(.horizontal, 9).padding(.vertical, 5)
                    .background(Color.orange.opacity(0.14), in: Capsule())
                }
                .buttonStyle(.plain)
                .help(L10n.t("点击重新连接引擎"))
                .accessibilityLabel(L10n.t("引擎未连接，点击重新连接"))
            }
            Button {
                store.cycleAppearance()
            } label: {
                Image(systemName: appearanceIcon)
                    .font(.system(size: 12.5, weight: .medium))
                    .frame(width: 26, height: 26)
                    .background(Color.primary.opacity(scheme == .dark ? 0.06 : 0.045),
                                in: RoundedRectangle(cornerRadius: 6))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help(appearanceHelpText)
            .accessibilityLabel(appearanceHelpText)
        }
        .padding(.leading, sidebarState.visible ? 14 : 74)
        .padding(.trailing, 14)
        .padding(.vertical, 6)
        .frame(minHeight: 46)
        .fireSkySurface(role: .navigation)
    }

    private var startAccessibilityHint: String {
        if store.busy { return L10n.t("当前有任务正在进行，完成或取消后可开始新预测。") }
        if !store.online { return L10n.t("引擎未连接，请重新连接后再开始。") }
        return L10n.t("先预检本地数据，确认后开始预测。")
    }

    private var workbench: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        WorkLocationView()
                        WorkParametersView()
                        workbenchActions
                        workbenchResults
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                }
                .defaultScrollAnchor(.top)
                .scrollIndicators(.hidden)
            } else {
                HStack(alignment: .top, spacing: 12) {
                    workbenchControls
                    workbenchResults
                }
                .padding(.leading, 14).padding(.trailing, 14)
                .padding(.top, 10).padding(.bottom, 12)
            }
        }
    }

    private var workbenchControls: some View {
        VStack(spacing: 10) {
            ScrollView {
                VStack(spacing: 10) {
                    WorkLocationView()
                    WorkParametersView()
                }.padding(.trailing, 2)
            }
            .defaultScrollAnchor(.top)
            .scrollIndicators(.hidden)
            workbenchActions
        }
        .frame(width: 274)
    }

    private var workbenchActions: some View {
        let staleHighlight = store.resultParametersStale && !store.busy && store.online
        return VStack(spacing: 10) {
            Button { store.beginCheck() } label: {
                HStack(spacing: 6) {
                    if staleHighlight {
                        Circle()
                            .fill(scheme == .dark ? Color(0x14, 0x14, 0x14) : Color.white)
                            .frame(width: 6, height: 6)
                    }
                    Text(staleHighlight
                         ? L10n.text(store.page == .nowcast ? "按新参数开始实况 ●" : "按新参数预测 ●",
                                     store.page == .nowcast ? "Start Nowcast ●" : "Re-run Forecast ●")
                         : L10n.text(store.page == .nowcast ? "开始实况分析" : "开始预测",
                                     store.page == .nowcast ? "Start Nowcast" : "Start Forecast"))
                }
                .fireSkyFont(size: 12, weight: .semibold)
                .foregroundStyle(scheme == .dark ? Color(0x14, 0x14, 0x14) : Color.white)
                .frame(maxWidth: .infinity).padding(.vertical, 9)
                .background(FSDesign.accentGradient(dark: scheme == .dark),
                            in: RoundedRectangle(cornerRadius: 8))
                .overlay {
                    if staleHighlight {
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(warm, lineWidth: 1.5)
                    }
                }
            }
            .buttonStyle(.plain)
            .opacity(store.busy || !store.online ? 0.45 : 1)
            .disabled(store.busy || !store.online)
            .help(store.busy
                  ? L10n.tf("当前有任务正在进行（%@），完成或取消后可开始新预测。", store.status)
                  : !store.online ? L10n.t("引擎未连接，请先在左下角重新连接。")
                  : L10n.t("先预检本地数据，确认后开始预测。"))
            .accessibilityHint(startAccessibilityHint)
            if store.page == .nowcast {
                Button(L10n.t("预热卫星与数据"), systemImage: "arrow.down.to.line") {
                    store.beginCheck()
                }
                .buttonStyle(.borderless).fireSkyFont(size: 10, relativeTo: .caption)
                .disabled(store.busy || !store.online)
            }
            if store.busy {
                ProgressView(value: store.percent, total: 100)
                HStack {
                    Text(store.status).fireSkyFont(size: 11, relativeTo: .caption)
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button { store.cancel() } label: { Image(systemName: "xmark") }
                        .help(L10n.t("取消任务"))
                        .accessibilityLabel(L10n.t("取消任务"))
                }
            }
            if !store.online && !store.error.isEmpty {
                Label(L10n.t("引擎未连接"), systemImage: "exclamationmark.triangle")
                    .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.red)
                Text(store.error).fireSkyFont(size: 11, relativeTo: .caption)
                    .foregroundStyle(.red.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                Button(L10n.t("重新连接")) { Task { await store.connect() } }
                    .buttonStyle(.borderless)
            }
        }
    }

    private var workbenchResults: some View {
        Group {
            if store.page == .nowcast {
                WorkNowcastResultsView()
            } else {
                WorkResultsView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}

struct WorkLocationView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(L10n.t(store.page == .regional ? "预测区域" : "观测点"))
                    .fireSkyFont(size: 12.5, weight: .semibold)
                Spacer()
                if store.page != .regional {
                    WorkSegments(options: ["地图", "收藏"], selection: $store.locationTab)
                        .frame(width: dynamicTypeSize.isAccessibilitySize ? 180 : 104)
                    if store.locationTab == "收藏" {
                        Menu {
                            if store.favorites.isEmpty {
                                Text(L10n.t("还没有收藏位置"))
                            } else {
                                ForEach(Array(store.favorites.enumerated()), id: \.offset) { _, item in
                                    Button(role: .destructive) {
                                        store.removeFavorite(item)
                                    } label: {
                                        Label(L10n.text("删除 \(item["name"] as? String ?? "收藏位置")",
                                                        "Remove \(item["name"] as? String ?? "favorite")"), systemImage: "trash")
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
                        .help(L10n.t("管理收藏位置"))
                        .accessibilityLabel(L10n.t("管理收藏位置"))
                    }
                }
            }
            HStack(spacing: 5) {
                TextField(L10n.text("搜索地名（如西湖、杭州）", "Search place (e.g. West Lake)"), text: $store.query).onSubmit { store.search() }
                    .textFieldStyle(.roundedBorder).controlSize(.regular)
                    .font(.system(size: 11))
                Button { store.search() } label: {
                    if store.searching { ProgressView().controlSize(.mini).frame(width: 14, height: 14) }
                    else { Image(systemName: "magnifyingglass").frame(width: 14, height: 14) }
                }
                    .help(L10n.t("搜索位置")).accessibilityLabel(L10n.t("搜索位置"))
                    .controlSize(.regular).disabled(store.searching)
                Button { store.locate() } label: {
                    if store.locating { ProgressView().controlSize(.mini).frame(width: 14, height: 14) }
                    else { Image(systemName: "location").frame(width: 14, height: 14) }
                }.help(L10n.t("获取当前位置")).accessibilityLabel(L10n.t("获取当前位置"))
                    .controlSize(.regular).disabled(store.locating)
            }
            if store.page == .regional {
                VStack(spacing: 6) {
                    coordinate("西界", $store.west)
                    coordinate("东界", $store.east)
                    coordinate("南界", $store.south)
                    coordinate("北界", $store.north)
                    Text("0.25° × 0.25°").foregroundStyle(.secondary).frame(maxWidth: .infinity)
                }
            } else if store.locationTab == "地图" {
                ForecastMap(store: store, mini: true).frame(height: 136)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            } else {
                // UX #7：收藏可删除（右键菜单），空收藏给出引导而不是空白。
                ScrollView {
                    if store.favorites.isEmpty {
                        VStack(spacing: 6) {
                            Image(systemName: "star").foregroundStyle(.secondary)
                            Text(L10n.t("还没有收藏位置"))
                                .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                            Text(L10n.t("搜索或在地图上选好地点后，点击下方「收藏当前点」。"))
                                .fireSkyFont(size: 11, relativeTo: .caption).foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                        }.frame(maxWidth: .infinity, minHeight: 130)
                    } else {
                        VStack(spacing: 6) {
                            ForEach(Array(store.favorites.enumerated()), id: \.offset) { _, item in
                                let favName = item["name"] as? String ?? "收藏位置"
                                let favNote = (item["note"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                                Button {
                                    store.applyLocation(item)
                                } label: {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(favName)
                                            .fireSkyFont(size: 12, weight: .medium)
                                        if !favNote.isEmpty {
                                            Text(favNote)
                                                .fireSkyFont(size: 10, relativeTo: .caption)
                                                .foregroundStyle(.secondary)
                                                .lineLimit(1)
                                        }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .buttonStyle(.borderless)
                                .contextMenu {
                                    Button(L10n.t("删除收藏"), role: .destructive) { store.removeFavorite(item) }
                                }
                                .help(L10n.t("点击切换到此位置；右键可删除收藏"))
                            }
                        }.padding(6)
                    }
                }.frame(height: 144)
            }
            HStack(spacing: 7) {
                Circle().fill(store.metadataReady ? warm : Color.orange).frame(width: 7, height: 7)
                VStack(alignment: .leading, spacing: 2) {
                    Text(store.name).fireSkyFont(size: 12.5, weight: .semibold)
                    Text(String(format: "%.4f° N, %.4f° E", store.lat, store.lon))
                        .fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary)
                }
            }
            TextField(L10n.text("机位备注（可选，如：屋顶西南视角）", "Spot note (optional, e.g. rooftop SW view)"), text: $store.spotNote)
                .textFieldStyle(.roundedBorder)
                .controlSize(.small)
                .font(.system(size: 11))
            Text(store.metadataReady
                 ? L10n.tf("位置已确认 · 海拔 %d m · %@", Int(store.elevation), store.timezone)
                 : L10n.t("位置待确认 · 展开「编辑坐标」获取海拔与时区"))
                .fireSkyFont(size: 10.5, relativeTo: .caption)
                .foregroundStyle(store.metadataReady ? Color.secondary : Color.orange)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .help(store.metadataReady
                      ? L10n.tf("海拔来源设置：%@；实际来源：%@", store.elevationSourceLabel, store.elevationSourceActualLabel)
                      : L10n.t("确认海拔与时区后才能收藏该点并开始预测。"))
                .accessibilityLabel(store.metadataReady
                                    ? L10n.tf("位置已确认，海拔 %d 米，时区 %@", Int(store.elevation), store.timezone)
                                    : L10n.t("位置待确认，请展开编辑坐标获取海拔与时区"))
            HStack {
                Button(L10n.t("收藏当前点"), systemImage: "star") { store.starLocation() }
                Spacer()
                Button(L10n.t("大地图"), systemImage: "arrow.up.left.and.arrow.down.right") { store.showMapPicker = true }
            }.buttonStyle(.borderless).fireSkyFont(size: 11, relativeTo: .caption)
        }.padding(12).workSurface()
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
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(L10n.t("观测参数")).fireSkyFont(size: 12.5, weight: .semibold)
                Spacer()
                Button(L10n.t("重置")) { store.chooseDate("今天"); store.phenomenon = "dusk" }
                    .buttonStyle(.borderless).fireSkyFont(size: 11, relativeTo: .caption)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(L10n.t("目标日期")).fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary)
                WorkSegments(options: ["今天", "明天", "自定义"],
                             selection: Binding(get: { store.dateTab }, set: { store.chooseDate($0) }))
            }
            if store.dateTab == "自定义" {
                WorkDateButton()
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(L10n.t("观测现象")).fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary)
                WorkSegments(options: ["dawn", "dusk"], titles: ["朝霞", "晚霞"],
                             selection: $store.phenomenon, accented: true)
            }
            Button(L10n.t("编辑坐标"), systemImage: "slider.horizontal.3") {
                store.editingCoordinates.toggle()
            }
            .buttonStyle(.borderless).fireSkyFont(size: 11, relativeTo: .caption)
            .frame(maxWidth: .infinity).padding(.vertical, 2)
            if store.editingCoordinates {
                VStack(spacing: 6) {
                    TextField(L10n.t("名称"), text: $store.name)
                    number(L10n.t("纬度"), $store.lat)
                    number(L10n.t("经度"), $store.lon)
                    number("海拔 (m)", $store.elevation)
                    HStack {
                        Text(L10n.t("时区")).foregroundStyle(.secondary)
                        Spacer()
                        Text(store.timezone).monospaced().fireSkyFont(size: 12)
                    }
                    Text(L10n.t("时区按经纬度自动计算"))
                        .fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary)
                    Button(L10n.t("获取海拔与时区")) { store.lookupMetadata() }
                        .buttonStyle(.borderless).fireSkyFont(size: 11.5)
                    Toggle(L10n.t("已确认海拔与时区"), isOn: $store.metadataReady)
                        .fireSkyFont(size: 11, relativeTo: .caption)
                }.textFieldStyle(.roundedBorder)
            }
            let pageError = store.errorText(for: store.page)
            if !pageError.isEmpty {
                HStack(alignment: .top, spacing: 6) {
                    Text(pageError).font(.system(size: 11)).foregroundStyle(.red)
                        .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    Spacer(minLength: 4)
                    Button { store.clearError() } label: { Image(systemName: "xmark") }
                        .buttonStyle(.borderless).font(.system(size: 10)).foregroundStyle(.secondary)
                        .help(L10n.t("关闭"))
                }
            }
        }.padding(12).workSurface()
    }
    private func number(_ title: String, _ value: Binding<Double>) -> some View {
        HStack {
            Text(title).fireSkyFont(size: 12).foregroundStyle(.secondary)
            Spacer()
            TextField(title, value: value, format: .number).labelsHidden().frame(width: 130)
        }
    }
}

struct WorkDateButton: View {
    @EnvironmentObject private var store: Store
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
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
            }
            .fireSkyFont(size: 12)
            .padding(.horizontal, 11).padding(.vertical, 6).frame(minHeight: 34)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).help(L10n.t("选择目标日期"))
            .accessibilityLabel(L10n.tf("目标日期 %@", store.targetDateLabel))
            .popover(isPresented: $store.showDatePicker, arrowEdge: .bottom) {
                WorkCalendarView().environment(\.dynamicTypeSize, dynamicTypeSize)
            }
    }
}

private struct WorkCalendarView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    private var month: ObservationMonth { ObservationMonth(containing: store.calendarMonth, timezone: store.timezone) }
    private var columns: [GridItem] { Array(repeating: GridItem(.flexible(), spacing: 4), count: 7) }
    private var dayCellSize: CGFloat { dynamicTypeSize.isAccessibilitySize ? 44 : 30 }
    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text(month.title).fireSkyFont(size: 13, weight: .semibold, relativeTo: .headline)
                Spacer()
                Button { store.calendarMonth = month.moved(-1) } label: {
                    Image(systemName: "chevron.left")
                        .frame(width: dynamicTypeSize.isAccessibilitySize ? 38 : 24,
                               height: dynamicTypeSize.isAccessibilitySize ? 38 : 24)
                }.help(L10n.t("上个月")).accessibilityLabel(L10n.t("上个月"))
                Button { store.calendarMonth = month.moved(1) } label: {
                    Image(systemName: "chevron.right")
                        .frame(width: dynamicTypeSize.isAccessibilitySize ? 38 : 24,
                               height: dynamicTypeSize.isAccessibilitySize ? 38 : 24)
                }.help(L10n.t("下个月")).accessibilityLabel(L10n.t("下个月"))
            }.buttonStyle(.plain)
            LazyVGrid(columns: columns, spacing: 4) {
                ForEach(month.weekdaySymbols, id: \.self) { day in
                    Text(day).fireSkyFont(size: 10, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, minHeight: dynamicTypeSize.isAccessibilitySize ? 38 : 22)
                }
                ForEach(month.days, id: \.self) { day in
                    let selected = month.calendar.isDate(day, inSameDayAs: store.date)
                    let current = month.calendar.isDate(day, equalTo: month.start, toGranularity: .month)
                    let today = month.calendar.isDate(day, inSameDayAs: .now)
                    Button { select(day) } label: {
                        Text("\(month.calendar.component(.day, from: day))")
                            .fireSkyFont(size: 11, weight: selected ? .semibold : .regular, relativeTo: .caption)
                            .monospacedDigit()
                            .foregroundStyle(selected ? (scheme == .dark ? Color.black : Color.white)
                                             : current ? Color.primary : Color.secondary.opacity(0.55))
                            .frame(maxWidth: .infinity).frame(height: dayCellSize)
                            .background(selected ? warm : .clear, in: Circle())
                            .overlay { Circle().stroke(today && !selected ? warm.opacity(0.7) : .clear, lineWidth: 1) }
                            .contentShape(Circle())
                    }.buttonStyle(.plain).accessibilityLabel(ObservationDate.label(day, timezone: store.timezone))
                        .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            Divider()
            HStack {
                Button(L10n.t("今天")) { select(.now) }.buttonStyle(.plain).foregroundStyle(warm)
                Spacer()
                Button { store.showDatePicker = false } label: {
                    Image(systemName: "xmark").frame(width: 24, height: 24)
                }.buttonStyle(.plain).help(L10n.t("关闭日历"))
                    .accessibilityLabel(L10n.t("关闭日历")).keyboardShortcut(.cancelAction)
            }.fireSkyFont(size: 11, relativeTo: .caption)
        }.padding(14).frame(width: dynamicTypeSize.isAccessibilitySize ? 380 : 262)
            .tint(warm).fireSkySurface(role: .overlay, cornerRadius: 10)
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
                Text(L10n.t("参数已变更 · 当前结果对应旧参数"))
                    .font(.system(size: 10, weight: .medium)).foregroundStyle(.orange)
                    .help(store.resultParamsSummary)
                Spacer(minLength: 4)
                Button(L10n.t("按新参数重算")) { store.beginCheck() }
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

struct WorkAnalysisTabs: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var tabIndicator

    private var useGlass: Bool { !reduceTransparency && contrast != .increased }

    var body: some View {
        HStack(spacing: 3) {
            ForEach(["概览", "大气剖面", "逐时台账"], id: \.self) { tab in
                let selected = store.resultTab == tab
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { store.resultTab = tab }
                } label: {
                    Text(L10n.t(tab))
                        .fireSkyFont(size: 11.5, weight: selected ? .semibold : .regular)
                        .foregroundStyle(selected ? Color.primary : Color.secondary)
                        .padding(.horizontal, 13).padding(.vertical, 5.5)
                        .background {
                            if selected {
                                Capsule()
                                    .fill(scheme == .dark ? Color.white.opacity(0.16) : Color.white.opacity(0.92))
                                    .shadow(color: Color.black.opacity(scheme == .dark ? 0.25 : 0.08),
                                            radius: 3, y: 1)
                                    .overlay {
                                        Capsule().strokeBorder(
                                            scheme == .dark ? Color.white.opacity(0.18) : Color.black.opacity(0.06),
                                            lineWidth: 0.5
                                        )
                                    }
                                    .matchedGeometryEffect(id: "active-tab-pill", in: tabIndicator)
                            }
                        }
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .padding(3)
        .background {
            if useGlass {
                #if compiler(>=6.2)
                if #available(macOS 26.0, *) {
                    Color.clear.glassEffect(.regular, in: .capsule)
                } else {
                    Capsule().fill(.ultraThinMaterial)
                }
                #else
                Capsule().fill(.ultraThinMaterial)
                #endif
            } else {
                Capsule().fill(scheme == .dark ? Color(0x16, 0x1E, 0x2E) : Color(0xEA, 0xF0, 0xF6))
            }
        }
        .overlay {
            Capsule().strokeBorder(
                scheme == .dark ? Color.white.opacity(0.10) : Color.black.opacity(0.06),
                lineWidth: 0.5
            )
        }
    }
}

struct WorkAODValue: View {
    let summary: AODSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(summary.source).font(.system(size: 9.5, weight: .medium)).foregroundStyle(coolAccent)
            Text(summary.value).font(.system(size: 16, weight: .semibold)).monospacedDigit()
                .foregroundStyle(summary.available ? Color.primary : Color.secondary)
                .lineLimit(1).minimumScaleFactor(0.8)
        }.frame(maxWidth: .infinity, alignment: .leading).help(summary.detail)
    }
}

struct WorkSharedDiagnosis: View {
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

struct WorkDiagnosticGroup<Content: View>: View {
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

func diagnosticRow(_ title: String, _ value: Any?, digits: Int = 2,
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

private struct WorkSurface: ViewModifier {
    var emphasized = false
    func body(content: Content) -> some View {
        // 内容表面退出通用玻璃：正文、指标和图表保持稳定底色与清晰边界。
        content.fireSkySurface(role: .content, emphasized: emphasized)
    }
}

struct WorkSegments: View {
    let options: [String]
    var titles: [String]? = nil
    @Binding var selection: String
    var accented = false
    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast
    var body: some View {
        HStack(spacing: 2) {
            ForEach(Array(options.enumerated()), id: \.element) { index, value in
                let selected = selection == value
                let label = titles?[index] ?? value
                Button { selection = value } label: {
                    Text(L10n.t(label))
                        .fireSkyFont(size: 11, weight: selected ? .semibold : .regular,
                                     relativeTo: .caption)
                        .foregroundStyle(selected && accented
                                          ? (scheme == .dark ? warm : Color(0xB3, 0x3F, 0x23))
                                          : selected ? Color.primary : Color.secondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity).padding(.vertical, 5)
                        .frame(minHeight: 26)
                        .background {
                            if selected {
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(accented
                                          ? warm.opacity(scheme == .dark ? 0.20 : 0.14)
                                          : scheme == .dark ? Color.white.opacity(0.16) : Color.white.opacity(0.94))
                                    .shadow(color: Color.black.opacity(scheme == .dark ? 0.25 : 0.08),
                                            radius: 2.5, y: 1)
                            }
                        }
                        .overlay {
                            if selected && (accented || contrast == .increased) {
                                RoundedRectangle(cornerRadius: 6)
                                    .strokeBorder(
                                        accented ? warm.opacity(0.32) : Color.primary.opacity(0.35),
                                        lineWidth: 0.75
                                    )
                            }
                        }
                        .contentShape(Rectangle())
                }.buttonStyle(.plain)
                    .accessibilityLabel(L10n.text(
                        "\(label)\(selected ? "，已选中" : "")",
                        "\(L10n.t(label))\(selected ? ", selected" : "")"
                    ))
                    .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }.padding(3)
            .background(Color.primary.opacity(scheme == .dark ? 0.075 : 0.05), in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.primary.opacity(contrast == .increased ? 0.35 : 0.07),
                                  lineWidth: contrast == .increased ? 1 : 0.5)
            }
    }
}

extension View {
    func workSurface(emphasized: Bool = false) -> some View { modifier(WorkSurface(emphasized: emphasized)) }
    func workChip() -> some View {
        padding(.horizontal, 6).padding(.vertical, 2)
            .background(.quaternary.opacity(0.45), in: RoundedRectangle(cornerRadius: 5))
            .overlay { RoundedRectangle(cornerRadius: 5).stroke(.secondary.opacity(0.22), lineWidth: 1) }
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

func diagnosticValue(_ value: Any?, digits: Int = 2, suffix: String = "",
                             percentValue: Bool = false) -> String {
    ReportDiagnosticValue.text(value, digits: digits, suffix: suffix, percent: percentValue) { sceneLabel($0) }
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
    guard let number = value as? NSNumber else { return "—" }
    return String(format: "%.0f%%", number.doubleValue * 100)
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

/// Percent text for 0–1 ratio fields; missing values show the missing mark, never 0%.
func ratioText(_ value: Any?) -> String {
    guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
    return String(format: "%.0f%%", number.doubleValue * 100)
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
    case "coverage": return "可烧云占比"
    case "ratio": return "可烧比例"
    default: return "回程透过率"
    }
}
