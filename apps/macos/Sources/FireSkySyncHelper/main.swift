import Foundation

// FireSkySyncHelper — headless background data-sync agent.
//
// Installed as a classic launchd LaunchAgent (com.firesky.datasync). Each tick
// it invokes the bundled Rust engine's one-shot `desktop-call data_sync_tick`,
// which judges due (publication-aware) and runs only the due sources under the
// cross-process sync lock. The helper itself holds no sync logic: the engine is
// the single cross-platform authority. It records a status file the app reads
// for "last background run" visibility and posts a user notification when a
// round actually downloaded data or failed.

let helperVersion = "firesky-sync-helper-v1"

func logLine(_ workspace: String, _ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    let line = "[\(stamp)] \(message)\n"
    let dir = URL(fileURLWithPath: workspace).appendingPathComponent(".firesky/state", isDirectory: true)
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let path = dir.appendingPathComponent("helper.log")
    if !FileManager.default.fileExists(atPath: path.path) {
        FileManager.default.createFile(atPath: path.path, contents: nil)
    }
    if let handle = try? FileHandle(forWritingTo: path) {
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data(line.utf8))
    }
    FileHandle.standardError.write(Data(line.utf8))
}

func writeStatus(_ workspace: String, _ status: [String: Any]) {
    let dir = URL(fileURLWithPath: workspace).appendingPathComponent(".firesky/state", isDirectory: true)
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let path = dir.appendingPathComponent("helper-status.json")
    guard let data = try? JSONSerialization.data(withJSONObject: status, options: [.prettyPrinted, .sortedKeys]) else { return }
    try? data.write(to: path, options: .atomic)
}

func notify(_ title: String, _ body: String) {
    // UNUserNotificationCenter is unreliable from a launchd GUI agent context;
    // osascript `display notification` routes through the user's notification
    // center without requiring a bundled app identifier or entitlement.
    let escapedTitle = title.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
    let escapedBody = body.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
    let script = "display notification \"\(escapedBody)\" with title \"\(escapedTitle)\""
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    process.arguments = ["-e", script]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try? process.run()
    process.waitUntilExit()
}

// Resolve the bundle Resources directory. The helper lives at
// FireSky.app/Contents/MacOS/FireSkySyncHelper, so Bundle.main resolves to the
// enclosing .app; fall back to the executable's sibling Resources for dev runs.
func resourcesDirectory() -> URL? {
    if let resources = Bundle.main.resourceURL,
       FileManager.default.fileExists(atPath: resources.appendingPathComponent("firesky-engine").path) {
        return resources
    }
    let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
    let contents = executable.deletingLastPathComponent().deletingLastPathComponent()
    let sibling = contents.appendingPathComponent("Resources", isDirectory: true)
    if FileManager.default.fileExists(atPath: sibling.appendingPathComponent("firesky-engine").path) {
        return sibling
    }
    return Bundle.main.resourceURL
}

func resolveEngine(_ workspace: String, _ resources: URL?) -> String? {
    let env = ProcessInfo.processInfo.environment
    if let explicit = env["FIRESKY_RUST_ENGINE_BIN"], !explicit.isEmpty,
       FileManager.default.isExecutableFile(atPath: explicit) {
        return explicit
    }
    if let resources {
        let bundled = resources.appendingPathComponent("firesky-engine").path
        if FileManager.default.isExecutableFile(atPath: bundled) { return bundled }
    }
    let dev = URL(fileURLWithPath: workspace).appendingPathComponent("engine/target/release/firesky-engine").path
    if FileManager.default.isExecutableFile(atPath: dev) { return dev }
    return nil
}

func engineEnvironment(_ workspace: String, _ resources: URL?) -> [String: String] {
    var env = ProcessInfo.processInfo.environment
    env["FIRESKY_WORKSPACE"] = workspace
    guard let resources else { return env }
    let appData = resources.appendingPathComponent("app-data", isDirectory: true)
    if FileManager.default.fileExists(atPath: appData.path) {
        env["FIRESKY_RESOURCE_DIR"] = appData.path
        env["FIRESKY_REQUIRE_APP_RESOURCES"] = "1"
    }
    let decoder = resources.appendingPathComponent("lib/libeccodes.dylib").path
    if FileManager.default.fileExists(atPath: decoder) {
        env["FIRESKY_ECCODES_LIBRARY"] = decoder
        env["ECCODES_DEFINITION_PATH"] = resources.appendingPathComponent("eccodes/definitions").path
        env["ECCODES_SAMPLES_PATH"] = resources.appendingPathComponent("eccodes/samples").path
    }
    return env
}

let sourceLabels: [(key: String, label: String)] = [("cams", "CAMS"), ("gfs", "GFS"), ("ec", "EC")]

func statusText(_ status: String) -> String {
    switch status {
    case "synced": return "已同步"
    case "current": return "已是最新"
    case "failed": return "失败"
    case "cancelled": return "已取消"
    default: return status.isEmpty ? "无结果" : status
    }
}

