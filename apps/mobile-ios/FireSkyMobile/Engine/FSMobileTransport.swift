import Foundation
import FSMobileKit

/// FFI 传输层：按 engine-mobile/include/fsmobile.h 的固定 C 契约对接进程内引擎。
/// apps/mobile-ios/Native/fsmobile.h 为该头文件的逐字镜像（2026-11-12 固定契约，
/// 引擎侧为契约源；接入真实引擎时链接 libengine_mobile.a 并删除 fsmobile_stub.c）。
///
/// 契约要点（与桌面 JSON-lines 语义逐帧一致）：
/// - 单实例服务：`fsm_start(workspace_root, definitions_path, cache_root, callback, userdata)`
///   返回 0 成功 / -1 失败；重复启动报错，先 fsm_stop 回收（幂等）。
/// - 帧回调 `(frame, userdata)`：UTF-8 单行 JSON、NUL 结尾、不含换行；
///   由引擎分配、回调返回后即失效，须在回调内复制。
/// - `fsm_send(line)`：不含换行的单行 JSON（容忍一个结尾换行），≤1 MiB。
/// - `fsm_cancel(request_id)`：0=已置位 / 1=无此进行中请求 / -1=未启动。
/// - `fsm_stop()`：幂等；不得在帧回调内调用。
/// - `fsm_last_error()`：线程局部，无错误时返回 NULL。
@MainActor
final class FSMobileTransport: EngineTransport {
    var onFrame: ((Data) -> Void)?
    private var started = false
    /// 生命周期内持有 C 回调闭包，供 fsm_start 注册后长期使用。
    private let frameCallback: fsm_frame_cb = fsmFrameTrampoline

    func start(workspace: String) throws {
        // 单实例契约：先回收既有实例（fsm_stop 幂等，未启动亦返回 0）。
        _ = fsm_stop()
        // definitions 注入（MM0 坑 10/12/13）：bundle resource 路径交引擎侧，
        // 由其先注入单数 ECCODES_DEFINITION_PATH 再初始化（v1.1.0 起
        // scripts/build-engine.sh 已把 brew definitions staging 进 Bundle）。
        // bundle 缺失该资源时传 NULL，契约语义「NULL 则不注入」。
        // cache_root 传 NULL → 引擎默认（由 workspace 语义推导）。
        let definitions = Self.bundledDefinitionsPath()
        // samples 契约不在 fsm_start 内（ffi 只注入 definitions）；ecCodes
        // 模板克隆/编码需要 samples，这里同样 bundle 优先注入，真机离线可用。
        if let samples = Self.bundledSamplesPath() {
            setenv("ECCODES_SAMPLES_PATH", samples, 1)
        }
        let user = Unmanaged.passUnretained(self).toOpaque()
        let result: Int32
        if let definitions {
            result = workspace.withCString { workspaceRoot in
                definitions.withCString { definitionsPath in
                    fsm_start(workspaceRoot, definitionsPath, nil, frameCallback, user)
                }
            }
        } else {
            result = workspace.withCString { workspaceRoot in
                fsm_start(workspaceRoot, nil, nil, frameCallback, user)
            }
        }
        guard result == 0 else {
            throw EngineError(message: "引擎启动失败：\(Self.lastErrorText())")
        }
        started = true
    }

    /// bundle 内 eccodes/definitions 资源目录；不存在或非目录返回 nil（不注入）。
    /// internal（非 private）：RealEngineChainTests 断言 bundle 优先路径（MM0 坑 12）。
    static func bundledDefinitionsPath() -> String? {
        bundledResourceDirectory("eccodes/definitions")
    }

    /// bundle 内 eccodes/samples 资源目录；不存在或非目录返回 nil。
    static func bundledSamplesPath() -> String? {
        bundledResourceDirectory("eccodes/samples")
    }

    private static func bundledResourceDirectory(_ relative: String) -> String? {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent(relative) else {
            return nil
        }
        var isDirectory: ObjCBool = false
        let path = url.path
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory),
              isDirectory.boolValue else { return nil }
        return path
    }

    func send(_ frame: Data) throws {
        guard started else {
            throw EngineError(message: "FFI 通道未建立，请重新连接引擎。")
        }
        // EngineClient 统一在帧尾追加 '\n'；契约的 fsm_send 接受不含换行的
        // 单行 JSON（容忍一个结尾换行），此处按契约剥离后下发。
        var line = frame
        if line.last == 10 { line.removeLast() }
        guard let text = String(data: line, encoding: .utf8), !text.contains("\n") else {
            throw EngineError(message: "引擎帧不是有效的 UTF-8 单行 JSON。")
        }
        let status = text.withCString { fsm_send($0) }
        guard status == 0 else {
            throw EngineError(message: "引擎发送失败：\(Self.lastErrorText())")
        }
    }

    func cancel(requestID: String) {
        guard started else { return }
        // 0=已置位；1=无此进行中请求（已完成或已取消）；-1=未启动。
        _ = requestID.withCString { fsm_cancel($0) }
    }

    func stop() {
        guard started else { return }
        started = false
        onFrame = nil
        _ = fsm_stop()
    }

    private static func lastErrorText() -> String {
        guard let pointer = fsm_last_error() else { return "未知错误" }
        return String(cString: pointer)
    }
}

/// C 回调蹦床：契约规定帧为 NUL 结尾单行 JSON（不含换行）、回调返回后失效，
/// 故先在回调内复制；帧回调可能来自工作线程，切回主线程后再触碰
/// MainActor 隔离的传输层。回调内绝不调用 fsm_stop。
private func fsmFrameTrampoline(_ frame: UnsafePointer<CChar>?, _ user: UnsafeMutableRawPointer?) {
    guard let frame, let user else { return }
    let data = Data(bytes: frame, count: strlen(frame))
    let transport = Unmanaged<FSMobileTransport>.fromOpaque(user).takeUnretainedValue()
    Task { @MainActor in
        transport.onFrame?(data)
    }
}
