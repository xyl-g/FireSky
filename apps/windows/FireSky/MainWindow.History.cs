using Microsoft.UI;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace FireSky;

/// <summary>
/// 任务历史独立页（v1.2.0）：列表 + 详情双栏、类型筛选、行内动作、事件时间线。
/// 行为对齐 macOS NativeTaskHistoryView 与 Store 任务历史合同。
/// </summary>
public sealed partial class MainWindow
{
    private async void OnRefreshTaskHistory(object sender, RoutedEventArgs args) =>
        await LoadTaskHistory();

    private async void OnLoadOlderTaskHistory(object sender, RoutedEventArgs args) =>
        await LoadTaskHistory(older: true);

    private async Task LoadTaskHistory(bool older = false)
    {
        if (!online || taskHistoryLoading) return;
        if (older && (!taskHistoryHasMore || taskHistoryCursor == null)) return;
        taskHistoryLoading = true;
        taskHistoryError = "";
        RenderTaskHistory();
        try
        {
            var parameters = new JsonObject { ["limit"] = 100 };
            if (older) parameters["before"] = taskHistoryCursor;
            var response = await engine.Call("task_history", parameters);
            if (StringOf(response["schema"]) != "firesky-task-history-v1"
                || BoolValue(response, "automatic_restart") != false
                || response["tasks"] is not JsonArray values
                || values.Any(node => node is not JsonObject)
                || Integer(response, "total") is not { } total
                || total < values.Count || total > int.MaxValue || values.Count > 100)
                throw new InvalidOperationException(L10n.T("任务历史协议不匹配，请重新连接引擎。"));
            var hasMore = BoolValue(response, "has_more")
                ?? throw new InvalidOperationException(L10n.T("任务历史分页状态无效，请重新连接引擎。"));
            var nextCursor = StringOrNull(response["next_cursor"]);
            if (hasMore != (nextCursor != null)
                || nextCursor is { } cursor && !IsTaskTimestamp(cursor))
                throw new InvalidOperationException(L10n.T("任务历史分页游标无效，请重新连接引擎。"));
            var parsed = new List<TaskHistoryItem>();
            foreach (var node in values.OfType<JsonObject>())
            {
                if (TaskHistoryItem.Parse(node) is not { } item)
                    throw new InvalidOperationException(L10n.T("任务历史包含无效记录，请检查引擎版本。"));
                parsed.Add(item);
            }
            if (parsed.Select(row => row.TaskId).Distinct().Count() != parsed.Count)
                throw new InvalidOperationException(L10n.T("任务历史包含重复记录，请重新连接引擎。"));
            var combined = older ? taskHistory.Concat(parsed).ToList() : parsed;
            if (combined.Select(row => row.TaskId).Distinct().Count() != combined.Count
                || combined.Count > total)
                throw new InvalidOperationException(L10n.T("任务历史分页包含重复或超出总数的记录。"));
            taskHistory.Clear();
            taskHistory.AddRange(combined);
            taskHistoryTotal = (int)total;
            taskHistoryIssues = (response["issues"] as JsonArray)?.Count ?? 0;
            taskHistoryHasMore = hasMore;
            taskHistoryCursor = nextCursor;
            taskHistoryLoaded = true;
            if (selectedTaskHistoryId.Length > 0
                && !combined.Any(row => row.TaskId == selectedTaskHistoryId))
                ClearTaskHistorySelection();
            ClearError("history");
        }
        catch (Exception error)
        {
            taskHistoryError = FriendlyMessages.Map(error.Message);
            Fail(error, "history");
        }
        finally
        {
            taskHistoryLoading = false;
            RenderTaskHistory();
        }
    }

    private IEnumerable<TaskHistoryItem> VisibleHistory() => historyKindFilter switch
    {
        "active" => taskHistory.Where(row => !row.IsBackgroundSync),
        "sync" => taskHistory.Where(row => row.IsBackgroundSync),
        // 全部：后台同步轮次默认折叠（UX #13），避免淹没主动任务。
        _ => taskHistory.Where(row => !row.IsBackgroundSync),
    };

