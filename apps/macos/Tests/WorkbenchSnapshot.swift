import AppKit
import SwiftUI

// Screenshot fixture only; excluded from the shipped app's Sources target.
@main
struct WorkbenchSnapshot: App {
    @NSApplicationDelegateAdaptor(WorkbenchSnapshotDelegate.self) private var delegate
    @StateObject private var store = Store()

    var body: some Scene {
        WindowGroup("FireSky · UI Test Fixture") {
            WorkbenchRootView().environmentObject(store)
                .environment(\.locale, Locale(identifier: "zh_CN"))
                .onAppear { delegate.capture(store) }
        }.defaultSize(width: 1440, height: 1040)
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

    func capture(_ store: Store) {
        guard !scheduled, let path = ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] else { return }
        scheduled = true
        self.store = store
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
        store.selectedModel = ProcessInfo.processInfo.environment["FIRESKY_TEST_MODEL"] ?? (partial ? "ec" : "gfs")
        if let tab = ProcessInfo.processInfo.environment["FIRESKY_TEST_TAB"] { store.resultTab = tab }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_CALENDAR"] == "1" {
            store.dateTab = "自定义"
        }
        if ProcessInfo.processInfo.environment["FIRESKY_TEST_TUTORIAL"] == "1" {
            store.openCAMSSetup()
        }
        Task {
            let env = ProcessInfo.processInfo.environment
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
                if isRegional, let coordinate = store.selectedRegionalCoordinate {
                    store.queryRegionalProfile(model: store.selectedModel, coordinate: coordinate)
                    for _ in 0..<200 where store.regionalProfileState == "loading" {
                        try? await Task.sleep(for: .milliseconds(100))
                    }
                    guard store.regionalProfileState == "ready" else {
                        fputs("Regional profile failed: \(store.regionalProfileError)\n", stderr); exit(1)
                    }
                } else if !isNowcast {
                    store.queryPointProfile()
                    for _ in 0..<200 where store.frozenProfileState == "loading" {
                        try? await Task.sleep(for: .milliseconds(100))
                    }
                    guard store.frozenProfileState == "ready" else {
                        fputs("Point profile failed: \(store.frozenProfileError)\n", stderr); exit(1)
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
            guard let main = NSApp.windows.first(where: { $0.isVisible }),
                  let view = (main.attachedSheet ?? main).contentView,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { NSApp.terminate(nil); return }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if let bytes = bitmap.representation(using: .png, properties: [:]) {
                try? bytes.write(to: URL(fileURLWithPath: path))
            }
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
            "confidence_score_semantics": "input-and-geometry-support",
            "source_roles": ["primary": "numerical_model", "auxiliary": "satellite_observation"],
            "data_provenance": ["observation_used": false, "cams_cache_used": true],
            "cache": ["status": "hit", "generated_at_utc": "2026-11-09T09:01:00Z"],
            "color_diagnosis": ["type": "orange_red", "confidence": 0.75, "cloud_phase": "ice"],
            "cloud_evolution": ["trend": "strengthening", "target_score_change": 0.12,
                                "cloud_hole_closure_risk": 0.1,
                                "comparison_start_local": "2026-11-09T17:00:00+08:00",
                                "comparison_end_local": "2026-11-09T17:15:00+08:00"],
            "cross_section": [
                "solar_azimuth_deg": 240.0, "solar_elevation_deg": -2.0,
                "distances_km": [0.0, 100.0, 200.0],
                "required_illumination_base_agl_m": [1000.0, 2500.0, 4000.0],
                "theoretical_targets": [
                    ["distance_km": 0.0, "cloud_base_agl_m": 4200, "cloud_top_agl_m": 6800],
                    ["distance_km": 100.0, "cloud_base_agl_m": 4300, "cloud_top_agl_m": 7000],
                    ["distance_km": 200.0, "cloud_base_agl_m": 4400, "cloud_top_agl_m": 7200],
                ],
            ],
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
        ]
    }
}
