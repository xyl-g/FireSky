package com.firesky.mobile.store

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.firesky.mobile.engine.EngineClient
import com.firesky.mobile.engine.EngineError
import com.firesky.mobile.engine.EngineTransport
import com.firesky.mobile.engine.JniTransport
import com.firesky.mobile.engine.MockTransport
import com.firesky.mobile.engine.RemoteHttpTransport
import com.firesky.mobile.ui.friendlyEngineMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID
import kotlin.math.abs

/** 数据会话行（同构 apps/macos PreparationActions.swift DataRow）。 */
data class DataRow(
    val source: String,
    val ready: Boolean,
    val old: Boolean,
    val verified: Boolean,
    val missing: Int,
    /** 是否为最新发布周期（v1.9.10 周期龄确认门控）。 */
    val latest: Boolean = true,
    /** 日期策略（v1.8.0）：past-local-cache-only 表示旧日期仅本地缓存。 */
    val policy: String = "",
) {
    val id: String get() = source
    val title: String get() = when (source) {
        "ecmwf" -> "EC"
        "hsd" -> "卫星帧"
        else -> source.uppercase()
    }
    val status: String get() = when {
        !ready -> "需下载"
        old -> "可更新"
        else -> "已下载"
    }
    val detail: String get() {
        val base = when {
            !ready -> if (missing > 0) "本次预测数据不完整" else "尚未下载本次预测数据"
            old -> if (verified) "有新发布的数据可更新" else "非最新发布周期"
            verified -> "发布周期已核验"
            else -> "更新待核验"
        }
        return if (policy == "past-local-cache-only") "旧日期仅本地缓存 · $base" else base
    }
}

/** 观测点（对应 Store 的 name/lat/lon/elevation/timezone/metadataReady）。 */
data class LocationState(
    val name: String = "杭州",
    val lat: Double = 30.2741,
    val lon: Double = 120.1551,
    val elevation: Double = 8.0,
    val timezone: String = "Asia/Shanghai",
    val metadataReady: Boolean = true,
)

/** 任务历史行（同构 NativeTaskHistoryItem；v1.6.0 历史页）。 */
data class TaskHistoryItem(
    val taskId: String,
    val method: String,
    val status: String,
    val createdAt: String,
    val cancelRequested: Boolean,
    val percent: Double?,
    val retryPayload: JSONObject?,
    val canRestoreResult: Boolean,
) {
    val isBackgroundSync: Boolean get() = method == "data_sync"
    val methodTitle: String get() = when (method) {
        "check" -> "数据检查"
        "prepare" -> "数据准备"
        "forecast" -> "预测计算"
        "publications" -> "发布核验"
        "network_probe" -> "下载探测"
        "data_sync" -> "后台同步"
        "terrain_download" -> "地形下载"
        else -> "任务"
    }
    val kindTitle: String get() = when (retryPayload?.optString("kind")) {
        "point" -> "单点"
        "regional" -> "区域"
        "nowcast" -> "实况"
        else -> methodTitle
    }
    val rowTitle: String get() = if (kindTitle == methodTitle) methodTitle else "$kindTitle · $methodTitle"
    val statusTitle: String get() = when (status) {
        "running" -> if (cancelRequested) "取消中" else "运行中"
        "done" -> "已完成"
        "failed" -> "失败"
        "cancelled" -> "已取消"
        "interrupted" -> "已中断"
        else -> status
    }
    val contextTitle: String get() {
        val payload = retryPayload ?: return if (isBackgroundSync) "后台数据同步轮次" else ""
        val name = payload.optString("name", "").ifEmpty {
            val regions = payload.optJSONArray("regions") ?: JSONArray()
            (0 until regions.length()).map { regions.optString(it) }.joinToString("、")
        }
        val date = payload.optString("target_date", "")
        val phenomenon = when (payload.optString("phenomenon")) {
            "dawn" -> "朝霞"
            "dusk" -> "晚霞"
            else -> ""
        }
        val text = listOf(name, date, phenomenon).filter { it.isNotEmpty() }.joinToString(" · ")
        return if (text.isEmpty() && isBackgroundSync) "后台数据同步轮次" else text
    }
    /** 本地时区短时间（UTC ISO8601 → MM-dd HH:mm）。 */
    val localTime: String get() = runCatching {
        val instant = java.time.Instant.parse(createdAt)
        val formatter = java.time.format.DateTimeFormatter.ofPattern("MM-dd HH:mm")
        formatter.withZone(java.time.ZoneId.systemDefault()).format(instant)
    }.getOrDefault(createdAt)

    /** 行尾摘要：运行中带百分比，中断标注不自动重跑（同 macOS 行内说明）。 */
    val rowTimeLine: String
        get() {
            var text = localTime
            if (status == "running" && percent != null) {
                text += " · %.0f%%".format(percent)
            }
            if (status == "interrupted") {
                text += " · 不会自动重跑"
            }
            return text
        }

    companion object {
        /** 解析与校验同构 NativeTaskHistoryItem.init?：非法记录返回 null。 */
        fun parse(value: JSONObject): TaskHistoryItem? {
            val taskId = value.optString("task_id")
            if (taskId.length != 64 || taskId.any { !it.isDigit() && it !in 'a'..'f' }) return null
            val method = value.optString("method")
            val status = value.optString("status")
            if (status !in listOf("running", "done", "failed", "cancelled", "interrupted")) return null
            val createdAt = value.optString("created_at_utc")
            if (createdAt.isEmpty()) return null
            val retry = value.optJSONObject("retry_request")
            val payload = retry?.optJSONObject("payload")?.takeIf {
                it.optString("kind") in listOf("point", "regional", "nowcast")
            }
            val reference = value.optJSONObject("result_ref")?.optJSONObject("snapshot")
            val canRestore = reference?.optString("contract") == "firesky-result-snapshot-v1" &&
                reference.has("sha256")
            return TaskHistoryItem(
                taskId = taskId,
                method = method,
                status = status,
                createdAt = createdAt,
                cancelRequested = value.optBoolean("cancel_requested", false),
                percent = value.optJSONObject("progress")?.optDouble("percent")?.takeUnless { it.isNaN() },
                retryPayload = payload,
                canRestoreResult = canRestore,
            )
        }
    }
}

/** 任务事件行（同构 NativeTaskEvent）。 */
data class TaskEventRow(
    val seq: Int,
    val at: String,
    val event: String,
    val stage: String,
    val message: String,
    val percent: Double?,
) {
    val title: String get() = when (event) {
        "running" -> "开始"
        "progress" -> "进度"
        "cancel_requested" -> "请求取消"
        "done" -> "完成"
        "failed" -> "失败"
        "cancelled" -> "已取消"
        "interrupted" -> "已中断"
        else -> "状态更新"
    }
    val detail: String get() {
        val parts = buildList {
            if (stage.isNotEmpty()) add(stage.take(128))
            if (message.isNotEmpty()) add(message.take(128))
            percent?.let { add("%.0f%%".format(it)) }
        }
        return parts.joinToString(" · ")
    }
    val localTime: String get() = runCatching {
        val instant = java.time.Instant.parse(at)
        val formatter = java.time.format.DateTimeFormatter.ofPattern("MM-dd HH:mm:ss")
        formatter.withZone(java.time.ZoneId.systemDefault()).format(instant)
    }.getOrDefault(at)
}

/** 地形下载区域预设（v1.4.0 macOS TerrainRegion 同构；全球走引擎缺省全域）。 */
enum class TerrainRegion(val label: String, val bounds: JSONObject?) {
    CHINA("中国", JSONObject().put("south", 18).put("north", 54).put("west", 73).put("east", 135)),
    EAST_ASIA("东亚", JSONObject().put("south", 20).put("north", 54).put("west", 100).put("east", 146)),
    SOUTHEAST_ASIA("东南亚", JSONObject().put("south", -11).put("north", 28).put("west", 92).put("east", 141)),
    ASIA("亚洲", JSONObject().put("south", -11).put("north", 59).put("west", 26).put("east", 179)),
    EUROPE("欧洲", JSONObject().put("south", 35).put("north", 59).put("west", -11).put("east", 40)),
    NORTH_AMERICA("北美", JSONObject().put("south", 15).put("north", 59).put("west", -170).put("east", -52)),
    SOUTH_AMERICA("南美", JSONObject().put("south", -56).put("north", 13).put("west", -82).put("east", -34)),
    AFRICA("非洲", JSONObject().put("south", -35).put("north", 37).put("west", -18).put("east", 52)),
    OCEANIA("大洋洲", JSONObject().put("south", -47).put("north", 0).put("west", 110).put("east", 179)),
    GLOBAL("全球", null);

    companion object {
        fun fromLabel(label: String): TerrainRegion? = entries.firstOrNull { it.label == label }
        val labels: List<String> get() = entries.map { it.label }
    }
}

/**
 * 单点工作台状态（v1 范围：无区域/实况，MOBILE_NATIVE_PLAN.md §3）。
 * 状态语义以 iOS Store.swift 为参照源；地图选点延后（openQuestions D1）。
 */
