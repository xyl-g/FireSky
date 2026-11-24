import SwiftUI

struct FireSkyMark: View {
    var body: some View {
        Canvas { context, size in
            context.scaleBy(x: size.width / 64, y: size.height / 64)

            // 1. Deep twilight navy-to-burgundy squircle tile matching the FireSky icon
            let tileRect = CGRect(x: 1, y: 1, width: 62, height: 62)
            let tilePath = Path(roundedRect: tileRect, cornerRadius: 14.5, style: .continuous)
            let bgGradient = Gradient(stops: [
                .init(color: Color(red: 0.06, green: 0.16, blue: 0.30), location: 0.0),
                .init(color: Color(red: 0.14, green: 0.15, blue: 0.28), location: 0.50),
                .init(color: Color(red: 0.37, green: 0.12, blue: 0.21), location: 1.0),
            ])
            context.fill(
                tilePath,
                with: .linearGradient(bgGradient,
                                      startPoint: CGPoint(x: 32, y: 1),
                                      endPoint: CGPoint(x: 32, y: 63))
            )

            // Subtle inner twilight rim
            context.stroke(
                tilePath,
                with: .linearGradient(
                    Gradient(colors: [Color.white.opacity(0.16), Color.white.opacity(0.03)]),
                    startPoint: CGPoint(x: 32, y: 1),
                    endPoint: CGPoint(x: 32, y: 63)
                ),
                lineWidth: 0.8
            )

            // Soft warm sunset ambient glow behind the cloud (clipped to tile, zero hard edge)
            var glowCtx = context
            glowCtx.clip(to: tilePath)
            glowCtx.fill(
                tilePath,
                with: .radialGradient(
                    Gradient(colors: [
                        Color(red: 1.00, green: 0.45, blue: 0.16).opacity(0.28),
                        Color(red: 0.90, green: 0.22, blue: 0.24).opacity(0.08),
                        Color.clear,
                    ]),
                    center: CGPoint(x: 32, y: 34),
                    startRadius: 3,
                    endRadius: 29
                )
            )

            // 2. Sculpted outer sunset cloud silhouette (golden-amber crown to crimson underside)
            var mainCloud = Path()
            mainCloud.move(to: CGPoint(x: 17.5, y: 45.2))
            // Left lower puff
            mainCloud.addCurve(to: CGPoint(x: 11.4, y: 37.8),
                               control1: CGPoint(x: 12.8, y: 45.2),
                               control2: CGPoint(x: 10.6, y: 41.6))
            mainCloud.addCurve(to: CGPoint(x: 16.5, y: 31.8),
                               control1: CGPoint(x: 11.9, y: 34.4),
                               control2: CGPoint(x: 14.0, y: 32.3))
            // Mid-left shoulder puff
            mainCloud.addCurve(to: CGPoint(x: 24.4, y: 23.6),
                               control1: CGPoint(x: 16.7, y: 26.8),
                               control2: CGPoint(x: 20.4, y: 23.8))
            // Tall central glowing dome
            mainCloud.addCurve(to: CGPoint(x: 42.4, y: 24.8),
                               control1: CGPoint(x: 27.2, y: 15.6),
                               control2: CGPoint(x: 39.2, y: 15.6))
            // Right upper shoulder
            mainCloud.addCurve(to: CGPoint(x: 50.0, y: 31.6),
                               control1: CGPoint(x: 46.8, y: 25.0),
                               control2: CGPoint(x: 49.6, y: 27.8))
            // Right glowing flame curl
            mainCloud.addCurve(to: CGPoint(x: 52.8, y: 38.4),
                               control1: CGPoint(x: 52.4, y: 32.4),
                               control2: CGPoint(x: 53.6, y: 35.4))
            mainCloud.addCurve(to: CGPoint(x: 44.2, y: 45.2),
                               control1: CGPoint(x: 52.0, y: 42.6),
                               control2: CGPoint(x: 48.6, y: 45.2))
            // Bottom edge with signature flame tail notch
            mainCloud.addLine(to: CGPoint(x: 34.2, y: 45.2))
            mainCloud.addCurve(to: CGPoint(x: 27.8, y: 48.0),
                               control1: CGPoint(x: 31.4, y: 45.4),
                               control2: CGPoint(x: 29.5, y: 46.8))
            mainCloud.addCurve(to: CGPoint(x: 29.8, y: 45.2),
                               control1: CGPoint(x: 28.5, y: 46.8),
                               control2: CGPoint(x: 29.2, y: 45.9))
            mainCloud.addLine(to: CGPoint(x: 17.5, y: 45.2))
            mainCloud.closeSubpath()

            // Outer golden-amber rim gradient
            context.fill(
                mainCloud,
                with: .linearGradient(
                    Gradient(stops: [
                        .init(color: Color(red: 1.00, green: 0.78, blue: 0.16), location: 0.0),
                        .init(color: Color(red: 0.99, green: 0.49, blue: 0.13), location: 0.48),
                        .init(color: Color(red: 0.85, green: 0.16, blue: 0.21), location: 1.0),
                    ]),
                    startPoint: CGPoint(x: 32, y: 17),
                    endPoint: CGPoint(x: 28, y: 47)
                )
            )

            // 3. Inner bright golden-orange upper body (creates crisp backlit rim along top crest)
            var innerGlow = Path()
            innerGlow.move(to: CGPoint(x: 19.4, y: 42.6))
            innerGlow.addCurve(to: CGPoint(x: 16.4, y: 34.6),
                               control1: CGPoint(x: 15.6, y: 41.2),
                               control2: CGPoint(x: 14.6, y: 37.2))
            innerGlow.addCurve(to: CGPoint(x: 25.4, y: 26.2),
                               control1: CGPoint(x: 18.2, y: 29.4),
                               control2: CGPoint(x: 21.8, y: 26.4))
            innerGlow.addCurve(to: CGPoint(x: 41.4, y: 27.6),
                               control1: CGPoint(x: 28.0, y: 19.4),
                               control2: CGPoint(x: 38.6, y: 19.6))
            innerGlow.addCurve(to: CGPoint(x: 45.6, y: 36.2),
                               control1: CGPoint(x: 45.4, y: 28.4),
                               control2: CGPoint(x: 47.0, y: 32.6))
            innerGlow.addCurve(to: CGPoint(x: 31.8, y: 43.2),
                               control1: CGPoint(x: 43.2, y: 40.4),
                               control2: CGPoint(x: 37.6, y: 43.0))
            innerGlow.closeSubpath()

            context.fill(
                innerGlow,
                with: .radialGradient(
                    Gradient(stops: [
                        .init(color: Color(red: 1.00, green: 0.85, blue: 0.24), location: 0.0),
                        .init(color: Color(red: 1.00, green: 0.56, blue: 0.14), location: 0.55),
                        .init(color: Color(red: 0.90, green: 0.25, blue: 0.16), location: 1.0),
                    ]),
                    center: CGPoint(x: 34, y: 26),
                    startRadius: 2,
                    endRadius: 22
                )
            )

            // 4. Lower-left sculpted deep-crimson cloud billow shadow
            var leftBillow = Path()
            leftBillow.move(to: CGPoint(x: 11.6, y: 38.6))
            leftBillow.addCurve(to: CGPoint(x: 18.4, y: 36.6),
                                control1: CGPoint(x: 13.4, y: 35.4),
                                control2: CGPoint(x: 16.4, y: 35.2))
            leftBillow.addCurve(to: CGPoint(x: 29.8, y: 45.2),
                                control1: CGPoint(x: 21.8, y: 38.6),
                                control2: CGPoint(x: 25.0, y: 43.8))
            leftBillow.addLine(to: CGPoint(x: 17.5, y: 45.2))
            leftBillow.addCurve(to: CGPoint(x: 11.6, y: 38.6),
                                control1: CGPoint(x: 13.2, y: 45.2),
                                control2: CGPoint(x: 11.0, y: 42.0))
            leftBillow.closeSubpath()

            context.fill(
                leftBillow,
                with: .linearGradient(
                    Gradient(colors: [
                        Color(red: 0.92, green: 0.22, blue: 0.19),
                        Color(red: 0.64, green: 0.09, blue: 0.22),
                    ]),
                    startPoint: CGPoint(x: 18, y: 36),
                    endPoint: CGPoint(x: 16, y: 46)
                )
            )

            // 5. Right curling golden flame wisp sweeping to the bottom tail
            var rightWisp = Path()
            rightWisp.move(to: CGPoint(x: 28.0, y: 47.9))
            rightWisp.addCurve(to: CGPoint(x: 39.2, y: 36.8),
                               control1: CGPoint(x: 31.2, y: 42.4),
                               control2: CGPoint(x: 34.4, y: 39.4))
            rightWisp.addCurve(to: CGPoint(x: 49.4, y: 31.9),
                               control1: CGPoint(x: 43.0, y: 34.8),
                               control2: CGPoint(x: 46.2, y: 31.4))
            rightWisp.addCurve(to: CGPoint(x: 52.7, y: 38.4),
                               control1: CGPoint(x: 52.2, y: 32.6),
                               control2: CGPoint(x: 53.6, y: 35.6))
            rightWisp.addCurve(to: CGPoint(x: 44.2, y: 45.2),
                               control1: CGPoint(x: 51.8, y: 42.6),
                               control2: CGPoint(x: 48.4, y: 45.2))
            rightWisp.addLine(to: CGPoint(x: 34.2, y: 45.2))
            rightWisp.addCurve(to: CGPoint(x: 28.0, y: 47.9),
                               control1: CGPoint(x: 31.6, y: 45.4),
                               control2: CGPoint(x: 29.8, y: 46.8))
            rightWisp.closeSubpath()

            context.fill(
                rightWisp,
                with: .linearGradient(
                    Gradient(stops: [
                        .init(color: Color(red: 1.00, green: 0.89, blue: 0.34), location: 0.0),
                        .init(color: Color(red: 0.99, green: 0.54, blue: 0.14), location: 0.45),
                        .init(color: Color(red: 0.84, green: 0.18, blue: 0.17), location: 1.0),
                    ]),
                    startPoint: CGPoint(x: 51, y: 33),
                    endPoint: CGPoint(x: 32, y: 47)
                )
            )

            // Crisp soft crest highlight on the right wisp tip
            var tipGlow = Path()
            tipGlow.move(to: CGPoint(x: 42.0, y: 38.2))
            tipGlow.addCurve(to: CGPoint(x: 51.4, y: 35.4),
                             control1: CGPoint(x: 45.2, y: 33.4),
                             control2: CGPoint(x: 49.6, y: 32.2))
            tipGlow.addCurve(to: CGPoint(x: 44.6, y: 42.6),
                             control1: CGPoint(x: 52.6, y: 38.8),
                             control2: CGPoint(x: 48.8, y: 42.0))
            tipGlow.addCurve(to: CGPoint(x: 42.0, y: 38.2),
                             control1: CGPoint(x: 42.4, y: 42.8),
                             control2: CGPoint(x: 40.8, y: 40.2))
            tipGlow.closeSubpath()

            context.fill(
                tipGlow,
                with: .radialGradient(
                    Gradient(colors: [
                        Color(red: 1.00, green: 0.94, blue: 0.52).opacity(0.88),
                        Color(red: 1.00, green: 0.68, blue: 0.20).opacity(0.0),
                    ]),
                    center: CGPoint(x: 49.2, y: 35.6),
                    startRadius: 0.5,
                    endRadius: 8.5
                )
            )
        }
        .accessibilityHidden(true)
    }
}

