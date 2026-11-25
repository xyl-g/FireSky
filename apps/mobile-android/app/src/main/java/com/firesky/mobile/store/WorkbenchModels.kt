package com.firesky.mobile.store

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
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

/** 任务历史行（同构 NativeTaskHistoryItem；支持 point/regional/nowcast）。 */
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
        val instant = Instant.parse(createdAt)
        val formatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")
        formatter.withZone(ZoneId.systemDefault()).format(instant)
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
        fun parse(value: JSONObject): TaskHistoryItem? = parseTaskHistoryItem(value)
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
        val instant = Instant.parse(at)
        val formatter = DateTimeFormatter.ofPattern("MM-dd HH:mm:ss")
        formatter.withZone(ZoneId.systemDefault()).format(instant)
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

/** 区域预测预设（由 hello.regions 填充；默认包含中国与东亚等）。 */
data class RegionalPreset(
    val code: String,
    val label: String,
    /** [west, south, east, north] */
    val bounds: List<Double>,
)

val DEFAULT_REGIONAL_PRESETS: List<RegionalPreset> = listOf(
    RegionalPreset("china", "中国", listOf(73.0, 18.0, 136.0, 54.0)),
    RegionalPreset("east-asia", "东亚", listOf(63.0, 12.0, 155.0, 60.0)),
    RegionalPreset("southeast-asia", "东南亚", listOf(85.0, -20.0, 155.0, 35.0)),
    RegionalPreset("south-asia", "南亚", listOf(55.0, -5.0, 110.0, 45.0)),
    RegionalPreset("europe", "欧洲", listOf(-25.0, 25.0, 50.0, 75.0)),
    RegionalPreset("north-america", "北美", listOf(-155.0, 5.0, -40.0, 80.0)),
)

/** 区域预测单格点 / 城市行（同构 RegionalForecast.swift RegionalCell）。 */
data class RegionalCell(
    val lat: Double,
    val lon: Double,
    val name: String,
    val quality: Double?,
    val level: String,
    val bestTime: String?,
    val error: String?,
    val peakQuality: Double? = null,
    val peakLevel: String? = null,
    val eventQuality: Double? = null,
    val confidenceScore: Double? = null,
    val pressureFamily: String? = null,
    val cloudCoverageEffective: Double? = null,
    val durationMinutes: Double? = null,
    val qualityAod: Double? = null,
    val incomingAdjustment: Double? = null,
    val observerAdjustment: Double? = null,
    val highCloud: Double? = null,
    val midCloud: Double? = null,
    val lowCloud: Double? = null,
    val timezone: String? = null,
) {
    val coordinateLabel: String get() = "%.2f°, %.2f°".format(lat, lon)
    val label: String get() = name.ifEmpty { coordinateLabel }
    val key: String get() = "%.6f,%.6f".format(lat, lon)
}

/** 区域单模型报告（同构 RegionalForecast.swift RegionalModel）。 */
data class RegionalModel(
    val available: Boolean,
    val error: String?,
    val cells: List<RegionalCell>,
    val ranked: List<RegionalCell>,
    val cityRanking: List<RegionalCell>,
    val cityRankingStatus: String,
    val cityRankingReason: String?,
    val missingCount: Int,
    val highCount: Int,
    val cycle: String?,
    val precision: String?,
    val regionCount: Int,
    val pointCount: Int,
    val successCount: Int,
    val errorCount: Int,
    val prefetchSeconds: Double?,
    val cacheStatus: String?,
    val cacheGeneratedAt: String?,
    val spacingDeg: Double = 0.25,
) {
    val validCount: Int get() = ranked.count()
    val peak: RegionalCell? get() = ranked.firstOrNull()
    val status: String get() = when {
        error != null -> "未完成"
        available -> "预测完成"
        else -> "等待预测"
    }
}

data class RegionalForecast(
    val models: Map<String, RegionalModel>,
) {
    fun model(name: String): RegionalModel = models[name] ?: EMPTY_REGIONAL_MODEL

    companion object {
        val EMPTY_REGIONAL_MODEL = RegionalModel(
            available = false,
            error = null,
            cells = emptyList(),
            ranked = emptyList(),
            cityRanking = emptyList(),
            cityRankingStatus = "unavailable",
            cityRankingReason = null,
            missingCount = 0,
            highCount = 0,
            cycle = null,
            precision = null,
            regionCount = 0,
            pointCount = 0,
            successCount = 0,
            errorCount = 0,
            prefetchSeconds = null,
            cacheStatus = null,
            cacheGeneratedAt = null,
        )
    }
}

/** 卫星实况概率剖面单元（同构 NowcastPresentation.swift）。 */
data class NowcastProfileCell(
    val id: String,
    val startKM: Double,
    val endKM: Double,
    val bottomKM: Double,
    val topKM: Double,
    val probability: Double?,
)

data class NowcastPressureRow(
    val pressure: Int,
    val heightKM: Double,
)

