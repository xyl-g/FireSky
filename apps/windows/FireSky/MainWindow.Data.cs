using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace FireSky;

/// <summary>
/// 数据管理页（v1.3.0+）：实际数据目录 + 模式库存（GFS / EC / CAMS）+ 手动删除
/// （data_delete_preview/execute，UI 不传磁盘路径）+ SRTM 地形面板
/// （terrain_inventory 估算确认 → terrain_download 区域/点下载）。
/// 探测与同步在「设置 → 数据」；是否满足某次预测以预检为准。
/// </summary>
public sealed partial class MainWindow
{
    // ---------- 手动删除（方案 5.2） ----------

    private JsonObject? pendingDataDelete;
    private JsonObject? dataDeletePreview;
    private bool dataDeletePreviewing, dataDeleteRunning;
    private string dataDeleteMessage = "";
    private bool dataDeleteFailed;

    /// <summary>选择范围并请求引擎预览；范围始终由库存周期键描述。</summary>
    private async Task BeginDataDelete(string source, string? cycle)
    {
        if (!online || busy || dataDeleteRunning || dataDeletePreviewing) return;
        var scope = new JsonObject { ["source"] = source };
        if (!string.IsNullOrEmpty(cycle)) scope["cycle"] = cycle;
        pendingDataDelete = scope;
        dataDeletePreview = null;
        dataDeleteMessage = "";
        dataDeleteFailed = false;
        dataDeletePreviewing = true;
        RenderDataDeleteMessage();
        try
        {
            dataDeletePreview = await engine.Call("data_delete_preview", new() { ["scope"] = scope.DeepClone() });
        }
        catch (Exception error)
        {
            pendingDataDelete = null;
            dataDeleteMessage = FriendlyMessages.Map(error.Message);
            dataDeleteFailed = true;
        }
        finally
        {
            dataDeletePreviewing = false;
            RenderDataDeleteMessage();
            await ShowDataDeleteConfirm();
        }
    }

    private void CancelDataDelete()
    {
        pendingDataDelete = null;
        dataDeletePreview = null;
    }

