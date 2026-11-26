using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Windows.System;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.Web.WebView2.Core;
using Windows.Devices.Geolocation;

namespace FireSky;

/// <summary>
/// FireSky Windows 原生客户端主窗口。信息架构与行为对齐 macOS 基准（v1.2.0）：
/// 单点/区域/实况预测 + 数据管理 + 任务历史独立页 + 设置四分类。
/// 业务能力全部来自 Rust 引擎；UI 不重算科学值、不跨模式填补缺测。
/// </summary>
public sealed partial class MainWindow : Window
{
    internal readonly EngineClient engine = new();
    private CancellationTokenSource? operation;
    private CancellationTokenSource? profileOperation;
    private CancellationTokenSource? frozenProfileOperation;
    private CancellationTokenSource? probeOperation;
    private CancellationTokenSource? syncOperation;
    private bool busy, searching, locating, savingCams, savingRetention, savingSync;
    private bool probingDownloads, cancellingProbe, dataSyncRunning;
    private bool initialized, mapReady, online;
    private bool restoringSession, suppressDraftEvents;
    private string page = "point", resultKind = "";
    private string settingsCategory = "data";
    private string ecmwfSource = "ecmwf";
    private string elevationSource = "auto";
    private string elevationSourceActual = "";
    private string dateTab = "今天";  // 状态键保持中文原文（显示经树遍历翻译）
    private string pointSelectedTime = "";
    private JsonObject? result;
    private JsonObject? resultParams;
    private JsonObject? regionalProfile;
    private JsonObject? frozenProfile;
    private string regionalProfileModel = "";
    private string frozenProfileModel = "";
    private string frozenProfileTime = "";
    private string frozenProfileState = "idle";
    private string frozenProfileError = "";
    private string regionalProfileState = "idle";
    private double regionalProfileLat, regionalProfileLon;
    internal List<JsonObject> regionDefinitions = [];
    private List<string> defaultRegionalPreset = ["china"];
    private readonly HashSet<string> selectedRegions = new() { "china" };
    private bool customRegion;
    private string errorMessage = "";
    private string errorPage = "";
    private List<JsonObject> favorites = [];
    private readonly List<ProbeRow> probeRows = [.. ProbeRow.Initial()];
    private DateTimeOffset? probeDate;
    private JsonObject? camsConfiguration;
    private string camsKeyDraft = "";
    private string camsMessage = "";
    private bool camsSaveFailed, camsConfigured, canSaveCams;
    private JsonObject? dataSyncSettingsResponse;
    private bool dataSyncEnabled, dataSyncCams, dataSyncGfs, dataSyncEc, dataSyncLoaded;
    private bool dataSyncConfigured, dataSyncRetentionConfigured;
    private string dataSyncInterval = "", dataSyncLeadHorizon = "", dataSyncAnchor = "";
    private string dataSyncMessage = "";
    private bool dataSyncSaveFailed;
    private JsonObject dataSyncState = new();
    private readonly Dictionary<string, JsonObject> dataSyncProgress = new();
    private Microsoft.UI.Dispatching.DispatcherQueueTimer? syncTickTimer, sessionSaveTimer;
    private readonly string settingsFile = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FireSky", "settings.json");
    private int locationRevision;
    private readonly Dictionary<string, TextBlock> metricValues = new();
    private readonly Dictionary<string, StackPanel> heroPanels = new();
    internal readonly List<TaskHistoryItem> taskHistory = new();
    private readonly List<TaskHistoryRow> taskHistoryRows = new();
    private readonly List<TaskEvent> taskHistoryEvents = new();
    private CancellationTokenSource? taskEventsOperation;
    private int taskHistoryTotal, taskHistoryIssues;
    private bool taskHistoryLoaded, taskHistoryLoading;
    private bool taskHistoryEventsLoading, taskHistoryEventsTruncated;
    private string taskHistoryError = "", taskHistoryEventsError = "";
    private string selectedTaskHistoryId = "", restoringTaskHistoryId = "", forgettingTaskHistoryId = "";
    private string historyKindFilter = "all";
    private sealed record ChartPoint(string Model, DateTimeOffset Time, double Quality, string Local);
    private List<ChartPoint>? chartPoints;
    private sealed record MetricSpec(string Key, string Title, string Caption);
    private static readonly MetricSpec[] Metrics =
    [
        new("burnable", L10n.T("有效可烧云量"), L10n.T("前向可烧云占视野比例")),
        new("material", L10n.T("云质厚度"), L10n.T("云体材质负载")),
        new("brightness", L10n.T("亮度色彩"), L10n.T("霞光亮度与色彩评分")),
        new("cloud_layer", L10n.T("主要云层"), L10n.T("云底–云顶 AGL")),
        new("aod", "AOD 550", L10n.T("气溶胶光学厚度 · 通透度")),
        new("incoming", L10n.T("入射受光"), L10n.T("太阳入射照亮云体的因子")),
        new("contrast", L10n.T("返程对比度"), L10n.T("观测者视线大气对比因子")),
        new("duration", L10n.T("持续时长"), L10n.T("核心观赏窗口 · 分钟")),
        new("confidence", L10n.T("输入支持度"), L10n.T("输入覆盖与几何支持 · 非命中概率")),
        new("convective", L10n.T("对流风险"), L10n.T("模式对流风险指标 · 非事件概率")),
    ];
    private static readonly string[] MetricGroups = [L10n.T("云况与色彩"), L10n.T("大气光学"), L10n.T("时效与可靠性")];

