import CoreLocation
import Combine
import SwiftUI
import UIKit

/// 顶层标签页：预测工作台 / 数据管理 / 任务历史 / 设置。
enum Page: String, CaseIterable, Identifiable {
    case point, data, history, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .point: L10n.navPoint
        case .data: L10n.navData
        case .history: L10n.navHistory
        case .settings: L10n.navSettings
        }
    }
    var symbol: String {
        switch self {
        case .point: "location.circle"
        case .data: "externaldrive"
        case .history: "clock.arrow.circlepath"
        case .settings: "gearshape"
        }
    }
}

/// 预测工作台模式：单点预测 / 区域预测 / 卫星实况。
enum WorkbenchMode: String, CaseIterable, Identifiable {
    case point, regional, nowcast
    var id: String { rawValue }

    var title: String {
        switch self {
        case .point: L10n.text("单点预测", "Point")
        case .regional: L10n.text("区域预测", "Regional")
        case .nowcast: L10n.text("卫星实况", "Nowcast")
        }
    }

    var symbol: String {
        switch self {
        case .point: "location.circle"
        case .regional: "map"
        case .nowcast: "antenna.radiowaves.left.and.right"
        }
    }
}

/// 任务历史行（同构 macOS NativeTaskHistoryItem）。
struct MobileTaskHistoryItem: Identifiable {
    var id: String { taskID }
    let taskID: String
    let method: String
    let status: String
    let createdAt: String
    let cancelRequested: Bool
    let percent: Double?
    let retryKind: String?
    let retryPayload: JSONObject?
    let canRestoreResult: Bool

    var isBackgroundSync: Bool { method == "data_sync" }
    var methodTitle: String {
        switch method {
        case "check": L10n.text("数据检查", "Data check")
        case "prepare": L10n.text("数据准备", "Data preparation")
        case "forecast": L10n.text("预测计算", "Forecast")
        case "publications": L10n.text("发布核验", "Publication check")
        case "network_probe": L10n.text("下载探测", "Download probe")
        case "data_sync": L10n.text("后台同步", "Background sync")
        case "terrain_download": L10n.text("地形下载", "Terrain download")
        default: L10n.text("任务", "Task")
        }
    }
    var kindTitle: String {
        switch retryKind {
        case "point": L10n.text("单点", "Point")
        case "regional": L10n.text("区域", "Regional")
        case "nowcast": L10n.text("实况", "Nowcast")
        default: methodTitle
        }
    }
    var rowTitle: String { kindTitle == methodTitle ? methodTitle : "\(kindTitle) · \(methodTitle)" }
    var statusTitle: String {
        switch status {
        case "running": cancelRequested ? "取消中" : "运行中"
        case "done": "已完成"
        case "failed": "失败"
        case "cancelled": "已取消"
        case "interrupted": "已中断"
        default: status
        }
    }
    var contextTitle: String {
        guard let payload = retryPayload else {
            return isBackgroundSync ? L10n.text("后台数据同步轮次", "Background sync round") : ""
        }
        let name = payload["name"] as? String
            ?? (payload["regions"] as? [String])?.joined(separator: "、")
            ?? ""
        let date = payload["target_date"] as? String ?? ""
        let phenomenon: String
        switch payload["phenomenon"] as? String {
        case "dawn": phenomenon = L10n.t("朝霞")
        case "dusk": phenomenon = L10n.t("晚霞")
        default: phenomenon = ""
        }
        let text = [name, date, phenomenon].filter { !$0.isEmpty }.joined(separator: " · ")
        if text.isEmpty && isBackgroundSync { return L10n.text("后台数据同步轮次", "Background sync round") }
        return text
    }
    var localTime: String {
        guard let date = ObservationDate.timestamp(createdAt) else { return createdAt }
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd HH:mm"
        return formatter.string(from: date)
    }
    var rowTimeLine: String {
        var text = localTime
        if status == "running", let percent {
            text += String(format: " · %.0f%%", percent)
        }
        if status == "interrupted" {
            text += L10n.text(" · 不会自动重跑", " · Will not re-run")
        }
        return text
    }

