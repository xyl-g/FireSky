using Microsoft.UI;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;

namespace FireSky;

/// <summary>
/// 点预测 / 实况结果渲染（深度展示对齐 macOS Workbench）：
/// GFS+EC 双摘要卡、分组指标卡、AOD 环境读数、统一对比评分表、对比曲线（缺测断线 + 悬停读数）、
/// 15 列逐时台账、v3 太阳方向平滑大气剖面、冻结剖面查询、弹窗诊断分组。
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
    private readonly StackPanel DiagnosticPanel = new() { Spacing = 14 };
    private bool showInvalidLedgerSteps;
    private bool diagnosticsDialogOpen;
    private bool pointShowIsobars;
    private bool crossSectionShowIsobars;
    private double crossSectionZoomScale = 1.0;
    private JsonObject? activeCrossSection;
    private bool activeCrossSectionIsNowcast;
    private JsonObject? activeRegionalCrossSection;

    internal static bool IsStepValid(JsonObject step) =>
        BoolValue(step, "valid") != false && Text(step, "selected_regime") != "invalid";

    private async void OnOpenDiagnostics(object sender, RoutedEventArgs args) =>
        await ShowDiagnosticsDialogAsync();

    internal async Task ShowDiagnosticsDialogAsync()
    {
        if (diagnosticsDialogOpen || Root?.XamlRoot == null) return;
        diagnosticsDialogOpen = true;
        RenderDiagnostics();
        var scroll = new ScrollViewer
        {
            MaxHeight = 560,
            MinWidth = 540,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = DiagnosticPanel,
        };
        var dialog = new ContentDialog
        {
            Title = L10n.T("诊断与时间窗"),
            CloseButtonText = L10n.T("关闭"),
            DefaultButton = ContentDialogButton.None,
            XamlRoot = Root.XamlRoot,
            Content = scroll,
        };
        try
        {
            await dialog.ShowAsync();
        }
        finally
        {
            scroll.Content = null;
            diagnosticsDialogOpen = false;
        }
    }

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
        var model = (page == "regional" || resultKind == "regional") && RegionalModel != null
            ? (TagOf(RegionalModel.SelectedItem) is { Length: > 0 } rTag ? rTag : selectedResultModel)
            : selectedResultModel;
        if (model is not ("gfs" or "ec")) model = "gfs";
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

    // ---------- 顶部紧凑双模型指数条（对齐 macOS Hero 顶栏 + 概览摘要详情卡） ----------

    private void BuildHeroCards()
    {
        HeroHost.Children.Clear();
        heroCards.Clear();
        var grid = new Grid { ColumnSpacing = 14 };
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
            Width = 1, Height = 30, Opacity = .55,
            VerticalAlignment = VerticalAlignment.Center,
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
        var panel = new StackPanel { Spacing = 2 };
        var row = new Grid { ColumnSpacing = 10 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var left = new StackPanel { Spacing = 1, VerticalAlignment = VerticalAlignment.Center };
        var headline = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 5 };
        headline.Children.Add(new Ellipse
        {
            Width = 6, Height = 6, Fill = ModelBrush(model),
            VerticalAlignment = VerticalAlignment.Center,
        });
        headline.Children.Add(new TextBlock
        {
            Text = model.ToUpperInvariant(), FontSize = 11.5,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
        });
        var score = new TextBlock
        {
            Text = "--", FontSize = 18, FontWeight = Microsoft.UI.Text.FontWeights.Bold,
            Foreground = ModelBrush(model), VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(2, -1, 0, 0),
        };
        headline.Children.Add(score);
        headline.Children.Add(new TextBlock
        {
            Text = "/ 2.5", FontSize = 11, Opacity = .62,
            VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(0, 0, 0, 1),
        });
        left.Children.Add(headline);
        var status = new TextBlock { Text = L10n.T("等待预测"), FontSize = 10, Opacity = .66 };
        left.Children.Add(status);

        var best = new TextBlock
        {
            Text = "--", FontSize = 14, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
        };
        Grid.SetColumn(left, 0);
        Grid.SetColumn(best, 1);
        row.Children.Add(left);
        row.Children.Add(best);
        panel.Children.Add(row);

        // 详情字段保留对象并在概览摘要卡渲染，不撑高顶部条。
        var level = new TextBlock { Text = L10n.T("峰值质量指数"), Visibility = Visibility.Collapsed };
        var window = new TextBlock { Text = L10n.T("推荐窗口 --"), Visibility = Visibility.Collapsed };
        var chips = new TextBlock { Text = "", Visibility = Visibility.Collapsed };
        var genusText = new TextBlock { Text = "" };
        var genusBadge = new Border { Visibility = Visibility.Collapsed, Child = genusText };
        var source = new TextBlock { Text = "", Visibility = Visibility.Collapsed };
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
        var isGeos = PointAod.Source == "GEOS-FP" || aerosolSource == "geos-fp";
        return source switch
        {
            "ec" => isGeos
                ? L10n.T("EC：TCC 总云 + PL-RH 云柱代理；气溶胶来自 GEOS-FP 环境读数")
                : L10n.T("EC：TCC 总云 + PL-RH 云柱代理；气溶胶来自 CAMS 环境读数"),
            _ => isGeos
                ? L10n.T("GFS：全量云场与可烧量来源；气溶胶来自 GEOS-FP 环境读数")
                : L10n.T("GFS：全量云场与可烧量来源；气溶胶来自 CAMS 环境读数"),
        };
    }

    // ---------- AOD 环境读数 ----------

    internal sealed record AodSummary(double[] Values, string[] Sources, string Detail)
    {
        public bool Available => Values.Length > 0;
        public string Source => Sources.Length == 0 ? L10n.T("暂无来源")
            : Sources.All(source => source == "CAMS") ? "CAMS"
            : Sources.All(source => source == "GEOS-FP") ? "GEOS-FP"
            : Sources.All(source => source != "CAMS" && source != "GEOS-FP") ? L10n.T("其他来源") : L10n.T("混合来源");
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
            var quality = Text(aerosol, "quality");
            var label = rawSource.StartsWith("geos", StringComparison.OrdinalIgnoreCase)
                || string.Equals(quality, "geos-fp", StringComparison.OrdinalIgnoreCase)
                ? "GEOS-FP"
                : rawSource.StartsWith("cams", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(quality, "cams", StringComparison.OrdinalIgnoreCase)
                    ? "CAMS"
                    : L10n.T("其他来源");
            values.Add(number);
            sources.Add(label);
            var time = Text(report, "anchor_local") is { Length: > 0 } anchor ? anchor : L10n.T("时刻未提供");
            details.Add($"{time} · {number:0.0000} · {rawSource}");
        }
        return new AodSummary([.. values], [.. sources],
            details.Count == 0 ? L10n.T("等待气溶胶环境读取") : string.Join("\n", details.Distinct().OrderBy(v => v)));
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
        if (ResultModelPillTrack != null)
            ResultModelPillTrack.Visibility = page == "nowcast" ? Visibility.Collapsed : Visibility.Visible;
        if (RegionalModelPillTrack != null)
            RegionalModelPillTrack.Visibility = hasResult && resultKind == "regional" ? Visibility.Visible : Visibility.Collapsed;
        if (DiagnosticsButton != null)
            DiagnosticsButton.Visibility = hasResult && resultKind != "regional"
                ? Visibility.Visible : Visibility.Collapsed;
        RegionalInspector.Visibility = page == "regional" ? Visibility.Visible : Visibility.Collapsed;
        if (!hasResult)
        {
            RegionalGridRows.Items.Clear();
            CityRankingRows.Items.Clear();
            ClearRegionalProfile();
            ResetHeroCards();
            ResetMetricCards();
            if (OverviewSummaryHost != null) OverviewSummaryHost.Children.Clear();
            if (OverviewSummaryCard != null) OverviewSummaryCard.Visibility = Visibility.Collapsed;
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
            ResetCrossSectionVisuals();
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
        var muted = SecondaryBrush();
        foreach (var (model, card) in heroCards)
        {
            card.Status.Text = L10n.T("等待预测");
            card.Status.Foreground = muted;
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
            card.Score.Foreground = muted;
        }
        ResultContext.Text = $"{LocationName.Text} · {TargetDateLabel()} {ResultPhenomenonLabel()}";
    }

    private void RenderHero()
    {
        var muted = SecondaryBrush();
        foreach (var model in new[] { "gfs", "ec" })
        {
            var card = heroCards[model];
            var error = ModelError(model);
            var report = ModelReport(model);
            card.Status.Text = ModelStatus(error, report, result != null);
            card.Status.Foreground = error != null
                ? new SolidColorBrush(Colors.Red)
                : muted;
            var quality = report != null && DoubleValue(report["peak_quality_index"] ?? report["quality_index"]) is { } q
                ? q : (double?)null;
            card.Score.Text = quality is { } value ? value.ToString("0.00") : "--";
            card.Score.Foreground = quality is { }
                ? ModelBrush(model)
                : muted;
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
        RenderOverviewSummaryCard(nowcastMode: false);
    }

    private void RenderOverviewSummaryCard(bool nowcastMode)
    {
        if (OverviewSummaryCard == null || OverviewSummaryHost == null) return;
        OverviewSummaryHost.Children.Clear();
        var models = nowcastMode ? new[] { "gfs" } : new[] { "gfs", "ec" };
        var grid = new Grid { ColumnSpacing = 14 };
        for (var i = 0; i < models.Length; i++)
        {
            var m = models[i];
            if (!heroCards.TryGetValue(m, out var card)) continue;
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var col = new StackPanel { Spacing = 4 };
            var head = new Grid { ColumnSpacing = 8 };
            head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var tagRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, VerticalAlignment = VerticalAlignment.Center };
            tagRow.Children.Add(new Ellipse { Width = 6, Height = 6, Fill = ModelBrush(m), VerticalAlignment = VerticalAlignment.Center });
            tagRow.Children.Add(new TextBlock
            {
                Text = nowcastMode ? L10n.T("实况综合结论") : m.ToUpperInvariant(),
                FontSize = 11.5, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            var lvl = new TextBlock { Text = card.Level.Text, FontSize = 11.5, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Foreground = ModelBrush(m) };
            Grid.SetColumn(tagRow, 0);
            Grid.SetColumn(lvl, 2);
            head.Children.Add(tagRow);
            head.Children.Add(lvl);
            col.Children.Add(head);
            col.Children.Add(new TextBlock { Text = card.Window.Text, FontSize = 11, Opacity = .8 });
            if (card.Chips.Text.Length > 0)
                col.Children.Add(new TextBlock { Text = card.Chips.Text, FontSize = 10.5, Opacity = .68, TextWrapping = TextWrapping.Wrap });
            if (card.GenusBadge.Visibility == Visibility.Visible && card.GenusText.Text.Length > 0)
            {
                col.Children.Add(new Border
                {
                    CornerRadius = new CornerRadius(6),
                    Padding = new Thickness(7, 2, 7, 2),
                    HorizontalAlignment = HorizontalAlignment.Left,
                    Background = ThemeBrush("FireSkyGroupedCardBrush"),
                    BorderBrush = ThemeBrush("FireSkyGroupedBorderBrush"),
                    BorderThickness = new Thickness(1),
                    Child = new TextBlock { Text = card.GenusText.Text, FontSize = 10.5, Opacity = .85 },
                });
            }
            if (card.Source.Text.Length > 0)
                col.Children.Add(new TextBlock { Text = card.Source.Text, FontSize = 10, Opacity = .6, TextWrapping = TextWrapping.Wrap });
            Grid.SetColumn(col, i);
            grid.Children.Add(col);
        }
        OverviewSummaryHost.Children.Add(grid);
        OverviewSummaryCard.Visibility = Visibility.Visible;
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
        var modelLabel = selectedResultModel.ToUpperInvariant();
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
            var row = new Grid { ColumnSpacing = 10, RowSpacing = 10 };
            var twoByTwo = (end - start) == 4;
            if (twoByTwo)
            {
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                row.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                row.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            }
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
                if (twoByTwo)
                {
                    var slot = index - start;
                    Grid.SetRow(border, slot / 2);
                    Grid.SetColumn(border, slot % 2);
                }
                else
                {
                    Grid.SetColumn(border, index - start);
                    row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                }
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

    // ---------- 评分组成（统一 GFS | EC 对比表，只读后端分量） ----------

    private void RenderBreakdown()
    {
        BreakdownHost.Children.Clear();
        var gfsReport = ModelReport("gfs");
        var ecReport = ModelReport("ec");
        var gfsErr = ModelError("gfs");
        var ecErr = ModelError("ec");
        if (gfsReport == null && ecReport == null && gfsErr == null && ecErr == null)
        {
            BreakdownCard.Visibility = Visibility.Collapsed;
            return;
        }
        BreakdownCard.Visibility = Visibility.Visible;

        // 标题栏：评分组成 + GFS / EC 摘要徽标
        var header = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        header.Children.Add(new TextBlock
        {
            Text = L10n.T("评分组成"),
            FontSize = 13,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
        });
        foreach (var model in new[] { "gfs", "ec" })
        {
            var err = ModelError(model);
            var report = ModelReport(model);
            var badge = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 5, VerticalAlignment = VerticalAlignment.Center };
            if (report != null)
            {
                badge.Children.Add(new Ellipse { Width = 6, Height = 6, Fill = ModelBrush(model), VerticalAlignment = VerticalAlignment.Center });
                badge.Children.Add(new TextBlock { Text = model.ToUpperInvariant(), FontSize = 11, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
                badge.Children.Add(new TextBlock
                {
                    Text = $"{QualityText(report["quality_index"], 3)} / 2.5",
                    FontSize = 11,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Foreground = ModelBrush(model),
                });
                var lvl = Text(report, "quality_level");
                if (lvl.Length > 0)
                    badge.Children.Add(new TextBlock { Text = L10n.T(lvl), FontSize = 10, Opacity = .68, VerticalAlignment = VerticalAlignment.Center });
            }
            else if (err != null)
            {
                badge.Children.Add(new Ellipse { Width = 6, Height = 6, Fill = new SolidColorBrush(Colors.Red), VerticalAlignment = VerticalAlignment.Center });
                badge.Children.Add(new TextBlock
                {
                    Text = $"{model.ToUpperInvariant()} {L10n.T("未完成")}",
                    FontSize = 10,
                    Foreground = new SolidColorBrush(Colors.Red),
                });
            }
            else
            {
                badge.Children.Add(new Ellipse
                {
                    Width = 6, Height = 6, Opacity = .45,
                    Fill = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
                    VerticalAlignment = VerticalAlignment.Center,
                });
                badge.Children.Add(new TextBlock
                {
                    Text = $"{model.ToUpperInvariant()} {L10n.T("暂无结果")}",
                    FontSize = 10, Opacity = .65,
                });
            }
            header.Children.Add(badge);
        }
        BreakdownHost.Children.Add(header);

        // 若单模型失败，简要提示
        foreach (var model in new[] { "gfs", "ec" })
        {
            if (ModelError(model) is { } err)
            {
                BreakdownHost.Children.Add(new TextBlock
                {
                    Text = $"{model.ToUpperInvariant()} {L10n.T("未完成")}：{err}",
                    FontSize = 10,
                    Foreground = new SolidColorBrush(Colors.Red),
                    TextWrapping = TextWrapping.Wrap,
                    IsTextSelectionEnabled = true,
                });
            }
        }

        BreakdownHost.Children.Add(new Border
        {
            Height = 1,
            Opacity = .45,
            Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
        });

        // 3 列对比表：评分项 | GFS | EC
        var table = new StackPanel { Spacing = 0 };
        var colHeader = new Grid { ColumnSpacing = 12, Padding = new Thickness(0, 2, 0, 5) };
        colHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(116) });
        colHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        colHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        colHeader.Children.Add(new TextBlock
        {
            Text = L10n.T("评分项"), FontSize = 10, Opacity = .68,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        for (var m = 0; m < 2; m++)
        {
            var model = m == 0 ? "gfs" : "ec";
            var h = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 5 };
            h.Children.Add(new Ellipse { Width = 5, Height = 5, Fill = ModelBrush(model), VerticalAlignment = VerticalAlignment.Center });
            h.Children.Add(new TextBlock
            {
                Text = model.ToUpperInvariant(), FontSize = 10, Opacity = .68,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            Grid.SetColumn(h, m + 1);
            colHeader.Children.Add(h);
        }
        table.Children.Add(colHeader);

        string[] rowTitles =
        [
            L10n.T("云量基础分"),
            L10n.T("可烧云占比"),
            L10n.T("入射遮挡"),
            L10n.T("持续时间修正"),
            L10n.T("入射 AOD 修正"),
            L10n.T("观测 AOD 修正"),
        ];

        (string Primary, string Detail, Brush PrimaryBrush) CellContent(int idx, JsonObject report)
        {
            var c = report["quality_components"] as JsonObject ?? new JsonObject();
            var primaryDefault = (Brush)Application.Current.Resources["TextFillColorPrimaryBrush"];
            switch (idx)
            {
                case 0:
                {
                    var delta = SignedDelta(c["coverage_base_score"] ?? c["coverage_score"]);
                    var load = QualityText(c["forward_burnable_cloud_load_effective"] ?? c["forward_burnable_cloud_load_peak"], 2);
                    return (delta, $"有效 {RatioText(c["burnable_cloud_coverage_effective"])} · 载荷 {load}", DeltaBrush(delta));
                }
                case 1:
                    return (RatioText(c["forward_burnable_cloud_ratio_peak"]),
                        $"前方可烧 {RatioText(c["forward_burnable_cloud_field_amount_peak"])} / 总云 {RatioText(c["forward_view_cloud_field_amount_peak"])}",
                        primaryDefault);
                case 2:
                    return (RatioText(c["solar_incoming_block_risk_at_best_cloud"] ?? c["solar_incoming_block_risk_mean"]),
                        L10n.T("已计入可烧量"),
                        primaryDefault);
                case 3:
                {
                    var delta = SignedDelta(c["duration_adjustment"]);
                    var mins = QualityText(report["duration_minutes"] ?? c["duration_minutes"], 0);
                    return (delta, $"{mins} 分 · ×{QualityText(c["duration_factor"], 3)}", DeltaBrush(delta));
                }
                case 4:
                {
                    var delta = SignedDelta(c["incoming_aod_adjustment"]);
                    return (delta, $"AOD {QualityText(c["quality_aod550"], 3)} · ×{QualityText(c["incoming_aod_illumination_factor"], 3)}", DeltaBrush(delta));
                }
                default:
                {
                    var delta = SignedDelta(c["observer_aod_adjustment"]);
                    return (delta, $"对比 ×{QualityText(c["observer_aod_contrast_factor"], 3)}", DeltaBrush(delta));
                }
            }
        }

        for (var idx = 0; idx < rowTitles.Length; idx++)
        {
            table.Children.Add(new Border
            {
                Height = 1,
                Opacity = .35,
                Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
            });
            var row = new Grid { ColumnSpacing = 12, Padding = new Thickness(0, 6, 0, 6) };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(116) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.Children.Add(new TextBlock
            {
                Text = rowTitles[idx], FontSize = 11, Opacity = .7,
                VerticalAlignment = VerticalAlignment.Center,
            });
            for (var m = 0; m < 2; m++)
            {
                var rep = m == 0 ? gfsReport : ecReport;
                FrameworkElement cellView;
                if (rep != null)
                {
                    var (primary, detail, brush) = CellContent(idx, rep);
                    var cellStack = new StackPanel
                    {
                        Orientation = Orientation.Horizontal, Spacing = 8,
                        VerticalAlignment = VerticalAlignment.Center,
                    };
                    cellStack.Children.Add(new TextBlock
                    {
                        Text = primary, FontSize = 11.5,
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        Foreground = brush, MinWidth = 48,
                        VerticalAlignment = VerticalAlignment.Center,
                    });
                    cellStack.Children.Add(new TextBlock
                    {
                        Text = detail, FontSize = 10, Opacity = .68,
                        TextTrimming = TextTrimming.CharacterEllipsis,
                        VerticalAlignment = VerticalAlignment.Center,
                    });
                    cellView = cellStack;
                }
                else
                {
                    cellView = new TextBlock
                    {
                        Text = "—", FontSize = 11, Opacity = .55,
                        VerticalAlignment = VerticalAlignment.Center,
                    };
                }
                Grid.SetColumn(cellView, m + 1);
                row.Children.Add(cellView);
            }
            table.Children.Add(row);
        }
        BreakdownHost.Children.Add(table);

        // 单行限制因素摘要
        var gfsLimit = StringOf((gfsReport?["quality_components"] as JsonObject)?["quality_limiting_factor"]);
        var ecLimit = StringOf((ecReport?["quality_components"] as JsonObject)?["quality_limiting_factor"]);
        var limitText = (gfsLimit.Length > 0, ecLimit.Length > 0) switch
        {
            (true, true) when gfsLimit == ecLimit => $"{L10n.T("限制因素")}：{gfsLimit}",
            (true, true) => $"{L10n.T("限制因素")} · GFS：{gfsLimit} · EC：{ecLimit}",
            (true, false) => $"GFS {L10n.T("限制因素")}：{gfsLimit}",
            (false, true) => $"EC {L10n.T("限制因素")}：{ecLimit}",
            _ => "",
        };
        if (limitText.Length > 0)
        {
            var limitBlock = new TextBlock
            {
                Text = limitText,
                FontSize = 10.5,
                Opacity = .72,
                TextTrimming = TextTrimming.CharacterEllipsis,
                MaxLines = 1,
            };
            ToolTipService.SetToolTip(limitBlock, limitText);
            BreakdownHost.Children.Add(limitBlock);
        }

        // 详情与口径 Expander（默认折叠）
        var sample = gfsReport ?? ecReport!;
        var sampleComp = sample["quality_components"] as JsonObject ?? new JsonObject();
        var radius = NumText(sampleComp["forward_field_radius_km"] ?? sample["analysis_radius_km"], 0, " km");
        var sector = NumText(sampleComp["forward_sector_half_angle_deg"], 0, "°", "±");
        var detailStack = new StackPanel { Spacing = 6, Padding = new Thickness(0, 4, 0, 0) };
        detailStack.Children.Add(new TextBlock
        {
            Text = $"{L10n.T("分析范围")} {radius} · {L10n.T("太阳方向扇区")} {sector}",
            FontSize = 10.5, Opacity = .75,
            FontWeight = Microsoft.UI.Text.FontWeights.Medium,
        });
        foreach (var model in new[] { "gfs", "ec" })
        {
            if (ModelReport(model) is not { } rep) continue;
            var c = rep["quality_components"] as JsonObject ?? new JsonObject();
            var src = (Text(rep, "model_source") is { Length: > 0 } s ? s : model) == "ec"
                ? L10n.T("EC TCC + PL-RH 云柱代理")
                : L10n.T("GFS 全量云场");
            var line = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            line.Children.Add(new TextBlock
            {
                Text = model.ToUpperInvariant(),
                FontSize = 10.5,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = ModelBrush(model),
                Width = 30,
            });
            line.Children.Add(new TextBlock
            {
                Text = $"原始分 {QualityText(c["raw_quality_index"], 3)} · {src} · 观测回程 τ {QualityText(c["observer_return_optical_depth_mean"], 3)} · T {QualityText(c["observer_return_transmission_mean"], 3)}",
                FontSize = 10.5,
                Opacity = .72,
                TextWrapping = TextWrapping.Wrap,
            });
            detailStack.Children.Add(line);
        }
        detailStack.Children.Add(new TextBlock
        {
            Text = L10n.T("几何遮挡已包含在可烧云量中，不再重复扣分；同一视线由近及远合成（低云遮挡其后中高云）。摄影焦段、Top-K、代表云与云边界不进入主评分。"),
            FontSize = 10,
            Opacity = .62,
            TextWrapping = TextWrapping.Wrap,
        });
        BreakdownHost.Children.Add(new Expander
        {
            Header = L10n.T("详情与口径"),
            HorizontalAlignment = HorizontalAlignment.Stretch,
            IsExpanded = false,
            Content = detailStack,
        });
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

    // ---------- 对比曲线（缺测断线 + 悬停读数） ----------

    private void BuildChartPoints()
    {
        var points = new List<ChartPoint>();
        if (resultKind == "nowcast")
        {
            var report = SelectedReport();
            if (report != null)
            {
                var segment = 0;
                foreach (var step in report["steps"] as JsonArray ?? [])
                {
                    if (step is not JsonObject row || !IsStepValid(row)
                        || DoubleValue(row["instant_quality_index"]) is not { } quality
                        || ParseStepTime(row, Timezone.Text) is not { } time)
                    {
                        segment++;
                        continue;
                    }
                    points.Add(new ChartPoint("gfs", time, quality, Text(row, "valid_time_local"), segment));
                }
            }
            chartPoints = points.OrderBy(point => point.Time).ToList();
            return;
        }
        foreach (var model in new[] { "gfs", "ec" })
        {
            var report = ModelReport(model);
            if (report == null) continue;
            var segment = 0;
            foreach (var step in report["steps"] as JsonArray ?? [])
            {
                if (step is not JsonObject row || !IsStepValid(row)
                    || DoubleValue(row["instant_quality_index"]) is not { } quality
                    || ParseStepTime(row, Timezone.Text) is not { } time)
                {
                    segment++;
                    continue;
                }
                points.Add(new ChartPoint(model, time, quality, Text(row, "valid_time_local"), segment));
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

    internal void SelectResultTab(int index)
    {
        selectedResultTabIndex = Math.Clamp(index, 0, 2);
        UpdateSegmentedPills(ResultTabPills, selectedResultTabIndex.ToString(CultureInfo.InvariantCulture));
        if (OverviewTabPanel != null)
            OverviewTabPanel.Visibility = selectedResultTabIndex == 0 ? Visibility.Visible : Visibility.Collapsed;
        if (ProfileTabPanel != null)
            ProfileTabPanel.Visibility = selectedResultTabIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
        if (LedgerTabPanel != null)
            LedgerTabPanel.Visibility = selectedResultTabIndex == 2 ? Visibility.Visible : Visibility.Collapsed;
        if (selectedResultTabIndex == 1 && initialized)
        {
            if (resultKind == "nowcast") RenderNowcastProfileTab();
            else if (resultKind == "point") RenderPointProfileTab();
        }
    }

    internal void SetSelectedResultModel(string model, bool render = true, bool updateMap = true)
    {
        selectedResultModel = model == "ec" ? "ec" : "gfs";
        UpdateSegmentedPills(ResultModelPills, selectedResultModel);
        UpdateSegmentedPills(RegionalResultModelPills, selectedResultModel);
        if (page == "regional" && RegionalModel != null)
        {
            var targetIdx = selectedResultModel == "ec" ? 1 : 0;
            if (RegionalModel.SelectedIndex != targetIdx)
            {
                suppressDraftEvents = true;
                try { RegionalModel.SelectedIndex = targetIdx; }
                finally { suppressDraftEvents = false; }
                ClearRegionalProfile();
            }
        }
        if (render && initialized) RenderResult();
        if (updateMap && initialized) _ = UpdateMap(forceFit: page == "regional");
    }

    private void OnChartSizeChanged(object sender, SizeChangedEventArgs args) => RenderChart();

    private void RenderChart()
    {
        if (QualityChart == null || !initialized) return;
        QualityChart.Children.Clear();
        BuildChartPoints();
        var points = chartPoints ?? [];
        ChartEmpty.Visibility = points.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        ChartLegend.Text = resultKind == "nowcast"
            ? L10n.T("质量轴固定 0–2.5 · 缺测断线不补零    — 实况")
            : L10n.T("质量轴固定 0–2.5 · 缺测断线不补零    — GFS  - - EC");
        if (points.Count == 0) return;
        var width = Math.Max(1, ((FrameworkElement)QualityChart.Parent).ActualWidth);
        var height = 200.0;
        var start = points.Min(point => point.Time).Ticks;
        var endTicks = points.Max(point => point.Time).Ticks;
        var span = Math.Max(1, endTicks - start);
        double X(DateTimeOffset time) => span == 1 && points.Count == 1
            ? width * 0.5 : (time.Ticks - start) / (double)span * width;
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
            int? prevSeg = null;
            foreach (var point in series)
            {
                if (prevSeg is { } ps && ps != point.Segment)
                    segment = null;
                else if (previous is { } last && (point.Time - last).TotalMinutes > 15)
                    segment = null; // 缺口断线
                if (segment == null)
                {
                    segment = NewSegment(ModelBrush(model), model == "ec");
                    QualityChart.Children.Add(segment);
                }
                segment.Points.Add(new Windows.Foundation.Point(X(point.Time), Y(point.Quality)));
                previous = point.Time;
                prevSeg = point.Segment;
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
            new(L10n.T("质量"), 68, item => !IsStepValid(item) ? "—"
                : QualityText(item["instant_quality_index"], 3)),
            new(L10n.T("档位"), 118, item => !IsStepValid(item) ? L10n.T("外推窗外/未判断")
                : Text(item, "instant_quality_level") is { Length: > 0 } level ? level : "—"),
            new(L10n.T("可烧"), 52, item =>
            {
                var burnable = BoolValue(item, "physically_burnable");
                return !IsStepValid(item) || burnable == null ? "—"
                    : burnable == true ? "✓" : "×";
            }),
            new(L10n.T("可烧云量"), 84, item => !IsStepValid(item) ? "—"
                : RatioText(item["burnable_cloud_coverage"])),
            new(L10n.T("主导压力族"), 96, item => !IsStepValid(item) ? "—"
                : FamilyLabel(item["dominant_burnable_pressure_family"]) is { Length: > 0 } family
                    ? family : "—"),
            new(L10n.T("云底–云顶 ASL"), 120, item => !IsStepValid(item) ? "—"
                : AslRangeLabel(item)),
            new(L10n.T("云层 AGL"), 112, item => !IsStepValid(item) ? "—"
                : CloudLayerAgl(item)),
            new(L10n.T("亮度颜色"), 68, item => !IsStepValid(item) ? "—"
                : RatioText(item["brightness_color"])),
            new(L10n.T("可烧比例"), 68, item => !IsStepValid(item) ? "—"
                : RatioText(item["forward_burnable_cloud_ratio"])),
            new(L10n.T("回程透过率"), 76, item => !IsStepValid(item) ? "—"
                : RatioText(item["observer_return_transmission"])),
            new(L10n.T("入射阻挡"), 72, item => !IsStepValid(item) ? "—"
                : RatioText(item["solar_incoming_block_risk"])),
            new("AOD 550", 72, item => !IsStepValid(item) ? "—"
                : QualityText(item["quality_aod550"], 2)),
            new(L10n.T("太阳仰角"), 78, item => !IsStepValid(item) ? "—"
                : QualityText(item["solar_elevation_deg"], 1) + "°"),
            new(L10n.T("场景"), 138, item => !IsStepValid(item) ? "—"
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
        var hiddenInvalidCount = allSteps.Count(step => !IsStepValid(step));
        var hasValidSteps = allSteps.Any(IsStepValid);
        var showInvalid = showInvalidLedgerSteps || !hasValidSteps;
        var visibleSteps = showInvalid
            ? allSteps
            : allSteps.Where(IsStepValid).ToList();
        for (var index = 0; index < visibleSteps.Count; index++)
        {
            var step = visibleSteps[index];
            var local = Text(step, "valid_time_local");
            var valid = IsStepValid(step);
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
        if (hasValidSteps && hiddenInvalidCount > 0)
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
        for (var colIndex = 0; colIndex < columns.Length; colIndex++)
        {
            var column = columns[colIndex];
            var text = item == null ? column.Title
                : !IsStepValid(item) && column.WarnInvalid ? column.Render(item)
                : column.Render(item);
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(column.Width) });
            var cell = new TextBlock
            {
                Text = text,
                FontSize = 11,
                Padding = new Thickness(6, 0, 6, 0),
                TextTrimming = TextTrimming.CharacterEllipsis,
                MaxLines = 1,
                VerticalAlignment = VerticalAlignment.Center,
                Opacity = item == null ? .7 : 1,
                FontWeight = item == null ? Microsoft.UI.Text.FontWeights.SemiBold
                    : Microsoft.UI.Text.FontWeights.Normal,
            };
            if (item != null && column.Title == L10n.T("时间") && Text(item, "valid_time_local") == bestTime
                && bestTime.Length > 0)
            {
                cell.Text = $"{cell.Text} ★";
                cell.Foreground = ThemeBrush("FireSkyAccentBrush");
            }
            Grid.SetColumn(cell, colIndex);
            row.Children.Add(cell);
        }
        if (item != null && !IsStepValid(item))
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
            MinWidth = 1310,
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

    // ---------- 大气剖面页（v3 平滑剖面 + 冻结剖面查询 + 交互悬停） ----------

    private sealed record CrossSectionParsed(
        double[] Distances,
        int[] Levels,
        double?[][] Heights,
        double?[][] Temps,
        double?[][] Rh,
        double?[][] Amounts,
        double?[][] Presence,
        double?[][] Fractions,
        int[][] FamilyCodes,
        int[][] Statuses,
        double?[][] LayerBases,
        double?[][] LayerTops,
        double[] Terrain,
        double[] RequiredAsl,
        List<JsonObject> Targets);

    private sealed record ColumnStratum(double BaseAsl, double TopAsl, double Amount, int Status);

    private sealed record CloudRunPoint(int SampleIndex, double Cx, double TopY, double BottomY, ColumnStratum Stratum);

    private static CrossSectionParsed? ParseCrossSection(JsonObject section)
    {
        var distances = (section["distances_km"] as JsonArray ?? [])
            .Select(DoubleValue)
            .Where(v => v.HasValue)
            .Select(v => v!.Value)
            .ToArray();
        if (distances.Length == 0) return null;
        var fields = section["fields"] as JsonObject ?? new JsonObject();
        double?[][] Grid(string key) =>
            (fields[key] as JsonArray ?? [])
                .Select(row => (row as JsonArray ?? [])
                    .Select(DoubleValue).ToArray()).ToArray();
        int[][] IntGrid(string key) =>
            (fields[key] as JsonArray ?? [])
                .Select(row => (row as JsonArray ?? [])
                    .Select(cell => (int?)DoubleValue(cell) ?? 0).ToArray()).ToArray();
        var levels = (section["pressure_levels_hpa"] as JsonArray ?? [])
            .Select(node => (int?)DoubleValue(node) ?? 0).ToArray();
        var terrain = (section["terrain_elevation_asl_m"] as JsonArray ?? [])
            .Select(DoubleValue).Where(v => v.HasValue).Select(v => v!.Value).ToArray();
        var required = (section["required_illumination_base_asl_m"] as JsonArray ?? [])
            .Select(DoubleValue).Where(v => v.HasValue).Select(v => v!.Value).ToArray();
        var targets = (section["theoretical_targets"] as JsonArray ?? [])
            .OfType<JsonObject>().ToList();
        return new CrossSectionParsed(
            distances, levels,
            Grid("geopotential_height_asl_m"),
            Grid("temperature_c"),
            Grid("rh_percent"),
            Grid("cloud_amount"),
            Grid("cloud_presence"),
            Grid("cloud_fraction_percent"),
            IntGrid("pressure_family_code"),
            IntGrid("cloud_layer_status"),
            Grid("cloud_layer_base_asl_m"),
            Grid("cloud_layer_top_asl_m"),
            terrain, required, targets);
    }

    private static double? ScalarAt(double?[][] grid, int sample, int level) =>
        sample >= 0 && sample < grid.Length && level >= 0 && level < grid[sample].Length
            ? grid[sample][level] : null;

    private static int IntAt(int[][] grid, int sample, int level) =>
        sample >= 0 && sample < grid.Length && level >= 0 && level < grid[sample].Length
            ? grid[sample][level] : 0;

    private static ColumnStratum? ColumnStratumAt(CrossSectionParsed data, int sample, int family)
    {
        var cloudyLevels = new List<int>();
        for (var l = 0; l < data.Levels.Length; l++)
        {
            if (IntAt(data.FamilyCodes, sample, l) != family) continue;
            var amt = ScalarAt(data.Amounts, sample, l)
                ?? Math.Max(ScalarAt(data.Presence, sample, l) ?? 0, (ScalarAt(data.Fractions, sample, l) ?? 0) / 100.0);
            if (amt >= 0.06) cloudyLevels.Add(l);
        }
        if (cloudyLevels.Count == 0) return null;

        var bases = cloudyLevels.Select(l => ScalarAt(data.LayerBases, sample, l)).Where(v => v.HasValue).Select(v => v!.Value).ToList();
        var tops = cloudyLevels.Select(l => ScalarAt(data.LayerTops, sample, l)).Where(v => v.HasValue).Select(v => v!.Value).ToList();
        var heights = cloudyLevels.Select(l => ScalarAt(data.Heights, sample, l)).Where(v => v.HasValue).Select(v => v!.Value).ToList();
        double? layerBase = bases.Count > 0 ? bases.Min() : (heights.Count > 0 ? heights.Min() - 700.0 : null);
        double? layerTop = tops.Count > 0 ? tops.Max() : (heights.Count > 0 ? heights.Max() + 700.0 : null);
        if (layerBase is not { } b || layerTop is not { } t || t <= b) return null;

        var amountMax = cloudyLevels.Select(l =>
            ScalarAt(data.Amounts, sample, l)
            ?? Math.Max(ScalarAt(data.Presence, sample, l) ?? 0, (ScalarAt(data.Fractions, sample, l) ?? 0) / 100.0)).Max();
        var status = cloudyLevels.Select(l => IntAt(data.Statuses, sample, l)).Max();
        return new ColumnStratum(b, t, Math.Max(0.06, amountMax), status);
    }

    private void ResetCrossSectionVisuals()
    {
        activeCrossSection = null;
        activeCrossSectionIsNowcast = false;
        crossSectionZoomScale = 1.0;
        UpdateCrossSectionZoomControls();
        if (ProfileTitleText != null) ProfileTitleText.Text = L10n.T("太阳方向大气剖面");
        if (ProfileStatus != null) ProfileStatus.Text = L10n.T("等待预测");
        if (FrozenProfileButton != null) FrozenProfileButton.Visibility = Visibility.Collapsed;
        if (ProfileTakeawayBanner != null) ProfileTakeawayBanner.Visibility = Visibility.Collapsed;
        if (ProfileControlsBar != null) ProfileControlsBar.Visibility = Visibility.Collapsed;
        if (PointCrossSection != null)
        {
            PointCrossSection.Children.Clear();
            PointCrossSection.Visibility = Visibility.Collapsed;
        }
        if (CrossSectionCrosshair != null) CrossSectionCrosshair.Visibility = Visibility.Collapsed;
        if (CrossSectionHoverCard != null) CrossSectionHoverCard.Visibility = Visibility.Collapsed;
        if (PointCrossSectionLegendScroll != null) PointCrossSectionLegendScroll.Visibility = Visibility.Collapsed;
        if (PointCloudGenusBar != null) PointCloudGenusBar.Visibility = Visibility.Collapsed;
        if (PointTargetsHost != null)
        {
            PointTargetsHost.Children.Clear();
            PointTargetsHost.Visibility = Visibility.Collapsed;
        }
        if (PointProfileNote != null) PointProfileNote.Text = "";
        if (NowcastFrozenExpander != null) NowcastFrozenExpander.Visibility = Visibility.Collapsed;
    }

    private void RenderPointProfileTab()
    {
        if (NowcastFrozenExpander != null) NowcastFrozenExpander.Visibility = Visibility.Collapsed;
        if (ProfileTitleText != null) ProfileTitleText.Text = L10n.T("太阳方向大气剖面");
        var report = SelectedReport();
        ProfileStatus.Text = L10n.T("周期 ") + Text(report, "cycle_time_utc");
        var source = report?["profile_source"] as JsonObject;
        var available = source != null && BoolValue(source, "available") == true;
        FrozenProfileButton.Content = L10n.T("刷新冻结剖面");
        FrozenProfileButton.Visibility = available && resultKind == "point"
            ? Visibility.Visible : Visibility.Collapsed;

        var section = frozenProfileState == "ready" && frozenProfile != null
            ? frozenProfile
            : report?["cross_section"] as JsonObject;
        if (section != null)
        {
            RenderPointCrossSectionChrome(section, report);
            PointProfileNote.Text = frozenProfileState switch
            {
                "loading" => L10n.T("正在读取冻结剖面…"),
                "error" or "unavailable" or "cancelled" => frozenProfileError,
                _ => "",
            };
            PointProfileNote.Foreground = frozenProfileState is "error" or "unavailable"
                ? new SolidColorBrush(Colors.Red)
                : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            return;
        }

        if (ProfileTakeawayBanner != null) ProfileTakeawayBanner.Visibility = Visibility.Collapsed;
        if (ProfileControlsBar != null) ProfileControlsBar.Visibility = Visibility.Collapsed;
        if (PointCrossSectionLegendScroll != null) PointCrossSectionLegendScroll.Visibility = Visibility.Collapsed;
        if (PointCloudGenusBar != null) PointCloudGenusBar.Visibility = Visibility.Collapsed;
        if (PointTargetsHost != null) PointTargetsHost.Visibility = Visibility.Collapsed;
        activeCrossSection = null;
        activeCrossSectionIsNowcast = false;
        PointCrossSection.Visibility = Visibility.Collapsed;

        if (frozenProfileState == "idle")
        {
            PointProfileNote.Text = available
                ? L10n.T("点击「刷新冻结剖面」查看该结果的太阳方向诊断。")
                : L10n.T("该结果没有可查询的冻结剖面。");
            PointProfileNote.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        }
        else
        {
            RenderFrozenProfile();
        }
    }

    private void RenderPointCrossSectionChrome(JsonObject section, JsonObject? report)
    {
        activeCrossSection = section;
        activeCrossSectionIsNowcast = false;
        var parsed = ParseCrossSection(section);
        if (parsed == null)
        {
            PointCrossSection.Visibility = Visibility.Collapsed;
            ProfileTakeawayBanner.Visibility = Visibility.Collapsed;
            ProfileControlsBar.Visibility = Visibility.Collapsed;
            PointCrossSectionLegendScroll.Visibility = Visibility.Collapsed;
            PointProfileNote.Text = L10n.T("后端未返回可绘制的剖面字段；可在诊断中查看理论目标与照明基准。");
            return;
        }

        // 1. 一图看懂横幅
        var (takeawayText, takeawayStatus) = BuildCrossSectionTakeaway(section, parsed);
        var accentColor = takeawayStatus == 3
            ? ColorFrom(0xF5, 0x6B, 0x2E)
            : takeawayStatus == 2 ? ColorFrom(0xDB, 0x8F, 0x33) : ColorFrom(0x7A, 0x86, 0x99);
        var isDark = Root?.ActualTheme == ElementTheme.Dark;
        ProfileTakeawayBanner.Visibility = Visibility.Visible;
        ProfileTakeawayBanner.Background = new SolidColorBrush(Windows.UI.Color.FromArgb(
            (byte)(takeawayStatus == 0 ? 15 : isDark ? 36 : 23), accentColor.R, accentColor.G, accentColor.B));
        ProfileTakeawayBanner.BorderBrush = new SolidColorBrush(Windows.UI.Color.FromArgb(
            (byte)(takeawayStatus == 0 ? 46 : 92), accentColor.R, accentColor.G, accentColor.B));
        ProfileTakeawayIcon.Glyph = takeawayStatus == 3 ? "\uE706" : takeawayStatus == 2 ? "\uE753" : "\uE946";
        ProfileTakeawayIcon.Foreground = new SolidColorBrush(accentColor);
        ProfileTakeawayText.Text = takeawayText;

        // 2. 遥测胶囊 + 等压线开关
        crossSectionShowIsobars = pointShowIsobars;
        ProfileControlsBar.Visibility = Visibility.Visible;
        ProfileTelemetryPills.Children.Clear();
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("仰角"), NumText(section["solar_elevation_deg"], 1, "°"));
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("方位"), NumText(section["solar_azimuth_deg"], 1, "°"));
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("范围"), NumText(section["analysis_radius_km"], 1, " km"));
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("时次"), TimeLabel(Text(section, "valid_time_local")));
        UpdateCrossSectionZoomControls();
        if (ProfileIsobarsToggle != null)
        {
            ProfileIsobarsToggle.Visibility = parsed.Levels.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            ProfileIsobarsToggle.IsChecked = crossSectionShowIsobars;
        }

        // 3. 主画布
        PointCrossSection.Visibility = Visibility.Visible;
        ResizePointCrossSectionChart();
        DrawCrossSection(PointCrossSection, section, crossSectionShowIsobars);

        // 4. 单行图例
        RenderCrossSectionLegend();

        // 5. 云属代理单行条
        var genusProxy = report?["cloud_genus_proxy"] as JsonObject;
        if (genusProxy != null)
        {
            var label = Text(genusProxy, "label");
            if (label.Length == 0) label = "unknown";
            var conf = DoubleValue(genusProxy["confidence"]) is { } c ? $" {(int)Math.Round(c * 100)}%" : "";
            string mainText;
            if (label != "unknown")
                mainText = $"{GenusLabel(label)}{conf}";
            else
            {
                var compat = string.Join("、", (genusProxy["compatible_genera"] as JsonArray ?? [])
                    .Select(StringOrNull).Where(s => !string.IsNullOrEmpty(s)).Select(s => GenusLabel(s!)));
                mainText = compat.Length > 0 ? $"{L10n.T("兼容")}：{compat}" : L10n.T("未定");
            }
            var candidates = "";
            if (genusProxy["candidate_scores"] is JsonObject scores)
            {
                var top3 = scores
                    .Select(kv => (Key: kv.Key, Score: DoubleValue(kv.Value)))
                    .Where(item => item.Score.HasValue)
                    .OrderByDescending(item => item.Score!.Value)
                    .Take(3)
                    .Select(item => $"{GenusLabel(item.Key)} {(int)Math.Round(item.Score!.Value * 100)}%")
                    .ToList();
                if (top3.Count > 0) candidates = $" · {L10n.T("候选")}：{string.Join(" · ", top3)}";
            }
            PointCloudGenusText.Text = $"{L10n.T("云属代理")}  {mainText}{candidates}";
            PointCloudGenusBar.Visibility = Visibility.Visible;
            ToolTipService.SetToolTip(PointCloudGenusBar,
                L10n.T("依据压力层、相态与云层几何的模型云属代理，不代表卫星或人工云属观测。"));
        }
        else
        {
            PointCloudGenusBar.Visibility = Visibility.Collapsed;
        }

        // 6. 理论候选云层（精简胶囊）
        RenderTargetsStrip(parsed.Targets);
    }

    private void AddTelemetryPill(StackPanel host, string label, string value)
    {
        var pill = new Border
        {
            Padding = new Thickness(8, 3, 8, 3),
            CornerRadius = new CornerRadius(12),
            Background = ThemeBrush("FireSkyGroupedCardBrush"),
            BorderBrush = ThemeBrush("FireSkyGroupedBorderBrush"),
            BorderThickness = new Thickness(1),
            Child = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Spacing = 4,
                Children =
                {
                    new TextBlock { Text = label, FontSize = 10, Opacity = .65 },
                    new TextBlock { Text = value, FontSize = 10.5, FontWeight = Microsoft.UI.Text.FontWeights.Medium },
                },
            },
        };
        host.Children.Add(pill);
    }

    private static (string Text, int Status) BuildCrossSectionTakeaway(JsonObject section, CrossSectionParsed data)
    {
        var az = DoubleValue(section["solar_azimuth_deg"]) is { } a ? $"{a:0}°" : "—";
        var el = DoubleValue(section["solar_elevation_deg"]) is { } e ? $"{e:0.0}°" : "—";
        var prefix = L10n.IsEnglish ? $"Sun Az {az} · El {el}" : $"太阳方位 {az} · 仰角 {el}";

        var litDistances = new List<double>();
        var litBases = new List<double>();
        var litFamilies = new HashSet<int>();
        var hasOccluded = false;
        for (var i = 0; i < data.Distances.Length; i++)
        {
            for (var fam = 1; fam <= 3; fam++)
            {
                if (ColumnStratumAt(data, i, fam) is not { } stratum) continue;
                if (stratum.Status == 3)
                {
                    litDistances.Add(data.Distances[i]);
                    litBases.Add(stratum.BaseAsl);
                    litFamilies.Add(fam);
                }
                else if (stratum.Status == 2)
                {
                    hasOccluded = true;
                }
            }
        }
        if (litDistances.Count > 0)
        {
            var minD = litDistances.Min(); var maxD = litDistances.Max();
            var minB = litBases.Min(); var maxB = litBases.Max();
            string FamName(int f) => L10n.IsEnglish
                ? (f == 1 ? "High" : f == 2 ? "Mid" : "Low")
                : (f == 1 ? "高" : f == 2 ? "中" : "低");
            var fams = string.Join("/", new[] { 3, 2, 1 }.Where(litFamilies.Contains).Select(FamName));
            var distText = Math.Abs(maxD - minD) < 1 ? $"{minD:0}" : $"{minD:0}–{maxD:0}";
            var baseText = Math.Abs(maxB - minB) < 150 ? $"{minB / 1000:0.0}" : $"{minB / 1000:0.0}–{maxB / 1000:0.0}";
            var detail = L10n.IsEnglish
                ? $"{fams} cloud base at ~{distText} km ({baseText} km ASL) is sunlit & visible"
                : $"约 {distText} km 处{fams}云底（{baseText} km）进入受光区且通视";
            return ($"{prefix} · {detail}", 3);
        }
        if (hasOccluded)
        {
            var detail = L10n.IsEnglish
                ? "Far-field clouds reach sunlit altitude, but sightline is occluded by foreground clouds or terrain"
                : "远场云层达到受光高度，但观测视线受前段云层或地形遮挡";
            return ($"{prefix} · {detail}", 2);
        }
        var noneDetail = L10n.IsEnglish
            ? "No cloud layers along the solar path meet both sunlit and line-of-sight criteria"
            : "沿太阳光路暂无同时满足受光高度与通视条件的云层";
        return ($"{prefix} · {noneDetail}", 0);
    }

    private void RenderCrossSectionLegend()
    {
        if (PointCrossSectionLegend == null || PointCrossSectionLegendScroll == null) return;
        PointCrossSectionLegend.Children.Clear();
        PointCrossSectionLegendScroll.Visibility = Visibility.Visible;

        void AddItem(FrameworkElement swatch, string label)
        {
            var item = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 5, VerticalAlignment = VerticalAlignment.Center };
            item.Children.Add(swatch);
            item.Children.Add(new TextBlock { Text = label, FontSize = 10, Opacity = .75 });
            PointCrossSectionLegend.Children.Add(item);
        }

        // 火烧云受光云底
        var fireGrad = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0),
            EndPoint = new Windows.Foundation.Point(0, 1),
        };
        fireGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0xB8, 0xD6, 0xF0), Offset = 0 });
        fireGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0xF5, 0x5D, 0x28), Offset = 1 });
        AddItem(new Border { Width = 14, Height = 8, CornerRadius = new CornerRadius(2), Background = fireGrad },
            L10n.T("火烧云受光云底"));

        // 高/中/低云层
        var strataSwatch = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2 };
        strataSwatch.Children.Add(new Border { Width = 5, Height = 7, CornerRadius = new CornerRadius(2), Background = new SolidColorBrush(ColorFrom(0xB8, 0xD9, 0xF5)) });
        strataSwatch.Children.Add(new Border { Width = 5, Height = 7, CornerRadius = new CornerRadius(2), Background = new SolidColorBrush(ColorFrom(0x7A, 0xBD, 0xC7)) });
        strataSwatch.Children.Add(new Border { Width = 5, Height = 7, CornerRadius = new CornerRadius(2), Background = new SolidColorBrush(ColorFrom(0x75, 0x82, 0x91)) });
        AddItem(strataSwatch, L10n.T("高/中/低云层"));

        // 受光区/地影区
        var corrGrad = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0),
            EndPoint = new Windows.Foundation.Point(0, 1),
        };
        corrGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(110, 250, 166, 66), Offset = 0 });
        corrGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(120, 31, 46, 77), Offset = 1 });
        AddItem(new Border { Width = 14, Height = 8, CornerRadius = new CornerRadius(2), Background = corrGrad },
            L10n.T("受光区/地影区"));

        // 受光高度线 / 受光被遮挡
        AddItem(new Line { X1 = 0, X2 = 14, Y1 = 4, Y2 = 4, Width = 14, Height = 8, StrokeThickness = 1.6, Stroke = new SolidColorBrush(ColorFrom(0xEB, 0x96, 0x30)), StrokeDashArray = [3.5, 2.5] },
            L10n.T("受光高度线"));
        AddItem(new Line { X1 = 0, X2 = 14, Y1 = 4, Y2 = 4, Width = 14, Height = 8, StrokeThickness = 1.4, Stroke = new SolidColorBrush(ColorFrom(0xE0, 0x94, 0x33)), StrokeDashArray = [2.5, 2] },
            L10n.T("受光被遮挡"));

        // 地形与视线
        var terrainSwatch = new Grid { Width = 14, Height = 8 };
        terrainSwatch.Children.Add(new Border { Height = 4, VerticalAlignment = VerticalAlignment.Bottom, Background = new SolidColorBrush(ColorFrom(0x69, 0x75, 0x73)) });
        terrainSwatch.Children.Add(new Line { X1 = 1, Y1 = 6, X2 = 13, Y2 = 1, StrokeThickness = 1.2, Stroke = new SolidColorBrush(ColorFrom(0xFC, 0x9E, 0x47)), StrokeDashArray = [2, 2] });
        AddItem(terrainSwatch, L10n.T("地形与视线"));
    }

    private void RenderTargetsStrip(List<JsonObject> targets)
    {
        if (PointTargetsHost == null) return;
        PointTargetsHost.Children.Clear();
        if (targets.Count == 0)
        {
            PointTargetsHost.Visibility = Visibility.Collapsed;
            return;
        }
        var burnable = targets.Where(t => Text(t, "status") == "burnable").ToList();
        PointTargetsHost.Visibility = Visibility.Visible;
        PointTargetsHost.Children.Add(new TextBlock
        {
            Text = L10n.IsEnglish
                ? "Sunlit & terrain-visible candidates (cloud occlusion not evaluated)"
                : "受光与几何/地形可见候选（未评估云遮挡）",
            FontSize = 10.5,
            FontWeight = Microsoft.UI.Text.FontWeights.Medium,
        });
        if (burnable.Count > 0)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            foreach (var target in burnable.Take(6))
            {
                var fam = Text(target, "pressure_family") switch
                {
                    "high" => L10n.T("高云族"), "middle" => L10n.T("中云族"), "low" => L10n.T("低云族"),
                    _ => Text(target, "pressure_family_label") is { Length: > 0 } l ? l : L10n.T("云层"),
                };
                var dist = NumText(target["distance_km"], 0, " km");
                var baseKm = DoubleValue(target["cloud_base_asl_m"]) is { } b ? $"{b / 1000:0.0}" : "—";
                var topKm = DoubleValue(target["cloud_top_asl_m"]) is { } t ? $"{t / 1000:0.0}" : "—";
                row.Children.Add(new Border
                {
                    Padding = new Thickness(7, 3, 7, 3),
                    CornerRadius = new CornerRadius(5),
                    Background = new SolidColorBrush(Windows.UI.Color.FromArgb(20, 240, 107, 54)),
                    BorderBrush = new SolidColorBrush(Windows.UI.Color.FromArgb(105, 240, 107, 54)),
                    BorderThickness = new Thickness(1),
                    Child = new TextBlock { Text = $"{fam} · {dist} · ASL {baseKm}–{topKm} km", FontSize = 10 },
                });
            }
            PointTargetsHost.Children.Add(new ScrollViewer
            {
                HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
                VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
                Content = row,
            });
        }
        else if (targets.FirstOrDefault(t => Text(t, "status") != "burnable") is { } diag)
        {
            var reason = Text(diag, "status_reason") is { Length: > 0 } r ? r : L10n.T("状态原因未提供");
            PointTargetsHost.Children.Add(new TextBlock
            {
                Text = $"当前太阳方向没有可烧目标云：{reason}",
                FontSize = 10, Opacity = .7, TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private void OnProfileIsobarsToggled(object sender, RoutedEventArgs args)
    {
        crossSectionShowIsobars = ProfileIsobarsToggle?.IsChecked == true;
        if (activeCrossSectionIsNowcast) nowcastShowIsobars = crossSectionShowIsobars;
        else pointShowIsobars = crossSectionShowIsobars;
        if (activeCrossSection == null || PointCrossSection == null) return;
        if (activeCrossSectionIsNowcast)
            DrawNowcastProfileHeatmap(activeCrossSection, crossSectionShowIsobars);
        else
            DrawCrossSection(PointCrossSection, activeCrossSection, crossSectionShowIsobars);
    }

    private void OnProfileZoomOut(object sender, RoutedEventArgs args) =>
        SetCrossSectionZoom(crossSectionZoomScale - 0.25);

    private void OnProfileZoomIn(object sender, RoutedEventArgs args) =>
        SetCrossSectionZoom(crossSectionZoomScale + 0.25);

    private void OnProfileZoomReset(object sender, RoutedEventArgs args) =>
        SetCrossSectionZoom(1.0, resetScroll: true);

    private void SetCrossSectionZoom(double scale, bool resetScroll = false)
    {
        crossSectionZoomScale = Math.Clamp(Math.Round(scale * 4) / 4, 1.0, 2.5);
        UpdateCrossSectionZoomControls();
        ResizePointCrossSectionChart(resetScroll || crossSectionZoomScale == 1.0);
        if (CrossSectionCrosshair != null) CrossSectionCrosshair.Visibility = Visibility.Collapsed;
        if (CrossSectionHoverCard != null) CrossSectionHoverCard.Visibility = Visibility.Collapsed;
        if (activeCrossSection == null || PointCrossSection?.Visibility != Visibility.Visible) return;
        if (activeCrossSectionIsNowcast)
            DrawNowcastProfileHeatmap(activeCrossSection, crossSectionShowIsobars);
        else
            DrawCrossSection(PointCrossSection, activeCrossSection, crossSectionShowIsobars);
    }

    private void UpdateCrossSectionZoomControls()
    {
        if (ProfileZoomText == null) return;
        ProfileZoomText.Text = $"{(int)Math.Round(crossSectionZoomScale * 100)}%";
        ProfileZoomOutButton.IsEnabled = crossSectionZoomScale > 1.0;
        ProfileZoomInButton.IsEnabled = crossSectionZoomScale < 2.5;
        ProfileZoomResetButton.IsEnabled = crossSectionZoomScale > 1.0;
    }

    private void ResizePointCrossSectionChart(bool resetScroll = false)
    {
        if (PointCrossSectionScrollViewer == null || PointCrossSection == null) return;
        var viewportWidth = PointCrossSectionScrollViewer.ActualWidth;
        var viewportHeight = PointCrossSectionScrollViewer.ActualHeight;
        if (viewportWidth <= 120 || viewportHeight <= 120) return;

        PointCrossSection.Width = Math.Max(220, viewportWidth * crossSectionZoomScale);
        PointCrossSection.Height = Math.Max(180, viewportHeight);
        if (resetScroll) PointCrossSectionScrollViewer.ChangeView(0, null, null, true);
    }

    private void OnCrossSectionSizeChanged(object sender, SizeChangedEventArgs args)
    {
        if (activeCrossSection == null || PointCrossSection == null || PointCrossSection.Visibility != Visibility.Visible)
            return;
        ResizePointCrossSectionChart();
        if (activeCrossSectionIsNowcast)
            DrawNowcastProfileHeatmap(activeCrossSection, crossSectionShowIsobars);
        else
            DrawCrossSection(PointCrossSection, activeCrossSection, crossSectionShowIsobars);
    }

    private void OnCrossSectionPointerMoved(object sender, PointerRoutedEventArgs args)
    {
        if (activeCrossSection == null || PointCrossSectionContainer == null
            || PointCrossSection.Visibility != Visibility.Visible)
        {
            if (CrossSectionCrosshair != null) CrossSectionCrosshair.Visibility = Visibility.Collapsed;
            if (CrossSectionHoverCard != null) CrossSectionHoverCard.Visibility = Visibility.Collapsed;
            return;
        }
        var viewportPos = args.GetCurrentPoint(PointCrossSectionContainer).Position;
        var contentPos = new Windows.Foundation.Point(
            viewportPos.X + PointCrossSectionScrollViewer.HorizontalOffset, viewportPos.Y);
        var viewportWidth = Math.Max(120, PointCrossSectionScrollViewer.ActualWidth);
        var width = Math.Max(120, PointCrossSection.Width is > 120 and var canvasWidth
            ? canvasWidth : PointCrossSection.ActualWidth);
        var height = Math.Max(120, PointCrossSectionContainer.ActualHeight);
        double left = 44;
        double right = width - (crossSectionShowIsobars ? 48.0 : 16.0);
        double top = 14;
        double bottom = height - 24;
        if (right <= left + 10 || bottom <= top + 10
            || viewportPos.X < 0 || viewportPos.X > viewportWidth
            || contentPos.X < left || contentPos.X > right
            || contentPos.Y < top || contentPos.Y > bottom)
        {
            CrossSectionCrosshair.Visibility = Visibility.Collapsed;
            CrossSectionHoverCard.Visibility = Visibility.Collapsed;
            return;
        }

        CrossSectionCrosshair.X1 = viewportPos.X;
        CrossSectionCrosshair.X2 = viewportPos.X;
        CrossSectionCrosshair.Y1 = top;
        CrossSectionCrosshair.Y2 = bottom;
        CrossSectionCrosshair.Visibility = Visibility.Visible;

        CrossSectionHoverHost.Children.Clear();
        if (activeCrossSectionIsNowcast)
        {
            PopulateNowcastHoverCard(activeCrossSection, contentPos, left, right, top, bottom);
        }
        else
        {
            PopulatePointHoverCard(activeCrossSection, (contentPos.X - left) / (right - left));
        }
        CrossSectionHoverCard.Visibility = CrossSectionHoverHost.Children.Count > 0
            ? Visibility.Visible : Visibility.Collapsed;
    }

    private void OnCrossSectionPointerExited(object sender, PointerRoutedEventArgs args)
    {
        if (CrossSectionCrosshair != null) CrossSectionCrosshair.Visibility = Visibility.Collapsed;
        if (CrossSectionHoverCard != null) CrossSectionHoverCard.Visibility = Visibility.Collapsed;
    }

    private void PopulatePointHoverCard(JsonObject section, double xRatio)
    {
        var data = ParseCrossSection(section);
        if (data == null || data.Distances.Length == 0) return;
        var maxDist = Math.Max(1.0, data.Distances[^1]);
        var targetDist = Math.Clamp(xRatio, 0, 1) * maxDist;
        var nearestIdx = 0;
        var bestDelta = double.MaxValue;
        for (var i = 0; i < data.Distances.Length; i++)
        {
            var d = Math.Abs(data.Distances[i] - targetDist);
            if (d < bestDelta) { bestDelta = d; nearestIdx = i; }
        }
        var sampleDist = data.Distances[nearestIdx];
        var terrainText = nearestIdx < data.Terrain.Length ? $"{data.Terrain[nearestIdx]:0} m" : "—";
        var reqText = nearestIdx < data.RequiredAsl.Length ? $"{data.RequiredAsl[nearestIdx] / 1000:0.0} km" : "—";
        CrossSectionHoverHost.Children.Add(new TextBlock
        {
            Text = L10n.IsEnglish
                ? $"{sampleDist:0} km · Terrain {terrainText} · Sunlit base {reqText}"
                : $"{sampleDist:0} km · 地形 {terrainText} · 受光基准 {reqText}",
            FontSize = 10.5,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });

        var hasStrata = false;
        foreach (var fam in new[] { 1, 2, 3 })
        {
            if (ColumnStratumAt(data, nearestIdx, fam) is not { } stratum) continue;
            hasStrata = true;
            var famName = L10n.IsEnglish
                ? (fam == 1 ? "High" : fam == 2 ? "Mid" : "Low")
                : (fam == 1 ? "高" : fam == 2 ? "中" : "低");
            var stText = stratum.Status == 3
                ? L10n.T("受光可烧")
                : stratum.Status == 2 ? L10n.T("达到受光但被遮挡") : L10n.T("地影区内");
            var stColor = stratum.Status == 3
                ? ColorFrom(0xF5, 0x6B, 0x2E)
                : stratum.Status == 2 ? ColorFrom(0xE0, 0x94, 0x33) : ColorFrom(0x8A, 0x94, 0xA6);
            var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 5 };
            row.Children.Add(new Ellipse { Width = 6, Height = 6, Fill = new SolidColorBrush(stColor), VerticalAlignment = VerticalAlignment.Center });
            row.Children.Add(new TextBlock
            {
                Text = $"{famName} {stratum.BaseAsl / 1000:0.0}–{stratum.TopAsl / 1000:0.0} km · {stText}",
                FontSize = 10,
            });
            CrossSectionHoverHost.Children.Add(row);
        }
        if (!hasStrata)
        {
            CrossSectionHoverHost.Children.Add(new TextBlock
            {
                Text = L10n.T("该列无显著模式云层（缺测或低于阈值）"),
                FontSize = 10, Opacity = .7,
            });
        }
    }

    private async void OnQueryPointProfile(object sender, RoutedEventArgs args) => await QueryPointProfile();

    private async Task QueryPointProfile()
    {
        var selectedModel = selectedResultModel is "gfs" or "ec" ? selectedResultModel : "gfs";
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
            : BuildProfileQuery(report, model, lat, lon, localTime: validTimeLocal);
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
        if (resultKind == "nowcast")
        {
            if (NowcastFrozenStatus != null)
            {
                NowcastFrozenStatus.Text = frozenProfileState switch
                {
                    "loading" => L10n.T("正在读取冻结剖面…"),
                    "ready" when frozenProfile != null =>
                        $"{Text(frozenProfile, "valid_time_local").Replace("T", " ")} · {L10n.T("读取完成")}",
                    "idle" => "",
                    _ => frozenProfileError.Length > 0 ? frozenProfileError : L10n.T("该结果没有可用剖面。"),
                };
                NowcastFrozenStatus.Foreground = frozenProfileState is "error" or "unavailable"
                    ? new SolidColorBrush(Colors.Red)
                    : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            }
            return;
        }
        if (frozenProfileState == "ready" && frozenProfile != null)
        {
            RenderPointCrossSectionChrome(frozenProfile, SelectedReport());
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
            return;
        }
        var message = frozenProfileState switch
        {
            "loading" => L10n.T("正在读取冻结剖面…"),
            "idle" => SelectedReport()?["cross_section"] is JsonObject ? "" : L10n.T("点击「刷新冻结剖面」查看该结果的太阳方向诊断。"),
            "cancelled" => frozenProfileError,
            "unavailable" => frozenProfileError,
            _ => frozenProfileError.Length > 0 ? frozenProfileError : L10n.T("该结果没有可用剖面。"),
        };
        PointProfileNote.Text = message;
        PointProfileNote.Foreground = frozenProfileState is "error" or "unavailable"
            ? new SolidColorBrush(Colors.Red)
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
    }

    internal static int NestedArrayCount(JsonNode? node) =>
        (node as JsonArray)?.OfType<JsonArray>().Sum(row => row.Count)
        ?? (node as JsonArray)?.Count ?? 0;

    // ---------- v3 平滑曲线路径构造器 ----------

    private static Windows.Foundation.Point Midpoint(Windows.Foundation.Point a, Windows.Foundation.Point b) =>
        new((a.X + b.X) * 0.5, (a.Y + b.Y) * 0.5);

    private static void AppendSmoothSegment(PathFigure figure, IReadOnlyList<Windows.Foundation.Point> points)
    {
        if (points.Count <= 1) return;
        if (points.Count == 2)
        {
            figure.Segments.Add(new LineSegment { Point = points[1] });
            return;
        }
        figure.Segments.Add(new LineSegment { Point = Midpoint(points[0], points[1]) });
        for (var i = 1; i < points.Count - 1; i++)
        {
            figure.Segments.Add(new QuadraticBezierSegment
            {
                Point1 = points[i],
                Point2 = Midpoint(points[i], points[i + 1]),
            });
        }
        figure.Segments.Add(new LineSegment { Point = points[^1] });
    }

    private static PathGeometry SmoothOpenGeometry(IReadOnlyList<Windows.Foundation.Point> points)
    {
        var geometry = new PathGeometry();
        if (points.Count == 0) return geometry;
        var figure = new PathFigure { StartPoint = points[0], IsClosed = false };
        AppendSmoothSegment(figure, points);
        geometry.Figures.Add(figure);
        return geometry;
    }

    private static PathGeometry SmoothClosedBandGeometry(
        IReadOnlyList<Windows.Foundation.Point> topPoints,
        IReadOnlyList<Windows.Foundation.Point> bottomPointsReversed)
    {
        var geometry = new PathGeometry();
        if (topPoints.Count == 0 || bottomPointsReversed.Count == 0) return geometry;
        var figure = new PathFigure { StartPoint = topPoints[0], IsClosed = true, IsFilled = true };
        AppendSmoothSegment(figure, topPoints);
        figure.Segments.Add(new LineSegment { Point = bottomPointsReversed[0] });
        AppendSmoothSegment(figure, bottomPointsReversed);
        geometry.Figures.Add(figure);
        return geometry;
    }

    // ---------- v3 剖面图层绘制（暮光天空渐变 + 地影 + 平滑云层 + 受光辉光 + 地形视线） ----------

    internal static void DrawCrossSection(Canvas canvas, JsonObject section, bool showIsobars = false)
    {
        canvas.Children.Clear();
        var data = ParseCrossSection(section);
        if (data == null) return;

        var isDark = canvas.ActualTheme == ElementTheme.Dark;
        var width = Math.Max(220, canvas.Width is > 120 and var w
            ? w : canvas.ActualWidth is > 120 and var actualWidth ? actualWidth : 680);
        var height = Math.Max(180, canvas.Height is > 120 and var h
            ? h : canvas.ActualHeight is > 120 and var actualHeight ? actualHeight : 340);
        double left = 44;
        double right = width - (showIsobars ? 48.0 : 16.0);
        double top = 14;
        double bottom = height - 24;
        if (right <= left + 24 || bottom <= top + 24) return;

        var plotW = right - left;
        var plotH = bottom - top;
        var maxDistance = Math.Max(1.0, data.Distances[^1]);
        const double maxHeight = 18_000.0;
        double X(double dist) => left + Math.Clamp(dist, 0, maxDistance) / maxDistance * plotW;
        double Y(double alt) => top + (1.0 - Math.Clamp(alt, 0, maxHeight) / maxHeight) * plotH;

        var plotLayer = new Canvas
        {
            Width = width,
            Height = height,
            Clip = new RectangleGeometry { Rect = new Windows.Foundation.Rect(left, top, plotW, plotH) },
        };
        canvas.Children.Add(plotLayer);

        // Layer 1 — Twilight sky backdrop
        var skyGrad = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0),
            EndPoint = new Windows.Foundation.Point(0, 1),
        };
        if (isDark)
        {
            skyGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0x0F, 0x1A, 0x30), Offset = 0.0 });
            skyGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0x1F, 0x2B, 0x45), Offset = 0.55 });
            skyGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0x38, 0x30, 0x38), Offset = 1.0 });
        }
        else
        {
            skyGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0xCC, 0xE0, 0xF5), Offset = 0.0 });
            skyGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0xE8, 0xF0, 0xFA), Offset = 0.55 });
            skyGrad.GradientStops.Add(new GradientStop { Color = ColorFrom(0xFA, 0xEA, 0xDB), Offset = 1.0 });
        }
        var skyRect = new Rectangle { Width = plotW, Height = plotH, Fill = skyGrad };
        Canvas.SetLeft(skyRect, left);
        Canvas.SetTop(skyRect, top);
        plotLayer.Children.Add(skyRect);

        var horizonWash = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0.38, 0.25),
            EndPoint = new Windows.Foundation.Point(1.0, 1.0),
        };
        horizonWash.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(0, 250, 160, 87), Offset = 0.0 });
        horizonWash.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb((byte)(isDark ? 46 : 52), 250, 160, 87), Offset = 1.0 });
        var washRect = new Rectangle { Width = plotW, Height = plotH, Fill = horizonWash };
        Canvas.SetLeft(washRect, left);
        Canvas.SetTop(washRect, top);
        plotLayer.Children.Add(washRect);

        // Layer 2 — Earth Shadow (below requiredASL) & Sunlit Corridor (above requiredASL)
        var hasRequired = data.RequiredAsl.Length == data.Distances.Length;
        var reqPoints = hasRequired
            ? Enumerable.Range(0, data.Distances.Length)
                .Select(i => new Windows.Foundation.Point(X(data.Distances[i]), Y(data.RequiredAsl[i])))
                .ToList()
            : [];
        if (reqPoints.Count > 1)
        {
            var shadowFig = new PathFigure { StartPoint = reqPoints[0], IsClosed = true, IsFilled = true };
            AppendSmoothSegment(shadowFig, reqPoints);
            shadowFig.Segments.Add(new LineSegment { Point = new Windows.Foundation.Point(right, bottom) });
            shadowFig.Segments.Add(new LineSegment { Point = new Windows.Foundation.Point(left, bottom) });
            var shadowGeo = new PathGeometry();
            shadowGeo.Figures.Add(shadowFig);
            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
            {
                Data = shadowGeo,
                Fill = new SolidColorBrush(isDark
                    ? Windows.UI.Color.FromArgb(72, 8, 15, 33)
                    : Windows.UI.Color.FromArgb(30, 61, 82, 115)),
            });

            var sunlitFig = new PathFigure { StartPoint = new Windows.Foundation.Point(left, top), IsClosed = true, IsFilled = true };
            sunlitFig.Segments.Add(new LineSegment { Point = new Windows.Foundation.Point(right, top) });
            sunlitFig.Segments.Add(new LineSegment { Point = reqPoints[^1] });
            var revReq = reqPoints.AsEnumerable().Reverse().ToList();
            AppendSmoothSegment(sunlitFig, revReq);
            var sunlitGeo = new PathGeometry();
            sunlitGeo.Figures.Add(sunlitFig);
            var sunlitGrad = new LinearGradientBrush
            {
                StartPoint = new Windows.Foundation.Point(0, 0),
                EndPoint = new Windows.Foundation.Point(1, 1),
            };
            sunlitGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(6, 250, 173, 76), Offset = 0.0 });
            sunlitGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb((byte)(isDark ? 42 : 38), 250, 153, 61), Offset = 1.0 });
            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path { Data = sunlitGeo, Fill = sunlitGrad });
        }

        // Layer 3 — Altitude & dashed distance gridlines (+ optional Isobars)
        for (var alt = 0.0; alt <= maxHeight; alt += 3000.0)
        {
            plotLayer.Children.Add(new Line
            {
                X1 = left, X2 = right, Y1 = Y(alt), Y2 = Y(alt),
                StrokeThickness = 0.6,
                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb((byte)(alt == 0 ? 20 : 38), 140, 150, 165)),
            });
        }
        var distStep = maxDistance <= 120 ? 25.0 : maxDistance <= 250 ? 50.0 : 100.0;
        for (var dist = distStep; dist <= maxDistance + 0.01; dist += distStep)
        {
            var lx = X(dist);
            plotLayer.Children.Add(new Line
            {
                X1 = lx, X2 = lx, Y1 = top, Y2 = bottom,
                StrokeThickness = 0.6,
                StrokeDashArray = [3, 4],
                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(30, 140, 150, 165)),
            });
        }
        if (showIsobars)
        {
            for (var l = 0; l < data.Levels.Length; l++)
            {
                var pts = new List<Windows.Foundation.Point>();
                var temps = new List<double>();
                for (var i = 0; i < data.Distances.Length; i++)
                {
                    if (ScalarAt(data.Heights, i, l) is not { } hAlt) continue;
                    pts.Add(new Windows.Foundation.Point(X(data.Distances[i]), Y(hAlt)));
                    if (ScalarAt(data.Temps, i, l) is { } temp) temps.Add(temp);
                }
                if (pts.Count <= 1) continue;
                var avg = temps.Count == 0 ? 0 : temps.Average();
                var isoColor = avg < -30 ? ColorFrom(0x7A, 0x8F, 0xB8)
                    : avg < 0 ? ColorFrom(0xAD, 0x80, 0x75) : ColorFrom(0xCC, 0x6B, 0x4D);
                plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                {
                    Data = SmoothOpenGeometry(pts),
                    Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(95, isoColor.R, isoColor.G, isoColor.B)),
                    StrokeThickness = 1.0,
                });
            }
        }

        // Layer 4 — Continuous organic cloud strata (low 3 -> mid 2 -> high 1)
        var sampleCount = Math.Max(1, data.Distances.Length - 1);
        var columnSpan = plotW / sampleCount;
        var taper = Math.Min(columnSpan * 0.45, plotW * 0.06);

        foreach (var family in new[] { 3, 2, 1 })
        {
            var runs = new List<List<CloudRunPoint>>();
            var currentRun = new List<CloudRunPoint>();
            for (var i = 0; i < data.Distances.Length; i++)
            {
                if (ColumnStratumAt(data, i, family) is { } stratum)
                {
                    var cx = X(data.Distances[i]);
                    var terrainY = i < data.Terrain.Length ? Y(data.Terrain[i]) : bottom;
                    var botY = Math.Min(terrainY - 1, Y(stratum.BaseAsl));
                    var topY = Math.Max(top + 2, Y(stratum.TopAsl));
                    if (topY > botY - 2)
                    {
                        var mid = Math.Clamp((topY + botY) * 0.5, top + 3, bottom - 3);
                        topY = mid - 1.5;
                        botY = mid + 1.5;
                    }
                    currentRun.Add(new CloudRunPoint(i, cx, topY, botY, stratum));
                }
                else if (currentRun.Count > 0)
                {
                    runs.Add(currentRun);
                    currentRun = [];
                }
            }
            if (currentRun.Count > 0) runs.Add(currentRun);

            foreach (var run in runs)
            {
                var first = run[0];
                var last = run[^1];
                var leftX = Math.Max(left, first.Cx - taper);
                var rightX = Math.Min(right, last.Cx + taper);
                var leftMidY = (first.TopY + first.BottomY) * 0.5;
                var rightMidY = (last.TopY + last.BottomY) * 0.5;

                var topPts = new List<Windows.Foundation.Point> { new(leftX, leftMidY) };
                foreach (var p in run) topPts.Add(new(p.Cx, p.TopY));
                topPts.Add(new(rightX, rightMidY));

                var botPtsRev = new List<Windows.Foundation.Point> { new(rightX, rightMidY) };
                for (var r = run.Count - 1; r >= 0; r--) botPtsRev.Add(new(run[r].Cx, run[r].BottomY));
                botPtsRev.Add(new(leftX, leftMidY));

                var avgAmount = run.Average(p => p.Stratum.Amount);
                var alpha = Math.Min(0.86, 0.42 + avgAmount * 0.48);
                var aTop = (byte)Math.Clamp(alpha * 255, 0, 255);
                var aBot = (byte)Math.Clamp(alpha * 0.94 * 255, 0, 255);
                Windows.UI.Color topColor, botColor;
                switch (family)
                {
                    case 1:
                        topColor = isDark ? Windows.UI.Color.FromArgb(aTop, 199, 224, 247) : Windows.UI.Color.FromArgb(aTop, 235, 245, 255);
                        botColor = isDark ? Windows.UI.Color.FromArgb(aBot, 138, 178, 222) : Windows.UI.Color.FromArgb(aBot, 163, 199, 235);
                        break;
                    case 2:
                        topColor = isDark ? Windows.UI.Color.FromArgb(aTop, 143, 201, 209) : Windows.UI.Color.FromArgb(aTop, 173, 219, 224);
                        botColor = isDark ? Windows.UI.Color.FromArgb(aBot, 92, 153, 166) : Windows.UI.Color.FromArgb(aBot, 112, 171, 184);
                        break;
                    default:
                        topColor = isDark ? Windows.UI.Color.FromArgb(aTop, 128, 140, 156) : Windows.UI.Color.FromArgb(aTop, 158, 171, 184);
                        botColor = isDark ? Windows.UI.Color.FromArgb(aBot, 84, 97, 112) : Windows.UI.Color.FromArgb(aBot, 112, 125, 138);
                        break;
                }
                var cloudGrad = new LinearGradientBrush
                {
                    StartPoint = new Windows.Foundation.Point(0, 0),
                    EndPoint = new Windows.Foundation.Point(0, 1),
                };
                cloudGrad.GradientStops.Add(new GradientStop { Color = topColor, Offset = 0.0 });
                cloudGrad.GradientStops.Add(new GradientStop { Color = botColor, Offset = 1.0 });

                plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                {
                    Data = SmoothClosedBandGeometry(topPts, botPtsRev),
                    Fill = cloudGrad,
                });
                plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                {
                    Data = SmoothOpenGeometry(topPts),
                    Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb((byte)(isDark ? 64 : 115), 255, 255, 255)),
                    StrokeThickness = 1.0,
                });

                // Fire-sky illuminated base underglow (status == 3) & lit-but-occluded (status == 2)
                var subStart = 0;
                while (subStart < run.Count)
                {
                    var st = run[subStart].Stratum.Status;
                    var subEnd = subStart;
                    while (subEnd + 1 < run.Count && run[subEnd + 1].Stratum.Status == st)
                        subEnd++;
                    if (st == 3 || st == 2)
                    {
                        var subLeft = subStart == 0 ? leftX : (run[subStart - 1].Cx + run[subStart].Cx) * 0.5;
                        var subRight = subEnd == run.Count - 1 ? rightX : (run[subEnd].Cx + run[subEnd + 1].Cx) * 0.5;

                        var subTopPts = new List<Windows.Foundation.Point>();
                        var subBotPts = new List<Windows.Foundation.Point>();
                        if (subStart == 0)
                        {
                            var my = (run[0].TopY + run[0].BottomY) * 0.5;
                            subTopPts.Add(new(leftX, my));
                            subBotPts.Add(new(leftX, my));
                        }
                        else
                        {
                            var bMid = (run[subStart - 1].BottomY + run[subStart].BottomY) * 0.5;
                            var mMid = ((run[subStart - 1].TopY + run[subStart - 1].BottomY) * 0.5
                                + (run[subStart].TopY + run[subStart].BottomY) * 0.5) * 0.5;
                            subTopPts.Add(new(subLeft, mMid));
                            subBotPts.Add(new(subLeft, bMid));
                        }
                        for (var si = subStart; si <= subEnd; si++)
                        {
                            var p = run[si];
                            subTopPts.Add(new(p.Cx, p.TopY + (p.BottomY - p.TopY) * 0.42));
                            subBotPts.Add(new(p.Cx, p.BottomY));
                        }
                        if (subEnd == run.Count - 1)
                        {
                            var my = (run[subEnd].TopY + run[subEnd].BottomY) * 0.5;
                            subTopPts.Add(new(rightX, my));
                            subBotPts.Add(new(rightX, my));
                        }
                        else
                        {
                            var bMid = (run[subEnd].BottomY + run[subEnd + 1].BottomY) * 0.5;
                            var mMid = ((run[subEnd].TopY + run[subEnd].BottomY) * 0.5
                                + (run[subEnd + 1].TopY + run[subEnd + 1].BottomY) * 0.5) * 0.5;
                            subTopPts.Add(new(subRight, mMid));
                            subBotPts.Add(new(subRight, bMid));
                        }

                        var revSubBot = subBotPts.AsEnumerable().Reverse().ToList();
                        if (st == 3)
                        {
                            var glowFill = new LinearGradientBrush
                            {
                                StartPoint = new Windows.Foundation.Point(0, 0),
                                EndPoint = new Windows.Foundation.Point(0, 1),
                            };
                            glowFill.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(0, 255, 158, 68), Offset = 0.0 });
                            glowFill.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(115, 255, 158, 68), Offset = 0.45 });
                            glowFill.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(220, 245, 93, 40), Offset = 1.0 });
                            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                            {
                                Data = SmoothClosedBandGeometry(subTopPts, revSubBot),
                                Fill = glowFill,
                            });
                            var subBotCurve = SmoothOpenGeometry(subBotPts);
                            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                            {
                                Data = subBotCurve,
                                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(76, 250, 115, 46)),
                                StrokeThickness = 5.0,
                                StrokeStartLineCap = PenLineCap.Round,
                                StrokeEndLineCap = PenLineCap.Round,
                                StrokeLineJoin = PenLineJoin.Round,
                            });
                            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                            {
                                Data = SmoothOpenGeometry(subBotPts),
                                Stroke = new SolidColorBrush(ColorFrom(0xFF, 0x8A, 0x33)),
                                StrokeThickness = 2.2,
                                StrokeStartLineCap = PenLineCap.Round,
                                StrokeEndLineCap = PenLineCap.Round,
                                StrokeLineJoin = PenLineJoin.Round,
                            });
                        }
                        else
                        {
                            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                            {
                                Data = SmoothClosedBandGeometry(subTopPts, revSubBot),
                                Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(40, 230, 153, 56)),
                            });
                            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                            {
                                Data = SmoothOpenGeometry(subBotPts),
                                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(225, 224, 148, 51)),
                                StrokeThickness = 1.2,
                                StrokeDashArray = [4, 3],
                                StrokeStartLineCap = PenLineCap.Round,
                                StrokeEndLineCap = PenLineCap.Round,
                            });
                        }
                    }
                    subStart = subEnd + 1;
                }
            }
        }

        // Layer 5 — Smooth requiredASL curve + inline pill
        if (reqPoints.Count > 1)
        {
            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
            {
                Data = SmoothOpenGeometry(reqPoints),
                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(52, 245, 168, 61)),
                StrokeThickness = 5.0,
            });
            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
            {
                Data = SmoothOpenGeometry(reqPoints),
                Stroke = new SolidColorBrush(ColorFrom(0xEB, 0x96, 0x30)),
                StrokeThickness = 1.8,
                StrokeDashArray = [7, 4],
                StrokeStartLineCap = PenLineCap.Round,
                StrokeEndLineCap = PenLineCap.Round,
            });

            var pillIdx = Math.Clamp((int)(reqPoints.Count * 0.72), 0, reqPoints.Count - 1);
            var anchorPt = reqPoints[pillIdx];
            var pill = new Border
            {
                Padding = new Thickness(6, 2, 6, 2),
                CornerRadius = new CornerRadius(9),
                Background = new SolidColorBrush(isDark
                    ? Windows.UI.Color.FromArgb(145, 12, 18, 30)
                    : Windows.UI.Color.FromArgb(170, 255, 255, 255)),
                BorderBrush = new SolidColorBrush(Windows.UI.Color.FromArgb(120, 235, 150, 48)),
                BorderThickness = new Thickness(0.8),
                Child = new TextBlock
                {
                    Text = L10n.T("受光高度线"),
                    FontSize = 9,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Foreground = new SolidColorBrush(ColorFrom(0xF5, 0xB8, 0x57)),
                },
            };
            Canvas.SetLeft(pill, Math.Clamp(anchorPt.X - 36, left + 6, right - 78));
            Canvas.SetTop(pill, Math.Clamp(anchorPt.Y - 22, top + 6, bottom - 24));
            plotLayer.Children.Add(pill);
        }

        // Layer 6 — Smooth mountain/terrain silhouette
        if (data.Terrain.Length == data.Distances.Length)
        {
            var terrainPts = Enumerable.Range(0, data.Distances.Length)
                .Select(i => new Windows.Foundation.Point(X(data.Distances[i]), Y(data.Terrain[i])))
                .ToList();
            var tFig = new PathFigure { StartPoint = terrainPts[0], IsClosed = true, IsFilled = true };
            AppendSmoothSegment(tFig, terrainPts);
            tFig.Segments.Add(new LineSegment { Point = new Windows.Foundation.Point(right, bottom) });
            tFig.Segments.Add(new LineSegment { Point = new Windows.Foundation.Point(left, bottom) });
            var tGeo = new PathGeometry();
            tGeo.Figures.Add(tFig);
            var tGrad = new LinearGradientBrush
            {
                StartPoint = new Windows.Foundation.Point(0, 0),
                EndPoint = new Windows.Foundation.Point(0, 1),
            };
            if (isDark)
            {
                tGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(194, 87, 102, 107), Offset = 0.0 });
                tGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(235, 38, 46, 56), Offset = 1.0 });
            }
            else
            {
                tGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(122, 112, 125, 117), Offset = 0.0 });
                tGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(174, 71, 84, 82), Offset = 1.0 });
            }
            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path { Data = tGeo, Fill = tGrad });
            plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
            {
                Data = SmoothOpenGeometry(terrainPts),
                Stroke = new SolidColorBrush(ColorFrom(0x73, 0x82, 0x7D)),
                StrokeThickness = 1.6,
            });
        }

        // Layer 7 — Observer beacon, dashed sightline to primary burnable base, top-right sun cue
        var observerY = data.Terrain.Length > 0 ? Y(data.Terrain[0]) : bottom;
        var observer = new Windows.Foundation.Point(X(0), Math.Clamp(observerY, top + 10, bottom - 5));
        if (PrimaryBurnableFocus(data) is { } focus)
        {
            var targetPt = new Windows.Foundation.Point(X(focus.DistanceKm), Y(focus.BaseAsl));
            plotLayer.Children.Add(new Line
            {
                X1 = observer.X, Y1 = observer.Y, X2 = targetPt.X, Y2 = targetPt.Y,
                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(158, 252, 158, 71)),
                StrokeThickness = 1.3,
                StrokeDashArray = [5, 4],
            });
            var ringOuter = new Ellipse
            {
                Width = 16, Height = 16,
                Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(72, 250, 115, 46)),
            };
            Canvas.SetLeft(ringOuter, targetPt.X - 8);
            Canvas.SetTop(ringOuter, targetPt.Y - 8);
            plotLayer.Children.Add(ringOuter);

            var ringInner = new Ellipse
            {
                Width = 9, Height = 9,
                Fill = new SolidColorBrush(ColorFrom(0xFF, 0x8F, 0x38)),
                Stroke = new SolidColorBrush(Colors.White),
                StrokeThickness = 1.3,
            };
            Canvas.SetLeft(ringInner, targetPt.X - 4.5);
            Canvas.SetTop(ringInner, targetPt.Y - 4.5);
            plotLayer.Children.Add(ringInner);

            var baseKm = focus.BaseAsl / 1000.0;
            var targetPill = new Border
            {
                Padding = new Thickness(7, 2, 7, 2),
                CornerRadius = new CornerRadius(10),
                Background = new SolidColorBrush(Windows.UI.Color.FromArgb(224, 235, 92, 41)),
                Child = new TextBlock
                {
                    Text = L10n.IsEnglish ? $"Lit base {baseKm:0.0}km" : $"可烧云底 {baseKm:0.0}km",
                    FontSize = 9.5,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Foreground = new SolidColorBrush(Colors.White),
                },
            };
            var pX = Math.Clamp(targetPt.X - 46, left + 6, right - 102);
            var pY = targetPt.Y - 28 >= top + 6 ? targetPt.Y - 28 : Math.Min(bottom - 24, targetPt.Y + 10);
            Canvas.SetLeft(targetPill, pX);
            Canvas.SetTop(targetPill, pY);
            plotLayer.Children.Add(targetPill);
        }

        var obsHalo = new Ellipse
        {
            Width = 16, Height = 16,
            Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(72, 245, 122, 61)),
        };
        Canvas.SetLeft(obsHalo, observer.X - 8);
        Canvas.SetTop(obsHalo, observer.Y - 8);
        plotLayer.Children.Add(obsHalo);
        var obsDot = new Ellipse
        {
            Width = 9, Height = 9,
            Fill = new SolidColorBrush(ColorFrom(0xF5, 0x7A, 0x3D)),
            Stroke = new SolidColorBrush(Colors.White),
            StrokeThickness = 1.4,
        };
        Canvas.SetLeft(obsDot, observer.X - 4.5);
        Canvas.SetTop(obsDot, observer.Y - 4.5);
        plotLayer.Children.Add(obsDot);

        var obsLabel = new TextBlock
        {
            Text = L10n.T("观测点"),
            FontSize = 10,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        };
        Canvas.SetLeft(obsLabel, observer.X + 10);
        Canvas.SetTop(obsLabel, Math.Max(top + 6, observer.Y - 16));
        plotLayer.Children.Add(obsLabel);

        var sunText = DoubleValue(section["solar_azimuth_deg"]) is { } azDeg
            ? (L10n.IsEnglish ? $"☀ Sun {azDeg:0}°" : $"☀ 太阳方向 {azDeg:0}°")
            : (L10n.IsEnglish ? "☀ Sun" : "☀ 太阳方向");
        var sunCue = new TextBlock
        {
            Text = sunText,
            FontSize = 10,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Foreground = new SolidColorBrush(ColorFrom(0xFA, 0xB8, 0x57)),
        };
        Canvas.SetLeft(sunCue, Math.Max(left + 8, right - 108));
        Canvas.SetTop(sunCue, top + 6);
        plotLayer.Children.Add(sunCue);

        // Crisp rounded plot border
        var borderRect = new Rectangle
        {
            Width = plotW,
            Height = plotH,
            RadiusX = 10,
            RadiusY = 10,
            Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb((byte)(isDark ? 72 : 56), 140, 150, 165)),
            StrokeThickness = 1.0,
        };
        Canvas.SetLeft(borderRect, left);
        Canvas.SetTop(borderRect, top);
        canvas.Children.Add(borderRect);

        // Outside axis labels: Altitude (left)
        for (var alt = 0.0; alt <= maxHeight; alt += 3000.0)
        {
            var lbl = new TextBlock
            {
                Text = $"{alt / 1000:0}km",
                FontSize = 9.5,
                Opacity = .68,
                Width = left - 8,
                TextAlignment = TextAlignment.Right,
            };
            Canvas.SetLeft(lbl, 2);
            Canvas.SetTop(lbl, Y(alt) - 7);
            canvas.Children.Add(lbl);
        }

        // Outside axis labels: Distance (bottom, skip 0km)
        for (var dist = distStep; dist <= maxDistance + 0.01; dist += distStep)
        {
            var lbl = new TextBlock { Text = $"{dist:0}km", FontSize = 9.5, Opacity = .68 };
            Canvas.SetLeft(lbl, Math.Min(right - 28, X(dist) - 12));
            Canvas.SetTop(lbl, bottom + 4);
            canvas.Children.Add(lbl);
        }

        // Outside axis labels: Isobars (right)
        if (showIsobars)
        {
            var lastLabelY = double.NegativeInfinity;
            for (var l = 0; l < data.Levels.Length; l++)
            {
                double? lastH = null;
                for (var i = data.Distances.Length - 1; i >= 0; i--)
                {
                    if (ScalarAt(data.Heights, i, l) is { } hv) { lastH = hv; break; }
                }
                if (lastH is not { } lh) continue;
                var ly = Y(lh);
                if (ly < top + 4 || ly > bottom - 4 || Math.Abs(ly - lastLabelY) < 13) continue;
                lastLabelY = ly;
                var lbl = new TextBlock { Text = $"{data.Levels[l]}hPa", FontSize = 8.5, Opacity = .72 };
                Canvas.SetLeft(lbl, right + 5);
                Canvas.SetTop(lbl, ly - 6);
                canvas.Children.Add(lbl);
            }
        }
    }

    private static (double DistanceKm, double BaseAsl)? PrimaryBurnableFocus(CrossSectionParsed data)
    {
        foreach (var target in data.Targets)
        {
            if (Text(target, "status") != "burnable") continue;
            if (DoubleValue(target["distance_km"]) is { } dist && DoubleValue(target["cloud_base_asl_m"]) is { } b)
                return (dist, b);
        }
        var lit = new List<(double Dist, double Base)>();
        for (var i = 0; i < data.Distances.Length; i++)
        {
            for (var fam = 1; fam <= 3; fam++)
            {
                if (ColumnStratumAt(data, i, fam) is { Status: 3 } s)
                    lit.Add((data.Distances[i], s.BaseAsl));
            }
        }
        if (lit.Count == 0) return null;
        lit.Sort((a, b) => a.Dist.CompareTo(b.Dist));
        return lit[lit.Count / 2];
    }
}
