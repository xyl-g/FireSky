using Microsoft.UI;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;

namespace FireSky;

/// <summary>
/// 点预测 / 实况结果渲染（深度展示对齐 macOS Workbench）：
/// GFS+EC 双摘要卡、分组指标卡、AOD 环境读数、评分组成、对比曲线（缺测断线 + 悬停读数）、
/// 15 列逐时台账、太阳方向剖面图层、冻结剖面查询、诊断分组。
/// 所有数值只读后端报告，不在客户端重算或填补缺测。
/// </summary>
public sealed partial class MainWindow
{
    private sealed class HeroCard
    {
        internal required StackPanel Panel { get; init; }
        internal required TextBlock Status { get; init; }
        internal required TextBlock Score { get; init; }
        internal required TextBlock Level { get; init; }
        internal required TextBlock Best { get; init; }
        internal required TextBlock Window { get; init; }
        internal required TextBlock Chips { get; init; }
        internal required Border GenusBadge { get; init; }
        internal required TextBlock GenusText { get; init; }
        internal required TextBlock Source { get; init; }
        internal required TextBlock Error { get; init; }
    }

    private readonly Dictionary<string, HeroCard> heroCards = new();
    private bool showInvalidLedgerSteps;

    private static readonly (string Key, string Key1, string Title, string Caption)[] NowcastMetrics =
    [
        ("near_cloud_ratio", "near_cloud_ratio", L10n.T("近场云量"), L10n.T("观测点附近卫星云覆盖")),
        ("far_cloud_ratio", "far_cloud_ratio", L10n.T("远场云量"), L10n.T("太阳方向远场云覆盖")),
        ("cloud_blanket_score", "cloud_blanket_score", L10n.T("云幕"), L10n.T("光学观测 · 卫星云幕评分")),
        ("cloud_texture_score", "cloud_texture_score", L10n.T("纹理"), L10n.T("光学观测 · 卫星云顶纹理")),
        ("horizon_clearance_score", "horizon_clearance_score", L10n.T("地平线通透"), L10n.T("光学观测 · 地平线通透度")),
        ("satellite_low_cloud_state", "satellite_low_cloud_state", L10n.T("低云状态"), L10n.T("卫星校准与垂直先验")),
        ("cloud_type_label", "cloud_type_label", L10n.T("多光谱云类"), L10n.T("桌面实况未计算 · HSD 通道不产生云类概率")),
        ("satellite_motion_quality", "satellite_motion_quality", L10n.T("运动质量"), L10n.T("卫星云体运动状态")),
        ("geometric_path_blocking_risk", "geometric_path_blocking_risk", L10n.T("光路阻塞风险"), L10n.T("太阳入射路径")),
        ("calibrated_sample_coverage_ratio", "calibrated_sample_coverage_ratio", L10n.T("采样覆盖率"), L10n.T("卫星有效采样比例")),
        ("confidence", "confidence", L10n.T("实况支持度"), L10n.T("卫星反演支持 · 非命中概率")),
        ("effective_convective_risk", "effective_convective_risk", L10n.T("区域对流风险"), L10n.T("雷达有效值 · 非事件概率")),
        ("effective_path_blocking_risk", "effective_path_blocking_risk", L10n.T("光路阻塞风险"), L10n.T("雷达有效值 · 光路约束风险")),
        ("convective_score_ceiling", "convective_score_ceiling", L10n.T("对流顶棚"), L10n.T("评分顶棚 · 约束封顶上限")),
        ("aod", "aod", "AOD 550", L10n.T("气溶胶光学厚度")),
    ];

    internal JsonObject? SelectedReport()
    {
        var model = TagOf(ResultModel?.SelectedItem) is { Length: > 0 } tag ? tag : "gfs";
        return result?["model_results"] is JsonObject models ? models[model] as JsonObject : result;
    }

    private JsonObject? ModelReport(string model) =>
        result?["model_results"] is JsonObject models ? models[model] as JsonObject : null;

    private string? ModelError(string model) =>
        (result?["model_errors"] as JsonObject)?[model] is JsonValue value
        && value.GetValueKind() == System.Text.Json.JsonValueKind.String
            ? value.GetValue<string>() : null;

    private static string ModelStatus(string? error, JsonObject? report, bool hasResult) =>
        error != null ? L10n.T("未完成") : report != null ? L10n.T("预测完成") : hasResult ? L10n.T("暂无结果") : L10n.T("等待预测");

    private static Brush ModelBrush(string model) => new SolidColorBrush(
        model == "ec" ? ColorFrom(0x14, 0x8A, 0x9C) : ColorFrom(0xE3, 0x66, 0x4A));

    private static Windows.UI.Color ColorFrom(byte r, byte g, byte b) =>
        Windows.UI.Color.FromArgb(255, r, g, b);

    private static string ModelColorHex(string model) => model == "ec" ? "#148A9C" : "#E3664A";

    // ---------- Hero 双摘要卡 ----------