    static func parse(_ value: JSONObject) -> MobileTaskHistoryItem? {
        guard let taskID = value["task_id"] as? String,
              taskID.count == 64, taskID.allSatisfy({ $0.isHexDigit }),
              let method = value["method"] as? String,
              let status = value["status"] as? String,
              ["running", "done", "failed", "cancelled", "interrupted"].contains(status),
              let createdAt = value["created_at_utc"] as? String,
              ObservationDate.timestamp(createdAt) != nil else {
            return nil
        }
        let retry = value["retry_request"] as? JSONObject
        let kind = retry?["kind"] as? String
        let validKind = kind.flatMap { ["point", "regional", "nowcast"].contains($0) ? $0 : nil }
        let payload = validKind != nil ? (retry?["payload"] as? JSONObject) : nil
        let snapshot = (value["result_ref"] as? JSONObject)?["snapshot"] as? JSONObject
        let canRestore = snapshot?["contract"] as? String == "firesky-result-snapshot-v1"
            && snapshot?["sha256"] is String
        return MobileTaskHistoryItem(
            taskID: taskID, method: method, status: status, createdAt: createdAt,
            cancelRequested: value["cancel_requested"] as? Bool ?? false,
            percent: (value["progress"] as? JSONObject)?["percent"] as? Double,
            retryKind: validKind,
            retryPayload: payload,
            canRestoreResult: canRestore
        )
    }
}

/// 任务事件行（同构 macOS NativeTaskEvent）。
struct MobileTaskEventRow: Identifiable {
    let seq: Int
    let at: String
    let event: String
    let stage: String
    let message: String
    let percent: Double?

    var id: Int { seq }
    var title: String {
        switch event {
        case "running": L10n.text("开始", "Started")
        case "progress": L10n.text("进度", "Progress")
        case "cancel_requested": L10n.text("请求取消", "Cancel requested")
        case "done": L10n.text("完成", "Done")
        case "failed": L10n.status("失败")
        case "cancelled": L10n.status("已取消")
        case "interrupted": L10n.status("已中断")
        default: L10n.text("状态更新", "Status update")
        }
    }
    var detail: String {
        var values: [String] = []
        if !stage.isEmpty { values.append(String(stage.prefix(128))) }
        if !message.isEmpty { values.append(String(message.prefix(128))) }
        if let percent { values.append(String(format: "%.0f%%", percent)) }
        return values.joined(separator: " · ")
    }
    var localTime: String {
        guard let date = ObservationDate.timestamp(at) else { return at }
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd HH:mm:ss"
        return formatter.string(from: date)
    }
}

/// 地形下载区域预设（v1.4.0 macOS TerrainRegion 同构）。
enum TerrainRegion: String, CaseIterable, Identifiable {
    case china = "中国"
    case eastAsia = "东亚"
    case southeastAsia = "东南亚"
    case asia = "亚洲"
    case europe = "欧洲"
    case northAmerica = "北美"
    case southAmerica = "南美"
    case africa = "非洲"
    case oceania = "大洋洲"
    case global = "全球"

    var id: String { rawValue }
    var bounds: JSONObject? {
        switch self {
        case .china: ["south": 18, "north": 54, "west": 73, "east": 135]
        case .eastAsia: ["south": 20, "north": 54, "west": 100, "east": 146]
        case .southeastAsia: ["south": -11, "north": 28, "west": 92, "east": 141]
        case .asia: ["south": -11, "north": 59, "west": 26, "east": 179]
        case .europe: ["south": 35, "north": 59, "west": -11, "east": 40]
        case .northAmerica: ["south": 15, "north": 59, "west": -170, "east": -52]
        case .southAmerica: ["south": -56, "north": 13, "west": -82, "east": -34]
        case .africa: ["south": -35, "north": 37, "west": -18, "east": 52]
        case .oceania: ["south": -47, "north": 0, "west": 110, "east": 179]
        case .global: nil
        }
    }
}

