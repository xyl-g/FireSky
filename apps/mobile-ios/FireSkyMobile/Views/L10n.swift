import Foundation

/// 界面多语言（v1.9.5）：轻量键值查找，支持中文（默认）/ English。
/// 语言偏好持久化到 UserDefaults（键 appLanguage，macOS/Android 同契约）；
/// "system" 跟随系统首选语言。覆盖范围渐进扩展——未覆盖的 key 回落中文。
enum L10n {

    // MARK: - 语言

    enum Language: String, CaseIterable, Identifiable {
        case system = "system"
        case chinese = "zh"
        case english = "en"
        var id: String { rawValue }

        var label: String {
            switch self {
            case .system: return "跟随系统 / System"
            case .chinese: return "中文"
            case .english: return "English"
            }
        }
    }

    static var language: Language {
        get {
            if let raw = UserDefaults.standard.string(forKey: "appLanguage"),
               let lang = Language(rawValue: raw) {
                return lang
            }
            return .system
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: "appLanguage") }
    }

    /// 当前生效语言（system 时探测系统首选语言）。
    static var effective: Language {
        switch language {
        case .chinese, .english: return language
        case .system:
            let preferred = Locale.preferredLanguages.first ?? "zh"
            return preferred.hasPrefix("zh") ? .chinese : .english
        }
    }

    private static var isEnglish: Bool { effective == .english }

    // MARK: - 通用查找

    /// 键值查找：中文 key → 当前语言文案；English 未覆盖时回落中文。
    static func text(_ chinese: String, _ english: String? = nil) -> String {
        guard isEnglish, let en = english else { return chinese }
        return en
    }

    // MARK: - 高频字符串

    // 导航
    static var navPoint: String { text("单点预测", "Point Forecast") }
    static var navData: String { text("数据管理", "Data") }
    static var navHistory: String { text("任务历史", "History") }
    static var navSettings: String { text("设置", "Settings") }

    // 通用按钮
    static var start: String { text("开始预测", "Start Forecast") }
    static var cancel: String { text("取消", "Cancel") }
    static var delete: String { text("删除", "Delete") }
    static var refresh: String { text("刷新", "Refresh") }
    static var reconnect: String { text("重新连接", "Reconnect") }
    static var reset: String { text("重置", "Reset") }
    static var loadOlder: String { text("加载更早任务", "Load older tasks") }

    // 状态
    static func status(_ chinese: String) -> String {
        guard isEnglish else { return chinese }
        switch chinese {
        case "已完成": return "Completed"
        case "需下载": return "Download needed"
        case "可更新": return "Update available"
        case "已下载": return "Downloaded"
        case "运行中": return "Running"
        case "取消中": return "Cancelling"
        case "已取消": return "Cancelled"
        case "失败": return "Failed"
        case "已中断": return "Interrupted"
        case "等待预测": return "Awaiting forecast"
        case "未完成": return "Incomplete"
        case "预测完成": return "Forecast complete"
        case "暂无结果": return "No result"
        case "发布周期已核验": return "Publication verified"
        case "更新待核验": return "Update pending verification"
        case "有新发布的数据可更新": return "Newer data available"
        case "非最新发布周期": return "Not the latest cycle"
        case "本次预测数据不完整": return "Forecast data incomplete"
        case "尚未下载本次预测数据": return "Forecast data not downloaded"
        default: return chinese
        }
    }

    // 分类
    static var catGeneral: String { text("通用", "General") }
    static var catData: String { text("数据", "Data") }
    static var catAdvanced: String { text("高级", "Advanced") }

    private static let dictionary: [String: String] = [
        // 设置分类 / 通用
        "通用": "General",
        "数据": "Data",
        "高级": "Advanced",
        "主题": "Theme",
        "跟随系统": "Follow system",
        "浅色": "Light",
        "深色": "Dark",
        "取消": "Cancel",
        "确定": "OK",
        "刷新": "Refresh",
        "删除": "Delete",
        "重试": "Retry",
        "保留": "Keep",
        "迁移": "Migrate",
        "慢": "Slow",
        "天": "d",
        "小时": "h",
        "分钟": "min",
        "个": "files",
        "同步": "Sync",
        "慢速": "Slow",
        // 软件更新
        "软件更新": "Software update",
        "检查更新": "Check for updates",
        "下载并安装": "Download & install",
        "发布说明": "Release notes",
        "下载中…": "Downloading…",
        "下载 DMG、校验 SHA256 后自动安装到「应用程序」并重启":
            "Downloads the DMG, verifies SHA256, installs into Applications and relaunches",
        "启动时自动检查更新": "Check for updates at launch",
        "正在安装，完成后应用将自动重启。":
            "Installing; the app will relaunch automatically when done.",
        "当前使用旧版隐藏目录布局（.cache / .firesky）。":
            "Currently using the legacy hidden-directory layout (.cache / .firesky).",
        "已使用可见目录布局（gfs/、reports/ 等直接位于工作区下）。":
            "Using the visible-directory layout (gfs/, reports/ etc. directly under the workspace).",
        "迁移到可见目录": "Migrate to visible directories",
        "迁移到可见目录布局？": "Migrate to the visible-directory layout?",
        "迁移会把数据移动到工作区下的可见目录（gfs/、ecmwf/、cams/、reports/ 等），旧目录残壳保留，可随时确认后手动删除。迁移期间会暂停任务与同步。":
            "Data moves into visible directories under the workspace (gfs/, ecmwf/, cams/, reports/ etc.); old directory shells are kept for manual deletion. Tasks and sync pause during migration.",
        "迁移数据到工作区可见目录并重连引擎":
            "Migrate data into the visible workspace layout and reconnect the engine",
        "迁移中…": "Migrating…",
        "没有检测到旧布局数据（.cache / .firesky），迁移将只写入布局标记。":
            "No legacy layout data detected (.cache / .firesky); migration will only write the layout marker.",
        "有任务或同步正在进行，完成后再迁移":
            "A task or sync is running; migrate after it finishes",
        "数据固定保存在 macOS 系统数据目录（可在访达中前往）。开发/运维可用 FIRESKY_WORKSPACE 环境变量覆盖。":
            "Data lives in the macOS system data directory (open it in Finder). Developers can override via FIRESKY_WORKSPACE.",
        "在访达中打开数据目录": "Open data directory in Finder",
        "在访达中打开": "Reveal in Finder",
        "数据目录": "Data directory",
        "数据目录未设置": "Data directory not set",
        "目录": "Directories",
        "自定义引擎路径仅供开发调试；日常使用保持留空即可。":
            "A custom engine path is for development only; leave it empty for daily use.",
        "留空使用内置引擎": "Leave empty to use the bundled engine",
        "Rust 引擎": "Rust engine",
        "历史报告": "Past reports",
        "报告地图": "Report map",
        "卫星数据": "Satellite data",
        "连通性": "Connectivity",
        "下载数据": "Download data",
        "需先补齐本次数据": "Download the missing data first",
        "数据已齐，可开始预测": "Data ready — start the forecast",
        "数据已齐，可开始分析": "Data ready — start the analysis",
        "数据已齐，可更新 %@": "Data ready — can update %@",
        "更新数据": "Update data",
        "返回更新数据": "Back and update data",
        "返回并点击「更新数据」获取最新周期": "Go back and tap \u{201c}Update data\u{201d} for the latest cycle",
        "确定预测": "Confirm forecast",
        "确定分析": "Confirm analysis",
        "下载缺失数据": "Download missing data",
        "继续预测": "Forecast anyway",
        "预测准备": "Forecast preparation",
        "实况准备": "Nowcast preparation",
        "数据周期确认": "Cycle confirmation",
        "正在检查本地数据": "Checking local data",
        "引擎未连接。": "Engine is offline.",
        // 下载源 / CAMS
        "下载源": "Data source",
        "EC 下载源": "EC download source",
        "ECMWF 官方": "ECMWF official",
        "AWS 欧洲镜像": "AWS Europe mirror",
        "Google Cloud 镜像": "Google Cloud mirror",
        "EC 下载源同时用于下载探测、数据准备和预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS。":
            "The EC source is used for download probes, data preparation and forecasts. GFS uses NOAA NOMADS; CAMS uses Copernicus ADS.",
        "重新连接": "Reconnect",
        "重新连接引擎": "Reconnect engine",
        "重新检查": "Re-check",
        "重新检查 CAMS 配置": "Re-check CAMS configuration",
        "保存 Key": "Save key",
        "粘贴 ADS Personal Access Token": "Paste ADS Personal Access Token",
        "仅以明文保存在本机配置文件，文件权限 0600；不会写入工程或显示已有 Key。":
            "Stored in plain text in a local file with 0600 permissions; never written to the repo or displayed back.",
        "配置教程": "Setup guide",
        "注册并登录 ADS": "Register and sign in to ADS",
        "使用 ECMWF 账号登录 Atmosphere Data Store。":
            "Sign in to the Atmosphere Data Store with your ECMWF account.",
        "复制 Personal Access Token": "Copy the Personal Access Token",
        "在官方 API 配置页复制 key 后面的 Token，不要复制 url 或整段配置。":
            "Copy the token after the key on the official API page — not the url or the whole config.",
        "获取 Token": "Get token",
        "接受 CAMS 数据集条款": "Accept the CAMS dataset terms",
        "在数据集 Download 页底部接受 Terms of Use，完成网页端授权。":
            "Accept the Terms of Use at the bottom of the dataset Download page to authorize.",
        "打开 ADS": "Open ADS",
        "打开数据集": "Open dataset",
        "返回 FireSky 保存 Key": "Return to FireSky and save the key",
        "粘贴到上方输入框并保存，再到预测准备窗口下载数据。":
            "Paste it above and save, then download data from the forecast preparation sheet.",
        "环境变量正在覆盖配置。修改 CDSAPI_KEY / CDSAPI_URL 后需重新启动应用。":
            "Environment variables override the configuration. Restart the app after changing CDSAPI_KEY / CDSAPI_URL.",
        // 同步
        "同步设置": "Sync settings",
        "启用后台同步": "Enable background sync",
        "后台同步已启用": "Background sync enabled",
        "同步间隔": "Sync interval",
        "锚定区域": "Anchor region",
        "GFS/EC 时效上限": "GFS/EC lead horizon",
        "GFS 周期": "GFS cycles",
        "EC 周期": "EC cycles",
        "CAMS 周期": "CAMS cycles",
        "存储与保留": "Storage & retention",
        "保存同步设置": "Save sync settings",
        "保存保留设置": "Save retention settings",
        "已启用自定义保留策略": "Custom retention policy enabled",
        "未配置缓存保留策略，缓存将随同步增长；可在下方「存储与保留」中设置。":
            "No retention policy configured; the cache grows with sync. Set it under Storage & retention below.",
        "自动清理按周期、扫描或生成时间滚动保留；新数据校验成功后才会清理旧文件。留空表示不自动清理。":
            "Retention rolls by cycle, scan or generation time; old files are removed only after new data validates. Leave empty to disable.",
        "不限制": "Unlimited",
        "立即同步": "Sync now",
        "立即同步已启用的数据源（可解除失败熔断）":
            "Syncs the enabled sources now (also clears a failure suspension)",
        "同步状态": "Sync status",
        "同步": "Sync",
        "先启用并保存后台同步": "Enable and save background sync first",
        "已同步": "Synced",
        "已是最新": "Up to date",
        "失败": "Failed",
        "已取消": "Cancelled",
        "从未同步": "Never synced",
        "已暂停：立即同步或重新保存设置可解锁":
            "Suspended: Sync now or re-save settings to unlock",
        "后台助手（关闭应用后仍同步）": "Background helper (syncs while the app is closed)",
        "安装为系统 launchd 代理，按同步间隔在后台自动拉齐最新已发布周期。":
            "Installs a launchd agent that pulls the latest published cycles in the background.",
        "安装助手": "Install helper",
        "卸载助手": "Uninstall helper",
        "卸载后台同步助手？": "Uninstall the background sync helper?",
        "卸载后，关闭应用时的后台同步将停止；已下载的数据会保留，可随时重新安装助手。":
            "Background sync stops while the app is closed; downloaded data is kept and the helper can be reinstalled anytime.",
        "卸载": "Uninstall",
        "保留助手": "Keep helper",
        "已安装并运行": "Installed and running",
        "已安装（未加载）": "Installed (not loaded)",
        "未安装": "Not installed",
        "当前为源码运行环境，后台助手仅在正式 FireSky 安装包中可用。":
            "Source-run environment: the background helper ships only with the official FireSky bundle.",
        "上次后台运行": "Last background run",
        "卸载前最后一次后台运行": "Last background run before uninstall",
        "后台同步与下载探测已移到「设置 → 数据」":
            "Background sync and download probes moved to Settings → Data",
        // 数据管理 / 库存
        "本地数据": "Local data",
        "刷新本地清单": "Refresh inventory",
        "卫星数据": "Satellite data",
        "已统计占用仅覆盖模式数据（GFS / EC / CAMS）；地形与卫星以目录为准。":
            "Usage stats cover model data (GFS / EC / CAMS); terrain and satellite are per-directory.",
        "完成一次预测或在设置中配置数据源后，这里会显示库存。":
            "Inventory appears after a forecast or once sources are configured in Settings.",
        "删除该周期": "Delete this cycle",
        "清空该数据源": "Clear this source",
        "删除本地数据？": "Delete local data?",
        "正在生成删除预览…": "Preparing delete preview…",
        // 地形
        "地形 SRTM": "Terrain SRTM",
        "下载区域地形": "Download region terrain",
        "下载区域": "Download region",
        "仅当前地点": "Current spot only",
        "只下载当前地点对应的一块瓦片": "Downloads only the tile for the current spot",
        "刷新地形状态": "Refresh terrain status",
        "开始下载": "Start download",
        "确认估算后下载所选区域的 SRTM 瓦片（跳过已有；可随时取消续传）":
            "Estimates first, then downloads the region's SRTM tiles (skips existing; resumable)",
        "全球瓦片下载后会覆盖任意地点，无需再按点下载。":
            "Once global tiles are downloaded, any location is covered — no per-spot download.",
        "显示本地已有地形瓦片。": "Shows local terrain tiles.",
        "已有本地瓦片": "Local tile exists",
        "未下载": "Not downloaded",
        "下载全球地形数据？": "Download global terrain?",
        "当前纬度不在 SRTM 覆盖范围（-56 ≤ 纬度 < 60）。":
            "Current latitude is outside SRTM coverage (-56 ≤ lat < 60).",
        "校验瓦片数据…": "Verifying tile data…",
        "本地缓存为空": "Local cache is empty",
        "按抽样估算体积约 %.0f GB（抽样混合了海洋小瓦片与陆地大瓦片，实际体积以运行时用量为准）。":
            "Estimated size about %.0f GB by sampling (samples mix small ocean tiles and large land tiles; actual usage applies).",
        "暂时无法估算体积（网络或服务不可用），开始后会显示实际用量。":
            "Size cannot be estimated right now (network or service unavailable); actual usage shows once started.",
        // 格式化模板（tf）：与 Android values-en 同口径
        "需下载 %@": "Download needed: %@",
        "数据已齐，可更新 %@": "Data ready — can update %@",
        "删除 %@ 的周期 %@：共 %d 个文件，预计释放 %.1f MB。占用中的文件会跳过；报告与任务记录不受影响。":
            "Deletes cycle %@ of %@: %d files, about %.1f MB. Files in use are skipped; reports and task records are unaffected.",
        "清空 %@ 的全部本地数据：共 %d 个文件，预计释放 %.1f MB。占用中的文件会跳过。":
            "Deletes all local data of %@: %d files, about %.1f MB. Files in use are skipped.",
        "当前有 %d 个文件被下载任务占用，将跳过。":
            "%d files are held by download tasks and will be skipped.",
        "下载%@地形数据？": "Download %@ terrain?",
        "%.0f%% · 已下载 %d/%d · 已有 %d · 缺失 %d · 失败 %d":
            "%.0f%% · downloaded %d/%d · existing %d · missing %d · failed %d",
        "当前 %@": "Current %@",
        "本地已有 %d 块瓦片。": "%d local tiles.",
        "本地瓦片：%@": "Local tiles: %@",
        "目标 %@（%@）· 瓦片 %@ · %@": "Target %@ (%@) · tile %@ · %@",
        "将下载%@的 SRTM 瓦片：候选 %d 块，本地已有 %d 块会自动跳过（海洋上的瓦片是很小的空值瓦片，同样会下载）。下载耗时较长且占用磁盘，可随时取消，重新开始会跳过已下载瓦片。":
            "Downloads the SRTM tiles of %@: %d candidates; %d already local are skipped automatically (ocean tiles are tiny null tiles and are also downloaded). The download takes a while and uses disk; it can be cancelled anytime and resumes by skipping downloaded tiles.",
        "当前版本 %@": "Current version %@",
        "发现新版本 %@，可在下载后自动安装。":
            "Version %@ is available; it can install automatically after download.",
        "已下载 %@ / %@": "Downloaded %@ / %@",
        " 等 %d 项": " and %d more",
        "将迁移 %d 个文件（共 %.1f MB）：%@%@。迁移使用同卷移动，失败自动回滚；迁移完成后引擎将按新布局重连。":
            "Migrates %d files (%.1f MB): %@%@. Same-volume moves with automatic rollback; the engine reconnects on the new layout afterwards.",
        "最近周期 %@": "Latest cycle %@",
        "上次同步 %@": "Last synced %@",
        "最近错误：%@": "Last error: %@",
        "缺 %d 个周期：%@": "Missing %d cycles: %@",
        "连续失败 %d 次": "%d consecutive failures",
    ]

    /// 集中英译表查找（与 macOS 字典同源；未收录回落原文）。
    static func t(_ chinese: String) -> String {
        guard isEnglish, let en = dictionary[chinese] else { return chinese }
        return en
    }

    /// 格式化模板查找：模板含 %@/%d 占位。
    static func tf(_ template: String, _ args: CVarArg...) -> String {
        let format = isEnglish ? (dictionary[template] ?? template) : template
        return String(format: format, arguments: args)
    }
}
