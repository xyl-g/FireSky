package com.firesky.mobile.engine

import org.json.JSONObject
import java.io.BufferedReader
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * 远程服务器状态快照（对应 GET /api/v1/firesky/desktop/status）。
 */
data class RemoteServerStatus(
    val instanceId: String,
    val running: Boolean,
    val workspace: String,
    val protocol: Int,
    val engine: String,
)

/**
 * RemoteHttpTransport：通过 HTTP/HTTPS + SSE 实时流对接 FireSky 服务器桌面 HTTP 边界
 * （Mac / Windows / Docker + Cloudflare Tunnel 或局域网 HTTP），实现完整 EngineTransport 契约。
 *
 * 协议要点：
 * - `GET  <base>/api/v1/firesky/desktop/status` → 发现 `instance_id` / `workspace` / `protocol`；
 * - `POST <base>/api/v1/firesky/desktop/jobs` → 202 Accepted 提交作业；遇 `409 engine_exited`
 *   自动刷新 `/desktop/status` 并重试一次；
 * - `GET  <base>/api/v1/firesky/desktop/jobs/<request_id>/events?instance_id=<instance_id>` →
 *   优先建立 SSE (`Accept: text/event-stream`) 实时增量流；遇 `404`/`405`/非 2xx 或断流自动回退
 *   自适应轮询（`GET /desktop/jobs/<request_id>?instance_id=...`）；终态后自动 `release`；
 * - `POST <base>/api/v1/firesky/desktop/jobs/<request_id>/cancel` → 取消远程作业；
 * - 永不发送 `Origin` 头，且异常文案永不泄露明文令牌。
 */
