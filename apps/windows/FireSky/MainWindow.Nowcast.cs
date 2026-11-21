using Microsoft.UI;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;

namespace FireSky;

/// <summary>
/// 实况专用展示（对齐 macOS WorkNowcast*）：卫星帧信息、指标卡分组、
/// 质量趋势、卫星后验概率剖面热图（压力层 + unknown 琥珀）、冻结卫星剖面查询、
/// 不可用原因透传。桌面 bands 恒为 HSD B07/B13/B15，不冒充 HCAI 云类真值。
/// </summary>
public sealed partial class MainWindow
{
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

    private void RenderNowcastResult()
    {
        // Hero 区：单卡显示卫星帧与质量摘要（隐藏 GFS/EC 双卡布局）。
        HeroHost.Visibility = Visibility.Visible;
        MetricHost.Visibility = Visibility.Visible;
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
        card.Level.Text = Text(report, "quality_level") is { Length: > 0 } level ? level : L10n.T("峰值质量指数");
        card.Best.Text = TimeLabel(Text(report, "best_time_local"));
        card.Window.Text =
            $"推荐窗口 {TimeLabel(Text(report?["timing_window"], "start_time_local"))} – {TimeLabel(Text(report?["timing_window"], "end_time_local"))}";
        var source = Text(observation, "source");
        var bands = Text(observation, "bands");
        var frame = Text(observation, "frame_time_utc");
        card.Chips.Text = string.Join("   ",
            new[]
            {
                frame.Length >= 16 ? $"卫星帧 {frame[0..16].Replace("T", " ")}" : "",
                bands is { Length: > 0 } ? bands : "",
                source is { Length: > 0 } ? $"来源 {source}" : "",
            }.Where(part => part.Length > 0));
        ToolTipService.SetToolTip(card.Chips, L10n.T("卫星帧时次与通道来自真实观测载荷；缺项如实显示。"));
        card.Source.Text = L10n.T("HSD B07/B13/B15 云概率代理（posterior）；HSD 分支不发布 COT、云相或官方云高真值。");
        card.Error.Text = "";
        card.Error.Visibility = Visibility.Collapsed;
        // EC 卡隐藏（实况为单来源）。
        heroCards["ec"].Panel.Visibility = Visibility.Collapsed;
    }

    private void RenderNowcastMetrics()
    {
        var report = SelectedReport();
        var aod = AodReading([report]);
        // 复用指标卡布局：按实况指标重建。
        MetricHost.Children.Clear();
        var groups = new[] { (0, 5), (5, 10), (10, 15) };
        var titles = new[] { L10n.T("卫星云况"), L10n.T("运动采样与光学"), L10n.T("雷达与可靠性") };
        for (var group = 0; group < groups.Length; group++)
        {
            var (start, end) = groups[group];
            var section = new StackPanel { Spacing = 7 };
            section.Children.Add(new TextBlock
            {
                Text = titles[group], FontSize = 10, Opacity = .65,
                FontWeight = Microsoft.UI.Text.FontWeights.Medium,
            });
            var row = new Grid { ColumnSpacing = 10 };
            for (var index = start; index < end; index++)
            {
                var spec = NowcastMetrics[index];
                var card = new StackPanel { Spacing = 6, Padding = new Thickness(12) };
                card.Children.Add(new TextBlock
                {
                    Text = spec.Title, FontSize = 12, Opacity = .85,
                });
                var value = new TextBlock
                {
                    Text = NowcastValue(spec.Key, report ?? []),
                    FontSize = 18, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    Opacity = 1,
                };
                card.Children.Add(value);
                card.Children.Add(new TextBlock
                {
                    Text = spec.Caption, FontSize = 9, Opacity = .6, TextWrapping = TextWrapping.Wrap,
                });
                var border = new Border
                {
                    Child = card, Padding = new Thickness(2),
                    CornerRadius = new CornerRadius(8),
                    Background = (Brush)Application.Current.Resources["LayerFillColorDefaultBrush"],
                    BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
                    BorderThickness = new Thickness(1),
                };
                Grid.SetColumn(border, index - start);
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                row.Children.Add(border);
            }
            section.Children.Add(row);
            MetricHost.Children.Add(section);
        }
        if (report == null)
            MetricHost.Children.Add(new TextBlock
            {
                Text = L10n.T("暂无实况结果"), Opacity = .65,
            });
    }

    private void RenderNowcastProfileTab()
    {
        var report = SelectedReport();
        var observation = report?["nowcast"] as JsonObject ?? new JsonObject();
        ProfileStatus.Text = L10n.T("周期 ") + Text(report, "cycle_time_utc");
        var profile = observation["satellite_vertical_profile"] as JsonObject;
        if (profile == null)
        {
            PointProfileNote.Text = L10n.T("该实况结果没有卫星后验概率剖面。");
            PointProfileNote.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
            PointCrossSection.Visibility = Visibility.Collapsed;
            FrozenProfileButton.Visibility = Visibility.Collapsed;
            return;
        }
        var status = Text(profile, "status");
        var samples = profile["samples"] as JsonArray ?? [];
        var sampleCount = (long?)DoubleValue(profile["sample_count"]) ?? samples.Count;
        var usable = (long?)DoubleValue(profile["usable_sample_count"]) ?? sampleCount;
        var coverage = DoubleValue(profile["coverage"]);
        var statusLabel = status switch
        {
            "available" => L10n.T("完整覆盖"), "partial_unknown" => L10n.T("部分未知"), _ => L10n.T("不可用"),
        };
        PointProfileNote.Text = $"后验概率剖面 {statusLabel} · 有效采样 {usable}/{sampleCount}"
            + (coverage is { } value ? $" · 覆盖 {value * 100:0}%" : "")
            + (Text(profile, "valid_time_utc") is { Length: >= 16 } time
                ? $" · {time[0..16].Replace("T", " ")}" : "");
        PointProfileNote.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        DrawNowcastProfileHeatmap(profile);
        // 冻结卫星剖面查询（时次选择走峰值/诊断时刻）。
        FrozenProfileButton.Visibility =
            BoolValue(report?["profile_source"], "available") == true
            ? Visibility.Visible : Visibility.Collapsed;
    }

