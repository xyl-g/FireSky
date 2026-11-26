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
            foreach (var (chineseKey, english) in Dictionary)
            {
                reverse.TryAdd(english, chineseKey);
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
        // 品牌 / 空态 / 跨端对齐
        ["朝晚霞观测台"] = "Twilight Workbench",
        ["观测工作台"] = "WORKBENCH",
        ["参数已变更 · 当前结果对应旧参数"] = "Parameters changed · showing previous result",
        ["表单改动不会自动重算，可按当前参数重新运行。"] = "Form edits do not auto-recalculate; rerun with current parameters.",
        ["按新参数重算"] = "Recalculate",
        ["实况窗口说明"] = "Nowcast Window",
        ["卫星实况仅适用距朝霞/晚霞 4 小时以内的当前观测窗口；远期日期请改用「单点预测」或「区域预测」。"] =
            "Satellite nowcast applies within 4 hours of the target dawn/dusk window; use Point or Regional Forecast for later dates.",
        ["实况地图提示"] = "Nowcast Map Notice",
        ["实况地图未发布"] = "Nowcast map not published",
        ["准备就绪，开始首次朝晚霞预测"] = "Ready for your first twilight forecast",
        ["预测完成后，这里会依次展示双模式行动结论、GFS 与 EC 独立指数、大气垂直剖面和逐时台账。"] =
            "Once complete, you will see the action summary, independent GFS/EC scores, atmospheric cross-section, and hourly ledger here.",
        ["准备就绪，开始卫星实况观测"] = "Ready for satellite nowcast",
        ["分析完成后，这里会展示卫星帧评分、云幕/纹理光学指标、后验云概率垂直剖面与逐时实况台账。"] =
            "Once complete, you will see the satellite frame score, cloud optical metrics, posterior profile, and hourly ledger here.",
        ["双模式独立对比"] = "Independent GFS & EC",
        ["GFS 0.25° 与 EC 公开数据各自评分（0–2.5），不平均、不互相填补。"] =
            "GFS 0.25° and EC Open Data scored independently on 0–2.5 without blending.",
        ["太阳方向大气剖面"] = "Solar Atmospheric Profile",
        ["沿太阳光路查看高中低云层、地形遮挡与理论受光高度。"] =
            "Inspect cloud layers, terrain masking, and illuminated altitudes along the solar path.",
        ["最佳时刻与窗口"] = "Best Time & Window",
        ["给出推荐到达时段、主要限制因素与模型云属代理。"] =
            "Recommended viewing window, limiting factors, and cloud genus proxy.",
        // 区域预设 / 地图图层
        ["将当前区域设为默认"] = "Set Current Region as Default",
        ["恢复默认区域"] = "Reset to Default Region",
        ["已将当前区域设为默认"] = "Saved current region as default",
        ["请至少选择一个预设区域后再设为默认。"] = "Select at least one preset region before setting as default.",
        ["平滑渲染"] = "Smooth rendering",
        ["网格线"] = "Grid lines",
        ["采样点"] = "Sample points",
        ["自定义范围"] = "Custom bounds",
        // 云属代理 / 诊断 / 台账
        ["卷云"] = "Cirrus",
        ["卷积云"] = "Cirrocumulus",
        ["卷层云"] = "Cirrostratus",
        ["高积云"] = "Altocumulus",
        ["高层云"] = "Altostratus",
        ["雨层云"] = "Nimbostratus",
        ["层积云"] = "Stratocumulus",
        ["层云"] = "Stratus",
        ["积云"] = "Cumulus",
        ["积雨云"] = "Cumulonimbus",
        ["依据压力层、相态与云层几何的模型云属代理，不代表卫星或人工云属观测。"] =
            "Model-based cloud genus proxy from pressure, phase, and layer geometry; not a satellite or human observation.",
        ["云属与相态（模型代理 · 不参与评分）"] = "Cloud Genus & Phase (Model Proxy · Non-scoring)",
        ["云属代理"] = "Cloud genus proxy",
        ["云属未定"] = "Genus undetermined",
        ["推断依据"] = "Inference basis",
        ["评分影响"] = "Scoring effect",
        ["不参与评分"] = "Not used in scoring",
        ["相态"] = "Phase",
        ["冰相成分"] = "Ice phase",
        ["液态水成分"] = "Liquid water",
        ["冰水混合成分"] = "Mixed phase",
        ["相态未知"] = "Unknown phase",
        ["冰含量占比"] = "Ice fraction",
        ["未解析（GFS 桌面点预测不解析目标云相态）"] = "Unresolved (GFS desktop point forecast does not resolve target cloud phase)",
        ["隐藏未判断时次"] = "Hide unevaluated",
        ["当前时段内没有有效判断时次。"] = "No valid evaluated time steps in this window.",
        ["云底–云顶为海拔 ASL 口径，「云层 AGL」为离地口径；— 表示缺测，默认仅展示有效判断时次；点击行仅切换本地选中时刻，不驱动地图。"] =
            "Cloud base–top are ASL; Cloud layer AGL is above ground. — indicates missing; unevaluated steps are hidden by default. Clicking a row selects time locally.",
        // 预检周期龄确认
        ["数据周期确认"] = "Data Cycle Confirmation",
        ["以下本地数据源尚未核验为最新发布周期；使用旧周期可立即出图，但临近起报切换时结果可能略有偏差。"] =
            "The datasets below are unverified or behind the latest published cycle. Continuing now uses local cache and may slightly differ near a cycle switch.",
        ["更新到最新数据"] = "Update to Latest",
        ["使用当前缓存继续"] = "Continue with Cache",
        ["使用当前本地缓存直接继续预测"] = "Continue forecasting directly with current local cache",
        ["返回"] = "Back",
        ["待核验"] = "Unverified",
        ["更新待核验"] = "Update unverified",
        ["有新发布的数据可更新"] = "Newer cycle available",
        ["发布周期已核验"] = "Cycle verified",
        ["本次预测数据不完整"] = "Incomplete local data",
        ["尚未下载本次预测数据"] = "Not yet downloaded",
        // 数据管理 / 保留策略 / 同步 / 更新
        ["立即按策略清理"] = "Clean up now",
        ["立即按已保存规则清理超出保留范围的旧文件（跳过占用中文件）"] =
            "Immediately prune old files beyond saved retention rules (skips busy files)",
        ["请先填写并保存至少一项保留规则"] = "Save at least one retention rule first",
        ["正在按保留策略清理旧数据…"] = "Applying retention policy…",
        ["尚未保存任何保留规则，未执行清理。"] = "No retention rules saved; nothing to clean.",
        ["保留设置已保存；可点击「立即按策略清理」，或等待下一次成功准备后自动清理。"] =
            "Retention policy saved; click Clean up now or wait for the next successful prepare.",
        ["已清空保留设置。"] = "Retention policy cleared.",
        ["已启用自定义保留策略。"] = "Custom retention policy enabled.",
        ["未启用自定义保留策略。"] = "Custom retention policy not enabled.",
        ["自动清理按周期、扫描或生成时间滚动保留；保存策略后可点击「立即按策略清理」立刻释放空间，后续也会在新数据校验成功后自动清理旧文件。占用中的文件会自动跳过。"] =
            "Auto-pruning retains items by cycle, scan, or creation time. Click Clean up now to reclaim space immediately, or wait for the next verified download. Busy files are skipped.",
        ["监控间隔 5–1440 分钟（建议设为 10–15 分钟轻量监控上游更新，仅检测到新起报周期才下载，无更新不占带宽）；时效上限仅 GFS/EC 需要（3–240 小时、3 的倍数），CAMS 自动拉齐全部时效；锚定区域决定 CAMS 洲包络与 GFS/EC 下载范围。"] =
            "Polling interval 5–1440 min (10–15 min recommended for lightweight upstream monitoring; downloads only when a newer cycle is published). Lead horizon applies to GFS/EC (3–240 h, multiples of 3).",
        ["已统计占用仅覆盖模式数据（GFS / EC / CAMS）；地形与卫星以目录为准。"] =
            "Storage totals cover model data (GFS / EC / CAMS); terrain and satellite follow disk folders.",
        ["完成一次预测或在设置中配置数据源后，这里会显示库存。"] =
            "Inventory will appear here after running a forecast or configuring sources in Settings.",
        ["预测结果与附加缓存"] = "Forecast Results & Auxiliary Data",
        ["计算过程中的中间产物（衍生张量、卫星解包帧、临时排行缓存）会在计算结束后立即自动删除，磁盘仅保留最终结果与原始气象/地形数据。"] =
            "Temporary intermediate artifacts (derived tensors, unpacked satellite bundles, ranking scratch) are deleted immediately after computation; only final results and raw meteorological/terrain data are kept.",
        ["单点预测结果缓存"] = "Point Forecast Cache",
        ["区域预测结果缓存"] = "Regional Forecast Results",
        ["地图渲染帧缓存"] = "Map Frame Cache",
        ["历史报告产物"] = "Forecast Reports",
        ["卫星实况帧（Himawari HSD）"] = "Satellite Frames (Himawari HSD)",
        ["SRTM 地形瓦片"] = "SRTM Terrain Tiles",
        ["按地点与起报周期复用单点计算结果"] = "Reused for matching point + model cycle",
        ["按区域范围、模式与周期复用计算结果"] = "Reused for matching region + model cycle",
        ["地图与剖面可视化中间帧"] = "Intermediate rendered frames",
        ["已归档预测报告与快照"] = "Archived forecast reports",
        ["葵花卫星多波段观测帧"] = "Himawari multi-band scan frames",
        ["离线高程与地形遮挡计算瓦片"] = "Offline elevation & horizon blocking tiles",
        ["在资源管理器中打开"] = "Open in File Explorer",
        ["确认估算后下载所选区域的 SRTM 瓦片（跳过已有；可随时取消续传）"] =
            "Estimate and download SRTM tiles for selected region (skips existing; resumable)",
        ["定向重试上次失败的 SRTM 瓦片，自动跳过已完成瓦片"] =
            "Retry only the failed SRTM tiles from the previous run",
        ["未在 GitHub Releases 页面找到有效版本标签"] = "Could not find a valid release tag on GitHub Releases",
        // v1.9.10 视觉重构：工作台分组 + 设置运行状态摘要
        ["观测参数"] = "Parameters",
        ["运行状态摘要"] = "Runtime Summary",
        ["管理数据源与同步"] = "Manage data & sync",
        ["本地引擎"] = "Local Engine",
        ["已连接"] = "Connected",
        ["未连接"] = "Offline",
        ["后台同步"] = "Background Sync",
        ["按需准备"] = "On-demand",
        ["已启用"] = "Enabled",
        ["已配置"] = "Configured",
        ["未配置"] = "Not configured",
        ["CAMS ADS"] = "CAMS ADS",
        ["保留策略"] = "Retention Policy",
        ["外观与语言"] = "Appearance & Language",
        ["位置与海拔"] = "Location & Elevation",
        ["海拔来源"] = "Elevation source",
        ["下载连通性"] = "Download Connectivity",
        ["后台同步中"] = "Syncing in background",
        ["退避冷却中（立即同步或重新保存设置可马上重试）"] =
            "Cooling down (run Sync Now or re-save settings to retry immediately)",
        ["浏览器下载"] = "Download in Browser",
        // v2.0.0 光谱色彩 & 方位雷达
        ["光谱色彩预测"] = "Spectral Color Prediction",
        ["天顶 → 地平线 4 段天空光谱渐变"] = "Zenith → Horizon 4-stop sky spectrum gradient",
        ["推荐拍摄方位"] = "Best Shooting Direction",
        ["色彩预测"] = "Color Prediction",
    };
}
