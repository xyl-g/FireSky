import AppKit
import SwiftUI
import UserNotifications

@main
struct FireSkyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = Store()
    @StateObject private var updater = Updater()
    @StateObject private var reminders = ReminderCenter()
    var body: some Scene {
        WindowGroup("FireSky") {
            WorkbenchRootView().environmentObject(store)
                .environmentObject(updater)
                .environmentObject(reminders)
                .environment(\.locale, Locale(identifier: "zh_CN"))
                .onAppear {
                    delegate.store = store
                    delegate.scheduleSnapshot()
                }
        }
        .defaultSize(width: 1440, height: 900)
        .commands {
            CommandGroup(replacing: .newItem) {
                // UX #5：⌘1-5 切页不再被全局 busy 锁死；任务继续，顶栏可见进度。
                Button("单点预测") { store.page = .point }
                    .keyboardShortcut("1")
                Button("区域预测") { store.page = .regional }
                    .keyboardShortcut("2")
                Button("卫星实况") { store.page = .nowcast }
                    .keyboardShortcut("3")
                Button("数据管理") { store.page = .data }
                    .keyboardShortcut("4")
                Button("任务历史") { store.page = .history }
                    .keyboardShortcut("5")
            }
            CommandGroup(replacing: .appSettings) {
                Button("设置…") { store.page = .settings }.keyboardShortcut(",")
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    weak var store: Store?
    private var scheduled = false
    /// v1.9.7 退出守卫防循环：用户本轮已确认过「继续运行」的时间戳——
    /// 系统注销/关机会反复发起 terminate，不再每次都弹确认框（5s 内
    /// 只问一次）；用户选择「仍要退出」后置位强制标志，任何后续
    /// terminate 直接放行，杜绝确认框循环。
    private var lastConfirmTime: Date?
    private var forceTerminate = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.activate(ignoringOtherApps: true)
        UNUserNotificationCenter.current().delegate = self
        // Intel CI cold start: SwiftUI .onAppear can fail to fire on older
        // macOS runner images; schedule here too (idempotent via `scheduled`).
        scheduleSnapshot()
        // v1.9.7 启动兜底：8s 后仍无可见窗口则强制前置主窗口——修复极端
        // 情况下“界面没出来”（应用在跑、窗口不可见）导致的观感卡死。
        // v1.9.8 走查：快照/夹具运行（CI 冒烟）延迟出窗是预期行为，跳过
        // 兜底，避免与截图流程竞争窗口状态。
        if ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] == nil {
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                guard let self,
                      NSApp.windows.first(where: { $0.isVisible && !$0.isSheet }) == nil
                else { return }
                NSApp.activate(ignoringOtherApps: true)
                NSApp.windows
                    .first(where: { !$0.isSheet && ($0 is NSPanel || $0.isKeyWindow || $0.isMainWindow || !($0 is NSPanel)) })?
                    .makeKeyAndOrderFront(nil)
            }
        }
        // 从挂载的磁盘映像（DMG）启动时提供安装到「应用程序」的引导；
        // 快照/夹具与常规路径启动不受影响。
        if ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] == nil,
           Bundle.main.bundlePath.hasPrefix("/Volumes/") {
            DispatchQueue.main.async { self.promptInstallToApplications() }
        }
    }

    /// DMG 内双击启动的安装引导：
    /// - 「应用程序」已有相同或更新版本 → 提供打开已装版本；
    /// - 否则提示安装：旧版本移入废纸篓后跨卷拷贝新版本并重新打开。
    /// AppKit 没有 Finder 式跨卷 move API，copyItem + 重新打开是确定性行为；
    /// 无写权限（非管理员）时提示手动拖拽而不是静默失败。
    private func promptInstallToApplications() {
        let fileManager = FileManager.default
        let appsDirectory = fileManager.urls(for: .applicationDirectory, in: .localDomainMask)[0]
        let destination = appsDirectory.appendingPathComponent("FireSky.app")
        let currentVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        let installedVersion = Bundle(url: destination)?.infoDictionary?["CFBundleShortVersionString"] as? String
        if let installed = installedVersion,
               Updater.compareVersions(installed, currentVersion) != .orderedAscending {
            let alert = NSAlert()
            alert.messageText = "从磁盘映像启动了 FireSky"
            alert.informativeText = "「应用程序」中已安装相同或更新版本（\(installed)）。打开已安装版本并退出这份磁盘映像副本？"
            alert.addButton(withTitle: "打开已安装版本")
            alert.addButton(withTitle: "保持现状")
            if alert.runModal() == .alertFirstButtonReturn {
                NSWorkspace.shared.openApplication(at: destination, configuration: .init())
                NSApp.terminate(nil)
            }
            return
        }
        let alert = NSAlert()
        alert.messageText = "将 FireSky 安装到「应用程序」文件夹？"
        alert.informativeText = "当前应用还在磁盘映像里运行。安装后可从启动台正常打开；设置与已下载数据不受影响。"
        alert.addButton(withTitle: "安装到「应用程序」")
        alert.addButton(withTitle: "保持现状")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        if fileManager.fileExists(atPath: destination.path) {
            // 旧版本先移入废纸篓，避免目标已存在导致拷贝失败。
            try? fileManager.trashItem(at: destination, resultingItemURL: nil)
        }
        do {
            try fileManager.copyItem(at: Bundle.main.bundleURL, to: destination)
            // LaunchServices 会把 openApplication 指向同 bundle id 的现有实例
            // （本进程），因此改用独立接力进程：旧实例退出后 1 秒再打开
            // /Applications 里的新实例，完成「安装并重启」的用户预期。
            let relay = Process()
            relay.executableURL = URL(fileURLWithPath: "/bin/sh")
            relay.arguments = ["-c",
                               "sleep 1; /usr/bin/open \"\(destination.path)\""]
            try relay.run()
            NSApp.terminate(nil)
        } catch {
            let failure = NSAlert()
            failure.messageText = "安装未完成"
            failure.informativeText = "无法拷贝到「应用程序」：\(error.localizedDescription)。可将磁盘映像里的 FireSky 拖到「应用程序」文件夹完成手动安装。"
            failure.runModal()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    /// UX #12：仍有任务在运行时退出需要显式确认；快照/夹具运行直接放行。
    /// v1.9.7：确认过「继续运行」后 5s 内不再重复弹窗（注销/关机重试场景）；
    /// 「仍要退出」后强制放行后续 terminate，修复确认框循环。
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if forceTerminate { return .terminateNow }
        guard let store, !store.isSnapshotRun, store.hasActiveWork else { return .terminateNow }
        if let last = lastConfirmTime, Date().timeIntervalSince(last) < 5 {
            return .terminateCancel
        }
        let alert = NSAlert()
        alert.messageText = "任务仍在运行，确定退出？"
        alert.informativeText = "当前状态：\(store.status)。退出会中断正在进行的预测、下载或同步；已完成的上一份有效结果不会丢失。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "继续运行")
        alert.addButton(withTitle: "仍要退出")
        lastConfirmTime = Date()
        let choice = alert.runModal()
        if choice == .alertSecondButtonReturn {
            forceTerminate = true
            return .terminateNow
        }
        return .terminateCancel
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        if #available(macOS 11.0, *) {
            completionHandler([.banner, .sound])
        } else {
            completionHandler([.alert, .sound])
        }
    }
    func applicationWillTerminate(_ notification: Notification) { store?.shutdown() }
    func scheduleSnapshot() {
        guard !scheduled, let path = ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] else { return }
        scheduled = true
        Task {
            let env = ProcessInfo.processInfo.environment
            if let width = env["FIRESKY_NATIVE_WIDTH"].flatMap(Double.init),
               let height = env["FIRESKY_NATIVE_HEIGHT"].flatMap(Double.init),
               width >= 1120, height >= 720, width <= 2560, height <= 1600 {
                NSApp.windows.first(where: { $0.isVisible })?.setContentSize(NSSize(width: width, height: height))
            }
            try? await Task.sleep(for: .seconds(5))
            if ProcessInfo.processInfo.environment["FIRESKY_NATIVE_PREFLIGHT"] == "1" {
                store?.metadataReady = true
                store?.beginCheck()
                try? await Task.sleep(for: .seconds(4))
            }
            var base = NSApp.windows.first(where: { $0.isVisible })
            if base == nil {
                // Cold-start fallback: order the first window front and recheck.
                NSApp.windows.first?.makeKeyAndOrderFront(nil)
                try? await Task.sleep(for: .seconds(2))
                base = NSApp.windows.first(where: { $0.isVisible })
            }
            guard let base else {
                NSApp.terminate(nil)
                return
            }
            let window = base.attachedSheet ?? base
            guard
                  let view = window.contentView,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
                NSApp.terminate(nil)
                return
            }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if let data = bitmap.representation(using: .png, properties: [:]) {
                try? data.write(to: URL(fileURLWithPath: path))
            }
            if base.attachedSheet != nil {
                store?.closePreflight()
                try? await Task.sleep(for: .milliseconds(250))
            }
            NSApp.terminate(nil)
        }
    }
}
