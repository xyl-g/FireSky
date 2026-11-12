import Foundation
import Darwin

private typealias FFIEngineHandle = OpaquePointer
private typealias FFITaskHandle = OpaquePointer
private typealias FFIProgressCallback = @convention(c) (UnsafePointer<CChar>?, UnsafeMutableRawPointer?) -> Void

private typealias FFIVersion = @convention(c) () -> UInt32
private typealias FFIEngineOpen = @convention(c) (
    UnsafePointer<CChar>?,
    UnsafeMutablePointer<FFIEngineHandle?>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32
private typealias FFIEngineFree = @convention(c) (FFIEngineHandle?) -> Void
private typealias FFIEngineCall = @convention(c) (
    FFIEngineHandle?,
    UnsafePointer<CChar>?,
    UnsafePointer<CChar>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32
private typealias FFIEngineStart = @convention(c) (
    FFIEngineHandle?,
    UnsafePointer<CChar>?,
    UnsafePointer<CChar>?,
    FFIProgressCallback?,
    UnsafeMutableRawPointer?,
    UnsafeMutablePointer<FFITaskHandle?>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32
private typealias FFITaskCancel = @convention(c) (
    FFITaskHandle?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32
private typealias FFITaskJoin = @convention(c) (
    FFITaskHandle?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32
private typealias FFITaskFree = @convention(c) (FFITaskHandle?) -> Void
private typealias FFIStringFree = @convention(c) (UnsafeMutablePointer<CChar>?) -> Void

private final class FFIRequestBox: @unchecked Sendable {
    weak var owner: FFIEngine?
    let progress: ((JSONObject) -> Void)?
    let lock = NSLock()
    var task: FFITaskHandle?
    var continuation: CheckedContinuation<JSONObject, Error>?
    var cancelled = false
    var completed = false
    var taskReleased = false
    var retainedReleased = false

    init(owner: FFIEngine, progress: ((JSONObject) -> Void)?) {
        self.owner = owner
        self.progress = progress
    }
}

private func loadFFISymbol<T>(_ handle: UnsafeMutableRawPointer, _ name: String, as type: T.Type) throws -> T {
    guard let symbol = dlsym(handle, name) else {
        throw EngineError(message: "Rust FFI 缺少符号 \(name)。")
    }
    return unsafeBitCast(symbol, to: type)
}

private let fireskyFFIProgressCallback: FFIProgressCallback = { event, userData in
    guard let event, let userData else { return }
    let text = String(cString: event)
    let box = Unmanaged<FFIRequestBox>.fromOpaque(userData).takeUnretainedValue()
    Task { @MainActor in
        box.owner?.receiveProgress(box, text: text)
    }
}

/// Dynamic C-ABI client. The library is loaded at runtime so the Swift package
/// remains buildable in development without a bundled engine.
final class FFIEngine: @unchecked Sendable {
    private let library: UnsafeMutableRawPointer
    private let version: FFIVersion
    private let open: FFIEngineOpen
    private let free: FFIEngineFree
    private let callFunction: FFIEngineCall
    private let start: FFIEngineStart
    private let cancel: FFITaskCancel
    private let join: FFITaskJoin
    private let taskFree: FFITaskFree
    private let stringFree: FFIStringFree
    private var runtime: FFIEngineHandle?
    private let activeLock = NSLock()
    private var active: [ObjectIdentifier: FFIRequestBox] = [:]
    private let activeGroup = DispatchGroup()
    private var stopping = false

    init(libraryPath: String, workspace: String) throws {
        guard let library = dlopen(libraryPath, RTLD_NOW | RTLD_LOCAL) else {
            let message = dlerror().map { String(cString: $0) } ?? "无法加载 Rust FFI 动态库。"
            throw EngineError(message: "无法加载 Rust FFI：\(message)")
        }
        self.library = library
        do {
            version = try loadFFISymbol(library, "firesky_ffi_abi_version", as: FFIVersion.self)
            open = try loadFFISymbol(library, "firesky_engine_open", as: FFIEngineOpen.self)
            free = try loadFFISymbol(library, "firesky_engine_free", as: FFIEngineFree.self)
            callFunction = try loadFFISymbol(library, "firesky_engine_call", as: FFIEngineCall.self)
            start = try loadFFISymbol(library, "firesky_engine_start", as: FFIEngineStart.self)
            cancel = try loadFFISymbol(library, "firesky_task_cancel", as: FFITaskCancel.self)
            join = try loadFFISymbol(library, "firesky_task_join", as: FFITaskJoin.self)
            taskFree = try loadFFISymbol(library, "firesky_task_free", as: FFITaskFree.self)
            stringFree = try loadFFISymbol(library, "firesky_ffi_string_free", as: FFIStringFree.self)
            guard version() == 1 else {
                throw EngineError(message: "Rust FFI ABI 版本不匹配。")
            }
            var handle: FFIEngineHandle?
            var error: UnsafeMutablePointer<CChar>?
            let code = workspace.withCString { path in
                open(path, &handle, &error)
            }
            if code != 0 || handle == nil {
                let message = copyAndFree(error, using: stringFree) ?? "无法创建 Rust 运行时。"
                throw EngineError(message: message)
            }
            runtime = handle
        } catch {
            dlclose(library)
            throw error
        }
    }

    deinit {
        stop()
        dlclose(library)
    }

    func call(_ method: String, _ params: JSONObject = [:],
              progress onProgress: ((JSONObject) -> Void)? = nil) async throws -> JSONObject {
        let paramsData = try JSONSerialization.data(withJSONObject: params)
        guard let paramsText = String(data: paramsData, encoding: .utf8) else {
            throw EngineError(message: "请求参数无法编码为 UTF-8。")
        }
        // Keep even short calls off the UI actor. Inventory and location
        // queries can touch the filesystem or geospatial resources, and the
        // task path also gives stop() one lifecycle gate for every FFI call.
        let box = FFIRequestBox(owner: self, progress: onProgress)
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                box.continuation = continuation
                guard register(box) else {
                    continuation.resume(throwing: EngineError(message: "Rust FFI 运行时正在关闭。"))
                    return
                }
                do {
                    let task = try startTask(method: method, params: paramsText, box: box)
                    box.lock.lock()
                    let wasCompleted = box.completed
                    if !wasCompleted {
                        box.task = task
                    }
                    let wasCancelled = box.cancelled
                    box.lock.unlock()
                    if wasCompleted {
                        releaseTaskOnce(box, task)
                        unregister(box)
                        releaseRetainedBox(box)
                        return
                    }
                    if wasCancelled {
                        cancelTask(box)
                    }
                    let taskAddress = UInt(bitPattern: task)
                    DispatchQueue.global(qos: .userInitiated).async { [weak self, weak box] in
                        guard let self, let box else { return }
                        guard let task = OpaquePointer(bitPattern: taskAddress) else { return }
                        let result = self.joinTask(task)
                        self.finish(box, task: task, result: result)
                    }
                } catch {
                    unregister(box)
                    continuation.resume(throwing: error)
                }
            }
        }, onCancel: { [weak self, weak box] in
            guard let self, let box else { return }
            box.lock.lock()
            box.cancelled = true
            box.lock.unlock()
            self.cancelTask(box)
        })
    }

    private func callSync(_ method: String, _ params: String) throws -> JSONObject {
        guard let runtime else { throw EngineError(message: "Rust FFI 运行时未连接。") }
        var response: UnsafeMutablePointer<CChar>?
        var error: UnsafeMutablePointer<CChar>?
        let code = method.withCString { methodPtr in
            params.withCString { paramsPtr in
                callFunction(runtime, methodPtr, paramsPtr, &response, &error)
            }
        }
        let errorText = copyAndFree(error, using: stringFree)
        if code != 0 {
            throw EngineError(message: takeEnvelopeError(errorText) ?? "Rust FFI 调用失败。")
        }
        guard let response = copyAndFree(response, using: stringFree),
              let object = try JSONSerialization.jsonObject(with: Data(response.utf8)) as? JSONObject,
              object["ok"] as? Bool == true else {
            throw EngineError(message: "Rust FFI 返回了无效结果。")
        }
        return object["result"] as? JSONObject ?? [:]
    }

    private func startTask(method: String, params: String, box: FFIRequestBox) throws -> FFITaskHandle {
        guard let runtime else { throw EngineError(message: "Rust FFI 运行时未连接。") }
        var task: FFITaskHandle?
        var error: UnsafeMutablePointer<CChar>?
        let userData = Unmanaged.passRetained(box).toOpaque()
        let code = method.withCString { methodPtr in
            params.withCString { paramsPtr in
                start(runtime, methodPtr, paramsPtr, fireskyFFIProgressCallback, userData, &task, &error)
            }
        }
        guard code == 0, let task else {
            Unmanaged<FFIRequestBox>.fromOpaque(userData).release()
            let message = copyAndFree(error, using: stringFree)
            throw EngineError(message: takeEnvelopeError(message) ?? "无法启动 Rust FFI 任务。")
        }
        return task
    }

    private func joinTask(_ task: FFITaskHandle) -> (Int32, String?, String?) {
        var response: UnsafeMutablePointer<CChar>?
        var error: UnsafeMutablePointer<CChar>?
        let code = join(task, &response, &error)
        let responseText = copyAndFree(response, using: stringFree)
        let errorText = copyAndFree(error, using: stringFree)
        return (code, responseText, errorText)
    }

    private func finish(
        _ box: FFIRequestBox,
        task: FFITaskHandle,
        result: (Int32, String?, String?)
    ) {
        box.lock.lock()
        guard !box.completed else {
            box.lock.unlock()
            releaseTaskOnce(box, task)
            return
        }
        box.completed = true
        let continuation = box.continuation
        box.continuation = nil
        box.lock.unlock()

        // The worker that called join owns task cleanup. This keeps stop()
        // from racing task_join/task_free or unloading the dylib.
        releaseTaskOnce(box, task)
        unregister(box)
        releaseRetainedBox(box)

        let (code, responseText, errorText) = result
        if code != 0 {
            continuation?.resume(throwing: EngineError(message: takeEnvelopeError(errorText) ?? "Rust FFI 任务失败。"))
            return
        }
        guard let responseText,
              let object = try? JSONSerialization.jsonObject(with: Data(responseText.utf8)) as? JSONObject,
              object["ok"] as? Bool == true,
              let result = object["result"] as? JSONObject else {
            continuation?.resume(throwing: EngineError(message: "Rust FFI 返回了无效结果。"))
            return
        }
        continuation?.resume(returning: result)
    }

    @MainActor
    fileprivate func receiveProgress(_ box: FFIRequestBox, text: String) {
        guard let data = text.data(using: .utf8),
              let envelope = try? JSONSerialization.jsonObject(with: data) as? JSONObject,
              let event = envelope["event"] as? JSONObject else { return }
        box.progress?(event)
    }

    private func register(_ box: FFIRequestBox) -> Bool {
        activeLock.lock()
        guard !stopping else {
            activeLock.unlock()
            return false
        }
        active[ObjectIdentifier(box)] = box
        activeGroup.enter()
        activeLock.unlock()
        return true
    }

    private func unregister(_ box: FFIRequestBox) {
        activeLock.lock()
        let removed = active.removeValue(forKey: ObjectIdentifier(box)) != nil
        activeLock.unlock()
        if removed {
            activeGroup.leave()
        }
    }

    private func releaseTaskOnce(_ box: FFIRequestBox, _ task: FFITaskHandle) {
        box.lock.lock()
        guard !box.taskReleased else {
            box.lock.unlock()
            return
        }
        box.taskReleased = true
        box.lock.unlock()
        taskFree(task)
    }

    private func releaseRetainedBox(_ box: FFIRequestBox) {
        box.lock.lock()
        guard !box.retainedReleased else {
            box.lock.unlock()
            return
        }
        box.retainedReleased = true
        box.lock.unlock()
        Unmanaged<FFIRequestBox>.fromOpaque(Unmanaged.passUnretained(box).toOpaque()).release()
    }

    private func cancelTask(_ box: FFIRequestBox) {
        box.lock.lock()
        guard let task = box.task, !box.completed, !box.taskReleased else {
            box.lock.unlock()
            return
        }
        var error: UnsafeMutablePointer<CChar>?
        _ = cancel(task, &error)
        box.lock.unlock()
        _ = copyAndFree(error, using: stringFree)
    }

    func stop() {
        activeLock.lock()
        let boxes = Array(active.values)
        stopping = true
        activeLock.unlock()
        for box in boxes {
            box.lock.lock()
            box.cancelled = true
            box.lock.unlock()
            cancelTask(box)
        }
        // joinTask/finish run off the main actor, so waiting here cannot
        // deadlock the UI callback queue. Do not unload the library until
        // every worker has joined and released its task handle.
        activeGroup.wait()
        if let runtime {
            free(runtime)
            self.runtime = nil
        }
    }
}

private func copyAndFree(
    _ pointer: UnsafeMutablePointer<CChar>?,
    using free: FFIStringFree
) -> String? {
    guard let pointer else { return nil }
    let value = String(cString: pointer)
    free(pointer)
    return value
}

private func takeEnvelopeError(_ text: String?) -> String? {
    guard let text else { return nil }
    return text.data(using: .utf8)
        .flatMap { try? JSONSerialization.jsonObject(with: $0) as? JSONObject }
        .flatMap { ($0["error"] as? JSONObject)?["message"] as? String }
        ?? text
}
