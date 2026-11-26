import AppKit
import CoreLocation
import Combine
import SwiftUI

enum Page: String, CaseIterable, Identifiable {
    case point, regional, nowcast, data, history, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .point: L10n.text("单点预测", "Point Forecast")
        case .regional: L10n.text("区域预测", "Regional Forecast")
        case .nowcast: L10n.text("实况观测", "Nowcast")
        case .data: L10n.text("数据管理", "Data Management")
        case .history: L10n.text("任务历史", "Task History")
        case .settings: L10n.text("设置", "Settings")
        }
    }
    var symbol: String {
        switch self {
        case .point: "location.circle"
        case .regional: "map"
        case .nowcast: "scope"
        case .data: "externaldrive"
        case .history: "clock.arrow.circlepath"
        case .settings: "gearshape"
        }
    }
    /// 需要地点上下文副标题的页面；管理页只显示标题本身，不借用预测地点。
    var showsLocationSubtitle: Bool {
        switch self {
        case .point, .nowcast: true
        case .regional: true
        case .data, .history, .settings: false
        }
    }
}

/// 预测详情快照读取状态（v1.9.5 任务详情 → 预测详情）。
enum TaskResultLoadState {
    case idle
    case loading
    case loaded
    /// 快照过期/被清理/校验失败——附带可读原因。
    case unavailable(String)
}

