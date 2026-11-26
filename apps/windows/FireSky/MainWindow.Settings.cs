using Microsoft.UI;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;

namespace FireSky;

/// <summary>
/// 设置页业务逻辑（v1.2.0 四分类）：通用主题、数据（下载源 / CAMS / 连通性 /
/// 后台同步 / 存储与保留 / 数据目录）、位置与海拔（海拔来源三档）、高级（引擎）。
/// 合同与 macOS Store 保持一致：UI 只镜像引擎设置文件，引擎是权威校验方。
/// </summary>
public sealed partial class MainWindow
{
    // ---------- 通用 ----------

    // OnAppearanceChanged / OnSettingsCategoryChanged / OnChooseDirectory 在核心文件。

    private void UpdateRuntimeSummary()
    {
        if (SummaryEngineText == null) return;
        SummaryEngineText.Text = online ? L10n.T("已连接") : L10n.T("未连接");
        SummaryEngineDot.Fill = ThemeBrush(online ? "FireSkySuccessBrush" : "FireSkyWarningBrush");

        var syncHealthy = dataSyncEnabled || dataSyncConfigured;
        SummarySyncText.Text = dataSyncEnabled ? L10n.T("已启用")
            : dataSyncConfigured ? L10n.T("已配置") : L10n.T("按需准备");
        SummarySyncDot.Fill = ThemeBrush(syncHealthy ? "FireSkySuccessBrush" : "FireSkyWarningBrush");

        SummaryCamsText.Text = camsConfigured ? L10n.T("已配置") : L10n.T("未配置");
        SummaryCamsDot.Fill = ThemeBrush(camsConfigured ? "FireSkySuccessBrush" : "FireSkyWarningBrush");

        SummaryRetentionText.Text = dataSyncRetentionConfigured ? L10n.T("已配置") : L10n.T("不限制");
        SummaryRetentionDot.Fill = ThemeBrush(dataSyncRetentionConfigured ? "FireSkySuccessBrush" : "FireSkyWarningBrush");
    }

    // ---------- 数据：下载源 ----------

    // OnEcmwfSourceChanged 在核心文件。

    // ---------- 数据：CAMS 账号 ----------

    internal string CamConfigurationDescription() => camsConfiguration switch
    {
        null => L10n.T("正在检查本地配置"),
        _ => StringOf(camsConfiguration["state"]) switch
        {
            "configured" => L10n.T("已检测到 Key，下载时验证授权。"),
            "wrong_endpoint" => L10n.T("接口地址不是 Copernicus ADS，请重新配置。"),
            "tls_disabled" => L10n.T("配置关闭了 TLS 验证，请重新保存以启用安全连接。"),
            "unreadable" => L10n.T("配置文件无法读取或格式有误，请修正后重新检查。"),
            _ => L10n.T("尚未配置 CAMS Key，本地数据缺失时无法下载。"),
        },
    };

    private async Task LoadCamsConfiguration()
    {
        if (!online) return;
        try
        {
            camsConfiguration = await engine.Call("cams_credentials");
        }
        catch (Exception)
        {
            camsMessage = L10n.T("无法检查配置，请确认本地引擎已连接。");
            camsSaveFailed = true;
        }
        ApplyCamsState();
    }

    private async void OnRefreshCams(object sender, RoutedEventArgs args) => await LoadCamsConfiguration();

    private void ApplyCamsState()
    {
        camsConfigured = BoolValue(camsConfiguration, "configured") == true;
        canSaveCams = BoolValue(camsConfiguration, "can_save") == true;
        CamConfigText.Text = CamConfigurationDescription();
        CamStatusIcon.Glyph = camsConfigured ? "\uE73E" : "\uE192";
        CamStatusIcon.Foreground = camsConfigured
            ? new SolidColorBrush(Colors.Green)
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        CamOverrideHint.Visibility = camsConfiguration != null && !canSaveCams
            ? Visibility.Visible : Visibility.Collapsed;
        CamMessageText.Text = camsMessage;
        CamMessageText.Foreground = camsSaveFailed
            ? new SolidColorBrush(Colors.Red)
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        SaveCamKeyButton.IsEnabled = online && !busy && !savingCams && canSaveCams
            && CamKeyBox.Password.Trim().Length > 0;
        CamRefreshButton.IsEnabled = online && !savingCams;
        UpdateRuntimeSummary();
    }

