using System.Collections.Concurrent;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json.Nodes;

namespace FireSky;

/// <summary>
/// Windows client for the Rust desktop runtime.
///
/// The packaged application loads the versioned C ABI directly. JSONL is kept
/// behind an explicit FIRESKY_NATIVE_BACKEND=jsonl switch for diagnostics only;
/// a missing FFI library is never silently replaced in a release install.
/// </summary>
public sealed class EngineClient : IDisposable
{
    private FfiEngine? ffi;
    private JsonlEngine? jsonl;

    public void Start(string workspace, string engineBinary = "")
    {
        Dispose();
        if (!Directory.Exists(workspace))
            throw new InvalidOperationException("请选择有效的 FireSky 数据目录。");

        var backend = Environment.GetEnvironmentVariable("FIRESKY_NATIVE_BACKEND")
            ?.Trim().ToLowerInvariant() ?? "auto";
        var resourceRoot = Path.Combine(AppContext.BaseDirectory, "Resources");
        var appDataRoot = Path.Combine(resourceRoot, "app-data");
        var manifestPath = Path.Combine(appDataRoot, "RESOURCE-MANIFEST.json");
        var packagedFfi = new[]
        {
            Path.Combine(resourceRoot, "firesky_engine.dll"),
            Path.Combine(resourceRoot, "libfiresky_engine.dll"),
        }.Any(File.Exists);
        var packagedInstall = packagedFfi
            || Directory.Exists(appDataRoot)
            || File.Exists(manifestPath)
            || AppContext.BaseDirectory.Contains(
                $"{Path.DirectorySeparatorChar}publish{Path.DirectorySeparatorChar}",
                StringComparison.OrdinalIgnoreCase)
            || Environment.GetEnvironmentVariable("FIRESKY_REQUIRE_APP_RESOURCES") is "1" or "true" or "yes";
        if (packagedInstall)
        {
            if (!Directory.Exists(appDataRoot) || !File.Exists(manifestPath))
                throw new InvalidOperationException(
                    "FireSky 安装包缺少应用资源清单，请重新安装。");
            if (backend == "jsonl")
                throw new InvalidOperationException(
                    "正式 FireSky 安装包不允许使用 JSONL 后端。");
        }
        var explicitLibrary = Environment.GetEnvironmentVariable("FIRESKY_ENGINE_FFI_LIBRARY");
        if (string.IsNullOrWhiteSpace(explicitLibrary) &&
            Path.GetExtension(engineBinary).Equals(".dll", StringComparison.OrdinalIgnoreCase))
            explicitLibrary = engineBinary;
        var rustTarget = RuntimeInformation.ProcessArchitecture switch
        {
            Architecture.Arm64 => "aarch64-pc-windows-msvc",
            Architecture.X64 => "x86_64-pc-windows-msvc",
            _ => "",
        };
        var candidates = new[]
        {
            explicitLibrary,
            Path.Combine(AppContext.BaseDirectory, "Resources", "firesky_engine.dll"),
            Path.Combine(AppContext.BaseDirectory, "Resources", "libfiresky_engine.dll"),
            Path.Combine(AppContext.BaseDirectory, "firesky_engine.dll"),
            Path.Combine(AppContext.BaseDirectory, "libfiresky_engine.dll"),
            Path.Combine(workspace, "engine", "target", "release", "firesky_engine.dll"),
            string.IsNullOrEmpty(rustTarget) ? null : Path.Combine(
                workspace, "engine", "target", rustTarget, "release", "firesky_engine.dll"),
        }.Where(path => !string.IsNullOrWhiteSpace(path))
         .Select(path => Path.GetFullPath(path!))
         .Distinct(StringComparer.OrdinalIgnoreCase)
         .ToArray();

        var library = candidates.FirstOrDefault(File.Exists);
        if (library is not null)
        {
            try
            {
                ConfigureFfiEnvironment(workspace, library, packagedInstall);
                ffi = new FfiEngine(library, workspace);
                return;
            }
            catch
            {
                Dispose();
                if (backend == "ffi" || packagedInstall)
                    throw;
            }
        }
        else if (backend == "ffi" || packagedInstall)
        {
            throw new InvalidOperationException(
                "未找到 Rust FFI 动态库，请安装包含 Resources\\firesky_engine.dll 的 FireSky。");
        }

        if (backend != "jsonl")
        {
            throw new InvalidOperationException(
                "Rust FFI 动态库不可用。开发诊断可显式设置 FIRESKY_NATIVE_BACKEND=jsonl。");
        }

        var explicitBinary = Environment.GetEnvironmentVariable("FIRESKY_RUST_ENGINE_BIN");
        if (string.IsNullOrWhiteSpace(explicitBinary)) explicitBinary = engineBinary;
        if (!string.IsNullOrWhiteSpace(explicitBinary) && !File.Exists(explicitBinary))
            throw new InvalidOperationException("指定的 Rust 引擎不存在，请检查引擎路径。");
        var binaries = new[]
        {
            explicitBinary,
            Path.Combine(AppContext.BaseDirectory, "Resources", "firesky-engine.exe"),
            Path.Combine(AppContext.BaseDirectory, "firesky-engine.exe"),
            Path.Combine(workspace, "engine", "target", "release", "firesky-engine.exe"),
            string.IsNullOrEmpty(rustTarget) ? null : Path.Combine(
                workspace, "engine", "target", rustTarget, "release", "firesky-engine.exe"),
        }.Where(path => !string.IsNullOrWhiteSpace(path))
         .Select(path => Path.GetFullPath(path!))
         .ToArray();
        var binary = binaries.FirstOrDefault(File.Exists)
            ?? throw new InvalidOperationException("Rust 引擎不可用，请选择 firesky-engine.exe。");
        jsonl = new JsonlEngine();
        try
        {
            jsonl.Start(workspace, binary);
        }
        catch
        {
            jsonl.Dispose();
            jsonl = null;
            throw;
        }
    }

