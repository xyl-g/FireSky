using Microsoft.UI;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;

namespace FireSky;

/// <summary>
/// 实况专用展示（对齐 macOS WorkNowcast* v3）：卫星帧信息、零空缺分组指标卡、
/// 质量趋势、v3 卫星后验概率平滑剖面（天空渐变 + 未知琥珀斜纹幕墙 + GFS 先验等压线 + 悬停读数）、
/// 折叠式冻结卫星剖面查询、不可用原因透传。桌面 bands 恒为 HSD B07/B13/B15，不冒充 HCAI 云类真值。
/// </summary>
public sealed partial class MainWindow
{
    private bool nowcastShowIsobars = true;

    internal JsonObject NowcastObservation =>
        (SelectedReport()?["nowcast"] as JsonObject) ?? [];

    private string NowcastValue(string key, JsonObject report)
    {
        var observation = report["nowcast"] as JsonObject ?? new JsonObject();
        if (key == "aod") return PointAod.Value;
        if (key == "satellite_motion_quality")
        {
            var value = Text(observation, "satellite_motion_quality");
            return value switch
            {
                "observation" => L10n.T("原始观测"), "strong" => L10n.T("可靠"), "decayed" => L10n.T("已衰减"),
                "unknown" => L10n.T("未知"), "diagnostic-only" => L10n.T("仅供诊断"),
                "" => "—", _ => value,
            };
        }
        if (key == "satellite_low_cloud_state")
        {
            var value = Text(observation, "satellite_low_cloud_state");
            return value switch
            {
                "detected" => L10n.T("检测到低云"), "clear" => L10n.T("无低云"), "unknown" => L10n.T("未知"),
                "" => "—", _ => value,
            };
        }
        if (key == "cloud_type_label")
        {
            if ((observation as IDictionary<string, JsonNode?>).Count == 0) return "—";
            return Text(observation, "cloud_type_label") is { Length: > 0 } label ? label : L10n.T("未计算");
        }
        if (DoubleValue(observation[key]) is not { } number) return "—";
        return key switch
        {
            "near_cloud_ratio" or "far_cloud_ratio" or "geometric_path_blocking_risk"
                or "calibrated_sample_coverage_ratio" or "confidence" or "convective_score_ceiling"
                or "radar_reliability_score" or "radar_local_precip_ratio" or "radar_path_precip_ratio"
                or "cloud_blanket_score" or "cloud_texture_score" or "horizon_clearance_score"
                or "effective_convective_risk" or "effective_path_blocking_risk"
                => $"{number * 100:0}%",
            _ => number.ToString("0.00", CultureInfo.InvariantCulture),
        };
    }

    private void UpdateNowcastBanners(JsonObject? report)
    {
        if (NowcastNoticeBar == null || NowcastMapWarningBar == null) return;
        var isNowcastPage = page == "nowcast";
        NowcastNoticeBar.IsOpen = isNowcastPage && (report == null || resultKind != "nowcast" || dateTab != "今天");
        if (isNowcastPage && report?["map_capability"] is JsonObject mapCapability
            && BoolValue(mapCapability, "available") == false)
        {
            var rawReason = Text(mapCapability, "reason");
            if (rawReason != "native-nowcast-map-publication-not-requested")
            {
                NowcastMapWarningBar.Message = string.IsNullOrWhiteSpace(rawReason)
                    ? L10n.T("实况地图未发布")
                    : FriendlyMessages.Map(rawReason);
                NowcastMapWarningBar.IsOpen = true;
                return;
            }
        }
        NowcastMapWarningBar.IsOpen = false;
        NowcastMapWarningBar.Message = "";
    }

    private void RenderNowcastResult()
    {
        HeroHost.Visibility = Visibility.Visible;
        MetricHost.Visibility = Visibility.Visible;
        BreakdownCard.Visibility = Visibility.Collapsed;
        UpdateNowcastBanners(SelectedReport());
        RenderNowcastHero();
        RenderNowcastMetrics();
        RenderChart();
        RenderLedger();
        RenderDiagnostics();
        RenderNowcastProfileTab();
    }

