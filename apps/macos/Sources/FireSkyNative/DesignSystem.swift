import SwiftUI

/// FireSky 设计系统 v2 token 与共享组件（DESIGN.md 2026-11-21 方案 P1）。
/// 四端同构：Android DesignSystem.kt / iOS DesignSystem.swift / Web app.css
/// 以同一组值映射；改动需四端同步。
enum FSDesign {
    /// 主行动强调渐变（135°，暖橙→琥珀）。
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

    /// 卡片 hairline 边框（dark: 白 8%；light: 黑 6%）。
    static var cardBorderDark: Color { Color.white.opacity(0.08) }
    static var cardBorderLight: Color { Color.black.opacity(0.06) }

    /// 状态徽标底色：语义色 14%。
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

    /// 状态徽标前景（语义色，dark 提亮）。
    static func chipForeground(_ status: String, dark: Bool) -> Color {
        switch status {
        case "done": Color(red: dark ? 0.20 : 0.23, green: dark ? 0.80 : 0.61, blue: dark ? 0.44 : 0.34)
        case "failed": Color(red: 1.00, green: 0.42, blue: 0.32)
        case "interrupted": Color(red: dark ? 1.00 : 0.79, green: dark ? 0.70 : 0.63, blue: dark ? 0.25 : 0.24)
        default: dark ? .white : Color(0x1B, 0x22, 0x24)
        }
    }

    /// 深色页面底渐变修正（方案 D5）：去掉紫色相，青绿同族。
    static func pageBackground(dark: Bool) -> [Color] {
        dark
            ? [Color(red: 0.07, green: 0.10, blue: 0.11),
               Color(red: 0.09, green: 0.14, blue: 0.13),
               Color(red: 0.08, green: 0.13, blue: 0.12)]
            : [Color(red: 0.89, green: 0.93, blue: 0.94),
               Color(red: 0.96, green: 0.92, blue: 0.90),
               Color(red: 0.89, green: 0.94, blue: 0.92)]
    }
}

/// 状态徽标（方案 4.1）：语义色 14% 底 + 图标 + 文字，圆角 6。
/// 替换裸彩色文字，全端统一五态。
struct StatusChip: View {
    let status: String
    let title: String
    @Environment(\.colorScheme) private var scheme

    private var dark: Bool { scheme == .dark }

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
        .background(FSDesign.chipBackground(status, dark: dark), in: RoundedRectangle(cornerRadius: 6))
        .foregroundStyle(FSDesign.chipForeground(status, dark: dark))
    }
}

/// 主行动按钮（方案 4.2）：强调渐变底 + 白/深字，圆角 8。
struct AccentButton: View {
    let title: String
    var systemImage: String? = nil
    var disabled = false
    let action: () -> Void
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let systemImage {
                    Image(systemName: systemImage).font(.system(size: 12, weight: .semibold))
                }
                Text(title).font(.system(size: 13, weight: .semibold))
            }
            .frame(maxWidth: .infinity)
            .frame(height: 41)
            .background(
                disabled
                    ? AnyShapeStyle(Color.gray.opacity(0.3))
                    : AnyShapeStyle(FSDesign.accentGradient(dark: scheme == .dark)),
                in: RoundedRectangle(cornerRadius: 8)
            )
            .foregroundStyle(scheme == .dark ? Color(0x14, 0x14, 0x14) : Color.white)
            .opacity(disabled ? 0.45 : 1)
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }
}

extension Color {
    /// `Color(0x1B, 0x22, 0x24)` 形式的 hex 便捷构造（设计 token 直译）。
    init(_ red: Int, _ green: Int, _ blue: Int, _ alpha: Double = 1) {
        self.init(red: Double(red) / 255, green: Double(green) / 255,
                  blue: Double(blue) / 255, opacity: alpha)
    }
}

/// 玻璃卡片（设计 P3，QiuChenly AppStore 风格借鉴）：regularMaterial 毛玻璃
/// + 16px 圆角 + hairline 边框；emphasized 用于 hero 区（暖橙 hairline）。
struct GlassCard: ViewModifier {
    var emphasized = false
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        let dark = scheme == .dark
        content
            .background {
                if #available(macOS 26.0, *) {
                    // glassEffect 的视觉圆角约为参数一半，24pt 才能达到
                    // 规格 16pt 观感；dark 下叠加 tint 保证与背景分离。
                    Color.clear
                        .glassEffect(.regular, in: .rect(cornerRadius: 24))
                        .background(
                            dark ? Color.white.opacity(0.06) : Color.clear,
                            in: .rect(cornerRadius: 24)
                        )
                } else {
                    RoundedRectangle(cornerRadius: 20)
                        .fill(.regularMaterial.opacity(emphasized ? 0.92 : 0.62))
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: 24)
                    .strokeBorder(
                        emphasized
                            ? Color(red: 1.00, green: 0.55, blue: 0.38).opacity(0.55)
                            : dark ? Color.white.opacity(0.14) : Color.black.opacity(0.08),
                        lineWidth: 1
                    )
            }
    }
}

extension View {
    func glassCard(emphasized: Bool = false) -> some View {
        modifier(GlassCard(emphasized: emphasized))
    }
}
