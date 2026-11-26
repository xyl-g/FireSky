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
        internal required string Source { get; init; }
        internal required string Title { get; init; }
        internal required string Detail { get; init; }
        internal required string StatusBadge { get; init; }
        internal required bool Old { get; init; }
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
        var confirmTitle = isNowcast ? L10n.T("确定分析") : L10n.T("确定预测");
        Title = isNowcast ? L10n.T("实况准备") : L10n.T("预测准备");
        PrimaryButtonText = L10n.T("下载缺失数据");
        SecondaryButtonText = confirmTitle;
        IsSecondaryButtonEnabled = false;
        CloseButtonText = L10n.T("取消");
        IsPrimaryButtonEnabled = false;
        DefaultButton = ContentDialogButton.Primary;
        var content = new StackPanel { Spacing = 16, MinWidth = 460 };
        var header = new StackPanel { Spacing = 6 };
        header.Children.Add(new TextBlock
        {
            Text = payload["name"]?.GetValue<string>() ?? L10n.T("本次区域"),
            FontSize = 16, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        header.Children.Add(headerSubtitle);
        var headerGrid = new Grid();
        headerGrid.Children.Add(header);
        refresh.HorizontalAlignment = HorizontalAlignment.Right;
        refresh.VerticalAlignment = VerticalAlignment.Center;
        ToolTipService.SetToolTip(refresh, L10n.T("重新检查"));
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
                // 周期龄确认主按钮「更新到最新数据」：回到预检并立即执行 prepare 下载最新数据。
                e.Cancel = true;
                RestorePreflightMode();
                var deferral = e.GetDeferral();
                try { await Prepare(); }
                finally { deferral.Complete(); }
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
            var prepareDeferral = e.GetDeferral();
            try { await Prepare(); }
            finally { prepareDeferral.Complete(); }
        };
        SecondaryButtonClick += async (_, e) =>
        {
            if (mode == "cycleage")
            {
                // 次按钮「使用当前缓存继续」：提交会话并带 cycle_age_confirmed。
                ConfirmedSession = session;
                CycleAgeConfirmed = true;
                return;
            }
            e.Cancel = true;
            if (!ready || working || session == null) return;
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

    private string ConfirmTitle => isNowcast ? L10n.T("确定分析") : L10n.T("确定预测");

    /// <summary>镜像引擎 needs_cycle_age_confirmation：就绪数据中存在未核验或非最新发布。</summary>
    private bool NeedsCycleAgeConfirmation =>
        ready && checkRows.Any(row => row.Ready && (!row.Verified || !row.Latest));

    /// <summary>周期龄确认弹层：列出过时/未核验数据源，主按钮「更新到最新数据」，
    /// 次按钮「使用当前缓存继续」，返回回到预检（对齐 macOS CycleAgeConfirmSheet）。</summary>
    private void ShowCycleAgeConfirm()
    {
        mode = "cycleage";
        Title = L10n.T("数据周期确认");
        IsPrimaryButtonEnabled = !working && session != null;
        IsSecondaryButtonEnabled = !working && session != null;
        PrimaryButtonText = L10n.T("更新到最新数据");
        SecondaryButtonText = L10n.T("使用当前缓存继续");
        CloseButtonText = L10n.T("返回");
        DefaultButton = ContentDialogButton.Primary;
        refresh.IsEnabled = false;

        var flagged = checkRows.Where(row => row.Ready && (!row.Verified || !row.Latest)).ToList();
        var body = new StackPanel { Spacing = 14, MinWidth = 460 };

        var intro = new Grid { ColumnSpacing = 12 };
        intro.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        intro.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        var iconBox = new Border
        {
            Width = 38, Height = 38, CornerRadius = new CornerRadius(10),
            Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(0x22, 0xD2, 0x5A, 0x36)),
            Child = new FontIcon
            {
                Glyph = "\uE823", FontSize = 18,
                Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(0xFF, 0xD2, 0x5A, 0x36)),
            },
        };
        var introText = new TextBlock
        {
            Text = L10n.T("以下本地数据源尚未核验为最新发布周期；使用旧周期可立即出图，但临近起报切换时结果可能略有偏差。"),
            TextWrapping = TextWrapping.Wrap,
            IsTextSelectionEnabled = true,
            FontSize = 12,
            Opacity = .85,
            VerticalAlignment = VerticalAlignment.Center,
        };
        Grid.SetColumn(introText, 1);
        intro.Children.Add(iconBox);
        intro.Children.Add(introText);
        body.Children.Add(intro);

        var listPanel = new StackPanel { Spacing = 0 };
        for (var i = 0; i < flagged.Count; i++)
        {
            var row = flagged[i];
            var line = new Grid { Padding = new Thickness(12, 10, 12, 10), ColumnSpacing = 10 };
            line.ColumnDefinitions.Add(new() { Width = new GridLength(24) });
            line.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
            line.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            line.Children.Add(new SymbolIcon(row.Source == "hsd" ? Symbol.Globe : Symbol.Save)
            {
                VerticalAlignment = VerticalAlignment.Center,
            });
            var textCol = new StackPanel { Spacing = 2 };
            textCol.Children.Add(new TextBlock
            {
                Text = row.Title,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            textCol.Children.Add(new TextBlock
            {
                Text = row.Detail,
                FontSize = 12,
                Opacity = .75,
                TextWrapping = TextWrapping.Wrap,
            });
            Grid.SetColumn(textCol, 1);
            line.Children.Add(textCol);

            var badgeColor = row.Old
                ? Windows.UI.Color.FromArgb(0x24, 0xD2, 0x5A, 0x36)
                : Windows.UI.Color.FromArgb(0x20, 0x8A, 0x63, 0x21);
            var badgeTextColor = row.Old
                ? Windows.UI.Color.FromArgb(0xFF, 0xD2, 0x5A, 0x36)
                : Windows.UI.Color.FromArgb(0xFF, 0xB8, 0x7A, 0x1A);
            var chip = new Border
            {
                CornerRadius = new CornerRadius(6),
                Padding = new Thickness(8, 3, 8, 3),
                Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(badgeColor),
                VerticalAlignment = VerticalAlignment.Center,
                Child = new TextBlock
                {
                    Text = row.StatusBadge,
                    FontSize = 11,
                    FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                    Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(badgeTextColor),
                },
            };
            Grid.SetColumn(chip, 2);
            line.Children.Add(chip);
            listPanel.Children.Add(line);

            if (i < flagged.Count - 1)
            {
                listPanel.Children.Add(new Border
                {
                    BorderThickness = new Thickness(0, 0, 0, 1),
                    BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
                });
            }
        }

        body.Children.Add(new Border
        {
            CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1),
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["LayerFillColorDefaultBrush"],
            Child = listPanel,
        });

        dialogContent.Children.Clear();
        dialogContent.Children.Add(WrapScrollable(body));
    }

    private void RestorePreflightMode()
    {
        mode = "preflight";
        Title = isNowcast ? L10n.T("实况准备") : L10n.T("预测准备");
        PrimaryButtonText = ready ? ConfirmTitle : L10n.T("下载缺失数据");
        SecondaryButtonText = ready ? L10n.T("更新数据") : ConfirmTitle;
        CloseButtonText = L10n.T("取消");
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
            Text = payload["name"]?.GetValue<string>() ?? L10n.T("本次区域"),
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
        PrimaryButtonText = mode == "cycleage" ? L10n.T("更新到最新数据")
            : ready ? ConfirmTitle : L10n.T("下载缺失数据");
        SecondaryButtonText = mode == "cycleage" ? L10n.T("使用当前缓存继续")
            : ready ? L10n.T("更新数据") : ConfirmTitle;
        IsPrimaryButtonEnabled = !value && session != null;
        IsSecondaryButtonEnabled = !value && session != null && (mode == "cycleage" || ready);
        if (GetTemplateChild("SecondaryButton") is Button secondary)
        {
            var tip = mode == "cycleage"
                ? L10n.T("使用当前本地缓存直接继续预测")
                : ready ? L10n.T("更新本次预测所需数据") : L10n.T("需先下载数据方可预测");
            ToolTipService.SetToolTip(secondary, tip);
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetHelpText(secondary, tip);
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
        operation = new(); Busy(true); state.Text = L10n.T("正在检查本地数据");
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
        revision++; ready = false; operation = new(); Busy(true); state.Text = L10n.T("正在准备数据");
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
            var aerosolSrc = item["aerosol_source"]?.GetValue<string>() ?? "";
            var title = source == "ecmwf" ? "EC"
                : source == "cams" && aerosolSrc == "geos-fp" ? "GEOS-FP"
                : source == "geos-fp" ? "GEOS-FP"
                : source == "hsd" ? L10n.T("卫星帧")
                : source.ToUpperInvariant();
            var available = item["ready"]?.GetValue<bool>() ?? false;
            var verified = item["verified"]?.GetValue<bool>() ?? false;
            var latest = item["is_latest"]?.GetValue<bool>() ?? false;
            var old = verified && !latest;
            var count = item["missing_count"]?.GetValue<int>() ?? 0;
            var detail = !available ? (count > 0 ? L10n.T("本次预测数据不完整") : L10n.T("尚未下载本次预测数据"))
                : old ? L10n.T("有新发布的数据可更新") : verified ? L10n.T("发布周期已核验") : L10n.T("更新待核验");
            var badgeText = !available ? L10n.T("需下载") : old ? L10n.T("可更新") : verified ? L10n.T("已下载") : L10n.T("待核验");
            checkRows.Add(new CheckRow
            {
                Source = source, Title = title, Detail = detail, StatusBadge = badgeText,
                Old = old, Ready = available, Verified = verified, Latest = latest,
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
                Text = detail,
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
                Text = !available ? L10n.T("需下载") : old ? L10n.T("可更新") : L10n.T("已下载"),
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
        state.Text = !ready ? L10n.T("需下载 ") + string.Join("、", missing)
            : updates.Count > 0 ? L10n.T("数据已齐，可更新 ") + string.Join("、", updates)
            : isNowcast ? L10n.T("数据已齐，可开始分析") : L10n.T("数据已齐，可开始预测");
        headerSubtitle.Text = state.Text;
        Busy(false);
    }
}
