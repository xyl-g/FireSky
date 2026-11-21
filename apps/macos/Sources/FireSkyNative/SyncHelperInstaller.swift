import Foundation
import Darwin

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

    /// v1.9.8 一致性校验：读取已安装 plist 里烘焙的 workspace 绝对路径，
    /// 与当前数据目录比对——数据目录是系统默认但 FIRESKY_WORKSPACE 覆盖
    /// / 旧版 plist（历史 bug：陈旧路径在测试工作区全源同步）会不一致。
    /// 不一致时后台轮把数据同步到错误目录，必须提示重装。
    static func installedWorkspace(expected: String) -> Bool {
        guard let data = try? Data(contentsOf: plistURL),
              let plist = try? PropertyListSerialization.propertyList(
                  from: data, options: [], format: nil
              ) as? [String: Any],
              let arguments = plist["ProgramArguments"] as? [String] else {
            return true
        }
        guard let index = arguments.firstIndex(of: "--workspace"),
              index + 1 < arguments.count else {
            return true
        }
        return arguments[index + 1] == expected
    }

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
        // Self-heal: an orphaned engine round from a previous install/uninstall
        // would otherwise keep holding sync.lock and make every new round yield.
        terminateHelperRounds(workspace: workspace)
        // Reload cleanly: bootout any previous instance (ignore "not loaded"),
        // then bootstrap the freshly written plist into the user GUI domain.
        _ = launchctl(["bootout", "gui/\(getuid())/\(label)"])
        let result = launchctl(["bootstrap", "gui/\(getuid())", plistURL.path])
        if result.status != 0 {
            throw EngineError(message: "后台助手注册失败：\(result.output.isEmpty ? "launchctl 退出码 \(result.status)" : result.output)")
        }
    }

    static func uninstall(workspace: String) throws {
        _ = launchctl(["bootout", "gui/\(getuid())/\(label)"])
        if FileManager.default.fileExists(atPath: plistURL.path) {
            try FileManager.default.removeItem(at: plistURL)
        }
        // bootout ends the helper main process, but its engine child can survive
        // as an orphan and keep syncing while holding sync.lock. End those rounds
        // so uninstall actually stops background sync.
        terminateHelperRounds(workspace: workspace)
    }

    /// PIDs of the helper's engine rounds parsed from `ps -axo pid=,command=`.
    /// The app itself never invokes `data_sync_tick`, so matching the helper's
    /// `desktop-call --workspace <ws> data_sync_tick` argv identifies only helper
    /// rounds (including orphans left behind by an earlier uninstall).
    static func roundProcessIDs(psOutput: String, workspace: String) -> [pid_t] {
        guard !workspace.isEmpty else { return [] }
        let needle = "--workspace \(workspace) data_sync_tick"
        var pids: [pid_t] = []
        for line in psOutput.split(separator: "\n") {
            let text = String(line)
            guard text.contains("firesky-engine"), text.contains("desktop-call"),
                  text.contains(needle) else { continue }
            let trimmed = text.trimmingCharacters(in: .whitespaces)
            guard let token = trimmed.split(separator: " ").first,
                  let pid = pid_t(token), pid > 1 else { continue }
            pids.append(pid)
        }
        return pids
    }

    /// Stop in-flight helper rounds for this workspace: SIGTERM, then SIGKILL
    /// after a short grace for any survivor. Best-effort; a no-op when none run.
    static func terminateHelperRounds(workspace: String) {
        let listing = run("/bin/ps", ["-axo", "pid=,command="])
        guard listing.status == 0 else { return }
        let pids = roundProcessIDs(psOutput: listing.output, workspace: workspace)
        guard !pids.isEmpty else { return }
        for pid in pids { kill(pid, SIGTERM) }
        for _ in 0..<20 {
            if !pids.contains(where: { kill($0, 0) == 0 }) { return }
            usleep(100_000)
        }
        for pid in pids where kill(pid, 0) == 0 { kill(pid, SIGKILL) }
    }

    /// v1.9.6：清理其它工作区的杂散 helper 轮。陈旧 launchd plist 指向
    /// 旧/临时 workspace（如 /tmp/fs-layout-ws）时，helper 会以全源设置
    /// 在错误工作区长期同步，与真实工作区轮并行抢 NOMADS/ADS 带宽，且
    /// 本工作区的 terminateHelperRounds 按 workspace 过滤杀不到它。
    /// 应用启动时调用一次，把「不是当前 workspace」的 data_sync_tick 轮
    /// 全部终结（SIGTERM→SIGKILL）。正式 helper 由 plist 拉起，被误杀时
    /// 下个 tick 自动重启；只有杂散轮会真正消失。
    static func terminateStaleHelpers(expectedWorkspace: String) {
        guard !expectedWorkspace.isEmpty else { return }
        let listing = run("/bin/ps", ["-axo", "pid=,command="])
        guard listing.status == 0 else { return }
        var pids: [pid_t] = []
        for line in listing.output.split(separator: "\n") {
            let text = String(line)
            guard text.contains("firesky-engine"), text.contains("desktop-call"),
                  text.contains("data_sync_tick"),
                  !text.contains("--workspace \(expectedWorkspace) data_sync_tick") else { continue }
            let trimmed = text.trimmingCharacters(in: .whitespaces)
            guard let token = trimmed.split(separator: " ").first,
                  let pid = pid_t(token), pid > 1 else { continue }
            pids.append(pid)
        }
        guard !pids.isEmpty else { return }
        for pid in pids { kill(pid, SIGTERM) }
        for _ in 0..<20 {
            if !pids.contains(where: { kill($0, 0) == 0 }) { return }
            usleep(100_000)
        }
        for pid in pids where kill(pid, 0) == 0 { kill(pid, SIGKILL) }
    }

    private static func launchctl(_ arguments: [String]) -> (status: Int32, output: String) {
        run("/bin/launchctl", arguments)
    }

    private static func run(_ executable: String, _ arguments: [String]) -> (status: Int32, output: String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
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
