import Foundation
import Security

/// 远程服务器配置（本机引擎 vs Mac 远程 HTTP / Cloudflare Tunnel）。
struct RemoteServerConfig: Equatable {
    var mode: String // "local" | "remote"
    var serverURL: String

    var isRemote: Bool { mode == "remote" }

    static let defaultMode = "local"
}

/// 远程 Mac 服务 `/desktop/status` 响应摘要。
struct RemoteServerStatus: Equatable {
    let instanceID: String
    let workspace: String
    let engine: String
    let protocolVersion: Int
}

/// Keychain 凭据存储：仅把 Bearer Token 存入 iOS Keychain，绝不写入 UserDefaults 或日志。
enum ServerCredentialStore {
    private static let service = "com.firesky.mobile.remote-server"
    private static let account = "bearer-token"

    static func loadToken() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess,
              let data = item as? Data,
              let token = String(data: data, encoding: .utf8),
              !token.isEmpty else {
            return nil
        }
        return token
    }

    static func hasToken() -> Bool {
        loadToken() != nil
    }

    static func saveToken(_ rawToken: String) throws {
        let token = rawToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else {
            deleteToken()
            return
        }
        guard let data = token.data(using: .utf8) else {
            throw EngineError(message: L10n.text("访问令牌格式无效。", "Invalid access token format."))
        }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        var addQuery = query
        addQuery[kSecValueData as String] = data
        addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(addQuery as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw EngineError(message: L10n.text(
                "无法保存访问令牌到钥匙串（错误码 \(status)）。",
                "Failed to save access token to Keychain (status \(status))."
            ))
        }
    }

    @discardableResult
    static func deleteToken() -> OSStatus {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        return SecItemDelete(query as CFDictionary)
    }
}

/// 远程 HTTP 传输层：通过 Mac HTTP `/api/v1/firesky/desktop/*` 异步作业端点
/// 承载与桌面 / FFI 完全同构的 JSON-lines 协议帧。
@MainActor
final class RemoteHttpTransport: EngineTransport {
    var onFrame: ((Data) -> Void)?

    let baseURL: URL
    private let token: String
    private let session: URLSession
    private var started = false
    private var instanceID: String?
    private var statusTask: Task<String, Error>?
    private var inFlightTasks: [String: Task<Void, Never>] = [:]
    private var cancelledIDs: Set<String> = []

    init(urlString: String, token: String, session: URLSession = .shared) throws {
        self.baseURL = try Self.normalizeBaseURL(urlString)
        self.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        self.session = session
    }

    // MARK: - URL 归一化与轮询节奏

    nonisolated static func normalizeBaseURL(_ raw: String) throws -> URL {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              var components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              (scheme == "http" || scheme == "https"),
              let host = components.host,
              !host.isEmpty else {
            throw EngineError(message: L10n.text(
                "请输入有效的服务器地址（以 http:// 或 https:// 开头）。",
                "Enter a valid server URL starting with http:// or https://."
            ))
        }
        components.scheme = scheme
        components.query = nil
        components.fragment = nil

        var path = components.path
        while path.hasSuffix("/") {
            path.removeLast()
        }
        if path.lowercased().hasSuffix("/api/v1/firesky") {
            path = String(path.dropLast("/api/v1/firesky".count))
            while path.hasSuffix("/") {
                path.removeLast()
            }
        }
        components.path = path

        guard let normalized = components.url else {
            throw EngineError(message: L10n.text(
                "服务器地址无法解析，请检查格式。",
                "Unable to parse the server URL."
            ))
        }
        return normalized
    }

    nonisolated static func pollIntervalMilliseconds(elapsedSeconds: TimeInterval) -> UInt64 {
        if elapsedSeconds < 2.0 {
            return 150
        }
        if elapsedSeconds < 10.0 {
            return 350
        }
        return 600
    }

    // MARK: - 连通性测试