    private void RenderNowcastHero()
    {
        var report = SelectedReport();
        var card = heroCards["gfs"];
        var observation = report?["nowcast"] as JsonObject ?? new JsonObject();
        var quality = report != null && DoubleValue(report["peak_quality_index"] ?? report["quality_index"]) is { } q
            ? q : (double?)null;
        card.Status.Text = report == null ? L10n.T("暂无结果") : L10n.T("分析完成");
        card.Score.Text = quality is { } value ? value.ToString("0.00") : "--";
        card.Score.Foreground = quality is { }
            ? ModelBrush("gfs")
            : (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        card.Level.Text = Text(report, "peak_quality_level") is { Length: > 0 } pLevel ? pLevel
            : Text(report, "quality_level") is { Length: > 0 } level ? level : L10n.T("峰值质量指数");
        card.Best.Text = TimeLabel(Text(report, "best_time_local"));
        card.Window.Text =
            $"推荐窗口 {TimeLabel(Text(report?["timing_window"], "start_time_local"))} – {TimeLabel(Text(report?["timing_window"], "end_time_local"))}";
        var source = Text(observation, "source");
        var bands = Text(observation, "bands");
        var channel = Text(observation, "image_channel");
        var frame = Text(observation, "image_valid_time_utc");
        if (frame.Length == 0) frame = Text(observation, "frame_time_utc");
        card.Chips.Text = string.Join("   ",
            new[]
            {
                frame.Length >= 16 ? $"卫星帧 {frame[0..16].Replace("T", " ")}" : "",
                bands is { Length: > 0 } ? bands : channel,
                source is { Length: > 0 } ? $"来源 {source}" : "",
            }.Where(part => part.Length > 0));
        ToolTipService.SetToolTip(card.Chips, L10n.T("卫星帧时次与通道来自真实观测载荷；缺项如实显示。"));
        card.Source.Text = L10n.T("HSD B07/B13/B15 云概率代理（posterior）；HSD 分支不发布 COT、云相或官方云高真值。");
        card.GenusText.Text = "";
        card.GenusBadge.Visibility = Visibility.Collapsed;
        card.Error.Text = "";
        card.Error.Visibility = Visibility.Collapsed;
        heroCards["ec"].Panel.Visibility = Visibility.Collapsed;
        RenderOverviewSummaryCard(nowcastMode: true);
    }

    private void RenderNowcastMetrics()
    {
        var report = SelectedReport();
        var observation = report?["nowcast"] as JsonObject ?? new JsonObject();
        var aod = AodReading([report]);
        MetricHost.Children.Clear();
        if (report == null)
        {
            MetricHost.Children.Add(new TextBlock
            {
                Text = L10n.T("暂无实况结果"),
                Opacity = .65,
            });
            return;
        }

        string DynamicCaption((string Key, string _, string Title, string Caption) spec)
        {
            if (spec.Key == "aod") return aod.Caption;
            if (spec.Key == "geometric_path_blocking_risk")
            {
                return DoubleValue(observation["geometric_path_samples"]) is { } samples
                    ? (L10n.IsEnglish
                        ? $"Solar path · {(int)samples} light-path samples"
                        : $"太阳入射路径 · 基于 {(int)samples} 个光路样本")
                    : L10n.T("光路样本数未提供");
            }
            return L10n.T(spec.Caption);
        }

        FrameworkElement BuildCell((string Key, string _, string Title, string Caption) spec)
        {
            var stack = new StackPanel { Spacing = 3 };
            stack.Children.Add(new TextBlock
            {
                Text = L10n.T(spec.Title),
                FontSize = 11,
                Opacity = .8,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
                TextTrimming = TextTrimming.CharacterEllipsis,
            });
            if (spec.Key == "aod")
            {
                var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
                row.Children.Add(new TextBlock
                {
                    Text = aod.Value,
                    FontSize = 17,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                });
                if (aod.Available)
                {
                    row.Children.Add(new TextBlock
                    {
                        Text = aod.Source,
                        FontSize = 10,
                        Opacity = .72,
                        VerticalAlignment = VerticalAlignment.Center,
                    });
                }
                ToolTipService.SetToolTip(row, aod.Detail);
                stack.Children.Add(row);
            }
            else
            {
                stack.Children.Add(new TextBlock
                {
                    Text = NowcastValue(spec.Key, report),
                    FontSize = 17,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                });
            }
            var capText = DynamicCaption(spec);
            var cap = new TextBlock
            {
                Text = capText,
                FontSize = 9.5,
                Opacity = .6,
                TextTrimming = TextTrimming.CharacterEllipsis,
                MaxLines = 1,
            };
            ToolTipService.SetToolTip(cap, capText);
            stack.Children.Add(cap);
            return stack;
        }

        Border BuildGroupedGridCard(string title, string? subtitle,
            IReadOnlyList<(string Key, string Key1, string Title, string Caption)> specs, int columns)
        {
            var cardStack = new StackPanel { Spacing = 10 };
            var header = new Grid { ColumnSpacing = 8 };
            header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            header.Children.Add(new TextBlock
            {
                Text = title,
                FontSize = 11,
                Opacity = .7,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            if (!string.IsNullOrEmpty(subtitle))
            {
                var sub = new TextBlock
                {
                    Text = subtitle,
                    FontSize = 9.5,
                    Opacity = .68,
                    TextTrimming = TextTrimming.CharacterEllipsis,
                    MaxLines = 1,
                    VerticalAlignment = VerticalAlignment.Center,
                };
                ToolTipService.SetToolTip(sub, subtitle);
                Grid.SetColumn(sub, 1);
                header.Children.Add(sub);
            }
            cardStack.Children.Add(header);

            var rows = (specs.Count + columns - 1) / columns;
            for (var r = 0; r < rows; r++)
            {
                if (r > 0)
                {
                    cardStack.Children.Add(new Border
                    {
                        Height = 1,
                        Opacity = .4,
                        Background = (Brush)Application.Current.Resources["DividerStrokeColorDefaultBrush"],
                    });
                }
                var grid = new Grid { ColumnSpacing = 14 };
                for (var c = 0; c < columns; c++)
                {
                    grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                    var idx = r * columns + c;
                    if (idx < specs.Count)
                    {
                        var cell = BuildCell(specs[idx]);
                        Grid.SetColumn(cell, c);
                        grid.Children.Add(cell);
                    }
                }
                cardStack.Children.Add(grid);
            }

            return new Border
            {
                Child = cardStack,
                Padding = new Thickness(14),
                CornerRadius = new CornerRadius(14),
                Background = ThemeBrush("FireSkyCardBrush"),
                BorderBrush = ThemeBrush("FireSkyCardBorderBrush"),
                BorderThickness = new Thickness(1),
            };
        }

        // 1. 卫星云况（6 项零空缺 3×2；仅当 cloud_type_label 已计算时才追加第 7 项）
        var cloudSpecs = NowcastMetrics[0..6].ToList();
        if (Text(observation, "cloud_type_label") is { Length: > 0 })
            cloudSpecs.Add(NowcastMetrics[6]);
        MetricHost.Children.Add(BuildGroupedGridCard(L10n.T("卫星云况"), null, cloudSpecs, 3));

        // 2. 运动与采样 (2×2) | 光学与雷达 (2×2 + 内联雷达时效)
        var motionSpecs = NowcastMetrics[7..11];
        var opticsRadarSpecs = new[]
        {
            NowcastMetrics[14],
            NowcastMetrics[11],
            NowcastMetrics[12],
            NowcastMetrics[13],
        };
        var radarSource = Text(observation, "radar_source");
        var radarTimeliness = string.IsNullOrEmpty(radarSource)
            ? L10n.T("雷达：后端未接入雷达观测")
            : $"雷达 {radarSource} · 观测 {TimeLabel(Text(observation, "radar_valid_time_utc"))} UTC · 帧龄 {(DoubleValue(observation["radar_age_minutes"]) is { } age ? $"{age:0}" : "—")} 分钟";

        var split = new Grid { ColumnSpacing = 12 };
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        split.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var leftCard = BuildGroupedGridCard(L10n.T("运动与采样"), null, motionSpecs, 2);
        var rightCard = BuildGroupedGridCard(L10n.T("光学与雷达"), radarTimeliness, opticsRadarSpecs, 2);
        Grid.SetColumn(leftCard, 0);
        Grid.SetColumn(rightCard, 1);
        split.Children.Add(leftCard);
        split.Children.Add(rightCard);
        MetricHost.Children.Add(split);
    }

    private sealed record NowcastProfileCell(double StartKm, double EndKm, double BottomKm, double TopKm, double? Probability);
    private sealed record NowcastPressureRow(int Pressure, double HeightKm);
    private sealed record NowcastColumnSlice(double StartKm, double EndKm, List<NowcastProfileCell> Cells)
    {
        public bool IsFullyUnknown => Cells.All(c => c.Probability == null);
    }

    private static (List<NowcastProfileCell> Cells, List<NowcastPressureRow> Rows, List<string> Bands, double? Bearing)
        ParseNowcastProfile(JsonObject profile, JsonObject? report)
    {
        var cells = new List<NowcastProfileCell>();
        var samples = (profile["samples"] as JsonArray ?? [])
            .OfType<JsonObject>()
            .Where(s => double.IsFinite(Number(s, "distance_km")))
            .OrderBy(s => Number(s, "distance_km"))
            .ToList();

        var allLevels = new List<JsonObject>();
        for (var i = 0; i < samples.Count; i++)
        {
            var sample = samples[i];
            var distance = Number(sample, "distance_km");
            var before = i > 0 ? Number(samples[i - 1], "distance_km") : distance - 10;
            var after = i + 1 < samples.Count ? Number(samples[i + 1], "distance_km") : distance + 10;
            var startKm = Math.Max(0, (before + distance) * 0.5);
            var endKm = (distance + after) * 0.5;
            var levels = (sample["levels"] as JsonArray ?? [])
                .OfType<JsonObject>()
                .Where(l => (DoubleValue(l["height_m"]) ?? -1) >= 0)
                .OrderBy(l => DoubleValue(l["height_m"]) ?? 0)
                .ToList();
            allLevels.AddRange(levels);
            for (var layer = 0; layer < levels.Count; layer++)
            {
                var lvl = levels[layer];
                var h = DoubleValue(lvl["height_m"]) ?? 0;
                var lower = layer > 0 ? DoubleValue(levels[layer - 1]["height_m"]) ?? 0 : 0;
                var upper = layer + 1 < levels.Count ? DoubleValue(levels[layer + 1]["height_m"]) ?? h + 1000 : h + 1000;
                var retrieved = Text(sample, "status") == "retrieved" && Text(lvl, "status") != "unknown";
                var prob = retrieved
                    ? DoubleValue(lvl["hsd_posterior_percent"] ?? lvl["cloud_probability_percent"])
                    : null;
                if (prob.HasValue) prob = Math.Clamp(prob.Value, 0, 100);
                cells.Add(new NowcastProfileCell(
                    startKm, endKm,
                    Math.Max(0, (lower + h) / 2000.0),
                    (h + upper) / 2000.0,
                    prob));
            }
        }

        var maxLevelH = Math.Max(10_000.0, allLevels
            .Select(l => DoubleValue(l["height_m"]))
            .Where(v => v.HasValue)
            .Select(v => v!.Value)
            .DefaultIfEmpty(10_000.0)
            .Max());
        var pressures = (profile["pressure_levels_hpa"] as JsonArray ?? [])
            .Select(node => (int?)DoubleValue(node))
            .Where(v => v.HasValue)
            .Select(v => v!.Value)
            .ToList();
        var rows = new List<NowcastPressureRow>();
        for (var idx = 0; idx < pressures.Count; idx++)
        {
            var p = pressures[idx];
            var matched = allLevels.FirstOrDefault(l =>
                Math.Abs((DoubleValue(l["pressure_hpa"]) ?? -1) - p) < 0.5 && DoubleValue(l["height_m"]) != null);
            var hM = matched != null
                ? DoubleValue(matched["height_m"])!.Value
                : maxLevelH * (0.08 + idx / (double)Math.Max(1, pressures.Count - 1) * 0.84);
            rows.Add(new NowcastPressureRow(p, hM / 1000.0));
        }

        var bands = (profile["bands"] as JsonArray ?? [])
            .Select(StringOrNull).Where(s => !string.IsNullOrEmpty(s)).Cast<string>().ToList();
        double? bearing = samples.Select(s => DoubleValue(s["bearing_deg"])).FirstOrDefault(v => v.HasValue)
            ?? DoubleValue(report?["cross_section"]?["solar_azimuth_deg"]);

        return (cells, rows, bands, bearing);
    }

    private void RenderNowcastProfileTab()
    {
        var report = SelectedReport();
        var observation = report?["nowcast"] as JsonObject ?? new JsonObject();
        if (ProfileTitleText != null) ProfileTitleText.Text = L10n.T("太阳方向卫星剖面");
        if (FrozenProfileButton != null) FrozenProfileButton.Visibility = Visibility.Collapsed;
        if (PointCloudGenusBar != null) PointCloudGenusBar.Visibility = Visibility.Collapsed;
        if (PointTargetsHost != null)
        {
            PointTargetsHost.Children.Clear();
            PointTargetsHost.Visibility = Visibility.Collapsed;
        }
        ProfileStatus.Text = L10n.T("周期 ") + Text(report, "cycle_time_utc");

        // 折叠冻结剖面查询面板
        if (NowcastFrozenExpander != null && NowcastProfileStepPicker != null)
        {
            var stepTimes = (report?["steps"] as JsonArray ?? [])
                .OfType<JsonObject>()
                .Select(s => Text(s, "valid_time_local"))
                .Where(t => t.Length > 0)
                .ToList();
            var sourceAvailable = BoolValue(report?["profile_source"], "available") == true;
            NowcastFrozenExpander.Visibility = Visibility.Visible;
            NowcastProfileStepPicker.Items.Clear();
            foreach (var st in stepTimes)
                NowcastProfileStepPicker.Items.Add(new ComboBoxItem { Content = st.Replace("T", " "), Tag = st });
            var best = Text(report, "best_time_local");
            var idx = stepTimes.FindIndex(t => t == best);
            NowcastProfileStepPicker.SelectedIndex = idx >= 0 ? idx : (stepTimes.Count > 0 ? 0 : -1);
            NowcastProfileStepPicker.IsEnabled = sourceAvailable && stepTimes.Count > 0;
            NowcastFrozenQueryButton.IsEnabled = sourceAvailable && stepTimes.Count > 0 && !busy;
            if (!sourceAvailable || stepTimes.Count == 0)
                NowcastFrozenStatus.Text = L10n.T("此结果没有可查询的冻结卫星剖面。");
            else
                RenderFrozenProfile();
        }

        var profile = observation["satellite_vertical_profile"] as JsonObject;
        if (profile == null)
        {
            activeCrossSection = null;
            activeCrossSectionIsNowcast = true;
            ProfileTakeawayBanner.Visibility = Visibility.Collapsed;
            ProfileControlsBar.Visibility = Visibility.Collapsed;
            PointCrossSectionLegendScroll.Visibility = Visibility.Collapsed;
            PointCrossSection.Visibility = Visibility.Collapsed;
            PointProfileNote.Text = L10n.T("该实况结果没有卫星后验概率剖面。");
            PointProfileNote.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            return;
        }

        var (cells, rows, bands, bearing) = ParseNowcastProfile(profile, report);
        var status = Text(profile, "status");
        var samples = profile["samples"] as JsonArray ?? [];
        var sampleCount = (long?)DoubleValue(profile["sample_count"]) ?? samples.Count;
        var usable = (long?)DoubleValue(profile["usable_sample_count"]) ?? sampleCount;
        var unknownCount = (long?)DoubleValue(profile["unknown_sample_count"]);
        var coverage = DoubleValue(profile["coverage"]);
        var statusLabel = status switch
        {
            "available" => L10n.T("完整覆盖"), "partial_unknown" => L10n.T("部分未知"), _ => L10n.T("不可用"),
        };
        var coverageText = coverage is { } cov ? $"{cov * 100:0}%" : "—";
        ProfileStatus.Text = (bearing is { } b ? $"方位 {b:0.0}° · " : "") + "卫星证据 / GFS 高度先验";

        if (cells.Count == 0)
        {
            activeCrossSection = null;
            activeCrossSectionIsNowcast = true;
            ProfileTakeawayBanner.Visibility = Visibility.Collapsed;
            ProfileControlsBar.Visibility = Visibility.Collapsed;
            PointCrossSectionLegendScroll.Visibility = Visibility.Collapsed;
            PointCrossSection.Visibility = Visibility.Collapsed;
            PointProfileNote.Text = Text(profile, "reason") is { Length: > 0 } r ? r : L10n.T("暂无可绘制的冻结卫星剖面");
            return;
        }

        // 一图看懂横幅
        var peakCell = cells.Where(c => c.Probability.HasValue).MaxBy(c => c.Probability!.Value);
        var hasSignal = (peakCell?.Probability ?? 0) >= 15.0;
        var bearingText = bearing is { } br ? $"{br:0.0}°" : "—";
        var prefix = L10n.IsEnglish
            ? $"Bearing {bearingText} · {statusLabel} ({coverageText})"
            : $"方位 {bearingText} · {statusLabel}（{coverageText}）";
        string takeaway;
        if (peakCell != null && hasSignal)
        {
            var midKm = (peakCell.BottomKm + peakCell.TopKm) * 0.5;
            takeaway = prefix + (L10n.IsEnglish
                ? $" · Peak cloud posterior {peakCell.Probability!.Value:0}% (~{midKm:0.0} km)"
                : $" · 峰值云后验 {peakCell.Probability!.Value:0}%（约 {midKm:0.0} km）");
        }
        else
        {
            takeaway = prefix + " · " + (L10n.IsEnglish ? "No significant cloud signal" : "暂无显著云信号");
        }
        var bannerColor = hasSignal ? ColorFrom(0x56, 0x8F, 0x9B) : ColorFrom(0xB7, 0x79, 0x1F);
        var isDark = Root?.ActualTheme == ElementTheme.Dark;
        ProfileTakeawayBanner.Visibility = Visibility.Visible;
        ProfileTakeawayBanner.Background = new SolidColorBrush(Windows.UI.Color.FromArgb(
            (byte)(isDark ? 36 : 23), bannerColor.R, bannerColor.G, bannerColor.B));
        ProfileTakeawayBanner.BorderBrush = new SolidColorBrush(Windows.UI.Color.FromArgb(
            82, bannerColor.R, bannerColor.G, bannerColor.B));
        ProfileTakeawayIcon.Glyph = hasSignal ? "\uE753" : "\uE946";
        ProfileTakeawayIcon.Foreground = new SolidColorBrush(bannerColor);
        ProfileTakeawayText.Text = takeaway;

        // 遥测胶囊 + GFS 先验等压线开关
        ProfileControlsBar.Visibility = Visibility.Visible;
        ProfileTelemetryPills.Children.Clear();
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("时次"), TimeLabel(Text(profile, "valid_time_utc")));
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("状态"), statusLabel);
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("采样点"), $"{usable}/{sampleCount}");
        AddTelemetryPill(ProfileTelemetryPills, L10n.T("覆盖率"), coverageText);
        if (unknownCount.HasValue)
            AddTelemetryPill(ProfileTelemetryPills, L10n.T("未知样本"), $"{unknownCount.Value}");

        crossSectionShowIsobars = nowcastShowIsobars;
        UpdateCrossSectionZoomControls();
        if (ProfileIsobarsToggle != null)
        {
            ProfileIsobarsToggle.Visibility = rows.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            ProfileIsobarsToggle.IsChecked = nowcastShowIsobars;
        }

        // 画布与状态记录（供 resize & pointer hover 使用）
        var profileForRender = profile.DeepClone() as JsonObject ?? profile;
        if (bearing.HasValue && profileForRender["bearing_deg"] == null)
            profileForRender["bearing_deg"] = bearing.Value;
        activeCrossSection = profileForRender;
        activeCrossSectionIsNowcast = true;
        PointCrossSection.Visibility = Visibility.Visible;
        ResizePointCrossSectionChart();
        DrawNowcastProfileHeatmap(profileForRender, crossSectionShowIsobars);

        // 图例
        RenderNowcastProfileLegend(bands.Contains("CLOUD_MASK"));
        PointProfileNote.Text = "";
    }