    private void BuildHeroCards()
    {
        HeroHost.Children.Clear();
        heroCards.Clear();
        var grid = new Grid { ColumnSpacing = 18 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var gfs = BuildHeroCard("gfs");
        var ec = BuildHeroCard("ec");
        heroCards["gfs"] = gfs;
        heroCards["ec"] = ec;
        Grid.SetColumn(gfs.Panel, 0);
        var divider = new Border
        {
            Width = 1, Height = 132, Opacity = .5,
            Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
        };
        Grid.SetColumn(divider, 1);
        Grid.SetColumn(ec.Panel, 2);
        grid.Children.Add(gfs.Panel);
        grid.Children.Add(divider);
        grid.Children.Add(ec.Panel);
        HeroHost.Children.Add(grid);
    }

    private static HeroCard BuildHeroCard(string model)
    {
        var panel = new StackPanel { Spacing = 8 };
        var top = new Grid { ColumnSpacing = 8 };
        top.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        top.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        top.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        top.Children.Add(new StackPanel
        {
            Orientation = Orientation.Horizontal, Spacing = 6,
            Children =
            {
                new Ellipse
                {
                    Width = 6, Height = 6, Fill = ModelBrush(model),
                    VerticalAlignment = VerticalAlignment.Center,
                },
                new TextBlock
                {
                    Text = model.ToUpperInvariant(), FontSize = 12,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                },
            },
        });
        var status = new TextBlock { Text = L10n.T("等待预测"), FontSize = 9, Opacity = .7 };
        Grid.SetColumn(status, 2);
        top.Children.Add(status);
        panel.Children.Add(top);
        var middle = new Grid { ColumnSpacing = 12 };
        middle.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        middle.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var scoreStack = new StackPanel { Spacing = 3 };
        var score = new TextBlock
        {
            Text = "--", FontSize = 30, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Foreground = ModelBrush(model),
        };
        var level = new TextBlock { Text = L10n.T("峰值质量指数"), FontSize = 9, Opacity = .65 };
        scoreStack.Children.Add(score);
        scoreStack.Children.Add(level);
        var bestStack = new StackPanel { Spacing = 3 };
        bestStack.Children.Add(new TextBlock { Text = L10n.T("最佳时刻"), FontSize = 9, Opacity = .65 });
        var best = new TextBlock
        {
            Text = "--", FontSize = 21, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        };
        bestStack.Children.Add(best);
        Grid.SetColumn(scoreStack, 0);
        Grid.SetColumn(bestStack, 1);
        middle.Children.Add(scoreStack);
        middle.Children.Add(bestStack);
        panel.Children.Add(middle);
        var window = new TextBlock { Text = L10n.T("推荐窗口 --"), FontSize = 10, Opacity = .7 };
        panel.Children.Add(window);
        var chips = new TextBlock { Text = "", FontSize = 9, Opacity = .7, TextWrapping = TextWrapping.Wrap };
        panel.Children.Add(chips);
        var genusText = new TextBlock { Text = "", FontSize = 10, Opacity = .85 };
        var genusBadge = new Border
        {
            CornerRadius = new CornerRadius(6),
            Padding = new Thickness(7, 2, 7, 2),
            HorizontalAlignment = HorizontalAlignment.Left,
            Background = new SolidColorBrush(Windows.UI.Color.FromArgb(0x1C, 0x88, 0x99, 0xAA)),
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(1),
            Visibility = Visibility.Collapsed,
            Child = genusText,
        };
        panel.Children.Add(genusBadge);
        var source = new TextBlock { Text = "", FontSize = 9, Opacity = .65, TextWrapping = TextWrapping.Wrap };
        panel.Children.Add(source);
        var error = new TextBlock
        {
            Text = "", FontSize = 10, Foreground = new SolidColorBrush(Colors.Red),
            TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true, Visibility = Visibility.Collapsed,
        };
        panel.Children.Add(error);
        return new HeroCard
        {
            Panel = panel, Status = status, Score = score, Level = level,
            Best = best, Window = window, Chips = chips,
            GenusBadge = genusBadge, GenusText = genusText,
            Source = source, Error = error,
        };
    }

    private string CycleLabel(string model, JsonObject? report)
    {
        var cycle = Text(report, "cycle_time_utc");
        if (cycle.Length >= 16) return $"{cycle[11..16]}Z {cycle[0..10]}";
        return "";
    }

    private static string FamilyLabel(JsonNode? value)
    {
        var family = StringOrNull(value);
        return family switch
        {
            "high" => L10n.T("高云族"), "middle" => L10n.T("中云族"), "low" => L10n.T("低云族"),
            null => "", _ => family,
        };
    }

    private static string GenusLabel(string value) => value switch
    {
        "cirrus" => L10n.T("卷云"),
        "cirrocumulus" => L10n.T("卷积云"),
        "cirrostratus" => L10n.T("卷层云"),
        "altocumulus" => L10n.T("高积云"),
        "altostratus" => L10n.T("高层云"),
        "nimbostratus" => L10n.T("雨层云"),
        "stratocumulus" => L10n.T("层积云"),
        "stratus" => L10n.T("层云"),
        "cumulus" => L10n.T("积云"),
        "cumulonimbus" => L10n.T("积雨云"),
        _ => value.Replace("_", " "),
    };

    private static string PhaseLabel(string value) => value switch
    {
        "ice" => L10n.T("冰相成分"),
        "water" => L10n.T("液态水成分"),
        "mixed" => L10n.T("冰水混合成分"),
        _ => L10n.T("相态未知"),
    };

    private static string? CloudGenusBrief(JsonObject? proxy, JsonObject? report = null)
    {
        if (proxy == null) return null;
        var label = Text(proxy, "label");
        if (label.Length == 0) label = "unknown";
        var conf = DoubleValue(proxy["confidence"]);
        var pct = conf is { } c ? $" {(int)Math.Round(c * 100)}%" : "";
        if (label != "unknown")
        {
            return L10n.IsEnglish
                ? $"{GenusLabel(label)} (proxy{pct})"
                : $"{GenusLabel(label)}（代理{pct}）";
        }
        var compatible = (proxy["compatible_genera"] as JsonArray ?? [])
            .Select(node => StringOrNull(node))
            .Where(name => !string.IsNullOrEmpty(name))
            .Cast<string>()
            .ToList();
        if (compatible.Count == 0 && report?["dominant_burnable_cloud_genus_diagnosis"] is JsonObject diag)
        {
            compatible = (diag["compatible_genera"] as JsonArray ?? [])
                .Select(node => StringOrNull(node))
                .Where(name => !string.IsNullOrEmpty(name))
                .Cast<string>()
                .ToList();
        }
        if (compatible.Count > 0)
        {
            var first = GenusLabel(compatible[0]);
            return L10n.IsEnglish ? $"{first} etc. (proxy)" : $"{first}等（代理）";
        }
        return null;
    }

    private string ModelSourceNote(string model)
    {
        var report = ModelReport(model);
        if (report == null) return model == "ec" ? L10n.T("EC：未预测") : L10n.T("GFS：未预测");
        var source = Text(report, "model_source");
        return source switch
        {
            "ec" => L10n.T("EC：TCC 总云 + PL-RH 云柱代理；气溶胶来自 CAMS 环境读数"),
            _ => L10n.T("GFS：全量云场与可烧量来源；气溶胶来自 CAMS 环境读数"),
        };
    }

    // ---------- AOD 环境读数 ----------

    internal sealed record AodSummary(double[] Values, string[] Sources, string Detail)
    {
        public bool Available => Values.Length > 0;
        public string Source => Sources.Length == 0 ? L10n.T("暂无来源")
            : Sources.All(source => source == "CAMS") ? "CAMS"
            : Sources.All(source => source != "CAMS") ? L10n.T("其他来源") : L10n.T("混合来源");
        public string Value => !Available ? "—"
            : Values.Min().ToString("0.00", CultureInfo.InvariantCulture) == Values.Max().ToString("0.00", CultureInfo.InvariantCulture)
                ? Values.Min().ToString("0.00", CultureInfo.InvariantCulture)
                : $"{Values.Min():0.00} – {Values.Max():0.00}";
        public string Caption => Values.Length == 0 ? L10n.T("暂无可用 AOD 观测")
            : Values.Distinct().Count() > 1 ? L10n.T("事件时刻取值范围 · 未取平均") : L10n.T("事件时刻 · 气溶胶光学厚度");
    }

    internal static AodSummary AodReading(JsonObject?[] reports)
    {
        var values = new List<double>();
        var sources = new List<string>();
        var details = new List<string>();
        foreach (var report in reports)
        {
            if (report?["aerosol"] is not JsonObject aerosol) continue;
            if (DoubleValue(aerosol["aod550"]) is not { } number || number < 0) continue;
            var rawSource = Text(aerosol, "source");
            var cams = rawSource.StartsWith("cams", StringComparison.OrdinalIgnoreCase)
                || Text(aerosol, "quality") == "cams";
            values.Add(number);
            sources.Add(cams ? "CAMS" : L10n.T("其他来源"));
            var time = Text(report, "anchor_local") is { Length: > 0 } anchor ? anchor : L10n.T("时刻未提供");
            details.Add($"{time} · {number:0.0000} · {rawSource}");
        }
        return new AodSummary([.. values], [.. sources],
            details.Count == 0 ? L10n.T("等待 CAMS 环境读取") : string.Join("\n", details.Distinct().OrderBy(v => v)));
    }

    private AodSummary PointAod => AodReading([ModelReport("gfs"), ModelReport("ec")]);

    // ---------- 顶层渲染 ----------

    private void RenderResult()
    {
        if (ResultPanel == null) return;
        var hasResult = result != null && resultKind == page;
        UpdateNowcastBanners(hasResult && resultKind == "nowcast" ? SelectedReport() : null);
        ResultContext.Text = $"{ResultTitle()} {ResultPhenomenonLabel()}";
        // UX #1：标题跟随原始参数；表单改动不清空结果，只显示 stale 徽标。
        StaleBanner.IsOpen = hasResult && ResultParametersStale;
        ToolTipService.SetToolTip(StaleBanner, ResultParamsSummary());
        ResultModel.Visibility = page == "nowcast" ? Visibility.Collapsed : Visibility.Visible;
        RegionalInspector.Visibility = page == "regional" ? Visibility.Visible : Visibility.Collapsed;
        if (!hasResult)
        {
            RegionalGridRows.Items.Clear();
            CityRankingRows.Items.Clear();
            ClearRegionalProfile();
            ResetHeroCards();
            ResetMetricCards();
            PointVisualHost.Children.Clear();
            PointVisualHost.Visibility = Visibility.Collapsed;
            BreakdownCard.Visibility = Visibility.Collapsed;
            BreakdownHost.Children.Clear();
            chartPoints = null;
            QualityChart.Children.Clear();
            ChartEmpty.Visibility = Visibility.Visible;
            ChartLegend.Text = "";
            LedgerHost.Children.Clear();
            DiagnosticPanel.Children.Clear();
            ProfileStatus.Text = L10n.T("等待预测");
            FrozenProfileButton.Visibility = Visibility.Collapsed;
            PointCrossSection.Visibility = Visibility.Collapsed;
            PointProfileNote.Text = "";
            frozenProfile = null; frozenProfileModel = ""; frozenProfileState = "idle";
            if (page == "regional")
            {
                EmptyStatePanel.Visibility = Visibility.Collapsed;
                ResultPanel.Visibility = Visibility.Collapsed;
                RegionalGridSummary.Text = L10n.T("等待预测");
            }
            else
            {
                ResultPanel.Visibility = Visibility.Collapsed;
                ResultTabs.Visibility = Visibility.Collapsed;
                EmptyStatePanel.Visibility = Visibility.Visible;
                if (page == "nowcast")
                {
                    EmptyStateTitle.Text = L10n.T("准备就绪，开始卫星实况观测");
                    EmptyStateDescription.Text = L10n.T("分析完成后，这里会展示卫星帧评分、云幕/纹理光学指标、后验云概率垂直剖面与逐时实况台账。");
                    EmptyStateCards.Visibility = Visibility.Collapsed;
                }
                else
                {
                    EmptyStateTitle.Text = L10n.T("准备就绪，开始首次朝晚霞预测");
                    EmptyStateDescription.Text = L10n.T("预测完成后，这里会依次展示双模式行动结论、GFS 与 EC 独立指数、大气垂直剖面和逐时台账。");
                    EmptyStateCards.Visibility = Visibility.Visible;
                }
            }
            return;
        }
        EmptyStatePanel.Visibility = Visibility.Collapsed;
        ResultPanel.Visibility = Visibility.Visible;
        if (resultKind == "regional")
        {
            ResultTabs.Visibility = Visibility.Collapsed;
            HeroHost.Visibility = Visibility.Collapsed;
            MetricHost.Visibility = Visibility.Collapsed;
            BreakdownCard.Visibility = Visibility.Collapsed;
            RenderRegionalInspector(SelectedReport());
            return;
        }
        ResultTabs.Visibility = Visibility.Visible;
        HeroHost.Visibility = Visibility.Visible;
        MetricHost.Visibility = Visibility.Visible;
        if (resultKind == "nowcast")
        {
            PointVisualHost.Children.Clear();
            PointVisualHost.Visibility = Visibility.Collapsed;
            RenderNowcastResult();
            return;
        }
        heroCards["ec"].Panel.Visibility = Visibility.Visible;
        BuildMetrics();
        RenderHero();
        RenderPointVisuals();
        RenderMetrics();
        RenderBreakdown();
        RenderChart();
        RenderLedger();
        RenderDiagnostics();
        RenderPointProfileTab();
    }

    private void ResetHeroCards()
    {
        heroCards["ec"].Panel.Visibility = Visibility.Visible;
        foreach (var (model, card) in heroCards)
        {
            card.Status.Text = L10n.T("等待预测");
            card.Status.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            card.Score.Text = "--";
            card.Level.Text = L10n.T("峰值质量指数");
            card.Best.Text = "--";
            card.Window.Text = L10n.T("推荐窗口 --");
            card.Chips.Text = "";
            card.GenusText.Text = "";
            card.GenusBadge.Visibility = Visibility.Collapsed;
            card.Source.Text = "";
            card.Error.Text = "";
            card.Error.Visibility = Visibility.Collapsed;
            card.Score.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        }
        ResultContext.Text = $"{LocationName.Text} · {TargetDateLabel()} {ResultPhenomenonLabel()}";
    }

    private void RenderHero()
    {
        foreach (var model in new[] { "gfs", "ec" })
        {
            var card = heroCards[model];
            var error = ModelError(model);
            var report = ModelReport(model);
            card.Status.Text = ModelStatus(error, report, result != null);
            card.Status.Foreground = error != null
                ? new SolidColorBrush(Colors.Red)
                : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            var quality = report != null && DoubleValue(report["peak_quality_index"] ?? report["quality_index"]) is { } q
                ? q : (double?)null;
            card.Score.Text = quality is { } value ? value.ToString("0.00") : "--";
            card.Score.Foreground = quality is { }
                ? ModelBrush(model)
                : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            card.Level.Text = Text(report, "peak_quality_level") is { Length: > 0 } level ? level
                : Text(report, "quality_level") is { Length: > 0 } fallback ? fallback : L10n.T("峰值质量指数");
            card.Best.Text = TimeLabel(Text(report, "best_time_local"));
            card.Window.Text =
                $"推荐窗口 {TimeLabel(Text(report?["timing_window"], "start_time_local"))} – {TimeLabel(Text(report?["timing_window"], "end_time_local"))}";
            var cycle = CycleLabel(model, report);
            var family = FamilyLabel(report?["dominant_burnable_pressure_family"]);
            card.Chips.Text = string.Join("   ",
                new[] { cycle.Length > 0 ? $"起报 {cycle}" : "", report == null ? L10n.T("未预测") : family.Length > 0 ? family : L10n.T("压力族未提供") }
                .Where(part => part.Length > 0));
            ToolTipService.SetToolTip(card.Chips, report == null
                ? L10n.T("尚未运行预测；完成一次预测后显示主导可烧压力族。")
                : L10n.T("主导可烧压力族（来自本次预测结果）"));
            var genus = CloudGenusBrief(report?["cloud_genus_proxy"] as JsonObject, report);
            if (genus != null)
            {
                card.GenusText.Text = genus;
                card.GenusBadge.Visibility = Visibility.Visible;
                ToolTipService.SetToolTip(card.GenusBadge,
                    L10n.T("依据压力层、相态与云层几何的模型云属代理，不代表卫星或人工云属观测。"));
            }
            else
            {
                card.GenusText.Text = "";
                card.GenusBadge.Visibility = Visibility.Collapsed;
            }
            card.Source.Text = ModelSourceNote(model);
            card.Error.Text = error ?? "";
            card.Error.Visibility = error != null ? Visibility.Visible : Visibility.Collapsed;
        }
    }

    // ---------- 点预测光谱色彩 & 拍摄方位雷达（v2.0.0） ----------

    private void RenderPointVisuals()
    {
        if (PointVisualHost == null) return;
        PointVisualHost.Children.Clear();
        var report = SelectedReport();
        if (report == null)
        {
            PointVisualHost.Visibility = Visibility.Collapsed;
            return;
        }
        var modelLabel = (TagOf(ResultModel?.SelectedItem) is { Length: > 0 } tag ? tag : "gfs").ToUpperInvariant();
        if (report["spectral_color"] is JsonObject spectral
            && BuildSpectralColorCard(modelLabel, spectral) is { } spectralCard)
        {
            PointVisualHost.Children.Add(spectralCard);
        }
        if (report["azimuth_radar"] is JsonObject radar
            && BuildAzimuthRadarCard(modelLabel, radar) is { } radarCard)
        {
            PointVisualHost.Children.Add(radarCard);
        }
        PointVisualHost.Visibility = PointVisualHost.Children.Count > 0
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private static bool TryParseHexColor(string? hex, out Windows.UI.Color color)
    {
        color = default;
        if (string.IsNullOrWhiteSpace(hex)) return false;
        var span = hex.Trim().AsSpan();
        if (span.StartsWith("#")) span = span[1..];
        if (span.Length == 6
            && byte.TryParse(span[0..2], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var r)
            && byte.TryParse(span[2..4], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var g)
            && byte.TryParse(span[4..6], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var b))
        {
            color = ColorFrom(r, g, b);
            return true;
        }
        if (span.Length == 8
            && byte.TryParse(span[0..2], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var a)
            && byte.TryParse(span[2..4], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var r8)
            && byte.TryParse(span[4..6], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var g8)
            && byte.TryParse(span[6..8], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var b8))
        {
            color = Windows.UI.Color.FromArgb(a, r8, g8, b8);
            return true;
        }
        return false;
    }

    private FrameworkElement? BuildSpectralColorCard(string modelLabel, JsonObject spectral)
    {
        var peakHex = Text(spectral, "peak_hex");
        var hasPeakColor = TryParseHexColor(peakHex, out var peakColor);
        var labelZh = Text(spectral, "peak_label_zh");
        var labelEn = Text(spectral, "peak_label_en");
        var colorLabel = L10n.IsEnglish
            ? (labelEn.Length > 0 ? labelEn : labelZh)
            : (labelZh.Length > 0 ? labelZh : labelEn);
        var gradientColors = new List<Windows.UI.Color>();
        foreach (var node in spectral["sky_gradient_hex"] as JsonArray ?? [])
        {
            if (TryParseHexColor(StringOrNull(node), out var stopColor))
                gradientColors.Add(stopColor);
        }
        if (!hasPeakColor && gradientColors.Count == 0 && colorLabel.Length == 0)
            return null;

        var solarAz = DoubleValue(spectral["solar_azimuth_deg"]);
        var solarEl = DoubleValue(spectral["solar_elevation_deg"]);

        var panel = new StackPanel { Spacing = 10 };
        var header = new Grid();
        header.Children.Add(new TextBlock
        {
            Text = L10n.T("光谱色彩预测"),
            FontSize = 13,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        header.Children.Add(new TextBlock
        {
            Text = modelLabel,
            FontSize = 10,
            Opacity = .65,
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center,
        });
        panel.Children.Add(header);

        var summaryRow = new Grid { ColumnSpacing = 12 };
        summaryRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        summaryRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        var swatchColor = hasPeakColor ? peakColor
            : (gradientColors.Count > 0 ? gradientColors[gradientColors.Count / 2] : ColorFrom(0xE3, 0x66, 0x4A));
        var swatch = new Border
        {
            Width = 36,
            Height = 36,
            CornerRadius = new CornerRadius(10),
            Background = new SolidColorBrush(swatchColor),
            BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
            BorderThickness = new Thickness(1),
            VerticalAlignment = VerticalAlignment.Center,
        };
        if (hasPeakColor) ToolTipService.SetToolTip(swatch, peakHex.ToUpperInvariant());
        Grid.SetColumn(swatch, 0);
        summaryRow.Children.Add(swatch);

        var metaStack = new StackPanel { Spacing = 3, VerticalAlignment = VerticalAlignment.Center };
        var headlineParts = new List<string>();
        if (colorLabel.Length > 0) headlineParts.Add(colorLabel);
        if (hasPeakColor) headlineParts.Add(peakHex.ToUpperInvariant());
        metaStack.Children.Add(new TextBlock
        {
            Text = headlineParts.Count > 0 ? string.Join(" · ", headlineParts) : L10n.T("色彩预测"),
            FontSize = 13,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            IsTextSelectionEnabled = true,
        });

        var solarParts = new List<string>();
        if (solarAz is { } az)
            solarParts.Add(L10n.IsEnglish ? $"Solar azimuth {az:0.0}°" : $"太阳方位 {az:0.0}°");
        if (solarEl is { } el)
            solarParts.Add(L10n.IsEnglish ? $"Solar elevation {el:0.0}°" : $"太阳仰角 {el:0.0}°");
        if (solarParts.Count > 0)
        {
            metaStack.Children.Add(new TextBlock
            {
                Text = string.Join(" · ", solarParts),
                FontSize = 11,
                Opacity = .72,
                TextWrapping = TextWrapping.Wrap,
            });
        }
        Grid.SetColumn(metaStack, 1);
        summaryRow.Children.Add(metaStack);
        panel.Children.Add(summaryRow);

        var stops = gradientColors.Count >= 2
            ? gradientColors
            : [swatchColor, swatchColor];
        var gradientBrush = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0.5),
            EndPoint = new Windows.Foundation.Point(1, 0.5),
        };
        for (var i = 0; i < stops.Count; i++)
        {
            gradientBrush.GradientStops.Add(new GradientStop
            {
                Color = stops[i],
                Offset = stops.Count == 1 ? 0 : i / (double)(stops.Count - 1),
            });
        }
        panel.Children.Add(new Border
        {
            Height = 14,
            CornerRadius = new CornerRadius(7),
            Background = gradientBrush,
            BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
            BorderThickness = new Thickness(1),
        });
        panel.Children.Add(new TextBlock
        {
            Text = L10n.T("天顶 → 地平线 4 段天空光谱渐变"),
            FontSize = 9,
            Opacity = .6,
        });

        return new Border
        {
            Child = panel,
            Padding = new Thickness(16),
            CornerRadius = new CornerRadius(14),
            Background = ThemeBrush("FireSkyCardBrush"),
            BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
            BorderThickness = new Thickness(1),
        };
    }

    private FrameworkElement? BuildAzimuthRadarCard(string modelLabel, JsonObject radar)
    {
        var bestDeg = DoubleValue(radar["best_azimuth_deg"]);
        var dirZh = Text(radar, "best_direction_zh");
        var dirEn = Text(radar, "best_direction_en");
        var direction = L10n.IsEnglish
            ? (dirEn.Length > 0 ? dirEn : dirZh)
            : (dirZh.Length > 0 ? dirZh : dirEn);
        var recZh = Text(radar, "recommendation_zh");
        var recEn = Text(radar, "recommendation_en");
        var recommendation = L10n.IsEnglish
            ? (recEn.Length > 0 ? recEn : recZh)
            : (recZh.Length > 0 ? recZh : recEn);
        var bestScore = DoubleValue(radar["best_score"]);
        var solarAz = DoubleValue(radar["solar_azimuth_deg"]);
        var bearings = (radar["bearings"] as JsonArray ?? [])
            .OfType<JsonObject>()
            .Select(item => (Deg: DoubleValue(item["deg"]), Score: DoubleValue(item["score"])))
            .Where(item => item.Deg != null && item.Score != null)
            .Select(item => (Deg: item.Deg!.Value, Score: Math.Clamp(item.Score!.Value, 0, 1)))
            .ToList();

        if (bestDeg == null && direction.Length == 0 && recommendation.Length == 0 && bearings.Count == 0)
            return null;

        var panel = new StackPanel { Spacing = 10 };
        var header = new Grid();
        header.Children.Add(new TextBlock
        {
            Text = L10n.T("推荐拍摄方位"),
            FontSize = 13,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        header.Children.Add(new TextBlock
        {
            Text = modelLabel,
            FontSize = 10,
            Opacity = .65,
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center,
        });
        panel.Children.Add(header);

        var body = new Grid { ColumnSpacing = 16 };
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        const double size = 136;
        const double cx = size / 2;
        const double cy = size / 2;
        const double maxR = 46;
        Windows.Foundation.Point Polar(double deg, double normRadius)
        {
            var r = Math.Clamp(normRadius, 0, 1) * maxR;
            var rad = deg * Math.PI / 180.0;
            return new Windows.Foundation.Point(cx + r * Math.Sin(rad), cy - r * Math.Cos(rad));
        }

        var compass = new Canvas { Width = size, Height = size };
        foreach (var fraction in new[] { 0.5, 1.0 })
        {
            var r = maxR * fraction;
            compass.Children.Add(new Ellipse
            {
                Width = r * 2,
                Height = r * 2,
                Stroke = new SolidColorBrush(Colors.Gray),
                StrokeThickness = 1,
                Opacity = .25,
                RenderTransform = new TranslateTransform { X = cx - r, Y = cy - r },
            });
        }
        compass.Children.Add(new Line
        {
            X1 = cx, Y1 = cy - maxR, X2 = cx, Y2 = cy + maxR,
            Stroke = new SolidColorBrush(Colors.Gray), StrokeThickness = 1, Opacity = .2,
        });
        compass.Children.Add(new Line
        {
            X1 = cx - maxR, Y1 = cy, X2 = cx + maxR, Y2 = cy,
            Stroke = new SolidColorBrush(Colors.Gray), StrokeThickness = 1, Opacity = .2,
        });

        void Cardinal(string text, double x, double y) => compass.Children.Add(new TextBlock
        {
            Text = text,
            FontSize = 9,
            Opacity = .65,
            RenderTransform = new TranslateTransform { X = x, Y = y },
        });
        Cardinal("N", cx - 4, cy - maxR - 15);
        Cardinal("E", cx + maxR + 5, cy - 6);
        Cardinal("S", cx - 3, cy + maxR + 2);
        Cardinal("W", cx - maxR - 14, cy - 6);

        if (bearings.Count >= 3)
        {
            var polygon = new Polygon
            {
                Fill = ThemeBrush("FireSkyAccentTintBrush"),
                Stroke = ThemeBrush("FireSkyAccentBrush"),
                StrokeThickness = 1.6,
            };
            foreach (var (deg, score) in bearings)
                polygon.Points.Add(Polar(deg, score));
            compass.Children.Add(polygon);
        }

        if (solarAz is { } sDeg)
        {
            var sunTip = Polar(sDeg, 1.0);
            compass.Children.Add(new Line
            {
                X1 = cx, Y1 = cy, X2 = sunTip.X, Y2 = sunTip.Y,
                Stroke = ThemeBrush("FireSkyWarningBrush"),
                StrokeThickness = 1.2,
                StrokeDashArray = [3, 2],
            });
            compass.Children.Add(new Ellipse
            {
                Width = 6, Height = 6,
                Fill = ThemeBrush("FireSkyWarningBrush"),
                RenderTransform = new TranslateTransform { X = sunTip.X - 3, Y = sunTip.Y - 3 },
            });
        }

        if (bestDeg is { } bDeg)
        {
            var bestTip = Polar(bDeg, 1.0);
            compass.Children.Add(new Line
            {
                X1 = cx, Y1 = cy, X2 = bestTip.X, Y2 = bestTip.Y,
                Stroke = ThemeBrush("FireSkyCoolAccentBrush"),
                StrokeThickness = 2,
            });
            compass.Children.Add(new Ellipse
            {
                Width = 8, Height = 8,
                Fill = ThemeBrush("FireSkyCoolAccentBrush"),
                RenderTransform = new TranslateTransform { X = bestTip.X - 4, Y = bestTip.Y - 4 },
            });
        }

        Grid.SetColumn(compass, 0);
        body.Children.Add(compass);

        var infoStack = new StackPanel { Spacing = 6, VerticalAlignment = VerticalAlignment.Center };
        var dirText = direction.Length > 0 && bestDeg is { } bd1
            ? $"{direction}（{bd1:0}°）"
            : direction.Length > 0 ? direction
            : bestDeg is { } bd2 ? $"{bd2:0}°" : "--";
        infoStack.Children.Add(new TextBlock
        {
            Text = L10n.IsEnglish ? $"Best Azimuth: {dirText}" : $"最佳拍摄朝向：{dirText}",
            FontSize = 14,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });

        var stats = new List<string>();
        if (bestScore is { } bs)
            stats.Add(L10n.IsEnglish ? $"Direction score {bs * 100:0}%" : $"方位评分 {bs * 100:0}%");
        if (solarAz is { } sa)
            stats.Add(L10n.IsEnglish ? $"Solar azimuth {sa:0.0}°" : $"太阳方位 {sa:0.0}°");
        if (stats.Count > 0)
        {
            infoStack.Children.Add(new TextBlock
            {
                Text = string.Join(" · ", stats),
                FontSize = 11,
                Opacity = .72,
                TextWrapping = TextWrapping.Wrap,
            });
        }

        if (recommendation.Length > 0)
        {
            infoStack.Children.Add(new TextBlock
            {
                Text = recommendation,
                FontSize = 12,
                Opacity = .9,
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
            });
        }

        Grid.SetColumn(infoStack, 1);
        body.Children.Add(infoStack);
        panel.Children.Add(body);

        return new Border
        {
            Child = panel,
            Padding = new Thickness(16),
            CornerRadius = new CornerRadius(14),
            Background = ThemeBrush("FireSkyCardBrush"),
            BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
            BorderThickness = new Thickness(1),
        };
    }

    // ---------- 分组指标卡 ----------

    private sealed class MetricCard
    {
        internal required TextBlock Gfs { get; init; }
        internal required TextBlock Ec { get; init; }
        internal TextBlock? GfsSub { get; init; }
        internal TextBlock? EcSub { get; init; }
        internal required TextBlock Caption { get; init; }
        internal required TextBlock Source { get; init; }
    }

    private readonly Dictionary<string, MetricCard> metricCards = new();

    private static string RatioText(JsonNode? node) =>
        DoubleValue(node) is { } value ? $"{value * 100:0}%" : "—";

    private static string MetricValue(string key, JsonObject? report, AodSummary aod)
    {
        if (report == null) return "--";
        if (key == "aod") return aod.Value;
        var components = report["quality_components"] as JsonObject;
        return key switch
        {
            "burnable" => DoubleValue(components?["burnable_cloud_coverage_effective"]
                ?? report["burnable_cloud_coverage_effective"]) is { } v ? $"{v * 100:0}%" : "—",
            "material" => DoubleValue(components?["cloud_material"]) is { } v ? v.ToString("0.00") : "—",
            "brightness" => DoubleValue(components?["brightness_color"]) is { } v ? v.ToString("0.00") : "—",
            "cloud_layer" => CloudLayerLabel(report),
            "incoming" => DoubleValue(components?["incoming_aod_illumination_factor"]) is { } v
                ? $"×{v:0.000}" : "—",
            "contrast" => DoubleValue(components?["observer_aod_contrast_factor"]) is { } v
                ? $"×{v:0.000}" : "—",
            "duration" => DoubleValue(report["duration_minutes"] ?? components?["duration_minutes"])
                is { } v ? $"{v:0} 分钟" : "—",
            "confidence" => DoubleValue(report["confidence_score"]) is { } v ? $"{v * 100:0}%" : "—",
            "convective" => DoubleValue(report["convective_risk_score"]) is { } v ? $"{v * 100:0}%" : "—",
            _ => "—",
        };
    }

    private static string CloudLayerLabel(JsonObject? report)
    {
        if (report == null) return "--";
        var baseAgl = DoubleValue(report["dominant_burnable_cloud_base_agl_mean_m"]);
        var topAgl = DoubleValue(report["dominant_burnable_cloud_top_agl_mean_m"]);
        return baseAgl is { } b && topAgl is { } t
            ? $"{(int)Math.Round(b)}–{(int)Math.Round(t)} m" : "—";
    }

    private void BuildMetrics()
    {
        MetricHost.Children.Clear();
        metricCards.Clear();
        var ranges = new[] { (0, 4), (4, 7), (7, 10) };
        for (var group = 0; group < ranges.Length; group++)
        {
            var (start, end) = ranges[group];
            var section = new StackPanel { Spacing = 7 };
            section.Children.Add(new TextBlock
            {
                Text = MetricGroups[group], FontSize = 10, Opacity = .65,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
            });
            var row = new Grid { ColumnSpacing = 10 };
            for (var index = start; index < end; index++)
            {
                var spec = Metrics[index];
                var card = new StackPanel { Spacing = 6, Padding = new Thickness(12) };
                card.Children.Add(new TextBlock
                {
                    Text = spec.Title, FontSize = 12, Opacity = .85,
                });
                var aodShared = spec.Key == "aod";
                var source = new TextBlock { Text = "", FontSize = 8, Opacity = .8 };
                TextBlock gfs, ec;
                TextBlock? gfsSub = null, ecSub = null;
                if (aodShared)
                {
                    var aodBlock = new StackPanel { Spacing = 3 };
                    source = new TextBlock { Text = "", FontSize = 9, Opacity = .8 };
                    aodBlock.Children.Add(source);
                    gfs = ec = new TextBlock
                    {
                        Text = "--", FontSize = 19,
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    };
                    aodBlock.Children.Add(gfs);
                    card.Children.Add(aodBlock);
                }
                else
                {
                    var pair = new Grid { ColumnSpacing = 8 };
                    pair.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                    pair.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                    gfs = new TextBlock
                    {
                        Text = "--", FontSize = 17, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        Foreground = ModelBrush("gfs"),
                    };
                    ec = new TextBlock
                    {
                        Text = "--", FontSize = 17, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        Foreground = ModelBrush("ec"),
                    };
                    var gfsStack = new StackPanel { Spacing = 2 };
                    gfsStack.Children.Add(new TextBlock { Text = "GFS", FontSize = 9, Opacity = .7, Foreground = ModelBrush("gfs") });
                    gfsStack.Children.Add(gfs);
                    var ecStack = new StackPanel { Spacing = 2 };
                    ecStack.Children.Add(new TextBlock { Text = "EC", FontSize = 9, Opacity = .7, Foreground = ModelBrush("ec") });
                    ecStack.Children.Add(ec);
                    if (spec.Key == "cloud_layer")
                    {
                        gfsSub = new TextBlock
                        {
                            Text = "", FontSize = 10, Opacity = .72,
                            TextTrimming = TextTrimming.CharacterEllipsis,
                            Visibility = Visibility.Collapsed,
                        };
                        ecSub = new TextBlock
                        {
                            Text = "", FontSize = 10, Opacity = .72,
                            TextTrimming = TextTrimming.CharacterEllipsis,
                            Visibility = Visibility.Collapsed,
                        };
                        gfsStack.Children.Add(gfsSub);
                        ecStack.Children.Add(ecSub);
                    }
                    Grid.SetColumn(gfsStack, 0);
                    Grid.SetColumn(ecStack, 1);
                    pair.Children.Add(gfsStack);
                    pair.Children.Add(ecStack);
                    card.Children.Add(pair);
                }
                var caption = new TextBlock
                {
                    Text = spec.Caption, FontSize = 9, Opacity = .6, TextWrapping = TextWrapping.Wrap,
                };
                card.Children.Add(caption);
                var border = new Border
                {
                    Child = card, Padding = new Thickness(2),
                    CornerRadius = new CornerRadius(14),
                    Background = ThemeBrush("FireSkyCardBrush"),
                    BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
                    BorderThickness = new Thickness(1),
                };
                Grid.SetColumn(border, index - start);
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                row.Children.Add(border);
                metricCards[spec.Key] = new MetricCard
                {
                    Gfs = gfs, Ec = ec, GfsSub = gfsSub, EcSub = ecSub, Caption = caption, Source = source,
                };
            }
            section.Children.Add(row);
            MetricHost.Children.Add(section);
        }
    }

    private void ResetMetricCards()
    {
        foreach (var card in metricCards.Values)
        {
            card.Gfs.Text = "--";
            card.Ec.Text = card.Gfs == card.Ec ? "--" : "--";
            if (card.GfsSub != null) { card.GfsSub.Text = ""; card.GfsSub.Visibility = Visibility.Collapsed; }
            if (card.EcSub != null) { card.EcSub.Text = ""; card.EcSub.Visibility = Visibility.Collapsed; }
            card.Source.Text = "";
            card.Caption.Opacity = .6;
        }
    }

    private void RenderMetrics()
    {
        var aod = PointAod;
        foreach (var spec in Metrics)
        {
            if (!metricCards.TryGetValue(spec.Key, out var card)) continue;
            if (spec.Key == "aod")
            {
                card.Source.Text = aod.Source;
                card.Gfs.Text = aod.Value;
                card.Ec.Text = aod.Value;
                card.Caption.Text = aod.Caption;
                ToolTipService.SetToolTip(card.Gfs, aod.Detail);
                continue;
            }
            var gfsReport = ModelReport("gfs");
            var ecReport = ModelReport("ec");
            card.Gfs.Text = MetricValue(spec.Key, gfsReport, aod);
            card.Ec.Text = MetricValue(spec.Key, ecReport, aod);
            if (spec.Key == "cloud_layer")
            {
                card.Caption.Text = L10n.T("云底–云顶为海拔 ASL 口径，「云层 AGL」为离地口径");
                ApplyGenusSub(card.GfsSub, gfsReport);
                ApplyGenusSub(card.EcSub, ecReport);
            }
        }
    }

    private static void ApplyGenusSub(TextBlock? target, JsonObject? report)
    {
        if (target == null) return;
        var genus = CloudGenusBrief(report?["cloud_genus_proxy"] as JsonObject, report);
        if (genus != null)
        {
            target.Text = genus;
            target.Visibility = Visibility.Visible;
            ToolTipService.SetToolTip(target,
                L10n.T("依据压力层、相态与云层几何的模型云属代理，不代表卫星或人工云属观测。"));
        }
        else
        {
            target.Text = "";
            target.Visibility = Visibility.Collapsed;
        }
    }

    // ---------- 评分组成（只读后端分量） ----------

    private void RenderBreakdown()
    {
        BreakdownHost.Children.Clear();
        var hasContent = false;
        BreakdownCard.Visibility = Visibility.Visible;
        foreach (var model in new[] { "gfs", "ec" })
        {
            var error = ModelError(model);
            var report = ModelReport(model);
            if (error != null)
            {
                BreakdownHost.Children.Add(new TextBlock
                {
                    Text = $"{model.ToUpperInvariant()} 未完成：{error}",
                    FontSize = 10, Foreground = new SolidColorBrush(Colors.Red),
                    TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
                });
                hasContent = true;
                continue;
            }
            if (report == null)
            {
                BreakdownHost.Children.Add(new TextBlock
                {
                    Text = $"{model.ToUpperInvariant()} 暂无结果", FontSize = 10, Opacity = .65,
                });
                hasContent = true;
                continue;
            }
            BreakdownHost.Children.Add(BuildBreakdownCard(model, report));
            hasContent = true;
        }
        BreakdownCard.Visibility = hasContent ? Visibility.Visible : Visibility.Collapsed;
    }

    private static string SignedDelta(JsonNode? value)
    {
        if (DoubleValue(value) is not { } number) return "—";
        if (Math.Abs(number) < 0.0005) return "0.000";
        return number.ToString("+0.000;-0.000", CultureInfo.InvariantCulture);
    }

    private static Brush DeltaBrush(string delta) =>
        delta is not ("—" or "0.000") && delta.StartsWith('-')
            ? new SolidColorBrush(Colors.Red)
            : delta == "—" || delta == "0.000"
                ? (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"]
                : new SolidColorBrush(Colors.Green);

    private static string NumText(JsonNode? node, int digits, string suffix = "", string sign = "")
    {
        if (DoubleValue(node) is not { } value) return "—";
        return sign + value.ToString($"0.{new string('0', digits)}", CultureInfo.InvariantCulture) + suffix;
    }

    private static string QualityText(JsonNode? node, int digits) =>
        DoubleValue(node) is { } value ? value.ToString($"0.{new string('0', digits)}", CultureInfo.InvariantCulture) : "—";

    private FrameworkElement BuildBreakdownCard(string model, JsonObject report)
    {
        var components = report["quality_components"] as JsonObject ?? new JsonObject();
        var modelSource = Text(report, "model_source") is { Length: > 0 } source ? source : model;
        var panel = new StackPanel { Spacing = 8 };
        var head = new Grid { ColumnSpacing = 8 };
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        head.Children.Add(new StackPanel
        {
            Orientation = Orientation.Horizontal, Spacing = 6,
            Children =
            {
                new Ellipse { Width = 6, Height = 6, Fill = ModelBrush(model) },
                new TextBlock
                {
                    Text = model.ToUpperInvariant(), FontSize = 11,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                },
            },
        });
        head.Children.Add(new TextBlock
        {
            Text = $"{QualityText(report["quality_index"], 3)} / 2.5",
            FontSize = 12, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Foreground = ModelBrush(model),
        });
        head.Children.Add(new TextBlock { Text = Text(report, "quality_level"), FontSize = 9, Opacity = .65 });
        var limiting = StringOf(components["quality_limiting_factor"]);
        if (limiting.Length > 0)
        {
            var limitingText = new TextBlock
            {
                Text = $"当前限制因素 {limiting}", FontSize = 9, Opacity = .65,
                TextTrimming = TextTrimming.CharacterEllipsis,
            };
            ToolTipService.SetToolTip(limitingText, limiting);
            Grid.SetColumn(limitingText, 4);
            head.Children.Add(limitingText);
        }
        for (var i = 0; i < head.Children.Count; i++) Grid.SetColumn((FrameworkElement)head.Children[i], i);
        panel.Children.Add(head);
        void AdjustmentRow(string title, string measured, string? delta)
        {
            var row = new Grid { ColumnSpacing = 10, Padding = new Thickness(0, 5, 0, 5) };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(64) });
            row.Children.Add(new TextBlock { Text = title, FontSize = 10, Opacity = .7 });
            row.Children.Add(new TextBlock { Text = measured, FontSize = 10, TextWrapping = TextWrapping.Wrap });
            var deltaText = new TextBlock
            {
                Text = delta ?? "—", FontSize = 11,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                Foreground = DeltaBrush(delta ?? "—"),
                HorizontalAlignment = HorizontalAlignment.Right,
            };
            Grid.SetColumn(deltaText, 2);
            row.Children.Add(deltaText);
            panel.Children.Add(row);
        }
        AdjustmentRow(L10n.T("云量基础分"),
            $"有效可烧 {RatioText(components["burnable_cloud_coverage_effective"])} · 可烧载荷 {QualityText(components["forward_burnable_cloud_load_effective"] ?? components["forward_burnable_cloud_load_peak"], 2)} · 前方总云场 {RatioText(components["forward_view_cloud_field_amount_peak"])}",
            SignedDelta(components["coverage_base_score"] ?? components["coverage_score"]));
        AdjustmentRow(L10n.T("可烧云比例"),
            $"可烧/总云量 {RatioText(components["forward_burnable_cloud_ratio_peak"])} · 前方可烧云量 {RatioText(components["forward_burnable_cloud_field_amount_peak"])}", null);
        AdjustmentRow(L10n.T("几何遮挡（已计入可烧量）"),
            $"最佳云入射遮挡 {RatioText(components["solar_incoming_block_risk_at_best_cloud"] ?? components["solar_incoming_block_risk_mean"])} · 不重复扣分", null);
        AdjustmentRow(L10n.T("持续时间修正"),
            $"{QualityText(report["duration_minutes"] ?? components["duration_minutes"], 0)} 分钟 · ×{QualityText(components["duration_factor"], 3)}",
            SignedDelta(components["duration_adjustment"]));
        AdjustmentRow(L10n.T("入射 AOD 修正"),
            $"本地 AOD {QualityText(components["quality_aod550"], 3)} · 入射因子 ×{QualityText(components["incoming_aod_illumination_factor"], 3)}",
            SignedDelta(components["incoming_aod_adjustment"]));
        AdjustmentRow(L10n.T("观测 AOD 修正"),
            $"观测对比因子 ×{QualityText(components["observer_aod_contrast_factor"], 3)}",
            SignedDelta(components["observer_aod_adjustment"]));
        void CapRow(string title, string value)
        {
            var row = new Grid { ColumnSpacing = 6 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.Children.Add(new TextBlock { Text = title, FontSize = 9, Opacity = .65 });
            row.Children.Add(new TextBlock
            {
                Text = value, FontSize = 9, FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                TextWrapping = TextWrapping.Wrap,
            });
            Grid.SetColumn((FrameworkElement)row.Children[1], 1);
            panel.Children.Add(row);
        }
        CapRow(L10n.T("修正后原始分"), QualityText(components["raw_quality_index"], 3));
        CapRow(L10n.T("云量基础分"), QualityText(components["coverage_base_score"] ?? components["coverage_score"], 3));
        CapRow(L10n.T("分析范围"), NumText(components["forward_field_radius_km"] ?? report["analysis_radius_km"], 0, " km"));
        CapRow(L10n.T("太阳方向扇区"), NumText(components["forward_sector_half_angle_deg"], 0, "°", "±"));
        CapRow(L10n.T("总云场来源"), modelSource == "ec" ? L10n.T("EC TCC + PL-RH 云柱代理") : L10n.T("GFS 全量云场"));
        CapRow(L10n.T("可烧量来源"), StringOf(components["quality_coverage_source"]) is { Length: > 0 } coverageSource
            ? coverageSource : L10n.T("前方全量云场"));
        CapRow(L10n.T("持续时间修正系数"), $"×{QualityText(components["duration_factor"], 3)}");
        CapRow(L10n.T("入射 / 观测回程因子"),
            $"×{QualityText(components["incoming_aod_illumination_factor"], 3)} / ×{QualityText(components["observer_aod_contrast_factor"], 3)}");
        CapRow(L10n.T("观测回程光学量"),
            $"τ {QualityText(components["observer_return_optical_depth_mean"], 3)} · T {QualityText(components["observer_return_transmission_mean"], 3)}");
        panel.Children.Add(new TextBlock
        {
            Text = L10n.T("几何遮挡已包含在可烧云量中，不再重复扣分；持续时间和 AOD 各修正一次。低 AOD 小幅加分，高 AOD 有符号扣分。可烧量只包含既受太阳入射照亮、又能被地面观测者看到的云底面积。摄影焦段、Top-K、代表云和云边界不进入主评分。"),
            FontSize = 9, Opacity = .6, TextWrapping = TextWrapping.Wrap,
        });
        return new Border
        {
            Child = panel,
            Padding = new Thickness(12),
            CornerRadius = new CornerRadius(12),
            Background = ThemeBrush("FireSkyGroupedCardBrush"),
            BorderBrush = ThemeBrush("FireSkyGroupedBorderBrush"),
            BorderThickness = new Thickness(1),
        };
    }

    // ---------- 对比曲线（缺测断线 + 悬停读数） ----------

    private void BuildChartPoints()
    {
        var points = new List<ChartPoint>();
        foreach (var model in new[] { "gfs", "ec" })
        {
            var report = ModelReport(model);
            if (report == null) continue;
            foreach (var step in report["steps"] as JsonArray ?? [])
            {
                if (step is not JsonObject row) continue;
                if (BoolValue(row, "valid") == false) continue;
                if (DoubleValue(row["instant_quality_index"]) is not { } quality) continue;
                var local = Text(row, "valid_time_local");
                var time = ParseStepTime(row, Timezone.Text);
                if (time == null) continue;
                points.Add(new ChartPoint(model, time.Value, quality, local));
            }
        }
        chartPoints = points.OrderBy(point => point.Time).ToList();
    }

    private static DateTimeOffset? ParseStepTime(JsonObject row, string timezone)
    {
        var utc = Text(row, "valid_time_utc");
        if (utc.Length > 0 && HistoryTime.TryParse(utc, out var parsed)) return parsed;
        var local = Text(row, "valid_time_local");
        if (local.Length >= 19 && DateTime.TryParseExact(local, "yyyy-MM-dd'T'HH:mm:ss",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out var localDt))
        {
            var zone = SafeZone(timezone);
            return new DateTimeOffset(localDt, zone.GetUtcOffset(localDt));
        }
        return null;
    }

    private static TimeZoneInfo SafeZone(string timezone)
    {
        try { return TimeZoneInfo.FindSystemTimeZoneById(timezone); }
        catch (Exception) { return TimeZoneInfo.Local; }
    }

    private void OnResultModelChanged(object sender, SelectionChangedEventArgs args)
    {
        if (!initialized) return;
        RenderResult();
        _ = UpdateMap();
    }

    private void OnChartSizeChanged(object sender, SizeChangedEventArgs args) => RenderChart();

    private void RenderChart()
    {
        if (QualityChart == null || !initialized) return;
        QualityChart.Children.Clear();
        BuildChartPoints();
        var points = chartPoints ?? [];
        ChartEmpty.Visibility = points.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        ChartLegend.Text = L10n.T("质量轴固定 0–2.5 · 缺测断线不补零    — GFS  - - EC");
        if (points.Count == 0) return;
        var width = Math.Max(1, ((FrameworkElement)QualityChart.Parent).ActualWidth);
        var height = 200.0;
        var start = points.Min(point => point.Time).Ticks;
        var endTicks = points.Max(point => point.Time).Ticks;
        var span = Math.Max(1, endTicks - start);
        double X(DateTimeOffset time) => (time.Ticks - start) / (double)span * width;
        double Y(double quality) => height * (1 - Math.Clamp(quality / 2.5, 0, 1));
        foreach (var y in new[] { 0.0, height / 2, height })
            QualityChart.Children.Add(new Line
            {
                X1 = 0, X2 = width, Y1 = y, Y2 = y, StrokeThickness = .5,
                Stroke = new SolidColorBrush(Colors.Gray), Opacity = .25,
            });
        // 按模型与时间间隙分段：缺测时次断线，不跨缺口连接。
        foreach (var model in new[] { "gfs", "ec" })
        {
            var series = points.Where(point => point.Model == model).ToList();
            if (series.Count == 0) continue;
            Polyline? segment = null;
            DateTimeOffset? previous = null;
            foreach (var point in series)
            {
                if (previous is { } last && (point.Time - last).TotalMinutes > 15)
                {
                    segment = null; // 缺口断线
                }
                segment ??= NewSegment(ModelBrush(model), model == "ec");
                segment.Points.Add(new Windows.Foundation.Point(X(point.Time), Y(point.Quality)));
                previous = point.Time;
            }
        }
        // 最佳时刻标记（菱形）。
        foreach (var model in new[] { "gfs", "ec" })
        {
            var series = points.Where(point => point.Model == model).ToList();
            if (series.Count == 0) continue;
            var best = series.OrderByDescending(point => point.Quality).First();
            QualityChart.Children.Add(new Rectangle
            {
                Width = 8, Height = 8,
                Fill = ModelBrush(model),
                RenderTransform = new TranslateTransform
                {
                    X = X(best.Time) - 4, Y = Y(best.Quality) - 4,
                },
            });
        }
        // 选中时刻竖线（点选台账时）。
        if (pointSelectedTime.Length > 0)
        {
            var selected = points.Where(point => point.Local == pointSelectedTime)
                .MinBy(point => point.Time);
            if (selected != null)
                QualityChart.Children.Add(new Line
                {
                    X1 = X(selected.Time), X2 = X(selected.Time), Y1 = 0, Y2 = height,
                    Stroke = (Brush)Application.Current.Resources["AccentFillColorDefaultBrush"],
                    StrokeThickness = 1, Opacity = .5,
                });
        }
    }

    private static Polyline NewSegment(Brush brush, bool dashed)
    {
        var line = new Polyline
        {
            StrokeThickness = 2,
            Stroke = brush,
            StrokeLineJoin = PenLineJoin.Round,
        };
        if (dashed) line.StrokeDashArray = [4, 3];
        return line;
    }

    private void OnChartPointerMoved(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs args)
    {
        if (chartPoints is not { Count: > 0 } points)
        {
            ChartToolTip.Visibility = Visibility.Collapsed;
            return;
        }
        var grid = (FrameworkElement)sender;
        var position = args.GetCurrentPoint(grid).Position;
        var width = Math.Max(1, grid.ActualWidth);
        var start = points.Min(point => point.Time).Ticks;
        var span = Math.Max(1, points.Max(point => point.Time).Ticks - start);
        var targetTicks = start + position.X / width * span;
        var target = new DateTimeOffset((long)targetTicks, TimeSpan.Zero);
        var nearest = points.MinBy(point => Math.Abs((point.Time - target).Ticks));
        if (nearest == null)
        {
            ChartToolTip.Visibility = Visibility.Collapsed;
            return;
        }
        ChartToolTipText.Text =
            $"{nearest.Model.ToUpperInvariant()} · {TimeLabel(nearest.Local)} · {nearest.Quality:0.000} / 2.5";
        ChartToolTip.Visibility = Visibility.Visible;
        ChartToolTip.RenderTransform = new TranslateTransform
        {
            X = Math.Max(0, Math.Min(width - 160, position.X + 12)),
            Y = Math.Max(0, position.Y - 36),
        };
    }

    private void OnChartPointerExited(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs args)
    {
        ChartToolTip.Visibility = Visibility.Collapsed;
    }

    // ---------- 逐时台账（15 列） ----------

    private sealed record LedgerColumn(string Title, double Width, Func<JsonObject, string> Render,
        bool WarnInvalid = true);

    private void RenderLedger()
    {
        LedgerHost.Children.Clear();
        var report = SelectedReport();
        var bestTime = Text(report, "best_time_local");
        LedgerColumn[] columns =
        [
            new(L10n.T("时间"), 88, item => TimeLabel(Text(item, "valid_time_local"))),
            new(L10n.T("质量"), 68, item => BoolValue(item, "valid") == false ? "—"
                : QualityText(item["instant_quality_index"], 3)),
            new(L10n.T("档位"), 92, item => BoolValue(item, "valid") == false ? L10n.T("缺测")
                : Text(item, "instant_quality_level") is { Length: > 0 } level ? level : "—"),
            new(L10n.T("可烧"), 52, item =>
            {
                var burnable = BoolValue(item, "physically_burnable");
                return BoolValue(item, "valid") == false || burnable == null ? "—"
                    : burnable == true ? "✓" : "×";
            }),
            new(L10n.T("可烧云量"), 84, item => BoolValue(item, "valid") == false ? "—"
                : RatioText(item["burnable_cloud_coverage"])),
            new(L10n.T("主导压力族"), 96, item => BoolValue(item, "valid") == false ? "—"
                : FamilyLabel(item["dominant_burnable_pressure_family"]) is { Length: > 0 } family
                    ? family : "—"),
            new(L10n.T("云底–云顶 ASL"), 120, item => BoolValue(item, "valid") == false ? "—"
                : AslRangeLabel(item)),
            new(L10n.T("云层 AGL"), 112, item => BoolValue(item, "valid") == false ? "—"
                : CloudLayerAgl(item)),
            new(L10n.T("亮度颜色"), 68, item => BoolValue(item, "valid") == false ? "—"
                : RatioText(item["brightness_color"])),
            new(L10n.T("可烧比例"), 68, item => BoolValue(item, "valid") == false ? "—"
                : RatioText(item["forward_burnable_cloud_ratio"])),
            new(L10n.T("回程透过率"), 76, item => BoolValue(item, "valid") == false ? "—"
                : RatioText(item["observer_return_transmission"])),
            new(L10n.T("入射阻挡"), 72, item => BoolValue(item, "valid") == false ? "—"
                : RatioText(item["solar_incoming_block_risk"])),
            new("AOD 550", 72, item => BoolValue(item, "valid") == false ? "—"
                : QualityText(item["quality_aod550"], 2)),
            new(L10n.T("太阳仰角"), 78, item => BoolValue(item, "valid") == false ? "—"
                : QualityText(item["solar_elevation_deg"], 1) + "°"),
            new(L10n.T("场景"), 138, item => BoolValue(item, "valid") == false ? "—"
                : Text(item, "selected_regime") is { Length: > 0 } scene ? scene : "—"),
        ];
        var header = BuildLedgerRow(null, columns, -1, "", false);
        LedgerHost.Children.Add(header);
        LedgerHost.Children.Add(new Border
        {
            Height = 1,
            Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
        });
        var allSteps = (report?["steps"] as JsonArray ?? []).OfType<JsonObject>().ToList();
        var hiddenInvalidCount = allSteps.Count(step => BoolValue(step, "valid") == false);
        var visibleSteps = showInvalidLedgerSteps
            ? allSteps
            : allSteps.Where(step => BoolValue(step, "valid") != false).ToList();
        for (var index = 0; index < visibleSteps.Count; index++)
        {
            var step = visibleSteps[index];
            var local = Text(step, "valid_time_local");
            var valid = BoolValue(step, "valid") != false;
            var rowButton = BuildLedgerRow(step, columns, index, bestTime, valid && local.Length > 0);
            if (local.Length > 0 && local == pointSelectedTime)
                rowButton.Background = (Brush)Application.Current.Resources["SubtleFillColorSecondaryBrush"];
            LedgerHost.Children.Add(rowButton);
            LedgerHost.Children.Add(new Border
            {
                Height = 1, Opacity = .5,
                Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
            });
        }
        if (allSteps.Count == 0)
        {
            LedgerHost.Children.Add(new TextBlock
            {
                Text = report == null ? L10n.T("等待预测") : L10n.T("本结果没有逐时台账。"),
                Opacity = .65, Padding = new Thickness(0, 24, 0, 24),
            });
        }
        else if (visibleSteps.Count == 0)
        {
            LedgerHost.Children.Add(new TextBlock
            {
                Text = L10n.T("当前时段内没有有效判断时次。"),
                Opacity = .65, Padding = new Thickness(0, 24, 0, 24),
            });
        }
        var footer = new Grid { Padding = new Thickness(0, 8, 0, 8), ColumnSpacing = 10 };
        footer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        footer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        footer.Children.Add(new TextBlock
        {
            Text = L10n.T("云底–云顶为海拔 ASL 口径，「云层 AGL」为离地口径；— 表示缺测，默认仅展示有效判断时次；点击行仅切换本地选中时刻，不驱动地图。"),
            FontSize = 9, Opacity = .6, TextWrapping = TextWrapping.Wrap,
            VerticalAlignment = VerticalAlignment.Center,
        });
        if (hiddenInvalidCount > 0)
        {
            var toggleText = showInvalidLedgerSteps
                ? L10n.T("隐藏未判断时次")
                : (L10n.IsEnglish
                    ? $"Show {hiddenInvalidCount} unevaluated"
                    : $"显示 {hiddenInvalidCount} 个未判断时次");
            var toggleButton = new Button
            {
                Content = toggleText,
                FontSize = 10,
                Padding = new Thickness(8, 3, 8, 3),
                BorderThickness = new Thickness(0),
                Background = new SolidColorBrush(Colors.Transparent),
                VerticalAlignment = VerticalAlignment.Center,
            };
            toggleButton.Click += (_, _) =>
            {
                showInvalidLedgerSteps = !showInvalidLedgerSteps;
                RenderLedger();
            };
            Grid.SetColumn(toggleButton, 1);
            footer.Children.Add(toggleButton);
        }
        LedgerHost.Children.Add(footer);
    }

    private Button BuildLedgerRow(JsonObject? item, LedgerColumn[] columns, int index,
        string bestTime, bool clickable)
    {
        var row = new Grid { ColumnSpacing = 0 };
        foreach (var column in columns)
        {
            var text = item == null ? column.Title
                : BoolValue(item, "valid") == false && column.WarnInvalid ? column.Render(item)
                : column.Render(item);
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(column.Width) });
            var cell = new TextBlock
            {
                Text = text,
                FontSize = 11,
                VerticalAlignment = VerticalAlignment.Center,
                Opacity = item == null ? .7 : 1,
                FontWeight = item == null ? Microsoft.UI.Text.FontWeights.SemiBold
                    : Microsoft.UI.Text.FontWeights.Normal,
            };
            if (item != null && column.Title == "时间" && Text(item, "valid_time_local") == bestTime
                && bestTime.Length > 0)
            {
                cell.Text = $"{cell.Text} ★";
                cell.Foreground = (Brush)Application.Current.Resources["AccentFillColorDefaultBrush"];
            }
            row.Children.Add(cell);
        }
        if (item != null && BoolValue(item, "valid") == false)
        {
            foreach (var child in row.Children.OfType<TextBlock>())
                if (child.Text.Length == 0 || child.Text == "—")
                    child.Opacity = .5;
        }
        var button = new Button
        {
            Content = row,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            Padding = new Thickness(0),
            BorderThickness = new Thickness(0),
            Background = new SolidColorBrush(Colors.Transparent),
            IsEnabled = clickable,
            MinWidth = 1284,
        };
        if (clickable && item != null)
        {
            var local = Text(item, "valid_time_local");
            button.Click += (_, _) =>
            {
                pointSelectedTime = pointSelectedTime == local ? "" : local;
                RenderChart();
                RenderLedger();
                _ = UpdateMap();
            };
        }
        return button;
    }

    private static string AslRangeLabel(JsonObject item)
    {
        var baseA = DoubleValue(item["dominant_burnable_cloud_base_asl_mean_m"]);
        var topA = DoubleValue(item["dominant_burnable_cloud_top_asl_mean_m"]);
        return baseA is { } b && topA is { } t ? $"{b / 1000:0.0}–{t / 1000:0.0} km" : "—";
    }

    private static string CloudLayerAgl(JsonObject item)
    {
        var baseAgl = DoubleValue(item["dominant_burnable_cloud_base_agl_mean_m"]);
        var topAgl = DoubleValue(item["dominant_burnable_cloud_top_agl_mean_m"]);
        return baseAgl is { } b && topAgl is { } t
            ? $"{(int)Math.Round(b)}–{(int)Math.Round(t)} m" : "—";
    }

    // ---------- 诊断与时间窗 ----------

    private static string DiagnosticText(JsonNode? value, int digits = 2, string suffix = "",
        bool percent = false)
    {
        if (value is System.Text.Json.Nodes.JsonValue v)
        {
            switch (v.GetValueKind())
            {
                case System.Text.Json.JsonValueKind.Null or System.Text.Json.JsonValueKind.Undefined:
                    return "—";
                case System.Text.Json.JsonValueKind.String:
                    return v.GetValue<string>() + suffix;
                default:
                    if (DoubleValue(v) is not { } number) return "—";
                    return percent
                        ? $"{number * 100:0}%"
                        : number.ToString($"0.{new string('0', digits)}", CultureInfo.InvariantCulture) + suffix;
            }
        }
        if (value is JsonArray list)
            return string.Join("、", list.Select(node => DiagnosticText(node, digits, suffix, percent)));
        if (value is JsonObject obj)
            return string.Join(" · ", obj.OrderBy(pair => pair.Key)
                .Select(pair => $"{pair.Key}={DiagnosticText(pair.Value, digits, suffix, percent)}"));
        return "—";
    }

    private void RenderDiagnostics()
    {
        DiagnosticPanel.Children.Clear();
        var report = SelectedReport();
        if (report == null)
        {
            DiagnosticPanel.Children.Add(new TextBlock
            {
                Text = L10n.T("等待预测"), Opacity = .65, Padding = new Thickness(0, 24, 0, 24),
            });
            return;
        }
        DiagnosticPanel.Children.Add(new TextBlock
        {
            Text = Text(report, "summary") is { Length: > 0 } summary ? summary : "—",
            TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
        });
        void Group(string title, params (string, string)[] rows)
        {
            var panel = new StackPanel { Spacing = 5 };
            panel.Children.Add(new TextBlock
            {
                Text = title, FontSize = 11, Opacity = .7,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            foreach (var (label, value) in rows)
            {
                var line = new Grid { ColumnSpacing = 12 };
                line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) });
                line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                line.Children.Add(new TextBlock { Text = label, FontSize = 11, Opacity = .65 });
                var text = new TextBlock
                {
                    Text = value, FontSize = 11, IsTextSelectionEnabled = true,
                    TextWrapping = TextWrapping.Wrap,
                };
                Grid.SetColumn(text, 1);
                line.Children.Add(text);
                panel.Children.Add(line);
            }
            DiagnosticPanel.Children.Add(new Border
            {
                Child = panel, Padding = new Thickness(14), CornerRadius = new CornerRadius(14),
                Background = ThemeBrush("FireSkyCardBrush"),
                BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
                BorderThickness = new Thickness(1),
            });
        }
        Group(L10n.T("运行与时间"),
            (L10n.T("预测周期"), DiagnosticText(report["cycle_time_utc"])),
            (L10n.T("本地锚点"), DiagnosticText(report["anchor_local"])),
            (L10n.T("时区"), DiagnosticText(report["timezone_name"])),
            (L10n.T("模式"), DiagnosticText(report["forecast_mode"])),
            (L10n.T("计算后端"), DiagnosticText(report["compute_backend"])),
            (L10n.T("分析半径"), DiagnosticText(report["analysis_radius_km"], 0, " km")),
            (L10n.T("选中场景"), Text(report, "selected_regime") is { Length: > 0 } regime ? regime : "—"),
            (L10n.T("持续时长"), DiagnosticText(report["duration_minutes"], 0, L10n.T(" 分钟"))),
            (L10n.T("跨模式分歧"), L10n.T("当前后端未计算，GFS/EC 请并列比较")));
        if (report["cache"] is JsonObject cache)
            Group(L10n.T("缓存状态"),
                (L10n.T("状态"), DiagnosticText(cache["status"])),
                (L10n.T("生成时间（UTC）"), DiagnosticText(cache["generated_at_utc"])));
        Group(L10n.T("云体几何与可解释性"),
            (L10n.T("几何代理"), DiagnosticText(report["cloud_geometry_proxy_source"])),
            (L10n.T("几何支持样本"), DiagnosticText(report["cloud_geometry_support_count"], 0)),
            (L10n.T("几何不确定度"), DiagnosticText(report["cloud_geometry_uncertainty_m"], 0, " m")),
            (L10n.T("几何支持度"), DiagnosticText(report["cloud_geometry_confidence"], percent: true)),
            (L10n.T("置信度语义"), DiagnosticText(report["confidence_kind"] ?? report["confidence_score_semantics"])),
            (L10n.T("质量指数语义"), DiagnosticText(report["quality_index_semantics"])),
            (L10n.T("主压力族"), FamilyLabel(report["dominant_burnable_pressure_family"]) is { Length: > 0 } family
                ? family : "—"));
        Group(L10n.T("云相与云族诊断"),
            (L10n.T("云相"), DiagnosticText(report["cloud_phase_diagnosis"])),
            (L10n.T("云族说明"), DiagnosticText(report["cloud_family_diagnosis"])));
        if (report["cloud_genus_proxy"] is JsonObject || report["cloud_phase_proxy"] is JsonObject)
        {
            var proxyRows = new List<(string, string)>();
            if (report["cloud_genus_proxy"] is JsonObject genusProxy)
            {
                var label = Text(genusProxy, "label");
                if (label.Length > 0 && label != "unknown")
                {
                    var conf = DoubleValue(genusProxy["confidence"]) is { } c ? $" ({(int)Math.Round(c * 100)}%)" : "";
                    proxyRows.Add((L10n.T("云属代理"), GenusLabel(label) + conf));
                }
                else if (CloudGenusBrief(genusProxy, report) is { } compat)
                {
                    proxyRows.Add((L10n.T("云属未定"), compat));
                }
                if (Text(genusProxy, "source") is { Length: > 0 } gSrc)
                    proxyRows.Add((L10n.T("推断依据"), gSrc));
                if (BoolValue(genusProxy, "scoring_effect") == false)
                    proxyRows.Add((L10n.T("评分影响"), L10n.T("不参与评分")));
            }
            if (report["cloud_phase_proxy"] is JsonObject phaseProxy)
            {
                var pLabel = Text(phaseProxy, "label");
                var pSource = Text(phaseProxy, "source");
                if (pLabel == "unknown" && pSource == "not-resolved-for-native-burnable-target")
                {
                    proxyRows.Add((L10n.T("相态"), L10n.T("未解析（GFS 桌面点预测不解析目标云相态）")));
                }
                else if (pLabel.Length > 0)
                {
                    var ice = DoubleValue(phaseProxy["ice_fraction"]) is { } iceVal
                        ? $" · {L10n.T("冰含量占比")} {(int)Math.Round(iceVal * 100)}%" : "";
                    proxyRows.Add((L10n.T("相态"), PhaseLabel(pLabel) + ice));
                }
            }
            if (proxyRows.Count > 0)
                Group(L10n.T("云属与相态（模型代理 · 不参与评分）"), [.. proxyRows]);
        }
        if (report["scene_scores"] is JsonObject scores && (scores as IDictionary<string, JsonNode?>).Count > 0)
        {
            Group(L10n.T("场景分数"), scores.OrderBy(pair => pair.Key)
                .Select(pair => (pair.Key, DiagnosticText(pair.Value, percent: true))).ToArray());
        }
        if (report["nowcast"] == null)
        {
            Group(L10n.T("共享诊断"),
                (L10n.T("AOD 修正语义"), DiagnosticText(report["aod_adjustment_semantics"])),
                (L10n.T("模型身份"), DiagnosticText(report["model_identity"])));
        }
        if (report["aerosol"] is JsonObject aerosol)
            Group(L10n.T("气溶胶来源"),
                ("AOD 550", DiagnosticText(aerosol["aod550"], 3)),
                (L10n.T("来源"), DiagnosticText(aerosol["source"])),
                (L10n.T("数据质量"), DiagnosticText(aerosol["quality"])));
        if (report["profile_source"] is JsonObject profileSource)
        {
            var available = BoolValue(profileSource, "available") == true;
            Group(L10n.T("剖面能力"),
                (L10n.T("冻结剖面"), available ? L10n.T("可查询") : L10n.T("不可用")),
                (L10n.T("原因"), available ? "—" : DiagnosticText(profileSource["reason"])),
                (L10n.T("冻结源有效期"), DiagnosticText(profileSource["expires_at_utc"])));
        }
        if (report["data_provenance"] is JsonObject provenance
            && (provenance as IDictionary<string, JsonNode?>).Count > 0)
        {
            Group(L10n.T("数据来源"), provenance.OrderBy(pair => pair.Key)
                .Select(pair => (pair.Key, DiagnosticText(pair.Value))).ToArray());
        }
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        if (Text(report, "html_report_path") is { Length: > 0 } htmlPath)
        {
            var open = new Button { Content = L10n.T("完整报告"), Padding = new Thickness(10, 4, 10, 4) };
            open.Click += (_, _) => System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(htmlPath) { UseShellExecute = true });
            actions.Children.Add(open);
        }
        if (report["map_capability"] is JsonObject mapCapability
            && BoolValue(mapCapability, "available") == false
            && Text(mapCapability, "reason") != "native-nowcast-map-publication-not-requested")
        {
            actions.Children.Add(new TextBlock
            {
                Text = FriendlyMessages.Map(Text(mapCapability, "reason")),
                FontSize = 10, Opacity = .7, TextWrapping = TextWrapping.Wrap,
                VerticalAlignment = VerticalAlignment.Center,
            });
        }
        if (actions.Children.Count > 0) DiagnosticPanel.Children.Add(actions);
    }