data class WorkbenchState(
    // 顶层页面（工作台 / 设置，对应 iOS Page.point / Page.settings）
    val page: String = "workbench",
    // 参数表单
    val location: LocationState = LocationState(),
    val query: String = "",
    val dateTab: String = "今天", // 今天 / 明天 / 自定义
    val targetDate: LocalDate = LocalDate.now(),
    val phenomenon: String = "dusk", // dawn / dusk
    val editingCoordinates: Boolean = false,
    val favorites: List<JSONObject> = emptyList(),
    // 会话与进度
    val busy: Boolean = false,
    val checking: Boolean = false,
    val status: String = "正在连接引擎",
    val error: String = "",
    val errorPage: String? = null,
    val online: Boolean = false,
    val percent: Double = 0.0,
    val showPreflight: Boolean = false,
    val showCycleAgeConfirm: Boolean = false,
    val publicationsPending: Boolean = false,
    val session: String? = null,
    val rows: List<DataRow> = emptyList(),
    val ready: Boolean = false,
    // 结果卡片（保留旧结果与原始参数以便展示「参数已变更」横幅）
    val result: JSONObject? = null,
    val resultParams: JSONObject? = null,
    val resultKind: String? = null,
    val selectedModel: String = "gfs",
    val resultTab: String = "概览",
    // 设置
    val appearance: String = "system",
    val ecmwfSource: String = "ecmwf",
    val elevationSource: String = "auto", // auto / http / srtm（v1.1.1 §1）
    val transportMode: String = "ffi", // mock / ffi（MN2 起默认真引擎；设置页可切回 mock）
    val camsConfiguration: JSONObject? = null,
    val camsKeyDraft: String = "",
    val savingCAMS: Boolean = false,
    val camsMessage: String = "",
    val camsSaveFailed: Boolean = false,
    val showCAMSTutorial: Boolean = false,
    // 后台同步设置
    val dataSyncEnabled: Boolean = false,
    val dataSyncCAMS: Boolean = false,
    val dataSyncGFS: Boolean = true,
    val dataSyncEC: Boolean = true,
    val dataSyncInterval: String = "15",
    val dataSyncLeadHorizon: String = "72",
    val dataSyncConfigured: Boolean = false,
    val dataSyncRetentionConfigured: Boolean = false,
    val dataSyncLoaded: Boolean = false,
    val savingDataSync: Boolean = false,
    val dataSyncRunning: Boolean = false,
    val dataSyncMessage: String = "",
    val dataSyncSaveFailed: Boolean = false,
    val dataSyncState: JSONObject = JSONObject(),
    // 缓存保留策略
    val retentionGFS: String = "",
    val retentionECMWF: String = "",
    val retentionCAMS: String = "",
    val retentionSatelliteDays: String = "",
    val retentionReportDays: String = "",
    val retentionMapDays: String = "",
    val retentionConfigured: Boolean = false,
    val savingRetention: Boolean = false,
    val applyingRetention: Boolean = false,
    val retentionMessage: String = "",
    val retentionSaveFailed: Boolean = false,
    // 任务历史（v1.6.0 与 macOS 任务历史页对齐）
    val taskHistory: List<TaskHistoryItem> = emptyList(),
    val taskHistoryTotal: Int = 0,
    val taskHistoryLoaded: Boolean = false,
    val taskHistoryLoading: Boolean = false,
    val taskHistoryError: String = "",
    /** 分页（v1.8.1 可用性）：has_more 时提供「加载更早任务」。 */
    val taskHistoryHasMore: Boolean = false,
    val taskHistoryCursor: String? = null,
    val selectedTaskId: String? = null,
    val taskEvents: List<TaskEventRow> = emptyList(),
    val taskEventsLoading: Boolean = false,
    val taskEventsError: String = "",
    val pendingForgetTaskId: String? = null,
    val restoringTaskId: String? = null,
    // v1.9.5 任务详情 → 预测详情：已完成的预测任务读取结果快照。
    val taskResultTaskId: String? = null,
    val taskResult: JSONObject? = null,
    val taskResultLoading: Boolean = false,
    val taskResultError: String = "",
    // 数据管理（库存 / 手动删除 / 地形区域下载）
    val pendingDataDelete: JSONObject? = null,
    val dataDeletePreview: JSONObject? = null,
    val dataDeletePreviewing: Boolean = false,
    val dataDeleteRunning: Boolean = false,
    val dataDeleteMessage: String = "",
    val dataDeleteFailed: Boolean = false,
    val terrain: JSONObject? = null,
    val terrainLoading: Boolean = false,
    val terrainDownloading: Boolean = false,
    val terrainCancelling: Boolean = false,
    val terrainMessage: String = "",
    val terrainMessageFailed: Boolean = false,
    val terrainProgress: JSONObject = JSONObject(),
    val terrainRegion: String = "中国",
    val terrainEstimate: JSONObject? = null,
    val terrainEstimateLoading: Boolean = false,
    val showTerrainConfirm: Boolean = false,
    val terrainLastFailedCount: Int = 0,
    val terrainLastFailedRegion: String? = null,
    val terrainLastFailedTiles: List<String> = emptyList(),
    // 本地数据清单（库存 + 附加类目）
    val inventory: JSONArray = JSONArray(),
    val inventoryCategories: List<JSONObject> = emptyList(),
    // 远程服务器（Mac / LAN / Cloudflare Tunnel）
    val connectionMode: String = "local", // "local" | "remote"
    val connectionModeDraft: String = "local",
    val remoteServerUrl: String = "",
    val remoteTokenDraft: String = "",
    val remoteTokenSaved: Boolean = false,
    val remoteTesting: Boolean = false,
    val remoteTestMessage: String = "",
    val remoteTestOk: Boolean = false,
    val remoteWorkspace: String = "",
    // 设置分类（通用/数据/高级）
    val settingsCategory: String = "通用",
) {
    val isRemoteMode: Boolean get() = connectionMode == "remote"

    /** 仅返回归属于 targetPage 的错误文本；errorPage == null 时为全局连接错误。 */
    fun errorText(targetPage: String): String {
        val currentErrorPage = errorPage ?: return error
        return if (currentErrorPage == targetPage) error else ""
    }

    /** 镜像引擎 `needs_cycle_age_confirmation`：已就绪数据中存在未核验或非最新周期时需显式确认。 */
    val needsCycleAgeConfirmation: Boolean
        get() = ready && rows.any { it.ready && (!it.verified || !it.latest) }

    val flaggedCycleAgeRows: List<DataRow>
        get() = rows.filter { it.ready && (!it.verified || !it.latest) }

    /** 当前表单是否与最近一次归属于工作台的结果参数不一致。 */
    val resultParametersStale: Boolean
        get() {
            if (result == null || resultKind != "point") return false
            val params = resultParams ?: return false
            fun sameString(key: String, current: String): Boolean {
                if (!params.has(key)) return true
                return params.optString(key) == current
            }
            fun sameNumber(key: String, current: Double): Boolean {
                if (!params.has(key)) return true
                val saved = params.optDouble(key)
                return saved.isNaN() || abs(saved - current) < 1e-9
            }
            if (!sameString("target_date", targetDate.toString())) return true
            if (!sameString("phenomenon", phenomenon)) return true
            if (!sameString("ecmwf_source", ecmwfSource)) return true
            if (!sameNumber("lat", location.lat) || !sameNumber("lon", location.lon)) return true
            if (!sameNumber("elevation_m", location.elevation)) return true
            if (!sameString("timezone_name", location.timezone)) return true
            return false
        }

    /** 结果卡片标题：优先使用生成该结果的原始参数。 */
    val resultTitle: String
        get() {
            val params = if (result != null && resultKind == "point") resultParams else null
            val name = params?.optString("name", "")?.ifEmpty { null } ?: location.name
            val date = params?.optString("target_date", "")?.ifEmpty { null } ?: targetDate.toString()
            return "$name · $date"
        }

    /** 结果现象键（"dawn" / "dusk"），跟随结果的原始参数。 */
    val resultPhenomenon: String
        get() {
            val params = if (result != null && resultKind == "point") resultParams else null
            return params?.optString("phenomenon", "")?.ifEmpty { null } ?: phenomenon
        }
}

/**
 * 单点工作台 ViewModel —— 同构 Store.swift 的 point 作用域
 * （connect/search/metadata→check→publications→prepare→confirm 进度三链、取消、CAMS 设置）。
 */
