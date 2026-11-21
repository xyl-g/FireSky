import SwiftUI

/// FireSkyMobile App 壳（FireSkyApp.swift 迁移）。
/// AppKit WindowGroup + CommandMenu/快捷键 + 多窗口 → 单窗口 TabView 导航；
/// 快捷键（⌘1 单点 / ⌘4 数据 / ⌘, 设置）语义降级为底部标签页切换；
/// applicationWillTerminate → scenePhase 语义（见 onChange）。
@main
struct FireSkyMobileApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var store = Store()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .environment(\.locale, Locale(identifier: L10n.effective == .english ? "en_US" : "zh_CN"))
                .tint(warm)
                .preferredColorScheme(store.appearance == "dark" ? .dark : store.appearance == "light" ? .light : nil)
        }
        .onChange(of: scenePhase) { phase in
            // iOS 进程随时可能被系统回收，引擎通道随进程消亡；
            // 回前台若已断线则自动重连（对应桌面「重新连接」入口）。
            if phase == .active, !store.online, !store.busy, !store.probingDownloads {
                Task { await store.connect() }
            }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: Store
    @State private var tab: Page = .point
    /// v1.9.5 i18n：观察语言偏好变化——改动触发整棵树重建，L10n 文案随之刷新。
    @AppStorage("appLanguage") private var appLanguage = "system"

    var body: some View {
        TabView(selection: $tab) {
            PointWorkbenchView()
                .tabItem { Label(L10n.navPoint, systemImage: "location.circle") }
                .tag(Page.point)
            InventoryView()
                .tabItem { Label(L10n.navData, systemImage: "externaldrive") }
                .tag(Page.data)
            HistoryView()
                .tabItem { Label(L10n.navHistory, systemImage: "clock.arrow.circlepath") }
                .tag(Page.history)
            SettingsView()
                .tabItem { Label(L10n.navSettings, systemImage: "gearshape") }
                .tag(Page.settings)
        }
        .environment(\.timeZone, TimeZone(identifier: store.timezone) ?? .current)
        .onAppear { tab = store.page }
        .onChange(of: tab) { newTab in
            // 对应桌面侧栏「if !store.busy { store.page = page }」：
            // 任务进行中保持当前页。
            if store.busy {
                tab = store.page
            } else {
                store.page = newTab
                if newTab == .data {
                    Task {
                        await store.loadInventory()
                        store.loadTerrain()
                    }
                }
                if newTab == .history { store.loadTaskHistory() }
                if newTab == .settings { Task { await store.refreshCAMSConfiguration() } }
            }
        }
        .onChange(of: store.page) { newPage in
            if tab != newPage { tab = newPage }
        }
        .sheet(isPresented: $store.showPreflight, onDismiss: {
            if store.checking || store.session != nil && !store.busy { store.closePreflight() }
        }) { PreflightView().tint(warm).interactiveDismissDisabled(store.busy) }
        .sheet(isPresented: $store.showMapPicker) { MapPickerSheet() }
        .alert("需要配置 CAMS Key", isPresented: $store.showCAMSReminder) {
            Button("查看教程并配置") { store.openCAMSSetup() }
            Button("稍后", role: .cancel) {}
        } message: {
            Text("\(store.camsConfigurationDescription)\n已有本地数据仍可使用。")
        }
        .task { await store.connect() }
    }
}
