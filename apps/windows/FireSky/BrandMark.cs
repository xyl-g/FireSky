using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Foundation;
using Windows.UI;
using Path = Microsoft.UI.Xaml.Shapes.Path;

namespace FireSky;

/// <summary>
/// FireSky 品牌标（对齐 macOS BrandMark.swift）：
/// 深暮蓝到酒红渐变圆角方块 + 发光多层朝晚霞云朵剪影。
/// </summary>
public sealed class FireSkyMark : UserControl
{
    public FireSkyMark()
    {
        Content = BuildCanvas();
    }

    private static UIElement BuildCanvas()
    {
        var canvas = new Canvas { Width = 64, Height = 64 };

        // 1. Deep twilight navy-to-burgundy squircle tile (#0F294D -> #242647 -> #5E1F36)
        var tile = new Border
        {
            Width = 62,
            Height = 62,
            CornerRadius = new CornerRadius(14.5),
            Background = new LinearGradientBrush
            {
                StartPoint = new Point(0.5, 0),
                EndPoint = new Point(0.5, 1),
                GradientStops =
                {
                    new GradientStop { Color = Color.FromArgb(255, 0x0F, 0x29, 0x4D), Offset = 0.0 },
                    new GradientStop { Color = Color.FromArgb(255, 0x24, 0x26, 0x47), Offset = 0.5 },
                    new GradientStop { Color = Color.FromArgb(255, 0x5E, 0x1F, 0x36), Offset = 1.0 },
                },
            },
            BorderThickness = new Thickness(0.8),
            BorderBrush = new LinearGradientBrush
            {
                StartPoint = new Point(0.5, 0),
                EndPoint = new Point(0.5, 1),
                GradientStops =
                {
                    new GradientStop { Color = Color.FromArgb(0x29, 255, 255, 255), Offset = 0.0 },
                    new GradientStop { Color = Color.FromArgb(0x08, 255, 255, 255), Offset = 1.0 },
                },
            },
            Child = new Ellipse
            {
                Width = 58,
                Height = 58,
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 2, 0, 0),
                Fill = new RadialGradientBrush
                {
                    Center = new Point(0.5, 0.52),
                    GradientOrigin = new Point(0.5, 0.52),
                    RadiusX = 0.5,
                    RadiusY = 0.5,
                    GradientStops =
                    {
                        new GradientStop { Color = Color.FromArgb(0x47, 0xFF, 0x73, 0x29), Offset = 0.0 },
                        new GradientStop { Color = Color.FromArgb(0x14, 0xE6, 0x38, 0x3D), Offset = 0.55 },
                        new GradientStop { Color = Color.FromArgb(0x00, 0xE6, 0x38, 0x3D), Offset = 1.0 },
                    },
                },
            },
        };
        Canvas.SetLeft(tile, 1);
        Canvas.SetTop(tile, 1);
        canvas.Children.Add(tile);

        // 2. Sculpted outer sunset cloud silhouette
        var mainCloud = new Path
        {
            Data = BuildMainCloud(),
            Fill = new LinearGradientBrush
            {
                StartPoint = new Point(0.5, 0),
                EndPoint = new Point(0.4, 1),
                GradientStops =
                {
                    new GradientStop { Color = Color.FromArgb(255, 0xFF, 0xC7, 0x29), Offset = 0.0 },
                    new GradientStop { Color = Color.FromArgb(255, 0xFC, 0x7D, 0x21), Offset = 0.48 },
                    new GradientStop { Color = Color.FromArgb(255, 0xD9, 0x29, 0x36), Offset = 1.0 },
                },
            },
        };
        canvas.Children.Add(mainCloud);

        // 3. Inner bright golden-orange upper body
        var innerGlow = new Path
        {
            Data = BuildInnerGlow(),
            Fill = new RadialGradientBrush
            {
                Center = new Point(0.6, 0.3),
                GradientOrigin = new Point(0.6, 0.3),
                RadiusX = 0.72,
                RadiusY = 0.8,
                GradientStops =
                {
                    new GradientStop { Color = Color.FromArgb(255, 0xFF, 0xD9, 0x3D), Offset = 0.0 },
                    new GradientStop { Color = Color.FromArgb(255, 0xFF, 0x8F, 0x24), Offset = 0.55 },
                    new GradientStop { Color = Color.FromArgb(255, 0xE6, 0x40, 0x29), Offset = 1.0 },
                },
            },
        };
        canvas.Children.Add(innerGlow);

