import SwiftUI

/// FireSkyMobile App 壳（FireSkyApp.swift 迁移）。
@main
struct FireSkyMobileApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var store: Store

    init() {
        let store = Store()
        Self.applyUITestLaunchOverrides(to: store)
        _store = StateObject(wrappedValue: store)
    }

    private static func applyUITestLaunchOverrides(to store: Store) {
        let args = ProcessInfo.processInfo.arguments
        guard args.contains("-UITestMockMode") else { return }

        func argValue(_ flag: String) -> String? {
            guard let idx = args.firstIndex(of: flag), args.indices.contains(idx + 1) else { return nil }
            return args[idx + 1]
        }

        if let theme = argValue("-UITestTheme")?.lowercased(),
           ["light", "dark", "system"].contains(theme) {
            store.appearance = theme
        }
        if let pageArg = argValue("-UITestPage")?.lowercased() {
            switch pageArg {
            case "workbench", "point": store.page = .point
            case "data": store.page = .data
            case "history": store.page = .history
            case "settings": store.page = .settings
            default: break
            }
        }
        if let modeArg = argValue("-UITestMode")?.lowercased(),
           ["point", "regional", "nowcast"].contains(modeArg) {
            store.workbenchMode = modeArg
        }
        if let tabArg = argValue("-UITestTab"), !tabArg.isEmpty {
            store.resultTab = tabArg
            store.nowcastResultTab = tabArg
        }
        if let diagArg = argValue("-UITestDiagnostics")?.lowercased() {
            let open = diagArg == "1" || diagArg == "true"
            store.showPointDiagnostics = open && store.activeWorkbenchMode == .point
            store.showNowcastDiagnostics = open && store.activeWorkbenchMode == .nowcast
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .environment(\.locale, store.appLocale)
                .tint(warm)
                .preferredColorScheme(store.appearance == "dark" ? .dark : store.appearance == "light" ? .light : nil)
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active, !store.online, !store.busy, !store.probingDownloads {
                Task { await store.connect() }
            }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: Store
    @State private var tab: Page = .point
    @AppStorage("appLanguage") private var appLanguage = "system"

    var body: some View {
        let _ = appLanguage
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
                if newTab == .settings {
                    Task {
                        await store.refreshCAMSConfiguration()
                        await store.loadRetentionSettings()
                        await store.loadDataSyncSettings()
                    }
                }
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