    private async void OnSaveCamsKey(object sender, RoutedEventArgs args)
    {
        if (!online || busy || savingCams || !canSaveCams) return;
        var key = CamKeyBox.Password.Trim();
        if (key.Length == 0) return;
        savingCams = true;
        camsMessage = "";
        CamSavingRing.IsActive = true;
        ApplyCamsState();
        try
        {
            // UX #8：草稿只在保存成功后清空；失败时保留，避免用户重新粘贴。
            camsConfiguration = await engine.Call("save_cams_key", new() { ["key"] = key });
            CamKeyBox.Password = "";
            if (key == camsKeyDraft) camsKeyDraft = "";
            // 保存 Key 后 CAMS 探测结果与时间戳过期。
            var cams = probeRows.FirstOrDefault(row => row.Source == "cams");
            if (cams != null)
            {
                cams.State = "waiting";
                cams.Detail = L10n.T("尚未探测");
                cams.Host = ""; cams.Bytes = 0; cams.ElapsedMs = 0;
            }
            probeDate = null;
            camsMessage = L10n.T("已保存到本机配置文件。");
            camsSaveFailed = false;
            RenderProbeRows();
        }
        catch (Exception error)
        {
            camsMessage = FriendlyMessages.Map(error.Message);
            camsSaveFailed = true;
        }
        finally
        {
            savingCams = false;
            CamSavingRing.IsActive = false;
            ApplyCamsState();
        }
    }

    // ---------- 数据：下载连通性（network_probe） ----------

    private void ResetProbeRows()
    {
        probeRows.Clear();
        probeRows.AddRange(ProbeRow.Initial());
        probeDate = null;
        ProbeErrorText.Text = "";
        ProbeErrorText.Visibility = Visibility.Collapsed;
        RenderProbeRows();
    }

    private async void OnProbeDownloads(object sender, RoutedEventArgs args)
    {
        if (!online || busy || savingCams || probingDownloads) return;
        probingDownloads = true;
        cancellingProbe = false;
        probeDate = null;
        ProbeErrorText.Text = "";
        ProbeErrorText.Visibility = Visibility.Collapsed;
        probeRows.Clear();
        probeRows.AddRange(ProbeRow.Initial());
        probeOperation = new CancellationTokenSource();
        var token = probeOperation.Token;
        RenderProbeRows();
        UpdateProbeControls();
        try
        {
            var response = await engine.Call("network_probe", new() { ["ecmwf_source"] = ecmwfSource },
                progress: e => DispatcherQueue.TryEnqueue(() =>
                {
                    if (cancellingProbe || e["probe"] is not JsonObject json) return;
                    if (ProbeRow.From(json) is not { } row) return;
                    var index = probeRows.FindIndex(candidate => candidate.Source == row.Source);
                    if (index >= 0) probeRows[index] = row;
                    RenderProbeRows();
                }), cancellation: token);
            token.ThrowIfCancellationRequested();
            var rows = (response["datasets"] as JsonArray ?? [])
                .OfType<JsonObject>().Select(ProbeRow.From).Where(row => row != null).ToList();
            if (rows.Count != 3 || rows.Select(row => row!.Source).ToHashSet()
                    .SetEquals(["gfs", "ec", "cams"]) is false)
                throw new InvalidOperationException(L10n.T("探测结果不完整，请重新探测。"));
            probeRows.Clear();
            probeRows.AddRange(rows!);
            probeDate = DateTimeOffset.Now;
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            MarkCancelledProbeRows(cancelled: true);
            probeDate = DateTimeOffset.Now;
            ProbeErrorText.Text = L10n.T("探测已取消；已完成的结果保留。");
            ProbeErrorText.Visibility = Visibility.Visible;
        }
        catch (Exception error)
        {
            var cancelled = token.IsCancellationRequested;
            MarkCancelledProbeRows(cancelled);
            probeDate = DateTimeOffset.Now;
            ProbeErrorText.Text = cancelled
                ? L10n.T("探测已取消；已完成的结果保留。")
                : FriendlyMessages.Map(error.Message);
            ProbeErrorText.Visibility = Visibility.Visible;
        }
        finally
        {
            probingDownloads = false;
            cancellingProbe = false;
            RenderProbeRows();
            UpdateProbeControls();
        }
    }

    private void MarkCancelledProbeRows(bool cancelled)
    {
        foreach (var row in probeRows.Where(row => row.Active || row.State == "waiting"))
        {
            row.State = cancelled ? "cancelled" : "error";
            row.Detail = cancelled ? L10n.T("已停止，尚未验证下载。") : L10n.T("探测中断，尚未验证下载。");
        }
    }

    private void OnCancelProbe(object sender, RoutedEventArgs args)
    {
        if (!probingDownloads) return;
        cancellingProbe = true;
        probeOperation?.Cancel();
    }

