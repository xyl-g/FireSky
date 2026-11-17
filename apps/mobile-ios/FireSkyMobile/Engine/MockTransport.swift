import Foundation

/// MockTransport：进程内假引擎，让 UI 在 Rust 引擎库（engine-mobile）可用前
/// 可编译、可运行。完全走 EngineTransport 的帧语义——JSON 行进、JSON 行出，
/// 模拟 hello → check → prepare → forecast 全链路、progress 帧、取消与
/// CAMS 配置 / 清单 / 下载探测，供模拟器冒烟与单元测试使用。
@MainActor
final class MockTransport: EngineTransport {
    var onFrame: ((Data) -> Void)?
    private var cancelled: Set<String> = []
    private var camsConfigured = false
    private var lastReport: JSONObject = [:]

    /// nonisolated：允许作为 EngineClient 的非隔离默认工厂值使用。
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
        data.append(10) // '\n'：与桌面 JSON-lines 协议一致的帧结尾
        onFrame?(data)
    }

    private func emitProgress(id: String, percent: Double, source: String, message: String) {
        emit(["id": id, "type": "progress",
              "event": ["percent": percent, "source": source, "message": message]])
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
            ])
        case "search":
            await pause(400)
            emitResult(id: id, Self.searchResult(query: params["query"] as? String ?? ""))
        case "metadata":
            await pause(300)
            emitResult(id: id, ["elevation_m": 12.0, "timezone_name": "Asia/Shanghai"])
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
            emitResult(id: id, ["datasets": Self.mockInventory])
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

    /// 一份结构与桌面 forecast 结果一致的完整单点报告（GFS/EC 双模型）。
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
            // 平滑的霞光质量曲线（18:10 前后峰值 1.9 附近）。
            let quality = 1.9 * exp(-pow(Double(minute - 30) / 34.0, 2)) + 0.12
            let solar = 4.2 - Double(minute) * 0.06
            if quality > best { best = quality; bestTime = localStamp(time, timezone: timezone) }
            steps.append([
                "valid_time_utc": isoUTC(time),
                "valid_time_local": localStamp(time, timezone: timezone),
                "instant_quality_index": (quality * 1000).rounded() / 1000,
                "instant_quality_level": quality >= 1.5 ? "值得一看" : "一般",
                "solar_elevation_deg": (solar * 100).rounded() / 100,
                "valid": true,
                "physically_burnable": quality >= 1.0,
            ])
        }
        func model(aod: Double, material: Double, brightness: Double) -> JSONObject {
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
                "summary": "模拟报告：傍晚云层完整，霞光质量指数峰值为 \(String(format: "%.2f", best))，核心窗口约 60 分钟。",
                "cycle_time_utc": isoUTC(calendar.date(bySettingHour: 6, minute: 0, second: 0, of: now) ?? now),
                "duration_minutes": 58,
                "confidence_score": 0.82,
                "model_disagreement_score": 0.09,
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
                "steps": steps,
            ]
        }
        return [
            "kind": "point",
            "model_results": ["gfs": model(aod: 0.31, material: 0.54, brightness: 0.78),
                              "ec": model(aod: 0.34, material: 0.51, brightness: 0.74)],
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
}