    private void RenderNowcastProfileLegend(bool hcai)
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

        var postGrad = new LinearGradientBrush
        {
            StartPoint = new Windows.Foundation.Point(0, 0.5),
            EndPoint = new Windows.Foundation.Point(1, 0.5),
        };
        postGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(40, 86, 143, 155), Offset = 0 });
        postGrad.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(215, 98, 170, 188), Offset = 1 });
        AddItem(new Border { Width = 44, Height = 7, CornerRadius = new CornerRadius(3), Background = postGrad },
            L10n.T("卫星后验概率 0–100%"));

        AddItem(new Border
        {
            Width = 12, Height = 8,
            Background = new SolidColorBrush(Windows.UI.Color.FromArgb(28, 183, 121, 31)),
            BorderBrush = new SolidColorBrush(ColorFrom(0xB7, 0x79, 0x1F)),
            BorderThickness = new Thickness(1),
        }, L10n.T("未知/缺失（非晴空）"));

        AddItem(new Line
        {
            X1 = 0, X2 = 14, Y1 = 4, Y2 = 4, Width = 14, Height = 8,
            StrokeThickness = 1.2,
            Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(153, 86, 143, 155)),
            StrokeDashArray = [4, 3],
        }, L10n.T("GFS 压力层先验（hPa）"));

        PointCrossSectionLegend.Children.Add(new TextBlock
        {
            Text = hcai
                ? L10n.T("HCAI 官方云掩膜/云相/云顶与 COT 产品 · 缺测不解释为晴空。")
                : L10n.T("HSD B07/B13/B15 云概率代理 · 连续高度层来自 GFS 先验（非观测真值），缺测不解释为晴空。"),
            FontSize = 9.5, Opacity = .65, VerticalAlignment = VerticalAlignment.Center,
        });
    }

    private void PopulateNowcastHoverCard(JsonObject profile, Windows.Foundation.Point pos,
        double left, double right, double top, double bottom)
    {
        var (cells, rows, _, _) = ParseNowcastProfile(profile, SelectedReport());
        if (cells.Count == 0) return;
        var maxHeightKm = Math.Max(16.0, Math.Ceiling(Math.Max(
            cells.Max(c => c.TopKm),
            rows.Count > 0 ? rows.Max(r => r.HeightKm) : 15.0)));
        var maxDistanceKm = Math.Max(1.0, cells.Max(c => c.EndKm));
        var xRatio = Math.Clamp((pos.X - left) / (right - left), 0.0, 1.0);
        var yRatio = Math.Clamp(1.0 - (pos.Y - top) / (bottom - top), 0.0, 1.0);
        var distKm = xRatio * maxDistanceKm;
        var heightKm = yRatio * maxHeightKm;

        var exact = cells.FirstOrDefault(c =>
            distKm >= c.StartKm && distKm <= c.EndKm && heightKm >= c.BottomKm && heightKm <= c.TopKm);
        var sameCol = exact == null
            ? cells.Where(c => distKm >= c.StartKm && distKm <= c.EndKm).ToList()
            : [];
        var colUnknown = sameCol.Count > 0 && sameCol.All(c => c.Probability == null);

        string statusText;
        Windows.UI.Color statusColor;
        if (exact != null)
        {
            if (exact.Probability is { } prob)
            {
                statusText = L10n.IsEnglish ? $"Posterior {prob:0}%" : $"后验概率 {prob:0}%";
                statusColor = ColorFrom(0x56, 0x8F, 0x9B);
            }
            else
            {
                statusText = L10n.T("未知/缺测");
                statusColor = ColorFrom(0xB7, 0x79, 0x1F);
            }
        }
        else if (colUnknown)
        {
            statusText = L10n.T("未知/缺测");
            statusColor = ColorFrom(0xB7, 0x79, 0x1F);
        }
        else
        {
            statusText = L10n.IsEnglish ? "No sample level" : "无采样层";
            statusColor = ColorFrom(0x88, 0x94, 0xA6);
        }

        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        row.Children.Add(new Ellipse { Width = 6, Height = 6, Fill = new SolidColorBrush(statusColor), VerticalAlignment = VerticalAlignment.Center });
        row.Children.Add(new TextBlock
        {
            Text = L10n.IsEnglish
                ? $"{distKm:0} km · {heightKm:0.0} km · {statusText}"
                : $"距离 {distKm:0} km · 高度 {heightKm:0.0} km · {statusText}",
            FontSize = 10.5,
            FontWeight = Microsoft.UI.Text.FontWeights.Medium,
        });
        CrossSectionHoverHost.Children.Add(row);
    }

    /// <summary>v3 卫星后验概率剖面：暮光天空渐变 + 平滑水平概率带 + 琥珀斜纹未知幕墙 + GFS 先验等压线。</summary>
    private void DrawNowcastProfileHeatmap(JsonObject profile, bool showIsobars = true)
    {
        PointCrossSection.Visibility = Visibility.Visible;
        PointCrossSection.Children.Clear();
        var (cells, rows, _, bearing) = ParseNowcastProfile(profile, SelectedReport());
        if (cells.Count == 0) return;

        var isDark = PointCrossSection.ActualTheme == ElementTheme.Dark;
        var width = Math.Max(220, PointCrossSection.Width is > 120 and var w
            ? w : PointCrossSection.ActualWidth is > 120 and var actualWidth ? actualWidth : 680);
        var height = Math.Max(180, PointCrossSection.Height is > 120 and var h
            ? h : PointCrossSection.ActualHeight is > 120 and var actualHeight ? actualHeight : 340);
        var showRightAxis = showIsobars && rows.Count > 0;
        double left = 44;
        double right = width - (showRightAxis ? 48.0 : 16.0);
        double top = 14;
        double bottom = height - 24;
        if (right <= left + 24 || bottom <= top + 24) return;

        var plotW = right - left;
        var plotH = bottom - top;
        var maxHeightKm = Math.Max(16.0, Math.Ceiling(Math.Max(
            cells.Max(c => c.TopKm),
            rows.Count > 0 ? rows.Max(r => r.HeightKm) : 15.0)));
        var maxDistanceKm = Math.Max(1.0, cells.Max(c => c.EndKm));
        double X(double km) => left + Math.Clamp(km, 0, maxDistanceKm) / maxDistanceKm * plotW;
        double Y(double km) => top + (1.0 - Math.Clamp(km, 0, maxHeightKm) / maxHeightKm) * plotH;

        var plotLayer = new Canvas
        {
            Width = width,
            Height = height,
            Clip = new RectangleGeometry { Rect = new Windows.Foundation.Rect(left, top, plotW, plotH) },
        };
        PointCrossSection.Children.Add(plotLayer);

        // 暮光天空渐变背景
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
            StartPoint = new Windows.Foundation.Point(0.40, 0.25),
            EndPoint = new Windows.Foundation.Point(1.0, 1.0),
        };
        horizonWash.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb(0, 250, 160, 87), Offset = 0.0 });
        horizonWash.GradientStops.Add(new GradientStop { Color = Windows.UI.Color.FromArgb((byte)(isDark ? 41 : 46), 250, 160, 87), Offset = 1.0 });
        var washRect = new Rectangle { Width = plotW, Height = plotH, Fill = horizonWash };
        Canvas.SetLeft(washRect, left);
        Canvas.SetTop(washRect, top);
        plotLayer.Children.Add(washRect);

        // 网格线
        var heightStep = maxHeightKm <= 16 ? 4.0 : 5.0;
        for (var km = 0.0; km <= maxHeightKm + 0.01; km += heightStep)
        {
            plotLayer.Children.Add(new Line
            {
                X1 = left, X2 = right, Y1 = Y(km), Y2 = Y(km),
                StrokeThickness = 0.6,
                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(36, 140, 150, 165)),
            });
        }
        var distanceStep = maxDistanceKm <= 120 ? 20.0 : maxDistanceKm <= 300 ? 50.0 : 100.0;
        for (var km = distanceStep; km <= maxDistanceKm + 0.01; km += distanceStep)
        {
            var lx = X(km);
            plotLayer.Children.Add(new Line
            {
                X1 = lx, X2 = lx, Y1 = top, Y2 = bottom,
                StrokeThickness = 0.6,
                StrokeDashArray = [3, 4],
                Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(28, 140, 150, 165)),
            });
        }

        // 归组列
        var columns = cells
            .GroupBy(c => $"{(int)Math.Round(c.StartKm * 100)}-{(int)Math.Round(c.EndKm * 100)}")
            .Select(g => new NowcastColumnSlice(
                g.First().StartKm, g.First().EndKm,
                g.OrderBy(c => c.BottomKm).ToList()))
            .OrderBy(col => col.StartKm)
            .ToList();

        Windows.UI.Color BandColor(double prob)
        {
            var clamped = Math.Clamp(prob, 0, 100);
            if (clamped < 15)
            {
                var a = (byte)Math.Round((0.03 + clamped / 15.0 * 0.11) * 255);
                return Windows.UI.Color.FromArgb(a, 86, 143, 155);
            }
            if (clamped <= 50)
            {
                var t = (clamped - 15) / 35.0;
                var a = (byte)Math.Round(((isDark ? 0.22 : 0.18) + t * 0.32) * 255);
                return Windows.UI.Color.FromArgb(a, 86, 143, 155);
            }
            {
                var t = (clamped - 50) / 50.0;
                var a = (byte)Math.Round(((isDark ? 0.56 : 0.52) + t * 0.30) * 255);
                return Windows.UI.Color.FromArgb(a, 98, 170, 188);
            }
        }

        var colIdx = 0;
        while (colIdx < columns.Count)
        {
            if (columns[colIdx].IsFullyUnknown)
            {
                var endIdx = colIdx;
                while (endIdx + 1 < columns.Count && columns[endIdx + 1].IsFullyUnknown) endIdx++;
                var veilLeft = Math.Max(left, X(columns[colIdx].StartKm));
                var veilRight = Math.Min(right, X(columns[endIdx].EndKm));
                var veilW = Math.Max(1, veilRight - veilLeft);
                var veilH = Math.Max(1, plotH - 2);

                var veilCanvas = new Canvas
                {
                    Width = veilW, Height = veilH,
                    Clip = new RectangleGeometry { Rect = new Windows.Foundation.Rect(0, 0, veilW, veilH) },
                };
                Canvas.SetLeft(veilCanvas, veilLeft);
                Canvas.SetTop(veilCanvas, top + 1);
                veilCanvas.Children.Add(new Rectangle
                {
                    Width = veilW, Height = veilH, RadiusX = 4, RadiusY = 4,
                    Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(28, 183, 121, 31)),
                });
                var span = veilW + veilH;
                for (var off = -veilH; off <= span; off += 10.0)
                {
                    veilCanvas.Children.Add(new Line
                    {
                        X1 = off, Y1 = veilH, X2 = off + veilH * 0.45, Y2 = 0,
                        Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(40, 183, 121, 31)),
                        StrokeThickness = 0.8,
                    });
                }
                veilCanvas.Children.Add(new Rectangle
                {
                    Width = veilW, Height = veilH, RadiusX = 4, RadiusY = 4,
                    Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(190, 183, 121, 31)),
                    StrokeThickness = 1.0,
                    StrokeDashArray = [4, 3],
                });
                plotLayer.Children.Add(veilCanvas);

                if (veilW >= 42)
                {
                    var badge = new Border
                    {
                        Padding = new Thickness(6, 2, 6, 2),
                        CornerRadius = new CornerRadius(9),
                        Background = new SolidColorBrush(isDark
                            ? Windows.UI.Color.FromArgb(170, 0, 0, 0)
                            : Windows.UI.Color.FromArgb(180, 255, 255, 255)),
                        BorderBrush = new SolidColorBrush(Windows.UI.Color.FromArgb(145, 183, 121, 31)),
                        BorderThickness = new Thickness(0.8),
                        Child = new TextBlock
                        {
                            Text = L10n.T("未知/缺测"),
                            FontSize = 9,
                            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                            Foreground = new SolidColorBrush(ColorFrom(0xB7, 0x79, 0x1F)),
                        },
                    };
                    Canvas.SetLeft(badge, veilLeft + (veilW - 58) * 0.5);
                    Canvas.SetTop(badge, top + plotH * 0.5 - 10);
                    plotLayer.Children.Add(badge);
                }

                colIdx = endIdx + 1;
                continue;
            }

            var runEnd = colIdx;
            while (runEnd + 1 < columns.Count && !columns[runEnd + 1].IsFullyUnknown) runEnd++;
            var runCols = columns.GetRange(colIdx, runEnd - colIdx + 1);
            var maxLayers = runCols.Max(c => c.Cells.Count);

            for (var layerIdx = 0; layerIdx < maxLayers; layerIdx++)
            {
                var segStart = 0;
                while (segStart < runCols.Count)
                {
                    if (layerIdx >= runCols[segStart].Cells.Count || runCols[segStart].Cells[layerIdx].Probability == null)
                    {
                        segStart++;
                        continue;
                    }
                    var segEnd = segStart;
                    while (segEnd + 1 < runCols.Count
                           && layerIdx < runCols[segEnd + 1].Cells.Count
                           && runCols[segEnd + 1].Cells[layerIdx].Probability != null)
                        segEnd++;

                    var bandCells = Enumerable.Range(segStart, segEnd - segStart + 1)
                        .Select(idx => runCols[idx].Cells[layerIdx]).ToList();
                    var bandLeft = Math.Max(left, X(bandCells[0].StartKm));
                    var bandRight = Math.Min(right, X(bandCells[^1].EndKm));
                    var bandWidth = Math.Max(1.0, bandRight - bandLeft);

                    var topPts = new List<Windows.Foundation.Point>();
                    var botPts = new List<Windows.Foundation.Point>();
                    for (var idx = 0; idx < bandCells.Count; idx++)
                    {
                        var c = bandCells[idx];
                        var cLeft = Math.Max(left, X(c.StartKm));
                        var cRight = Math.Min(right, X(c.EndKm));
                        var cMid = (cLeft + cRight) * 0.5;
                        var tY = Math.Max(top + 1, Y(c.TopKm));
                        var bY = Math.Min(bottom - 1, Math.Max(tY + 2, Y(c.BottomKm)));
                        if (idx == 0) { topPts.Add(new(cLeft, tY)); botPts.Add(new(cLeft, bY)); }
                        topPts.Add(new(cMid, tY));
                        botPts.Add(new(cMid, bY));
                        if (idx == bandCells.Count - 1) { topPts.Add(new(cRight, tY)); botPts.Add(new(cRight, bY)); }
                    }

                    Brush fillBrush;
                    if (bandCells.Count == 1)
                    {
                        fillBrush = new SolidColorBrush(BandColor(bandCells[0].Probability ?? 0));
                    }
                    else
                    {
                        var grad = new LinearGradientBrush
                        {
                            StartPoint = new Windows.Foundation.Point(0, 0.5),
                            EndPoint = new Windows.Foundation.Point(1, 0.5),
                        };
                        foreach (var c in bandCells)
                        {
                            var cMid = (Math.Max(left, X(c.StartKm)) + Math.Min(right, X(c.EndKm))) * 0.5;
                            var loc = Math.Clamp((cMid - bandLeft) / bandWidth, 0.0, 1.0);
                            grad.GradientStops.Add(new GradientStop { Color = BandColor(c.Probability ?? 0), Offset = loc });
                        }
                        fillBrush = grad;
                    }

                    var revBot = botPts.AsEnumerable().Reverse().ToList();
                    plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                    {
                        Data = SmoothClosedBandGeometry(topPts, revBot),
                        Fill = fillBrush,
                    });

                    // 强云核 (>=45%) 柔和亮线勾勒
                    var coreStart = 0;
                    while (coreStart < bandCells.Count)
                    {
                        if ((bandCells[coreStart].Probability ?? 0) < 45) { coreStart++; continue; }
                        var coreEnd = coreStart;
                        while (coreEnd + 1 < bandCells.Count && (bandCells[coreEnd + 1].Probability ?? 0) >= 45) coreEnd++;
                        var coreTop = new List<Windows.Foundation.Point>();
                        var coreBot = new List<Windows.Foundation.Point>();
                        for (var ci = coreStart; ci <= coreEnd; ci++)
                        {
                            var c = bandCells[ci];
                            var cLeft = Math.Max(left, X(c.StartKm));
                            var cRight = Math.Min(right, X(c.EndKm));
                            var cMid = (cLeft + cRight) * 0.5;
                            var tY = Math.Max(top + 1, Y(c.TopKm));
                            var bY = Math.Min(bottom - 1, Math.Max(tY + 2, Y(c.BottomKm)));
                            if (ci == coreStart) { coreTop.Add(new(cLeft, tY)); coreBot.Add(new(cLeft, bY)); }
                            coreTop.Add(new(cMid, tY));
                            coreBot.Add(new(cMid, bY));
                            if (ci == coreEnd) { coreTop.Add(new(cRight, tY)); coreBot.Add(new(cRight, bY)); }
                        }
                        var revCoreBot = coreBot.AsEnumerable().Reverse().ToList();
                        plotLayer.Children.Add(new Microsoft.UI.Xaml.Shapes.Path
                        {
                            Data = SmoothClosedBandGeometry(coreTop, revCoreBot),
                            Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(158, 98, 170, 188)),
                            StrokeThickness = 1.1,
                        });
                        coreStart = coreEnd + 1;
                    }

                    segStart = segEnd + 1;
                }
            }

            // 局部未知层单元格
            foreach (var col in runCols)
            {
                foreach (var c in col.Cells.Where(cell => cell.Probability == null))
                {
                    var cLeft = Math.Max(left, X(c.StartKm));
                    var cRight = Math.Min(right, X(c.EndKm));
                    var cTop = Math.Max(top + 1, Y(c.TopKm));
                    var cBottom = Math.Min(bottom - 1, Y(c.BottomKm));
                    if (cRight <= cLeft || cBottom <= cTop) continue;
                    var cellRect = new Rectangle
                    {
                        Width = cRight - cLeft, Height = cBottom - cTop,
                        RadiusX = 3, RadiusY = 3,
                        Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(28, 183, 121, 31)),
                        Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(204, 183, 121, 31)),
                        StrokeThickness = 0.9,
                        StrokeDashArray = [3, 2],
                    };
                    Canvas.SetLeft(cellRect, cLeft);
                    Canvas.SetTop(cellRect, cTop);
                    plotLayer.Children.Add(cellRect);
                }
            }

            colIdx = runEnd + 1;
        }

        // GFS 先验等压线（可开关）
        if (showRightAxis)
        {
            foreach (var row in rows.OrderByDescending(r => r.HeightKm))
            {
                if (row.HeightKm < 0 || row.HeightKm > maxHeightKm) continue;
                var lineY = Y(row.HeightKm);
                plotLayer.Children.Add(new Line
                {
                    X1 = left, X2 = right, Y1 = lineY, Y2 = lineY,
                    Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb(72, 86, 143, 155)),
                    StrokeThickness = 0.7,
                    StrokeDashArray = [4, 3],
                });
            }
        }

        // 观测点信标与右上太阳方向
        var obsX = X(0);
        var obsY = bottom - 5;
        var halo = new Ellipse { Width = 14, Height = 14, Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(66, 227, 102, 74)) };
        Canvas.SetLeft(halo, obsX - 7);
        Canvas.SetTop(halo, obsY - 7);
        plotLayer.Children.Add(halo);
        var dot = new Ellipse
        {
            Width = 8, Height = 8,
            Fill = ModelBrush("gfs"),
            Stroke = new SolidColorBrush(Colors.White),
            StrokeThickness = 1.2,
        };
        Canvas.SetLeft(dot, obsX - 4);
        Canvas.SetTop(dot, obsY - 4);
        plotLayer.Children.Add(dot);
        var obsLbl = new TextBlock
        {
            Text = L10n.T("观测点"),
            FontSize = 10,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        };
        Canvas.SetLeft(obsLbl, obsX + 9);
        Canvas.SetTop(obsLbl, obsY - 16);
        plotLayer.Children.Add(obsLbl);

        var sunText = bearing is { } bDeg
            ? (L10n.IsEnglish ? $"☀ Sun {bDeg:0}°" : $"☀ 太阳方向 {bDeg:0}°")
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

        // 外框
        var borderRect = new Rectangle
        {
            Width = plotW, Height = plotH,
            RadiusX = 10, RadiusY = 10,
            Stroke = new SolidColorBrush(Windows.UI.Color.FromArgb((byte)(isDark ? 72 : 56), 140, 150, 165)),
            StrokeThickness = 1.0,
        };
        Canvas.SetLeft(borderRect, left);
        Canvas.SetTop(borderRect, top);
        PointCrossSection.Children.Add(borderRect);

        // 左侧高度坐标
        for (var km = 0.0; km <= maxHeightKm + 0.01; km += heightStep)
        {
            var lbl = new TextBlock
            {
                Text = $"{km:0}km", FontSize = 9.5, Opacity = .68,
                Width = left - 8, TextAlignment = TextAlignment.Right,
            };
            Canvas.SetLeft(lbl, 2);
            Canvas.SetTop(lbl, Y(km) - 7);
            PointCrossSection.Children.Add(lbl);
        }

        // 底部距离坐标
        for (var km = distanceStep; km <= maxDistanceKm + 0.01; km += distanceStep)
        {
            var lbl = new TextBlock { Text = $"{km:0}km", FontSize = 9.5, Opacity = .68 };
            Canvas.SetLeft(lbl, Math.Min(right - 28, X(km) - 12));
            Canvas.SetTop(lbl, bottom + 4);
            PointCrossSection.Children.Add(lbl);
        }

        // 右侧 GFS 先验等压线标签
        if (showRightAxis)
        {
            var lastY = double.NegativeInfinity;
            foreach (var row in rows.OrderByDescending(r => r.HeightKm))
            {
                if (row.HeightKm < 0 || row.HeightKm > maxHeightKm) continue;
                var ly = Y(row.HeightKm);
                if (Math.Abs(ly - lastY) < 12) continue;
                lastY = ly;
                var lbl = new TextBlock
                {
                    Text = $"{row.Pressure}hPa", FontSize = 8.5,
                    Foreground = new SolidColorBrush(ColorFrom(0x56, 0x8F, 0x9B)),
                };
                Canvas.SetLeft(lbl, right + 5);
                Canvas.SetTop(lbl, ly - 6);
                PointCrossSection.Children.Add(lbl);
            }
        }
    }

    private async void OnQueryNowcastProfile(object sender, RoutedEventArgs args)
    {
        var validTime = TagOf(NowcastProfileStepPicker?.SelectedItem);
        if (string.IsNullOrEmpty(validTime))
            validTime = Text(SelectedReport(), "best_time_local");
        await QueryNowcastProfile(validTime);
    }

    private async Task QueryNowcastProfile(string validTimeLocal)
    {
        if (resultKind != "nowcast" || SelectedReport() is not { } report)
        {
            frozenProfileState = "unavailable";
            frozenProfileError = L10n.T("该结果没有可查询的冻结卫星剖面。");
            RenderFrozenProfile();
            return;
        }
        await RunFrozenProfileQuery("satellite", Latitude.Value, Longitude.Value,
            validTimeLocal, report);
    }
}
