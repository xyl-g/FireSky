import XCTest
@testable import FireSkyMobile

/// FSMobileTransport（FFI C 契约）——真实 engine-mobile 静态库行为验证。
/// fsmobile_stub.c 占位桩已删除，App 链接 engine-mobile 产出的 libengine_mobile.a
/// （project.yml LIBRARY_SEARCH_PATHS 指向 engine-mobile/target，见 scripts/build-engine.sh）。
@MainActor
final class FSMobileTransportTests: XCTestCase {
    /// 真实引擎握手：fsm_start → hello 请求 → result 帧 protocol == 1（smoke.rs 同语义）。
    func testHelloHandshakeOverRealEngine() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("firesky-ffi-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let transport = FSMobileTransport()
        let helloResult = expectation(description: "hello result frame")
        var frame: JSONObject?
        transport.onFrame = { data in
            guard let object = (try? JSONSerialization.jsonObject(with: data)) as? JSONObject,
                  object["id"] as? String == "hello" else { return }
            frame = object
            if object["type"] as? String == "result" { helloResult.fulfill() }
        }
        try transport.start(workspace: dir.path)
        defer { transport.stop() }

        var request: JSONObject = ["id": "hello", "method": "hello", "params": [:] as JSONObject]
        var data = try JSONSerialization.data(withJSONObject: request)
        data.append(10) // '\n'：EngineClient 帧契约；fsm_send 容忍结尾换行
        try transport.send(data)
        await fulfillment(of: [helloResult], timeout: 10)

        XCTAssertEqual(frame?["type"] as? String, "result")
        let result = frame?["result"] as? JSONObject
        XCTAssertEqual(result?["protocol"] as? Int, 1, "真实引擎 hello 应返回 protocol == 1")
    }

    /// 单实例契约（shim 侧语义）：fsm_stop 幂等，start 先回收既有实例再启动——
    /// 重连必须成功（C 层「重复 fsm_start 报错」由 engine-mobile smoke.rs 覆盖）。
    func testRestartReclaimsSingleton() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("firesky-ffi-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let first = FSMobileTransport()
        try first.start(workspace: dir.path)

        let second = FSMobileTransport()
        try second.start(workspace: dir.path) // 先 fsm_stop 回收 first，再启动
        defer { second.stop() }

        let helloResult = expectation(description: "hello result after restart")
        second.onFrame = { data in
            guard let object = (try? JSONSerialization.jsonObject(with: data)) as? JSONObject,
                  object["id"] as? String == "hello",
                  object["type"] as? String == "result" else { return }
            helloResult.fulfill()
        }
        var request: JSONObject = ["id": "hello", "method": "hello", "params": [:] as JSONObject]
        var data = try JSONSerialization.data(withJSONObject: request)
        data.append(10)
        try second.send(data)
        await fulfillment(of: [helloResult], timeout: 10)
    }

    func testSendWithoutStartThrows() {
        let transport = FSMobileTransport()
        XCTAssertThrowsError(try transport.send(Data("[1]\n".utf8)))
    }
}
