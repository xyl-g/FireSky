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
                Button("单点预测") { if !store.busy { store.page = .point } }
                    .keyboardShortcut("1")
                Button("区域预测") { if !store.busy { store.page = .regional } }
                    .keyboardShortcut("2")
                Button("卫星实况") { if !store.busy { store.page = .nowcast } }
                    .keyboardShortcut("3")
                Button("数据与下载") { if !store.busy { store.page = .data } }
                    .keyboardShortcut("4")
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
