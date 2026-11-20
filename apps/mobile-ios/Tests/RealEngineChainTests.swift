import Darwin
import XCTest
@testable import FireSkyMobile

/// MN1/MN3 验收：iOS 模拟器内真实静态引擎全链路 hello→check→prepare→forecast，
/// 以及 kill-restart 后 Store.connect 自动 result_restore。
/// 预置缓存 = 桌面 live 缓存 .cache/{gfs,ecmwf,cams} 的日期子树（APFS
/// clonefile 零拷贝种子到临时 workspace，避免污染原缓存）。原生 ready 契约
/// 要求 CAMS 同样就绪：取 gfs∩ecmwf 最新交集日 D，且 CAMS 在 D 或 D-1 有
/// 真实 GRIB 数据（事件 CAMS bracket run 通常落在 D-1 12Z），三源种子
/// {D, D-1}。
/// definitions = **App bundle 内 eccodes/definitions**（v1.1.0 起由
/// scripts/build-engine.sh staging + project.yml 文件夹引用打包；MM0 坑 12：
/// 宿主 env 注入是假阳性，默认清除）。宿主注入仅作为显式标注的调试旁路
/// （FIRESKY_TEST_HOST_DEFINITIONS_BYPASS=1，默认关闭，不构成验收证据）。
/// 缓存夹具缺失时 XCTSkip，保证无数据机器上测试套件仍可运行；但宿主有
/// ecCodes 而 bundle 未 staging 时直接 XCTFail（防假阳性）。
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

    // MARK: - definitions：bundle 优先（MM0 坑 12）

    /// 清除宿主注入并断言 bundle definitions 就位。
    /// 返回 fsm_start 之后期望的 ECCODES_DEFINITION_PATH；
    /// bundle 缺失且宿主有 ecCodes 时记录 XCTFail 并返回 nil（调用方立即 return）。
    private func activateBundleDefinitions() throws -> String? {
        let fileManager = FileManager.default
        let env = ProcessInfo.processInfo.environment
        // 显式标注的调试旁路（默认关闭）：宿主 definitions 注入，仅用于
        // 无 bundle staging 的开发机调试，不构成 bundle 生效的验收证据。
        if env["FIRESKY_TEST_HOST_DEFINITIONS_BYPASS"] == "1" {
            let candidates = [
                env["FIRESKY_ECCODES_DEFINITIONS"],
                "/opt/homebrew/share/eccodes/definitions",
                "/usr/local/share/eccodes/definitions",
            ].compactMap { $0 }
            guard let definitions = candidates.first(where: { fileManager.fileExists(atPath: $0) }) else {
                throw XCTSkip("调试旁路已开启但宿主无 ecCodes definitions")
            }
            setenv("ECCODES_DEFINITION_PATH", definitions, 1)
            setenv("ECCODES_SAMPLES_PATH", (definitions as NSString).deletingLastPathComponent + "/samples", 1)
            return definitions
        }
        // 默认路径：清空宿主可能残留的注入，解码只能依赖 bundle definitions。
        unsetenv("ECCODES_DEFINITION_PATH")
        unsetenv("ECCODES_SAMPLES_PATH")
        guard let bundled = FSMobileTransport.bundledDefinitionsPath() else {
            let hostHasEccodes = ["/opt/homebrew/share/eccodes/definitions",
                                  "/usr/local/share/eccodes/definitions"]
                .contains { fileManager.fileExists(atPath: $0) }
            if hostHasEccodes {
                XCTFail("Bundle 未打包 eccodes/definitions：先运行 apps/mobile-ios/scripts/build-engine.sh staging，再 xcodegen + 重新构建（MM0 坑 12 防假阳性）")
                return nil
            }
            throw XCTSkip("本机无 ecCodes 且 bundle 未 staging definitions（brew install eccodes + build-engine.sh）")
        }
        let bundleResource = Bundle.main.resourceURL?
            .appendingPathComponent("eccodes/definitions").path
        XCTAssertEqual(bundled, bundleResource, "definitions 必须来自 App bundle 资源目录")
        XCTAssertTrue(bundled.hasPrefix(Bundle.main.bundlePath),
                      "definitions 路径必须位于 App bundle 内，而非宿主路径：\(bundled)")
        XCTAssertNotNil(FSMobileTransport.bundledSamplesPath(),
                        "bundle 应同时打包 eccodes/samples（build-engine.sh staging）")
        return bundled
    }

    // MARK: - 缓存种子与 D 选取（沿用 MN1 逻辑）

    /// 种子三源缓存 {D, D-1} 到临时 workspace，返回 (workspace, targetDate)。
    private func makeSeededWorkspace() throws -> (URL, String) {
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
        return (workspace, targetDate)
    }

    // MARK: - 全链路驱动

    /// hello→check→prepare→forecast 全链（纯离线：预置种子 + confirmed 确认）。
    /// 返回存活的 transport（调用方负责 stop）与 forecast 结果。
    private func runFullChain(
        workspace: URL,
        targetDate: String,
        expectedDefinitions: String
    ) async throws -> (FSMobileTransport, JSONObject) {
        let transport = FSMobileTransport()
        attach(transport)
        try transport.start(workspace: workspace.path)

        // bundle 生效证据 ①：fsm_start 已把期望 definitions 路径注入进程环境
        // （ffi 契约：先注入单数 ECCODES_DEFINITION_PATH 再初始化引擎）。
        if let injected = getenv("ECCODES_DEFINITION_PATH") {
            XCTAssertEqual(String(cString: injected), expectedDefinitions,
                           "fsm_start 应注入 bundle definitions 路径")
        } else {
            XCTFail("fsm_start 未注入 ECCODES_DEFINITION_PATH")
        }

        let hello = try await call(transport, method: "hello", params: [:], id: "hello", timeout: 30)
        XCTAssertEqual(hello["protocol"] as? Int, 1, "真实静态引擎握手 protocol == 1")
        let capabilities = Set(hello["capabilities"] as? [String] ?? [])
        XCTAssertTrue(capabilities.isSuperset(of: ["task_history", "result_restore"]),
                      "真实引擎 hello 应声明 task_history/result_restore 能力")

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
            transport.stop()
            XCTFail("check 未返回会话令牌：\(check)")
            throw EngineError(message: "check 未返回会话令牌")
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
        // 纯离线全链：allow_download:false + confirmed:true（§3.5）。
        let forecast = try await call(
            transport, method: "forecast",
            params: ["session": session, "confirmed": true,
                     "cycle_age_confirmed": true, "allow_download": false],
            id: "forecast", timeout: 600)
        // bundle 生效证据 ②：仅靠 bundle definitions 完成 GFS/EC 双模型 GRIB 解码。
        let modelResults = forecast["model_results"] as? JSONObject
        XCTAssertNotNil(modelResults?["gfs"], "forecast 应含 GFS 模型结果（bundle definitions 解码成功）")
        XCTAssertNotNil(modelResults?["ec"], "forecast 应含 EC 模型结果（bundle definitions 解码成功）")
        return (transport, forecast)
    }

    // MARK: - 测试

    func testPreseededCacheFullChain() async throws {
        let (workspace, targetDate) = try makeSeededWorkspace()
        defer { try? FileManager.default.removeItem(at: workspace) }
        guard let expectedDefinitions = try activateBundleDefinitions() else { return }
        let (transport, forecast) = try await runFullChain(
            workspace: workspace, targetDate: targetDate,
            expectedDefinitions: expectedDefinitions)
        defer { transport.stop() }

        // 帧级桌面 parity 对照样本（宿主桌面引擎跑同一 payload 后 diff）。
        let dump = URL(fileURLWithPath: "/tmp/firesky-mn1-sim-forecast.json")
        if let data = try? JSONSerialization.data(withJSONObject: forecast, options: [.sortedKeys, .prettyPrinted]) {
            try? data.write(to: dump)
        }
    }

    /// kill-restart 恢复：同 workspace 先跑完整 forecast，停掉引擎实例后
    /// 新建 Store（默认 ffi 通道）→ connect 应自动 task_history → result_restore
    /// 恢复上次 forecast 的 result 与 location/date/phenomenon 参数。
    func testKillRestartRestoresLatestForecast() async throws {
        let (workspace, targetDate) = try makeSeededWorkspace()
        defer { try? FileManager.default.removeItem(at: workspace) }
        guard let expectedDefinitions = try activateBundleDefinitions() else { return }
        let (transport, forecast) = try await runFullChain(
            workspace: workspace, targetDate: targetDate,
            expectedDefinitions: expectedDefinitions)
        // kill：显式停掉第一个引擎实例（单实例契约），模拟进程回收。
        // 注意不能用 defer 延后 stop——fsm 是进程级单例，会误杀 Store 的新实例。
        transport.stop()

        // restart：新 Store 实例指向同一 workspace；固定 ffi 通道并在结束后还原 defaults。
        let defaults = UserDefaults.standard
        let previousTransport = defaults.string(forKey: "engineTransport")
        let previousWorkspace = defaults.string(forKey: "workspace")
        defaults.set("ffi", forKey: "engineTransport")
        defer {
            if let previousTransport {
                defaults.set(previousTransport, forKey: "engineTransport")
            } else {
                defaults.removeObject(forKey: "engineTransport")
            }
            if let previousWorkspace {
                defaults.set(previousWorkspace, forKey: "workspace")
            } else {
                defaults.removeObject(forKey: "workspace")
            }
        }
        let store = Store()
        store.workspace = workspace.path
        defer { store.shutdown() }
        XCTAssertEqual(store.engineTransport, "ffi", "v1.1.0 默认通道应为 ffi")
        await store.connect()
        XCTAssertTrue(store.online, "重启后引擎应在线：\(store.error)")
        guard let restored = store.result else {
            return XCTFail("connect 未自动恢复上次 forecast 结果：\(store.error)")
        }
        let models = restored["model_results"] as? JSONObject
        XCTAssertNotNil(models?["gfs"], "恢复结果应含 GFS 模型")
        XCTAssertNotNil(models?["ec"], "恢复结果应含 EC 模型")
        XCTAssertEqual(store.selectedModel, "gfs", "gfs 存在时默认选 gfs（与 confirm 一致）")
        XCTAssertEqual(store.status, "已恢复历史结果")
        XCTAssertEqual(store.error, "")
        XCTAssertNil(store.session, "恢复不应遗留预检会话")
        // 参数恢复：location/date/phenomenon 与上次 forecast 的 payload 一致。
        XCTAssertEqual(store.name, "杭州")
        XCTAssertEqual(store.lat, 30.2741, accuracy: 1e-6)
        XCTAssertEqual(store.lon, 120.1551, accuracy: 1e-6)
        XCTAssertEqual(store.timezone, "Asia/Shanghai")
        XCTAssertEqual(store.phenomenon, "dusk")
        XCTAssertEqual(store.targetDateLabel, targetDate)
        XCTAssertTrue(store.metadataReady)
        // 恢复内容与原始 forecast 帧一致（关键字段逐项比对）。
        let originalGFS = (forecast["model_results"] as? JSONObject)?["gfs"] as? JSONObject
        let restoredGFS = models?["gfs"] as? JSONObject
        XCTAssertEqual(restoredGFS?["best_time_local"] as? String,
                       originalGFS?["best_time_local"] as? String)
        XCTAssertEqual((restoredGFS?["peak_quality_index"] as? NSNumber)?.doubleValue,
                       (originalGFS?["peak_quality_index"] as? NSNumber)?.doubleValue)
    }
}