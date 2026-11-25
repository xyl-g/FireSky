import AppKit
import CryptoKit
import Foundation

/// 应用内更新（v1.5.0）：GitHub Releases 最新版检查 → 架构匹配的 DMG 下载
/// （进度/取消 + SHA256SUMS 校验）→ 挂载安装并重启。检查与下载走
/// URLSession（系统代理设置；源码运行时若环境带 proxy 变量则一并遵循），
/// 安装用 hdiutil 挂载 + FileManager 跨卷拷贝（旧版本先入废纸篓），
/// 与 v1.4.0 的 DMG 自助安装引导同一套落盘语义。
@MainActor
final class Updater: ObservableObject {
    enum Phase: Equatable {
        case idle, checking, current, available, downloading, installing, failed
    }

    struct Info: Equatable {
        let version: String
        let notes: String
        let assetURL: URL
        let assetBytes: Int64
        /// SHA256SUMS.txt 资产地址；存在才执行完整性校验。
        let checksumURL: URL?
    }

    static let repository = "xyl-g/FireSky"

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var info: Info?
    @Published private(set) var message = ""
    @Published private(set) var receivedBytes: Int64 = 0
    @Published private(set) var totalBytes: Int64 = 0
    @Published private(set) var bytesPerSecond: Double = 0
    @Published var autoCheck: Bool {
        didSet { UserDefaults.standard.set(autoCheck, forKey: "autoUpdateCheck") }
    }

    private var checkTask: Task<Void, Never>?
    private var downloadTask: Task<Void, Never>?

    init() {
        autoCheck = UserDefaults.standard.object(forKey: "autoUpdateCheck") == nil
            ? true
            : UserDefaults.standard.bool(forKey: "autoUpdateCheck")
    }

    // MARK: - 纯函数（fixture 覆盖）

    /// "v1.5.0" → "1.5.0"；其余原样返回。
    nonisolated static func normalizeTag(_ tag: String) -> String {
        tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
    }

    /// 资产名匹配：FireSky-{版本}-macos-{架构}.dmg。
    nonisolated static func assetName(version: String, architecture: String) -> String {
        "FireSky-\(version)-macos-\(architecture).dmg"
    }

    /// 语义化版本比较（逐段数值，缺失段按 0）；与 AppDelegate 共用。
    nonisolated static func compareVersions(_ left: String, _ right: String) -> ComparisonResult {
        var leftParts = left.split(separator: ".").map { Int($0) ?? 0 }
        var rightParts = right.split(separator: ".").map { Int($0) ?? 0 }
        while leftParts.count < rightParts.count { leftParts.append(0) }
        while rightParts.count < leftParts.count { rightParts.append(0) }
        for (l, r) in zip(leftParts, rightParts) {
            if l < r { return .orderedAscending }
            if l > r { return .orderedDescending }
        }
        return .orderedSame
    }

    /// 当前机器架构（arm64 / x86_64）。
    nonisolated static func machineArchitecture() -> String {
        var system = utsname()
        uname(&system)
        let machine = withUnsafeBytes(of: &system.machine) { raw -> String in
            let data = Data(raw).prefix(while: { $0 != 0 })
            return String(decoding: data, as: UTF8.self)
        }
        return machine
    }