data class NowcastProfileStats(
    val present: Boolean,
    val validTimeText: String?,
    val statusLabel: String,
    val sampleText: String,
    val unknownCount: Int?,
    val coverageText: String,
    val pressureCount: Int,
    val bands: List<String>,
    val pressureRows: List<NowcastPressureRow>,
)

/**
 * 工作台全局状态（纯远程薄客户端：支持单点 / 区域 / 卫星实况三模式）。
 */
data class WorkbenchState(
    // 顶层页面（工作台 / 数据管理 / 任务历史 / 设置）
    val page: String = "workbench",
    // 工作台模式（"point" | "regional" | "nowcast"）
    val workbenchMode: String = "point",
    // 参数表单
    val location: LocationState = LocationState(),
    val query: String = "",
    val dateTab: String = "今天", // 今天 / 明天 / 自定义
    val targetDate: LocalDate = LocalDate.now(),
    val phenomenon: String = "dusk", // dawn / dusk
    val editingCoordinates: Boolean = false,
    val favorites: List<JSONObject> = emptyList(),
    // 区域预测参数与检视态
    val availableRegions: List<RegionalPreset> = DEFAULT_REGIONAL_PRESETS,
    val selectedRegionCode: String = "china",
    val regionalModel: String = "gfs",
    val selectedRegionalCell: RegionalCell? = null,
    val regionalInspectorMode: String = "cities", // "cities" | "grid"
    val regionalQuery: String = "",
    // 会话与进度
    val busy: Boolean = false,
    val checking: Boolean = false,
    val status: String = "未配置服务器",
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
    val elevationSource: String = "auto",
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
    // 任务历史
    val taskHistory: List<TaskHistoryItem> = emptyList(),
    val taskHistoryTotal: Int = 0,
    val taskHistoryLoaded: Boolean = false,
    val taskHistoryLoading: Boolean = false,
    val taskHistoryError: String = "",
    val taskHistoryHasMore: Boolean = false,
    val taskHistoryCursor: String? = null,
    val selectedTaskId: String? = null,
    val taskEvents: List<TaskEventRow> = emptyList(),
    val taskEventsLoading: Boolean = false,
    val taskEventsError: String = "",
    val pendingForgetTaskId: String? = null,
    val restoringTaskId: String? = null,
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
    // 远程服务器数据清单
    val inventory: JSONArray = JSONArray(),
    val inventoryCategories: List<JSONObject> = emptyList(),
    // 远程服务器连接（Mac / Windows / Docker + CF Tunnel / LAN）
    val serverConfigured: Boolean = false,
    val isRemoteTransport: Boolean = true,
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

    val selectedRegionPreset: RegionalPreset
        get() = availableRegions.firstOrNull { it.code == selectedRegionCode }
            ?: availableRegions.firstOrNull()
            ?: DEFAULT_REGIONAL_PRESETS.first()

    /** 当前表单是否与最近一次归属于当前工作台模式的结果参数不一致。 */
    val resultParametersStale: Boolean
        get() {
            if (result == null || resultKind != workbenchMode) return false
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
            if (resultKind == "regional") {
                if (!sameString("model_source", regionalModel)) return true
                if (!sameString("ecmwf_source", ecmwfSource)) return true
                val regions = params.optJSONArray("regions")
                if (regions != null && regions.length() > 0) {
                    val savedCodes = (0 until regions.length()).map { regions.optString(it) }
                    if (selectedRegionCode !in savedCodes) return true
                }
                return false
            }
            if (resultKind == "point" && !sameString("ecmwf_source", ecmwfSource)) return true
            if (!sameNumber("lat", location.lat) || !sameNumber("lon", location.lon)) return true
            if (!sameNumber("elevation_m", location.elevation)) return true
            if (!sameString("timezone_name", location.timezone)) return true
            return false
        }

    /** 结果卡片标题：优先使用生成该结果的原始参数。 */
    val resultTitle: String
        get() {
            val params = if (result != null && resultKind == workbenchMode) resultParams else null
            if (workbenchMode == "regional") {
                val regionCode = params?.optJSONArray("regions")?.optString(0)?.ifEmpty { null } ?: selectedRegionCode
                val regionLabel = availableRegions.firstOrNull { it.code == regionCode }?.label ?: regionCode
                val date = params?.optString("target_date", "")?.ifEmpty { null } ?: targetDate.toString()
                return "$regionLabel · $date"
            }
            val name = params?.optString("name", "")?.ifEmpty { null } ?: location.name
            val date = params?.optString("target_date", "")?.ifEmpty { null } ?: targetDate.toString()
            return "$name · $date"
        }

    /** 结果现象键（"dawn" / "dusk"），跟随结果的原始参数。 */
    val resultPhenomenon: String
        get() {
            val params = if (result != null && resultKind == workbenchMode) resultParams else null
            return params?.optString("phenomenon", "")?.ifEmpty { null } ?: phenomenon
        }
}