class RemoteHttpTransport(
    baseUrl: String = "",
    token: String = "",
    private val pollDelayProvider: (elapsedMs: Long) -> Long = ::defaultPollDelayMs,
    private val connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
    private val readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
    private val sseReadTimeoutMs: Int = DEFAULT_SSE_READ_TIMEOUT_MS,
) : EngineTransport {

    @Volatile
    override var onFrame: FrameListener? = null

    @Volatile
    private var running = false

    @Volatile
    private var rawBaseUrl: String = baseUrl

    @Volatile
    private var normalizedBaseUrl: String = ""

    @Volatile
    private var bearerToken: String = token.trim()

    @Volatile
    private var instanceId: String? = null

    private val statusLock = Any()
    private val cancelledIds = ConcurrentHashMap.newKeySet<String>()
    private val activeConnections = ConcurrentHashMap<String, HttpURLConnection>()

    @Volatile
    private var executor: ExecutorService? = null

    override fun start(config: JSONObject) {
        stop()
        val configuredUrl = config.optString("base_url", "").ifBlank { rawBaseUrl }
        val configuredToken = if (config.has("token")) config.optString("token", "") else bearerToken
        rawBaseUrl = configuredUrl
        bearerToken = configuredToken.trim()
        normalizedBaseUrl = normalizeBaseUrl(configuredUrl)
        instanceId = null
        cancelledIds.clear()
        executor = Executors.newCachedThreadPool { runnable ->
            Thread(runnable, "firesky-remote-http").apply { isDaemon = true }
        }
        running = true
    }

    override fun send(frame: String) {
        if (!running) {
            throw EngineError("远程引擎未连接，请检查设置。")
        }
        if (frame.toByteArray(Charsets.UTF_8).size > EngineClient.MAX_LINE_BYTES) {
            throw EngineError("请求过大，请缩小输入内容。")
        }
        val request = runCatching { JSONObject(frame) }.getOrNull()
            ?: throw EngineError("请求帧格式无效。")
        val id = request.optString("id")
        val method = request.optString("method")
        val params = request.optJSONObject("params") ?: JSONObject()
        if (id.isEmpty() || method.isEmpty()) {
            throw EngineError("请求帧缺少 id 或 method。")
        }
        val exec = executor ?: throw EngineError("远程引擎未连接，请检查设置。")
        exec.execute {
            runRpcJob(id, method, params)
        }
    }

    override fun cancel(requestId: String) {
        if (!running || requestId.isEmpty()) return
        cancelledIds.add(requestId)
        val currentInstance = instanceId ?: return
        val exec = executor ?: return
        runCatching {
            exec.execute {
                postCancelQuietly(requestId, currentInstance)
            }
        }
    }

    override fun stop() {
        running = false
        instanceId = null
        cancelledIds.clear()
        activeConnections.values.forEach { conn ->
            runCatching { conn.disconnect() }
        }
        activeConnections.clear()
        executor?.shutdownNow()
        executor = null
    }

    private fun runRpcJob(id: String, method: String, params: JSONObject) {
        val base = normalizedBaseUrl
        val token = bearerToken
        try {
            if (!running) return
            var currentInstance = ensureInstanceId(base, token)
            if (!running) return

            val submitBody = JSONObject()
                .put("request_id", id)
                .put("instance_id", currentInstance)
                .put("method", method)
                .put("params", params)
            var submitResp = executeHttp(
                url = "$base$API_PREFIX/desktop/jobs",
                method = "POST",
                token = token,
                body = submitBody.toString(),
                connectTimeoutMs = connectTimeoutMs,
                readTimeoutMs = readTimeoutMs,
            )

            // 409 engine_exited：服务端实例已重启，重新拉取 /desktop/status 并重试一次。
            if (submitResp.statusCode == 409 && isEngineExited(submitResp.body)) {
                currentInstance = refreshInstanceId(base, token)
                val retryBody = JSONObject()
                    .put("request_id", id)
                    .put("instance_id", currentInstance)
                    .put("method", method)
                    .put("params", params)
                submitResp = executeHttp(
                    url = "$base$API_PREFIX/desktop/jobs",
                    method = "POST",
                    token = token,
                    body = retryBody.toString(),
                    connectTimeoutMs = connectTimeoutMs,
                    readTimeoutMs = readTimeoutMs,
                )
            }

            if (submitResp.statusCode !in 200..299) {
                throw EngineError(mapHttpError(submitResp.statusCode, submitResp.body, method, token))
            }

            if (cancelledIds.contains(id)) {
                postCancelQuietly(id, currentInstance)
            }

            val sseOutcome = subscribeSseUntilTerminal(base, token, id, currentInstance, method)
            if (!sseOutcome.terminal && running) {
                pollUntilTerminal(
                    base = base,
                    token = token,
                    id = id,
                    currentInstance = currentInstance,
                    method = method,
                    initialLastSeq = sseOutcome.lastSeq,
                )
            }
        } catch (error: Exception) {
            if (!running) return
            val safeMessage = sanitizeMessage(error.message ?: "远程请求失败。", token)
            emitErrorFrame(id, safeMessage.ifEmpty { "远程请求失败。" })
        } finally {
            cancelledIds.remove(id)
            activeConnections.remove(id)
        }
    }

    private data class SseOutcome(val terminal: Boolean, val lastSeq: Long)

    /**
     * 优先订阅 SSE 实时事件流；若服务端返回 404/405/非 2xx 或在终态前断开，
     * 透明回退到自适应轮询并保留已收到的 `lastSeq` 避免重复进度帧。
     */
    private fun subscribeSseUntilTerminal(
        base: String,
        token: String,
        id: String,
        currentInstance: String,
        method: String,
    ): SseOutcome {
        val encodedId = URLEncoder.encode(id, "UTF-8")
        val encodedInstance = URLEncoder.encode(currentInstance, "UTF-8")
        val eventsUrl = "$base$API_PREFIX/desktop/jobs/$encodedId/events?instance_id=$encodedInstance"
        var lastSeq = -1L
        var connection: HttpURLConnection? = null
        try {
            connection = (URI.create(eventsUrl).toURL().openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                this.connectTimeout = connectTimeoutMs
                this.readTimeout = sseReadTimeoutMs
                useCaches = false
                setRequestProperty("Accept", "text/event-stream")
                setRequestProperty("Cache-Control", "no-cache")
                val trimmedToken = token.trim()
                if (trimmedToken.isNotEmpty()) {
                    setRequestProperty("Authorization", "Bearer $trimmedToken")
                }
            }
            activeConnections[id] = connection
            val code = connection.responseCode
            if (!running) return SseOutcome(terminal = true, lastSeq = lastSeq)

            if (code !in 200..299) {
                val errBody = connection.errorStream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
                if (code == 409 && isEngineExited(errBody)) {
                    synchronized(statusLock) {
                        if (instanceId == currentInstance) instanceId = null
                    }
                    throw EngineError(mapHttpError(code, errBody, method, token))
                }
                // 404 / 405 / 其他非 2xx：回退至轮询端点
                return SseOutcome(terminal = false, lastSeq = lastSeq)
            }

            BufferedReader(InputStreamReader(connection.inputStream, Charsets.UTF_8)).use { reader ->
                val dataBuffer = StringBuilder()
                while (running) {
                    val line = reader.readLine() ?: break
                    when {
                        line.isEmpty() -> {
                            if (dataBuffer.isNotEmpty()) {
                                val payload = dataBuffer.toString()
                                dataBuffer.setLength(0)
                                val (terminal, updatedSeq) = processSsePayload(
                                    payload = payload,
                                    base = base,
                                    token = token,
                                    id = id,
                                    currentInstance = currentInstance,
                                    lastSeq = lastSeq,
                                )
                                lastSeq = updatedSeq
                                if (terminal) return SseOutcome(terminal = true, lastSeq = lastSeq)
                            }
                        }
                        line.startsWith(":") -> Unit // SSE keepalive comment
                        line.startsWith("data:") -> {
                            val chunk = line.removePrefix("data:").let {
                                if (it.startsWith(" ")) it.substring(1) else it
                            }
                            if (dataBuffer.isNotEmpty()) dataBuffer.append('\n')
                            dataBuffer.append(chunk)
                        }
                    }
                }
                if (running && dataBuffer.isNotEmpty()) {
                    val (terminal, updatedSeq) = processSsePayload(
                        payload = dataBuffer.toString(),
                        base = base,
                        token = token,
                        id = id,
                        currentInstance = currentInstance,
                        lastSeq = lastSeq,
                    )
                    lastSeq = updatedSeq
                    if (terminal) return SseOutcome(terminal = true, lastSeq = lastSeq)
                }
            }
            return SseOutcome(terminal = false, lastSeq = lastSeq)
        } catch (error: EngineError) {
            throw error
        } catch (_: IOException) {
            // 流提前断开或读取超时，透明回退至自适应轮询
            return SseOutcome(terminal = false, lastSeq = lastSeq)
        } finally {
            activeConnections.remove(id, connection)
            runCatching { connection?.disconnect() }
        }
    }

    private fun processSsePayload(
        payload: String,
        base: String,
        token: String,
        id: String,
        currentInstance: String,
        lastSeq: Long,
    ): Pair<Boolean, Long> {
        val snapshot = runCatching { JSONObject(payload) }.getOrNull() ?: return false to lastSeq
        return applySnapshot(snapshot, base, token, id, currentInstance, lastSeq)
    }

    private fun applySnapshot(
        snapshot: JSONObject,
        base: String,
        token: String,
        id: String,
        currentInstance: String,
        previousSeq: Long,
    ): Pair<Boolean, Long> {
        var lastSeq = previousSeq
        val seq = snapshot.optLong("progress_seq", 0L)
        val progressObj = snapshot.optJSONObject("progress")
        if (seq > lastSeq && progressObj != null) {
            lastSeq = seq
            emitFrame(
                JSONObject()
                    .put("id", id)
                    .put("type", "progress")
                    .put("event", progressObj),
            )
        }

        return when (val status = snapshot.optString("status")) {
            "", "running" -> false to lastSeq
            "done" -> {
                releaseJobQuietly(base, token, id, currentInstance)
                val resultObj = snapshot.optJSONObject("result") ?: JSONObject()
                emitFrame(
                    JSONObject()
                        .put("id", id)
                        .put("type", "result")
                        .put("result", resultObj),
                )
                true to lastSeq
            }
            "failed", "cancelled" -> {
                releaseJobQuietly(base, token, id, currentInstance)
                val rawError = snapshot.optJSONObject("error")
                val rawMessage = rawError?.optString("message").orEmpty().ifEmpty {
                    if (status == "cancelled") "desktop operation cancelled" else "操作失败"
                }
                val safeError = JSONObject()
                    .put("code", rawError?.optString("code").orEmpty().ifEmpty { status })
                    .put("message", sanitizeMessage(rawMessage, token))
                emitFrame(
                    JSONObject()
                        .put("id", id)
                        .put("type", "error")
                        .put("error", safeError),
                )
                true to lastSeq
            }
            else -> {
                releaseJobQuietly(base, token, id, currentInstance)
                emitErrorFrame(id, "远程任务返回未知状态：$status")
                true to lastSeq
            }
        }
    }

    private fun pollUntilTerminal(
        base: String,
        token: String,
        id: String,
        currentInstance: String,
        method: String,
        initialLastSeq: Long = -1L,
    ) {
        val encodedId = URLEncoder.encode(id, "UTF-8")
        val encodedInstance = URLEncoder.encode(currentInstance, "UTF-8")
        val pollUrl = "$base$API_PREFIX/desktop/jobs/$encodedId?instance_id=$encodedInstance"
        val startedAtMs = System.currentTimeMillis()
        var lastSeq = initialLastSeq

        while (running) {
            val resp = executeHttp(
                url = pollUrl,
                method = "GET",
                token = token,
                body = null,
                connectTimeoutMs = connectTimeoutMs,
                readTimeoutMs = readTimeoutMs,
            )
            if (!running) return

            if (resp.statusCode !in 200..299) {
                if (resp.statusCode == 409 && isEngineExited(resp.body)) {
                    synchronized(statusLock) {
                        if (instanceId == currentInstance) instanceId = null
                    }
                }
                throw EngineError(mapHttpError(resp.statusCode, resp.body, method, token))
            }

            val snapshot = runCatching { JSONObject(resp.body) }.getOrNull()
                ?: throw EngineError("远程服务器返回了无效的任务状态。")

            val (terminal, updatedSeq) = applySnapshot(snapshot, base, token, id, currentInstance, lastSeq)
            lastSeq = updatedSeq
            if (terminal) return

            val elapsed = (System.currentTimeMillis() - startedAtMs).coerceAtLeast(0L)
            val delayMs = pollDelayProvider(elapsed).coerceAtLeast(1L)
            try {
                Thread.sleep(delayMs)
            } catch (_: InterruptedException) {
                return
            }
        }
    }

    private fun ensureInstanceId(base: String, token: String): String {
        instanceId?.let { return it }
        return refreshInstanceId(base, token)
    }

    private fun refreshInstanceId(base: String, token: String): String = synchronized(statusLock) {
        val status = fetchStatus(base, token, connectTimeoutMs, readTimeoutMs)
        instanceId = status.instanceId
        status.instanceId
    }

    private fun postCancelQuietly(id: String, currentInstance: String) {
        val base = normalizedBaseUrl
        if (base.isEmpty()) return
        val encodedId = URLEncoder.encode(id, "UTF-8")
        runCatching {
            executeHttp(
                url = "$base$API_PREFIX/desktop/jobs/$encodedId/cancel",
                method = "POST",
                token = bearerToken,
                body = JSONObject().put("instance_id", currentInstance).toString(),
                connectTimeoutMs = connectTimeoutMs,
                readTimeoutMs = readTimeoutMs,
            )
        }
    }

    private fun releaseJobQuietly(base: String, token: String, id: String, currentInstance: String) {
        val encodedId = URLEncoder.encode(id, "UTF-8")
        runCatching {
            executeHttp(
                url = "$base$API_PREFIX/desktop/jobs/$encodedId/release",
                method = "POST",
                token = token,
                body = JSONObject().put("instance_id", currentInstance).toString(),
                connectTimeoutMs = connectTimeoutMs,
                readTimeoutMs = RELEASE_READ_TIMEOUT_MS,
            )
        }
    }

    private fun emitErrorFrame(id: String, message: String) {
        emitFrame(
            JSONObject()
                .put("id", id)
                .put("type", "error")
                .put("error", JSONObject().put("code", "remote_error").put("message", message)),
        )
    }

    private fun emitFrame(frame: JSONObject) {
        onFrame?.onFrame(frame.toString())
    }

    private data class HttpResponse(val statusCode: Int, val body: String)

    companion object {
        private const val API_PREFIX = "/api/v1/firesky"
        private const val DEFAULT_CONNECT_TIMEOUT_MS = 10_000
        private const val DEFAULT_READ_TIMEOUT_MS = 15_000
        private const val DEFAULT_SSE_READ_TIMEOUT_MS = 60_000
        private const val RELEASE_READ_TIMEOUT_MS = 3_000
        private const val MAX_RESPONSE_BYTES = 48 * 1024 * 1024

        /** 自适应轮询间隔：前 2s 为 150ms，2s–10s 为 350ms，10s 后为 600ms。 */
        fun defaultPollDelayMs(elapsedMs: Long): Long = when {
            elapsedMs < 2_000L -> 150L
            elapsedMs < 10_000L -> 350L
            else -> 600L
        }

        /**
         * 规范化服务器基础地址：去首尾空白、去掉末尾 `/`、去掉误粘贴的 `/api/v1/firesky` 后缀，
         * 校验必须为 `http://` 或 `https://` 且包含有效主机名。
         */
        fun normalizeBaseUrl(raw: String): String {
            val trimmed = raw.trim()
            if (trimmed.isEmpty()) {
                throw EngineError("请输入远程服务器地址。")
            }
            val lower = trimmed.lowercase()
            if (!lower.startsWith("http://") && !lower.startsWith("https://")) {
                throw EngineError("服务器地址必须以 http:// 或 https:// 开头。")
            }
            var normalized = trimmed.trimEnd('/')
            if (normalized.endsWith(API_PREFIX)) {
                normalized = normalized.removeSuffix(API_PREFIX).trimEnd('/')
            }
            val uri = runCatching { URI.create(normalized) }.getOrNull()
                ?: throw EngineError("服务器地址格式无效。")
            val scheme = uri.scheme?.lowercase()
            if ((scheme != "http" && scheme != "https") || uri.host.isNullOrBlank()) {
                throw EngineError("服务器地址格式无效，请检查主机名与端口。")
            }
            return normalized
        }

        /**
         * 测试连接：规范化 URL 后请求 `GET <base>/api/v1/firesky/desktop/status`，
         * 返回远端 `workspace` / `engine` / `protocol` / `instanceId`。
         */
        fun testConnection(
            baseUrl: String,
            token: String,
            connectTimeoutMs: Int = DEFAULT_CONNECT_TIMEOUT_MS,
            readTimeoutMs: Int = DEFAULT_READ_TIMEOUT_MS,
        ): RemoteServerStatus {
            val normalized = normalizeBaseUrl(baseUrl)
            return fetchStatus(normalized, token.trim(), connectTimeoutMs, readTimeoutMs)
        }

        private fun fetchStatus(
            normalizedBaseUrl: String,
            token: String,
            connectTimeoutMs: Int,
            readTimeoutMs: Int,
        ): RemoteServerStatus {
            val resp = executeHttp(
                url = "$normalizedBaseUrl$API_PREFIX/desktop/status",
                method = "GET",
                token = token,
                body = null,
                connectTimeoutMs = connectTimeoutMs,
                readTimeoutMs = readTimeoutMs,
            )
            if (resp.statusCode !in 200..299) {
                throw EngineError(mapHttpError(resp.statusCode, resp.body, method = null, token = token))
            }
            val json = runCatching { JSONObject(resp.body) }.getOrNull()
                ?: throw EngineError("远程服务器返回的状态格式无效。")
            val instanceId = json.optString("instance_id")
            val running = json.optBoolean("running", false)
            val protocol = json.optInt("protocol", -1)
            if (instanceId.isEmpty() || !running) {
                throw EngineError("远程 FireSky 服务器尚未就绪。")
            }
            if (protocol != 1) {
                throw EngineError("远程服务器协议版本不匹配（protocol=$protocol）。")
            }
            return RemoteServerStatus(
                instanceId = instanceId,
                running = running,
                workspace = json.optString("workspace", ""),
                protocol = protocol,
                engine = json.optString("engine", "firesky-rust"),
            )
        }

        private fun executeHttp(
            url: String,
            method: String,
            token: String,
            body: String?,
            connectTimeoutMs: Int,
            readTimeoutMs: Int,
        ): HttpResponse {
            var connection: HttpURLConnection? = null
            try {
                connection = (URI.create(url).toURL().openConnection() as HttpURLConnection).apply {
                    requestMethod = method
                    this.connectTimeout = connectTimeoutMs
                    this.readTimeout = readTimeoutMs
                    useCaches = false
                    setRequestProperty("Accept", "application/json")
                    val trimmedToken = token.trim()
                    if (trimmedToken.isNotEmpty()) {
                        setRequestProperty("Authorization", "Bearer $trimmedToken")
                    }
                    if (method == "POST") {
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json")
                    }
                }
                if (body != null && method == "POST") {
                    connection.outputStream.use { stream ->
                        stream.write(body.toByteArray(Charsets.UTF_8))
                    }
                }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val text = stream?.let { readBoundedUtf8(it, MAX_RESPONSE_BYTES) }.orEmpty()
                return HttpResponse(code, text)
            } catch (error: IOException) {
                val sanitized = sanitizeMessage(error.message ?: error.javaClass.simpleName, token)
                val friendly = friendlyNetworkDetail(sanitized)
                throw EngineError("无法连接远程服务器：$friendly")
            } finally {
                connection?.disconnect()
            }
        }

        private fun friendlyNetworkDetail(raw: String): String {
            val lower = raw.lowercase()
            return when {
                "unexpected end of stream" in lower || "eof" in lower || "connection reset" in lower || "broken pipe" in lower ->
                    "服务端未响应或连接已断开，请确认桌面端 FireSky 已启动"
                "failed to connect" in lower || "econnrefused" in lower || "connect exception" in lower ->
                    "无法连接到服务端，请确认桌面端已启动且地址端口正确"
                "timed out" in lower || "timeout" in lower ->
                    "连接超时，请检查服务端状态与网络连接"
                "unable to resolve host" in lower || "unknownhost" in lower ->
                    "无法解析服务器地址，请检查域名或网络"
                else -> raw.replace(Regex("""on com\.android\.okhttp\.Address@[0-9a-fA-F]+"""), "").trim()
            }
        }

        private fun readBoundedUtf8(stream: InputStream, maxBytes: Int): String {
            stream.use { input ->
                val out = ByteArrayOutputStream()
                val buf = ByteArray(64 * 1024)
                var total = 0
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    total += n
                    if (total > maxBytes) {
                        throw EngineError("服务端返回数据过大（超过 48 MB），请在桌面端查看完整结果。")
                    }
                    out.write(buf, 0, n)
                }
                return out.toString(Charsets.UTF_8.name())
            }
        }

        internal fun isEngineExited(responseBody: String): Boolean {
            val code = runCatching {
                JSONObject(responseBody).optJSONObject("error")?.optString("code")
            }.getOrNull()
            return code == "engine_exited" || responseBody.contains("engine_exited")
        }

        internal fun mapHttpError(
            statusCode: Int,
            responseBody: String,
            method: String?,
            token: String,
        ): String {
            val parsedError = runCatching { JSONObject(responseBody).optJSONObject("error") }.getOrNull()
            val errorCode = parsedError?.optString("code").orEmpty()
            val errorMsg = parsedError?.optString("message").orEmpty()
            val mapped = when {
                statusCode == 401 ->
                    "服务器鉴权失败：请检查访问令牌（Token）是否正确。"
                statusCode == 403 && (errorCode == "untrusted_host" || responseBody.contains("untrusted_host")) ->
                    "Mac 端未开启远程访问：请在 Mac「设置 → 高级 → 局域网与隧道服务」开启令牌认证或局域网访问。"
                statusCode == 403 && (errorCode == "untrusted_origin" || responseBody.contains("untrusted_origin")) ->
                    "远程访问来源校验未通过（untrusted_origin）。"
                statusCode == 404 && method == "save_cams_key" ->
                    "远程模式下不支持通过网络修改 Mac 的 CAMS Key，请在 Mac 端本地配置。"
                statusCode == 429 ->
                    "Mac 端引擎正忙（HTTP 429），请稍后再试。"
                statusCode == 409 && errorCode == "engine_exited" ->
                    "Mac 端服务已重启，请重新连接并核对数据。"
                errorMsg.isNotBlank() -> errorMsg
                else -> "远程服务器返回错误（HTTP $statusCode）。"
            }
            return sanitizeMessage(mapped, token)
        }

        internal fun sanitizeMessage(message: String, token: String): String {
            val trimmed = token.trim()
            val noToken = if (trimmed.isNotEmpty()) message.replace(trimmed, "***") else message
            return noToken.replace(Regex("(?i)Bearer\\s+\\S+"), "Bearer ***")
        }
    }
}