    /// 从 release JSON 解析更新信息（架构匹配 DMG 资产 + SHA256SUMS.txt 地址）。
    nonisolated static func parseRelease(_ release: JSONObject, architecture: String) -> Info? {
        guard release["draft"] as? Bool != true,
              release["prerelease"] as? Bool != true else { return nil }
        let tag = release["tag_name"] as? String ?? ""
        let version = normalizeTag(tag)
        guard !version.isEmpty, version.count <= 32,
              version.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "." }) else {
            return nil
        }
        let assets = release["assets"] as? [JSONObject] ?? []
        let wanted = assetName(version: version, architecture: architecture)
        guard let asset = assets.first(where: { $0["name"] as? String == wanted }),
              let urlString = asset["browser_download_url"] as? String,
              let assetURL = URL(string: urlString),
              urlString.contains("/releases/download/") else {
            return nil
        }
        let checksumURL = assets
            .first(where: { $0["name"] as? String == "SHA256SUMS.txt" })
            .flatMap { ($0["browser_download_url"] as? String).flatMap(URL.init(string:)) }
        let body = release["body"] as? String ?? ""
        return Info(version: version,
                    notes: String(body.prefix(1200)),
                    assetURL: assetURL,
                    assetBytes: (asset["size"] as? NSNumber)?.int64Value ?? 0,
                    checksumURL: checksumURL)
    }

    // MARK: - 会话（系统代理 + 环境代理）

    /// 解析环境变量代理；未配置时返回 `nil`，让 URLSession 默认遵循 macOS 系统代理（HTTP/HTTPS/SOCKS/PAC）。
    nonisolated static func proxyDictionary(environment: [String: String]) -> [AnyHashable: Any]? {
        let raw = environment["https_proxy"] ?? environment["HTTPS_PROXY"]
            ?? environment["all_proxy"] ?? environment["ALL_PROXY"]
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else {
            return nil
        }
        let normalized = trimmed.contains("://") ? trimmed : "http://\(trimmed)"
        guard let url = URL(string: normalized),
              let host = url.host,
              !host.isEmpty else {
            return nil
        }
        let scheme = (url.scheme ?? "http").lowercased()
        if scheme.hasPrefix("socks") {
            return [
                "SOCKSEnable": true,
                "SOCKSProxy": host,
                "SOCKSPort": url.port ?? 1080,
            ]
        }
        let port = url.port ?? 8080
        return [
            kCFNetworkProxiesHTTPEnable as String: true,
            kCFNetworkProxiesHTTPProxy as String: host,
            kCFNetworkProxiesHTTPPort as String: port,
            "HTTPSEnable": true,
            "HTTPSProxy": host,
            "HTTPSPort": port,
        ]
    }

    private func makeSessionConfiguration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 3600
        configuration.urlCache = nil
        configuration.httpMaximumConnectionsPerHost = 6
        // 仅当终端显式注入代理变量时覆盖；默认保持 nil 以走系统代理。
        if let proxy = Self.proxyDictionary(environment: ProcessInfo.processInfo.environment) {
            configuration.connectionProxyDictionary = proxy
        }
        return configuration
    }

    private func makeSession() -> URLSession {
        URLSession(configuration: makeSessionConfiguration())
    }

    func openDownloadInBrowser() {
        let fallback = URL(string: "https://github.com/\(Self.repository)/releases/latest")!
        NSWorkspace.shared.open(info?.assetURL ?? fallback)
    }

    // MARK: - 检查

    func checkForUpdates(interactive: Bool) {
        guard phase != .checking, phase != .downloading, phase != .installing else { return }
        checkTask?.cancel()
        checkTask = Task { [weak self] in
            guard let self else { return }
            phase = .checking
            message = ""
            do {
                let info = try await fetchLatestRelease()
                guard let info else {
                    phase = .current
                    let current = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
                    message = "已是最新版本（\(current)）。"
                    return
                }
                self.info = info
                phase = .available
                message = ""
            } catch {
                try? Task.checkCancellation()
                info = nil
                phase = .failed
                // 静默检查失败不打扰；手动检查给出具体原因。
                message = interactive
                    ? friendlyUpdateError(error)
                    : ""
                if !interactive { phase = .idle }
            }
        }
    }

    private func fetchLatestRelease() async throws -> Info? {
        let current = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        let userAgent = "FireSky/\(current.isEmpty ? "dev" : current) (macOS)"
        let architecture = Self.machineArchitecture()

        var request = URLRequest(url: URL(string: "https://api.github.com/repos/\(Self.repository)/releases/latest")!)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 20

        let session = makeSession()
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 403 || status == 429 {
            return try await fetchLatestReleaseFallback(
                session: session,
                userAgent: userAgent,
                architecture: architecture,
                current: current
            )
        }
        guard status == 200 else {
            throw UpdateError.network("GitHub 返回状态 \(status)")
        }
        guard let release = try? JSONSerialization.jsonObject(with: data) as? JSONObject else {
            throw UpdateError.network("GitHub 响应无法解析")
        }
        guard let parsed = Self.parseRelease(release, architecture: architecture) else {
            throw UpdateError.network("最新 Release 没有适用于 \(architecture) 的安装包")
        }
        guard Self.compareVersions(parsed.version, current) == .orderedDescending else {
            return nil
        }
        return parsed
    }

    private func fetchLatestReleaseFallback(
        session: URLSession,
        userAgent: String,
        architecture: String,
        current: String
    ) async throws -> Info? {
        var request = URLRequest(url: URL(string: "https://github.com/\(Self.repository)/releases/latest")!)
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 20
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            throw UpdateError.network("GitHub Releases 页面返回状态 \(status)")
        }
        guard let finalURL = response.url,
              finalURL.path.contains("/releases/tag/") else {
            throw UpdateError.network("未在 GitHub Releases 页面找到有效版本标签")
        }
        let tag = finalURL.lastPathComponent
        let version = Self.normalizeTag(tag)
        guard !version.isEmpty, version.count <= 32,
              version.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "." }) else {
            throw UpdateError.network("GitHub Releases 标签格式无效：\(tag)")
        }
        guard Self.compareVersions(version, current) == .orderedDescending else {
            return nil
        }
        let assetName = Self.assetName(version: version, architecture: architecture)
        guard let assetURL = URL(string: "https://github.com/\(Self.repository)/releases/download/\(tag)/\(assetName)") else {
            throw UpdateError.network("无法构造 Release 安装包下载地址")
        }
        let checksumURL = URL(string: "https://github.com/\(Self.repository)/releases/download/\(tag)/SHA256SUMS.txt")
        let notes = "由于 GitHub API 请求受限（403/429），本次通过 Releases 页面重定向回退检测到 v\(version)，未获取详细更新日志；请前往 GitHub Releases 页面查看完整说明。"
        return Info(
            version: version,
            notes: notes,
            assetURL: assetURL,
            assetBytes: 0,
            checksumURL: checksumURL
        )
    }

    // MARK: - 下载 + 校验 + 安装

    func downloadAndInstall() {
        guard let info, phase == .available else { return }
        phase = .downloading
        receivedBytes = 0
        totalBytes = info.assetBytes
        bytesPerSecond = 0
        message = ""
        downloadTask = Task { [weak self] in
            guard let self else { return }
            do {
                let archive = try await downloadDMG(info)
                try await verifyChecksum(info, archive)
                bytesPerSecond = 0
                phase = .installing
                let installed = try await installArchive(archive, version: info.version)
                try await NSWorkspace.shared.openApplication(at: installed, configuration: .init())
                NSApp.terminate(nil)
            } catch is CancellationError {
                bytesPerSecond = 0
                phase = .available
                message = "已取消下载；未安装任何内容。"
            } catch {
                bytesPerSecond = 0
                phase = .failed
                message = friendlyUpdateError(error)
            }
        }
    }

    func cancelDownload() {
        guard phase == .downloading else { return }
        downloadTask?.cancel()
    }

    /// 仅供界面图册快照夹具展示「发现新版本」弹层状态，不触发任何网络请求。
    func adoptTestAvailable(version: String, notes: String) {
        info = Info(
            version: version,
            notes: notes,
            assetURL: URL(string: "https://github.com/\(Self.repository)/releases/download/v\(version)/FireSky-\(version)-macos-arm64.dmg")!,
            assetBytes: 48 * 1_048_576,
            checksumURL: nil
        )
        phase = .available
        message = ""
    }

    /// 通过底层 URLSessionDownloadTask 后台大块流式落盘（脱离 @MainActor），按 ~200ms 节流回报进度与速度。
    private func downloadDMG(_ info: Info) async throws -> URL {
        let current = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        let userAgent = "FireSky/\(current.isEmpty ? "dev" : current) (macOS)"
        var request = URLRequest(url: info.assetURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 600)
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 600

        let archive = FileManager.default.temporaryDirectory
            .appendingPathComponent("firesky-update-\(info.version).dmg")
        try? FileManager.default.removeItem(at: archive)

        let delegate = DMGDownloadDelegate(
            destination: archive,
            expectedSizeHint: info.assetBytes,
            onProgress: { [weak self] written, expected, speed in
                Task { @MainActor [weak self] in
                    guard let self, self.phase == .downloading else { return }
                    self.receivedBytes = written
                    if expected > 0 { self.totalBytes = expected }
                    self.bytesPerSecond = speed
                }
            },
            onFinalBytes: { [weak self] written, total in
                Task { @MainActor [weak self] in
                    guard let self, self.phase == .downloading else { return }
                    self.receivedBytes = written
                    if total > 0 { self.totalBytes = total }
                }
            }
        )
        let session = URLSession(configuration: makeSessionConfiguration(), delegate: delegate, delegateQueue: nil)
        let task = session.downloadTask(with: request)

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                delegate.start(task: task, continuation: continuation)
            }
        } onCancel: {
            delegate.cancel()
        }
    }

    /// SHA256SUMS.txt 校验（存在才校验；缺失视为通过并在安装摘要中说明）。
    private func verifyChecksum(_ info: Info, _ archive: URL) async throws {
        guard let checksumURL = info.checksumURL else { return }
        let request = URLRequest(url: checksumURL, timeoutInterval: 30)
        let (data, response) = try await makeSession().data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw UpdateError.network("无法获取 SHA256SUMS.txt 进行校验")
        }
        let text = String(decoding: data, as: UTF8.self)
        let expected = text
            .split(separator: "\n")
            .compactMap { line -> String? in
                let parts = line.split(separator: " ", omittingEmptySubsequences: true)
                guard parts.count >= 2 else { return nil }
                let name = parts[1].trimmingCharacters(in: CharacterSet(charactersIn: "*"))
                return name == Self.assetName(version: info.version,
                                              architecture: Self.machineArchitecture())
                    ? String(parts[0]) : nil
            }
            .first
        guard let expected, !expected.isEmpty else {
            throw UpdateError.network("SHA256SUMS.txt 中没有本架构安装包的校验和")
        }
        let actual = try await Task.detached(priority: .userInitiated) {
            try Self.sha256(of: archive)
        }.value
        guard actual == expected.lowercased() else {
            try? FileManager.default.removeItem(at: archive)
            throw UpdateError.checksum
        }
    }

    nonisolated static func sha256(of file: URL) throws -> String {
        guard let handle = try? FileHandle(forReadingFrom: file) else {
            throw UpdateError.network("无法读取安装包进行校验")
        }
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let chunk = try handle.read(upToCount: 8 << 20)
            guard let chunk, !chunk.isEmpty else { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// hdiutil 挂载 → 拷贝到 /Applications（旧版入废纸篓）→ 卸载。
    /// 阻塞调用放后台线程；同步语义与 DMG 自助安装一致。
    private func installArchive(_ archive: URL, version: String) async throws -> URL {
        try await Task.detached(priority: .userInitiated) {
            let fileManager = FileManager.default
            let mount = fileManager.temporaryDirectory
                .appendingPathComponent("firesky-update-mount-\(version)")
            try? fileManager.removeItem(at: mount)
            try fileManager.createDirectory(at: mount, withIntermediateDirectories: true)
            func hdiutil(_ arguments: [String]) throws {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
                process.arguments = arguments
                process.standardOutput = Pipe()
                process.standardError = Pipe()
                try process.run()
                process.waitUntilExit()
                guard process.terminationStatus == 0 else {
                    throw UpdateError.network("hdiutil \(arguments.first ?? "") 失败")
                }
            }
            defer { try? hdiutil(["detach", mount.path, "-force"]) }
            try hdiutil(["attach", archive.path, "-nobrowse", "-readonly",
                         "-mountpoint", mount.path])
            defer { try? FileManager.default.removeItem(at: mount) }
            let mounted = mount.appendingPathComponent("FireSky.app")
            guard fileManager.fileExists(atPath: mounted.appendingPathComponent("Contents/MacOS/FireSky").path) else {
                throw UpdateError.network("安装包内未找到 FireSky.app")
            }
            let destination = fileManager.urls(for: .applicationDirectory, in: .localDomainMask)[0]
                .appendingPathComponent("FireSky.app")
            if fileManager.fileExists(atPath: destination.path) {
                try? fileManager.trashItem(at: destination, resultingItemURL: nil)
            }
            do {
                try fileManager.copyItem(at: mounted, to: destination)
            } catch {
                throw UpdateError.install(error.localizedDescription)
            }
            return destination
        }.value
    }

    private func friendlyUpdateError(_ error: Error) -> String {
        switch error {
        case UpdateError.checksum:
            return "安装包校验和不匹配，已放弃安装；请到 GitHub Releases 手动下载。"
        case UpdateError.network(let detail):
            let description = detail.lowercased().contains("timed out")
                || detail.lowercased().contains("connection")
                ? "无法连接 GitHub（检查网络或系统代理设置）。\(detail)"
                : detail
            return "更新检查/下载失败：\(description)"
        case UpdateError.install(let detail):
            return "安装失败：\(detail)。可到 GitHub Releases 手动下载 DMG 拖拽安装。"
        default:
            return error.localizedDescription
        }
    }
}

enum UpdateError: LocalizedError {
    case network(String)
    case checksum
    case install(String)

    var errorDescription: String? {
        switch self {
        case .network(let detail): detail
        case .checksum: "SHA256 校验不匹配"
        case .install(let detail): detail
        }
    }
}

private final class DMGDownloadDelegate: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let destination: URL
    private let expectedSizeHint: Int64
    private let onProgress: @Sendable (Int64, Int64, Double) -> Void
    private let onFinalBytes: @Sendable (Int64, Int64) -> Void

    private let lock = NSLock()
    private var continuation: CheckedContinuation<URL, Error>?
    private var task: URLSessionDownloadTask?
    private var movedURL: URL?
    private var recordedError: Error?
    private var cancelled = false
    private var lastReportTime = CFAbsoluteTimeGetCurrent()
    private var lastReportBytes: Int64 = 0
    private var smoothedSpeed: Double = 0

    init(
        destination: URL,
        expectedSizeHint: Int64,
        onProgress: @escaping @Sendable (Int64, Int64, Double) -> Void,
        onFinalBytes: @escaping @Sendable (Int64, Int64) -> Void
    ) {
        self.destination = destination
        self.expectedSizeHint = expectedSizeHint
        self.onProgress = onProgress
        self.onFinalBytes = onFinalBytes
    }

    func start(task: URLSessionDownloadTask, continuation: CheckedContinuation<URL, Error>) {
        lock.lock()
        if cancelled {
            lock.unlock()
            task.cancel()
            continuation.resume(throwing: CancellationError())
            return
        }
        self.task = task
        self.continuation = continuation
        self.lastReportTime = CFAbsoluteTimeGetCurrent()
        self.lastReportBytes = 0
        lock.unlock()
        task.resume()
    }

    func cancel() {
        lock.lock()
        cancelled = true
        let currentTask = task
        let cont = continuation
        continuation = nil
        lock.unlock()
        currentTask?.cancel()
        cont?.resume(throwing: CancellationError())
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        let now = CFAbsoluteTimeGetCurrent()
        var shouldReport = false
        var speed: Double = 0

        lock.lock()
        let elapsed = now - lastReportTime
        if elapsed >= 0.20 || (totalBytesExpectedToWrite > 0 && totalBytesWritten >= totalBytesExpectedToWrite) {
            let dt = max(elapsed, 0.001)
            let delta = max(totalBytesWritten - lastReportBytes, 0)
            let instant = Double(delta) / dt
            smoothedSpeed = smoothedSpeed > 0 ? (0.3 * instant + 0.7 * smoothedSpeed) : instant
            lastReportTime = now
            lastReportBytes = totalBytesWritten
            speed = smoothedSpeed
            shouldReport = true
        }
        lock.unlock()

        if shouldReport {
            onProgress(totalBytesWritten, totalBytesExpectedToWrite, speed)
        }
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        if let http = downloadTask.response as? HTTPURLResponse, http.statusCode != 200 {
            lock.lock()
            recordedError = UpdateError.network("下载失败：GitHub 返回状态 \(http.statusCode)")
            lock.unlock()
            return
        }
        do {
            try? FileManager.default.removeItem(at: destination)
            // Foundation 会在代理方法返回后立即删除临时文件，必须在回调内同步搬运。
            try FileManager.default.moveItem(at: location, to: destination)
            lock.lock()
            movedURL = destination
            lock.unlock()
        } catch {
            lock.lock()
            recordedError = error
            lock.unlock()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        session.finishTasksAndInvalidate()

        lock.lock()
        let wasCancelled = cancelled
        let moved = movedURL
        let recErr = recordedError
        let cont = continuation
        continuation = nil
        lock.unlock()

        guard let cont else { return }

        if wasCancelled || (error as? URLError)?.code == .cancelled {
            try? FileManager.default.removeItem(at: destination)
            cont.resume(throwing: CancellationError())
            return
        }
        if let error {
            try? FileManager.default.removeItem(at: destination)
            cont.resume(throwing: error)
            return
        }
        if let recErr {
            try? FileManager.default.removeItem(at: destination)
            cont.resume(throwing: recErr)
            return
        }
        guard let moved else {
            cont.resume(throwing: UpdateError.network("下载未生成临时文件"))
            return
        }

        let actualBytes = (try? FileManager.default.attributesOfItem(atPath: moved.path)[.size] as? NSNumber)?.int64Value ?? 0
        let responseExpected = (task.response as? HTTPURLResponse)?.expectedContentLength ?? -1
        let expectedTotal = responseExpected > 0 ? responseExpected : expectedSizeHint
        if expectedTotal > 0 && actualBytes != expectedTotal {
            try? FileManager.default.removeItem(at: moved)
            cont.resume(throwing: UpdateError.network("下载不完整（\(actualBytes)/\(expectedTotal) 字节）"))
            return
        }

        onFinalBytes(actualBytes, expectedTotal > 0 ? expectedTotal : actualBytes)
        cont.resume(returning: moved)
    }
}
