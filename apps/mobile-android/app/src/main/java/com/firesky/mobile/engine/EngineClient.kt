package com.firesky.mobile.engine

import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.suspendCancellableCoroutine
import org.json.JSONException
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlin.coroutines.coroutineContext
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** 与桌面端一致的引擎错误（消息文本直接透出给 UI）。 */
class EngineError(message: String) : Exception(message)

/**
 * desktop JSON-lines 协议客户端 —— 同构 apps/macos/Sources/FireSkyNative/EngineClient.swift，
 * 协议帧/握手/取消语义照抄（蓝图 §2：「EngineClient.kt 新写，协议帧/握手/进度三链语义照抄」）。
 *
 * 帧契约（engine/src/rpc.rs::response）：
 * - 请求 `{"id": <uuid>, "method": <str>, "params": <object>}` + `\n`，行上限 1 MiB（MAX_LINE_BYTES）；
 * - 进度 `{"id","type":"progress","event":{…}}` → onProgress(event)；
 * - 错误 `{"id","type":"error","error":{"code","message"}}` → 抛 EngineError(message)；
 * - 结果 `{"id","type":"result","result":{…}}` → 返回 result；
 * - 取消：以独立 id 发送 `{"method":"cancel","params":{"request_id":<原 id>}}`。
 *
 * 握手（Store.connect 语义）：`call("hello")` 返回 `protocol == 1` 视为兼容，
 * 附带 `regions` / `cams_credentials`。
 */
class EngineClient(private val transport: EngineTransport) {

    private val pending = ConcurrentHashMap<String, CancellableContinuation<JSONObject>>()
    private val progress = ConcurrentHashMap<String, (JSONObject) -> Unit>()

    @Volatile
    private var closed = true

    /** 装配传输层并开始接收下行帧。 */
    fun start(config: JSONObject) {
        closed = false
        transport.onFrame = FrameListener(::receive)
        transport.start(config)
    }

    /**
     * 发起一次调用；请求协程被取消时自动发送 cancel 帧（对应 Swift 的
     * withTaskCancellationHandler onCancel 分支）。
     */
    suspend fun call(
        method: String,
        params: JSONObject = JSONObject(),
        onProgress: ((JSONObject) -> Unit)? = null,
    ): JSONObject {
        coroutineContext.ensureActive()
        if (closed) throw EngineError("引擎未连接，请检查设置。")
        val id = UUID.randomUUID().toString()
        val request = JSONObject().put("id", id).put("method", method).put("params", params)
        val frame = request.toString()
        if (frame.toByteArray(Charsets.UTF_8).size > MAX_LINE_BYTES) {
            throw EngineError("请求过大，请缩小输入内容。")
        }
        try {
            return suspendCancellableCoroutine { continuation ->
                pending[id] = continuation
                if (onProgress != null) progress[id] = onProgress
                try {
                    transport.send(frame)
                } catch (error: Exception) {
                    pending.remove(id)
                    progress.remove(id)
                    continuation.resumeWithException(
                        error as? EngineError ?: EngineError(error.message ?: "请求写入失败。"),
                    )
                }
                continuation.invokeOnCancellation { transport.cancel(id) }
            }
        } catch (error: CancellationException) {
            throw error
        }
    }

    /** 处理一条下行帧（运行在传输层线程；pending/progress 为并发容器）。 */
    private fun receive(frame: String) {
        val message = try {
            JSONObject(frame)
        } catch (_: JSONException) {
            return
        }
        val id = message.optString("id")
        if (id.isEmpty()) return
        when (message.optString("type")) {
            "progress" -> progress[id]?.invoke(message.optJSONObject("event") ?: JSONObject())
            "error" -> {
                val continuation = pending.remove(id) ?: return
                progress.remove(id)
                val text = message.optJSONObject("error")?.optString("message").orEmpty()
                runCatching { continuation.resumeWithException(EngineError(text.ifEmpty { "操作失败" })) }
            }
            else -> {
                val continuation = pending.remove(id) ?: return
                progress.remove(id)
                runCatching { continuation.resume(message.optJSONObject("result") ?: JSONObject()) }
            }
        }
    }

    /** 通道失效时失败全部在途请求（同构 Swift failAll）。 */
    private fun failAll(message: String) {
        val entries = pending.entries.toList()
        pending.clear()
        progress.clear()
        for (entry in entries) {
            runCatching { entry.value.resumeWithException(EngineError(message)) }
        }
    }

    fun stop() {
        closed = true
        transport.onFrame = null
        transport.stop()
        failAll("引擎连接已关闭。")
    }

    companion object {
        /** 对齐 engine/src/rpc.rs `MAX_LINE_BYTES`。 */
        const val MAX_LINE_BYTES: Int = 1024 * 1024
    }
}
