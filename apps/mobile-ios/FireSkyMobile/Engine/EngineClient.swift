import Foundation
import Combine

typealias JSONObject = [String: Any]

struct EngineError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// JSON-lines 协议客户端。协议帧 / 握手 / 进度三链语义照抄桌面版
/// （apps/macos/Sources/FireSkyNative/EngineClient.swift），仅把子进程
/// stdin/stdout 传输替换为可注入的 EngineTransport（Mock / FFI）。
@MainActor
final class EngineClient: ObservableObject {
    @Published var connected = false
    private var transport: EngineTransport?
    private let transportFactory: () -> EngineTransport
    private var pending: [String: CheckedContinuation<JSONObject, Error>] = [:]
    private var progress: [String: (JSONObject) -> Void] = [:]
    private(set) var workspace: String = ""

    init(transportFactory: @escaping () -> EngineTransport = { MockTransport() }) {
        self.transportFactory = transportFactory
    }

    func start(workspace: String) throws {
        stop()
        let root = URL(fileURLWithPath: workspace)
        guard FileManager.default.fileExists(atPath: root.path) else {
            throw EngineError(message: "请选择有效的 FireSky 数据目录。")
        }
        let transport = transportFactory()
        transport.onFrame = { [weak self] data in
            Task { @MainActor in self?.receive(data) }
        }
        try transport.start(workspace: workspace)
        self.transport = transport
        self.workspace = workspace
    }

    func call(_ method: String, _ params: JSONObject = [:],
              progress onProgress: ((JSONObject) -> Void)? = nil) async throws -> JSONObject {
        try Task.checkCancellation()
        guard let transport else {
            throw EngineError(message: "引擎未连接，请检查设置。")
        }
        let id = UUID().uuidString
        let request: JSONObject = ["id": id, "method": method, "params": params]
        var data = try JSONSerialization.data(withJSONObject: request)
        data.append(10)
        guard data.count <= 1024 * 1024 else {
            throw EngineError(message: "请求过大，请缩小输入内容。")
        }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                pending[id] = continuation
                progress[id] = onProgress
                do { try transport.send(data) }
                catch {
                    pending.removeValue(forKey: id)?.resume(throwing: error)
                    progress.removeValue(forKey: id)
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.transport?.cancel(requestID: id)
            }
        }
    }

    private func receive(_ bytes: Data) {
        guard let object = try? JSONSerialization.jsonObject(with: bytes) as? JSONObject,
              let id = object["id"] as? String else { return }
        if object["type"] as? String == "progress" {
            progress[id]?(object["event"] as? JSONObject ?? [:])
            return
        }
        guard let continuation = pending.removeValue(forKey: id) else { return }
        progress.removeValue(forKey: id)
        if object["type"] as? String == "error" {
            let error = object["error"] as? JSONObject ?? [:]
            continuation.resume(throwing: EngineError(message: error["message"] as? String ?? "操作失败"))
        } else {
            continuation.resume(returning: object["result"] as? JSONObject ?? [:])
        }
    }

    private func failAll(_ message: String) {
        connected = false
        let continuations = pending.values
        pending.removeAll()
        progress.removeAll()
        for continuation in continuations {
            continuation.resume(throwing: EngineError(message: message))
        }
    }

    func stop() {
        let transport = self.transport
        self.transport = nil
        transport?.stop()
        failAll("引擎连接已关闭。")
    }
}