    // ---------- 大气剖面页（点预测冻结剖面查询 + 剖面图层） ----------

    private void RenderPointProfileTab()
    {
        var report = SelectedReport();
        ProfileStatus.Text = L10n.T("周期 ") + Text(report, "cycle_time_utc");
        var source = report?["profile_source"] as JsonObject;
        var available = source != null && BoolValue(source, "available") == true;
        FrozenProfileButton.Visibility = available && resultKind == "point"
            ? Visibility.Visible : Visibility.Collapsed;
        if (frozenProfileState == "idle")
        {
            PointProfileNote.Text = available
                ? L10n.T("点击「读取冻结剖面」查看该结果的太阳方向诊断。")
                : L10n.T("该结果没有可查询的冻结剖面。");
            PointCrossSection.Visibility = Visibility.Collapsed;
        }
        RenderFrozenProfile();
    }

    private async void OnQueryPointProfile(object sender, RoutedEventArgs args)
    {
        await QueryPointProfile();
    }

    private async Task QueryPointProfile()
    {
        var selectedModel = TagOf(ResultModel.SelectedItem);
        if (selectedModel is not ("gfs" or "ec")) selectedModel = "gfs";
        if (resultKind != "point" || ModelReport(selectedModel) is not { } report)
        {
            frozenProfileState = "unavailable";
            frozenProfileError = L10n.T("该结果没有可读取的冻结剖面时次。");
            RenderFrozenProfile();
            return;
        }
        var model = Text(report["profile_source"], "model_source") is { Length: > 0 } sourceModel
            && sourceModel is "gfs" or "ec" ? sourceModel : selectedModel;
        var validTime = Text(report, "best_time_local") is { Length: > 0 } best ? best
            : StringOrNull((report["cross_section"] as JsonObject)?["valid_time_local"]) is { Length: > 0 } sectionTime
                ? sectionTime : Text(report, "anchor_local");
        await RunFrozenProfileQuery(model, Latitude.Value, Longitude.Value, validTime, report, point: true);
    }