    private void UpdateProbeControls()
    {
        if (ProbeStartButton == null) return;
        ProbeStartButton.Visibility = probingDownloads ? Visibility.Collapsed : Visibility.Visible;
        ProbeStartButton.IsEnabled = online && !busy && !savingCams && !probingDownloads;
        ProbeCancelButton.Visibility = probingDownloads ? Visibility.Visible : Visibility.Collapsed;
        ProbeCancelButton.IsEnabled = probingDownloads && !cancellingProbe;
        ProbeCopyButton.Visibility = probeDate != null && !probingDownloads
            ? Visibility.Visible : Visibility.Collapsed;
        ProbeFooter.Text = probingDownloads
            ? (cancellingProbe ? L10n.T("正在停止探测") : L10n.T("正在验证真实数据下载 · CAMS 等待上限约 90 秒"))
            : probeDate != null
                ? $"{probeDate:HH:mm:ss} · {probeRows.Count(row => row.Passed)}/3 项下载验证通过"
                : L10n.T("尚无本次网络的下载验证结果");
    }

    private void RenderProbeRows()
    {
        ProbeRowsHost.Children.Clear();
        for (var index = 0; index < probeRows.Count; index++)
        {
            var row = probeRows[index];
            var panel = new StackPanel { Spacing = 4 };
            var line = new Grid { ColumnSpacing = 10 };
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            FrameworkElement icon;
            if (row.Active)
            {
                icon = new ProgressRing { Width = 16, Height = 16, IsActive = true };
            }
            else
            {
                icon = new TextBlock
                {
                    Text = row.Passed ? "✓" : row.State == "waiting" || row.State == "cancelled" ? "○" : "!",
                    Foreground = row.Passed ? new SolidColorBrush(Colors.Green)
                        : row.State is "waiting" or "cancelled"
                            ? (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"]
                            : new SolidColorBrush(Colors.Orange),
                    FontSize = 13,
                };
            }
            icon.VerticalAlignment = VerticalAlignment.Top;
            var titleLine = new StackPanel { Spacing = 3 };
            titleLine.Children.Add(new TextBlock
            {
                Text = row.Title, FontWeight = Microsoft.UI.Text.FontWeights.Medium, FontSize = 13,
            });
            if (row.Host.Length > 0)
                titleLine.Children.Add(new TextBlock { Text = row.Host, FontSize = 10, Opacity = .65 });
            titleLine.Children.Add(new TextBlock
            {
                Text = row.Detail, FontSize = 11, Opacity = .75, TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
            });
            if (row.Bytes > 0 || row.ElapsedMs > 0)
                titleLine.Children.Add(new TextBlock
                {
                    Text = row.Measurement, FontSize = 10, Opacity = .65,
                });
            var label = new TextBlock
            {
                Text = row.Label, FontSize = 11,
                Foreground = row.Passed ? new SolidColorBrush(Colors.Green)
                    : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
                VerticalAlignment = VerticalAlignment.Top,
            };
            Grid.SetColumn(icon, 0);
            Grid.SetColumn(titleLine, 1);
            Grid.SetColumn(label, 2);
            line.Children.Add(icon);
            line.Children.Add(titleLine);
            line.Children.Add(label);
            panel.Children.Add(line);
            if (index < probeRows.Count - 1)
                panel.Children.Add(new Border
                {
                    Height = 1, Margin = new Thickness(0, 2, 0, 0),
                    Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
                });
            ProbeRowsHost.Children.Add(panel);
        }
        UpdateProbeControls();
    }

    private void OnCopyProbeReport(object sender, RoutedEventArgs args)
    {
        var timestamp = probeDate?.ToString("yyyy-MM-dd HH:mm:ss") ?? L10n.T("未完成");
        var report = new List<string> { $"FireSky 数据源下载探测 · {timestamp}" };
        report.AddRange(probeRows.Select(row =>
            $"{row.Title} [{row.Host}] · {row.Label}\n{row.Detail}\n{row.Measurement}"));
        var package = new DataPackage { RequestedOperation = DataPackageOperation.Copy };
        package.SetText(string.Join("\n\n", report));
        Clipboard.SetContent(package);
        Status.Text = L10n.T("探测报告已复制");
    }

    // ---------- 数据：后台同步（data_sync_* 四方法） ----------

    private async Task LoadDataSyncSettings()
    {
        if (!online) return;
        try
        {
            dataSyncSettingsResponse = await engine.Call("data_sync_settings");
            ApplyDataSyncStatus(dataSyncSettingsResponse);
        }
        catch (Exception error)
        {
            dataSyncMessage = FriendlyMessages.Map(error.Message);
            dataSyncSaveFailed = true;
            RenderSyncMessage();
        }
    }

    private void ApplyDataSyncStatus(JsonObject response)
    {
        var settings = response["settings"] as JsonObject ?? new JsonObject();
        var sources = settings["sources"] as JsonObject ?? new JsonObject();
        dataSyncEnabled = BoolValue(settings, "enabled") == true;
        dataSyncCams = BoolValue(sources, "cams") == true;
        dataSyncGfs = BoolValue(sources, "gfs") == true;
        dataSyncEc = BoolValue(sources, "ec") == true;
        dataSyncInterval = Number(settings, "interval_minutes") is { } interval && double.IsFinite(interval)
            && interval > 0 ? ((long)interval).ToString(CultureInfo.InvariantCulture) : "";
        dataSyncLeadHorizon = Number(settings, "lead_horizon_hours") is { } lead && double.IsFinite(lead)
            && lead > 0 ? ((long)lead).ToString(CultureInfo.InvariantCulture) : "";
        dataSyncAnchor = Text(settings, "anchor_region");
        dataSyncConfigured = BoolValue(response, "configured") == true;
        dataSyncRetentionConfigured = BoolValue(response, "retention_configured") == true;
        dataSyncState = (response["state"] as JsonObject)?["sources"] as JsonObject ?? new JsonObject();
        dataSyncLoaded = true;
        ApplySyncControls();
    }

    private void ApplySyncControls()
    {
        suppressDraftEvents = true;
        try
        {
            SyncEnabledToggle.IsOn = dataSyncEnabled;
            SyncCamsToggle.IsChecked = dataSyncCams;
            SyncGfsToggle.IsChecked = dataSyncGfs;
            SyncEcToggle.IsChecked = dataSyncEc;
            SyncIntervalBox.Text = dataSyncInterval;
            SyncLeadBox.Text = dataSyncLeadHorizon;
            var anchorIndex = 0;
            foreach (var item in SyncAnchorPicker.Items.OfType<ComboBoxItem>())
            {
                if (item.Tag?.ToString() == dataSyncAnchor && dataSyncAnchor.Length > 0)
                {
                    anchorIndex = SyncAnchorPicker.Items.IndexOf(item);
                    break;
                }
            }
            SyncAnchorPicker.SelectedIndex = anchorIndex;
        }
        finally { suppressDraftEvents = false; }
        SyncRetentionWarning.IsOpen = dataSyncEnabled && !dataSyncRetentionConfigured;
        SyncCamsToggle.IsEnabled = dataSyncEnabled;
        SyncGfsToggle.IsEnabled = dataSyncEnabled;
        SyncEcToggle.IsEnabled = dataSyncEnabled;
        SyncIntervalBox.IsEnabled = dataSyncEnabled;
        SyncLeadBox.IsEnabled = dataSyncEnabled;
        SyncAnchorPicker.IsEnabled = dataSyncEnabled;
        RenderSyncMessage();
        RenderSyncStatusRows();
        UpdateSyncControls();
        UpdateRuntimeSummary();
    }

    private void OnSyncToggled(object sender, RoutedEventArgs args)
    {
        if (!initialized || suppressDraftEvents) return;
        dataSyncEnabled = SyncEnabledToggle.IsOn;
        SyncCamsToggle.IsEnabled = dataSyncEnabled;
        SyncGfsToggle.IsEnabled = dataSyncEnabled;
        SyncEcToggle.IsEnabled = dataSyncEnabled;
        SyncIntervalBox.IsEnabled = dataSyncEnabled;
        SyncLeadBox.IsEnabled = dataSyncEnabled;
        SyncAnchorPicker.IsEnabled = dataSyncEnabled;
        SyncRetentionWarning.IsOpen = dataSyncEnabled && !dataSyncRetentionConfigured;
        UpdateRuntimeSummary();
    }

    private void OnSyncSourceToggled(object sender, RoutedEventArgs args)
    {
        if (!initialized || suppressDraftEvents) return;
        dataSyncCams = SyncCamsToggle.IsChecked == true;
        dataSyncGfs = SyncGfsToggle.IsChecked == true;
        dataSyncEc = SyncEcToggle.IsChecked == true;
    }

    private void OnSyncAnchorChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized || suppressDraftEvents) return;
        dataSyncAnchor = TagOf(SyncAnchorPicker.SelectedItem) ?? "";
    }

    private static long? DataSyncInteger(string value)
    {
        var trimmed = value.Trim();
        if (trimmed.Length == 0) return null;
        return int.TryParse(trimmed, NumberStyles.Integer, CultureInfo.InvariantCulture, out var number)
            ? number : -1;
    }

    private async void OnSaveDataSync(object sender, RoutedEventArgs args)
    {
        if (!online || busy || savingSync) return;
        savingSync = true;
        dataSyncMessage = "";
        SyncSavingRing.IsActive = true;
        UpdateSyncControls();
        try
        {
            var payload = new JsonObject
            {
                ["settings"] = new JsonObject
                {
                    ["enabled"] = dataSyncEnabled,
                    ["sources"] = new JsonObject
                    {
                        ["cams"] = dataSyncCams, ["gfs"] = dataSyncGfs, ["ec"] = dataSyncEc,
                    },
                    ["interval_minutes"] = DataSyncInteger(SyncIntervalBox.Text),
                    ["anchor_region"] = dataSyncAnchor.Length == 0 ? null : JsonValue.Create(dataSyncAnchor),
                    ["lead_horizon_hours"] = DataSyncInteger(SyncLeadBox.Text),
                    // EC 下载源复用现有 ecmwfSource。
                    ["ecmwf_source"] = ecmwfSource,
                },
            };
            // 重存设置即解锁通道：引擎在保存时清零熔断计数与挂起标记。
            dataSyncSettingsResponse = await engine.Call("save_data_sync_settings", payload);
            ApplyDataSyncStatus(dataSyncSettingsResponse);
            dataSyncMessage = dataSyncEnabled
                ? L10n.T("后台同步已保存；引擎按新设置在心跳周期内执行。")
                : L10n.T("后台同步已关闭。");
            dataSyncSaveFailed = false;
        }
        catch (Exception error)
        {
            dataSyncMessage = FriendlyMessages.Map(error.Message);
            dataSyncSaveFailed = true;
        }
        finally
        {
            savingSync = false;
            SyncSavingRing.IsActive = false;
            RenderSyncMessage();
            UpdateSyncControls();
        }
    }

    private List<string> DataSyncEnabledSources() =>
        new List<(string, bool)> { ("cams", dataSyncCams), ("gfs", dataSyncGfs), ("ec", dataSyncEc) }
            .Where(item => item.Item2).Select(item => item.Item1).ToList();

    private async void OnRunDataSyncNow(object sender, RoutedEventArgs args)
    {
        if (!online || busy || probingDownloads || dataSyncRunning || savingSync) return;
        var sources = DataSyncEnabledSources();
        if (!dataSyncEnabled || sources.Count == 0)
        {
            dataSyncMessage = L10n.T("请先在设置中启用后台数据同步并至少选择一个数据源。");
            dataSyncSaveFailed = true;
            RenderSyncMessage();
            return;
        }
        Status.Text = L10n.T("正在立即同步后台数据");
        var round = await RunDataSyncRound(sources, force: true);
        if (round.Error != null)
        {
            // 轮级拒绝不进状态文件也不进任务历史，直接展示具体原因。
            dataSyncMessage = FriendlyMessages.Map(round.Error.Message);
            dataSyncSaveFailed = true;
            Status.Text = L10n.T("立即同步未完成");
        }
        else
        {
            var summary = round.Response != null ? DataSyncRoundSummary(round.Response) : L10n.T("立即同步未完成，详见任务历史。");
            Status.Text = summary;
            dataSyncMessage = summary;
            dataSyncSaveFailed = summary.Contains(L10n.T("失败")) || summary.Contains(L10n.T("未完成"));
        }
        RenderSyncMessage();
        RenderSyncStatusRows();
    }

    private string dataSyncActiveSource = "";

    private async Task<(JsonObject? Response, Exception? Error)> RunDataSyncRound(
        List<string> sources,
        bool force = false,
        bool autoTick = false)
    {
        dataSyncRunning = true;
        dataSyncActiveSource = "";
        dataSyncProgress.Clear();
        UpdateSyncControls();
        JsonObject? response = null;
        Exception? roundError = null;
        syncOperation = new CancellationTokenSource();
        try
        {
            var parameters = new JsonObject
            {
                ["sources"] = new JsonArray(
                    sources.Select(source => JsonValue.Create(source)).ToArray<JsonNode>()),
            };
            if (force) parameters["force"] = true;
            if (autoTick) parameters["auto_tick"] = true;
            response = await engine.Call("data_sync_run", parameters,
                progress: e => DispatcherQueue.TryEnqueue(() =>
                {
                    var source = Text(e, "source");
                    var overallPercent = DoubleValue(e["percent"]) is { } op ? Math.Clamp(op, 0, 100) : 0;
                    var sourcePercent = DoubleValue(e["source_percent"]) is { } sp
                        ? Math.Clamp(sp, 0, 100)
                        : overallPercent;
                    if (source.Length > 0)
                    {
                        dataSyncActiveSource = source;
                        dataSyncProgress[source] = new JsonObject
                        {
                            ["percent"] = overallPercent,
                            ["source_percent"] = sourcePercent,
                            ["message"] = Text(e, "message"),
                        };
                    }
                    if (!busy) Status.Text = Text(e, "message") is { Length: > 0 } message
                        ? message : $"正在同步 {source} · {sourcePercent:0}%";
                    RenderSyncStatusRows();
                }), cancellation: syncOperation.Token);
        }
        catch (Exception error) { roundError = error; }
        finally
        {
            dataSyncRunning = false;
            dataSyncActiveSource = "";
            dataSyncProgress.Clear();
        }
        // 仅刷新每源状态；不回填设置字段，避免覆盖正在编辑的内容。
        try
        {
            var refresh = await engine.Call("data_sync_settings");
            dataSyncRetentionConfigured = BoolValue(refresh, "retention_configured") == true
                || dataSyncRetentionConfigured;
            dataSyncState = (refresh["state"] as JsonObject)?["sources"] as JsonObject ?? dataSyncState;
        }
        catch (Exception) { /* 状态刷新失败保留旧状态 */ }
        RenderSyncStatusRows();
        UpdateSyncControls();
        return (response, roundError);
    }

    private string DataSyncRoundSummary(JsonObject response)
    {
        var labels = new Dictionary<string, string> { ["cams"] = "CAMS", ["gfs"] = "GFS", ["ec"] = "EC" };
        var parts = new List<string>();
        foreach (var name in new[] { "cams", "gfs", "ec" })
        {
            if ((response["sources"] as JsonObject)?[name] is not JsonObject row) continue;
            var status = Text(row, "status");
            var text = status switch
            {
                "synced" => L10n.T("已同步"),
                "current" => L10n.T("已是最新"),
                "failed" => L10n.T("失败"),
                "cancelled" => L10n.T("已取消"),
                "in_progress" => L10n.T("后台同步中"),
                _ => status.Length == 0 ? L10n.T("无结果") : status,
            };
            parts.Add($"{labels.GetValueOrDefault(name, name)} {text}");
        }
        return parts.Count == 0 ? L10n.T("立即同步结束，没有可同步的数据源。") : L10n.T("立即同步结束：") + string.Join("、", parts);
    }

    private void RenderSyncMessage()
    {
        SyncMessageText.Text = dataSyncMessage;
        SyncMessageText.Foreground = dataSyncSaveFailed
            ? new SolidColorBrush(Colors.Red)
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        if (dataSyncMessage.Length == 0 && dataSyncLoaded)
            SyncMessageText.Text = dataSyncEnabled ? L10n.T("后台同步已启用") : L10n.T("已配置，当前关闭");
        SyncMessageText.Opacity = dataSyncMessage.Length == 0 ? .65 : .9;
    }

    /// <summary>每源同步状态行（对齐 macOS dataSyncSourceRow）。</summary>
    private void RenderSyncStatusRows()
    {
        if (SyncStatusRows == null || !dataSyncLoaded) return;
        SyncStatusRows.Children.Clear();
        var running = dataSyncRunning;
        foreach (var (source, label) in new[] { ("cams", "CAMS"), ("gfs", "GFS"), ("ec", "EC") })
        {
            var state = dataSyncState[source] as JsonObject ?? new JsonObject();
            var result = state["last_result"] as JsonObject ?? new JsonObject();
            var status = Text(result, "status");
            var files = (long?)DoubleValue(result["files"]) ?? 0;
            var cycle = Text(state, "last_cycle");
            var failures = (long?)DoubleValue(state["consecutive_failures"]) ?? 0;
            var suspended = BoolValue(state, "suspended") == true;
            var inProgress = BoolValue(state, "in_progress") == true || status == "in_progress";
            var nextRetry = Text(state, "next_retry_at_utc");
            var live = running && dataSyncActiveSource == source ? dataSyncProgress.GetValueOrDefault(source) : null;
            var badge = inProgress
                ? L10n.T("后台同步中")
                : status switch
                {
                    "synced" => L10n.T("已同步"), "current" => L10n.T("已是最新"), "failed" => L10n.T("失败"),
                    "cancelled" => L10n.T("已取消"), "in_progress" => L10n.T("后台同步中"), _ => L10n.T("从未同步"),
                };
            var details = new List<string>();
            if (cycle.Length > 0) details.Add($"最近周期 {cycle}");
            if (files > 0) details.Add($"{files} 个文件");
            if (Text(state, "last_attempt_utc") is { Length: > 0 } attempt)
                details.Add($"上次同步 {HistoryTime.Text(attempt)}");
            if (!inProgress && live == null)
            {
                if ((result["errors"] as JsonArray)?.OfType<JsonValue>().FirstOrDefault() is { } firstError)
                {
                    var message = firstError.TryGetValue<string>(out var text) ? text : "";
                    if (message.Length > 0) details.Add($"最近错误：{message}");
                }
                if (failures > 0) details.Add($"连续失败 {failures} 次");
                if (suspended)
                {
                    details.Add(nextRetry.Length > 0
                        ? $"退避冷却中 · 预计 {HistoryTime.Text(nextRetry)} 自动重试（立即同步可马上重试）"
                        : L10n.T("退避冷却中（立即同步或重新保存设置可马上重试）"));
                }
            }
            var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
            row.Children.Add(new TextBlock
            {
                Text = label, FontWeight = Microsoft.UI.Text.FontWeights.Medium, FontSize = 12,
                MinWidth = 44,
            });
            var body = new StackPanel { Spacing = 3 };
            if (live != null)
            {
                var livePercent = Math.Clamp(DoubleValue(live["source_percent"] ?? live["percent"]) ?? 0, 0, 100);
                var progressLine = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
                var bar = new ProgressBar { Width = 90, Minimum = 0, Maximum = 100, Value = livePercent };
                progressLine.Children.Add(bar);
                progressLine.Children.Add(new TextBlock
                {
                    Text = $"{livePercent:0}%", FontSize = 11, Opacity = .7,
                });
                body.Children.Add(progressLine);
                if (Text(live, "message") is { Length: > 0 } message)
                    body.Children.Add(new TextBlock
                    {
                        Text = message, FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap,
                    });
            }
            else
            {
                body.Children.Add(new TextBlock
                {
                    Text = badge, FontSize = 12,
                    Foreground = inProgress ? ThemeBrush("FireSkyCoolAccentBrush")
                        : status is "synced" or "current" ? new SolidColorBrush(Colors.Green)
                        : status == "failed" ? new SolidColorBrush(Colors.Red)
                        : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
                });
            }
            if (details.Count > 0)
                body.Children.Add(new TextBlock
                {
                    Text = string.Join(" · ", details), FontSize = 11, Opacity = .65,
                    TextWrapping = TextWrapping.Wrap,
                });
            row.Children.Add(body);
            SyncStatusRows.Children.Add(row);
        }
    }

    private void UpdateSyncControls()
    {
        if (SyncSaveButton == null) return;
        SyncSaveButton.IsEnabled = online && !savingSync && !busy;
        SyncRunNowButton.IsEnabled = online && !busy && !probingDownloads
            && !dataSyncRunning && !savingSync;
        SyncRunNowButton.Opacity = dataSyncEnabled ? 1 : .6;
        ToolTipService.SetToolTip(SyncRunNowButton, dataSyncEnabled
            ? L10n.T("立即同步已启用的数据源（可解除失败熔断）") : L10n.T("先启用并保存后台同步"));
    }

    /// <summary>60 秒心跳：due 时静默拉起一轮同步（tick 失败不写界面文案）。</summary>
    private void StartSyncTick()
    {
        syncTickTimer ??= DispatcherQueue.CreateTimer();
        syncTickTimer.IsRepeating = true;
        syncTickTimer.Interval = TimeSpan.FromSeconds(60);
        syncTickTimer.Tick += (_, _) => _ = SyncTick();
        syncTickTimer.Start();
    }

    private async Task SyncTick()
    {
        try
        {
            if (!online || busy || probingDownloads || dataSyncRunning
                || !dataSyncLoaded || !dataSyncEnabled) return;
            var due = await engine.Call("data_sync_due");
            if (BoolValue(due, "due_for_any") != true) return;
            if (due["due_sources"] is not JsonArray sources) return;
            var names = sources.Select(node => node?.GetValue<string>() ?? "")
                .Where(name => name.Length > 0).ToList();
            if (names.Count == 0) return;
            await RunDataSyncRound(names, autoTick: true);
            Status.Text = online ? L10n.T("本地引擎已连接") : Status.Text;
        }
        catch (Exception)
        {
            // tick 保持静默：失败留在每源状态与任务历史里，下个心跳重新判定。
        }
    }

    // ---------- 数据：存储与保留 ----------

    private bool applyingRetention;

    private void UpdateRetentionControls()
    {
        if (SaveRetentionButton == null || ApplyRetentionButton == null) return;
        var active = savingRetention || applyingRetention;
        RetentionRing.IsActive = active;
        SaveRetentionButton.IsEnabled = online && !busy && !active;
        ApplyRetentionButton.IsEnabled = online && dataSyncRetentionConfigured && !busy && !active;
        ToolTipService.SetToolTip(ApplyRetentionButton, dataSyncRetentionConfigured
            ? L10n.T("立即按已保存规则清理超出保留范围的旧文件（跳过占用中文件）")
            : L10n.T("请先填写并保存至少一项保留规则"));
        UpdateRuntimeSummary();
    }

    private async Task LoadRetentionSettings()
    {
        if (!online) return;
        try
        {
            var response = await engine.Call("retention_settings");
            var settings = response["settings"] as JsonObject ?? new JsonObject();
            RetentionGfs.Text = RetentionText(settings["gfs_cycles"]);
            RetentionEcmwf.Text = RetentionText(settings["ecmwf_cycles"]);
            RetentionCams.Text = RetentionText(settings["cams_cycles"]);
            RetentionSatelliteDays.Text = RetentionText(settings["satellite_days"]);
            RetentionReportDays.Text = RetentionText(settings["report_days"]);
            RetentionMapDays.Text = RetentionText(settings["map_days"]);
            var configured = BoolValue(response, "configured") == true;
            RetentionStatus.Text = configured ? L10n.T("已启用自定义保留策略。") : L10n.T("未启用自定义保留策略。");
            dataSyncRetentionConfigured = configured;
            SyncRetentionWarning.IsOpen = dataSyncEnabled && !configured;
            UpdateRetentionControls();
        }
        catch (Exception error)
        {
            RetentionStatus.Text = FriendlyMessages.Map(error.Message);
        }
    }

    private async void OnSaveRetention(object sender, RoutedEventArgs args)
    {
        if (!online || busy || savingRetention || applyingRetention) return;
        savingRetention = true;
        UpdateRetentionControls();
        try
        {
            var settings = new JsonObject
            {
                ["gfs_cycles"] = RetentionInteger(RetentionGfs.Text, L10n.T("GFS 周期")),
                ["ecmwf_cycles"] = RetentionInteger(RetentionEcmwf.Text, L10n.T("EC 周期")),
                ["cams_cycles"] = RetentionInteger(RetentionCams.Text, L10n.T("CAMS 周期")),
                ["satellite_days"] = RetentionNumber(RetentionSatelliteDays.Text, L10n.T("卫星数据")),
                ["report_days"] = RetentionNumber(RetentionReportDays.Text, L10n.T("历史报告")),
                ["map_days"] = RetentionNumber(RetentionMapDays.Text, L10n.T("报告地图")),
            };
            var response = await engine.Call("save_retention_settings",
                new JsonObject { ["settings"] = settings });
            var saved = response["settings"] as JsonObject ?? new JsonObject();
            RetentionGfs.Text = RetentionText(saved["gfs_cycles"]);
            RetentionEcmwf.Text = RetentionText(saved["ecmwf_cycles"]);
            RetentionCams.Text = RetentionText(saved["cams_cycles"]);
            RetentionSatelliteDays.Text = RetentionText(saved["satellite_days"]);
            RetentionReportDays.Text = RetentionText(saved["report_days"]);
            RetentionMapDays.Text = RetentionText(saved["map_days"]);
            var configured = BoolValue(response, "configured") == true;
            RetentionStatus.Text = configured
                ? L10n.T("保留设置已保存；可点击「立即按策略清理」，或等待下一次成功准备后自动清理。")
                : L10n.T("已清空保留设置。");
            ClearError("settings");
            // 同步页的「未配置保留」软警告依赖 retention_configured，一并刷新。
            dataSyncRetentionConfigured = configured;
            SyncRetentionWarning.IsOpen = dataSyncEnabled && !configured;
        }
        catch (Exception error)
        {
            RetentionStatus.Text = FriendlyMessages.Map(error.Message);
            Fail(error, "settings");
        }
        finally
        {
            savingRetention = false;
            UpdateRetentionControls();
        }
    }

    private async void OnApplyRetentionNow(object sender, RoutedEventArgs args)
    {
        if (!online || busy || savingRetention || applyingRetention) return;
        applyingRetention = true;
        RetentionStatus.Text = L10n.T("正在按保留策略清理旧数据…");
        UpdateRetentionControls();
        try
        {
            var response = await engine.Call("apply_retention_settings");
            var configured = BoolValue(response, "configured") == true;
            if (!configured || response["result"] is not JsonObject result)
            {
                RetentionStatus.Text = L10n.T("尚未保存任何保留规则，未执行清理。");
                return;
            }
            var removed = (long?)DoubleValue(result["removed_count"]) ?? 0;
            var freedMb = DoubleValue(result["freed_mb"]) ?? 0;
            var deferred = (long?)DoubleValue(result["deferred_count"]) ?? 0;
            var failed = (long?)DoubleValue(result["failed_count"]) ?? 0;
            var parts = new List<string>
            {
                removed > 0
                    ? (L10n.IsEnglish
                        ? $"Cleaned {removed} old items, freed {freedMb:0.0} MB"
                        : $"已清理 {removed} 项旧数据，释放 {freedMb:0.0} MB")
                    : L10n.T("当前没有超出保留策略的旧数据")
            };
            if (deferred > 0)
                parts.Add(L10n.IsEnglish ? $"skipped {deferred} busy items" : $"跳过 {deferred} 项占用中文件");
            if (failed > 0)
                parts.Add(L10n.IsEnglish ? $"{failed} failed" : $"{failed} 项清理失败");
            RetentionStatus.Text = string.Join(" · ", parts) + (L10n.IsEnglish ? "." : "。");
            ClearError("settings");
            await LoadInventory();
        }
        catch (Exception error)
        {
            RetentionStatus.Text = FriendlyMessages.Map(error.Message);
            Fail(error, "settings");
        }
        finally
        {
            applyingRetention = false;
            UpdateRetentionControls();
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
}
