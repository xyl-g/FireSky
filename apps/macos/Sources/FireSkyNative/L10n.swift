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
        default: return chinese
        }
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
