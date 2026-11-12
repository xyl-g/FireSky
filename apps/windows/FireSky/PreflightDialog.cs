using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace FireSky;

public sealed class PreflightDialog : ContentDialog
{
    private readonly EngineClient engine;
    private readonly string kind;
    private readonly JsonObject payload;
    private readonly StackPanel rows = new() { Spacing = 0 };
    private readonly TextBlock state = new() { TextWrapping = TextWrapping.Wrap };
    private readonly ProgressBar bar = new() { Minimum = 0, Maximum = 100, Visibility = Visibility.Collapsed };
    private readonly Button refresh = new() { Content = new SymbolIcon(Symbol.Refresh) };
    private CancellationTokenSource? operation;
    private string? session;
    private bool ready, working, closed;
    private int revision;
    public string? ConfirmedSession { get; private set; }

    public PreflightDialog(EngineClient engine, string kind, JsonObject payload)
    {
        this.engine = engine; this.kind = kind; this.payload = payload;
        Title = "预测准备"; PrimaryButtonText = "下载缺失数据";
        SecondaryButtonText = "确定预测"; IsSecondaryButtonEnabled = false; CloseButtonText = "取消";
        IsPrimaryButtonEnabled = false; DefaultButton = ContentDialogButton.Primary;
        var content = new StackPanel { Spacing = 16, MinWidth = 420 };
        var header = new Grid();
        header.Children.Add(new TextBlock { Text = payload["name"]?.GetValue<string>() ?? "本次区域", VerticalAlignment = VerticalAlignment.Center });
        refresh.HorizontalAlignment = HorizontalAlignment.Right;
        ToolTipService.SetToolTip(refresh, "重新检查");
        header.Children.Add(refresh);
        content.Children.Add(header); content.Children.Add(rows); content.Children.Add(bar);
        state.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        content.Children.Add(state); Content = content;
        Opened += async (_, _) => await Check();
        refresh.Click += async (_, _) => await Check();
        PrimaryButtonClick += async (_, e) =>
        {
            if (working || session == null) { e.Cancel = true; return; }
            if (ready) { ConfirmedSession = session; return; }
            e.Cancel = true;
            var deferral = e.GetDeferral();
            try { await Prepare(); }
            finally { deferral.Complete(); }
        };
        SecondaryButtonClick += async (_, e) =>
        {
            e.Cancel = true;
            if (!ready || working || session == null) return;
            var deferral = e.GetDeferral();
            try { await Prepare(); }
            finally { deferral.Complete(); }
        };
        Closed += (_, _) =>
        {
            closed = true; revision++; operation?.Cancel();
            if (ConfirmedSession == null && session != null)
                _ = engine.Call("dismiss", new() { ["session"] = session });
        };
    }

    private void Busy(bool value)
    {
        working = value;
        refresh.IsEnabled = !value;
        PrimaryButtonText = ready ? "确定预测" : "下载缺失数据";
        SecondaryButtonText = ready ? "更新数据" : "确定预测";
        IsPrimaryButtonEnabled = !value && session != null;
        IsSecondaryButtonEnabled = !value && ready && session != null;
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
        if (working) return;
        var current = ++revision;
        var old = session; session = null; ready = false;
        if (old != null) _ = engine.Call("dismiss", new() { ["session"] = old });
        operation = new(); Busy(true); state.Text = "正在检查本地数据";
        try
        {
            var response = await engine.Call("check", new() { ["kind"] = kind, ["payload"] = payload.DeepClone() }, cancellation: operation.Token);
            if (closed || current != revision)
            {
                if (response["session"] is { } stale) _ = engine.Call("dismiss", new() { ["session"] = stale.GetValue<string>() });
                return;
            }
            session = response["session"]?.GetValue<string>();
            Render(response);
        }
        catch (Exception error) { if (!closed) state.Text = error.Message; }
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
        if (session == null || working) return;
        revision++; ready = false; operation = new(); Busy(true); state.Text = "正在准备数据";
        try
        {
            var response = await engine.Call("prepare", new() { ["session"] = session },
                progress: e => DispatcherQueue.TryEnqueue(() =>
                {
                    if (closed) return;
                    bar.IsIndeterminate = false; bar.Value = e["percent"]?.GetValue<double>() ?? 0;
                    var message = e["message"]?.GetValue<string>();
                    state.Text = string.IsNullOrEmpty(message)
                        ? $"正在准备 {e["source"]?.GetValue<string>()} · {bar.Value:0}%"
                        : message;
                }), cancellation: operation.Token);
            if (!closed) Render(response);
        }
        catch (Exception error) { if (!closed) state.Text = error.Message; }
        finally { Busy(false); }
    }

    private void Render(JsonObject response)
    {
        var report = response["report"];
        ready = report?["ready"]?.GetValue<bool>() ?? false;
        rows.Children.Clear();
        var missing = new List<string>();
        var updates = new List<string>();
        foreach (var item in report?["datasets"] as JsonArray ?? new())
        {
            var source = item?["source"]?.GetValue<string>() ?? "";
            var title = source == "ecmwf" ? "EC" : source == "hsd" ? "卫星帧" : source.ToUpperInvariant();
            var available = item?["ready"]?.GetValue<bool>() ?? false;
            var verified = item?["verified"]?.GetValue<bool>() ?? false;
            var old = verified && !(item?["is_latest"]?.GetValue<bool>() ?? false);
            var count = item?["missing_count"]?.GetValue<int>() ?? 0;
            if (!available) missing.Add(title);
            if (available && old) updates.Add(title);
            var line = new Grid { Padding = new Thickness(0, 14, 0, 14), ColumnSpacing = 12 };
            line.ColumnDefinitions.Add(new() { Width = new GridLength(32) });
            line.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
            line.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            line.Children.Add(new SymbolIcon(source == "hsd" ? Symbol.Globe : Symbol.Save));
            var labels = new StackPanel { Spacing = 4 };
            labels.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            labels.Children.Add(new TextBlock
            {
                Text = !available ? (count > 0 ? "本次预测数据不完整" : "尚未下载本次预测数据")
                    : old ? "有新发布的数据可更新" : verified ? "发布周期已核验" : "更新待核验",
                FontSize = 12, Opacity = .8
            });
            Grid.SetColumn(labels, 1); line.Children.Add(labels);
            var badge = new TextBlock
            {
                Text = !available ? "需下载" : old ? "可更新" : "已下载",
                VerticalAlignment = VerticalAlignment.Center
            };
            Grid.SetColumn(badge, 2); line.Children.Add(badge); rows.Children.Add(line);
        }
        state.Text = !ready ? "需下载 " + string.Join("、", missing)
            : updates.Count > 0 ? "数据已齐，可更新 " + string.Join("、", updates) : "数据已齐，可开始预测";
    }
}
