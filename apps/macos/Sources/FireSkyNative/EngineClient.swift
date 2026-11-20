import Foundation
import Combine
import Darwin


struct EngineError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@MainActor
final class EngineClient: ObservableObject {
    @Published var connected = false
    private var ffi: FFIEngine?
    private var process: Process?
    private var input: FileHandle?
    private var pending: [String: CheckedContinuation<JSONObject, Error>] = [:]
    private var progress: [String: (JSONObject) -> Void] = [:]
    private var nativeLocation: EngineClient?
    private(set) var workspace: String = ""

    func start(workspace: String, engineBinary: String = "") throws {
        stop()
        let root = URL(fileURLWithPath: workspace)
        guard FileManager.default.fileExists(atPath: root.path) else {
            throw EngineError(message: "请选择有效的 FireSky 数据目录。")
        }
        let environment = ProcessInfo.processInfo.environment
        // Production and source-tree runs both use the FFI by default. The
        // JSONL process is a deliberate developer diagnostic path only.
        let backend = environment["FIRESKY_NATIVE_BACKEND"] ?? "ffi"
        // Dev/test runs pass an explicit engine binary; the dylib ships in the
        // same build directory right next to it.
        var engineBins = [engineBinary]
        if let envBin = environment["FIRESKY_RUST_ENGINE_BIN"] {
            engineBins.append(envBin)
        }
        let engineBinarySiblings = engineBins
            .filter { !$0.isEmpty }
            .map { URL(fileURLWithPath: $0).deletingLastPathComponent()
                .appendingPathComponent("libfiresky_engine.dylib").path }
        var ffiCandidatePaths: [String?] = [environment["FIRESKY_ENGINE_FFI_LIBRARY"]]
        for sibling in engineBinarySiblings {
            ffiCandidatePaths.append(sibling)
        }
        // The native release script places the dylib in the standard
        // `.app/Contents/Frameworks` directory. `privateFrameworksPath`
        // points to `PrivateFrameworks`, so it does not cover this path.
        ffiCandidatePaths.append(Bundle.main.bundleURL
            .appendingPathComponent("Contents/Frameworks/libfiresky_engine.dylib")
            .path)
        if let sharedFrameworks = Bundle.main.sharedFrameworksPath {
            ffiCandidatePaths.append(URL(fileURLWithPath: sharedFrameworks)
                .appendingPathComponent("libfiresky_engine.dylib").path)
        }
        if let privateFrameworks = Bundle.main.privateFrameworksPath {
            ffiCandidatePaths.append(URL(fileURLWithPath: privateFrameworks)
                .appendingPathComponent("libfiresky_engine.dylib").path)
        }
        if let resources = Bundle.main.resourceURL {
            ffiCandidatePaths.append(resources
                .appendingPathComponent("libfiresky_engine.dylib").path)
        }
        ffiCandidatePaths.append(root
            .appendingPathComponent("engine/target/release/libfiresky_engine.dylib").path)
        let ffiCandidates = ffiCandidatePaths.compactMap { $0 }.filter { !$0.isEmpty }
        let isBundledApp = Bundle.main.bundleURL.pathExtension == "app"
        guard backend == "ffi" || backend == "jsonl" else {
            throw EngineError(message: "未知的 FireSky 后端：\(backend)。")
        }
        if isBundledApp && backend == "jsonl" {
            throw EngineError(message: "正式 FireSky 应用不允许使用 JSONL 后端。")
        }
        if isBundledApp {
            guard let resources = Bundle.main.resourceURL else {
                throw EngineError(message: "FireSky 应用资源目录不可用，请重新安装。")
            }
            let appData = resources.appendingPathComponent("app-data", isDirectory: true)
            let manifest = appData.appendingPathComponent("RESOURCE-MANIFEST.json")
            guard FileManager.default.fileExists(atPath: appData.path),
                  FileManager.default.fileExists(atPath: manifest.path) else {
                throw EngineError(message: "FireSky 安装包缺少应用资源，请重新安装。")
            }
        }
        if backend == "ffi",
           let ffiPath = ffiCandidates.first(where: { FileManager.default.fileExists(atPath: $0) }) {
            do {
                try configureFFIEnvironment(workspace: workspace)
                ffi = try FFIEngine(libraryPath: ffiPath, workspace: workspace)
                try spawnNativeLocation(workspace: workspace,
                                        environment: ProcessInfo.processInfo.environment)
                self.workspace = workspace
                return
            } catch {
                stop()
                if backend == "ffi" || isBundledApp {
                    throw error
                }
            }
        } else if backend == "ffi" {
            throw EngineError(message: "未找到 Rust FFI 动态库，请重新安装 FireSky。")
        }
        var env = ProcessInfo.processInfo.environment
        if let resources = Bundle.main.resourceURL {
            let decoder = resources.appendingPathComponent("lib/libeccodes.dylib").path
            if env["FIRESKY_ECCODES_LIBRARY"] == nil,
               FileManager.default.fileExists(atPath: decoder) {
                env["FIRESKY_ECCODES_LIBRARY"] = decoder
                env["ECCODES_DEFINITION_PATH"] = resources.appendingPathComponent("eccodes/definitions").path
                env["ECCODES_SAMPLES_PATH"] = resources.appendingPathComponent("eccodes/samples").path
            }
        }
        let explicit = env["FIRESKY_RUST_ENGINE_BIN"].flatMap { $0.isEmpty ? nil : $0 }
            ?? (engineBinary.isEmpty ? nil : engineBinary)
        if let explicit, !FileManager.default.isExecutableFile(atPath: explicit) {
            throw EngineError(message: "指定的 Rust 引擎不可执行，请检查引擎路径。")
        }
        let candidates = [
            explicit,
            Bundle.main.resourceURL?.appendingPathComponent("firesky-engine").path,
            root.appendingPathComponent("engine/target/release/firesky-engine").path
        ].compactMap { $0 }.filter { !$0.isEmpty }
        guard let binary = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            throw EngineError(message: "Rust 引擎不可用，请选择 firesky-engine 或重新安装 FireSky。")
        }
        do {
            try launch(workspace: workspace, executable: binary,
                       arguments: ["desktop", "--workspace", root.path], environment: env)
            try spawnNativeLocation(workspace: workspace, environment: env)
        } catch {
            stop()
            throw error
        }
    }

    /// Optional development sidecar (`FIRESKY_LOCATION_ENGINE_BIN`): a separate
    /// Rust child that serves `search`/`metadata`/`inventory`. It must pass the
    /// handshake validation before any call is routed to it.
    private func spawnNativeLocation(workspace: String, environment env: [String: String]) throws {
        guard let binary = env["FIRESKY_LOCATION_ENGINE_BIN"], !binary.isEmpty else { return }
        guard FileManager.default.isExecutableFile(atPath: binary) else {
            throw EngineError(message: "Rust 位置引擎不可执行，请检查 FIRESKY_LOCATION_ENGINE_BIN。")
        }
        let location = EngineClient()
        var arguments = ["desktop-location"]
        if let config = env["FIRESKY_LOCATION_CONFIG"], !config.isEmpty {
            arguments += ["--config", config]
        }
        try location.launch(workspace: workspace, executable: binary,
                            arguments: arguments, environment: env,
                            logName: "location-engine.log")
        nativeLocation = location
    }

    private func configureFFIEnvironment(workspace: String) throws {
        setenv("FIRESKY_WORKSPACE", workspace, 1)
        guard let resources = Bundle.main.resourceURL else {
            if Bundle.main.bundleURL.pathExtension == "app" {
                throw EngineError(message: "FireSky 应用资源目录不可用，请重新安装。")
            }
            return
        }
        let appData = resources.appendingPathComponent("app-data").path
        if FileManager.default.fileExists(atPath: appData) {
            setenv("FIRESKY_RESOURCE_DIR", appData, 1)
            setenv("FIRESKY_REQUIRE_APP_RESOURCES", "1", 1)
        } else if Bundle.main.bundleURL.pathExtension == "app" {
            throw EngineError(message: "FireSky 安装包缺少应用资源，请重新安装。")
        }
        let decoder = resources.appendingPathComponent("lib/libeccodes.dylib").path
        if FileManager.default.fileExists(atPath: decoder) {
            setenv("FIRESKY_ECCODES_LIBRARY", decoder, 1)
            setenv("ECCODES_DEFINITION_PATH", resources.appendingPathComponent("eccodes/definitions").path, 1)
            setenv("ECCODES_SAMPLES_PATH", resources.appendingPathComponent("eccodes/samples").path, 1)
        }
    }

    private func launch(workspace: String, executable: String, arguments: [String],
                        environment env: [String: String], logName: String = "engine.log") throws {
        let root = URL(fileURLWithPath: workspace)
        let child = Process()
        let stdin = Pipe(), stdout = Pipe()
        child.executableURL = URL(fileURLWithPath: executable)
        child.arguments = arguments
        child.currentDirectoryURL = root
        child.environment = env
        child.standardInput = stdin
        child.standardOutput = stdout
        let logRoot = env["FIRESKY_NATIVE_LOG_DIR"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FireSky", isDirectory: true)
        try FileManager.default.createDirectory(at: logRoot, withIntermediateDirectories: true)
        let logPath = logRoot.appendingPathComponent(logName)
        if !FileManager.default.fileExists(atPath: logPath.path) {
            FileManager.default.createFile(atPath: logPath.path, contents: nil)
        }
        let log = try FileHandle(forWritingTo: logPath)
        try log.seekToEnd()
        child.standardError = log
        child.terminationHandler = { [weak self] _ in
            Task { @MainActor in
                guard self?.process === child else { return }
                self?.failAll("引擎已退出，请在设置中重新连接。")
            }
        }
        try child.run()
        self.process = child
        self.input = stdin.fileHandleForWriting
        self.workspace = workspace
        let output = stdout.fileHandleForReading
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var buffer = Data()
            while true {
                let chunk = output.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let newline = buffer.firstIndex(of: 10) {
                    let line = Data(buffer[..<newline])
                    buffer.removeSubrange(...newline)
                    Task { @MainActor in
                        guard self?.process === child else { return }
                        self?.receive(line)
                    }
                }
            }
        }
    }

    func call(_ method: String, _ params: JSONObject = [:],
              progress onProgress: ((JSONObject) -> Void)? = nil) async throws -> JSONObject {
        try Task.checkCancellation()
        if (method == "search" || method == "metadata" || method == "inventory"), let nativeLocation {
            return try await nativeLocation.call(method, params, progress: onProgress)
        }
        if method == "hello", let nativeLocation {
            let hello = try await nativeLocation.call("hello")
            let methods = Set(hello["capabilities"] as? [String] ?? [])
            guard hello["protocol"] as? Int == 1,
                  hello["engine"] as? String == "firesky-rust-location",
                  methods.isSuperset(of: ["search", "metadata", "inventory", "cancel"]) else {
                throw EngineError(message: "Rust 位置引擎协议不匹配。")
            }
        }
        if let ffi {
            return try await ffi.call(method, params, progress: onProgress)
        }
        guard let process, process.isRunning, let input else {
            throw EngineError(message: "引擎未连接，请检查设置。")
        }
        let id = UUID().uuidString
        let request: JSONObject = ["id": id, "method": method, "params": params]
        var data = try JSONSerialization.data(withJSONObject: request)
        data.append(10)
        guard data.count <= 1024 * 1024 else {
            throw EngineError(message: "请求过大，请缩小输入内容。")
        }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                pending[id] = continuation
                progress[id] = onProgress
                do { try input.write(contentsOf: data) }
                catch {
                    pending.removeValue(forKey: id)?.resume(throwing: error)
                    progress.removeValue(forKey: id)
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                guard let self, let input = self.input else { return }
                let cancel: JSONObject = ["id": UUID().uuidString, "method": "cancel",
                                          "params": ["request_id": id]]
                if var bytes = try? JSONSerialization.data(withJSONObject: cancel) {
                    bytes.append(10)
                    try? input.write(contentsOf: bytes)
                }
            }
        }
    }

    private func receive(_ bytes: Data) {
        guard let object = try? JSONSerialization.jsonObject(with: bytes) as? JSONObject,
              let id = object["id"] as? String else { return }
        if object["type"] as? String == "progress" {
            progress[id]?(object["event"] as? JSONObject ?? [:])
            return
        }
        guard let continuation = pending.removeValue(forKey: id) else { return }
        progress.removeValue(forKey: id)
        if object["type"] as? String == "error" {
            let error = object["error"] as? JSONObject ?? [:]
            continuation.resume(throwing: EngineError(message: error["message"] as? String ?? "操作失败"))
        } else {
            continuation.resume(returning: object["result"] as? JSONObject ?? [:])
        }
    }

    private func failAll(_ message: String) {
        connected = false
        let continuations = pending.values
        pending.removeAll()
        progress.removeAll()
        for continuation in continuations {
            continuation.resume(throwing: EngineError(message: message))
        }
    }

    func stop() {
        ffi?.stop()
        ffi = nil
        let location = nativeLocation
        nativeLocation = nil
        location?.stop()
        let old = process
        process = nil
        try? input?.close()
        input = nil
        old?.terminate()
        failAll("引擎连接已关闭。")
    }
}
