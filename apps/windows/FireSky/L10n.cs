using System.Collections.Generic;
using System.Globalization;
using Windows.Storage;

namespace FireSky;

/// 界面多语言（v1.9.7，macOS/iOS L10n 同构）：跟随系统 / 中文 / English。
/// 中心字典 key = 中文原文（与 macOS L10n.swift 同源口径，未收录回落原文）；
/// UI 文案经 ApplyLocalization 视觉树遍历替换，语言偏好存 LocalSettings。
internal static class L10n
{
    private const string SettingsKey = "appLanguage";

    /// system / zh / en。
    public static string Preference
    {
        get
        {
            try
            {
                return ApplicationData.Current.LocalSettings.Values[SettingsKey] as string ?? "system";
            }
            catch
            {
                return "system";
            }
        }
        set
        {
            try
            {
                ApplicationData.Current.LocalSettings.Values[SettingsKey] = value;
            }
            catch
            {
                // 未打包/受限环境：语言偏好回落内存态（重启丢失）。
                _ = value;
            }
        }
    }

    public static bool IsEnglish
    {
        get
        {
            switch (Preference)
            {
                case "zh": return false;
                case "en": return true;
                default:
                    var first = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName;
                    return !string.Equals(first, "zh", StringComparison.OrdinalIgnoreCase);
            }
        }
    }

    /// 查找：中文原文 → 当前语言文案；未收录回落原文。
    public static string T(string chinese)
    {
        if (!IsEnglish || !Dictionary.TryGetValue(chinese, out var english)) return chinese;
        return english;
    }

    private static Dictionary<string, string>? reverse;

    /// 双向归一：当前文案若是已知英文串反查回中文，否则原样（已是中文）。
    /// 使 en→zh→en 来回切换幂等。
    public static string Translate(string current)
    {
        if (reverse == null)
        {
            reverse = new Dictionary<string, string>();
            foreach (var (chinese, english) in Dictionary)
            {
                reverse.TryAdd(english, chinese);
            }
        }
        var chinese = reverse.TryGetValue(current, out var mapped) ? mapped : current;
        return T(chinese);
    }

    /// 状态映射（引擎中文状态 → 当前语言；macOS L10n.status 同构）。
    public static string Status(string chinese) => T(chinese);

