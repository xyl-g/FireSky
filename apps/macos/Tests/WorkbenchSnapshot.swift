import AppKit
import SwiftUI

// Screenshot fixture only; excluded from the shipped app's Sources target.
@main
struct WorkbenchSnapshot: App {
    @NSApplicationDelegateAdaptor(WorkbenchSnapshotDelegate.self) private var delegate
    @StateObject private var store = Store()
    @StateObject private var updater = Updater()
    @StateObject private var reminders = ReminderCenter()
    @StateObject private var localServer = LocalServerController()

    var body: some Scene {
        WindowGroup("FireSky · UI Test Fixture") {
            WorkbenchRootView().environmentObject(store)
                .environmentObject(updater)
                .environmentObject(reminders)
                .environmentObject(localServer)
                .environment(\.locale, Locale(identifier:
                    ProcessInfo.processInfo.environment["FIRESKY_TEST_ENGLISH"] == "1" ? "en_US" : "zh_CN"))
                .environment(\.dynamicTypeSize,
                             ProcessInfo.processInfo.environment["FIRESKY_TEST_LARGE_TEXT"] == "1"
                                ? .accessibility1 : .large)
                .onAppear { delegate.capture(store, updater, reminders) }
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1440, height: 1040)
    }
}

@MainActor
final class WorkbenchSnapshotDelegate: NSObject, NSApplicationDelegate {
    private weak var store: Store?
    private var scheduled = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { store?.shutdown() }

