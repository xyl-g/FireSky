package com.firesky.mobile.store

import com.firesky.mobile.ui.optNum
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * 纯函数 JSON 解析、参数构造与格式化辅助集（从 WorkbenchStore 拆出以保持 Store 精简）。
 */

internal fun parseTaskHistoryItem(value: JSONObject): TaskHistoryItem? {
    val taskId = value.optString("task_id")
    if (taskId.length != 64 || taskId.any { !it.isDigit() && it !in 'a'..'f' }) return null
    val method = value.optString("method")
    val status = value.optString("status")
    if (status !in listOf("running", "done", "failed", "cancelled", "interrupted")) return null
    val createdAt = value.optString("created_at_utc")
    if (createdAt.isEmpty()) return null
    val retry = value.optJSONObject("retry_request")
    val payload = retry?.optJSONObject("payload")?.let { rawPayload ->
        val kind = retry.optString("kind").ifEmpty { rawPayload.optString("kind") }
        if (kind in listOf("point", "regional", "nowcast")) {
            JSONObject(rawPayload.toString()).put("kind", kind)
        } else {
            null
        }
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

internal fun parseTaskEvents(response: JSONObject): List<TaskEventRow> {
    val array = response.optJSONArray("events") ?: return emptyList()
    return (0 until array.length()).mapNotNull { index ->
        val item = array.optJSONObject(index) ?: return@mapNotNull null
        val data = item.optJSONObject("data")
        TaskEventRow(
            seq = item.optInt("seq", 0),
            at = item.optString("at_utc"),
            event = item.optString("event"),
            stage = data?.optString("stage").orEmpty(),
            message = data?.optString("message").orEmpty(),
            percent = data?.optDouble("percent")?.takeUnless { it.isNaN() },
        )
    }
}

internal fun parseDataReportRows(report: JSONObject): Pair<Boolean, List<DataRow>> {
    val ready = report.optBoolean("ready", false)
    val array = report.optJSONArray("datasets") ?: JSONArray()
    val rows = (0 until array.length()).mapNotNull { index ->
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
    return ready to rows
}

internal fun parseRegionalPresets(hello: JSONObject): List<RegionalPreset> {
    val array = hello.optJSONArray("regions") ?: return DEFAULT_REGIONAL_PRESETS
    val parsed = (0 until array.length()).mapNotNull { index ->
        val item = array.optJSONObject(index) ?: return@mapNotNull null
        val code = item.optString("code").trim()
        val label = item.optString("label").ifEmpty { code }
        val boundsArr = item.optJSONArray("bounds")
        val bounds = if (boundsArr != null && boundsArr.length() == 4) {
            (0 until 4).mapNotNull { boundsArr.optDouble(it).takeIf { d -> d.isFinite() } }
        } else emptyList()
        if (code.isEmpty() || bounds.size != 4) null
        else RegionalPreset(code = code, label = label, bounds = bounds)
    }
    return parsed.ifEmpty { DEFAULT_REGIONAL_PRESETS }
}

internal fun buildForecastPayload(state: WorkbenchState, kind: String): JSONObject {
    val loc = state.location
    val value = JSONObject()
        .put("name", loc.name)
        .put("lat", loc.lat)
        .put("lon", loc.lon)
        .put("elevation_m", loc.elevation)
        .put("timezone_name", loc.timezone)
        .put("target_date", state.targetDate.toString())
        .put("phenomenon", state.phenomenon)
        .put("model_source", "both")
        .put("workers", 1)
        .put("forecast_mode", "model")
        .put("satellite_source", "off")
        .put("ecmwf_source", state.ecmwfSource)
        .put("elevation_source", state.elevationSource)
    when (kind) {
        "nowcast" -> {
            value.put("model_source", "satellite")
            value.put("forecast_mode", "nowcast")
            value.put("satellite_source", "auto")
            value.put("radius_km", 900.0)
        }
        "regional" -> {
            value.put("model_source", state.regionalModel)
            value.put("regions", JSONArray(listOf(state.selectedRegionCode)))
            value.put("spacing_deg", 0.25)
        }
    }
    return value
}

internal fun applyPayloadToLocation(loc: LocationState, point: JSONObject): LocationState {
    val lat = point.optDouble("lat").takeUnless { it.isNaN() } ?: loc.lat
    val lon = point.optDouble("lon").takeUnless { it.isNaN() } ?: loc.lon
    val elevation = point.optDouble("elevation_m").takeUnless { it.isNaN() } ?: loc.elevation
    val timezone = point.optString("timezone_name", "").ifEmpty { loc.timezone }
    return loc.copy(
        lat = lat,
        lon = lon,
        elevation = elevation,
        timezone = timezone,
        name = point.optString("name", "").ifEmpty { loc.name },
        metadataReady = true,
    )
}

internal fun pickSelectedModel(response: JSONObject, payload: JSONObject? = null): String {
    val modelSource = payload?.optString("model_source").orEmpty()
    if (modelSource == "gfs" || modelSource == "ec") return modelSource
    val models = response.optJSONObject("model_results") ?: return "gfs"
    return if (models.has("gfs")) "gfs" else "ec"
}

internal fun retentionText(value: Any?): String {
    val num = (value as? Number)?.toDouble() ?: return ""
    if (!num.isFinite()) return ""
    return if (num == num.toLong().toDouble()) num.toLong().toString() else "%.2f".format(num)
}

internal fun retentionIntegerOrNull(value: String): Any {
    val trimmed = value.trim()
    return if (trimmed.isEmpty()) JSONObject.NULL else (trimmed.toIntOrNull() ?: -1)
}

internal fun retentionNumberOrNull(value: String): Any {
    val trimmed = value.trim()
    return if (trimmed.isEmpty()) JSONObject.NULL else (trimmed.toDoubleOrNull() ?: -1.0)
}

internal fun dataSyncRoundSummary(response: JSONObject): String {
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

internal fun terrainResultMessage(result: JSONObject, region: String): String {
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

internal fun terrainFailed(result: JSONObject): Boolean =
    result.optInt("failed", 0) > 0 || result.optString("stopped_reason") == "disk_space"

// ---------------------------------------------------------------- Regional 解析（同构 RegionalForecast.swift）

fun parseRegionalForecast(result: JSONObject?): RegionalForecast {
    val reports = result?.optJSONObject("model_results") ?: JSONObject()
    val errors = result?.optJSONObject("model_errors") ?: JSONObject()
    val models = listOf("gfs", "ec").associateWith { model ->
        val err = errors.optString(model, "").ifEmpty { null }
        parseRegionalModel(reports.optJSONObject(model), err)
    }
    return RegionalForecast(models)
}

fun parseRegionalModel(report: JSONObject?, error: String? = null): RegionalModel {
    if (report == null || error != null) {
        return RegionalForecast.EMPTY_REGIONAL_MODEL.copy(error = error)
    }
    val cycle = report.optString("cycle_time_utc", "").ifEmpty { null }
    val precisionObj = report.optJSONObject("precision")
    val pointSpacing = precisionObj?.optNum("point_spacing_deg")
    val modelSpacing = precisionObj?.optNum("model_grid_spacing_deg")
    val precision = when {
        pointSpacing != null && modelSpacing != null -> "点 %.2f° · 模型 %.2f°".format(pointSpacing, modelSpacing)
        pointSpacing != null -> "点 %.2f°".format(pointSpacing)
        else -> null
    }
    val regionCount = report.optJSONArray("regions")?.length() ?: 0
    val pointCount = report.optInt("point_count", 0)
    val successCount = report.optInt("success_count", 0)
    val errorCount = report.optInt("error_count", 0)
    val timing = report.optJSONObject("timing")
    val prefetchSeconds = timing?.optNum("prefetch_seconds")
    val cache = report.optJSONObject("cache")
    val cacheStatus = cache?.optString("status", "")?.ifEmpty { null }
    val cacheGeneratedAt = cache?.optString("generated_at_utc", "")?.ifEmpty { null }

    val cityReport = report.optJSONObject("city_ranking")
    val cityRankingStatus = cityReport?.optString("status", "unavailable")?.ifEmpty { "unavailable" } ?: "unavailable"
    val cityRankingReason = cityReport?.optString("reason", "")?.ifEmpty { null }

    val byCoordinate = linkedMapOf<String, RegionalCell>()
    val pointsArr = report.optJSONArray("points") ?: report.optJSONArray("grid_points") ?: JSONArray()
    for (i in 0 until pointsArr.length()) {
        val row = pointsArr.optJSONObject(i) ?: continue
        val cell = parseRegionalCell(row, isError = false) ?: continue
        byCoordinate[cell.key] = cell
    }
    val errorsArr = report.optJSONArray("errors") ?: JSONArray()
    for (i in 0 until errorsArr.length()) {
        val row = errorsArr.optJSONObject(i) ?: continue
        val cell = parseRegionalCell(row, isError = true) ?: continue
        byCoordinate[cell.key] = cell
    }

    val cities = mutableListOf<RegionalCell>()
    val cityItems = cityReport?.optJSONArray("items") ?: JSONArray()
    for (i in 0 until cityItems.length()) {
        val row = cityItems.optJSONObject(i) ?: continue
        val cell = parseRegionalCell(row, isError = false) ?: continue
        cities.add(cell)
    }
    val cityRanking = cities.withIndex().sortedWith(
        compareByDescending<IndexedValue<RegionalCell>> { it.value.quality ?: -1.0 }
            .thenBy { it.index },
    ).map { it.value }

    val cells = byCoordinate.values.sortedWith(
        compareByDescending<RegionalCell> { it.lat }.thenBy { it.lon },
    )
    val ranked = cells.filter { it.quality != null }.sortedWith(
        compareByDescending<RegionalCell> { it.quality!! }
            .thenByDescending { it.lat }
            .thenBy { it.lon },
    )
    val highCount = ranked.count { (it.quality ?: 0.0) >= 1.5 }
    val reportedCount = if (pointCount > 0) pointCount else cells.size
    val missingCount = maxOf(cells.size - ranked.size, reportedCount - ranked.size, errorCount)

    return RegionalModel(
        available = true,
        error = null,
        cells = cells,
        ranked = ranked,
        cityRanking = cityRanking,
        cityRankingStatus = cityRankingStatus,
        cityRankingReason = cityRankingReason,
        missingCount = missingCount,
        highCount = highCount,
        cycle = cycle,
        precision = precision,
        regionCount = regionCount,
        pointCount = pointCount,
        successCount = successCount,
        errorCount = errorCount,
        prefetchSeconds = prefetchSeconds,
        cacheStatus = cacheStatus,
        cacheGeneratedAt = cacheGeneratedAt,
        spacingDeg = pointSpacing ?: 0.25,
    )
}

fun parseRegionalCell(row: JSONObject, isError: Boolean): RegionalCell? {
    val rawLat = row.optNum("lat") ?: return null
    val rawLon = row.optNum("lon") ?: return null
    if (!rawLat.isFinite() || !rawLon.isFinite() || rawLat !in -90.0..90.0 || rawLon !in -180.0..180.0) {
        return null
    }
    val lat = (rawLat * 1_000_000.0).roundToInt() / 1_000_000.0
    val lon = (rawLon * 1_000_000.0).roundToInt() / 1_000_000.0
    if (isError) {
        return RegionalCell(
            lat = lat,
            lon = lon,
            name = row.optString("name", ""),
            quality = null,
            level = "缺测",
            bestTime = null,
            error = row.optString("error", "").ifEmpty { "该网格未完成" },
        )
    }
    val rawQuality = row.optNum("peak_quality_index") ?: row.optNum("quality_index")
    val quality = rawQuality?.takeIf { it in 0.0..2.5 }
    val components = row.optJSONObject("quality_components") ?: JSONObject()
    val level = row.optString("peak_quality_level", "").ifEmpty {
        row.optString("quality_level", "").ifEmpty { "—" }
    }
    val bestTime = row.optString("best_time_local", "").ifEmpty { null }
    val errorText = if (quality == null) row.optString("error", "").ifEmpty { "缺测" } else null
    return RegionalCell(
        lat = lat,
        lon = lon,
        name = row.optString("name", ""),
        quality = quality,
        level = level,
        bestTime = bestTime,
        error = errorText,
        peakQuality = row.optNum("peak_quality_index"),
        peakLevel = row.optString("peak_quality_level", "").ifEmpty { null },
        eventQuality = row.optNum("quality_index"),
        confidenceScore = row.optNum("confidence_score"),
        pressureFamily = row.optString("dominant_burnable_pressure_family", "").ifEmpty { null },
        cloudCoverageEffective = components.optNum("burnable_cloud_coverage_effective"),
        durationMinutes = components.optNum("duration_minutes"),
        qualityAod = components.optNum("quality_aod550"),
        incomingAdjustment = components.optNum("incoming_aod_adjustment"),
        observerAdjustment = components.optNum("observer_aod_adjustment"),
        highCloud = row.optNum("high_cloud"),
        midCloud = row.optNum("mid_cloud"),
        lowCloud = row.optNum("low_cloud"),
        timezone = row.optString("timezone_name", "").ifEmpty { null },
    )
}

// ---------------------------------------------------------------- Nowcast 解析（同构 NowcastPresentation.swift）

fun parseNowcastProfileStats(report: JSONObject?): NowcastProfileStats {
    val obs = report?.optJSONObject("nowcast")
    val profile = obs?.optJSONObject("satellite_vertical_profile")
        ?: return NowcastProfileStats(
            present = false,
            validTimeText = null,
            statusLabel = "不可用",
            sampleText = "—",
            unknownCount = null,
            coverageText = "—",
            pressureCount = 0,
            bands = emptyList(),
            pressureRows = emptyList(),
        )
    val samplesArr = profile.optJSONArray("samples") ?: JSONArray()
    val samples = (0 until samplesArr.length()).mapNotNull { samplesArr.optJSONObject(it) }
    val levels = samples.flatMap { sample ->
        val arr = sample.optJSONArray("levels") ?: JSONArray()
        (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    }
    val maxHeight = max(10_000.0, levels.mapNotNull { it.optNum("height_m") }.maxOrNull() ?: 10_000.0)
    val pressuresArr = profile.optJSONArray("pressure_levels_hpa") ?: JSONArray()
    val pressures = (0 until pressuresArr.length()).mapNotNull { i ->
        val v = pressuresArr.optInt(i, -1)
        if (v > 0) v else null
    }
    val pressureRows = pressures.mapIndexed { index, pressure ->
        val matched = levels.firstOrNull {
            (it.optNum("pressure_hpa") ?: -1.0) == pressure.toDouble() && it.optNum("height_m") != null
        }
        val height = matched?.optNum("height_m")
            ?: (maxHeight * (0.08 + index.toDouble() / max(1, pressures.size - 1) * 0.84))
        NowcastPressureRow(pressure = pressure, heightKM = height / 1000.0)
    }
    val status = profile.optString("status", "unavailable")
    val statusLabel = when (status) {
        "available" -> "完整覆盖"
        "partial_unknown" -> "部分未知"
        else -> "不可用"
    }
    val sampleCount = if (profile.has("sample_count")) profile.optInt("sample_count", samples.size) else samples.size
    val usable = if (profile.has("usable_sample_count")) profile.optInt("usable_sample_count", sampleCount) else sampleCount
    val unknown = if (profile.has("unknown_sample_count") && !profile.isNull("unknown_sample_count")) {
        profile.optInt("unknown_sample_count")
    } else null
    val coverageText = profile.optNum("coverage")?.let { "${(it * 100.0).roundToInt()}%" } ?: "—"
    val validTime = profile.optString("valid_time_utc", "").ifEmpty { null }?.let {
        it.take(16).replace("T", " ")
    }
    val bandsArr = profile.optJSONArray("bands") ?: JSONArray()
    val bands = (0 until bandsArr.length()).mapNotNull { bandsArr.optString(it).ifEmpty { null } }
    return NowcastProfileStats(
        present = true,
        validTimeText = validTime,
        statusLabel = statusLabel,
        sampleText = "$usable/$sampleCount",
        unknownCount = unknown,
        coverageText = coverageText,
        pressureCount = pressures.size,
        bands = bands,
        pressureRows = pressureRows,
    )
}

fun parseNowcastProfileCells(report: JSONObject?): List<NowcastProfileCell> {
    val obs = report?.optJSONObject("nowcast") ?: return emptyList()
    val profile = obs.optJSONObject("satellite_vertical_profile") ?: return emptyList()
    if (profile.optString("status") == "unavailable") return emptyList()
    val samplesArr = profile.optJSONArray("samples") ?: return emptyList()
    val rawSamples = (0 until samplesArr.length()).mapNotNull { samplesArr.optJSONObject(it) }
    val bearings = rawSamples.mapNotNull { it.optNum("bearing_deg") }.distinct().sorted()
    val targetBearing = resolveNowcastBearing(report, bearings)
    if (targetBearing == null && bearings.size > 1) return emptyList()

    val samples = rawSamples
        .filter { it.optNum("distance_km") != null }
        .filter { sample ->
            if (targetBearing == null) true
            else {
                val value = sample.optNum("bearing_deg") ?: return@filter false
                angularDistance(value, targetBearing) < 0.5
            }
        }
        .sortedBy { it.optNum("distance_km")!! }

    val cells = mutableListOf<NowcastProfileCell>()
    samples.forEachIndexed { index, sample ->
        val distance = sample.optNum("distance_km") ?: return@forEachIndexed
        val before = if (index > 0) samples[index - 1].optNum("distance_km")!! else distance - 10.0
        val after = if (index + 1 < samples.size) samples[index + 1].optNum("distance_km")!! else distance + 10.0
        val levelsArr = sample.optJSONArray("levels") ?: JSONArray()
        val levels = (0 until levelsArr.length()).mapNotNull { levelsArr.optJSONObject(it) }
            .filter { (it.optNum("height_m") ?: -1.0) >= 0.0 }
            .sortedBy { it.optNum("height_m")!! }
        levels.forEachIndexed { layer, level ->
            val height = level.optNum("height_m") ?: return@forEachIndexed
            val lower = if (layer > 0) levels[layer - 1].optNum("height_m")!! else 0.0
            val upper = if (layer + 1 < levels.size) levels[layer + 1].optNum("height_m")!! else height + 1000.0
            val retrieved = sample.optString("status") == "retrieved" && level.optString("status") != "unknown"
            val prob = if (retrieved) {
                (level.optNum("hsd_posterior_percent") ?: level.optNum("cloud_probability_percent"))
                    ?.let { min(100.0, max(0.0, it)) }
            } else null
            cells.add(
                NowcastProfileCell(
                    id = "$index-$layer",
                    startKM = max(0.0, (before + distance) / 2.0),
                    endKM = (distance + after) / 2.0,
                    bottomKM = max(0.0, (lower + height) / 2000.0),
                    topKM = (height + upper) / 2000.0,
                    probability = prob,
                ),
            )
        }
    }
    return cells
}

private fun resolveNowcastBearing(report: JSONObject, bearings: List<Double>): Double? {
    val section = report.optJSONObject("cross_section")
    val stepsArr = report.optJSONArray("steps") ?: JSONArray()
    val steps = (0 until stepsArr.length()).mapNotNull { stepsArr.optJSONObject(it) }
    val bestTime = report.optString("best_time_local", "").ifEmpty { null }
    val best = bestTime?.let { t -> steps.firstOrNull { it.optString("valid_time_local") == t } }
    val diagnostic = steps.firstOrNull {
        it.optBoolean("valid", true) && it.optNum("solar_azimuth_deg") != null
    }
    val target = section?.optNum("solar_azimuth_deg")
        ?: best?.optNum("solar_azimuth_deg")
        ?: diagnostic?.optNum("solar_azimuth_deg")
    if (target != null && bearings.isNotEmpty()) {
        return bearings.minByOrNull { angularDistance(it, target) }
    }
    return if (bearings.size == 1) bearings.first() else null
}

private fun angularDistance(left: Double, right: Double): Double {
    val diff = abs((left - right) % 360.0)
    return min(diff, 360.0 - diff)
}
