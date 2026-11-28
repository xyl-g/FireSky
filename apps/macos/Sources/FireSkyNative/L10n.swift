import Foundation

/// 界面多语言（v1.10.0）：轻量键值查找，支持中文（默认）/ English。
/// 语言偏好持久化到 UserDefaults；"system" 跟随系统首选语言。
/// 覆盖范围渐进扩展——未覆盖的 key 回落中文原文。
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

    /// 集中英译表查找（v1.9.7 设置/数据页全量接入）：key = 中文原文，
    /// 与 Android values-en 口径一致；未收录回落原文。
    static func t(_ chinese: String) -> String {
        guard isEnglish, let en = dictionary[chinese] else { return chinese }
        return en
    }

    /// 格式化模板查找：模板含 %d/%@ 占位（调用点已把插值改写为格式化）。
    static func tf(_ template: String, _ args: CVarArg...) -> String {
        let format = isEnglish ? (dictionary[template] ?? template) : template
        return String(format: format, arguments: args)
    }

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
        "海拔": "Elevation",
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
        "下载前先知道": "Before you download",
        "首次下载耗时取决于本机缺失的数据集与网络。上方只列本次预测所需文件；已下载文件不会重复下载，过程中可取消，失败项会保留并提示重试。":
            "First-download time depends on which datasets are missing and your network. The list above covers only this forecast; existing files are not downloaded twice. You can cancel, and failed items stay listed for retry.",
        "CAMS Key 未配置时仍可使用本地缓存；如需气溶胶数据，可在设置中配置后重新预检。":
            "Local caches remain usable without a CAMS key. Configure it in Settings and re-check if you need aerosol data.",
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
        "自动清理按周期、扫描或生成时间滚动保留；保存策略后可点击「立即按策略清理」立刻释放空间，后续也会在新数据校验成功后自动清理旧文件。占用中的文件会自动跳过。":
            "Retention rolls by cycle, scan, or generation time. Click Clean up now after saving to reclaim space immediately, or let it run after the next validated download. Busy files are skipped automatically.",
        "不限制": "Unlimited",
        "立即同步": "Sync now",
        "立即同步已启用的数据源（可解除失败熔断）":
            "Syncs the enabled sources now (also clears a failure suspension)",
        "同步状态": "Sync status",
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
        // 点预测工作台 / 首次使用
        "观测工作台": "Forecast workspace",
        "显示侧栏": "Show sidebar",
        "隐藏侧栏": "Hide sidebar",
        "取消当前任务": "Cancel current task",
        "观测点": "Observation point",
        "地图": "Map",
        "收藏": "Favorites",
        "管理收藏位置": "Manage favorites",
        "还没有收藏位置": "No saved places",
        "搜索地名，如「西湖」或「杭州」": "Search for a place, such as West Lake or Hangzhou",
        "搜索位置": "Search location",
        "获取当前位置": "Use current location",
        "搜索或在地图上选好地点后，点击下方「收藏当前点」。":
            "Search for a place or choose one on the map, then save it below.",
        "删除收藏": "Remove favorite",
        "点击切换到此位置；右键可删除收藏": "Select this place; right-click to remove it",
        "收藏当前点": "Save this place",
        "大地图": "Open map",
        "位置已确认 · 海拔 %d m · %@": "Location confirmed · %d m elevation · %@",
        "位置待确认 · 展开「编辑坐标」获取海拔与时区":
            "Location needs confirmation · Edit coordinates to look up elevation and time zone",
        "位置已确认，海拔 %d 米，时区 %@": "Location confirmed, elevation %d m, time zone %@",
        "位置待确认，请展开编辑坐标获取海拔与时区":
            "Location needs confirmation; edit coordinates to look up elevation and time zone",
        "海拔来源设置：%@；实际来源：%@": "Elevation source setting: %@; actual source: %@",
        "确认海拔与时区后才能收藏该点并开始预测。":
            "Confirm elevation and time zone before saving this place or starting a forecast.",
        "观测参数": "Forecast settings",
        "重置": "Reset",
        "目标日期": "Target date",
        "网格": "Grid",
        "城市": "Cities",
        "今天": "Today",
        "明天": "Tomorrow",
        "自定义": "Custom",
        "选择目标日期": "Choose target date",
        "目标日期 %@": "Target date %@",
        "上个月": "Previous month",
        "下个月": "Next month",
        "关闭日历": "Close calendar",
        "推荐窗口 %@": "Recommended window %@",
        "观测现象": "Observation",
        "朝霞": "Dawn glow",
        "晚霞": "Dusk glow",
        "编辑坐标": "Edit coordinates",
        "名称": "Name",
        "纬度": "Latitude",
        "经度": "Longitude",
        "时区": "Time zone",
        "时区按经纬度自动计算": "Time zone is determined from coordinates",
        "获取海拔与时区": "Look up elevation and time zone",
        "已确认海拔与时区": "Elevation and time zone confirmed",
        "地图选点": "Choose a place on the map",
        "首次预测 · 四步完成": "First forecast · four steps",
        "每一步都可稍后调整": "You can change these settings later",
        "选好地点": "Choose a place",
        "搜索、地图选点或选择收藏点": "Search, pick on the map, or choose a favorite",
        "打开地图": "Open map",
        "确认位置": "Confirm location",
        "海拔与时区已确认": "Elevation and time zone confirmed",
        "获取并确认海拔与时区": "Look up and confirm elevation and time zone",
        "调整坐标": "Edit coordinates",
        "数据源": "Data sources",
        "CAMS 已配置": "CAMS is configured",
        "CAMS 可选，仍可使用本地数据": "CAMS is optional; local data can still be used",
        "查看配置": "View settings",
        "配置 CAMS": "Set up CAMS",
        "AOD 数据来源": "AOD data source",
        "NASA GEOS-FP（免密钥直连）": "NASA GEOS-FP (No key required)",
        "GEOS-FP · NASA 免密钥": "GEOS-FP · NASA no-key",
        "切换来源": "Switch source",
        "EC 下载源与 AOD 数据来源同时用于下载探测、数据准备和预测。GFS 使用 NOAA NOMADS；CAMS 需 ADS Key，GEOS-FP 免密钥直连。":
            "The EC and AOD sources are used for download probes, data preparation and forecasts. GFS uses NOAA NOMADS; CAMS needs an ADS key, while GEOS-FP connects without a key.",
        "当前 AOD 数据来源为 NASA GEOS-FP（免密钥），此处仅在切换回 CAMS 时需要配置。":
            "AOD source is currently set to NASA GEOS-FP (no key required); configure CAMS only if you switch back to CAMS.",
        "预检与下载": "Check data and download",
        "逐项确认缺失数据，过程中可取消": "Review missing data; downloads can be cancelled",
        "数据预检": "Check data",
        "提示：首次下载量取决于本机缺失的数据集；预检会列出本次所需文件与状态，下载后可随时重新预检。":
            "The first download depends on which datasets are missing. The check lists the files needed for this forecast; you can run it again after downloading.",
        "首次下载量取决于本机缺失的数据集；预检会列出本次所需文件与状态，下载后可随时重新预检。":
            "The first download depends on which datasets are missing. The check lists the files needed for this forecast; you can run it again after downloading.",
        // 行动摘要 / 结果分层
        "行动摘要": "Decision summary",
        "模式结果": "Model results",
        "概览": "Overview",
        "大气剖面": "Atmospheric profile",
        "诊断与时间窗": "Diagnostics & timing",
        "逐时台账": "Hourly ledger",
        "最佳时刻": "Best time",
        "指数 %@ / 2.5": "Index %@ / 2.5",
        "完成一次预测后，这里会给出是否值得外出、最佳时刻和主要限制。":
            "Run a forecast to see whether conditions are worth a trip, the best time, and key limitations.",
        "两个模式都未完成，暂不能给出观测建议。": "Neither model completed, so no recommendation is available yet.",
        "未知原因": "Unknown reason",
        "收藏该地点": "Save this place",
        "起报 %@": "Run %@",
        "尚未运行预测；完成一次预测后显示主导可烧压力族。":
            "No forecast has run yet. The dominant burnable-cloud pressure family appears after a forecast.",
        "主导可烧压力族（来自本次预测结果）":
            "Dominant burnable-cloud pressure family from this forecast",
        "无可烧目标云": "No burnable target cloud",
        "高云压力族": "High-cloud pressure family",
        "中云压力族": "Mid-level cloud pressure family",
        "低云压力族": "Low-cloud pressure family",
        "不可烧云挡光": "Non-burnable cloud blocking sunlight",
        "无可烧云": "No burnable cloud",
        "太阳入射阻挡已进入可烧云面积": "Solar obstruction is included in burnable-cloud area",
        "云质与垂直厚度": "Cloud structure and vertical depth",
        "太阳入射 AOD 削光": "AOD reduces incoming sunlight",
        "观测光路 AOD 降低对比度": "AOD reduces contrast along the viewing path",
        "可烧云绝对量": "Burnable-cloud amount",
        "当前结果对应旧参数，可点「按新参数重算」更新。":
            "This result uses earlier settings. Recalculate to update it.",
        "当前有任务正在进行（%@），完成或取消后可开始新预测。":
            "A task is already running (%@). Finish or cancel it before starting another forecast.",
        "引擎未连接，请先在左下角重新连接。": "Engine is offline. Reconnect from the lower-left corner.",
        "先预检本地数据，确认后开始预测。": "Check local data before starting the forecast.",
        "预热卫星与数据": "Prepare satellite and forecast data",
        "预测结果": "Forecast result",
        "主压力族": "Dominant pressure family",
        "结论：%@": "Conclusion: %@",
        "时间与窗口：%@": "Timing and window: %@",
        "导出摘要": "Export summary",
        "保存为文本文件": "Save as text",
        "复制摘要": "Copy summary",
        "把当前观测点加入收藏": "Save the current place to Favorites",
        "请先确认海拔与时区，再收藏该地点": "Confirm elevation and time zone before saving this place",
        "云况与色彩": "Clouds & color",
        "大气光学": "Atmospheric optics",
        "时效与可靠性": "Freshness & reliability",
        "有效可烧云量": "Visible burnable cloud",
        "前向可烧云占视野比例": "Visible burnable cloud coverage",
        "云质厚度": "Cloud depth",
        "云体材质负载": "Cloud material load",
        "亮度色彩": "Brightness & color",
        "霞光亮度与色彩评分": "Glow brightness and color score",
        "主要云层": "Main cloud layer",
        "云底–云顶 AGL": "Cloud base–top AGL",
        "气溶胶光学厚度 · 通透度": "Aerosol optical depth · clarity",
        "入射受光": "Incoming illumination",
        "太阳入射照亮云体的因子": "Sunlight reaching the cloud",
        "返程对比度": "Viewing-path contrast",
        "观测者视线大气对比因子": "Atmospheric contrast along the viewing path",
        "持续时长": "Duration",
        "核心观赏窗口 · 分钟": "Core viewing window · minutes",
        "输入支持度": "Input support",
        "输入覆盖与几何支持 · 非命中概率": "Input and geometry support · not a hit probability",
        "对流风险": "Convective risk",
        "模式对流风险指标 · 非事件概率": "Model convective-risk indicator · not event probability",
        "评分组成": "Score breakdown",
        "只读展示后端计算值 · 不在客户端重算": "Backend values shown as reported; no client-side recalculation",
        "诊断": "Diagnostics",
        "详情与口径": "Details & methodology",
        "查看技术诊断与时间窗元数据": "View technical diagnostics & timing metadata",
        "评分项": "Metric",
        "入射遮挡": "Solar obstruction",
        "已计入可烧量": "Included in burnable",
        "外推窗外/未判断": "Outside solar window",
        "刷新冻结剖面": "Reload frozen profile",
        "太阳方向卫星剖面": "Solar-path satellite profile",
        "按时次查询冻结剖面": "Inspect frozen profile by step",
        "运动与采样": "Motion & sampling",
        "隐藏详情": "Hide details",
        "起报": "Run",
        "预测完成": "Forecast complete",
        "等待预测": "Waiting for forecast",
        "暂无结果": "No result",
        "未预测": "Not forecast",
        "按新参数重算": "Recalculate with current settings",
        "当前有任务正在进行，完成或取消后可开始新预测。":
            "A task is running. Wait for it to finish or cancel it before starting another forecast.",
        "引擎未连接，请重新连接后再开始。": "Reconnect the engine before starting a forecast.",
        // 预检窗口
        "预检数据": "Check data",
        "%@ 的发布周期未核验或不是最新发布。可以继续预测（结果可能略有偏差），也可以返回并点击「更新数据」获取最新周期。":
            "The publication cycle is unverified or not the latest. You can continue with a possible accuracy impact, or go back and update the data.",
        "本次预测的必需数据": "Data required for this forecast",
        // 格式化模板
        "仅 %@ 可用：%@": "Only %@ available: %@",
        "两个模式结论一致：%@": "Both models agree: %@",
        "两个模式结论不同：GFS %@ · EC %@": "Models differ: GFS %@ · EC %@",
        "最佳 %@ · 推荐窗口 %@": "Best time %@ · recommended window %@",
        "%@ 最佳 %@ · 窗口 %@": "%@ best %@ · window %@",
        "%@ 未完成：%@": "%@ incomplete: %@",
        "%@ 暂无结果": "%@ has no result yet",
        "%@ 主要限制：%@": "%@ main limitation: %@",
        // 旧参数提示 / 逐时台账 / 区域 / 实况
        "参数已变更 · 当前结果对应旧参数": "Parameters changed · showing previous result",
        "时间": "Time",
        "质量": "Quality",
        "档位": "Level",
        "可烧": "Burnable",
        "可烧云量": "Burnable cloud",
        "主导压力族": "Pressure family",
        "云底–云顶 ASL": "Base–top ASL",
        "云层 AGL": "Layer AGL",
        "亮度颜色": "Brightness",
        "可烧比例": "Burn ratio",
        "回程透过率": "Return trans.",
        "入射阻挡": "Solar block",
        "太阳仰角": "Solar elev.",
        "场景": "Regime",
        "预测区域": "Region",
        "绘制范围": "Draw bounds",
        "取消绘制": "Cancel draw",
        "自定义范围": "Custom bounds",
        "质量指数": "Quality index",
        "缺测": "Missing",
        "高值排行": "Top cells",
        "城市排名": "City ranking",
        "搜索网格": "Search grid",
        "搜索城市": "Search city",
        "网格详情": "Cell detail",
        "卫星实况": "Satellite nowcast",
        "等待分析": "Waiting for analysis",
        "分析完成": "Analysis complete",
        "卫星云况": "Satellite clouds",
        "运动采样": "Motion sampling",
        "光学与雷达": "Optics & radar",
        "后台同步中": "Syncing in background",
        "退避冷却中 · 预计 %@ 自动重试（立即同步可马上重试）":
            "Cooling down · auto retry at %@ (run Sync Now to retry immediately)",
        "退避冷却中（立即同步或重新保存设置可马上重试）":
            "Cooling down (run Sync Now or re-save settings to retry immediately)",
    ]

    // MARK: - 高频字符串

    static var appName: String { text("FireSky") }

    // 导航
    static var navWorkbench: String { text("单点预测", "Point Forecast") }
    static var navRegional: String { text("区域预测", "Regional") }
    static var navNowcast: String { text("实况观测", "Nowcast") }
    static var navData: String { text("数据管理", "Data") }
    static var navHistory: String { text("任务历史", "History") }
    static var navSettings: String { text("设置", "Settings") }

    // 通用按钮
    static var start: String { text("开始预测", "Start Forecast") }
    static var cancel: String { text("取消", "Cancel") }
    static var delete: String { text("删除", "Delete") }
    static var save: String { text("保存", "Save") }
    static var refresh: String { text("刷新", "Refresh") }
    static var close: String { text("关闭", "Close") }
    static var retry: String { text("重试", "Retry") }
    static var confirm: String { text("确定", "Confirm") }

    // 状态
    static func status(_ chinese: String) -> String {
        guard isEnglish else { return chinese }
        switch chinese {
        case "已完成": return "Completed"
        case "需下载": return "Download needed"
        case "可更新": return "Update available"
        case "已下载": return "Downloaded"
        case "待核验": return "Unverified"
        case "运行中": return "Running"
        case "取消中": return "Cancelling"
        case "已取消": return "Cancelled"
        case "失败": return "Failed"
        case "已中断": return "Interrupted"
        case "发布周期已核验": return "Publication verified"
        case "更新待核验": return "Update pending verification"
        case "有新发布的数据可更新": return "Newer data available"
        case "非最新发布周期": return "Not the latest cycle"
        case "本次预测数据不完整": return "Forecast data incomplete"
        case "尚未下载本次预测数据": return "Forecast data not downloaded"
        case "未完成": return "Incomplete"
        case "预测完成": return "Forecast complete"
        case "等待预测": return "Waiting for forecast"
        case "暂无结果": return "No result"
        case "未预测": return "Not forecast"
        default: return chinese
        }
    }

    static func qualityLevel(_ value: String) -> String {
        guard isEnglish else { return value }
        switch value {
        case "无明显火烧云": return "No prominent burnable clouds"
        case "峰值质量指数": return "Peak quality index"
        case "微微烧": return "Very faint glow"
        case "小烧": return "Faint glow"
        case "小烧到中等烧": return "Faint to moderate glow"
        case "中等烧": return "Moderate glow"
        case "中等烧到大烧": return "Moderate to strong glow"
        case "不完美大烧": return "Strong glow, with limitations"
        case "典型大烧": return "Typical strong glow"
        case "优质大烧": return "Excellent glow"
        case "世纪大烧": return "Exceptional glow"
        case "测试峰值档位": return "Test peak category"
        case "测试事件档位": return "Test event category"
        case "测试档位": return "Test category"
        default: return value
        }
    }

    static func qualityLimit(_ value: String) -> String {
        guard isEnglish else { return value }
        return dictionary[value] ?? value
    }

    // 分类
    static var catGeneral: String { text("通用", "General") }
    static var catData: String { text("数据", "Data") }
    static var catAdvanced: String { text("高级", "Advanced") }

    // 数据管理
    static var dataTitle: String { text("数据管理", "Data Management") }
    static var terrainTitle: String { text("地形 SRTM", "Terrain SRTM") }
    static var localData: String { text("本地数据", "Local Data") }

    // 任务历史
    static var historyTitle: String { text("任务历史", "Task History") }
    static var noTasks: String { text("暂无任务记录", "No task records") }
    static var loadOlder: String { text("加载更早任务", "Load older tasks") }
}
