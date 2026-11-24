import XCTest
@testable import FireSkyMobile

private final class MockURLProtocol: URLProtocol {
    static let lock = NSLock()
    static var handler: ((URLRequest, Data?) throws -> (Int, JSONObject))?
    static var recordedRequests: [(method: String, url: URL, headers: [String: String], body: Data?)] = []

    static func reset(handler: @escaping (URLRequest, Data?) throws -> (Int, JSONObject)) {
        lock.lock()
        defer { lock.unlock() }
        recordedRequests = []
        self.handler = handler
    }

    static func clear() {
        lock.lock()
        defer { lock.unlock() }
        recordedRequests = []
        handler = nil
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        let bodyData = Self.extractBody(from: request)
        Self.lock.lock()
        Self.recordedRequests.append((
            method: request.httpMethod ?? "GET",
            url: request.url!,
            headers: request.allHTTPHeaderFields ?? [:],
            body: bodyData
        ))
        let currentHandler = Self.handler
        Self.lock.unlock()

        guard let currentHandler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }

        do {
            let (statusCode, json) = try currentHandler(request, bodyData)
            let data = try JSONSerialization.data(withJSONObject: json)
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: statusCode,
                httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/json"]
            )!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}

    private static func extractBody(from request: URLRequest) -> Data? {
        if let body = request.httpBody {
            return body
        }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 4096
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: bufferSize)
            if read > 0 {
                data.append(buffer, count: read)
            } else {
                break
            }
        }
        return data.isEmpty ? nil : data
    }
}

@MainActor
final class RemoteHttpTransportTests: XCTestCase {
    private var savedLanguage: L10n.Language = .system

    override func setUp() {
        super.setUp()
        savedLanguage = L10n.language
        L10n.language = .chinese
    }

    override func tearDown() {
        MockURLProtocol.clear()
        L10n.language = savedLanguage
        super.tearDown()
    }