@MainActor
final class Store: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    let engine = EngineClient(transportFactory: { MockTransport() })
    let isUITestMockMode: Bool

    @Published var page: Page = {
        guard let raw = ProcessInfo.processInfo.environment["FIRESKY_PAGE"],
              let forced = Page(rawValue: raw) else { return .point }
        return forced
    }()
    @Published var workbenchMode: String = "point"
    var activeWorkbenchMode: WorkbenchMode { WorkbenchMode(rawValue: workbenchMode) ?? .point }

    // 观测点与参数
    @Published var name = "杭州"
    @Published var query = ""
    @Published var lat = 30.2741 { didSet { metadataReady = false } }
    @Published var lon = 120.1551 { didSet { metadataReady = false } }
    @Published var elevation = 8.0
    @Published var timezone = "Asia/Shanghai"
    @Published var metadataReady = true
    @Published var date = Date()
    @Published var phenomenon = "dusk"

    // 区域预测
    @Published var regionalModel = "gfs"
    @Published var selectedRegions: Set<String> = ["china"]
    @Published var regionalQuery = ""
    @Published var regionalInspectorMode = "cities"
    @Published var selectedRegionalCoordinate: GridCoordinate?
    @Published var regionalCache = RegionalForecast(result: nil)

    // 卫星实况
    @Published var nowcastResultTab = "概览"

    // 运行态
    @Published var busy = false
    @Published var searching = false
    @Published var locating = false
    @Published var status = "正在连接服务器"
    @Published var error = ""
    @Published var errorPage: Page? = nil
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
    @Published var inventoryCategories: [JSONObject] = []
    @Published var result: JSONObject? {
        didSet {
            if result == nil { resultParams = nil }
            resultVersion += 1
            pointSelectedTime = nil
        }
    }
    @Published var pointSelectedTime: String?
    private(set) var resultVersion = 0
    @Published var resultKind: WorkbenchMode = .point
    @Published var resultParams: JSONObject?
    var resultsByMode: [WorkbenchMode: (result: JSONObject, params: JSONObject?)] = [:]
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

    // 任务历史
    @Published var taskHistory: [MobileTaskHistoryItem] = []
    @Published var taskHistoryTotal = 0
    @Published var taskHistoryLoaded = false
    @Published var taskHistoryLoading = false
    @Published var taskHistoryError = ""
    @Published var taskHistoryHasMore = false
    @Published var taskHistoryCursor: String?
    @Published var selectedTaskID: String?
    @Published var taskEvents: [MobileTaskEventRow] = []
    @Published var taskEventsLoading = false
    @Published var taskEventsError = ""
    @Published var taskResultTaskID: String?
    @Published var taskResult: JSONObject?
    @Published var taskResultLoading = false
    @Published var taskResultError = ""
    var taskResultCache: [String: JSONObject] = [:]
    @Published var pendingForgetTaskID: String?
    @Published var restoringTaskID: String?

    // 数据管理（手动删除 + 地形区域下载）
    @Published var pendingDataDelete: JSONObject?
    @Published var dataDeletePreview: JSONObject?
    @Published var dataDeletePreviewing = false
    @Published var dataDeleteRunning = false
    @Published var dataDeleteMessage = ""
    @Published var dataDeleteFailed = false
    @Published var terrain: JSONObject?
    @Published var terrainLoading = false
    @Published var terrainDownloading = false
    @Published var terrainCancelling = false
    @Published var terrainMessage = ""
    @Published var terrainMessageFailed = false
    @Published var terrainProgress: JSONObject = [:]
    @Published var terrainRegion: TerrainRegion = .china
    @Published var terrainEstimate: JSONObject?
    @Published var terrainEstimateLoading = false
    @Published var showTerrainConfirm = false
    @Published var terrainLastFailedCount = 0
    @Published var terrainLastFailedRegion: TerrainRegion? = nil
    @Published var terrainLastFailedTiles: [String] = []

    // 保留策略 & 后台同步
    @Published var retentionGFS = ""
    @Published var retentionECMWF = ""
    @Published var retentionCAMS = ""
    @Published var retentionSatelliteDays = ""
    @Published var retentionReportDays = ""
    @Published var retentionMapDays = ""
    @Published var retentionConfigured = false
    @Published var savingRetention = false
    @Published var applyingRetention = false
    @Published var retentionMessage = ""
    @Published var retentionSaveFailed = false
    @Published var regionDefinitions: [JSONObject] = []
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
    @Published var dataSyncState: JSONObject = [:]
    @Published var dataSyncProgress: JSONObject = [:]
    @Published var dataSyncActiveSource = ""

    // 设置
    @Published var settingsCategory = "通用"
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
    @Published var elevationSource: String
    @Published var elevationSourceActual = ""
    var elevationSourceLabel: String {
        switch elevationSource {
        case "http": return L10n.text("仅 HTTP（open-meteo）", "HTTP only (open-meteo)")
        case "srtm": return L10n.text("SRTM 下载（本地时区 + 瓦片）", "SRTM tiles (local TZ)")
        default: return L10n.text("自动（HTTP 优先，SRTM 兜底）", "Auto (HTTP first, SRTM fallback)")
        }
    }
    var elevationSourceActualLabel: String {
        switch elevationSourceActual {
        case "open-meteo-point-metadata": return "open-meteo (HTTP)"
        case "local-timezone+srtm": return L10n.text("本地时区 + SRTM", "Local TZ + SRTM")
        case "": return L10n.text("未获取", "Not fetched")
        default: return elevationSourceActual
        }
    }
    @Published var favorites: [JSONObject] = []
    @Published var workspace: String
    @Published var appearance: String
    @Published var appLanguage: L10n.Language = L10n.language {
        didSet { L10n.language = appLanguage }
    }
    var appLocale: Locale {
        Locale(identifier: L10n.effective == .english ? "en_US" : "zh_CN")
    }

    // 远程服务器薄客户端模式
    @Published var remoteServerURL: String
    @Published var hasSavedRemoteServer: Bool
    @Published var remoteTokenDraft = ""
    @Published var remoteTokenSaved: Bool
    @Published var remoteTesting = false
    @Published var remoteTestMessage = ""
    @Published var remoteTestOK = false
    @Published var remoteWorkspace = ""
    var isRemoteMode: Bool { !isUITestMockMode }
    var needsRemoteOnboarding: Bool { !isUITestMockMode && !hasSavedRemoteServer }
    var remoteSession: URLSession = .shared

    var engineConnection: AnyCancellable?
    let location = CLLocationManager()
    var operation: Task<Void, Never>?
    var searchTask: Task<Void, Never>?
    var publicationTask: Task<Void, Never>?
    var downloadProbeTask: Task<Void, Never>?
    var terrainTask: Task<Void, Never>?
    var terrainDownloadTask: Task<Void, Never>?
    var locationTimeout: Task<Void, Never>?
    var generation = UUID()
    var metadataGeneration = UUID()
    var requestKind: WorkbenchMode = .point
    var requestPayload: JSONObject = [:]
    var didCheckStartupCredentials = false
    var connecting = false

    override init() {
        let defaults = UserDefaults.standard
        let args = ProcessInfo.processInfo.arguments
        let env = ProcessInfo.processInfo.environment
        isUITestMockMode = args.contains("-UITestMockMode") || env["FIRESKY_TRANSPORT"] == "mock"

        let dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FireSky/Data", isDirectory: true)
        try? FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        workspace = dataDirectory.path
        appearance = defaults.string(forKey: "appearance") ?? "system"

        let savedURL = (defaults.string(forKey: "remoteServerURL") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        remoteServerURL = savedURL
        hasSavedRemoteServer = !savedURL.isEmpty
        remoteTokenSaved = ServerCredentialStore.hasToken()
        ecmwfSource = defaults.string(forKey: "ecmwfSource") ?? "ecmwf"
        elevationSource = ElevationSourceSetting.load(defaults)

        super.init()
        engineConnection = engine.$connected.sink { [weak self] value in self?.online = value }
        favorites = defaults.array(forKey: "favorites") as? [JSONObject] ?? []
        location.delegate = self
        location.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

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

        if !isUITestMockMode && !hasSavedRemoteServer {
            engine.stop()
            remoteWorkspace = ""
            clearError()
            status = L10n.text("请连接 FireSky 服务器", "Connect to FireSky server")
            return
        }

        do {
            if isUITestMockMode {
                try engine.start(workspace: workspace, transport: MockTransport())
            } else {
                let token = ServerCredentialStore.loadToken() ?? ""
                let transport = try RemoteHttpTransport(
                    urlString: remoteServerURL,
                    token: token,
                    session: remoteSession
                )
                try engine.start(workspace: workspace, transport: transport)
            }
            let hello = try await engine.call("hello")
            guard hello["protocol"] as? Int == 1 else {
                throw EngineError(message: L10n.text("服务端协议版本不匹配。", "Server protocol mismatch."))
            }
            online = true
            engine.connected = true
            regionDefinitions = hello["regions"] as? [JSONObject] ?? []
            camsConfiguration = hello["cams_credentials"] as? JSONObject
            if !didCheckStartupCredentials, let camsConfiguration {
                didCheckStartupCredentials = true
                showCAMSReminder = camsConfiguration["configured"] as? Bool != true
            }
            if isRemoteMode {
                remoteWorkspace = hello["workspace"] as? String ?? remoteWorkspace
                status = L10n.text("已连接到远程服务器", "Connected to remote server")
            } else {
                status = L10n.text("模拟模式已就绪", "Mock mode ready")
            }
            clearError()
            await loadRetentionSettings()
            await loadDataSyncSettings()
            let capabilities = Set(hello["capabilities"] as? [String] ?? [])
            if capabilities.isSuperset(of: ["task_history", "result_restore"]) {
                await restoreLatestForecastResult()
            }
            if page == .data {
                await loadInventory()
                loadTerrain()
            }
            if page == .history { loadTaskHistory() }
        } catch {
            if isRemoteMode {
                remoteWorkspace = ""
            }
            self.error = friendlyEngineMessage(error.localizedDescription)
            self.errorPage = nil
            status = L10n.text("服务器未连接", "Server offline")
        }
    }

    // MARK: - 定位与位置应用

    func applyLocation(_ point: JSONObject) {
        lat = (point["lat"] as? NSNumber)?.doubleValue ?? lat
        lon = (point["lon"] as? NSNumber)?.doubleValue ?? lon
        name = point["name"] as? String ?? "当前位置"
        timezone = point["timezone_name"] as? String ?? timezone
        elevation = (point["elevation_m"] as? NSNumber)?.doubleValue ?? elevation
        elevationSourceActual = point["source"] as? String ?? elevationSourceActual
        metadataReady = true
        if !busy { status = "位置已就绪" }
    }

    func locate() {
        guard !locating else { return }
        locating = true
        if !busy { status = "正在获取当前位置" }
        locationTimeout?.cancel()
        locationTimeout = Task {
            try? await Task.sleep(for: .seconds(20))
            guard !Task.isCancelled, locating else { return }
            locating = false
            location.stopUpdatingLocation()
            error = "定位超时，请检查系统定位权限，或在地图上选点。"
            errorPage = .point
        }
        switch location.authorizationStatus {
        case .denied, .restricted:
            locating = false
            error = "定位权限未开启，请在系统设置中允许 FireSky 使用位置。"
            errorPage = .point
        case .notDetermined: location.requestWhenInUseAuthorization()
        default: location.requestLocation()
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        guard locating else { return }
        if manager.authorizationStatus == .authorizedAlways || manager.authorizationStatus == .authorizedWhenInUse {
            manager.requestLocation()
        } else if manager.authorizationStatus == .denied || manager.authorizationStatus == .restricted {
            locating = false
            error = "定位未获授权，可继续在地图上选点。"
            errorPage = .point
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let point = locations.last, locating else { return }
        guard point.horizontalAccuracy >= 0, point.horizontalAccuracy <= 5000,
              abs(point.timestamp.timeIntervalSinceNow) <= 120 else {
            error = "定位精度不足或位置已过期，请重试。"
            errorPage = .point
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
        self.error = friendlyEngineMessage(error.localizedDescription)
        self.errorPage = .point
    }

    // MARK: - 错误隔离与生命周期

    func fail(_ error: Error, page targetPage: Page? = nil) {
        self.error = friendlyEngineMessage(error.localizedDescription)
        self.errorPage = targetPage ?? self.page
        status = Task.isCancelled ? "已取消" : "操作未完成"
    }

    func clearError() {
        error = ""
        errorPage = nil
    }

    func errorText(for targetPage: Page) -> String {
        guard !error.isEmpty else { return "" }
        guard let errorPage else { return error }
        return errorPage == targetPage ? error : ""
    }

    func saveAppearance() { UserDefaults.standard.set(appearance, forKey: "appearance") }

    func openCAMSSetup() {
        page = .settings
        settingsCategory = "数据"
        showCAMSTutorial = true
    }

    func shutdown() {
        downloadProbeTask?.cancel()
        locationTimeout?.cancel()
        operation?.cancel()
        searchTask?.cancel()
        publicationTask?.cancel()
        terrainTask?.cancel()
        terrainDownloadTask?.cancel()
        engine.stop()
    }
}

/// 引擎机器可读错误映射与内部码抑制（对齐 macOS friendlyEngineMessage）。
func friendlyEngineMessage(_ message: String) -> String {
    let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
    if text == "native-nowcast-map-publication-not-requested" {
        return ""
    }
    if text.hasPrefix("another data sync round is active in another process") {
        return "另一轮数据同步正在进行，本轮已让路；稍后会自动重试。"
    }
    switch text {
    case "another prepare/forecast is active":
        return "已有预测准备或计算正在进行，请等待完成或先取消，再发起本次操作。"
    case "data sync skipped because another prepare/forecast is active":
        return "预测准备或计算正在进行，本轮同步已让路；待当前操作完成后再同步。"
    case "another data sync is active":
        return "已有一轮数据同步正在进行，请稍候再试。"
    case "network probe is already active":
        return "网络探测正在进行，请稍候再试。"
    case "desktop operation cancelled", "nowcast preparation cancelled", "nowcast operation cancelled":
        return "操作已取消。"
    case "data cycle is unverified or older than the latest release; explicit cycle-age confirmation is required":
        return "数据周期未核验或不是最新发布，请在确认提示中选择「使用当前缓存继续」，或先更新到最新数据。"
    case "explicit confirmation is required before forecast":
        return "请先在预检窗口确认后再开始预测。"
    case "inputs are missing; prepare data before forecasting":
        return "本次预测数据不完整，请先点击「下载缺失数据」补齐。"
    case "strict input integrity check failed":
        return "数据完整性校验未通过，请重新下载本次数据后再试。"
    case "data sync is not enabled":
        return "后台数据同步未启用，请先在设置中开启。"
    case "native-report-has-no-map-cells":
        return "本次结果未生成地图格网（单点报告不含区域地图）。"
    default:
        return message
    }
}
