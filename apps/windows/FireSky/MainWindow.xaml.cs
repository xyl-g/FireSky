using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.Web.WebView2.Core;
using Windows.Devices.Geolocation;

namespace FireSky;

public sealed partial class MainWindow : Window
{
    private readonly EngineClient engine = new();
    private CancellationTokenSource? operation;
    private CancellationTokenSource? profileOperation;
    private bool busy, initialized, mapReady, online;
    private string page = "point", resultKind = "";
    private JsonObject? result;
    private JsonObject? regionalProfile;
    private string regionalProfileModel = "";
    private string regionalProfileState = "idle";
    private double regionalProfileLat, regionalProfileLon;
    private readonly string settingsFile = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FireSky", "settings.json");
    private int locationRevision;
    private readonly Dictionary<string, TextBlock> metricValues = new();
    private static readonly (string Key, string Title, string Caption)[] Metrics =
    [
        ("burnable", "有效可烧云量", "前向可烧云占视野比例"),
        ("material", "云质厚度", "云体材质负载"),
        ("brightness", "亮度色彩", "霞光亮度与色彩评分"),
        ("aod", "AOD 550", "气溶胶光学厚度 · 通透度"),
        ("incoming", "入射受光", "太阳入射照亮云体的因子"),
        ("contrast", "返程对比度", "观测者视线大气对比因子"),
        ("duration", "持续时长", "核心观赏窗口 · 分钟"),
        ("confidence", "置信度", "引擎结果置信评分"),
        ("disagreement", "模式分歧", "多模式结论分歧度"),
    ];

