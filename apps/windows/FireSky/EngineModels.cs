using System.Globalization;
using System.Text.Json.Nodes;

namespace FireSky;

/// <summary>
/// 下载连通性探测行（对齐 macOS DownloadProbeRow）：三数据源各一行，
/// 状态机为 waiting → testing → passed / not_configured / timeout / cancelled / error。
/// </summary>
public sealed class ProbeRow
{
    public required string Source { get; init; }
    public required string Title { get; init; }
    public string State { get; set; } = "waiting";
    public string Detail { get; set; } = "尚未探测";
    public string Host { get; set; } = "";
    public long Bytes { get; set; }
    public long ElapsedMs { get; set; }

    public bool Passed => State == "passed";
    public bool Active => State == "testing";
    public string Label => State switch
    {
        "waiting" => "未探测",
        "testing" => "探测中",
        "passed" => "下载验证通过",
        "not_configured" => "未配置",
        "timeout" => "超时，未验证",
        "cancelled" => "已取消",
        _ => "未通过",
    };

    public string Measurement =>
        $"{Bytes / 1024d:0.#} KB · {ElapsedMs / 1000d:0.0} 秒";

    public static ProbeRow[] Initial() =>
    [
        new() { Source = "gfs", Title = "GFS · NOAA NOMADS" },
        new() { Source = "ec", Title = "EC · ECMWF Open Data" },
        new() { Source = "cams", Title = "CAMS · Copernicus ADS" },
    ];

    /// <summary>引擎探测行解析；source 必须是三个已知源之一。</summary>
    public static ProbeRow? From(JsonObject? json)
    {
        if (json is null) return null;
        var source = StringOrNull(json["source"]);
        if (source is not ("gfs" or "ec" or "cams")) return null;
        return new ProbeRow
        {
            Source = source,
            Title = StringOrNull(json["title"]) ?? source.ToUpperInvariant(),
            State = StringOrNull(json["state"]) ?? "error",
            Detail = StringOrNull(json["detail"]) ?? "",
            Host = StringOrNull(json["host"]) ?? "",
            Bytes = (long?)DoubleOf(json["bytes_received"]) ?? 0,
            ElapsedMs = (long?)DoubleOf(json["elapsed_ms"]) ?? 0,
        };
    }

    private static string? StringOrNull(JsonNode? node) =>
        node is System.Text.Json.Nodes.JsonValue value
        && value.GetValueKind() == System.Text.Json.JsonValueKind.String
            ? value.GetValue<string>() : null;

    private static double? DoubleOf(JsonNode? node) =>
        node is System.Text.Json.Nodes.JsonValue value
        && value.TryGetValue<double>(out var number) && double.IsFinite(number)
            ? number : null;
}

/// <summary>
/// 历史/同步时间戳统一本地化展示（本机时区 "MM-dd HH:mm"）；兼容小数秒 ISO8601。
/// 解析失败时原样返回；原始 UTC 串由调用方放进 ToolTip。
/// </summary>
public static class HistoryTime
{
    public static string Text(string? iso)
    {
        if (string.IsNullOrWhiteSpace(iso) || !TryParse(iso, out var date)) return iso ?? "";
        return date.LocalDateTime.ToString("MM-dd HH:mm", CultureInfo.InvariantCulture);
    }

    public static bool TryParse(string value, out DateTimeOffset parsed)
    {
        return DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture,
            DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeUniversal
            | DateTimeStyles.AdjustToUniversal, out parsed);
    }
}
