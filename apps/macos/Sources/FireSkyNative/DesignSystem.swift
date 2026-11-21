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

    /// 状态徽标的低对比边界：默认用于增强状态分离，不承担唯一语义。
    static func cardBorder(status: String, dark: Bool) -> Color {
        switch status {
        case "done": return dark ? Color.white.opacity(0.18) : Color.black.opacity(0.14)
        case "failed": return Color(red: 1.00, green: 0.42, blue: 0.32).opacity(dark ? 0.40 : 0.32)
        case "interrupted": return dark ? Color.white.opacity(0.18) : Color.black.opacity(0.14)
        default: return dark ? Color.white.opacity(0.12) : Color.black.opacity(0.10)
        }
    }

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
        // v1.9.3 暗色修正：夜空蓝黑体系（深夜蓝渐变，与暖橙 accent 呼应），
        // 替换旧灰绿底（用户反馈"暗色丑"）。
        dark
            ? [Color(red: 0.03, green: 0.05, blue: 0.09),
               Color(red: 0.05, green: 0.08, blue: 0.14),
               Color(red: 0.04, green: 0.06, blue: 0.11)]
            : [Color(red: 0.89, green: 0.93, blue: 0.94),
               Color(red: 0.96, green: 0.92, blue: 0.90),
               Color(red: 0.89, green: 0.94, blue: 0.92)]
    }
}

/// 状态徽标：语义色 14% 底 + 图标 + 文字，圆角 6。
/// 图标、文案和对比边框共同承载状态，不只依赖颜色。
struct StatusChip: View {
    let status: String
    let title: String
    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast

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

    private var stateLabel: String {
        switch status {
        case "done": "完成"
        case "failed": "失败"
        case "interrupted": "已中断"
        case "running": "运行中"
        default: "待处理"
        }
    }

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: icon).font(.system(size: 11, weight: .semibold))
            Text(title).font(.system(size: 11, weight: .medium))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(FSDesign.chipBackground(status, dark: dark), in: RoundedRectangle(cornerRadius: 6))
        .overlay {
            RoundedRectangle(cornerRadius: 6)
                .strokeBorder(
                    contrast == .increased
                        ? (dark ? Color.white.opacity(0.6) : Color.black.opacity(0.45))
                        : FSDesign.cardBorder(status: status, dark: dark),
                    lineWidth: contrast == .increased ? 1.5 : 1
                )
        }
        .foregroundStyle(FSDesign.chipForeground(status, dark: dark))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title)，状态：\(stateLabel)")
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

/// Liquid Glass 分工（MACOS_LIQUID_GLASS_UX_HANDOFF §6）：
/// - content / grouped：稳定内容表面，不使用玻璃；
/// - navigation / overlay：系统材质，随背景、明暗和辅助功能设置自适应。
/// Reduce Transparency 或增强对比时，导航和浮层切换为稳定表面，但层级不变。
enum FireSkySurfaceRole {
    case content
    case grouped
    case navigation
    case overlay
}

struct FireSkySurface: ViewModifier {
    let role: FireSkySurfaceRole
    var emphasized = false

    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var highContrast: Bool { contrast == .increased }

    private var radius: CGFloat {
        switch role {
        case .content: return 16
        case .grouped, .overlay: return 12
        case .navigation: return 0
        }
    }

    private var solidFill: Color {
        switch role {
        case .content, .navigation:
            return Color(nsColor: .windowBackgroundColor)
        case .grouped, .overlay:
            return Color(nsColor: .controlBackgroundColor)
        }
    }

    private var usesSystemMaterial: Bool {
        !reduceTransparency && !highContrast && (role == .navigation || role == .overlay)
    }

    private var borderColor: Color {
        if highContrast {
            return scheme == .dark ? Color.white.opacity(0.55) : Color.black.opacity(0.45)
        }
        if emphasized {
            return scheme == .dark
                ? Color(red: 1.00, green: 0.55, blue: 0.38).opacity(0.42)
                : Color(red: 0.74, green: 0.32, blue: 0.24).opacity(0.30)
        }
        return scheme == .dark ? Color.white.opacity(0.10) : Color.black.opacity(0.08)
    }

    @ViewBuilder
    private var surfaceBackground: some View {
        if usesSystemMaterial {
            if #available(macOS 26.0, *) {
                Color.clear.glassEffect(.regular, in: .rect(cornerRadius: radius))
            } else {
                RoundedRectangle(cornerRadius: radius).fill(.regularMaterial)
            }
        } else {
            RoundedRectangle(cornerRadius: radius).fill(solidFill)
        }
    }

    @ViewBuilder
    private var surfaceBorder: some View {
        if radius == 0 {
            Rectangle()
                .strokeBorder(borderColor, lineWidth: highContrast ? 1 : 0.5)
        } else {
            RoundedRectangle(cornerRadius: radius)
                .strokeBorder(borderColor, lineWidth: highContrast ? 1.5 : 1)
        }
    }

    func body(content: Content) -> some View {
        content
            .background { surfaceBackground }
            .overlay { surfaceBorder }
    }
}

extension View {
    func fireSkySurface(role: FireSkySurfaceRole = .content, emphasized: Bool = false) -> some View {
        modifier(FireSkySurface(role: role, emphasized: emphasized))
    }
}

/// 兼容旧调用名：内容卡片不再使用 Liquid Glass，改用稳定内容表面。
struct GlassCard: ViewModifier {
    var emphasized = false

    func body(content: Content) -> some View {
        content.fireSkySurface(role: .content, emphasized: emphasized)
    }
}

extension View {
    func glassCard(emphasized: Bool = false) -> some View {
        modifier(GlassCard(emphasized: emphasized))
    }
}
