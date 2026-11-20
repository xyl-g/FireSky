using Microsoft.UI;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace FireSky;

/// <summary>
/// 数据管理页（v1.2.0 瘦身）：实际数据目录 + 模式库存（GFS / EC / CAMS）。
/// 探测与同步在「设置 → 数据」；是否满足某次预测以预检为准。
/// </summary>
public sealed partial class MainWindow
{
    private async Task LoadInventory()
    {
        if (!online) return;
        try
        {
            var response = await engine.Call("inventory");
            InventoryRows.Children.Clear();
            var filesTotal = 0L;
            var mbTotal = 0.0;
            foreach (var item in response["datasets"] as JsonArray ?? [])
            {
                if (item is not JsonObject dataset) continue;
                var source = Text(dataset, "source");
                var title = source == "ecmwf" ? "EC" : source.ToUpperInvariant();
                var files = (long?)DoubleValue(dataset["total_file_count"]) ?? 0;
                var storageMb = DoubleValue(dataset["total_storage_mb"]) ?? 0;
                filesTotal += files;
                mbTotal += storageMb;
                var stack = new StackPanel { Spacing = 12 };
                var header = new Grid { ColumnSpacing = 12 };
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
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
                var open = new Button
                {
                    Padding = new Thickness(6, 3, 6, 3),
                    BorderThickness = new Thickness(0),
                    Background = new SolidColorBrush(Colors.Transparent),
                    Content = new SymbolIcon(Symbol.Folder),
                };
                ToolTipService.SetToolTip(open, "在资源管理器中打开");
                open.Click += (_, _) =>
                {
                    if (path.Length > 0) System.Diagnostics.Process.Start(
                        new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
                };
                header.Children.Add(open);
                for (var i = 0; i < header.Children.Count; i++)
                    Grid.SetColumn((FrameworkElement)header.Children[i], i);
                stack.Children.Add(header);
                var details = new StackPanel { Spacing = 6 };
                var cycles = dataset["cycles"] as JsonObject ?? new JsonObject();
                foreach (var cycle in cycles.OrderByDescending(c => c.Key))
                    details.Children.Add(new TextBlock
                    {
                        Text = $"{cycle.Key}   {Number(cycle.Value, "files"):0} 个文件",
                        FontSize = 12, Opacity = .85,
                    });
                if (cycles.Count == 0)
                    details.Children.Add(new TextBlock { Text = "无数据", FontSize = 12, Opacity = .6 });
                stack.Children.Add(new Expander
                {
                    Header = $"{cycles.Count} 个周期",
                    Content = details,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                });
                InventoryRows.Children.Add(stack);
                InventoryRows.Children.Add(new Border
                {
                    Height = 1,
                    Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
                });
            }
            DataWorkspaceText.Text = WorkspacePath.Text.Length > 0
                ? WorkspacePath.Text : "数据目录未设置";
            DataWorkspaceText.Opacity = WorkspacePath.Text.Length > 0 ? .65 : .9;
            InventoryError.Visibility = Visibility.Collapsed;
        }
        catch (Exception error)
        {
            InventoryError.Text = FriendlyMessages.Map(error.Message);
            InventoryError.Visibility = Visibility.Visible;
        }
    }

    private async void OnRefreshInventory(object sender, RoutedEventArgs args) => await LoadInventory();

    private void OnOpenDataFolder(object sender, RoutedEventArgs args)
    {
        var path = WorkspacePath.Text;
        if (path.Length == 0 || !Directory.Exists(path))
        {
            Fail(new InvalidOperationException("数据目录尚未设置或不存在。"));
            return;
        }
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path)
        {
            UseShellExecute = true,
        });
    }
}
