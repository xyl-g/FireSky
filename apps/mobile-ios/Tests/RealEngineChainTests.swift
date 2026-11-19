import Darwin
import XCTest
@testable import FireSkyMobile

/// MN1 验收：iOS 模拟器内真实静态引擎全链路 hello→check→prepare→forecast。
/// 预置缓存 = 桌面 live 缓存 .cache/{gfs,ecmwf,cams} 的日期子树（APFS
/// clonefile 零拷贝种子到临时 workspace，避免污染原缓存）。原生 ready 契约
/// 要求 CAMS 同样就绪：取 gfs∩ecmwf 最新交集日 D，且 CAMS 在 D 或 D-1 有
/// 真实 GRIB 数据（事件 CAMS bracket run 通常落在 D-1 12Z），三源种子
/// {D, D-1}。definitions = 宿主 ecCodes 安装目录（模拟器与宿主共享文件
/// 系统，MM0 坑 12/13 的注入路径）。夹具缺失时 XCTSkip，保证无数据/无
/// ecCodes 的机器上测试套件仍可运行。
@MainActor
final class RealEngineChainTests: XCTestCase {
    private var handlers: [String: (JSONObject) -> Void] = [:]

    private func attach(_ transport: FSMobileTransport) {
        transport.onFrame = { [weak self] data in
            guard let self,
                  let object = (try? JSONSerialization.jsonObject(with: data)) as? JSONObject,
                  let id = object["id"] as? String else { return }
            self.handlers[id]?(object)
        }
    }

    private func call(
        _ transport: FSMobileTransport,
        method: String,
        params: JSONObject,
        id: String,
        timeout: TimeInterval
    ) async throws -> JSONObject {
        let done = expectation(description: "\(method) terminal frame")
        var result: JSONObject?
        var failure: String?
        handlers[id] = { frame in
            switch frame["type"] as? String {
            case "result":
                result = frame["result"] as? JSONObject
                done.fulfill()
            case "error":
                let error = frame["error"] as? JSONObject
                failure = (error?["message"] as? String) ?? (frame["message"] as? String) ?? "\(frame)"
                done.fulfill()
            default:
                break // progress 帧不终结请求
            }
        }
        defer { handlers[id] = nil }
        var data = try JSONSerialization.data(withJSONObject: [
            "id": id, "method": method, "params": params,
        ] as JSONObject)
        data.append(10) // EngineClient 帧契约；fsm_send 容忍结尾换行
        try transport.send(data)
        await fulfillment(of: [done], timeout: timeout)
        if let failure {
            XCTFail("\(method) 返回错误帧：\(failure)")
        }
        return result ?? [:]
    }

