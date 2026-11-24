import CoreLocation
import Combine
import SwiftUI
import UIKit

/// 页面枚举：单点预测 / 数据管理 / 任务历史 / 设置。
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

/// 任务历史行（同构 macOS NativeTaskHistoryItem）。
struct MobileTaskHistoryItem: Identifiable {
    var id: String { taskID }
    let taskID: String
    let method: String
    let status: String
    let createdAt: String
    let cancelRequested: Bool
    let percent: Double?
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
        switch retryPayload?["kind"] as? String {
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
    /// 本地时区短时间（UTC ISO8601 → MM-dd HH:mm）。
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
        let payload = (retry?["payload"] as? JSONObject).flatMap { payload in
            ["point", "regional", "nowcast"].contains(retry?["kind"] as? String ?? "") ? payload : nil
        }
        let snapshot = (value["result_ref"] as? JSONObject)?["snapshot"] as? JSONObject
        let canRestore = snapshot?["contract"] as? String == "firesky-result-snapshot-v1"
            && snapshot?["sha256"] is String
        return MobileTaskHistoryItem(
            taskID: taskID, method: method, status: status, createdAt: createdAt,
            cancelRequested: value["cancel_requested"] as? Bool ?? false,
            percent: (value["progress"] as? JSONObject)?["percent"] as? Double,
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
    let engine = EngineClient(transportFactory: Store.makeTransport)
    /// 截图夹具：`FIRESKY_PAGE=data|history|settings` 指定初始页
    @Published var page: Page = {
        guard let raw = ProcessInfo.processInfo.environment["FIRESKY_PAGE"],
              let forced = Page(rawValue: raw) else { return .point }
        return forced
    }()
    @Published var name = "杭州"
    @Published var query = ""
    @Published var lat = 30.2741 { didSet { metadataReady = false } }
    @Published var lon = 120.1551 { didSet { metadataReady = false } }
    @Published var elevation = 8.0
    @Published var timezone = "Asia/Shanghai"
    @Published var metadataReady = true
    @Published var date = Date()
    @Published var phenomenon = "dusk"
    @Published var busy = false
    @Published var searching = false
    @Published var locating = false
    @Published var status = "正在连接引擎"
    @Published var error = ""
    /// 错误归属页面；nil 表示全局错误（如引擎未连接），非 nil 时仅在对应页面展示，不串屏。
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
    @Published var resultKind: Page = .point
    @Published private(set) var resultParams: JSONObject?
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
    // 任务历史（v1.6.0 历史页）
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
    private var taskResultCache: [String: JSONObject] = [:]
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
    // 保留策略 & 后台同步（对齐 macOS v1.9.10）
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
    // 设置分类（通用 / 数据 / 高级）
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
        didSet {
            L10n.language = appLanguage
        }
    }
    var appLocale: Locale {
        Locale(identifier: L10n.effective == .english ? "en_US" : "zh_CN")
    }
    @Published var engineTransport: String
    // 远程服务器模式（Mac / LAN / Cloudflare Tunnel）
    @Published var connectionMode: String
    @Published var remoteServerURL: String
    @Published var remoteTokenDraft = ""
    @Published var remoteTokenSaved: Bool
    @Published var remoteTesting = false
    @Published var remoteTestMessage = ""
    @Published var remoteTestOK = false
    @Published var remoteWorkspace = ""
    var isRemoteMode: Bool { connectionMode == "remote" }
    var remoteSession: URLSession = .shared
    private var engineConnection: AnyCancellable?
    private let location = CLLocationManager()
    private var operation: Task<Void, Never>?
    private var searchTask: Task<Void, Never>?
    private var publicationTask: Task<Void, Never>?
    private var downloadProbeTask: Task<Void, Never>?
    private var terrainTask: Task<Void, Never>?
    private var terrainDownloadTask: Task<Void, Never>?
    private var locationTimeout: Task<Void, Never>?
    private var generation = UUID()
    private var metadataGeneration = UUID()
    private(set) var requestKind: Page = .point
    private var requestPayload: JSONObject = [:]
    private var didCheckStartupCredentials = false
    private var connecting = false

    private static func makeTransport() -> EngineTransport {
        let environment = ProcessInfo.processInfo.environment
        let choice = environment["FIRESKY_TRANSPORT"]
            ?? UserDefaults.standard.string(forKey: "engineTransport")
            ?? "ffi"
        return choice == "mock" ? MockTransport() : FSMobileTransport()
    }

    override init() {
        let defaults = UserDefaults.standard
        let dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FireSky/Data", isDirectory: true)
        try? FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        workspace = dataDirectory.path
        appearance = defaults.string(forKey: "appearance") ?? "system"
        engineTransport = defaults.string(forKey: "engineTransport") ?? "ffi"
        let argumentDomain = defaults.volatileDomain(forName: UserDefaults.argumentDomain)
        if ProcessInfo.processInfo.environment["FIRESKY_TRANSPORT"] != nil
            || argumentDomain["engineTransport"] != nil,
           argumentDomain["connectionMode"] == nil {
            connectionMode = RemoteServerConfig.defaultMode
        } else {
            connectionMode = defaults.string(forKey: "connectionMode") ?? RemoteServerConfig.defaultMode
        }
        remoteServerURL = defaults.string(forKey: "remoteServerURL") ?? ""
        remoteTokenSaved = ServerCredentialStore.hasToken()
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
            if isRemoteMode {
                let token = ServerCredentialStore.loadToken() ?? ""
                let transport = try RemoteHttpTransport(
                    urlString: remoteServerURL,
                    token: token,
                    session: remoteSession
                )
                try engine.start(workspace: workspace, transport: transport)
            } else {
                try engine.start(workspace: workspace)
            }
            let hello = try await engine.call("hello")
            guard hello["protocol"] as? Int == 1 else {
                throw EngineError(message: "引擎协议版本不匹配。")
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
                status = L10n.text("已连接到远程 Mac", "Connected to remote Mac")
            } else {
                status = "引擎已连接"
            }
            clearError()
            UserDefaults.standard.set(workspace, forKey: "workspace")
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
            status = "引擎未连接"
        }
    }

