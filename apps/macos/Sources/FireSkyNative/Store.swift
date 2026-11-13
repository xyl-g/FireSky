import AppKit
import CoreLocation
import Combine
import SwiftUI

enum Page: String, CaseIterable, Identifiable {
    case point, regional, nowcast, data, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .point: "单点预测"
        case .regional: "区域预测"
        case .nowcast: "实况观测"
        case .data: "数据与下载"
        case .settings: "设置"
        }
    }
    var symbol: String {
        switch self {
        case .point: "location.circle"
        case .regional: "map"
        case .nowcast: "scope"
        case .data: "arrow.down.circle"
        case .settings: "gearshape"
        }
    }
}

@MainActor
final class Store: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    let engine = EngineClient()
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
    @Published var west = 119.5 { didSet { result = nil } }
    @Published var east = 120.5 { didSet { result = nil } }
    @Published var south = 29.75 { didSet { result = nil } }
    @Published var north = 30.75 { didSet { result = nil } }
    @Published var busy = false
    @Published var locating = false
    @Published var status = "正在连接引擎"
    @Published var error = ""
    @Published var online = false
    @Published var percent = 0.0
    @Published var showPreflight = false
    @Published var showCycleAgeConfirm = false
    @Published var checking = false
    @Published var publicationsPending = false
    @Published var rows: [DataRow] = []
    @Published var ready = false
    @Published var session: String?
    @Published var inventory: [JSONObject] = []
    @Published var result: JSONObject? {
        didSet {
            resultVersion += 1
            regionalCache = RegionalForecast(result: result)
            selectedRegionalCoordinate = nil
            regionalProfileTask?.cancel()
            regionalProfileTask = nil
            regionalProfile = nil
            regionalProfileModel = ""
            regionalProfileCoordinate = nil
            regionalProfileState = "idle"
            regionalProfileError = ""
            showRegionalProfileFigure = false
            frozenProfileTask?.cancel()
            frozenProfileTask = nil
            frozenProfile = nil
            frozenProfileModel = ""
            frozenProfileTime = ""
            frozenProfileState = "idle"
            frozenProfileError = ""
            nowcastProfileStep = ""
            pointSelectedTime = nil
        }
    }
    /// Shared local selected hour for the point trend chart, ledger and timeline strip.
    /// Deliberately does not drive any map view (plan: views stay independent).
    @Published var pointSelectedTime: String?
    private(set) var resultVersion = 0
    private var regionalCache = RegionalForecast(result: nil)
    @Published var resultKind: Page = .point
    @Published var selectedModel = "gfs"
    @Published var satelliteMap = false
    @Published var expandedInventory: Set<String> = []
    @Published var locationTab = "地图"
    @Published var dateTab = "今天"
    @Published var resultTab = "概览"
    @Published var regionalQuery = ""
    @Published var regionalInspectorMode = "grid"
    @Published var editingRegionOnMap = false
    @Published var showRegionBounds = false
    @Published var showRegionalInspector = true
    @Published var showRegionalGrid = false
    /// 采样格点叠加层开关（regional-6）：复用 points[]，上限截断，与网格线开关并存。
    @Published var showRegionalSamples = false
    @Published var regionalDisplay = "gfs" {
        didSet { if regionalDisplay != "compare" { selectedModel = regionalDisplay } }
    }
    @Published var regionalCamera: RegionalCamera?
    @Published var selectedRegionalCoordinate: GridCoordinate?
    @Published var regionalFocusVersion = 0
    @Published var regionalFitVersion = 0
    @Published var regionalProfile: JSONObject?
    @Published var regionalProfileModel = ""
    @Published var regionalProfileCoordinate: GridCoordinate?
    @Published var regionalProfileState = "idle"
    @Published var regionalProfileError = ""
    /// 区域网格剖面图层弹窗开关（regional-7）。
    @Published var showRegionalProfileFigure = false
    @Published var frozenProfile: JSONObject?
    @Published var frozenProfileModel = ""
    @Published var frozenProfileTime = ""
    @Published var frozenProfileState = "idle"
    @Published var frozenProfileError = ""
    @Published var nowcastProfileStep = ""
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
    @Published var favorites: [JSONObject] = []
    @Published var regionDefinitions: [JSONObject] = []
    @Published var selectedRegions: Set<String> = ["east-asia"] { didSet { result = nil } }
    @Published var customRegion = false { didSet { result = nil } }
    @Published var workspace: String
    @Published var engineBinary: String
    @Published var appearance: String
    @Published var retentionGFS = ""
    @Published var retentionECMWF = ""
    @Published var retentionCAMS = ""
    @Published var retentionSatelliteDays = ""
    @Published var retentionReportDays = ""
    @Published var retentionMapDays = ""
    @Published var retentionConfigured = false
    @Published var savingRetention = false
    @Published var retentionMessage = ""
    @Published var retentionSaveFailed = false
    /// 后台数据同步（设计 11 节）：UI 只镜像引擎设置文件（事实来源在引擎），
    /// 不经 UserDefaults 持久化；默认全关。
    @Published var dataSyncEnabled = false
    @Published var dataSyncCAMS = false
    @Published var dataSyncGFS = false
    @Published var dataSyncEC = false
    @Published var dataSyncInterval = ""
    @Published var dataSyncAnchor = ""
    @Published var dataSyncLeadHorizon = ""
    @Published var dataSyncConfigured = false
    @Published var dataSyncRetentionConfigured = false
    @Published var dataSyncLoaded = false
    @Published var savingDataSync = false
    @Published var dataSyncMessage = ""
    @Published var dataSyncSaveFailed = false
    @Published var dataSyncRunning = false
    /// 每源 last_cycle/last_result/熔断状态（`rust-data-sync-state-v1` 镜像）。
    @Published var dataSyncState: JSONObject = [:]
    /// 同步进行中的每源实时进度（source → {percent, message}），由进度事件喂入。
    @Published var dataSyncProgress: JSONObject = [:]
    /// 后台同步助手（launchd LaunchAgent）安装/运行状态与上次运行结果。
    @Published var helperAvailable = false
    @Published var helperInstalled = false
    @Published var helperLoaded = false
    @Published var helperBusy = false
    @Published var helperMessage = ""
    @Published var helperMessageFailed = false
    @Published var helperStatus: JSONObject = [:]
    @Published var taskHistory: [NativeTaskHistoryItem] = []
    @Published var taskHistoryTotal = 0
    @Published var taskHistoryIssues = 0
    @Published var taskHistoryLoaded = false
    @Published var taskHistoryLoading = false
    @Published var taskHistoryError = ""
    @Published var selectedTaskHistoryID: String?
    @Published var taskHistoryEvents: [NativeTaskEvent] = []
    @Published var taskHistoryEventsLoading = false
    @Published var taskHistoryEventsError = ""
    @Published var taskHistoryEventsTruncated = false
    @Published var forgettingTaskHistoryID: String?
    @Published var restoringTaskHistoryID: String?
    @Published var pendingForgetTaskID: String?
    private var engineConnection: AnyCancellable?
    private let location = CLLocationManager()
    private var operation: Task<Void, Never>?
    private var publicationTask: Task<Void, Never>?
    private var regionalProfileTask: Task<Void, Never>?
    private var frozenProfileTask: Task<Void, Never>?
    private var downloadProbeTask: Task<Void, Never>?
    private var taskHistoryTask: Task<Void, Never>?
    private var taskHistoryEventsTask: Task<Void, Never>?
    /// 60 秒心跳（设计 5.1/11.3）：唯一节拍源，调度决策全部在引擎。
    private var syncTickTask: Task<Void, Never>?
    private var locationTimeout: Task<Void, Never>?
    private var generation = UUID()
    private var metadataGeneration = UUID()
    private(set) var requestKind: Page = .point
    private var requestPayload: JSONObject = [:]
    private var didCheckStartupCredentials = false

    override init() {
        let env = ProcessInfo.processInfo.environment
        let defaults = UserDefaults.standard
        let bundledEngine = Bundle.main.resourceURL?.appendingPathComponent("firesky-engine").path
        let isBundled = bundledEngine.map { FileManager.default.isExecutableFile(atPath: $0) } ?? false
        let dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FireSky/Data", isDirectory: true)
        let defaultWorkspace = isBundled ? dataDirectory.path : FileManager.default.currentDirectoryPath
        let explicitWorkspace = env["FIRESKY_WORKSPACE"]
        let savedWorkspace = defaults.string(forKey: "workspace")
        let bundleWorkspace = Bundle.main.object(forInfoDictionaryKey: "FireSkyWorkspace") as? String
        let directoryExists: (String) -> Bool = { path in
            var isDirectory: ObjCBool = false
            return FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory)
                && isDirectory.boolValue
        }
        // An explicitly supplied workspace is a developer/operator choice and
        // should fail visibly when invalid. A stale persisted workspace is
        // recoverable, so bundled apps fall back to their private data root.
        let root: String
        if let explicitWorkspace {
            root = explicitWorkspace
        } else if let savedWorkspace, directoryExists(savedWorkspace) {
            root = savedWorkspace
        } else if let bundleWorkspace, directoryExists(bundleWorkspace) {
            root = bundleWorkspace
        } else {
            root = defaultWorkspace
        }
        if isBundled && !directoryExists(root) {
            try? FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        }
        workspace = root
        engineBinary = env["FIRESKY_RUST_ENGINE_BIN"] ?? defaults.string(forKey: "engineBinary") ?? ""
        appearance = env["FIRESKY_NATIVE_APPEARANCE"] ?? defaults.string(forKey: "appearance") ?? "system"
        ecmwfSource = defaults.string(forKey: "ecmwfSource") ?? "ecmwf"
        super.init()
        engineConnection = engine.$connected.sink { [weak self] value in self?.online = value }
        favorites = defaults.array(forKey: "favorites") as? [JSONObject] ?? []
        let legacy = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".firesky-desktop")
        if favorites.isEmpty,
           let bytes = try? Data(contentsOf: legacy.appendingPathComponent("locations.json")),
           let saved = try? JSONSerialization.jsonObject(with: bytes) as? [JSONObject] {
            favorites = saved
        }
        if let bytes = try? Data(contentsOf: legacy.appendingPathComponent("config.json")),
           let config = try? JSONSerialization.jsonObject(with: bytes) as? JSONObject,
           let point = config["current_spot"] as? JSONObject {
            applyLocation(point)
            metadataReady = (point["elevation_m"] as? Double ?? -1) != -1
        }
        location.delegate = self
        location.desiredAccuracy = kCLLocationAccuracyHundredMeters
        if let page = env["FIRESKY_NATIVE_PAGE"].flatMap(Page.init(rawValue:)) {
            self.page = page
        }
    }

    func connect() async {
        guard !busy, !probingDownloads else { return }
        online = false
        downloadProbeRows = DownloadProbeRow.initial
        downloadProbeDate = nil
        downloadProbeError = ""
        do {
            try engine.start(workspace: workspace, engineBinary: engineBinary)
            let hello = try await engine.call("hello")
            guard hello["protocol"] as? Int == 1 else {
                throw EngineError(message: "引擎协议版本不匹配。")
            }
            let capabilities = Set(hello["capabilities"] as? [String] ?? [])
            guard capabilities.isSuperset(of: ["profile_query", "task_history", "task_events", "result_restore"]) else {
                throw EngineError(message: "Rust 引擎缺少任务历史或结果恢复能力。")
            }
            online = true
            engine.connected = true
            regionDefinitions = hello["regions"] as? [JSONObject] ?? []
            camsConfiguration = hello["cams_credentials"] as? JSONObject
            if !didCheckStartupCredentials, let camsConfiguration {
                didCheckStartupCredentials = true
                showCAMSReminder = camsConfiguration["configured"] as? Bool != true
            }
            await loadRetentionSettings()
            await loadDataSyncSettings()
            startSyncTick()
            refreshHelperStatus()
            status = "引擎已连接"
            UserDefaults.standard.set(workspace, forKey: "workspace")
            UserDefaults.standard.set(engineBinary, forKey: "engineBinary")
            if page == .data {
                await loadInventory()
                loadTaskHistory()
            }
        } catch { self.error = error.localizedDescription; status = "引擎未连接" }
    }

    func search() {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        busy = true
        status = "正在查找位置"
        operation = Task {
            defer { busy = false }
            do {
                let point = try await engine.call("search", ["query": text])
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
        metadataReady = false
        status = "正在获取海拔与时区"
        Task {
            do {
                let point = try await engine.call("metadata", ["lat": a, "lon": b])
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
        if manager.authorizationStatus == .authorized {
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
        var value: JSONObject = [
            "name": name, "lat": lat, "lon": lon, "elevation_m": elevation,
            "timezone_name": timezone, "target_date": targetDateLabel,
            "phenomenon": phenomenon, "model_source": "both", "workers": 1,
            "forecast_mode": "model", "satellite_source": "off",
            "ecmwf_source": ecmwfSource,
        ]
        if page == .nowcast {
            value["model_source"] = "satellite"
            value["forecast_mode"] = "nowcast"
            value["satellite_source"] = "auto"
            value["radius_km"] = 900.0
        }
        if page == .regional {
            value["regions"] = selectedRegions.sorted() + (customRegion ? ["custom"] : [])
            value["custom_bounds"] = ["west": west, "east": east, "south": south, "north": north]
            value["spacing_deg"] = 0.25
        }
        return value
    }

    func beginCheck() {
        guard !busy, online else { return }
        guard page == .regional || metadataReady else {
            error = "请先获取海拔与时区，或手动确认位置数据。"
            return
        }
        if page == .regional && customRegion
            && (!west.isFinite || !east.isFinite || !south.isFinite || !north.isFinite
                || west < -180 || west > 180 || east < -180 || east > 180
                || south < -90 || south > 90 || north < -90 || north > 90
                || east == west || north <= south) {
            error = "区域边界无效；东界小于西界表示跨越 180°，东界不能等于西界。"
            return
        }
        if page == .regional && selectedRegions.isEmpty && !customRegion {
            error = "请至少选择一个区域。"
            return
        }
        generation = UUID()
        let revision = generation
        requestKind = page
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
            let verified = $0["verified"] as? Bool ?? false
            let latest = $0["is_latest"] as? Bool ?? false
            return DataRow(source: $0["source"] as? String ?? "",
                    ready: $0["ready"] as? Bool ?? false,
                    old: !latest,
                    verified: verified,
                    latest: latest,
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
                // The publication probe can finish before the initial check
                // releases `busy`; its result is still valid for this session.
                guard generation == revision, showPreflight else { return }
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

    /// Mirrors the engine's `needs_cycle_age_confirmation`: a ready dataset
    /// that is unverified or not the latest release needs an explicit user
    /// acknowledgment before forecasting (B6).
    var needsCycleAgeConfirmation: Bool {
        ready && rows.contains { $0.ready && (!$0.verified || !$0.latest) }
    }

    var cycleAgeSummary: String {
        let titles = rows.filter { $0.ready && (!$0.verified || !$0.latest) }.map(\.title)
        return titles.isEmpty ? "部分数据" : titles.joined(separator: "、")
    }

    func confirm() {
        guard let session, ready, !busy else { return }
        if needsCycleAgeConfirmation {
            showCycleAgeConfirm = true
            return
        }
        runForecast(cycleAgeConfirmed: false)
    }

    private func runForecast(cycleAgeConfirmed: Bool) {
        guard let session, ready, !busy else { return }
        publicationTask?.cancel()
        showPreflight = false
        busy = true
        checking = false
        error = ""
        percent = 0
        status = "正在预测"
        let revision = generation
        let kind = requestKind
        operation = Task {
            defer { if generation == revision { busy = false } }
            do {
                var params: JSONObject = [
                    "session": session, "confirmed": true,
                ]
                if cycleAgeConfirmed { params["cycle_age_confirmed"] = true }
                let response = try await engine.call("forecast", params, progress: updateProgress)
                try Task.checkCancellation()
                guard generation == revision else { return }
                result = response
                resultKind = kind
                selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                if kind == .regional, regionalDisplay != "compare" { regionalDisplay = selectedModel }
                self.session = nil
                status = "预测完成"
            } catch { if generation == revision { self.session = nil; ready = false; fail(error) } }
        }
    }

    /// 周期龄确认对话框的「继续预测」：用户已知晓数据可能不是最新发布。
    func confirmCycleAgeAndForecast() {
        showCycleAgeConfirm = false
        runForecast(cycleAgeConfirmed: true)
    }

    func updateProgress(_ event: JSONObject) {
        percent = min(100, max(0, (event["percent"] as? NSNumber)?.doubleValue ?? 0))
        let source = event["source"] as? String ?? ""
        let message = event["message"] as? String ?? ""
        if dataSyncRunning, !source.isEmpty {
            dataSyncProgress[source] = ["percent": percent, "message": message]
        }
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
        showCycleAgeConfirm = false
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

    func loadTaskHistory() {
        guard online, !taskHistoryLoading else { return }
        taskHistoryTask?.cancel()
        taskHistoryTask = Task { [weak self] in
            guard let self else { return }
            taskHistoryLoading = true
            taskHistoryError = ""
            defer {
                taskHistoryLoading = false
                taskHistoryTask = nil
            }
            do {
                let response = try await engine.call("task_history", ["limit": 100])
                try Task.checkCancellation()
                guard response["schema"] as? String == "firesky-task-history-v1",
                      response["automatic_restart"] as? Bool == false,
                      let values = response["tasks"] as? [JSONObject],
                      let total = (response["total"] as? NSNumber)?.intValue,
                      total >= values.count,
                      values.count <= 100 else {
                    throw EngineError(message: "任务历史协议不匹配，请重新连接引擎。")
                }
                var parsed: [NativeTaskHistoryItem] = []
                for value in values {
                    guard let item = NativeTaskHistoryItem(value) else {
                        throw EngineError(message: "任务历史包含无效记录，请检查引擎版本。")
                    }
                    parsed.append(item)
                }
                guard Set(parsed.map(\.taskID)).count == parsed.count else {
                    throw EngineError(message: "任务历史包含重复记录，请重新连接引擎。")
                }
                taskHistory = parsed
                taskHistoryTotal = total
                taskHistoryIssues = (response["issues"] as? [Any])?.count ?? 0
                taskHistoryLoaded = true
                if let selectedTaskHistoryID,
                   !parsed.contains(where: { $0.taskID == selectedTaskHistoryID }) {
                    clearTaskHistorySelection()
                }
            } catch {
                guard !Task.isCancelled else { return }
                taskHistoryError = error.localizedDescription
            }
        }
    }

    func selectTaskHistory(_ taskID: String) {
        guard selectedTaskHistoryID != taskID else {
            clearTaskHistorySelection()
            return
        }
        guard taskHistory.contains(where: { $0.taskID == taskID }) else { return }
        taskHistoryEventsTask?.cancel()
        selectedTaskHistoryID = taskID
        taskHistoryEvents = []
        taskHistoryEventsError = ""
        taskHistoryEventsTruncated = false
        taskHistoryEventsLoading = true
        taskHistoryEventsTask = Task { [weak self] in
            guard let self else { return }
            defer {
                taskHistoryEventsLoading = false
                taskHistoryEventsTask = nil
            }
            do {
                let response = try await engine.call("task_events", [
                    "task_id": taskID, "after_seq": 0,
                ])
                try Task.checkCancellation()
                guard response["task_id"] as? String == taskID,
                      let values = response["events"] as? [JSONObject],
                      let next = (response["next_seq"] as? NSNumber)?.intValue,
                      next >= 0,
                      response["truncated"] as? Bool != nil else {
                    throw EngineError(message: "任务事件协议不匹配。")
                }
                var parsed: [NativeTaskEvent] = []
                var previous = 0
                for value in values {
                    guard let sequence = (value["seq"] as? NSNumber)?.intValue,
                          sequence > previous,
                          sequence <= next,
                          let atUTC = value["at_utc"] as? String,
                      taskTimestamp(atUTC) != nil,
                          let event = value["event"] as? String else {
                        throw EngineError(message: "任务事件顺序或时间无效。")
                    }
                    previous = sequence
                    parsed.append(NativeTaskEvent(
                        sequence: sequence, atUTC: atUTC, event: event,
                        data: value["data"] as? JSONObject ?? [:]
                    ))
                }
                taskHistoryEvents = parsed
                taskHistoryEventsTruncated = response["truncated"] as? Bool ?? false
            } catch {
                guard !Task.isCancelled else { return }
                taskHistoryEventsError = error.localizedDescription
            }
        }
    }

    func clearTaskHistorySelection() {
        taskHistoryEventsTask?.cancel()
        taskHistoryEventsTask = nil
        selectedTaskHistoryID = nil
        taskHistoryEvents = []
        taskHistoryEventsError = ""
        taskHistoryEventsLoading = false
        taskHistoryEventsTruncated = false
    }

    func forgetTaskHistory(_ taskID: String) {
        guard online, !busy, forgettingTaskHistoryID == nil,
              let item = taskHistory.first(where: { $0.taskID == taskID }),
              item.status != "running" else { return }
        forgettingTaskHistoryID = taskID
        Task { [weak self] in
            guard let self else { return }
            defer { forgettingTaskHistoryID = nil }
            do {
                let response = try await engine.call("task_forget", ["task_id": taskID])
                guard response["forgotten"] as? Bool == true else {
                    throw EngineError(message: "任务记录没有被删除。")
                }
                if selectedTaskHistoryID == taskID { clearTaskHistorySelection() }
                loadTaskHistory()
            } catch {
                taskHistoryError = error.localizedDescription
            }
        }
    }

    func restoreTaskParameters(_ item: NativeTaskHistoryItem) {
        guard !busy, restoringTaskHistoryID == nil,
              item.status != "running", let retry = item.retryRequest else { return }
        restoringTaskHistoryID = item.taskID
        applyRetryPayload(retry.payload, kind: retry.kind)
        page = retry.kind
        restoringTaskHistoryID = nil
        beginCheck()
    }

    func restoreTaskResult(_ item: NativeTaskHistoryItem) {
        guard !busy, restoringTaskHistoryID == nil,
              item.canRestoreResult,
              item.resultReference?["snapshot"] is JSONObject else { return }
        restoringTaskHistoryID = item.taskID
        let kind = item.retryRequest?.kind ?? .point
        Task { [weak self] in
            guard let self else { return }
            defer { restoringTaskHistoryID = nil }
            do {
                let response = try await engine.call("result_restore", [
                    "task_id": item.taskID,
                ])
                try Task.checkCancellation()
                if kind != .nowcast {
                    guard response["model_results"] is JSONObject else {
                        throw EngineError(message: "已发布结果格式无效。")
                    }
                }
                result = response
                resultKind = kind
                page = kind
                if kind != .nowcast {
                    if let model = item.retryRequest?.payload["model_source"] as? String,
                       ["gfs", "ec"].contains(model) {
                        selectedModel = model
                    } else {
                        selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                    }
                    if kind == .regional, regionalDisplay != "compare" { regionalDisplay = selectedModel }
                }
                status = "已恢复历史结果"
                error = ""
            } catch {
                guard !Task.isCancelled else { return }
                taskHistoryError = error.localizedDescription
            }
        }
    }

    private func applyRetryPayload(_ payload: JSONObject, kind: Page) {
        if let value = payload["name"] as? String { name = value }
        if let value = (payload["lat"] as? NSNumber)?.doubleValue { lat = value }
        if let value = (payload["lon"] as? NSNumber)?.doubleValue { lon = value }
        if let value = (payload["elevation_m"] as? NSNumber)?.doubleValue { elevation = value }
        if let value = payload["timezone_name"] as? String { timezone = value }
        if let value = payload["target_date"] as? String,
           let date = ObservationDate.date(value, timezone: timezone) {
            self.date = date
        }
        if let value = payload["phenomenon"] as? String { phenomenon = value }
        if let value = payload["ecmwf_source"] as? String { ecmwfSource = value }
        if kind == .regional {
            let regionCodes = (payload["regions"] as? [String]) ?? []
            selectedRegions = Set(regionCodes.filter { $0 != "custom" })
            if let bounds = payload["custom_bounds"] as? JSONObject,
               let west = (bounds["west"] as? NSNumber)?.doubleValue,
               let east = (bounds["east"] as? NSNumber)?.doubleValue,
               let south = (bounds["south"] as? NSNumber)?.doubleValue,
               let north = (bounds["north"] as? NSNumber)?.doubleValue {
                self.west = west; self.east = east; self.south = south; self.north = north
                customRegion = regionCodes.contains("custom") || selectedRegions.isEmpty
            } else {
                customRegion = false
            }
        }
        metadataReady = true
    }

    func fail(_ error: Error) {
        self.error = friendlyEngineMessage(error.localizedDescription)
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
        ForecastComparison(result: resultKind == .point ? result : nil)
    }

    var regionalForecast: RegionalForecast {
        resultKind == .regional ? regionalCache : RegionalForecast(result: nil)
    }

    var regionalTitle: String {
        let labels = regionDefinitions.compactMap { item -> String? in
            guard selectedRegions.contains(item["code"] as? String ?? "") else { return nil }
            return item["label"] as? String ?? item["code"] as? String
        } + (customRegion ? ["自定义区域"] : [])
        return labels.isEmpty ? "请选择区域" : labels.joined(separator: " · ")
    }

    func focusRegionalCell(_ coordinate: GridCoordinate) {
        selectedRegionalCoordinate = coordinate
        regionalFocusVersion += 1
    }

    /// 区域→单点点诊断入口（regional-5，方案 :32-33 保留的用户发起模式切换，
    /// 非图-表联动）。沿用 selectPoint 的坐标预填语义：坐标变化会按既有规则
    /// 作废当前结果，单点页显示「等待预测」，不会拿区域数据拼接填充。
    func openRegionalCellInPointForecast(_ cell: RegionalCell, model: String) {
        guard !busy else { return }
        selectPoint(lat: cell.coordinate.lat, lon: cell.coordinate.lon)
        name = cell.label
        selectedModel = model
        page = .point
    }

    func queryRegionalProfile(model: String, coordinate: GridCoordinate) {
        guard resultKind == .regional,
              let reports = result?["model_results"] as? JSONObject,
              let report = reports[model] as? JSONObject,
              let cell = regionalForecast.model(model).cell(at: coordinate),
              let bestTime = cell.bestTime,
              let request = ProfileQueryBuilder.request(
                  report: report, model: model, lat: coordinate.lat, lon: coordinate.lon,
                  localTime: bestTime) else {
            regionalProfile = nil
            regionalProfileModel = model
            regionalProfileCoordinate = coordinate
            regionalProfileState = "unavailable"
            regionalProfileError = "该网格没有可读取的冻结剖面时次。"
            return
        }
        regionalProfileTask?.cancel()
        regionalProfile = nil
        regionalProfileModel = model
        regionalProfileCoordinate = coordinate
        regionalProfileState = "loading"
        regionalProfileError = ""
        showRegionalProfileFigure = false
        regionalProfileTask = Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await engine.call("profile_query", request.params)
                try Task.checkCancellation()
                guard ProfileQueryBuilder.responseMatches(response, request: request) else {
                    throw EngineError(message: "剖面响应与当前结果、模式、坐标或时次不匹配。")
                }
                guard response["status"] as? String == "ready",
                      let section = response["cross_section"] as? JSONObject else {
                    let status = response["status"] as? String ?? "unavailable"
                    let reason = status == "expired"
                        ? "冻结源已过期，请重新运行预测以获取新剖面。"
                        : (response["reason"] as? String ?? "所选时次没有可用剖面。")
                    regionalProfile = nil
                    regionalProfileState = status
                    regionalProfileError = reason
                    return
                }
                guard ProfileQueryBuilder.sameUTC(
                    response["cycle_utc"] as? String ?? "", request.cycleUTC) else {
                    throw EngineError(message: "剖面响应的模式周期与已发布区域结果不匹配。")
                }
                regionalProfile = section
                regionalProfileState = "ready"
                regionalProfileError = ""
            } catch {
                guard !Task.isCancelled else { return }
                regionalProfile = nil
                regionalProfileState = "error"
                regionalProfileError = error.localizedDescription
            }
        }
    }

    func queryPointProfile() {
        guard resultKind == .point, let report = modelResult,
              let model = (report["profile_source"] as? JSONObject)?["model_source"] as? String,
              let validTime = (report["best_time_local"] as? String)
                  ?? (report["cross_section"] as? JSONObject)?["valid_time_local"] as? String
                  ?? (report["anchor_local"] as? String) else {
            frozenProfile = nil
            frozenProfileState = "unavailable"
            frozenProfileError = "该结果没有可读取的冻结剖面时次。"
            return
        }
        runFrozenProfileQuery(model: model, lat: lat, lon: lon,
                              validTimeLocal: validTime, report: report, point: true)
    }

    func queryNowcastProfile(validTimeLocal: String) {
        guard resultKind == .nowcast, let report = modelResult else {
            frozenProfile = nil
            frozenProfileState = "unavailable"
            frozenProfileError = "该结果没有可查询的冻结卫星剖面。"
            return
        }
        runFrozenProfileQuery(model: "satellite", lat: lat, lon: lon,
                              validTimeLocal: validTimeLocal, report: report)
    }

    private func runFrozenProfileQuery(model: String, lat: Double, lon: Double,
                                       validTimeLocal: String, report: JSONObject, point: Bool = false) {
        let build = point ? ProfileQueryBuilder.pointRequest : ProfileQueryBuilder.request
        guard let request = build(
            report, model, lat, lon, validTimeLocal) else {
            frozenProfile = nil
            frozenProfileModel = model
            frozenProfileTime = validTimeLocal
            frozenProfileState = "unavailable"
            frozenProfileError = "该结果没有可读取的冻结剖面时次。"
            return
        }
        frozenProfileTask?.cancel()
        frozenProfile = nil
        frozenProfileModel = model
        frozenProfileTime = validTimeLocal
        frozenProfileState = "loading"
        frozenProfileError = ""
        frozenProfileTask = Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await engine.call("profile_query", request.params)
                try Task.checkCancellation()
                guard ProfileQueryBuilder.responseMatches(response, request: request) else {
                    throw EngineError(message: "剖面响应与当前结果、模式、坐标或时次不匹配。")
                }
                guard response["status"] as? String == "ready",
                      var section = response["cross_section"] as? JSONObject else {
                    let status = response["status"] as? String ?? "unavailable"
                    let reason = status == "expired"
                        ? "冻结源已过期，请重新运行预测以获取新剖面。"
                        : (response["reason"] as? String ?? "所选时次没有可用剖面。")
                    frozenProfile = nil
                    frozenProfileState = status
                    frozenProfileError = reason
                    return
                }
                guard ProfileQueryBuilder.sameUTC(
                    response["cycle_utc"] as? String ?? "", request.cycleUTC) else {
                    throw EngineError(message: "剖面响应的模式周期与已发布结果不匹配。")
                }
                if model == "satellite", let times = response["source_times_utc"] {
                    section["source_times_utc"] = times
                }
                frozenProfile = section
                frozenProfileState = "ready"
                frozenProfileError = ""
            } catch {
                guard !Task.isCancelled else { return }
                frozenProfile = nil
                frozenProfileState = "error"
                frozenProfileError = error.localizedDescription
            }
        }
    }

    func applyDrawnRegion(_ bounds: GridBounds) {
        west = bounds.west; south = bounds.south
        east = bounds.east; north = bounds.north
        selectedRegions = []
        customRegion = true
        editingRegionOnMap = false
    }

    var resultPoints: [JSONObject] {
        guard resultKind == page else { return [] }
        return modelResult?["points"] as? [JSONObject] ?? []
    }

    var previewAreas: [[Double]] {
        var areas = regionDefinitions.compactMap { item -> [Double]? in
            guard selectedRegions.contains(item["code"] as? String ?? "") else { return nil }
            return item["bounds"] as? [Double]
        }
        if customRegion { areas.append([west, south, east, north]) }
        return areas
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

    func loadRetentionSettings() async {
        guard online else { return }
        do {
            let response = try await engine.call("retention_settings")
            let settings = response["settings"] as? JSONObject ?? [:]
            retentionGFS = retentionText(settings["gfs_cycles"])
            retentionECMWF = retentionText(settings["ecmwf_cycles"])
            retentionCAMS = retentionText(settings["cams_cycles"])
            retentionSatelliteDays = retentionText(settings["satellite_days"])
            retentionReportDays = retentionText(settings["report_days"])
            retentionMapDays = retentionText(settings["map_days"])
            retentionConfigured = response["configured"] as? Bool ?? false
            retentionMessage = ""
            retentionSaveFailed = false
        } catch {
            retentionMessage = error.localizedDescription
            retentionSaveFailed = true
        }
    }

    func saveRetentionSettings() {
        guard online, !busy, !savingRetention else { return }
        savingRetention = true
        retentionMessage = ""
        let payload: JSONObject = [
            "settings": [
                "gfs_cycles": retentionInteger(retentionGFS),
                "ecmwf_cycles": retentionInteger(retentionECMWF),
                "cams_cycles": retentionInteger(retentionCAMS),
                "satellite_days": retentionNumber(retentionSatelliteDays),
                "report_days": retentionNumber(retentionReportDays),
                "map_days": retentionNumber(retentionMapDays),
            ],
        ]
        Task {
            defer { savingRetention = false }
            do {
                let response = try await engine.call("save_retention_settings", payload)
                let settings = response["settings"] as? JSONObject ?? [:]
                retentionGFS = retentionText(settings["gfs_cycles"])
                retentionECMWF = retentionText(settings["ecmwf_cycles"])
                retentionCAMS = retentionText(settings["cams_cycles"])
                retentionSatelliteDays = retentionText(settings["satellite_days"])
                retentionReportDays = retentionText(settings["report_days"])
                retentionMapDays = retentionText(settings["map_days"])
                retentionConfigured = response["configured"] as? Bool ?? false
                retentionMessage = retentionConfigured ? "保留设置已保存；下一次成功准备后执行清理。" : "已清空保留设置。"
                retentionSaveFailed = false
                // 同步页的「未配置保留」软警告依赖 retention_configured，一并刷新。
                await refreshDataSyncState()
            } catch {
                retentionMessage = error.localizedDescription
                retentionSaveFailed = true
            }
        }
    }

    private func retentionText(_ value: Any?) -> String {
        guard let number = value as? NSNumber else { return "" }
        if number.doubleValue.rounded() == number.doubleValue {
            return String(number.intValue)
        }
        return String(format: "%.2f", number.doubleValue)
    }

    private func retentionInteger(_ value: String) -> Any {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? NSNull() : (Int(trimmed) ?? -1)
    }

    private func retentionNumber(_ value: String) -> Any {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? NSNull() : (Double(trimmed) ?? -1)
    }

    // MARK: 后台数据同步（设计 11 节）

    /// 连接时加载合并状态（设置 + 每源状态 + 保留配置提示），与
    /// loadRetentionSettings 同管道；非法值不在 UI 拦截，由引擎保存时报错回显。
    func loadDataSyncSettings() async {
        guard online else { return }
        do {
            let response = try await engine.call("data_sync_settings")
            applyDataSyncStatus(response)
        } catch {
            dataSyncMessage = error.localizedDescription
            dataSyncSaveFailed = true
        }
    }

    private func applyDataSyncStatus(_ response: JSONObject) {
        let settings = response["settings"] as? JSONObject ?? [:]
        let sources = settings["sources"] as? JSONObject ?? [:]
        dataSyncEnabled = settings["enabled"] as? Bool ?? false
        dataSyncCAMS = sources["cams"] as? Bool ?? false
        dataSyncGFS = sources["gfs"] as? Bool ?? false
        dataSyncEC = sources["ec"] as? Bool ?? false
        dataSyncInterval = dataSyncNumberText(settings["interval_minutes"])
        dataSyncLeadHorizon = dataSyncNumberText(settings["lead_horizon_hours"])
        dataSyncAnchor = settings["anchor_region"] as? String ?? ""
        dataSyncConfigured = response["configured"] as? Bool ?? false
        dataSyncRetentionConfigured = response["retention_configured"] as? Bool ?? false
        dataSyncState = (response["state"] as? JSONObject)?["sources"] as? JSONObject ?? [:]
        dataSyncLoaded = true
    }

    private func dataSyncNumberText(_ value: Any?) -> String {
        guard let number = value as? NSNumber else { return "" }
        return String(number.intValue)
    }

    private func dataSyncInteger(_ value: String) -> Any {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? NSNull() : (Int(trimmed) ?? -1)
    }

    func saveDataSyncSettings() {
        guard online, !busy, !savingDataSync else { return }
        savingDataSync = true
        dataSyncMessage = ""
        let payload: JSONObject = [
            "settings": [
                "enabled": dataSyncEnabled,
                "sources": ["cams": dataSyncCAMS, "gfs": dataSyncGFS, "ec": dataSyncEC],
                "interval_minutes": dataSyncInteger(dataSyncInterval),
                "anchor_region": (dataSyncAnchor.isEmpty ? NSNull() : dataSyncAnchor) as Any,
                "lead_horizon_hours": dataSyncInteger(dataSyncLeadHorizon),
                // EC 下载源复用现有 ecmwfSource（设计 11.1）。
                "ecmwf_source": ecmwfSource,
            ],
        ]
        Task {
            defer { savingDataSync = false }
            do {
                // 重存设置即解锁通道：引擎在保存时清零熔断计数与挂起标记。
                let response = try await engine.call("save_data_sync_settings", payload)
                applyDataSyncStatus(response)
                // 已安装助手时，重存设置同步刷新其 StartInterval（间隔可能变了）。
                if helperInstalled {
                    let ws = workspace
                    let interval = Int(dataSyncInterval) ?? 30
                    await Task.detached {
                        try? SyncHelperInstaller.install(workspace: ws, intervalMinutes: interval)
                    }.value
                    refreshHelperStatus()
                }
                dataSyncMessage = dataSyncEnabled
                    ? "后台同步已保存；引擎按新设置在心跳周期内执行。"
                    : "后台同步已关闭。"
                dataSyncSaveFailed = false
            } catch {
                dataSyncMessage = error.localizedDescription
                dataSyncSaveFailed = true
            }
        }
    }

    /// 当前镜像中启用的数据源；引擎仍是权威校验方（未启用的源会被拒绝）。
    private var dataSyncEnabledSources: [String] {
        [("cams", dataSyncCAMS), ("gfs", dataSyncGFS), ("ec", dataSyncEC)]
            .filter { $0.1 }.map { $0.0 }
    }

    /// 60 秒心跳（设计 5.1/11.3）：启动即先 tick 一次，之后每 60 秒一次；
    /// 引擎未连接、手动会话进行中或同步未启用时静默跳过。
    private func startSyncTick() {
        syncTickTask?.cancel()
        syncTickTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.syncTick()
                try? await Task.sleep(for: .seconds(60))
            }
        }
    }

    private func syncTick() async {
        guard online, !busy, !probingDownloads, !dataSyncRunning,
              dataSyncLoaded, dataSyncEnabled else { return }
        do {
            let due = try await engine.call("data_sync_due")
            guard !Task.isCancelled, due["due_for_any"] as? Bool == true,
                  let sources = due["due_sources"] as? [String], !sources.isEmpty else { return }
            // tick 静默（设计 11.3）：轮级错误不写界面文案，逐源结果留在
            // 状态文件与任务历史，下个心跳重新判定。
            _ = await runDataSyncRound(sources: sources)
        } catch {
            // tick 保持静默：失败留在每源状态与任务历史里，下个心跳重新判定。
        }
    }

    /// 仅刷新每源状态与保留配置提示；不回填设置字段，避免覆盖正在编辑的内容。
    private func refreshDataSyncState() async {
        guard online, let response = try? await engine.call("data_sync_settings") else { return }
        dataSyncRetentionConfigured = response["retention_configured"] as? Bool ?? dataSyncRetentionConfigured
        dataSyncState = (response["state"] as? JSONObject)?["sources"] as? JSONObject ?? dataSyncState
    }

    /// 一轮同步（tick 触发或手动）。引擎侧自带锁矩阵与熔断状态提交；
    /// busy 由 store.busy 表达手动会话，同步不占用它。返回轮次结果与轮级
    /// 错误：每源成败写在状态文件与任务历史里，但轮级拒绝（busy 让路、
    /// 源未启用、状态写盘失败）不会进入任何持久记录，手动路径必须展示。
    private func runDataSyncRound(sources: [String]) async -> (response: JSONObject?, error: Error?) {
        dataSyncRunning = true
        dataSyncProgress = [:]
        defer { dataSyncRunning = false }
        var response: JSONObject?
        var roundError: Error?
        do {
            response = try await engine.call("data_sync_run", ["sources": sources],
                                             progress: { [weak self] event in
                // 不覆盖进行中的手动操作文案（设计 11.3）。
                guard let self, !self.busy else { return }
                self.updateProgress(event)
            })
        } catch {
            roundError = error
        }
        await refreshDataSyncState()
        return (response, roundError)
    }

    /// 数据页「立即同步」（设计 5.1 第 4 条/11.2）：绕过 due 判定直接执行一轮，
    /// 显式传当前启用的源；引擎在源真正开始执行时清零其熔断状态（人工解锁）。
    func runDataSyncNow() {
        guard online, !busy, !probingDownloads, !dataSyncRunning, !savingDataSync else { return }
        let sources = dataSyncEnabledSources
        guard dataSyncEnabled, !sources.isEmpty else {
            dataSyncMessage = "请先在设置中启用后台数据同步并至少选择一个数据源。"
            dataSyncSaveFailed = true
            return
        }
        status = "正在立即同步后台数据"
        Task {
            let round = await runDataSyncRound(sources: sources)
            guard !busy else { return }
            if let error = round.error {
                // 轮级拒绝不进状态文件也不进任务历史，直接展示具体原因。
                dataSyncMessage = friendlyEngineMessage(error.localizedDescription)
                dataSyncSaveFailed = true
                status = "立即同步未完成"
            } else {
                let summary = round.response.map(dataSyncRoundSummary) ?? "立即同步未完成，详见任务历史。"
                status = summary
                // 数据页可见反馈：逐源成败直接展示在「立即同步」旁，
                // 不再只写全局 status（数据页不渲染它）。
                dataSyncMessage = summary
                dataSyncSaveFailed = summary.contains("失败") || summary.contains("未完成")
            }
        }
    }

    private func dataSyncRoundSummary(_ response: JSONObject) -> String {
        let labels = ["cams": "CAMS", "gfs": "GFS", "ec": "EC"]
        let names = ["cams", "gfs", "ec"]
        let parts = names.compactMap { name -> String? in
            guard let source = response["sources"] as? JSONObject, let row = source[name] as? JSONObject
            else { return nil }
            let status = row["status"] as? String ?? ""
            let text: String
            switch status {
            case "synced": text = "已同步"
            case "current": text = "已是最新"
            case "failed": text = "失败"
            case "cancelled": text = "已取消"
            default: text = status.isEmpty ? "无结果" : status
            }
            return "\(labels[name] ?? name) \(text)"
        }
        return parts.isEmpty ? "立即同步结束，没有可同步的数据源。" : "立即同步结束：" + parts.joined(separator: "、")
    }

    // MARK: 后台同步助手（launchd LaunchAgent，决策见跨平台选型文档）

    /// 读取助手安装/加载状态与上次后台运行结果（helper-status.json）。
    /// 应用关闭期间助手仍在同步，打开应用即可看到最近一轮结果。
    func refreshHelperStatus() {
        helperAvailable = SyncHelperInstaller.isAvailable
        helperInstalled = SyncHelperInstaller.isInstalled()
        helperLoaded = SyncHelperInstaller.isLoaded()
        let path = URL(fileURLWithPath: workspace)
            .appendingPathComponent(".firesky/state/helper-status.json")
        if let data = try? Data(contentsOf: path),
           let json = try? JSONSerialization.jsonObject(with: data) as? JSONObject {
            helperStatus = json
        } else {
            helperStatus = [:]
        }
    }

    func installHelper() {
        guard helperAvailable, !helperBusy else { return }
        helperBusy = true
        helperMessage = ""
        let interval = Int(dataSyncInterval) ?? 30
        let ws = workspace
        Task {
            defer { helperBusy = false }
            let outcome: Result<Void, Error> = await Task.detached {
                do {
                    try SyncHelperInstaller.install(workspace: ws, intervalMinutes: interval)
                    return .success(())
                } catch { return .failure(error) }
            }.value
            switch outcome {
            case .success:
                helperMessage = "后台助手已安装；关闭应用后仍按间隔同步。"
                helperMessageFailed = false
            case .failure(let error):
                helperMessage = error.localizedDescription
                helperMessageFailed = true
            }
            refreshHelperStatus()
        }
    }

    func uninstallHelper() {
        guard !helperBusy else { return }
        helperBusy = true
        helperMessage = ""
        Task {
            defer { helperBusy = false }
            let outcome: Result<Void, Error> = await Task.detached {
                do {
                    try SyncHelperInstaller.uninstall()
                    return .success(())
                } catch { return .failure(error) }
            }.value
            switch outcome {
            case .success:
                helperMessage = "后台助手已卸载。"
                helperMessageFailed = false
            case .failure(let error):
                helperMessage = error.localizedDescription
                helperMessageFailed = true
            }
            refreshHelperStatus()
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

    func copyDownloadProbeReport() {
        let timestamp = downloadProbeDate?.formatted(date: .numeric, time: .standard) ?? "未完成"
        let report = ["FireSky 数据源下载探测 · \(timestamp)"] + downloadProbeRows.map {
            "\($0.title) [\($0.host)] · \($0.label)\n\($0.detail)\n\($0.measurement)"
        }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(report.joined(separator: "\n\n"), forType: .string)
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

    func shutdown() {
        downloadProbeTask?.cancel()
        locationTimeout?.cancel()
        operation?.cancel()
        publicationTask?.cancel()
        regionalProfileTask?.cancel()
        frozenProfileTask?.cancel()
        taskHistoryTask?.cancel()
        taskHistoryEventsTask?.cancel()
        syncTickTask?.cancel()
        engine.stop()
    }
}

/// F3：引擎锁/让路等机器可读错误串映射为中文 + 可操作建议，界面不透传裸英文。
/// 仅映射已知的协议级错误；未知错误保留原文以便排查。
func friendlyEngineMessage(_ message: String) -> String {
    let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
    switch text {
    case "another prepare/forecast is active":
        return "已有预测准备或计算正在进行，请等待完成或先取消，再发起本次操作。"
    case "data sync skipped because another prepare/forecast is active":
        return "预测准备或计算正在进行，本轮同步已让路；待当前操作完成后再同步。"
    case "another data sync is active":
        return "已有一轮数据同步正在进行，请稍候再试。"
    case "another data sync round is active in another process":
        return "后台助手正在另一进程中同步数据，本轮已让路；稍后会自动重试。"
    case "network probe is already active":
        return "网络探测正在进行，请稍候再试。"
    case "desktop operation cancelled", "nowcast preparation cancelled":
        return "操作已取消。"
    case "data cycle is unverified or older than the latest release; explicit cycle-age confirmation is required":
        return "数据周期未核验或不是最新发布，请在确认提示中选择「继续预测」，或先更新数据。"
    case "explicit confirmation is required before forecast":
        return "请先在预检窗口确认后再开始预测。"
    case "inputs are missing; prepare data before forecasting":
        return "本次预测数据不完整，请先点击「下载缺失数据」补齐。"
    case "strict input integrity check failed":
        return "数据完整性校验未通过，请重新下载本次数据后再试。"
    case "data sync is not enabled":
        return "后台数据同步未启用，请先在设置中开启。"
    default:
        break
    }
    if text.hasPrefix("data sync source "), text.hasSuffix(" is not enabled in settings") {
        let name = text.dropFirst("data sync source ".count).dropLast(" is not enabled in settings".count)
        return "数据源 \(name.uppercased()) 未在设置中启用，请先勾选后再同步。"
    }
    return message
}