    private func makeSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        return URLSession(configuration: config)
    }

    func testURLNormalization() throws {
        let a = try RemoteHttpTransport.normalizeBaseURL("https://demo.trycloudflare.com/")
        XCTAssertEqual(a.absoluteString, "https://demo.trycloudflare.com")
        XCTAssertEqual(
            a.appendingPathComponent("api/v1/firesky/desktop/status").absoluteString,
            "https://demo.trycloudflare.com/api/v1/firesky/desktop/status"
        )

        let b = try RemoteHttpTransport.normalizeBaseURL("https://demo.trycloudflare.com/api/v1/firesky/")
        XCTAssertEqual(b.absoluteString, "https://demo.trycloudflare.com")
        XCTAssertEqual(
            b.appendingPathComponent("api/v1/firesky/desktop/jobs").absoluteString,
            "https://demo.trycloudflare.com/api/v1/firesky/desktop/jobs"
        )

        let c = try RemoteHttpTransport.normalizeBaseURL("  http://192.168.1.50:8765/api/v1/firesky  ")
        XCTAssertEqual(c.absoluteString, "http://192.168.1.50:8765")

        XCTAssertThrowsError(try RemoteHttpTransport.normalizeBaseURL(""))
        XCTAssertThrowsError(try RemoteHttpTransport.normalizeBaseURL("ftp://example.com"))
    }

    func testAdaptivePollingIntervals() {
        XCTAssertEqual(RemoteHttpTransport.pollIntervalMilliseconds(elapsedSeconds: 0.0), 150)
        XCTAssertEqual(RemoteHttpTransport.pollIntervalMilliseconds(elapsedSeconds: 1.9), 150)
        XCTAssertEqual(RemoteHttpTransport.pollIntervalMilliseconds(elapsedSeconds: 2.0), 350)
        XCTAssertEqual(RemoteHttpTransport.pollIntervalMilliseconds(elapsedSeconds: 9.9), 350)
        XCTAssertEqual(RemoteHttpTransport.pollIntervalMilliseconds(elapsedSeconds: 10.0), 600)
    }

    func testFullRPCFlowWithProgressAndRelease() async throws {
        let token = "secret-bearer-token-0123456789abcdef"
        var pollCount = 0

        MockURLProtocol.reset { request, bodyData in
            let path = request.url?.path ?? ""
            if request.httpMethod == "GET" && path == "/api/v1/firesky/desktop/status" {
                return (200, [
                    "instance_id": "inst-1",
                    "running": true,
                    "workspace": "/Users/test/FireSkyData",
                    "protocol": 1,
                    "engine": "firesky-rust",
                ])
            }
            if request.httpMethod == "POST" && path == "/api/v1/firesky/desktop/jobs" {
                let body = try XCTUnwrap(bodyData.flatMap { try? JSONSerialization.jsonObject(with: $0) as? JSONObject })
                XCTAssertEqual(body["instance_id"] as? String, "inst-1")
                XCTAssertEqual(body["method"] as? String, "prepare")
                return (202, [
                    "request_id": body["request_id"] as? String ?? "",
                    "instance_id": "inst-1",
                    "status": "running",
                ])
            }
            if request.httpMethod == "GET" && path.hasPrefix("/api/v1/firesky/desktop/jobs/") {
                XCTAssertEqual(request.url?.query, "instance_id=inst-1")
                pollCount += 1
                if pollCount == 1 {
                    return (200, [
                        "request_id": "id",
                        "instance_id": "inst-1",
                        "status": "running",
                        "progress_seq": 1,
                        "progress": ["percent": 42.0, "source": "gfs", "message": "下载中"],
                    ])
                } else {
                    return (200, [
                        "request_id": "id",
                        "instance_id": "inst-1",
                        "status": "done",
                        "progress_seq": 1,
                        "progress": ["percent": 42.0, "source": "gfs", "message": "下载中"],
                        "result": ["report": ["ready": true]],
                    ])
                }
            }
            if request.httpMethod == "POST" && path.hasSuffix("/release") {
                let body = try XCTUnwrap(bodyData.flatMap { try? JSONSerialization.jsonObject(with: $0) as? JSONObject })
                XCTAssertEqual(body["instance_id"] as? String, "inst-1")
                return (200, ["released": true])
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(path)")
            return (404, [:])
        }

        let transport = try RemoteHttpTransport(
            urlString: "https://demo.trycloudflare.com/api/v1/firesky/",
            token: token,
            session: makeSession()
        )
        let client = EngineClient { transport }
        try client.start(workspace: "/nonexistent/local/path/ignored-for-remote", transport: transport)

        var progressEvents: [JSONObject] = []
        let result = try await client.call("prepare", ["session": "s1"]) { event in
            progressEvents.append(event)
        }

        XCTAssertEqual((result["report"] as? JSONObject)?["ready"] as? Bool, true)
        XCTAssertEqual(progressEvents.count, 1)
        XCTAssertEqual(progressEvents.first?["percent"] as? Double, 42.0)

        let paths = MockURLProtocol.recordedRequests.map { "\($0.method) \($0.url.path)" }
        XCTAssertEqual(paths.count, 5)
        XCTAssertEqual(paths[0], "GET /api/v1/firesky/desktop/status")
        XCTAssertEqual(paths[1], "POST /api/v1/firesky/desktop/jobs")
        XCTAssertTrue(paths[2].hasPrefix("GET /api/v1/firesky/desktop/jobs/"))
        XCTAssertTrue(paths[3].hasPrefix("GET /api/v1/firesky/desktop/jobs/"))
        XCTAssertTrue(paths[4].hasPrefix("POST /api/v1/firesky/desktop/jobs/") && paths[4].hasSuffix("/release"))

        for recorded in MockURLProtocol.recordedRequests {
            XCTAssertEqual(recorded.headers["Authorization"], "Bearer \(token)")
            XCTAssertEqual(recorded.headers["Accept"], "application/json")
            XCTAssertNil(recorded.headers["Origin"])
        }
    }

    func test401UnauthorizedReturnsFriendlyErrorWithoutLeakingToken() async throws {
        let token = "super-secret-token-should-never-leak-123456"
        MockURLProtocol.reset { _, _ in
            (401, ["error": ["code": "authentication_required", "message": "HTTP authentication required"]])
        }

        let session = makeSession()
        do {
            _ = try await RemoteHttpTransport.testConnection(
                urlString: "https://demo.trycloudflare.com",
                token: token,
                session: session
            )
            XCTFail("Expected 401 to throw EngineError")
        } catch let error as EngineError {
            XCTAssertTrue(error.message.contains("鉴权失败"))
            XCTAssertFalse(error.message.contains(token))
        }

        let transport = try RemoteHttpTransport(
            urlString: "https://demo.trycloudflare.com",
            token: token,
            session: session
        )
        let client = EngineClient { transport }
        try client.start(workspace: "/ignored", transport: transport)
        do {
            _ = try await client.call("hello")
            XCTFail("Expected 401 to throw EngineError")
        } catch let error as EngineError {
            XCTAssertTrue(error.message.contains("鉴权失败"))
            XCTAssertFalse(error.message.contains(token))
        }
    }

    func test403UntrustedHostReturnsMacSettingsHint() async throws {
        MockURLProtocol.reset { _, _ in
            (403, ["error": ["code": "untrusted_host", "message": "local HTTP requires a loopback Host"]])
        }

        do {
            _ = try await RemoteHttpTransport.testConnection(
                urlString: "http://192.168.1.20:8765",
                token: "",
                session: makeSession()
            )
            XCTFail("Expected 403 untrusted_host to throw EngineError")
        } catch let error as EngineError {
            XCTAssertTrue(error.message.contains("Mac 端未开启远程访问"))
            XCTAssertTrue(error.message.contains("局域网与隧道服务"))
        }
    }

    func test409EngineExitedRefreshesInstanceIDAndRetriesOnce() async throws {
        var statusCalls = 0
        var startCalls = 0

        MockURLProtocol.reset { request, bodyData in
            let path = request.url?.path ?? ""
            if request.httpMethod == "GET" && path == "/api/v1/firesky/desktop/status" {
                statusCalls += 1
                let id = statusCalls == 1 ? "inst-old" : "inst-new"
                return (200, [
                    "instance_id": id,
                    "running": true,
                    "workspace": "/Users/test/FireSkyData",
                    "protocol": 1,
                    "engine": "firesky-rust",
                ])
            }
            if request.httpMethod == "POST" && path == "/api/v1/firesky/desktop/jobs" {
                startCalls += 1
                let body = try XCTUnwrap(bodyData.flatMap { try? JSONSerialization.jsonObject(with: $0) as? JSONObject })
                let instanceID = body["instance_id"] as? String
                if instanceID == "inst-old" {
                    return (409, ["error": ["code": "engine_exited", "message": "desktop runtime changed"]])
                }
                XCTAssertEqual(instanceID, "inst-new")
                return (202, [
                    "request_id": body["request_id"] as? String ?? "",
                    "instance_id": "inst-new",
                    "status": "running",
                ])
            }
            if request.httpMethod == "GET" && path.hasPrefix("/api/v1/firesky/desktop/jobs/") {
                XCTAssertEqual(request.url?.query, "instance_id=inst-new")
                return (200, [
                    "request_id": "id",
                    "instance_id": "inst-new",
                    "status": "done",
                    "progress_seq": 0,
                    "result": ["protocol": 1, "engine": "firesky-rust"],
                ])
            }
            if request.httpMethod == "POST" && path.hasSuffix("/release") {
                return (200, ["released": true])
            }
            XCTFail("Unexpected request: \(request.httpMethod ?? "") \(path)")
            return (404, [:])
        }

        let transport = try RemoteHttpTransport(
            urlString: "https://demo.trycloudflare.com",
            token: "valid-token-0123456789abcdef01234567",
            session: makeSession()
        )
        let client = EngineClient { transport }
        try client.start(workspace: "/ignored", transport: transport)

        let result = try await client.call("hello")
        XCTAssertEqual(result["protocol"] as? Int, 1)
        XCTAssertEqual(statusCalls, 2)
        XCTAssertEqual(startCalls, 2)
    }

    func test404SaveCamsKeyReturnsFriendlySecurityMessage() async throws {
        MockURLProtocol.reset { request, bodyData in
            let path = request.url?.path ?? ""
            if request.httpMethod == "GET" && path == "/api/v1/firesky/desktop/status" {
                return (200, [
                    "instance_id": "inst-1",
                    "running": true,
                    "workspace": "/Users/test/FireSkyData",
                    "protocol": 1,
                    "engine": "firesky-rust",
                ])
            }
            if request.httpMethod == "POST" && path == "/api/v1/firesky/desktop/jobs" {
                let body = try XCTUnwrap(bodyData.flatMap { try? JSONSerialization.jsonObject(with: $0) as? JSONObject })
                XCTAssertEqual(body["method"] as? String, "save_cams_key")
                return (404, ["error": ["code": "invalid_request", "message": "desktop workflow method is not available over HTTP"]])
            }
            return (404, [:])
        }

        let transport = try RemoteHttpTransport(
            urlString: "https://demo.trycloudflare.com",
            token: "token-0123456789abcdef0123456789abcdef",
            session: makeSession()
        )
        let client = EngineClient { transport }
        try client.start(workspace: "/ignored", transport: transport)

        do {
            _ = try await client.call("save_cams_key", ["key": "abc"])
            XCTFail("Expected 404 save_cams_key to throw EngineError")
        } catch let error as EngineError {
            XCTAssertTrue(error.message.contains("远程模式下不支持通过网络修改 Mac 的 CAMS Key"))
        }
    }
}
