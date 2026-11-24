import Foundation

/// MockTransport：进程内假引擎，完全走 EngineTransport 的帧语义——JSON 行进、JSON 行出，
/// 模拟 hello → check → prepare → forecast 全链路、progress 帧、取消、保留策略、
/// 后台同步、CAMS 配置 / 清单 / 下载探测，供模拟器冒烟与单元测试使用。
@MainActor
final class MockTransport: EngineTransport {
    var onFrame: ((Data) -> Void)?
    private var cancelled: Set<String> = []
    private var camsConfigured = false
    private var lastReport: JSONObject = [:]
    private var retentionSettings: JSONObject = [:]
    private var syncSettings: JSONObject = [
        "enabled": false,
        "sources": ["cams": false, "gfs": false, "ec": false],
    ]

    nonisolated init() {}

    func start(workspace: String) throws {
        cancelled = []
        lastReport = [:]
    }

    func send(_ frame: Data) throws {
        guard let request = try? JSONSerialization.jsonObject(with: frame) as? JSONObject,
              let id = request["id"] as? String else {
            throw EngineError(message: "MockTransport 收到无法解析的帧。")
        }
        let method = request["method"] as? String ?? ""
        let params = request["params"] as? JSONObject ?? [:]
        Task { [weak self] in
            await self?.handle(id: id, method: method, params: params)
        }
    }

    func cancel(requestID: String) {
        cancelled.insert(requestID)
    }

    func stop() {
        cancelled = []
        onFrame = nil
    }

    // MARK: - 帧输出

    private func emit(_ frame: JSONObject) {
        guard var data = try? JSONSerialization.data(withJSONObject: frame) else { return }
        data.append(10)
        onFrame?(data)
    }

    private func emitProgress(id: String, percent: Double, source: String, message: String) {
        emit(["id": id, "type": "progress",
              "event": ["percent": percent, "source": source, "message": message]])
    }

    private func emitEvent(id: String, _ event: JSONObject) {
        emit(["id": id, "type": "progress", "event": event])
    }

    private func emitResult(id: String, _ result: JSONObject) {
        emit(["id": id, "type": "result", "result": result])
    }

    private func emitError(id: String, _ message: String) {
        emit(["id": id, "type": "error", "error": ["message": message]])
    }

    // MARK: - 方法分派

