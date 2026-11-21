using System.Diagnostics;
using System.IO.Compression;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI;

namespace FireSky;

/// <summary>
/// 应用内更新（v1.5.0 口径）：GitHub Releases 最新版检查 → 架构匹配的 zip 下载
/// （进度/取消 + SHA256SUMS 校验）→ 解压暂存后由批处理脚本自替换并重启。
/// 检查与下载走 HttpClient（默认系统代理；环境变量代理与 macOS 同口径生效）。
/// 无写权限（如 Program Files 管理员安装）时不静默失败，给出手动安装指引。
/// </summary>
public sealed class Updater
{
    public enum Phase { Idle, Checking, Current, Available, Downloading, Installing, Failed }

    public sealed record Info(
        string Version,
        string Notes,
        string AssetUrl,
        long AssetBytes,
        string? ChecksumUrl);

    public const string Repository = "xyl-g/FireSky";

    private Phase phase = Phase.Idle;
    private Info? info;
    private string message = "";
    private long receivedBytes;
    private long totalBytes;
    private bool autoCheck = true;
    private CancellationTokenSource? checkOperation;
    private CancellationTokenSource? downloadOperation;

    public Phase CurrentPhase => phase;
    public Info? UpdateInfo => info;
    public string Message => message;
    public long ReceivedBytes => receivedBytes;
    public long TotalBytes => totalBytes;
    public bool AutoCheck => autoCheck;
    public bool IsUpdateAvailable => phase == Phase.Available;

    public event Action? Changed;

    private void Notify() => Changed?.Invoke();

    public void SetAutoCheck(bool value)
    {
        autoCheck = value;
        Notify();
    }

    // ---------- 纯函数（对齐 macOS Updater / UpdaterChecks） ----------

    /// <summary>"v1.5.0" → "1.5.0"；其余原样返回。</summary>
    public static string NormalizeTag(string tag) =>
        tag.StartsWith('v') ? tag[1..] : tag;

    /// <summary>资产名匹配：FireSky-{版本}-windows-{架构}.zip。</summary>
    public static string AssetName(string version, string architecture) =>
        $"FireSky-{version}-windows-{architecture}.zip";

    /// <summary>语义化版本比较（逐段数值，缺失段按 0）。-1/0/1 = 小于/相等/大于。</summary>
    public static int CompareVersions(string left, string right)
    {
        var leftParts = left.Split('.', StringSplitOptions.RemoveEmptyEntries)
            .Select(part => int.TryParse(part, out var n) ? n : 0).ToList();
        var rightParts = right.Split('.', StringSplitOptions.RemoveEmptyEntries)
            .Select(part => int.TryParse(part, out var n) ? n : 0).ToList();
        while (leftParts.Count < rightParts.Count) leftParts.Add(0);
        while (rightParts.Count < leftParts.Count) rightParts.Add(0);
        for (var i = 0; i < leftParts.Count; i++)
        {
            if (leftParts[i] < rightParts[i]) return -1;
            if (leftParts[i] > rightParts[i]) return 1;
        }
        return 0;
    }

    /// <summary>当前机器架构（x64 / arm64），与发布资产命名一致。</summary>
    public static string MachineArchitecture() =>
        System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture
            == System.Runtime.InteropServices.Architecture.Arm64 ? "arm64" : "x64";

    /// <summary>当前应用版本（来自程序集 Version，构建时注入 engine/Cargo.toml 版本）。</summary>
    public static string CurrentVersion()
    {
        var version = Assembly.GetEntryAssembly()?.GetName().Version;
        return version == null ? "" : $"{version.Major}.{version.Minor}.{version.Build}";
    }