        // 4. Lower-left sculpted deep-crimson cloud billow shadow
        var leftBillow = new Path
        {
            Data = BuildLeftBillow(),
            Fill = new LinearGradientBrush
            {
                StartPoint = new Point(0.35, 0),
                EndPoint = new Point(0.25, 1),
                GradientStops =
                {
                    new GradientStop { Color = Color.FromArgb(255, 0xEB, 0x38, 0x30), Offset = 0.0 },
                    new GradientStop { Color = Color.FromArgb(255, 0xA3, 0x17, 0x38), Offset = 1.0 },
                },
            },
        };
        canvas.Children.Add(leftBillow);

        // 5. Right curling golden flame wisp sweeping to the bottom tail
        var rightWisp = new Path
        {
            Data = BuildRightWisp(),
            Fill = new LinearGradientBrush
            {
                StartPoint = new Point(0.92, 0.08),
                EndPoint = new Point(0.16, 0.94),
                GradientStops =
                {
                    new GradientStop { Color = Color.FromArgb(255, 0xFF, 0xE3, 0x57), Offset = 0.0 },
                    new GradientStop { Color = Color.FromArgb(255, 0xFC, 0x8A, 0x24), Offset = 0.45 },
                    new GradientStop { Color = Color.FromArgb(255, 0xD6, 0x2E, 0x2B), Offset = 1.0 },
                },
            },
        };
        canvas.Children.Add(rightWisp);

        // 6. Crisp soft crest highlight on the right wisp tip
        var tipGlow = new Path
        {
            Data = BuildTipGlow(),
            Fill = new RadialGradientBrush
            {
                Center = new Point(0.75, 0.3),
                GradientOrigin = new Point(0.75, 0.3),
                RadiusX = 0.85,
                RadiusY = 0.85,
                GradientStops =
                {
                    new GradientStop { Color = Color.FromArgb(0xE0, 0xFF, 0xF0, 0x85), Offset = 0.0 },
                    new GradientStop { Color = Color.FromArgb(0x00, 0xFF, 0xAD, 0x33), Offset = 1.0 },
                },
            },
        };
        canvas.Children.Add(tipGlow);