    private func handle(id: String, method: String, params: JSONObject) async {
        switch method {
        case "hello":
            await pause(150)
            emitResult(id: id, [
                "protocol": 1,
                "engine": "firesky-rust-mock",
                "cams_credentials": camsCredentials(),
                "regions": [
                    ["code": "china", "label": "中国"],
                    ["code": "east_asia", "label": "东亚"],
                ],
            ])
        case "search":
            await pause(400)
            emitResult(id: id, Self.searchResult(query: params["query"] as? String ?? ""))
        case "metadata":
            await pause(300)
            emitResult(id: id, ["elevation_m": 12.0, "timezone_name": "Asia/Shanghai", "source": "open-meteo-point-metadata"])
        case "check":
            await checkFlow(id: id, kind: params["kind"] as? String ?? "point")
        case "publications":
            await pause(350)
            emitResult(id: id, ["report": lastReport])
        case "prepare":
            await prepareFlow(id: id)
        case "forecast":
            await forecastFlow(id: id)
        case "dismiss":
            emitResult(id: id, ["ok": true])
        case "inventory":
            emitResult(id: id, ["datasets": Self.mockInventory, "categories": Self.mockCategories])
        case "retention_settings":
            let configured = !retentionSettings.values.allSatisfy { $0 is NSNull } && !retentionSettings.isEmpty
            emitResult(id: id, ["settings": retentionSettings, "configured": configured])
        case "save_retention_settings":
            retentionSettings = params["settings"] as? JSONObject ?? [:]
            let configured = !retentionSettings.values.allSatisfy { $0 is NSNull } && !retentionSettings.isEmpty
            emitResult(id: id, ["settings": retentionSettings, "configured": configured])
        case "apply_retention_settings":
            let configured = !retentionSettings.values.allSatisfy { $0 is NSNull } && !retentionSettings.isEmpty
            emitResult(id: id, [
                "configured": configured,
                "result": ["removed_count": configured ? 3 : 0, "freed_mb": configured ? 48.2 : 0.0,
                           "deferred_count": 0, "failed_count": 0],
            ])
        case "data_sync_settings":
            let configured = syncSettings["enabled"] as? Bool == true
            let retentionConfigured = !retentionSettings.values.allSatisfy { $0 is NSNull } && !retentionSettings.isEmpty
            emitResult(id: id, [
                "settings": syncSettings,
                "configured": configured,
                "retention_configured": retentionConfigured,
                "state": ["sources": [:] as JSONObject],
            ])
        case "save_data_sync_settings":
            syncSettings = params["settings"] as? JSONObject ?? syncSettings
            let configured = syncSettings["enabled"] as? Bool == true
            let retentionConfigured = !retentionSettings.values.allSatisfy { $0 is NSNull } && !retentionSettings.isEmpty
            emitResult(id: id, [
                "settings": syncSettings,
                "configured": configured,
                "retention_configured": retentionConfigured,
                "state": ["sources": [:] as JSONObject],
            ])
        case "data_sync_due":
            emitResult(id: id, ["due_for_any": false, "due_sources": [] as [String]])
        case "data_sync_run":
            let sources = params["sources"] as? [String] ?? []
            var sourceMap: JSONObject = [:]
            for s in sources { sourceMap[s] = ["status": "current"] }
            emitResult(id: id, ["sources": sourceMap])
        case "task_history":
            emitResult(id: id, Self.mockTaskHistory)
        case "task_events":
            emitResult(id: id, Self.mockTaskEvents)
        case "task_forget":
            emitResult(id: id, ["forgotten": true])
        case "result_restore":
            emitResult(id: id, ["model_results": ["gfs": ["score": 0.8]]])
        case "data_delete_preview":
            emitResult(id: id, [
                "contract": "rust-data-delete-preview-v1",
                "preview_id": "mock0preview000001",
                "file_count": 42,
                "total_mb": 512.5,
                "protected": [],
            ])
        case "data_delete_execute":
            emitResult(id: id, [
                "contract": "rust-data-delete-execute-v1",
                "deleted": 42, "skipped": [], "failed": [], "freed_mb": 512.5,
            ])
        case "terrain_inventory":
            var inventory: JSONObject = [
                "contract": "rust-terrain-inventory-v1",
                "tile_count": 3,
                "tiles": [["name": "N30E119", "bytes": 25_934_402],
                          ["name": "N30E120", "bytes": 25_934_402],
                          ["name": "N31E120", "bytes": 25_934_402]],
                "coverage_ok": true,
                "tile": ["name": "N30E120", "exists": true, "bytes": 25_934_402,
                         "lat_range": [30.0, 31.0], "lon_range": [120.0, 121.0]],
            ]
            if (params["estimate_global"] as? Bool) == true {
                let bounds = (params["bounds"] as? JSONObject) ??
                    ["south": -56, "north": 59, "west": -180, "east": 179]
                let south = (bounds["south"] as? NSNumber)?.intValue ?? -56
                let north = (bounds["north"] as? NSNumber)?.intValue ?? 59
                let west = (bounds["west"] as? NSNumber)?.intValue ?? -180
                let east = (bounds["east"] as? NSNumber)?.intValue ?? 179
                let candidates = (north - south + 1) * (east - west + 1)
                inventory["global"] = ["candidates": candidates, "existing": 2,
                                       "estimated_bytes": candidates * 6_000_000,
                                       "estimated_gb": Double(candidates) * 0.006,
                                       "bounds": bounds]
            }
            emitResult(id: id, inventory)
        case "terrain_download":
            await mockTerrainDownload(id: id)
        case "network_probe":
            await probeFlow(id: id)
        case "cams_credentials":
            emitResult(id: id, camsCredentials())
        case "save_cams_key":
            let key = (params["key"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !key.isEmpty else { emitError(id: id, "Key 不能为空。"); return }
            camsConfigured = true
            emitResult(id: id, camsCredentials())
        default:
            emitError(id: id, "未知方法 \(method)（MockTransport）")
        }
    }

    private func camsCredentials() -> JSONObject {
        ["configured": camsConfigured, "can_save": true,
         "state": camsConfigured ? "configured" : "not_configured"]
    }

    private func pause(_ milliseconds: Int) async {
        try? await Task.sleep(for: .milliseconds(milliseconds))
    }

    // MARK: - 会话链路：check → prepare → forecast

    private func checkFlow(id: String, kind: String) async {
        for (percent, source, message) in [
            (5.0, "gfs", "正在检查本地数据"),
            (45.0, "ecmwf", "正在核对 EC 发布周期"),
            (90.0, "cams", "正在检查 CAMS 授权状态"),
        ] {
            if isCancelled(id) { emitError(id: id, "已取消"); return }
            emitProgress(id: id, percent: percent, source: source, message: message)
            await pause(280)
        }
        if isCancelled(id) { emitError(id: id, "已取消"); return }
        lastReport = [
            "ready": false,
            "datasets": [
                ["source": "gfs", "ready": true, "verified": true, "is_latest": true, "missing_count": 0],
                ["source": "ecmwf", "ready": false, "verified": false, "is_latest": false, "missing_count": 8],
                ["source": "cams", "ready": false, "verified": false, "is_latest": false, "missing_count": 2],
            ],
        ]
        emitResult(id: id, ["session": "mock-\(UUID().uuidString)",
                            "report": lastReport, "kind": kind])
    }

    private func prepareFlow(id: String) async {
        for (percent, source, message) in [
            (8.0, "gfs", "本地数据校验完成"),
            (34.0, "ecmwf", "正在下载 EC 开放数据"),
            (58.0, "ecmwf", "正在下载 EC 开放数据"),
            (82.0, "cams", "正在下载 CAMS 大气成分"),
        ] {
            if isCancelled(id) { emitError(id: id, "已取消"); return }
            emitProgress(id: id, percent: percent, source: source, message: message)
            await pause(520)
        }
        if isCancelled(id) { emitError(id: id, "已取消"); return }
        lastReport = [
            "ready": true,
            "datasets": [
                ["source": "gfs", "ready": true, "verified": true, "is_latest": true, "missing_count": 0],
                ["source": "ecmwf", "ready": true, "verified": true, "is_latest": true, "missing_count": 0],
                ["source": "cams", "ready": true, "verified": true, "is_latest": true, "missing_count": 0],
            ],
        ]
        emitResult(id: id, ["report": lastReport])
    }

    private func forecastFlow(id: String) async {
        for (percent, source, message) in [
            (10.0, "gfs", "正在计算 GFS 霞光质量"),
            (48.0, "gfs", "正在计算 GFS 霞光质量"),
            (74.0, "ec", "正在计算 EC 霞光质量"),
            (94.0, "cams", "正在合成 AOD 与诊断"),
        ] {
            if isCancelled(id) { emitError(id: id, "已取消"); return }
            emitProgress(id: id, percent: percent, source: source, message: message)
            await pause(560)
        }
        if isCancelled(id) { emitError(id: id, "已取消"); return }
        emitResult(id: id, Self.mockForecast)
    }

    private func probeFlow(id: String) async {
        var rows: [JSONObject] = []
        for row in DownloadProbeRow.initial {
            if isCancelled(id) { emitError(id: id, "探测已取消"); return }
            emit(["id": id, "type": "progress", "event": [
                "probe": ["source": row.source, "title": row.title, "state": "testing",
                          "detail": "正在建立连接（模拟）"] as JSONObject,
            ]])
            await pause(700)
            if isCancelled(id) { emitError(id: id, "探测已取消"); return }
            let passed: JSONObject = ["source": row.source, "title": row.title, "state": "passed",
                                      "host": row.source == "gfs" ? "noaa.ncep.noaa.gov"
                                        : row.source == "ec" ? "data.ecmwf.int" : "ads.atmosphere.copernicus.eu",
                                      "detail": "示例数据首块校验通过（模拟）",
                                      "bytes_received": 65536, "elapsed_ms": 812]
            emit(["id": id, "type": "progress", "event": ["probe": passed] as JSONObject])
            rows.append(passed)
        }
        emitResult(id: id, ["datasets": rows])
    }

    private func isCancelled(_ id: String) -> Bool { cancelled.contains(id) }

    // MARK: - 模拟数据

    static func searchResult(query: String) -> JSONObject {
        let table: [(String, String, Double, Double)] = [
            ("西湖", "西湖", 30.2431, 120.1503),
            ("杭州", "杭州", 30.2741, 120.1551),
            ("上海", "上海", 31.2304, 121.4737),
            ("北京", "北京", 39.9042, 116.4074),
        ]
        let match = table.first { query.contains($0.0) }
        let name = match?.1 ?? (query.isEmpty ? "模拟位置" : query)
        let lat = match?.2 ?? 30.0 + Double(abs(name.hashValue) % 100) * 0.01
        let lon = match?.3 ?? 120.0 + Double(abs(name.hashValue) % 100) * 0.01
        return ["name": name, "lat": lat, "lon": lon,
                "elevation_m": 12.0, "timezone_name": "Asia/Shanghai"]
    }

    private static func isoUTC(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }

    private static func localStamp(_ date: Date, timezone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timezone
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter.string(from: date)
    }

    static var mockForecast: JSONObject {
        let timezone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timezone
        let now = Date()
        let evening = calendar.date(bySettingHour: 17, minute: 40, second: 0, of: now) ?? now
        var steps: [JSONObject] = []
        var best = 0.0, bestTime = localStamp(evening, timezone: timezone)
        for minute in stride(from: 0, through: 130, by: 10) {
            let time = calendar.date(byAdding: .minute, value: minute, to: evening) ?? now
            let quality = 1.9 * exp(-pow(Double(minute - 30) / 34.0, 2)) + 0.12
            let solar = 4.2 - Double(minute) * 0.06
            let valid = minute <= 110
            if valid, quality > best { best = quality; bestTime = localStamp(time, timezone: timezone) }
            steps.append([
                "valid_time_utc": isoUTC(time),
                "valid_time_local": localStamp(time, timezone: timezone),
                "instant_quality_index": (quality * 1000).rounded() / 1000,
                "instant_quality_level": quality >= 1.5 ? "值得一看" : "一般",
                "solar_elevation_deg": (solar * 100).rounded() / 100,
                "burnable_cloud_coverage": min(0.85, 0.35 + quality * 0.2),
                "forward_burnable_cloud_ratio": min(0.9, 0.4 + quality * 0.2),
                "observer_return_transmission": 0.72,
                "brightness_color": 0.76,
                "quality_aod550": 0.31,
                "dominant_burnable_pressure_family": "high",
                "dominant_burnable_cloud_base_agl_mean_m": 6400.0,
                "dominant_burnable_cloud_top_agl_mean_m": 8900.0,
                "dominant_burnable_cloud_base_asl_mean_m": 6412.0,
                "dominant_burnable_cloud_top_asl_mean_m": 8912.0,
                "valid": valid,
                "physically_burnable": valid && quality >= 1.0,
            ])
        }
        let distances: [Double] = [0, 50, 100, 150, 200, 250, 300]
        let levels = [250, 300, 500, 700, 850]
        let heights: [[Double?]] = distances.map { _ in [10400, 9200, 5600, 3000, 1500] }
        let temps: [[Double?]] = distances.map { _ in [-48, -38, -12, 2, 12] }
        let rh: [[Double?]] = distances.map { _ in [82, 78, 45, 38, 52] }
        let amounts: [[Double?]] = distances.map { d in
            d >= 100 && d <= 250 ? [0.68, 0.74, 0.12, 0.04, 0.02] : [0.22, 0.25, 0.05, 0.02, 0.02]
        }
        let familyCodes: [[Int]] = distances.map { _ in [1, 1, 2, 2, 3] }
        let statuses: [[Int]] = distances.map { d in
            d >= 100 && d <= 250 ? [3, 3, 0, 0, 0] : [2, 2, 0, 0, 0]
        }
        let layerBases: [[Double?]] = distances.map { _ in [8600, 8600, 5100, 2600, 1100] }
        let layerTops: [[Double?]] = distances.map { _ in [10900, 10900, 6100, 3400, 1800] }
        let terrain: [Double] = [12, 40, 180, 320, 120, 60, 20]
        let requiredASL: [Double] = [12, 650, 1900, 3800, 6200, 8800, 11600]
        let mockCrossSection: JSONObject = [
            "distances_km": distances,
            "pressure_levels_hpa": levels,
            "solar_elevation_deg": 1.8,
            "solar_azimuth_deg": 248.0,
            "analysis_radius_km": 300.0,
            "offset_minutes": 10,
            "valid_time_local": bestTime,
            "spatial_sampling": "sun-ray",
            "terrain_elevation_asl_m": terrain,
            "required_illumination_base_asl_m": requiredASL,
            "fields": [
                "geopotential_height_asl_m": heights,
                "temperature_c": temps,
                "rh_percent": rh,
                "cloud_amount": amounts,
                "pressure_family_code": familyCodes,
                "cloud_layer_status": statuses,
                "cloud_layer_base_asl_m": layerBases,
                "cloud_layer_top_asl_m": layerTops,
            ],
            "theoretical_targets": [
                ["pressure_family": "high", "status": "burnable", "distance_km": 150.0,
                 "cloud_base_asl_m": 8600.0, "cloud_top_asl_m": 10900.0,
                 "cloud_phase": ["label": "ice"]],
            ],
        ]
        func model(aod: Double, material: Double, brightness: Double, genus: String, conf: Double) -> JSONObject {
            [
                "peak_quality_index": (best * 100).rounded() / 100,
                "quality_level": best >= 1.5 ? "值得一看" : "一般",
                "best_time_local": bestTime,
                "timing_window": [
                    "start_time_local": localStamp(calendar.date(byAdding: .minute, value: -25,
                                                                 to: calendar.date(byAdding: .minute, value: 30, to: evening)!) ?? now, timezone: timezone),
                    "end_time_local": localStamp(calendar.date(byAdding: .minute, value: 65,
                                                               to: calendar.date(byAdding: .minute, value: 30, to: evening)!) ?? now, timezone: timezone),
                ],
                "summary": "模拟报告：傍晚高云层完整，霞光质量指数峰值为 \(String(format: "%.2f", best))，核心窗口约 60 分钟。",
                "cycle_time_utc": isoUTC(calendar.date(bySettingHour: 6, minute: 0, second: 0, of: now) ?? now),
                "duration_minutes": 58,
                "confidence_score": 0.82,
                "convective_risk": 0.08,
                "model_disagreement_score": 0.09,
                "dominant_burnable_pressure_family": "high",
                "dominant_burnable_cloud_base_agl_mean_m": 6400.0,
                "dominant_burnable_cloud_top_agl_mean_m": 8900.0,
                "cloud_genus_proxy": [
                    "label": genus,
                    "confidence": conf,
                    "compatible_genera": ["cirrus", "cirrostratus"],
                    "candidate_scores": ["cirrus": conf, "cirrostratus": 0.21, "altocumulus": 0.07],
                    "source": "pressure-phase-geometry-proxy",
                    "scoring_effect": false,
                ],
                "aerosol": ["aod550": aod, "source": "cams", "quality": "cams"],
                "anchor_local": bestTime,
                "quality_components": [
                    "burnable_cloud_coverage_effective": 0.71,
                    "cloud_material": material,
                    "brightness_color": brightness,
                    "incoming_aod_illumination_factor": 0.66,
                    "observer_aod_contrast_factor": 0.59,
                    "quality_aod550": aod,
                    "duration_minutes": 58,
                ],
                "cross_section": mockCrossSection,
                "steps": steps,
            ]
        }
        return [
            "kind": "point",
            "model_results": ["gfs": model(aod: 0.31, material: 0.54, brightness: 0.78, genus: "cirrus", conf: 0.72),
                              "ec": model(aod: 0.34, material: 0.51, brightness: 0.74, genus: "cirrostratus", conf: 0.64)],
            "model_errors": [:] as JSONObject,
        ]
    }

    private static let mockInventory: [JSONObject] = [
        ["source": "gfs", "total_file_count": 126, "total_storage_mb": 3401.5,
         "path": "/mock/data/gfs",
         "cycles": ["2026-11-12 06Z": ["files": 42], "2026-11-12 00Z": ["files": 42],
                    "2026-11-11 18Z": ["files": 42]] as JSONObject],
        ["source": "ecmwf", "total_file_count": 64, "total_storage_mb": 1210.2,
         "path": "/mock/data/ecmwf",
         "cycles": ["2026-11-12 06Z": ["files": 32], "2026-11-12 00Z": ["files": 32]] as JSONObject],
        ["source": "cams", "total_file_count": 12, "total_storage_mb": 260.8,
         "path": "/mock/data/cams",
         "cycles": ["2026-11-12 00Z": ["files": 12]] as JSONObject],
    ]

    private static let mockCategories: [JSONObject] = [
        ["category": "point-forecasts", "file_count": 4, "bytes": 184_320],
        ["category": "regional-forecasts", "file_count": 0, "bytes": 0],
        ["category": "map-frames", "file_count": 0, "bytes": 0],
        ["category": "reports", "file_count": 2, "bytes": 92_160],
        ["category": "satellite", "file_count": 0, "bytes": 0],
        ["category": "srtm", "file_count": 3, "bytes": 77_803_206],
    ]

    private static let mockTaskHistory: JSONObject = {
        let payload: JSONObject = ["kind": "point",
                                   "payload": ["name": "杭州", "lat": 30.2741, "lon": 120.1551,
                                               "target_date": "2026-11-09", "phenomenon": "dusk"]]
        let running: JSONObject = [
            "task_id": String(repeating: "b", count: 64), "method": "prepare", "status": "running",
            "created_at_utc": "2026-11-21T09:00:00.000Z", "updated_at_utc": "2026-11-21T09:01:00.000Z",
            "cancel_requested": false, "event_seq": 2, "progress": ["percent": 40.0],
            "retry_request": payload,
        ]
        let done: JSONObject = [
            "task_id": String(repeating: "a", count: 64), "method": "forecast", "status": "done",
            "created_at_utc": "2026-11-20T12:27:33.706Z", "updated_at_utc": "2026-11-20T12:27:42.600Z",
            "cancel_requested": false, "event_seq": 5, "retry_request": payload,
            "result_ref": ["snapshot": ["contract": "firesky-result-snapshot-v1",
                                        "sha256": String(repeating: "c", count: 64)]],
        ]
        let sync: JSONObject = [
            "task_id": String(repeating: "d", count: 64), "method": "data_sync", "status": "done",
            "created_at_utc": "2026-11-20T04:00:00.000Z", "updated_at_utc": "2026-11-20T04:01:00.000Z",
            "cancel_requested": false, "event_seq": 1,
        ]
        return ["schema": "firesky-task-history-v1", "automatic_restart": false,
                "total": 3, "issues": [], "tasks": [running, done, sync]]
    }()

    private static let mockTaskEvents: JSONObject = [
        "task_id": String(repeating: "a", count: 64),
        "events": [
            ["seq": 1, "at_utc": "2026-11-20T12:27:31.000Z", "event": "running", "data": [:]],
            ["seq": 2, "at_utc": "2026-11-20T12:27:32.000Z", "event": "progress",
             "data": ["percent": 30.0, "stage": "prepare", "message": "正在准备 GFS 与 CAMS 数据"]],
            ["seq": 3, "at_utc": "2026-11-20T12:27:33.000Z", "event": "progress",
             "data": ["percent": 70.0, "stage": "prepare", "message": "GFS 数据已就绪"]],
            ["seq": 4, "at_utc": "2026-11-20T12:27:34.000Z", "event": "progress",
             "data": ["percent": 90.0, "stage": "forecast", "message": "预测计算进行中"]],
            ["seq": 5, "at_utc": "2026-11-20T12:27:35.000Z", "event": "done", "data": [:]],
        ],
        "next_seq": 6, "truncated": false,
    ]

    private func mockTerrainDownload(id: String) async {
        let total = 4
        for index in 1...total {
            if index > 1 {
                try? await Task.sleep(nanoseconds: 400_000_000)
            }
            emitEvent(id: id, ["terrain": [
                "stage": "global", "candidates": total,
                "downloaded": index, "absent": 0, "failed": 0, "existing": 0,
                "received_bytes": index * 10_000_000,
                "percent": Double(index) * 100.0 / Double(total),
                "current_tile": "N30E11\(index)",
            ]])
        }
        emitResult(id: id, [
            "contract": "rust-terrain-download-v1",
            "status": "done", "mode": "global", "candidates": total,
            "existing": 0, "downloaded": total, "absent": 0, "failed": 0,
            "received_bytes": total * 10_000_000, "received_gb": Double(total) * 0.01,
            "stopped_reason": NSNull(),
        ])
    }
}
