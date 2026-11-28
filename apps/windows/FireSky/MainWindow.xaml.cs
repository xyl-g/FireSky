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
    private bool mapPickerDialogOpen;
    private bool restoringSession, suppressDraftEvents;
    private string page = "point", resultKind = "";
    private string settingsCategory = "data";
    private string ecmwfSource = "ecmwf";
    private string aerosolSource = "cams";
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
        App.LocalDataRoot, "FireSky", "settings.json");
    private int locationRevision;
    private readonly Dictionary<string, TextBlock> metricValues = new();
    private readonly Dictionary<string, StackPanel> heroPanels = new();
    internal readonly List<TaskHistoryItem> taskHistory = new();
    private readonly List<TaskHistoryRow> taskHistoryRows = new();
    private readonly List<TaskEvent> taskHistoryEvents = new();
    private CancellationTokenSource? taskEventsOperation;
    private int taskHistoryTotal, taskHistoryIssues;
    private bool taskHistoryLoaded, taskHistoryLoading;
    private bool taskHistoryHasMore;
    private string? taskHistoryCursor;
    private bool taskHistoryEventsLoading, taskHistoryEventsTruncated;
    private string taskHistoryError = "", taskHistoryEventsError = "";
    private string selectedTaskHistoryId = "", restoringTaskHistoryId = "", forgettingTaskHistoryId = "";
    private string historyKindFilter = "all";
    private string phenomenon = "dusk";
    private string appearancePref = "system";
    private string locationViewMode = "map";
    private bool coordEditorExpanded;
    private int selectedResultTabIndex;
    private string selectedResultModel = "gfs";
    private sealed record ChartPoint(string Model, DateTimeOffset Time, double Quality, string Local, int Segment = 0);
    private List<ChartPoint>? chartPoints;
    private bool isUiAuditMock;
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
        InitializeSegmentedControls();
        AttachUpdater();
        BuildTerrainRegionPicker();
        TargetDate.Date = DateTimeOffset.Now;
        LoadSettings();
        BuildMetrics();
        RestoreSession();
        Navigation.SelectedItem = Navigation.MenuItems[0];
        initialized = true;
        SetLanguagePreference(L10n.Preference, apply: false);
        ApplyLocalization();
        UpdateRuntimeSummary();
        UpdateLocationSummary();
        AddKeyboardAccelerators();
        ApplyTitleBarTheme();
        Root.ActualThemeChanged += (_, _) =>
        {
            if (!initialized) return;
            RefreshSegmentedControlThemes();
            ApplyTitleBarTheme();
            ApplyMapThemeBackground();
            BuildMetrics();
            RenderResult();
            _ = UpdateMap();
            if (page == "data") _ = LoadInventory();
            UpdateRuntimeSummary();
        };
        Root.Loaded += async (_, _) =>
        {
            var uiAuditMock = Environment.GetEnvironmentVariable("FIRESKY_UI_AUDIT_MOCK") == "1";
            // Keep fixture runs deterministic and prevent external map tile requests.
            if (!uiAuditMock) await InitializeMap();
            if (uiAuditMock)
            {
                await RunUiAuditMockAsync();
                return;
            }
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
        if (value.TryGetValue<long>(out var whole)) return whole;
        if (value.TryGetValue<int>(out var intVal)) return intVal;
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

    private static string DefaultWorkspaceDirectory() =>
        Path.Combine(App.LocalDataRoot, "FireSky", "Data");

    private async Task Connect()
    {
        online = false;
        UpdateRuntimeSummary();
        Status.Text = L10n.T("正在连接引擎");
        try
        {
            if (string.IsNullOrWhiteSpace(WorkspacePath.Text))
                WorkspacePath.Text = DefaultWorkspaceDirectory();
            Directory.CreateDirectory(WorkspacePath.Text);
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
        if (camsConfigured || aerosolSource == "geos-fp" || !online) return;
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
            Navigation.SelectedItem = Navigation.SettingsItem;
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

    // ---------- 紧凑分段胶囊控件（对齐 macOS WorkSegments / WorkAnalysisTabs） ----------

    private void InitializeSegmentedControls()
    {
        RebuildLocalizedPills();
    }

    private void RebuildLocalizedPills()
    {
        BuildSegmentedPills(LocationViewPills,
            [("map", L10n.T("地图")), ("favorites", L10n.T("收藏"))],
            locationViewMode, tag => SetLocationViewMode(tag), compact: true);
        BuildSegmentedPills(DatePillHost,
            [("今天", L10n.T("今天")), ("明天", L10n.T("明天")), ("自定义", L10n.T("自定义"))],
            dateTab, tag => SelectDateTab(tag));
        BuildSegmentedPills(PhenomenonPillHost,
            [("dawn", L10n.T("朝霞")), ("dusk", L10n.T("晚霞"))],
            phenomenon, tag => SetPhenomenon(tag));
        BuildSegmentedPills(ResultTabPills,
            [("0", L10n.T("概览")), ("1", L10n.T("大气剖面")), ("2", L10n.T("逐时台账"))],
            selectedResultTabIndex.ToString(CultureInfo.InvariantCulture),
            tag => SelectResultTab(int.TryParse(tag, out var idx) ? idx : 0));
        BuildSegmentedPills(ResultModelPills,
            [("gfs", "GFS"), ("ec", "EC")],
            selectedResultModel, tag => SetSelectedResultModel(tag), compact: true);
        BuildSegmentedPills(RegionalResultModelPills,
            [("gfs", "GFS"), ("ec", "EC")],
            selectedResultModel, tag => SetSelectedResultModel(tag), compact: true);
        BuildSegmentedPills(HistoryFilterPills,
            [("all", L10n.T("全部")), ("active", L10n.T("主动任务")), ("sync", L10n.T("后台同步"))],
            historyKindFilter, tag => SelectHistoryFilter(tag));
        BuildSegmentedPills(SettingsCategoryPills,
            [("general", L10n.T("通用")), ("data", L10n.T("数据")), ("advanced", L10n.T("高级"))],
            settingsCategory, tag => SelectSettingsCategory(tag));
        BuildSegmentedPills(AppearancePills,
            [("system", L10n.T("跟随系统")), ("light", L10n.T("浅色")), ("dark", L10n.T("深色"))],
            appearancePref, tag => SetAppearancePreference(tag));
        BuildSegmentedPills(LanguagePills,
            [("system", "跟随系统 / System"), ("zh", "中文"), ("en", "English")],
            L10n.Preference, tag => SetLanguagePreference(tag));
    }

    private void RefreshSegmentedControlThemes()
    {
        UpdateSegmentedPills(LocationViewPills, locationViewMode);
        UpdateSegmentedPills(DatePillHost, dateTab);
        UpdateSegmentedPills(PhenomenonPillHost, phenomenon);
        UpdateSegmentedPills(ResultTabPills, selectedResultTabIndex.ToString(CultureInfo.InvariantCulture));
        UpdateSegmentedPills(ResultModelPills, selectedResultModel);
        UpdateSegmentedPills(RegionalResultModelPills, selectedResultModel);
        UpdateSegmentedPills(HistoryFilterPills, historyKindFilter);
        UpdateSegmentedPills(SettingsCategoryPills, settingsCategory);
        UpdateSegmentedPills(AppearancePills, appearancePref);
        UpdateSegmentedPills(LanguagePills, L10n.Preference);
    }

    private void BuildSegmentedPills(Panel? host, IReadOnlyList<(string Tag, string Label)> items,
        string selectedTag, Action<string> onSelect, bool compact = false)
    {
        if (host == null) return;
        host.Children.Clear();
        if (host is Grid grid)
        {
            grid.ColumnDefinitions.Clear();
            for (var i = 0; i < items.Count; i++)
                grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        }
        var pad = compact ? new Thickness(9, 3, 9, 3) : new Thickness(11, 5, 11, 5);
        for (var i = 0; i < items.Count; i++)
        {
            var (tag, label) = items[i];
            var btn = new Button
            {
                Tag = tag,
                Padding = pad,
                MinWidth = 0,
                MinHeight = 0,
                CornerRadius = new CornerRadius(9),
                BorderThickness = new Thickness(1),
                HorizontalAlignment = host is Grid ? HorizontalAlignment.Stretch : HorizontalAlignment.Left,
                HorizontalContentAlignment = HorizontalAlignment.Center,
                Content = new TextBlock
                {
                    Text = label,
                    FontSize = compact ? 11 : 12,
                    TextWrapping = TextWrapping.NoWrap,
                },
            };
            btn.Click += (_, _) => onSelect(tag);
            if (host is Grid) Grid.SetColumn(btn, i);
            host.Children.Add(btn);
        }
        UpdateSegmentedPills(host, selectedTag);
    }

    private Brush SecondaryBrush() => new SolidColorBrush(
        Root?.ActualTheme == ElementTheme.Dark
            ? Windows.UI.Color.FromArgb(255, 0xA8, 0xB5, 0xC8)
            : Windows.UI.Color.FromArgb(255, 0x5E, 0x6A, 0x7D));

    private void ApplyTitleBarTheme()
    {
        if (AppWindow?.TitleBar is not { } tb) return;
        var isDark = Root?.ActualTheme == ElementTheme.Dark;
        var fg = isDark ? Colors.White : Windows.UI.Color.FromArgb(255, 0x1A, 0x1D, 0x24);
        var hoverBg = isDark ? Windows.UI.Color.FromArgb(0x22, 0xFF, 0xFF, 0xFF) : Windows.UI.Color.FromArgb(0x14, 0x00, 0x00, 0x00);
        tb.ButtonForegroundColor = fg;
        tb.ButtonHoverForegroundColor = fg;
        tb.ButtonHoverBackgroundColor = hoverBg;
        tb.ButtonPressedForegroundColor = fg;
        tb.ButtonInactiveForegroundColor = isDark
            ? Windows.UI.Color.FromArgb(255, 0x99, 0xA4, 0xB6)
            : Windows.UI.Color.FromArgb(255, 0x6D, 0x77, 0x86);
    }

    private void UpdateSegmentedPills(Panel? host, string selectedTag)
    {
        if (host == null) return;
        var isDark = Root?.ActualTheme == ElementTheme.Dark;
        var cardBrush = new SolidColorBrush(isDark
            ? Windows.UI.Color.FromArgb(255, 0x28, 0x35, 0x4D)
            : Colors.White);
        var cardBorder = new SolidColorBrush(isDark
            ? Windows.UI.Color.FromArgb(255, 0x5B, 0x70, 0x91)
            : Windows.UI.Color.FromArgb(255, 0xCF, 0xDF, 0xED));
        var transparentBrush = new SolidColorBrush(Colors.Transparent);
        var primaryBrush = new SolidColorBrush(isDark
            ? Colors.White
            : Windows.UI.Color.FromArgb(255, 0x18, 0x1C, 0x24));
        var secondaryBrush = new SolidColorBrush(isDark
            ? Windows.UI.Color.FromArgb(255, 0xB2, 0xBF, 0xD2)
            : Windows.UI.Color.FromArgb(255, 0x5D, 0x68, 0x78));
        foreach (var child in host.Children.OfType<Button>())
        {
            var active = string.Equals(child.Tag?.ToString(), selectedTag, StringComparison.Ordinal);
            child.Background = active ? cardBrush : transparentBrush;
            child.BorderBrush = active ? cardBorder : transparentBrush;
            if (child.Content is TextBlock tb)
            {
                tb.FontWeight = active
                    ? Microsoft.UI.Text.FontWeights.SemiBold
                    : Microsoft.UI.Text.FontWeights.Normal;
                tb.Foreground = active ? primaryBrush : secondaryBrush;
            }
        }
    }

    private void LoadSettings()
    {
        var saved = LoadSettingsJson();
        var root = Environment.GetEnvironmentVariable("FIRESKY_WORKSPACE") ?? "";
        var savedWorkspace = Text(saved, "workspace").Trim();
        var workspace = root.Length > 0 ? root
            : savedWorkspace.Length > 0 ? savedWorkspace
            : DefaultWorkspaceDirectory();
        try { Directory.CreateDirectory(workspace); } catch { }
        WorkspacePath.Text = workspace;
        EnginePath.Text = Environment.GetEnvironmentVariable("FIRESKY_RUST_ENGINE_BIN")
            ?? Text(saved, "engineBinary");
        appearancePref = Text(saved, "appearance") is { Length: > 0 } app && app is "light" or "dark" ? app : "system";
        SetAppearancePreference(appearancePref, save: false);
        ecmwfSource = Text(saved, "ecmwfSource") is { Length: > 0 } source && source is "ecmwf" or "aws" or "google"
            ? source : "ecmwf";
        EcmwfSourcePicker.SelectedIndex = ecmwfSource switch { "aws" => 1, "google" => 2, _ => 0 };
        aerosolSource = Text(saved, "aerosolSource") == "geos-fp" ? "geos-fp" : "cams";
        AerosolSourcePicker.SelectedIndex = aerosolSource == "geos-fp" ? 1 : 0;
        ProbeEcNote.Text = ProbeSourceNote();
        elevationSource = Text(saved, "elevationSource") is { Length: > 0 } elevation && elevation is "auto" or "http" or "srtm"
            ? elevation : "auto";
        ElevationSourcePicker.SelectedIndex = elevationSource switch { "http" => 1, "srtm" => 2, _ => 0 };
        favorites = (saved["favorites"] as JsonArray ?? [])
            .OfType<JsonObject>().Select(node => node.DeepClone() as JsonObject ?? node).ToList();
        RenderFavorites();
        // 草稿随分类切换保留（v1.2.0 设置分类合同）。
        settingsCategory = Text(saved, "settingsCategory") is { Length: > 0 } category
            && category is "general" or "data" or "location" or "advanced"
                ? category == "location" ? "data" : category
                : "data";
        SelectSettingsCategory(settingsCategory, save: false);
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
        saved["appearance"] = appearancePref;
        saved["ecmwfSource"] = ecmwfSource;
        saved["aerosolSource"] = aerosolSource;
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
            ["phenomenon"] = phenomenon,
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
            if (Text(saved, "phenomenon") is { Length: > 0 } savedPhenomenon && savedPhenomenon is "dawn" or "dusk")
                SetPhenomenon(savedPhenomenon, save: false);
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
        PointFields.Visibility = page == "regional" ? Visibility.Collapsed : Visibility.Visible;
        RegionFields.Visibility = page == "regional" ? Visibility.Visible : Visibility.Collapsed;
        if (ExpandMapButton != null)
            ExpandMapButton.Visibility = page == "regional" || locationViewMode == "favorites"
                ? Visibility.Collapsed : Visibility.Visible;
        if (LocationViewPillTrack != null)
            LocationViewPillTrack.Visibility = page == "regional" ? Visibility.Collapsed : Visibility.Visible;
        SourcesLabel.Text = page == "nowcast" ? L10n.T("GFS · CAMS · 卫星帧") : "GFS + EC";
        PredictButton.Content = page == "nowcast" ? L10n.T("检查并预热") : L10n.T("开始预测");
        if (page == "regional")
        {
            MiniMapHost.Child = null;
            RegionalMapHost.Child = Map;
            RegionalMapHost.Visibility = Visibility.Visible;
            RegionalInspector.Visibility = Visibility.Visible;
            MiniMapHost.Visibility = Visibility.Collapsed;
            if (FavoritesPanel != null) FavoritesPanel.Visibility = Visibility.Collapsed;
            ResultTabs.Visibility = Visibility.Collapsed;
            var regionalModelTag = TagOf(RegionalModel?.SelectedItem) is { Length: > 0 } m ? m : selectedResultModel;
            SetSelectedResultModel(regionalModelTag, render: false, updateMap: false);
        }
        else
        {
            RegionalMapHost.Child = null;
            MiniMapHost.Child = Map;
            RegionalMapHost.Visibility = Visibility.Collapsed;
            RegionalInspector.Visibility = Visibility.Collapsed;
            SetLocationViewMode(locationViewMode, updateMap: false);
            ResultTabs.Visibility = Visibility.Visible;
        }
        UpdateLocationSummary();
        RenderPageErrors();
        RenderResult();
        _ = UpdateMap(forceFit: page == "regional");
        if (!isUiAuditMock)
        {
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

    private async void OnOpenMapPicker(object sender, RoutedEventArgs args)
    {
        if (mapPickerDialogOpen || Root?.XamlRoot == null || !mapReady
            || MiniMapHost?.Child != Map)
            return;

        mapPickerDialogOpen = true;
        var mapFrame = new Border
        {
            Width = 900,
            Height = 560,
            CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1),
        };
        ContentDialog? dialog = null;
        try
        {
            MiniMapHost.Child = null;
            mapFrame.Child = Map;
            dialog = new ContentDialog
            {
                Title = L10n.T("地图选点"),
                CloseButtonText = L10n.T("完成"),
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = Root.XamlRoot,
                FullSizeDesired = true,
                Width = 940,
                Height = 640,
                MinWidth = 920,
                MaxWidth = 960,
                MaxHeight = 700,
                Content = mapFrame,
            };
            await dialog.ShowAsync();
        }
        catch (Exception error)
        {
            Fail(error);
        }
        finally
        {
            if (dialog != null) dialog.Content = null;
            mapFrame.Child = null;
            MiniMapHost.Child = Map;
            mapPickerDialogOpen = false;
            if (mapReady)
            {
                try
                {
                    await Map.ExecuteScriptAsync("window.dispatchEvent(new Event('resize'))");
                    await UpdateMap();
                }
                catch (Exception error) { Fail(error); }
            }
        }
    }

    private void ApplyMapThemeBackground()
    {
        if (Map == null) return;
        Map.DefaultBackgroundColor = Root?.ActualTheme == ElementTheme.Dark
            ? Windows.UI.Color.FromArgb(255, 0x11, 0x18, 0x26)
            : Windows.UI.Color.FromArgb(255, 0xEE, 0xF3, 0xF8);
    }

    private async Task InitializeMap()
    {
        try
        {
            ApplyMapThemeBackground();
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
            ["dark"] = Root?.ActualTheme == ElementTheme.Dark,
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
            UpdateLocationSummary();
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
            UpdateLocationSummary();
            result = null; RenderResult(); await UpdateMap();
            ScheduleSessionSave();
        }
        catch (Exception error) { if (revision == locationRevision) Fail(error); }
    }

    private void OnToggleCoordEditor(object sender, RoutedEventArgs args)
    {
        coordEditorExpanded = !coordEditorExpanded;
        if (PointCoordEditorPanel != null)
            PointCoordEditorPanel.Visibility = coordEditorExpanded ? Visibility.Visible : Visibility.Collapsed;
        if (EditCoordsButtonText != null)
            EditCoordsButtonText.Text = L10n.T(coordEditorExpanded ? "收起坐标" : "编辑坐标");
    }

    private void SetLocationViewMode(string mode, bool updateMap = true)
    {
        locationViewMode = mode == "favorites" ? "favorites" : "map";
        UpdateSegmentedPills(LocationViewPills, locationViewMode);
        var showFavs = locationViewMode == "favorites" && page != "regional";
        if (MiniMapHost != null) MiniMapHost.Visibility = showFavs ? Visibility.Collapsed : Visibility.Visible;
        if (ExpandMapButton != null)
            ExpandMapButton.Visibility = showFavs || page == "regional"
                ? Visibility.Collapsed : Visibility.Visible;
        if (FavoritesPanel != null) FavoritesPanel.Visibility = showFavs ? Visibility.Visible : Visibility.Collapsed;
        if (!showFavs && updateMap) _ = UpdateMap();
    }

    private void UpdateLocationSummary()
    {
        if (LocationSummaryName == null) return;
        var name = LocationName?.Text?.Trim() ?? "";
        LocationSummaryName.Text = name.Length > 0 ? name : L10n.T("观测点");
        if (LocationSummaryCoords != null)
            LocationSummaryCoords.Text = LatLonLabel(Latitude.Value, Longitude.Value);
        if (LocationSummaryMeta != null)
        {
            var ready = MetadataReady?.IsChecked == true ? L10n.T("位置已确认") : L10n.T("未确认海拔/时区");
            var elev = double.IsFinite(Elevation.Value) ? $"{(int)Math.Round(Elevation.Value)} m" : "--";
            var tz = string.IsNullOrWhiteSpace(Timezone?.Text) ? "--" : Timezone.Text.Trim();
            LocationSummaryMeta.Text = $"{ready} · 海拔 {elev} · {tz}";
        }
    }

    private void OnLocationNameChanged(object sender, TextChangedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        UpdateLocationSummary();
        ScheduleSessionSave();
    }

    private void OnElevationOrZoneChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        UpdateLocationSummary();
        result = null; RenderResult();
        ScheduleSessionSave();
    }

    private void OnTimezoneChanged(object sender, TextChangedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        UpdateLocationSummary();
        result = null; RenderResult();
        ScheduleSessionSave();
    }

    private void OnMetadataReadyChanged(object sender, RoutedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        UpdateLocationSummary();
        ScheduleSessionSave();
    }

    private async void OnMetadata(object sender, RoutedEventArgs args) => await LookupMetadata();
    private async void OnCoordinatesChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!initialized || restoringSession) return;
        locationRevision++;
        MetadataReady.IsChecked = false;
        elevationSourceActual = "";
        RenderElevationSourceNote();
        UpdateLocationSummary();
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
        if (FavoritesEmptyHint != null)
            FavoritesEmptyHint.Visibility = favorites.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
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
                UpdateLocationSummary();
                SetLocationViewMode("map", updateMap: false);
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

    // ---------- 日期与观测时段 ----------

    private void SelectDateTab(string tab)
    {
        dateTab = tab is "明天" or "自定义" ? tab : "今天";
        UpdateSegmentedPills(DatePillHost, dateTab);
        TargetDate.Visibility = dateTab == "自定义" ? Visibility.Visible : Visibility.Collapsed;
        if (!initialized || restoringSession) return;
        suppressDraftEvents = true;
        try
        {
            if (dateTab == "今天") TargetDate.Date = DateTimeOffset.Now;
            if (dateTab == "明天") TargetDate.Date = DateTimeOffset.Now.AddDays(1);
        }
        finally { suppressDraftEvents = false; }
        if (page == "nowcast") UpdateNowcastBanners(resultKind == "nowcast" ? SelectedReport() : null);
        RenderResult();
        ScheduleSessionSave();
    }

    private void SetPhenomenon(string value, bool save = true)
    {
        phenomenon = value == "dawn" ? "dawn" : "dusk";
        UpdateSegmentedPills(PhenomenonPillHost, phenomenon);
        if (!save || !initialized || restoringSession) return;
        if (page == "nowcast") UpdateNowcastBanners(resultKind == "nowcast" ? SelectedReport() : null);
        RenderResult();
        ScheduleSessionSave();
    }

    private void OnCustomDateChanged(CalendarDatePicker sender, CalendarDatePickerDateChangedEventArgs args)
    {
        if (!initialized || restoringSession || suppressDraftEvents) return;
        var picked = args.NewDate?.LocalDateTime.Date;
        if ((dateTab == "今天" && picked == DateTime.Today)
            || (dateTab == "明天" && picked == DateTime.Today.AddDays(1)))
            return;
        if (dateTab != "自定义")
        {
            dateTab = "自定义";
            UpdateSegmentedPills(DatePillHost, dateTab);
            TargetDate.Visibility = Visibility.Visible;
        }
        if (page == "nowcast") UpdateNowcastBanners(resultKind == "nowcast" ? SelectedReport() : null);
        RenderResult();
        ScheduleSessionSave();
    }

    /// <summary>按当前时区把 dateTab 对齐到 date 实际落在的今天/明天/自定义。</summary>
    private void SyncDateTab()
    {
        var date = TargetDate.Date?.LocalDateTime.Date ?? DateTime.Today;
        var today = DateTime.Today;
        var tomorrow = today.AddDays(1);
        dateTab = date == today ? "今天" : date == tomorrow ? "明天" : "自定义";
        UpdateSegmentedPills(DatePillHost, dateTab);
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
            ["phenomenon"] = phenomenon,
            ["model_source"] = "both", ["workers"] = 1,
            ["forecast_mode"] = "model", ["satellite_source"] = "off",
            // v1.1.1 契约：EC 下载源与海拔来源随每次请求透传。
            ["ecmwf_source"] = ecmwfSource,
            ["aerosol_source"] = aerosolSource,
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
            {
                SetSelectedResultModel("ec", render: false, updateMap: false);
                if (kind == "regional" && RegionalModel != null) RegionalModel.SelectedIndex = 1;
            }
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
        showInvalidLedgerSteps = false;
        frozenProfile = null;
        frozenProfileModel = "";
        frozenProfileState = "idle";
        frozenProfileError = "";
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
            if (!SameString("phenomenon", phenomenon)) return true;
            if (!SameString("ecmwf_source", ecmwfSource)) return true;
            if (!SameString("aerosol_source", aerosolSource)) return true;
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
            ? Text(resultParams, "phenomenon") : phenomenon;
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

    private void SelectSettingsCategory(string category, bool save = true)
    {
        settingsCategory = category is "general" or "data" or "advanced" ? category : "general";
        UpdateSegmentedPills(SettingsCategoryPills, settingsCategory);
        ApplySettingsCategory();
        if (save && initialized && !restoringSession) SaveSettings();
    }

    private void ApplySettingsCategory()
    {
        if (GeneralSettings == null) return;
        GeneralSettings.Visibility = settingsCategory == "general" ? Visibility.Visible : Visibility.Collapsed;
        DataSettings.Visibility = settingsCategory == "data" ? Visibility.Visible : Visibility.Collapsed;
        AdvancedSettings.Visibility = settingsCategory == "advanced" ? Visibility.Visible : Visibility.Collapsed;
        if (settingsCategory == "general") UpdateRuntimeSummary();
    }

    private void OnOpenSyncSettings(object sender, RoutedEventArgs args)
    {
        NavigateTo("settings");
        SelectSettingsCategory("data", save: false);
    }

    private void OnSettingsDataAnchor(object sender, RoutedEventArgs args)
    {
        var section = (sender as Button)?.Tag?.ToString() switch
        {
            "sources" => DataSourcesSection,
            "cams" => DataCamsSection,
            "probe" => DataProbeSection,
            "sync" => DataSyncSection,
            "retention" => DataRetentionSection,
            "directory" => DataDirectorySection,
            "elevation" => ElevationSettingsSection,
            _ => null,
        };
        section?.StartBringIntoView();
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

    private void SetAppearancePreference(string pref, bool save = true)
    {
        appearancePref = pref is "light" or "dark" ? pref : "system";
        UpdateSegmentedPills(AppearancePills, appearancePref);
        if (Root != null)
        {
            Root.RequestedTheme = appearancePref switch
            {
                "light" => ElementTheme.Light, "dark" => ElementTheme.Dark, _ => ElementTheme.Default,
            };
        }
        RefreshSegmentedControlThemes();
        ApplyTitleBarTheme();
        ApplyMapThemeBackground();
        if (save && initialized && !restoringSession) SaveSettings();
    }

    private void OnEcmwfSourceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        var next = EcmwfSourcePicker.SelectedIndex >= 0
            ? TagOf(EcmwfSourcePicker.SelectedItem) : "ecmwf";
        if (next is not ("ecmwf" or "aws" or "google")) next = "ecmwf";
        if (next == ecmwfSource) return;
        ecmwfSource = next;
        // 修改来源后，旧连通性结果标记为过期，不能继续显示为当前配置通过。
        probeDate = null;
        ResetProbeRows();
        ProbeEcNote.Text = ProbeSourceNote();
        SaveSettings();
    }

    private void OnAerosolSourceChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        var next = AerosolSourcePicker.SelectedIndex >= 0
            ? TagOf(AerosolSourcePicker.SelectedItem) : "cams";
        if (next is not ("cams" or "geos-fp")) next = "cams";
        if (next == aerosolSource) return;
        aerosolSource = next;
        probeDate = null;
        ResetProbeRows();
        ProbeEcNote.Text = ProbeSourceNote();
        UpdateRuntimeSummary();
        SaveSettings();
    }

    internal string ProbeSourceNote() => aerosolSource == "geos-fp"
        ? $"当前使用 EC {EcmwfSourceLabel()}，AOD 使用 NASA GEOS-FP（免密钥）。GFS 使用 NOAA NOMADS。"
        : $"当前使用 EC {EcmwfSourceLabel()}。GFS 与 CAMS 使用各自官方入口。";

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

    private void SetLanguagePreference(string pref, bool apply = true)
    {
        var normalized = pref is "zh" or "en" ? pref : "system";
        L10n.Preference = normalized;
        UpdateSegmentedPills(LanguagePills, normalized);
        if (apply && initialized)
        {
            RebuildLocalizedPills();
            ApplyLocalization();
            BuildMetrics();
            RenderResult();
        }
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
            if (ToolTipService.GetToolTip(node) is string toolTip && !string.IsNullOrEmpty(toolTip))
                ToolTipService.SetToolTip(node, L10n.Translate(toolTip));
            var automationName = Microsoft.UI.Xaml.Automation.AutomationProperties.GetName(node);
            if (!string.IsNullOrEmpty(automationName))
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(node, L10n.Translate(automationName));

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

    // ---------- UI Audit Fixture Hook (inert unless FIRESKY_UI_AUDIT_MOCK=1) ----------

    private async Task RunUiAuditMockAsync()
    {
        isUiAuditMock = true;
        if (AppWindow?.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter)
            presenter.Maximize();
        Activate();
        restoringSession = true;
        try
        {
            var theme = Environment.GetEnvironmentVariable("FIRESKY_UI_AUDIT_THEME")?.Trim().ToLowerInvariant();
            if (theme == "light") SetAppearancePreference("light", save: false);
            else if (theme == "dark") SetAppearancePreference("dark", save: false);

            regionDefinitions =
            [
                new JsonObject
                {
                    ["code"] = "china",
                    ["label"] = "中国",
                    ["bounds"] = new JsonArray { 73.0, 18.0, 136.0, 54.0 },
                },
                new JsonObject
                {
                    ["code"] = "east_china",
                    ["label"] = "华东",
                    ["bounds"] = new JsonArray { 115.0, 24.0, 124.0, 35.0 },
                },
            ];
            BuildRegionPresets();
            RebuildSyncAnchorPicker();

            LocationName.Text = "界面测试（非真实预测）";
            Latitude.Value = 30.25;
            Longitude.Value = 120.25;
            Elevation.Value = 20;
            Timezone.Text = "Asia/Shanghai";
            MetadataReady.IsChecked = true;
            dateTab = "自定义";
            TargetDate.Date = new DateTimeOffset(2026, 11, 9, 12, 0, 0, TimeSpan.FromHours(8));
            SyncDateTab();
            SetPhenomenon("dusk", save: false);
            UpdateLocationSummary();
            online = true;
            camsConfigured = true;
            Status.Text = L10n.T("本地引擎已连接");

            PopulateAuditHistory();
        }
        finally
        {
            restoringSession = false;
        }

        var auditPage = Environment.GetEnvironmentVariable("FIRESKY_UI_AUDIT_PAGE")?.Trim().ToLowerInvariant();
        if (auditPage is not ("point" or "regional" or "nowcast" or "data" or "history" or "settings"))
            auditPage = "point";

        if (auditPage == "regional")
        {
            selectedRegions.Clear();
            selectedRegions.Add("china");
            customRegion = false;
            CustomRegionToggle.IsChecked = false;
            CustomBoundsFields.Visibility = Visibility.Collapsed;
            RenderRegionPresetsChecks();
            AdoptResult(BuildAuditRegionalResult(), "regional", BuildAuditParams("regional"));
        }
        else if (auditPage == "nowcast")
        {
            AdoptResult(BuildAuditNowcastReport(), "nowcast", BuildAuditParams("nowcast"));
        }
        else
        {
            AdoptResult(BuildAuditPointResult(), "point", BuildAuditParams("point"));
        }

        NavigateTo(auditPage);
        ApplyPage();

        if (auditPage == "history")
        {
            RenderTaskHistory();
            if (taskHistory.FirstOrDefault() is { } firstTask)
                OnSelectTaskHistory(firstTask.TaskId);
        }
        else if (auditPage == "data")
        {
            InventoryEmptyCard.Visibility = Visibility.Collapsed;
            TerrainMessage.Text = "中国区域 SRTM 地形就绪（审计样例 · 1,248 瓦片）";
            TerrainLocalTiles.Text = "已缓存 1,248 个本地 SRTM 瓦片";
        }
        else if (auditPage == "settings")
        {
            SelectSettingsCategory("general", save: false);
            UpdateRuntimeSummary();
        }

        if (int.TryParse(Environment.GetEnvironmentVariable("FIRESKY_UI_AUDIT_TAB"), out var tabIdx)
            && ResultTabs != null && ResultTabs.Visibility == Visibility.Visible
            && tabIdx >= 0 && tabIdx <= 2)
        {
            SelectResultTab(tabIdx);
        }

        await Task.Delay(500);
        ControlsScrollViewer?.ChangeView(null, 0, null, true);
        if (resultKind == "point")
        {
            RenderChart();
            RenderPointProfileTab();
        }
        else if (resultKind == "nowcast")
        {
            RenderChart();
            RenderNowcastProfileTab();
        }
        else if (auditPage == "regional")
        {
            if (!isUiAuditMock)
            {
                for (var waitMs = 0; !mapReady && waitMs < 3500; waitMs += 50)
                    await Task.Delay(50);
                await UpdateMap(forceFit: true);
                await Task.Delay(450);
            }
        }
        else if (auditPage is "point" or "nowcast")
        {
            if (!isUiAuditMock)
            {
                for (var waitMs = 0; !mapReady && waitMs < 2500; waitMs += 50)
                    await Task.Delay(50);
                await UpdateMap();
            }
        }

        RefreshSegmentedControlThemes();
        ApplyTitleBarTheme();
        ApplyMapThemeBackground();
        ControlsScrollViewer?.Focus(FocusState.Programmatic);

        if (string.Equals(Environment.GetEnvironmentVariable("FIRESKY_UI_AUDIT_DIALOG"), "diagnostics",
                StringComparison.OrdinalIgnoreCase)
            && auditPage is "point" or "nowcast")
        {
            _ = ShowDiagnosticsDialogAsync();
            await Task.Delay(300);
        }

        Activate();
        await Task.Delay(150);

        if (Environment.GetEnvironmentVariable("FIRESKY_UI_AUDIT_OUT_MARKER") is { Length: > 0 } markerPath)
        {
            var dir = Path.GetDirectoryName(markerPath);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            File.WriteAllText(markerPath, "ready\n");
        }
    }

    private void PopulateAuditHistory()
    {
        taskHistory.Clear();
        taskHistoryHasMore = false;
        taskHistoryCursor = null;
        string HexId(char c) => new(c, 64);
        var specs = new (char Id, string Kind, string Name, string Date, string Status)[]
        {
            ('a', "point", "杭州 · 单点测试", "2026-11-09", "done"),
            ('b', "regional", "中国 · 区域测试", "2026-11-09", "done"),
            ('c', "nowcast", "杭州 · 卫星实况测试", "2026-11-09", "done"),
        };
        foreach (var (id, kind, name, date, status) in specs)
        {
            var raw = new JsonObject
            {
                ["task_id"] = HexId(id),
                ["method"] = "forecast",
                ["status"] = status,
                ["created_at_utc"] = "2026-11-09T08:55:00Z",
                ["updated_at_utc"] = "2026-11-09T09:00:00Z",
                ["cancel_requested"] = false,
                ["event_seq"] = 4,
                ["retry_request"] = new JsonObject
                {
                    ["kind"] = kind,
                    ["payload"] = new JsonObject
                    {
                        ["name"] = name,
                        ["target_date"] = date,
                        ["phenomenon"] = "dusk",
                        ["model_source"] = "gfs",
                    },
                },
                ["result_ref"] = new JsonObject
                {
                    ["snapshot"] = new JsonObject
                    {
                        ["contract"] = "firesky-result-snapshot-v1",
                        ["sha256"] = HexId('f'),
                    },
                },
            };
            if (TaskHistoryItem.Parse(raw) is { } parsed)
                taskHistory.Add(parsed);
        }
        taskHistoryTotal = taskHistory.Count;
        taskHistoryLoaded = true;
    }

    private JsonObject BuildAuditParams(string kind) => new()
    {
        ["name"] = "界面测试（非真实预测）",
        ["lat"] = 30.25,
        ["lon"] = 120.25,
        ["elevation_m"] = 20.0,
        ["timezone_name"] = "Asia/Shanghai",
        ["target_date"] = "2026-11-09",
        ["phenomenon"] = "dusk",
        ["ecmwf_source"] = ecmwfSource,
        ["aerosol_source"] = aerosolSource,
        ["model_source"] = kind == "nowcast" ? "satellite" : "gfs",
        ["regions"] = new JsonArray { "china" },
    };

    private static JsonObject BuildAuditPointResult() => new()
    {
        ["model_results"] = new JsonObject
        {
            ["gfs"] = BuildAuditModelReport("gfs"),
            ["ec"] = BuildAuditModelReport("ec"),
        },
        ["model_errors"] = new JsonObject(),
    };

    private static JsonObject BuildAuditRegionalResult() => new()
    {
        ["model_results"] = new JsonObject
        {
            ["gfs"] = BuildAuditRegionalModel("gfs"),
            ["ec"] = BuildAuditRegionalModel("ec"),
        },
        ["model_errors"] = new JsonObject(),
    };

    private static JsonObject BuildAuditRegionalModel(string model)
    {
        var points = new JsonArray();
        var errors = new JsonArray();
        for (var y = 0; y <= 20; y++)
        {
            for (var x = 0; x <= 20; x++)
            {
                var lat = 28.0 + y * 0.25;
                var lon = 118.0 + x * 0.25;
                if ((x + y) % (model == "gfs" ? 19 : 11) == 0)
                {
                    errors.Add(new JsonObject { ["lat"] = lat, ["lon"] = lon, ["error"] = "测试缺测" });
                }
                else
                {
                    var dx = x - (model == "gfs" ? 8.0 : 14.0);
                    var dy = y - 12.0;
                    var q = Math.Max(0, 2.3 - Math.Sqrt(dx * dx + dy * dy) / 6.0);
                    points.Add(new JsonObject
                    {
                        ["lat"] = lat,
                        ["lon"] = lon,
                        ["peak_quality_index"] = q,
                        ["peak_quality_level"] = "测试档位",
                        ["best_time_local"] = "2026-11-09T17:10:00+08:00",
                    });
                }
            }
        }
        var cities = new JsonArray
        {
            new JsonObject
            {
                ["name"] = "杭州", ["lat"] = 30.2741, ["lon"] = 120.1551,
                ["peak_quality_index"] = model == "gfs" ? 2.08 : 1.86,
                ["peak_quality_level"] = "绚丽火烧云",
                ["best_time_local"] = "2026-11-09T17:10:00+08:00", ["timezone_name"] = "Asia/Shanghai",
            },
            new JsonObject
            {
                ["name"] = "宁波", ["lat"] = 29.8683, ["lon"] = 121.5440,
                ["peak_quality_index"] = model == "gfs" ? 1.84 : 1.63,
                ["peak_quality_level"] = "中等晚霞",
                ["best_time_local"] = "2026-11-09T17:05:00+08:00", ["timezone_name"] = "Asia/Shanghai",
            },
            new JsonObject
            {
                ["name"] = "绍兴", ["lat"] = 30.0303, ["lon"] = 120.5802,
                ["peak_quality_index"] = model == "gfs" ? 1.71 : 1.54,
                ["peak_quality_level"] = "中等晚霞",
                ["best_time_local"] = "2026-11-09T17:15:00+08:00", ["timezone_name"] = "Asia/Shanghai",
            },
        };
        return new JsonObject
        {
            ["points"] = points,
            ["errors"] = errors,
            ["point_count"] = 441,
            ["error_count"] = errors.Count,
            ["usable_count"] = points.Count,
            ["spacing_deg"] = 0.25,
            ["cycle_time_utc"] = model == "gfs" ? "2026-11-09T00:00:00Z" : "2026-11-08T18:00:00Z",
            ["city_ranking"] = new JsonObject
            {
                ["contract"] = "rust-regional-city-ranking-v1",
                ["status"] = "ready",
                ["model_source"] = model,
                ["count"] = cities.Count,
                ["items"] = cities,
            },
        };
    }

    private static JsonObject BuildAuditNowcastReport()
    {
        var report = BuildAuditModelReport("gfs");
        var samples = new JsonArray();
        for (var i = 0; i < 12; i++)
        {
            var levels = new JsonArray();
            for (var layer = 0; layer < 8; layer++)
            {
                levels.Add(new JsonObject
                {
                    ["height_m"] = layer * 1500.0 + 500.0,
                    ["pressure_hpa"] = new int[] { 925, 850, 700, 600, 500, 400, 300, 200 }[layer],
                    ["status"] = "retrieved",
                    ["hsd_posterior_percent"] = (double)((i * 17 + layer * 29) % 100),
                });
            }
            samples.Add(new JsonObject
            {
                ["distance_km"] = i * 20.0,
                ["bearing_deg"] = 240.0,
                ["status"] = i == 5 ? "unknown" : "retrieved",
                ["levels"] = levels,
            });
        }
        report["nowcast"] = new JsonObject
        {
            ["source"] = "Himawari HSD",
            ["bands"] = "B07/B13/B15",
            ["image_channel"] = "HSD-IR",
            ["image_valid_time_utc"] = "2026-11-09T09:00:00Z",
            ["near_cloud_ratio"] = 0.42,
            ["far_cloud_ratio"] = 0.68,
            ["cloud_blanket_score"] = 0.74,
            ["cloud_texture_score"] = 0.66,
            ["horizon_clearance_score"] = 0.81,
            ["satellite_low_cloud_state"] = "detected",
            ["satellite_motion_quality"] = "strong",
            ["geometric_path_blocking_risk"] = 0.17,
            ["geometric_path_samples"] = 24,
            ["calibrated_sample_coverage_ratio"] = 0.91,
            ["confidence"] = 0.76,
            ["effective_convective_risk"] = 0.12,
            ["effective_path_blocking_risk"] = 0.17,
            ["convective_score_ceiling"] = 0.64,
            ["radar_source"] = "测试雷达（非真实观测）",
            ["radar_valid_time_utc"] = "2026-11-09T08:54:00Z",
            ["radar_age_minutes"] = 6,
            ["radar_max_dbz"] = 12.4,
            ["radar_reliability_score"] = 0.82,
            ["satellite_vertical_profile"] = new JsonObject
            {
                ["status"] = "partial_unknown",
                ["sample_count"] = 12,
                ["usable_sample_count"] = 11,
                ["unknown_sample_count"] = 1,
                ["coverage"] = 0.917,
                ["valid_time_utc"] = "2026-11-09T09:00:00Z",
                ["pressure_levels_hpa"] = new JsonArray { 925, 850, 700, 600, 500, 400, 300, 200 },
                ["samples"] = samples,
            },
        };
        report["summary"] = "界面测试数据，非真实实况分析。";
        return report;
    }

    private static JsonObject BuildAuditModelReport(string model)
    {
        var ec = model == "ec";
        double[] values = ec ? [0.3, 0.8, 1.36, 1.1] : [0.2, 0.4, 0.72, 0.5];
        var steps = new JsonArray();
        for (var idx = 0; idx < values.Length; idx++)
        {
            steps.Add(new JsonObject
            {
                ["valid_time_utc"] = $"2026-11-09T09:{idx * 5:00}:00Z",
                ["valid_time_local"] = $"2026-11-09T17:{idx * 5:00}:00+08:00",
                ["instant_quality_index"] = values[idx],
                ["instant_quality_level"] = "测试档位",
                ["physically_burnable"] = idx > 0,
                ["solar_elevation_deg"] = idx - 3.0,
                ["solar_azimuth_deg"] = 240.0,
                ["burnable_cloud_coverage"] = 0.42,
                ["dominant_burnable_pressure_family"] = ec ? "middle" : "high",
                ["dominant_burnable_cloud_base_asl_mean_m"] = ec ? 3900.0 : 8200.0,
                ["dominant_burnable_cloud_top_asl_mean_m"] = ec ? 8800.0 : 13400.0,
                ["dominant_burnable_cloud_base_agl_mean_m"] = 4200.0,
                ["dominant_burnable_cloud_top_agl_mean_m"] = 6800.0,
                ["brightness_color"] = ec ? 0.84 : 0.55,
                ["forward_burnable_cloud_ratio"] = ec ? 0.66 : 0.42,
                ["observer_return_transmission"] = ec ? 0.81 : 0.77,
                ["solar_incoming_block_risk"] = 0.14,
                ["quality_aod550"] = ec ? 0.23 : 0.21,
                ["selected_regime"] = "burnable_cloud_field",
                ["valid"] = true,
            });
        }
        // 附加一条窗外未判断时次，验证台账默认折叠与切换
        steps.Add(new JsonObject
        {
            ["valid_time_utc"] = "2026-11-09T09:25:00Z",
            ["valid_time_local"] = "2026-11-09T17:25:00+08:00",
            ["selected_regime"] = "invalid",
            ["valid"] = false,
        });

        var hexes = ec
            ? new[] { "#F6C46A", "#F2A152", "#EC7C43", "#E85D3F", "#D94649", "#B93B5B", "#8F3561" }
            : new[] { "#F8D17E", "#F5B562", "#F2944B", "#F07A3E", "#E36243", "#C54F54", "#98415C" };
        var dirs = new (string Zh, string En)[]
        {
            ("北", "N"), ("东北偏北", "NNE"), ("东北", "NE"), ("东北偏东", "ENE"),
            ("东", "E"), ("东南偏东", "ESE"), ("东南", "SE"), ("东南偏南", "SSE"),
            ("南", "S"), ("西南偏南", "SSW"), ("西南", "SW"), ("西南偏西", "WSW"),
            ("西", "W"), ("西北偏西", "WNW"), ("西北", "NW"), ("西北偏北", "NNW"),
        };
        var peakDeg = ec ? 247.5 : 225.0;
        var peakScore = ec ? 0.84 : 0.72;
        var bearings = new JsonArray();
        for (var i = 0; i < dirs.Length; i++)
        {
            var deg = i * 22.5;
            var diff = Math.Abs(((deg - peakDeg + 540.0) % 360.0) - 180.0);
            var shape = Math.Max(0.18, Math.Cos(diff * Math.PI / 180.0) * 0.5 + 0.5);
            var score = Math.Min(1.0, Math.Max(0.14, peakScore * shape));
            bearings.Add(new JsonObject
            {
                ["deg"] = deg,
                ["label_zh"] = dirs[i].Zh,
                ["label_en"] = dirs[i].En,
                ["score"] = Math.Round(score, 3),
            });
        }

        return new JsonObject
        {
            ["model_source"] = model,
            ["quality_index"] = ec ? 1.36 : 0.72,
            ["peak_quality_index"] = ec ? 1.36 : 0.72,
            ["peak_quality_level"] = ec ? "绚丽晚霞（测试）" : "中等晚霞（测试）",
            ["quality_level"] = ec ? "绚丽晚霞（测试）" : "中等晚霞（测试）",
            ["convective_risk_score"] = ec ? 0.12 : 0.18,
            ["dominant_burnable_pressure_family"] = ec ? "middle" : "high",
            ["dominant_burnable_cloud_base_agl_mean_m"] = 4200.0,
            ["dominant_burnable_cloud_top_agl_mean_m"] = 6800.0,
            ["forecast_mode"] = "point",
            ["compute_backend"] = "cpu",
            ["analysis_radius_km"] = 300.0,
            ["timezone_name"] = "Asia/Shanghai",
            ["cycle_time_utc"] = "2026-11-09T00:00:00Z",
            ["anchor_local"] = "2026-11-09T17:00:00+08:00",
            ["best_time_local"] = "2026-11-09T17:10:00+08:00",
            ["duration_minutes"] = ec ? 31 : 18,
            ["confidence_score"] = ec ? 0.79 : 0.61,
            ["selected_regime"] = "burnable_cloud_field",
            ["summary"] = "界面测试样例，用于离线 UI 审计验证（非真实气象预测）。",
            ["timing_window"] = new JsonObject
            {
                ["start_time_local"] = "2026-11-09T17:00:00+08:00",
                ["end_time_local"] = "2026-11-09T17:15:00+08:00",
            },
            ["aerosol"] = new JsonObject { ["aod550"] = ec ? 0.23 : 0.21, ["source"] = "cams-ads fixture", ["quality"] = "cams" },
            ["cloud_genus_proxy"] = new JsonObject
            {
                ["label"] = ec ? "altocumulus" : "cirrus",
                ["compatible_genera"] = ec ? new JsonArray { "altocumulus", "altostratus" } : new JsonArray { "cirrus", "cirrostratus" },
                ["candidate_scores"] = ec
                    ? new JsonObject { ["altocumulus"] = 0.68, ["altostratus"] = 0.22, ["nimbostratus"] = 0.10 }
                    : new JsonObject { ["cirrus"] = 0.72, ["cirrostratus"] = 0.19, ["cirrocumulus"] = 0.09 },
                ["confidence"] = ec ? 0.68 : 0.72,
                ["source"] = "pressure-phase-geometry-proxy",
                ["scoring_effect"] = false,
            },
            ["quality_components"] = new JsonObject
            {
                ["coverage_base_score"] = ec ? 1.18 : 0.78,
                ["raw_quality_index"] = ec ? 1.36 : 0.72,
                ["burnable_cloud_coverage_effective"] = ec ? 0.66 : 0.42,
                ["forward_burnable_cloud_load_effective"] = ec ? 0.71 : 0.48,
                ["forward_burnable_cloud_ratio_peak"] = ec ? 0.74 : 0.58,
                ["forward_burnable_cloud_field_amount_peak"] = ec ? 0.62 : 0.44,
                ["forward_view_cloud_field_amount_peak"] = ec ? 0.84 : 0.76,
                ["solar_incoming_block_risk_at_best_cloud"] = ec ? 0.11 : 0.19,
                ["cloud_material"] = ec ? 0.73 : 0.59,
                ["brightness_color"] = ec ? 0.84 : 0.55,
                ["quality_aod550"] = ec ? 0.23 : 0.21,
                ["duration_adjustment"] = ec ? 0.12 : -0.04,
                ["duration_factor"] = ec ? 1.092 : 0.945,
                ["incoming_aod_adjustment"] = ec ? -0.03 : -0.05,
                ["incoming_aod_illumination_factor"] = ec ? 0.92 : 0.86,
                ["observer_aod_adjustment"] = ec ? 0.09 : 0.03,
                ["observer_aod_contrast_factor"] = ec ? 0.81 : 0.77,
                ["observer_return_optical_depth_mean"] = ec ? 0.21 : 0.26,
                ["observer_return_transmission_mean"] = ec ? 0.81 : 0.77,
                ["forward_field_radius_km"] = 300,
                ["forward_sector_half_angle_deg"] = 25,
                ["quality_limiting_factor"] = ec ? "低层云轻微遮挡" : "前向有效可烧云量偏少",
            },
            ["spectral_color"] = new JsonObject
            {
                ["contract"] = "firesky-spectral-color-v1",
                ["peak_hex"] = hexes[3],
                ["peak_label_zh"] = ec ? "绯红火烧云" : "金橙火烧云",
                ["peak_label_en"] = ec ? "Crimson Glow" : "Golden Orange Glow",
                ["sky_gradient_hex"] = ec
                    ? new JsonArray { "#1A2D5E", "#6F4E7C", "#E85D3F", "#F7A05E" }
                    : new JsonArray { "#1E3468", "#7A5B7A", "#F07A3E", "#F8BC73" },
                ["solar_elevation_deg"] = ec ? -1.4 : -0.9,
                ["solar_azimuth_deg"] = 240.0,
            },
            ["azimuth_radar"] = new JsonObject
            {
                ["contract"] = "firesky-azimuth-radar-v1",
                ["solar_azimuth_deg"] = 240.0,
                ["best_azimuth_deg"] = peakDeg,
                ["best_direction_zh"] = ec ? "西南偏西" : "西南",
                ["best_direction_en"] = ec ? "WSW" : "SW",
                ["best_score"] = peakScore,
                ["recommendation_zh"] = ec
                    ? "建议面向西南偏西（248°）拍摄，中高云画布条件最佳，近处低云遮挡少"
                    : "建议面向西南（225°）拍摄，中高云画布条件最佳，近处低云遮挡少",
                ["recommendation_en"] = ec
                    ? "Best view toward WSW (248°): optimal mid/high canvas with low near-field blocking"
                    : "Best view toward SW (225°): optimal mid/high canvas with low near-field blocking",
                ["bearings"] = bearings,
            },
            ["cross_section"] = BuildAuditCrossSection(model),
            ["steps"] = steps,
        };
    }

    private static JsonObject BuildAuditCrossSection(string model)
    {
        var distances = new JsonArray();
        for (var i = 0; i <= 24; i++) distances.Add(i * 12.5);
        int[] levels = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150];
        double[] levelHeights = [180, 760, 1450, 3050, 4400, 5800, 7600, 9200, 10500, 11900, 13400];
        double[] levelTemps = [15, 9, 3, -6, -14, -24, -35, -44, -50, -55, -60];
        var terrain = new double[25];
        for (var s = 0; s <= 24; s++)
        {
            var westRidge = 620.0 * Math.Exp(-Math.Pow((s - 10.0) / 3.8, 2));
            var eastRidge = 390.0 * Math.Exp(-Math.Pow((s - 20.0) / 2.6, 2));
            terrain[s] = 120.0 + westRidge + eastRidge;
        }
        int Family(int l) => l >= 7 ? 1 : l >= 4 ? 2 : 3;
        bool Cloudy(int s, int l)
        {
            var shift = model == "ec" ? 3 : 0;
            return Family(l) switch
            {
                1 => l >= 7 && s >= 5 + shift && s <= 19 + shift,
                2 => s >= 2 + shift && s <= 14 + shift,
                _ => l <= 2 && s >= 14 - shift && s <= 24,
            };
        }
        double BaseFor(int f) => f == 1 ? 8200 : f == 2 ? 3900 : 700;
        double TopFor(int f) => f == 1 ? 14600 : f == 2 ? 8800 : 3400;

        JsonArray Grid(Func<int, int, double> fn)
        {
            var outer = new JsonArray();
            for (var s = 0; s <= 24; s++)
            {
                var row = new JsonArray();
                for (var l = 0; l < levels.Length; l++) row.Add(fn(s, l));
                outer.Add(row);
            }
            return outer;
        }
        JsonArray IntGrid(Func<int, int, int> fn)
        {
            var outer = new JsonArray();
            for (var s = 0; s <= 24; s++)
            {
                var row = new JsonArray();
                for (var l = 0; l < levels.Length; l++) row.Add(fn(s, l));
                outer.Add(row);
            }
            return outer;
        }

        var terrainArr = new JsonArray();
        var reqArr = new JsonArray();
        for (var s = 0; s <= 24; s++)
        {
            terrainArr.Add(terrain[s]);
            reqArr.Add(5200.0 + s * 115.0);
        }
        var levelsArr = new JsonArray();
        foreach (var p in levels) levelsArr.Add(p);

        return new JsonObject
        {
            ["valid_time_local"] = "2026-11-09T17:10:00+08:00",
            ["solar_azimuth_deg"] = 240.0,
            ["solar_elevation_deg"] = -2.0,
            ["analysis_radius_km"] = 300.0,
            ["distances_km"] = distances,
            ["pressure_levels_hpa"] = levelsArr,
            ["terrain_elevation_asl_m"] = terrainArr,
            ["required_illumination_base_asl_m"] = reqArr,
            ["theoretical_targets"] = new JsonArray
            {
                new JsonObject { ["status"] = "burnable", ["pressure_family"] = "high", ["distance_km"] = 75.0, ["cloud_base_asl_m"] = 8200.0, ["cloud_top_asl_m"] = 14600.0 },
                new JsonObject { ["status"] = "burnable", ["pressure_family"] = "middle", ["distance_km"] = 175.0, ["cloud_base_asl_m"] = 3900.0, ["cloud_top_asl_m"] = 8800.0 },
            },
            ["fields"] = new JsonObject
            {
                ["geopotential_height_asl_m"] = Grid((s, l) => levelHeights[l] + terrain[s] + 70 * Math.Sin((s + l) * 0.4)),
                ["temperature_c"] = Grid((s, l) => levelTemps[l] + 1.2 * Math.Sin(s * 0.35)),
                ["rh_percent"] = Grid((s, l) => Cloudy(s, l) ? 78 + ((s * 7 + l * 11) % 20) : 28 + ((s * 5 + l * 3) % 34)),
                ["cloud_amount"] = Grid((s, l) => Cloudy(s, l) ? 0.32 + ((s + l * 3) % 5) * 0.12 : 0),
                ["cloud_presence"] = Grid((s, l) => Cloudy(s, l) ? 1 : 0),
                ["cloud_fraction_percent"] = Grid((s, l) => Cloudy(s, l) ? 38 + ((s * 3 + l) % 6) * 10 : 0),
                ["pressure_family_code"] = IntGrid((_, l) => Family(l)),
                ["cloud_layer_status"] = IntGrid((s, l) => !Cloudy(s, l) ? 0 : (s >= 10 && s <= 13 ? 3 : (s >= 14 && s <= 16 ? 2 : 0))),
                ["cloud_layer_base_asl_m"] = Grid((s, l) => Cloudy(s, l) ? BaseFor(Family(l)) : 0),
                ["cloud_layer_top_asl_m"] = Grid((s, l) => Cloudy(s, l) ? TopFor(Family(l)) : 0),
            },
        };
    }

}