    func testRemoteServer() async {
        guard !remoteTesting else { return }
        remoteTesting = true
        remoteTestMessage = ""
        remoteTestOK = false
        defer { remoteTesting = false }

        let draft = remoteTokenDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        let effectiveToken = draft.isEmpty ? (ServerCredentialStore.loadToken() ?? "") : draft
        do {
            let remoteStatus = try await RemoteHttpTransport.testConnection(
                urlString: remoteServerURL,
                token: effectiveToken,
                session: remoteSession
            )
            remoteWorkspace = remoteStatus.workspace
            remoteTestOK = true
            remoteTestMessage = remoteStatus.workspace.isEmpty
                ? L10n.text("连接成功（FireSky Mac 服务在线）。", "Connected (FireSky Mac server online).")
                : L10n.text("连接成功：\(remoteStatus.workspace)", "Connected: \(remoteStatus.workspace)")
        } catch {
            remoteTestOK = false
            remoteTestMessage = friendlyEngineMessage(error.localizedDescription)
        }
    }

    func saveAndConnectRemoteServer() async {
        guard !busy, !probingDownloads, !remoteTesting else { return }
        remoteTesting = true
        remoteTestMessage = ""
        remoteTestOK = false
        defer { remoteTesting = false }

        do {
            let normalized = try RemoteHttpTransport.normalizeBaseURL(remoteServerURL)
            remoteServerURL = normalized.absoluteString
            UserDefaults.standard.set(remoteServerURL, forKey: "remoteServerURL")

            let draft = remoteTokenDraft.trimmingCharacters(in: .whitespacesAndNewlines)
            if !draft.isEmpty {
                try ServerCredentialStore.saveToken(draft)
                remoteTokenDraft = ""
                remoteTokenSaved = true
            }

            connectionMode = "remote"
            UserDefaults.standard.set("remote", forKey: "connectionMode")
            resetModeSessionState()
            status = L10n.text("正在连接远程服务器", "Connecting to remote server")
            await connect()

            if online {
                remoteTestOK = true
                remoteTestMessage = remoteWorkspace.isEmpty
                    ? L10n.text("已连接到远程服务器。", "Connected to remote server.")
                    : L10n.text("已连接：\(remoteWorkspace)", "Connected: \(remoteWorkspace)")
                await loadInventory()
                loadTaskHistory()
                await refreshCAMSConfiguration()
                await loadRetentionSettings()
                await loadDataSyncSettings()
            } else {
                remoteTestOK = false
                remoteTestMessage = error.isEmpty
                    ? L10n.text("连接远程服务器失败。", "Failed to connect to remote server.")
                    : error
            }
        } catch {
            remoteTestOK = false
            remoteTestMessage = friendlyEngineMessage(error.localizedDescription)
        }
    }

    func switchToLocalEngine() async {
        guard !busy, !probingDownloads else { return }
        connectionMode = "local"
        UserDefaults.standard.set("local", forKey: "connectionMode")
        remoteTestMessage = ""
        remoteTestOK = false
        remoteWorkspace = ""
        resetModeSessionState()
        status = "正在连接引擎"
        await connect()
        if online {
            if page == .data {
                await loadInventory()
                loadTerrain()
            }
            if page == .history { loadTaskHistory() }
        }
    }

    func clearRemoteToken() {
        ServerCredentialStore.deleteToken()
        remoteTokenDraft = ""
        remoteTokenSaved = false
        remoteTestOK = false
        remoteTestMessage = L10n.text("已清除保存的访问令牌。", "Saved access token cleared.")
    }

