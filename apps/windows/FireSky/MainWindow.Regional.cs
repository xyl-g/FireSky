using Microsoft.UI;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace FireSky;

/// <summary>
/// 区域预测检查器：网格/城市排行、地图选点聚焦、网格详情、
/// 冻结剖面查询（rust-desktop-profile-query-v1 合同校验）与剖面图层。
/// </summary>
public sealed partial class MainWindow
{
    internal sealed class ProfileQuerySpec
    {
        internal required JsonObject Payload { get; init; }
        internal required string ResultId { get; init; }
        internal required long SourceBytes { get; init; }
        internal required string SourceSha256 { get; init; }
        internal required string Model { get; init; }
        internal required double Lat { get; init; }
        internal required double Lon { get; init; }
        internal required string ValidTimeUtc { get; init; }
        internal required string CycleUtc { get; init; }
    }

    /// <summary>profile_query 请求构建（对齐 ProfileQueryBuilder.request）。</summary>
    internal static ProfileQuerySpec? BuildProfileQuery(JsonObject report, string model,
        double lat, double lon, string localTime)
    {
        if (model is not ("gfs" or "ec" or "satellite") || !double.IsFinite(lat)
            || lat is < -90 or > 90 || !double.IsFinite(lon) || lon is < -180 or > 180)
            return null;
        var source = report["profile_source"] as JsonObject;
        if (source == null || Text(source, "contract") != "rust-desktop-profile-source-v1"
            || BoolValue(source, "available") != true
            || Text(source, "query_method") != "profile_query"
            || Text(source, "model_source") != model
            || Text(source, "profile_kind") != (model == "satellite"
                ? "satellite-step-solar-path-diagnostic" : "model-pressure-cross-section"))
            return null;
        var resultId = Text(source, "result_id");
        var sourceIdentity = source["source_identity"] as JsonObject;
        var bytes = Integer(sourceIdentity, "bytes");
        var sha = Text(sourceIdentity, "sha256");
        var cycle = Text(source, "cycle_utc");
        if (!IsHex64(resultId) || bytes is null || bytes < 0 || !IsHex64(sha)
            || !TryUtc(cycle, out var cycleUtc) || !TryUtc(localTime, out var validUtc))
            return null;
        return new ProfileQuerySpec
        {
            Payload = new JsonObject
            {
                ["result_id"] = resultId,
                ["source_identity"] = new JsonObject
                {
                    ["bytes"] = bytes.Value, ["sha256"] = sha,
                },
                ["model_source"] = model, ["lat"] = lat, ["lon"] = lon,
                ["valid_time_utc"] = validUtc,
            },
            ResultId = resultId, SourceBytes = bytes.Value, SourceSha256 = sha, Model = model,
            Lat = lat, Lon = lon, ValidTimeUtc = validUtc, CycleUtc = cycleUtc,
        };
    }

    internal static bool ProfileResponseMatches(JsonObject response, ProfileQuerySpec request)
    {
        if (Text(response, "contract") != "rust-desktop-profile-query-v1"
            || Text(response, "result_id") != request.ResultId
            || Text(response, "model_source") != request.Model
            || Math.Abs(Number(response, "lat") - request.Lat) > 0.000001
            || Math.Abs(Number(response, "lon") - request.Lon) > 0.000001
            || !SameUtc(Text(response, "valid_time_utc"), request.ValidTimeUtc))
            return false;
        var identity = response["source_identity"] as JsonObject;
        return Integer(identity, "bytes") == request.SourceBytes
            && Text(identity, "sha256") == request.SourceSha256;
    }

