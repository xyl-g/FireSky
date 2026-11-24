import XCTest
@testable import FireSkyMobile

/// MockTransport / EngineClient 协议语义测试：握手、check→prepare→forecast
/// 三链、进度帧与取消（与桌面 JSON-lines 验收矩阵同语义）。
@MainActor
final class MockTransportTests: XCTestCase {
    private func makeClient() throws -> (EngineClient, URL) {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("firesky-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let transport = MockTransport()
        let client = EngineClient { transport }
        try client.start(workspace: dir.path)
        return (client, dir)
    }

    func testHelloHandshake() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }
        let hello = try await client.call("hello")
        XCTAssertEqual(hello["protocol"] as? Int, 1)
        XCTAssertEqual(hello["engine"] as? String, "firesky-rust-mock")
        XCTAssertNotNil(hello["cams_credentials"] as? JSONObject)
    }

    func testCheckPrepareForecastChain() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }
        // check：未就绪 + 会话令牌
        let check = try await client.call("check", ["kind": "point", "payload": [:] as JSONObject])
        let session = try XCTUnwrap(check["session"] as? String)
        let report = try XCTUnwrap(check["report"] as? JSONObject)
        XCTAssertEqual(report["ready"] as? Bool, false)
        // prepare：进度帧非空，最终就绪
        var percentages: [Double] = []
        let prepared = try await client.call("prepare", ["session": session]) { event in
            if let percent = event["percent"] as? Double { percentages.append(percent) }
        }
        XCTAssertEqual((prepared["report"] as? JSONObject)?["ready"] as? Bool, true)
        XCTAssertFalse(percentages.isEmpty)
        // forecast：GFS/EC 双模型
        let forecast = try await client.call("forecast", ["session": session, "confirmed": true])
        let models = try XCTUnwrap(forecast["model_results"] as? JSONObject)
        XCTAssertNotNil(models["gfs"] as? JSONObject)
        XCTAssertNotNil(models["ec"] as? JSONObject)
        // dismiss
        _ = try await client.call("dismiss", ["session": "mock-x"])
    }

    func testCancelMarksRequest() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }
        var progressCalls = 0
        let task = Task {
            _ = try await client.call("check", ["kind": "point", "payload": [:] as JSONObject],
                                      progress: { _ in progressCalls += 1 })
        }
        try await Task.sleep(for: .milliseconds(200))
        task.cancel()
        do {
            _ = try await task.value
            XCTFail("取消后的请求应当抛错")
        } catch is CancellationError {
            // 预期：任务取消 → CancellationError
        } catch let error as EngineError {
            // 或引擎侧返回「已取消」错误帧
            XCTAssertTrue(error.message.contains("取消"))
        }
    }

    func testSearchAndMetadata() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }
        let point = try await client.call("search", ["query": "西湖"])
        XCTAssertEqual(point["name"] as? String, "西湖")
        let metadata = try await client.call("metadata", ["lat": 30.2431, "lon": 120.1503])
        XCTAssertNotNil(metadata["elevation_m"] as? NSNumber)
        XCTAssertEqual(metadata["timezone_name"] as? String, "Asia/Shanghai")
    }

    func testStoreErrorIsolationAndStaleBannerHygiene() {
        let store = Store()
        store.page = .point
        store.fail(EngineError(message: "数据错误"), page: .data)
        XCTAssertEqual(store.errorText(for: .data), "数据错误")
        XCTAssertEqual(store.errorText(for: .point), "")
        XCTAssertEqual(store.errorText(for: .settings), "")
        store.clearError()
        XCTAssertEqual(store.errorText(for: .data), "")

        XCTAssertFalse(store.resultParametersStale)
        store.adoptResult(["model_results": [:]], kind: .point, params: store.payload())
        XCTAssertFalse(store.resultParametersStale)
        store.phenomenon = "dawn"
        XCTAssertTrue(store.resultParametersStale)
        store.page = .settings
        XCTAssertFalse(store.resultParametersStale)
    }

    func testCycleAgeConfirmationFlag() {
        let store = Store()
        store.ready = true
        store.rows = [
            DataRow(source: "gfs", ready: true, old: false, verified: true, latest: true, missing: 0),
            DataRow(source: "ecmwf", ready: true, old: true, verified: true, latest: false, missing: 0),
        ]
        XCTAssertTrue(store.needsCycleAgeConfirmation)

        store.rows = [
            DataRow(source: "gfs", ready: true, old: false, verified: true, latest: true, missing: 0),
            DataRow(source: "ecmwf", ready: true, old: false, verified: true, latest: true, missing: 0),
        ]
        XCTAssertFalse(store.needsCycleAgeConfirmation)
    }

    func testRetentionAndInventoryCategoriesOverMock() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }

        let inv = try await client.call("inventory")
        let categories = try XCTUnwrap(inv["categories"] as? [JSONObject])
        XCTAssertEqual(
            Set(categories.compactMap { $0["category"] as? String }),
            Set(["point-forecasts", "regional-forecasts", "map-frames", "reports", "satellite", "srtm"])
        )

        let saved = try await client.call("save_retention_settings", ["settings": ["gfs_cycles": 4]])
        XCTAssertEqual(saved["configured"] as? Bool, true)
        let applied = try await client.call("apply_retention_settings")
        XCTAssertEqual(applied["configured"] as? Bool, true)
        let result = try XCTUnwrap(applied["result"] as? JSONObject)
        XCTAssertEqual((result["removed_count"] as? NSNumber)?.intValue, 3)

        let sync = try await client.call("data_sync_run", ["sources": ["gfs"], "force": true])
        XCTAssertNotNil((sync["sources"] as? JSONObject)?["gfs"])
    }
}