// Build a human summary + per-source rows from a tick `round` object.
func summarizeRound(_ round: [String: Any]) -> (summary: String, sources: [String: Any], downloaded: Bool, failed: Bool) {
    let sources = round["sources"] as? [String: Any] ?? [:]
    var parts: [String] = []
    var rows: [String: Any] = [:]
    var downloaded = false
    var failed = false
    for source in sourceLabels {
        guard let row = sources[source.key] as? [String: Any] else { continue }
        let status = row["status"] as? String ?? ""
        let files = (row["files"] as? NSNumber)?.intValue ?? 0
        var detail = "\(source.label) \(statusText(status))"
        if status == "synced", files > 0 { detail += " \(files) 文件" }
        if status == "failed", let errors = row["errors"] as? [String], let first = errors.first, !first.isEmpty {
            detail += "（\(first)）"
        }
        parts.append(detail)
        rows[source.key] = ["status": status, "files": files]
        if status == "synced" { downloaded = true }
        if status == "failed" { failed = true }
    }
    let summary = parts.isEmpty ? "没有可同步的数据源" : parts.joined(separator: "、")
    return (summary, rows, downloaded, failed)
}

func run() -> Int32 {
    let arguments = Array(CommandLine.arguments.dropFirst())
    guard arguments.contains("--sync-helper") else {
        FileHandle.standardError.write(Data("usage: FireSkySyncHelper --sync-helper [--workspace PATH]\n".utf8))
        return 2
    }
    let env = ProcessInfo.processInfo.environment
    var workspace = env["FIRESKY_WORKSPACE"] ?? ""
    if let index = arguments.firstIndex(of: "--workspace"), index + 1 < arguments.count {
        workspace = arguments[index + 1]
    }
    guard !workspace.isEmpty else {
        FileHandle.standardError.write(Data("FireSkySyncHelper: FIRESKY_WORKSPACE is not set\n".utf8))
        return 2
    }
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: workspace, isDirectory: &isDirectory), isDirectory.boolValue else {
        logLine(workspace, "workspace does not exist: \(workspace)")
        return 2
    }
    let resources = resourcesDirectory()
    guard let engine = resolveEngine(workspace, resources) else {
        logLine(workspace, "engine binary not found")
        writeStatus(workspace, [
            "contract": helperVersion, "last_run_utc": ISO8601DateFormatter().string(from: Date()),
            "outcome": "failed", "summary": "未找到 Rust 引擎", "error": "engine binary not found",
        ])
        return 1
    }

    let process = Process()
    process.executableURL = URL(fileURLWithPath: engine)
    process.arguments = ["desktop-call", "--workspace", workspace, "data_sync_tick"]
    process.currentDirectoryURL = URL(fileURLWithPath: workspace)
    process.environment = engineEnvironment(workspace, resources)
    let stdout = Pipe(), stderr = Pipe()
    process.standardOutput = stdout
    process.standardError = stderr
    do {
        try process.run()
    } catch {
        logLine(workspace, "failed to launch engine: \(error.localizedDescription)")
        return 1
    }
    let outData = stdout.fileHandleForReading.readDataToEndOfFile()
    let errData = stderr.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    let errText = String(data: errData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let stamp = ISO8601DateFormatter().string(from: Date())

    if process.terminationStatus != 0 {
        // A concurrent round (app or another helper) holds the cross-process
        // lock: this is a normal yield, not a failure. Stay silent.
        if errText.contains("active") {
            logLine(workspace, "skipped: another sync/operation is active")
            writeStatus(workspace, [
                "contract": helperVersion, "last_run_utc": stamp,
                "outcome": "busy", "summary": "另一轮同步正在进行，本次让路", "error": "",
            ])
            return 0
        }
        logLine(workspace, "engine failed: \(errText)")
        writeStatus(workspace, [
            "contract": helperVersion, "last_run_utc": stamp,
            "outcome": "failed", "summary": "同步失败", "error": errText,
        ])
        notify("FireSky 数据同步", "后台同步失败：\(errText)")
        return 1
    }

    guard let result = try? JSONSerialization.jsonObject(with: outData) as? [String: Any] else {
        logLine(workspace, "engine returned unparseable output")
        writeStatus(workspace, [
            "contract": helperVersion, "last_run_utc": stamp,
            "outcome": "failed", "summary": "引擎输出无法解析", "error": "unparseable output",
        ])
        return 1
    }
    if result["skipped"] as? Bool == true {
        logLine(workspace, "skipped: no sources due")
        writeStatus(workspace, [
            "contract": helperVersion, "last_run_utc": stamp,
            "outcome": "skipped", "summary": "暂无到期的数据源", "error": "",
        ])
        return 0
    }
    let round = result["round"] as? [String: Any] ?? [:]
    let summary = summarizeRound(round)
    logLine(workspace, "round complete: \(summary.summary)")
    writeStatus(workspace, [
        "contract": helperVersion, "last_run_utc": stamp,
        "outcome": summary.failed ? "failed" : (summary.downloaded ? "synced" : "current"),
        "summary": summary.summary, "sources": summary.sources, "error": "",
    ])
    if summary.downloaded || summary.failed {
        notify("FireSky 数据同步", summary.summary)
    }
    return 0
}

exit(run())