    private void RenderRegionalInspector(JsonObject? report)
    {
        RegionalGridRows.Items.Clear();
        CityRankingRows.Items.Clear();
        if (report == null)
        {
            RegionalGridSummary.Text = "当前模式没有可用区域结果。";
            RegionalProfileStatus.Text = "当前模式没有可读取的冻结剖面。";
            RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
            return;
        }
        var points = (report["points"] as JsonArray ?? [])
            .OfType<JsonObject>()
            .Where(row => double.IsFinite(Number(row, "lat")) && double.IsFinite(Number(row, "lon")))
            .OrderByDescending(row => Score(row))
            .ThenByDescending(row => Number(row, "lat"))
            .ThenBy(row => Number(row, "lon"))
            .ToList();
        var cityReport = report["city_ranking"] as JsonObject;
        var cities = (cityReport?["items"] as JsonArray ?? [])
            .OfType<JsonObject>()
            .Where(row => double.IsFinite(Number(row, "lat")) && double.IsFinite(Number(row, "lon")))
            .OrderByDescending(row => Score(row))
            .ToList();
        var missing = (long?)DoubleValue(report["error_count"]) ?? 0;
        var usable = (long?)DoubleValue(report["usable_count"]) ?? points.Count;
        RegionalGridSummary.Text =
            $"{points.Count} 个网格点 · {cities.Count} 个城市 · 缺测 {missing} · 周期 {Text(report, "cycle_time_utc")}"
            + (Text(report, "spacing_deg") is { Length: > 0 } spacing ? $" · 格点 {spacing}°" : "");
        foreach (var row in points.Take(2000))
            RegionalGridRows.Items.Add(RegionalRowButton(row, false));
        foreach (var row in cities.Take(2000))
            CityRankingRows.Items.Add(RegionalRowButton(row, true));
        if (points.Count == 0)
            RegionalGridRows.Items.Add(new TextBlock
            {
                Text = "暂无可用网格结果。", Opacity = .65, Margin = new Thickness(4),
            });
        if (cities.Count == 0)
        {
            var reason = Text(cityReport, "reason");
            CityRankingRows.Items.Add(new TextBlock
            {
                Text = string.IsNullOrWhiteSpace(reason) ? "该区域暂无城市排名。" : reason,
                Opacity = .65, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(4),
            });
        }
        RegionalProfileSelection.Text = string.IsNullOrWhiteSpace(regionalProfileModel)
            ? "选择网格后读取冻结剖面"
            : $"{regionalProfileModel.ToUpperInvariant()} · {regionalProfileLat:0.00}°, {regionalProfileLon:0.00}°";
        RenderProfileSection();
    }

    private Button RegionalRowButton(JsonObject row, bool city)
    {
        var name = city ? Text(row, "name") : $"{Number(row, "lat"):0.00}°, {Number(row, "lon"):0.00}°";
        if (string.IsNullOrWhiteSpace(name))
            name = $"{Number(row, "lat"):0.00}°, {Number(row, "lon"):0.00}°";
        var score = row["peak_quality_index"] ?? row["quality_index"];
        var best = TimeLabel(Text(row, "best_time_local"));
        var button = new Button
        {
            Content = $"{name}    {(score == null ? "--" : Score(row).ToString("0.00"))}    {best}",
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(8, 6, 8, 6),
            Background = new SolidColorBrush(Colors.Transparent),
        };
        var snapshot = row.DeepClone() as JsonObject ?? row;
        button.Click += (_, _) => _ = FocusRegionalPoint(snapshot);
        ToolTipService.SetToolTip(button, city
            ? "点击查看城市详情与冻结剖面"
            : "点击查看网格详情与冻结剖面");
        return button;
    }