    private func resetModeSessionState() {
        generation = UUID()
        session = nil
        result = nil
        inventory = []
        inventoryCategories = []
        taskHistory = []
        taskHistoryLoaded = false
        taskHistoryError = ""
        clearTaskSelection()
    }

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
            let restoredPayload = (item["retry_request"] as? JSONObject)?["payload"] as? JSONObject
            if let restoredPayload {
                applyRestoredPayload(restoredPayload)
            }
            adoptResult(response, kind: .point, params: restoredPayload)
            selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
            status = "已恢复历史结果"
            clearError()
        } catch {
            guard generation == revision, !Task.isCancelled else { return }
            self.error = "历史结果恢复失败：\(friendlyEngineMessage(error.localizedDescription))"
            self.errorPage = .point
        }
    }

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
        syncDateTab()
    }

    func search() {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !searching else { return }
        searching = true
        if !busy { status = "正在查找位置" }
        let source = elevationSource
        searchTask = Task {
            defer { searching = false }
            do {
                let point = try await engine.call(
                    "search", ElevationSourceSetting.searchParams(query: text, source: source))
                try Task.checkCancellation()
                applyLocation(point)
                query = ""
                clearError()
            } catch { fail(error, page: .point) }
        }
    }

    func selectPoint(lat: Double, lon: Double) {
        self.lat = lat
        self.lon = lon
        self.name = String(format: "地图选点 %.2f, %.2f", lat, lon)
        metadataReady = false
        lookupMetadata()
    }

    func lookupMetadata() {
        let revision = UUID()
        metadataGeneration = revision
        let a = lat, b = lon
        let source = elevationSource
        metadataReady = false
        if !busy { status = "正在获取海拔与时区" }
        Task {
            do {
                let point = try await engine.call(
                    "metadata", ElevationSourceSetting.metadataParams(lat: a, lon: b, source: source))
                guard metadataGeneration == revision, a == lat, b == lon,
                      source == elevationSource else { return }
                elevation = (point["elevation_m"] as? NSNumber)?.doubleValue ?? 0
                timezone = point["timezone_name"] as? String ?? timezone
                elevationSourceActual = point["source"] as? String ?? ""
                metadataReady = true
                if !busy { status = "位置已就绪" }
            } catch {
                guard metadataGeneration == revision else { return }
                self.error = friendlyEngineMessage(error.localizedDescription)
                self.errorPage = .point
                if !busy { status = "海拔未获取，可手动填写" }
            }
        }
    }

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

    var targetDateLabel: String { ObservationDate.label(date, timezone: timezone) }

    func payload() -> JSONObject {
        [
            "name": name, "lat": lat, "lon": lon, "elevation_m": elevation,
            "timezone_name": timezone, "target_date": targetDateLabel,
            "phenomenon": phenomenon, "model_source": "both", "workers": 1,
            "forecast_mode": "model", "satellite_source": "off",
            "ecmwf_source": ecmwfSource,
            "elevation_source": elevationSource,
        ]
    }

    func beginCheck() {
        guard !busy, online else { return }
        guard metadataReady else {
            error = "请先获取海拔与时区，或手动确认位置数据。"
            errorPage = .point
            return
        }
        generation = UUID()
        let revision = generation
        requestKind = .point
        requestPayload = payload()
        session = nil
        rows = []
        ready = false
        clearError()
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
            } catch { if generation == revision { fail(error, page: requestKind) } }
        }
    }

    private func applyReport(_ response: JSONObject) {
        let report = response["report"] as? JSONObject ?? [:]
        ready = report["ready"] as? Bool ?? false
        rows = (report["datasets"] as? [JSONObject] ?? []).map {
            let verified = $0["verified"] as? Bool ?? false
            let latest = $0["is_latest"] as? Bool ?? false
            return DataRow(
                source: $0["source"] as? String ?? "",
                ready: $0["ready"] as? Bool ?? false,
                old: !latest,
                verified: verified,
                latest: latest,
                missing: $0["missing_count"] as? Int ?? 0,
                policy: $0["policy"] as? String ?? ""
            )
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
        clearError()
        status = "正在准备数据"
        let revision = generation
        operation = Task {
            defer { if generation == revision { busy = false; checking = false } }
            do {
                let response = try await engine.call("prepare", ["session": session], progress: updateProgress)
                try Task.checkCancellation()
                guard generation == revision, showPreflight else { return }
                applyReport(response)
            } catch { if generation == revision { fail(error, page: requestKind) } }
        }
    }

    /// Mirrors the engine's `needs_cycle_age_confirmation`: a ready dataset
    /// that is unverified or not the latest release needs an explicit user
    /// acknowledgment before forecasting.
    var needsCycleAgeConfirmation: Bool {
        ready && rows.contains { $0.ready && (!$0.verified || !$0.latest) }
    }

    func confirm() {
        guard session != nil, ready, !busy else { return }
        if needsCycleAgeConfirmation {
            showCycleAgeConfirm = true
            return
        }
        runForecast(cycleAgeConfirmed: false)
    }

    func confirmCycleAgeAndForecast() {
        showCycleAgeConfirm = false
        runForecast(cycleAgeConfirmed: true)
    }

    private func runForecast(cycleAgeConfirmed: Bool) {
        guard let session, ready, !busy else { return }
        publicationTask?.cancel()
        showPreflight = false
        busy = true
        checking = false
        clearError()
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
                adoptResult(response, kind: kind, params: requestPayload)
                selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                self.session = nil
                status = "预测完成"
            } catch { if generation == revision { self.session = nil; ready = false; fail(error, page: kind) } }
        }
    }

    func updateProgress(_ event: JSONObject) {
        percent = min(100, max(0, (event["percent"] as? NSNumber)?.doubleValue ?? 0))
        let source = event["source"] as? String ?? ""
        let message = event["message"] as? String ?? ""
        if dataSyncRunning, !source.isEmpty {
            dataSyncActiveSource = source
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
        clearError()
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
            if let wrapped = response["categories"] as? JSONObject,
               let rows = wrapped["categories"] as? [JSONObject] {
                inventoryCategories = rows
            } else {
                inventoryCategories = response["categories"] as? [JSONObject] ?? []
            }
        } catch { fail(error, page: .data) }
    }

    // MARK: - 任务历史（v1.6.0 与 macOS 历史页对齐）

    func loadTaskHistory(older: Bool = false) {
        guard online, !taskHistoryLoading else { return }
        if older && (taskHistoryCursor == nil || !taskHistoryHasMore) { return }
        Task { [weak self] in
            guard let self else { return }
            taskHistoryLoading = true
            taskHistoryError = ""
            defer { taskHistoryLoading = false }
            do {
                var params: JSONObject = ["limit": 100]
                if older, let cursor = taskHistoryCursor {
                    params["before"] = cursor
                }
                let response = try await engine.call("task_history", params)
                guard response["schema"] as? String == "firesky-task-history-v1",
                      let tasks = response["tasks"] as? [JSONObject] else {
                    throw EngineError(message: "任务历史协议不匹配，请重新连接引擎。")
                }
                let parsed = tasks.compactMap(MobileTaskHistoryItem.parse)
                if Set(parsed.map(\.taskID)).count != parsed.count {
                    throw EngineError(message: "任务历史包含重复记录。")
                }
                taskHistory = older ? taskHistory + parsed : parsed
                taskHistoryTotal = (response["total"] as? NSNumber)?.intValue ?? parsed.count
                taskHistoryHasMore = response["has_more"] as? Bool ?? false
                taskHistoryCursor = response["next_cursor"] as? String
                taskHistoryLoaded = true
                if let selectedTaskID, !parsed.contains(where: { $0.taskID == selectedTaskID }) {
                    clearTaskSelection()
                }
            } catch is CancellationError {
            } catch {
                taskHistoryError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func selectTask(_ taskID: String) {
        if selectedTaskID == taskID {
            clearTaskSelection()
            return
        }
        guard taskHistory.contains(where: { $0.taskID == taskID }) else { return }
        selectedTaskID = taskID
        taskEvents = []
        taskEventsError = ""
        taskEventsLoading = true
        if let item = taskHistory.first(where: { $0.taskID == taskID }) {
            loadTaskResult(for: item)
        } else {
            resetTaskResult()
        }
        Task { [weak self] in
            guard let self else { return }
            defer { taskEventsLoading = false }
            do {
                let response = try await engine.call("task_events", ["task_id": taskID, "after_seq": 0])
                guard response["task_id"] as? String == taskID,
                      let values = response["events"] as? [JSONObject] else {
                    throw EngineError(message: "任务事件协议不匹配。")
                }
                var previous = 0
                let rows: [MobileTaskEventRow] = try values.compactMap { value in
                    let seq = (value["seq"] as? NSNumber)?.intValue ?? 0
                    guard seq > previous else { throw EngineError(message: "事件顺序无效。") }
                    previous = seq
                    guard let at = value["at_utc"] as? String, let event = value["event"] as? String else {
                        throw EngineError(message: "事件字段无效。")
                    }
                    let data = value["data"] as? JSONObject ?? [:]
                    return MobileTaskEventRow(
                        seq: seq, at: at, event: event,
                        stage: data["stage"] as? String ?? "",
                        message: data["message"] as? String ?? "",
                        percent: data["percent"] as? Double
                    )
                }
                taskEvents = rows
            } catch {
                taskEventsError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func clearTaskSelection() {
        selectedTaskID = nil
        taskEvents = []
        taskEventsError = ""
        taskEventsLoading = false
        resetTaskResult()
    }

    private func loadTaskResult(for item: MobileTaskHistoryItem) {
        guard item.method == "forecast", item.status == "done" else {
            resetTaskResult()
            return
        }
        guard taskResultTaskID != item.taskID else { return }
        taskResultTaskID = item.taskID
        if let cached = taskResultCache[item.taskID] {
            taskResult = cached
            taskResultLoading = false
            taskResultError = ""
            return
        }
        taskResult = nil
        taskResultLoading = true
        taskResultError = ""
        Task { [weak self] in
            guard let self else { return }
            defer { taskResultLoading = false }
            do {
                let response = try await engine.call("result_restore", ["task_id": item.taskID])
                guard let results = response["model_results"] as? JSONObject,
                      !results.isEmpty else {
                    throw EngineError(message: "预测详情快照不完整。")
                }
                taskResultCache[item.taskID] = response
                taskResult = response
            } catch {
                taskResultError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    private func resetTaskResult() {
        taskResultTaskID = nil
        taskResult = nil
        taskResultLoading = false
        taskResultError = ""
    }

    func setPendingForgetTask(_ taskID: String?) {
        pendingForgetTaskID = taskID
    }

    func forgetPendingTask() {
        let taskID = pendingForgetTaskID
        pendingForgetTaskID = nil
        guard let taskID, online, !busy,
              taskHistory.first(where: { $0.taskID == taskID })?.status != "running" else { return }
        Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await engine.call("task_forget", ["task_id": taskID])
                guard response["forgotten"] as? Bool == true else {
                    throw EngineError(message: "任务记录没有被删除。")
                }
                if selectedTaskID == taskID { clearTaskSelection() }
                loadTaskHistory()
            } catch {
                taskHistoryError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func restoreTaskResult(_ item: MobileTaskHistoryItem) {
        guard !busy, restoringTaskID == nil, item.canRestoreResult else { return }
        restoringTaskID = item.taskID
        Task { [weak self] in
            guard let self else { return }
            defer { restoringTaskID = nil }
            do {
                let response = try await engine.call("result_restore", ["task_id": item.taskID])
                guard response["model_results"] is JSONObject else {
                    throw EngineError(message: "已发布结果格式无效。")
                }
                if let payload = item.retryPayload {
                    applyRestoredPayload(payload)
                }
                adoptResult(response, kind: .point, params: item.retryPayload)
                page = .point
                selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                status = "已恢复历史结果"
                clearError()
            } catch {
                taskHistoryError = friendlyEngineMessage(error.localizedDescription)
            }
        }
    }

    func restoreTaskParameters(_ item: MobileTaskHistoryItem) {
        guard !busy, restoringTaskID == nil, let payload = item.retryPayload, online else { return }
        applyRestoredPayload(payload)
        page = .point
        beginCheck()
    }

    // MARK: - 数据管理（手动删除，v1.3.0 macOS 契约）

    func beginDataDelete(source: String, cycle: String?) {
        guard online, !busy, !dataDeleteRunning, !dataDeletePreviewing else { return }
        var scope: JSONObject = ["source": source]
        if let cycle { scope["cycle"] = cycle }
        pendingDataDelete = scope
        dataDeletePreview = nil
        dataDeleteMessage = ""
        dataDeleteFailed = false
        dataDeletePreviewing = true
        Task { [weak self] in
            guard let self else { return }
            defer { dataDeletePreviewing = false }
            do {
                dataDeletePreview = try await engine.call("data_delete_preview", ["scope": scope])
            } catch {
                pendingDataDelete = nil
                dataDeleteMessage = friendlyEngineMessage(error.localizedDescription)
                dataDeleteFailed = true
            }
        }
    }

    func cancelDataDelete() {
        pendingDataDelete = nil
        dataDeletePreview = nil
    }

    func executeDataDelete() {
        guard let scope = pendingDataDelete,
              let preview = dataDeletePreview,
              let previewID = preview["preview_id"] as? String,
              online, !dataDeleteRunning else { return }
        dataDeleteRunning = true
        Task { [weak self] in
            guard let self else { return }
            defer { dataDeleteRunning = false }
            do {
                let result = try await engine.call("data_delete_execute", [
                    "scope": scope, "preview_id": previewID, "confirm": true,
                ])
                pendingDataDelete = nil
                dataDeletePreview = nil
                let deleted = (result["deleted"] as? NSNumber)?.intValue ?? 0
                let skipped = (result["skipped"] as? [Any])?.count ?? 0
                let failed = (result["failed"] as? [Any])?.count ?? 0
                let mb = (result["freed_mb"] as? NSNumber)?.doubleValue ?? 0
                dataDeleteMessage = "已删除 \(deleted) 个文件，释放 \(String(format: "%.1f", mb)) MB"
                    + (skipped > 0 ? "；占用中跳过 \(skipped) 个" : "")
                    + (failed > 0 ? "；失败 \(failed) 个" : "")
                dataDeleteFailed = failed > 0
                await loadInventory()
            } catch {
                dataDeletePreview = nil
                dataDeleteMessage = friendlyEngineMessage(error.localizedDescription)
                dataDeleteFailed = true
            }
        }
    }

    // MARK: - 地形区域下载（v1.4.0 macOS 契约）

    func loadTerrain() {
        guard online, !terrainLoading, !terrainDownloading else { return }
        terrainTask?.cancel()
        terrainTask = Task { [weak self] in
            guard let self else { return }
            terrainLoading = true
            defer {
                terrainLoading = false
                terrainTask = nil
            }
            do {
                terrain = try await engine.call("terrain_inventory", ["lat": lat, "lon": lon])
            } catch is CancellationError {
            } catch {
                terrain = nil
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
        }
    }

    func loadTerrainEstimate() {
        guard online, !terrainDownloading, !terrainEstimateLoading else { return }
        terrainEstimateLoading = true
        var params: JSONObject = ["estimate_global": true, "lat": lat, "lon": lon]
        if let bounds = terrainRegion.bounds {
            params["bounds"] = bounds
        }
        Task { [weak self] in
            guard let self else { return }
            defer { terrainEstimateLoading = false }
            do {
                terrainEstimate = try await engine.call("terrain_inventory", params)
                showTerrainConfirm = terrainEstimate != nil
            } catch {
                terrainEstimate = nil
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
        }
    }

    func dismissTerrainConfirm() {
        showTerrainConfirm = false
    }

    func downloadTerrainRegion() {
        downloadTerrain(region: terrainRegion, tiles: nil)
    }

    func retryFailedTerrainTiles() {
        let region = terrainLastFailedRegion ?? terrainRegion
        let tiles = terrainLastFailedTiles.isEmpty ? nil : terrainLastFailedTiles
        downloadTerrain(region: region, tiles: tiles)
    }

    func downloadTerrain(region: TerrainRegion, tiles: [String]? = nil) {
        guard online, !busy, !terrainDownloading else { return }
        terrainDownloading = true
        terrainCancelling = false
        terrainMessage = ""
        terrainProgress = [:]
        showTerrainConfirm = false
        var params: JSONObject = ["mode": "global", "timeout_seconds": 300.0]
        if let tiles, !tiles.isEmpty {
            params["tiles"] = tiles
        } else if let bounds = region.bounds {
            params["bounds"] = bounds
        }
        terrainDownloadTask = Task { [weak self] in
            guard let self else { return }
            defer {
                terrainDownloading = false
                terrainCancelling = false
                terrainDownloadTask = nil
            }
            do {
                let result = try await engine.call("terrain_download", params, progress: { [weak self] event in
                    guard let self, let terrain = event["terrain"] as? JSONObject else { return }
                    self.terrainProgress = terrain
                })
                try Task.checkCancellation()
                let downloaded = (result["downloaded"] as? NSNumber)?.intValue ?? 0
                let absent = (result["absent"] as? NSNumber)?.intValue ?? 0
                let failed = (result["failed"] as? NSNumber)?.intValue ?? 0
                let failedTiles = result["failed_tiles"] as? [String] ?? []
                terrainLastFailedCount = failed
                terrainLastFailedRegion = failed > 0 ? region : nil
                terrainLastFailedTiles = failed > 0 ? failedTiles : []
                let gb = (result["received_gb"] as? NSNumber)?.doubleValue ?? 0
                let summary = "新增 \(downloaded) 块、缺失 \(absent) 块、失败 \(failed) 块，占用 \(String(format: "%.1f", gb)) GB。"
                switch result["status"] as? String ?? "" {
                case "cancelled":
                    terrainMessage = (result["stopped_reason"] as? String == "disk_space"
                        ? "\(region.rawValue)下载因磁盘空间不足停止：" : "\(region.rawValue)下载已停止：") + summary
                    terrainMessageFailed = result["stopped_reason"] as? String == "disk_space"
                default:
                    terrainMessage = (failed > 0 ? "\(region.rawValue)下载完成（部分失败）：" : "\(region.rawValue)下载完成：") + summary
                    terrainMessageFailed = failed > 0
                }
            } catch is CancellationError {
                terrainMessage = "已停止下载；已完成的瓦片保留，重新开始会跳过它们。"
                terrainMessageFailed = false
            } catch {
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
            loadTerrain()
        }
    }

    func downloadTerrainPoint() {
        guard online, !busy, !terrainDownloading else { return }
        terrainDownloading = true
        terrainCancelling = false
        terrainMessage = ""
        terrainProgress = [:]
        terrainDownloadTask = Task { [weak self] in
            guard let self else { return }
            defer {
                terrainDownloading = false
                terrainCancelling = false
                terrainDownloadTask = nil
            }
            do {
                let result = try await engine.call("terrain_download",
                                                   ["lat": lat, "lon": lon, "timeout_seconds": 600.0])
                try Task.checkCancellation()
                switch result["status"] as? String ?? "" {
                case "exists":
                    terrainMessage = "地形瓦片已存在，直接复用本地数据。"
                    terrainMessageFailed = false
                case "downloaded":
                    terrainMessage = "已下载地形瓦片 \(result["tile"] as? String ?? "")。"
                    terrainMessageFailed = false
                case "out_of_coverage":
                    terrainMessage = result["message"] as? String ?? "该位置不在 SRTM 覆盖范围内。"
                    terrainMessageFailed = true
                default:
                    terrainMessage = "下载未完成。"
                    terrainMessageFailed = true
                }
            } catch is CancellationError {
                terrainMessage = "已停止下载。"
                terrainMessageFailed = false
            } catch {
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
            loadTerrain()
        }
    }

    func cancelTerrainDownload() {
        guard terrainDownloading else { return }
        terrainCancelling = true
        terrainDownloadTask?.cancel()
    }

    // MARK: - 保留策略与后台同步（v1.9.10 macOS 对齐）

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
            retentionMessage = friendlyEngineMessage(error.localizedDescription)
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
                retentionMessage = retentionConfigured
                    ? L10n.text("保留设置已保存；可点击「立即按策略清理」，或等待下一次成功准备后自动清理。",
                                "Retention policy saved; tap Clean up now or wait for the next prepare.")
                    : L10n.text("已清空保留设置。", "Retention policy cleared.")
                retentionSaveFailed = false
                await refreshDataSyncState()
            } catch {
                retentionMessage = friendlyEngineMessage(error.localizedDescription)
                retentionSaveFailed = true
            }
        }
    }

    func applyRetentionNow() {
        guard online, !busy, !savingRetention, !applyingRetention else { return }
        applyingRetention = true
        retentionMessage = ""
        retentionSaveFailed = false
        Task {
            defer { applyingRetention = false }
            do {
                let response = try await engine.call("apply_retention_settings")
                let configured = response["configured"] as? Bool ?? false
                guard configured, let result = response["result"] as? JSONObject else {
                    retentionMessage = L10n.text("尚未保存任何保留规则，未执行清理。",
                                                 "No retention rules saved; nothing to clean.")
                    retentionSaveFailed = false
                    return
                }
                let removed = (result["removed_count"] as? NSNumber)?.intValue ?? 0
                let freedMB = (result["freed_mb"] as? NSNumber)?.doubleValue ?? 0
                let deferred = (result["deferred_count"] as? NSNumber)?.intValue ?? 0
                let failed = (result["failed_count"] as? NSNumber)?.intValue ?? 0
                var parts: [String] = []
                if removed > 0 {
                    parts.append(L10n.text(
                        String(format: "已清理 %d 项旧数据，释放 %.1f MB", removed, freedMB),
                        String(format: "Cleaned %d old items, freed %.1f MB", removed, freedMB)
                    ))
                } else {
                    parts.append(L10n.text("当前没有超出保留策略的旧数据", "No old data exceeded the retention policy"))
                }
                if deferred > 0 {
                    parts.append(L10n.text("跳过 \(deferred) 项占用中文件", "skipped \(deferred) busy items"))
                }
                if failed > 0 {
                    parts.append(L10n.text("\(failed) 项清理失败", "\(failed) failed"))
                }
                retentionMessage = parts.joined(separator: " · ") + "。"
                retentionSaveFailed = failed > 0
                await loadInventory()
            } catch {
                retentionMessage = friendlyEngineMessage(error.localizedDescription)
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

    func loadDataSyncSettings() async {
        guard online else { return }
        do {
            let response = try await engine.call("data_sync_settings")
            applyDataSyncStatus(response)
        } catch {
            dataSyncMessage = friendlyEngineMessage(error.localizedDescription)
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
                "ecmwf_source": ecmwfSource,
            ],
        ]
        Task {
            defer { savingDataSync = false }
            do {
                let response = try await engine.call("save_data_sync_settings", payload)
                applyDataSyncStatus(response)
                dataSyncMessage = dataSyncEnabled
                    ? L10n.text("后台同步已保存。", "Background sync saved.")
                    : L10n.text("后台同步已关闭。", "Background sync disabled.")
                dataSyncSaveFailed = false
            } catch {
                dataSyncMessage = friendlyEngineMessage(error.localizedDescription)
                dataSyncSaveFailed = true
            }
        }
    }

    private var dataSyncEnabledSources: [String] {
        [("cams", dataSyncCAMS), ("gfs", dataSyncGFS), ("ec", dataSyncEC)]
            .filter { $0.1 }.map { $0.0 }
    }

    func refreshDataSyncState() async {
        guard online, let response = try? await engine.call("data_sync_settings") else { return }
        dataSyncRetentionConfigured = response["retention_configured"] as? Bool ?? dataSyncRetentionConfigured
        dataSyncState = (response["state"] as? JSONObject)?["sources"] as? JSONObject ?? dataSyncState
    }

    private func runDataSyncRound(sources: [String], force: Bool = false) async -> (response: JSONObject?, error: Error?) {
        dataSyncRunning = true
        dataSyncProgress = [:]
        dataSyncActiveSource = ""
        defer {
            dataSyncRunning = false
            dataSyncActiveSource = ""
        }
        var response: JSONObject?
        var roundError: Error?
        var params: JSONObject = ["sources": sources]
        if force { params["force"] = true }
        do {
            response = try await engine.call("data_sync_run", params, progress: { [weak self] event in
                guard let self, !self.busy else { return }
                self.updateProgress(event)
            })
        } catch {
            roundError = error
        }
        await refreshDataSyncState()
        return (response, roundError)
    }

    func runDataSyncNow() {
        guard online, !busy, !probingDownloads, !dataSyncRunning, !savingDataSync else { return }
        let sources = dataSyncEnabledSources
        guard dataSyncEnabled, !sources.isEmpty else {
            dataSyncMessage = L10n.text("请先在设置中启用后台数据同步并至少选择一个数据源。",
                                        "Enable background sync and select at least one source first.")
            dataSyncSaveFailed = true
            return
        }
        status = "正在立即同步后台数据"
        Task {
            let round = await runDataSyncRound(sources: sources, force: true)
            guard !busy else { return }
            if let error = round.error {
                dataSyncMessage = friendlyEngineMessage(error.localizedDescription)
                dataSyncSaveFailed = true
                status = "立即同步未完成"
            } else {
                let summary = round.response.map(dataSyncRoundSummary) ?? "立即同步未完成，详见任务历史。"
                status = summary
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

    // MARK: - 错误隔离与结果参数追踪

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

    func adoptResult(_ response: JSONObject, kind: Page, params: JSONObject?) {
        resultParams = params
        result = response
        resultKind = kind
    }

    var resultParametersStale: Bool {
        guard result != nil, resultKind == page, let params = resultParams else { return false }
        func sameString(_ key: String, _ current: String) -> Bool {
            guard let saved = params[key] as? String else { return true }
            return saved == current
        }
        func sameNumber(_ key: String, _ current: Double) -> Bool {
            guard let saved = (params[key] as? NSNumber)?.doubleValue else { return true }
            return abs(saved - current) < 1e-9
        }
        if !sameString("target_date", targetDateLabel) { return true }
        if !sameString("phenomenon", phenomenon) { return true }
        if !sameString("ecmwf_source", ecmwfSource) { return true }
        if !sameNumber("lat", lat) || !sameNumber("lon", lon) { return true }
        if !sameNumber("elevation_m", elevation) { return true }
        if !sameString("timezone_name", timezone) { return true }
        return false
    }

    var resultTitle: String {
        if let params = resultParams, result != nil, resultKind == page {
            let name = params["name"] as? String ?? name
            let date = params["target_date"] as? String ?? targetDateLabel
            return "\(name) · \(date)"
        }
        return "\(name) · \(targetDateLabel)"
    }

    var resultPhenomenonLabel: String {
        let value = (result != nil && resultKind == page ? resultParams?["phenomenon"] as? String : nil) ?? phenomenon
        return value == "dawn" ? "朝霞" : "晚霞"
    }

    var resultParamsSummary: String {
        guard let params = resultParams else { return "" }
        var parts: [String] = []
        if let value = params["name"] as? String { parts.append("地点：\(value)") }
        if let lat = (params["lat"] as? NSNumber)?.doubleValue,
           let lon = (params["lon"] as? NSNumber)?.doubleValue {
            parts.append(String(format: "坐标：%.4f, %.4f", lat, lon))
        }
        if let value = params["target_date"] as? String { parts.append("日期：\(value)") }
        if let value = params["phenomenon"] as? String {
            parts.append("现象：\(value == "dawn" ? "朝霞" : "晚霞")")
        }
        return "结果原始参数 — " + parts.joined(separator: "；")
    }

    private func syncDateTab() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        if calendar.isDateInToday(date) {
            dateTab = "今天"
        } else if calendar.isDateInTomorrow(date) {
            dateTab = "明天"
        } else {
            dateTab = "自定义"
        }
    }

    func saveAppearance() { UserDefaults.standard.set(appearance, forKey: "appearance") }

    var camsConfigured: Bool { camsConfiguration?["configured"] as? Bool == true }
    var canSaveCAMS: Bool { camsConfiguration?["can_save"] as? Bool == true }
    var camsConfigurationDescription: String {
        guard let camsConfiguration else { return L10n.text("正在检查本地配置", "Checking local configuration") }
        switch camsConfiguration["state"] as? String {
        case "configured": return L10n.text("已检测到 Key，下载时验证授权。", "Key detected; authorization verified on download.")
        case "wrong_endpoint": return L10n.text("接口地址不是 Copernicus ADS，请重新配置。", "Endpoint is not Copernicus ADS; please reconfigure.")
        case "tls_disabled": return L10n.text("配置关闭了 TLS 验证，请重新保存以启用安全连接。", "TLS verification disabled; save key again to enable.")
        case "unreadable": return L10n.text("配置文件无法读取或格式有误，请修正后重新检查。", "Configuration unreadable; please fix and re-check.")
        default: return L10n.text("尚未配置 CAMS Key，本地数据缺失时无法下载。", "CAMS key not configured; cannot download missing data.")
        }
    }

    func openCAMSSetup() {
        page = .settings
        settingsCategory = "数据"
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
        if isRemoteMode {
            camsMessage = L10n.text(
                "远程模式下不支持通过网络修改 Mac 的 CAMS Key，请在 Mac 端本地配置。",
                "Saving CAMS Key remotely is disabled for security; configure it locally on your Mac."
            )
            camsSaveFailed = true
            return
        }
        guard online, !busy, !savingCAMS, !probingDownloads, canSaveCAMS else { return }
        savingCAMS = true
        camsMessage = ""
        let key = camsKeyDraft
        Task {
            defer { savingCAMS = false }
            do {
                camsConfiguration = try await engine.call("save_cams_key", ["key": key])
                if camsKeyDraft == key { camsKeyDraft = "" }
                if let index = downloadProbeRows.firstIndex(where: { $0.source == "cams" }) {
                    downloadProbeRows[index] = DownloadProbeRow.initial[index]
                }
                downloadProbeDate = nil
                camsMessage = "已保存到本机配置文件。"
                camsSaveFailed = false
            } catch {
                camsMessage = friendlyEngineMessage(error.localizedDescription)
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
                downloadProbeError = cancelled
                    ? "探测已取消；已完成的结果保留。"
                    : friendlyEngineMessage(error.localizedDescription)
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
        guard metadataReady else {
            error = "请先确认海拔与时区。"
            errorPage = .point
            return
        }
        favorites.removeAll { ($0["lat"] as? Double) == lat && ($0["lon"] as? Double) == lon }
        favorites.append(["name": name, "lat": lat, "lon": lon,
                          "elevation_m": elevation, "timezone_name": timezone])
        UserDefaults.standard.set(favorites, forKey: "favorites")
    }

    func removeFavorite(_ favorite: JSONObject) {
        let favLat = (favorite["lat"] as? NSNumber)?.doubleValue
        let favLon = (favorite["lon"] as? NSNumber)?.doubleValue
        favorites.removeAll {
            ($0["lat"] as? NSNumber)?.doubleValue == favLat
                && ($0["lon"] as? NSNumber)?.doubleValue == favLon
        }
        UserDefaults.standard.set(favorites, forKey: "favorites")
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
