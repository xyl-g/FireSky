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
    private readonly List<TaskHistoryItem> taskHistory = new();
    private readonly List<TaskHistoryRow> taskHistoryRows = new();
    private readonly List<TaskEvent> taskHistoryEvents = new();
    private CancellationTokenSource? taskEventsOperation;
    private int taskHistoryTotal, taskHistoryIssues;
    private bool taskHistoryLoaded, taskHistoryLoading;
    private bool taskHistoryEventsLoading, taskHistoryEventsTruncated;
    private string taskHistoryError = "", taskHistoryEventsError = "";
    private string selectedTaskHistoryId = "", restoringTaskHistoryId = "", forgettingTaskHistoryId = "";
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

    // Task history records come from the engine verbatim; every access below
    // tolerates wrong JSON types the same way Swift's conditional casts do.
    private static string? StringOrNull(JsonNode? node) =>
        node is JsonValue value && value.GetValueKind() == JsonValueKind.String
            ? value.GetValue<string>() : null;
    private static string StringOf(JsonNode? node) => StringOrNull(node) ?? "";
    private static bool? BoolValue(JsonNode? node, string key)
    {
        if (node?[key] is not JsonValue value) return null;
        return value.GetValueKind() == JsonValueKind.True ? true
            : value.GetValueKind() == JsonValueKind.False ? false : null;
    }
    private static double? DoubleValue(JsonNode? node) =>
        node is JsonValue value && value.TryGetValue<double>(out var number)
            && double.IsFinite(number) ? number : null;
    private static bool IsTaskId(string value) =>
        value.Length == 64 && value.All(Uri.IsHexDigit);
    // Engine task stamps are RFC3339 ("…T…Z", with or without fractional
    // seconds); requiring the time separator keeps date-only strings invalid.
    private static bool IsTaskTimestamp(string value) =>
        value.Contains('T', StringComparison.Ordinal) && TryUtc(value, out _);

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
        UpdateTaskHistoryControls();
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
        if (page == "data")
        {
            _ = LoadInventory();
            _ = LoadTaskHistory();
        }
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

    // ---- 任务历史（行为对齐 macOS NativeTaskHistoryView 与 Store 的任务历史合同） ----

    private async void OnRefreshTaskHistory(object sender, RoutedEventArgs args) => await LoadTaskHistory();

    private async Task LoadTaskHistory()
    {
        if (!online || taskHistoryLoading) return;
        taskHistoryLoading = true;
        taskHistoryError = "";
        RenderTaskHistory();
        try
        {
            var response = await engine.Call("task_history", new() { ["limit"] = 100 });
            if (StringOf(response["schema"]) != "firesky-task-history-v1"
                || BoolValue(response, "automatic_restart") != false
                || response["tasks"] is not JsonArray values
                || values.Any(node => node is not JsonObject)
                || Integer(response, "total") is not { } total
                || total < values.Count || values.Count > 100)
                throw new InvalidOperationException("任务历史协议不匹配，请重新连接引擎。");
            var parsed = new List<TaskHistoryItem>();
            foreach (var node in values.OfType<JsonObject>())
            {
                if (TaskHistoryItem.Parse(node) is not { } item)
                    throw new InvalidOperationException("任务历史包含无效记录，请检查引擎版本。");
                parsed.Add(item);
            }
            if (parsed.Select(row => row.TaskId).Distinct().Count() != parsed.Count)
                throw new InvalidOperationException("任务历史包含重复记录，请重新连接引擎。");
            taskHistory.Clear();
            taskHistory.AddRange(parsed);
            taskHistoryTotal = (int)total;
            taskHistoryIssues = (response["issues"] as JsonArray)?.Count ?? 0;
            taskHistoryLoaded = true;
            if (selectedTaskHistoryId.Length > 0
                && !parsed.Any(row => row.TaskId == selectedTaskHistoryId))
                ClearTaskHistorySelection();
        }
        catch (Exception error)
        {
            taskHistoryError = error.Message;
        }
        finally
        {
            taskHistoryLoading = false;
            RenderTaskHistory();
        }
    }

    private void RenderTaskHistory()
    {
        TaskHistoryCaption.Text = taskHistoryLoaded
            ? $"显示最近 {taskHistory.Count} 项，共 {taskHistoryTotal} 项"
            : "记录数据检查、准备、预测和下载探测任务";
        TaskHistoryError.Text = taskHistoryError;
        TaskHistoryError.Visibility = taskHistoryError.Length > 0
            ? Visibility.Visible : Visibility.Collapsed;
        TaskHistoryLoadingPanel.Visibility = taskHistoryLoading && taskHistory.Count == 0
            ? Visibility.Visible : Visibility.Collapsed;
        TaskHistoryEmpty.Visibility = !taskHistoryLoading && taskHistory.Count == 0
            ? Visibility.Visible : Visibility.Collapsed;
        TaskHistoryEmptyText.Text = online
            ? "完成一次检查或预测后会显示在这里。" : "引擎未连接。";
        TaskHistoryIssuesNotice.Text = taskHistoryIssues > 0
            ? $"{taskHistoryIssues} 项记录无法核验，未将未知状态标为中断。" : "";
        TaskHistoryIssuesNotice.Visibility = taskHistoryIssues > 0
            ? Visibility.Visible : Visibility.Collapsed;
        RenderTaskHistoryRows();
        UpdateTaskHistoryControls();
    }

    private void RenderTaskHistoryRows()
    {
        TaskHistoryRows.Children.Clear();
        taskHistoryRows.Clear();
        foreach (var item in taskHistory)
            TaskHistoryRows.Children.Add(BuildTaskHistoryRow(item));
    }

    private FrameworkElement BuildTaskHistoryRow(TaskHistoryItem item)
    {
        var info = new StackPanel { Spacing = 5 };
        var titleLine = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7 };
        titleLine.Children.Add(new TextBlock
        {
            Text = $"{item.KindTitle} · {item.MethodTitle}",
            FontSize = 13,
            FontWeight = Microsoft.UI.Text.FontWeights.Medium,
            VerticalAlignment = VerticalAlignment.Center,
        });
        titleLine.Children.Add(new TextBlock
        {
            Text = item.StatusTitle,
            FontSize = 11,
            FontWeight = Microsoft.UI.Text.FontWeights.Medium,
            Foreground = StatusBrush(item.Status),
            VerticalAlignment = VerticalAlignment.Center,
        });
        info.Children.Add(titleLine);
        if (item.ContextTitle.Length > 0)
            info.Children.Add(new TextBlock
            {
                Text = item.ContextTitle, FontSize = 11, Opacity = .7,
                TextWrapping = TextWrapping.Wrap,
            });
        var metaLine = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        metaLine.Children.Add(new TextBlock
        {
            Text = item.CreatedAtUtc, FontSize = 10, Opacity = .6,
            VerticalAlignment = VerticalAlignment.Center,
        });
        if (item.Status == "running" && DoubleValue(item.Progress["percent"]) is { } percent)
            metaLine.Children.Add(new TextBlock
            {
                Text = $"{percent:0}%", FontSize = 10, Opacity = .6,
                VerticalAlignment = VerticalAlignment.Center,
            });
        if (item.Status == "interrupted")
            metaLine.Children.Add(new TextBlock
            {
                Text = "不会自动重跑", FontSize = 10, Opacity = .6,
                VerticalAlignment = VerticalAlignment.Center,
            });
        info.Children.Add(metaLine);

        var actions = new StackPanel
        {
            Orientation = Orientation.Horizontal, Spacing = 8,
            VerticalAlignment = VerticalAlignment.Top,
        };
        var restoreResult = new Button { Content = "恢复结果", Padding = new Thickness(8, 3, 8, 3) };
        ToolTipService.SetToolTip(restoreResult, "恢复已发布结果");
        restoreResult.Visibility = item.CanRestoreResult ? Visibility.Visible : Visibility.Collapsed;
        restoreResult.Click += (_, _) => _ = RestoreTaskResult(item);
        actions.Children.Add(restoreResult);
        var restoreParameters = new Button { Content = "恢复参数", Padding = new Thickness(8, 3, 8, 3) };
        ToolTipService.SetToolTip(restoreParameters, "恢复参数并重新检查数据");
        restoreParameters.Visibility = item.RetryPayload != null
            ? Visibility.Visible : Visibility.Collapsed;
        restoreParameters.Click += (_, _) => RestoreTaskParameters(item);
        actions.Children.Add(restoreParameters);
        var forget = new Button { Content = "删除", Padding = new Thickness(8, 3, 8, 3) };
        ToolTipService.SetToolTip(forget, "删除任务记录");
        forget.Click += (_, _) => _ = ConfirmForgetTask(item.TaskId);
        actions.Children.Add(forget);

        var row = new Grid { ColumnSpacing = 12 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.Children.Add(new TextBlock
        {
            Text = StatusGlyph(item.Status),
            Foreground = StatusBrush(item.Status),
            FontSize = 12,
            Margin = new Thickness(0, 2, 0, 0),
            VerticalAlignment = VerticalAlignment.Top,
        });
        Grid.SetColumn(info, 1);
        Grid.SetColumn(actions, 2);
        row.Children.Add(info);
        row.Children.Add(actions);

        // 点击整行切换事件视图；行内操作按钮自行处理指针事件，不会触发外层点击。
        var open = new Button
        {
            Content = row,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            Padding = new Thickness(0),
            BorderThickness = new Thickness(0),
            Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent),
        };
        open.Click += (_, _) => OnSelectTaskHistory(item.TaskId);
        taskHistoryRows.Add(new TaskHistoryRow
        {
            Item = item, RestoreResult = restoreResult,
            RestoreParameters = restoreParameters, Forget = forget,
        });
        return new Border
        {
            BorderThickness = new Thickness(0, 0, 0, 1),
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            Child = open,
        };
    }

    private static Brush StatusBrush(string status) => status switch
    {
        "done" => new SolidColorBrush(Microsoft.UI.Colors.Green),
        "failed" => new SolidColorBrush(Microsoft.UI.Colors.Red),
        "interrupted" => new SolidColorBrush(Microsoft.UI.Colors.Orange),
        _ => new SolidColorBrush(Microsoft.UI.Colors.Gray),
    };

    private static string StatusGlyph(string status) => status switch
    {
        "done" => "✓", "failed" => "!", "cancelled" => "×", "interrupted" => "‖", _ => "○",
    };

    private void UpdateTaskHistoryControls()
    {
        if (TaskHistoryRefresh == null) return;
        TaskHistoryRefresh.IsEnabled = online && !taskHistoryLoading;
        var restoring = restoringTaskHistoryId.Length > 0;
        var forgetting = forgettingTaskHistoryId.Length > 0;
        foreach (var row in taskHistoryRows)
        {
            row.RestoreResult.IsEnabled = !busy && !restoring;
            row.RestoreParameters.IsEnabled = !busy && !restoring && row.Item.Status != "running";
            row.Forget.IsEnabled = online && !busy && !forgetting && row.Item.Status != "running";
        }
    }

    private void OnSelectTaskHistory(string taskId)
    {
        if (selectedTaskHistoryId == taskId)
        {
            ClearTaskHistorySelection();
            return;
        }
        if (!taskHistory.Any(row => row.TaskId == taskId)) return;
        taskEventsOperation?.Cancel();
        taskEventsOperation?.Dispose();
        taskEventsOperation = new CancellationTokenSource();
        var token = taskEventsOperation.Token;
        selectedTaskHistoryId = taskId;
        taskHistoryEvents.Clear();
        taskHistoryEventsError = "";
        taskHistoryEventsTruncated = false;
        taskHistoryEventsLoading = true;
        RenderTaskEvents();
        _ = LoadTaskEvents(taskId, token);
    }

    private void ClearTaskHistorySelection()
    {
        taskEventsOperation?.Cancel();
        taskEventsOperation?.Dispose();
        taskEventsOperation = null;
        selectedTaskHistoryId = "";
        taskHistoryEvents.Clear();
        taskHistoryEventsError = "";
        taskHistoryEventsLoading = false;
        taskHistoryEventsTruncated = false;
        RenderTaskEvents();
    }

    private async Task LoadTaskEvents(string taskId, CancellationToken token)
    {
        try
        {
            var response = await engine.Call("task_events",
                new() { ["task_id"] = taskId, ["after_seq"] = 0 }, cancellation: token);
            token.ThrowIfCancellationRequested();
            if (selectedTaskHistoryId != taskId) return;
            if (StringOf(response["task_id"]) != taskId
                || response["events"] is not JsonArray values
                || values.Any(node => node is not JsonObject)
                || Integer(response, "next_seq") is not { } next || next < 0
                || BoolValue(response, "truncated") is null)
                throw new InvalidOperationException("任务事件协议不匹配。");
            var parsed = new List<TaskEvent>();
            var previous = 0L;
            foreach (var node in values.OfType<JsonObject>())
            {
                if (Integer(node, "seq") is not { } sequence
                    || sequence <= previous || sequence > next
                    || StringOrNull(node["at_utc"]) is not { } atUtc || !IsTaskTimestamp(atUtc)
                    || StringOrNull(node["event"]) is not { } title)
                    throw new InvalidOperationException("任务事件顺序或时间无效。");
                previous = sequence;
                parsed.Add(new TaskEvent
                {
                    Sequence = sequence, AtUtc = atUtc, Event = title,
                    Data = node["data"] as JsonObject ?? new(),
                });
            }
            taskHistoryEvents.Clear();
            taskHistoryEvents.AddRange(parsed);
            taskHistoryEventsTruncated = BoolValue(response, "truncated") == true;
            taskHistoryEventsError = "";
            taskHistoryEventsLoading = false;
            RenderTaskEvents();
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception error)
        {
            if (selectedTaskHistoryId != taskId || token.IsCancellationRequested) return;
            taskHistoryEventsLoading = false;
            taskHistoryEventsError = error.Message;
            RenderTaskEvents();
        }
    }

    private void RenderTaskEvents()
    {
        TaskEventsPanel.Children.Clear();
        var item = taskHistory.FirstOrDefault(row => row.TaskId == selectedTaskHistoryId);
        if (item == null)
        {
            TaskEventsPanel.Visibility = Visibility.Collapsed;
            return;
        }
        TaskEventsPanel.Visibility = Visibility.Visible;
        var header = new Grid();
        header.Children.Add(new TextBlock
        {
            Text = $"{item.KindTitle} · {item.MethodTitle}事件",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
        });
        var close = new Button
        {
            Content = new SymbolIcon(Symbol.Clear),
            Padding = new Thickness(6, 3, 6, 3),
            HorizontalAlignment = HorizontalAlignment.Right,
        };
        ToolTipService.SetToolTip(close, "关闭任务事件");
        close.Click += (_, _) => ClearTaskHistorySelection();
        header.Children.Add(close);
        TaskEventsPanel.Children.Add(header);
        if (taskHistoryEventsLoading)
        {
            TaskEventsPanel.Children.Add(new ProgressRing { Width = 16, Height = 16, IsActive = true });
            return;
        }
        if (taskHistoryEventsError.Length > 0)
        {
            TaskEventsPanel.Children.Add(new TextBlock
            {
                Text = taskHistoryEventsError, FontSize = 11,
                Foreground = new SolidColorBrush(Microsoft.UI.Colors.Red),
                TextWrapping = TextWrapping.Wrap,
            });
            return;
        }
        if (taskHistoryEventsTruncated)
            TaskEventsPanel.Children.Add(new TextBlock
            {
                Text = "较早事件已超出保留上限。", FontSize = 11, Opacity = .65,
            });
        foreach (var entry in taskHistoryEvents)
        {
            var line = new Grid { ColumnSpacing = 9, Padding = new Thickness(0, 3, 0, 3) };
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var title = new TextBlock
            {
                Text = entry.Title, FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                VerticalAlignment = VerticalAlignment.Center,
            };
            var detail = new TextBlock
            {
                Text = entry.Detail, FontSize = 10, Opacity = .65,
                TextWrapping = TextWrapping.Wrap,
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(title, 1);
            Grid.SetColumn(detail, 2);
            line.Children.Add(new TextBlock
            {
                Text = entry.AtUtc, FontSize = 10, Opacity = .6,
                VerticalAlignment = VerticalAlignment.Center,
            });
            line.Children.Add(title);
            line.Children.Add(detail);
            TaskEventsPanel.Children.Add(line);
        }
    }

    private async Task ConfirmForgetTask(string taskId)
    {
        if (!online || busy || forgettingTaskHistoryId.Length > 0) return;
        var item = taskHistory.FirstOrDefault(row => row.TaskId == taskId);
        if (item == null || item.Status == "running") return;
        var dialog = new ContentDialog
        {
            Title = "删除任务记录？",
            Content = new TextBlock
            {
                Text = "只删除状态和事件，不删除已发布报告或天气数据。",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "删除记录",
            CloseButtonText = "保留记录",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Root.XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        await ForgetTaskHistory(taskId);
    }

    private async Task ForgetTaskHistory(string taskId)
    {
        if (!online || busy || forgettingTaskHistoryId.Length > 0) return;
        var item = taskHistory.FirstOrDefault(row => row.TaskId == taskId);
        if (item == null || item.Status == "running") return;
        forgettingTaskHistoryId = taskId;
        UpdateTaskHistoryControls();
        try
        {
            var response = await engine.Call("task_forget", new() { ["task_id"] = taskId });
            if (BoolValue(response, "forgotten") != true)
                throw new InvalidOperationException("任务记录没有被删除。");
            if (selectedTaskHistoryId == taskId) ClearTaskHistorySelection();
            _ = LoadTaskHistory();
        }
        catch (Exception error)
        {
            taskHistoryError = error.Message;
            RenderTaskHistory();
        }
        finally
        {
            forgettingTaskHistoryId = "";
            UpdateTaskHistoryControls();
        }
    }

    private async Task RestoreTaskResult(TaskHistoryItem item)
    {
        if (busy || restoringTaskHistoryId.Length > 0 || !item.CanRestoreResult) return;
        restoringTaskHistoryId = item.TaskId;
        UpdateTaskHistoryControls();
        var kind = item.RetryKind.Length > 0 ? item.RetryKind : "point";
        try
        {
            var response = await engine.Call("result_restore", new() { ["task_id"] = item.TaskId });
            if (kind != "nowcast" && response["model_results"] is not JsonObject)
                throw new InvalidOperationException("已发布结果格式无效。");
            result = response;
            resultKind = kind;
            SelectNavigation(kind);
            if (kind != "nowcast")
            {
                var model = StringOrNull(item.RetryPayload?["model_source"]);
                if (model is not ("gfs" or "ec"))
                    model = (response["model_results"] as JsonObject)?["gfs"] is null ? "ec" : "gfs";
                ResultModel.SelectedIndex = model == "ec" ? 1 : 0;
            }
            RenderResult();
            await UpdateMap();
            Status.Text = "已恢复历史结果";
            ErrorBar.IsOpen = false;
        }
        catch (Exception error)
        {
            taskHistoryError = error.Message;
            RenderTaskHistory();
        }
        finally
        {
            restoringTaskHistoryId = "";
            UpdateTaskHistoryControls();
        }
    }

    private void RestoreTaskParameters(TaskHistoryItem item)
    {
        if (busy || restoringTaskHistoryId.Length > 0
            || item.Status == "running" || item.RetryPayload == null) return;
        restoringTaskHistoryId = item.TaskId;
        ApplyRetryPayload(item.RetryKind, item.RetryPayload);
        SelectNavigation(item.RetryKind);
        restoringTaskHistoryId = "";
        OnPredict(this, new RoutedEventArgs());
    }

    private void ApplyRetryPayload(string kind, JsonObject payload)
    {
        if (StringOrNull(payload["name"]) is { } name) LocationName.Text = name;
        if (DoubleValue(payload["lat"]) is { } lat) Latitude.Value = lat;
        if (DoubleValue(payload["lon"]) is { } lon) Longitude.Value = lon;
        if (DoubleValue(payload["elevation_m"]) is { } elevation) Elevation.Value = elevation;
        if (StringOrNull(payload["timezone_name"]) is { } timezone) Timezone.Text = timezone;
        if (StringOrNull(payload["target_date"]) is { } date
            && DateTimeOffset.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var target))
            TargetDate.Date = target;
        if (StringOrNull(payload["phenomenon"]) is { } phenomenon
            && phenomenon is "dawn" or "dusk")
            Phenomenon.SelectedIndex = phenomenon == "dawn" ? 0 : 1;
        if (kind == "regional" && payload["custom_bounds"] is JsonObject bounds
            && DoubleValue(bounds["west"]) is { } west && DoubleValue(bounds["east"]) is { } east
            && DoubleValue(bounds["south"]) is { } south && DoubleValue(bounds["north"]) is { } north)
        {
            West.Value = west; East.Value = east; South.Value = south; North.Value = north;
        }
        MetadataReady.IsChecked = true;
    }

    private void SelectNavigation(string kind)
    {
        Navigation.SelectedItem = kind switch
        {
            "regional" => Navigation.MenuItems[1],
            "nowcast" => Navigation.MenuItems[2],
            _ => Navigation.MenuItems[0],
        };
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

    // 校验规则与 Swift NativeTaskHistoryItem 一致：任一字段无效即整条记录无效。
    private sealed class TaskHistoryItem
    {
        internal required string TaskId { get; init; }
        internal required string Method { get; init; }
        internal required string Status { get; init; }
        internal required bool CancelRequested { get; init; }
        internal required long EventSeq { get; init; }
        internal required string RetryKind { get; init; }
        internal required JsonObject? RetryPayload { get; init; }
        internal required JsonObject? ResultRef { get; init; }
        internal required JsonObject Progress { get; init; }

        internal static TaskHistoryItem? Parse(JsonObject value)
        {
            if (StringOrNull(value["task_id"]) is not { } taskId || !IsTaskId(taskId)
                || StringOrNull(value["method"]) is not { } method
                || StringOrNull(value["status"]) is not { } status
                || status is not ("running" or "done" or "failed" or "cancelled" or "interrupted")
                || StringOrNull(value["created_at_utc"]) is not { } createdAt
                || !IsTaskTimestamp(createdAt)
                || StringOrNull(value["updated_at_utc"]) is not { } updatedAt
                || !IsTaskTimestamp(updatedAt)
                || Integer(value, "event_seq") is not { } eventSeq || eventSeq < 1)
                return null;
            string retryKind = "";
            JsonObject? retryPayload = null;
            if (value["retry_request"] is JsonObject retry
                && StringOrNull(retry["kind"]) is { } kind
                && kind is "point" or "regional" or "nowcast"
                && retry["payload"] is JsonObject payload)
            {
                retryKind = kind;
                retryPayload = payload;
            }
            return new TaskHistoryItem
            {
                TaskId = taskId, Method = method, Status = status,
                CancelRequested = BoolValue(value, "cancel_requested") == true,
                EventSeq = eventSeq, RetryKind = retryKind, RetryPayload = retryPayload,
                ResultRef = value["result_ref"] as JsonObject,
                Progress = value["progress"] as JsonObject ?? new(),
            };
        }

        internal string KindTitle => RetryKind switch
        {
            "point" => "单点", "regional" => "区域", "nowcast" => "实况", _ => MethodTitle,
        };

        internal string MethodTitle => Method switch
        {
            "check" => "数据检查", "prepare" => "数据准备", "forecast" => "预测计算",
            "publications" => "发布核验", "network_probe" => "下载探测", _ => "任务",
        };

        internal string StatusTitle => Status switch
        {
            "running" => CancelRequested ? "取消中" : "运行中",
            "done" => "已完成", "failed" => "失败",
            "cancelled" => "已取消", "interrupted" => "已中断", _ => Status,
        };

        internal string ContextTitle
        {
            get
            {
                if (RetryPayload == null) return "";
                var name = StringOrNull(RetryPayload["name"]);
                if (name == null && RetryPayload["regions"] is JsonArray regions
                    && regions.All(node => node is JsonValue value
                        && value.GetValueKind() == JsonValueKind.String))
                    name = string.Join("、", regions.Select(node => StringOf(node))
                        .Where(text => text.Length > 0));
                var date = StringOf(RetryPayload["target_date"]);
                var phenomenon = StringOf(RetryPayload["phenomenon"]) switch
                {
                    "dawn" => "朝霞", "dusk" => "晚霞", _ => "",
                };
                return string.Join(" · ", new[] { name ?? "", date, phenomenon }
                    .Where(part => part.Length > 0));
            }
        }

        internal bool CanRestoreResult =>
            Status == "done"
            && ResultRef?["snapshot"] is JsonObject snapshot
            && StringOf(snapshot["contract"]) == "firesky-result-snapshot-v1"
            && snapshot["sha256"] is JsonValue sha
            && sha.GetValueKind() == JsonValueKind.String;
    }

    private sealed class TaskEvent
    {
        internal required long Sequence { get; init; }
        internal required string AtUtc { get; init; }
        internal required string Event { get; init; }
        internal required JsonObject Data { get; init; }

        internal string Title => Event switch
        {
            "running" => "开始", "progress" => "进度", "cancel_requested" => "请求取消",
            "done" => "完成", "failed" => "失败", "cancelled" => "已取消",
            "interrupted" => "已中断", _ => "状态更新",
        };

        internal string Detail
        {
            get
            {
                var values = new List<string>();
                foreach (var node in new[] { Data["stage"], Data["source"] })
                    if (StringOrNull(node) is { } text && text.Length > 0)
                        values.Add(text.Length > 128 ? text.Substring(0, 128) : text);
                if (DoubleValue(Data["percent"]) is { } percent)
                    values.Add($"{percent:0}%");
                return string.Join(" · ", values);
            }
        }
    }

    private sealed class TaskHistoryRow
    {
        internal required TaskHistoryItem Item { get; init; }
        internal required Button RestoreResult { get; init; }
        internal required Button RestoreParameters { get; init; }
        internal required Button Forget { get; init; }
    }
}