    /// <summary>从 release JSON 解析更新信息（架构匹配 zip 资产 + SHA256SUMS.txt 地址）。</summary>
    public static Info? ParseRelease(JsonObject? release, string architecture)
    {
        if (release == null) return null;
        if (BoolOf(release["draft"]) || BoolOf(release["prerelease"])) return null;
        var version = NormalizeTag(TextOf(release["tag_name"]));
        if (version.Length == 0 || version.Length > 32
            || version.Any(character => !char.IsLetterOrDigit(character) && character != '.'))
            return null;
        var wanted = AssetName(version, architecture);
        string? assetUrl = null;
        long assetBytes = 0;
        string? checksumUrl = null;
        foreach (var asset in release["assets"] as JsonArray ?? [])
        {
            if (asset is not JsonObject item) continue;
            var name = TextOf(item["name"]);
            var url = TextOf(item["browser_download_url"]);
            if (name == wanted && url.Contains("/releases/download/", StringComparison.Ordinal))
            {
                assetUrl = url;
                assetBytes = (long?)DoubleOf(item["size"]) ?? 0;
            }
            if (name == "SHA256SUMS.txt" && url.Length > 0) checksumUrl = url;
        }
        if (assetUrl == null) return null;
        var body = TextOf(release["body"]);
        return new Info(version, body.Length > 1200 ? body[..1200] : body,
            assetUrl, assetBytes, checksumUrl);
    }

