package com.firesky.mobile.store

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.firesky.mobile.engine.EngineClient
import com.firesky.mobile.engine.EngineError
import com.firesky.mobile.engine.EngineTransport
import com.firesky.mobile.engine.JniTransport
import com.firesky.mobile.engine.MockTransport
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

/** 数据会话行（同构 apps/macos PreparationActions.swift DataRow）。 */
data class DataRow(
    val source: String,
    val ready: Boolean,
    val old: Boolean,
    val verified: Boolean,
    val missing: Int,
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
    val detail: String get() = when {
        !ready -> if (missing > 0) "本次预测数据不完整" else "尚未下载本次预测数据"
        old -> "有新发布的数据可更新"
        verified -> "发布周期已核验"
        else -> "更新待核验"
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
    val online: Boolean = false,
    val percent: Double = 0.0,
    val showPreflight: Boolean = false,
    val publicationsPending: Boolean = false,
    val session: String? = null,
    val rows: List<DataRow> = emptyList(),
    val ready: Boolean = false,
    // 结果卡片
    val result: JSONObject? = null,
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
    // 任务历史（v1.6.0 与 macOS 任务历史页对齐）
    val taskHistory: List<TaskHistoryItem> = emptyList(),
    val taskHistoryTotal: Int = 0,
    val taskHistoryLoaded: Boolean = false,
    val taskHistoryLoading: Boolean = false,
    val taskHistoryError: String = "",
    val selectedTaskId: String? = null,
    val taskEvents: List<TaskEventRow> = emptyList(),
    val taskEventsLoading: Boolean = false,
    val taskEventsError: String = "",
    val pendingForgetTaskId: String? = null,
    val restoringTaskId: String? = null,
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
    // 本地数据清单（库存）
    val inventory: JSONArray = JSONArray(),
    // 设置分类（通用/数据/位置与海拔/高级）
    val settingsCategory: String = "通用",
)

/**
 * 单点工作台 ViewModel —— 同构 Store.swift 的 point 作用域
 * （connect/search/metadata→check→publications→prepare→confirm 进度三链、取消、CAMS 设置）。
 */
class WorkbenchStore(application: Application) : AndroidViewModel(application) {

    private val prefs = application.getSharedPreferences("firesky", Application.MODE_PRIVATE)

    private val _state = MutableStateFlow(WorkbenchState())
    val state: StateFlow<WorkbenchState> = _state.asStateFlow()

    private var engine: EngineClient = buildEngine(_state.value.transportMode)

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
        val favorites = runCatching {
            val array = JSONArray(prefs.getString(PREF_FAVORITES, "[]") ?: "[]")
            (0 until array.length()).mapNotNull { index -> array.optJSONObject(index) }
        }.getOrDefault(emptyList())
        _state.value = _state.value.copy(
            appearance = appearance,
            ecmwfSource = ecmwfSource,
            elevationSource = elevationSource,
            transportMode = transportMode,
            favorites = favorites,
        )
        engine = buildEngine(transportMode)
        viewModelScope.launch { connect() }
    }

    private fun buildEngine(mode: String): EngineClient =
        EngineClient(transport(mode))

    private fun transport(mode: String): EngineTransport = when (mode) {
        "ffi" -> JniTransport(getApplication())
        else -> MockTransport()
    }

    /**
     * FFI 引擎启动配置（Mock 忽略）：workspace/cache/definitions 均落在沙箱
     * filesDir；definitions 由 JniTransport 先把 assets/eccodes/definitions 解压到
     * filesDir 再交 fsm_start（引擎侧先注入单数 ECCODES_DEFINITION_PATH 再初始化）。
     */
    private fun engineConfig(): JSONObject {
        val files = getApplication<Application>().filesDir
        return JSONObject()
            .put("transport", _state.value.transportMode)
            .put("workspace_root", files.absolutePath)
            .put("cache_root", File(files, "cache").absolutePath)
            .put("definitions_dir", File(files, "eccodes/definitions").absolutePath)
    }

    // ---------------------------------------------------------------- 连接

    fun connect() {
        val current = _state.value
        if (current.busy) return
        _state.update { it.copy(online = false, error = "") }
        viewModelScope.launch {
            try {
                engine.start(engineConfig())
                val hello = engine.call("hello")
                if (hello.optInt("protocol") != 1) throw EngineError("引擎协议版本不匹配。")
                val capabilities = (hello.optJSONArray("capabilities") ?: JSONArray()).let { array ->
                    (0 until array.length()).map { index -> array.optString(index) }.toSet()
                }
                _state.update {
                    it.copy(
                        online = true,
                        status = "引擎已连接",
                        error = "",
                        camsConfiguration = hello.optJSONObject("cams_credentials"),
                    )
                }
                // MN2 会话恢复（协议参照 macOS Store.connect/loadTaskHistory/restoreTaskResult）：
                // hello 后拉任务历史，自动恢复最近一次可恢复的单点 forecast 结果。
                if ("task_history" in capabilities && "result_restore" in capabilities) {
                    restoreLatestResult()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                _state.update {
                    it.copy(online = false, status = "引擎未连接", error = error.message ?: "引擎未连接")
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
                        selectedModel = selected,
                        status = "已恢复历史结果",
                        error = "",
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
        _state.update { it.copy(busy = true, status = "正在查找位置") }
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
                fail(error)
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
                result = null,
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
                result = null,
                status = "位置已就绪",
            )
        }
    }

    /** MN3 真机阶段接入系统定位（需 ACCESS_FINE_LOCATION 运行时权限）；当前显式说明。 */
    fun locate() {
        _state.update {
            it.copy(error = "定位功能在 MN3 真机阶段接入；当前请用搜索、收藏或手动坐标。")
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
                        error = error.message ?: "海拔未获取",
                        status = "海拔未获取，可手动填写",
                    )
                }
            }
        }
    }

    fun starLocation() {
        val current = _state.value
        if (!current.location.metadataReady) {
            _state.update { it.copy(error = "请先确认海拔与时区。") }
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
        _state.update { it.copy(dateTab = tab, targetDate = date, result = null) }
    }

    fun setCustomDate(date: LocalDate) {
        _state.update { it.copy(dateTab = "自定义", targetDate = date, result = null) }
    }

    fun setPhenomenon(value: String) {
        _state.update { it.copy(phenomenon = value, result = null) }
    }

    fun resetParameters() {
        val zone = runCatching { ZoneId.of(_state.value.location.timezone) }
            .getOrDefault(ZoneId.systemDefault())
        _state.update {
            it.copy(dateTab = "今天", targetDate = LocalDate.now(zone), phenomenon = "dusk", result = null)
        }
    }

    fun setEditingCoordinates(value: Boolean) {
        _state.update { it.copy(editingCoordinates = value) }
    }

    fun editLocation(transform: (LocationState) -> LocationState) {
        _state.update { it.copy(location = transform(it.location), result = null) }
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
            _state.update { it.copy(error = "请先获取海拔与时区，或手动确认位置数据。") }
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
                showPreflight = true,
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
                if (generation == revision) fail(error)
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
            it.copy(busy = true, checking = true, percent = 0.0, status = "正在检查本地数据", rows = emptyList())
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
                if (generation == revision) fail(error)
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
                DataRow(
                    source = item.optString("source"),
                    ready = item.optBoolean("ready", false),
                    old = item.optBoolean("verified", false) && !item.optBoolean("is_latest", false),
                    verified = item.optBoolean("verified", false),
                    missing = item.optInt("missing_count", 0),
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
            it.copy(busy = true, checking = true, ready = false, percent = 0.0, error = "", status = "正在准备数据")
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
                if (generation == revision) fail(error)
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
        publicationJob?.cancel()
        val revision = generation
        _state.update {
            it.copy(showPreflight = false, busy = true, checking = false, error = "", percent = 0.0, status = "正在预测")
        }
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            try {
                val response = engine.call(
                    "forecast",
                    JSONObject().put("session", token).put("confirmed", true),
                ) { event -> updateProgress(event) }
                ensureActive()
                if (generation != revision) return@launch
                val models = response.optJSONObject("model_results")
                val selected = if (models?.has("gfs") == true) "gfs" else "ec"
                _state.update {
                    it.copy(
                        result = response,
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
                    fail(error)
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
                checking = false,
                publicationsPending = false,
                busy = false,
                ready = false,
                error = "",
                status = "已取消",
            )
        }
        dismissQuietly(token ?: return)
    }

    fun cancel() {
        operationJob?.cancel()
        _state.update { it.copy(status = "正在取消，等待当前阶段结束") }
    }

    private fun fail(error: Exception) {
        _state.update {
            it.copy(
                error = error.message ?: "操作未完成",
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
        }
    }

    /** 设置分类；CAMS 提醒等外部跳转可直接落位（macOS settingsCategory 同构）。 */
    fun setSettingsCategory(value: String) {
        if (value in listOf("通用", "数据", "位置与海拔", "高级")) {
            _state.update { it.copy(settingsCategory = value) }
        }
    }

    fun setAppearance(value: String) {
        prefs.edit().putString(PREF_APPEARANCE, value).apply()
        _state.update { it.copy(appearance = value) }
    }

    fun setEcmwfSource(value: String) {
        prefs.edit().putString(PREF_ECMWF_SOURCE, value).apply()
        _state.update { it.copy(ecmwfSource = value, result = null) }
    }

    /** 海拔来源三档（v1.1.1 §1）：持久化后对下一次 search/metadata 生效。 */
    fun setElevationSource(value: String) {
        val normalized = ElevationSourceSetting.save(prefs, value)
        _state.update { it.copy(elevationSource = normalized) }
    }

    fun setTransportMode(value: String) {
        prefs.edit().putString(PREF_TRANSPORT, value).apply()
        engine.stop()
        engine = buildEngine(value)
        _state.update { it.copy(transportMode = value, result = null) }
        connect()
    }

    val camsConfigured: Boolean get() = _state.value.camsConfiguration?.optBoolean("configured", false) == true
    val canSaveCAMS: Boolean get() = _state.value.camsConfiguration?.optBoolean("can_save", false) == true

    val camsConfigurationDescription: String
        get() {
            val configuration = _state.value.camsConfiguration ?: return "正在检查本地配置"
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

    fun loadTaskHistory() {
        val current = _state.value
        if (!current.online || current.taskHistoryLoading) return
        viewModelScope.launch {
            _state.update { it.copy(taskHistoryLoading = true, taskHistoryError = "") }
            try {
                val response = engine.call("task_history", JSONObject().put("limit", 100))
                if (response.optString("schema") != "firesky-task-history-v1") {
                    throw EngineError("任务历史协议不匹配，请重新连接引擎。")
                }
                val tasks = (response.optJSONArray("tasks") ?: JSONArray()).let { array ->
                    (0 until array.length()).mapNotNull { index ->
                        array.optJSONObject(index)?.let { TaskHistoryItem.parse(it) }
                    }
                }
                val total = response.optInt("total", tasks.size)
                _state.update {
                    it.copy(
                        taskHistory = tasks,
                        taskHistoryTotal = total,
                        taskHistoryLoaded = true,
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
                it.copy(selectedTaskId = null, taskEvents = emptyList(), taskEventsError = "")
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
                selectedModel = selected,
                restoringTaskId = null,
                status = "已恢复历史结果",
                error = "",
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
                result = null,
            )
        }
    }

    // ---------------------------------------------------------------- 数据管理（v1.6.0）

    fun loadInventory() {
        val current = _state.value
        if (!current.online) return
        viewModelScope.launch {
            try {
                val response = engine.call("inventory")
                val datasets = response.optJSONArray("datasets") ?: JSONArray()
                _state.update { it.copy(inventory = datasets, error = "") }
            } catch (_: Exception) {
                // 库存读取失败保留上一次内容；错误行显示全局 error。
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
                _state.update { it.copy(terrain = response, terrainMessage = "", terrainMessageFailed = false) }
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
    fun downloadTerrainRegion() {
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
        TerrainRegion.fromLabel(current.terrainRegion)?.bounds?.let { params.put("bounds", it) }
        viewModelScope.launch {
            try {
                val result = engine.call("terrain_download", params) { event ->
                    val terrain = event.optJSONObject("terrain") ?: return@call
                    _state.update { it.copy(terrainProgress = terrain) }
                }
                _state.update { state ->
                    state.copy(
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
                    it.copy(terrainMessage = error.message ?: "下载失败", terrainMessageFailed = true)
                }
            } finally {
                _state.update { it.copy(terrainDownloading = false, terrainCancelling = false) }
                loadTerrain()
            }
        }
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
    }
}