    /// <summary>卫星后验概率剖面热图：距离×高度色块；unknown 层琥珀虚线（不伪造概率）。</summary>
    private void DrawNowcastProfileHeatmap(JsonObject profile)
    {
        PointCrossSection.Visibility = Visibility.Visible;
        PointCrossSection.Children.Clear();
        var samples = (profile["samples"] as JsonArray ?? [])
            .OfType<JsonObject>()
            .Where(sample => double.IsFinite(Number(sample, "distance_km")))
            .OrderBy(sample => Number(sample, "distance_km"))
            .ToList();
        if (samples.Count == 0) return;
        var width = Math.Max(120, PointCrossSection.ActualWidth is > 120 and var w ? w : 640);
        var height = Math.Max(120, PointCrossSection.ActualHeight);
        double left = 40, right = width - 12, top = 10, bottom = height - 24;
        var maxDistance = Math.Max(1, Number(samples[^1], "distance_km"));
        var maxAltitude = 18_000.0;
        double X(double distance) => left + distance / maxDistance * (right - left);
        double Y(double altitude) => top + (1 - Math.Clamp(altitude, 0, maxAltitude) / maxAltitude) * (bottom - top);
        for (var altitude = 0; altitude <= maxAltitude; altitude += 3000)
        {
            PointCrossSection.Children.Add(new Line
            {
                X1 = left, X2 = right, Y1 = Y(altitude), Y2 = Y(altitude),
                StrokeThickness = .6, Opacity = .22, Stroke = new SolidColorBrush(Colors.Gray),
            });
            PointCrossSection.Children.Add(new TextBlock
            {
                Text = $"{altitude / 1000:0}km", FontSize = 8, Opacity = .6,
                RenderTransform = new TranslateTransform { X = 2, Y = Y(altitude) - 6 },
            });
        }
        for (var index = 0; index < samples.Count; index++)
        {
            var sample = samples[index];
            var distance = Number(sample, "distance_km");
            var before = index > 0 ? Number(samples[index - 1], "distance_km") : distance - 10;
            var after = index + 1 < samples.Count ? Number(samples[index + 1], "distance_km") : distance + 10;
            var startX = X(Math.Max(0, (before + distance) / 2));
            var endX = X((distance + after) / 2);
            var cellWidth = Math.Max(4, endX - startX - 1);
            var levels = (sample["levels"] as JsonArray ?? [])
                .OfType<JsonObject>()
                .Where(level => (DoubleValue(level["height_m"]) ?? -1) >= 0)
                .OrderBy(level => DoubleValue(level["height_m"]) ?? 0)
                .ToList();
            for (var layer = 0; layer < levels.Count; layer++)
            {
                var level = levels[layer];
                var altitude = DoubleValue(level["height_m"]) ?? 0;
                var lower = layer > 0 ? DoubleValue(levels[layer - 1]["height_m"]) ?? 0 : 0;
                var upper = layer + 1 < levels.Count
                    ? DoubleValue(levels[layer + 1]["height_m"]) ?? altitude + 1000 : altitude + 1000;
                var topY = Y((altitude + upper) / 2);
                var cellHeight = Math.Max(4, Y((altitude + lower) / 2) - topY);
                var unknown = Text(level, "status") == "unknown";
                var probability = DoubleValue(level["hsd_posterior_percent"]
                    ?? level["cloud_probability_percent"]);
                var retrieved = Text(sample, "status") == "retrieved" && !unknown;
                Rectangle cell;
                if (retrieved && probability is { } value)
                {
                    // 后验概率 0–100% 色带（暖色强度）。
                    var t = Math.Clamp(value / 100.0, 0, 1);
                    cell = new Rectangle
                    {
                        Width = cellWidth, Height = cellHeight,
                        Fill = new SolidColorBrush(ColorFrom(
                            (byte)(90 + 130 * t), (byte)(70 + 40 * t), (byte)(120 - 40 * t)))
                        {
                            Opacity = 0.25 + 0.6 * t,
                        },
                        RenderTransform = new TranslateTransform { X = startX, Y = topY },
                    };
                }
                else
                {
                    // unknown 层琥珀虚线框：显式标注缺测，不冒充数据。
                    cell = new Rectangle
                    {
                        Width = cellWidth, Height = cellHeight,
                        Stroke = new SolidColorBrush(ColorFrom(0xD4, 0x8C, 0x38)),
                        StrokeThickness = 1.1,
                        StrokeDashArray = [3, 3],
                        Fill = null,
                        RenderTransform = new TranslateTransform { X = startX, Y = topY },
                    };
                }
                PointCrossSection.Children.Add(cell);
            }
        }
        PointCrossSection.Children.Add(new TextBlock
        {
            Text = L10n.T("色块：HSD 后验云概率（0–100%，暖色强）· 琥珀虚线：unknown 采样 · 高度轴海拔口径"),
            FontSize = 9, Opacity = .65,
            RenderTransform = new TranslateTransform { X = left, Y = bottom + 4 },
        });
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
