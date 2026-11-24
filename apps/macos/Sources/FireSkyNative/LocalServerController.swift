import Combine
import Darwin
import Foundation
import Security

/// 管理内置 `firesky-engine http` 本地 / 局域网 HTTP 服务子进程，
/// 支持本机回环（配合 Cloudflare Tunnel）与局域网模式、Bearer Token 持久化及健康检查。
@MainActor
final class LocalServerController: ObservableObject {
    private enum DefaultsKey {
        static let port = "lanServerPort"
        static let bindLAN = "lanServerBindLAN"
        static let autoStart = "lanServerAutoStart"
        static let requireToken = "lanServerRequireToken"
    }

    @Published private(set) var running: Bool = false
    @Published private(set) var starting: Bool = false
    @Published var port: Int {
        didSet {
            let clamped = Self.clampPort(port)
            if port != clamped {
                port = clamped
                return
            }
            UserDefaults.standard.set(clamped, forKey: DefaultsKey.port)
        }
    }
    @Published var bindLAN: Bool {
        didSet { UserDefaults.standard.set(bindLAN, forKey: DefaultsKey.bindLAN) }
    }
    @Published var autoStart: Bool {
        didSet { UserDefaults.standard.set(autoStart, forKey: DefaultsKey.autoStart) }
    }
    @Published var requireToken: Bool {
        didSet { UserDefaults.standard.set(requireToken, forKey: DefaultsKey.requireToken) }
    }
    @Published private(set) var token: String = ""
    @Published private(set) var lanAddress: String = ""
    @Published private(set) var statusMessage: String = ""
    @Published private(set) var errorMessage: String = ""

    private var process: Process?
    private var startTask: Task<Void, Never>?
    private var stopping = false
    private var activeWorkspace: String = ""

    init() {
        let defaults = UserDefaults.standard
        let savedPort = defaults.object(forKey: DefaultsKey.port) as? Int ?? 8765
        port = Self.clampPort(savedPort)
        bindLAN = defaults.bool(forKey: DefaultsKey.bindLAN)
        autoStart = defaults.bool(forKey: DefaultsKey.autoStart)
        requireToken = defaults.object(forKey: DefaultsKey.requireToken) == nil
            ? true
            : defaults.bool(forKey: DefaultsKey.requireToken)
        lanAddress = Self.detectLANAddress()
    }

    // MARK: - 计算属性

    var localURL: String {
        "http://127.0.0.1:\(port)"
    }

    var lanURL: String? {
        lanAddress.isEmpty ? nil : "http://\(lanAddress):\(port)"
    }

    var cloudflaredCommand: String {
        "cloudflared tunnel --url http://127.0.0.1:\(port)"
    }

    var webRootAvailable: Bool {
        resolveWebRoot(workspace: activeWorkspace) != nil
    }

    // MARK: - 生命周期控制

