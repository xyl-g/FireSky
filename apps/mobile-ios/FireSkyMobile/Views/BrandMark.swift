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
