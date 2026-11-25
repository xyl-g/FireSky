using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace FireSky;

/// <summary>
/// 软件更新 UI 接线（v1.5.0）：设置 → 通用「软件更新」块（检查/发布说明/下载
/// 进度/自动检查开关）+ 侧栏设置入口绿色徽标 + 启动静默检查（8 秒，失败不打扰）。
/// </summary>
public sealed partial class MainWindow
{
    internal readonly Updater updater = new();
    private Microsoft.UI.Xaml.Controls.InfoBadge? settingsBadge;
    private Microsoft.UI.Xaml.Controls.NavigationViewItem? settingsNavItem;

    private void AttachUpdater()
    {
        updater.Changed += () => DispatcherQueue.TryEnqueue(ApplyUpdaterUi);
        // v1.5.0：侧栏设置入口绿色徽标；WinUI 的 SettingsItem 只能在代码中定制。
        if (Navigation.SettingsItem is Microsoft.UI.Xaml.Controls.NavigationViewItem item)
        {
            settingsNavItem = item;
            settingsBadge = new Microsoft.UI.Xaml.Controls.InfoBadge
            {
                Background = new SolidColorBrush(Colors.Green),
                Visibility = Visibility.Collapsed,
            };
            ToolTipService.SetToolTip(settingsBadge, "");
            item.InfoBadge = settingsBadge;
        }
        UpdaterAutoCheckToggle.IsOn = updater.AutoCheck;
        UpdaterCurrentVersion.Text = $"当前版本 {Updater.CurrentVersion()}";
        ApplyUpdaterUi();
    }

    /// <summary>启动后 8 秒静默检查（默认开，可在设置 → 通用关闭）。</summary>
    private void StartAutoUpdateCheck()
    {
        if (!updater.AutoCheck) return;
        var timer = DispatcherQueue.CreateTimer();
        timer.IsRepeating = false;
        timer.Interval = TimeSpan.FromSeconds(8);
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            if (updater.AutoCheck) updater.CheckForUpdates(interactive: false);
        };
        timer.Start();
    }

    /// <summary>按阶段渲染更新块与侧栏徽标（对齐 macOS updaterBlock）。</summary>
    private void ApplyUpdaterUi()
    {
        if (UpdaterCheckButton == null) return;
        UpdaterCurrentVersion.Text = $"当前版本 {Updater.CurrentVersion()}";
        var phase = updater.CurrentPhase;
        UpdaterCheckingRing.IsActive = phase == Updater.Phase.Checking;
        UpdaterCheckButton.IsEnabled = phase is not (Updater.Phase.Checking
            or Updater.Phase.Downloading or Updater.Phase.Installing);
        UpdaterMessage.Text = updater.Message;
        UpdaterMessage.Visibility = updater.Message.Length > 0
            ? Visibility.Visible : Visibility.Collapsed;
        UpdaterMessage.Foreground = phase == Updater.Phase.Failed
            ? new SolidColorBrush(Colors.Red)
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        UpdaterAvailablePanel.Visibility = phase == Updater.Phase.Available
            ? Visibility.Visible : Visibility.Collapsed;
        UpdaterDownloadingPanel.Visibility = phase == Updater.Phase.Downloading
            ? Visibility.Visible : Visibility.Collapsed;
        UpdaterInstallingPanel.Visibility = phase == Updater.Phase.Installing
            ? Visibility.Visible : Visibility.Collapsed;
        if (phase == Updater.Phase.Available && updater.UpdateInfo is { } info)
        {
            UpdaterAvailableText.Text = $"发现新版本 {info.Version}，可在下载后自动安装。";
            if (info.Notes.Length > 0)
            {
                UpdaterNotes.Visibility = Visibility.Visible;
                UpdaterNotesText.Text = info.Notes;
            }
            else
            {
                UpdaterNotes.Visibility = Visibility.Collapsed;
            }
            UpdaterDownloadButton.IsEnabled = !HasActiveWork;
            ToolTipService.SetToolTip(UpdaterDownloadButton,
                L10n.T("下载 zip、校验 SHA256 后自动替换安装并重启"));
        }
        if (phase == Updater.Phase.Downloading)
        {
            var total = updater.TotalBytes;
            var speedSuffix = updater.BytesPerSecond > 0
                ? $" · {FormatBytes((long)updater.BytesPerSecond)}/s"
                : "";
            UpdaterDownloadBar.IsIndeterminate = total <= 0;
            if (total > 0)
            {
                UpdaterDownloadBar.Maximum = total;
                UpdaterDownloadBar.Value = updater.ReceivedBytes;
                UpdaterDownloadText.Text =
                    $"已下载 {FormatBytes(updater.ReceivedBytes)} / {FormatBytes(total)}{speedSuffix}";
            }
            else
            {
                UpdaterDownloadText.Text = $"已下载 {FormatBytes(updater.ReceivedBytes)}{speedSuffix}";
            }
        }
        // 侧栏设置入口绿色徽标（更新入口在设置 → 通用）。
        if (settingsBadge != null)
        {
            settingsBadge.Visibility = updater.IsUpdateAvailable
                ? Visibility.Visible : Visibility.Collapsed;
            ToolTipService.SetToolTip(settingsBadge, updater.IsUpdateAvailable
                ? $"发现新版本 {updater.UpdateInfo?.Version ?? ""}" : "");
        }
        if (settingsNavItem != null)
            ToolTipService.SetToolTip(settingsNavItem, updater.IsUpdateAvailable
                ? $"发现新版本 {updater.UpdateInfo?.Version ?? ""}" : L10n.T("设置"));
    }

    private static string FormatBytes(long bytes) => bytes switch
    {
        >= 1_073_741_824 => $"{bytes / 1073741824.0:0.00} GB",
        >= 1_048_576 => $"{bytes / 1048576.0:0.0} MB",
        >= 1024 => $"{bytes / 1024.0:0} KB",
        _ => $"{bytes} B",
    };

    private void OnCheckForUpdates(object sender, RoutedEventArgs args)
    {
        updater.CheckForUpdates(interactive: true);
        ApplyUpdaterUi();
    }

    private void OnDownloadAndInstallUpdate(object sender, RoutedEventArgs args)
    {
        if (HasActiveWork)
        {
            Fail(new InvalidOperationException(L10n.T("请等待当前任务完成后再更新。")));
            return;
        }
        updater.DownloadAndInstall();
        ApplyUpdaterUi();
    }

    private void OnCancelUpdateDownload(object sender, RoutedEventArgs args)
        => updater.CancelDownload();

    private void OnOpenUpdateInBrowser(object sender, RoutedEventArgs args)
        => updater.OpenDownloadInBrowser();

    private void OnUpdaterAutoCheckToggled(object sender, RoutedEventArgs args)
    {
        if (!initialized) return;
        updater.SetAutoCheck(UpdaterAutoCheckToggle.IsOn);
        SaveSettings();
    }
}