    /// <summary>profile_query 构建与校验（对齐 ProfileQueryBuilder）：point 请求优先用冻结源 observer 坐标。</summary>
    private ProfileQuerySpec? BuildPointProfileQuery(JsonObject report, string model,
        double lat, double lon, string localTime)
    {
        if ((report["cross_section"] as JsonObject)?["observer"] is JsonObject observer
            && DoubleValue(observer["lat"]) is { } frozenLat && double.IsFinite(frozenLat)
            && DoubleValue(observer["lon"]) is { } frozenLon && double.IsFinite(frozenLon))
        {
            lat = frozenLat;
            lon = frozenLon;
        }
        return BuildProfileQuery(report, model, lat, lon, localTime);
    }

    private async Task RunFrozenProfileQuery(string model, double lat, double lon,
        string validTimeLocal, JsonObject report, bool point = false)
    {
        var request = point
            ? BuildPointProfileQuery(report, model, lat, lon, validTimeLocal)
            : BuildProfileQuery(report, model, lat, lon, validTimeLocal);
        frozenProfile = null;
        frozenProfileModel = model;
        frozenProfileTime = validTimeLocal;
        frozenProfileError = "";
        if (request == null)
        {
            frozenProfileState = "unavailable";
            frozenProfileError = L10n.T("该结果没有可读取的冻结剖面时次。");
            RenderFrozenProfile();
            return;
        }
        frozenProfileOperation?.Cancel();
        frozenProfileOperation?.Dispose();
        frozenProfileOperation = new CancellationTokenSource();
        var token = frozenProfileOperation.Token;
        frozenProfileState = "loading";
        RenderFrozenProfile();
        try
        {
            var response = await engine.Call("profile_query", request.Payload, cancellation: token);
            token.ThrowIfCancellationRequested();
            if (!ProfileResponseMatches(response, request))
                throw new InvalidOperationException(L10n.T("剖面响应与当前结果、模式、坐标或时次不匹配。"));
            if (!SameUtc(Text(response, "cycle_utc"), request.CycleUtc))
                throw new InvalidOperationException(L10n.T("剖面响应的模式周期与已发布结果不匹配。"));
            var status = Text(response, "status");
            if (status != "ready" || response["cross_section"] is not JsonObject section)
            {
                frozenProfileState = string.IsNullOrWhiteSpace(status) ? "unavailable" : status;
                frozenProfileError = Text(response, "reason") is { Length: > 0 } reason
                    ? reason : L10n.T("所选时次没有可用剖面。");
            }
            else
            {
                frozenProfile = section;
                frozenProfileState = "ready";
            }
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
            frozenProfileState = "cancelled";
            frozenProfileError = L10n.T("剖面读取已取消。");
        }
        catch (Exception error)
        {
            frozenProfileState = "error";
            frozenProfileError = FriendlyMessages.Map(error.Message);
        }
        RenderFrozenProfile();
    }

