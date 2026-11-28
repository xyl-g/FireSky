import Foundation

/// 界面多语言（v1.9.10）：轻量键值查找，支持中文（默认）/ English。
/// 语言偏好持久化到 UserDefaults（键 appLanguage，macOS/Android 同契约）；
/// "system" 跟随系统首选语言。覆盖范围与 macOS dictionary 对齐。
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
    static var close: String { text("关闭", "Close") }
    static var loadOlder: String { text("加载更早任务", "Load older tasks") }

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
        case "等待预测": return "Waiting for forecast"
        case "未完成": return "Incomplete"
        case "预测完成": return "Forecast complete"
        case "暂无结果": return "No result"
        case "未预测": return "Not forecast"
        case "发布周期已核验": return "Publication verified"
        case "更新待核验": return "Update pending verification"
        case "有新发布的数据可更新": return "Newer data available"
        case "非最新发布周期": return "Not the latest cycle"
        case "本次预测数据不完整": return "Forecast data incomplete"
        case "尚未下载本次预测数据": return "Forecast data not downloaded"
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
        "返回": "Back",
        "关闭": "Close",
        "刷新": "Refresh",
        "删除": "Delete",
        "重试": "Retry",
        "保留": "Keep",
        "慢": "Slow",
        "天": "d",
        "小时": "h",
        "分钟": "min",
        "个": "files",
        "同步": "Sync",
        "慢速": "Slow",
        "数据目录": "Data directory",
        "数据目录未设置": "Data directory not set",
        "目录": "Directories",
        "Rust 引擎": "Rust engine",
        "历史报告": "Past reports",
        "报告地图": "Report map",
        "卫星数据": "Satellite data",
        "连通性": "Connectivity",
        "下载数据": "Download data",
        "需先补齐本次数据": "Download the missing data first",
        "数据已齐，可开始预测": "Data ready — start the forecast",
        "数据已齐，可更新 %@": "Data ready — can update %@",
        "更新数据": "Update data",
        "返回更新数据": "Back and update data",
        "返回并点击「更新数据」获取最新周期": "Go back and tap \u{201c}Update data\u{201d} for the latest cycle",
        "确定预测": "Confirm forecast",
        "下载缺失数据": "Download missing data",
        "下载前先知道": "Before you download",
        "首次下载耗时取决于本机缺失的数据集与网络。上方只列本次预测所需文件；已下载文件不会重复下载，过程中可取消，失败项会保留并提示重试。":
            "First-download time depends on which datasets are missing and your network. The list above covers only this forecast; existing files are not downloaded twice. You can cancel, and failed items stay listed for retry.",
        "CAMS Key 未配置时仍可使用本地缓存；如需气溶胶数据，可在设置中配置后重新预检。":
            "Local caches remain usable without a CAMS key. Configure it in Settings and re-check if you need aerosol data.",
        "继续预测": "Forecast anyway",
        "预测准备": "Forecast preparation",
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
        "后台同步": "Background sync",
        "启用后台同步": "Enable background sync",
        "后台同步已启用": "Background sync enabled",
        "同步间隔": "Sync interval",
        "锚定区域": "Anchor region",
        "未设置": "Not set",
        "GFS/EC 时效上限": "GFS/EC lead horizon",
        "GFS 周期": "GFS cycles",
        "EC 周期": "EC cycles",
        "CAMS 周期": "CAMS cycles",
        "存储与保留": "Storage & retention",
        "海拔": "Elevation",
        "保存同步设置": "Save sync settings",
        "保存保留设置": "Save retention settings",
        "已启用自定义保留策略": "Custom retention policy enabled",
        "未配置缓存保留策略，缓存将随同步增长；可在下方「存储与保留」中设置。":
            "No retention policy configured; the cache grows with sync. Set it under Storage & retention below.",
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
        // 格式化模板（tf）
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
        "最近周期 %@": "Latest cycle %@",
        "上次同步 %@": "Last synced %@",
        "最近错误：%@": "Last error: %@",
        "缺 %d 个周期：%@": "Missing %d cycles: %@",
        "连续失败 %d 次": "%d consecutive failures",
        // 点预测工作台 / 首次使用
        "观测点": "Observation point",
        "地图": "Map",
        "收藏": "Favorites",
        "观测参数": "Forecast settings",
        "重置": "Reset",
        "目标日期": "Target date",
        "今天": "Today",
        "明天": "Tomorrow",
        "自定义": "Custom",
        "推荐窗口 %@": "Recommended window %@",
        "观测现象": "Observation",
        "朝霞": "Dawn glow",
        "晚霞": "Dusk glow",
        "编辑坐标": "Edit coordinates",
        "名称": "Name",
        "纬度": "Latitude",
        "经度": "Longitude",
        "时区": "Time zone",
        "获取海拔与时区": "Look up elevation and time zone",
        "大地图": "Open map",
        "收藏当前点": "Save this place",
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
        "预测完成": "Forecast complete",
        "等待预测": "Waiting for forecast",
        "暂无结果": "No result",
        "未预测": "Not forecast",
        "按新参数重算": "Recalculate with current settings",
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
        "质量指数": "Quality index",
        "缺测": "Missing",
        "仅 %@ 可用：%@": "Only %@ available: %@",
        "两个模式结论一致：%@": "Both models agree: %@",
        "两个模式结论不同：GFS %@ · EC %@": "Models differ: GFS %@ · EC %@",
        "最佳 %@ · 推荐窗口 %@": "Best time %@ · recommended window %@",
        "%@ 最佳 %@ · 窗口 %@": "%@ best %@ · window %@",
        "%@ 未完成：%@": "%@ incomplete: %@",
        "%@ 暂无结果": "%@ has no result yet",
        "%@ 主要限制：%@": "%@ main limitation: %@",
        "事件时刻取值范围 · 未取平均": "Event-time range · not averaged",
        "事件时刻 · 气溶胶光学厚度": "Event-time aerosol optical depth",
        "暂无可用 AOD 观测": "No AOD observations available",
        "诊断": "Diagnostics",
        "详情与口径": "Details & methodology",
        "评分项": "Metric",
        "云量基础分": "Cloud base score",
        "可烧云占比": "Burnable ratio",
        "入射遮挡": "Solar obstruction",
        "持续时间修正": "Duration adj.",
        "入射 AOD 修正": "Incoming AOD adj.",
        "观测 AOD 修正": "Observer AOD adj.",
        "已计入可烧量": "Included in burnable",
        "外推窗外/未判断": "Outside solar window",
        "刷新冻结剖面": "Reload frozen profile",
        "太阳方向大气剖面": "Solar-path atmospheric profile",
        "太阳方向卫星剖面": "Solar-path satellite profile",
        "按时次查询冻结剖面": "Inspect frozen profile by step",
        "运动与采样": "Motion & sampling",
        "查看技术诊断与时间窗元数据": "View technical diagnostics & timing metadata",
        "一图看懂": "At a glance",
        "受光高度线": "Sunlit Base",
        "未知/缺测": "Unknown / Missing",
        "等压线": "Isobars",
        "仰角": "El",
        "方位": "Az",
        "范围": "Range",
        "时次": "Time",
        "地形": "Terrain",
        "受光基准": "Sunlit base",
        "云属代理": "Genus proxy",
        "太阳方向": "Sun",
        "评分组成（后端只读 · 不在客户端重算）": "Score Breakdown (Backend Read-only)",
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