    private static void ConfigureFfiEnvironment(string workspace, string libraryPath,
        bool requireAppResources)
    {
        Environment.SetEnvironmentVariable("FIRESKY_WORKSPACE", workspace);
        var resources = Path.Combine(AppContext.BaseDirectory, "Resources");
        var appData = Path.Combine(resources, "app-data");
        var manifest = Path.Combine(appData, "RESOURCE-MANIFEST.json");
        if (Directory.Exists(appData) && File.Exists(manifest))
        {
            Environment.SetEnvironmentVariable("FIRESKY_RESOURCE_DIR", appData);
            Environment.SetEnvironmentVariable("FIRESKY_REQUIRE_APP_RESOURCES", "1");
        }
        else
        {
            if (requireAppResources)
                throw new InvalidOperationException(
                    "FireSky 安装包缺少 Resources\\app-data\\RESOURCE-MANIFEST.json。");
            // A developer may provide an external, verified resource root.
            // Never erase that explicit environment configuration merely
            // because the installed-app layout is not being used.
        }

        var ecCodesRoot = Path.Combine(resources, "eccodes");
        var ecCodesDll = new[]
        {
            Path.Combine(ecCodesRoot, "bin", "eccodes.dll"),
            Path.Combine(ecCodesRoot, "eccodes.dll"),
        }.FirstOrDefault(File.Exists);
        if (ecCodesDll is not null)
        {
            if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("FIRESKY_ECCODES_LIBRARY")))
                Environment.SetEnvironmentVariable("FIRESKY_ECCODES_LIBRARY", ecCodesDll);
            var definitions = new[]
            {
                Path.Combine(ecCodesRoot, "share", "eccodes", "definitions"),
                Path.Combine(ecCodesRoot, "definitions"),
            }.FirstOrDefault(Directory.Exists);
            var samples = new[]
            {
                Path.Combine(ecCodesRoot, "share", "eccodes", "samples"),
                Path.Combine(ecCodesRoot, "samples"),
            }.FirstOrDefault(Directory.Exists);
            if (definitions is not null &&
                string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("ECCODES_DEFINITION_PATH")))
                Environment.SetEnvironmentVariable("ECCODES_DEFINITION_PATH", definitions);
            if (samples is not null &&
                string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("ECCODES_SAMPLES_PATH")))
                Environment.SetEnvironmentVariable("ECCODES_SAMPLES_PATH", samples);
            PrependPath(Path.GetDirectoryName(ecCodesDll)!);
        }
        PrependPath(Path.GetDirectoryName(libraryPath)!);
        AddDllDirectory(Path.GetDirectoryName(libraryPath)!);
    }

    private static void PrependPath(string directory)
    {
        var path = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        var pieces = path.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries);
        if (pieces.Any(item => string.Equals(item, directory, StringComparison.OrdinalIgnoreCase))) return;
        Environment.SetEnvironmentVariable("PATH", directory + Path.PathSeparator + path);
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr AddDllDirectory(string newDirectory);

    public Task<JsonObject> Call(string method, JsonObject? parameters = null,
        Action<JsonObject>? progress = null, CancellationToken cancellation = default)
    {
        if (ffi is not null)
            return ffi.Call(method, parameters, progress, cancellation);
        if (jsonl is not null)
            return jsonl.Call(method, parameters, progress, cancellation);
        throw new InvalidOperationException("引擎未连接，请在设置中重新连接。");
    }

    public void Dispose()
    {
        var currentFfi = ffi;
        ffi = null;
        currentFfi?.Dispose();
        var currentJsonl = jsonl;
        jsonl = null;
        currentJsonl?.Dispose();
    }

    private sealed class FfiEngine : IDisposable
    {
        private const uint AbiVersion = 1;
        private const int FfiOk = 0;
        private const int FfiCancelled = 5;
        private const int MaxMethodBytes = 128;
        private const int MaxJsonBytes = 8 * 1024 * 1024;

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate uint AbiVersionFn();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int EngineOpenFn(IntPtr workspace, out IntPtr handle, out IntPtr error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate void EngineFreeFn(IntPtr handle);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int EngineCallFn(IntPtr handle, IntPtr method, IntPtr parameters,
            out IntPtr response, out IntPtr error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int EngineStartFn(IntPtr handle, IntPtr method, IntPtr parameters,
            ProgressFn? callback, IntPtr userData, out IntPtr task, out IntPtr error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int TaskCancelFn(IntPtr task, out IntPtr error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int TaskJoinFn(IntPtr task, out IntPtr response, out IntPtr error);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate void TaskFreeFn(IntPtr task);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate void StringFreeFn(IntPtr value);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate void ProgressFn(IntPtr eventJson, IntPtr userData);
        // Rust invokes this after firesky_engine_start returns; keep the
        // delegate rooted for the lifetime of the process.
        private static readonly ProgressFn ProgressCallback = ProgressThunk;

        private readonly IntPtr library;
        private readonly AbiVersionFn abiVersion;
        private readonly EngineOpenFn open;
        private readonly EngineFreeFn free;
        private readonly EngineCallFn callFunction;
        private readonly EngineStartFn start;
        private readonly TaskCancelFn cancel;
        private readonly TaskJoinFn join;
        private readonly TaskFreeFn taskFree;
        private readonly StringFreeFn stringFree;
        private readonly object stateLock = new();
        private readonly Dictionary<FfiRequest, FfiRequest> active = new();
        private IntPtr runtime;
        private bool stopping;
        private bool disposed;

        internal FfiEngine(string libraryPath, string workspace)
        {
            library = NativeLibrary.Load(libraryPath);
            try
            {
                abiVersion = Export<AbiVersionFn>("firesky_ffi_abi_version");
                open = Export<EngineOpenFn>("firesky_engine_open");
                free = Export<EngineFreeFn>("firesky_engine_free");
                callFunction = Export<EngineCallFn>("firesky_engine_call");
                start = Export<EngineStartFn>("firesky_engine_start");
                cancel = Export<TaskCancelFn>("firesky_task_cancel");
                join = Export<TaskJoinFn>("firesky_task_join");
                taskFree = Export<TaskFreeFn>("firesky_task_free");
                stringFree = Export<StringFreeFn>("firesky_ffi_string_free");
                if (abiVersion() != AbiVersion)
                    throw new InvalidOperationException("Rust FFI ABI 版本不匹配。");

                using var workspaceText = new Utf8(workspace);
                var code = open(workspaceText.Pointer, out runtime, out var error);
                var message = TakeAndFree(error);
                if (code != FfiOk || runtime == IntPtr.Zero)
                    throw new InvalidOperationException(message ?? "无法创建 Rust 运行时。");
            }
            catch
            {
                if (runtime != IntPtr.Zero)
                {
                    free?.Invoke(runtime);
                    runtime = IntPtr.Zero;
                }
                NativeLibrary.Free(library);
                throw;
            }
        }

        private T Export<T>(string name) where T : Delegate
        {
            var address = NativeLibrary.GetExport(library, name);
            return Marshal.GetDelegateForFunctionPointer<T>(address);
        }

        public Task<JsonObject> Call(string method, JsonObject? parameters,
            Action<JsonObject>? onProgress, CancellationToken cancellation)
        {
            cancellation.ThrowIfCancellationRequested();
            var payload = parameters?.ToJsonString() ?? "{}";
            if (Encoding.UTF8.GetByteCount(method) > MaxMethodBytes)
                throw new InvalidOperationException("方法名过长。");
            if (Encoding.UTF8.GetByteCount(payload) > MaxJsonBytes)
                throw new InvalidOperationException("请求过大，请缩小输入内容。");
            // Keep every Rust operation on the worker path. Even inventory,
            // metadata and credential reads can touch disk or geospatial
            // resources; never block the WinUI dispatcher with FFI work.
            return CallAsync(method, payload, onProgress, cancellation);
        }

        private async Task<JsonObject> CallAsync(string method, string payload,
            Action<JsonObject>? onProgress, CancellationToken cancellation)
        {
            var request = new FfiRequest(this, onProgress);
            request.CancellationRegistration = cancellation.Register(
                static state => ((FfiRequest)state!).Owner.RequestCancel((FfiRequest)state!),
                request);
            var started = false;
            try
            {
                lock (stateLock)
                {
                    EnsureRuntime();
                    if (stopping) throw new InvalidOperationException("Rust FFI 运行时正在关闭。");
                    request.Handle = GCHandle.Alloc(request);
                    using var methodText = new Utf8(method);
                    using var paramsText = new Utf8(payload);
                    var code = start(runtime, methodText.Pointer, paramsText.Pointer,
                        ProgressCallback, GCHandle.ToIntPtr(request.Handle), out var task, out var error);
                    var message = TakeAndFree(error);
                    if (code != FfiOk || task == IntPtr.Zero)
                        throw new InvalidOperationException(ParseError(message) ?? "无法启动 Rust FFI 任务。");
                    request.SetTask(task);
                    active.Add(request, request);
                }
                request.StartJoin();
                started = true;
                return await request.Completion.Task.WaitAsync(cancellation).ConfigureAwait(false);
            }
            catch
            {
                if (!started)
                {
                    request.ReleaseHandle();
                    request.CancellationRegistration.Dispose();
                }
                throw;
            }
        }

        private void EnsureRuntime()
        {
            if (disposed || runtime == IntPtr.Zero)
                throw new InvalidOperationException("Rust FFI 运行时未连接。");
        }

        private void RequestCancel(FfiRequest request)
        {
            IntPtr task;
            lock (request.Gate)
            {
                request.CancelRequested = true;
                task = request.Task;
                if (task == IntPtr.Zero || request.TaskReleased) return;
                var code = cancel(task, out var error);
                _ = TakeAndFree(error);
                if (code != FfiOk && code != FfiCancelled) return;
            }
        }

        private void JoinRequest(FfiRequest request)
        {
            IntPtr task;
            lock (request.Gate) task = request.Task;
            try
            {
                var code = join(task, out var response, out var error);
                var responseText = TakeAndFree(response);
                var errorText = TakeAndFree(error);
                if (code == FfiCancelled)
                {
                    request.Completion.TrySetCanceled();
                }
                else if (code != FfiOk)
                {
                    request.Completion.TrySetException(
                        new InvalidOperationException(ParseError(errorText) ?? "Rust FFI 任务失败。"));
                }
                else
                {
                    var result = ParseResult(responseText);
                    request.Completion.TrySetResult(result);
                }
            }
            catch (Exception failure)
            {
                request.Completion.TrySetException(failure);
            }
            finally
            {
                try
                {
                    lock (request.Gate)
                    {
                        if (!request.TaskReleased)
                        {
                            request.TaskReleased = true;
                            taskFree(task);
                        }
                    }
                }
                catch (Exception cleanupError)
                {
                    request.Completion.TrySetException(cleanupError);
                }
                try
                {
                    request.CancellationRegistration.Dispose();
                }
                catch (Exception registrationError)
                {
                    request.Completion.TrySetException(registrationError);
                }
                try
                {
                    request.ReleaseHandle();
                }
                catch (Exception handleError)
                {
                    request.Completion.TrySetException(handleError);
                }
                lock (stateLock) active.Remove(request);
                request.Cleanup.TrySetResult(true);
            }
        }

        private static void ProgressThunk(IntPtr eventJson, IntPtr userData)
        {
            if (eventJson == IntPtr.Zero || userData == IntPtr.Zero) return;
            try
            {
                var handle = GCHandle.FromIntPtr(userData);
                if (!handle.IsAllocated || handle.Target is not FfiRequest request) return;
                var text = Marshal.PtrToStringUTF8(eventJson);
                if (text is null || JsonNode.Parse(text) is not JsonObject envelope
                    || envelope["event"] is not JsonObject eventObject) return;
                request.Progress?.Invoke(eventObject);
            }
            catch
            {
                // A UI callback cannot be allowed to unwind through the C ABI.
            }
        }

        private JsonObject ParseResult(string? text)
        {
            if (text is null || JsonNode.Parse(text) is not JsonObject envelope
                || envelope["ok"]?.GetValue<bool>() != true)
                throw new InvalidOperationException("Rust FFI 返回了无效结果。");
            return envelope["result"] as JsonObject ?? new JsonObject();
        }

        private static string? ParseError(string? text)
        {
            if (text is null || JsonNode.Parse(text) is not JsonObject envelope) return null;
            return envelope["error"]?["message"]?.GetValue<string>();
        }

        private string? TakeAndFree(IntPtr value)
        {
            if (value == IntPtr.Zero) return null;
            try { return Marshal.PtrToStringUTF8(value); }
            finally { stringFree(value); }
        }

        public void Dispose()
        {
            List<FfiRequest> requests;
            lock (stateLock)
            {
                if (disposed) return;
                disposed = true;
                stopping = true;
                requests = active.Keys.ToList();
            }
            foreach (var request in requests) RequestCancel(request);
            foreach (var request in requests)
            {
                try { request.Cleanup.Task.GetAwaiter().GetResult(); }
                catch { /* cancellation and shutdown are expected here */ }
            }
            lock (stateLock)
            {
                if (runtime != IntPtr.Zero)
                {
                    free(runtime);
                    runtime = IntPtr.Zero;
                }
            }
            NativeLibrary.Free(library);
        }

        private sealed class FfiRequest
        {
            internal readonly FfiEngine Owner;
            internal readonly Action<JsonObject>? Progress;
            internal readonly TaskCompletionSource<JsonObject> Completion =
                new(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly TaskCompletionSource<bool> Cleanup =
                new(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly object Gate = new();
            internal GCHandle Handle;
            internal CancellationTokenRegistration CancellationRegistration;
            internal IntPtr Task;
            internal bool TaskReleased;
            internal bool CancelRequested;

            internal FfiRequest(FfiEngine owner, Action<JsonObject>? progress)
            {
                Owner = owner;
                Progress = progress;
            }

            internal void SetTask(IntPtr task)
            {
                var cancelRequested = false;
                lock (Gate)
                {
                    Task = task;
                    cancelRequested = CancelRequested;
                }
                if (cancelRequested) Owner.RequestCancel(this);
            }

            // 字段 Task（原生任务句柄）遮蔽了 System.Threading.Tasks.Task。
            internal void StartJoin() => _ = System.Threading.Tasks.Task.Run(() => Owner.JoinRequest(this));

            internal void ReleaseHandle()
            {
                lock (Gate)
                {
                    if (Handle.IsAllocated) Handle.Free();
                }
            }
        }

        private sealed class Utf8 : IDisposable
        {
            private readonly GCHandle pin;
            internal readonly IntPtr Pointer;

            internal Utf8(string text)
            {
                var bytes = Encoding.UTF8.GetBytes(text + "\0");
                pin = GCHandle.Alloc(bytes, GCHandleType.Pinned);
                Pointer = pin.AddrOfPinnedObject();
            }

            public void Dispose() => pin.Free();
        }
    }

    /// <summary>
    /// Explicit development-only JSONL bridge. It is deliberately not used by
    /// an installed app unless FIRESKY_NATIVE_BACKEND=jsonl is set.
    /// </summary>
    private sealed class JsonlEngine : IDisposable
    {
        private Process? process;
        private JsonlEngine? nativeLocation;
        private readonly SemaphoreSlim writeLock = new(1, 1);
        private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonObject>> pending = new();
        private readonly ConcurrentDictionary<string, Action<JsonObject>> progress = new();

        internal void Start(string workspace, string binary)
        {
            Launch(workspace, binary, ["desktop", "--workspace", Path.GetFullPath(workspace)]);
            var locationBinary = Environment.GetEnvironmentVariable("FIRESKY_LOCATION_ENGINE_BIN");
            if (string.IsNullOrWhiteSpace(locationBinary)) return;
            if (!File.Exists(locationBinary))
                throw new InvalidOperationException("Rust 位置引擎不存在，请检查 FIRESKY_LOCATION_ENGINE_BIN。");
            var location = new JsonlEngine();
            location.Launch(workspace, locationBinary, ["desktop-location"], "location-engine.log");
            nativeLocation = location;
        }

        private void Launch(string workspace, string executable, IEnumerable<string> arguments,
            string logName = "engine.log")
        {
            var info = new ProcessStartInfo(executable)
            {
                WorkingDirectory = workspace,
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
                CreateNoWindow = true,
            };
            foreach (var argument in arguments) info.ArgumentList.Add(argument);
            var ecCodesRoot = Path.Combine(AppContext.BaseDirectory, "Resources", "eccodes");
            var ecCodesBinary = new[]
            {
                Path.Combine(ecCodesRoot, "bin", "eccodes.dll"),
                Path.Combine(ecCodesRoot, "eccodes.dll"),
            }.FirstOrDefault(File.Exists);
            if (ecCodesBinary is not null)
            {
                info.Environment["FIRESKY_ECCODES_LIBRARY"] = ecCodesBinary;
                info.Environment["PATH"] = Path.GetDirectoryName(ecCodesBinary) +
                    Path.PathSeparator + (info.Environment["PATH"] ?? "");
                foreach (var (variable, folder) in new[]
                {
                    ("ECCODES_DEFINITION_PATH", "definitions"),
                    ("ECCODES_SAMPLES_PATH", "samples"),
                })
                {
                    var path = Path.Combine(ecCodesRoot, "share", "eccodes", folder);
                    if (!Directory.Exists(path)) path = Path.Combine(ecCodesRoot, folder);
                    if (Directory.Exists(path)) info.Environment[variable] = path;
                }
            }
            process = Process.Start(info) ?? throw new InvalidOperationException("无法启动引擎。");
            var child = process;
            var logPath = Path.Combine(
                Environment.GetEnvironmentVariable("FIRESKY_NATIVE_LOG_DIR")
                    ?? Path.Combine(App.LocalDataRoot, "FireSky"),
                logName);
            Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
            _ = ReadOutput(child);
            _ = Task.Run(async () =>
            {
                try
                {
                    await using var log = new StreamWriter(logPath, append: true);
                    while (await child.StandardError.ReadLineAsync() is { } line)
                        await log.WriteLineAsync(line);
                }
                catch (Exception) { }
            });
        }

        internal async Task<JsonObject> Call(string method, JsonObject? parameters,
            Action<JsonObject>? onProgress, CancellationToken cancellation)
        {
            if ((method is "search" or "metadata" or "inventory") && nativeLocation is { } location)
                return await location.Call(method, parameters, onProgress, cancellation);
            if (method == "hello" && nativeLocation is { } locationEngine)
            {
                var hello = await locationEngine.Call("hello", null, null, cancellation);
                var capabilities = hello["capabilities"] as JsonArray;
                if (hello["protocol"]?.GetValue<int>() != 1
                    || hello["engine"]?.GetValue<string>() != "firesky-rust-location"
                    || capabilities == null
                    || new[] { "search", "metadata", "inventory", "cancel" }.Any(
                        name => !capabilities.Any(value => value?.GetValue<string>() == name)))
                    throw new InvalidOperationException("Rust 位置引擎协议不匹配。");
            }
            if (process is not { HasExited: false } child)
                throw new InvalidOperationException("引擎未连接，请在设置中重新连接。");
            cancellation.ThrowIfCancellationRequested();
            var id = Guid.NewGuid().ToString("N");
            var completion = new TaskCompletionSource<JsonObject>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            pending[id] = completion;
            if (onProgress != null) progress[id] = onProgress;
            try
            {
                await Write(child, new JsonObject
                {
                    ["id"] = id, ["method"] = method, ["params"] = parameters ?? new JsonObject()
                });
                using var registration = cancellation.Register(() => _ = SendCancel(child, id));
                return await completion.Task;
            }
            finally
            {
                pending.TryRemove(id, out _);
                progress.TryRemove(id, out _);
            }
        }

        private async Task SendCancel(Process child, string id)
        {
            try
            {
                await Write(child, new JsonObject
                {
                    ["id"] = Guid.NewGuid().ToString("N"), ["method"] = "cancel",
                    ["params"] = new JsonObject { ["request_id"] = id }
                });
            }
            catch (Exception) { }
        }

        private async Task Write(Process child, JsonObject request)
        {
            var text = request.ToJsonString();
            if (Encoding.UTF8.GetByteCount(text) > 1024 * 1024)
                throw new InvalidOperationException("请求过大，请缩小输入内容。");
            await writeLock.WaitAsync();
            try
            {
                await child.StandardInput.WriteLineAsync(text);
                await child.StandardInput.FlushAsync();
            }
            finally { writeLock.Release(); }
        }

        private async Task ReadOutput(Process child)
        {
            try
            {
                while (await child.StandardOutput.ReadLineAsync() is { } line)
                {
                    if (JsonNode.Parse(line) is not JsonObject obj) continue;
                    var id = obj["id"]?.GetValue<string>() ?? "";
                    if (obj["type"]?.GetValue<string>() == "progress")
                    {
                        if (progress.TryGetValue(id, out var callback) && obj["event"] is JsonObject item)
                            callback(item);
                    }
                    else if (pending.TryRemove(id, out var completion))
                    {
                        if (obj["type"]?.GetValue<string>() == "error")
                            completion.TrySetException(new InvalidOperationException(
                                obj["error"]?["message"]?.GetValue<string>() ?? "操作失败"));
                        else completion.TrySetResult(obj["result"] as JsonObject ?? new JsonObject());
                    }
                }
            }
            catch (Exception error)
            {
                if (ReferenceEquals(process, child)) FailAll(error);
            }
            finally
            {
                if (ReferenceEquals(process, child))
                    FailAll(new InvalidOperationException("引擎已退出，请重新连接。"));
            }
        }

        private void FailAll(Exception error)
        {
            foreach (var (id, completion) in pending)
                if (pending.TryRemove(id, out _)) completion.TrySetException(error);
            progress.Clear();
        }

        public void Dispose()
        {
            var location = nativeLocation;
            nativeLocation = null;
            location?.Dispose();
            var old = process;
            process = null;
            if (old != null)
            {
                try { if (!old.HasExited) old.Kill(entireProcessTree: true); }
                catch (InvalidOperationException) { }
                old.Dispose();
            }
            FailAll(new InvalidOperationException("引擎连接已关闭。"));
        }
    }
}
