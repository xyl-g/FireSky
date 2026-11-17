import Foundation

/// 引擎传输层抽象——desktop JSON-lines 协议的帧通道。
///
/// 桌面版以子进程 stdin/stdout 承载同一协议；移动端把服务搬进进程内
/// （engine-mobile crate，内存 duplex），协议帧零改动：
/// 请求 / 响应 / 进度帧均为 UTF-8 单行 JSON + '\n'。
/// 实现方：MockTransport（无引擎库时驱动 UI）、FSMobileTransport（fsm_* C 契约）。
@MainActor
protocol EngineTransport: AnyObject {
    /// 收到一帧（一条完整 JSON 行，不含结尾换行）。
    var onFrame: ((Data) -> Void)? { get set }

    /// 建立通道。实现方自行完成服务初始化（fsm_start、ecCodes definitions 注入等）。
    func start(workspace: String) throws

    /// 发送一帧请求：`frame` 为 UTF-8 单行 JSON（含结尾 '\n'）。
    func send(_ frame: Data) throws

    /// 取消在途请求（对齐桌面协议 method=cancel；FFI 实现映射 fsm_cancel）。
    func cancel(requestID: String)

    /// 关闭并释放通道。
    func stop()
}
