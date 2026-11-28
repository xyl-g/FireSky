package com.firesky.mobile.engine

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.sin

/**
 * MockTransport：进程内模拟 desktop JSON-lines 服务，严格仅用于离线单元测试与 Compose UI 测试。
 * 支持 point / regional / nowcast 全链路（hello → check → publications → prepare → forecast）。
 */
class MockTransport : EngineTransport {

    @Volatile
    override var onFrame: FrameListener? = null

    private val running = AtomicBoolean(false)
    private val cancelledId = AtomicReference<String?>(null)
    private var worker: ExecutorService? = null

    private var sessionToken: String? = null
    private var lastKind: String = "point"
    private var lastPayload: JSONObject? = null
    private var datasets: List<Triple<String, String, Boolean>> = emptyList()
    private var camsConfigured = false
    private var retentionSettings: JSONObject = JSONObject()
    private var dataSyncSettings: JSONObject = JSONObject()
        .put("enabled", false)
        .put("sources", JSONObject().put("cams", false).put("gfs", true).put("ec", true))
        .put("interval_minutes", 15)
        .put("lead_horizon_hours", 72)

    override fun start(config: JSONObject) {
        stop()
        cancelledId.set(null)
        sessionToken = null
        lastKind = "point"
        lastPayload = null
        camsConfigured = false
        retentionSettings = JSONObject()
        dataSyncSettings = JSONObject()
            .put("enabled", false)
            .put("sources", JSONObject().put("cams", false).put("gfs", true).put("ec", true))
            .put("interval_minutes", 15)
            .put("lead_horizon_hours", 72)
        datasets = listOf(
            Triple("gfs", "GFS", true),
            Triple("ecmwf", "EC", true),
            Triple("cams", "CAMS", false),
        )
        val executor = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "firesky-mock-engine").apply { isDaemon = true }
        }
        worker = executor
        running.set(true)
    }

    override fun send(frame: String) {
        if (!running.get()) throw EngineError("引擎未连接，请检查设置。")
        if (frame.toByteArray(Charsets.UTF_8).size > EngineClient.MAX_LINE_BYTES) {
            emitFrame(
                JSONObject()
                    .put("id", JSONObject.NULL)
                    .put("type", "error")
                    .put("error", JSONObject().put("code", "invalid_request").put("message", "request exceeds 1 MiB")),
            )
            return
        }
        val request = try {
            JSONObject(frame)
        } catch (_: Exception) {
            emitFrame(
                JSONObject()
                    .put("id", JSONObject.NULL)
                    .put("type", "error")
                    .put(
                        "error",
                        JSONObject().put("code", "invalid_request")
                            .put("message", "expected id, method and object params"),
                    ),
            )
            return
        }
        worker?.execute { serve(request) }
    }

    override fun cancel(requestId: String) {
        cancelledId.set(requestId)
    }

    override fun stop() {
        running.set(false)
        worker?.shutdownNow()
        worker = null
        sessionToken = null
    }

    // ---------------------------------------------------------------- 服务端

    private fun serve(request: JSONObject) {
        val id = request.optString("id")
        val method = request.optString("method")
        val params = request.optJSONObject("params") ?: JSONObject()
        try {
            val result = dispatch(method, params, id)
            emitFrame(
                JSONObject().put("id", id).put("type", "result").put("result", result),
            )
        } catch (error: Exception) {
            val message = when (error) {
                is EngineError -> error.message ?: "操作失败"
                is InterruptedException -> "任务已取消"
                else -> error.message ?: "操作失败"
            }
            emitFrame(
                JSONObject().put("id", id).put("type", "error")
                    .put("error", JSONObject().put("code", "operation_failed").put("message", message)),
            )
        }
    }

    private fun dispatch(method: String, params: JSONObject, requestId: String): JSONObject = when (method) {
        "hello" -> hello()
        "search" -> search(params)
        "metadata" -> metadata(params)
        "check" -> check(params)
        "publications" -> JSONObject().put("report", availabilityReport())
        "prepare" -> prepare(requestId)
        "forecast" -> forecast(requestId)
        "dismiss" -> dismiss()
        "inventory" -> inventory()
        "retention_settings" -> retentionStatus()
        "save_retention_settings" -> saveRetentionSettings(params)
        "apply_retention_settings" -> applyRetentionSettings()
        "data_sync_settings" -> dataSyncStatus()
        "save_data_sync_settings" -> saveDataSyncSettings(params)
        "data_sync_run" -> dataSyncRun(params)
        "task_history" -> taskHistory()
        "task_events" -> taskEvents()
        "task_forget" -> JSONObject().put("forgotten", true)
        "result_restore" -> resultRestore(params)
        "data_delete_preview" -> dataDeletePreview()
        "data_delete_execute" -> dataDeleteExecute()
        "terrain_inventory" -> terrainInventory(params)
        "terrain_download" -> terrainDownload(requestId)
        "cams_credentials" -> camsCredentials()
        "save_cams_key" -> saveCamsKey(params)
        "profile_query" -> profileQuery(params)
        "network_probe" -> networkProbe(requestId, params)
        "cancel" -> JSONObject()
        else -> throw EngineError("unsupported method: $method")
    }

    /** 供 UI 审计模式直接获取模拟结果，无需完整交互等待。 */
    internal fun auditForecast(kind: String, date: String, name: String, phenomenon: String, regionalModel: String, regionCode: String): JSONObject = when (kind) {
        "regional" -> regionalForecastResult(date, regionalModel, regionCode)
        "nowcast" -> nowcastReport(date, name, phenomenon)
        else -> {
            val gfs = pointReport(date, peakIndex = 1.82, peakMinute = 19 * 60 + 5, spread = 46.0, modelSource = "gfs")
            val ec = pointReport(date, peakIndex = 1.58, peakMinute = 19 * 60 + 25, spread = 58.0, modelSource = "ec")
            JSONObject()
                .put("report", availabilityReport())
                .put("model_results", JSONObject().put("gfs", gfs).put("ec", ec))
                .put("model_errors", JSONObject())
        }
    }

    private fun profileQuery(params: JSONObject): JSONObject {
        val validTime = params.optString("valid_time_utc", "2026-11-12T09:55:00Z")
        return JSONObject()
            .put("contract", "rust-desktop-profile-query-v1")
            .put("status", "ready")
            .put("result_id", params.optString("result_id", "a".repeat(64)))
            .put("model_source", params.optString("model_source", "satellite"))
            .put("lat", params.optDouble("lat", 30.2741))
            .put("lon", params.optDouble("lon", 120.1551))
            .put("valid_time_utc", validTime)
            .put("cycle_utc", validTime)
            .put("source_identity", params.optJSONObject("source_identity") ?: JSONObject().put("bytes", 4096).put("sha256", "b".repeat(64)))
            .put("source_times_utc", JSONArray(listOf("09:10Z", "09:20Z")))
            .put("cross_section", mockCrossSection(validTime))
    }

    private fun hello(): JSONObject {
        val regions = JSONArray()
            .put(JSONObject().put("code", "china").put("label", "中国").put("bounds", JSONArray(listOf(73.0, 18.0, 136.0, 54.0))))
            .put(JSONObject().put("code", "east-asia").put("label", "东亚").put("bounds", JSONArray(listOf(63.0, 12.0, 155.0, 60.0))))
            .put(JSONObject().put("code", "southeast-asia").put("label", "东南亚").put("bounds", JSONArray(listOf(85.0, -20.0, 155.0, 35.0))))
            .put(JSONObject().put("code", "europe").put("label", "欧洲").put("bounds", JSONArray(listOf(-25.0, 25.0, 50.0, 75.0))))
        return JSONObject()
            .put("protocol", 1)
            .put("engine", "firesky-mock")
            .put("workspace", "/mock/firesky")
            .put("migration_stage", "mock-transport")
            .put(
                "capabilities",
                JSONArray(
                    listOf(
                        "point", "regional", "nowcast",
                        "score_window", "check_desktop_data", "desktop_inventory",
                        "desktop_publications", "desktop_prepare", "desktop_session", "location",
                        "data_sync", "task_history", "task_events", "task_forget", "result_restore",
                        "data_delete", "terrain_inventory", "terrain_download", "network_probe",
                    ),
                ),
            )
            .put("regions", regions)
            .put("cams_credentials", camsCredentials())
    }

    private fun search(params: JSONObject): JSONObject {
        val query = params.optString("query", "").trim()
        if (query.isEmpty()) throw EngineError("搜索词为空。")
        val offset = (query.hashCode().mod(2000) - 1000) / 10000.0
        return JSONObject()
            .put("name", query)
            .put("lat", 30.2741 + offset)
            .put("lon", 120.1551 - offset)
            .put("elevation_m", 12.0)
            .put("timezone_name", "Asia/Shanghai")
    }

    private fun metadata(params: JSONObject): JSONObject {
        val lat = params.optDouble("lat", Double.NaN)
        val lon = params.optDouble("lon", Double.NaN)
        if (lat.isNaN() || lon.isNaN()) throw EngineError("expected lat and lon")
        return JSONObject()
            .put("elevation_m", 8.0 + kotlin.math.abs(lat).toInt() % 30)
            .put("timezone_name", "Asia/Shanghai")
    }

    private fun check(params: JSONObject): JSONObject {
        val kind = params.optString("kind", "point")
        if (kind !in listOf("point", "regional", "nowcast")) {
            throw EngineError("unsupported check kind: $kind")
        }
        val payload = params.optJSONObject("payload") ?: JSONObject()
        lastKind = kind
        lastPayload = payload
        val token = UUID.randomUUID().toString()
        sessionToken = token
        return JSONObject().put("session", token).put("report", availabilityReport())
    }

    private fun prepare(requestId: String): JSONObject {
        sessionToken ?: throw EngineError("会话不存在，请重新检查数据。")
        val pending = datasets.filter { !it.third }
        if (pending.isNotEmpty()) {
            for ((source, _, _) in pending) {
                stage(requestId, source, "正在准备 $source 数据", steps = 6, delayMs = 40)
            }
        }
        datasets = datasets.map { it.copy(third = true) }
        return JSONObject().put("report", availabilityReport())
    }

    private fun forecast(requestId: String): JSONObject {
        sessionToken ?: throw EngineError("会话不存在，请重新检查数据。")
        val payload = lastPayload ?: JSONObject()
        if (!datasets.all { it.third }) throw EngineError("本地数据不完整，请先准备数据。")
        val payloadDate = payload.optString("target_date").ifEmpty { java.time.LocalDate.now().toString() }

        return when (lastKind) {
            "regional" -> {
                val model = payload.optString("model_source", "gfs").takeIf { it == "gfs" || it == "ec" } ?: "gfs"
                val regionCode = payload.optJSONArray("regions")?.optString(0)?.ifEmpty { null } ?: "china"
                stage(requestId, model, "正在计算 ${model.uppercase()} 区域网格", steps = 5, delayMs = 40)
                stage(requestId, "city_ranking", "正在汇总城市榜单", steps = 3, delayMs = 30)
                regionalForecastResult(payloadDate, model, regionCode)
            }
            "nowcast" -> {
                stage(requestId, "hsd", "正在解码 Himawari B07/B13/B15 通道", steps = 4, delayMs = 40)
                stage(requestId, "nowcast", "正在追踪卫星云体平流与垂直先验", steps = 4, delayMs = 40)
                nowcastReport(payloadDate, payload.optString("name", "杭州"), payload.optString("phenomenon", "dusk"))
            }
            else -> {
                stage(requestId, "gfs", "正在计算 GFS", steps = 5, delayMs = 40)
                stage(requestId, "ec", "正在计算 ECMWF", steps = 5, delayMs = 40)
                stage(requestId, "assess", "正在评估霞光质量", steps = 3, delayMs = 30)
                val gfs = pointReport(payloadDate, peakIndex = 1.82, peakMinute = 19 * 60 + 5, spread = 46.0, modelSource = "gfs")
                val ec = pointReport(payloadDate, peakIndex = 1.58, peakMinute = 19 * 60 + 25, spread = 58.0, modelSource = "ec")
                JSONObject()
                    .put("report", availabilityReport())
                    .put("model_results", JSONObject().put("gfs", gfs).put("ec", ec))
                    .put("model_errors", JSONObject())
            }
        }
    }

    private fun dismiss(): JSONObject {
        sessionToken = null
        return JSONObject()
    }

    private fun taskHistory(): JSONObject {
        val pointPayload = JSONObject()
            .put("kind", "point")
            .put(
                "payload",
                JSONObject()
                    .put("name", "杭州").put("lat", 30.2741).put("lon", 120.1551)
                    .put("target_date", "2026-11-09").put("phenomenon", "dusk"),
            )
        val regionalPayload = JSONObject()
            .put("kind", "regional")
            .put(
                "payload",
                JSONObject()
                    .put("regions", JSONArray(listOf("china")))
                    .put("model_source", "gfs")
                    .put("target_date", "2026-11-10")
                    .put("phenomenon", "dusk"),
            )
        val nowcastPayload = JSONObject()
            .put("kind", "nowcast")
            .put(
                "payload",
                JSONObject()
                    .put("name", "杭州").put("lat", 30.2741).put("lon", 120.1551)
                    .put("target_date", "2026-11-11").put("phenomenon", "dusk"),
            )
        val running = JSONObject()
            .put("task_id", "b".repeat(64)).put("method", "prepare").put("status", "running")
            .put("created_at_utc", "2026-11-21T09:00:00.000Z")
            .put("updated_at_utc", "2026-11-21T09:01:00.000Z")
            .put("cancel_requested", false)
            .put("event_seq", 2).put("progress", JSONObject().put("percent", 40.0))
            .put("retry_request", pointPayload)
        val donePoint = JSONObject()
            .put("task_id", "a".repeat(64)).put("method", "forecast").put("status", "done")
            .put("created_at_utc", "2026-11-20T12:27:33.706Z")
            .put("updated_at_utc", "2026-11-20T12:27:42.600Z")
            .put("cancel_requested", false)
            .put("event_seq", 5).put("retry_request", pointPayload)
            .put(
                "result_ref",
                JSONObject().put(
                    "snapshot",
                    JSONObject().put("contract", "firesky-result-snapshot-v1").put("sha256", "c".repeat(64)),
                ),
            )
        val doneRegional = JSONObject()
            .put("task_id", "e".repeat(64)).put("method", "forecast").put("status", "done")
            .put("created_at_utc", "2026-11-20T11:10:00.000Z")
            .put("updated_at_utc", "2026-11-20T11:12:00.000Z")
            .put("cancel_requested", false)
            .put("event_seq", 4).put("retry_request", regionalPayload)
            .put(
                "result_ref",
                JSONObject().put(
                    "snapshot",
                    JSONObject().put("contract", "firesky-result-snapshot-v1").put("sha256", "1".repeat(64)),
                ),
            )
        val doneNowcast = JSONObject()
            .put("task_id", "f".repeat(64)).put("method", "forecast").put("status", "done")
            .put("created_at_utc", "2026-11-20T10:15:00.000Z")
            .put("updated_at_utc", "2026-11-20T10:16:00.000Z")
            .put("cancel_requested", false)
            .put("event_seq", 3).put("retry_request", nowcastPayload)
            .put(
                "result_ref",
                JSONObject().put(
                    "snapshot",
                    JSONObject().put("contract", "firesky-result-snapshot-v1").put("sha256", "2".repeat(64)),
                ),
            )
        val sync = JSONObject()
            .put("task_id", "d".repeat(64)).put("method", "data_sync").put("status", "done")
            .put("created_at_utc", "2026-11-20T04:00:00.000Z")
            .put("updated_at_utc", "2026-11-20T04:01:00.000Z")
            .put("cancel_requested", false).put("event_seq", 1)
        return JSONObject()
            .put("schema", "firesky-task-history-v1")
            .put("automatic_restart", false)
            .put("total", 5)
            .put("issues", JSONArray())
            .put("tasks", JSONArray().put(running).put(donePoint).put(doneRegional).put(doneNowcast).put(sync))
    }

    private fun taskEvents(): JSONObject {
        fun event(seq: Int, name: String, percent: Double?, stage: String, message: String): JSONObject {
            val data = JSONObject()
            if (stage.isNotEmpty()) data.put("stage", stage)
            if (message.isNotEmpty()) data.put("message", message)
            percent?.let { data.put("percent", it) }
            return JSONObject()
                .put("seq", seq).put("at_utc", "2026-11-20T12:27:3$seq.000Z")
                .put("event", name).put("data", data)
        }
        return JSONObject()
            .put("task_id", "a".repeat(64))
            .put(
                "events",
                JSONArray()
                    .put(event(1, "running", null, "", "预测计算已开始"))
                    .put(event(2, "progress", 30.0, "prepare", "正在准备 GFS 与 CAMS 数据"))
                    .put(event(3, "progress", 70.0, "prepare", "GFS 数据已就绪"))
                    .put(event(4, "progress", 90.0, "forecast", "预测计算进行中"))
                    .put(event(5, "done", null, "", "预测完成")),
            )
            .put("next_seq", 6).put("truncated", false)
    }

    private fun resultRestore(params: JSONObject): JSONObject = when (params.optString("task_id")) {
        "e".repeat(64) -> regionalForecastResult("2026-11-10", "gfs", "china")
        "f".repeat(64) -> nowcastReport("2026-11-11", "杭州", "dusk")
        else -> JSONObject().put(
            "model_results",
            JSONObject()
                .put("gfs", pointReport("2026-11-09", peakIndex = 1.75, peakMinute = 18 * 60 + 50, spread = 44.0))
                .put("ec", pointReport("2026-11-09", peakIndex = 1.52, peakMinute = 19 * 60 + 10, spread = 54.0)),
        )
    }

    private fun dataDeletePreview(): JSONObject = JSONObject()
        .put("contract", "rust-data-delete-preview-v1")
        .put("preview_id", "mock0preview000001")
        .put("file_count", 42)
        .put("total_mb", 512.5)
        .put("protected", JSONArray())

    private fun dataDeleteExecute(): JSONObject = JSONObject()
        .put("contract", "rust-data-delete-execute-v1")
        .put("deleted", 42).put("skipped", JSONArray()).put("failed", JSONArray())
        .put("freed_mb", 512.5)

    private fun terrainInventory(params: JSONObject): JSONObject {
        val result = JSONObject()
            .put("contract", "rust-terrain-inventory-v1")
            .put("tile_count", 3)
            .put(
                "tiles",
                JSONArray()
                    .put(JSONObject().put("name", "N30E119").put("bytes", 25_934_402))
                    .put(JSONObject().put("name", "N30E120").put("bytes", 25_934_402))
                    .put(JSONObject().put("name", "N31E120").put("bytes", 25_934_402)),
            )
            .put("coverage_ok", true)
            .put(
                "tile",
                JSONObject().put("name", "N30E120").put("exists", true).put("bytes", 25_934_402)
                    .put("lat_range", JSONArray(listOf(30.0, 31.0))).put("lon_range", JSONArray(listOf(120.0, 121.0))),
            )
        if (params.optBoolean("estimate_global", false)) {
            val bounds = params.optJSONObject("bounds") ?: JSONObject()
                .put("south", -56).put("north", 59).put("west", -180).put("east", 179)
            val candidates = (bounds.optInt("north", 0) - bounds.optInt("south", 0) + 1) *
                (bounds.optInt("east", 0) - bounds.optInt("west", 0) + 1)
            result.put(
                "global",
                JSONObject()
                    .put("candidates", candidates)
                    .put("existing", 2)
                    .put("estimated_bytes", candidates * 6_000_000L)
                    .put("estimated_gb", candidates * 0.006)
                    .put("bounds", bounds),
            )
        }
        return result
    }

    private fun terrainDownload(requestId: String): JSONObject {
        val total = 4
        for (index in 1..total) {
            if (index > 1) Thread.sleep(150)
            emitFrame(
                JSONObject().put("id", requestId).put("type", "progress").put(
                    "event",
                    JSONObject().put(
                        "terrain",
                        JSONObject()
                            .put("stage", "global").put("candidates", total)
                            .put("downloaded", index).put("absent", 0).put("failed", 0)
                            .put("existing", 0).put("received_bytes", index * 10_000_000L)
                            .put("percent", index * 100.0 / total)
                            .put("current_tile", "N30E11$index"),
                    ),
                ),
            )
        }
        return JSONObject()
            .put("contract", "rust-terrain-download-v1")
            .put("status", "done").put("mode", "global").put("candidates", total)
            .put("existing", 0).put("downloaded", total).put("absent", 0).put("failed", 0)
            .put("received_bytes", total * 10_000_000L).put("received_gb", total * 0.01)
            .put("stopped_reason", JSONObject.NULL)
    }

    private fun networkProbe(requestId: String, params: JSONObject): JSONObject {
        val aerosolSource = params.optString("aerosol_source", "cams")
        val rows = JSONArray()
        val sources = listOf(
            Triple("gfs", "GFS · NOAA NOMADS", "noaa.ncep.noaa.gov"),
            Triple("ec", "EC · ECMWF Open Data", "data.ecmwf.int"),
            Triple(
                "cams",
                if (aerosolSource == "geos-fp") "GEOS-FP · NASA OPeNDAP" else "CAMS · Copernicus ADS",
                if (aerosolSource == "geos-fp") "opendap.nccs.nasa.gov" else "ads.atmosphere.copernicus.eu",
            ),
        )
        for ((source, title, host) in sources) {
            checkCancelled(requestId)
            emitProbeProgress(
                requestId,
                JSONObject()
                    .put("source", source)
                    .put("title", title)
                    .put("state", "testing")
                    .put("detail", "正在建立连接（模拟）"),
            )
            Thread.sleep(40)
            checkCancelled(requestId)
            val row = JSONObject()
                .put("source", source)
                .put("title", title)
                .put("state", "passed")
                .put("host", host)
                .put("detail", "示例数据首块校验通过（模拟）")
                .put("bytes_received", 65_536)
                .put("elapsed_ms", 812)
            emitProbeProgress(requestId, row)
            rows.put(row)
        }
        return JSONObject()
            .put("checked_at", java.time.Instant.now().toString())
            .put("all_passed", true)
            .put("datasets", rows)
    }

    private fun emitProbeProgress(requestId: String, row: JSONObject) {
        emitFrame(
            JSONObject()
                .put("id", requestId)
                .put("type", "progress")
                .put("event", JSONObject().put("probe", row)),
        )
    }

    private fun inventory(): JSONObject {
        val cycles = JSONObject()
            .put("2026-11-11T12", JSONObject().put("files", 42))
            .put("2026-11-11T00", JSONObject().put("files", 42))
        val rows = JSONArray()
        for ((source, _, ready) in datasets) {
            rows.put(
                JSONObject()
                    .put("source", source)
                    .put("cycles", JSONObject(cycles.toString()))
                    .put("total_file_count", if (ready) 84 else 0)
                    .put("total_storage_mb", if (ready) 2048.0 else 0.0)
                    .put("path", "/mock/cache/$source"),
            )
        }
        val categories = JSONArray()
            .put(JSONObject().put("category", "point-forecasts").put("path", "/mock/cache/point-forecasts").put("file_count", 4).put("bytes", 2_400_000L))
            .put(JSONObject().put("category", "regional-forecasts").put("path", "/mock/cache/regional-forecasts").put("file_count", 2).put("bytes", 18_500_000L))
            .put(JSONObject().put("category", "map-frames").put("path", "/mock/cache/map-frames").put("file_count", 1).put("bytes", 4_200_000L))
            .put(JSONObject().put("category", "reports").put("path", "/mock/reports").put("file_count", 3).put("bytes", 720_000L))
            .put(JSONObject().put("category", "satellite").put("path", "/mock/cache/himawari-hsd").put("file_count", 2).put("bytes", 34_000_000L))
            .put(JSONObject().put("category", "srtm").put("path", "/mock/cache/srtm").put("file_count", 3).put("bytes", 77_803_206L))
        return JSONObject()
            .put("datasets", rows)
            .put("categories", JSONObject().put("contract", "firesky-data-categories-v1").put("categories", categories))
    }

    private fun retentionConfigured(): Boolean = listOf(
        "gfs_cycles", "ecmwf_cycles", "cams_cycles", "satellite_days", "report_days", "map_days",
    ).any { key ->
        val value = retentionSettings.opt(key)
        value != null && value != JSONObject.NULL
    }

    private fun retentionStatus(): JSONObject = JSONObject()
        .put("configured", retentionConfigured())
        .put("settings", JSONObject(retentionSettings.toString()))

    private fun saveRetentionSettings(params: JSONObject): JSONObject {
        retentionSettings = JSONObject(params.optJSONObject("settings")?.toString() ?: "{}")
        return retentionStatus()
    }

    private fun applyRetentionSettings(): JSONObject {
        if (!retentionConfigured()) {
            return JSONObject().put("configured", false).put("result", JSONObject.NULL)
        }
        return JSONObject()
            .put("configured", true)
            .put(
                "result",
                JSONObject()
                    .put("removed_count", 6)
                    .put("freed_mb", 128.4)
                    .put("deferred_count", 0)
                    .put("failed_count", 0),
            )
    }

    private fun dataSyncStatus(): JSONObject = JSONObject()
        .put("configured", true)
        .put("retention_configured", retentionConfigured())
        .put("settings", JSONObject(dataSyncSettings.toString()))
        .put("state", JSONObject().put("sources", JSONObject()))

    private fun saveDataSyncSettings(params: JSONObject): JSONObject {
        val incoming = params.optJSONObject("settings") ?: JSONObject()
        val interval = incoming.optInt("interval_minutes", 15)
        if (interval !in 5..1440) {
            throw EngineError("同步间隔必须在 5–1440 分钟之间。")
        }
        dataSyncSettings = JSONObject(incoming.toString())
        return dataSyncStatus()
    }

    private fun dataSyncRun(params: JSONObject): JSONObject {
        val sources = params.optJSONArray("sources") ?: JSONArray()
        val forced = params.optBoolean("force", false)
        val resultSources = JSONObject()
        for (i in 0 until sources.length()) {
            val name = sources.optString(i)
            resultSources.put(name, JSONObject().put("status", if (forced) "synced" else "up_to_date"))
        }
        return JSONObject().put("sources", resultSources).put("forced", forced)
    }

    private fun camsCredentials(): JSONObject = JSONObject()
        .put("configured", camsConfigured)
        .put("can_save", true)
        .put("state", if (camsConfigured) "configured" else "unconfigured")

    private fun saveCamsKey(params: JSONObject): JSONObject {
        val key = params.optString("key", "").trim()
        if (key.isEmpty()) throw EngineError("Key 为空，未保存。")
        camsConfigured = true
        return camsCredentials()
    }

    // ---------------------------------------------------------------- 合成报告

    private fun availabilityReport(): JSONObject {
        val rows = JSONArray()
        for ((source, title, ready) in datasets) {
            rows.put(
                JSONObject()
                    .put("source", source)
                    .put("title", title)
                    .put("ready", ready)
                    .put("verified", true)
                    .put("is_latest", true)
                    .put("missing_count", 0)
                    .put("detail", if (ready) "服务端已就绪（模拟传输）" else "需准备本次数据（模拟传输）"),
            )
        }
        return JSONObject().put("ready", datasets.all { it.third }).put("datasets", rows)
    }

    private fun regionalForecastResult(date: String, model: String, regionCode: String): JSONObject {
        val points = JSONArray()
        var lat = 20.0
        while (lat <= 50.0) {
            var lon = 76.0
            while (lon <= 132.0) {
                val wave = 0.95 + 0.72 * sin((lat - 26.0) * 0.17) * cos((lon - 114.0) * 0.08) +
                    0.35 * exp(-((lat - 31.0) * (lat - 31.0) + (lon - 120.0) * (lon - 120.0)) / 95.0)
                val q = wave.coerceIn(0.15, 2.38)
                points.put(
                    JSONObject()
                        .put("lat", lat)
                        .put("lon", lon)
                        .put("peak_quality_index", q)
                        .put("quality_index", (q * 0.92).coerceAtLeast(0.1))
                        .put("peak_quality_level", qualityLevel(q))
                        .put("quality_level", qualityLevel(q))
                        .put("best_time_local", "${date}T17:45:00")
                        .put("confidence_score", 0.78)
                        .put("dominant_burnable_pressure_family", if (q >= 1.2) "high" else "middle")
                        .put("timezone_name", "Asia/Shanghai")
                        .put(
                            "quality_components",
                            JSONObject()
                                .put("burnable_cloud_coverage_effective", 0.58)
                                .put("duration_minutes", 36.0)
                                .put("quality_aod550", 0.24)
                                .put("incoming_aod_adjustment", 0.04)
                                .put("observer_aod_adjustment", 0.02),
                        ),
                )
                lon += 2.0
            }
            lat += 2.0
        }

        val cities = listOf(
            Triple("杭州", 30.2741 to 120.1551, 2.18),
            Triple("上海", 31.2304 to 121.4737, 2.04),
            Triple("南京", 32.0603 to 118.7969, 1.92),
            Triple("厦门", 24.4798 to 118.0894, 1.84),
            Triple("成都", 30.5728 to 104.0668, 1.71),
            Triple("北京", 39.9042 to 116.4074, 1.56),
            Triple("青岛", 36.0671 to 120.3826, 1.48),
            Triple("昆明", 25.0389 to 102.7183, 1.35),
        )
        val cityItems = JSONArray()
        for ((name, coords, score) in cities) {
            cityItems.put(
                JSONObject()
                    .put("name", name)
                    .put("lat", coords.first)
                    .put("lon", coords.second)
                    .put("peak_quality_index", score)
                    .put("quality_index", score * 0.94)
                    .put("peak_quality_level", qualityLevel(score))
                    .put("quality_level", qualityLevel(score))
                    .put("best_time_local", "${date}T17:48:00")
                    .put("confidence_score", 0.81)
                    .put("dominant_burnable_pressure_family", "high")
                    .put("timezone_name", "Asia/Shanghai")
                    .put(
                        "quality_components",
                        JSONObject()
                            .put("burnable_cloud_coverage_effective", 0.64)
                            .put("duration_minutes", 40.0)
                            .put("quality_aod550", 0.22)
                            .put("incoming_aod_adjustment", 0.05)
                            .put("observer_aod_adjustment", 0.03),
                    ),
            )
        }

        val report = JSONObject()
            .put("scope", "regional")
            .put("regions", JSONArray(listOf(regionCode)))
            .put("target_date", date)
            .put("model_source", model)
            .put("cycle_time_utc", "${date}T00:00:00Z")
            .put("precision", JSONObject().put("point_spacing_deg", 2.0).put("model_grid_spacing_deg", 0.25))
            .put("point_count", points.length())
            .put("success_count", points.length())
            .put("error_count", 0)
            .put("points", points)
            .put("errors", JSONArray())
            .put("timing", JSONObject().put("prefetch_seconds", 1.4))
            .put(
                "city_ranking",
                JSONObject()
                    .put("contract", "rust-regional-city-ranking-v1")
                    .put("status", "available")
                    .put("source", "china-city-catalog")
                    .put("model_source", model)
                    .put("items", cityItems),
            )

        return JSONObject()
            .put("report", availabilityReport())
            .put("model_results", JSONObject().put(model, report))
            .put("model_errors", JSONObject())
    }

    private fun nowcastReport(date: String, name: String, phenomenon: String): JSONObject {
        val steps = JSONArray()
        var bestIndex = 0.0
        var bestTime = "${date}T17:55:00"
        for (minute in 17 * 60..19 * 60 step 5) {
            val delta = (minute - (17 * 60 + 55)).toDouble()
            val quality = 0.22 + 1.66 * exp(-(delta * delta) / (2 * 32.0 * 32.0))
            val solar = 7.0 * cos(delta / 160.0 * kotlin.math.PI / 2) - 1.2
            val time = "%sT%02d:%02d:00".format(date, minute / 60, minute % 60)
            if (quality > bestIndex) {
                bestIndex = quality
                bestTime = time
            }
            val valid = quality > 0.35
            steps.put(
                JSONObject()
                    .put("valid_time_local", time)
                    .put("valid", valid)
                    .put("selected_regime", if (valid) "high-canvas" else "invalid")
                    .put("instant_quality_index", quality)
                    .put("instant_quality_level", qualityLevel(quality))
                    .put("physically_burnable", valid && quality > 0.55)
                    .put("burnable_cloud_coverage", (quality / 2.6).coerceIn(0.05, 0.85))
                    .put("brightness_color", (0.4 + quality / 3.5).coerceIn(0.2, 0.95))
                    .put("forward_burnable_cloud_ratio", (0.3 + quality / 3.2).coerceIn(0.1, 0.9))
                    .put("solar_incoming_block_risk", 0.18)
                    .put("quality_aod550", 0.24)
                    .put("solar_elevation_deg", solar)
                    .put("solar_azimuth_deg", 251.2),
            )
        }
        val profileSamples = JSONArray()
        for (dist in listOf(40.0, 120.0, 200.0, 280.0, 340.0)) {
            val isUnknown = dist == 280.0
            val levels = JSONArray()
                .put(JSONObject().put("pressure_hpa", 300).put("height_m", 9100.0).put("status", if (isUnknown) "unknown" else "retrieved").put("hsd_posterior_percent", if (isUnknown) JSONObject.NULL else 72.0))
                .put(JSONObject().put("pressure_hpa", 500).put("height_m", 5600.0).put("status", if (isUnknown) "unknown" else "retrieved").put("hsd_posterior_percent", if (isUnknown) JSONObject.NULL else 38.0))
                .put(JSONObject().put("pressure_hpa", 700).put("height_m", 3000.0).put("status", if (isUnknown) "unknown" else "retrieved").put("hsd_posterior_percent", if (isUnknown) JSONObject.NULL else 12.0))
            profileSamples.put(
                JSONObject()
                    .put("distance_km", dist)
                    .put("bearing_deg", 251.2)
                    .put("status", if (isUnknown) "unknown" else "retrieved")
                    .put("levels", levels),
            )
        }
        val nowcastObs = JSONObject()
            .put("source", "NOAA Himawari-9 HSD")
            .put("image_channel", "B07/B13/B15")
            .put("image_valid_time_utc", "${date}T09:20:00Z")
            .put("near_cloud_ratio", 0.34)
            .put("far_cloud_ratio", 0.52)
            .put("cloud_blanket_score", 0.41)
            .put("cloud_texture_score", 0.68)
            .put("horizon_clearance_score", 0.79)
            .put("horizon_gap_score", 0.74)
            .put("overcast_shadow_factor", 0.16)
            .put("lagrangian_b13_trend_k_per_hour", -1.85)
            .put("satellite_low_cloud_state", "clear")
            .put("cloud_type_label", "")
            .put("satellite_motion_quality", "strong")
            .put("geometric_path_blocking_risk", 0.21)
            .put("geometric_path_samples", 18)
            .put("calibrated_sample_coverage_ratio", 0.94)
            .put("confidence", 0.82)
            .put("effective_convective_risk", 0.08)
            .put("effective_path_blocking_risk", 0.19)
            .put("convective_score_ceiling", 0.92)
            .put("motion_correlation", 0.86)
            .put("satellite_motion_age_minutes", 10.0)
            .put("satellite_advection_horizon_hours", 1.25)
            .put("radar_source", "CMA-Mosaic")
            .put("radar_valid_time_utc", "${date}T09:18:00Z")
            .put("radar_age_minutes", 12.0)
            .put("radar_max_dbz", 14.0)
            .put("radar_local_precip_ratio", 0.0)
            .put("radar_path_precip_ratio", 0.02)
            .put("radar_reliability_score", 0.91)
            .put(
                "satellite_vertical_profile",
                JSONObject()
                    .put("status", "partial_unknown")
                    .put("valid_time_utc", "${date}T09:20:00Z")
                    .put("sample_count", 5)
                    .put("usable_sample_count", 4)
                    .put("unknown_sample_count", 1)
                    .put("coverage", 0.80)
                    .put("pressure_levels_hpa", JSONArray(listOf(300, 500, 700)))
                    .put("bands", JSONArray(listOf("B07", "B13", "B15")))
                    .put("samples", profileSamples),
            )

        return JSONObject()
            .put("contract", "rust-nowcast-runtime-v1")
            .put("observer_name", name)
            .put("phenomenon", phenomenon)
            .put("target_date", date)
            .put("model_source", "satellite")
            .put("forecast_mode", "nowcast")
            .put("compute_backend", "cpu")
            .put("cycle_time_utc", "${date}T09:20:00Z")
            .put("peak_quality_index", bestIndex)
            .put("peak_quality_level", qualityLevel(bestIndex))
            .put("quality_index", bestIndex * 0.93)
            .put("quality_level", qualityLevel(bestIndex * 0.93))
            .put("best_time_local", bestTime)
            .put("duration_status", "estimated")
            .put("duration_minutes", 35.0)
            .put("confidence_score", 0.82)
            .put("selected_regime", "high-canvas")
            .put("timing_window", JSONObject().put("start_time_local", "${date}T17:35:00").put("end_time_local", "${date}T18:10:00"))
            .put("aerosol", JSONObject().put("aod550", 0.24).put("source", "CAMS").put("quality", "cams"))
            .put(
                "quality_components",
                JSONObject()
                    .put("coverage_base_score", 0.76)
                    .put("raw_quality_index", 1.79)
                    .put("forward_burnable_cloud_ratio_peak", 0.56)
                    .put("forward_burnable_cloud_load_effective", 0.62)
                    .put("solar_incoming_block_risk_mean", 0.18)
                    .put("quality_aod550", 0.24),
            )
            .put("map_capability", JSONObject().put("available", false).put("reason", "native-nowcast-map-publication-not-requested"))
            .put(
                "profile_source",
                JSONObject()
                    .put("contract", "rust-desktop-profile-source-v1")
                    .put("available", true)
                    .put("query_method", "profile_query")
                    .put("model_source", "satellite")
                    .put("profile_kind", "satellite-step-solar-path-diagnostic")
                    .put("cycle_utc", "${date}T09:20:00Z")
                    .put("result_id", "a".repeat(64))
                    .put("source_identity", JSONObject().put("bytes", 4096).put("sha256", "b".repeat(64))),
            )
            .put("spectral_color", mockSpectralColor())
            .put("azimuth_radar", mockAzimuthRadar())
            .put("nowcast", nowcastObs)
            .put("cross_section", mockCrossSection(bestTime))
            .put("steps", steps)
            .put("summary", JSONArray(listOf("Himawari 卫星实况：高空卷云适中、地平线通透度良好。")))
    }

    private fun pointReport(date: String, peakIndex: Double, peakMinute: Int, spread: Double, modelSource: String = "gfs"): JSONObject {
        val steps = JSONArray()
        var bestIndex = 0.0
        var bestTime = "--"
        var windowStart = ""
        var windowEnd = ""
        for (minute in 17 * 60..20 * 60 + 30 step 5) {
            val delta = (minute - peakMinute).toDouble()
            val quality = 0.12 + peakIndex * exp(-(delta * delta) / (2 * spread * spread))
            val solar = 8.0 * cos(delta / 190.0 * kotlin.math.PI / 2) - 1.0
            val time = "%sT%02d:%02d:00".format(date, minute / 60, minute % 60)
            val valid = quality > 0.25
            if (valid && quality > bestIndex) {
                bestIndex = quality
                bestTime = time
            }
            if (quality >= 1.0) {
                if (windowStart.isEmpty()) windowStart = time
                windowEnd = time
            }
            steps.put(
                JSONObject()
                    .put("valid_time_local", time)
                    .put("valid", valid)
                    .put("selected_regime", if (valid) "burnable_cloud_field" else "invalid")
                    .put("instant_quality_index", quality)
                    .put("instant_quality_level", qualityLevel(quality))
                    .put("solar_elevation_deg", solar)
                    .put("physically_burnable", valid && quality > 0.55)
                    .put("burnable_cloud_coverage", (quality / 2.5).coerceIn(0.08, 0.88))
                    .put("dominant_burnable_pressure_family", if (quality >= 1.1) "high" else "middle")
                    .put("brightness_color", (0.42 + quality / 3.4).coerceIn(0.2, 0.95))
                    .put("forward_burnable_cloud_ratio", (0.35 + quality / 3.0).coerceIn(0.1, 0.92))
                    .put("observer_return_transmission", 0.73)
                    .put("solar_incoming_block_risk", 0.14)
                    .put("quality_aod550", 0.28),
            )
        }
        val covScore = if (modelSource == "gfs") 1.72 else 1.48
        val durAdj = if (modelSource == "gfs") 0.12 else 0.08
        val inAodAdj = -0.04
        val obsAodAdj = -0.02
        return JSONObject()
            .put("model_source", modelSource)
            .put("peak_quality_index", bestIndex)
            .put("peak_quality_level", qualityLevel(bestIndex))
            .put("quality_index", bestIndex * 0.96)
            .put("quality_level", qualityLevel(bestIndex))
            .put("best_time_local", bestTime)
            .put(
                "timing_window",
                JSONObject().put("start_time_local", windowStart.ifEmpty { "--" })
                    .put("end_time_local", windowEnd.ifEmpty { "--" }),
            )
            .put(
                "quality_components",
                JSONObject()
                    .put("coverage_base_score", covScore)
                    .put("raw_quality_index", bestIndex + 0.06)
                    .put("burnable_cloud_coverage_effective", 0.62 + bestIndex / 12)
                    .put("forward_burnable_cloud_load_effective", 0.64)
                    .put("forward_burnable_cloud_ratio_peak", if (modelSource == "gfs") 0.78 else 0.66)
                    .put("forward_burnable_cloud_field_amount_peak", if (modelSource == "gfs") 0.56 else 0.48)
                    .put("forward_view_cloud_field_amount_peak", 0.72)
                    .put("solar_incoming_block_risk_at_best_cloud", if (modelSource == "gfs") 0.14 else 0.22)
                    .put("duration_adjustment", durAdj)
                    .put("duration_factor", if (modelSource == "gfs") 1.068 else 1.045)
                    .put("incoming_aod_adjustment", inAodAdj)
                    .put("observer_aod_adjustment", obsAodAdj)
                    .put("cloud_material", 0.44 + bestIndex / 18)
                    .put("brightness_color", 0.53 + bestIndex / 8)
                    .put("incoming_aod_illumination_factor", 0.71)
                    .put("observer_aod_contrast_factor", 0.66)
                    .put("observer_return_optical_depth_mean", 0.31)
                    .put("observer_return_transmission_mean", 0.73)
                    .put("quality_aod550", 0.28)
                    .put("forward_field_radius_km", 200.0)
                    .put("forward_sector_half_angle_deg", 35.0)
                    .put("quality_limiting_factor", if (modelSource == "gfs") "轻度气溶胶消光" else "近场中低云轻微遮挡"),
            )
            .put("duration_minutes", if (modelSource == "gfs") 42.0 else 36.0)
            .put("confidence_score", 0.74)
            .put("convective_risk", 0.12)
            .put("model_disagreement_score", 0.18)
            .put("dominant_burnable_pressure_family", "high")
            .put("dominant_burnable_cloud_base_agl_mean_m", 7200.0)
            .put("dominant_burnable_cloud_top_agl_mean_m", 9800.0)
            .put(
                "cloud_genus_proxy",
                JSONObject()
                    .put("label", "cirrus")
                    .put("confidence", 0.72)
                    .put("source", "pressure-phase-geometry-proxy")
                    .put("scoring_effect", false)
                    .put("compatible_genera", JSONArray(listOf("cirrus", "cirrostratus")))
                    .put(
                        "candidate_scores",
                        JSONObject().put("cirrus", 0.72).put("cirrostratus", 0.21).put("altocumulus", 0.07),
                    ),
            )
            .put("spectral_color", mockSpectralColor())
            .put("azimuth_radar", mockAzimuthRadar())
            .put("cross_section", mockCrossSection(bestTime))
            .put("aerosol", JSONObject().put("aod550", 0.28).put("source", "CAMS").put("quality", "cams"))
            .put("summary", "模拟结果：傍晚高云量适中，霞光窗口集中，建议提前 15 分钟到场。")
            .put("cycle_time_utc", "2026-11-12T12:00:00Z")
            .put("steps", steps)
    }

    private fun mockSpectralColor(): JSONObject = JSONObject()
        .put("peak_hex", "#E85D2A")
        .put("peak_label_zh", "炽烈橙红霞")
        .put("peak_label_en", "Fiery Coral-Orange Glow")
        .put("sky_gradient_hex", JSONArray(listOf("#243B6B", "#7B5074", "#D86843", "#F49A42")))
        .put("solar_azimuth_deg", 251.2)
        .put("solar_elevation_deg", -1.4)

    private fun mockAzimuthRadar(): JSONObject {
        val bearings = JSONArray()
        val labelsZh = listOf("北", "北东北", "东北", "东东北", "东", "东东南", "东南", "南东南", "南", "南西南", "西南", "西西南", "西", "西西北", "西北", "北西北")
        for (i in 0 until 16) {
            val deg = i * 22.5
            val delta = kotlin.math.abs(((deg - 251.2 + 540.0) % 360.0) - 180.0)
            val score = (0.25 + 0.68 * exp(-(delta * delta) / (2 * 42.0 * 42.0))).coerceIn(0.1, 0.95)
            bearings.put(
                JSONObject()
                    .put("deg", deg)
                    .put("score", score)
                    .put("label_zh", labelsZh[i])
                    .put("label_en", "${deg.toInt()}°")
                    .put("canvas", 0.72)
                    .put("low_block", 0.14),
            )
        }
        return JSONObject()
            .put("solar_azimuth_deg", 251.2)
            .put("best_azimuth_deg", 247.5)
            .put("best_direction_zh", "西西南")
            .put("best_direction_en", "WSW")
            .put("best_score", 0.91)
            .put("recommendation_zh", "建议朝西西南方向（248°）低仰角取景，高空卷云受光完整。")
            .put("recommendation_en", "Face WSW (248°) for the clearest sunlit high cloud canvas.")
            .put("bearings", bearings)
    }

    private fun mockCrossSection(validTimeLocal: String): JSONObject {
        val distances = listOf(0.0, 40.0, 80.0, 120.0, 160.0, 200.0)
        val levels = listOf(250, 300, 500, 700, 850)
        val levelHeights = listOf(10300.0, 9100.0, 5600.0, 3000.0, 1500.0)
        val levelTemps = listOf(-46.0, -37.0, -14.0, 2.0, 11.0)
        val terrain = listOf(12.0, 85.0, 240.0, 620.0, 310.0, 90.0)
        val requiredAsl = listOf(1800.0, 2600.0, 3500.0, 4600.0, 5800.0, 7100.0)

        fun <T> grid(fn: (Int, Int) -> T): JSONArray = JSONArray().apply {
            for (i in distances.indices) {
                put(JSONArray().apply {
                    for (l in levels.indices) put(fn(i, l))
                })
            }
        }

        val cloudMask = Array(distances.size) { i ->
            Array(levels.size) { l ->
                ((l == 0 || l == 1) && i in 1..4) || (l == 2 && i in 2..3)
            }
        }
        fun layerStatus(i: Int, l: Int): Int {
            if (!cloudMask[i][l]) return 0
            if (l == 2 && i == 2) return 2
            return 3
        }

        return JSONObject()
            .put("distances_km", JSONArray(distances))
            .put("pressure_levels_hpa", JSONArray(levels))
            .put("terrain_elevation_asl_m", JSONArray(terrain))
            .put("required_illumination_base_asl_m", JSONArray(requiredAsl))
            .put("solar_elevation_deg", -1.4)
            .put("solar_azimuth_deg", 251.2)
            .put("analysis_radius_km", 200.0)
            .put("offset_minutes", 0.0)
            .put("valid_time_local", validTimeLocal)
            .put("spatial_sampling", "mock-6x5")
            .put(
                "fields",
                JSONObject()
                    .put("geopotential_height_asl_m", grid { _, l -> levelHeights[l] })
                    .put("temperature_c", grid { _, l -> levelTemps[l] })
                    .put("rh_percent", grid { i, l -> if (cloudMask[i][l]) 88.0 else 42.0 })
                    .put("cloud_amount", grid { i, l -> if (cloudMask[i][l]) 0.68 else 0.02 })
                    .put("cloud_presence", grid { i, l -> if (cloudMask[i][l]) 0.72 else 0.0 })
                    .put("cloud_fraction_percent", grid { i, l -> if (cloudMask[i][l]) 68.0 else 2.0 })
                    .put("pressure_family_code", grid { _, l -> if (l <= 1) 1 else if (l == 2) 2 else 3 })
                    .put("cloud_layer_status", grid { i, l -> layerStatus(i, l) })
                    .put("cloud_layer_base_asl_m", grid { _, l -> levelHeights[l] - 650.0 })
                    .put("cloud_layer_top_asl_m", grid { _, l -> levelHeights[l] + 650.0 }),
            )
            .put(
                "theoretical_targets",
                JSONArray().put(
                    JSONObject()
                        .put("status", "burnable")
                        .put("pressure_family", "high")
                        .put("distance_km", 120.0)
                        .put("cloud_base_asl_m", 7800.0)
                        .put("cloud_top_asl_m", 10400.0)
                        .put("cloud_phase", JSONObject().put("label", "ice")),
                ),
            )
    }

    private fun qualityLevel(value: Double): String = when {
        value >= 1.75 -> "绝佳"
        value >= 1.25 -> "优秀"
        value >= 0.75 -> "良好"
        else -> "一般"
    }

    // ---------------------------------------------------------------- 进度链

    private fun stage(requestId: String, source: String, label: String, steps: Int, delayMs: Long) {
        for (step in 1..steps) {
            checkCancelled(requestId)
            emitFrame(
                progressFrame(
                    requestId,
                    percent = step * 100.0 / (steps + 1),
                    source = source,
                    message = "$label $step/$steps",
                ),
            )
            Thread.sleep(delayMs)
        }
        checkCancelled(requestId)
    }

    private fun progressFrame(requestId: String, percent: Double, source: String, message: String): JSONObject =
        JSONObject()
            .put("id", requestId)
            .put("type", "progress")
            .put("event", JSONObject().put("percent", percent).put("source", source).put("message", message))

    private fun checkCancelled(requestId: String) {
        if (cancelledId.compareAndSet(requestId, null)) throw EngineError("任务已取消")
        if (!running.get()) throw InterruptedException("engine stopped")
    }

    private fun emitFrame(frame: JSONObject) {
        if (!running.get()) return
        onFrame?.onFrame(frame.toString())
    }
}