    private void OnHistoryFilterChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        RenderTaskHistoryRows();
    }

    private void SelectHistoryFilter(string filter)
    {
        historyKindFilter = filter is "active" or "sync" ? filter : "all";
        UpdateSegmentedPills(HistoryFilterPills, historyKindFilter);
        RenderTaskHistory();
    }

    private void RenderTaskHistory()
    {
        HistoryCaption.Text = taskHistoryLoaded
            ? $"最近 {taskHistory.Count} 项 · 共 {taskHistoryTotal} 项"
            : L10n.T("记录数据检查、准备、预测和下载探测任务");
        HistoryIssues.Text = taskHistoryIssues > 0 ? $"{taskHistoryIssues} 项无法核验" : "";
        HistoryIssues.Visibility = taskHistoryIssues > 0 ? Visibility.Visible : Visibility.Collapsed;
        HistoryLoadingPanel.Visibility = taskHistoryLoading && taskHistory.Count == 0
            ? Visibility.Visible : Visibility.Collapsed;
        HistoryEmpty.Visibility = !taskHistoryLoading && !VisibleHistory().Any()
            ? Visibility.Visible : Visibility.Collapsed;
        HistoryEmptyText.Text = !taskHistoryLoaded ? (online
            ? L10n.T("完成一次检查或预测后会显示在这里。") : L10n.T("引擎未连接。"))
            : taskHistory.Count > 0 ? L10n.T("换个筛选条件试试。") : "";
        RenderTaskHistoryRows();
        UpdateTaskHistoryControls();
    }

    private void RenderTaskHistoryRows()
    {
        HistoryListHost.Children.Clear();
        taskHistoryRows.Clear();
        foreach (var item in VisibleHistory())
        {
            HistoryListHost.Children.Add(BuildTaskHistoryRow(item));
            HistoryListHost.Children.Add(new Border
            {
                BorderThickness = new Thickness(0, 0, 0, 1),
                BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
                Margin = new Thickness(52, 0, 0, 0),
            });
        }
        if (!online && taskHistoryError.Length == 0 && taskHistory.Count > 0)
            HistoryListHost.Children.Add(new TextBlock
            {
                Text = L10n.T("引擎未连接，显示的是上次读取的记录。"),
                FontSize = 11, Opacity = .65, Margin = new Thickness(0, 10, 0, 0),
            });
    }

    private FrameworkElement BuildTaskHistoryRow(TaskHistoryItem item)
    {
        var info = new StackPanel { Spacing = 5 };
        var titleLine = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7 };
        titleLine.Children.Add(new TextBlock
        {
            Text = item.RowTitle,
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
        // UX #4：本地时区展示，原始 UTC 放进 ToolTip。
        var created = new TextBlock
        {
            Text = HistoryTime.Text(item.CreatedAtUtc), FontSize = 10, Opacity = .6,
            VerticalAlignment = VerticalAlignment.Center,
        };
        ToolTipService.SetToolTip(created, $"UTC：{item.CreatedAtUtc}");
        metaLine.Children.Add(created);
        if (item.Status == "running" && DoubleValue(item.Progress["percent"]) is { } percent)
            metaLine.Children.Add(new TextBlock
            {
                Text = $"{percent:0}%", FontSize = 10, Opacity = .6,
                VerticalAlignment = VerticalAlignment.Center,
            });
        if (item.Status == "interrupted")
            metaLine.Children.Add(new TextBlock
            {
                Text = L10n.T("不会自动重跑"), FontSize = 10, Opacity = .6,
                VerticalAlignment = VerticalAlignment.Center,
            });
        info.Children.Add(metaLine);

        var actions = new StackPanel
        {
            Orientation = Orientation.Horizontal, Spacing = 8,
            VerticalAlignment = VerticalAlignment.Top,
        };
        var restoreResult = new Button { Content = L10n.T("恢复结果"), Padding = new Thickness(8, 3, 8, 3) };
        ToolTipService.SetToolTip(restoreResult, L10n.T("恢复已发布结果"));
        restoreResult.Visibility = item.CanRestoreResult ? Visibility.Visible : Visibility.Collapsed;
        restoreResult.Click += (_, _) => _ = RestoreTaskResult(item);
        actions.Children.Add(restoreResult);
        var restoreParameters = new Button { Content = L10n.T("恢复参数并重新检查"), Padding = new Thickness(8, 3, 8, 3) };
        ToolTipService.SetToolTip(restoreParameters, L10n.T("恢复参数并重新检查数据"));
        restoreParameters.Visibility = item.RetryPayload != null
            ? Visibility.Visible : Visibility.Collapsed;
        restoreParameters.Click += (_, _) => RestoreTaskParameters(item);
        actions.Children.Add(restoreParameters);
        var forget = new Button { Content = L10n.T("删除记录"), Padding = new Thickness(8, 3, 8, 3) };
        ToolTipService.SetToolTip(forget, L10n.T("删除任务记录"));
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

        // 点击整行切换详情；行内操作按钮自行处理指针事件，不会触发外层点击。
        var open = new Button
        {
            Content = row,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            Padding = new Thickness(12, 12, 12, 12),
            BorderThickness = new Thickness(0),
            Background = new SolidColorBrush(Colors.Transparent),
        };
        if (selectedTaskHistoryId == item.TaskId)
            open.Background = (Brush)Application.Current.Resources["SubtleFillColorSecondaryBrush"];
        open.Click += (_, _) => OnSelectTaskHistory(item.TaskId);
        taskHistoryRows.Add(new TaskHistoryRow
        {
            Item = item, RestoreResult = restoreResult,
            RestoreParameters = restoreParameters, Forget = forget,
        });
        return open;
    }

    private static Brush StatusBrush(string status) => status switch
    {
        "done" => new SolidColorBrush(Colors.Green),
        "failed" => new SolidColorBrush(Colors.Red),
        "interrupted" => new SolidColorBrush(Colors.Orange),
        _ => new SolidColorBrush(Colors.Gray),
    };

    private static string StatusGlyph(string status) => status switch
    {
        "done" => "✓", "failed" => "!", "cancelled" => "×", "interrupted" => "‖", _ => "○",
    };

    private void UpdateTaskHistoryControls()
    {
        if (HistoryRefresh == null) return;
        HistoryRefresh.IsEnabled = online && !taskHistoryLoading;
        HistoryLoadOlder.Visibility = taskHistoryHasMore && taskHistory.Count > 0
            ? Visibility.Visible : Visibility.Collapsed;
        HistoryLoadOlder.IsEnabled = online && !taskHistoryLoading && taskHistoryHasMore;
        HistoryLoadOlderRing.IsActive = taskHistoryLoading && taskHistory.Count > 0;
        HistoryLoadOlderText.Text = taskHistoryLoading
            ? L10n.T("正在加载…") : L10n.T("加载更早任务");
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
        RenderTaskHistoryRows();
        _ = LoadTaskEvents(taskId, token);
    }

    private void OnCloseHistoryDetail(object sender, RoutedEventArgs args) => ClearTaskHistorySelection();

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
        RenderTaskHistoryRows();
    }

    private async Task LoadTaskEvents(string taskId, CancellationToken token)
    {
        if (isUiAuditMock)
        {
            taskHistoryEvents.Clear();
            taskHistoryEvents.Add(new TaskEvent
            {
                Sequence = 1, AtUtc = "2026-11-09T08:55:01Z", Event = "preflight_checked",
                Data = new JsonObject { ["status"] = "ready" },
            });
            taskHistoryEvents.Add(new TaskEvent
            {
                Sequence = 2, AtUtc = "2026-11-09T09:00:00Z", Event = "forecast_done",
                Data = new JsonObject { ["models"] = "gfs+ec" },
            });
            taskHistoryEventsTruncated = false;
            taskHistoryEventsError = "";
            taskHistoryEventsLoading = false;
            RenderTaskEvents();
            return;
        }
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
                throw new InvalidOperationException(L10n.T("任务事件协议不匹配。"));
            var parsed = new List<TaskEvent>();
            var previous = 0L;
            foreach (var node in values.OfType<JsonObject>())
            {
                if (Integer(node, "seq") is not { } sequence
                    || sequence <= previous || sequence > next
                    || StringOrNull(node["at_utc"]) is not { } atUtc || !IsTaskTimestamp(atUtc)
                    || StringOrNull(node["event"]) is not { } title)
                    throw new InvalidOperationException(L10n.T("任务事件顺序或时间无效。"));
                previous = sequence;
                parsed.Add(new TaskEvent
                {
                    Sequence = sequence, AtUtc = atUtc, Event = title,
                    Data = node["data"] as JsonObject ?? new JsonObject(),
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
            taskHistoryEventsError = FriendlyMessages.Map(error.Message);
            RenderTaskEvents();
        }
    }

    private void RenderTaskEvents()
    {
        var item = taskHistory.FirstOrDefault(row => row.TaskId == selectedTaskHistoryId);
        HistoryDetail.Visibility = item == null ? Visibility.Collapsed : Visibility.Visible;
        if (item == null) return;
        HistoryDetailTitle.Text = item.RowTitle;
        HistoryDetailContext.Text = item.ContextTitle;
        RenderTaskSummary(item);
        HistoryEventsTruncated.Visibility = taskHistoryEventsTruncated
            ? Visibility.Visible : Visibility.Collapsed;
        HistoryEventsPanel.Children.Clear();
        if (taskHistoryEventsLoading)
        {
            HistoryEventsPanel.Children.Add(new ProgressRing { Width = 16, Height = 16, IsActive = true });
            return;
        }
        if (taskHistoryEventsError.Length > 0)
        {
            HistoryEventsPanel.Children.Add(new TextBlock
            {
                Text = taskHistoryEventsError, FontSize = 11,
                Foreground = new SolidColorBrush(Colors.Red),
                TextWrapping = TextWrapping.Wrap,
            });
            return;
        }
        if (taskHistoryEvents.Count == 0)
        {
            HistoryEventsPanel.Children.Add(new TextBlock
            {
                Text = L10n.T("没有已记录的事件。"), FontSize = 11, Opacity = .65,
            });
            return;
        }
        foreach (var entry in taskHistoryEvents)
        {
            var line = new Grid { ColumnSpacing = 9, Padding = new Thickness(0, 3, 0, 3) };
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var time = new TextBlock
            {
                Text = HistoryTime.Text(entry.AtUtc), FontSize = 10, Opacity = .65,
                VerticalAlignment = VerticalAlignment.Center,
            };
            ToolTipService.SetToolTip(time, $"UTC：{entry.AtUtc}");
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
            line.Children.Add(time);
            line.Children.Add(title);
            line.Children.Add(detail);
            HistoryEventsPanel.Children.Add(line);
        }
    }

    /// <summary>详情摘要卡：状态、原始 UTC、进度、恢复说明；动作按钮集中在列表行。</summary>
    private void RenderTaskSummary(TaskHistoryItem item)
    {
        HistorySummaryPanel.Children.Clear();
        void Row(string title, string value, string? help = null)
        {
            var line = new Grid { ColumnSpacing = 8 };
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(72) });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var label = new TextBlock { Text = title, FontSize = 11, Opacity = .65 };
            var text = new TextBlock
            {
                Text = value, FontSize = 11, IsTextSelectionEnabled = true,
                TextWrapping = TextWrapping.Wrap,
            };
            if (help != null) ToolTipService.SetToolTip(text, help);
            Grid.SetColumn(text, 1);
            line.Children.Add(label);
            line.Children.Add(text);
            HistorySummaryPanel.Children.Add(line);
        }
        Row(L10n.T("状态"), item.StatusTitle + (item.Status == "interrupted" ? L10n.T("（不会自动重跑）") : ""));
        Row(L10n.T("创建时间"), HistoryTime.Text(item.CreatedAtUtc), $"UTC：{item.CreatedAtUtc}");
        Row(L10n.T("更新时间"), HistoryTime.Text(item.UpdatedAtUtc), $"UTC：{item.UpdatedAtUtc}");
        if (item.Status == "running" && DoubleValue(item.Progress["percent"]) is { } percent)
            Row(L10n.T("进度"), $"{percent:0}%");
        if (item.CancelRequested) Row(L10n.T("取消"), L10n.T("已请求取消，等待任务停止"));
        if (item.CanRestoreResult) Row(L10n.T("恢复"), L10n.T("可从已发布快照恢复本任务结果"));
        if (item.RetryPayload != null) Row(L10n.T("重试"), L10n.T("可恢复参数并重新检查数据"));
        Row(L10n.T("删除记录"), L10n.T("只删除状态和事件，不影响报告与天气数据"));
    }

    private async Task ConfirmForgetTask(string taskId)
    {
        if (!online || busy || forgettingTaskHistoryId.Length > 0) return;
        var item = taskHistory.FirstOrDefault(row => row.TaskId == taskId);
        if (item == null || item.Status == "running") return;
        var dialog = new ContentDialog
        {
            Title = L10n.T("删除任务记录？"),
            Content = new TextBlock
            {
                Text = L10n.T("只删除状态和事件，不删除已发布报告或天气数据。释放磁盘空间请到「数据管理」操作。"),
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = L10n.T("删除记录"),
            CloseButtonText = L10n.T("保留记录"),
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
                throw new InvalidOperationException(L10n.T("任务记录没有被删除。"));
            if (selectedTaskHistoryId == taskId) ClearTaskHistorySelection();
            _ = LoadTaskHistory();
        }
        catch (Exception error)
        {
            taskHistoryError = FriendlyMessages.Map(error.Message);
            Fail(error, "history");
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
                throw new InvalidOperationException(L10n.T("已发布结果格式无效。"));
            // UX #1：恢复结果时先回填结果的原始参数，标题/表单与结果本体不再错位。
            if (item.RetryPayload != null) ApplyRetryPayload(item.RetryPayload, kind);
            AdoptResult(response, kind, item.RetryPayload);
            SelectNavigation(kind);
            if (kind != "nowcast")
            {
                var model = StringOrNull(item.RetryPayload?["model_source"]);
                if (model is not ("gfs" or "ec"))
                    model = (response["model_results"] as JsonObject)?["gfs"] is null ? "ec" : "gfs";
                SetSelectedResultModel(model, render: false, updateMap: false);
                if (kind == "regional" && RegionalModel != null)
                    RegionalModel.SelectedIndex = model == "ec" ? 1 : 0;
            }
            RenderResult();
            await UpdateMap();
            Status.Text = L10n.T("已恢复历史结果");
            ClearError();
        }
        catch (Exception error)
        {
            taskHistoryError = FriendlyMessages.Map(error.Message);
            Fail(error, "history");
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
        if (busy || searching || locating || restoringTaskHistoryId.Length > 0
            || item.Status == "running" || item.RetryPayload == null) return;
        restoringTaskHistoryId = item.TaskId;
        ApplyRetryPayload(item.RetryPayload, item.RetryKind);
        SelectNavigation(item.RetryKind);
        restoringTaskHistoryId = "";
        OnPredict(this, new RoutedEventArgs());
    }

    private void ApplyRetryPayload(JsonObject payload, string kind)
    {
        if (StringOrNull(payload["name"]) is { } name) LocationName.Text = name;
        if (DoubleValue(payload["lat"]) is { } lat) Latitude.Value = lat;
        if (DoubleValue(payload["lon"]) is { } lon) Longitude.Value = lon;
        if (DoubleValue(payload["elevation_m"]) is { } elevation) Elevation.Value = elevation;
        if (StringOrNull(payload["timezone_name"]) is { } timezone) Timezone.Text = timezone;
        if (StringOrNull(payload["target_date"]) is { } date
            && DateTimeOffset.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var target))
        {
            suppressDraftEvents = true;
            try { TargetDate.Date = target; }
            finally { suppressDraftEvents = false; }
            SyncDateTab();
        }
        if (StringOrNull(payload["phenomenon"]) is { } restoredPhenomenon
            && restoredPhenomenon is "dawn" or "dusk")
            SetPhenomenon(restoredPhenomenon);
        if (StringOrNull(payload["ecmwf_source"]) is { } source && source is "ecmwf" or "aws" or "google")
        {
            ecmwfSource = source;
            EcmwfSourcePicker.SelectedIndex = source switch { "aws" => 1, "google" => 2, _ => 0 };
        }
        if (StringOrNull(payload["aerosol_source"]) is { } aerosol && aerosol is "cams" or "geos-fp")
        {
            aerosolSource = aerosol;
            AerosolSourcePicker.SelectedIndex = aerosol == "geos-fp" ? 1 : 0;
        }
        if (kind == "regional")
        {
            selectedRegions.Clear();
            foreach (var region in payload["regions"] as JsonArray ?? [])
                if (region?.GetValue<string>() is { Length: > 0 } code && code != "custom")
                    selectedRegions.Add(code);
            customRegion = (payload["regions"] as JsonArray)?.Any(node =>
                node?.GetValue<string>() == "custom") == true;
            CustomRegionToggle.IsChecked = customRegion;
            RenderRegionPresetsChecks();
            if (payload["custom_bounds"] is JsonObject bounds
                && DoubleValue(bounds["west"]) is { } west && DoubleValue(bounds["east"]) is { } east
                && DoubleValue(bounds["south"]) is { } south && DoubleValue(bounds["north"]) is { } north)
            {
                suppressDraftEvents = true;
                try
                {
                    West.Value = west; East.Value = east; South.Value = south; North.Value = north;
                }
                finally { suppressDraftEvents = false; }
            }
        }
        MetadataReady.IsChecked = true;
    }

    private void RenderRegionPresetsChecks()
    {
        foreach (var child in RegionPresetsHost.Children.OfType<CheckBox>())
        {
            var code = Text(regionDefinitionFor(child), "code");
            if (code.Length > 0) child.IsChecked = selectedRegions.Contains(code);
        }
    }

    private JsonObject? regionDefinitionFor(CheckBox toggle) =>
        regionDefinitions.FirstOrDefault(region => Text(region, "label") == (toggle.Content as string)
            || Text(region, "code") == (toggle.Content as string));

    private void SelectNavigation(string kind)
    {
        NavigateTo(kind switch
        {
            "regional" => "regional",
            "nowcast" => "nowcast",
            _ => "point",
        });
    }

    // 校验规则与 Swift NativeTaskHistoryItem 一致：任一字段无效即整条记录无效。
    internal sealed class TaskHistoryItem
    {
        internal required string TaskId { get; init; }
        internal required string Method { get; init; }
        internal required string Status { get; init; }
        internal required string CreatedAtUtc { get; init; }
        internal required string UpdatedAtUtc { get; init; }
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
                CreatedAtUtc = createdAt, UpdatedAtUtc = updatedAt,
                CancelRequested = BoolValue(value, "cancel_requested") == true,
                EventSeq = eventSeq, RetryKind = retryKind, RetryPayload = retryPayload,
                ResultRef = value["result_ref"] as JsonObject,
                Progress = value["progress"] as JsonObject ?? new JsonObject(),
            };
        }

        internal string KindTitle => RetryKind switch
        {
            "point" => L10n.T("单点"), "regional" => L10n.T("区域"), "nowcast" => L10n.T("实况"), _ => MethodTitle,
        };

        internal string MethodTitle => Method switch
        {
            "check" => L10n.T("数据检查"), "prepare" => L10n.T("数据准备"), "forecast" => L10n.T("预测计算"),
            "publications" => L10n.T("发布核验"), "network_probe" => L10n.T("下载探测"),
            "data_sync" => L10n.T("后台同步"), _ => L10n.T("任务"),
        };

        /// <summary>UX #13：行标题去重（无重试类别时 kindTitle 与 methodTitle 相同）。</summary>
        internal string RowTitle => KindTitle == MethodTitle ? MethodTitle : $"{KindTitle} · {MethodTitle}";

        /// <summary>UX #13：后台同步轮次默认折叠。</summary>
        internal bool IsBackgroundSync => Method == "data_sync";

        internal string StatusTitle => Status switch
        {
            "running" => CancelRequested ? L10n.T("取消中") : L10n.T("运行中"),
            "done" => L10n.T("已完成"), "failed" => L10n.T("失败"),
            "cancelled" => L10n.T("已取消"), "interrupted" => L10n.T("已中断"), _ => Status,
        };

        internal string ContextTitle
        {
            get
            {
                if (RetryPayload == null) return IsBackgroundSync ? L10n.T("后台数据同步轮次") : "";
                var name = StringOrNull(RetryPayload["name"]);
                if (name == null && RetryPayload["regions"] is JsonArray regions
                    && regions.All(node => node is JsonValue value
                        && value.GetValueKind() == JsonValueKind.String))
                    name = string.Join("、", regions.Select(node => StringOf(node))
                        .Where(text => text.Length > 0));
                var date = StringOf(RetryPayload["target_date"]);
                var phenomenon = StringOf(RetryPayload["phenomenon"]) switch
                {
                    "dawn" => L10n.T("朝霞"), "dusk" => L10n.T("晚霞"), _ => "",
                };
                var text = string.Join(" · ", new[] { name ?? "", date, phenomenon }
                    .Where(part => part.Length > 0));
                if (text.Length == 0 && IsBackgroundSync) return L10n.T("后台数据同步轮次");
                return text;
            }
        }

        internal bool CanRestoreResult =>
            Status == "done"
            && ResultRef?["snapshot"] is JsonObject snapshot
            && StringOf(snapshot["contract"]) == "firesky-result-snapshot-v1"
            && snapshot["sha256"] is JsonValue sha
            && sha.GetValueKind() == JsonValueKind.String;
    }

    internal sealed class TaskEvent
    {
        internal required long Sequence { get; init; }
        internal required string AtUtc { get; init; }
        internal required string Event { get; init; }
        internal required JsonObject Data { get; init; }

        internal string Title => Event switch
        {
            "running" => L10n.T("开始"), "progress" => L10n.T("进度"), "cancel_requested" => L10n.T("请求取消"),
            "done" => L10n.T("完成"), "failed" => L10n.T("失败"), "cancelled" => L10n.T("已取消"),
            "interrupted" => L10n.T("已中断"), _ => L10n.T("状态更新"),
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

    internal sealed class TaskHistoryRow
    {
        internal required TaskHistoryItem Item { get; init; }
        internal required Button RestoreResult { get; init; }
        internal required Button RestoreParameters { get; init; }
        internal required Button Forget { get; init; }
    }
}