/// 地形下载区域预设（用户口径：按区域下载而非单点；全球是其中一个预设）。
/// bounds 为 1° 网格含边界的覆盖矩形，直接传给引擎 bulk 契约。
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
    let engine = EngineClient()
    @Published var page: Page = .point { didSet { sessionChanged() } }
    // UX #3（v1.1.1）：参数变更不再清空已有结果；旧结果保留并标注其原始参数
    // （resultParams + resultParametersStale），新预测成功后才替换。
    // 失败/取消不覆盖上一份有效结果的基线约束不变。
    @Published var name = "杭州" { didSet { sessionChanged() } }
    @Published var query = ""
    @Published var lat = 30.2741 { didSet { metadataReady = false; sessionChanged() } }
    @Published var lon = 120.1551 { didSet { metadataReady = false; sessionChanged() } }
    @Published var elevation = 8.0 { didSet { sessionChanged() } }
    @Published var timezone = "Asia/Shanghai" { didSet { sessionChanged() } }
    @Published var metadataReady = true { didSet { sessionChanged() } }
    @Published var date = Date() { didSet { sessionChanged() } }
    @Published var phenomenon = "dusk" { didSet { sessionChanged() } }
    @Published var west = 119.5 { didSet { sessionChanged() } }
    @Published var east = 120.5 { didSet { sessionChanged() } }
    @Published var south = 29.75 { didSet { sessionChanged() } }
    @Published var north = 30.75 { didSet { sessionChanged() } }
    @Published var busy = false
    /// UX #5：地名搜索是轻量任务，使用独立状态，不占用全局 busy。
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
    /// UX #1/#3：当前结果对应的原始请求参数（预测成功或历史恢复时写入）。
    /// 标题与「参数已变更」徽标都以它为准，而不是当前表单。
    @Published private(set) var resultParams: JSONObject?
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
    /// 区域渲染平滑过渡开关：默认开启，采用 Web Mercator 对齐双线性平滑热力图并严格保留缺测透明。
    @Published var showRegionalSmooth = true
    /// 采样格点叠加层开关（regional-6）：复用 points[]，上限截断，与网格线开关并存。
    @Published var showRegionalSamples = false
    @Published var regionalDisplay = "gfs" {
        didSet { if regionalDisplay != "compare" { selectedModel = regionalDisplay } }
    }
    /// 区域预测一次只跑一个模式（GFS 或 EC），选择器与展示共用同一状态。
    var regionalModel: String {
        get { regionalDisplay == "ec" ? "ec" : "gfs" }
        set { regionalDisplay = newValue == "ec" ? "ec" : "gfs" }
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
            downloadProbeDate = nil
            downloadProbeRows = DownloadProbeRow.initial
            downloadProbeError = ""
        }
    }
    /// 海拔来源三档（v1.1.1 引擎契约）：auto = HTTP 优先、SRTM 兜底；
    /// http = 仅 open-meteo；srtm = 本地时区 + SRTM 瓦片。所有 metadata
    /// 调用与预测 payload 透传；响应 source 字段用于展示实际来源。
    @Published var elevationSource = "auto" {
        didSet {
            guard ["auto", "http", "srtm"].contains(elevationSource) else {
                elevationSource = "auto"
                return
            }
            guard sessionPersistenceEnabled else { return }
            UserDefaults.standard.set(elevationSource, forKey: "elevationSource")
        }
    }
    /// 最近一次 metadata 响应里的实际来源（open-meteo-point-metadata / local-timezone+srtm）。
    @Published var elevationSourceActual = ""
    var elevationSourceLabel: String {
        switch elevationSource {
        case "http": return "仅 HTTP（open-meteo）"
        case "srtm": return "SRTM 下载（本地时区 + 瓦片）"
        default: return "自动（HTTP 优先，SRTM 兜底）"
        }
    }
    var elevationSourceActualLabel: String {
        switch elevationSourceActual {
        case "open-meteo-point-metadata": return "open-meteo（HTTP）"
        case "local-timezone+srtm": return "本地时区 + SRTM"
        case "": return "未获取"
        default: return elevationSourceActual
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
    @Published var selectedRegions: Set<String> = ["china"] { didSet { sessionChanged() } }
    @Published var defaultRegionalPreset: [String] = ["china"]
    @Published var customRegion = false { didSet { sessionChanged() } }
    @Published var workspace: String
    @Published var engineBinary: String
    @Published var appearance: String
    @Published var appLanguage: L10n.Language = L10n.language {
        didSet {
            if !isSnapshotRun { L10n.language = appLanguage }
        }
    }
    /// 独立软件更新弹窗状态（设置页与顶栏更新提示共用）。
    @Published var showUpdateSheet = false
    /// 上一次区域地形下载失败瓦片数与具体瓦片名，便于提供直达定向重试入口。
    @Published var terrainLastFailedCount = 0
    @Published var terrainLastFailedRegion: TerrainRegion? = nil
    @Published var terrainLastFailedTiles: [String] = []
    var appLocale: Locale {
        let effective: L10n.Language
        switch appLanguage {
        case .chinese, .english: effective = appLanguage
        case .system:
            let preferred = Locale.preferredLanguages.first ?? "zh"
            effective = preferred.hasPrefix("zh") ? .chinese : .english
        }
        return Locale(identifier: effective == .english ? "en_US" : "zh_CN")
    }
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
    /// 设置当前分类；CAMS 提醒、数据页跳转共用同一落位，避免各处自持状态。
    @Published var settingsCategory: SettingsCategory = .data
    /// UX #11：卸载后台助手是破坏性操作，确认弹窗由设置数据分区持有状态。
    @Published var confirmUninstallHelper = false
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
    /// 当前正在执行同步的单一数据源名（用于避免已完成源停留在中间进度百分比）。
    @Published var dataSyncActiveSource = ""
    /// 后台同步助手（launchd LaunchAgent）安装/运行状态与上次运行结果。
    @Published var helperAvailable = false
    @Published var helperInstalled = false
    @Published var helperLoaded = false
    /// v1.9.8 一致性校验：已安装 plist 的工作区与当前数据目录不一致。
    @Published var helperWorkspaceMismatch = false
    @Published var helperBusy = false
    @Published var helperMessage = ""
    @Published var helperMessageFailed = false
    @Published var helperStatus: JSONObject = [:]
    @Published var taskHistory: [NativeTaskHistoryItem] = []
    /// 数据布局迁移（方案 §8 阶段 B）：Legacy(.cache) → 可见目录。
    @Published var layout: JSONObject?
    @Published var layoutLoading = false
    @Published var layoutMessage = ""
    @Published var layoutMessageFailed = false
    @Published var layoutMigrating = false
    @Published var showLayoutConfirm = false
    /// 手动数据删除（方案 5.2）：引擎预览 → 确认 → 执行，对象身份由引擎冻结。
    @Published var pendingDataDelete: JSONObject?
    @Published var dataDeletePreview: JSONObject?
    @Published var dataDeletePreviewing = false
    @Published var dataDeleteRunning = false
    @Published var dataDeleteMessage = ""
    @Published var dataDeleteFailed = false
    /// SRTM 地形下载（方案 7）：独立契约，不附着 metadata。下载动作按用户
    /// 口径为全球批量（跳过已有、海洋瓦片自动缺失、可取消续传）。
    @Published var terrain: JSONObject?
    @Published var terrainLoading = false
    @Published var terrainDownloading = false
    @Published var terrainCancelling = false
    @Published var terrainMessage = ""
    @Published var terrainMessageFailed = false
    @Published var terrainProgress: JSONObject = [:]
    /// 全球/区域下载确认所需的估算（候选数/已有/体积抽样估算）。
    @Published var terrainEstimate: JSONObject?
    @Published var terrainEstimateLoading = false
    @Published var showGlobalTerrainConfirm = false
    /// 当前选择的下载区域预设（默认中国）。
    @Published var terrainRegion: TerrainRegion = .china
    @Published var taskHistoryTotal = 0
    @Published var taskHistoryIssues = 0
    @Published var taskHistoryLoaded = false
    @Published var taskHistoryLoading = false
    @Published var taskHistoryError = ""
    /// 分页（v1.8.1 可用性）：has_more 时提供「加载更早任务」。
    @Published var taskHistoryHasMore = false
    @Published var taskHistoryCursor: String?
    @Published var selectedTaskHistoryID: String?
    @Published var taskHistoryEvents: [NativeTaskEvent] = []
    @Published var taskHistoryEventsLoading = false
    @Published var taskHistoryEventsError = ""
    @Published var taskHistoryEventsTruncated = false
    @Published var forgettingTaskHistoryID: String?
    @Published var restoringTaskHistoryID: String?
    @Published var pendingForgetTaskID: String?
    /// v1.9.5 任务详情 → 预测详情：选中已完成的预测任务时读取结果快照。
    @Published var taskResultTaskID: String?
    @Published var taskResult: JSONObject?
    @Published var taskResultState: TaskResultLoadState = .idle
    private var taskResultTask: Task<Void, Never>?
    private var taskResultCache: [String: JSONObject] = [:]
    private var engineConnection: AnyCancellable?
    /// 设置页同步状态轮询（v1.9.6）。
    private var syncRefreshTimer: AnyCancellable?
    private let location = CLLocationManager()
    private var operation: Task<Void, Never>?
    private var searchTask: Task<Void, Never>?
    private var publicationTask: Task<Void, Never>?
    private var regionalProfileTask: Task<Void, Never>?
    private var frozenProfileTask: Task<Void, Never>?
    private var downloadProbeTask: Task<Void, Never>?
    private var taskHistoryTask: Task<Void, Never>?
    private var terrainTask: Task<Void, Never>?
    private var terrainDownloadTask: Task<Void, Never>?
    private var taskHistoryEventsTask: Task<Void, Never>?
    /// 60 秒心跳（设计 5.1/11.3）：唯一节拍源，调度决策全部在引擎。
    private var syncTickTask: Task<Void, Never>?
    private var locationTimeout: Task<Void, Never>?
    private var generation = UUID()
    private var metadataGeneration = UUID()
    private(set) var requestKind: Page = .point
    private var requestPayload: JSONObject = [:]
    private var didCheckStartupCredentials = false
    var didScheduleAutoUpdateCheck = false
    /// UX #7：会话持久化（上次地点/日期/模式）。快照/截图夹具运行不写用户默认值。
    private var sessionSaveTask: Task<Void, Never>?
    private var restoringSession = false
    var sessionPersistenceEnabled = ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] == nil
    /// 快照/截图夹具运行：退出确认等交互护栏在此模式下自动放行。
    let isSnapshotRun = ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] != nil

    override init() {
        let env = ProcessInfo.processInfo.environment
        let defaults = UserDefaults.standard
        let bundledEngine = Bundle.main.resourceURL?.appendingPathComponent("firesky-engine").path
        let isBundled = bundledEngine.map { FileManager.default.isExecutableFile(atPath: $0) } ?? false
        let dataDirectory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FireSky/Data", isDirectory: true)
        let defaultWorkspace = isBundled ? dataDirectory.path : FileManager.default.currentDirectoryPath
        let explicitWorkspace = env["FIRESKY_WORKSPACE"]
        // v1.8.1 用户裁定：数据目录固定在系统默认位置，UI 不再提供设置。
        // UserDefaults 里旧版本遗留的 workspace 值一律忽略（旧行为会把
        // 走查/临时目录写进 defaults 并长期覆盖系统默认）。
        let root: String
        if let explicitWorkspace {
            root = explicitWorkspace
        } else {
            root = defaultWorkspace
        }
        if isBundled && !FileManager.default.fileExists(atPath: root) {
            try? FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        }
        workspace = root
        // v1.9.6：启动时清理其它工作区的杂散 helper 轮（仅正式安装包——
        // 源码运行环境的测试工作区轮不归 GUI 管）。陈旧 launchd plist 指向
        // 旧 workspace 时，杂散轮会与真实轮并行抢带宽（走查实例：/tmp
        // 测试工作区全源同步 56 min）。正式 helper 被 SIGTERM 后由 launchd
        // 在下个 tick 自动拉起，只有杂散轮真正消失。
        if isBundled {
            let expectedWorkspace = root
            Task.detached(priority: .utility) {
                SyncHelperInstaller.terminateStaleHelpers(expectedWorkspace: expectedWorkspace)
            }
        }
        engineBinary = env["FIRESKY_RUST_ENGINE_BIN"] ?? defaults.string(forKey: "engineBinary") ?? ""
        appearance = env["FIRESKY_NATIVE_APPEARANCE"] ?? defaults.string(forKey: "appearance") ?? "system"
        ecmwfSource = defaults.string(forKey: "ecmwfSource") ?? "ecmwf"
        let savedDefaultRegions = defaults.stringArray(forKey: "defaultRegionalPreset") ?? ["china"]
        let resolvedDefaultRegions = savedDefaultRegions.isEmpty ? ["china"] : savedDefaultRegions
        defaultRegionalPreset = resolvedDefaultRegions
        selectedRegions = Set(resolvedDefaultRegions)
        let savedElevationSource = defaults.string(forKey: "elevationSource") ?? "auto"
        elevationSource = ["auto", "http", "srtm"].contains(savedElevationSource) ? savedElevationSource : "auto"
        super.init()
        engineConnection = engine.$connected.sink { [weak self] value in self?.online = value }
        // v1.9.6：设置页可见时每 15s 轮询同步状态与后台助手状态——后台
        // 轮次、CAMS 补齐与熔断变化不再依赖手动刷新；手动会话（busy）期间
        // 跳过，避免覆盖进行中的操作文案。
        syncRefreshTimer = Timer.publish(every: 15, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                guard let self, self.page == .settings, self.online,
                      !self.busy, !self.dataSyncRunning else { return }
                Task { await self.refreshDataSyncState() }
                self.refreshHelperStatus()
            }
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
        restoreSession()
        location.delegate = self
        location.desiredAccuracy = kCLLocationAccuracyHundredMeters
        if let page = env["FIRESKY_NATIVE_PAGE"].flatMap(Page.init(rawValue:)) {
            self.page = page
        }
    }

    func connect() async {
        guard !online, !busy, !probingDownloads else { return }
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
                // Snapshot smoke renders the cold-start workbench; the modal
                // CAMS onboarding alert can hang the main thread on runner
                // images with broken IconServices (Intel macOS 26.6.x nib
                // icon loading), starving the snapshot task of main-actor time.
                let snapshotSmoke =
                    ProcessInfo.processInfo.environment["FIRESKY_NATIVE_SNAPSHOT"] != nil
                showCAMSReminder =
                    !snapshotSmoke && camsConfiguration["configured"] as? Bool != true
            }
            await loadRetentionSettings()
            await loadDataSyncSettings()
            refreshHelperStatus()
            startSyncTick()
            status = "引擎已连接"
            if page == .data {
                await loadInventory()
                loadTerrain()
            }
            if page == .data || page == .history {
                loadTaskHistory()
            }
        } catch { self.error = error.localizedDescription; self.errorPage = nil; status = "引擎未连接" }
    }

    func search() {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !searching else { return }
        // UX #5：搜索不占用全局 busy，不锁死切页与表单；独立任务句柄，
        // 避免覆盖预测任务的取消句柄。
        searching = true
        if !busy { status = "正在查找位置" }
        searchTask = Task {
            defer { searching = false }
            do {
                let point = try await engine.call("search", ["query": text])
                try Task.checkCancellation()
                applyLocation(point)
                query = ""
            } catch { fail(error) }
        }
    }

    func selectPoint(lat: Double, lon: Double) {
        // UX #5：预测进行中仍可编辑位置表单；运行中的预测使用启动时的参数快照。
        self.lat = lat
        self.lon = lon
        // UX #6：带坐标命名，多个地图选点收藏可区分。
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
                let point = try await engine.call("metadata", [
                    "lat": a, "lon": b, "elevation_source": source,
                ])
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
        // UX #5：定位是轻量任务，不占用也不受全局 busy 限制。
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
        self.error = friendlyEngineMessage(error.localizedDescription)
    }

    var targetDateLabel: String { ObservationDate.label(date, timezone: timezone) }

    func payload() -> JSONObject {
        var value: JSONObject = [
            "name": name, "lat": lat, "lon": lon, "elevation_m": elevation,
            "timezone_name": timezone, "target_date": targetDateLabel,
            "phenomenon": phenomenon, "model_source": "both", "workers": 1,
            "forecast_mode": "model", "satellite_source": "off",
            "ecmwf_source": ecmwfSource,
            "elevation_source": elevationSource,
        ]
        if page == .nowcast {
            value["model_source"] = "satellite"
            value["forecast_mode"] = "nowcast"
            value["satellite_source"] = "auto"
            value["radius_km"] = 900.0
        }
        if page == .regional {
            // 单模型区域预测：只检查/准备/计算所选模式，避免双模式串行翻倍耗时。
            value["model_source"] = regionalModel
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
            } catch { if generation == revision { fail(error, page: requestKind) } }
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
                    missing: $0["missing_count"] as? Int ?? 0,
                    policy: $0["policy"] as? String ?? "")
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
            } catch { if generation == revision { fail(error, page: requestKind) } }
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
        guard session != nil, ready, !busy else { return }
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
                adoptResult(response, kind: kind, params: requestPayload)
                selectedModel = (response["model_results"] as? JSONObject)?["gfs"] == nil ? "ec" : "gfs"
                if kind == .regional, regionalDisplay != "compare" { regionalDisplay = selectedModel }
                self.session = nil
                status = "预测完成"
            } catch { if generation == revision { self.session = nil; ready = false; fail(error, page: kind) } }
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
            if let wrapped = response["categories"] as? JSONObject,
               let rows = wrapped["categories"] as? [JSONObject] {
                inventoryCategories = rows
            } else {
                inventoryCategories = response["categories"] as? [JSONObject] ?? []
            }
        } catch { fail(error, page: .data) }
    }

    func saveCurrentRegionsAsDefault() {
        let codes = selectedRegions.sorted()
        guard !codes.isEmpty else { return }
        defaultRegionalPreset = codes
        if sessionPersistenceEnabled {
            UserDefaults.standard.set(codes, forKey: "defaultRegionalPreset")
        }
        status = L10n.text("已将当前区域设为默认", "Saved current region as default")
    }

    func applyDefaultRegions() {
        selectedRegions = Set(defaultRegionalPreset)
        customRegion = false
        regionalCamera = nil
        regionalFitVersion += 1
    }

    // MARK: - 手动数据删除（方案 5.2）

    /// 选择范围并请求引擎预览；范围始终由库存周期键描述，不传磁盘路径。
    func beginDataDelete(source: String, cycle: String?) {
        guard online, !busy, !dataDeleteRunning, !dataDeletePreviewing, !probingDownloads else { return }
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

    /// 确认后执行。预览过期（范围内容变化）时保留范围并提示重新确认，
    /// 不静默扩大或重试删除。
    func executeDataDelete() {
        guard online, !busy, !dataDeleteRunning,
              let scope = pendingDataDelete,
              let preview = dataDeletePreview,
              let previewID = preview["preview_id"] as? String else { return }
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
                dataDeleteMessage = dataDeleteResultSummary(result)
                dataDeleteFailed = ((result["failed"] as? [Any])?.count ?? 0) > 0
                await loadInventory()
            } catch {
                dataDeletePreview = nil
                dataDeleteMessage = friendlyEngineMessage(error.localizedDescription)
                dataDeleteFailed = true
            }
        }
    }

    /// 已删除 / 跳过 / 失败 / 实际释放空间的一行摘要。
    private func dataDeleteResultSummary(_ result: JSONObject) -> String {
        let deleted = (result["deleted"] as? NSNumber)?.intValue ?? 0
        let skipped = (result["skipped"] as? [Any])?.count ?? 0
        let failed = (result["failed"] as? [Any])?.count ?? 0
        let mb = (result["freed_mb"] as? NSNumber)?.doubleValue ?? 0
        var parts = ["已删除 \(deleted) 个文件，释放 \(String(format: "%.1f", mb)) MB"]
        if skipped > 0 { parts.append("占用中跳过 \(skipped) 个") }
        if failed > 0 { parts.append("失败 \(failed) 个") }
        return parts.joined(separator: "；")
    }

    // MARK: - SRTM 地形下载（方案 7）

    /// 读取当前地点对应瓦片与本地状态；进数据管理页时调用。
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
                terrainMessage = ""
                terrainMessageFailed = false
            } catch {
                guard !Task.isCancelled else { return }
                terrain = nil
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
        }
    }

    /// 请求区域/全球下载估算（HEAD 抽样，几秒）；完成后弹确认框。
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
                showGlobalTerrainConfirm = terrainEstimate != nil
            } catch {
                terrainEstimate = nil
                terrainMessage = friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = true
            }
        }
    }

    /// 地形下载任务：按区域预设（含全球）批量下载，跳过已有、可取消续传；
    /// 点级下载保留为次级入口。不触发预测也不回填地点。
    func downloadTerrain(region: TerrainRegion, tiles: [String]? = nil) {
        guard online, !busy, !terrainDownloading, !probingDownloads else { return }
        terrainDownloading = true
        terrainCancelling = false
        terrainMessage = ""
        terrainProgress = [:]
        showGlobalTerrainConfirm = false
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
                let result = try await engine.call("terrain_download", params,
                                                   progress: { [weak self] event in
                    guard let self, let terrain = event["terrain"] as? JSONObject else { return }
                    self.terrainProgress = terrain
                })
                try Task.checkCancellation()
                let failed = (result["failed"] as? NSNumber)?.intValue ?? 0
                let failedTiles = result["failed_tiles"] as? [String] ?? []
                terrainLastFailedCount = failed
                terrainLastFailedRegion = failed > 0 ? region : nil
                terrainLastFailedTiles = failed > 0 ? failedTiles : []
                terrainMessage = terrainResultMessage(result, region: region)
                terrainMessageFailed = terrainMessageFailedFlag(result)
            } catch {
                terrainMessage = Task.isCancelled
                    ? "已停止下载；已完成的瓦片保留，重新开始会跳过它们。"
                    : friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = !Task.isCancelled
            }
            loadTerrain()
        }
    }

    /// 仅重试上次区域下载失败的瓦片（如引擎返回了失败清单则定向重拉）。
    func retryFailedTerrainTiles() {
        let region = terrainLastFailedRegion ?? terrainRegion
        let tiles = terrainLastFailedTiles.isEmpty ? nil : terrainLastFailedTiles
        downloadTerrain(region: region, tiles: tiles)
    }

    /// 点级下载（仅当前地点瓦片），保持独立入口。
    func downloadTerrainPoint() {
        guard online, !busy, !terrainDownloading, !probingDownloads else { return }
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
                                                   ["lat": lat, "lon": lon, "timeout_seconds": 600.0],
                                                   progress: { [weak self] event in
                    guard let self, let terrain = event["terrain"] as? JSONObject else { return }
                    self.terrainProgress = terrain
                })
                try Task.checkCancellation()
                terrainMessage = terrainResultMessage(result, region: nil)
                terrainMessageFailed = terrainMessageFailedFlag(result)
            } catch {
                terrainMessage = Task.isCancelled
                    ? "已停止下载；未写入半成品瓦片。"
                    : friendlyEngineMessage(error.localizedDescription)
                terrainMessageFailed = !Task.isCancelled
            }
            loadTerrain()
        }
    }

    private func terrainResultMessage(_ result: JSONObject, region: TerrainRegion?) -> String {
        let status = result["status"] as? String ?? ""
        if let region {
            let downloaded = (result["downloaded"] as? NSNumber)?.intValue ?? 0
            let absent = (result["absent"] as? NSNumber)?.intValue ?? 0
            let failed = (result["failed"] as? NSNumber)?.intValue ?? 0
            let gb = (result["received_gb"] as? NSNumber)?.doubleValue ?? 0
            let summary = "新增 \(downloaded) 块、缺失 \(absent) 块、失败 \(failed) 块，占用 \(String(format: "%.1f", gb)) GB。"
            switch status {
            case "cancelled":
                if result["stopped_reason"] as? String == "disk_space" {
                    return "\(region.rawValue)下载因磁盘空间不足停止：\(summary)重新开始会跳过已有瓦片。"
                }
                return "\(region.rawValue)下载已停止：\(summary)重新开始会跳过已有瓦片。"
            default:
                if failed > 0 {
                    return "\(region.rawValue)下载完成（部分失败）：\(summary)再次运行可重试失败瓦片。"
                }
                return "\(region.rawValue)下载完成：\(summary)"
            }
        }
        switch status {
        case "exists": return "地形瓦片已存在，直接复用本地数据。"
        case "downloaded": return "已下载地形瓦片 \(result["tile"] as? String ?? "")。"
        case "out_of_coverage": return result["message"] as? String ?? "该位置不在 SRTM 覆盖范围内。"
        default: return "下载未完成。"
        }
    }

    private func terrainMessageFailedFlag(_ result: JSONObject) -> Bool {
        let status = result["status"] as? String ?? ""
        if status == "cancelled" || status == "done" {
            return ((result["failed"] as? NSNumber)?.intValue ?? 0) > 0
                || result["stopped_reason"] as? String == "disk_space"
        }
        return status == "out_of_coverage" || status == "failed"
    }

    func cancelTerrainDownload() {
        guard terrainDownloading else { return }
        terrainCancelling = true
        terrainDownloadTask?.cancel()
    }

    // MARK: - 数据布局迁移（方案 §8 阶段 B）

    func loadLayout() {
        guard online, !layoutLoading, !layoutMigrating else { return }
        Task { [weak self] in
            guard let self else { return }
            layoutLoading = true
            defer { layoutLoading = false }
            do {
                layout = try await engine.call("data_layout_status")
            } catch {
                layout = nil
            }
        }
    }

    /// 迁移后布局变化使本进程内已解析路径失效：停引擎通道并重连，
    /// Runtime::new 重新按可见布局解析。
    func migrateLayout() {
        guard online, !busy, !layoutMigrating else { return }
        layoutMigrating = true
        layoutMessage = ""
        showLayoutConfirm = false
        Task { [weak self] in
            guard let self else { return }
            defer { layoutMigrating = false }
            do {
                let response = try await engine.call("data_layout_migrate")
                let migrated = (response["result"] as? JSONObject)?["migrated"] as? [Any] ?? []
                if response["status"] as? String == "already-visible" {
                    layoutMessage = "已在使用可见目录布局。"
                    layoutMessageFailed = false
                } else {
                    layoutMessage = "迁移完成（\(migrated.count) 项），正在按新布局重连引擎…"
                    layoutMessageFailed = false
                    engine.stop()
                    await connect()
                }
                loadLayout()
            } catch {
                layoutMessage = friendlyEngineMessage(error.localizedDescription)
                layoutMessageFailed = true
            }
        }
    }

    func dismissLayoutConfirm() {
        showLayoutConfirm = false
    }

    func loadTaskHistory(older: Bool = false) {
        guard online, !taskHistoryLoading else { return }
        if older && (taskHistoryCursor == nil || !taskHistoryHasMore) { return }
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
                var params: JSONObject = ["limit": 100]
                if older, let cursor = taskHistoryCursor {
                    params["before"] = cursor
                }
                let response = try await engine.call("task_history", params)
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
                // 加载更早 = 追加；首页 = 替换。
                taskHistory = older ? taskHistory + parsed : parsed
                taskHistoryTotal = total
                taskHistoryHasMore = response["has_more"] as? Bool ?? false
                taskHistoryCursor = response["next_cursor"] as? String
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
        // v1.9.5：预测任务详情随选中项加载（预测详情卡）。
        if let item = taskHistory.first(where: { $0.taskID == taskID }) {
            loadTaskResult(for: item)
        } else {
            resetTaskResult()
        }
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
        resetTaskResult()
    }

    /// v1.9.5 任务详情 → 预测详情：选中已完成的预测任务时经 result_restore
    /// 读取不可变结果快照（引擎侧 SHA256 校验，fail-closed）。快照内容
    /// 不可变，会话内按 task_id 缓存；读取失败（过期/清理）转为可读说明。
    func loadTaskResult(for item: NativeTaskHistoryItem) {
        guard item.method == "forecast", item.status == "done" else {
            resetTaskResult()
            return
        }
        guard taskResultTaskID != item.taskID else { return }
        taskResultTask?.cancel()
        taskResultTaskID = item.taskID
        if let cached = taskResultCache[item.taskID] {
            taskResult = cached
            taskResultState = .loaded
            return
        }
        taskResult = nil
        taskResultState = .loading
        taskResultTask = Task { [weak self] in
            guard let self else { return }
            defer { taskResultTask = nil }
            do {
                let response = try await engine.call("result_restore", ["task_id": item.taskID])
                try Task.checkCancellation()
                guard let results = response["model_results"] as? JSONObject,
                      !results.isEmpty else {
                    throw EngineError(message: "预测详情快照不完整。")
                }
                taskResultCache[item.taskID] = response
                taskResult = response
                taskResultState = .loaded
            } catch is CancellationError {
            } catch {
                taskResultState = .unavailable(error.localizedDescription)
            }
        }
    }

    private func resetTaskResult() {
        taskResultTask?.cancel()
        taskResultTask = nil
        taskResultTaskID = nil
        taskResult = nil
        taskResultState = .idle
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
                // UX #1：恢复结果时先回填结果的原始参数（与重试路径一致），
                // 标题/表单与结果本体不再错位。
                if let retry = item.retryRequest {
                    applyRetryPayload(retry.payload, kind: kind)
                }
                adoptResult(response, kind: kind, params: item.retryRequest?.payload)
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
            if let model = payload["model_source"] as? String, ["gfs", "ec"].contains(model) {
                regionalModel = model
            }
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
        syncDateTab()
    }

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
    /// 非图-表联动）。沿用 selectPoint 的坐标预填语义；UX #3/#5 起坐标变化
    /// 不再作废已有结果：旧结果保留并标注原始参数，单点页显示「等待预测」，
    /// 不会拿区域数据拼接填充。
    func openRegionalCellInPointForecast(_ cell: RegionalCell, model: String) {
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

    /// 顶栏快速切换明暗主题：跟随系统 → 浅色 → 深色。
    func cycleAppearance() {
        switch appearance {
        case "system": appearance = "light"
        case "light": appearance = "dark"
        default: appearance = "system"
        }
        saveAppearance()
    }

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
        // 设置已拆分类：CAMS 在「数据」分区，进入时直接落位并展开教程。
        page = .settings
        settingsCategory = .data
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
                retentionMessage = retentionConfigured
                    ? L10n.text("保留设置已保存；可点击「立即按策略清理」，或等待下一次成功准备后自动清理。",
                                "Retention policy saved; click Clean up now or wait for the next successful prepare.")
                    : L10n.text("已清空保留设置。", "Retention policy cleared.")
                retentionSaveFailed = false
                // 同步页的「未配置保留」软警告依赖 retention_configured，一并刷新。
                await refreshDataSyncState()
            } catch {
                retentionMessage = error.localizedDescription
                retentionSaveFailed = true
            }
        }
    }

    /// 立即按已保存的保留策略清理旧数据（跳过占用中的文件，并刷新库存）。
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
        refreshHelperStatus()
        // 已安装并正常运行 launchd 后台同步助手时，把定时调度让给独立 helper 进程，
        // 避免主应用进程自己挂起排队轮询（导致退出主应用被「正在同步」拦截）。
        if helperInstalled && helperLoaded && !helperWorkspaceMismatch {
            await refreshDataSyncState()
            return
        }
        do {
            let due = try await engine.call("data_sync_due")
            guard !Task.isCancelled, due["due_for_any"] as? Bool == true,
                  let sources = due["due_sources"] as? [String], !sources.isEmpty else { return }
            // tick 静默（设计 11.3）：轮级错误不写界面文案，逐源结果留在
            // 状态文件与任务历史，下个心跳重新判定。
            _ = await runDataSyncRound(sources: sources, paramsOverride: ["sources": sources, "auto_tick": true])
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
    private func runDataSyncRound(
        sources: [String],
        force: Bool = false,
        paramsOverride: JSONObject? = nil
    ) async -> (response: JSONObject?, error: Error?) {
        dataSyncRunning = true
        dataSyncProgress = [:]
        dataSyncActiveSource = ""
        defer {
            dataSyncRunning = false
            dataSyncActiveSource = ""
        }
        var response: JSONObject?
        var roundError: Error?
        var params: JSONObject = paramsOverride ?? ["sources": sources]
        if force { params["force"] = true }
        do {
            response = try await engine.call("data_sync_run", params,
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
    /// 显式传当前启用的源并开启 force 强制核验最新发布周期；引擎在源真正开始执行时清零其熔断状态（人工解锁）。
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
            let round = await runDataSyncRound(sources: sources, force: true)
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
            case "in_progress": text = "后台同步中"
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
        helperWorkspaceMismatch = helperInstalled
            && !SyncHelperInstaller.installedWorkspace(expected: workspace)
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
        let ws = workspace
        Task {
            defer { helperBusy = false }
            let outcome: Result<Void, Error> = await Task.detached {
                do {
                    try SyncHelperInstaller.uninstall(workspace: ws)
                    return .success(())
                } catch { return .failure(error) }
            }.value
            switch outcome {
            case .success:
                helperMessage = "后台助手已卸载，在途同步已停止。"
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
        // UX #8：草稿只在保存成功后清空；失败时保留，避免用户重新粘贴。
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

    /// UX #7：删除收藏（按坐标匹配），持久化到用户默认值。
    func removeFavorite(_ favorite: JSONObject) {
        let lat = (favorite["lat"] as? NSNumber)?.doubleValue
        let lon = (favorite["lon"] as? NSNumber)?.doubleValue
        favorites.removeAll {
            ($0["lat"] as? NSNumber)?.doubleValue == lat
                && ($0["lon"] as? NSNumber)?.doubleValue == lon
        }
        UserDefaults.standard.set(favorites, forKey: "favorites")
    }

    // MARK: - UX #7 会话持久化（上次地点/日期/模式）

    /// 表单参数变化后防抖保存；恢复会话与快照/夹具运行不写用户默认值。
    private func sessionChanged() {
        guard sessionPersistenceEnabled, !restoringSession else { return }
        sessionSaveTask?.cancel()
        sessionSaveTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard let self, !Task.isCancelled else { return }
            self.saveSession()
        }
    }

    /// 内部可见以便截图夹具做持久化往返断言；快照运行默认不落盘。
    func saveSession() {
        guard sessionPersistenceEnabled else { return }
        let session: JSONObject = [
            "name": name, "lat": lat, "lon": lon, "elevation_m": elevation,
            "timezone_name": timezone, "metadata_ready": metadataReady,
            "date": date.timeIntervalSince1970, "date_tab": dateTab,
            "phenomenon": phenomenon, "page": page.rawValue,
            "selected_regions": selectedRegions.sorted(), "custom_region": customRegion,
            "west": west, "east": east, "south": south, "north": north,
        ]
        UserDefaults.standard.set(session, forKey: "lastSession")
    }

    /// 内部可见以便截图夹具做持久化往返断言。
    func restoreSession() {
        guard sessionPersistenceEnabled,
              let saved = UserDefaults.standard.dictionary(forKey: "lastSession") else { return }
        restoringSession = true
        defer { restoringSession = false }
        if let value = saved["name"] as? String, !value.isEmpty { name = value }
        if let value = (saved["lat"] as? NSNumber)?.doubleValue, value.isFinite { lat = value }
        if let value = (saved["lon"] as? NSNumber)?.doubleValue, value.isFinite { lon = value }
        if let value = (saved["elevation_m"] as? NSNumber)?.doubleValue, value.isFinite { elevation = value }
        if let value = saved["timezone_name"] as? String, !value.isEmpty { timezone = value }
        if let value = (saved["date"] as? NSNumber)?.doubleValue, value.isFinite {
            date = Date(timeIntervalSince1970: value)
        }
        if let value = saved["date_tab"] as? String, ["今天", "明天", "自定义"].contains(value) {
            dateTab = value
        }
        if let value = saved["phenomenon"] as? String, ["dawn", "dusk"].contains(value) {
            phenomenon = value
        }
        if let value = saved["metadata_ready"] as? Bool { metadataReady = value }
        if let value = saved["selected_regions"] as? [String] { selectedRegions = Set(value) }
        if let value = saved["custom_region"] as? Bool { customRegion = value }
        if let value = (saved["west"] as? NSNumber)?.doubleValue, value.isFinite { west = value }
        if let value = (saved["east"] as? NSNumber)?.doubleValue, value.isFinite { east = value }
        if let value = (saved["south"] as? NSNumber)?.doubleValue, value.isFinite { south = value }
        if let value = (saved["north"] as? NSNumber)?.doubleValue, value.isFinite { north = value }
        // 「今天/明天」是相对启动时刻的语义：跨天重启后按保存日期重算标签。
        syncDateTab()
        if dateTab == "今天" || dateTab == "明天" { chooseDate(dateTab) }
        if let raw = saved["page"] as? String, let savedPage = Page(rawValue: raw) {
            page = savedPage
        }
    }

    /// 按当前时区把 dateTab 对齐到 date 实际落在的今天/明天/自定义。
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

    // MARK: - UX #1/#3 结果与原始参数

    /// 统一结果入口：预测成功与历史恢复都经由这里，同时记录结果的原始参数。
    /// 内部可见以便截图夹具构造「参数已变更」场景。
    func adoptResult(_ response: JSONObject, kind: Page, params: JSONObject?) {
        resultParams = params
        result = response
        resultKind = kind
    }

    /// 当前表单是否与结果原始参数不一致（仅在结果归属当前页面时生效，不跨页误报）。
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
        if resultKind == .regional {
            if !sameString("model_source", regionalModel) { return true }
            if let regions = params["regions"] as? [String] {
                let current = selectedRegions.sorted() + (customRegion ? ["custom"] : [])
                if Set(regions) != Set(current) { return true }
            }
            if customRegion, let bounds = params["custom_bounds"] as? JSONObject {
                if !sameNumberNested(bounds, "west", west) || !sameNumberNested(bounds, "east", east)
                    || !sameNumberNested(bounds, "south", south) || !sameNumberNested(bounds, "north", north) {
                    return true
                }
            }
        } else {
            if !sameNumber("lat", lat) || !sameNumber("lon", lon) { return true }
            if !sameNumber("elevation_m", elevation) { return true }
            if !sameString("timezone_name", timezone) { return true }
        }
        return false
    }

    private func sameNumberNested(_ object: JSONObject, _ key: String, _ current: Double) -> Bool {
        guard let saved = (object[key] as? NSNumber)?.doubleValue else { return true }
        return abs(saved - current) < 1e-9
    }

    /// 结果区标题：仅在结果归属当前页面时以结果原始参数为准，避免把实况/区域旧地点串到单点页。
    var resultTitle: String {
        if let params = resultParams, result != nil, resultKind == page {
            let name = params["name"] as? String ?? name
            let date = params["target_date"] as? String ?? targetDateLabel
            return "\(name) · \(date)"
        }
        return "\(name) · \(targetDateLabel)"
    }

    /// 结果区现象标签：与 resultTitle 同口径，跟随当前页结果原始参数。
    var resultPhenomenonLabel: String {
        let value = (result != nil && resultKind == page ? resultParams?["phenomenon"] as? String : nil) ?? phenomenon
        return value == "dawn" ? "朝霞" : "晚霞"
    }

    /// 「参数已变更」徽标的完整说明（help 提示全文，不截断）。
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
        if resultKind == .regional {
            if let value = params["model_source"] as? String {
                parts.append("模式：\(value == "ec" ? "ECMWF" : "GFS")")
            }
            if let regions = params["regions"] as? [String], !regions.isEmpty {
                parts.append("区域：\(regions.joined(separator: "、"))")
            }
        }
        return "结果原始参数 — " + parts.joined(separator: "；")
    }

    /// UX #12：退出确认依据——是否有仍在运行的前台任务/探测/同步。
    var hasActiveWork: Bool { busy || probingDownloads || dataSyncRunning }

    func shutdown() {
        downloadProbeTask?.cancel()
        locationTimeout?.cancel()
        operation?.cancel()
        searchTask?.cancel()
        publicationTask?.cancel()
        regionalProfileTask?.cancel()
        frozenProfileTask?.cancel()
        taskHistoryTask?.cancel()
        taskHistoryEventsTask?.cancel()
        syncTickTask?.cancel()
        sessionSaveTask?.cancel()
        saveSession()
        engine.stop()
    }
}

/// F3：引擎锁/让路等机器可读错误串映射为中文 + 可操作建议，界面不透传裸英文。
/// 仅映射已知的协议级错误；未知错误保留原文以便排查。
func friendlyEngineMessage(_ message: String) -> String {
    let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
    // The lock holder may be the app's own sync round, the background helper, or
    // an orphan left by a previous uninstall — never attribute it to the helper
    // alone. The engine may append "(pid N)"; match the stable prefix.
    if text.hasPrefix("another data sync round is active in another process") {
        return "另一轮数据同步正在进行（应用内同步或后台助手），本轮已让路；稍后会自动重试。"
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
    case "desktop operation cancelled", "nowcast preparation cancelled":
        return "操作已取消。"
    case "nowcast operation cancelled":
        return "操作已取消。"
    case "nowcast targets more than four hours in the future":
        return "实况观测仅支持距朝霞/晚霞时刻 4 小时以内的目标；当前时间距离太远，请在事件前 4 小时内再来，或改用单点/区域预测。"
    case "native nowcast preparation requires two recent HSD frames":
        return "实况分析需要最近两帧卫星数据，当前缓存不足；请检查网络后点击「下载缺失数据」重试。"
    case "native nowcast preparation requires a non-fast context":
        return "实况准备需要先完成完整预检，请刷新预检后再下载数据。"
    case "nowcast GFS requirements exceed the bounded hour limit":
        return "实况所需 GFS 时次超出上限，请刷新预检后重试；若持续出现请更换目标日期。"
    case "HSD preflight frames missing", "HSD preflight frame time missing":
        return "卫星数据预检结果不完整，请刷新预检后重试。"
    case "nowcast cycle is missing":
        return "实况数据周期缺失，请刷新预检后重试。"
    case "native nowcast requires fresh paired motion for a future event":
        return "未来时刻的实况分析需要最新成对卫星运动数据，请先点击「下载缺失数据」补齐卫星数据。"
    case "HSD observation precedes GFS prior cycle":
        return "卫星观测早于 GFS 先验周期，暂无可用组合；请稍后刷新预检重试。"
    case "data cycle is unverified or older than the latest release; explicit cycle-age confirmation is required":
        return "数据周期未核验或不是最新发布，请在确认提示中选择「继续预测」，或先更新数据。"
    case "explicit confirmation is required before forecast":
        return "请先在预检窗口确认后再开始预测。"
    case "inputs are missing; prepare data before forecasting":
        return "本次预测数据不完整，请先点击「下载缺失数据」补齐。"
    case "strict input integrity check failed":
        return "数据完整性校验未通过，请重新下载本次数据后再试。"
    case "assessment main bundle exceeds bounded output size":
        return "区域范围或格点密度超出单次输出上限，请缩小区域、降低密度或改用自定义格点后重试。"
    case "Himawari calibrated scan is outside the supported five-minute nowcast window":
        return "卫星扫描与目标时刻相差超过 30 分钟，实况窗口尚未开启；请在朝霞/晚霞时刻前半小时再来，或改用单点/区域预测。"
    case "cross-section offset must exactly match the event's five-minute grid":
        return "剖面时刻与事件网格不匹配，请重新发起预测；若持续出现请刷新预检后重试。"
    case "native terrain prepass did not produce a final event plan":
        return "地形数据预下载未完成，请重试「下载缺失数据」；若持续失败请检查网络后刷新预检。"
    case "unknown regional domain":
        return "所选预测区域无效，请重新选择区域或自定义范围。"
    case "invalid EC data operation/planning limits":
        return "EC 数据计划超出规模限制，请缩小预测区域或减少目标日期后重试。"
    case "data sync is not enabled":
        return "后台数据同步未启用，请先在设置中开启。"
    case "native-report-has-no-map-cells":
        return "本次结果未生成地图格网（单点报告不含区域地图）。"
    default:
        break
    }
    if text.hasPrefix("data sync source "), text.hasSuffix(" is not enabled in settings") {
        let name = text.dropFirst("data sync source ".count).dropLast(" is not enabled in settings".count)
        return "数据源 \(name.uppercased()) 未在设置中启用，请先勾选后再同步。"
    }
    if text.hasPrefix("all model forecasts failed") {
        if text.contains("wrapped GFS segments overlap")
            || text.contains("wrapped GFS segments carry different fields")
        {
            return "跨日界线区域的数据段拼接失败，请重新点击「开始预测」重试；若持续出现请刷新预检后重试。"
        }
        return "模型预测失败，请检查数据完整性后重试；若持续出现请刷新预检或缩小预测区域。"
    }
    return message
}
