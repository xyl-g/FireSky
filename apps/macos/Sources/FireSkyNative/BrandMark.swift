import SwiftUI

struct FireSkyMark: View {
    var body: some View {
        Canvas { context, size in
            context.scaleBy(x: size.width / 64, y: size.height / 64)
            let ink = Color(red: 0.13, green: 0.17, blue: 0.18)
            let glow = Color(red: 1, green: 0.58, blue: 0.40)
            context.fill(Path(roundedRect: CGRect(x: 1, y: 1, width: 62, height: 62), cornerRadius: 14),
                         with: .color(ink))
            var sky = context
            sky.clip(to: Path(CGRect(x: 0, y: 0, width: 64, height: 33)))
            sky.fill(Path(ellipseIn: CGRect(x: 16, y: 13, width: 32, height: 32)), with: .color(glow))
            var rays = Path()
            rays.move(to: CGPoint(x: 32, y: 6))
            rays.addLine(to: CGPoint(x: 32, y: 8))
            rays.move(to: CGPoint(x: 13, y: 13))
            rays.addLine(to: CGPoint(x: 15, y: 15))
            rays.move(to: CGPoint(x: 51, y: 13))
            rays.addLine(to: CGPoint(x: 49, y: 15))
            context.stroke(rays, with: .color(glow), style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
            var horizon = Path()
            horizon.move(to: CGPoint(x: 12, y: 37))
            horizon.addLine(to: CGPoint(x: 52, y: 37))
            context.stroke(horizon, with: .color(.white), style: StrokeStyle(lineWidth: 3, lineCap: .round))
            var reflection = Path()
            reflection.move(to: CGPoint(x: 23, y: 46))
            reflection.addLine(to: CGPoint(x: 41, y: 46))
            context.stroke(reflection, with: .color(glow), style: StrokeStyle(lineWidth: 3, lineCap: .round))
        }
        .accessibilityHidden(true)
    }
}

/// 品牌插画空态（方案 B3）：品牌标 + 弧线轨道 + 三颗星点，供空态场景使用
/// （比裸图标更有“夜空”叙事感；四端同构）。
struct FireSkyMarkScene: View {
    var size: CGFloat = 96

    var body: some View {
        ZStack {
            // 轨道弧线（黄道感）。
            Circle()
                .trim(from: 0.0, to: 0.72)
                .stroke(Color.white.opacity(0.14), style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                .rotationEffect(.degrees(-120))
            Circle()
                .trim(from: 0.0, to: 0.45)
                .stroke(Color(red: 1, green: 0.58, blue: 0.40).opacity(0.35),
                        style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                .rotationEffect(.degrees(140))
            // 三颗星点（暖橙/白交替）。
            ForEach(0..<3, id: \.self) { index in
                let angle = (-120 + Double(index) * 55) * .pi / 180
                let radius = size * 0.46
                Circle()
                    .fill(index == 1 ? Color(red: 1, green: 0.58, blue: 0.40) : Color.white.opacity(0.55))
                    .frame(width: index == 2 ? 3 : 4.5, height: index == 2 ? 3 : 4.5)
                    .offset(x: cos(angle) * radius, y: sin(angle) * radius)
            }
            FireSkyMark().frame(width: size * 0.62, height: size * 0.62)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