class WorkbenchStore private constructor(
    application: Application,
    private val injectedTransport: EngineTransport?,
) : AndroidViewModel(application) {

    constructor(application: Application) : this(application, null)

    private val prefs = application.getSharedPreferences("firesky", Application.MODE_PRIVATE)

    private val _state = MutableStateFlow(WorkbenchState())
    val state: StateFlow<WorkbenchState> = _state.asStateFlow()

    private var engine: EngineClient

    private var operationJob: Job? = null
    private var publicationJob: Job? = null
    private var metadataGeneration = UUID.randomUUID()
    private var generation = UUID.randomUUID()
    private var requestPayload: JSONObject = JSONObject()

    init {
        val appearance = prefs.getString(PREF_APPEARANCE, null) ?: "system"
        val ecmwfSource = prefs.getString(PREF_ECMWF_SOURCE, null) ?: "ecmwf"
        val elevationSource = ElevationSourceSetting.load(prefs)
        val transportMode = prefs.getString(PREF_TRANSPORT, null) ?: "ffi"
        val connectionMode = if (prefs.getString(PREF_CONNECTION_MODE, null) == "remote") "remote" else "local"
        val remoteServerUrl = prefs.getString(PREF_REMOTE_SERVER_URL, null) ?: ""
        val remoteTokenSaved = !prefs.getString(PREF_REMOTE_TOKEN, null).isNullOrEmpty()
        val favorites = runCatching {
            val array = JSONArray(prefs.getString(PREF_FAVORITES, "[]") ?: "[]")
            (0 until array.length()).mapNotNull { index -> array.optJSONObject(index) }
        }.getOrDefault(emptyList())
        _state.value = _state.value.copy(
            appearance = appearance,
            ecmwfSource = ecmwfSource,
            elevationSource = elevationSource,
            transportMode = transportMode,
            connectionMode = connectionMode,
            connectionModeDraft = connectionMode,
            remoteServerUrl = remoteServerUrl,
            remoteTokenSaved = remoteTokenSaved,
            favorites = favorites,
        )
        engine = buildEngine(_state.value)
        viewModelScope.launch { connect() }
    }

    private fun savedRemoteToken(): String = prefs.getString(PREF_REMOTE_TOKEN, null).orEmpty()

    private fun buildEngine(state: WorkbenchState = _state.value): EngineClient =
        EngineClient(injectedTransport ?: transport(state))

    private fun transport(state: WorkbenchState): EngineTransport =
        if (state.connectionMode == "remote") {
            RemoteHttpTransport(
                baseUrl = state.remoteServerUrl,
                token = savedRemoteToken(),
            )
        } else {
            when (state.transportMode) {
                "ffi" -> JniTransport(getApplication())
                else -> MockTransport()
            }
        }

    /**
     * FFI 引擎启动配置（Mock 忽略）：workspace/cache/definitions 均落在沙箱
     * filesDir；definitions 由 JniTransport 先把 assets/eccodes/definitions 解压到
     * filesDir 再交 fsm_start（引擎侧先注入单数 ECCODES_DEFINITION_PATH 再初始化）。
     */
    private fun engineConfig(): JSONObject {
        val current = _state.value
        val files = getApplication<Application>().filesDir
        val config = JSONObject()
            .put("transport", current.transportMode)
            .put("connection_mode", current.connectionMode)
            .put("workspace_root", files.absolutePath)
            .put("cache_root", File(files, "cache").absolutePath)
            .put("definitions_dir", File(files, "eccodes/definitions").absolutePath)
        if (current.isRemoteMode) {
            config.put("base_url", current.remoteServerUrl)
            config.put("token", savedRemoteToken())
        }
        return config
    }

    // ---------------------------------------------------------------- 连接

    fun connect() {
        val current = _state.value
        if (current.busy) return
        _state.update { it.copy(online = false, error = "", errorPage = null) }
        viewModelScope.launch {
            try {
                engine.start(engineConfig())
                val hello = engine.call("hello")
                if (hello.optInt("protocol") != 1) throw EngineError("引擎协议版本不匹配。")
                val capabilities = (hello.optJSONArray("capabilities") ?: JSONArray()).let { array ->
                    (0 until array.length()).map { index -> array.optString(index) }.toSet()
                }
                val workspace = hello.optString("workspace", "")
                _state.update {
                    val isRemote = it.isRemoteMode
                    val remoteMsg = if (isRemote) {
                        if (workspace.isNotEmpty()) "已连接到远程 Mac（工作区：$workspace）" else "已连接到远程 Mac"
                    } else {
                        it.remoteTestMessage
                    }
                    it.copy(
                        online = true,
                        status = if (isRemote) "已连接远程 Mac" else "引擎已连接",
                        error = "",
                        errorPage = null,
                        camsConfiguration = hello.optJSONObject("cams_credentials"),
                        remoteWorkspace = if (isRemote) workspace else it.remoteWorkspace,
                        remoteTestOk = if (isRemote) true else it.remoteTestOk,
                        remoteTestMessage = remoteMsg,
                    )
                }
                if ("data_sync" in capabilities) {
                    loadDataSyncSettings()
                }
                loadRetentionSettings()
                if (_state.value.isRemoteMode) {
                    loadInventory()
                    if ("task_history" in capabilities) {
                        loadTaskHistory()
                    }
                }
                // MN2 会话恢复（协议参照 macOS Store.connect/loadTaskHistory/restoreTaskResult）：
                // hello 后拉任务历史，自动恢复最近一次可恢复的单点 forecast 结果。
                if ("task_history" in capabilities && "result_restore" in capabilities) {
                    restoreLatestResult()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                val friendly = friendlyEngineMessage(error.message ?: "引擎未连接")
                _state.update {
                    it.copy(
                        online = false,
                        status = "引擎未连接",
                        error = friendly,
                        errorPage = null,
                        remoteTestOk = if (it.isRemoteMode) false else it.remoteTestOk,
                        remoteTestMessage = if (it.isRemoteMode) friendly else it.remoteTestMessage,
                    )
                }
            }
        }
    }

    /**
     * kill-restart 恢复链（同构 macOS Store.restoreTaskResult，§3.4）：
     * task_history(limit=20) → 最近 status=done 且 result_ref.snapshot 契约有效的
     * point forecast → result_restore(task_id) → 应用 result/selectedModel 与
     * location/date/phenomenon。守 busy/generation：不得覆盖进行中会话；恢复失败
     * 静默（不影响「引擎已连接」状态，同 macOS taskHistoryError 不阻断工作台）。
     */
    private suspend fun restoreLatestResult() {
        val snapshot = _state.value
        if (snapshot.busy || snapshot.result != null) return
        val revision = generation
        try {
            val history = engine.call("task_history", JSONObject().put("limit", 20))
            if (history.optString("schema") != "firesky-task-history-v1") return
            val tasks = history.optJSONArray("tasks") ?: return
            for (index in 0 until tasks.length()) {
                val item = tasks.optJSONObject(index) ?: continue
                if (item.optString("method") != "forecast" || item.optString("status") != "done") continue
                val reference = item.optJSONObject("result_ref")?.optJSONObject("snapshot") ?: continue
                if (reference.optString("contract") != "firesky-result-snapshot-v1") continue
                if (!reference.has("sha256")) continue
                val retry = item.optJSONObject("retry_request") ?: continue
                if (retry.optString("kind") != "point") continue
                val taskId = item.optString("task_id")
                if (taskId.isEmpty()) continue
                val response = engine.call("result_restore", JSONObject().put("task_id", taskId))
                val current = _state.value
                if (current.busy || current.result != null || generation != revision) return
                val models = response.optJSONObject("model_results") ?: return
                val payload = retry.optJSONObject("payload") ?: JSONObject()
                // selectedModel：retry payload 显式单模型优先，否则 gfs 缺失回退 ec
                // （同 macOS restoreTaskResult 与 confirm 的缺省逻辑）。
                val modelSource = payload.optString("model_source")
                val selected = when {
                    modelSource == "gfs" || modelSource == "ec" -> modelSource
                    models.has("gfs") -> "gfs"
                    else -> "ec"
                }
                val restoredDate = runCatching {
                    LocalDate.parse(payload.optString("target_date"))
                }.getOrNull()
                _state.update { state ->
                    val loc = state.location
                    val lat = payload.optDouble("lat").takeUnless { it.isNaN() } ?: loc.lat
                    val lon = payload.optDouble("lon").takeUnless { it.isNaN() } ?: loc.lon
                    val elevation = payload.optDouble("elevation_m").takeUnless { it.isNaN() } ?: loc.elevation
                    val timezone = payload.optString("timezone_name", "").ifEmpty { loc.timezone }
                    state.copy(
                        location = loc.copy(
                            name = payload.optString("name", "").ifEmpty { loc.name },
                            lat = lat,
                            lon = lon,
                            elevation = elevation,
                            timezone = timezone,
                            metadataReady = true,
                        ),
                        targetDate = restoredDate ?: state.targetDate,
                        dateTab = if (restoredDate != null) "自定义" else state.dateTab,
                        phenomenon = payload.optString("phenomenon", "").ifEmpty { state.phenomenon },
                        result = response,
                        resultParams = payload,
                        resultKind = "point",
                        selectedModel = selected,
                        status = "已恢复历史结果",
                        error = "",
                        errorPage = null,
                    )
                }
                return
            }
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            // 历史损坏/恢复失败不阻断连接；用户仍可正常发起新预测。
        }
    }

    // ---------------------------------------------------------------- 位置

    fun search() {
        val text = _state.value.query.trim()
        if (text.isEmpty() || _state.value.busy) return
        _state.update { it.copy(busy = true, status = "正在查找位置", error = "", errorPage = null) }
        viewModelScope.launch {
            try {
                val point = engine.call(
                    "search",
                    ElevationSourceSetting.searchParams(text, _state.value.elevationSource),
                )
                applyLocation(point)
                _state.update { it.copy(query = "") }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                fail(error, page = "workbench")
            } finally {
                _state.update { it.copy(busy = false) }
            }
        }
    }

    fun selectPoint(lat: Double, lon: Double) {
        if (_state.value.busy) return
        _state.update {
            it.copy(
                location = it.location.copy(lat = lat, lon = lon, name = "地图选点", metadataReady = false),
            )
        }
        lookupMetadata()
    }

    fun applyFavorite(index: Int) {
        val favorite = _state.value.favorites.getOrNull(index) ?: return
        applyLocation(favorite)
    }

    fun applyLocation(point: JSONObject) {
        _state.update { current ->
            val loc = current.location
            val lat = point.optDouble("lat").takeUnless { it.isNaN() } ?: loc.lat
            val lon = point.optDouble("lon").takeUnless { it.isNaN() } ?: loc.lon
            val elevation = point.optDouble("elevation_m").takeUnless { it.isNaN() } ?: loc.elevation
            val timezone = point.optString("timezone_name", "").ifEmpty { loc.timezone }
            current.copy(
                location = loc.copy(
                    lat = lat,
                    lon = lon,
                    elevation = elevation,
                    timezone = timezone,
                    name = point.optString("name", "当前位置"),
                    metadataReady = true,
                ),
                status = "位置已就绪",
            )
        }
    }

    /** MN3 真机阶段接入系统定位（需 ACCESS_FINE_LOCATION 运行时权限）；当前显式说明。 */
    fun locate() {
        _state.update {
            it.copy(
                error = "定位功能在 MN3 真机阶段接入；当前请用搜索、收藏或手动坐标。",
                errorPage = "workbench",
            )
        }
    }

    fun lookupMetadata() {
        val revision = UUID.randomUUID()
        metadataGeneration = revision
        val lat = _state.value.location.lat
        val lon = _state.value.location.lon
        _state.update { it.copy(location = it.location.copy(metadataReady = false), status = "正在获取海拔与时区") }
        viewModelScope.launch {
            try {
                val point = engine.call(
                    "metadata",
                    ElevationSourceSetting.metadataParams(lat, lon, _state.value.elevationSource),
                )
                if (metadataGeneration != revision) return@launch
                val elevation = point.optDouble("elevation_m").takeUnless { it.isNaN() } ?: 0.0
                val timezone = point.optString("timezone_name", "").ifEmpty { _state.value.location.timezone }
                _state.update {
                    it.copy(
                        location = it.location.copy(elevation = elevation, timezone = timezone, metadataReady = true),
                        status = "位置已就绪",
                    )
                }
            } catch (error: Exception) {
                if (metadataGeneration != revision) return@launch
                _state.update {
                    it.copy(
                        error = friendlyEngineMessage(error.message ?: "海拔未获取"),
                        errorPage = "workbench",
                        status = "海拔未获取，可手动填写",
                    )
                }
            }
        }
    }

    fun starLocation() {
        val current = _state.value
        if (!current.location.metadataReady) {
            _state.update { it.copy(error = "请先确认海拔与时区。", errorPage = "workbench") }
            return
        }
        val loc = current.location
        val favorite = JSONObject()
            .put("name", loc.name)
            .put("lat", loc.lat)
            .put("lon", loc.lon)
            .put("elevation_m", loc.elevation)
            .put("timezone_name", loc.timezone)
        val favorites = current.favorites.filter {
            !(it.optDouble("lat", Double.NaN) == loc.lat && it.optDouble("lon", Double.NaN) == loc.lon)
        } + favorite
        prefs.edit().putString(PREF_FAVORITES, JSONArray(favorites).toString()).apply()
        _state.update { it.copy(favorites = favorites) }
    }

    // ---------------------------------------------------------------- 参数表单

    val targetDateLabel: String get() = _state.value.targetDate.toString()

    fun chooseDate(tab: String) {
        val zone = runCatching { ZoneId.of(_state.value.location.timezone) }
            .getOrDefault(ZoneId.systemDefault())
        val today = LocalDate.now(zone)
        val date = when (tab) {
            "明天" -> today.plusDays(1)
            "自定义" -> _state.value.targetDate
            else -> today
        }
        _state.update { it.copy(dateTab = tab, targetDate = date) }
    }

    fun setCustomDate(date: LocalDate) {
        _state.update { it.copy(dateTab = "自定义", targetDate = date) }
    }

    fun setPhenomenon(value: String) {
        _state.update { it.copy(phenomenon = value) }
    }

    fun resetParameters() {
        val zone = runCatching { ZoneId.of(_state.value.location.timezone) }
            .getOrDefault(ZoneId.systemDefault())
        _state.update {
            it.copy(dateTab = "今天", targetDate = LocalDate.now(zone), phenomenon = "dusk")
        }
    }

    fun setEditingCoordinates(value: Boolean) {
        _state.update { it.copy(editingCoordinates = value) }
    }

    fun editLocation(transform: (LocationState) -> LocationState) {
        _state.update { it.copy(location = transform(it.location)) }
    }

    fun setMetadataReady(value: Boolean) {
        _state.update { it.copy(location = it.location.copy(metadataReady = value)) }
    }

    fun setQuery(value: String) {
        _state.update { it.copy(query = value) }
    }

    fun selectModel(value: String) {
        _state.update { it.copy(selectedModel = value) }
    }

    fun selectResultTab(value: String) {
        _state.update { it.copy(resultTab = value) }
    }

    // ---------------------------------------------------------------- 数据会话

    private fun payload(): JSONObject {
        val current = _state.value
        val loc = current.location
        return JSONObject()
            .put("name", loc.name)
            .put("lat", loc.lat)
            .put("lon", loc.lon)
            .put("elevation_m", loc.elevation)
            .put("timezone_name", loc.timezone)
            .put("target_date", targetDateLabel)
            .put("phenomenon", current.phenomenon)
            .put("model_source", "both")
            .put("workers", 1)
            .put("forecast_mode", "model")
            .put("satellite_source", "off")
            .put("ecmwf_source", current.ecmwfSource)
    }

    /** 取消落定（对齐 Store.fail 的 Task.isCancelled 分支）。 */
    private fun markCancelled(revision: UUID) {
        if (generation == revision) {
            _state.update { it.copy(status = "已取消") }
        }
    }

    fun beginCheck() {
        val current = _state.value
        if (current.busy || !current.online) return
        if (!current.location.metadataReady) {
            _state.update { it.copy(error = "请先获取海拔与时区，或手动确认位置数据。", errorPage = "workbench") }
            return
        }
        generation = UUID.randomUUID()
        val revision = generation
        requestPayload = payload()
        _state.update {
            it.copy(
                session = null,
                rows = emptyList(),
                ready = false,
                error = "",
                errorPage = null,
                showPreflight = true,
                showCycleAgeConfirm = false,
                busy = true,
                checking = true,
                percent = 0.0,
                status = "正在检查本地数据",
            )
        }
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            try {
                val response = engine.call(
                    "check",
                    JSONObject().put("kind", "point").put("payload", requestPayload),
                )
                if (generation != revision) {
                    dismissQuietly(response.optString("session"))
                    return@launch
                }
                val token = response.optString("session")
                _state.update { it.copy(session = token) }
                applyReport(response)
                verifyPublications(revision, token)
            } catch (error: CancellationException) {
                markCancelled(revision)
                throw error
            } catch (error: Exception) {
                if (generation == revision) fail(error, page = "workbench")
            } finally {
                if (generation == revision) {
                    _state.update { it.copy(busy = false, checking = false) }
                }
            }
        }
    }

    fun refreshCheck() {
        if (_state.value.busy) return
        dismissToken()
        generation = UUID.randomUUID()
        beginCheckWithRevision()
    }

    private fun beginCheckWithRevision() {
        // refreshCheck 复用 beginCheck 的既有 showPreflight 会话，不再重开面板。
        val revision = generation
        _state.update {
            it.copy(
                busy = true,
                checking = true,
                percent = 0.0,
                error = "",
                errorPage = null,
                status = "正在检查本地数据",
                rows = emptyList(),
            )
        }
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            try {
                val response = engine.call(
                    "check",
                    JSONObject().put("kind", "point").put("payload", requestPayload),
                )
                if (generation != revision) return@launch
                val token = response.optString("session")
                _state.update { it.copy(session = token) }
                applyReport(response)
                verifyPublications(revision, token)
            } catch (error: CancellationException) {
                markCancelled(revision)
                throw error
            } catch (error: Exception) {
                if (generation == revision) fail(error, page = "workbench")
            } finally {
                if (generation == revision) {
                    _state.update { it.copy(busy = false, checking = false) }
                }
            }
        }
    }

    private fun verifyPublications(revision: UUID, token: String) {
        _state.update { it.copy(publicationsPending = true) }
        publicationJob?.cancel()
        publicationJob = viewModelScope.launch {
            try {
                val response = engine.call("publications", JSONObject().put("session", token))
                val snapshot = _state.value
                if (generation != revision || !snapshot.showPreflight || snapshot.busy) return@launch
                applyReport(response)
            } catch (_: Exception) {
                // 发布源中断不影响本地可用性结论（同 Store.swift 注释语义）。
            } finally {
                if (generation == revision) {
                    _state.update { it.copy(publicationsPending = false) }
                }
            }
        }
    }

    private fun applyReport(response: JSONObject) {
        val report = response.optJSONObject("report") ?: JSONObject()
        val ready = report.optBoolean("ready", false)
        val rows = (report.optJSONArray("datasets") ?: JSONArray()).let { array ->
            (0 until array.length()).mapNotNull { index ->
                val item = array.optJSONObject(index) ?: return@mapNotNull null
                val verified = item.optBoolean("verified", false)
                val latest = item.optBoolean("is_latest", false)
                DataRow(
                    source = item.optString("source"),
                    ready = item.optBoolean("ready", false),
                    old = verified && !latest,
                    verified = verified,
                    missing = item.optInt("missing_count", 0),
                    latest = latest,
                    policy = item.optString("policy", ""),
                )
            }
        }
        val missing = rows.filter { !it.ready }.joinToString("、") { it.title }
        _state.update {
            it.copy(
                ready = ready,
                rows = rows,
                status = if (ready) "本次数据已下载" else "缺少 $missing",
            )
        }
    }

    fun prepare() {
        val token = _state.value.session ?: return
        if (_state.value.busy) return
        publicationJob?.cancel()
        val revision = generation
        _state.update {
            it.copy(
                busy = true,
                checking = true,
                ready = false,
                percent = 0.0,
                error = "",
                errorPage = null,
                showCycleAgeConfirm = false,
                status = "正在准备数据",
            )
        }
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            try {
                val response = engine.call(
                    "prepare",
                    JSONObject().put("session", token),
                ) { event -> updateProgress(event) }
                ensureActive()
                if (generation != revision) return@launch
                applyReport(response)
                // 语义对齐 Store.prepare：留在准备面板，等用户点「确定预测」。
            } catch (error: CancellationException) {
                markCancelled(revision)
                throw error
            } catch (error: Exception) {
                if (generation == revision) fail(error, page = "workbench")
            } finally {
                if (generation == revision) {
                    _state.update { it.copy(busy = false, checking = false) }
                }
            }
        }
    }

    fun confirm() {
        val current = _state.value
        val token = current.session
        if (token == null || !current.ready || current.busy) return
        if (current.needsCycleAgeConfirmation) {
            _state.update { it.copy(showCycleAgeConfirm = true) }
            return
        }
        runForecast(cycleAgeConfirmed = false)
    }

    fun dismissCycleAgeConfirm() {
        _state.update { it.copy(showCycleAgeConfirm = false) }
    }

    fun confirmCycleAgeAndForecast() {
        _state.update { it.copy(showCycleAgeConfirm = false) }
        runForecast(cycleAgeConfirmed = true)
    }

    fun updateToLatestFromCycleAge() {
        _state.update { it.copy(showCycleAgeConfirm = false) }
        prepare()
    }

    private fun runForecast(cycleAgeConfirmed: Boolean) {
        val current = _state.value
        val token = current.session
        if (token == null || !current.ready || current.busy) return
        publicationJob?.cancel()
        val revision = generation
        val paramsSnapshot = JSONObject(requestPayload.toString())
        _state.update {
            it.copy(
                showPreflight = false,
                showCycleAgeConfirm = false,
                busy = true,
                checking = false,
                error = "",
                errorPage = null,
                percent = 0.0,
                status = "正在预测",
            )
        }
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            try {
                val forecastParams = JSONObject()
                    .put("session", token)
                    .put("confirmed", true)
                if (cycleAgeConfirmed) {
                    forecastParams.put("cycle_age_confirmed", true)
                }
                val response = engine.call("forecast", forecastParams) { event -> updateProgress(event) }
                ensureActive()
                if (generation != revision) return@launch
                val models = response.optJSONObject("model_results")
                val selected = if (models?.has("gfs") == true) "gfs" else "ec"
                _state.update {
                    it.copy(
                        result = response,
                        resultParams = paramsSnapshot,
                        resultKind = "point",
                        selectedModel = selected,
                        session = null,
                        status = "预测完成",
                    )
                }
            } catch (error: CancellationException) {
                markCancelled(revision)
                throw error
            } catch (error: Exception) {
                if (generation == revision) {
                    _state.update { it.copy(session = null, ready = false) }
                    fail(error, page = "workbench")
                }
            } finally {
                if (generation == revision) {
                    _state.update { it.copy(busy = false) }
                }
            }
        }
    }

    /** 进度三链：event.percent / event.source / event.message（同 Store.updateProgress）。 */
    private fun updateProgress(event: JSONObject) {
        val percent = event.optDouble("percent", 0.0).coerceIn(0.0, 100.0)
        val source = event.optString("source", "")
        val message = event.optString("message", "")
        _state.update {
            val status = if (message.isEmpty()) {
                if (it.checking) "正在准备 $source 数据" else "正在计算 $source"
            } else {
                message
            }
            it.copy(percent = percent, status = status)
        }
    }

    private fun dismissQuietly(token: String) {
        if (token.isEmpty()) return
        viewModelScope.launch {
            runCatching { engine.call("dismiss", JSONObject().put("session", token)) }
        }
    }

    private fun dismissToken() {
        val token = _state.value.session
        _state.update { it.copy(session = null) }
        dismissQuietly(token ?: return)
    }

    fun closePreflight() {
        generation = UUID.randomUUID()
        val token = _state.value.session
        publicationJob?.cancel()
        operationJob?.cancel()
        _state.update {
            it.copy(
                session = null,
                showPreflight = false,
                showCycleAgeConfirm = false,
                checking = false,
                publicationsPending = false,
                busy = false,
                ready = false,
                error = "",
                errorPage = null,
                status = "已取消",
            )
        }
        dismissQuietly(token ?: return)
    }

    fun cancel() {
        operationJob?.cancel()
        _state.update { it.copy(status = "正在取消，等待当前阶段结束") }
    }

    fun clearError() {
        _state.update { it.copy(error = "", errorPage = null, taskHistoryError = "") }
    }

    private fun fail(error: Exception, page: String? = null) {
        val raw = error.message ?: "操作未完成"
        val friendly = friendlyEngineMessage(raw).ifEmpty { raw }
        _state.update {
            it.copy(
                error = friendly,
                errorPage = page ?: it.page,
                status = if (error is CancellationException) "已取消" else "操作未完成",
            )
        }
    }

    // ---------------------------------------------------------------- 设置

    fun setPage(page: String) {
        val previous = _state.value.page
        _state.update { it.copy(page = page) }
        if (page == previous) return
        when (page) {
            "data" -> {
                loadInventory()
                loadTerrain()
            }
            "history" -> loadTaskHistory()
            "settings" -> {
                loadRetentionSettings()
                loadDataSyncSettings()
            }
        }
    }

    /** 设置分类；CAMS 提醒等外部跳转可直接落位（macOS settingsCategory 同构）。 */
    fun setSettingsCategory(value: String) {
        if (value in listOf("通用", "数据", "高级")) {
            _state.update { it.copy(settingsCategory = value) }
        }
    }

    fun setAppearance(value: String) {
        prefs.edit().putString(PREF_APPEARANCE, value).apply()
        _state.update { it.copy(appearance = value) }
    }

    fun setEcmwfSource(value: String) {
        prefs.edit().putString(PREF_ECMWF_SOURCE, value).apply()
        _state.update { it.copy(ecmwfSource = value) }
    }

    /** 海拔来源三档（v1.1.1 §1）：持久化后对下一次 search/metadata 生效。 */
    fun setElevationSource(value: String) {
        val normalized = ElevationSourceSetting.save(prefs, value)
        _state.update { it.copy(elevationSource = normalized) }
    }

    fun setTransportMode(value: String) {
        prefs.edit()
            .putString(PREF_TRANSPORT, value)
            .putString(PREF_CONNECTION_MODE, "local")
            .apply()
        switchEngineAndResetState(
            nextConnectionMode = "local",
            nextConnectionModeDraft = "local",
            nextTransportMode = value,
        )
        connect()
    }

    // ---------------------------------------------------------------- 远程服务器（Mac / CF Tunnel）

    fun setConnectionModeDraft(mode: String) {
        val normalized = if (mode == "remote") "remote" else "local"
        _state.update { it.copy(connectionModeDraft = normalized) }
    }

    fun switchToLocalEngine() {
        val current = _state.value
        if (current.connectionMode == "local" && current.connectionModeDraft == "local") return
        prefs.edit().putString(PREF_CONNECTION_MODE, "local").apply()
        switchEngineAndResetState(
            nextConnectionMode = "local",
            nextConnectionModeDraft = "local",
            nextTransportMode = current.transportMode,
        )
        connect()
    }

    fun setRemoteServerUrl(url: String) {
        _state.update { it.copy(remoteServerUrl = url, remoteTestMessage = "", remoteTestOk = false) }
    }

    fun setRemoteTokenDraft(token: String) {
        _state.update { it.copy(remoteTokenDraft = token, remoteTestMessage = "", remoteTestOk = false) }
    }

    fun clearRemoteToken() {
        prefs.edit().remove(PREF_REMOTE_TOKEN).apply()
        _state.update {
            it.copy(
                remoteTokenDraft = "",
                remoteTokenSaved = false,
                remoteTestMessage = "",
                remoteTestOk = false,
            )
        }
    }

    fun testRemoteServer() {
        val current = _state.value
        if (current.remoteTesting) return
        val url = current.remoteServerUrl
        val token = current.remoteTokenDraft.trim().ifEmpty { savedRemoteToken() }
        _state.update { it.copy(remoteTesting = true, remoteTestMessage = "", remoteTestOk = false) }
        viewModelScope.launch {
            try {
                val status = withContext(Dispatchers.IO) {
                    RemoteHttpTransport.testConnection(url, token)
                }
                val workspaceLabel = status.workspace.ifEmpty { "默认工作区" }
                _state.update {
                    it.copy(
                        remoteTesting = false,
                        remoteTestOk = true,
                        remoteWorkspace = status.workspace,
                        remoteTestMessage = "连接成功 · Mac 工作区：$workspaceLabel",
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        remoteTesting = false,
                        remoteTestOk = false,
                        remoteTestMessage = friendlyEngineMessage(error.message ?: "连接测试失败"),
                    )
                }
            }
        }
    }

    fun saveAndConnectRemoteServer() {
        val current = _state.value
        if (current.remoteTesting) return
        val normalizedUrl = try {
            RemoteHttpTransport.normalizeBaseUrl(current.remoteServerUrl)
        } catch (error: Exception) {
            _state.update {
                it.copy(
                    remoteTestOk = false,
                    remoteTestMessage = error.message ?: "请输入有效的服务器地址。",
                )
            }
            return
        }
        val draftToken = current.remoteTokenDraft.trim()
        val editor = prefs.edit()
            .putString(PREF_REMOTE_SERVER_URL, normalizedUrl)
            .putString(PREF_CONNECTION_MODE, "remote")
        val hasSavedToken = if (draftToken.isNotEmpty()) {
            editor.putString(PREF_REMOTE_TOKEN, draftToken)
            true
        } else {
            current.remoteTokenSaved || savedRemoteToken().isNotEmpty()
        }
        editor.apply()

        switchEngineAndResetState(
            nextConnectionMode = "remote",
            nextConnectionModeDraft = "remote",
            nextTransportMode = current.transportMode,
            nextRemoteServerUrl = normalizedUrl,
            nextRemoteTokenDraft = "",
            nextRemoteTokenSaved = hasSavedToken,
        )
        connect()
    }

    private fun switchEngineAndResetState(
        nextConnectionMode: String,
        nextConnectionModeDraft: String,
        nextTransportMode: String,
        nextRemoteServerUrl: String = _state.value.remoteServerUrl,
        nextRemoteTokenDraft: String = _state.value.remoteTokenDraft,
        nextRemoteTokenSaved: Boolean = _state.value.remoteTokenSaved,
    ) {
        generation = UUID.randomUUID()
        metadataGeneration = UUID.randomUUID()
        operationJob?.cancel()
        publicationJob?.cancel()
        engine.stop()
        taskResultCache.clear()
        _state.update {
            it.copy(
                connectionMode = nextConnectionMode,
                connectionModeDraft = nextConnectionModeDraft,
                transportMode = nextTransportMode,
                remoteServerUrl = nextRemoteServerUrl,
                remoteTokenDraft = nextRemoteTokenDraft,
                remoteTokenSaved = nextRemoteTokenSaved,
                remoteTestMessage = "",
                remoteTestOk = false,
                remoteWorkspace = if (nextConnectionMode == "remote") it.remoteWorkspace else "",
                online = false,
                busy = false,
                checking = false,
                publicationsPending = false,
                session = null,
                rows = emptyList(),
                ready = false,
                showPreflight = false,
                showCycleAgeConfirm = false,
                percent = 0.0,
                status = if (nextConnectionMode == "remote") "正在连接远程 Mac" else "正在连接引擎",
                error = "",
                errorPage = null,
                result = null,
                resultParams = null,
                resultKind = null,
                camsConfiguration = null,
                camsMessage = "",
                camsSaveFailed = false,
                dataSyncLoaded = false,
                dataSyncMessage = "",
                dataSyncSaveFailed = false,
                retentionMessage = "",
                retentionSaveFailed = false,
                taskHistory = emptyList(),
                taskHistoryTotal = 0,
                taskHistoryLoaded = false,
                taskHistoryHasMore = false,
                taskHistoryCursor = null,
                taskHistoryError = "",
                selectedTaskId = null,
                taskEvents = emptyList(),
                taskEventsError = "",
                taskResultTaskId = null,
                taskResult = null,
                taskResultError = "",
                inventory = JSONArray(),
                inventoryCategories = emptyList(),
                terrain = null,
                terrainMessage = "",
                terrainMessageFailed = false,
            )
        }
        engine = buildEngine(_state.value)
    }

    val camsConfigured: Boolean get() = _state.value.camsConfiguration?.optBoolean("configured", false) == true
    val canSaveCAMS: Boolean
        get() = !_state.value.isRemoteMode && _state.value.camsConfiguration?.optBoolean("can_save", false) == true

    val camsConfigurationDescription: String
        get() {
            val configuration = _state.value.camsConfiguration
                ?: return if (_state.value.isRemoteMode) "正在检查远程 Mac 配置" else "正在检查本地配置"
            return when (configuration.optString("state")) {
                "configured" -> "已检测到 Key，下载时验证授权。"
                "wrong_endpoint" -> "接口地址不是 Copernicus ADS，请重新配置。"
                "tls_disabled" -> "配置关闭了 TLS 验证，请重新保存以启用安全连接。"
                "unreadable" -> "配置文件无法读取或格式有误，请修正后重新检查。"
                else -> "尚未配置 CAMS Key，本地数据缺失时无法下载。"
            }
        }

    fun refreshCAMSConfiguration() {
        if (!_state.value.online || _state.value.savingCAMS) return
        viewModelScope.launch {
            try {
                val configuration = engine.call("cams_credentials")
                _state.update { it.copy(camsConfiguration = configuration) }
            } catch (_: Exception) {
                _state.update {
                    it.copy(camsMessage = "无法检查配置，请确认本地引擎已连接。", camsSaveFailed = true)
                }
            }
        }
    }

    fun setCAMSKeyDraft(value: String) {
        _state.update { it.copy(camsKeyDraft = value) }
    }

    fun setShowCAMSTutorial(value: Boolean) {
        _state.update { it.copy(showCAMSTutorial = value) }
    }

    fun saveCAMSKey() {
        val current = _state.value
        if (current.isRemoteMode) {
            _state.update {
                it.copy(
                    camsMessage = "远程模式下不支持通过网络修改 Mac 的 CAMS Key，请在 Mac 端本地配置。",
                    camsSaveFailed = true,
                )
            }
            return
        }
        val key = current.camsKeyDraft.trim()
        if (!_state.value.online || current.busy || current.savingCAMS || !canSaveCAMS || key.isEmpty()) return
        _state.update { it.copy(savingCAMS = true, camsMessage = "", camsKeyDraft = "") }
        viewModelScope.launch {
            try {
                val configuration = engine.call("save_cams_key", JSONObject().put("key", key))
                _state.update {
                    it.copy(
                        camsConfiguration = configuration,
                        camsMessage = "已保存到本机配置文件。",
                        camsSaveFailed = false,
                        savingCAMS = false,
                    )
                }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        camsMessage = error.message ?: "保存失败",
                        camsSaveFailed = true,
                        savingCAMS = false,
                    )
                }
            }
        }
    }

    // ---------------------------------------------------------------- 任务历史（v1.6.0）

    fun loadTaskHistory(older: Boolean = false) {
        val current = _state.value
        if (!current.online || current.taskHistoryLoading) return
        if (older && (current.taskHistoryCursor == null || !current.taskHistoryHasMore)) return
        viewModelScope.launch {
            _state.update { it.copy(taskHistoryLoading = true, taskHistoryError = "") }
            try {
                val params = JSONObject().put("limit", 100)
                if (older) {
                    current.taskHistoryCursor?.let { params.put("before", it) }
                }
                val response = engine.call("task_history", params)
                if (response.optString("schema") != "firesky-task-history-v1") {
                    throw EngineError("任务历史协议不匹配，请重新连接引擎。")
                }
                val tasks = (response.optJSONArray("tasks") ?: JSONArray()).let { array ->
                    (0 until array.length()).mapNotNull { index ->
                        array.optJSONObject(index)?.let { TaskHistoryItem.parse(it) }
                    }
                }
                val total = response.optInt("total", tasks.size)
                _state.update { state ->
                    state.copy(
                        // 加载更早 = 追加；首页 = 替换。
                        taskHistory = if (older) state.taskHistory + tasks else tasks,
                        taskHistoryTotal = total,
                        taskHistoryLoaded = true,
                        taskHistoryHasMore = response.optBoolean("has_more", false),
                        taskHistoryCursor = response.optString("next_cursor", "").ifEmpty { null },
                        taskHistoryError = "",
                    )
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                _state.update { it.copy(taskHistoryError = error.message ?: "任务历史读取失败") }
            } finally {
                _state.update { it.copy(taskHistoryLoading = false) }
            }
        }
    }

    fun selectTask(taskId: String) {
        if (_state.value.selectedTaskId == taskId) {
            _state.update {
                it.copy(
                    selectedTaskId = null, taskEvents = emptyList(), taskEventsError = "",
                    // v1.9.5：取消选中时同步清预测详情。
                    taskResultTaskId = null, taskResult = null,
                    taskResultLoading = false, taskResultError = "",
                )
            }
            return
        }
        if (_state.value.taskHistory.none { it.taskId == taskId }) return
        _state.update {
            it.copy(
                selectedTaskId = taskId,
                taskEvents = emptyList(),
                taskEventsError = "",
                taskEventsLoading = true,
            )
        }
        // v1.9.5：已完成的预测任务随选中项加载预测详情（结果快照）。
        _state.value.taskHistory.firstOrNull { it.taskId == taskId }?.let { loadTaskResult(it) }
        viewModelScope.launch {
            try {
                val response = engine.call(
                    "task_events",
                    JSONObject().put("task_id", taskId).put("after_seq", 0),
                )
                val events = (response.optJSONArray("events") ?: JSONArray()).let { array ->
                    (0 until array.length()).mapNotNull { index ->
                        val item = array.optJSONObject(index) ?: return@mapNotNull null
                        TaskEventRow(
                            seq = item.optInt("seq", 0),
                            at = item.optString("at_utc"),
                            event = item.optString("event"),
                            stage = item.optJSONObject("data")?.optString("stage").orEmpty(),
                            message = item.optJSONObject("data")?.optString("message").orEmpty(),
                            percent = item.optJSONObject("data")?.optDouble("percent")?.takeUnless { it.isNaN() },
                        )
                    }
                }
                _state.update { it.copy(taskEvents = events, taskEventsLoading = false) }
            } catch (error: Exception) {
                _state.update {
                    it.copy(taskEventsError = error.message ?: "事件读取失败", taskEventsLoading = false)
                }
            }
        }
    }

    /** 「删除记录」语义同 macOS：只删除状态与事件，不删除报告与数据。 */
    fun setPendingForgetTask(taskId: String?) {
        _state.update { it.copy(pendingForgetTaskId = taskId) }
    }

    // v1.9.5 任务详情 → 预测详情：快照不可变，会话内按 task_id 缓存。
    private val taskResultCache = mutableMapOf<String, JSONObject>()

    /** 已完成的预测任务经 result_restore 读取不可变结果快照（引擎 SHA256 校验）。 */
    private fun loadTaskResult(item: TaskHistoryItem) {
        if (item.method != "forecast" || item.status != "done") return
        if (_state.value.taskResultTaskId == item.taskId) return
        _state.update { it.copy(taskResultTaskId = item.taskId) }
        taskResultCache[item.taskId]?.let { cached ->
            _state.update { it.copy(taskResult = cached, taskResultLoading = false, taskResultError = "") }
            return
        }
        _state.update { it.copy(taskResult = null, taskResultLoading = true, taskResultError = "") }
        viewModelScope.launch {
            try {
                val response = engine.call(
                    "result_restore",
                    JSONObject().put("task_id", item.taskId),
                )
                val results = response.optJSONObject("model_results")
                if (results == null || results.length() == 0) {
                    throw IllegalStateException("预测详情快照不完整")
                }
                taskResultCache[item.taskId] = response
                _state.update { it.copy(taskResult = response, taskResultLoading = false) }
            } catch (error: Exception) {
                _state.update {
                    it.copy(taskResultError = error.message ?: "预测详情读取失败", taskResultLoading = false)
                }
            }
        }
    }

    fun forgetPendingTask() {
        val taskId = _state.value.pendingForgetTaskId ?: return
        _state.update { it.copy(pendingForgetTaskId = null) }
        val current = _state.value
        if (!current.online || current.busy) return
        if (current.taskHistory.firstOrNull { it.taskId == taskId }?.status == "running") return
        viewModelScope.launch {
            try {
                engine.call("task_forget", JSONObject().put("task_id", taskId))
                if (_state.value.selectedTaskId == taskId) {
                    _state.update { it.copy(selectedTaskId = null, taskEvents = emptyList()) }
                }
                loadTaskHistory()
            } catch (error: Exception) {
                _state.update { it.copy(taskHistoryError = error.message ?: "记录删除失败") }
            }
        }
    }

    /** 恢复已发布结果（同构 macOS restoreTaskResult：回填原始参数 + adoptResult）。 */
    fun restoreTaskResult(item: TaskHistoryItem) {
        val current = _state.value
        if (current.busy || current.restoringTaskId != null || !item.canRestoreResult) return
        _state.update { it.copy(restoringTaskId = item.taskId) }
        viewModelScope.launch {
            try {
                val response = engine.call("result_restore", JSONObject().put("task_id", item.taskId))
                applyRestored(response, item)
            } catch (error: Exception) {
                _state.update {
                    it.copy(taskHistoryError = error.message ?: "结果恢复失败", restoringTaskId = null)
                }
            }
        }
    }

    /** 恢复参数并重新检查（同构 macOS restoreTaskParameters）。 */
    fun restoreTaskParameters(item: TaskHistoryItem) {
        val current = _state.value
        val payload = item.retryPayload ?: return
        if (current.busy || current.restoringTaskId != null || current.online.not()) return
        applyHistoryPayload(payload)
        beginCheck()
    }

    private suspend fun applyRestored(response: JSONObject, item: TaskHistoryItem) {
        val models = response.optJSONObject("model_results") ?: throw EngineError("已发布结果格式无效。")
        val payload = item.retryPayload ?: JSONObject()
        val modelSource = payload.optString("model_source")
        val selected = when {
            modelSource == "gfs" || modelSource == "ec" -> modelSource
            models.has("gfs") -> "gfs"
            else -> "ec"
        }
        applyHistoryPayload(payload)
        _state.update {
            it.copy(
                result = response,
                resultParams = payload,
                resultKind = "point",
                selectedModel = selected,
                restoringTaskId = null,
                page = "workbench",
                status = "已恢复历史结果",
                error = "",
                errorPage = null,
            )
        }
    }

    /** 历史 payload → 表单（name/lat/lon/elevation_m/timezone_name/target_date/phenomenon）。 */
    private fun applyHistoryPayload(payload: JSONObject) {
        val current = _state.value
        val loc = current.location
        val lat = payload.optDouble("lat").takeUnless { it.isNaN() } ?: loc.lat
        val lon = payload.optDouble("lon").takeUnless { it.isNaN() } ?: loc.lon
        val elevation = payload.optDouble("elevation_m").takeUnless { it.isNaN() } ?: loc.elevation
        val timezone = payload.optString("timezone_name", "").ifEmpty { loc.timezone }
        val restoredDate = runCatching { LocalDate.parse(payload.optString("target_date")) }.getOrNull()
        _state.update {
            it.copy(
                location = loc.copy(
                    name = payload.optString("name", "").ifEmpty { loc.name },
                    lat = lat,
                    lon = lon,
                    elevation = elevation,
                    timezone = timezone,
                    metadataReady = true,
                ),
                targetDate = restoredDate ?: current.targetDate,
                dateTab = if (restoredDate != null) "自定义" else current.dateTab,
                phenomenon = payload.optString("phenomenon", "").ifEmpty { current.phenomenon },
            )
        }
    }

    // ---------------------------------------------------------------- 存储与保留 + 后台同步

    fun setRetentionGFS(value: String) = _state.update { it.copy(retentionGFS = value) }
    fun setRetentionECMWF(value: String) = _state.update { it.copy(retentionECMWF = value) }
    fun setRetentionCAMS(value: String) = _state.update { it.copy(retentionCAMS = value) }
    fun setRetentionSatelliteDays(value: String) = _state.update { it.copy(retentionSatelliteDays = value) }
    fun setRetentionReportDays(value: String) = _state.update { it.copy(retentionReportDays = value) }
    fun setRetentionMapDays(value: String) = _state.update { it.copy(retentionMapDays = value) }

    fun loadRetentionSettings() {
        if (!_state.value.online) return
        viewModelScope.launch {
            try {
                val response = engine.call("retention_settings")
                val settings = response.optJSONObject("settings") ?: JSONObject()
                _state.update {
                    it.copy(
                        retentionGFS = retentionText(settings.opt("gfs_cycles")),
                        retentionECMWF = retentionText(settings.opt("ecmwf_cycles")),
                        retentionCAMS = retentionText(settings.opt("cams_cycles")),
                        retentionSatelliteDays = retentionText(settings.opt("satellite_days")),
                        retentionReportDays = retentionText(settings.opt("report_days")),
                        retentionMapDays = retentionText(settings.opt("map_days")),
                        retentionConfigured = response.optBoolean("configured", false),
                        retentionMessage = "",
                        retentionSaveFailed = false,
                    )
                }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        retentionMessage = friendlyEngineMessage(error.message ?: "读取保留设置失败"),
                        retentionSaveFailed = true,
                    )
                }
            }
        }
    }

    fun saveRetentionSettings() {
        val current = _state.value
        if (!current.online || current.busy || current.savingRetention || current.applyingRetention) return
        _state.update { it.copy(savingRetention = true, retentionMessage = "", retentionSaveFailed = false) }
        val settingsObj = JSONObject()
            .put("gfs_cycles", retentionIntegerOrNull(current.retentionGFS))
            .put("ecmwf_cycles", retentionIntegerOrNull(current.retentionECMWF))
            .put("cams_cycles", retentionIntegerOrNull(current.retentionCAMS))
            .put("satellite_days", retentionNumberOrNull(current.retentionSatelliteDays))
            .put("report_days", retentionNumberOrNull(current.retentionReportDays))
            .put("map_days", retentionNumberOrNull(current.retentionMapDays))
        viewModelScope.launch {
            try {
                val response = engine.call(
                    "save_retention_settings",
                    JSONObject().put("settings", settingsObj),
                )
                val settings = response.optJSONObject("settings") ?: JSONObject()
                val configured = response.optBoolean("configured", false)
                _state.update {
                    it.copy(
                        retentionGFS = retentionText(settings.opt("gfs_cycles")),
                        retentionECMWF = retentionText(settings.opt("ecmwf_cycles")),
                        retentionCAMS = retentionText(settings.opt("cams_cycles")),
                        retentionSatelliteDays = retentionText(settings.opt("satellite_days")),
                        retentionReportDays = retentionText(settings.opt("report_days")),
                        retentionMapDays = retentionText(settings.opt("map_days")),
                        retentionConfigured = configured,
                        dataSyncRetentionConfigured = configured,
                        savingRetention = false,
                        retentionSaveFailed = false,
                        retentionMessage = if (configured) {
                            "保留设置已保存；可点击「立即按策略清理」，或等待下一次成功准备后自动清理。"
                        } else {
                            "已清空保留设置。"
                        },
                    )
                }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        savingRetention = false,
                        retentionSaveFailed = true,
                        retentionMessage = friendlyEngineMessage(error.message ?: "保存失败"),
                    )
                }
            }
        }
    }

    /** 立即按已保存的保留策略清理旧数据（跳过占用中文件，并刷新库存）。 */
    fun applyRetentionNow() {
        val current = _state.value
        if (!current.online || current.busy || current.savingRetention || current.applyingRetention) return
        _state.update { it.copy(applyingRetention = true, retentionMessage = "", retentionSaveFailed = false) }
        viewModelScope.launch {
            try {
                val response = engine.call("apply_retention_settings")
                val configured = response.optBoolean("configured", false)
                val result = response.optJSONObject("result")
                if (!configured || result == null) {
                    _state.update {
                        it.copy(
                            applyingRetention = false,
                            retentionSaveFailed = false,
                            retentionMessage = "尚未保存任何保留规则，未执行清理。",
                        )
                    }
                    return@launch
                }
                val removed = result.optInt("removed_count", 0)
                val freedMb = result.optDouble("freed_mb").takeUnless { it.isNaN() } ?: 0.0
                val deferred = result.optInt("deferred_count", 0)
                val failed = result.optInt("failed_count", 0)
                val parts = buildList {
                    if (removed > 0) {
                        add("已清理 $removed 项旧数据，释放 %.1f MB".format(freedMb))
                    } else {
                        add("当前没有超出保留策略的旧数据")
                    }
                    if (deferred > 0) add("跳过 $deferred 项占用中文件")
                    if (failed > 0) add("$failed 项清理失败")
                }
                _state.update {
                    it.copy(
                        applyingRetention = false,
                        retentionSaveFailed = failed > 0,
                        retentionMessage = parts.joinToString(" · ") + "。",
                    )
                }
                loadInventory()
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        applyingRetention = false,
                        retentionSaveFailed = true,
                        retentionMessage = friendlyEngineMessage(error.message ?: "清理失败"),
                    )
                }
            }
        }
    }

    private fun retentionText(value: Any?): String {
        val num = (value as? Number)?.toDouble() ?: return ""
        if (!num.isFinite()) return ""
        return if (num == num.toLong().toDouble()) num.toLong().toString() else "%.2f".format(num)
    }

    private fun retentionIntegerOrNull(value: String): Any {
        val trimmed = value.trim()
        return if (trimmed.isEmpty()) JSONObject.NULL else (trimmed.toIntOrNull() ?: -1)
    }

    private fun retentionNumberOrNull(value: String): Any {
        val trimmed = value.trim()
        return if (trimmed.isEmpty()) JSONObject.NULL else (trimmed.toDoubleOrNull() ?: -1.0)
    }

    fun setDataSyncEnabled(value: Boolean) = _state.update { it.copy(dataSyncEnabled = value) }
    fun setDataSyncCAMS(value: Boolean) = _state.update { it.copy(dataSyncCAMS = value) }
    fun setDataSyncGFS(value: Boolean) = _state.update { it.copy(dataSyncGFS = value) }
    fun setDataSyncEC(value: Boolean) = _state.update { it.copy(dataSyncEC = value) }
    fun setDataSyncInterval(value: String) = _state.update { it.copy(dataSyncInterval = value) }
    fun setDataSyncLeadHorizon(value: String) = _state.update { it.copy(dataSyncLeadHorizon = value) }

    fun loadDataSyncSettings() {
        if (!_state.value.online) return
        viewModelScope.launch {
            try {
                val response = engine.call("data_sync_settings")
                applyDataSyncStatus(response)
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        dataSyncMessage = friendlyEngineMessage(error.message ?: "读取同步设置失败"),
                        dataSyncSaveFailed = true,
                    )
                }
            }
        }
    }

    private fun applyDataSyncStatus(response: JSONObject) {
        val settings = response.optJSONObject("settings") ?: JSONObject()
        val sources = settings.optJSONObject("sources") ?: JSONObject()
        val interval = (settings.opt("interval_minutes") as? Number)?.toInt()?.toString() ?: "15"
        val horizon = (settings.opt("lead_horizon_hours") as? Number)?.toInt()?.toString() ?: "72"
        val stateSources = response.optJSONObject("state")?.optJSONObject("sources") ?: JSONObject()
        _state.update {
            it.copy(
                dataSyncEnabled = settings.optBoolean("enabled", false),
                dataSyncCAMS = sources.optBoolean("cams", false),
                dataSyncGFS = sources.optBoolean("gfs", true),
                dataSyncEC = sources.optBoolean("ec", true),
                dataSyncInterval = interval,
                dataSyncLeadHorizon = horizon,
                dataSyncConfigured = response.optBoolean("configured", false),
                dataSyncRetentionConfigured = response.optBoolean("retention_configured", false),
                dataSyncState = stateSources,
                dataSyncLoaded = true,
            )
        }
    }

    fun saveDataSyncSettings() {
        val current = _state.value
        if (!current.online || current.busy || current.savingDataSync) return
        _state.update { it.copy(savingDataSync = true, dataSyncMessage = "", dataSyncSaveFailed = false) }
        val payload = JSONObject().put(
            "settings",
            JSONObject()
                .put("enabled", current.dataSyncEnabled)
                .put(
                    "sources",
                    JSONObject()
                        .put("cams", current.dataSyncCAMS)
                        .put("gfs", current.dataSyncGFS)
                        .put("ec", current.dataSyncEC),
                )
                .put("interval_minutes", current.dataSyncInterval.trim().toIntOrNull() ?: -1)
                .put("lead_horizon_hours", current.dataSyncLeadHorizon.trim().toIntOrNull() ?: -1)
                .put("ecmwf_source", current.ecmwfSource),
        )
        viewModelScope.launch {
            try {
                val response = engine.call("save_data_sync_settings", payload)
                applyDataSyncStatus(response)
                _state.update {
                    it.copy(
                        savingDataSync = false,
                        dataSyncSaveFailed = false,
                        dataSyncMessage = if (it.dataSyncEnabled) "后台同步设置已保存。" else "后台同步已关闭。",
                    )
                }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        savingDataSync = false,
                        dataSyncSaveFailed = true,
                        dataSyncMessage = friendlyEngineMessage(error.message ?: "保存同步设置失败"),
                    )
                }
            }
        }
    }

    /** 立即同步：显式传启用源并带上 `"force": true` 强制核验最新发布周期。 */
    fun runDataSyncNow() {
        val current = _state.value
        if (!current.online || current.busy || current.dataSyncRunning || current.savingDataSync) return
        val enabledSources = listOf("cams" to current.dataSyncCAMS, "gfs" to current.dataSyncGFS, "ec" to current.dataSyncEC)
            .filter { it.second }
            .map { it.first }
        if (!current.dataSyncEnabled || enabledSources.isEmpty()) {
            _state.update {
                it.copy(
                    dataSyncMessage = "请先启用后台同步并至少选择一个数据源。",
                    dataSyncSaveFailed = true,
                )
            }
            return
        }
        _state.update {
            it.copy(dataSyncRunning = true, dataSyncMessage = "", dataSyncSaveFailed = false, status = "正在立即同步后台数据")
        }
        viewModelScope.launch {
            try {
                val params = JSONObject()
                    .put("sources", JSONArray(enabledSources))
                    .put("force", true)
                val response = engine.call("data_sync_run", params) { event ->
                    if (!_state.value.busy) updateProgress(event)
                }
                val summary = dataSyncRoundSummary(response)
                _state.update {
                    it.copy(
                        dataSyncRunning = false,
                        dataSyncMessage = summary,
                        dataSyncSaveFailed = summary.contains("失败") || summary.contains("未完成"),
                        status = summary,
                    )
                }
                loadDataSyncSettings()
                loadInventory()
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        dataSyncRunning = false,
                        dataSyncMessage = friendlyEngineMessage(error.message ?: "立即同步未完成"),
                        dataSyncSaveFailed = true,
                    )
                }
            }
        }
    }

    private fun dataSyncRoundSummary(response: JSONObject): String {
        val sources = response.optJSONObject("sources") ?: return "立即同步完成。"
        val labels = mapOf("cams" to "CAMS", "gfs" to "GFS", "ec" to "EC")
        val parts = listOf("cams", "gfs", "ec").mapNotNull { key ->
            val row = sources.optJSONObject(key) ?: return@mapNotNull null
            val statusText = when (row.optString("status")) {
                "synced" -> "已同步"
                "up_to_date" -> "已是最新"
                "failed" -> "失败"
                "skipped_busy" -> "让路跳过"
                else -> row.optString("status").ifEmpty { "完成" }
            }
            "${labels[key]} $statusText"
        }
        return if (parts.isEmpty()) "立即同步完成。" else parts.joinToString(" · ")
    }

    // ---------------------------------------------------------------- 数据管理（v1.6.0）

    fun loadInventory() {
        val current = _state.value
        if (!current.online) return
        viewModelScope.launch {
            try {
                val response = engine.call("inventory")
                val datasets = response.optJSONArray("datasets") ?: JSONArray()
                val categoriesArray = response.optJSONObject("categories")?.optJSONArray("categories")
                    ?: response.optJSONArray("categories")
                val categories = if (categoriesArray != null) {
                    (0 until categoriesArray.length()).mapNotNull { categoriesArray.optJSONObject(it) }
                } else {
                    emptyList()
                }
                _state.update {
                    val clearDataError = it.errorPage == "data"
                    it.copy(
                        inventory = datasets,
                        inventoryCategories = categories,
                        error = if (clearDataError) "" else it.error,
                        errorPage = if (clearDataError) null else it.errorPage,
                    )
                }
            } catch (error: Exception) {
                fail(error, page = "data")
            }
        }
    }

    /** 选择范围并请求引擎预览；范围始终是库存周期键，不传磁盘路径。 */
    fun beginDataDelete(source: String, cycle: String?) {
        val current = _state.value
        if (!current.online || current.busy || current.dataDeleteRunning || current.dataDeletePreviewing) return
        val scope = JSONObject().put("source", source)
        if (cycle != null) scope.put("cycle", cycle)
        _state.update {
            it.copy(
                pendingDataDelete = scope,
                dataDeletePreview = null,
                dataDeleteMessage = "",
                dataDeleteFailed = false,
                dataDeletePreviewing = true,
            )
        }
        viewModelScope.launch {
            try {
                val preview = engine.call("data_delete_preview", JSONObject().put("scope", scope))
                _state.update { it.copy(dataDeletePreview = preview, dataDeletePreviewing = false) }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        pendingDataDelete = null,
                        dataDeletePreviewing = false,
                        dataDeleteMessage = error.message ?: "预览失败",
                        dataDeleteFailed = true,
                    )
                }
            }
        }
    }

    fun cancelDataDelete() {
        _state.update { it.copy(pendingDataDelete = null, dataDeletePreview = null) }
    }

    /** 确认后执行；预览过期保留范围并提示重新确认，不静默扩大。 */
    fun executeDataDelete() {
        val current = _state.value
        val scope = current.pendingDataDelete ?: return
        val preview = current.dataDeletePreview ?: return
        val previewId = preview.optString("preview_id")
        if (!current.online || current.dataDeleteRunning || previewId.isEmpty()) return
        _state.update { it.copy(dataDeleteRunning = true) }
        viewModelScope.launch {
            try {
                val result = engine.call(
                    "data_delete_execute",
                    JSONObject().put("scope", scope).put("preview_id", previewId).put("confirm", true),
                )
                val deleted = result.optInt("deleted", 0)
                val skipped = result.optJSONArray("skipped")?.length() ?: 0
                val failed = result.optJSONArray("failed")?.length() ?: 0
                val mb = result.optDouble("freed_mb").takeUnless { it.isNaN() } ?: 0.0
                _state.update {
                    it.copy(
                        pendingDataDelete = null,
                        dataDeletePreview = null,
                        dataDeleteRunning = false,
                        dataDeleteMessage = "已删除 $deleted 个文件，释放 %.1f MB".format(mb) +
                            (if (skipped > 0) "；占用中跳过 $skipped 个" else "") +
                            (if (failed > 0) "；失败 $failed 个" else ""),
                        dataDeleteFailed = failed > 0,
                    )
                }
                loadInventory()
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        dataDeletePreview = null,
                        dataDeleteRunning = false,
                        dataDeleteMessage = error.message ?: "删除失败",
                        dataDeleteFailed = true,
                    )
                }
            }
        }
    }

    // ---------------------------------------------------------------- 地形区域下载（v1.6.0）

    fun loadTerrain() {
        val current = _state.value
        if (!current.online || current.terrainLoading || current.terrainDownloading) return
        viewModelScope.launch {
            _state.update { it.copy(terrainLoading = true) }
            try {
                val response = engine.call(
                    "terrain_inventory",
                    JSONObject().put("lat", current.location.lat).put("lon", current.location.lon),
                )
                // 成功刷新不清空刚设置的下载完成消息；仅失败时覆盖。
                _state.update { it.copy(terrain = response) }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        terrain = null,
                        terrainMessage = error.message ?: "地形状态读取失败",
                        terrainMessageFailed = true,
                    )
                }
            } finally {
                _state.update { it.copy(terrainLoading = false) }
            }
        }
    }

    fun setTerrainRegion(value: String) {
        _state.update { it.copy(terrainRegion = value) }
    }

    /** 请求所选区域估算（HEAD 抽样）；完成后弹确认。 */
    fun loadTerrainEstimate() {
        val current = _state.value
        if (!current.online || current.terrainDownloading || current.terrainEstimateLoading) return
        _state.update { it.copy(terrainEstimateLoading = true) }
        val params = JSONObject()
            .put("estimate_global", true)
            .put("lat", current.location.lat)
            .put("lon", current.location.lon)
        TerrainRegion.fromLabel(current.terrainRegion)?.bounds?.let { params.put("bounds", it) }
        viewModelScope.launch {
            try {
                val response = engine.call("terrain_inventory", params)
                _state.update { it.copy(terrainEstimate = response, showTerrainConfirm = true) }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        terrainEstimate = null,
                        terrainMessage = error.message ?: "估算失败",
                        terrainMessageFailed = true,
                    )
                }
            } finally {
                _state.update { it.copy(terrainEstimateLoading = false) }
            }
        }
    }

    fun dismissTerrainConfirm() {
        _state.update { it.copy(showTerrainConfirm = false) }
    }

    /** 批量（区域/全球）下载：跳过已有、可取消续传；进度经 terrain 事件回填。 */
    fun downloadTerrainRegion(tiles: List<String>? = null) {
        val current = _state.value
        if (!current.online || current.busy || current.terrainDownloading) return
        _state.update {
            it.copy(
                terrainDownloading = true,
                terrainCancelling = false,
                terrainMessage = "",
                terrainProgress = JSONObject(),
                showTerrainConfirm = false,
            )
        }
        val params = JSONObject().put("mode", "global").put("timeout_seconds", 300)
        if (!tiles.isNullOrEmpty()) {
            params.put("tiles", JSONArray(tiles))
        } else {
            TerrainRegion.fromLabel(current.terrainRegion)?.bounds?.let { params.put("bounds", it) }
        }
        viewModelScope.launch {
            try {
                val result = engine.call("terrain_download", params) { event ->
                    val terrain = event.optJSONObject("terrain") ?: return@call
                    _state.update { it.copy(terrainProgress = terrain) }
                }
                val failedCount = result.optInt("failed", 0)
                val failedTilesArray = result.optJSONArray("failed_tiles")
                val failedTiles = if (failedTilesArray != null) {
                    (0 until failedTilesArray.length()).mapNotNull { failedTilesArray.optString(it).ifEmpty { null } }
                } else {
                    emptyList()
                }
                _state.update { state ->
                    state.copy(
                        terrainLastFailedCount = failedCount,
                        terrainLastFailedRegion = if (failedCount > 0) state.terrainRegion else null,
                        terrainLastFailedTiles = if (failedCount > 0) failedTiles else emptyList(),
                        terrainMessage = terrainResultMessage(result, state.terrainRegion),
                        terrainMessageFailed = terrainFailed(result),
                    )
                }
            } catch (error: CancellationException) {
                _state.update {
                    it.copy(
                        terrainMessage = "已停止下载；已完成的瓦片保留，重新开始会跳过它们。",
                        terrainMessageFailed = false,
                    )
                }
            } catch (error: Exception) {
                _state.update {
                    it.copy(
                        terrainMessage = friendlyEngineMessage(error.message ?: "下载失败"),
                        terrainMessageFailed = true,
                    )
                }
            } finally {
                _state.update { it.copy(terrainDownloading = false, terrainCancelling = false) }
                loadTerrain()
            }
        }
    }

    /** 仅重试上次区域下载失败的瓦片（如引擎返回了失败清单则定向重拉）。 */
    fun retryFailedTerrainTiles() {
        val current = _state.value
        val region = current.terrainLastFailedRegion ?: current.terrainRegion
        if (region != current.terrainRegion) {
            _state.update { it.copy(terrainRegion = region) }
        }
        val tiles = current.terrainLastFailedTiles.takeIf { it.isNotEmpty() }
        downloadTerrainRegion(tiles = tiles)
    }

    /** 点级下载（次级入口）：仅当前地点对应瓦片。 */
    fun downloadTerrainPoint() {
        val current = _state.value
        if (!current.online || current.busy || current.terrainDownloading) return
        _state.update {
            it.copy(
                terrainDownloading = true,
                terrainCancelling = false,
                terrainMessage = "",
                terrainProgress = JSONObject(),
            )
        }
        val params = JSONObject()
            .put("lat", current.location.lat)
            .put("lon", current.location.lon)
            .put("timeout_seconds", 600)
        viewModelScope.launch {
            try {
                val result = engine.call("terrain_download", params)
                _state.update { state ->
                    val status = result.optString("status")
                    state.copy(
                        terrainMessage = when (status) {
                            "exists" -> "地形瓦片已存在，直接复用本地数据。"
                            "downloaded" -> "已下载地形瓦片 ${result.optString("tile")}。"
                            "out_of_coverage" -> result.optString("message", "该位置不在 SRTM 覆盖范围内。")
                            else -> "下载未完成。"
                        },
                        terrainMessageFailed = status == "out_of_coverage" || status == "failed",
                    )
                }
            } catch (error: CancellationException) {
                _state.update { it.copy(terrainMessage = "已停止下载。", terrainMessageFailed = false) }
            } catch (error: Exception) {
                _state.update { it.copy(terrainMessage = error.message ?: "下载失败", terrainMessageFailed = true) }
            } finally {
                _state.update { it.copy(terrainDownloading = false, terrainCancelling = false) }
                loadTerrain()
            }
        }
    }

    fun cancelTerrainDownload() {
        if (!_state.value.terrainDownloading) return
        _state.update { it.copy(terrainCancelling = true) }
    }

    private fun terrainResultMessage(result: JSONObject, region: String): String {
        val downloaded = result.optInt("downloaded", 0)
        val absent = result.optInt("absent", 0)
        val failed = result.optInt("failed", 0)
        val gb = result.optDouble("received_gb").takeUnless { it.isNaN() } ?: 0.0
        val summary = "新增 $downloaded 块、缺失 $absent 块、失败 $failed 块，占用 %.1f GB。".format(gb)
        return when (result.optString("status")) {
            "cancelled" -> if (result.optString("stopped_reason") == "disk_space") {
                "$region 下载因磁盘空间不足停止：$summary"
            } else {
                "$region 下载已停止：$summary"
            }
            else -> if (failed > 0) "$region 下载完成（部分失败）：$summary" else "$region 下载完成：$summary"
        }
    }

    private fun terrainFailed(result: JSONObject): Boolean =
        result.optInt("failed", 0) > 0 || result.optString("stopped_reason") == "disk_space"

    // ---------------------------------------------------------------- 生命周期

    override fun onCleared() {
        operationJob?.cancel()
        publicationJob?.cancel()
        engine.stop()
        super.onCleared()
    }

    private companion object {
        const val PREF_APPEARANCE = "appearance"
        const val PREF_ECMWF_SOURCE = "ecmwfSource"
        const val PREF_TRANSPORT = "transportMode"
        const val PREF_FAVORITES = "favorites"
        const val PREF_CONNECTION_MODE = "connectionMode"
        const val PREF_REMOTE_SERVER_URL = "remoteServerUrl"
        const val PREF_REMOTE_TOKEN = "remoteToken"
    }
}
