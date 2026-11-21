package com.firesky.mobile.engine

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * MockTransport：进程内模拟 desktop JSON-lines 服务，让 Android 骨架在 engine-mobile FFI
 * （MN2 产出 `libengine_mobile.so`）接入前即可编译、运行
 * hello → search/metadata → check → publications → prepare → forecast 全链路。
 *
 * 帧语义与真实引擎一致（engine/src/rpc.rs）：
 * - result / error / progress 三类下行帧；
 * - `cancel(request_id)` 打断在途任务并以 error 帧收尾（消息「任务已取消」）；
 * - 超 1 MiB 行回 `{"id":null,"type":"error","code":"invalid_request"}`；
 * - check→publications→prepare→forecast 会话令牌语义（session token）。
 *
 * 预置缓存样本（对齐 MN1 验收「allow_download:false + 预置缓存样本」）：
 * GFS/EC 数据标记已就绪，CAMS 缺失 → 演示「下载缺失数据」→ prepare 进度链。
 */
class MockTransport : EngineTransport {

    @Volatile
    override var onFrame: FrameListener? = null

    private val running = AtomicBoolean(false)
    private val cancelledId = AtomicReference<String?>(null)
    private var worker: ExecutorService? = null

    // 以下状态仅在 mock 引擎单线程（worker）内读写。
    private var sessionToken: String? = null
    private var lastPayload: JSONObject? = null
    private var datasets: List<Triple<String, String, Boolean>> = emptyList()
    private var camsConfigured = false

    override fun start(config: JSONObject) {
        stop()
        cancelledId.set(null)
        sessionToken = null
        lastPayload = null
        camsConfigured = false
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
            // 对齐 rpc.rs：超限行直接回 invalid_request，不中断服务。
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
        "task_history" -> taskHistory()
        "task_events" -> taskEvents()
        "task_forget" -> JSONObject().put("forgotten", true)
        "result_restore" -> resultRestore()
        "data_delete_preview" -> dataDeletePreview()
        "data_delete_execute" -> dataDeleteExecute()
        "terrain_inventory" -> terrainInventory(params)
        "terrain_download" -> terrainDownload(requestId)
        "cams_credentials" -> camsCredentials()
        "save_cams_key" -> saveCamsKey(params)
        "cancel" -> JSONObject()
        else -> throw EngineError("unsupported method: $method")
    }