    func testPreseededCacheFullChain() async throws {
        let fileManager = FileManager.default
        // #file = <root>/apps/mobile-ios/Tests/RealEngineChainTests.swift
        let repoRoot = URL(fileURLWithPath: #file)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let liveCache = repoRoot.appendingPathComponent(".cache")
        let gfsFixture = liveCache.appendingPathComponent("gfs")
        let ecmwfFixture = liveCache.appendingPathComponent("ecmwf")
        let camsFixture = liveCache.appendingPathComponent("cams")
        guard fileManager.fileExists(atPath: gfsFixture.path),
              fileManager.fileExists(atPath: ecmwfFixture.path),
              fileManager.fileExists(atPath: camsFixture.path) else {
            throw XCTSkip("仓库 .cache 无 gfs/ecmwf/cams 桌面 live 缓存")
        }

        // definitions/samples：环境覆盖优先，其次 Homebrew 标准前缀。
        let env = ProcessInfo.processInfo.environment
        let definitionsCandidates = [
            env["FIRESKY_ECCODES_DEFINITIONS"],
            "/opt/homebrew/share/eccodes/definitions",
            "/usr/local/share/eccodes/definitions",
        ].compactMap { $0 }
        guard let definitions = definitionsCandidates.first(where: { fileManager.fileExists(atPath: $0) }) else {
            throw XCTSkip("宿主无 ecCodes definitions（brew install eccodes 或设 FIRESKY_ECCODES_DEFINITIONS）")
        }
        let samples = (definitions as NSString).deletingLastPathComponent + "/samples"

        // 取 gfs/ecmwf 共同覆盖、且 CAMS 在 D 或 D-1 有真实数据的最新日期
        // （ISO 日期串字典序即时间序）。
        let gfsDates = Set((try? fileManager.contentsOfDirectory(atPath: gfsFixture.path)) ?? [])
        let ecmwfDates = Set((try? fileManager.contentsOfDirectory(atPath: ecmwfFixture.path)) ?? [])
        let isoDate = /^\d{4}-\d{2}-\d{2}$/
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.calendar = Calendar(identifier: .gregorian)
        func previousDay(_ date: String) -> String? {
            guard let day = formatter.date(from: date),
                  let prior = formatter.calendar.date(byAdding: .day, value: -1, to: day) else { return nil }
            return formatter.string(from: prior)
        }
        func camsHasData(_ date: String) -> Bool {
            let dir = camsFixture.appendingPathComponent(date)
            guard let enumerator = fileManager.enumerator(
                at: dir, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]) else { return false }
            for case let url as URL in enumerator where ["grib", "grib2"].contains(url.pathExtension) {
                return true
            }
            return false
        }
        guard let targetDate = gfsDates.intersection(ecmwfDates)
            .filter({ $0.wholeMatch(of: isoDate) != nil })
            .sorted().reversed()
            .first(where: { camsHasData($0) || previousDay($0).map(camsHasData) == true }) else {
            throw XCTSkip("gfs/ecmwf 交集日期均无 CAMS 真实数据（D 或 D-1）")
        }
        let seedDates = [targetDate, previousDay(targetDate)].compactMap { $0 }

        let workspace = fileManager.temporaryDirectory
            .appendingPathComponent("firesky-mn1-chain-\(UUID().uuidString)", isDirectory: true)
        let cacheRoot = workspace.appendingPathComponent(".cache", isDirectory: true)
        for source in ["gfs", "ecmwf", "cams"] {
            for date in seedDates {
                let from = liveCache.appendingPathComponent("\(source)/\(date)")
                let to = cacheRoot.appendingPathComponent("\(source)/\(date)")
                guard fileManager.fileExists(atPath: from.path) else { continue }
                try fileManager.createDirectory(at: to.deletingLastPathComponent(), withIntermediateDirectories: true)
                // APFS clonefile 整树零拷贝；跨卷等失败时退回真实拷贝。
                if clonefile(from.path, to.path, 0) != 0 {
                    try fileManager.copyItem(at: from, to: to)
                }
            }
        }
        defer { try? fileManager.removeItem(at: workspace) }

        // FSMobileTransport 的 definitions 参数传 NULL（bundle 未打包资源），
        // 契约语义「NULL 不注入」→ 进程环境生效（ecCodes 读单数变量，坑 10）。
        setenv("ECCODES_DEFINITION_PATH", definitions, 1)
        setenv("ECCODES_SAMPLES_PATH", samples, 1)

        let transport = FSMobileTransport()
        attach(transport)
        try transport.start(workspace: workspace.path)
        defer { transport.stop() }

        let hello = try await call(transport, method: "hello", params: [:], id: "hello", timeout: 30)
        XCTAssertEqual(hello["protocol"] as? Int, 1, "真实静态引擎握手 protocol == 1")

        // Store.payload() 同构：杭州默认观测点，dusk，双模型，无卫星链。
        let payload: JSONObject = [
            "name": "杭州", "lat": 30.2741, "lon": 120.1551, "elevation_m": 8.0,
            "timezone_name": "Asia/Shanghai", "target_date": targetDate,
            "phenomenon": "dusk", "model_source": "both", "workers": 1,
            "forecast_mode": "model", "satellite_source": "off",
            "ecmwf_source": "ecmwf",
        ]
        let check = try await call(
            transport, method: "check",
            params: ["kind": "point", "payload": payload],
            id: "check", timeout: 180)
        guard let session = check["session"] as? String else {
            return XCTFail("check 未返回会话令牌：\(check)")
        }
        let report = check["report"] as? JSONObject ?? [:]
        if report["ready"] as? Bool != true {
            let datasets = (report["datasets"] as? [JSONObject] ?? []).map {
                "\($0["source"] ?? "?"): ready=\($0["ready"] ?? "?") missing=\($0["missing_count"] ?? "?")"
            }.joined(separator: "; ")
            XCTFail("预置缓存 \(targetDate) 未就绪：\(datasets)")
        }

        let prepared = try await call(
            transport, method: "prepare", params: ["session": session],
            id: "prepare", timeout: 300)
        XCTAssertEqual((prepared["report"] as? JSONObject)?["ready"] as? Bool, true, "prepare 后数据应就绪")

        // 夹具为历史周期缓存（相对运行日已过期）；桌面 forecast 的周期时效
        // 闸门要求显式确认，MN1 验收以预置样本为准，故显式确认周期时效。
        let forecast = try await call(
            transport, method: "forecast",
            params: ["session": session, "confirmed": true, "cycle_age_confirmed": true],
            id: "forecast", timeout: 600)
        let modelResults = forecast["model_results"] as? JSONObject
        XCTAssertNotNil(modelResults?["gfs"], "forecast 应含 GFS 模型结果")
        XCTAssertNotNil(modelResults?["ec"], "forecast 应含 EC 模型结果")

        // 帧级桌面 parity 对照样本（宿主桌面引擎跑同一 payload 后 diff）。
        let dump = URL(fileURLWithPath: "/tmp/firesky-mn1-sim-forecast.json")
        if let data = try? JSONSerialization.data(withJSONObject: forecast, options: [.sortedKeys, .prettyPrinted]) {
            try? data.write(to: dump)
        }
    }
}
