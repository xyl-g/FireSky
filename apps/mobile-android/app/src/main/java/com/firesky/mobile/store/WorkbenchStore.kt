package com.firesky.mobile.store

import android.app.Application
import androidx.annotation.VisibleForTesting
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.firesky.mobile.engine.EngineClient
import com.firesky.mobile.engine.EngineError
import com.firesky.mobile.engine.EngineTransport
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
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

/**
 * FireSky 远程薄客户端 ViewModel —— 负责远程服务器连接/配对、单点/区域/卫星实况
 * 三模式工作台（check→publications→prepare→forecast）、任务历史与恢复、数据清单与地形管理。
 */
class WorkbenchStore private constructor(
    application: Application,
    private val injectedTransport: EngineTransport?,
) : AndroidViewModel(application) {

    constructor(application: Application) : this(application, null)

    private val prefs = application.getSharedPreferences("firesky", Application.MODE_PRIVATE)

    private val _state = MutableStateFlow(WorkbenchState())
    val state: StateFlow<WorkbenchState> = _state.asStateFlow()

    private var engine: EngineClient? = null

    private var operationJob: Job? = null
    private var publicationJob: Job? = null
    private var metadataGeneration = UUID.randomUUID()
    private var generation = UUID.randomUUID()
    private var requestKind: String = "point"
    private var requestPayload: JSONObject = JSONObject()
    private val taskResultCache = mutableMapOf<String, JSONObject>()

    init {
        val appearance = prefs.getString(PREF_APPEARANCE, null) ?: "system"
        val ecmwfSource = prefs.getString(PREF_ECMWF_SOURCE, null) ?: "ecmwf"
        val elevationSource = ElevationSourceSetting.load(prefs)
        val remoteServerUrl = prefs.getString(PREF_REMOTE_SERVER_URL, null) ?: ""
        val remoteTokenSaved = !prefs.getString(PREF_REMOTE_TOKEN, null).isNullOrEmpty()
        val favorites = runCatching {
            val array = JSONArray(prefs.getString(PREF_FAVORITES, "[]") ?: "[]")
            (0 until array.length()).mapNotNull { index -> array.optJSONObject(index) }
        }.getOrDefault(emptyList())
        val hasMock = injectedTransport != null
        val configured = hasMock || remoteServerUrl.isNotBlank()

        _state.value = _state.value.copy(
            appearance = appearance,
            ecmwfSource = ecmwfSource,
            elevationSource = elevationSource,
            serverConfigured = configured,
            isRemoteTransport = !hasMock,
            remoteServerUrl = remoteServerUrl,
            remoteTokenSaved = remoteTokenSaved,
            favorites = favorites,
            status = if (configured) "正在连接 FireSky 服务器" else "请先连接 FireSky 服务器",
        )
        if (configured) {
            engine = buildEngine(_state.value)
            viewModelScope.launch { connect() }
        }
    }

    private fun savedRemoteToken(): String = prefs.getString(PREF_REMOTE_TOKEN, null).orEmpty()

    private fun buildEngine(state: WorkbenchState = _state.value): EngineClient {
        val transport = injectedTransport ?: RemoteHttpTransport(
            baseUrl = state.remoteServerUrl,
            token = savedRemoteToken(),
        )
        return EngineClient(transport)
    }

    private fun engineConfig(): JSONObject {
        val current = _state.value
        val config = JSONObject().put("connection_mode", "remote")
        if (current.remoteServerUrl.isNotBlank()) {
            config.put("base_url", current.remoteServerUrl)
            config.put("token", savedRemoteToken())
        }
        return config
    }

    // ---------------------------------------------------------------- 连接与同步

    fun connect() {
        val current = _state.value
        if (current.busy) return
        if (injectedTransport == null && current.remoteServerUrl.isBlank()) {
            _state.update {
                it.copy(
                    serverConfigured = false,
                    online = false,
                    status = "请先连接 FireSky 服务器",
                    error = "",
                    errorPage = null,
                )
            }
            return
        }
        val activeEngine = engine ?: buildEngine(current).also { engine = it }
        _state.update { it.copy(online = false, error = "", errorPage = null, status = "正在连接 FireSky 服务器") }
        viewModelScope.launch {
            try {
                activeEngine.start(engineConfig())
                val hello = activeEngine.call("hello")
                if (hello.optInt("protocol") != 1) throw EngineError("引擎协议版本不匹配。")
                val capabilities = (hello.optJSONArray("capabilities") ?: JSONArray()).let { array ->
                    (0 until array.length()).map { index -> array.optString(index) }.toSet()
                }
                val workspace = hello.optString("workspace", "")
                val regions = parseRegionalPresets(hello)
                _state.update { state ->
                    val nextRegionCode = if (regions.any { it.code == state.selectedRegionCode }) {
                        state.selectedRegionCode
                    } else {
                        regions.firstOrNull { it.code == "china" }?.code
                            ?: regions.firstOrNull()?.code
                            ?: "china"
                    }
                    val remoteMsg = if (workspace.isNotEmpty()) {
                        "已连接到服务器（工作区：$workspace）"
                    } else {
                        "已连接到 FireSky 服务器"
                    }
                    state.copy(
                        serverConfigured = true,
                        online = true,
                        status = if (workspace.isNotEmpty()) "已连接 · $workspace" else "已连接 FireSky 服务器",
                        error = "",
                        errorPage = null,
                        availableRegions = regions,
                        selectedRegionCode = nextRegionCode,
                        camsConfiguration = hello.optJSONObject("cams_credentials"),
                        remoteWorkspace = workspace,
                        remoteTestOk = true,
                        remoteTestMessage = remoteMsg,
                    )
                }
                if ("data_sync" in capabilities) {
                    loadDataSyncSettings()
                }
                loadRetentionSettings()
                loadInventory()
                if ("task_history" in capabilities) {
                    loadTaskHistory()
                }
                if ("task_history" in capabilities && "result_restore" in capabilities) {
                    restoreLatestResult()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                val friendly = friendlyEngineMessage(error.message ?: "服务器未连接")
                _state.update {
                    it.copy(
                        online = false,
                        status = "服务器未连接",
                        error = friendly,
                        errorPage = null,
                        remoteTestOk = false,
                        remoteTestMessage = friendly,
                    )
                }
            }
        }
    }

    private suspend fun restoreLatestResult() {
        val activeEngine = engine ?: return
        val snapshot = _state.value
        if (snapshot.busy || snapshot.result != null) return
        val revision = generation
        try {
            val history = activeEngine.call("task_history", JSONObject().put("limit", 20))
            if (history.optString("schema") != "firesky-task-history-v1") return
            val tasks = history.optJSONArray("tasks") ?: return
            for (index in 0 until tasks.length()) {
                val raw = tasks.optJSONObject(index) ?: continue
                val item = TaskHistoryItem.parse(raw) ?: continue
                if (item.method != "forecast" || item.status != "done" || !item.canRestoreResult) continue
                val retry = item.retryPayload ?: continue
                val kind = retry.optString("kind").ifEmpty { "point" }
                if (kind !in listOf("point", "nowcast")) continue
                val response = activeEngine.call("result_restore", JSONObject().put("task_id", item.taskId))
                val current = _state.value
                if (current.busy || current.result != null || generation != revision) return
                if (kind != "nowcast" && response.optJSONObject("model_results") == null) continue
                applyHistoryPayload(retry, kind)
                val selected = if (kind == "nowcast") "gfs" else pickSelectedModel(response, retry)
                _state.update { state ->
                    state.copy(
                        workbenchMode = kind,
                        result = response,
                        resultParams = retry,
                        resultKind = kind,
                        selectedModel = selected,
                        regionalModel = if (kind == "regional") selected else state.regionalModel,
                        selectedRegionalCell = null,
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
            // 历史恢复失败不阻断连接
        }
    }

    // ---------------------------------------------------------------- 工作台模式与区域控制

    fun setWorkbenchMode(mode: String) {
        if (mode !in listOf("point", "regional", "nowcast")) return
        _state.update {
            it.copy(
                workbenchMode = mode,
                resultTab = "概览",
                error = if (it.errorPage == "workbench") "" else it.error,
                errorPage = if (it.errorPage == "workbench") null else it.errorPage,
            )
        }
    }

    fun selectRegionalPreset(code: String) {
        _state.update { it.copy(selectedRegionCode = code) }
    }

    fun setRegionalModel(model: String) {
        val normalized = if (model == "ec") "ec" else "gfs"
        _state.update {
            it.copy(
                regionalModel = normalized,
                selectedModel = if (it.workbenchMode == "regional") normalized else it.selectedModel,
                selectedRegionalCell = null,
            )
        }
    }

    fun selectRegionalCell(cell: RegionalCell?) {
        _state.update { it.copy(selectedRegionalCell = cell) }
    }

    fun setRegionalInspectorMode(mode: String) {
        val normalized = if (mode == "grid") "grid" else "cities"
        _state.update { it.copy(regionalInspectorMode = normalized) }
    }

    fun setRegionalQuery(query: String) {
        _state.update { it.copy(regionalQuery = query) }
    }

    fun openRegionalCellInPointForecast(cell: RegionalCell, model: String) {
        _state.update { state ->
            state.copy(
                location = state.location.copy(
                    name = cell.label,
                    lat = cell.lat,
                    lon = cell.lon,
                    timezone = cell.timezone ?: state.location.timezone,
                    metadataReady = true,
                ),
                workbenchMode = "point",
                selectedModel = if (model == "ec") "ec" else "gfs",
                selectedRegionalCell = null,
            )
        }
    }

    // ---------------------------------------------------------------- 位置

    fun search() {
        val activeEngine = engine ?: return
        val text = _state.value.query.trim()
        if (text.isEmpty() || _state.value.busy) return
        _state.update { it.copy(busy = true, status = "正在查找位置", error = "", errorPage = null) }
        viewModelScope.launch {
            try {
                val point = activeEngine.call(
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
        val clampedLat = lat.coerceIn(-85.0, 85.0)
        val normLon = ((lon + 540.0) % 360.0) - 180.0
        val latLabel = if (clampedLat >= 0) "%.2f°N".format(clampedLat) else "%.2f°S".format(-clampedLat)
        val lonLabel = if (normLon >= 0) "%.2f°E".format(normLon) else "%.2f°W".format(-normLon)
        _state.update {
            it.copy(
                location = it.location.copy(
                    lat = clampedLat,
                    lon = normLon,
                    name = "$latLabel, $lonLabel",
                    metadataReady = false,
                ),
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
            current.copy(
                location = applyPayloadToLocation(current.location, point),
                status = "位置已就绪",
            )
        }
    }

    fun locate() {
        _state.update {
            it.copy(
                error = "请使用位置搜索、收藏点或手动输入坐标。",
                errorPage = "workbench",
            )
        }
    }

    fun lookupMetadata() {
        val activeEngine = engine ?: return
        val revision = UUID.randomUUID()
        metadataGeneration = revision
        val lat = _state.value.location.lat
        val lon = _state.value.location.lon
        _state.update { it.copy(location = it.location.copy(metadataReady = false), status = "正在获取海拔与时区") }
        viewModelScope.launch {
            try {
                val point = activeEngine.call(
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

    private fun markCancelled(revision: UUID) {
        if (generation == revision) {
            _state.update { it.copy(status = "已取消") }
        }
    }

    fun beginCheck() {
        val current = _state.value
        val activeEngine = engine ?: return
        if (current.busy || !current.online) return
        if (current.workbenchMode != "regional" && !current.location.metadataReady) {
            _state.update { it.copy(error = "请先获取海拔与时区，或手动确认位置数据。", errorPage = "workbench") }
            return
        }
        generation = UUID.randomUUID()
        val revision = generation
        requestKind = current.workbenchMode
        requestPayload = buildForecastPayload(current, requestKind)
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
                status = "正在检查服务端数据",
            )
        }
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            try {
                val response = activeEngine.call(
                    "check",
                    JSONObject().put("kind", requestKind).put("payload", requestPayload),
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
        val activeEngine = engine ?: return
        val revision = generation
        _state.update {
            it.copy(
                busy = true,
                checking = true,
                percent = 0.0,
                error = "",
                errorPage = null,
                status = "正在检查服务端数据",
                rows = emptyList(),
            )
        }
        operationJob?.cancel()
        operationJob = viewModelScope.launch {
            try {
                val response = activeEngine.call(
                    "check",
                    JSONObject().put("kind", requestKind).put("payload", requestPayload),
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
        val activeEngine = engine ?: return
        _state.update { it.copy(publicationsPending = true) }
        publicationJob?.cancel()
        publicationJob = viewModelScope.launch {
            try {
                val response = activeEngine.call("publications", JSONObject().put("session", token))
                val snapshot = _state.value
                if (generation != revision || !snapshot.showPreflight || snapshot.busy) return@launch
                applyReport(response)
            } catch (_: Exception) {
                // 发布源中断不影响本地可用性结论
            } finally {
                if (generation == revision) {
                    _state.update { it.copy(publicationsPending = false) }
                }
            }
        }
    }

    private fun applyReport(response: JSONObject) {
        val report = response.optJSONObject("report") ?: JSONObject()
        val (ready, rows) = parseDataReportRows(report)
        val missing = rows.filter { !it.ready }.joinToString("、") { it.title }
        _state.update {
            it.copy(
                ready = ready,
                rows = rows,
                status = if (ready) "服务端数据已就绪" else "缺少 $missing",
            )
        }
    }

    fun prepare() {
        val activeEngine = engine ?: return
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
                val response = activeEngine.call(
                    "prepare",
                    JSONObject().put("session", token),
                ) { event -> updateProgress(event) }
                ensureActive()
                if (generation != revision) return@launch
                applyReport(response)
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
        val activeEngine = engine ?: return
        val current = _state.value
        val token = current.session
        if (token == null || !current.ready || current.busy) return
        publicationJob?.cancel()
        val revision = generation
        val kind = requestKind
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
                status = when (kind) {
                    "regional" -> "正在计算区域预测"
                    "nowcast" -> "正在分析卫星实况"
                    else -> "正在预测"
                },
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
                val response = activeEngine.call("forecast", forecastParams) { event -> updateProgress(event) }
                ensureActive()
                if (generation != revision) return@launch
                val selected = when (kind) {
                    "nowcast" -> "gfs"
                    "regional" -> paramsSnapshot.optString("model_source", current.regionalModel)
                        .takeIf { it == "gfs" || it == "ec" } ?: pickSelectedModel(response, paramsSnapshot)
                    else -> pickSelectedModel(response, paramsSnapshot)
                }
                _state.update {
                    it.copy(
                        workbenchMode = kind,
                        result = response,
                        resultParams = paramsSnapshot,
                        resultKind = kind,
                        selectedModel = selected,
                        regionalModel = if (kind == "regional") selected else it.regionalModel,
                        selectedRegionalCell = null,
                        resultTab = "概览",
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
        val activeEngine = engine ?: return
        if (token.isEmpty()) return
        viewModelScope.launch {
            runCatching { activeEngine.call("dismiss", JSONObject().put("session", token)) }
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

    // ---------------------------------------------------------------- 设置与远程服务器连接

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

    fun setElevationSource(value: String) {
        val normalized = ElevationSourceSetting.save(prefs, value)
        _state.update { it.copy(elevationSource = normalized) }
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
                        remoteTestMessage = "连接成功 · 服务器工作区：$workspaceLabel",
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
        val editor = prefs.edit().putString(PREF_REMOTE_SERVER_URL, normalizedUrl)
        val hasSavedToken = if (draftToken.isNotEmpty()) {
            editor.putString(PREF_REMOTE_TOKEN, draftToken)
            true
        } else {
            current.remoteTokenSaved || savedRemoteToken().isNotEmpty()
        }
        editor.apply()

        resetEngineAndState(
            nextRemoteServerUrl = normalizedUrl,
            nextRemoteTokenDraft = "",
            nextRemoteTokenSaved = hasSavedToken,
        )
        connect()
    }

    private fun resetEngineAndState(
        nextRemoteServerUrl: String,
        nextRemoteTokenDraft: String,
        nextRemoteTokenSaved: Boolean,
    ) {
        generation = UUID.randomUUID()
        metadataGeneration = UUID.randomUUID()
        operationJob?.cancel()
        publicationJob?.cancel()
        engine?.stop()
        taskResultCache.clear()
        _state.update {
            it.copy(
                serverConfigured = true,
                remoteServerUrl = nextRemoteServerUrl,
                remoteTokenDraft = nextRemoteTokenDraft,
                remoteTokenSaved = nextRemoteTokenSaved,
                remoteTestMessage = "",
                remoteTestOk = false,
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
                status = "正在连接 FireSky 服务器",
                error = "",
                errorPage = null,
                result = null,
                resultParams = null,
                resultKind = null,
                selectedRegionalCell = null,
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
        get() = !_state.value.isRemoteTransport && _state.value.camsConfiguration?.optBoolean("can_save", false) == true

    val camsConfigurationDescription: String
        get() {
            val configuration = _state.value.camsConfiguration ?: return "正在检查服务器配置"
            return when (configuration.optString("state")) {
                "configured" -> "已检测到 Key，下载时验证授权。"
                "wrong_endpoint" -> "接口地址不是 Copernicus ADS，请在服务器端修正。"
                "tls_disabled" -> "配置关闭了 TLS 验证，请在服务器端重新保存以启用安全连接。"
                "unreadable" -> "配置文件无法读取或格式有误，请在服务器端修正。"
                else -> "服务器尚未配置 CAMS Key，缺少气溶胶数据时无法下载。"
            }
        }

    fun refreshCAMSConfiguration() {
        val activeEngine = engine ?: return
        if (!_state.value.online || _state.value.savingCAMS) return
        viewModelScope.launch {
            try {
                val configuration = activeEngine.call("cams_credentials")
                _state.update { it.copy(camsConfiguration = configuration) }
            } catch (_: Exception) {
                _state.update {
                    it.copy(camsMessage = "无法检查配置，请确认服务器已连接。", camsSaveFailed = true)
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
        val activeEngine = engine ?: return
        val current = _state.value
        if (current.isRemoteTransport) {
            _state.update {
                it.copy(
                    camsMessage = "远程薄客户端不支持通过网络修改服务器 CAMS Key，请在 Mac / 服务端本地配置。",
                    camsSaveFailed = true,
                )
            }
            return
        }
        val key = current.camsKeyDraft.trim()
        if (!current.online || current.busy || current.savingCAMS || !canSaveCAMS || key.isEmpty()) return
        _state.update { it.copy(savingCAMS = true, camsMessage = "", camsKeyDraft = "") }
        viewModelScope.launch {
            try {
                val configuration = activeEngine.call("save_cams_key", JSONObject().put("key", key))
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

    // ---------------------------------------------------------------- 任务历史

    fun loadTaskHistory(older: Boolean = false) {
        val activeEngine = engine ?: return
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
                val response = activeEngine.call("task_history", params)
                if (response.optString("schema") != "firesky-task-history-v1") {
                    throw EngineError("任务历史协议不匹配，请重新连接服务器。")
                }
                val tasks = (response.optJSONArray("tasks") ?: JSONArray()).let { array ->
                    (0 until array.length()).mapNotNull { index ->
                        array.optJSONObject(index)?.let { TaskHistoryItem.parse(it) }
                    }
                }
                val total = response.optInt("total", tasks.size)
                _state.update { state ->
                    state.copy(
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
        val activeEngine = engine ?: return
        if (_state.value.selectedTaskId == taskId) {
            _state.update {
                it.copy(
                    selectedTaskId = null, taskEvents = emptyList(), taskEventsError = "",
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
        _state.value.taskHistory.firstOrNull { it.taskId == taskId }?.let { loadTaskResult(it) }
        viewModelScope.launch {
            try {
                val response = activeEngine.call(
                    "task_events",
                    JSONObject().put("task_id", taskId).put("after_seq", 0),
                )
                val events = parseTaskEvents(response)
                _state.update { it.copy(taskEvents = events, taskEventsLoading = false) }
            } catch (error: Exception) {
                _state.update {
                    it.copy(taskEventsError = error.message ?: "事件读取失败", taskEventsLoading = false)
                }
            }
        }
    }

    fun setPendingForgetTask(taskId: String?) {
        _state.update { it.copy(pendingForgetTaskId = taskId) }
    }

    private fun loadTaskResult(item: TaskHistoryItem) {
        val activeEngine = engine ?: return
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
                val response = activeEngine.call(
                    "result_restore",
                    JSONObject().put("task_id", item.taskId),
                )
                val hasContent = (response.optJSONObject("model_results")?.length() ?: 0) > 0 ||
                    response.has("nowcast") ||
                    response.has("peak_quality_index")
                if (!hasContent) {
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
        val activeEngine = engine ?: return
        val taskId = _state.value.pendingForgetTaskId ?: return
        _state.update { it.copy(pendingForgetTaskId = null) }
        val current = _state.value
        if (!current.online || current.busy) return
        if (current.taskHistory.firstOrNull { it.taskId == taskId }?.status == "running") return
        viewModelScope.launch {
            try {
                activeEngine.call("task_forget", JSONObject().put("task_id", taskId))
                if (_state.value.selectedTaskId == taskId) {
                    _state.update { it.copy(selectedTaskId = null, taskEvents = emptyList()) }
                }
                loadTaskHistory()
            } catch (error: Exception) {
                _state.update { it.copy(taskHistoryError = error.message ?: "记录删除失败") }
            }
        }
    }

    fun restoreTaskResult(item: TaskHistoryItem) {
        val activeEngine = engine ?: return
        val current = _state.value
        if (current.busy || current.restoringTaskId != null || !item.canRestoreResult) return
        _state.update { it.copy(restoringTaskId = item.taskId) }
        viewModelScope.launch {
            try {
                val response = activeEngine.call("result_restore", JSONObject().put("task_id", item.taskId))
                applyRestored(response, item)
            } catch (error: Exception) {
                _state.update {
                    it.copy(taskHistoryError = error.message ?: "结果恢复失败", restoringTaskId = null)
                }
            }
        }
    }

    fun restoreTaskParameters(item: TaskHistoryItem) {
        val current = _state.value
        val payload = item.retryPayload ?: return
        if (current.busy || current.restoringTaskId != null || !current.online) return
        val kind = payload.optString("kind").takeIf { it in listOf("point", "regional", "nowcast") } ?: "point"
        applyHistoryPayload(payload, kind)
        _state.update { it.copy(workbenchMode = kind, page = "workbench") }
        beginCheck()
    }

    private fun applyRestored(response: JSONObject, item: TaskHistoryItem) {
        val payload = item.retryPayload ?: JSONObject()
        val kind = payload.optString("kind").takeIf { it in listOf("point", "regional", "nowcast") }
            ?: if (response.has("nowcast")) "nowcast" else "point"
        if (kind != "nowcast" && response.optJSONObject("model_results") == null) {
            throw EngineError("已发布结果格式无效。")
        }
        val selected = if (kind == "nowcast") "gfs" else pickSelectedModel(response, payload)
        applyHistoryPayload(payload, kind)
        _state.update {
            it.copy(
                workbenchMode = kind,
                result = response,
                resultParams = payload,
                resultKind = kind,
                selectedModel = selected,
                regionalModel = if (kind == "regional") selected else it.regionalModel,
                selectedRegionalCell = null,
                resultTab = "概览",
                restoringTaskId = null,
                page = "workbench",
                status = "已恢复历史结果",
                error = "",
                errorPage = null,
            )
        }
    }

    private fun applyHistoryPayload(payload: JSONObject, kind: String) {
        val restoredDate = runCatching { LocalDate.parse(payload.optString("target_date")) }.getOrNull()
        _state.update { current ->
            val updatedLocation = applyPayloadToLocation(current.location, payload)
            val regionCode = if (kind == "regional") {
                val firstRegion = payload.optJSONArray("regions")?.optString(0)?.ifEmpty { null }
                firstRegion ?: current.selectedRegionCode
            } else {
                current.selectedRegionCode
            }
            val modelSource = if (kind == "regional") {
                payload.optString("model_source").takeIf { it == "gfs" || it == "ec" } ?: current.regionalModel
            } else {
                current.regionalModel
            }
            current.copy(
                location = updatedLocation,
                targetDate = restoredDate ?: current.targetDate,
                dateTab = if (restoredDate != null) "自定义" else current.dateTab,
                phenomenon = payload.optString("phenomenon", "").ifEmpty { current.phenomenon },
                ecmwfSource = payload.optString("ecmwf_source", "").ifEmpty { current.ecmwfSource },
                selectedRegionCode = regionCode,
                regionalModel = modelSource,
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
        val activeEngine = engine ?: return
        if (!_state.value.online) return
        viewModelScope.launch {
            try {
                val response = activeEngine.call("retention_settings")
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
        val activeEngine = engine ?: return
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
                val response = activeEngine.call(
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

    fun applyRetentionNow() {
        val activeEngine = engine ?: return
        val current = _state.value
        if (!current.online || current.busy || current.savingRetention || current.applyingRetention) return
        _state.update { it.copy(applyingRetention = true, retentionMessage = "", retentionSaveFailed = false) }
        viewModelScope.launch {
            try {
                val response = activeEngine.call("apply_retention_settings")
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

    fun setDataSyncEnabled(value: Boolean) = _state.update { it.copy(dataSyncEnabled = value) }
    fun setDataSyncCAMS(value: Boolean) = _state.update { it.copy(dataSyncCAMS = value) }
    fun setDataSyncGFS(value: Boolean) = _state.update { it.copy(dataSyncGFS = value) }
    fun setDataSyncEC(value: Boolean) = _state.update { it.copy(dataSyncEC = value) }
    fun setDataSyncInterval(value: String) = _state.update { it.copy(dataSyncInterval = value) }
    fun setDataSyncLeadHorizon(value: String) = _state.update { it.copy(dataSyncLeadHorizon = value) }

    fun loadDataSyncSettings() {
        val activeEngine = engine ?: return
        if (!_state.value.online) return
        viewModelScope.launch {
            try {
                val response = activeEngine.call("data_sync_settings")
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
        val activeEngine = engine ?: return
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
                val response = activeEngine.call("save_data_sync_settings", payload)
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

    fun runDataSyncNow() {
        val activeEngine = engine ?: return
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
                val response = activeEngine.call("data_sync_run", params) { event ->
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

    // ---------------------------------------------------------------- 数据管理

    fun loadInventory() {
        val activeEngine = engine ?: return
        val current = _state.value
        if (!current.online) return
        viewModelScope.launch {
            try {
                val response = activeEngine.call("inventory")
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

    fun beginDataDelete(source: String, cycle: String?) {
        val activeEngine = engine ?: return
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
                val preview = activeEngine.call("data_delete_preview", JSONObject().put("scope", scope))
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

    fun executeDataDelete() {
        val activeEngine = engine ?: return
        val current = _state.value
        val scope = current.pendingDataDelete ?: return
        val preview = current.dataDeletePreview ?: return
        val previewId = preview.optString("preview_id")
        if (!current.online || current.dataDeleteRunning || previewId.isEmpty()) return
        _state.update { it.copy(dataDeleteRunning = true) }
        viewModelScope.launch {
            try {
                val result = activeEngine.call(
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

    // ---------------------------------------------------------------- 地形区域下载

    fun loadTerrain() {
        val activeEngine = engine ?: return
        val current = _state.value
        if (!current.online || current.terrainLoading || current.terrainDownloading) return
        viewModelScope.launch {
            _state.update { it.copy(terrainLoading = true) }
            try {
                val response = activeEngine.call(
                    "terrain_inventory",
                    JSONObject().put("lat", current.location.lat).put("lon", current.location.lon),
                )
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

    fun loadTerrainEstimate() {
        val activeEngine = engine ?: return
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
                val response = activeEngine.call("terrain_inventory", params)
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

    fun downloadTerrainRegion(tiles: List<String>? = null) {
        val activeEngine = engine ?: return
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
                val result = activeEngine.call("terrain_download", params) { event ->
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
            } catch (_: CancellationException) {
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

    fun retryFailedTerrainTiles() {
        val current = _state.value
        val region = current.terrainLastFailedRegion ?: current.terrainRegion
        if (region != current.terrainRegion) {
            _state.update { it.copy(terrainRegion = region) }
        }
        val tiles = current.terrainLastFailedTiles.takeIf { it.isNotEmpty() }
        downloadTerrainRegion(tiles = tiles)
    }

    fun downloadTerrainPoint() {
        val activeEngine = engine ?: return
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
                val result = activeEngine.call("terrain_download", params)
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
            } catch (_: CancellationException) {
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

    // ---------------------------------------------------------------- 生命周期

    override fun onCleared() {
        operationJob?.cancel()
        publicationJob?.cancel()
        engine?.stop()
        super.onCleared()
    }

    companion object {
        private const val PREF_APPEARANCE = "appearance"
        private const val PREF_ECMWF_SOURCE = "ecmwfSource"
        private const val PREF_FAVORITES = "favorites"
        private const val PREF_REMOTE_SERVER_URL = "remoteServerUrl"
        private const val PREF_REMOTE_TOKEN = "remoteToken"

        @VisibleForTesting
        fun forTesting(application: Application, transport: EngineTransport): WorkbenchStore =
            WorkbenchStore(application, transport)
    }
}
