import Foundation

@main
struct LocationClientProbe {
    @MainActor
    static func main() async {
        let args = CommandLine.arguments
        let client = EngineClient()
        do {
            if args[3] == "cancel" {
                try client.start(workspace: args[1], python: args[2])
                _ = try await client.call("hello")
                let operation = Task {
                    _ = try await client.call("metadata", ["lat": 51.0, "lon": -1.0])
                    try Task.checkCancellation()
                }
                let marker = ProcessInfo.processInfo.environment["FIRESKY_PROBE_CANCEL_MARKER"]!
                while !FileManager.default.fileExists(atPath: marker) {
                    try await Task.sleep(nanoseconds: 10_000_000)
                }
                operation.cancel()
                print("{\"cancel_sent\":true}")
                fflush(stdout)
                var cancelled = false
                do { try await operation.value }
                catch { cancelled = operation.isCancelled }
                guard cancelled else {
                    throw EngineError(message: "cancelled task returned success")
                }
                _ = try await client.call("probe")
                client.stop()
                print("{\"passed\":true}")
                return
            }
            var output: JSONObject = ["passed": true]
            for _ in 0..<(args[3] == "restart" ? 2 : 1) {
                output.removeValue(forKey: "search")
                try client.start(workspace: args[1], python: args[2])
                _ = try await client.call("hello")
                if args[3] == "oversized" {
                    var rejected = false
                    do {
                        _ = try await client.call("search", ["query": String(repeating: "x", count: 1024 * 1024)])
                    } catch {
                        rejected = error.localizedDescription.contains("请求过大")
                    }
                    guard rejected else {
                        throw EngineError(message: "oversized request was not rejected locally")
                    }
                }
                do {
                    output["search"] = try await client.call("search", ["query": "City"])
                    guard args[3] != "failure" else {
                        throw EngineError(message: "failed native request unexpectedly succeeded")
                    }
                    output["metadata"] = try await client.call("metadata", ["lat": 51.23445, "lon": -0.12345])
                    if args[3] == "inventory" || args[3] == "inventoryfailure" {
                        output["inventory"] = try await client.call("inventory")
                        guard args[3] != "inventoryfailure" else {
                            throw EngineError(message: "invalid inventory unexpectedly succeeded")
                        }
                    }
                } catch {
                    guard (args[3] == "failure" && output["search"] == nil)
                        || (args[3] == "inventoryfailure" && output["inventory"] == nil) else { throw error }
                    output["nativeFailure"] = error.localizedDescription
                }
                _ = try await client.call("probe")
                client.stop()
            }
            print(String(data: try JSONSerialization.data(withJSONObject: output), encoding: .utf8)!)
        } catch {
            client.stop()
            print(String(data: try! JSONSerialization.data(withJSONObject: [
                "passed": false, "error": error.localizedDescription]), encoding: .utf8)!)
            exit(1)
        }
    }
}