    private static readonly Dictionary<string, string> Dictionary = new()
    {
        // 导航 / 页面
        ["单点预测"] = "Point Forecast",
        ["区域预测"] = "Regional",
        ["实况观测"] = "Nowcast",
        ["数据管理"] = "Data Management",
        ["任务历史"] = "Task History",
        ["设置"] = "Settings",
        ["观测点"] = "Observer",
        // 分类
        ["通用"] = "General",
        ["数据"] = "Data",
        ["高级"] = "Advanced",
        ["外观"] = "Appearance",
        ["主题"] = "Theme",
        ["跟随系统"] = "Follow system",
        ["浅色"] = "Light",
        ["深色"] = "Dark",
        // 通用按钮
        ["取消"] = "Cancel",
        ["确定"] = "OK",
        ["删除"] = "Delete",
        ["刷新"] = "Refresh",
        ["重试"] = "Retry",
        ["保留"] = "Keep",
        ["迁移"] = "Migrate",
        ["保存"] = "Save",
        ["开始预测"] = "Start Forecast",
        ["开始实况分析"] = "Start Nowcast",
        ["重新连接"] = "Reconnect",
        ["重新检查"] = "Re-check",
        ["下载缺失数据"] = "Download missing data",
        ["确定预测"] = "Confirm forecast",
        ["更新数据"] = "Update data",
        ["数据已齐，可开始预测"] = "Data ready — start the forecast",
        ["预测准备"] = "Forecast preparation",
        ["正在检查本地数据"] = "Checking local data",
        ["引擎未连接"] = "Engine offline",
        ["引擎未连接。"] = "Engine is offline.",
        ["需下载"] = "Download needed",
        ["已下载"] = "Downloaded",
        ["可更新"] = "Update available",
        ["运行中"] = "Running",
        ["取消中"] = "Cancelling",
        ["已取消"] = "Cancelled",
        ["失败"] = "Failed",
        ["已中断"] = "Interrupted",
        ["已完成"] = "Completed",
        ["等待预测"] = "Awaiting forecast",
        ["未完成"] = "Incomplete",
        ["预测完成"] = "Forecast complete",
        ["暂无结果"] = "No result",
        // 设置 → 数据
        ["下载源"] = "Data source",
        ["EC 下载源"] = "EC download source",
        ["ECMWF 官方"] = "ECMWF official",
        ["AWS 欧洲镜像"] = "AWS Europe mirror",
        ["Google Cloud 镜像"] = "Google Cloud mirror",
        ["EC 下载源同时用于下载探测、数据准备和预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS。"] =
            "The EC source is used for download probes, data preparation and forecasts. GFS uses NOAA NOMADS; CAMS uses Copernicus ADS.",
        ["CAMS · Copernicus ADS"] = "CAMS · Copernicus ADS",
        ["保存 Key"] = "Save key",
        ["粘贴 ADS Personal Access Token"] = "Paste ADS Personal Access Token",
        ["配置教程"] = "Setup guide",
        ["重新检查 CAMS 配置"] = "Re-check CAMS configuration",
        ["连通性"] = "Connectivity",
        ["同步设置"] = "Sync settings",
        ["启用后台同步"] = "Enable background sync",
        ["后台同步已启用"] = "Background sync enabled",
        ["同步间隔"] = "Sync interval",
        ["锚定区域"] = "Anchor region",
        ["GFS/EC 时效上限"] = "GFS/EC lead horizon",
        ["GFS 周期"] = "GFS cycles",
        ["EC 周期"] = "EC cycles",
        ["CAMS 周期"] = "CAMS cycles",
        ["存储与保留"] = "Storage & retention",
        ["保存同步设置"] = "Save sync settings",
        ["保存保留设置"] = "Save retention settings",
        ["已启用自定义保留策略"] = "Custom retention policy enabled",
        ["不限制"] = "Unlimited",
        ["立即同步"] = "Sync now",
        ["同步状态"] = "Sync status",
        ["已同步"] = "Synced",
        ["已是最新"] = "Up to date",
        ["从未同步"] = "Never synced",
        ["后台助手（关闭应用后仍同步）"] = "Background helper (syncs while the app is closed)",
        ["安装助手"] = "Install helper",
        ["卸载助手"] = "Uninstall helper",
        ["已安装并运行"] = "Installed and running",
        ["已安装（未加载）"] = "Installed (not loaded)",
        ["未安装"] = "Not installed",
        ["上次后台运行"] = "Last background run",
        ["下载数据"] = "Download data",
        // 数据管理
        ["本地数据"] = "Local data",
        ["刷新本地清单"] = "Refresh inventory",
        ["地形 SRTM"] = "Terrain SRTM",
        ["下载区域地形"] = "Download region terrain",
        ["仅当前地点"] = "Current spot only",
        ["刷新地形状态"] = "Refresh terrain status",
        ["开始下载"] = "Start download",
        ["取消下载"] = "Cancel download",
        ["正在停止…"] = "Stopping…",
        ["区域"] = "Region",
        ["已有本地瓦片"] = "Local tile exists",
        ["未下载"] = "Not downloaded",
        ["卫星数据"] = "Satellite data",
        ["目录"] = "Directories",
        ["删除该周期"] = "Delete this cycle",
        ["清空该数据源"] = "Clear this source",
        ["删除本地数据？"] = "Delete local data?",
        ["正在生成删除预览…"] = "Preparing delete preview…",
        ["软件更新"] = "Software update",
        ["检查更新"] = "Check for updates",
        ["下载并安装"] = "Download & install",
        ["发布说明"] = "Release notes",
        ["启动时自动检查更新"] = "Check for updates at launch",
        ["历史报告"] = "Past reports",
        ["报告地图"] = "Report map",
        ["数据目录"] = "Data directory",
        ["在访达中打开数据目录"] = "Open data directory in File Explorer",
        ["在文件资源管理器中打开数据目录"] = "Open data directory in File Explorer",
        ["迁移"] = "Migrate",
        ["迁移中…"] = "Migrating…",
        ["保留助手"] = "Keep helper",
        ["卸载"] = "Uninstall",
        ["本地缓存为空"] = "Local cache is empty",
        ["Rust 引擎"] = "Rust engine",
        ["留空使用内置引擎"] = "Leave empty to use the bundled engine",
        ["语言"] = "Language",
        ["界面语言"] = "Interface language",
        ["跟随系统 / System"] = "Follow system / 系统",
        ["中文"] = "中文",
        ["English"] = "English",
    };
}
