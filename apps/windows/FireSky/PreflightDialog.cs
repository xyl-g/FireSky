using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace FireSky;

/// <summary>
/// 预检对话框（对齐 macOS PreflightView）：数据集行、发布核验、准备下载，
/// 以及周期龄确认（未核验/非最新发布需显式确认后才发起 forecast）。
/// WinUI 不允许同时打开两个 ContentDialog，周期龄确认复用本对话框切换内容。
/// </summary>
public sealed class PreflightDialog : ContentDialog
{
    private sealed class CheckRow
    {
        internal required string Title { get; init; }
        internal required bool Ready { get; init; }
        internal required bool Verified { get; init; }
        internal required bool Latest { get; init; }
    }

    private readonly EngineClient engine;
    private readonly string kind;
    private readonly JsonObject payload;
    private readonly bool isNowcast;
    private readonly StackPanel rows = new() { Spacing = 0 };
    private readonly TextBlock headerSubtitle = new() { FontSize = 13, Opacity = .8, TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock state = new() { TextWrapping = TextWrapping.Wrap };
    private readonly ProgressBar bar = new() { Minimum = 0, Maximum = 100, Visibility = Visibility.Collapsed };
    private readonly Button refresh = new() { Content = new SymbolIcon(Symbol.Refresh) };
    private readonly Grid dialogContent;
    private CancellationTokenSource? operation;
    private string? session;
    private bool ready, working, closed;
    private int revision;
    private string mode = "preflight";
    private List<CheckRow> checkRows = [];
    /// <summary>周期龄确认后的 forecast 参数：带 confirmed 标记的会话。</summary>
    public string? ConfirmedSession { get; private set; }
    public bool CycleAgeConfirmed { get; private set; }

    public PreflightDialog(EngineClient engine, string kind, JsonObject payload)
    {
        this.engine = engine;
        this.kind = kind;
        this.payload = payload;
        isNowcast = kind == "nowcast";
        var confirmTitle = isNowcast ? "确定分析" : "确定预测";
        Title = isNowcast ? "实况准备" : "预测准备";
        PrimaryButtonText = "下载缺失数据";
        SecondaryButtonText = confirmTitle;
        IsSecondaryButtonEnabled = false;
        CloseButtonText = "取消";
        IsPrimaryButtonEnabled = false;
        DefaultButton = ContentDialogButton.Primary;
        var content = new StackPanel { Spacing = 16, MinWidth = 460 };
        var header = new StackPanel { Spacing = 6 };
        header.Children.Add(new TextBlock
        {
            Text = payload["name"]?.GetValue<string>() ?? "本次区域",
            FontSize = 16, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        header.Children.Add(headerSubtitle);
        var headerGrid = new Grid();
        headerGrid.Children.Add(header);
        refresh.HorizontalAlignment = HorizontalAlignment.Right;
        refresh.VerticalAlignment = VerticalAlignment.Center;
        ToolTipService.SetToolTip(refresh, "重新检查");
        Grid.SetColumn(refresh, 1);
        headerGrid.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        headerGrid.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        headerGrid.Children.Add(refresh);
        content.Children.Add(headerGrid);
        content.Children.Add(rows);
        content.Children.Add(bar);
        state.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        content.Children.Add(state);
        Content = dialogContent = new Grid();
        dialogContent.Children.Add(WrapScrollable(content));
        Opened += async (_, _) => await Check();
        refresh.Click += async (_, _) => await Check();
        PrimaryButtonClick += async (_, e) =>
        {
            if (mode == "cycleage")
            {
                // 周期龄确认「继续预测」：提交会话并带 cycle_age_confirmed。
                ConfirmedSession = session;
                CycleAgeConfirmed = true;
                return;
            }
            if (working || session == null) { e.Cancel = true; return; }
            if (ready)
            {
                if (NeedsCycleAgeConfirmation) { e.Cancel = true; ShowCycleAgeConfirm(); return; }
                ConfirmedSession = session;
                return;
            }
            e.Cancel = true;
            var deferral = e.GetDeferral();
            try { await Prepare(); }
            finally { deferral.Complete(); }
        };
        SecondaryButtonClick += async (_, e) =>
        {
            e.Cancel = true;
            if (mode == "cycleage" || !ready || working || session == null) return;
            // B6 基准：数据已就绪但有未核验/非最新发布的周期时，先弹确认。
            if (NeedsCycleAgeConfirmation) { ShowCycleAgeConfirm(); return; }
            var deferral = e.GetDeferral();
            try { await Prepare(); }
            finally { deferral.Complete(); }
        };
        CloseButtonClick += (_, e) =>
        {
            // 周期龄确认的「返回更新数据」回到预检界面（对齐 macOS 确认弹层取消），
            // 会话保留，用户可继续「更新数据」或「取消」。
            if (mode == "cycleage") { e.Cancel = true; RestorePreflightMode(); }
        };
        Closed += (_, _) =>
        {
            closed = true; revision++; operation?.Cancel();
            if (ConfirmedSession == null && session != null)
                _ = engine.Call("dismiss", new() { ["session"] = session });
        };
    }

    private string ConfirmTitle => isNowcast ? "确定分析" : "确定预测";

    /// <summary>镜像引擎 needs_cycle_age_confirmation：就绪数据中存在未核验或非最新发布。</summary>
    private bool NeedsCycleAgeConfirmation =>
        ready && checkRows.Any(row => row.Ready && (!row.Verified || !row.Latest));

    private string CycleAgeSummary
    {
        get
        {
            var titles = checkRows.Where(row => row.Ready && (!row.Verified || !row.Latest))
                .Select(row => row.Title).ToList();
            return titles.Count == 0 ? "部分数据" : string.Join("、", titles);
        }
    }

    /// <summary>周期龄确认弹层：主按钮「继续预测」经 PrimaryButtonClick 提交，
    /// 「返回更新数据」经 CloseButtonClick 回到预检（macOS confirmationDialog 口径）。</summary>
    private void ShowCycleAgeConfirm()
    {
        mode = "cycleage";
        Title = "数据周期确认";
        IsPrimaryButtonEnabled = true;
        IsSecondaryButtonEnabled = false;
        PrimaryButtonText = "继续预测";
        SecondaryButtonText = "";
        CloseButtonText = "返回更新数据";
        DefaultButton = ContentDialogButton.Primary;
        refresh.IsEnabled = false;
        dialogContent.Children.Clear();
        dialogContent.Children.Add(new TextBlock
        {
            Text = $"{CycleAgeSummary} 的发布周期未核验或不是最新发布。可以继续预测（结果可能略有偏差），也可以返回并点击「更新数据」获取最新周期。",
            TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
        });
    }

    private void RestorePreflightMode()
    {
        mode = "preflight";
        Title = isNowcast ? "实况准备" : "预测准备";
        PrimaryButtonText = ready ? ConfirmTitle : "下载缺失数据";
        SecondaryButtonText = ready ? "更新数据" : ConfirmTitle;
        CloseButtonText = "取消";
        IsPrimaryButtonEnabled = !working && session != null;
        IsSecondaryButtonEnabled = !working && ready && session != null;
        DefaultButton = ContentDialogButton.Primary;
        refresh.IsEnabled = !working;
        dialogContent.Children.Clear();
        dialogContent.Children.Add(BuildPreflightBody());
        Busy(working);
    }

    private Grid BuildPreflightBody()
    {
        var content = new StackPanel { Spacing = 16 };
        var header = new StackPanel { Spacing = 6 };
        header.Children.Add(new TextBlock
        {
            Text = payload["name"]?.GetValue<string>() ?? "本次区域",
            FontSize = 16, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        header.Children.Add(headerSubtitle);
        var headerGrid = new Grid();
        headerGrid.Children.Add(header);
        refresh.HorizontalAlignment = HorizontalAlignment.Right;
        refresh.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(refresh, 1);
        headerGrid.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        headerGrid.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        headerGrid.Children.Add(refresh);
        content.Children.Add(headerGrid);
        content.Children.Add(rows);
        content.Children.Add(bar);
        content.Children.Add(state);
        return WrapScrollable(content);
    }

    private static Grid WrapScrollable(StackPanel content)
    {
        var scroll = new ScrollViewer
        {
            Content = content, MaxHeight = 420, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        };
        var grid = new Grid();
        grid.Children.Add(scroll);
        return grid;
    }

    private void Busy(bool value)
    {
        working = value;
        refresh.IsEnabled = !value && mode == "preflight";
        PrimaryButtonText = mode == "cycleage" ? "继续预测"
            : ready ? ConfirmTitle : "下载缺失数据";
        SecondaryButtonText = mode == "cycleage" ? ""
            : ready ? "更新数据" : ConfirmTitle;
        IsPrimaryButtonEnabled = !value && session != null && mode == "preflight";
        IsSecondaryButtonEnabled = !value && ready && session != null && mode == "preflight";
        if (GetTemplateChild("SecondaryButton") is Button secondary)
        {
            ToolTipService.SetToolTip(secondary, ready ? "更新本次预测所需数据" : "需先下载数据方可预测");
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetHelpText(
                secondary, ready ? "更新本次预测所需数据" : "需先下载数据方可预测");
        }
        bar.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
        bar.IsIndeterminate = value;
    }

    private async Task Check()
    {
        if (working || mode != "preflight") return;
        var current = ++revision;
        var old = session; session = null; ready = false;
        if (old != null) _ = engine.Call("dismiss", new() { ["session"] = old });
        operation = new(); Busy(true); state.Text = "正在检查本地数据";
        headerSubtitle.Text = "";
        try
        {
            var response = await engine.Call("check",
                new() { ["kind"] = kind, ["payload"] = payload.DeepClone() },
                cancellation: operation.Token);
            if (closed || current != revision)
            {
                if (response["session"] is { } stale)
                    _ = engine.Call("dismiss", new() { ["session"] = stale.GetValue<string>() });
                return;
            }
            session = response["session"]?.GetValue<string>();
            Render(response);
        }
        catch (Exception error)
        {
            if (!closed) state.Text = FriendlyMessages.Map(error.Message);
        }
        finally { Busy(false); }
        if (!closed && session != null) _ = Publications(current, session);
    }

    private async Task Publications(int current, string token)
    {
        try
        {
            var response = await engine.Call("publications", new() { ["session"] = token });
            if (!closed && current == revision && !working) { Render(response); Busy(false); }
        }
        catch (Exception) { /* Local readiness does not depend on provider availability. */ }
    }

    private async Task Prepare()
    {
        if (session == null || working || mode != "preflight") return;
        revision++; ready = false; operation = new(); Busy(true); state.Text = "正在准备数据";
        try
        {
            var response = await engine.Call("prepare", new() { ["session"] = session },
                progress: e => DispatcherQueue.TryEnqueue(() =>
                {
                    if (closed || mode != "preflight") return;
                    bar.IsIndeterminate = false;
                    bar.Value = e["percent"]?.GetValue<double>() ?? 0;
                    var message = e["message"]?.GetValue<string>();
                    state.Text = string.IsNullOrEmpty(message)
                        ? $"正在准备 {e["source"]?.GetValue<string>()} · {bar.Value:0}%"
                        : message;
                }), cancellation: operation.Token);
            if (!closed && mode == "preflight") Render(response);
        }
        catch (Exception error)
        {
            if (!closed && mode == "preflight") state.Text = FriendlyMessages.Map(error.Message);
        }
        finally { Busy(false); }
    }

    private void Render(JsonObject response)
    {
        var report = response["report"];
        ready = report?["ready"]?.GetValue<bool>() ?? false;
        checkRows = [];
        rows.Children.Clear();
        var missing = new List<string>();
        var updates = new List<string>();
        var datasetRows = (report?["datasets"] as JsonArray ?? new())
            .OfType<JsonObject>().ToList();
        for (var index = 0; index < datasetRows.Count; index++)
        {
            var item = datasetRows[index];
            var source = item["source"]?.GetValue<string>() ?? "";
            var title = source == "ecmwf" ? "EC" : source == "hsd" ? "卫星帧" : source.ToUpperInvariant();
            var available = item["ready"]?.GetValue<bool>() ?? false;
            var verified = item["verified"]?.GetValue<bool>() ?? false;
            var latest = item["is_latest"]?.GetValue<bool>() ?? false;
            var old = verified && !latest;
            var count = item["missing_count"]?.GetValue<int>() ?? 0;
            checkRows.Add(new CheckRow
            {
                Title = title, Ready = available, Verified = verified, Latest = latest,
            });
            if (!available) missing.Add(title);
            if (available && old) updates.Add(title);
            var line = new Grid { Padding = new Thickness(0, 14, 0, 14), ColumnSpacing = 12 };
            line.ColumnDefinitions.Add(new() { Width = new GridLength(32) });
            line.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
            line.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            line.Children.Add(new SymbolIcon(source == "hsd" ? Symbol.Globe : Symbol.Save));
            var labels = new StackPanel { Spacing = 4 };
            labels.Children.Add(new TextBlock
            {
                Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            labels.Children.Add(new TextBlock
            {
                Text = !available ? (count > 0 ? "本次预测数据不完整" : "尚未下载本次预测数据")
                    : old ? "有新发布的数据可更新" : verified ? "发布周期已核验" : "更新待核验",
                FontSize = 12, Opacity = .8,
            });
            Grid.SetColumn(labels, 1);
            line.Children.Add(labels);
            var badge = new StackPanel
            {
                Orientation = Orientation.Horizontal, Spacing = 6,
                VerticalAlignment = VerticalAlignment.Center,
            };
            var glyph = new FontIcon
            {
                Glyph = !available ? "\uE896" : old ? "\uE72C" : "\uE73E",
                FontSize = 15,
                Foreground = !available || old
                    ? (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["TextFillColorPrimaryBrush"]
                    : new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Green),
            };
            badge.Children.Add(glyph);
            badge.Children.Add(new TextBlock
            {
                Text = !available ? "需下载" : old ? "可更新" : "已下载",
                VerticalAlignment = VerticalAlignment.Center,
            });
            Grid.SetColumn(badge, 2);
            line.Children.Add(badge);
            rows.Children.Add(line);
            if (index < datasetRows.Count - 1)
                rows.Children.Add(new Microsoft.UI.Xaml.Controls.Border
                {
                    BorderThickness = new Thickness(0, 0, 0, 1),
                    BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources
                        ["CardStrokeColorDefaultBrush"],
                });
        }
        state.Text = !ready ? "需下载 " + string.Join("、", missing)
            : updates.Count > 0 ? "数据已齐，可更新 " + string.Join("、", updates)
            : isNowcast ? "数据已齐，可开始分析" : "数据已齐，可开始预测";
        headerSubtitle.Text = state.Text;
        Busy(false);
    }
}
