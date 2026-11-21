import SwiftUI

/// FireSky 设计系统 v2 token（DESIGN.md 方案 P1；与 macOS DesignSystem.swift 同值）。
enum FSDesign {
    static func accentGradient(dark: Bool) -> LinearGradient {
        LinearGradient(
            colors: dark
                ? [Color(red: 1.00, green: 0.55, blue: 0.38),
                   Color(red: 0.98, green: 0.75, blue: 0.14)]
                : [Color(red: 0.88, green: 0.42, blue: 0.24),
                   Color(red: 0.85, green: 0.54, blue: 0.18)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }

    static func chipBackground(_ status: String, dark: Bool) -> Color {
        let rgb: (Double, Double, Double)
        let alpha: Double
        switch status {
        case "done":
            rgb = dark ? (0.20, 0.80, 0.44) : (0.23, 0.61, 0.34)
            alpha = 0.14
        case "failed":
            rgb = (1.00, 0.42, 0.32)
            alpha = dark ? 0.14 : 0.12
        case "interrupted":
            rgb = dark ? (1.00, 0.70, 0.25) : (0.79, 0.63, 0.24)
            alpha = 0.14
        default:
            rgb = dark ? (0.50, 0.55, 0.55) : (0.45, 0.45, 0.45)
            alpha = 0.12
        }
        return Color(red: rgb.0, green: rgb.1, blue: rgb.2, opacity: alpha)
    }

    static func chipForeground(_ status: String, dark: Bool) -> Color {
        switch status {
        case "done": Color(red: dark ? 0.20 : 0.23, green: dark ? 0.80 : 0.61, blue: dark ? 0.44 : 0.34)
        case "failed": Color(red: 1.00, green: 0.42, blue: 0.32)
        case "interrupted": Color(red: dark ? 1.00 : 0.79, green: dark ? 0.70 : 0.63, blue: dark ? 0.25 : 0.24)
        default: dark ? .white : Color(red: 0x1B / 255.0, green: 0x22 / 255.0, blue: 0x24 / 255.0)
        }
    }
}

/// 状态徽标（方案 4.1）。
struct StatusChip: View {
    let status: String
    let title: String
    @Environment(\.colorScheme) private var scheme

    private var icon: String {
        switch status {
        case "done": "checkmark.circle.fill"
        case "failed": "exclamationmark.circle.fill"
        case "interrupted": "pause.circle"
        case "running": "clock"
        default: "arrow.down.circle"
        }
    }

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: icon).font(.system(size: 10, weight: .semibold))
            Text(title).font(.system(size: 11, weight: .medium))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(
            FSDesign.chipBackground(status, dark: scheme == .dark),
            in: RoundedRectangle(cornerRadius: 6)
        )
        .foregroundStyle(FSDesign.chipForeground(status, dark: scheme == .dark))
    }
}