    private void OnRegionalInspectorModeChanged(object sender, SelectionChangedEventArgs args)
    {
        if (RegionalInspectorMode == null || RegionalGridRows == null
            || CityRankingRows == null) return;
        var mode = TagOf(RegionalInspectorMode.SelectedItem);
        RegionalGridRows.Visibility = mode == "grid" ? Visibility.Visible : Visibility.Collapsed;
        CityRankingRows.Visibility = mode == "cities" ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task FocusRegionalPoint(double lat, double lon)
    {
        var report = SelectedReport();
        var rows = (report?["points"] as JsonArray ?? []).OfType<JsonObject>().ToList();
        rows.AddRange((report?["city_ranking"]?["items"] as JsonArray ?? []).OfType<JsonObject>());
        var nearest = rows
            .Where(row => Math.Abs(Number(row, "lat") - lat) <= .2 &&
                         Math.Abs(Number(row, "lon") - lon) <= .2)
            .OrderBy(row =>
            {
                var dy = Number(row, "lat") - lat;
                var dx = Number(row, "lon") - lon;
                return dy * dy + dx * dx;
            })
            .FirstOrDefault();
        if (nearest == null)
        {
            RegionalProfileStatus.Text = "地图选点未命中当前结果网格。";
            return;
        }
        await FocusRegionalPoint(nearest);
    }

    private async Task FocusRegionalPoint(JsonObject row)
    {
        var report = SelectedReport();
        if (report == null) return;
        var lat = Number(row, "lat");
        var lon = Number(row, "lon");
        if (!double.IsFinite(lat) || !double.IsFinite(lon)) return;
        regionalProfileLat = lat;
        regionalProfileLon = lon;
        regionalProfileModel = TagOf(ResultModel.SelectedItem) is { Length: > 0 } model
            && model is "gfs" or "ec" ? model : "gfs";
        RegionalProfileSelection.Text =
            $"{regionalProfileModel.ToUpperInvariant()} · {lat:0.00}°, {lon:0.00}°";
        ShowRegionalCellDetail(row);
        await QueryRegionalProfile(report, regionalProfileModel, lat, lon,
            Text(row, "best_time_local"));
    }

    /// <summary>网格/城市详情弹层（regional-5）：峰值/事件窗质量、压力族、AOD 修正、置信度「未提供」语义。</summary>
    private void ShowRegionalCellDetail(JsonObject row)
    {
        var detail = new StackPanel { Spacing = 5 };
        var city = Text(row, "name") is { Length: > 0 } name;
        detail.Children.Add(new TextBlock
        {
            Text = city ? Text(row, "name")
                : $"{Number(row, "lat"):0.00}°, {Number(row, "lon"):0.00}°",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        void Line(string label, string value)
        {
            var grid = new Grid { ColumnSpacing = 10 };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(120) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.Children.Add(new TextBlock { Text = label, FontSize = 11, Opacity = .65 });
            var text = new TextBlock { Text = value, FontSize = 11, IsTextSelectionEnabled = true };
            Grid.SetColumn(text, 1);
            grid.Children.Add(text);
            detail.Children.Add(grid);
        }
        var quality = DoubleValue(row["peak_quality_index"] ?? row["quality_index"]);
        Line("峰值质量", quality is { } value ? $"{value:0.000} / 2.5" : "未计算");
        Line("峰值档位", Text(row, "peak_quality_level") is { Length: > 0 } level ? level : "—");
        Line("最佳时刻", Text(row, "best_time_local") is { Length: > 0 } best ? TimeLabel(best) : "—");
        Line("事件窗质量", DoubleValue(row["event_window_quality"]) is { } window
            ? $"{window:0.000} / 2.5" : "未计算");
        Line("主导压力族", FamilyLabel(row["pressure_family"]) is { Length: > 0 } family
            ? family : "未提供");
        Line("高/中/低云",
            $"{PercentText(row["high_cloud_fraction"])} / {PercentText(row["mid_cloud_fraction"])} / {PercentText(row["low_cloud_fraction"])}");
        Line("AOD 修正", DoubleValue(row["aod550"]) is { } aod ? aod.ToString("0.000") : "未提供");
        Line("置信度", "未提供（区域格点不发布置信度）");
        if (Text(row, "reason") is { Length: > 0 } reason)
            detail.Children.Add(new TextBlock
            {
                Text = reason, FontSize = 11, Foreground = new SolidColorBrush(Colors.Orange),
                TextWrapping = TextWrapping.Wrap,
            });
        var open = new Button { Content = "在单点模式打开", Padding = new Thickness(10, 4, 10, 4) };
        open.Click += (_, _) => OpenRegionalCellInPointForecast(row);
        detail.Children.Add(open);
        RegionalProfileRows.Items.Clear();
        RegionalProfileRows.Items.Add(detail);
    }

    private static string PercentText(JsonNode? node) =>
        DoubleValue(node) is { } value ? $"{(int)Math.Round(value * 100)}%" : "—";

    /// <summary>「在单点模式打开」：区域网格 → 单点预填（对齐 macOS openRegionalCellInPointForecast）。</summary>
    private void OpenRegionalCellInPointForecast(JsonObject row)
    {
        suppressDraftEvents = true;
        try
        {
            if (Text(row, "name") is { Length: > 0 } name) LocationName.Text = name;
            else LocationName.Text = "地图选点";
            Latitude.Value = Number(row, "lat");
            Longitude.Value = Number(row, "lon");
            Timezone.Text = Text(row, "timezone_name") is { Length: > 0 } timezone
                ? timezone : Timezone.Text;
        }
        finally { suppressDraftEvents = false; }
        result = null;
        MetadataReady.IsChecked = false;
        NavigateTo("point");
        RenderResult();
        _ = UpdateMap();
        _ = LookupMetadata();
    }

    private async Task QueryRegionalProfile(JsonObject report, string model,
        double lat, double lon, string localTime)
    {
        profileOperation?.Cancel();
        profileOperation?.Dispose();
        profileOperation = new CancellationTokenSource();
        var token = profileOperation.Token;
        regionalProfile = null;
        regionalProfileState = "loading";
        RenderProfileSection();
        var request = BuildProfileQuery(report, model, lat, lon, localTime);
        if (request == null)
        {
            regionalProfileState = "unavailable";
            RegionalProfileStatus.Text = "该网格没有可读取的冻结剖面时次。";
            RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
            return;
        }
        try
        {
            var response = await engine.Call("profile_query", request.Payload, cancellation: token);
            token.ThrowIfCancellationRequested();
            if (!ProfileResponseMatches(response, request))
                throw new InvalidOperationException("剖面响应与当前结果、模式、坐标或时次不匹配。");
            if (!SameUtc(Text(response, "cycle_utc"), request.CycleUtc))
                throw new InvalidOperationException("剖面响应的模式周期与区域结果不匹配。");
            var status = Text(response, "status");
            if (status != "ready" || response["cross_section"] is not JsonObject section)
            {
                regionalProfileState = string.IsNullOrWhiteSpace(status) ? "unavailable" : status;
                RegionalProfileStatus.Text = string.IsNullOrWhiteSpace(Text(response, "reason"))
                    ? "所选时次没有可用剖面。" : Text(response, "reason");
                RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
                return;
            }
            regionalProfile = section;
            regionalProfileState = "ready";
            RenderProfileSection();
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            regionalProfileState = "cancelled";
            RegionalProfileStatus.Text = "剖面读取已取消。";
        }
        catch (Exception error)
        {
            regionalProfileState = "error";
            RegionalProfileStatus.Text = FriendlyMessages.Map(error.Message);
        }
    }

    private void RenderProfileSection()
    {
        RegionalProfileRows.Items.Clear();
        RegionalCrossSection.Visibility = Visibility.Collapsed;
        if (regionalProfileState == "loading")
        {
            RegionalProfileStatus.Text = "正在读取所选网格的冻结剖面…";
            RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
            return;
        }
        if (regionalProfileState != "ready" || regionalProfile == null)
        {
            if (regionalProfileState == "idle")
                RegionalProfileStatus.Text = "未选择网格。";
            RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
            return;
        }
        var fields = regionalProfile["fields"] as JsonObject;
        var distanceCount = (regionalProfile["distances_km"] as JsonArray)?.Count ?? 0;
        RegionalProfileStatus.Text =
            $"{Text(regionalProfile, "valid_time_local")}".Replace("T", " ")
            + $" · 太阳高度 {Number(regionalProfile, "solar_elevation_deg"):0.0}°"
            + $" · 太阳方位 {Number(regionalProfile, "solar_azimuth_deg"):0.0}°"
            + $" · {distanceCount} 个距离样本";
        var summary = new StackPanel { Spacing = 3, Padding = new Thickness(0, 4, 0, 0) };
        summary.Children.Add(new TextBlock
        {
            Text = $"云量样本 {NestedArrayCount(fields?["cloud_fraction_percent"])} · 温度样本 {NestedArrayCount(fields?["temperature_c"])}"
                   + $" · 湿度样本 {NestedArrayCount(fields?["relative_humidity_percent"])} · 消光样本 {NestedArrayCount(fields?["extinction_coefficient"])}",
            FontSize = 11, Opacity = .8,
        });
        RegionalProfileRows.Items.Add(summary);
        RegionalCrossSectionExpand.Visibility = Visibility.Visible;
        DrawCrossSection(RegionalCrossSection, regionalProfile);
    }

    private void OnToggleRegionalCrossSection(object sender, RoutedEventArgs args)
    {
        RegionalCrossSection.Visibility = RegionalCrossSection.Visibility == Visibility.Visible
            ? Visibility.Collapsed : Visibility.Visible;
    }

    private void ClearRegionalProfile()
    {
        profileOperation?.Cancel();
        profileOperation?.Dispose();
        profileOperation = null;
        regionalProfile = null;
        regionalProfileModel = "";
        regionalProfileState = "idle";
        regionalProfileLat = regionalProfileLon = 0;
        if (RegionalProfileSelection != null)
            RegionalProfileSelection.Text = "选择网格后读取冻结剖面";
        if (RegionalProfileStatus != null)
            RegionalProfileStatus.Text = "未选择网格";
        if (RegionalProfileRows != null)
            RegionalProfileRows.Items.Clear();
        if (RegionalCrossSection != null)
            RegionalCrossSection.Visibility = Visibility.Collapsed;
        if (RegionalCrossSectionExpand != null)
            RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
    }
}