    private void RenderFrozenProfile()
    {
        if (frozenProfileState != "ready" || frozenProfile == null)
        {
            var message = frozenProfileState switch
            {
                "loading" => L10n.T("正在读取冻结剖面…"),
                "idle" => L10n.T("点击「读取冻结剖面」查看该结果的太阳方向诊断。"),
                "cancelled" => frozenProfileError,
                "unavailable" => frozenProfileError,
                _ => frozenProfileError.Length > 0 ? frozenProfileError : L10n.T("该结果没有可用剖面。"),
            };
            PointProfileNote.Text = message;
            PointProfileNote.Foreground = frozenProfileState is "error" or "unavailable"
                ? new SolidColorBrush(Colors.Red)
                : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            PointCrossSection.Visibility = Visibility.Collapsed;
            return;
        }
        var fields = frozenProfile["fields"] as JsonObject;
        var distanceCount = (frozenProfile["distances_km"] as JsonArray)?.Count ?? 0;
        PointProfileNote.Text =
            $"{Text(frozenProfile, "valid_time_local")}".Replace("T", " ")
            + $" · 太阳高度 {Number(frozenProfile, "solar_elevation_deg"):0.0}°"
            + $" · 太阳方位 {Number(frozenProfile, "solar_azimuth_deg"):0.0}°"
            + $" · {distanceCount} 个距离样本"
            + $" · 云量样本 {NestedArrayCount(fields?["cloud_fraction_percent"])}"
            + $" · 温度样本 {NestedArrayCount(fields?["temperature_c"])}";
        PointProfileNote.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        PointCrossSection.Visibility = Visibility.Visible;
        DrawCrossSection(PointCrossSection, frozenProfile);
    }