    public MainWindow()
    {
        InitializeComponent();
        SystemBackdrop = new MicaBackdrop();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TitleBar);
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1440, 900));
        BuildMetrics();
        TargetDate.Date = DateTimeOffset.Now;
        var root = Environment.GetEnvironmentVariable("FIRESKY_WORKSPACE") ?? "";
        JsonObject saved = new();
        try { saved = JsonNode.Parse(File.ReadAllText(settingsFile)) as JsonObject ?? new(); }
        catch (Exception) { }
        WorkspacePath.Text = root.Length > 0 ? root : Text(saved, "workspace");
        EnginePath.Text = Environment.GetEnvironmentVariable("FIRESKY_RUST_ENGINE_BIN")
            ?? Text(saved, "engineBinary");
        Appearance.SelectedIndex = Text(saved, "appearance") switch { "light" => 1, "dark" => 2, _ => 0 };
        Navigation.SelectedItem = Navigation.MenuItems[0];
        initialized = true;
        Root.Loaded += async (_, _) =>
        {
            await InitializeMap();
            await Connect();
        };
        Closed += (_, _) => { operation?.Cancel(); profileOperation?.Cancel(); engine.Dispose(); };
    }

    private static string Text(JsonNode? node, string key) => node?[key]?.GetValue<string>() ?? "";
    private static bool Flag(JsonNode? node, string key) => node?[key]?.GetValue<bool>() ?? false;
    private static double Number(JsonNode? node, string key) => node?[key]?.GetValue<double>() ?? 0;
    private static long? Integer(JsonNode? node, string key)
    {
        if (node?[key] is not JsonValue value) return null;
        try
        {
            var number = value.GetValue<double>();
            if (!double.IsFinite(number) || number != Math.Truncate(number)
                || number < long.MinValue || number > long.MaxValue) return null;
            return (long)number;
        }
        catch (InvalidOperationException) { return null; }
    }

    private static double Score(JsonNode? node)
    {
        var value = node?["peak_quality_index"] ?? node?["quality_index"];
        try
        {
            var score = value?.GetValue<double>() ?? double.NaN;
            return double.IsFinite(score) ? score : -1;
        }
        catch (InvalidOperationException) { return -1; }
    }

    private static bool IsHex64(string value) =>
        value.Length == 64 && value.All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f');

    private static bool TryUtc(string value, out string normalized)
    {
        if (DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture,
                DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeUniversal |
                DateTimeStyles.AdjustToUniversal, out var parsed))
        {
            normalized = parsed.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'",
                CultureInfo.InvariantCulture);
            return true;
        }
        normalized = "";
        return false;
    }

    private static bool SameUtc(string left, string right)
    {
        if (!DateTimeOffset.TryParse(left, CultureInfo.InvariantCulture,
                DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeUniversal |
                DateTimeStyles.AdjustToUniversal, out var a)
            || !DateTimeOffset.TryParse(right, CultureInfo.InvariantCulture,
                DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeUniversal |
                DateTimeStyles.AdjustToUniversal, out var b))
            return false;
        return Math.Abs((a - b).TotalSeconds) <= 0.000001;
    }

    private void SetBusy(bool value)
    {
        busy = value;
        Inputs.IsEnabled = !value;
        Navigation.IsEnabled = !value;
        Navigation.IsPaneToggleButtonVisible = !value;
        PredictButton.IsEnabled = !value && online;
        SaveRetentionButton.IsEnabled = !value && online;
        BusyRing.IsActive = value;
        CancelButton.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
    }

    private void Fail(Exception error)
    {
        ErrorBar.Message = error.Message;
        ErrorBar.IsOpen = true;
        Status.Text = error is OperationCanceledException ? "已取消" : "操作未完成";
        SettingsStatus.Text = error.Message;
    }

    private async Task Connect()
    {
        if (busy) return;
        online = false;
        SetBusy(true);
        try
        {
            engine.Start(WorkspacePath.Text, EnginePath.Text);
            var hello = await engine.Call("hello");
            if (Number(hello, "protocol") != 1) throw new InvalidOperationException("引擎协议版本不匹配。");
            var capabilities = hello["capabilities"] as JsonArray ?? new();
            foreach (var required in new[] { "profile_query", "task_history", "task_events", "result_restore" })
            {
                if (!capabilities.Any(item => string.Equals(item?.GetValue<string>(), required,
                        StringComparison.Ordinal)))
                    throw new InvalidOperationException($"Rust 引擎缺少 {required} 能力。");
            }
            online = true;
            ErrorBar.IsOpen = false;
            Status.Text = "本地引擎已连接";
            SettingsStatus.Text = "已连接";
            await LoadRetentionSettings();
            SaveSettings();
        }
        catch (Exception error) { Fail(error); }
        finally { SetBusy(false); }
    }

    private void SaveSettings()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(settingsFile)!);
        File.WriteAllText(settingsFile, new JsonObject
        {
            ["workspace"] = WorkspacePath.Text, ["engineBinary"] = EnginePath.Text,
            ["appearance"] = (Appearance.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "system"
        }.ToJsonString());
    }

    private async Task LoadRetentionSettings()
    {
        if (!online) return;
        try
        {
            var response = await engine.Call("retention_settings");
            var settings = response["settings"] as JsonObject ?? new();
            RetentionGfs.Text = RetentionText(settings["gfs_cycles"]);
            RetentionEcmwf.Text = RetentionText(settings["ecmwf_cycles"]);
            RetentionCams.Text = RetentionText(settings["cams_cycles"]);
            RetentionSatelliteDays.Text = RetentionText(settings["satellite_days"]);
            RetentionReportDays.Text = RetentionText(settings["report_days"]);
            RetentionMapDays.Text = RetentionText(settings["map_days"]);
            var configured = response["configured"]?.GetValue<bool>() == true;
            RetentionStatus.Text = configured ? "已启用自定义保留策略。" : "未启用自定义保留策略。";
        }
        catch (Exception error)
        {
            RetentionStatus.Text = error.Message;
        }
    }

    private async void OnSaveRetention(object sender, RoutedEventArgs args)
    {
        if (!online || busy) return;
        try
        {
            var settings = new JsonObject
            {
                ["gfs_cycles"] = RetentionInteger(RetentionGfs.Text, "GFS 周期"),
                ["ecmwf_cycles"] = RetentionInteger(RetentionEcmwf.Text, "EC 周期"),
                ["cams_cycles"] = RetentionInteger(RetentionCams.Text, "CAMS 周期"),
                ["satellite_days"] = RetentionNumber(RetentionSatelliteDays.Text, "卫星数据"),
                ["report_days"] = RetentionNumber(RetentionReportDays.Text, "历史报告"),
                ["map_days"] = RetentionNumber(RetentionMapDays.Text, "报告地图"),
            };
            SaveRetentionButton.IsEnabled = false;
            var response = await engine.Call("save_retention_settings",
                new JsonObject { ["settings"] = settings });
            var saved = response["settings"] as JsonObject ?? new();
            RetentionGfs.Text = RetentionText(saved["gfs_cycles"]);
            RetentionEcmwf.Text = RetentionText(saved["ecmwf_cycles"]);
            RetentionCams.Text = RetentionText(saved["cams_cycles"]);
            RetentionSatelliteDays.Text = RetentionText(saved["satellite_days"]);
            RetentionReportDays.Text = RetentionText(saved["report_days"]);
            RetentionMapDays.Text = RetentionText(saved["map_days"]);
            RetentionStatus.Text = response["configured"]?.GetValue<bool>() == true
                ? "保留设置已保存；下一次成功准备后执行清理。" : "已清空保留设置。";
            ErrorBar.IsOpen = false;
        }
        catch (Exception error)
        {
            RetentionStatus.Text = error.Message;
            Fail(error);
        }
        finally
        {
            SaveRetentionButton.IsEnabled = !busy && online;
        }
    }

    private static JsonNode? RetentionInteger(string value, string label)
    {
        var trimmed = value.Trim();
        if (trimmed.Length == 0) return null;
        if (!int.TryParse(trimmed, NumberStyles.Integer, CultureInfo.InvariantCulture, out var number)
            || number < 1 || number > 10_000)
            throw new InvalidOperationException($"{label}必须是 1 到 10000 的整数，留空表示不限制。");
        return JsonValue.Create(number);
    }

    private static JsonNode? RetentionNumber(string value, string label)
    {
        var trimmed = value.Trim();
        if (trimmed.Length == 0) return null;
        if (!double.TryParse(trimmed, NumberStyles.Float, CultureInfo.InvariantCulture, out var number)
            || !double.IsFinite(number) || number < 0 || number > 3650)
            throw new InvalidOperationException($"{label}必须是 0 到 3650 的数字，留空表示不限制。");
        return JsonValue.Create(number);
    }

    private static string RetentionText(JsonNode? value)
    {
        if (value == null) return "";
        var number = value.GetValue<double>();
        return Math.Abs(number - Math.Round(number)) < 0.000001
            ? ((long)Math.Round(number)).ToString(CultureInfo.InvariantCulture)
            : number.ToString("0.##", CultureInfo.InvariantCulture);
    }

    private void OnNavigationChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (busy) return;
        page = args.IsSettingsSelected ? "settings"
            : (args.SelectedItem as NavigationViewItem)?.Tag?.ToString() ?? "point";
        if (ForecastPage == null) return;
        ForecastPage.Visibility = page is "point" or "regional" or "nowcast" ? Visibility.Visible : Visibility.Collapsed;
        DataPage.Visibility = page == "data" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility = page == "settings" ? Visibility.Visible : Visibility.Collapsed;
        PageTitle.Text = page == "regional" ? "预测区域" : "观测点";
        PointFields.Visibility = page == "regional" ? Visibility.Collapsed : Visibility.Visible;
        RegionFields.Visibility = page == "regional" ? Visibility.Visible : Visibility.Collapsed;
        SourcesLabel.Text = page == "nowcast" ? "GFS · CAMS · 卫星帧" : "GFS + EC";
        PredictButton.Content = page == "nowcast" ? "检查并预热" : "开始预测";
        if (page == "regional")
        {
            MiniMapHost.Child = null;
            RegionalMapHost.Child = Map;
            RegionalMapHost.Visibility = Visibility.Visible;
            RegionalInspector.Visibility = Visibility.Visible;
            MiniMapHost.Visibility = Visibility.Collapsed;
            ResultTabs.Visibility = Visibility.Collapsed;
        }
        else
        {
            RegionalMapHost.Child = null;
            MiniMapHost.Child = Map;
            RegionalMapHost.Visibility = Visibility.Collapsed;
            RegionalInspector.Visibility = Visibility.Collapsed;
            MiniMapHost.Visibility = Visibility.Visible;
            ResultTabs.Visibility = Visibility.Visible;
        }
        RenderResult();
        _ = UpdateMap();
        if (page == "data") _ = LoadInventory();
        if (page == "settings") _ = LoadRetentionSettings();
    }

    private async Task InitializeMap()
    {
        try
        {
            await Map.EnsureCoreWebView2Async();
            Map.CoreWebView2.SetVirtualHostNameToFolderMapping("firesky.local",
                Path.Combine(AppContext.BaseDirectory, "Assets", "Map"),
                CoreWebView2HostResourceAccessKind.DenyCors);
            Map.CoreWebView2.Settings.AreDevToolsEnabled = false;
            Map.CoreWebView2.Settings.IsStatusBarEnabled = false;
            Map.CoreWebView2.NavigationStarting += (_, e) =>
            {
                if (!e.Uri.StartsWith("https://firesky.local/", StringComparison.OrdinalIgnoreCase)) e.Cancel = true;
            };
            Map.CoreWebView2.NewWindowRequested += (_, e) => e.Handled = true;
            Map.CoreWebView2.WebMessageReceived += async (_, e) =>
            {
                if (!e.Source.StartsWith("https://firesky.local/", StringComparison.OrdinalIgnoreCase)) return;
                try
                {
                    var message = JsonNode.Parse(e.WebMessageAsJson);
                    if (Text(message, "type") == "ready")
                    {
                        mapReady = true;
                        await UpdateMap();
                    }
                    else if (Text(message, "type") == "point" && !busy)
                    {
                        var lat = Number(message, "lat"); var lon = Number(message, "lon");
                        if (!double.IsFinite(lat) || !double.IsFinite(lon) || Math.Abs(lat) > 90 || Math.Abs(lon) > 180) return;
                        if (page == "regional")
                        {
                            if (result is not null && resultKind == "regional")
                            {
                                await FocusRegionalPoint(lat, lon);
                            }
                            else
                            {
                                lat = Math.Round(lat * 4) / 4; lon = Math.Round(lon * 4) / 4;
                                West.Value = Math.Max(-180, lon - .5); East.Value = Math.Min(180, lon + .5);
                                South.Value = Math.Max(-89, lat - .5); North.Value = Math.Min(89, lat + .5);
                                result = null; RenderResult(); await UpdateMap();
                            }
                        }
                        else
                        {
                            Latitude.Value = lat; Longitude.Value = lon; LocationName.Text = "地图选点";
                            await LookupMetadata();
                        }
                    }
                }
                catch (Exception error) { Fail(error); }
            };
            Map.Source = new Uri("https://firesky.local/map.html");
        }
        catch (Exception error) { Fail(error); }
    }

    private async Task UpdateMap()
    {
        if (!initialized || !mapReady || !double.IsFinite(Latitude.Value) || !double.IsFinite(Longitude.Value)) return;
        var report = SelectedReport();
        var state = new JsonObject
        {
            ["lat"] = Latitude.Value, ["lon"] = Longitude.Value, ["regional"] = page == "regional",
            ["west"] = West.Value, ["east"] = East.Value, ["south"] = South.Value, ["north"] = North.Value,
            ["points"] = resultKind == page ? report?["points"]?.DeepClone() : null,
        };
        try { await Map.ExecuteScriptAsync("window.fireSky.update(" + state.ToJsonString() + ")"); }
        catch (Exception error) { Fail(error); }
    }

    private async void OnSearch(AutoSuggestBox sender, AutoSuggestBoxQuerySubmittedEventArgs args)
    {
        if (busy || !online || string.IsNullOrWhiteSpace(args.QueryText)) return;
        SetBusy(true);
        operation = new();
        try
        {
            var point = await engine.Call("search", new() { ["query"] = args.QueryText }, cancellation: operation.Token);
            Latitude.Value = Number(point, "lat"); Longitude.Value = Number(point, "lon");
            LocationName.Text = Text(point, "name"); Elevation.Value = Number(point, "elevation_m");
            Timezone.Text = Text(point, "timezone_name"); MetadataReady.IsChecked = true;
            result = null; RenderResult(); await UpdateMap();
            Status.Text = "位置已就绪";
        }
        catch (Exception error) { Fail(error); }
        finally { SetBusy(false); }
    }

    private async void OnLocate(object sender, RoutedEventArgs args)
    {
        if (busy) return;
        SetBusy(true);
        Status.Text = "正在获取当前位置";
        try
        {
            var access = await Geolocator.RequestAccessAsync();
            if (access != GeolocationAccessStatus.Allowed) throw new InvalidOperationException("定位未获授权，可在地图上选点。");
            var locator = new Geolocator { DesiredAccuracyInMeters = 100 };
            var point = await locator.GetGeopositionAsync(TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(20));
            if (point.Coordinate.Accuracy > 5000 || DateTimeOffset.Now - point.Coordinate.Timestamp > TimeSpan.FromMinutes(2))
                throw new InvalidOperationException("定位精度不足或位置已过期。");
            Latitude.Value = point.Coordinate.Point.Position.Latitude;
            Longitude.Value = point.Coordinate.Point.Position.Longitude;
            LocationName.Text = "当前位置";
            await LookupMetadata();
        }
        catch (Exception error) { Fail(error); }
        finally { SetBusy(false); }
    }

    private async Task LookupMetadata()
    {
        if (!online) return;
        var revision = ++locationRevision;
        var a = Latitude.Value; var b = Longitude.Value;
        MetadataReady.IsChecked = false;
        Status.Text = "正在获取海拔与时区";
        try
        {
            var response = await engine.Call("metadata", new() { ["lat"] = a, ["lon"] = b });
            if (revision != locationRevision || a != Latitude.Value || b != Longitude.Value) return;
            Elevation.Value = Number(response, "elevation_m");
            Timezone.Text = Text(response, "timezone_name");
            MetadataReady.IsChecked = true; Status.Text = "位置已就绪";
            result = null; RenderResult(); await UpdateMap();
        }
        catch (Exception error) { if (revision == locationRevision) Fail(error); }
    }

    private async void OnMetadata(object sender, RoutedEventArgs args) => await LookupMetadata();
    private void OnCoordinatesChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!initialized) return;
        locationRevision++;
        MetadataReady.IsChecked = false;
        result = null; ClearRegionalProfile(); RenderResult(); _ = UpdateMap();
    }
    private void OnBoundsChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!initialized) return;
        result = null; ClearRegionalProfile(); RenderResult(); _ = UpdateMap();
    }

    private JsonObject Payload()
    {
        if (TargetDate.Date == null) throw new InvalidOperationException("请选择日期。");
        if (page != "regional" && MetadataReady.IsChecked != true)
            throw new InvalidOperationException("请获取海拔与时区，或手动确认位置数据。");
        var validRegionalLongitudes = double.IsFinite(West.Value) && double.IsFinite(East.Value)
            && West.Value >= -180 && West.Value <= 180
            && East.Value >= -180 && East.Value <= 180
            && West.Value != East.Value
            && !(West.Value == 180 && East.Value == -180);
        if (page == "regional" &&
            (!validRegionalLongitudes || !double.IsFinite(South.Value) || !double.IsFinite(North.Value)
             || South.Value < -90 || South.Value > 90
             || North.Value < -90 || North.Value > 90
             || North.Value <= South.Value))
            throw new InvalidOperationException("区域范围无效。");
        var payload = new JsonObject
        {
            ["name"] = LocationName.Text, ["lat"] = Latitude.Value, ["lon"] = Longitude.Value,
            ["elevation_m"] = Elevation.Value, ["timezone_name"] = Timezone.Text,
            ["target_date"] = TargetDate.Date.Value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            ["phenomenon"] = (Phenomenon.SelectedItem as ComboBoxItem)?.Tag?.ToString(),
            ["model_source"] = "both", ["workers"] = 1,
            ["forecast_mode"] = "model", ["satellite_source"] = "off",
        };
        if (page == "nowcast")
        {
            payload["model_source"] = "satellite";
            payload["forecast_mode"] = "nowcast";
            payload["satellite_source"] = "auto";
            payload["radius_km"] = 900.0;
        }
        if (page == "regional")
        {
            payload["regions"] = new JsonArray("custom");
            payload["custom_bounds"] = new JsonObject
            {
                ["west"] = West.Value, ["east"] = East.Value, ["south"] = South.Value, ["north"] = North.Value,
            };
            payload["custom_timezone_name"] = Timezone.Text;
            payload["spacing_deg"] = .25;
        }
        return payload;
    }

    private async void OnPredict(object sender, RoutedEventArgs args)
    {
        if (busy || !online) return;
        JsonObject payload;
        try { payload = Payload(); } catch (Exception error) { Fail(error); return; }
        var kind = page;
        var dialog = new PreflightDialog(engine, kind, payload) { XamlRoot = Root.XamlRoot };
        await dialog.ShowAsync();
        if (dialog.ConfirmedSession is not { } token) return;
        SetBusy(true);
        operation = new();
        ErrorBar.IsOpen = false;
        Status.Text = "正在预测";
        try
        {
            var response = await engine.Call("forecast", new() { ["session"] = token, ["confirmed"] = true },
                progress: e => DispatcherQueue.TryEnqueue(() =>
                    Status.Text = $"正在计算 {Text(e, "source")} · {Number(e, "percent"):0}%"),
                cancellation: operation.Token);
            operation.Token.ThrowIfCancellationRequested();
            result = response;
            resultKind = kind;
            if (result["model_results"]?["gfs"] == null && result["model_results"]?["ec"] != null)
                ResultModel.SelectedIndex = 1;
            Status.Text = "预测完成"; RenderResult(); await UpdateMap();
        }
        catch (Exception error) { Fail(error); }
        finally { SetBusy(false); }
    }

    private JsonObject? SelectedReport()
    {
        var model = (ResultModel?.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "gfs";
        return result?["model_results"] is JsonObject models ? models[model] as JsonObject : result;
    }

    private void RenderResult()
    {
        if (ResultPanel == null) return;
        ResultPanel.Visibility = Visibility.Visible;
        var current = result != null && resultKind == page;
        ResultContext.Text = $"{LocationName.Text} · {TargetDate.Date:yyyy-MM-dd} · {((Phenomenon.SelectedItem as ComboBoxItem)?.Content)}";
        ResultModel.Visibility = page == "nowcast" ? Visibility.Collapsed : Visibility.Visible;
        if (!current)
        {
            RegionalInspector.Visibility = Visibility.Collapsed;
            RegionalGridRows.Items.Clear();
            CityRankingRows.Items.Clear();
            ClearRegionalProfile();
            ResultScore.Text = "--"; ResultLevel.Text = "等待预测"; ResultSummary.Text = "峰值质量指数";
            BestTime.Text = "--"; BestWindow.Text = "推荐窗口 --";
            foreach (var value in metricValues.Values) value.Text = "--";
            QualityChart.Children.Clear(); ChartEmpty.Visibility = Visibility.Visible;
            LedgerRows.Items.Clear(); return;
        }
        RegionalInspector.Visibility = resultKind == "regional"
            ? Visibility.Visible : Visibility.Collapsed;
        ResultTabs.Visibility = resultKind == "regional"
            ? Visibility.Collapsed : Visibility.Visible;
        ResultModel.Visibility = resultKind == "nowcast" ? Visibility.Collapsed : Visibility.Visible;
        var model = (ResultModel.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "gfs";
        if (result["model_errors"]?[model] is JsonValue error)
        {
            ResultScore.Text = "未完成"; ResultSummary.Text = error.GetValue<string>(); return;
        }
        var report = SelectedReport();
        if (resultKind == "regional")
            RenderRegionalInspector(report);
        ResultScore.Text = resultKind == "regional" ? $"{(report?["points"] as JsonArray)?.Count ?? 0} 个网格点"
            : (report?["peak_quality_index"] ?? report?["quality_index"]) is { } quality ? quality.GetValue<double>().ToString("0.00") : "--";
        ResultSummary.Text = resultKind == "regional"
            ? string.Join("   ", (report?["points"] as JsonArray ?? new()).Take(6)
                .Select(p => $"{Number(p, "lat"):0.00}, {Number(p, "lon"):0.00}: {Number(p, "peak_quality_index"):0.00}"))
            : Text(report, "summary");
        ResultLevel.Text = Text(report, "quality_level");
        BestTime.Text = TimeLabel(Text(report, "best_time_local"));
        BestWindow.Text = $"推荐窗口 {TimeLabel(Text(report?["timing_window"], "start_time_local"))} – {TimeLabel(Text(report?["timing_window"], "end_time_local"))}";
        var components = report?["quality_components"];
        foreach (var (key, target) in metricValues)
        {
            JsonNode? value = key switch
            {
                "burnable" => components?["burnable_cloud_coverage_effective"] ?? report?["burnable_cloud_coverage_effective"],
                "material" => components?["cloud_material"],
                "brightness" => components?["brightness_color"],
                "aod" => components?["quality_aod550"] ?? report?["quality_aod550"],
                "incoming" => components?["incoming_aod_illumination_factor"],
                "contrast" => components?["observer_aod_contrast_factor"],
                "duration" => report?["duration_minutes"] ?? components?["duration_minutes"],
                "confidence" => report?["confidence_score"],
                _ => report?["model_disagreement_score"],
            };
            target.Text = value == null ? "--" : key is "burnable" or "confidence"
                ? (value.GetValue<double>() * 100).ToString("0") + "%" : value.GetValue<double>().ToString("0.00");
        }
        DiagnosticText.Text = Text(report, "summary");
        ProfileStatus.Text = "周期 " + Text(report, "cycle_time_utc");
        LedgerRows.Items.Clear();
        foreach (var step in report?["steps"] as JsonArray ?? new())
            LedgerRows.Items.Add($"{TimeLabel(Text(step, "valid_time_local"))}     {Number(step, "instant_quality_index"):0.000}     {Number(step, "solar_elevation_deg"):0.00}°");
        RenderChart();
    }

    private void RenderRegionalInspector(JsonObject? report)
    {
        RegionalGridRows.Items.Clear();
        CityRankingRows.Items.Clear();
        if (report == null)
        {
            RegionalGridSummary.Text = "当前模式没有可用区域结果。";
            RegionalProfileStatus.Text = "当前模式没有可读取的冻结剖面。";
            return;
        }

        var points = (report["points"] as JsonArray ?? new())
            .OfType<JsonObject>()
            .Where(row => double.IsFinite(Number(row, "lat")) && double.IsFinite(Number(row, "lon")))
            .OrderByDescending(row => Score(row))
            .ThenByDescending(row => Number(row, "lat"))
            .ThenBy(row => Number(row, "lon"))
            .ToList();
        var cityReport = report["city_ranking"] as JsonObject;
        var cities = (cityReport?["items"] as JsonArray ?? new())
            .OfType<JsonObject>()
            .Where(row => double.IsFinite(Number(row, "lat")) && double.IsFinite(Number(row, "lon")))
            .OrderByDescending(row => Score(row))
            .ToList();
        var missing = report["error_count"]?.GetValue<int>() ?? 0;
        RegionalGridSummary.Text = $"{points.Count} 个网格点 · {cities.Count} 个城市 · 缺测 {missing} · 周期 {Text(report, "cycle_time_utc")}";
        foreach (var row in points.Take(2000))
            RegionalGridRows.Items.Add(RegionalRowButton(row, false));
        foreach (var row in cities.Take(2000))
            CityRankingRows.Items.Add(RegionalRowButton(row, true));
        if (points.Count == 0)
            RegionalGridRows.Items.Add(new TextBlock { Text = "暂无可用网格结果。", Opacity = .65, Margin = new Thickness(4) });
        if (cities.Count == 0)
        {
            var reason = Text(cityReport, "reason");
            CityRankingRows.Items.Add(new TextBlock
            {
                Text = string.IsNullOrWhiteSpace(reason) ? "该区域暂无城市排名。" : reason,
                Opacity = .65, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(4)
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
            Content = $"{name}    {((score == null) ? "--" : Score(row).ToString("0.00"))}    {best}",
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(8, 6, 8, 6),
            Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent),
        };
        var snapshot = row.DeepClone() as JsonObject ?? row;
        button.Click += async (_, _) => await FocusRegionalPoint(snapshot);
        return button;
    }

    private void OnRegionalInspectorModeChanged(object sender, SelectionChangedEventArgs args)
    {
        if (RegionalInspectorMode == null) return;
        var mode = (RegionalInspectorMode.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "grid";
        RegionalGridRows.Visibility = mode == "grid" ? Visibility.Visible : Visibility.Collapsed;
        CityRankingRows.Visibility = mode == "cities" ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task FocusRegionalPoint(double lat, double lon)
    {
        var report = SelectedReport();
        var rows = (report?["points"] as JsonArray ?? new()).OfType<JsonObject>().ToList();
        var cities = (report?["city_ranking"]?["items"] as JsonArray ?? new()).OfType<JsonObject>();
        rows.AddRange(cities);
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
        regionalProfileModel = (ResultModel.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "gfs";
        RegionalProfileSelection.Text = $"{regionalProfileModel.ToUpperInvariant()} · {lat:0.00}°, {lon:0.00}°";
        await QueryRegionalProfile(report, regionalProfileModel, lat, lon,
            Text(row, "best_time_local"));
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
            return;
        }
        try
        {
            var response = await engine.Call("profile_query", request.Payload,
                cancellation: token);
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
            RegionalProfileStatus.Text = error.Message;
        }
    }

    private sealed class ProfileQuerySpec
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

    private static ProfileQuerySpec? BuildProfileQuery(JsonObject report, string model,
        double lat, double lon, string localTime)
    {
        if (model is not ("gfs" or "ec") || !double.IsFinite(lat) || !double.IsFinite(lon)
            || lat is < -90 or > 90 || lon is < -180 or > 180)
            return null;
        var source = report["profile_source"] as JsonObject;
        if (source == null || Text(source, "contract") != "rust-desktop-profile-source-v1"
            || source["available"]?.GetValue<bool>() != true
            || Text(source, "query_method") != "profile_query"
            || Text(source, "model_source") != model
            || Text(source, "profile_kind") != "model-pressure-cross-section")
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
                ["source_identity"] = new JsonObject { ["bytes"] = bytes.Value, ["sha256"] = sha },
                ["model_source"] = model, ["lat"] = lat, ["lon"] = lon,
                ["valid_time_utc"] = validUtc,
            },
            ResultId = resultId, SourceBytes = bytes.Value, SourceSha256 = sha, Model = model,
            Lat = lat, Lon = lon, ValidTimeUtc = validUtc, CycleUtc = cycleUtc,
        };
    }

    private static bool ProfileResponseMatches(JsonObject response, ProfileQuerySpec request)
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

    private void RenderProfileSection()
    {
        RegionalProfileRows.Items.Clear();
        if (regionalProfileState == "loading")
        {
            RegionalProfileStatus.Text = "正在读取所选网格的冻结剖面…";
            return;
        }
        if (regionalProfileState != "ready" || regionalProfile == null)
        {
            if (regionalProfileState == "idle")
                RegionalProfileStatus.Text = "未选择网格。";
            return;
        }
        var fields = regionalProfile["fields"] as JsonObject;
        var distanceCount = (regionalProfile["distances_km"] as JsonArray)?.Count ?? 0;
        RegionalProfileStatus.Text =
            $"{Text(regionalProfile, "valid_time_local")} · 太阳高度 {Number(regionalProfile, "solar_elevation_deg"):0.0}° · " +
            $"太阳方位 {Number(regionalProfile, "solar_azimuth_deg"):0.0}° · {distanceCount} 个距离样本";
        AddProfileMetric("云量样本", NestedArrayCount(fields?["cloud_fraction_percent"]));
        AddProfileMetric("温度样本", NestedArrayCount(fields?["temperature_c"]));
        AddProfileMetric("湿度样本", NestedArrayCount(fields?["relative_humidity_percent"]));
        AddProfileMetric("消光样本", NestedArrayCount(fields?["extinction_coefficient"]));
    }

    private void AddProfileMetric(string title, int value)
    {
        RegionalProfileRows.Items.Add(new TextBlock
        {
            Text = $"{title}  {value}",
            FontSize = 11,
            Opacity = .8,
            Margin = new Thickness(0, 2, 0, 2)
        });
    }

    private static int NestedArrayCount(JsonNode? node)
    {
        return (node as JsonArray)?.OfType<JsonArray>().Sum(row => row.Count)
            ?? (node as JsonArray)?.Count ?? 0;
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
        RegionalProfileRows?.Items.Clear();
    }

    private static string TimeLabel(string value) => value.Length >= 16 ? value.Substring(11, 5) : "--";

    private void BuildMetrics()
    {
        for (var i = 0; i < Metrics.Length; i++)
        {
            var spec = Metrics[i];
            var stack = new StackPanel { Spacing = 10 };
            stack.Children.Add(new TextBlock { Text = spec.Title, FontSize = 12, Opacity = .8 });
            var value = new TextBlock { Text = "--", FontSize = 21, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold };
            metricValues[spec.Key] = value; stack.Children.Add(value);
            stack.Children.Add(new TextBlock { Text = spec.Caption, FontSize = 9, Opacity = .6, TextTrimming = TextTrimming.CharacterEllipsis });
            var border = new Border
            {
                Child = stack, Padding = new Thickness(13), CornerRadius = new CornerRadius(8),
                Background = (Brush)Application.Current.Resources["LayerFillColorDefaultBrush"],
                BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"], BorderThickness = new Thickness(1)
            };
            Grid.SetRow(border, i / 3); Grid.SetColumn(border, i % 3); MetricGrid.Children.Add(border);
        }
    }

    private void OnChartSizeChanged(object sender, SizeChangedEventArgs args) => RenderChart();
    private void RenderChart()
    {
        if (QualityChart == null || !initialized) return;
        QualityChart.Children.Clear();
        var steps = SelectedReport()?["steps"] as JsonArray ?? new();
        ChartEmpty.Visibility = steps.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        var width = Math.Max(1, ((FrameworkElement)QualityChart.Parent).ActualWidth);
        var height = 164.0;
        foreach (var y in new[] { 0.0, 82.0, 164.0 })
            QualityChart.Children.Add(new Microsoft.UI.Xaml.Shapes.Line
            {
                X1 = 0, X2 = width, Y1 = y, Y2 = y, StrokeThickness = .5,
                Stroke = new SolidColorBrush(Microsoft.UI.Colors.Gray), Opacity = .25
            });
        var segment = new Microsoft.UI.Xaml.Shapes.Polyline
        {
            StrokeThickness = 2, Stroke = new SolidColorBrush(Microsoft.UI.ColorHelper.FromArgb(255, 227, 102, 74))
        };
        for (var i = 0; i < steps.Count; i++)
        {
            if (steps[i]?["valid"]?.GetValue<bool>() == false || steps[i]?["instant_quality_index"] == null) continue;
            segment.Points.Add(new Windows.Foundation.Point(
                width * i / Math.Max(1, steps.Count - 1),
                height * (1 - Math.Clamp(Number(steps[i], "instant_quality_index") / 2.5, 0, 1))));
        }
        QualityChart.Children.Add(segment);
    }

    private void OnResultModelChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        RenderResult(); _ = UpdateMap();
    }

    private async Task LoadInventory()
    {
        if (!online) return;
        try
        {
            var response = await engine.Call("inventory");
            InventoryRows.Children.Clear();
            foreach (var item in response["datasets"] as JsonArray ?? new())
            {
                var source = Text(item, "source");
                var stack = new StackPanel { Spacing = 12 };
                stack.Children.Add(new TextBlock
                {
                    Text = source == "ecmwf" ? "EC" : source.ToUpperInvariant(),
                    FontSize = 20, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold
                });
                stack.Children.Add(new TextBlock
                {
                    Text = $"{Number(item, "total_file_count"):0} 个文件 · {Number(item, "total_storage_mb") / 1024:0.00} GB"
                });
                var details = new StackPanel { Spacing = 8 };
                foreach (var cycle in (item?["cycles"] as JsonObject ?? new()).OrderByDescending(c => c.Key))
                    details.Children.Add(new TextBlock { Text = $"{cycle.Key}   {Number(cycle.Value, "files"):0} 个文件" });
                stack.Children.Add(new Expander { Header = "已下载周期", Content = details, HorizontalAlignment = HorizontalAlignment.Stretch });
                var path = Text(item, "path");
                var open = new Button { Content = "打开文件夹" };
                open.Click += (_, _) => System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
                stack.Children.Add(open);
                InventoryRows.Children.Add(stack);
            }
        }
        catch (Exception error) { Fail(error); }
    }

    private async void OnRefreshInventory(object sender, RoutedEventArgs args) => await LoadInventory();
    private void OnDownloadForLocation(object sender, RoutedEventArgs args)
    {
        Navigation.SelectedItem = Navigation.MenuItems[0];
        OnPredict(sender, args);
    }
    private async void OnReconnect(object sender, RoutedEventArgs args) => await Connect();
    private void OnAppearanceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (Root == null) return;
        Root.RequestedTheme = Appearance.SelectedIndex switch { 1 => ElementTheme.Light, 2 => ElementTheme.Dark, _ => ElementTheme.Default };
        if (initialized) SaveSettings();
    }
    private void OnCancel(object sender, RoutedEventArgs args)
    {
        operation?.Cancel(); Status.Text = "正在取消，等待当前阶段结束";
    }
}
