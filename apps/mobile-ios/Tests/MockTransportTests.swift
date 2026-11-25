import XCTest
@testable import FireSkyMobile

/// MockTransport / EngineClient / Regional / Nowcast 协议与模型测试。
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
        let regions = try XCTUnwrap(hello["regions"] as? [JSONObject])
        XCTAssertEqual(regions.first?["code"] as? String, "china")
    }

    func testCheckPrepareForecastChain() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }
        let check = try await client.call("check", ["kind": "point", "payload": [:] as JSONObject])
        let session = try XCTUnwrap(check["session"] as? String)
        let report = try XCTUnwrap(check["report"] as? JSONObject)
        XCTAssertEqual(report["ready"] as? Bool, false)

        var percentages: [Double] = []
        let prepared = try await client.call("prepare", ["session": session]) { event in
            if let percent = event["percent"] as? Double { percentages.append(percent) }
        }
        XCTAssertEqual((prepared["report"] as? JSONObject)?["ready"] as? Bool, true)
        XCTAssertFalse(percentages.isEmpty)

        let forecast = try await client.call("forecast", ["session": session, "confirmed": true])
        let models = try XCTUnwrap(forecast["model_results"] as? JSONObject)
        XCTAssertNotNil(models["gfs"] as? JSONObject)
        XCTAssertNotNil(models["ec"] as? JSONObject)

        _ = try await client.call("dismiss", ["session": "mock-x"])
    }

    func testRegionalCheckPrepareForecastAndHistoryRestore() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }

        let check = try await client.call("check", [
            "kind": "regional",
            "payload": ["regions": ["china"], "model_source": "gfs"] as JSONObject,
        ])
        let session = try XCTUnwrap(check["session"] as? String)
        XCTAssertEqual(check["kind"] as? String, "regional")

        let prepared = try await client.call("prepare", ["session": session])
        XCTAssertEqual((prepared["report"] as? JSONObject)?["ready"] as? Bool, true)

        let forecast = try await client.call("forecast", ["session": session, "confirmed": true])
        let regional = RegionalForecast(result: forecast)
        let gfs = regional.model("gfs")
        XCTAssertTrue(gfs.available)
        XCTAssertFalse(gfs.cells.isEmpty)
        XCTAssertEqual(gfs.cityRankingStatus, "available")
        XCTAssertEqual(gfs.cityRanking.first?.name, "杭州")
        XCTAssertNotNil(gfs.hit(lat: 30.0, lon: 121.0, maxDistanceDeg: 2.0))

        // History restore switches Store to regional mode
        let store = Store()
        try store.engine.start(workspace: dir.path, transport: MockTransport())
        store.online = true
        store.engine.connected = true
        let item = MobileTaskHistoryItem(
            taskID: MockTransport.regionalTaskID,
            method: "forecast",
            status: "done",
            createdAt: "2026-11-20T11:15:00.000Z",
            cancelRequested: false,
            percent: nil,
            retryKind: "regional",
            retryPayload: ["regions": ["china"], "target_date": "2026-11-20",
                           "phenomenon": "dusk", "model_source": "gfs"],
            canRestoreResult: true
        )
        store.restoreTaskResult(item)
        try await Task.sleep(for: .milliseconds(250))
        XCTAssertEqual(store.workbenchMode, "regional")
        XCTAssertEqual(store.resultKind, .regional)
        XCTAssertTrue(store.regionalForecast.model("gfs").available)
        XCTAssertFalse(store.regionalForecast.model("gfs").cityRanking.isEmpty)
    }

    func testNowcastCheckPrepareForecastAndHistoryRestore() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }

        let check = try await client.call("check", [
            "kind": "nowcast",
            "payload": ["name": "杭州", "lat": 30.2741, "lon": 120.1551] as JSONObject,
        ])
        let session = try XCTUnwrap(check["session"] as? String)
        XCTAssertEqual(check["kind"] as? String, "nowcast")

        let prepared = try await client.call("prepare", ["session": session])
        XCTAssertEqual((prepared["report"] as? JSONObject)?["ready"] as? Bool, true)

        let forecast = try await client.call("forecast", ["session": session, "confirmed": true])
        let presentation = NowcastPresentation(report: forecast)
        XCTAssertEqual(presentation.observation["source"] as? String, "himawari-ahi")
        XCTAssertEqual(presentation.lagrangianTrendKPerHour ?? 0, -2.4, accuracy: 0.01)
        XCTAssertEqual(presentation.horizonGapScore ?? 0, 0.78, accuracy: 0.01)
        XCTAssertEqual(presentation.overcastShadowFactor ?? 0, 0.12, accuracy: 0.01)
        XCTAssertFalse(presentation.profileCells.isEmpty)

        let curve = ForecastComparison.curve(report: forecast, model: "nowcast", timezone: "Asia/Shanghai")
        XCTAssertFalse(curve.isEmpty)

        // History restore switches Store to nowcast mode
        let store = Store()
        try store.engine.start(workspace: dir.path, transport: MockTransport())
        store.online = true
        store.engine.connected = true
        let item = MobileTaskHistoryItem(
            taskID: MockTransport.nowcastTaskID,
            method: "forecast",
            status: "done",
            createdAt: "2026-11-20T10:05:00.000Z",
            cancelRequested: false,
            percent: nil,
            retryKind: "nowcast",
            retryPayload: ["name": "杭州", "lat": 30.2741, "lon": 120.1551,
                           "elevation_m": 8.0, "timezone_name": "Asia/Shanghai",
                           "target_date": "2026-11-20", "phenomenon": "dusk"],
            canRestoreResult: true
        )
        store.restoreTaskResult(item)
        try await Task.sleep(for: .milliseconds(250))
        XCTAssertEqual(store.workbenchMode, "nowcast")
        XCTAssertEqual(store.resultKind, .nowcast)
        XCTAssertNotNil(store.nowcastReport)
    }

    func testCancelMarksRequest() async throws {
        let (client, dir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dir) }
        var progressCalls = 0
        let task = Task {
            _ = try await client.call("check", ["kind": "point", "payload": [:] as JSONObject],
                                      progress: { _ in progressCalls += 1 })
        }
        try await Task.sleep(for: .milliseconds(150))
        task.cancel()
        do {
            _ = try await task.value
            XCTFail("取消后的请求应当抛错")
        } catch is CancellationError {
        } catch let error as EngineError {
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
        store.adoptResult(["model_results": [:]], kind: .point, params: store.payload(for: .point))
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