    internal static int NestedArrayCount(JsonNode? node) =>
        (node as JsonArray)?.OfType<JsonArray>().Sum(row => row.Count)
        ?? (node as JsonArray)?.Count ?? 0;

    // ---------- 剖面图层绘制（距离×高度，缺测层不绘制） ----------

    internal static void DrawCrossSection(Canvas canvas, JsonObject section)
    {
        canvas.Children.Clear();
        var distances = (section["distances_km"] as JsonArray ?? [])
            .Select(node => Number(node, "x")).Where(value => double.IsFinite(value)).ToList();
        if (distances.Count == 0) return;
        var fields = section["fields"] as JsonObject ?? new JsonObject();
        double?[][] Grid(string key) =>
            (fields[key] as JsonArray ?? [])
                .Select(row => (row as JsonArray ?? [])
                    .Select(cell => DoubleValue(cell)).ToArray()).ToArray();
        var heights = Grid("geopotential_height_asl_m");
        var temps = Grid("temperature_c");
        var rh = Grid("rh_percent");
        var amounts = Grid("cloud_amount");
        var presence = Grid("cloud_presence");
        var fractions = Grid("cloud_fraction_percent");
        var familyCodes = Grid("pressure_family_code");
        var statuses = Grid("cloud_layer_status");
        var terrain = (section["terrain_elevation_asl_m"] as JsonArray ?? [])
            .Select(node => Number(node, "x")).Where(value => double.IsFinite(value)).ToList();
        var required = (section["required_illumination_base_asl_m"] as JsonArray ?? [])
            .Select(node => Number(node, "x")).Where(value => double.IsFinite(value)).ToList();
        var levels = (section["pressure_levels_hpa"] as JsonArray ?? [])
            .Select(node => (int?)DoubleValue(node) ?? 0).ToList();

        var width = Math.Max(120, canvas.ActualWidth is > 120 and var w ? w : 640);
        var height = Math.Max(120, canvas.ActualHeight);
        double left = 40, right = width - 12, top = 10, bottom = height - 24;
        var maxDistance = Math.Max(1, distances[^1]);
        const double maxHeight = 18_000.0;
        double X(double distance) => left + distance / maxDistance * (right - left);
        double Y(double altitude) => top + (1 - Math.Clamp(altitude, 0, maxHeight) / maxHeight) * (bottom - top);

        // 高度网格与标签
        for (var altitude = 0; altitude <= maxHeight; altitude += 3000)
        {
            canvas.Children.Add(new Line
            {
                X1 = left, X2 = right, Y1 = Y(altitude), Y2 = Y(altitude),
                StrokeThickness = .6, Opacity = .22, Stroke = new SolidColorBrush(Colors.Gray),
            });
            canvas.Children.Add(new TextBlock
            {
                Text = $"{altitude / 1000:0}km", FontSize = 8, Opacity = .6,
                RenderTransform = new TranslateTransform
                {
                    X = 2, Y = Y(altitude) - 6,
                },
            });
        }
        // 距离网格
        for (var index = 0; index < distances.Count; index += 4)
        {
            canvas.Children.Add(new Line
            {
                X1 = X(distances[index]), X2 = X(distances[index]), Y1 = top, Y2 = bottom,
                StrokeThickness = .6, Opacity = .15, Stroke = new SolidColorBrush(Colors.Gray),
            });
            canvas.Children.Add(new TextBlock
            {
                Text = $"{(int)distances[index]}km", FontSize = 8, Opacity = .6,
                RenderTransform = new TranslateTransform
                {
                    X = X(distances[index]) - 12, Y = bottom + 2,
                },
            });
        }
        // 地形填充与观测点
        if (terrain.Count == distances.Count)
        {
            var polygon = new Polygon
            {
                Fill = new SolidColorBrush(ColorFrom(0x69, 0x73, 0x70)) { Opacity = .18 },
                StrokeThickness = 0,
            };
            polygon.Points.Add(new Windows.Foundation.Point(X(distances[0]), Y(terrain[0])));
            for (var index = 1; index < distances.Count; index++)
                polygon.Points.Add(new Windows.Foundation.Point(X(distances[index]), Y(terrain[index])));
            polygon.Points.Add(new Windows.Foundation.Point(right, bottom));
            polygon.Points.Add(new Windows.Foundation.Point(left, bottom));
            canvas.Children.Add(polygon);
            var line = new Polyline
            {
                Stroke = new SolidColorBrush(ColorFrom(0x69, 0x73, 0x70)),
                StrokeThickness = 1.6,
            };
            for (var index = 0; index < distances.Count; index++)
                line.Points.Add(new Windows.Foundation.Point(X(distances[index]), Y(terrain[index])));
            canvas.Children.Add(line);
            canvas.Children.Add(new Ellipse
            {
                Width = 10, Height = 10,
                Fill = new SolidColorBrush(ColorFrom(0xF2, 0x7D, 0x42)),
                RenderTransform = new TranslateTransform
                {
                    X = X(distances[0]) - 5, Y = Y(terrain[0]) - 5,
                },
            });
            canvas.Children.Add(new TextBlock
            {
                Text = L10n.T("观测点"), FontSize = 8, Opacity = .7,
                RenderTransform = new TranslateTransform
                {
                    X = X(distances[0]) + 10, Y = Y(terrain[0]) - 16,
                },
            });
        }
        // 云量/湿度色块 + 云层状态描边（缺测层不绘制，不解释成晴空）
        var sampleCount = Math.Max(1, distances.Count - 1);
        var columnSpan = (right - left) / sampleCount;
        var cellWidth = Math.Max(6, columnSpan * 0.72);
        for (var i = 0; i < distances.Count; i++)
        {
            for (var l = 0; l < levels.Count && l < heights[i].Length; l++)
            {
                if (heights[i][l] is not { } altitude) continue;
                var centerX = X(distances[i]);
                var topY = Y(altitude + 700);
                var cellHeight = Math.Max(5, Y(altitude - 700) - topY);
                var fill = CloudCellFill(amounts[i].Length > l ? amounts[i][l] : null,
                    presence[i].Length > l ? presence[i][l] : null,
                    rh[i].Length > l ? rh[i][l] : null,
                    fractions[i].Length > l ? fractions[i][l] : null,
                    (int)(familyCodes[i].Length > l ? familyCodes[i][l] ?? 0 : 0));
                if (fill != null)
                    canvas.Children.Add(new Rectangle
                    {
                        Width = cellWidth, Height = cellHeight,
                        Fill = fill,
                        RenderTransform = new TranslateTransform
                        {
                            X = centerX - cellWidth / 2, Y = topY,
                        },
                    });
                var status = (int)(statuses[i].Length > l ? statuses[i][l] ?? 0 : 0);
                if (status == 3 || status == 2)
                {
                    var stroke = new Rectangle
                    {
                        Width = cellWidth, Height = cellHeight,
                        StrokeThickness = status == 3 ? 1.8 : 1.4,
                        Stroke = status == 3
                            ? new SolidColorBrush(ColorFrom(0xF0, 0x6B, 0x36))
                            : new SolidColorBrush(ColorFrom(0xD4, 0x8C, 0x38)),
                        StrokeDashArray = status == 2 ? [4, 3] : null,
                        Fill = null,
                        RenderTransform = new TranslateTransform
                        {
                            X = centerX - cellWidth / 2, Y = topY,
                        },
                    };
                    canvas.Children.Add(stroke);
                }
            }
        }
        // 温度层线（按层平均温度着色）
        for (var l = 0; l < levels.Count; l++)
        {
            var line = new Polyline
            {
                StrokeThickness = 1.1, Opacity = .62,
            };
            var tempsSeen = new List<double>();
            for (var i = 0; i < distances.Count && i < heights.Length; i++)
            {
                if (heights[i].Length <= l || heights[i][l] is not { } altitude) continue;
                line.Points.Add(new Windows.Foundation.Point(X(distances[i]), Y(altitude)));
                if (i < temps.Length && l < temps[i].Length && temps[i][l] is { } temp)
                    tempsSeen.Add(temp);
            }
            if (line.Points.Count <= 1) continue;
            var average = tempsSeen.Count == 0 ? 0 : tempsSeen.Average();
            line.Stroke = new SolidColorBrush(average < -30 ? ColorFrom(0x8C, 0x66, 0x73)
                : average < 0 ? ColorFrom(0xB0, 0x6B, 0x5C) : ColorFrom(0xC7, 0x4F, 0x38));
            canvas.Children.Add(line);
            if (l % 2 == 0 && line.Points.Count > 0)
            {
                var last = line.Points[^1];
                canvas.Children.Add(new TextBlock
                {
                    Text = $"{levels[l]}hPa", FontSize = 8, Opacity = .6,
                    RenderTransform = new TranslateTransform
                    {
                        X = Math.Max(0, last.X - 34), Y = last.Y - 12,
                    },
                });
            }
        }
        // 理论照明基准（ASL，琥珀虚线）
        if (required.Count == distances.Count)
        {
            var line = new Polyline
            {
                Stroke = new SolidColorBrush(ColorFrom(0xD4, 0x8C, 0x38)),
                StrokeThickness = 1.2,
                StrokeDashArray = [4, 3],
            };
            for (var index = 0; index < distances.Count; index++)
                line.Points.Add(new Windows.Foundation.Point(X(distances[index]), Y(required[index])));
            canvas.Children.Add(line);
        }
    }

    private static Brush? CloudCellFill(double? amount, double? presence, double? rh,
        double? fraction, int family)
    {
        var cloudAmount = amount ?? Math.Max(presence ?? 0, (fraction ?? 0) / 100);
        var humidity = Math.Max(0, (rh ?? 0) - 55) / 45;
        if (cloudAmount < 0.18)
        {
            if (fraction is { } f && f <= 0) return null;
            if (humidity < 0.06) return null;
            return new SolidColorBrush(ColorFrom(0x70, 0x97, 0x7E))
            {
                Opacity = Math.Min(0.34, 0.04 + humidity * 0.26),
            };
        }
        var alpha = Math.Min(0.82, 0.10 + cloudAmount * 0.68);
        return family switch
        {
            1 => new SolidColorBrush(ColorFrom(0x74, 0x9D, 0xC2)) { Opacity = alpha },
            2 => new SolidColorBrush(ColorFrom(0x69, 0x9A, 0x8E)) { Opacity = alpha },
            _ => new SolidColorBrush(ColorFrom(0x7E, 0x88, 0x91)) { Opacity = alpha },
        };
    }
}
