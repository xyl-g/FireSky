import Foundation

// SyncHelperInstaller — installs/removes the background data-sync LaunchAgent.
//
// Decision (BACKGROUND_SYNC_PLATFORM_SELECTION): macOS uses a classic launchd
// LaunchAgent so sync keeps running after the app quits. The agent runs the
// bundled headless FireSkySyncHelper on a StartInterval; the engine remains the
// single due/publication/lock authority. The plist bakes in the absolute helper
// path and the user's workspace (the helper cannot derive the workspace from its
// own bundle), so it survives the app being closed or moved between launches.
enum SyncHelperInstaller {
    static let label = "com.firesky.datasync"

    static var plistURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents", isDirectory: true)
            .appendingPathComponent("\(label).plist")
    }

    /// The headless helper shipped inside the app bundle. Nil for source-tree
    /// runs (installation is only meaningful for a bundled app).
    static var helperURL: URL? {
        let candidate = Bundle.main.bundleURL
            .appendingPathComponent("Contents/MacOS/FireSkySyncHelper")
        return FileManager.default.isExecutableFile(atPath: candidate.path) ? candidate : nil
    }

    static var isAvailable: Bool { helperURL != nil }

    static func isInstalled() -> Bool {
        FileManager.default.fileExists(atPath: plistURL.path)
    }

    static func isLoaded() -> Bool {
        let result = launchctl(["print", "gui/\(getuid())/\(label)"])
        return result.status == 0
    }

    static func install(workspace: String, intervalMinutes: Int) throws {
        guard let helper = helperURL else {
            throw EngineError(message: "未找到后台同步助手，请重新安装 FireSky。")
        }
        guard !workspace.isEmpty else {
            throw EngineError(message: "请先选择有效的数据目录。")
        }
        let interval = max(60, (intervalMinutes > 0 ? intervalMinutes : 30) * 60)
        let stateDir = URL(fileURLWithPath: workspace)
            .appendingPathComponent(".firesky/state", isDirectory: true)
        try FileManager.default.createDirectory(at: stateDir, withIntermediateDirectories: true)
        let launchdLog = stateDir.appendingPathComponent("helper-launchd.log").path
        let plist: [String: Any] = [
            "Label": label,
            "ProgramArguments": [helper.path, "--sync-helper"],
            "EnvironmentVariables": ["FIRESKY_WORKSPACE": workspace],
            "StartInterval": interval,
            "RunAtLoad": true,
            "ProcessType": "Background",
            "LowPriorityIO": true,
            "StandardOutPath": launchdLog,
            "StandardErrorPath": launchdLog,
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        let directory = plistURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try data.write(to: plistURL, options: .atomic)
        // Reload cleanly: bootout any previous instance (ignore "not loaded"),
        // then bootstrap the freshly written plist into the user GUI domain.
        _ = launchctl(["bootout", "gui/\(getuid())/\(label)"])
        let result = launchctl(["bootstrap", "gui/\(getuid())", plistURL.path])
        if result.status != 0 {
            throw EngineError(message: "后台助手注册失败：\(result.output.isEmpty ? "launchctl 退出码 \(result.status)" : result.output)")
        }
    }

    static func uninstall() throws {
        _ = launchctl(["bootout", "gui/\(getuid())/\(label)"])
        if FileManager.default.fileExists(atPath: plistURL.path) {
            try FileManager.default.removeItem(at: plistURL)
        }
    }

    private static func launchctl(_ arguments: [String]) -> (status: Int32, output: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
        } catch {
            return (-1, error.localizedDescription)
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return (process.terminationStatus, output)
    }
}