    nonisolated static func testConnection(
        urlString: String,
        token: String,
        session: URLSession = .shared
    ) async throws -> RemoteServerStatus {
        let baseURL = try normalizeBaseURL(urlString)
        let cleanToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        let request = makeRequest(
            baseURL: baseURL,
            relativePath: "api/v1/firesky/desktop/status",
            method: "GET",
            token: cleanToken
        )
        let (data, response) = try await performData(request, session: session)
        let status = try parseStatusResponse(data: data, response: response)
        guard status.protocolVersion == 1 else {
            throw EngineError(message: L10n.text(
                "远程引擎协议版本不匹配（期望 1，实际 \(status.protocolVersion)）。",
                "Remote engine protocol mismatch (expected 1, got \(status.protocolVersion))."
            ))
        }
        return status
    }

    // MARK: - EngineTransport

    func start(workspace: String) throws {
        started = true
        instanceID = nil
        statusTask?.cancel()
        statusTask = nil
        for task in inFlightTasks.values {
            task.cancel()
        }
        inFlightTasks.removeAll()
        cancelledIDs.removeAll()
    }

    func send(_ frame: Data) throws {
        guard started else {
            throw EngineError(message: L10n.text(
                "远程连接尚未建立，请重新连接。",
                "Remote connection is not active; please reconnect."
            ))
        }
        guard let request = try? JSONSerialization.jsonObject(with: frame) as? JSONObject,
              let id = request["id"] as? String,
              let method = request["method"] as? String else {
            throw EngineError(message: L10n.text(
                "请求帧不是有效的 JSON。",
                "Request frame is not valid JSON."
            ))
        }
        let params = request["params"] as? JSONObject ?? [:]
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.executeRPC(id: id, method: method, params: params)
            self.inFlightTasks.removeValue(forKey: id)
        }
        inFlightTasks[id] = task
    }

    func cancel(requestID: String) {
        cancelledIDs.insert(requestID)
        guard started else { return }
        let currentInstance = instanceID
        Task { @MainActor [weak self] in
            guard let self, let instance = currentInstance ?? self.instanceID else { return }
            await self.sendCancel(requestID: requestID, instanceID: instance)
        }
    }

    func stop() {
        started = false
        onFrame = nil
        instanceID = nil
        statusTask?.cancel()
        statusTask = nil
        for task in inFlightTasks.values {
            task.cancel()
        }
        inFlightTasks.removeAll()
        cancelledIDs.removeAll()
    }

    // MARK: - RPC 生命周期

    private func executeRPC(id: String, method: String, params: JSONObject) async {
        do {
            if cancelledIDs.contains(id) {
                emitCancelled(id: id)
                return
            }

            var currentInstance = try await ensureInstanceID()
            if cancelledIDs.contains(id) {
                emitCancelled(id: id)
                return
            }

            var startOutcome = try await postStartJob(
                requestID: id,
                instanceID: currentInstance,
                method: method,
                params: params
            )

            if case .engineExited = startOutcome {
                currentInstance = try await refreshInstanceID()
                if cancelledIDs.contains(id) {
                    emitCancelled(id: id)
                    return
                }
                startOutcome = try await postStartJob(
                    requestID: id,
                    instanceID: currentInstance,
                    method: method,
                    params: params
                )
                if case .engineExited = startOutcome {
                    throw EngineError(message: L10n.text(
                        "Mac 端服务实例已刷新但仍冲突，请重试。",
                        "Mac server instance changed again; please retry."
                    ))
                }
            }

            if cancelledIDs.contains(id) {
                await sendCancel(requestID: id, instanceID: currentInstance)
            }

            try await pollUntilTerminal(id: id, method: method, instanceID: currentInstance)
        } catch is CancellationError {
            return
        } catch let error as EngineError {
            guard started else { return }
            if cancelledIDs.contains(id) {
                emitCancelled(id: id)
            } else {
                emitError(id: id, message: error.message)
            }
        } catch {
            guard started else { return }
            if cancelledIDs.contains(id) {
                emitCancelled(id: id)
            } else {
                emitError(id: id, message: Self.friendlyNetworkError(error))
            }
        }
    }

    private enum StartOutcome {
        case accepted
        case engineExited
    }

    private func ensureInstanceID() async throws -> String {
        if let instanceID {
            return instanceID
        }
        return try await refreshInstanceID()
    }

    private func refreshInstanceID() async throws -> String {
        instanceID = nil
        if let existing = statusTask {
            return try await existing.value
        }
        let task = Task<String, Error> { [baseURL, token, session] in
            let request = Self.makeRequest(
                baseURL: baseURL,
                relativePath: "api/v1/firesky/desktop/status",
                method: "GET",
                token: token
            )
            let (data, response) = try await Self.performData(request, session: session)
            let status = try Self.parseStatusResponse(data: data, response: response)
            guard status.protocolVersion == 1 else {
                throw EngineError(message: L10n.text(
                    "远程引擎协议版本不匹配（期望 1，实际 \(status.protocolVersion)）。",
                    "Remote engine protocol mismatch (expected 1, got \(status.protocolVersion))."
                ))
            }
            return status.instanceID
        }
        statusTask = task
        defer { statusTask = nil }
        let fetched = try await task.value
        instanceID = fetched
        return fetched
    }

    private func postStartJob(
        requestID: String,
        instanceID: String,
        method: String,
        params: JSONObject
    ) async throws -> StartOutcome {
        let body: JSONObject = [
            "request_id": requestID,
            "instance_id": instanceID,
            "method": method,
            "params": params,
        ]
        let bodyData = try JSONSerialization.data(withJSONObject: body)
        let request = Self.makeRequest(
            baseURL: baseURL,
            relativePath: "api/v1/firesky/desktop/jobs",
            method: "POST",
            token: token,
            body: bodyData
        )
        let (data, response) = try await Self.performData(request, session: session)
        let statusCode = response.statusCode
        if (200..<300).contains(statusCode) {
            return .accepted
        }
        if statusCode == 409, Self.parseErrorCode(from: data) == "engine_exited" {
            self.instanceID = nil
            return .engineExited
        }
        throw Self.mapHTTPError(statusCode: statusCode, data: data, method: method)
    }

    private func pollUntilTerminal(id: String, method: String, instanceID: String) async throws {
        let startedAt = Date()
        var lastSeq: UInt64 = 0

        while started {
            try Task.checkCancellation()
            let elapsed = Date().timeIntervalSince(startedAt)
            let delayMs = Self.pollIntervalMilliseconds(elapsedSeconds: elapsed)
            try await Task.sleep(nanoseconds: delayMs * 1_000_000)
            try Task.checkCancellation()
            guard started else { return }

            let request = Self.makeRequest(
                baseURL: baseURL,
                relativePath: "api/v1/firesky/desktop/jobs/\(id)",
                queryItems: [URLQueryItem(name: "instance_id", value: instanceID)],
                method: "GET",
                token: token
            )
            let (data, response) = try await Self.performData(request, session: session)
            let statusCode = response.statusCode

            if statusCode == 409, Self.parseErrorCode(from: data) == "engine_exited" {
                self.instanceID = nil
                throw EngineError(message: L10n.text(
                    "Mac 端服务已重启，请重试。",
                    "Mac server restarted; please retry."
                ))
            }
            guard (200..<300).contains(statusCode) else {
                throw Self.mapHTTPError(statusCode: statusCode, data: data, method: method)
            }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? JSONObject else {
                throw EngineError(message: L10n.text(
                    "远程服务器返回了无法解析的响应。",
                    "Remote server returned an unreadable response."
                ))
            }

            if let seqNumber = json["progress_seq"] as? NSNumber {
                let seq = seqNumber.uint64Value
                if seq > lastSeq, let progress = json["progress"] as? JSONObject {
                    lastSeq = seq
                    emit(["id": id, "type": "progress", "event": progress])
                }
            }

            let jobStatus = json["status"] as? String ?? ""
            switch jobStatus {
            case "running":
                continue
            case "done":
                await releaseJob(requestID: id, instanceID: instanceID)
                let result = json["result"] as? JSONObject ?? [:]
                emit(["id": id, "type": "result", "result": result])
                return
            case "failed", "cancelled":
                await releaseJob(requestID: id, instanceID: instanceID)
                var errorPayload = json["error"] as? JSONObject ?? [:]
                if errorPayload["message"] == nil {
                    errorPayload["message"] = jobStatus == "cancelled"
                        ? "desktop operation cancelled"
                        : L10n.text("远程任务执行失败。", "Remote job failed.")
                }
                emit(["id": id, "type": "error", "error": errorPayload])
                return
            default:
                throw EngineError(message: L10n.text(
                    "远程任务返回未知状态：\(jobStatus)",
                    "Remote job returned unknown status: \(jobStatus)"
                ))
            }
        }
    }

    private func sendCancel(requestID: String, instanceID: String) async {
        guard let body = try? JSONSerialization.data(withJSONObject: ["instance_id": instanceID]) else {
            return
        }
        let request = Self.makeRequest(
            baseURL: baseURL,
            relativePath: "api/v1/firesky/desktop/jobs/\(requestID)/cancel",
            method: "POST",
            token: token,
            body: body
        )
        _ = try? await Self.performData(request, session: session)
    }

    private func releaseJob(requestID: String, instanceID: String) async {
        guard let body = try? JSONSerialization.data(withJSONObject: ["instance_id": instanceID]) else {
            return
        }
        let request = Self.makeRequest(
            baseURL: baseURL,
            relativePath: "api/v1/firesky/desktop/jobs/\(requestID)/release",
            method: "POST",
            token: token,
            body: body
        )
        _ = try? await Self.performData(request, session: session)
    }

    // MARK: - 帧输出

    private func emit(_ frame: JSONObject) {
        guard started, var data = try? JSONSerialization.data(withJSONObject: frame) else { return }
        data.append(10)
        onFrame?(data)
    }

    private func emitError(id: String, message: String) {
        emit(["id": id, "type": "error", "error": ["message": message]])
    }

    private func emitCancelled(id: String) {
        emit(["id": id, "type": "error", "error": ["code": "cancelled", "message": "desktop operation cancelled"]])
    }

    // MARK: - HTTP 工具与错误映射

    private nonisolated static func makeRequest(
        baseURL: URL,
        relativePath: String,
        queryItems: [URLQueryItem] = [],
        method: String,
        token: String,
        body: Data? = nil
    ) -> URLRequest {
        var url = baseURL.appendingPathComponent(relativePath)
        if !queryItems.isEmpty, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.queryItems = queryItems
            if let composed = components.url {
                url = composed
            }
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        }
        return request
    }

    private nonisolated static func performData(
        _ request: URLRequest,
        session: URLSession
    ) async throws -> (Data, HTTPURLResponse) {
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw EngineError(message: L10n.text(
                    "远程服务器响应格式异常。",
                    "Unexpected response from remote server."
                ))
            }
            return (data, http)
        } catch let error as EngineError {
            throw error
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw EngineError(message: friendlyNetworkError(error))
        }
    }

    private nonisolated static func parseStatusResponse(
        data: Data,
        response: HTTPURLResponse
    ) throws -> RemoteServerStatus {
        guard (200..<300).contains(response.statusCode) else {
            throw mapHTTPError(statusCode: response.statusCode, data: data, method: "status")
        }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? JSONObject,
              let instanceID = json["instance_id"] as? String,
              !instanceID.isEmpty,
              json["running"] as? Bool == true else {
            throw EngineError(message: L10n.text(
                "远程服务状态响应无效，请确认目标为 FireSky Mac 桌面服务。",
                "Invalid status response; ensure the URL points to a FireSky Mac server."
            ))
        }
        let workspace = json["workspace"] as? String ?? ""
        let engine = json["engine"] as? String ?? "firesky-rust"
        let protocolVersion = (json["protocol"] as? NSNumber)?.intValue ?? 0
        return RemoteServerStatus(
            instanceID: instanceID,
            workspace: workspace,
            engine: engine,
            protocolVersion: protocolVersion
        )
    }

    private nonisolated static func parseErrorObject(from data: Data) -> JSONObject? {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? JSONObject else {
            return nil
        }
        return json["error"] as? JSONObject
    }

    private nonisolated static func parseErrorCode(from data: Data) -> String? {
        parseErrorObject(from: data)?["code"] as? String
    }

    nonisolated static func mapHTTPError(statusCode: Int, data: Data, method: String) -> EngineError {
        let errorObject = parseErrorObject(from: data)
        let code = errorObject?["code"] as? String ?? ""
        let serverMessage = errorObject?["message"] as? String ?? ""
        let bodyText = String(data: data, encoding: .utf8) ?? ""

        if statusCode == 401 {
            return EngineError(message: L10n.text(
                "服务器鉴权失败：请检查访问令牌（Token）是否正确。",
                "Authentication failed: check your access token."
            ))
        }
        if statusCode == 403 && (code == "untrusted_host" || bodyText.contains("untrusted_host")) {
            return EngineError(message: L10n.text(
                "Mac 端未开启远程访问：请在 Mac「设置 → 高级 → 局域网与隧道服务」开启令牌认证或局域网访问。",
                "Remote access is disabled on Mac: enable token auth or LAN access in Mac Settings → Advanced."
            ))
        }
        if statusCode == 404 && method == "save_cams_key" {
            return EngineError(message: L10n.text(
                "远程模式下不支持通过网络修改 Mac 的 CAMS Key，请在 Mac 端本地配置。",
                "Saving CAMS Key remotely is disabled for security; configure it locally on your Mac."
            ))
        }
        if statusCode == 429 {
            return EngineError(message: L10n.text(
                "Mac 端任务队列繁忙，请稍后重试。",
                "Mac server is busy; please try again shortly."
            ))
        }
        if !serverMessage.isEmpty {
            return EngineError(message: serverMessage)
        }
        return EngineError(message: L10n.text(
            "远程服务器请求失败（HTTP \(statusCode)）。",
            "Remote server request failed (HTTP \(statusCode))."
        ))
    }

    private nonisolated static func friendlyNetworkError(_ error: Error) -> String {
        if let urlError = error as? URLError {
            switch urlError.code {
            case .timedOut:
                return L10n.text("连接远程服务器超时，请检查网络或隧道状态。", "Connection to remote server timed out.")
            case .cannotFindHost, .dnsLookupFailed:
                return L10n.text("无法解析服务器域名，请检查服务器地址。", "Could not resolve server host; check the URL.")
            case .cannotConnectToHost, .networkConnectionLost, .notConnectedToInternet:
                return L10n.text("无法连接到远程服务器，请确认 Mac 服务已开启且网络可达。", "Could not connect to remote server; ensure Mac service is running.")
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate, .serverCertificateNotYetValid:
                return L10n.text("TLS 安全连接失败，请检查 HTTPS 证书或隧道地址。", "TLS connection failed; check the HTTPS certificate or tunnel URL.")
            case .appTransportSecurityRequiresSecureConnection:
                return L10n.text("当前地址需要 HTTPS，或仅允许局域网内使用 HTTP。", "HTTPS is required for non-local hosts; HTTP is allowed on LAN only.")
            default:
                break
            }
        }
        return error.localizedDescription
    }
}