/// 品牌插画空态（方案 B3）：品牌标 + 弧线轨道 + 三颗星点，供空态场景使用
/// （比裸图标更有“夜空”叙事感；四端同构）。
struct FireSkyMarkScene: View {
    @Environment(\.colorScheme) private var scheme
    var size: CGFloat = 96

    var body: some View {
        let dark = scheme == .dark
        ZStack {
            // 轨道弧线（黄道感）。
            Circle()
                .trim(from: 0.0, to: 0.72)
                .stroke(
                    dark ? Color.white.opacity(0.14) : Color(0x4F, 0x76, 0xB8).opacity(0.20),
                    style: StrokeStyle(lineWidth: 1.5, lineCap: .round)
                )
                .rotationEffect(.degrees(-120))
            Circle()
                .trim(from: 0.0, to: 0.45)
                .stroke(Color(red: 0xF4 / 255.0, green: 0x9B / 255.0, blue: 0x6A / 255.0).opacity(dark ? 0.35 : 0.48),
                        style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                .rotationEffect(.degrees(140))
            // 三颗星点（暖橙/蓝白交替）。
            ForEach(0..<3, id: \.self) { index in
                let angle = (-120 + Double(index) * 55) * .pi / 180
                let radius = size * 0.46
                Circle()
                    .fill(index == 1
                          ? Color(red: 0xF4 / 255.0, green: 0x9B / 255.0, blue: 0x6A / 255.0)
                          : (dark ? Color.white.opacity(0.55) : Color(0x4F, 0x76, 0xB8).opacity(0.45)))
                    .frame(width: index == 2 ? 3 : 4.5, height: index == 2 ? 3 : 4.5)
                    .offset(x: cos(angle) * radius, y: sin(angle) * radius)
            }
            FireSkyMark().frame(width: size * 0.62, height: size * 0.62)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
