import XCTest
@testable import FireSkyMobile

/// 海拔来源三档（v1.1.1 任务书 §1）单测：
/// 归一/持久化（UserDefaults 重启恢复）与 search/metadata 参数透传。
final class ElevationSourceTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "firesky-elevation-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    func testDefaultIsAutoWhenUnset() {
        XCTAssertEqual(ElevationSourceSetting.load(defaults), "auto")
        XCTAssertEqual(ElevationSourceSetting.normalize(nil), "auto")
    }

    func testPersistRoundTripAcrossRestart() {
        for value in ["auto", "http", "srtm"] {
            XCTAssertEqual(ElevationSourceSetting.save(value, defaults: defaults), value)
            XCTAssertEqual(defaults.string(forKey: ElevationSourceSetting.key), value)
            // 新实例读同一 suite = 进程重启后恢复。
            let reopened = UserDefaults(suiteName: suiteName)
            XCTAssertEqual(ElevationSourceSetting.load(reopened!), value)
        }
    }

    func testInvalidValuesNormalizeToAuto() {
        for bad in ["bogus", "", "HTTP", "srtm3", "auto "] {
            XCTAssertEqual(ElevationSourceSetting.save(bad, defaults: defaults), "auto")
            XCTAssertEqual(ElevationSourceSetting.load(defaults), "auto")
        }
        // 存储里被外部写坏的值同样归一。
        defaults.set("bogus", forKey: ElevationSourceSetting.key)
        XCTAssertEqual(ElevationSourceSetting.load(defaults), "auto")
    }

    func testSearchParamsPassThroughElevationSource() {
        for value in ["auto", "http", "srtm"] {
            let params = ElevationSourceSetting.searchParams(query: "杭州", source: value)
            XCTAssertEqual(params["query"] as? String, "杭州")
            XCTAssertEqual(params["elevation_source"] as? String, value)
            XCTAssertEqual(params.count, 2)
        }
        // 非法档归一为 auto，绝不把非法值透传给引擎。
        let params = ElevationSourceSetting.searchParams(query: "杭州", source: "bogus")
        XCTAssertEqual(params["elevation_source"] as? String, "auto")
    }

    func testMetadataParamsPassThroughElevationSource() {
        for value in ["auto", "http", "srtm"] {
            let params = ElevationSourceSetting.metadataParams(lat: 30.2741, lon: 120.1551, source: value)
            XCTAssertEqual(params["lat"] as? Double, 30.2741)
            XCTAssertEqual(params["lon"] as? Double, 120.1551)
            XCTAssertEqual(params["elevation_source"] as? String, value)
            XCTAssertEqual(params.count, 3)
        }
        let params = ElevationSourceSetting.metadataParams(lat: 0, lon: 0, source: "bogus")
        XCTAssertEqual(params["elevation_source"] as? String, "auto")
    }
}

/// Store 接线：elevationSource 从 UserDefaults 恢复、setElevationSource 持久化。
/// 不触发 connect（引擎通道不启动），仅验证设置状态与持久化语义。
@MainActor
final class StoreElevationSourceTests: XCTestCase {
    func testStoreLoadsAndPersistsElevationSource() {
        let defaults = UserDefaults.standard
        let previous = defaults.string(forKey: ElevationSourceSetting.key)
        defer {
            if let previous {
                defaults.set(previous, forKey: ElevationSourceSetting.key)
            } else {
                defaults.removeObject(forKey: ElevationSourceSetting.key)
            }
        }
        defaults.set("srtm", forKey: ElevationSourceSetting.key)
        let store = Store()
        XCTAssertEqual(store.elevationSource, "srtm")
        store.setElevationSource("http")
        XCTAssertEqual(store.elevationSource, "http")
        XCTAssertEqual(defaults.string(forKey: ElevationSourceSetting.key), "http")
        // 非法值归一为 auto。
        store.setElevationSource("bogus")
        XCTAssertEqual(store.elevationSource, "auto")
        // 缺省恢复 auto。
        defaults.removeObject(forKey: ElevationSourceSetting.key)
        XCTAssertEqual(Store().elevationSource, "auto")
    }
}