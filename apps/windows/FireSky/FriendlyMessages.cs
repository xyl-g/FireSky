namespace FireSky;

/// <summary>
/// 引擎锁/让路等机器可读错误串映射为中文 + 可操作建议，界面不透传裸英文。
/// 仅映射已知的协议级错误；未知错误保留原文以便排查。与 macOS
/// Store.friendlyEngineMessage 保持同一份映射。
/// </summary>
public static class FriendlyMessages
{
    public static string Map(string? message)
    {
        if (message is null) return "操作未完成。";
        var text = message.Trim();
        // 锁持有者可能是应用自身的同步轮、后台助手或残留进程；只匹配稳定前缀。
        if (text.StartsWith("another data sync round is active in another process",
                StringComparison.Ordinal))
            return "另一轮数据同步正在进行（应用内同步或后台助手），本轮已让路；稍后会自动重试。";
        var mapped = text switch
        {
            "another prepare/forecast is active"
                => "已有预测准备或计算正在进行，请等待完成或先取消，再发起本次操作。",
            "data sync skipped because another prepare/forecast is active"
                => "预测准备或计算正在进行，本轮同步已让路；待当前操作完成后再同步。",
            "another data sync is active" => "已有一轮数据同步正在进行，请稍候再试。",
            "network probe is already active" => "网络探测正在进行，请稍候再试。",
            "desktop operation cancelled" or "nowcast preparation cancelled"
                or "nowcast operation cancelled" => "操作已取消。",
            "nowcast targets more than four hours in the future"
                => "实况观测仅支持距朝霞/晚霞时刻 4 小时以内的目标；当前时间距离太远，请在事件前 4 小时内再来，或改用单点/区域预测。",
            "native nowcast preparation requires two recent HSD frames"
                => "实况分析需要最近两帧卫星数据，当前缓存不足；请检查网络后点击「下载缺失数据」重试。",
            "native nowcast preparation requires a non-fast context"
                => "实况准备需要先完成完整预检，请刷新预检后再下载数据。",
            "nowcast GFS requirements exceed the bounded hour limit"
                => "实况所需 GFS 时次超出上限，请刷新预检后重试；若持续出现请更换目标日期。",
            "HSD preflight frames missing" or "HSD preflight frame time missing"
                => "卫星数据预检结果不完整，请刷新预检后重试。",
            "nowcast cycle is missing" => "实况数据周期缺失，请刷新预检后重试。",
            "native nowcast requires fresh paired motion for a future event"
                => "未来时刻的实况分析需要最新成对卫星运动数据，请先点击「下载缺失数据」补齐卫星数据。",
            "HSD observation precedes GFS prior cycle"
                => "卫星观测早于 GFS 先验周期，暂无可用组合；请稍后刷新预检重试。",
            "data cycle is unverified or older than the latest release; explicit cycle-age confirmation is required"
                => "数据周期未核验或不是最新发布，请在确认提示中选择「继续预测」，或先更新数据。",
            "explicit confirmation is required before forecast"
                => "请先在预检窗口确认后再开始预测。",
            "inputs are missing; prepare data before forecasting"
                => "本次预测数据不完整，请先点击「下载缺失数据」补齐。",
            "strict input integrity check failed"
                => "数据完整性校验未通过，请重新下载本次数据后再试。",
            "assessment main bundle exceeds bounded output size"
                => "区域范围或格点密度超出单次输出上限，请缩小区域、降低密度或改用自定义格点后重试。",
            "Himawari calibrated scan is outside the supported five-minute nowcast window"
                => "卫星扫描与目标时刻相差超过 30 分钟，实况窗口尚未开启；请在朝霞/晚霞时刻前半小时再来，或改用单点/区域预测。",
            "cross-section offset must exactly match the event's five-minute grid"
                => "剖面时刻与事件网格不匹配，请重新发起预测；若持续出现请刷新预检后重试。",
            "native terrain prepass did not produce a final event plan"
                => "地形数据预下载未完成，请重试「下载缺失数据」；若持续失败请检查网络后刷新预检。",
            "unknown regional domain" => "所选预测区域无效，请重新选择区域或自定义范围。",
            "invalid EC data operation/planning limits"
                => "EC 数据计划超出规模限制，请缩小预测区域或减少目标日期后重试。",
            "data sync is not enabled" => "后台数据同步未启用，请先在设置中开启。",
            "native-report-has-no-map-cells" => "本次结果未生成地图格网（单点报告不含区域地图）。",
            _ => null,
        };
        if (mapped is not null) return mapped;
        if (text.StartsWith("data sync source ", StringComparison.Ordinal)
            && text.EndsWith(" is not enabled in settings", StringComparison.Ordinal))
        {
            var name = text["data sync source ".Length..^" is not enabled in settings".Length];
            return $"数据源 {name.ToUpperInvariant()} 未在设置中启用，请先勾选后再同步。";
        }
        if (text.StartsWith("all model forecasts failed", StringComparison.Ordinal)
            && (text.Contains("wrapped GFS segments overlap")
                || text.Contains("wrapped GFS segments carry different fields")))
            return "跨日界线区域的数据段拼接失败，请重新点击「开始预测」重试；若持续出现请刷新预检后重试。";
        if (text.StartsWith("all model forecasts failed", StringComparison.Ordinal))
            return "模型预测失败，请检查数据完整性后重试；若持续出现请刷新预检或缩小预测区域。";
        return message;
    }
}
