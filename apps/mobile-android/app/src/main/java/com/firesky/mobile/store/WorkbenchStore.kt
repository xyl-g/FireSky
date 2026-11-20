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
        _state.update { it.copy(page = page) }
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