    public MainWindow()
    {
        InitializeComponent();
        SystemBackdrop = new MicaBackdrop();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TitleBar);
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1440, 900));
        if (AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = 1120;
            presenter.PreferredMinimumHeight = 720;
        }
        BuildHeroCards();
        AttachUpdater();
        BuildTerrainRegionPicker();
        TargetDate.Date = DateTimeOffset.Now;
        LoadSettings();
        BuildMetrics();
        RestoreSession();
        Navigation.SelectedItem = Navigation.MenuItems[0];
        initialized = true;
        LanguageChoice.SelectedIndex = L10n.Preference switch { "zh" => 1, "en" => 2, _ => 0 };
        ApplyLocalization();
        UpdateRuntimeSummary();
        AddKeyboardAccelerators();
        Root.ActualThemeChanged += (_, _) =>
        {
            if (!initialized) return;
            BuildMetrics();
            RenderResult();
            if (page == "data") _ = LoadInventory();
            UpdateRuntimeSummary();
        };
        Root.Loaded += async (_, _) =>
        {
            await InitializeMap();
            await Connect();
            StartAutoUpdateCheck();
        };
        AppWindow.Closing += (_, args) =>
        {
            if (!HasActiveWork) return;
            args.Cancel = !ConfirmExitWhileBusy();
        };
        Closed += (_, _) =>
        {
            CancelActiveWork();
            engine.Dispose();
        };
        StartSyncTick();
    }

    // ---------- 基础 JSON 容错助手（与 Swift 条件转换同语义） ----------

    internal static string Text(JsonNode? node, string key) => node?[key] is System.Text.Json.Nodes.JsonValue value
        && value.GetValueKind() == System.Text.Json.JsonValueKind.String
        ? value.GetValue<string>() : "";
    private static bool Flag(JsonNode? node, string key) => node?[key]?.GetValue<bool>() ?? false;
    internal static double Number(JsonNode? node, string key)
    {
        if (node?[key] is not System.Text.Json.Nodes.JsonValue value) return double.NaN;
        try
        {
            var number = value.GetValue<double>();
            return double.IsFinite(number) ? number : double.NaN;
        }
        catch (InvalidOperationException) { return double.NaN; }
    }
    internal static long? Integer(JsonNode? node, string key)
    {
        if (node?[key] is not System.Text.Json.Nodes.JsonValue value) return null;
        try
        {
            var number = value.GetValue<double>();
            if (!double.IsFinite(number) || number != Math.Truncate(number)
                || number < long.MinValue || number > long.MaxValue) return null;
            return (long)number;
        }
        catch (InvalidOperationException) { return null; }
    }
    private static bool IsHex64(string value) =>
        value.Length == 64 && value.All(character =>
            character is >= '0' and <= '9' or >= 'a' and <= 'f');

    internal static bool TryUtc(string value, out string normalized)
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

    internal static bool SameUtc(string left, string right)
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

    internal static double Score(JsonNode? node)
    {
        var value = node?["peak_quality_index"] ?? node?["quality_index"];
        try
        {
            var score = value?.GetValue<double>() ?? double.NaN;
            return double.IsFinite(score) ? score : -1;
        }
        catch (InvalidOperationException) { return -1; }
    }

    internal static string TimeLabel(string value) => value.Length >= 16 ? value.Substring(11, 5) : "--";

    /// <summary>经纬度按正负显示 N/S/E/W（简洁化规则：不用固定 N/E）。</summary>
    internal static string LatLonLabel(double lat, double lon) =>
        $"{Math.Abs(lat):0.0000}° {(lat >= 0 ? "N" : "S")}, {Math.Abs(lon):0.0000}° {(lon >= 0 ? "E" : "W")}";

    private static string FormatNumber(JsonNode? node, int digits, string suffix = "") =>
        Number(node, "x") is var value && !double.IsFinite(Number(node, "x")) ? "—"
        : value.ToString($"0.{new string('0', digits)}", CultureInfo.InvariantCulture) + suffix;

    // ---------- 任务历史记录容错（行为与 Swift NativeTaskHistoryItem 一致） ----------

    internal static string? StringOrNull(JsonNode? node) =>
        node is System.Text.Json.Nodes.JsonValue value
        && value.GetValueKind() == System.Text.Json.JsonValueKind.String
            ? value.GetValue<string>() : null;
    internal static string StringOf(JsonNode? node) => StringOrNull(node) ?? "";
    internal static bool? BoolValue(JsonNode? node, string key)
    {
        if (node?[key] is not System.Text.Json.Nodes.JsonValue value) return null;
        return value.GetValueKind() == System.Text.Json.JsonValueKind.True ? true
            : value.GetValueKind() == System.Text.Json.JsonValueKind.False ? false : null;
    }
    internal static double? DoubleValue(JsonNode? node) =>
        node is System.Text.Json.Nodes.JsonValue value && value.TryGetValue<double>(out var number)
        && double.IsFinite(number) ? number : null;
    internal static bool IsTaskId(string value) =>
        value.Length == 64 && value.All(Uri.IsHexDigit);
    private static bool IsTaskTimestamp(string value) =>
        value.Contains('T', StringComparison.Ordinal) && TryUtc(value, out _);

    // ---------- 状态与忙碌管理（UX #5：切页不锁 UI） ----------

    internal bool HasActiveWork => busy || probingDownloads || dataSyncRunning;

    private void SetBusy(bool value)
    {
        busy = value;
        // 顶栏/底部保留进度与取消入口；导航与输入面板保持可用，冲突的
        // 写操作按钮按资源实际禁用。
        PredictButton.IsEnabled = !value && online;
        SaveRetentionButton.IsEnabled = !value && online;
        UpdateRetentionControls();
        SyncRunNowButton.IsEnabled = !value && online && dataSyncRunning == false;
        BusyRing.IsActive = value;
        CancelButton.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
        UpdateTaskHistoryControls();
        UpdateProbeControls();
        UpdateSyncControls();
    }

    private void SetActionBusy(string status)
    {
        Status.Text = status;
    }

    private void UpdateActionButtons()
    {
        PredictButton.IsEnabled = online && !busy && !searching && !locating;
        StarButton.IsEnabled = online && !busy;
    }

    private void Fail(Exception error, string? targetPage = null)
    {
        var message = error is OperationCanceledException ? L10n.T("操作已取消。") : FriendlyMessages.Map(error.Message);
        errorMessage = message;
        errorPage = targetPage ?? page;
        RenderPageErrors();
        Status.Text = error is OperationCanceledException ? L10n.T("已取消") : L10n.T("操作未完成");
        if (errorPage == "settings") SettingsStatusMessage(message, false);
    }

    private void ClearError(string? targetPage = null)
    {
        if (targetPage != null && errorPage != targetPage) return;
        errorMessage = "";
        errorPage = "";
        RenderPageErrors();
    }

    private void OnPageErrorBarClosed(InfoBar sender, InfoBarClosedEventArgs args)
    {
        ClearError();
    }

    private void RenderPageErrors()
    {
        var hasError = errorMessage.Length > 0;
        var forecastActive = hasError && errorPage == page && page is "point" or "regional" or "nowcast";
        ApplyErrorBar(ErrorBar, forecastActive ? errorMessage : "");
        ApplyErrorBar(DataErrorBar, hasError && errorPage == "data" && page == "data" ? errorMessage : "");
        ApplyErrorBar(HistoryErrorBar, hasError && errorPage == "history" && page == "history" ? errorMessage : "");
        ApplyErrorBar(SettingsErrorBar, hasError && errorPage == "settings" && page == "settings" ? errorMessage : "");
    }

    private static void ApplyErrorBar(InfoBar? bar, string message)
    {
        if (bar == null) return;
        if (string.IsNullOrEmpty(message))
        {
            bar.IsOpen = false;
            bar.Message = "";
            bar.Content = null;
            return;
        }
        bar.Message = message;
        bar.Content = new TextBlock
        {
            Text = message, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
            FontSize = 11,
        };
        bar.IsOpen = true;
    }

    private void SettingsStatusMessage(string message, bool transient)
    {
        RetentionStatus.Text = message;
    }

    private void CancelActiveWork()
    {
        operation?.Cancel();
        profileOperation?.Cancel();
        frozenProfileOperation?.Cancel();
        probeOperation?.Cancel();
        syncOperation?.Cancel();
        taskEventsOperation?.Cancel();
    }

    /// <summary>退出确认（UX #12）：仍有前台任务/探测/同步时要求显式确认。</summary>
    private static bool ConfirmExitWhileBusy()
    {
        // Closing 回调是同步的；WinUI 异步对话框无法在此等待，用原生 MessageBox。
        var result = MessageBoxW(0, L10n.T("任务仍在运行，确定退出？\n\n当前状态：退出会中断正在进行的预测、下载或同步；已完成的上一份有效结果不会丢失。"),
            "FireSky", 0x30 /* MB_ICONWARNING */ | 0x4 /* MB_YESNO */ | 0x10000 /* MB_TOPMOST */);
        return result == 6 /* IDYES */;
    }

    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern int MessageBoxW(nint hWnd, string text, string caption, uint type);

    internal Brush ThemeBrush(string key)
    {
        var themeKey = Root?.ActualTheme == ElementTheme.Dark ? "Dark" : "Light";
        if (Application.Current.Resources.ThemeDictionaries.TryGetValue(themeKey, out var dictObj)
            && dictObj is ResourceDictionary dict
            && dict.TryGetValue(key, out var themed) && themed is Brush themedBrush)
            return themedBrush;
        if (Application.Current.Resources.TryGetValue(key, out var value) && value is Brush brush)
            return brush;
        return (Brush)Application.Current.Resources["LayerFillColorDefaultBrush"];
    }

    // ---------- 连接与启动 ----------

    private async Task Connect()
    {
        online = false;
        UpdateRuntimeSummary();
        Status.Text = L10n.T("正在连接引擎");
        try
        {
            engine.Start(WorkspacePath.Text, EnginePath.Text);
            var hello = await engine.Call("hello");
            if (Number(hello, "protocol") != 1) throw new InvalidOperationException(L10n.T("引擎协议版本不匹配。"));
            var capabilities = hello["capabilities"] as JsonArray ?? [];
            foreach (var required in new[] { "profile_query", "task_history", "task_events", "result_restore" })
            {
                if (!capabilities.Any(item => string.Equals(item?.GetValue<string>(), required,
                        StringComparison.Ordinal)))
                    throw new InvalidOperationException($"Rust 引擎缺少 {required} 能力。");
            }
            regionDefinitions = (hello["regions"] as JsonArray ?? [])
                .OfType<JsonObject>().Select(node => node.DeepClone() as JsonObject ?? node).ToList();
            BuildRegionPresets();
            RebuildSyncAnchorPicker();
            online = true;
            ClearError();
            Status.Text = L10n.T("本地引擎已连接");
            await Task.WhenAll(
                LoadRetentionSettings(),
                LoadCamsConfiguration(),
                LoadDataSyncSettings());
            SaveSettings();
            await MaybeCamsReminder();
        }
        catch (Exception error) { Fail(error); }
        finally
        {
            UpdateActionButtons();
            UpdateRuntimeSummary();
        }
    }

    /// <summary>启动时 CAMS 未配置 → 提醒直达设置→数据（macOS openCAMSSetup 基准）。</summary>
    private async Task MaybeCamsReminder()
    {
        if (camsConfigured || !online) return;
        var dialog = new ContentDialog
        {
            Title = L10n.T("CAMS 数据源未配置"),
            Content = new TextBlock
            {
                Text = L10n.T("尚未配置 CAMS Key，本地数据缺失时无法下载 CAMS 数据。可以现在到「设置 → 数据」粘贴 ADS Personal Access Token；已有完整本地数据时可继续使用。"),
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = L10n.T("去配置"),
            CloseButtonText = L10n.T("稍后"),
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = Root.XamlRoot,
        };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary)
        {
            Navigation.SelectedItem = Navigation.MenuItems[4] is { } settingsItem
                ? Navigation.MenuItems[4] : Navigation.SettingsItem;
            settingsCategory = "data";
            ApplySettingsCategory();
            CamTutorial.IsExpanded = true;
        }
    }

    // ---------- 设置持久化与会话持久化（UX #7） ----------

    private JsonObject LoadSettingsJson()
    {
        try { return JsonNode.Parse(File.ReadAllText(settingsFile)) as JsonObject ?? new JsonObject(); }
        catch (Exception) { return []; }
    }

    private void LoadSettings()
    {
        var saved = LoadSettingsJson();
        var root = Environment.GetEnvironmentVariable("FIRESKY_WORKSPACE") ?? "";
        WorkspacePath.Text = root.Length > 0 ? root : Text(saved, "workspace");
        EnginePath.Text = Environment.GetEnvironmentVariable("FIRESKY_RUST_ENGINE_BIN")
            ?? Text(saved, "engineBinary");
        Appearance.SelectedIndex = Text(saved, "appearance") switch { "light" => 1, "dark" => 2, _ => 0 };
        Root.RequestedTheme = Appearance.SelectedIndex switch
        {
            1 => ElementTheme.Light, 2 => ElementTheme.Dark, _ => ElementTheme.Default,
        };
        ecmwfSource = Text(saved, "ecmwfSource") is { Length: > 0 } source && source is "ecmwf" or "aws" or "google"
            ? source : "ecmwf";
        EcmwfSourcePicker.SelectedIndex = ecmwfSource switch { "aws" => 1, "google" => 2, _ => 0 };
        elevationSource = Text(saved, "elevationSource") is { Length: > 0 } elevation && elevation is "auto" or "http" or "srtm"
            ? elevation : "auto";
        ElevationSourcePicker.SelectedIndex = elevationSource switch { "http" => 1, "srtm" => 2, _ => 0 };
        favorites = (saved["favorites"] as JsonArray ?? [])
            .OfType<JsonObject>().Select(node => node.DeepClone() as JsonObject ?? node).ToList();
        RenderFavorites();
        // 草稿随分类切换保留（v1.2.0 设置分类合同）。
        settingsCategory = Text(saved, "settingsCategory") is { Length: > 0 } category
            && category is "general" or "data" or "location" or "advanced" ? category : "data";
        SettingsCategories.SelectedIndex = settingsCategory switch
        {
            "general" => 0, "data" => 1, "location" => 2, _ => 3,
        };
        updater.SetAutoCheck(Flag(saved, "autoUpdateCheck") || saved["autoUpdateCheck"] == null);
        var savedDefaults = (saved["defaultRegionalPreset"] as JsonArray ?? [])
            .Select(node => StringOrNull(node))
            .Where(code => !string.IsNullOrWhiteSpace(code))
            .Cast<string>()
            .Distinct()
            .ToList();
        defaultRegionalPreset = savedDefaults.Count > 0 ? savedDefaults : ["china"];
        showRegionalSmooth = saved["showRegionalSmooth"] == null || Flag(saved, "showRegionalSmooth");
        if (ShowSmoothToggle != null) ShowSmoothToggle.IsChecked = showRegionalSmooth;
        if (saved["lastSession"] == null)
        {
            selectedRegions.Clear();
            foreach (var code in defaultRegionalPreset) selectedRegions.Add(code);
        }
        ApplySettingsCategory();
    }

    private void SaveSettings()
    {
        var saved = LoadSettingsJson();
        saved["workspace"] = WorkspacePath.Text;
        saved["engineBinary"] = EnginePath.Text;
        saved["appearance"] = TagOf(Appearance.SelectedItem);
        saved["ecmwfSource"] = ecmwfSource;
        saved["elevationSource"] = elevationSource;
        saved["settingsCategory"] = settingsCategory;
        saved["autoUpdateCheck"] = updater.AutoCheck;
        saved["showRegionalSmooth"] = showRegionalSmooth;
        saved["defaultRegionalPreset"] = new JsonArray(defaultRegionalPreset.Select(code => (JsonNode)JsonValue.Create(code)!).ToArray());
        saved["favorites"] = new JsonArray(favorites.Select(node => (JsonNode)node.DeepClone()).ToArray());
        Directory.CreateDirectory(Path.GetDirectoryName(settingsFile)!);
        File.WriteAllText(settingsFile, saved.ToJsonString());
    }

    private static string TagOf(object? item) => (item as ComboBoxItem)?.Tag?.ToString()
        ?? (item as RadioButton)?.Tag?.ToString() ?? (item as FrameworkElement)?.Name ?? "";

    private void SaveSession()
    {
        if (restoringSession) return;
        var saved = LoadSettingsJson();
        saved["lastSession"] = new JsonObject
        {
            ["name"] = LocationName.Text,
            ["lat"] = Latitude.Value, ["lon"] = Longitude.Value,
            ["elevation_m"] = Elevation.Value,
            ["timezone_name"] = Timezone.Text,
            ["metadata_ready"] = MetadataReady.IsChecked == true,
            ["date"] = TargetDate.Date?.ToUnixTimeSeconds(),
            ["date_tab"] = dateTab,
            ["phenomenon"] = TagOf(Phenomenon.SelectedItem),
            ["page"] = page,
            ["selected_regions"] = new JsonArray(selectedRegions.OrderBy(v => v).Select(v => JsonValue.Create(v)).ToArray<JsonNode>()),
            ["custom_region"] = customRegion,
            ["west"] = West.Value, ["east"] = East.Value, ["south"] = South.Value, ["north"] = North.Value,
        };
        Directory.CreateDirectory(Path.GetDirectoryName(settingsFile)!);
        File.WriteAllText(settingsFile, saved.ToJsonString());
    }

    private void ScheduleSessionSave()
    {
        if (restoringSession || !initialized) return;
        // 500ms 防抖，对齐 macOS sessionChanged。
        sessionSaveTimer ??= DispatcherQueue.CreateTimer();
        sessionSaveTimer.IsRepeating = false;
        sessionSaveTimer.Interval = TimeSpan.FromMilliseconds(500);
        sessionSaveTimer.Tick += (_, _) =>
        {
            sessionSaveTimer.Stop();
            SaveSession();
        };
        sessionSaveTimer.Start();
    }

    private void RestoreSession()
    {
        var saved = LoadSettingsJson()["lastSession"] as JsonObject;
        if (saved == null) return;
        restoringSession = true;
        try
        {
            if (Text(saved, "name") is { Length: > 0 } name) LocationName.Text = name;
            if (Number(saved, "lat") is { } lat && double.IsFinite(lat)) Latitude.Value = lat;
            if (Number(saved, "lon") is { } lon && double.IsFinite(lon)) Longitude.Value = lon;
            if (Number(saved, "elevation_m") is { } elevation && double.IsFinite(elevation)) Elevation.Value = elevation;
            if (Text(saved, "timezone_name") is { Length: > 0 } timezone) Timezone.Text = timezone;
            if (Flag(saved, "metadata_ready")) MetadataReady.IsChecked = true;
            if (Integer(saved, "date") is { } epoch)
                TargetDate.Date = DateTimeOffset.FromUnixTimeSeconds(epoch);
            dateTab = Text(saved, "date_tab") is { Length: > 0 } tab && tab is "今天" or "明天" or "自定义" ? tab : "今天";
            if (Text(saved, "phenomenon") is { Length: > 0 } phenomenon && phenomenon is "dawn" or "dusk")
                Phenomenon.SelectedIndex = phenomenon == "dawn" ? 0 : 1;
            selectedRegions.Clear();
            foreach (var region in saved["selected_regions"] as JsonArray ?? [])
                if (region?.GetValue<string>() is { Length: > 0 } code) selectedRegions.Add(code);
            customRegion = Flag(saved, "custom_region");
            if (selectedRegions.Count == 0 && !customRegion)
            {
                foreach (var code in defaultRegionalPreset) selectedRegions.Add(code);
            }
            CustomRegionToggle.IsChecked = customRegion;
            CustomBoundsFields.Visibility = customRegion ? Visibility.Visible : Visibility.Collapsed;
            if (Number(saved, "west") is { } west && double.IsFinite(west)) West.Value = west;
            if (Number(saved, "east") is { } east && double.IsFinite(east)) East.Value = east;
            if (Number(saved, "south") is { } south && double.IsFinite(south)) South.Value = south;
            if (Number(saved, "north") is { } north && double.IsFinite(north)) North.Value = north;
            SyncDateTab();
        }
        finally { restoringSession = false; }
    }

    // ---------- 导航 ----------

    private void OnNavigationChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        page = args.IsSettingsSelected ? "settings"
            : (args.SelectedItem as NavigationViewItem)?.Tag?.ToString() ?? "point";
        if (!initialized) return;
        SaveSession();
        ApplyPage();
    }

    private void ApplyPage()
    {
        ForecastPage.Visibility = page is "point" or "regional" or "nowcast" ? Visibility.Visible : Visibility.Collapsed;
        DataPage.Visibility = page == "data" ? Visibility.Visible : Visibility.Collapsed;
        HistoryPage.Visibility = page == "history" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility = page == "settings" ? Visibility.Visible : Visibility.Collapsed;
        PageTitle.Text = L10n.Translate(page switch
        {
            "regional" => L10n.T("预测区域"), "nowcast" => L10n.T("实况观测"), "data" => L10n.T("数据管理"),
            "history" => L10n.T("任务历史"), "settings" => L10n.T("设置"), _ => L10n.T("观测点"),
        });
        PageSubtitle.Text = page switch
        {
            "point" or "nowcast" => $"{LocationName.Text} · {LatLonLabel(Latitude.Value, Longitude.Value)}",
            "regional" => RegionalTitle(),
            _ => "",
        };
        PointFields.Visibility = page == "regional" ? Visibility.Collapsed : Visibility.Visible;
        RegionFields.Visibility = page == "regional" ? Visibility.Visible : Visibility.Collapsed;
        StarButton.Visibility = page == "regional" ? Visibility.Collapsed : Visibility.Visible;
        SourcesLabel.Text = page == "nowcast" ? L10n.T("GFS · CAMS · 卫星帧") : "GFS + EC";
        PredictButton.Content = page == "nowcast" ? L10n.T("检查并预热") : L10n.T("开始预测");
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
        RenderPageErrors();
        RenderResult();
        _ = UpdateMap();
        if (page == "data")
        {
            _ = LoadInventory();
            _ = LoadTerrain();
        }
        if (page == "history") _ = LoadTaskHistory();
        if (page == "settings")
        {
            _ = LoadRetentionSettings();
            UpdateRuntimeSummary();
        }
    }

    private string RegionalTitle()
    {
        var names = regionDefinitions
            .Where(region => selectedRegions.Contains(Text(region, "code")))
            .Select(region => Text(region, "label") is { Length: > 0 } label ? label : Text(region, "code"))
            .ToList();
        if (customRegion) names.Add(L10n.T("自定义范围"));
        return names.Count == 0 ? L10n.T("区域") : string.Join("、", names);
    }

    // ---------- 键盘快捷键（⌘1-5 / ⌘, 的 Windows 等价） ----------

    private void AddKeyboardAccelerators()
    {
        void Add(int index, VirtualKey key, string target, Action<string> apply)
        {
            var accelerator = new KeyboardAccelerator
            {
                Modifiers = VirtualKeyModifiers.Control, Key = key,
            };
            accelerator.Invoked += (_, args) =>
            {
                apply(target);
                args.Handled = true;
            };
            Root.KeyboardAccelerators.Add(accelerator);
        }
        Add(0, VirtualKey.Number1, "point", NavigateTo);
        Add(1, VirtualKey.Number2, "regional", NavigateTo);
        Add(2, VirtualKey.Number3, "nowcast", NavigateTo);
        Add(3, VirtualKey.Number4, "data", NavigateTo);
        Add(4, VirtualKey.Number5, "history", NavigateTo);
        var settings = new KeyboardAccelerator
        {
            Modifiers = VirtualKeyModifiers.Control, Key = (VirtualKey)188,
        };
        settings.Invoked += (_, args) =>
        {
            NavigateTo("settings");
            args.Handled = true;
        };
        Root.KeyboardAccelerators.Add(settings);
    }

    private void NavigateTo(string target)
    {
        if (target == "settings")
        {
            Navigation.SelectedItem = Navigation.SettingsItem;
            return;
        }
        var item = Navigation.MenuItems.OfType<NavigationViewItem>()
            .FirstOrDefault(candidate => candidate.Tag?.ToString() == target);
        if (item != null) Navigation.SelectedItem = item;
    }

    // ---------- 地图 ----------

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
                    switch (Text(message, "type"))
                    {
                        case "ready":
                            mapReady = true;
                            await Map.ExecuteScriptAsync(MapEnhancerScript());
                            await UpdateMap();
                            break;
                        case "point" when !busy:
                            await HandleMapPoint(Number(message, "lat"), Number(message, "lon"));
                            break;
                        case "region" when !busy:
                            ApplyMapRegion(message);
                            break;
                    }
                }
                catch (Exception error) { Fail(error); }
            };
            Map.Source = new Uri("https://firesky.local/map.html");
        }
        catch (Exception error) { Fail(error); }
    }

    private async Task HandleMapPoint(double lat, double lon)
    {
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
            Latitude.Value = lat; Longitude.Value = lon; LocationName.Text = L10n.T("地图选点");
            await LookupMetadata();
        }
    }

    /// <summary>地图拖拽绘制的区域范围（跨 180° 时按引擎短弧语义保存 west&gt;east）。</summary>
    private void ApplyMapRegion(JsonNode? message)
    {
        var west = Number(message, "west");
        var east = Number(message, "east");
        var south = Number(message, "south");
        var north = Number(message, "north");
        if (!double.IsFinite(west) || !double.IsFinite(east)
            || !double.IsFinite(south) || !double.IsFinite(north)) return;
        if (page != "regional") return;
        suppressDraftEvents = true;
        try
        {
            West.Value = west; East.Value = east; South.Value = south; North.Value = north;
            CustomRegionToggle.IsChecked = true;
        }
        finally { suppressDraftEvents = false; }
        result = null; ClearRegionalProfile(); RenderResult(); _ = UpdateMap();
        ScheduleSessionSave();
    }

    private async Task UpdateMap(bool forceFit = false)
    {
        if (!initialized || !mapReady || !double.IsFinite(Latitude.Value) || !double.IsFinite(Longitude.Value)) return;
        var report = SelectedReport();
        var regionalBounds = page == "regional" ? RegionalActiveBounds() : null;
        var west = regionalBounds?.West ?? West.Value;
        var south = regionalBounds?.South ?? South.Value;
        var east = regionalBounds?.East ?? East.Value;
        var north = regionalBounds?.North ?? North.Value;
        var state = new JsonObject
        {
            ["lat"] = Latitude.Value, ["lon"] = Longitude.Value, ["regional"] = page == "regional",
            ["west"] = west, ["east"] = east, ["south"] = south, ["north"] = north,
            ["points"] = resultKind == page ? report?["points"]?.DeepClone() : null,
            ["show_grid"] = showRegionalGrid,
            ["show_samples"] = showRegionalSamples,
            ["show_smooth"] = showRegionalSmooth,
            ["force_fit"] = forceFit,
            ["samples"] = page == "regional" && resultKind == page && showRegionalSamples
                ? report?["points"]?.DeepClone() : null,
            ["selected_time"] = page == "point" && pointSelectedTime.Length > 0 ? pointSelectedTime : null,
        };
        try { await Map.ExecuteScriptAsync("window.fireSky.update(" + state.ToJsonString() + ")"); }
        catch (Exception error) { Fail(error, page); }
    }

    internal bool showRegionalGrid = true;
    internal bool showRegionalSamples = true;
    internal bool showRegionalSmooth = true;

    // ---------- 位置搜索 / 定位（UX #5：轻量任务不占全局 busy） ----------

    private async void OnSearch(AutoSuggestBox sender, AutoSuggestBoxQuerySubmittedEventArgs args)
    {
        if (!online || searching || locating || string.IsNullOrWhiteSpace(args.QueryText)) return;
        searching = true;
        UpdateActionButtons();
        Status.Text = L10n.T("正在搜索地点");
        try
        {
            var point = await engine.Call("search", new() { ["query"] = args.QueryText });
            Latitude.Value = Number(point, "lat"); Longitude.Value = Number(point, "lon");
            LocationName.Text = Text(point, "name"); Elevation.Value = Number(point, "elevation_m");
            Timezone.Text = Text(point, "timezone_name");
            elevationSourceActual = Text(point, "source");
            MetadataReady.IsChecked = true;
            result = null; RenderResult(); await UpdateMap();
            Status.Text = L10n.T("位置已就绪");
        }
        catch (Exception error) { Fail(error); }
        finally
        {
            searching = false;
            UpdateActionButtons();
        }
    }

    private async void OnLocate(object sender, RoutedEventArgs args)
    {
        if (!online || searching || locating || busy) return;
        locating = true;
        UpdateActionButtons();
        Status.Text = L10n.T("正在获取当前位置");
        try
        {
            var access = await Geolocator.RequestAccessAsync();
            if (access != GeolocationAccessStatus.Allowed) throw new InvalidOperationException(L10n.T("定位未获授权，可在地图上选点。"));
            var locator = new Geolocator { DesiredAccuracyInMeters = 100 };
            var point = await locator.GetGeopositionAsync(TimeSpan.FromMinutes(2), TimeSpan.FromSeconds(20));
            if (point.Coordinate.Accuracy > 5000 || DateTimeOffset.Now - point.Coordinate.Timestamp > TimeSpan.FromMinutes(2))
                throw new InvalidOperationException(L10n.T("定位精度不足或位置已过期。"));
            Latitude.Value = point.Coordinate.Point.Position.Latitude;
            Longitude.Value = point.Coordinate.Point.Position.Longitude;
            LocationName.Text = L10n.T("当前位置");
            await LookupMetadata();
        }
        catch (Exception error) { Fail(error); }
        finally
        {
            locating = false;
            UpdateActionButtons();
        }
    }

    private async Task LookupMetadata()
    {
        if (!online) return;
        var revision = ++locationRevision;
        var a = Latitude.Value; var b = Longitude.Value;
        MetadataReady.IsChecked = false;
        Status.Text = L10n.T("正在获取海拔与时区");
        try
        {
            // v1.1.1 契约：metadata 透传 elevation_source；响应 source 为实际来源。
            var response = await engine.Call("metadata", new()
            {
                ["lat"] = a, ["lon"] = b, ["elevation_source"] = elevationSource,
            });
            if (revision != locationRevision || a != Latitude.Value || b != Longitude.Value) return;
            Elevation.Value = Number(response, "elevation_m");
            Timezone.Text = Text(response, "timezone_name");
            elevationSourceActual = Text(response, "source");
            RenderElevationSourceNote();
            MetadataReady.IsChecked = true; Status.Text = L10n.T("位置已就绪");
            result = null; RenderResult(); await UpdateMap();
            ScheduleSessionSave();
        }
        catch (Exception error) { if (revision == locationRevision) Fail(error); }
    }

    private async void OnMetadata(object sender, RoutedEventArgs args) => await LookupMetadata();
    private async void OnCoordinatesChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        locationRevision++;
        MetadataReady.IsChecked = false;
        elevationSourceActual = "";
        RenderElevationSourceNote();
        result = null; ClearRegionalProfile(); RenderResult(); _ = UpdateMap();
        ScheduleSessionSave();
    }
    private void OnBoundsChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!initialized || restoringSession || suppressDraftEvents) return;
        result = null; ClearRegionalProfile(); RenderResult(); _ = UpdateMap();
        ScheduleSessionSave();
    }

    private void RenderElevationSourceNote()
    {
        ElevationSourceNote.Text =
            $"海拔来源策略 {ElevationSourceLabel()} · 实际 {ElevationSourceActualLabel()}";
    }

    internal string ElevationSourceLabel() => elevationSource switch
    {
        "http" => L10n.T("仅 HTTP（open-meteo）"),
        "srtm" => L10n.T("SRTM 下载（本地时区 + 瓦片）"),
        _ => L10n.T("自动（HTTP 优先，SRTM 兜底）"),
    };

    internal string ElevationSourceActualLabel() => elevationSourceActual switch
    {
        "open-meteo-point-metadata" => "open-meteo（HTTP）",
        "local-timezone+srtm" => L10n.T("本地时区 + SRTM"),
        "" => L10n.T("未获取"),
        _ => elevationSourceActual,
    };

    // ---------- 收藏（UX #7） ----------

    private void OnStarLocation(object sender, RoutedEventArgs args)
    {
        if (!online || busy) return;
        if (MetadataReady.IsChecked != true)
        {
            Fail(new InvalidOperationException(L10n.T("请先确认海拔与时区，再收藏该地点。")));
            return;
        }
        favorites.RemoveAll(favorite =>
            Math.Abs(Number(favorite, "lat") - Latitude.Value) < 1e-9
            && Math.Abs(Number(favorite, "lon") - Longitude.Value) < 1e-9);
        favorites.Add(new JsonObject
        {
            ["name"] = LocationName.Text,
            ["lat"] = Latitude.Value, ["lon"] = Longitude.Value,
            ["elevation_m"] = Elevation.Value, ["timezone_name"] = Timezone.Text,
        });
        SaveSettings();
        RenderFavorites();
        Status.Text = L10n.T("已收藏当前位置");
    }

    private void RenderFavorites()
    {
        FavoritesHost.Children.Clear();
        FavoritesExpander.Visibility = favorites.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
        foreach (var favorite in favorites)
        {
            var name = Text(favorite, "name");
            var lat = Number(favorite, "lat");
            var lon = Number(favorite, "lon");
            var row = new Grid { ColumnSpacing = 8 };
            row.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            var apply = new Button
            {
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Left,
                Padding = new Thickness(8, 4, 8, 4),
                Background = new SolidColorBrush(Colors.Transparent),
                BorderThickness = new Thickness(0),
                Content = new TextBlock
                {
                    Text = $"{name}  {LatLonLabel(lat, lon)}",
                    TextTrimming = TextTrimming.CharacterEllipsis,
                },
            };
            apply.Click += async (_, _) =>
            {
                LocationName.Text = name;
                suppressDraftEvents = true;
                try
                {
                    Latitude.Value = lat; Longitude.Value = lon;
                    if (Number(favorite, "elevation_m") is { } elevation && double.IsFinite(elevation)) Elevation.Value = elevation;
                    if (Text(favorite, "timezone_name") is { Length: > 0 } timezone) Timezone.Text = timezone;
                }
                finally { suppressDraftEvents = false; }
                MetadataReady.IsChecked = true;
                elevationSourceActual = "";
                result = null; RenderResult(); await UpdateMap();
                ScheduleSessionSave();
            };
            row.Children.Add(apply);
            var remove = new Button
            {
                Padding = new Thickness(6, 4, 6, 4),
                BorderThickness = new Thickness(0),
                Background = new SolidColorBrush(Colors.Transparent),
                Content = new SymbolIcon(Symbol.Delete),
            };
            ToolTipService.SetToolTip(remove, L10n.T("删除收藏"));
            remove.Click += (_, _) =>
            {
                favorites.RemoveAll(candidate =>
                    Math.Abs(Number(candidate, "lat") - lat) < 1e-9
                    && Math.Abs(Number(candidate, "lon") - lon) < 1e-9);
                SaveSettings();
                RenderFavorites();
            };
            Grid.SetColumn(remove, 1);
            row.Children.Add(remove);
            FavoritesHost.Children.Add(row);
        }
    }

    // ---------- 日期 ----------

    private void OnDateTabChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        dateTab = DateTabs.SelectedIndex switch { 0 => "今天", 1 => "明天", _ => "自定义" };
        TargetDate.Visibility = dateTab == "自定义" ? Visibility.Visible : Visibility.Collapsed;
        if (dateTab == "今天") TargetDate.Date = DateTimeOffset.Now;
        if (dateTab == "明天") TargetDate.Date = DateTimeOffset.Now.AddDays(1);
        if (page == "nowcast") UpdateNowcastBanners(resultKind == "nowcast" ? SelectedReport() : null);
        ScheduleSessionSave();
    }

    private void OnCustomDateChanged(CalendarDatePicker sender, CalendarDatePickerDateChangedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        if (dateTab != "自定义") { dateTab = "自定义"; DateTabs.SelectedIndex = 2; TargetDate.Visibility = Visibility.Visible; }
        if (page == "nowcast") UpdateNowcastBanners(resultKind == "nowcast" ? SelectedReport() : null);
        ScheduleSessionSave();
    }

    /// <summary>按当前时区把 dateTab 对齐到 date 实际落在的今天/明天/自定义。</summary>
    private void SyncDateTab()
    {
        var date = TargetDate.Date?.LocalDateTime.Date ?? DateTime.Today;
        var today = DateTime.Today;
        var tomorrow = today.AddDays(1);
        DateTabs.SelectedIndex = date == today ? 0 : date == tomorrow ? 1 : 2;
        TargetDate.Visibility = dateTab == "自定义" ? Visibility.Visible : Visibility.Collapsed;
    }

    // ---------- 区域预设 ----------

    private void BuildRegionPresets()
    {
        RegionPresetsHost.Children.Clear();
        foreach (var region in regionDefinitions)
        {
            var code = Text(region, "code");
            if (code.Length == 0) continue;
            var label = Text(region, "label") is { Length: > 0 } name ? name : code;
            var toggle = new CheckBox { Content = label, IsChecked = selectedRegions.Contains(code) };
            toggle.Checked += (_, _) => { selectedRegions.Add(code); SessionAndRender(); };
            toggle.Unchecked += (_, _) => { selectedRegions.Remove(code); SessionAndRender(); };
            RegionPresetsHost.Children.Add(toggle);
        }
    }

    private void OnCustomRegionToggled(object sender, RoutedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        customRegion = CustomRegionToggle.IsChecked == true;
        CustomBoundsFields.Visibility = customRegion ? Visibility.Visible : Visibility.Collapsed;
        SessionAndRender();
    }

    private void SessionAndRender()
    {
        result = null; RenderResult(); ScheduleSessionSave(); _ = UpdateMap();
    }

    /// <summary>区域地图叠加开关：平滑渲染、网格线与采样点标注（对齐 macOS regional-6）。</summary>
    private void OnMapOverlayToggled(object sender, RoutedEventArgs args)
    {
        if (!initialized || ShowGridToggle == null) return;
        showRegionalSmooth = ShowSmoothToggle?.IsChecked == true;
        showRegionalGrid = ShowGridToggle.IsChecked == true;
        showRegionalSamples = ShowSamplesToggle?.IsChecked == true;
        SaveSettings();
        _ = UpdateMap();
    }

    private void RebuildSyncAnchorPicker()
    {
        SyncAnchorPicker.Items.Clear();
        SyncAnchorPicker.Items.Add(new ComboBoxItem { Content = L10n.T("未设置"), Tag = "" });
        foreach (var region in regionDefinitions)
        {
            var code = Text(region, "code");
            if (code.Length == 0) continue;
            SyncAnchorPicker.Items.Add(new ComboBoxItem
            {
                Content = Text(region, "label") is { Length: > 0 } label ? label : code,
                Tag = code,
            });
        }
        SyncAnchorPicker.SelectedIndex = 0;
    }

    // ---------- 预测负载与生命周期 ----------

    private string TargetDateLabel() =>
        TargetDate.Date?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? "";

    private JsonObject Payload()
    {
        if (TargetDate.Date == null) throw new InvalidOperationException(L10n.T("请选择日期。"));
        if (page != "regional" && MetadataReady.IsChecked != true)
            throw new InvalidOperationException(L10n.T("请获取海拔与时区，或手动确认位置数据。"));
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
            throw new InvalidOperationException(L10n.T("区域范围无效。"));
        if (page == "regional" && !customRegion && selectedRegions.Count == 0)
            throw new InvalidOperationException(L10n.T("请至少选择一个预设区域或勾选自定义范围。"));
        var payload = new JsonObject
        {
            ["name"] = LocationName.Text, ["lat"] = Latitude.Value, ["lon"] = Longitude.Value,
            ["elevation_m"] = Elevation.Value, ["timezone_name"] = Timezone.Text,
            ["target_date"] = TargetDateLabel(),
            ["phenomenon"] = TagOf(Phenomenon.SelectedItem),
            ["model_source"] = "both", ["workers"] = 1,
            ["forecast_mode"] = "model", ["satellite_source"] = "off",
            // v1.1.1 契约：EC 下载源与海拔来源随每次请求透传。
            ["ecmwf_source"] = ecmwfSource,
            ["elevation_source"] = elevationSource,
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
            // 区域单模型（macOS Store.regionalModel 基准）：默认 GFS，可显式选 EC。
            payload["model_source"] = TagOf(RegionalModel.SelectedItem) is { Length: > 0 } model
                && model is "gfs" or "ec" ? model : "gfs";
            var regions = new JsonArray();
            foreach (var code in selectedRegions.OrderBy(v => v)) regions.Add(code);
            if (customRegion) regions.Add("custom");
            payload["regions"] = regions;
            if (customRegion)
            {
                payload["custom_bounds"] = new JsonObject
                {
                    ["west"] = West.Value, ["east"] = East.Value, ["south"] = South.Value, ["north"] = North.Value,
                };
            }
            payload["custom_timezone_name"] = Timezone.Text;
            payload["spacing_deg"] = .25;
        }
        return payload;
    }

    private async void OnPredict(object sender, RoutedEventArgs args)
    {
        if (!online || busy || searching || locating) return;
        JsonObject payload;
        try { payload = Payload(); } catch (Exception error) { Fail(error, page); return; }
        var kind = page;
        var dialog = new PreflightDialog(engine, kind, payload) { XamlRoot = Root.XamlRoot };
        await dialog.ShowAsync();
        if (dialog.ConfirmedSession is not { } token) return;
        await RunForecast(token, kind, payload, dialog.CycleAgeConfirmed);
    }

    private async Task RunForecast(string token, string kind, JsonObject payload, bool cycleAgeConfirmed)
    {
        SetBusy(true);
        operation = new();
        ClearError(kind);
        Status.Text = L10n.T("正在预测");
        try
        {
            var parameters = new JsonObject { ["session"] = token, ["confirmed"] = true };
            if (cycleAgeConfirmed) parameters["cycle_age_confirmed"] = true;
            var response = await engine.Call("forecast", parameters,
                progress: e => DispatcherQueue.TryEnqueue(() =>
                {
                    var progressPercent = DoubleValue(e["percent"]) is { } p
                        ? Math.Clamp(p, 0, 100) : 0;
                    Status.Text = e["message"]?.GetValue<string>() is { Length: > 0 } message
                        ? message
                        : $"正在计算 {Text(e, "source")} · {progressPercent:0}%";
                }),
                cancellation: operation.Token);
            operation.Token.ThrowIfCancellationRequested();
            AdoptResult(response, kind, payload);
            if (result?["model_results"]?["gfs"] == null && result?["model_results"]?["ec"] != null)
                ResultModel.SelectedIndex = 1;
            Status.Text = L10n.T("预测完成");
            RenderResult();
            await UpdateMap();
            SaveSession();
        }
        catch (Exception error) { Fail(error, kind); }
        finally { SetBusy(false); }
    }

    /// <summary>统一结果入口（UX #1）：预测与历史恢复都经这里，记录结果原始参数。</summary>
    private void AdoptResult(JsonObject response, string kind, JsonObject? parameters)
    {
        resultParams = parameters?.DeepClone() as JsonObject;
        result = response;
        resultKind = kind;
        pointSelectedTime = "";
    }

    private void OnRecalculate(object sender, RoutedEventArgs args)
    {
        // 「用当前参数重算」= 按当前表单走完整预检流程（失败/取消不覆盖旧结果）。
        OnPredict(sender, args);
    }

    /// <summary>当前表单是否与结果原始参数不一致（仅在结果归属当前页面时生效，不跨页误报）。</summary>
    private bool ResultParametersStale
    {
        get
        {
            if (result == null || resultParams == null || resultKind != page) return false;
            bool SameString(string key, string current) =>
                resultParams![key] is not System.Text.Json.Nodes.JsonValue value
                || value.GetValueKind() != System.Text.Json.JsonValueKind.String
                || value.GetValue<string>() == current;
            bool SameNumber(string key, double current) =>
                resultParams![key] is not System.Text.Json.Nodes.JsonValue value
                || !value.TryGetValue<double>(out var saved)
                || Math.Abs(saved - current) < 1e-9;
            if (!SameString("target_date", TargetDateLabel())) return true;
            if (!SameString("phenomenon", TagOf(Phenomenon.SelectedItem))) return true;
            if (!SameString("ecmwf_source", ecmwfSource)) return true;
            if (resultKind == "regional")
            {
                if (!SameString("model_source", TagOf(RegionalModel.SelectedItem))) return true;
                if (resultParams["regions"] is JsonArray regions)
                {
                    var current = selectedRegions.OrderBy(v => v).ToList();
                    if (customRegion) current.Add("custom");
                    var saved = regions.Select(node => node?.GetValue<string>() ?? "").OrderBy(v => v).ToList();
                    if (!saved.SequenceEqual(current)) return true;
                }
                if (customRegion && resultParams["custom_bounds"] is JsonObject bounds)
                {
                    if (!SameNumberNested(bounds, "west", West.Value)
                        || !SameNumberNested(bounds, "east", East.Value)
                        || !SameNumberNested(bounds, "south", South.Value)
                        || !SameNumberNested(bounds, "north", North.Value)) return true;
                }
            }
            else
            {
                if (!SameNumber("lat", Latitude.Value) || !SameNumber("lon", Longitude.Value)) return true;
                if (!SameNumber("elevation_m", Elevation.Value)) return true;
                if (!SameString("timezone_name", Timezone.Text)) return true;
            }
            return false;
        }
    }

    private static bool SameNumberNested(JsonObject node, string key, double current) =>
        node[key] is not System.Text.Json.Nodes.JsonValue value
        || !value.TryGetValue<double>(out var saved)
        || Math.Abs(saved - current) < 1e-9;

    /// <summary>结果区标题：仅在结果归属当前页面时以结果原始参数为准，不跨页串标题。</summary>
    private string ResultTitle()
    {
        if (resultParams != null && result != null && resultKind == page)
        {
            var name = Text(resultParams, "name") is { Length: > 0 } saved ? saved : LocationName.Text;
            var date = Text(resultParams, "target_date") is { Length: > 0 } savedDate
                ? savedDate : TargetDateLabel();
            return $"{name} · {date}";
        }
        return $"{LocationName.Text} · {TargetDateLabel()}";
    }

    private string ResultPhenomenonLabel()
    {
        var value = result != null && resultParams != null && resultKind == page
            ? Text(resultParams, "phenomenon") : TagOf(Phenomenon.SelectedItem);
        return value == "dawn" ? L10n.T("朝霞") : L10n.T("晚霞");
    }

    private string ResultParamsSummary()
    {
        if (resultParams == null) return "";
        var parts = new List<string>();
        if (Text(resultParams, "name") is { Length: > 0 } name) parts.Add($"地点：{name}");
        if (Number(resultParams, "lat") is { } lat && double.IsFinite(lat)
            && Number(resultParams, "lon") is { } lon && double.IsFinite(lon))
            parts.Add($"坐标：{lat:0.0000}, {lon:0.0000}");
        if (Text(resultParams, "target_date") is { Length: > 0 } date) parts.Add($"日期：{date}");
        if (Text(resultParams, "phenomenon") is { Length: > 0 } phenomenon)
            parts.Add($"现象：{(phenomenon == "dawn" ? L10n.T("朝霞") : L10n.T("晚霞"))}");
        if (resultKind == "regional")
        {
            if (Text(resultParams, "model_source") is { Length: > 0 } model)
                parts.Add($"模式：{(model == "ec" ? "ECMWF" : "GFS")}");
            if (resultParams["regions"] is JsonArray regions && regions.Count > 0)
                parts.Add($"区域：{string.Join("、", regions.Select(node => node?.GetValue<string>() ?? ""))}");
        }
        return L10n.T("结果原始参数 — ") + string.Join("；", parts);
    }

    // ---------- 设置分类 ----------

    private void OnSettingsCategoryChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        settingsCategory = SettingsCategories.SelectedIndex switch
        {
            0 => "general", 1 => "data", 2 => "location", _ => "advanced",
        };
        ApplySettingsCategory();
        SaveSettings();
    }

    private void ApplySettingsCategory()
    {
        if (GeneralSettings == null) return;
        GeneralSettings.Visibility = settingsCategory == "general" ? Visibility.Visible : Visibility.Collapsed;
        DataSettings.Visibility = settingsCategory == "data" ? Visibility.Visible : Visibility.Collapsed;
        LocationSettings.Visibility = settingsCategory == "location" ? Visibility.Visible : Visibility.Collapsed;
        AdvancedSettings.Visibility = settingsCategory == "advanced" ? Visibility.Visible : Visibility.Collapsed;
        if (settingsCategory == "general") UpdateRuntimeSummary();
    }

    private void OnOpenSyncSettings(object sender, RoutedEventArgs args)
    {
        NavigateTo("settings");
        settingsCategory = "data";
        SettingsCategories.SelectedIndex = 1;
        ApplySettingsCategory();
    }

    private void OnChooseDirectory(object sender, RoutedEventArgs args)
    {
        _ = ChooseDirectoryAsync();
    }

    private async Task ChooseDirectoryAsync()
    {
        try
        {
            var folder = new Windows.Storage.Pickers.FolderPicker
            {
                SuggestedStartLocation = Windows.Storage.Pickers.PickerLocationId.ComputerFolder,
            };
            folder.FileTypeFilter.Add("*");
            WinRT.Interop.InitializeWithWindow.Initialize(folder, WinRT.Interop.WindowNative.GetWindowHandle(this));
            var result = await folder.PickSingleFolderAsync();
            if (result != null) WorkspacePath.Text = result.Path;
        }
        catch (Exception error) { Fail(error, "settings"); }
    }

    private void OnAppearanceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (Root == null || !initialized) return;
        Root.RequestedTheme = Appearance.SelectedIndex switch
        {
            1 => ElementTheme.Light, 2 => ElementTheme.Dark, _ => ElementTheme.Default,
        };
        SaveSettings();
    }

    private void OnEcmwfSourceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        var next = SettingsCategories.SelectedIndex >= 0
            ? TagOf(EcmwfSourcePicker.SelectedItem) : "ecmwf";
        if (next is not ("ecmwf" or "aws" or "google")) next = "ecmwf";
        if (next == ecmwfSource) return;
        ecmwfSource = next;
        // 修改来源后，旧连通性结果标记为过期，不能继续显示为当前配置通过。
        probeDate = null;
        ResetProbeRows();
        ProbeEcNote.Text = $"当前使用 EC {EcmwfSourceLabel()}。GFS 与 CAMS 使用各自官方入口。";
        SaveSettings();
    }

    internal string EcmwfSourceLabel() => ecmwfSource switch
    {
        "aws" => L10n.T("AWS 欧洲镜像"),
        "google" => L10n.T("Google Cloud 镜像"),
        _ => L10n.T("ECMWF 官方"),
    };

    private void OnElevationSourceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        var next = TagOf(ElevationSourcePicker.SelectedItem);
        if (next is not ("auto" or "http" or "srtm")) next = "auto";
        if (next == elevationSource) return;
        elevationSource = next;
        RenderElevationSourceNote();
        SaveSettings();
    }

    private void OnReconnect(object sender, RoutedEventArgs args) => _ = ReconnectAsync();

    private async Task ReconnectAsync()
    {
        if (HasActiveWork) { Fail(new InvalidOperationException(L10n.T("请等待当前任务完成后再重新连接。")), "settings"); return; }
        await Connect();
    }

    private void OnCancel(object sender, RoutedEventArgs args)
    {
        operation?.Cancel();
        probeOperation?.Cancel();
        syncOperation?.Cancel();
        Status.Text = L10n.T("正在取消，等待当前阶段结束");
    }
    // ---------- 界面多语言（v1.9.7，macOS L10n 同构） ----------

    private void OnLanguageChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!initialized) return;
        var index = LanguageChoice.SelectedIndex;
        L10n.Preference = index switch { 1 => "zh", 2 => "en", _ => "system" };
        ApplyLocalization();
    }

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        var count = Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            var child = Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChild(root, i);
            yield return child;
            foreach (var nested in Descendants(child)) yield return nested;
        }
    }

    /// 视觉树遍历替换：字典命中的文案换为当前语言；en→zh 反向归一使
    /// 语言来回切换幂等。动态构建的面板（指标卡/历史行等）构建后重刷。
    internal void ApplyLocalization()
    {
        foreach (var node in Descendants(Root))
        {
            switch (node)
            {
                case TextBlock text when !string.IsNullOrEmpty(text.Text):
                    text.Text = L10n.Translate(text.Text);
                    break;
                case Microsoft.UI.Xaml.Documents.Run run when !string.IsNullOrEmpty(run.Text):
                    run.Text = L10n.Translate(run.Text);
                    break;
                case Microsoft.UI.Xaml.Controls.Primitives.ButtonBase button
                    when button.Content is string content && !string.IsNullOrEmpty(content):
                    button.Content = L10n.Translate(content);
                    break;
                case RadioButton radio when radio.Content is string content:
                    radio.Content = L10n.Translate(content);
                    break;
                case CheckBox checkbox when checkbox.Content is string content:
                    checkbox.Content = L10n.Translate(content);
                    break;
                case ComboBoxItem item when item.Content is string content:
                    item.Content = L10n.Translate(content);
                    break;
                case NavigationViewItem nav when nav.Content is string content:
                    nav.Content = L10n.Translate(content);
                    break;
                case ToggleSwitch toggle:
                    if (toggle.OnContent is string onText) toggle.OnContent = L10n.Translate(onText);
                    if (toggle.OffContent is string offText) toggle.OffContent = L10n.Translate(offText);
                    break;
                case TextBox box when !string.IsNullOrEmpty(box.PlaceholderText):
                    box.PlaceholderText = L10n.Translate(box.PlaceholderText);
                    break;
                case InfoBar info:
                    if (!string.IsNullOrEmpty(info.Title)) info.Title = L10n.Translate(info.Title);
                    if (!string.IsNullOrEmpty(info.Message)) info.Message = L10n.Translate(info.Message);
                    break;
            }
        }
    }

}