    func start(workspace: String, customEngineBinary: String = "") {
        stop()
        errorMessage = ""

        let trimmedWorkspace = workspace.trimmingCharacters(in: .whitespacesAndNewlines)
        var isDirectory: ObjCBool = false
        guard !trimmedWorkspace.isEmpty,
              FileManager.default.fileExists(atPath: trimmedWorkspace, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            statusMessage = ""
            errorMessage = "请选择有效的 FireSky 数据目录。"
            return
        }
        activeWorkspace = trimmedWorkspace

        guard let enginePath = resolveEngineBinary(workspace: trimmedWorkspace,
                                                   customEngineBinary: customEngineBinary) else {
            statusMessage = ""
            errorMessage = "未找到可执行的 firesky-engine，请检查引擎路径或重新安装 FireSky。"
            return
        }

        let needsToken = bindLAN || requireToken
        let tokenPath = tokenFileURL(workspace: trimmedWorkspace).path
        if needsToken {
            do {
                token = try ensureTokenFile(workspace: trimmedWorkspace)
            } catch {
                statusMessage = ""
                errorMessage = "无法创建或读取访问令牌：\(error.localizedDescription)"
                return
            }
        } else {
            refreshTokenPreview(workspace: trimmedWorkspace)
        }

        lanAddress = Self.detectLANAddress()
        starting = true
        running = false
        statusMessage = "正在启动本地服务…"

        let host = bindLAN ? "0.0.0.0" : "127.0.0.1"
        let targetPort = port
        let environment = buildEnvironment(workspace: trimmedWorkspace,
                                           needsToken: needsToken,
                                           tokenPath: tokenPath)

        let child = Process()
        child.executableURL = URL(fileURLWithPath: enginePath)
        child.arguments = [
            "http",
            "--workspace", trimmedWorkspace,
            "--host", host,
            "--port", String(targetPort),
        ]
        child.currentDirectoryURL = URL(fileURLWithPath: trimmedWorkspace)
        child.environment = environment
        child.standardInput = FileHandle.nullDevice

        let logHandle = openLogFileHandle(environment: environment)
        child.standardOutput = logHandle
        child.standardError = logHandle

        stopping = false
        child.terminationHandler = { [weak self] terminated in
            Task { @MainActor in
                guard let self, self.process === terminated else { return }
                let intentional = self.stopping
                self.process = nil
                self.running = false
                self.starting = false
                if !intentional {
                    self.statusMessage = ""
                    self.errorMessage = "本地 HTTP 服务已退出（退出码 \(terminated.terminationStatus)）。"
                }
            }
        }

        do {
            try child.run()
        } catch {
            starting = false
            running = false
            statusMessage = ""
            errorMessage = "启动本地 HTTP 服务失败：\(error.localizedDescription)"
            return
        }

        self.process = child
        startTask = Task { [weak self] in
            guard let self else { return }
            let ready = await self.waitForHealth(port: targetPort, process: child)
            guard !Task.isCancelled, self.process === child else { return }
            self.startTask = nil
            self.starting = false
            if ready {
                self.running = true
                self.errorMessage = ""
                self.lanAddress = Self.detectLANAddress()
                self.statusMessage = self.bindLAN
                    ? "正在局域网与本机端口 \(targetPort) 提供服务"
                    : "正在本机 127.0.0.1:\(targetPort) 提供服务"
            } else {
                self.stopping = true
                self.process = nil
                if child.isRunning {
                    child.terminate()
                }
                self.running = false
                self.statusMessage = ""
                if self.errorMessage.isEmpty {
                    self.errorMessage = "本地 HTTP 服务未在 3 秒内就绪，请检查端口 \(targetPort) 是否已被占用。"
                }
            }
        }
    }

    func stop() {
        startTask?.cancel()
        startTask = nil
        stopping = true
        if let child = process {
            process = nil
            if child.isRunning {
                child.terminate()
            }
        }
        running = false
        starting = false
        statusMessage = ""
    }