        return new Viewbox { Child = canvas, Stretch = Stretch.Uniform };
    }

    private static PathGeometry BuildMainCloud()
    {
        var figure = new PathFigure { StartPoint = new Point(17.5, 45.2), IsClosed = true, IsFilled = true };
        figure.Segments.Add(Bezier(12.8, 45.2, 10.6, 41.6, 11.4, 37.8));
        figure.Segments.Add(Bezier(11.9, 34.4, 14.0, 32.3, 16.5, 31.8));
        figure.Segments.Add(Bezier(16.7, 26.8, 20.4, 23.8, 24.4, 23.6));
        figure.Segments.Add(Bezier(27.2, 15.6, 39.2, 15.6, 42.4, 24.8));
        figure.Segments.Add(Bezier(46.8, 25.0, 49.6, 27.8, 50.0, 31.6));
        figure.Segments.Add(Bezier(52.4, 32.4, 53.6, 35.4, 52.8, 38.4));
        figure.Segments.Add(Bezier(52.0, 42.6, 48.6, 45.2, 44.2, 45.2));
        figure.Segments.Add(new LineSegment { Point = new Point(34.2, 45.2) });
        figure.Segments.Add(Bezier(31.4, 45.4, 29.5, 46.8, 27.8, 48.0));
        figure.Segments.Add(Bezier(28.5, 46.8, 29.2, 45.9, 29.8, 45.2));
        figure.Segments.Add(new LineSegment { Point = new Point(17.5, 45.2) });
        return new PathGeometry { Figures = { figure } };
    }

    private static PathGeometry BuildInnerGlow()
    {
        var figure = new PathFigure { StartPoint = new Point(19.4, 42.6), IsClosed = true, IsFilled = true };
        figure.Segments.Add(Bezier(15.6, 41.2, 14.6, 37.2, 16.4, 34.6));
        figure.Segments.Add(Bezier(18.2, 29.4, 21.8, 26.4, 25.4, 26.2));
        figure.Segments.Add(Bezier(28.0, 19.4, 38.6, 19.6, 41.4, 27.6));
        figure.Segments.Add(Bezier(45.4, 28.4, 47.0, 32.6, 45.6, 36.2));
        figure.Segments.Add(Bezier(43.2, 40.4, 37.6, 43.0, 31.8, 43.2));
        return new PathGeometry { Figures = { figure } };
    }

    private static PathGeometry BuildLeftBillow()
    {
        var figure = new PathFigure { StartPoint = new Point(11.6, 38.6), IsClosed = true, IsFilled = true };
        figure.Segments.Add(Bezier(13.4, 35.4, 16.4, 35.2, 18.4, 36.6));
        figure.Segments.Add(Bezier(21.8, 38.6, 25.0, 43.8, 29.8, 45.2));
        figure.Segments.Add(new LineSegment { Point = new Point(17.5, 45.2) });
        figure.Segments.Add(Bezier(13.2, 45.2, 11.0, 42.0, 11.6, 38.6));
        return new PathGeometry { Figures = { figure } };
    }

    private static PathGeometry BuildRightWisp()
    {
        var figure = new PathFigure { StartPoint = new Point(28.0, 47.9), IsClosed = true, IsFilled = true };
        figure.Segments.Add(Bezier(31.2, 42.4, 34.4, 39.4, 39.2, 36.8));
        figure.Segments.Add(Bezier(43.0, 34.8, 46.2, 31.4, 49.4, 31.9));
        figure.Segments.Add(Bezier(52.2, 32.6, 53.6, 35.6, 52.7, 38.4));
        figure.Segments.Add(Bezier(51.8, 42.6, 48.4, 45.2, 44.2, 45.2));
        figure.Segments.Add(new LineSegment { Point = new Point(34.2, 45.2) });
        figure.Segments.Add(Bezier(31.6, 45.4, 29.8, 46.8, 28.0, 47.9));
        return new PathGeometry { Figures = { figure } };
    }

    private static PathGeometry BuildTipGlow()
    {
        var figure = new PathFigure { StartPoint = new Point(42.0, 38.2), IsClosed = true, IsFilled = true };
        figure.Segments.Add(Bezier(45.2, 33.4, 49.6, 32.2, 51.4, 35.4));
        figure.Segments.Add(Bezier(52.6, 38.8, 48.8, 42.0, 44.6, 42.6));
        figure.Segments.Add(Bezier(42.4, 42.8, 40.8, 40.2, 42.0, 38.2));
        return new PathGeometry { Figures = { figure } };
    }

    private static BezierSegment Bezier(double c1x, double c1y, double c2x, double c2y, double x, double y) =>
        new()
        {
            Point1 = new Point(c1x, c1y),
            Point2 = new Point(c2x, c2y),
            Point3 = new Point(x, y),
        };
}

/// <summary>
/// 品牌插画空态（对齐 macOS FireSkyMarkScene）：品牌标 + 弧线轨道 + 三颗星点。
/// </summary>
public sealed class FireSkyMarkScene : UserControl
{
    public FireSkyMarkScene()
    {
        Width = 64;
        Height = 64;
        var grid = new Grid();
        var outerRing = new Ellipse
        {
            Stroke = new SolidColorBrush(Color.FromArgb(0x28, 0x8C, 0x9E, 0xB8)),
            StrokeThickness = 1.4,
            StrokeDashArray = { 10, 4 },
        };
        var warmArc = new Ellipse
        {
            Margin = new Thickness(4),
            Stroke = new SolidColorBrush(Color.FromArgb(0x59, 0xF4, 0x9B, 0x6A)),
            StrokeThickness = 1.4,
            StrokeDashArray = { 7, 9 },
        };
        grid.Children.Add(outerRing);
        grid.Children.Add(warmArc);
        var canvas = new Canvas { Width = 64, Height = 64 };
        for (var i = 0; i < 3; i++)
        {
            var angle = (-120 + i * 55) * Math.PI / 180.0;
            var radius = 64 * 0.45;
            var starSize = i == 2 ? 3.2 : 4.4;
            var star = new Ellipse
            {
                Width = starSize,
                Height = starSize,
                Fill = new SolidColorBrush(i == 1
                    ? Color.FromArgb(0xFF, 0xF4, 0x9B, 0x6A)
                    : Color.FromArgb(0x99, 0x94, 0xA8, 0xC4)),
            };
            Canvas.SetLeft(star, 32 + Math.Cos(angle) * radius - starSize / 2);
            Canvas.SetTop(star, 32 + Math.Sin(angle) * radius - starSize / 2);
            canvas.Children.Add(star);
        }
        grid.Children.Add(canvas);
        var mark = new FireSkyMark { Width = 38, Height = 38, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        grid.Children.Add(mark);
        Content = grid;
    }
}
