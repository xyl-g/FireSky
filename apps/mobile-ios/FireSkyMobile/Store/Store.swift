import CoreLocation
import Combine
import SwiftUI
import UIKit

/// 页面枚举：v1 仅单点预测 / 数据与下载 / 设置。
/// 区域预测（用户裁定排除）与实况观测（延后 MN4 裁决）未迁移；
/// AppKit 菜单/快捷键（⌘1/⌘4/⌘,）降级为底部标签页导航。
enum Page: String, CaseIterable, Identifiable {
    case point, data, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .point: "单点预测"
        case .data: "数据与下载"
        case .settings: "设置"
        }
    }
    var symbol: String {
        switch self {
        case .point: "location.circle"
        case .data: "arrow.down.circle"
        case .settings: "gearshape"
        }
    }
}

/// 状态语义以桌面版 Store.swift（701 行）为参照源；区域/实况字段随页面一并排除，
/// AppKit 专属项（NSOpenPanel/NSPasteboard/进程内引擎二进制路径）按移动端语义替换。
@MainActor
final class Store: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    let engine = EngineClient(transportFactory: Store.makeTransport)
    @Published var page: Page = .point
    @Published var name = "杭州"
    @Published var query = ""
    @Published var lat = 30.2741 { didSet { metadataReady = false; result = nil } }
    @Published var lon = 120.1551 { didSet { metadataReady = false; result = nil } }
    @Published var elevation = 8.0 { didSet { result = nil } }
    @Published var timezone = "Asia/Shanghai" { didSet { result = nil } }
    @Published var metadataReady = true
    @Published var date = Date() { didSet { result = nil } }
    @Published var phenomenon = "dusk" { didSet { result = nil } }
    @Published var busy = false
    @Published var locating = false
    @Published var status = "正在连接引擎"
    @Published var error = ""
    @Published var online = false
    @Published var percent = 0.0
    @Published var showPreflight = false
    @Published var checking = false
    @Published var publicationsPending = false
    @Published var rows: [DataRow] = []
    @Published var ready = false
    @Published var session: String?
    @Published var inventory: [JSONObject] = []
    @Published var result: JSONObject? {
        didSet { resultVersion += 1 }
    }
    private(set) var resultVersion = 0
    @Published var selectedModel = "gfs"
    @Published var expandedInventory: Set<String> = []
    @Published var locationTab = "地图"
    @Published var dateTab = "今天"
    @Published var resultTab = "概览"
    @Published var editingCoordinates = false
    @Published var showMapPicker = false
    @Published var showDatePicker = false
    @Published var calendarMonth = Date()
    @Published var showCAMSReminder = false
    @Published var showCAMSTutorial = false
    @Published var camsConfiguration: JSONObject?
    @Published var camsKeyDraft = ""
    @Published var savingCAMS = false
    @Published var camsMessage = ""
    @Published var camsSaveFailed = false
    @Published var downloadProbeRows = DownloadProbeRow.initial
    @Published var probingDownloads = false
    @Published var cancellingDownloadProbe = false
    @Published var downloadProbeDate: Date?
    @Published var downloadProbeError = ""
    @Published var ecmwfSource = "ecmwf" {
        didSet {
            UserDefaults.standard.set(ecmwfSource, forKey: "ecmwfSource")
            result = nil
            downloadProbeDate = nil
            downloadProbeRows = DownloadProbeRow.initial
            downloadProbeError = ""
        }
    }
    var ecmwfSourceLabel: String {
        switch ecmwfSource {
        case "aws": return "AWS 欧洲镜像"
        case "google": return "Google Cloud 镜像"
        default: return "ECMWF 官方"
        }
    }
    /// 海拔来源三档（v1.1.1 §1）：auto / http / srtm，持久化在 UserDefaults，
    /// 所有 search/metadata 调用点透传 elevation_source。
    @Published var elevationSource: String
    @Published var favorites: [JSONObject] = []
    @Published var workspace: String
    @Published var appearance: String
    /// 引擎通道：ffi（engine-mobile C 契约，v1.1.0 起默认）或 mock（内置模拟，
    /// 设置页可切回，供无引擎库环境调试 UI）。
    @Published var engineTransport: String
    private var engineConnection: AnyCancellable?
    private let location = CLLocationManager()
    private var operation: Task<Void, Never>?
    private var publicationTask: Task<Void, Never>?
    private var downloadProbeTask: Task<Void, Never>?
    private var locationTimeout: Task<Void, Never>?
    private var generation = UUID()
    private var metadataGeneration = UUID()
    private(set) var requestKind: Page = .point
    private var requestPayload: JSONObject = [:]
    private var didCheckStartupCredentials = false
    /// 连接重入保护：scenePhase 与 .task 可能在启动时并发触发 connect，
    /// 二次 engine.start 会 stop 掉前一次仍在握手的通道。
    private var connecting = false

    private static func makeTransport() -> EngineTransport {
        let choice = UserDefaults.standard.string(forKey: "engineTransport") ?? "ffi"
        return choice == "mock" ? MockTransport() : FSMobileTransport()
    }

    override init() {
        let defaults = UserDefaults.standard
        let dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FireSky/Data", isDirectory: true)
        // iOS 厚客户端：缓存根/数据目录固定在应用沙箱（桌面版可选任意目录的
        // NSOpenPanel 语义降级为固定沙箱路径 + 文本框微调）。
        try? FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        workspace = dataDirectory.path
        appearance = defaults.string(forKey: "appearance") ?? "system"
        engineTransport = defaults.string(forKey: "engineTransport") ?? "ffi"
        ecmwfSource = defaults.string(forKey: "ecmwfSource") ?? "ecmwf"
        elevationSource = ElevationSourceSetting.load(defaults)
        super.init()
        engineConnection = engine.$connected.sink { [weak self] value in self?.online = value }
        favorites = defaults.array(forKey: "favorites") as? [JSONObject] ?? []
        location.delegate = self
        location.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func switchEngineTransport(_ value: String) {
        guard value != engineTransport, !busy, !probingDownloads else { return }
        engineTransport = value
        UserDefaults.standard.set(value, forKey: "engineTransport")
        status = "正在连接引擎"
        Task { await connect() }
    }

    /// 海拔来源切换：持久化后对下一次 search/metadata 生效（不清空已有结果）。
    func setElevationSource(_ value: String) {
        elevationSource = ElevationSourceSetting.save(value)
    }

    func connect() async {
        guard !busy, !probingDownloads, !connecting else { return }
        connecting = true
        defer { connecting = false }
        online = false
        downloadProbeRows = DownloadProbeRow.initial
        downloadProbeDate = nil
        downloadProbeError = ""
        do {
            try engine.start(workspace: workspace)
            let hello = try await engine.call("hello")
            guard hello["protocol"] as? Int == 1 else {
                throw EngineError(message: "引擎协议版本不匹配。")
            }
            online = true
            engine.connected = true
            camsConfiguration = hello["cams_credentials"] as? JSONObject
            if !didCheckStartupCredentials, let camsConfiguration {
                didCheckStartupCredentials = true
                showCAMSReminder = camsConfiguration["configured"] as? Bool != true
            }
            status = "引擎已连接"
            error = ""
            UserDefaults.standard.set(workspace, forKey: "workspace")
            // result_restore 链（参照 macOS Store.loadTaskHistory/restoreTaskResult）：
            // hello 能力含 task_history/result_restore 时自动恢复最近一次可恢复的
            // 单点 forecast。Mock 通道 hello 无 capabilities → 跳过，不影响 mock 链路。
            let capabilities = Set(hello["capabilities"] as? [String] ?? [])
            if capabilities.isSuperset(of: ["task_history", "result_restore"]) {
                await restoreLatestForecastResult()
            }
            if page == .data { await loadInventory() }
        } catch { self.error = error.localizedDescription; status = "引擎未连接" }
    }

    /// 冷启动/kill-restart 后恢复最近一次已完成的单点预测结果。
    /// 协议与桌面逐帧一致：task_history(limit 20，created_at_utc 降序) →
    /// 首个 done 且带 firesky-result-snapshot-v1 引用的 point forecast →
    /// result_restore(task_id) → 应用 result 与 location/date/phenomenon 参数。
    /// 守 busy/generation/session：不得覆盖进行中的会话或用户已产生的新结果。
    private func restoreLatestForecastResult() async {
        guard online, !busy, !showPreflight, session == nil, result == nil else { return }
        let revision = generation
        do {
            let history = try await engine.call("task_history", ["limit": 20])
            guard generation == revision, !busy, !showPreflight, session == nil, result == nil,
                  history["schema"] as? String == "firesky-task-history-v1",
                  let tasks = history["tasks"] as? [JSONObject] else { return }
            guard let item = tasks.first(where: { task in
                guard task["method"] as? String == "forecast",
                      task["status"] as? String == "done",
                      let taskID = task["task_id"] as? String, !taskID.isEmpty,
                      let retry = task["retry_request"] as? JSONObject,
                      retry["kind"] as? String == "point",
                      retry["payload"] is JSONObject,
                      let reference = task["result_ref"] as? JSONObject,
                      let snapshot = reference["snapshot"] as? JSONObject,
                      snapshot["contract"] as? String == "firesky-result-snapshot-v1",
                      snapshot["sha256"] is String else { return false }
                return true
            }), let taskID = item["task_id"] as? String else { return }
            let response = try await engine.call("result_restore", ["task_id": taskID])
            guard generation == revision, !busy, !showPreflight, session == nil, result == nil,
                  response["model_results"] is JSONObject else { return }
            // 先落参数再落 result：lat/lon/date/phenomenon 的 didSet 会清 result。
            if let payload = (item["retry_request"] as? JSONObject)?["payload"] as? JSONObject {
                applyRestoredPayload(payload)
            }
            result = response
            // 与 confirm() 一致：gfs 缺失回退 ec。
            selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
            status = "已恢复历史结果"
            error = ""
        } catch {
            // 恢复失败不打断连接（引擎仍在线，可正常发起新预测）。
            guard generation == revision, !Task.isCancelled else { return }
            self.error = "历史结果恢复失败：\(error.localizedDescription)"
        }
    }

    /// 恢复 retry_request.payload 中的观测参数（macOS applyRetryPayload 的单点子集）。
    private func applyRestoredPayload(_ payload: JSONObject) {
        if let value = payload["name"] as? String { name = value }
        if let value = (payload["lat"] as? NSNumber)?.doubleValue { lat = value }
        if let value = (payload["lon"] as? NSNumber)?.doubleValue { lon = value }
        if let value = (payload["elevation_m"] as? NSNumber)?.doubleValue { elevation = value }
        if let value = payload["timezone_name"] as? String { timezone = value }
        if let value = payload["target_date"] as? String,
           let day = ObservationDate.date(value, timezone: timezone) {
            date = day
        }
        if let value = payload["phenomenon"] as? String { phenomenon = value }
        if let value = payload["ecmwf_source"] as? String { ecmwfSource = value }
        metadataReady = true
    }

    func search() {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        busy = true
        status = "正在查找位置"
        let source = elevationSource
        operation = Task {
            defer { busy = false }
            do {
                let point = try await engine.call(
                    "search", ElevationSourceSetting.searchParams(query: text, source: source))
                try Task.checkCancellation()
                applyLocation(point)
                query = ""
            } catch { fail(error) }
        }
    }

    func selectPoint(lat: Double, lon: Double) {
        guard !busy else { return }
        self.lat = lat
        self.lon = lon
        self.name = "地图选点"
        metadataReady = false
        result = nil
        lookupMetadata()
    }

    func lookupMetadata() {
        let revision = UUID()
        metadataGeneration = revision
        let a = lat, b = lon
        let source = elevationSource
        metadataReady = false
        status = "正在获取海拔与时区"
        Task {
            do {
                let point = try await engine.call(
                    "metadata", ElevationSourceSetting.metadataParams(lat: a, lon: b, source: source))
                guard metadataGeneration == revision, a == lat, b == lon else { return }
                elevation = (point["elevation_m"] as? NSNumber)?.doubleValue ?? 0
                timezone = point["timezone_name"] as? String ?? timezone
                metadataReady = true
                status = "位置已就绪"
            } catch {
                guard metadataGeneration == revision else { return }
                self.error = error.localizedDescription
                status = "海拔未获取，可手动填写"
            }
        }
    }

    func applyLocation(_ point: JSONObject) {
        lat = (point["lat"] as? NSNumber)?.doubleValue ?? lat
        lon = (point["lon"] as? NSNumber)?.doubleValue ?? lon
        name = point["name"] as? String ?? "当前位置"
        timezone = point["timezone_name"] as? String ?? timezone
        elevation = (point["elevation_m"] as? NSNumber)?.doubleValue ?? elevation
        metadataReady = true
        result = nil
        status = "位置已就绪"
    }

    func locate() {
        guard !locating, !busy else { return }
        locating = true
        status = "正在获取当前位置"
        locationTimeout?.cancel()
        locationTimeout = Task {
            try? await Task.sleep(for: .seconds(20))
            guard !Task.isCancelled, locating else { return }
            locating = false
            location.stopUpdatingLocation()
            error = "定位超时，请检查系统定位权限，或在地图上选点。"
        }
        switch location.authorizationStatus {
        case .denied, .restricted:
            locating = false
            error = "定位权限未开启，请在系统设置中允许 FireSky 使用位置。"
        case .notDetermined: location.requestWhenInUseAuthorization()
        default: location.requestLocation()
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        guard locating else { return }
        if manager.authorizationStatus == .authorizedAlways {
            manager.requestLocation()
        } else if manager.authorizationStatus == .denied || manager.authorizationStatus == .restricted {
            locating = false
            error = "定位未获授权，可继续在地图上选点。"
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let point = locations.last, locating else { return }
        guard point.horizontalAccuracy >= 0, point.horizontalAccuracy <= 5000,
              abs(point.timestamp.timeIntervalSinceNow) <= 120 else {
            error = "定位精度不足或位置已过期，请重试。"
            locating = false
            return
        }
        locating = false
        locationTimeout?.cancel()
        selectPoint(lat: point.coordinate.latitude, lon: point.coordinate.longitude)
        name = "当前位置"
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        locating = false
        locationTimeout?.cancel()
        self.error = error.localizedDescription
    }

    var targetDateLabel: String { ObservationDate.label(date, timezone: timezone) }

    func payload() -> JSONObject {
        [
            "name": name, "lat": lat, "lon": lon, "elevation_m": elevation,
            "timezone_name": timezone, "target_date": targetDateLabel,
            "phenomenon": phenomenon, "model_source": "both", "workers": 1,
            "forecast_mode": "model", "satellite_source": "off",
            "ecmwf_source": ecmwfSource,
        ]
    }

    func beginCheck() {
        guard !busy, online else { return }
        guard metadataReady else {
            error = "请先获取海拔与时区，或手动确认位置数据。"
            return
        }
        generation = UUID()
        let revision = generation
        requestKind = .point
        requestPayload = payload()
        session = nil
        rows = []
        ready = false
        error = ""
        showPreflight = true
        check(revision: revision)
    }

    func refreshCheck() {
        guard !busy else { return }
        dismissToken()
        generation = UUID()
        check(revision: generation)
    }

    private func check(revision: UUID) {
        busy = true
        checking = true
        percent = 0
        status = "正在检查本地数据"
        operation = Task {
            defer { if generation == revision { busy = false; checking = false } }
            do {
                let response = try await engine.call("check", [
                    "kind": requestKind.rawValue, "payload": requestPayload,
                ])
                guard generation == revision, showPreflight else {
                    if let token = response["session"] as? String {
                        _ = try? await engine.call("dismiss", ["session": token])
                    }
                    return
                }
                session = response["session"] as? String
                applyReport(response)
                verifyPublications(revision: revision)
            } catch { if generation == revision { fail(error) } }
        }
    }

    private func applyReport(_ response: JSONObject) {
        let report = response["report"] as? JSONObject ?? [:]
        ready = report["ready"] as? Bool ?? false
        rows = (report["datasets"] as? [JSONObject] ?? []).map {
            DataRow(source: $0["source"] as? String ?? "",
                    ready: $0["ready"] as? Bool ?? false,
                    old: ($0["verified"] as? Bool == true) && !($0["is_latest"] as? Bool ?? false),
                    verified: $0["verified"] as? Bool ?? false,
                    missing: $0["missing_count"] as? Int ?? 0)
        }
        status = ready ? "本次数据已下载" : "缺少 " + rows.filter { !$0.ready }.map(\.title).joined(separator: "、")
    }

    private func verifyPublications(revision: UUID) {
        guard let session else { return }
        publicationsPending = true
        publicationTask = Task {
            defer { if generation == revision { publicationsPending = false } }
            do {
                let response = try await engine.call("publications", ["session": session])
                guard generation == revision, showPreflight, !busy else { return }
                applyReport(response)
            } catch { /* A publication outage must not invalidate local availability. */ }
        }
    }

    func prepare() {
        guard let session, !busy else { return }
        publicationTask?.cancel()
        busy = true
        checking = true
        ready = false
        percent = 0
        error = ""
        status = "正在准备数据"
        let revision = generation
        operation = Task {
            defer { if generation == revision { busy = false; checking = false } }
            do {
                let response = try await engine.call("prepare", ["session": session], progress: updateProgress)
                try Task.checkCancellation()
                guard generation == revision, showPreflight else { return }
                applyReport(response)
                // Deliberately stay in the sheet until the user confirms.
            } catch { if generation == revision { fail(error) } }
        }
    }

    func confirm() {
        guard let session, ready, !busy else { return }
        publicationTask?.cancel()
        showPreflight = false
        busy = true
        checking = false
        error = ""
        percent = 0
        status = "正在预测"
        let revision = generation
        operation = Task {
            defer { if generation == revision { busy = false } }
            do {
                let response = try await engine.call("forecast", [
                    "session": session, "confirmed": true,
                ], progress: updateProgress)
                try Task.checkCancellation()
                guard generation == revision else { return }
                result = response
                selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                self.session = nil
                status = "预测完成"
            } catch { if generation == revision { self.session = nil; ready = false; fail(error) } }
        }
    }

    func updateProgress(_ event: JSONObject) {
        percent = min(100, max(0, (event["percent"] as? NSNumber)?.doubleValue ?? 0))
        let source = event["source"] as? String ?? ""
        let message = event["message"] as? String ?? ""
        status = message.isEmpty
            ? (checking ? "正在准备 \(source) 数据" : "正在计算 \(source)")
            : message
    }

    private func dismissToken() {
        if let session {
            Task { _ = try? await engine.call("dismiss", ["session": session]) }
        }
        session = nil
    }

    func closePreflight() {
        generation = UUID()
        publicationTask?.cancel()
        operation?.cancel()
        dismissToken()
        showPreflight = false
        checking = false
        publicationsPending = false
        busy = false
        ready = false
        error = ""
        status = "已取消"
    }

    func cancel() {
        operation?.cancel()
        status = "正在取消，等待当前阶段结束"
    }

    func loadInventory() async {
        guard online else { return }
        do {
            let response = try await engine.call("inventory")
            inventory = response["datasets"] as? [JSONObject] ?? []
        } catch { fail(error) }
    }

    func fail(_ error: Error) {
        self.error = error.localizedDescription
        status = Task.isCancelled ? "已取消" : "操作未完成"
    }

    var modelResult: JSONObject? {
        guard let result else { return nil }
        if let models = result["model_results"] as? JSONObject {
            return models[selectedModel] as? JSONObject
        }
        return result
    }

    var pointComparison: ForecastComparison {
        ForecastComparison(result: requestKind == .point ? result : nil)
    }

    func saveAppearance() { UserDefaults.standard.set(appearance, forKey: "appearance") }

    var camsConfigured: Bool { camsConfiguration?["configured"] as? Bool == true }
    var canSaveCAMS: Bool { camsConfiguration?["can_save"] as? Bool == true }
    var camsConfigurationDescription: String {
        guard let camsConfiguration else { return "正在检查本地配置" }
        switch camsConfiguration["state"] as? String {
        case "configured": return "已检测到 Key，下载时验证授权。"
        case "wrong_endpoint": return "接口地址不是 Copernicus ADS，请重新配置。"
        case "tls_disabled": return "配置关闭了 TLS 验证，请重新保存以启用安全连接。"
        case "unreadable": return "配置文件无法读取或格式有误，请修正后重新检查。"
        default: return "尚未配置 CAMS Key，本地数据缺失时无法下载。"
        }
    }

    func openCAMSSetup() {
        page = .settings
        showCAMSTutorial = true
    }

    func refreshCAMSConfiguration() async {
        guard online, !savingCAMS else { return }
        do {
            camsConfiguration = try await engine.call("cams_credentials")
        } catch {
            camsMessage = "无法检查配置，请确认本地引擎已连接。"
            camsSaveFailed = true
        }
    }

    func saveCAMSKey() {
        guard online, !busy, !savingCAMS, !probingDownloads, canSaveCAMS else { return }
        savingCAMS = true
        camsMessage = ""
        let key = camsKeyDraft
        camsKeyDraft = ""
        Task {
            defer { savingCAMS = false }
            do {
                camsConfiguration = try await engine.call("save_cams_key", ["key": key])
                if let index = downloadProbeRows.firstIndex(where: { $0.source == "cams" }) {
                    downloadProbeRows[index] = DownloadProbeRow.initial[index]
                }
                downloadProbeDate = nil
                camsMessage = "已保存到本机配置文件。"
                camsSaveFailed = false
            } catch {
                camsMessage = error.localizedDescription
                camsSaveFailed = true
            }
        }
    }

    func probeDownloads() {
        guard online, !busy, !savingCAMS, !probingDownloads else { return }
        probingDownloads = true
        cancellingDownloadProbe = false
        downloadProbeDate = nil
        downloadProbeError = ""
        downloadProbeRows = DownloadProbeRow.initial
        downloadProbeTask = Task { [self] in
            defer {
                probingDownloads = false
                cancellingDownloadProbe = false
                downloadProbeTask = nil
            }
            do {
                let response = try await engine.call("network_probe", ["ecmwf_source": ecmwfSource],
                                                     progress: { [weak self] event in
                    guard let self, !self.cancellingDownloadProbe,
                          let json = event["probe"] as? JSONObject,
                          let row = DownloadProbeRow(json),
                          let index = self.downloadProbeRows.firstIndex(where: { $0.id == row.id }) else { return }
                    self.downloadProbeRows[index] = row
                })
                try Task.checkCancellation()
                let rows = (response["datasets"] as? [JSONObject] ?? []).compactMap(DownloadProbeRow.init)
                guard Set(rows.map(\.source)) == Set(["gfs", "ec", "cams"]), rows.count == 3 else {
                    throw EngineError(message: "探测结果不完整，请重新探测。")
                }
                downloadProbeRows = rows
                downloadProbeDate = Date()
            } catch {
                let cancelled = Task.isCancelled
                for index in downloadProbeRows.indices where downloadProbeRows[index].active
                    || downloadProbeRows[index].state == "waiting" {
                    downloadProbeRows[index].state = cancelled ? "cancelled" : "error"
                    downloadProbeRows[index].detail = cancelled ? "已停止，尚未验证下载。" : "探测中断，尚未验证下载。"
                }
                downloadProbeDate = Date()
                downloadProbeError = cancelled ? "探测已取消；已完成的结果保留。" : error.localizedDescription
            }
        }
    }

    func cancelDownloadProbe() {
        guard probingDownloads else { return }
        cancellingDownloadProbe = true
        downloadProbeTask?.cancel()
    }

    /// NSPasteboard 语义 → UIPasteboard。
    func copyDownloadProbeReport() {
        let timestamp = downloadProbeDate?.formatted(date: .numeric, time: .standard) ?? "未完成"
        let report = ["FireSky 数据源下载探测 · \(timestamp)"] + downloadProbeRows.map {
            "\($0.title) [\($0.host)] · \($0.label)\n\($0.detail)\n\($0.measurement)"
        }
        UIPasteboard.general.string = report.joined(separator: "\n\n")
    }

    func chooseDate(_ tab: String) {
        dateTab = tab
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        if tab == "今天" { date = Date() }
        if tab == "明天" { date = calendar.date(byAdding: .day, value: 1, to: Date()) ?? Date() }
    }

    func starLocation() {
        guard metadataReady else { error = "请先确认海拔与时区。"; return }
        favorites.removeAll { ($0["lat"] as? Double) == lat && ($0["lon"] as? Double) == lon }
        favorites.append(["name": name, "lat": lat, "lon": lon,
                          "elevation_m": elevation, "timezone_name": timezone])
        UserDefaults.standard.set(favorites, forKey: "favorites")
    }

    /// AppKit applicationWillTerminate → 移动端在退到后台时挂起即可，
    /// 进程回收由系统负责；前台返回时若已断线由 App 壳自动重连。
    func shutdown() {
        downloadProbeTask?.cancel()
        locationTimeout?.cancel()
        operation?.cancel()
        publicationTask?.cancel()
        engine.stop()
    }
}
