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
    private double regionalCrossSectionZoomScale = 1.0;

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
            RegionalGridSummary.Text = L10n.T("当前模式没有可用区域结果。");
            RegionalProfileStatus.Text = L10n.T("当前模式没有可读取的冻结剖面。");
            CollapseRegionalCrossSection();
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
                Text = L10n.T("暂无可用网格结果。"), Opacity = .65, Margin = new Thickness(4),
            });
        if (cities.Count == 0)
        {
            var reason = Text(cityReport, "reason");
            CityRankingRows.Items.Add(new TextBlock
            {
                Text = string.IsNullOrWhiteSpace(reason) ? L10n.T("该区域暂无城市排名。") : reason,
                Opacity = .65, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(4),
            });
        }
        RegionalProfileSelection.Text = string.IsNullOrWhiteSpace(regionalProfileModel)
            ? L10n.T("选择网格后读取冻结剖面")
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
            ? L10n.T("点击查看城市详情与冻结剖面")
            : L10n.T("点击查看网格详情与冻结剖面"));
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
            RegionalProfileStatus.Text = L10n.T("地图选点未命中当前结果网格。");
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
        regionalProfileModel = TagOf(RegionalModel?.SelectedItem) is { Length: > 0 } model
            && model is "gfs" or "ec" ? model : "gfs";
        RegionalProfileSelection.Text =
            $"{regionalProfileModel.ToUpperInvariant()} · {lat:0.00}°, {lon:0.00}°";
        ShowRegionalCellDetail(row);
        await QueryRegionalProfile(report, regionalProfileModel, lat, lon,
            Text(row, "best_time_local"));
    }

    private void OnRegionalModelChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized || restoringSession || suppressDraftEvents) return;
        var tag = TagOf(RegionalModel?.SelectedItem) is { Length: > 0 } selected ? selected : "gfs";
        SetSelectedResultModel(tag, render: false, updateMap: false);
        ClearRegionalProfile();
        RenderResult();
        _ = UpdateMap(forceFit: page == "regional");
        ScheduleSessionSave();
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
        Line(L10n.T("峰值质量"), quality is { } value ? $"{value:0.000} / 2.5" : L10n.T("未计算"));
        Line(L10n.T("峰值档位"), Text(row, "peak_quality_level") is { Length: > 0 } level ? level : "—");
        Line(L10n.T("最佳时刻"), Text(row, "best_time_local") is { Length: > 0 } best ? TimeLabel(best) : "—");
        Line(L10n.T("事件窗质量"), DoubleValue(row["event_window_quality"]) is { } window
            ? $"{window:0.000} / 2.5" : L10n.T("未计算"));
        Line(L10n.T("主导压力族"), FamilyLabel(row["pressure_family"]) is { Length: > 0 } family
            ? family : L10n.T("未提供"));
        Line(L10n.T("高/中/低云"),
            $"{PercentText(row["high_cloud_fraction"])} / {PercentText(row["mid_cloud_fraction"])} / {PercentText(row["low_cloud_fraction"])}");
        Line(L10n.T("AOD 修正"), DoubleValue(row["aod550"]) is { } aod ? aod.ToString("0.000") : L10n.T("未提供"));
        Line(L10n.T("置信度"), L10n.T("未提供（区域格点不发布置信度）"));
        if (Text(row, "reason") is { Length: > 0 } reason)
            detail.Children.Add(new TextBlock
            {
                Text = reason, FontSize = 11, Foreground = new SolidColorBrush(Colors.Orange),
                TextWrapping = TextWrapping.Wrap,
            });
        var open = new Button { Content = L10n.T("在单点模式打开"), Padding = new Thickness(10, 4, 10, 4) };
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
            else LocationName.Text = L10n.T("地图选点");
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
            RegionalProfileStatus.Text = L10n.T("该网格没有可读取的冻结剖面时次。");
            RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
            return;
        }
        try
        {
            var response = await engine.Call("profile_query", request.Payload, cancellation: token);
            token.ThrowIfCancellationRequested();
            if (!ProfileResponseMatches(response, request))
                throw new InvalidOperationException(L10n.T("剖面响应与当前结果、模式、坐标或时次不匹配。"));
            if (!SameUtc(Text(response, "cycle_utc"), request.CycleUtc))
                throw new InvalidOperationException(L10n.T("剖面响应的模式周期与区域结果不匹配。"));
            var status = Text(response, "status");
            if (status != "ready" || response["cross_section"] is not JsonObject section)
            {
                regionalProfileState = string.IsNullOrWhiteSpace(status) ? "unavailable" : status;
                RegionalProfileStatus.Text = string.IsNullOrWhiteSpace(Text(response, "reason"))
                    ? L10n.T("所选时次没有可用剖面。") : Text(response, "reason");
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
            RegionalProfileStatus.Text = L10n.T("剖面读取已取消。");
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
        CollapseRegionalCrossSection();
        if (regionalProfileState == "loading")
        {
            RegionalProfileStatus.Text = L10n.T("正在读取所选网格的冻结剖面…");
            return;
        }
        if (regionalProfileState != "ready" || regionalProfile == null)
        {
            if (regionalProfileState == "idle")
                RegionalProfileStatus.Text = L10n.T("未选择网格。");
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
        UpdateRegionalCrossSectionZoomControls();
        RegionalCrossSectionExpand.Visibility = Visibility.Visible;
        activeRegionalCrossSection = regionalProfile;
    }

    private void CollapseRegionalCrossSection()
    {
        activeRegionalCrossSection = null;
        if (RegionalCrossSectionScrollViewer != null)
            RegionalCrossSectionScrollViewer.Visibility = Visibility.Collapsed;
        if (RegionalCrossSection != null)
            RegionalCrossSection.Visibility = Visibility.Collapsed;
        if (RegionalProfileZoomControls != null)
            RegionalProfileZoomControls.Visibility = Visibility.Collapsed;
        if (RegionalCrossSectionExpand != null)
        {
            RegionalCrossSectionExpand.Content = L10n.T("展开剖面图层");
            RegionalCrossSectionExpand.Visibility = Visibility.Collapsed;
        }
    }

    private void OnToggleRegionalCrossSection(object sender, RoutedEventArgs args)
    {
        var expanded = RegionalCrossSectionScrollViewer.Visibility != Visibility.Visible;
        RegionalCrossSectionScrollViewer.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        RegionalCrossSection.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        RegionalProfileZoomControls.Visibility = expanded ? Visibility.Visible : Visibility.Collapsed;
        RegionalCrossSectionExpand.Content = L10n.T(expanded ? "收起剖面图层" : "展开剖面图层");
        if (expanded && activeRegionalCrossSection != null)
        {
            ResizeRegionalCrossSectionChart(resetScroll: true);
            DrawCrossSection(RegionalCrossSection, activeRegionalCrossSection);
        }
    }

    private void OnRegionalCrossSectionSizeChanged(object sender, SizeChangedEventArgs args)
    {
        if (RegionalCrossSectionScrollViewer.Visibility == Visibility.Visible && activeRegionalCrossSection != null)
        {
            ResizeRegionalCrossSectionChart();
            DrawCrossSection(RegionalCrossSection, activeRegionalCrossSection);
        }
    }

    private void OnRegionalProfileZoomOut(object sender, RoutedEventArgs args) =>
        SetRegionalCrossSectionZoom(regionalCrossSectionZoomScale - 0.25);

    private void OnRegionalProfileZoomIn(object sender, RoutedEventArgs args) =>
        SetRegionalCrossSectionZoom(regionalCrossSectionZoomScale + 0.25);

    private void OnRegionalProfileZoomReset(object sender, RoutedEventArgs args) =>
        SetRegionalCrossSectionZoom(1.0, resetScroll: true);

    private void SetRegionalCrossSectionZoom(double scale, bool resetScroll = false)
    {
        regionalCrossSectionZoomScale = Math.Clamp(Math.Round(scale * 4) / 4, 1.0, 2.5);
        UpdateRegionalCrossSectionZoomControls();
        ResizeRegionalCrossSectionChart(resetScroll || regionalCrossSectionZoomScale == 1.0);
        if (activeRegionalCrossSection != null
            && RegionalCrossSectionScrollViewer.Visibility == Visibility.Visible)
            DrawCrossSection(RegionalCrossSection, activeRegionalCrossSection);
    }

    private void UpdateRegionalCrossSectionZoomControls()
    {
        if (RegionalProfileZoomText == null) return;
        RegionalProfileZoomText.Text = $"{(int)Math.Round(regionalCrossSectionZoomScale * 100)}%";
        RegionalProfileZoomOutButton.IsEnabled = regionalCrossSectionZoomScale > 1.0;
        RegionalProfileZoomInButton.IsEnabled = regionalCrossSectionZoomScale < 2.5;
        RegionalProfileZoomResetButton.IsEnabled = regionalCrossSectionZoomScale > 1.0;
    }

    private void ResizeRegionalCrossSectionChart(bool resetScroll = false)
    {
        if (RegionalCrossSectionScrollViewer == null || RegionalCrossSection == null) return;
        var viewportWidth = RegionalCrossSectionScrollViewer.ActualWidth;
        var viewportHeight = RegionalCrossSectionScrollViewer.ActualHeight;
        if (viewportWidth <= 120 || viewportHeight <= 120) return;

        RegionalCrossSection.Width = Math.Max(220, viewportWidth * regionalCrossSectionZoomScale);
        RegionalCrossSection.Height = Math.Max(180, viewportHeight);
        if (resetScroll) RegionalCrossSectionScrollViewer.ChangeView(0, null, null, true);
    }

    private void ClearRegionalProfile()
    {
        profileOperation?.Cancel();
        profileOperation?.Dispose();
        profileOperation = null;
        regionalProfile = null;
        activeRegionalCrossSection = null;
        regionalProfileModel = "";
        regionalProfileState = "idle";
        regionalProfileLat = regionalProfileLon = 0;
        if (RegionalProfileSelection != null)
            RegionalProfileSelection.Text = L10n.T("选择网格后读取冻结剖面");
        if (RegionalProfileStatus != null)
            RegionalProfileStatus.Text = L10n.T("未选择网格");
        if (RegionalProfileRows != null)
            RegionalProfileRows.Items.Clear();
        regionalCrossSectionZoomScale = 1.0;
        UpdateRegionalCrossSectionZoomControls();
        CollapseRegionalCrossSection();
    }

    private void OnSetDefaultRegion(object sender, RoutedEventArgs args)
    {
        var codes = selectedRegions.OrderBy(v => v).ToList();
        if (codes.Count == 0)
        {
            Fail(new InvalidOperationException(L10n.T("请至少选择一个预设区域后再设为默认。")), "regional");
            return;
        }
        defaultRegionalPreset = codes;
        SaveSettings();
        Status.Text = L10n.T("已将当前区域设为默认");
    }

    private void OnResetDefaultRegion(object sender, RoutedEventArgs args)
    {
        selectedRegions.Clear();
        foreach (var code in defaultRegionalPreset) selectedRegions.Add(code);
        customRegion = false;
        suppressDraftEvents = true;
        try
        {
            CustomRegionToggle.IsChecked = false;
            CustomBoundsFields.Visibility = Visibility.Collapsed;
            RenderRegionPresetsChecks();
        }
        finally { suppressDraftEvents = false; }
        ClearError("regional");
        RenderResult();
        ScheduleSessionSave();
        _ = UpdateMap(forceFit: true);
    }

    private (double West, double South, double East, double North)? RegionalActiveBounds()
    {
        var areas = new List<(double W, double S, double E, double N)>();
        foreach (var region in regionDefinitions)
        {
            var code = Text(region, "code");
            if (!selectedRegions.Contains(code)) continue;
            if (region["bounds"] is JsonArray bounds && bounds.Count >= 4)
            {
                var w = DoubleValue(bounds[0]);
                var s = DoubleValue(bounds[1]);
                var e = DoubleValue(bounds[2]);
                var n = DoubleValue(bounds[3]);
                if (w is { } west && s is { } south && e is { } east && n is { } north && north > south)
                    areas.Add((west, south, east, north));
            }
        }
        if (areas.Count == 0 && (selectedRegions.Contains("china") || !customRegion))
        {
            // 引擎连接前立即按完整中国边界自动缩放（西 73°E，南 18°N，东 136°E，北 54°N）。
            areas.Add((73.0, 18.0, 136.0, 54.0));
        }
        if (customRegion && double.IsFinite(West.Value) && double.IsFinite(East.Value)
            && double.IsFinite(South.Value) && double.IsFinite(North.Value)
            && North.Value > South.Value && West.Value != East.Value)
        {
            areas.Add((West.Value, South.Value, East.Value, North.Value));
        }
        if (areas.Count == 0) return null;
        if (areas.Count == 1) return areas[0];
        // 若包含跨 180° 区域则取首个边界，否则取联合外包框。
        if (areas.Any(a => a.W > a.E)) return areas[0];
        return (
            areas.Min(a => a.W),
            areas.Min(a => a.S),
            areas.Max(a => a.E),
            areas.Max(a => a.N)
        );
    }

    /// <summary>
    /// WebView2 Leaflet 增强：切换至区域大图前刷新容器尺寸确保完整中国视野居正，
    /// 并支持双线性平滑插值热图图层（严格保持缺测透明且不遮挡网格点击）。
    /// </summary>
    private static string MapEnhancerScript() => """
        (() => {
          if (window.__fireSkyEnhanced) return;
          window.__fireSkyEnhanced = true;
          const origUpdate = window.fireSky && window.fireSky.update;
          if (!origUpdate) return;
          let prevRegional = null;
          function buildSmoothOverlay(points) {
            const valid = [];
            let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
            for (const p of points) {
              if (!Number.isFinite(p.lat) || !Number.isFinite(p.lon)) continue;
              const v = p.peak_quality_index ?? p.quality_index;
              if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) continue;
              valid.push({ lat: p.lat, lon: p.lon, q: v });
              if (p.lat < minLat) minLat = p.lat;
              if (p.lat > maxLat) maxLat = p.lat;
              if (p.lon < minLon) minLon = p.lon;
              if (p.lon > maxLon) maxLon = p.lon;
            }
            if (valid.length === 0 || maxLon - minLon > 350) return null;
            const step = 0.25;
            const cols = Math.round((maxLon - minLon) / step) + 1;
            const rows = Math.round((maxLat - minLat) / step) + 1;
            if (cols < 1 || rows < 1 || cols * rows > 400000) return null;
            const g = new Float64Array(rows * cols);
            g.fill(NaN);
            for (const item of valid) {
              const c = Math.round((item.lon - minLon) / step);
              const r = Math.round((item.lat - minLat) / step);
              if (c >= 0 && c < cols && r >= 0 && r < rows) g[r * cols + c] = item.q;
            }
            const west = Math.max(-180, minLon - step / 2);
            const east = Math.min(180, maxLon + step / 2);
            const south = Math.max(-85, minLat - step / 2);
            const north = Math.min(85, maxLat + step / 2);
            const mercY = lat => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360));
            const invMercY = y => (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI;
            const yMin = mercY(south), yMax = mercY(north);
            const rawW = Math.max(cols * 3, 32), rawH = Math.max(rows * 3, 32), maxDim = 900;
            const scale = Math.max(1, rawW / maxDim, rawH / maxDim);
            const w = Math.max(16, Math.round(rawW / scale));
            const h = Math.max(16, Math.round(rawH / scale));
            const canvas = document.createElement('canvas');
            canvas.width = w; canvas.height = h;
            const ctx = canvas.getContext('2d');
            const img = ctx.createImageData(w, h);
            const data = img.data;
            for (let py = 0; py < h; py++) {
              const fracY = (py + 0.5) / h;
              const lat = invMercY(yMax - fracY * (yMax - yMin));
              const rFloat = (lat - minLat) / step;
              const rNear = Math.round(rFloat);
              if (rNear < 0 || rNear >= rows) continue;
              const r0 = Math.max(0, Math.min(rows - 1, Math.floor(rFloat)));
              const r1 = Math.max(0, Math.min(rows - 1, r0 + 1));
              const ty = Math.max(0, Math.min(1, rFloat - r0));
              for (let px = 0; px < w; px++) {
                const lon = west + ((px + 0.5) / w) * (east - west);
                const cFloat = (lon - minLon) / step;
                const cNear = Math.round(cFloat);
                if (cNear < 0 || cNear >= cols || Number.isNaN(g[rNear * cols + cNear])) continue;
                const c0 = Math.max(0, Math.min(cols - 1, Math.floor(cFloat)));
                const c1 = Math.max(0, Math.min(cols - 1, c0 + 1));
                const tx = Math.max(0, Math.min(1, cFloat - c0));
                const s00 = g[r0 * cols + c0], s01 = g[r0 * cols + c1];
                const s10 = g[r1 * cols + c0], s11 = g[r1 * cols + c1];
                let sum = 0, wt = 0;
                const w00 = (1 - tx) * (1 - ty), w01 = tx * (1 - ty), w10 = (1 - tx) * ty, w11 = tx * ty;
                if (!Number.isNaN(s00) && w00 > 0) { sum += s00 * w00; wt += w00; }
                if (!Number.isNaN(s01) && w01 > 0) { sum += s01 * w01; wt += w01; }
                if (!Number.isNaN(s10) && w10 > 0) { sum += s10 * w10; wt += w10; }
                if (!Number.isNaN(s11) && w11 > 0) { sum += s11 * w11; wt += w11; }
                if (wt <= 0) continue;
                const q = sum / wt;
                const t = Math.min(1, Math.max(0, q / 2.5));
                let rgb = [201, 60, 145];
                for (let i = 1; i < STOPS.length; i++) {
                  if (t <= STOPS[i][0]) {
                    const [l0, a] = STOPS[i - 1], [r0s, b] = STOPS[i];
                    const f = (t - l0) / (r0s - l0);
                    rgb = [Math.round(a[0] + (b[0] - a[0]) * f), Math.round(a[1] + (b[1] - a[1]) * f), Math.round(a[2] + (b[2] - a[2]) * f)];
                    break;
                  }
                }
                const alpha = Math.round((t <= 0.001 ? 12 : (42 + 190 * Math.sqrt(t))) * 0.9);
                const idx = (py * w + px) * 4;
                data[idx] = rgb[0]; data[idx + 1] = rgb[1]; data[idx + 2] = rgb[2]; data[idx + 3] = alpha;
              }
            }
            ctx.putImageData(img, 0, 0);
            return L.imageOverlay(canvas.toDataURL('image/png'), [[south, west], [north, east]], { interactive: false });
          }
          window.fireSky.update = function(state) {
            const legend = document.querySelector('.legend');
            if (legend) legend.style.display = state.regional ? 'block' : 'none';
            const mapEl = document.getElementById('map');
            if (mapEl) {
              mapEl.style.background = state.dark ? '#111826' : '#EEF3F8';
              const tiles = mapEl.querySelector('.leaflet-tile-pane');
              if (tiles) tiles.style.filter = state.dark ? 'invert(0.92) hue-rotate(180deg) brightness(0.92) contrast(0.92)' : '';
            }
            const switchedToRegional = typeof map !== 'undefined' && prevRegional !== !!state.regional;
            if (switchedToRegional) {
              prevRegional = !!state.regional;
              map.invalidateSize(false);
              lastKey = '';
            }
            if (state.force_fit) lastKey = '';
            if (state.regional && state.show_smooth && Array.isArray(state.points) && state.points.length > 0) {
              const pts = state.points;
              const overlay = buildSmoothOverlay(pts);
              if (overlay) {
                const missingOnly = pts.filter(p => {
                  const v = p.peak_quality_index ?? p.quality_index;
                  return !(typeof v === 'number' && Number.isFinite(v) && v >= 0);
                });
                const patched = Object.assign({}, state, { points: missingOnly });
                origUpdate(patched);
                overlay.addTo(grid);
                if (state.show_grid !== false && Number.isFinite(state.west) && Number.isFinite(state.east)
                    && Number.isFinite(state.south) && Number.isFinite(state.north) && state.north > state.south) {
                  gridLines(state.west, state.south, state.east, state.north).addTo(grid);
                }
                if ((switchedToRegional || state.force_fit) && Number.isFinite(state.west) && Number.isFinite(state.east)
                    && Number.isFinite(state.south) && Number.isFinite(state.north) && state.north > state.south) {
                  setTimeout(() => { map.invalidateSize(false); fitWrapped(state.south, state.west, state.north, state.east); }, 80);
                }
                return;
              }
            }
            origUpdate(state);
            if (state.regional && state.show_grid !== false && Number.isFinite(state.west) && Number.isFinite(state.east)
                && Number.isFinite(state.south) && Number.isFinite(state.north) && state.north > state.south) {
              gridLines(state.west, state.south, state.east, state.north).addTo(grid);
            }
            if ((switchedToRegional || state.force_fit) && state.regional && Number.isFinite(state.west) && Number.isFinite(state.east)
                && Number.isFinite(state.south) && Number.isFinite(state.north) && state.north > state.south) {
              setTimeout(() => { map.invalidateSize(false); fitWrapped(state.south, state.west, state.north, state.east); }, 80);
            }
          };
        })();
        """;
}
