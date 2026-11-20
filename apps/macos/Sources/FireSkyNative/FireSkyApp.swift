import AppKit
import SwiftUI

@main
struct FireSkyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = Store()
    var body: some Scene {
        WindowGroup("FireSky") {
            WorkbenchRootView().environmentObject(store)
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
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var store: Store?
    private var scheduled = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.activate(ignoringOtherApps: true)
        // Intel CI cold start: SwiftUI .onAppear can fail to fire on older
        // macOS runner images; schedule here too (idempotent via `scheduled`).
        scheduleSnapshot()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    /// UX #12：仍有任务在运行时退出需要显式确认；快照/夹具运行直接放行。
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let store, !store.isSnapshotRun, store.hasActiveWork else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "任务仍在运行，确定退出？"
        alert.informativeText = "当前状态：\(store.status)。退出会中断正在进行的预测、下载或同步；已完成的上一份有效结果不会丢失。"
        alert.alertStyle = .warning
        alert.addButton(withTitle: "继续运行")
        alert.addButton(withTitle: "仍要退出")
        return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
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
