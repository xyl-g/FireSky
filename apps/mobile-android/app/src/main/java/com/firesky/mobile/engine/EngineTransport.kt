package com.firesky.mobile.engine

import org.json.JSONObject

/**
 * 引擎传输层抽象 —— 对齐 docs/MOBILE_NATIVE_PLAN.md §1.1 的桥接五原语：
 * `start(config)` / `send(frame)` / `on_frame 回调` / `cancel(request_id)` / `stop`。
 *
 * desktop JSON-lines 服务（engine/src/rpc.rs::serve）承载于该抽象之后：
 * - MockTransport：进程内模拟引擎（当前默认，编译即可全链路演示）；
 * - JniTransport：engine-mobile FFI（MN2 接入 `libengine_mobile.so` 后启用）。
 * 协议帧与握手语义零改动（同构 EngineClient.swift 的「仅换传输层」决策）。
 */
interface EngineTransport {
    /** 建立双向帧通道。config 为 JSON 对象（workspace/缓存目录、definitions 路径等）。 */
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