    /// <summary>SHA256SUMS.txt 中本架构资产的校验和行；缺失返回 null。</summary>
    public static string? ChecksumRow(string checksumsText, string assetName)
    {
        foreach (var line in checksumsText.Split('\n'))
        {
            var parts = line.Trim().Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 2) continue;
            var name = parts[1].TrimStart('*');
            if (name == assetName && IsHex64(parts[0])) return parts[0].ToLowerInvariant();
        }
        return null;
    }

    private static bool BoolOf(JsonNode? node) => node?.GetValue<bool>() ?? false;
    private static string TextOf(JsonNode? node) => node is System.Text.Json.Nodes.JsonValue value
        && value.GetValueKind() == System.Text.Json.JsonValueKind.String
        ? value.GetValue<string>() : "";
    private static double? DoubleOf(JsonNode? node) =>
        node is System.Text.Json.Nodes.JsonValue value && value.TryGetValue<double>(out var number)
        && double.IsFinite(number) ? number : null;
    private static bool IsHex64(string value) =>
        value.Length == 64 && value.All(character => Uri.IsHexDigit(character));

    // ---------- 会话（系统代理 + 环境代理，与 macOS 同口径） ----------

    private static HttpClient MakeClient(int timeoutSeconds)
    {
        var handler = new HttpClientHandler
        {
            UseProxy = true,
            Proxy = EnvironmentProxy(),
            DefaultProxyCredentials = CredentialCache.DefaultCredentials,
            AutomaticDecompression = DecompressionMethods.None,
        };
        var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(timeoutSeconds) };
        client.DefaultRequestHeaders.UserAgent.ParseAdd(
            $"FireSky-Updater/{CurrentVersion()}");
        return client;
    }

    private static IWebProxy? EnvironmentProxy()
    {
        var environment = Environment.GetEnvironmentVariables();
        var proxy = (environment["https_proxy"] ?? environment["HTTPS_PROXY"]
            ?? environment["all_proxy"] ?? environment["ALL_PROXY"]) as string;
        if (string.IsNullOrWhiteSpace(proxy)) return null;
        try
        {
            if (!proxy.Contains("://")) proxy = $"http://{proxy}";
            return new WebProxy(new Uri(proxy));
        }
        catch (UriFormatException) { return null; }
    }

    private sealed class UpdateException(string message) : Exception(message);

    // ---------- 检查 ----------

    public async void CheckForUpdates(bool interactive)
    {
        if (phase is Phase.Checking or Phase.Downloading or Phase.Installing) return;
        checkOperation?.Cancel();
        checkOperation?.Dispose();
        checkOperation = new CancellationTokenSource();
        var token = checkOperation.Token;
        phase = Phase.Checking;
        message = "";
        Notify();
        try
        {
            var found = await FetchLatestRelease(token);
            if (found == null)
            {
                phase = Phase.Current;
                message = $"已是最新版本（{CurrentVersion()}）。";
            }
            else
            {
                info = found;
                phase = Phase.Available;
                message = "";
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            phase = Phase.Idle;
        }
        catch (Exception error)
        {
            info = null;
            // 静默检查失败不打扰；手动检查给出具体原因。
            message = interactive ? FriendlyUpdateError(error) : "";
            phase = interactive ? Phase.Failed : Phase.Idle;
        }
        Notify();
    }

    private async Task<Info?> FetchLatestRelease(CancellationToken token)
    {
        using var client = MakeClient(20);
        using var request = new HttpRequestMessage(HttpMethod.Get,
            $"https://api.github.com/repos/{Repository}/releases/latest");
        request.Headers.Accept.ParseAdd("application/vnd.github+json");
        using var response = await client.SendAsync(request, token);
        if (response.StatusCode != HttpStatusCode.OK)
            throw new UpdateException($"GitHub 返回状态 {(int)response.StatusCode}");
        var text = await response.Content.ReadAsStringAsync(token);
        var release = JsonNode.Parse(text) as JsonObject
            ?? throw new UpdateException("GitHub 响应无法解析");
        var architecture = MachineArchitecture();
        var parsed = ParseRelease(release, architecture)
            ?? throw new UpdateException($"最新 Release 没有适用于 {architecture} 的安装包");
        if (CompareVersions(parsed.Version, CurrentVersion()) <= 0) return null;
        return parsed;
    }

    // ---------- 下载 + 校验 + 安装 ----------

    public async void DownloadAndInstall()
    {
        if (info == null || phase != Phase.Available) return;
        var target = info;
        phase = Phase.Downloading;
        receivedBytes = 0;
        totalBytes = target.AssetBytes;
        message = "";
        Notify();
        downloadOperation?.Cancel();
        downloadOperation?.Dispose();
        downloadOperation = new CancellationTokenSource();
        var token = downloadOperation.Token;
        try
        {
            var archive = await DownloadZip(target, token);
            await VerifyChecksum(target, archive, token);
            phase = Phase.Installing;
            Notify();
            await InstallArchive(target, archive);
            // InstallArchive 成功后进程已被批处理重启流程接管；这里等待退出。
            await Task.Delay(1500, CancellationToken.None);
            Environment.Exit(0);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            phase = Phase.Available;
            message = "已取消下载；未安装任何内容。";
            Notify();
        }
        catch (Exception error)
        {
            phase = Phase.Failed;
            message = FriendlyUpdateError(error);
            Notify();
        }
    }

    public void CancelDownload()
    {
        if (phase != Phase.Downloading) return;
        downloadOperation?.Cancel();
    }

    /// <summary>流式下载 zip 到临时目录，按 1 MiB 落盘并刷新进度。</summary>
    private async Task<string> DownloadZip(Info target, CancellationToken token)
    {
        using var client = MakeClient(600);
        using var response = await client.GetAsync(target.AssetUrl, HttpCompletionOption.ResponseHeadersRead, token);
        if (response.StatusCode != HttpStatusCode.OK)
            throw new UpdateException($"下载失败：GitHub 返回状态 {(int)response.StatusCode}");
        if (response.Content.Headers.ContentLength is { } length && length > 0)
            totalBytes = length;
        Notify();
        var archive = Path.Combine(Path.GetTempPath(), $"firesky-update-{target.Version}",
            AssetName(target.Version, MachineArchitecture()));
        Directory.CreateDirectory(Path.GetDirectoryName(archive)!);
        if (File.Exists(archive)) File.Delete(archive);
        await using var stream = await response.Content.ReadAsStreamAsync(token);
        await using var output = new FileStream(archive, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        var buffer = new byte[1 << 20];
        while (true)
        {
            token.ThrowIfCancellationRequested();
            var read = await stream.ReadAsync(buffer, token);
            if (read == 0) break;
            await output.WriteAsync(buffer.AsMemory(0, read), token);
            receivedBytes += read;
        }
        if (totalBytes > 0 && receivedBytes != totalBytes)
        {
            File.Delete(archive);
            throw new UpdateException($"下载不完整（{receivedBytes}/{totalBytes} 字节）");
        }
        return archive;
    }

    /// <summary>SHA256SUMS.txt 校验（存在才校验；缺失视为通过并在安装摘要中说明）。</summary>
    private async Task VerifyChecksum(Info target, string archive, CancellationToken token)
    {
        if (target.ChecksumUrl == null) return;
        using var client = MakeClient(30);
        using var response = await client.GetAsync(target.ChecksumUrl, token);
        if (response.StatusCode != HttpStatusCode.OK)
            throw new UpdateException("无法获取 SHA256SUMS.txt 进行校验");
        var text = await response.Content.ReadAsStringAsync(token);
        var expected = ChecksumRow(text, AssetName(target.Version, MachineArchitecture()))
            ?? throw new UpdateException("SHA256SUMS.txt 中没有本架构安装包的校验和");
        var actual = await Task.Run(() => Sha256OfFile(archive), token);
        if (actual != expected)
        {
            File.Delete(archive);
            throw new UpdateException("SHA256 校验不匹配，已放弃安装");
        }
    }

    internal static string Sha256OfFile(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    /// <summary>解压到临时目录 → 校验 FireSky.exe → 生成批处理脚本自替换并重启。
    /// 无写权限时抛出明确错误（手动安装指引），不静默失败。</summary>
    private static async Task InstallArchive(Info target, string archive)
    {
        await Task.Run(() =>
        {
            var appDirectory = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            // 写权限探测：Program Files 管理员安装时放弃自动替换并说明。
            try
            {
                var probe = Path.Combine(appDirectory, $".update-probe-{Guid.NewGuid():N}");
                using (File.Create(probe)) { }
                File.Delete(probe);
            }
            catch (Exception error)
            {
                throw new UpdateException(
                    $"安装目录没有写权限（{error.Message}）。请到 GitHub Releases 手动下载安装器或 zip 安装。");
            }
            var stage = Path.Combine(Path.GetTempPath(), $"firesky-update-{target.Version}", "staged");
            if (Directory.Exists(stage)) Directory.Delete(stage, true);
            ZipFile.ExtractToDirectory(archive, stage, overwriteFiles: true);
            var stagedExe = Path.Combine(stage, "FireSky", "FireSky.exe");
            if (!File.Exists(stagedExe))
                throw new UpdateException("安装包内未找到 FireSky\\FireSky.exe");
            var backup = $"{appDirectory}.old-{DateTime.Now:yyyyMMddHHmmss}";
            var pid = Environment.ProcessId;
            var script = Path.Combine(Path.GetTempPath(), $"firesky-update-{target.Version}", "apply.cmd");
            var content = $"""
                @echo off
                rem FireSky 自替换：等待旧进程退出 → 旧目录改名备份 → 新版本就位 → 重启 → 清理
                :wait
                tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
                if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)
                if exist "{appDirectory}" move /y "{appDirectory}" "{backup}" >nul
                move /y "{Path.Combine(stage, "FireSky")}" "{appDirectory}" >nul
                if exist "{backup}" rd /s /q "{backup}"
                start "" "{Path.Combine(appDirectory, "FireSky.exe")}"
                del "%~f0"
                """;
            Directory.CreateDirectory(Path.GetDirectoryName(script)!);
            File.WriteAllText(script, content);
            Process.Start(new ProcessStartInfo("cmd.exe", $"/c \"\"{script}\"\"")
            {
                UseShellExecute = true,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
        });
    }

    private static string FriendlyUpdateError(Exception error)
    {
        if (error is UpdateException specific)
        {
            var detail = specific.Message;
            return detail.Contains("校验和", StringComparison.Ordinal) ? detail
                : detail.Contains("权限", StringComparison.Ordinal) ? detail
                : $"更新检查/下载失败：{detail}";
        }
        var lower = error.Message.ToLowerInvariant();
        if (lower.Contains("timeout") || lower.Contains("timed out")
            || lower.Contains("connection") || lower.Contains("network")
            || lower.Contains("socket"))
            return $"无法连接 GitHub（检查网络或代理设置）。{error.Message}";
        return $"更新检查/下载失败：{error.Message}";
    }
}
