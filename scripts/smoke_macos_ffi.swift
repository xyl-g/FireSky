import Darwin
import Foundation

private typealias EngineHandle = OpaquePointer
private typealias ABIVersion = @convention(c) () -> UInt32
private typealias EngineOpen = @convention(c) (
    UnsafePointer<CChar>?,
    UnsafeMutablePointer<EngineHandle?>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32
private typealias EngineFree = @convention(c) (EngineHandle?) -> Void
private typealias EngineCall = @convention(c) (
    EngineHandle?,
    UnsafePointer<CChar>?,
    UnsafePointer<CChar>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?,
    UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32
private typealias StringFree = @convention(c) (UnsafeMutablePointer<CChar>?) -> Void

@main
private enum Smoke {
    static func main() {
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            guard args.count == 2 else {
                throw SmokeError.usage
            }
            try run(libraryPath: args[0], workspace: args[1])
            print("macOS Swift FFI smoke: ok")
        } catch {
            fputs("macOS Swift FFI smoke: \(error)\n", stderr)
            exit(1)
        }
    }

    private static func run(libraryPath: String, workspace: String) throws {
        guard let library = dlopen(libraryPath, RTLD_NOW | RTLD_LOCAL) else {
            throw SmokeError.message(dlerror().map { String(cString: $0) } ?? "dlopen failed")
        }
        defer { dlclose(library) }

        let version: ABIVersion = try symbol(library, "firesky_ffi_abi_version")
        let open: EngineOpen = try symbol(library, "firesky_engine_open")
        let free: EngineFree = try symbol(library, "firesky_engine_free")
        let call: EngineCall = try symbol(library, "firesky_engine_call")
        let stringFree: StringFree = try symbol(library, "firesky_ffi_string_free")
        guard version() == 1 else {
            throw SmokeError.message("unsupported FFI ABI version")
        }

        var handle: EngineHandle?
        var openError: UnsafeMutablePointer<CChar>?
        let openCode = workspace.withCString { pointer in
            open(pointer, &handle, &openError)
        }
        if let openError {
            defer { stringFree(openError) }
            if openCode != 0 {
                throw SmokeError.message(String(cString: openError))
            }
        }
        guard openCode == 0, let handle else {
            throw SmokeError.message("firesky_engine_open returned no handle")
        }
        defer { free(handle) }

        var response: UnsafeMutablePointer<CChar>?
        var callError: UnsafeMutablePointer<CChar>?
        let hello = "hello"
        let code = hello.withCString { method in
            call(handle, method, nil, &response, &callError)
        }
        if let callError {
            defer { stringFree(callError) }
            if code != 0 {
                throw SmokeError.message(String(cString: callError))
            }
        }
        guard code == 0, let response else {
            throw SmokeError.message("firesky_engine_call returned no response")
        }
        defer { stringFree(response) }
        let object = try JSONSerialization.jsonObject(
            with: Data(String(cString: response).utf8)
        ) as? [String: Any]
        guard object?["contract"] as? String == "firesky-ffi-call-v1",
              object?["abi_version"] as? Int == 1,
              object?["ok"] as? Bool == true,
              (object?["result"] as? [String: Any])?["engine"] as? String == "firesky-rust" else {
            throw SmokeError.message("invalid hello response envelope")
        }
    }

    private static func symbol<T>(_ handle: UnsafeMutableRawPointer, _ name: String) throws -> T {
        guard let pointer = dlsym(handle, name) else {
            throw SmokeError.message("missing FFI symbol \(name)")
        }
        return unsafeBitCast(pointer, to: T.self)
    }
}

private enum SmokeError: Error, CustomStringConvertible {
    case usage
    case message(String)

    var description: String {
        switch self {
        case .usage: "usage: smoke_macos_ffi.swift LIBRARY WORKSPACE"
        case .message(let value): value
        }
    }
}