    private fun hello(): JSONObject = JSONObject()
        .put("protocol", 1)
        .put("engine", "firesky-mock")
        .put("migration_stage", "mock-transport")
        .put(
            "capabilities",
            JSONArray(
                listOf(
                    "score_window", "check_desktop_data", "desktop_inventory",
                    "desktop_publications", "desktop_prepare", "desktop_session", "location",
                    "task_history", "task_events", "task_forget", "result_restore",
                    "data_delete", "terrain_inventory", "terrain_download",
                ),
            ),
        )
        // v1 范围：无区域预测（MOBILE_NATIVE_PLAN.md §3）。
        .put("regions", JSONArray())
        .put("cams_credentials", camsCredentials())

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
        if (params.optString("kind") != "point") {
            throw EngineError("v1 仅支持单点预测（kind=point），区域预测已按蓝图排除。")
        }
        val payload = params.optJSONObject("payload") ?: JSONObject()
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
                stage(requestId, source, "正在准备 $source 数据", steps = 8, delayMs = 90)
            }
        }
        datasets = datasets.map { it.copy(third = true) }
        return JSONObject().put("report", availabilityReport())
    }

    private fun forecast(requestId: String): JSONObject {
        sessionToken ?: throw EngineError("会话不存在，请重新检查数据。")
        val payload = lastPayload ?: JSONObject()
        if (!datasets.all { it.third }) throw EngineError("本地数据不完整，请先准备数据。")
        stage(requestId, "gfs", "正在计算 GFS", steps = 6, delayMs = 80)
        stage(requestId, "ec", "正在计算 ECMWF", steps = 6, delayMs = 80)
        stage(requestId, "assess", "正在评估霞光质量", steps = 4, delayMs = 80)
        val payloadDate = payload.optString("target_date").ifEmpty { java.time.LocalDate.now().toString() }
        val gfs = pointReport(payloadDate, peakIndex = 1.82, peakMinute = 19 * 60 + 5, spread = 46.0)
        val ec = pointReport(payloadDate, peakIndex = 1.58, peakMinute = 19 * 60 + 25, spread = 58.0)
        return JSONObject()
            .put("report", availabilityReport())
            .put("model_results", JSONObject().put("gfs", gfs).put("ec", ec))
            .put("model_errors", JSONObject())
    }

    private fun dismiss(): JSONObject {
        sessionToken = null
        return JSONObject()
    }

    private fun taskHistory(): JSONObject {
        val payload = JSONObject()
            .put("kind", "point")
            .put("payload", JSONObject()
                .put("name", "杭州").put("lat", 30.2741).put("lon", 120.1551)
                .put("target_date", "2026-11-09").put("phenomenon", "dusk"))
        val running = JSONObject()
            .put("task_id", "b".repeat(64)).put("method", "prepare").put("status", "running")
            .put("created_at_utc", "2026-11-21T09:00:00.000Z")
            .put("updated_at_utc", "2026-11-21T09:01:00.000Z")
            .put("cancel_requested", false)
            .put("event_seq", 2).put("progress", JSONObject().put("percent", 40.0))
            .put("retry_request", payload)
        val done = JSONObject()
            .put("task_id", "a".repeat(64)).put("method", "forecast").put("status", "done")
            .put("created_at_utc", "2026-11-20T12:27:33.706Z")
            .put("updated_at_utc", "2026-11-20T12:27:42.600Z")
            .put("cancel_requested", false)
            .put("event_seq", 5).put("retry_request", payload)
            .put("result_ref", JSONObject().put("snapshot", JSONObject()
                .put("contract", "firesky-result-snapshot-v1").put("sha256", "c".repeat(64))))
        val sync = JSONObject()
            .put("task_id", "d".repeat(64)).put("method", "data_sync").put("status", "done")
            .put("created_at_utc", "2026-11-20T04:00:00.000Z")
            .put("updated_at_utc", "2026-11-20T04:01:00.000Z")
            .put("cancel_requested", false).put("event_seq", 1)
        return JSONObject()
            .put("schema", "firesky-task-history-v1")
            .put("automatic_restart", false)
            .put("total", 3)
            .put("issues", JSONArray())
            .put("tasks", JSONArray().put(running).put(done).put(sync))
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
            .put("events", JSONArray()
                .put(event(1, "running", null, "", "预测计算已开始"))
                .put(event(2, "progress", 30.0, "prepare", "正在准备 GFS 与 CAMS 数据"))
                .put(event(3, "progress", 70.0, "prepare", "GFS 数据已就绪"))
                .put(event(4, "progress", 90.0, "forecast", "预测计算进行中"))
                .put(event(5, "done", null, "", "预测完成")))
            .put("next_seq", 6).put("truncated", false)
    }

    private fun resultRestore(): JSONObject = JSONObject()
        .put("model_results", JSONObject().put("gfs", JSONObject().put("score", 0.8)))

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
            .put("tiles", JSONArray()
                .put(JSONObject().put("name", "N30E119").put("bytes", 25_934_402))
                .put(JSONObject().put("name", "N30E120").put("bytes", 25_934_402))
                .put(JSONObject().put("name", "N31E120").put("bytes", 25_934_402)))
            .put("coverage_ok", true)
            .put("tile", JSONObject().put("name", "N30E120").put("exists", true).put("bytes", 25_934_402)
                .put("lat_range", JSONArray(listOf(30.0, 31.0))).put("lon_range", JSONArray(listOf(120.0, 121.0))))
        if (params.optBoolean("estimate_global", false)) {
            // 与真引擎同口径：bounds（缺省全球）→ 候选数 + 已有 + 估算体积。
            val bounds = params.optJSONObject("bounds") ?: JSONObject()
                .put("south", -56).put("north", 59).put("west", -180).put("east", 179)
            val candidates = (bounds.optInt("north", 0) - bounds.optInt("south", 0) + 1) *
                (bounds.optInt("east", 0) - bounds.optInt("west", 0) + 1)
            result.put("global", JSONObject()
                .put("candidates", candidates)
                .put("existing", 2)
                .put("estimated_bytes", candidates * 6_000_000L)
                .put("estimated_gb", candidates * 0.006)
                .put("bounds", bounds))
        }
        return result
    }

    /** mock 批量下载：模拟 4 块瓦片进度后完成（真下载走 JniTransport 真引擎）。 */
    private fun terrainDownload(requestId: String): JSONObject {
        val total = 4
        for (index in 1..total) {
            if (index > 1) Thread.sleep(400)
            emitFrame(JSONObject().put("id", requestId).put("type", "progress").put("event",
                JSONObject().put("terrain", JSONObject()
                    .put("stage", "global").put("candidates", total)
                    .put("downloaded", index).put("absent", 0).put("failed", 0)
                    .put("existing", 0).put("received_bytes", index * 10_000_000L)
                    .put("percent", index * 100.0 / total)
                    .put("current_tile", "N30E11$index"))))
        }
        return JSONObject()
            .put("contract", "rust-terrain-download-v1")
            .put("status", "done").put("mode", "global").put("candidates", total)
            .put("existing", 0).put("downloaded", total).put("absent", 0).put("failed", 0)
            .put("received_bytes", total * 10_000_000L).put("received_gb", total * 0.01)
            .put("stopped_reason", JSONObject.NULL)
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
                    .put("path", "/data/data/com.firesky.mobile/files/cache/$source"),
            )
        }
        return JSONObject().put("datasets", rows)
    }

    private fun camsCredentials(): JSONObject = JSONObject()
        .put("configured", camsConfigured)
        .put("can_save", true)
        .put(
            "state",
            if (camsConfigured) "configured" else "unconfigured",
        )

    private fun saveCamsKey(params: JSONObject): JSONObject {
        val key = params.optString("key", "").trim()
        if (key.isEmpty()) throw EngineError("Key 为空，未保存。")
        camsConfigured = true
        return camsCredentials()
    }

    // ---------------------------------------------------------------- 合成报告

    /** 数据可用性报告（对应 Store.applyReport 读取的 report 结构）。 */
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
                    .put(
                        "detail",
                        if (ready) "本地预置缓存样本（模拟传输）" else "需下载本次数据（模拟传输）",
                    ),
            )
        }
        return JSONObject().put("ready", datasets.all { it.third }).put("datasets", rows)
    }

    /**
     * 合成单点报告：以目标日期傍晚为窗生成正弦质量曲线，结构与桌面 point 预测
     * 的 report 键一致（ForecastComparison.swift / Workbench.swift metricSpec 消费）。
     */
    private fun pointReport(date: String, peakIndex: Double, peakMinute: Int, spread: Double): JSONObject {
        val steps = JSONArray()
        var bestIndex = 0.0
        var bestTime = "--"
        var windowStart = ""
        var windowEnd = ""
        for (minute in 17 * 60..20 * 60 + 30 step 5) {
            val delta = (minute - peakMinute).toDouble()
            val quality = 0.12 + peakIndex * kotlin.math.exp(-(delta * delta) / (2 * spread * spread))
            val solar = 8.0 * kotlin.math.cos(delta / 190.0 * kotlin.math.PI / 2) - 1.0
            val time = "%sT%02d:%02d:00".format(date, minute / 60, minute % 60)
            val valid = quality > 0.15
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
                    .put("instant_quality_index", quality)
                    .put("instant_quality_level", qualityLevel(quality))
                    .put("solar_elevation_deg", solar)
                    .put("physically_burnable", quality > 0.55),
            )
        }
        return JSONObject()
            .put("peak_quality_index", bestIndex)
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
                    .put("burnable_cloud_coverage_effective", 0.62 + bestIndex / 12)
                    .put("cloud_material", 0.44 + bestIndex / 18)
                    .put("brightness_color", 0.53 + bestIndex / 8)
                    .put("incoming_aod_illumination_factor", 0.71)
                    .put("observer_aod_contrast_factor", 0.66)
                    .put("quality_aod550", 0.28),
            )
            .put("duration_minutes", 42.0)
            .put("confidence_score", 0.74)
            .put("model_disagreement_score", 0.18)
            .put(
                "aerosol",
                JSONObject().put("aod550", 0.28).put("source", "CAMS").put("quality", "cams"),
            )
            .put("summary", "模拟结果：傍晚云量适中，霞光窗口集中，建议提前 15 分钟到场。")
            .put("cycle_time_utc", "2026-11-12T12:00:00Z")
            .put("steps", steps)
    }

    private fun qualityLevel(value: Double): String = when {
        value >= 1.75 -> "绝佳"
        value >= 1.25 -> "优秀"
        value >= 0.75 -> "良好"
        else -> "一般"
    }

    // ---------------------------------------------------------------- 进度链

    /** 分阶段发 progress 帧（对应桌面 progress 三链语义），可被 cancel 打断。 */
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
            .put(
                "event",
                JSONObject().put("percent", percent).put("source", source).put("message", message),
            )

    private fun checkCancelled(requestId: String) {
        if (cancelledId.compareAndSet(requestId, null)) throw EngineError("任务已取消")
        if (!running.get()) throw InterruptedException("engine stopped")
    }

    private fun emitFrame(frame: JSONObject) {
        if (!running.get()) return
        onFrame?.onFrame(frame.toString())
    }
}
