package com.firesky.mobile.engine

import org.json.JSONObject

/**
 * 引擎传输层抽象 —— 桥接五原语：
 * `start(config)` / `send(frame)` / `on_frame 回调` / `cancel(request_id)` / `stop`。
 *
 * desktop JSON-lines 服务（engine/src/rpc.rs::serve）承载于该抽象之后：
 * - RemoteHttpTransport：纯远程薄客户端默认通道（HTTP/HTTPS + SSE 实时流，回退自适应轮询）；
 * - MockTransport：进程内模拟引擎（仅用于离线单元测试与 Compose UI 测试）。
 */
interface EngineTransport {
    /** 建立双向帧通道。config 为 JSON 对象（远程服务器 base_url/token 等）。 */
    fun start(config: JSONObject)

    /** 写出一条请求帧。实现负责按 JSON-lines 契约补结尾换行（或等价帧边界）。 */
    fun send(frame: String)

    /** 取消在途请求（对应帧 `{"method":"cancel","params":{"request_id":…}}`）。 */
    fun cancel(requestId: String)

    /** 关闭通道并释放引擎资源；已挂起的请求由 EngineClient 统一失败。 */
    fun stop()

    /** 引擎下行帧回调（progress/result/error）。EngineClient.start 时装配。 */
    var onFrame: FrameListener?
}

fun interface FrameListener {
    fun onFrame(frame: String)
}