    func capture(_ store: Store, _ updater: Updater, _ reminders: ReminderCenter) {
        guard !scheduled, let path = ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] else { return }
        scheduled = true
        self.store = store
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_UPDATE_SHEET"] == "1" {
            updater.adoptTestAvailable(
                version: "2.0.0",
                notes: "• 窗口与全局格局：隐藏冗余标题栏，统一侧栏与顶栏空间层级。\n• 设置与更新弹层：改为顶部胶囊导航、图标行布局与独立软件更新弹层。\n• 首次空态与任务历史：收敛无结果占位卡片，修复空历史头部垂直偏移。\n• 地形下载与保留策略：移除下载确认误用红色按钮，补齐失败瓦片重试入口。"
            )
            store.showUpdateSheet = true
        }
        store.name = "界面测试（非真实预测）"
        store.timezone = "Asia/Shanghai"
        store.date = ISO8601DateFormatter().date(from: "2026-11-09T04:00:00Z")!
        let isNowcast = ProcessInfo.processInfo.environment["FIRESKY_TEST_NOWCAST"] == "1"
        let isRegional = ProcessInfo.processInfo.environment["FIRESKY_TEST_REGIONAL"] == "1"
        store.page = isRegional ? .regional : isNowcast ? .nowcast : .point
        if isRegional {
            store.selectedRegions = []
            store.customRegion = true
            store.west = 118; store.east = 123
            store.south = 28; store.north = 33
        }
        store.resultKind = store.page
        let partial = ProcessInfo.processInfo.environment["FIRESKY_TEST_PARTIAL"] == "1"
        store.result = isNowcast ? Self.nowcastReport() : [
            "model_results": partial ? ["gfs": Self.report("gfs")] : ["gfs": Self.report("gfs"), "ec": Self.report("ec")],
            "model_errors": partial ? ["ec": "测试场景：EC 未完成"] : [:],
        ]
        if isRegional {
            store.result = [
                "model_results": partial ? ["gfs": Self.regionalReport("gfs")] :
                    ["gfs": Self.regionalReport("gfs"), "ec": Self.regionalReport("ec")],
                "model_errors": partial ? ["ec": "测试场景：EC 未完成"] : [:],
            ]
            if ProcessInfo.processInfo.environment["FIRESKY_TEST_COMPARE"] == "1" { store.regionalDisplay = "compare" }
            if ProcessInfo.processInfo.environment["FIRESKY_TEST_INSPECT"] == "1" {
                store.selectedRegionalCoordinate = GridCoordinate(lat: 30, lon: 120)
            }
        }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_REMINDER_FUTURE"] == "1",
           var result = store.result,
           var models = result["model_results"] as? JSONObject {
            let future = ISO8601DateFormatter().string(from: Date().addingTimeInterval(3 * 3600))
            for model in ["gfs", "ec"] {
                if var report = models[model] as? JSONObject {
                    report["best_time_local"] = future
                    models[model] = report
                }
            }
            result["model_results"] = models
            store.result = result
            if ProcessInfo.processInfo.environment["FIRESKY_TEST_REMINDER_SCHEDULED"] == "1",
               let report = models["gfs"] as? JSONObject,
               let bestTime = report["best_time_local"] as? String {
                let key = [store.name, String(store.lat), String(store.lon),
                           store.targetDateLabel, store.phenomenon, "gfs", bestTime].joined(separator: "|")
                reminders.adoptTestScheduled(key: key)
            }
        }
        if let replay = ProcessInfo.processInfo.environment["FIRESKY_TEST_REPLAY"] {
            do {
                let root = URL(fileURLWithPath: replay)
                let request = try JSONSerialization.jsonObject(
                    with: Data(contentsOf: root.appendingPathComponent("evidence/request.json"))) as! JSONObject
                let result = try JSONSerialization.jsonObject(
                    with: Data(contentsOf: root.appendingPathComponent("evidence/forecast.json"))) as! JSONObject
                store.name = "历史缓存回放"
                store.lat = (request["lat"] as? NSNumber)?.doubleValue ?? store.lat
                store.lon = (request["lon"] as? NSNumber)?.doubleValue ?? store.lon
                store.timezone = request["timezone_name"] as? String ?? "Asia/Shanghai"
                if let date = request["target_date"] as? String {
                    store.date = ISO8601DateFormatter().date(from: date + "T04:00:00Z")!
                }
                store.result = result
                store.resultKind = store.page
                if isRegional, let models = result["model_results"] as? JSONObject,
                   let report = models["gfs"] as? JSONObject,
                   let point = (report["points"] as? [JSONObject])?.first,
                   let lat = point["lat"] as? NSNumber, let lon = point["lon"] as? NSNumber {
                    store.selectedRegionalCoordinate = GridCoordinate(lat: lat.doubleValue, lon: lon.doubleValue)
                }
            } catch {
                fputs("Cannot load historical replay: \(error)\n", stderr)
                exit(1)
            }
        }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_EMPTY"] == "1" { store.result = nil }
        // v1.1.1 引擎契约：所有预测 payload 透传 elevation_source。
        precondition(store.payload()["elevation_source"] as? String == store.elevationSource)
        store.elevationSource = "srtm"
        precondition(store.payload()["elevation_source"] as? String == "srtm")
        store.elevationSource = "auto"
        precondition(store.payload()["elevation_source"] as? String == "auto")
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_SETTINGS"] == "1" {
            store.page = .settings
            store.settingsCategory = SettingsCategory(rawValue:
                ProcessInfo.processInfo.environment["FIRESKY_TEST_SETTINGS_CATEGORY"] ?? "general") ?? .general
        }
        if let page = ProcessInfo.processInfo.environment["FIRESKY_TEST_PAGE"].flatMap(Page.init(rawValue:)) {
            store.page = page
        }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_STALE"] == "1" {
            // UX #3：改参数保留旧结果并标注原始参数；新结果到来前徽标常亮。
            precondition(store.result != nil)
            let params: JSONObject = [
                "name": "杭州", "lat": store.lat, "lon": store.lon,
                "elevation_m": store.elevation, "timezone_name": store.timezone,
                "target_date": store.targetDateLabel, "phenomenon": store.phenomenon,
                "ecmwf_source": store.ecmwfSource,
            ]
            store.adoptResult(store.result!, kind: store.page, params: params)
            precondition(!store.resultParametersStale)
            store.phenomenon = store.phenomenon == "dusk" ? "dawn" : "dusk"
            precondition(store.resultParametersStale)
            precondition(store.result != nil, "参数变更不得清空旧结果")
            precondition(store.resultTitle.contains(params["target_date"] as! String))
            precondition(store.resultParamsSummary.contains("结果原始参数"))
            print("Stale result banner fixture: ready")
        }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_SESSION"] == "1" {
            // UX #7：会话持久化往返（测试后恢复原有默认值，不污染真实会话）。
            let defaults = UserDefaults.standard
            let previous = defaults.dictionary(forKey: "lastSession")
            store.sessionPersistenceEnabled = true
            store.name = "会话测试地点"
            store.lat = 29.5; store.lon = 119.0
            store.phenomenon = "dawn"
            store.saveSession()
            precondition(defaults.dictionary(forKey: "lastSession")?["name"] as? String == "会话测试地点")
            store.name = "临时改名"; store.lat = 0; store.phenomenon = "dusk"
            store.restoreSession()
            precondition(store.name == "会话测试地点")
            precondition(abs(store.lat - 29.5) < 1e-9 && abs(store.lon - 119.0) < 1e-9)
            precondition(store.phenomenon == "dawn")
            store.sessionPersistenceEnabled = false
            if let previous {
                defaults.set(previous, forKey: "lastSession")
            } else {
                defaults.removeObject(forKey: "lastSession")
            }
            print("Session persistence fixture: ready")
        }
        store.selectedModel = ProcessInfo.processInfo.environment["FIRESKY_TEST_MODEL"] ?? (partial ? "ec" : "gfs")
        if let tab = ProcessInfo.processInfo.environment["FIRESKY_TEST_TAB"] { store.resultTab = tab }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_CALENDAR"] == "1" {
            store.dateTab = "自定义"
        }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_TUTORIAL"] == "1" {
            store.openCAMSSetup()
        }
        let previousLanguage = UserDefaults.standard.string(forKey: "appLanguage")
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_ENGLISH"] == "1" {
            L10n.language = .english
        }
        Task {
            defer {
                if let previousLanguage {
                    UserDefaults.standard.set(previousLanguage, forKey: "appLanguage")
                } else {
                    UserDefaults.standard.removeObject(forKey: "appLanguage")
                }
            }
            let env = ProcessInfo.processInfo.environment
            if env["FIRESKY_TEST_REQUIRE_ENGINE"] == "1" {
                for _ in 0..<200 where !store.online {
                    try? await Task.sleep(for: .milliseconds(100))
                }
                guard store.online else { fputs("Screenshot engine did not connect\n", stderr); exit(1) }
            }
            if env["FIRESKY_TEST_READ_PROFILE"] == "1" {
                TaskHistoryChecks.run()
                for _ in 0..<200 where !store.online {
                    try? await Task.sleep(for: .milliseconds(100))
                }
                guard store.online else { fputs("Replay engine did not connect\n", stderr); exit(1) }
                do {
                    let response = try await store.engine.call("task_history", ["limit": 100])
                    let tasks = (response["tasks"] as? [JSONObject] ?? []).compactMap(NativeTaskHistoryItem.init)
                    guard let item = tasks.first(where: { $0.method == "forecast" && $0.canRestoreResult }) else {
                        fputs("Historical replay has no restorable UI task\n", stderr); exit(1)
                    }
                    store.restoreTaskResult(item)
                    for _ in 0..<200 where store.restoringTaskHistoryID != nil {
                        try? await Task.sleep(for: .milliseconds(100))
                    }
                    guard store.restoringTaskHistoryID == nil, store.taskHistoryError.isEmpty else {
                        fputs("Historical UI restore failed: \(store.taskHistoryError)\n", stderr); exit(1)
                    }
                    // UX #1：恢复结果后表单/标题必须回填结果的原始参数。
                    if let retry = item.retryRequest {
                        let payload = retry.payload
                        if let value = payload["name"] as? String {
                            precondition(store.name == value, "恢复后名称未回填")
                        }
                        if let value = (payload["lat"] as? NSNumber)?.doubleValue {
                            precondition(abs(store.lat - value) < 1e-9, "恢复后纬度未回填")
                        }
                        if let value = (payload["lon"] as? NSNumber)?.doubleValue {
                            precondition(abs(store.lon - value) < 1e-9, "恢复后经度未回填")
                        }
                        if let value = payload["target_date"] as? String {
                            precondition(store.targetDateLabel == value, "恢复后日期未回填")
                        }
                        precondition(store.resultParams != nil, "恢复结果缺少原始参数快照")
                        precondition(!store.resultParametersStale, "恢复后表单与结果参数应一致")
                        print("Historical restore backfill: ready")
                    }
                    // Single-model contract: a restored regional snapshot may
                    // contain only one model. Keep the product's restored
                    // selection when the requested model is absent instead of
                    // forcing a model with no points to inspect.
                    let requestedModel = env["FIRESKY_TEST_MODEL"] ?? "gfs"
                    let restoredModels = store.result?["model_results"] as? JSONObject
                    if restoredModels?[requestedModel] != nil || restoredModels == nil {
                        store.selectedModel = requestedModel
                    }
                    if isRegional, store.regionalDisplay != "compare" {
                        store.regionalDisplay = store.selectedModel
                    }
                    if isRegional, let report = store.modelResult,
                       let point = (report["points"] as? [JSONObject])?.first,
                       let lat = point["lat"] as? NSNumber, let lon = point["lon"] as? NSNumber {
                        store.selectedRegionalCoordinate = GridCoordinate(lat: lat.doubleValue, lon: lon.doubleValue)
                    }
                    print("Historical replay UI restore: ready")
                } catch {
                    fputs("Historical UI restore failed: \(error)\n", stderr); exit(1)
                }
                    let tolerateStale = env["FIRESKY_TEST_ALLOW_STALE_PROFILE"] == "1"
                    if isRegional, let coordinate = store.selectedRegionalCoordinate {
                        store.queryRegionalProfile(model: store.selectedModel, coordinate: coordinate)
                        for _ in 0..<200 where store.regionalProfileState == "loading" {
                            try? await Task.sleep(for: .milliseconds(100))
                        }
                        guard store.regionalProfileState == "ready"
                                || (tolerateStale && store.regionalProfileError.contains("过期")) else {
                            fputs("Regional profile failed: \(store.regionalProfileError)\n", stderr); exit(1)
                        }
                        if store.regionalProfileState != "ready" {
                            print("Regional frozen profile expired (expected for historical fixture)")
                        }
                    } else if !isNowcast {
                        store.queryPointProfile()
                        for _ in 0..<200 where store.frozenProfileState == "loading" {
                            try? await Task.sleep(for: .milliseconds(100))
                        }
                        guard store.frozenProfileState == "ready"
                                || (tolerateStale && store.frozenProfileError.contains("过期")) else {
                            fputs("Point profile failed: \(store.frozenProfileError)\n", stderr); exit(1)
                        }
                        if store.frozenProfileState != "ready" {
                            print("Point frozen profile expired (expected for historical fixture)")
                        }
                    }
                    print("Historical replay FFI profile: ready (\(store.selectedModel))")
            }
            if env["FIRESKY_TEST_DOWNLOAD_PROBE"] == "1" {
                store.page = .data
                for _ in 0..<100 where !store.online {
                    try? await Task.sleep(for: .milliseconds(100))
                }
                store.probeDownloads()
                if env["FIRESKY_TEST_CANCEL_PROBE"] == "1" {
                    try? await Task.sleep(for: .milliseconds(300))
                    store.cancelDownloadProbe()
                }
                while store.probingDownloads {
                    try? await Task.sleep(for: .milliseconds(200))
                }
                print("Download probe UI: " + store.downloadProbeRows.map { "\($0.source)=\($0.state)" }.joined(separator: ", "))
                if env["FIRESKY_TEST_PROBE_SETTINGS"] == "1" { store.page = .settings }
            }
            if env["FIRESKY_TEST_HISTORY"] == "1" {
                store.page = .history
                store.loadTaskHistory()
                for _ in 0..<200 where !store.taskHistoryLoaded {
                    try? await Task.sleep(for: .milliseconds(100))
                }
                if let item = store.taskHistory.first(where: { $0.method == "forecast" && $0.canRestoreResult })
                    ?? store.taskHistory.first {
                    store.selectTaskHistory(item.taskID)
                }
            }
            if let exportPath = env["FIRESKY_TEST_EXPORT_DUMP"] {
                let text = SummaryExport.summaryText(store: store, comparison: store.pointComparison)
                try? text.write(to: URL(fileURLWithPath: exportPath), atomically: true, encoding: .utf8)
            }
            if let width = env["FIRESKY_NATIVE_WIDTH"].flatMap(Double.init),
               let height = env["FIRESKY_NATIVE_HEIGHT"].flatMap(Double.init),
               width >= 1120, height >= 720, width <= 2560, height <= 1600 {
                NSApp.windows.first(where: { $0.isVisible })?.setContentSize(NSSize(width: width, height: height))
            }
            if env["FIRESKY_TEST_CALENDAR"] == "1" {
                try? await Task.sleep(for: .seconds(1))
                store.showDatePicker = true
            }
            let delay = env["FIRESKY_TEST_CAPTURE_DELAY"].flatMap(Double.init) ?? 4
            try? await Task.sleep(for: .seconds(min(600, max(1, delay))))
            if let scrollStr = env["FIRESKY_TEST_SCROLL_Y"], let scrollY = Double(scrollStr), scrollY > 0,
               let mainWindow = NSApp.windows.first(where: { $0.isVisible }),
               let rootView = mainWindow.contentView {
                @MainActor
                func collectScrollViews(_ v: NSView) -> [NSScrollView] {
                    var res: [NSScrollView] = []
                    if let sv = v as? NSScrollView { res.append(sv) }
                    for sub in v.subviews { res.append(contentsOf: collectScrollViews(sub)) }
                    return res
                }
                let target = collectScrollViews(rootView)
                    .compactMap { sv -> (NSScrollView, NSView, CGFloat)? in
                        guard let doc = sv.documentView else { return nil }
                        let overflow = doc.frame.height - sv.contentView.bounds.height
                        return overflow > 100 ? (sv, doc, overflow) : nil
                    }
                    .max(by: { $0.2 < $1.2 })
                if let (sv, doc, maxOffset) = target {
                    let clamped = min(maxOffset, CGFloat(scrollY))
                    let targetY = doc.isFlipped ? clamped : max(0, maxOffset - clamped)
                    sv.contentView.scroll(to: NSPoint(x: 0, y: targetY))
                    sv.reflectScrolledClipView(sv.contentView)
                    try? await Task.sleep(for: .milliseconds(450))
                }
            }
            guard let main = NSApp.windows.first(where: { $0.isVisible }),
                  let view = (main.attachedSheet ?? main).contentView,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { NSApp.terminate(nil); return }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if let bytes = bitmap.representation(using: .png, properties: [:]) {
                try? bytes.write(to: URL(fileURLWithPath: path))
            }
            if main.attachedSheet != nil {
                store.showUpdateSheet = false
                store.closePreflight()
                try? await Task.sleep(for: .milliseconds(350))
            }
            store.shutdown()
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { exit(0) }
            NSApp.terminate(nil)
        }
    }

    private static func regionalReport(_ model: String) -> [String: Any] {
        var points: [[String: Any]] = []
        var errors: [[String: Any]] = []
        for y in 0...20 {
            for x in 0...20 {
                let lat = 28 + Double(y) * 0.25, lon = 118 + Double(x) * 0.25
                if (x + y) % (model == "gfs" ? 19 : 11) == 0 {
                    errors.append(["lat": lat, "lon": lon, "error": "测试缺测"])
                } else {
                    let dx = Double(x) - (model == "gfs" ? 8 : 14), dy = Double(y) - 12
                    let quality = max(0, 2.3 - sqrt(dx * dx + dy * dy) / 6)
                    points.append(["lat": lat, "lon": lon, "peak_quality_index": quality,
                                   "peak_quality_level": "测试档位", "best_time_local": "2026-11-09T17:10:00+08:00"])
                }
            }
        }
        let cityItems: [[String: Any]] = [
            ["name": "杭州", "lat": 30.2741, "lon": 120.1551,
             "peak_quality_index": model == "gfs" ? 2.08 : 1.86,
             "best_time_local": "2026-11-09T17:10:00+08:00", "timezone_name": "Asia/Shanghai"],
            ["name": "宁波", "lat": 29.8683, "lon": 121.5440,
             "peak_quality_index": model == "gfs" ? 1.84 : 1.63,
             "best_time_local": "2026-11-09T17:05:00+08:00", "timezone_name": "Asia/Shanghai"],
            ["name": "绍兴", "lat": 30.0303, "lon": 120.5802,
             "peak_quality_index": model == "gfs" ? 1.71 : 1.54,
             "best_time_local": "2026-11-09T17:15:00+08:00", "timezone_name": "Asia/Shanghai"],
        ]
        return ["points": points, "errors": errors, "point_count": 441, "error_count": errors.count,
                "success_count": points.count, "regions": ["fixture"],
                "precision": ["point_spacing_deg": 0.25, "model_grid_spacing_deg": 0.25],
                "timing": ["prefetch_seconds": 1.2, "native_timings_ms": ["assessment": 123.4]],
                "cycle_time_utc": model == "gfs" ? "2026-11-09T00:00:00Z" : "2026-11-08T18:00:00Z",
                "city_ranking": ["contract": "rust-regional-city-ranking-v1", "status": "ready",
                                 "source": "china-city-catalog", "model_source": model,
                                 "count": cityItems.count, "items": cityItems]]
    }

    private static func nowcastReport() -> [String: Any] {
        var value = report("gfs")
        let samples: [[String: Any]] = (0..<12).map { index in
            let levels: [[String: Any]] = (0..<8).map { layer in
                ["height_m": Double(layer) * 1500 + 500, "status": "retrieved",
                 "hsd_posterior_percent": Double((index * 17 + layer * 29) % 100)]
            }
            return ["distance_km": Double(index) * 20, "bearing_deg": 240.0,
                    "status": index == 5 ? "unknown" : "retrieved",
                    "levels": levels]
        }
        value["nowcast"] = [
            "image_valid_time_utc": "2026-11-09T09:00:00Z",
            "convective_score_ceiling": 0.64,
            "geometric_path_blocking_risk": 0.17,
            "calibrated_sample_coverage_ratio": 0.91,
            "near_cloud_ratio": 0.42, "far_cloud_ratio": 0.68,
            "satellite_low_cloud_state": "detected", "confidence": 0.76,
            "satellite_motion_quality": "strong", "satellite_motion_age_minutes": 7,
            "satellite_advection_horizon_hours": 0.5, "motion_correlation": 0.83,
            "radar_source": "测试雷达（非真实观测）",
            "radar_max_dbz": 12.4,
            "radar_reliability_score": 0.82,
            "satellite_vertical_profile": [
                "status": "available",
                "samples": samples,
            ],
        ]
        value["summary"] = "界面测试数据，非真实实况分析。"
        value["cross_section"] = ["solar_azimuth_deg": 240.0]
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_NOWCAST_MAP_UNAVAILABLE"] == "1" {
            value["map_capability"] = [
                "available": false,
                "reason": "卫星扫描与目标时刻相差超过 30 分钟（界面测试）",
            ]
        }
        return value
    }

    private static func report(_ model: String) -> [String: Any] {
        let ec = model == "ec"
        let values = ec ? [0.3, 0.8, 1.36, 1.1] : [0.2, 0.4, 0.72, 0.5]
        return [
            "peak_quality_index": ec ? 1.36 : 0.72, "peak_quality_level": "测试峰值档位", "quality_level": "测试事件档位",
            "convective_risk": ec ? 0.12 : 0.18,
            "dominant_burnable_cloud_base_agl_mean_m": 4200,
            "dominant_burnable_cloud_top_agl_mean_m": 6800,
            "forecast_mode": "point", "compute_backend": "cpu", "analysis_radius_km": 300,
            "timezone_name": "Asia/Shanghai", "cycle_time_utc": "2026-11-09T00:00:00Z",
            "cloud_genus_proxy": [
                "label": ec ? "altocumulus" : "cirrus",
                "compatible_genera": ec ? ["altocumulus", "altostratus"] : ["cirrus", "cirrostratus"],
                "candidate_scores": ec
                    ? ["altocumulus": 0.68, "altostratus": 0.22, "nimbostratus": 0.10]
                    : ["cirrus": 0.72, "cirrostratus": 0.19, "cirrocumulus": 0.09],
                "confidence": ec ? 0.68 : 0.72,
            ],
            "confidence_score_semantics": "input-and-geometry-support",
            "source_roles": ["primary": "numerical_model", "auxiliary": "satellite_observation"],
            "data_provenance": ["observation_used": false, "cams_cache_used": true],
            "cache": ["status": "hit", "generated_at_utc": "2026-11-09T09:01:00Z"],
            "color_diagnosis": ["type": "orange_red", "confidence": 0.75, "cloud_phase": "ice"],
            "cloud_evolution": ["trend": "strengthening", "target_score_change": 0.12,
                                "cloud_hole_closure_risk": 0.1,
                                "comparison_start_local": "2026-11-09T17:00:00+08:00",
                                "comparison_end_local": "2026-11-09T17:15:00+08:00"],
            "cross_section": crossSection(model),
            "anchor_local": "2026-11-09T17:00:00+08:00",
            "aerosol": ["aod550": 0.21, "source": "cams-ads fixture", "quality": "cams"],
            "best_time_local": "2026-11-09T17:10:00+08:00",
            "timing_window": ["start_time_local": "2026-11-09T17:00:00+08:00", "end_time_local": "2026-11-09T17:15:00+08:00"],
            "duration_minutes": ec ? 31 : 18, "confidence_score": ec ? 0.79 : 0.61,
            "model_disagreement_score": ec ? 0.2 : 0.3,
            "quality_components": [
                "burnable_cloud_coverage_effective": ec ? 0.66 : 0.42,
                "cloud_material": ec ? 0.73 : 0.59, "brightness_color": ec ? 0.84 : 0.55,
                "quality_aod550": ec ? 0.23 : 0.21, "incoming_aod_illumination_factor": ec ? 0.92 : 0.86,
                "observer_aod_contrast_factor": ec ? 0.81 : 0.77,
            ],
            "steps": values.enumerated().map { index, value -> [String: Any] in
                ["valid_time_utc": String(format: "2026-11-09T09:%02d:00Z", index * 5),
                 "valid_time_local": String(format: "2026-11-09T17:%02d:00+08:00", index * 5),
                 "instant_quality_index": value, "instant_quality_level": "测试档位",
                 "physically_burnable": index > 0, "solar_elevation_deg": Double(index) - 3,
                 "solar_azimuth_deg": 240.0, "burnable_cloud_coverage": 0.42,
                 "dominant_burnable_cloud_base_agl_mean_m": 4200,
                 "dominant_burnable_cloud_top_agl_mean_m": 6800,
                 "quality_aod550": 0.21, "selected_regime": "burnable_cloud_field", "valid": true]
            },
            "spectral_color": spectralColorFixture(model),
            "azimuth_radar": azimuthRadarFixture(model),
        ]
    }

    private static func spectralColorFixture(_ model: String) -> [String: Any] {
        let ec = model == "ec"
        let hexes = ec
            ? ["#F6C46A", "#F2A152", "#EC7C43", "#E85D3F", "#D94649", "#B93B5B", "#8F3561"]
            : ["#F8D17E", "#F5B562", "#F2944B", "#F07A3E", "#E36243", "#C54F54", "#98415C"]
        let labelsZh = ["金黄微光", "琥珀橙", "亮橙红", ec ? "绯红火烧云" : "金橙火烧云", "胭脂晚霞", "暮色紫红", "深绛蓝调"]
        let labelsEn = ["Gold", "Amber", "Orange", ec ? "Crimson Glow" : "Golden Orange Glow", "Carmine", "Magenta", "Deep Dusk"]
        let minutes = [55, 0, 5, 10, 15, 20, 25]
        let hours = [16, 17, 17, 17, 17, 17, 17]
        let stepRows: [[String: Any]] = hexes.indices.map { idx in
            [
                "valid_time_local": String(format: "2026-11-09T%02d:%02d:00+08:00", hours[idx], minutes[idx]),
                "offset_minutes": (idx - 3) * 5,
                "hex": hexes[idx],
                "label_zh": labelsZh[idx],
                "label_en": labelsEn[idx],
                "luminance": 0.85 - abs(Double(idx - 3)) * 0.14,
                "is_peak": idx == 3,
            ]
        }
        return [
            "contract": "firesky-spectral-color-v1",
            "peak_hex": hexes[3],
            "peak_label_zh": labelsZh[3],
            "peak_label_en": labelsEn[3],
            "peak_time_local": "2026-11-09T17:10:00+08:00",
            "sky_gradient_hex": ec
                ? ["#1A2D5E", "#6F4E7C", "#E85D3F", "#F7A05E"]
                : ["#1E3468", "#7A5B7A", "#F07A3E", "#F8BC73"],
            "solar_elevation_deg": ec ? -1.4 : -0.9,
            "solar_azimuth_deg": 240.0,
            "cloud_base_km": 4.2,
            "aod550": ec ? 0.23 : 0.21,
            "steps": stepRows,
        ]
    }

    private static func azimuthRadarFixture(_ model: String) -> [String: Any] {
        let ec = model == "ec"
        let dirs: [(String, String)] = [
            ("北", "N"), ("东北偏北", "NNE"), ("东北", "NE"), ("东北偏东", "ENE"),
            ("东", "E"), ("东南偏东", "ESE"), ("东南", "SE"), ("东南偏南", "SSE"),
            ("南", "S"), ("西南偏南", "SSW"), ("西南", "SW"), ("西南偏西", "WSW"),
            ("西", "W"), ("西北偏西", "WNW"), ("西北", "NW"), ("西北偏北", "NNW"),
        ]
        let peakDeg = ec ? 247.5 : 225.0
        let peakScore = ec ? 0.84 : 0.72
        let bearings: [[String: Any]] = dirs.enumerated().map { idx, pair in
            let deg = Double(idx) * 22.5
            let diff = abs(((deg - peakDeg + 540).truncatingRemainder(dividingBy: 360)) - 180)
            let shape = max(0.18, cos(diff * .pi / 180.0) * 0.5 + 0.5)
            let score = min(1.0, max(0.14, peakScore * shape))
            let canvas = min(1.0, max(0.22, score + 0.08))
            let lowBlock = min(0.85, max(0.08, 0.42 - 0.30 * shape))
            return [
                "deg": deg,
                "label_zh": pair.0,
                "label_en": pair.1,
                "score": (score * 1000).rounded() / 1000,
                "canvas": (canvas * 1000).rounded() / 1000,
                "low_block": (lowBlock * 1000).rounded() / 1000,
            ]
        }
        return [
            "contract": "firesky-azimuth-radar-v1",
            "solar_azimuth_deg": 240.0,
            "best_azimuth_deg": peakDeg,
            "best_direction_zh": ec ? "西南偏西" : "西南",
            "best_direction_en": ec ? "WSW" : "SW",
            "best_score": peakScore,
            "recommendation_zh": ec
                ? "建议面向西南偏西（248°）拍摄，中高云画布条件最佳，近处低云遮挡少"
                : "建议面向西南（225°）拍摄，中高云画布条件最佳，近处低云遮挡少",
            "recommendation_en": ec
                ? "Best view toward WSW (248°): optimal mid/high canvas with low near-field blocking"
                : "Best view toward SW (225°): optimal mid/high canvas with low near-field blocking",
            "bearings": bearings,
        ]
    }

    private static func crossSection(_ model: String) -> [String: Any] {
        let distances = (0...24).map { Double($0) * 12.5 }
        let levels = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150]
        let levelHeights = [180.0, 760, 1450, 3050, 4400, 5800, 7600, 9200, 10500, 11900, 13400]
        let levelTemps = [15.0, 9, 3, -6, -14, -24, -35, -44, -50, -55, -60]
        let terrain = distances.indices.map { sample in
            let westRidge = 620 * exp(-pow((Double(sample) - 10) / 3.8, 2))
            let eastRidge = 390 * exp(-pow((Double(sample) - 20) / 2.6, 2))
            return 120 + westRidge + eastRidge
        }
        func family(_ level: Int) -> Int { level >= 7 ? 1 : level >= 4 ? 2 : 3 }
        func cloudy(_ sample: Int, _ level: Int) -> Bool {
            let shift = model == "ec" ? 3 : 0
            switch family(level) {
            case 1: return level >= 7 && (5 + shift...19 + shift).contains(sample)
            case 2: return (2 + shift...14 + shift).contains(sample)
            default: return level <= 2 && (14 - shift...24).contains(sample)
            }
        }
        func grid(_ value: (Int, Int) -> Double) -> [[Double]] {
            distances.indices.map { sample in levels.indices.map { level in value(sample, level) } }
        }
        func intGrid(_ value: (Int, Int) -> Int) -> [[Int]] {
            distances.indices.map { sample in levels.indices.map { level in value(sample, level) } }
        }
        let baseByFamily: [Int: Double] = [1: 8200, 2: 3900, 3: 700]
        let topByFamily: [Int: Double] = [1: 14600, 2: 8800, 3: 3400]
        let fields: [String: Any] = [
            "geopotential_height_asl_m": grid { sample, level in
                levelHeights[level] + terrain[sample] + 70 * sin(Double(sample + level) * 0.4)
            },
            "temperature_c": grid { sample, level in
                levelTemps[level] + 1.2 * sin(Double(sample) * 0.35)
            },
            "rh_percent": grid { sample, level in
                cloudy(sample, level) ? 78 + Double((sample * 7 + level * 11) % 20)
                    : 28 + Double((sample * 5 + level * 3) % 34)
            },
            "cloud_amount": grid { sample, level in
                cloudy(sample, level) ? 0.32 + Double((sample + level * 3) % 5) * 0.12 : 0
            },
            "cloud_presence": grid { sample, level in cloudy(sample, level) ? 1 : 0 },
            "cloud_fraction_percent": grid { sample, level in
                cloudy(sample, level) ? 38 + Double((sample * 3 + level) % 6) * 10 : 0
            },
            "pressure_family_code": intGrid { _, level in family(level) },
            "cloud_layer_status": intGrid { sample, level in
                guard cloudy(sample, level) else { return 0 }
                if (10...13).contains(sample) { return 3 }
                if (14...16).contains(sample) { return 2 }
                return 0
            },
            "cloud_layer_base_asl_m": grid { sample, level in
                cloudy(sample, level) ? baseByFamily[family(level)] ?? 0 : 0
            },
            "cloud_layer_top_asl_m": grid { sample, level in
                cloudy(sample, level) ? topByFamily[family(level)] ?? 0 : 0
            },
        ]
        return [
            "solar_azimuth_deg": 240.0,
            "solar_elevation_deg": -2.0,
            "analysis_radius_km": 300,
            "spatial_sampling": "测试样例 · 非真实预测",
            "distances_km": distances,
            "pressure_levels_hpa": levels,
            "terrain_elevation_asl_m": terrain,
            "required_illumination_base_asl_m": distances.indices.map { 5200 + Double($0) * 115 },
            "theoretical_targets": [
                ["distance_km": 75.0, "cloud_base_asl_m": 8200.0, "cloud_top_asl_m": 14600.0],
                ["distance_km": 175.0, "cloud_base_asl_m": 3900.0, "cloud_top_asl_m": 8800.0],
            ],
            "fields": fields,
        ]
    }
}