    func regenerateToken(workspace: String) {
        let trimmed = workspace.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            errorMessage = "请先选择 FireSky 数据目录。"
            return
        }
        activeWorkspace = trimmed
        do {
            let fresh = try Self.generateHexToken()
            try writeTokenAtomically(fresh, workspace: trimmed)
            token = fresh
            errorMessage = ""
        } catch {
            errorMessage = "重新生成访问令牌失败：\(error.localizedDescription)"
        }
    }

    func refreshTokenPreview(workspace: String) {
        let trimmed = workspace.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            token = ""
            return
        }
        activeWorkspace = trimmed
        token = loadExistingToken(workspace: trimmed) ?? ""
    }

    func refreshLANAddress() {
        lanAddress = Self.detectLANAddress()
    }

    // MARK: - 路径与环境

    private func resolveEngineBinary(workspace: String, customEngineBinary: String) -> String? {
        let fileManager = FileManager.default
        let envBin = ProcessInfo.processInfo.environment["FIRESKY_RUST_ENGINE_BIN"]
        let cwd = fileManager.currentDirectoryPath
        let candidates: [String?] = [
            customEngineBinary.isEmpty ? nil : customEngineBinary,
            envBin?.isEmpty == true ? nil : envBin,
            Bundle.main.resourceURL?.appendingPathComponent("firesky-engine").path,
            URL(fileURLWithPath: workspace)
                .appendingPathComponent("engine/target/release/firesky-engine").path,
            URL(fileURLWithPath: cwd)
                .appendingPathComponent("engine/target/release/firesky-engine").path,
        ]
        return candidates
            .compactMap { $0 }
            .first { fileManager.isExecutableFile(atPath: $0) }
    }

    private func resolveWebRoot(workspace: String) -> String? {
        let fileManager = FileManager.default
        let cwd = fileManager.currentDirectoryPath
        var candidates: [URL] = []
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("web", isDirectory: true) {
            candidates.append(bundled)
        }
        if !workspace.isEmpty {
            candidates.append(
                URL(fileURLWithPath: workspace)
                    .appendingPathComponent("apps/shared-web/dist", isDirectory: true)
            )
        }
        candidates.append(
            URL(fileURLWithPath: cwd)
                .appendingPathComponent("apps/shared-web/dist", isDirectory: true)
        )
        for directory in candidates {
            let index = directory.appendingPathComponent("index.html").path
            let spaFallback = directory.appendingPathComponent("200.html").path
            if fileManager.fileExists(atPath: index) || fileManager.fileExists(atPath: spaFallback) {
                return directory.path
            }
        }
        return nil
    }

    private func buildEnvironment(workspace: String,
                                  needsToken: Bool,
                                  tokenPath: String) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let fileManager = FileManager.default
        env["FIRESKY_WORKSPACE"] = workspace

        if let webRoot = resolveWebRoot(workspace: workspace) {
            env["FIRESKY_WEB_ROOT"] = webRoot
        }

        if let resources = Bundle.main.resourceURL {
            let appData = resources.appendingPathComponent("app-data", isDirectory: true)
            if fileManager.fileExists(atPath: appData.path) {
                env["FIRESKY_RESOURCE_DIR"] = appData.path
                env["FIRESKY_REQUIRE_APP_RESOURCES"] = "1"
            }
            let decoder = resources.appendingPathComponent("lib/libeccodes.dylib").path
            if env["FIRESKY_ECCODES_LIBRARY"] == nil,
               fileManager.fileExists(atPath: decoder) {
                env["FIRESKY_ECCODES_LIBRARY"] = decoder
                env["ECCODES_DEFINITION_PATH"] = resources
                    .appendingPathComponent("eccodes/definitions").path
                env["ECCODES_SAMPLES_PATH"] = resources
                    .appendingPathComponent("eccodes/samples").path
            }
        }

        // 避免外部环境残留 FIRESKY_HTTP_TOKEN 与 FIRESKY_HTTP_TOKEN_FILE 冲突。
        env.removeValue(forKey: "FIRESKY_HTTP_TOKEN")
        // 局域网模式或开启令牌鉴权的本机模式（Cloudflare Tunnel 会透传公网域名 Host）均放行非 loopback Host，
        // 同时由 bind 地址（127.0.0.1 vs 0.0.0.0）与 Token 鉴权保障安全边界。
        if bindLAN || needsToken {
            env["FIRESKY_HTTP_ALLOW_REMOTE"] = "1"
        } else {
            env.removeValue(forKey: "FIRESKY_HTTP_ALLOW_REMOTE")
        }

        if needsToken {
            env["FIRESKY_HTTP_TOKEN_FILE"] = tokenPath
        } else {
            env.removeValue(forKey: "FIRESKY_HTTP_TOKEN_FILE")
        }
        return env
    }

    private func openLogFileHandle(environment: [String: String]) -> FileHandle {
        let fileManager = FileManager.default
        let logRoot = environment["FIRESKY_NATIVE_LOG_DIR"].map { URL(fileURLWithPath: $0) }
            ?? fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FireSky", isDirectory: true)
        do {
            try fileManager.createDirectory(at: logRoot, withIntermediateDirectories: true)
            let logURL = logRoot.appendingPathComponent("http-server.log")
            if !fileManager.fileExists(atPath: logURL.path) {
                fileManager.createFile(atPath: logURL.path, contents: nil)
            }
            let handle = try FileHandle(forWritingTo: logURL)
            try handle.seekToEnd()
            return handle
        } catch {
            return FileHandle.nullDevice
        }
    }

    // MARK: - 健康检查

    private func waitForHealth(port: Int, process: Process) async -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/v1/firesky/health") else {
            return false
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 0.5
        configuration.timeoutIntervalForResource = 0.5
        configuration.connectionProxyDictionary = [:]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }

        let deadline = Date().addingTimeInterval(3.0)
        while Date() < deadline {
            if Task.isCancelled || self.process !== process || !process.isRunning {
                return false
            }
            var request = URLRequest(url: url, timeoutInterval: 0.5)
            request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            if let (_, response) = try? await session.data(for: request),
               let http = response as? HTTPURLResponse,
               http.statusCode == 200 {
                return true
            }
            try? await Task.sleep(nanoseconds: 150_000_000)
        }
        return false
    }

    // MARK: - 令牌读写（0600 权限）

    private func tokenFileURL(workspace: String) -> URL {
        URL(fileURLWithPath: workspace)
            .appendingPathComponent("state", isDirectory: true)
            .appendingPathComponent("http-token", isDirectory: false)
    }

    private func ensureTokenFile(workspace: String) throws -> String {
        if let existing = loadExistingToken(workspace: workspace) {
            try enforcePrivatePermissions(at: tokenFileURL(workspace: workspace).path)
            return existing
        }
        let fresh = try Self.generateHexToken()
        try writeTokenAtomically(fresh, workspace: workspace)
        return fresh
    }

    private func loadExistingToken(workspace: String) -> String? {
        let url = tokenFileURL(workspace: workspace)
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              attrs[.type] as? FileAttributeType == .typeRegular,
              let data = try? Data(contentsOf: url),
              data.count <= 4097 else {
            return nil
        }
        let raw = String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\r\n"))
        guard Self.isValidToken(raw) else { return nil }
        return raw
    }

    private func writeTokenAtomically(_ value: String, workspace: String) throws {
        let url = tokenFileURL(workspace: workspace)
        let directory = url.deletingLastPathComponent()
        let fileManager = FileManager.default
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)

        let tempURL = directory.appendingPathComponent(".http-token.tmp.\(UUID().uuidString)")
        let payload = Data((value + "\n").utf8)
        let fd = open(tempURL.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        var closed = false
        defer {
            if !closed {
                close(fd)
            }
        }
        let written: Bool = payload.withUnsafeBytes { rawBuffer in
            guard let base = rawBuffer.baseAddress else { return true }
            var offset = 0
            while offset < rawBuffer.count {
                let n = Darwin.write(fd, base.advanced(by: offset), rawBuffer.count - offset)
                if n <= 0 { return false }
                offset += n
            }
            return true
        }
        guard written else {
            let code = POSIXErrorCode(rawValue: errno) ?? .EIO
            close(fd)
            closed = true
            try? fileManager.removeItem(at: tempURL)
            throw POSIXError(code)
        }
        fchmod(fd, S_IRUSR | S_IWUSR)
        close(fd)
        closed = true

        if Darwin.rename(tempURL.path, url.path) != 0 {
            let code = POSIXErrorCode(rawValue: errno) ?? .EIO
            try? fileManager.removeItem(at: tempURL)
            throw POSIXError(code)
        }
    }

    private func enforcePrivatePermissions(at path: String) throws {
        let attrs = try FileManager.default.attributesOfItem(atPath: path)
        let posix = (attrs[.posixPermissions] as? NSNumber)?.uint16Value ?? 0
        if (posix & 0o077) != 0 {
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
        }
    }

    private static func generateHexToken() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        guard status == errSecSuccess else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }

    private static func isValidToken(_ token: String) -> Bool {
        guard (32...4096).contains(token.count) else { return false }
        return token.unicodeScalars.allSatisfy { scalar in
            let v = scalar.value
            return (v >= 48 && v <= 57)   // 0-9
                || (v >= 65 && v <= 90)   // A-Z
                || (v >= 97 && v <= 122)  // a-z
                || v == 45 || v == 46 || v == 95 || v == 126 // - . _ ~
        }
    }

    // MARK: - 局域网 IP 探测

    private static func clampPort(_ value: Int) -> Int {
        min(max(value, 1024), 65535)
    }

    static func detectLANAddress() -> String {
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return "" }
        defer { freeifaddrs(ifaddr) }

        var candidates: [(name: String, ip: String)] = []
        var pointer: UnsafeMutablePointer<ifaddrs>? = first
        while let current = pointer {
            pointer = current.pointee.ifa_next
            let flags = Int32(current.pointee.ifa_flags)
            guard (flags & IFF_UP) != 0, (flags & IFF_LOOPBACK) == 0 else { continue }
            guard let addr = current.pointee.ifa_addr,
                  addr.pointee.sa_family == UInt8(AF_INET) else { continue }

            var buffer = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
            let converted: String? = addr.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { sinPtr in
                var inAddr = sinPtr.pointee.sin_addr
                guard inet_ntop(AF_INET, &inAddr, &buffer, socklen_t(INET_ADDRSTRLEN)) != nil else {
                    return nil
                }
                return String(cString: buffer)
            }
            guard let ip = converted, !ip.isEmpty, !ip.hasPrefix("169.254.") else { continue }
            let name = String(cString: current.pointee.ifa_name)
            candidates.append((name: name, ip: ip))
        }

        if let en0 = candidates.first(where: { $0.name == "en0" }) {
            return en0.ip
        }
        if let en1 = candidates.first(where: { $0.name == "en1" }) {
            return en1.ip
        }
        if let privateAddr = candidates.first(where: { isPrivateIPv4($0.ip) }) {
            return privateAddr.ip
        }
        return candidates.first?.ip ?? ""
    }

    private static func isPrivateIPv4(_ ip: String) -> Bool {
        let parts = ip.split(separator: ".").compactMap { Int($0) }
        guard parts.count == 4 else { return false }
        if parts[0] == 10 { return true }
        if parts[0] == 172 && (16...31).contains(parts[1]) { return true }
        if parts[0] == 192 && parts[1] == 168 { return true }
        return false
    }
}