    /// <summary>确认框展示具体范围 + 文件数 + 预计释放空间；确认后执行。</summary>
    private async Task ShowDataDeleteConfirm()
    {
        if (pendingDataDelete == null || dataDeletePreview == null) return;
        var scope = pendingDataDelete;
        var source = Text(scope, "source");
        var title = source == "ecmwf" ? "EC" : source.ToUpperInvariant();
        var files = (long?)DoubleValue(dataDeletePreview["file_count"]) ?? 0;
        var mb = DoubleValue(dataDeletePreview["total_mb"]) ?? 0;
        var protectedCount = (dataDeletePreview["protected"] as JsonArray)?.Count ?? 0;
        var cycle = Text(scope, "cycle");
        string text;
        if (cycle.Length > 0)
        {
            text = $"删除 {title} 的周期 {cycle}：共 {files} 个文件，预计释放 {mb:0.0} MB。占用中的文件会跳过；报告与任务记录不受影响。";
        }
        else
        {
            text = $"清空 {title} 的全部本地数据：共 {files} 个文件，预计释放 {mb:0.0} MB。占用中的文件会跳过。";
            if (protectedCount > 0)
                text += $"当前有 {protectedCount} 个文件被下载任务占用，将跳过。";
        }
        var dialog = new ContentDialog
        {
            Title = L10n.T("删除本地数据？"),
            Content = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap },
            PrimaryButtonText = L10n.T("删除"),
            CloseButtonText = L10n.T("取消"),
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Root.XamlRoot,
        };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary)
        {
            await ExecuteDataDelete(scope);
        }
        else
        {
            CancelDataDelete();
        }
    }

    /// <summary>确认后执行；预览过期（范围内容变化）时提示重新确认，不静默扩大。</summary>
    private async Task ExecuteDataDelete(JsonObject scope)
    {
        var previewId = Text(dataDeletePreview, "preview_id");
        if (previewId.Length == 0 || dataDeleteRunning) return;
        dataDeleteRunning = true;
        try
        {
            var result = await engine.Call("data_delete_execute", new()
            {
                ["scope"] = scope.DeepClone(), ["preview_id"] = previewId, ["confirm"] = true,
            });
            pendingDataDelete = null;
            dataDeletePreview = null;
            dataDeleteMessage = DataDeleteResultSummary(result);
            dataDeleteFailed = (result["failed"] as JsonArray)?.Count > 0;
            await LoadInventory();
        }
        catch (Exception error)
        {
            dataDeletePreview = null;
            dataDeleteMessage = FriendlyMessages.Map(error.Message);
            dataDeleteFailed = true;
        }
        finally
        {
            dataDeleteRunning = false;
            RenderDataDeleteMessage();
        }
    }

    private static string DataDeleteResultSummary(JsonObject result)
    {
        var deleted = (long?)DoubleValue(result["deleted"]) ?? 0;
        var skipped = (result["skipped"] as JsonArray)?.Count ?? 0;
        var failed = (result["failed"] as JsonArray)?.Count ?? 0;
        var mb = DoubleValue(result["freed_mb"]) ?? 0;
        var parts = new List<string> { $"已删除 {deleted} 个文件，释放 {mb:0.0} MB" };
        if (skipped > 0) parts.Add($"占用中跳过 {skipped} 个");
        if (failed > 0) parts.Add($"失败 {failed} 个");
        return string.Join("；", parts);
    }

    private void RenderDataDeleteMessage()
    {
        DataDeleteMessage.Text = dataDeleteMessage;
        DataDeleteMessage.Foreground = dataDeleteFailed
            ? new SolidColorBrush(Colors.Red)
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        DataDeleteMessage.Visibility = dataDeleteMessage.Length > 0
            ? Visibility.Visible : Visibility.Collapsed;
    }

    // ---------- 库存（含每周期/每源删除入口） ----------

    private async Task LoadInventory()
    {
        if (!online) return;
        try
        {
            var response = await engine.Call("inventory");
            InventoryRows.Children.Clear();
            foreach (var item in response["datasets"] as JsonArray ?? [])
            {
                if (item is not JsonObject dataset) continue;
                var source = Text(dataset, "source");
                var title = source == "ecmwf" ? "EC" : source.ToUpperInvariant();
                var files = (long?)DoubleValue(dataset["total_file_count"]) ?? 0;
                var storageMb = DoubleValue(dataset["total_storage_mb"]) ?? 0;
                var stack = new StackPanel { Spacing = 12 };
                var header = new Grid { ColumnSpacing = 12 };
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.Children.Add(new FontIcon
                {
                    Glyph = "\uEDA2", FontSize = 17,
                    Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
                });
                header.Children.Add(new TextBlock
                {
                    Text = title, FontSize = 18,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Margin = new Thickness(8, 0, 0, 0),
                });
                // 库存状态使用中性表述（不冒充"已就绪"）。
                header.Children.Add(new TextBlock
                {
                    Text = $"{files:0} 个文件", VerticalAlignment = VerticalAlignment.Center, FontSize = 12,
                });
                header.Children.Add(new TextBlock
                {
                    Text = $"{storageMb / 1024:0.00} GB", VerticalAlignment = VerticalAlignment.Center,
                    FontSize = 12, Opacity = .75,
                });
                var path = Text(dataset, "path");
                var open = new IconButton("\uE838", L10n.T("在资源管理器中打开"));
                open.Click += (_, _) =>
                {
                    if (path.Length > 0) System.Diagnostics.Process.Start(
                        new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
                };
                header.Children.Add(open);
                // v1.3.0：整源删除入口（UI 只描述范围，不传磁盘路径）。
                var sourceDelete = new IconButton("\uE74D", L10n.T("清空该数据源"));
                sourceDelete.IsEnabled = !dataDeleteRunning;
                var capturedSource = source;
                sourceDelete.Click += (_, _) => _ = BeginDataDelete(capturedSource, null);
                header.Children.Add(sourceDelete);
                for (var i = 0; i < header.Children.Count; i++)
                    Grid.SetColumn((FrameworkElement)header.Children[i], i);
                stack.Children.Add(header);
                var cycles = dataset["cycles"] as JsonObject ?? [];
                var details = new StackPanel { Spacing = 6 };
                foreach (var cycle in cycles.OrderByDescending(c => c.Key))
                {
                    var cycleRow = new Grid { ColumnSpacing = 10 };
                    cycleRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                    cycleRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                    cycleRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                    cycleRow.Children.Add(new TextBlock
                    {
                        Text = cycle.Key, FontSize = 12, Opacity = .85,
                    });
                    cycleRow.Children.Add(new TextBlock
                    {
                        Text = $"{Number(cycle.Value, "files"):0} 个文件", FontSize = 12, Opacity = .65,
                    });
                    var cycleDelete = new IconButton("\uE74D", L10n.T("删除该周期"));
                    cycleDelete.IsEnabled = !dataDeleteRunning;
                    var capturedCycle = cycle.Key;
                    cycleDelete.Click += (_, _) => _ = BeginDataDelete(capturedSource, capturedCycle);
                    Grid.SetColumn(cycleDelete, 2);
                    cycleRow.Children.Add(cycleDelete);
                    details.Children.Add(cycleRow);
                }
                if (cycles.Count == 0)
                    details.Children.Add(new TextBlock { Text = L10n.T("无数据"), FontSize = 12, Opacity = .6 });
                stack.Children.Add(new Expander
                {
                    Header = $"{cycles.Count} 个周期",
                    Content = details,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                });
                InventoryRows.Children.Add(new Border
                {
                    Padding = new Thickness(18),
                    CornerRadius = new CornerRadius(14),
                    Background = ThemeBrush("FireSkyCardBrush"),
                    BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
                    BorderThickness = new Thickness(1),
                    Child = stack,
                });
            }
            DataWorkspaceText.Text = WorkspacePath.Text.Length > 0
                ? WorkspacePath.Text : L10n.T("数据目录未设置");
            DataWorkspaceText.Opacity = WorkspacePath.Text.Length > 0 ? .65 : .9;
            var hasDatasets = (response["datasets"] as JsonArray)?.OfType<JsonObject>().Any() == true;
            InventoryEmptyCard.Visibility = !hasDatasets ? Visibility.Visible : Visibility.Collapsed;
            RenderInventoryCategories(response["categories"]);
            InventoryError.Visibility = Visibility.Collapsed;
        }
        catch (Exception error)
        {
            InventoryEmptyCard.Visibility = Visibility.Collapsed;
            InventoryCategoriesHost.Children.Clear();
            InventoryError.Text = FriendlyMessages.Map(error.Message);
            InventoryError.Visibility = Visibility.Visible;
            Fail(error, "data");
        }
    }

    private void RenderInventoryCategories(JsonNode? categoriesNode)
    {
        if (InventoryCategoriesHost == null) return;
        InventoryCategoriesHost.Children.Clear();
        JsonArray? rows = null;
        if (categoriesNode is JsonObject wrapped && wrapped["categories"] is JsonArray nested)
            rows = nested;
        else if (categoriesNode is JsonArray direct)
            rows = direct;
        var items = rows?.OfType<JsonObject>().ToList() ?? [];
        if (items.Count == 0) return;

        var cardStack = new StackPanel { Spacing = 10 };
        var header = new StackPanel { Spacing = 3 };
        header.Children.Add(new TextBlock
        {
            Text = L10n.T("预测结果与附加缓存"),
            FontSize = 15,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        header.Children.Add(new TextBlock
        {
            Text = L10n.T("计算过程中的中间产物（衍生张量、卫星解包帧、临时排行缓存）会在计算结束后立即自动删除，磁盘仅保留最终结果与原始气象/地形数据。"),
            FontSize = 11,
            Opacity = .7,
            TextWrapping = TextWrapping.Wrap,
        });
        cardStack.Children.Add(header);
        cardStack.Children.Add(new Border
        {
            Height = 1,
            Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
        });

        for (var i = 0; i < items.Count; i++)
        {
            var item = items[i];
            var key = Text(item, "category");
            var files = (long?)DoubleValue(item["file_count"]) ?? 0;
            var bytes = (long?)DoubleValue(item["bytes"]) ?? 0;
            var path = Text(item, "path");

            var row = new Grid { ColumnSpacing = 12, Padding = new Thickness(0, 4, 0, 4) };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(24) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            row.Children.Add(new FontIcon
            {
                Glyph = CategoryGlyph(key),
                FontSize = 15,
                Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
                VerticalAlignment = VerticalAlignment.Center,
            });
            var textCol = new StackPanel { Spacing = 2 };
            textCol.Children.Add(new TextBlock
            {
                Text = CategoryTitle(key),
                FontSize = 13,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
            });
            var subtitle = CategorySubtitle(key);
            if (subtitle.Length > 0)
            {
                textCol.Children.Add(new TextBlock
                {
                    Text = subtitle,
                    FontSize = 11,
                    Opacity = .65,
                    TextWrapping = TextWrapping.Wrap,
                });
            }
            Grid.SetColumn(textCol, 1);
            row.Children.Add(textCol);

            var filesText = new TextBlock
            {
                Text = L10n.IsEnglish ? $"{files} files" : $"{files} 个文件",
                FontSize = 12,
                Opacity = .75,
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(filesText, 2);
            row.Children.Add(filesText);

            var sizeText = new TextBlock
            {
                Text = FormatBytes(bytes),
                FontSize = 12,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                MinWidth = 72,
                HorizontalTextAlignment = TextAlignment.Right,
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(sizeText, 3);
            row.Children.Add(sizeText);

            if (path.Length > 0)
            {
                var open = new IconButton("\uE838", L10n.T("在资源管理器中打开"));
                open.Click += (_, _) =>
                {
                    if (Directory.Exists(path))
                    {
                        System.Diagnostics.Process.Start(
                            new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
                    }
                };
                Grid.SetColumn(open, 4);
                row.Children.Add(open);
            }

            cardStack.Children.Add(row);
            if (i < items.Count - 1)
            {
                cardStack.Children.Add(new Border
                {
                    Height = 1,
                    Opacity = .45,
                    Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
                });
            }
        }

        InventoryCategoriesHost.Children.Add(new Border
        {
            Padding = new Thickness(18),
            CornerRadius = new CornerRadius(14),
            Background = ThemeBrush("FireSkyCardBrush"),
            BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
            BorderThickness = new Thickness(1),
            Child = cardStack,
        });
    }

    private static string CategoryTitle(string key) => key switch
    {
        "point-forecasts" => L10n.T("单点预测结果缓存"),
        "regional-forecasts" => L10n.T("区域预测结果缓存"),
        "map-frames" => L10n.T("地图渲染帧缓存"),
        "reports" => L10n.T("历史报告产物"),
        "satellite" => L10n.T("卫星实况帧（Himawari HSD）"),
        "srtm" => L10n.T("SRTM 地形瓦片"),
        _ => key,
    };

    private static string CategorySubtitle(string key) => key switch
    {
        "point-forecasts" => L10n.T("按地点与起报周期复用单点计算结果"),
        "regional-forecasts" => L10n.T("按区域范围、模式与周期复用计算结果"),
        "map-frames" => L10n.T("地图与剖面可视化中间帧"),
        "reports" => L10n.T("已归档预测报告与快照"),
        "satellite" => L10n.T("葵花卫星多波段观测帧"),
        "srtm" => L10n.T("离线高程与地形遮挡计算瓦片"),
        _ => "",
    };

    private static string CategoryGlyph(string key) => key switch
    {
        "point-forecasts" => "\uE707",
        "regional-forecasts" => "\uE826",
        "map-frames" => "\uEB9F",
        "reports" => "\uE8A5",
        "satellite" => "\uE701",
        "srtm" => "\uE7F4",
        _ => "\uE8B7",
    };

    /// <summary>轻量图标按钮（访达/删除等行内动作）。</summary>
    private sealed class IconButton : Button
    {
        public IconButton(string glyph, string help)
        {
            Padding = new Thickness(6, 3, 6, 3);
            BorderThickness = new Thickness(0);
            Background = new SolidColorBrush(Colors.Transparent);
            Content = new FontIcon { Glyph = glyph, FontSize = 14 };
            ToolTipService.SetToolTip(this, help);
        }
    }

    private async void OnRefreshInventory(object sender, RoutedEventArgs args) => await LoadInventory();

    private void OnOpenDataFolder(object sender, RoutedEventArgs args)
    {
        var path = WorkspacePath.Text;
        if (path.Length == 0 || !Directory.Exists(path))
        {
            Fail(new InvalidOperationException(L10n.T("数据目录尚未设置或不存在。")), "data");
            return;
        }
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path)
        {
            UseShellExecute = true,
        });
    }

    // ---------- SRTM 地形下载（方案 7 / v1.3-v1.4） ----------

    /// <summary>区域预设（用户口径，对齐 macOS TerrainRegion）；全球 = 整覆盖域。</summary>
    internal static readonly (string Label, int South, int North, int West, int East)[] TerrainRegions =
    [
        // Label 是状态键/引擎参数（terrain region / anchor），保持中文原文；
        // 显示经 ComboBoxItem Content 的 L10n.T 翻译。
        ("中国", 18, 54, 73, 135),
        ("东亚", 20, 54, 100, 146),
        ("东南亚", -11, 28, 92, 141),
        ("亚洲", -11, 59, 26, 179),
        ("欧洲", 35, 59, -11, 40),
        ("北美", 15, 59, -170, -52),
        ("南美", -56, 13, -82, -34),
        ("非洲", -35, 37, -18, 52),
        ("大洋洲", -47, 0, 110, 179),
        ("全球", 0, 0, 0, 0),
    ];

    private JsonObject? terrain;
    private bool terrainLoading, terrainEstimateLoading, terrainDownloading, terrainCancelling;
    private string terrainMessage = "";
    private bool terrainMessageFailed;
    private JsonObject terrainProgress = [];
    private int terrainRegionIndex;
    private bool showTerrainConfirm;
    private int terrainLastFailedCount;
    private (string Label, int South, int North, int West, int East)? terrainLastFailedRegion;
    private List<string> terrainLastFailedTiles = [];
    private CancellationTokenSource? terrainOperation;

    private void BuildTerrainRegionPicker()
    {
        TerrainRegionPicker.Items.Clear();
        foreach (var region in TerrainRegions)
            TerrainRegionPicker.Items.Add(new ComboBoxItem { Content = L10n.T(region.Label) });
        TerrainRegionPicker.SelectedIndex = 0;
        TerrainRegionPicker.SelectionChanged += (_, _) =>
        {
            if (initialized && TerrainRegionPicker.SelectedIndex >= 0)
                terrainRegionIndex = TerrainRegionPicker.SelectedIndex;
        };
    }

    private async Task LoadTerrain()
    {
        if (!online || terrainLoading || terrainDownloading) return;
        terrainLoading = true;
        RenderTerrainControls();
        try
        {
            terrain = await engine.Call("terrain_inventory",
                new() { ["lat"] = Latitude.Value, ["lon"] = Longitude.Value });
            terrainMessage = "";
            terrainMessageFailed = false;
        }
        catch (Exception error)
        {
            terrain = null;
            terrainMessage = FriendlyMessages.Map(error.Message);
            terrainMessageFailed = true;
        }
        finally
        {
            terrainLoading = false;
            RenderTerrain();
        }
    }

    private async void OnRefreshTerrain(object sender, RoutedEventArgs args) => await LoadTerrain();

    /// <summary>请求区域/全球下载估算（HEAD 抽样，几秒）；完成后弹确认框。</summary>
    private async void OnTerrainRegionEstimate(object sender, RoutedEventArgs args)
    {
        if (!online || terrainDownloading || terrainEstimateLoading) return;
        terrainEstimateLoading = true;
        RenderTerrainControls();
        var region = TerrainRegions[terrainRegionIndex];
        var parameters = new JsonObject
        {
            ["estimate_global"] = true,
            ["lat"] = Latitude.Value, ["lon"] = Longitude.Value,
        };
        if (region.Label != "全球")
        {
            parameters["bounds"] = new JsonObject
            {
                ["south"] = region.South, ["north"] = region.North,
                ["west"] = region.West, ["east"] = region.East,
            };
        }
        JsonObject? estimate = null;
        string? errorMessage = null;
        try
        {
            estimate = await engine.Call("terrain_inventory", parameters);
        }
        catch (Exception error)
        {
            errorMessage = FriendlyMessages.Map(error.Message);
        }
        finally
        {
            terrainEstimateLoading = false;
        }
        if (estimate == null)
        {
            terrainMessage = errorMessage ?? L10n.T("无法获取下载估算。");
            terrainMessageFailed = true;
            RenderTerrain();
            return;
        }
        var global = estimate["global"] as JsonObject ?? [];
        var candidates = (long?)DoubleValue(global["candidates"]) ?? 0;
        var existing = (long?)DoubleValue(global["existing"]) ?? 0;
        var confirmTitle = region.Label == "全球" ? L10n.T("下载全球地形数据？") : $"下载{region.Label}地形数据？";
        var confirmText = $"将下载{region.Label}的 SRTM 瓦片：候选 {candidates} 块，本地已有 {existing} 块会自动跳过"
            + L10n.T("（海洋上的瓦片是很小的空值瓦片，同样会下载）。下载耗时较长且占用磁盘，可随时取消，重新开始会跳过已下载瓦片。");
        if (DoubleValue(global["estimated_gb"]) is { } gb)
            confirmText += $"按抽样估算体积约 {gb:0} GB（抽样混合了海洋小瓦片与陆地大瓦片，实际体积以运行时用量为准）。";
        else
            confirmText += L10n.T("暂时无法估算体积（网络或服务不可用），开始后会显示实际用量。");
        var dialog = new ContentDialog
        {
            Title = confirmTitle,
            Content = new TextBlock { Text = confirmText, TextWrapping = TextWrapping.Wrap },
            PrimaryButtonText = L10n.T("开始下载"),
            CloseButtonText = L10n.T("取消"),
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Root.XamlRoot,
        };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary)
        {
            DownloadTerrainRegion(region);
        }
    }

    private void OnTerrainRetryFailed(object sender, RoutedEventArgs args)
    {
        var region = terrainLastFailedRegion ?? TerrainRegions[terrainRegionIndex];
        var tiles = terrainLastFailedTiles.Count > 0 ? terrainLastFailedTiles : null;
        DownloadTerrainRegion(region, tiles);
    }

    /// <summary>区域/全球批量下载：跳过已有、可取消续传；不占计算锁。</summary>
    private async void DownloadTerrainRegion((string Label, int South, int North, int West, int East) region,
        List<string>? tiles = null)
    {
        if (!online || busy || terrainDownloading) return;
        terrainDownloading = true;
        terrainCancelling = false;
        terrainMessage = "";
        terrainProgress = [];
        terrainOperation?.Cancel();
        terrainOperation?.Dispose();
        terrainOperation = new CancellationTokenSource();
        var token = terrainOperation.Token;
        RenderTerrainControls();
        var parameters = new JsonObject { ["mode"] = "global", ["timeout_seconds"] = 300.0 };
        if (tiles is { Count: > 0 })
        {
            parameters["tiles"] = new JsonArray(tiles.Select(t => (JsonNode)JsonValue.Create(t)!).ToArray());
        }
        else if (region.Label != "全球")
        {
            parameters["bounds"] = new JsonObject
            {
                ["south"] = region.South, ["north"] = region.North,
                ["west"] = region.West, ["east"] = region.East,
            };
        }
        try
        {
            var result = await engine.Call("terrain_download", parameters,
                progress: e => DispatcherQueue.TryEnqueue(() =>
                {
                    if (e["terrain"] is JsonObject progress)
                    {
                        terrainProgress = progress;
                        RenderTerrainProgress();
                    }
                }), cancellation: token);
            token.ThrowIfCancellationRequested();
            var failed = (int)(DoubleValue(result["failed"]) ?? 0);
            var failedTiles = (result["failed_tiles"] as JsonArray ?? [])
                .Select(node => StringOrNull(node))
                .Where(name => !string.IsNullOrEmpty(name))
                .Cast<string>()
                .ToList();
            terrainLastFailedCount = failed;
            terrainLastFailedRegion = failed > 0 ? region : null;
            terrainLastFailedTiles = failed > 0 ? failedTiles : [];
            terrainMessage = TerrainResultMessage(result, region.Label);
            terrainMessageFailed = TerrainMessageFailedFlag(result);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            terrainMessage = L10n.T("已停止下载；已完成的瓦片保留，重新开始会跳过它们。");
            terrainMessageFailed = false;
        }
        catch (Exception error)
        {
            terrainMessage = FriendlyMessages.Map(error.Message);
            terrainMessageFailed = true;
        }
        finally
        {
            terrainDownloading = false;
            terrainCancelling = false;
            RenderTerrain();
            _ = LoadTerrain();
        }
    }

    /// <summary>点级下载（仅当前地点瓦片），独立次级入口。</summary>
    private async void OnTerrainPointDownload(object sender, RoutedEventArgs args)
    {
        if (!online || busy || terrainDownloading) return;
        terrainDownloading = true;
        terrainCancelling = false;
        terrainMessage = "";
        terrainProgress = [];
        terrainOperation?.Cancel();
        terrainOperation?.Dispose();
        terrainOperation = new CancellationTokenSource();
        var token = terrainOperation.Token;
        RenderTerrainControls();
        try
        {
            var result = await engine.Call("terrain_download", new()
            {
                ["lat"] = Latitude.Value, ["lon"] = Longitude.Value, ["timeout_seconds"] = 600.0,
            }, progress: e => DispatcherQueue.TryEnqueue(() =>
            {
                if (e["terrain"] is JsonObject progress)
                {
                    terrainProgress = progress;
                    RenderTerrainProgress();
                }
            }), cancellation: token);
            token.ThrowIfCancellationRequested();
            terrainMessage = TerrainResultMessage(result, null);
            terrainMessageFailed = TerrainMessageFailedFlag(result);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            terrainMessage = L10n.T("已停止下载；未写入半成品瓦片。");
            terrainMessageFailed = false;
        }
        catch (Exception error)
        {
            terrainMessage = FriendlyMessages.Map(error.Message);
            terrainMessageFailed = true;
        }
        finally
        {
            terrainDownloading = false;
            terrainCancelling = false;
            RenderTerrain();
        }
    }

    private void OnCancelTerrainDownload(object sender, RoutedEventArgs args)
    {
        if (!terrainDownloading) return;
        terrainCancelling = true;
        terrainOperation?.Cancel();
    }

    /// <summary>下载结果文案（对齐 macOS terrainResultMessage）。</summary>
    private static string TerrainResultMessage(JsonObject result, string? regionLabel)
    {
        var status = Text(result, "status");
        if (regionLabel != null)
        {
            var downloaded = (long?)DoubleValue(result["downloaded"]) ?? 0;
            var absent = (long?)DoubleValue(result["absent"]) ?? 0;
            var failed = (long?)DoubleValue(result["failed"]) ?? 0;
            var gb = DoubleValue(result["received_gb"]) ?? 0;
            var summary = $"新增 {downloaded} 块、缺失 {absent} 块、失败 {failed} 块，占用 {gb:0.0} GB。";
            if (status == "cancelled")
            {
                return Text(result, "stopped_reason") == "disk_space"
                    ? $"{regionLabel}下载因磁盘空间不足停止：{summary}重新开始会跳过已有瓦片。"
                    : $"{regionLabel}下载已停止：{summary}重新开始会跳过已有瓦片。";
            }
            return failed > 0
                ? $"{regionLabel}下载完成（部分失败）：{summary}再次运行可重试失败瓦片。"
                : $"{regionLabel}下载完成：{summary}";
        }
        return status switch
        {
            "exists" => L10n.T("地形瓦片已存在，直接复用本地数据。"),
            "downloaded" => $"已下载地形瓦片 {Text(result, "tile")}。",
            "out_of_coverage" => Text(result, "message") is { Length: > 0 } message
                ? message : L10n.T("该位置不在 SRTM 覆盖范围内。"),
            _ => L10n.T("下载未完成。"),
        };
    }

    private static bool TerrainMessageFailedFlag(JsonObject result)
    {
        var status = Text(result, "status");
        if (status is "cancelled" or "done")
            return ((long?)DoubleValue(result["failed"]) ?? 0) > 0
                || Text(result, "stopped_reason") == "disk_space";
        return status is "out_of_coverage" or "failed";
    }

    /// <summary>地形面板状态渲染：副标题、按钮可用性、消息与本地瓦片行。</summary>
    private void RenderTerrain()
    {
        if (TerrainSubtitle == null) return;
        string subtitle;
        if (terrain == null)
        {
            subtitle = online ? L10n.T("全球瓦片下载后会覆盖任意地点，无需再按点下载。") : L10n.T("引擎未连接。");
        }
        else if (BoolValue(terrain, "coverage_ok") == false)
        {
            subtitle = L10n.T("当前纬度不在 SRTM 覆盖范围（-56 ≤ 纬度 < 60）。");
        }
        else if (terrain["tile"] is not JsonObject tile)
        {
            subtitle = L10n.T("显示本地已有地形瓦片。");
        }
        else
        {
            var name = Text(tile, "name");
            var exists = BoolValue(tile, "exists") == true;
            subtitle = $"目标 {LocationName.Text}（{LatLonLabel(Latitude.Value, Longitude.Value)}）"
                + $" · 瓦片 {name} · {(exists ? L10n.T("已有本地瓦片") : L10n.T("未下载"))}";
        }
        TerrainSubtitle.Text = subtitle;
        var coverageMissing = BoolValue(terrain, "coverage_ok") == false;
        TerrainPointButton.IsEnabled = online && !busy && !terrainDownloading && !coverageMissing;
        TerrainRefreshButton.IsEnabled = online && !terrainDownloading;
        TerrainRegionButton.IsEnabled = online && !busy && !terrainDownloading;
        TerrainRegionPicker.IsEnabled = online && !terrainDownloading;
        TerrainMessage.Text = terrainMessage;
        TerrainMessage.Foreground = terrainMessageFailed
            ? new SolidColorBrush(Colors.Red)
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        TerrainMessage.Visibility = terrainMessage.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        var count = (long?)DoubleValue(terrain?["tile_count"]) ?? 0;
        if (count > 8)
        {
            TerrainLocalTiles.Text = $"本地已有 {count} 块瓦片。";
            TerrainLocalTiles.Visibility = Visibility.Visible;
        }
        else if (terrain?["tiles"] is JsonArray tiles && tiles.Count > 0)
        {
            var names = string.Join("、", tiles.Select(node => node is JsonObject tile ? Text(tile, "name") : ""));
            TerrainLocalTiles.Text = $"本地瓦片：{names}";
            TerrainLocalTiles.Visibility = Visibility.Visible;
        }
        else
        {
            TerrainLocalTiles.Text = "";
            TerrainLocalTiles.Visibility = Visibility.Collapsed;
        }
        RenderTerrainProgress();
        RenderTerrainControls();
    }

    private void RenderTerrainProgress()
    {
        if (TerrainBulkBar == null) return;
        var bulkRun = terrainProgress.ContainsKey("candidates");
        TerrainBulkProgress.Visibility = terrainDownloading && bulkRun
            ? Visibility.Visible : Visibility.Collapsed;
        TerrainPointProgress.Visibility = terrainDownloading && !bulkRun
            ? Visibility.Visible : Visibility.Collapsed;
        if (terrainDownloading && bulkRun)
        {
            var percent = DoubleValue(terrainProgress["percent"]) ?? 0;
            var downloaded = (long?)DoubleValue(terrainProgress["downloaded"]) ?? 0;
            var candidates = (long?)DoubleValue(terrainProgress["candidates"]) ?? 0;
            var absent = (long?)DoubleValue(terrainProgress["absent"]) ?? 0;
            var failed = (long?)DoubleValue(terrainProgress["failed"]) ?? 0;
            var existing = (long?)DoubleValue(terrainProgress["existing"]) ?? 0;
            var bytes = (long?)DoubleValue(terrainProgress["received_bytes"]) ?? 0;
            var current = Text(terrainProgress, "current_tile");
            TerrainBulkBar.Value = percent;
            TerrainBulkCounts.Text =
                $"{percent:0}% · 已下载 {downloaded}/{candidates} · 已有 {existing} · 缺失 {absent} · 失败 {failed}"
                + (current.Length > 0 ? $" · 当前 {current}" : "");
            TerrainBulkBytes.Text = bytes >= 1_073_741_824
                ? $"{bytes / 1073741824.0:0.00} GB" : $"{bytes / 1048576.0:0.0} MB";
        }
        else if (terrainDownloading)
        {
            var percent = DoubleValue(terrainProgress["percent"]);
            if (percent is { } value)
            {
                TerrainPointBar.IsIndeterminate = false;
                TerrainPointBar.Value = value;
                TerrainPointText.Text = $"{value:0}%";
            }
            else
            {
                TerrainPointBar.IsIndeterminate = true;
                TerrainPointText.Text = Text(terrainProgress, "stage") == "verifying"
                    ? L10n.T("校验瓦片数据…") : L10n.T("下载中…");
            }
            if (DoubleValue(terrainProgress["received_bytes"]) is { } received)
            {
                var size = received >= 1_048_576
                    ? $"{received / 1048576.0:0.0} MB" : $"{received / 1024.0:0} KB";
                TerrainPointText.Text = DoubleValue(terrainProgress["total_bytes"]) is { } total
                    ? $"{size} / {total / 1048576.0:0.0} MB" : size;
            }
        }
        RenderTerrainControls();
    }

    private void RenderTerrainControls()
    {
        if (TerrainLoadingRing == null) return;
        TerrainLoadingRing.IsActive = terrainLoading || terrainEstimateLoading;
        TerrainCancelButton.Visibility = terrainDownloading ? Visibility.Visible : Visibility.Collapsed;
        TerrainCancelButton.IsEnabled = terrainDownloading && !terrainCancelling;
        TerrainRegionButton.Visibility = terrainDownloading ? Visibility.Collapsed : Visibility.Visible;
        ToolTipService.SetToolTip(TerrainRegionButton, L10n.T("确认估算后下载所选区域的 SRTM 瓦片（跳过已有；可随时取消续传）"));
        if (TerrainRetryFailedButton != null)
        {
            TerrainRetryFailedButton.Visibility = !terrainDownloading && terrainLastFailedCount > 0
                ? Visibility.Visible : Visibility.Collapsed;
            TerrainRetryFailedButton.IsEnabled = online && !busy && !terrainDownloading;
            TerrainRetryFailedButton.Content = L10n.IsEnglish
                ? $"Retry failed ({terrainLastFailedCount})"
                : $"重试失败项（{terrainLastFailedCount}）";
            ToolTipService.SetToolTip(TerrainRetryFailedButton, L10n.T("定向重试上次失败的 SRTM 瓦片，自动跳过已完成瓦片"));
        }
        TerrainRegionPicker.Visibility = terrainDownloading ? Visibility.Collapsed : Visibility.Visible;
        TerrainPointButton.Visibility = terrainDownloading ? Visibility.Collapsed : Visibility.Visible;
        TerrainRefreshButton.Visibility = terrainDownloading ? Visibility.Collapsed : Visibility.Visible;
    }
}
