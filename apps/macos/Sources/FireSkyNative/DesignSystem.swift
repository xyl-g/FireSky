import SwiftUI

/// FireSky 设计系统 v2 token 与共享组件（DESIGN.md 2026-11-21 方案 P1）。
/// 四端同构：Android DesignSystem.kt / iOS DesignSystem.swift / Web app.css
/// 以同一组值映射；改动需四端同步。
enum FSDesign {
    // MARK: - Palette
    /// 品牌主色：铜橙。深色提亮以维持暗底对比，浅色压深以维持文字对比。
    static func accent(dark: Bool) -> Color {
        dark ? Color(0xFF, 0x9B, 0x6A) : Color(0xD2, 0x5A, 0x36)
    }

    /// 主行动强调渐变（135°，铜橙 → 琥珀）。
    static func accentGradient(dark: Bool) -> LinearGradient {
        LinearGradient(
            colors: dark
                ? [Color(0xFF, 0x9B, 0x6A), Color(0xFF, 0xC2, 0x73)]
                : [Color(0xB8, 0x4B, 0x2E), Color(0x9B, 0x40, 0x1F)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }

    /// 第二模型 / 辅助数据色：冷静蓝，与铜橙形成稳定互补。
    static func coolAccent(dark: Bool) -> Color {
        dark ? Color(0x8E, 0xB7, 0xFF) : Color(0x4F, 0x76, 0xB8)
    }

    /// 页面环境底色：冷瓷白 ↔ 暖杏光；深色为带靛蓝的夜空。
    static func pageBackground(dark: Bool) -> [Color] {
        dark
            ? [Color(0x0B, 0x12, 0x22),
               Color(0x12, 0x1B, 0x2E),
               Color(0x08, 0x0E, 0x18)]
            : [Color(0xEE, 0xF3, 0xF8),
               Color(0xF8, 0xF5, 0xF0),
               Color(0xEB, 0xF1, 0xF6)]
    }

    /// 右上角暖光，让深色页有地平线感；浅色页则提供柔和杏色环境光。
    static func ambientGlow(dark: Bool) -> Color {
        dark ? Color(0x8A, 0x55, 0x46).opacity(0.20)
             : Color(0xF4, 0xD2, 0xAE).opacity(0.32)
    }

    /// 左下角冷光，平衡暖色，避免浅色页泛黄、深色页死黑。
    static func coolGlow(dark: Bool) -> Color {
        dark ? Color(0x2B, 0x55, 0x8A).opacity(0.22)
             : Color(0xCB, 0xDF, 0xF2).opacity(0.30)
    }

    /// 稳定内容表面：不是玻璃，但需要承接页面色调，避免系统灰卡“跳出来”。
    static func surfaceFill(role: FireSkySurfaceRole, dark: Bool) -> Color {
        switch role {
        case .content:
            return dark ? Color(0x18, 0x21, 0x32) : Color(0xFC, 0xFD, 0xFF)
        case .grouped, .overlay:
            return dark ? Color(0x20, 0x2A, 0x3D) : Color(0xF3, 0xF6, 0xFA)
        case .navigation:
            return dark ? Color(0x11, 0x18, 0x26) : Color(0xF5, 0xF7, 0xFA)
        }
    }

    /// 与蓝灰表面同族的边界色；强调态才使用品牌色。
    static func surfaceBorder(role: FireSkySurfaceRole, dark: Bool, emphasized: Bool) -> Color {
        if emphasized {
            return dark
                ? Color(0xFF, 0x9B, 0x6A).opacity(0.48)
                : Color(0xC4, 0x5B, 0x38).opacity(0.34)
        }
        switch role {
        case .content:
            return dark
                ? Color(0x4B, 0x5D, 0x78).opacity(0.55)
                : Color(0xC9, 0xD5, 0xE1).opacity(0.85)
        case .grouped, .overlay:
            return dark
                ? Color(0x50, 0x61, 0x7B).opacity(0.45)
                : Color(0xD8, 0xE1, 0xEA).opacity(0.85)
        case .navigation:
            return dark ? Color.white.opacity(0.08) : Color.black.opacity(0.06)
        }
    }

    /// 卡片阴影使用蓝灰色而非纯黑，避免深色模式出现脏灰边。
    static func cardShadow(dark: Bool) -> Color {
        dark ? Color.black.opacity(0.30) : Color(0x2A, 0x3C, 0x58).opacity(0.10)
    }

    /// 卡片 hairline 边框（旧调用保留，已改为蓝灰色系）。
    static var cardBorderDark: Color { Color(0x4B, 0x5D, 0x78).opacity(0.55) }
    static var cardBorderLight: Color { Color(0xC9, 0xD5, 0xE1).opacity(0.85) }

    /// 状态徽标的低对比边界：默认用于增强状态分离，不承担唯一语义。
    static func cardBorder(status: String, dark: Bool) -> Color {
        switch status {
        case "done":
            return dark ? Color(0x5E, 0xD3, 0xA5).opacity(0.32)
                        : Color(0x2E, 0x8B, 0x72).opacity(0.30)
        case "failed":
            return dark ? Color(0xFF, 0x8A, 0x7A).opacity(0.42)
                        : Color(0xD4, 0x4D, 0x45).opacity(0.34)
        case "interrupted":
            return dark ? Color(0xFF, 0xC2, 0x73).opacity(0.38)
                        : Color(0xA8, 0x79, 0x2C).opacity(0.32)
        default:
            return dark ? Color(0x7D, 0x90, 0xA8).opacity(0.26)
                        : Color(0x94, 0xA3, 0xB4).opacity(0.30)
        }
    }

    /// 状态徽标底色：语义色 15%。
    static func chipBackground(_ status: String, dark: Bool) -> Color {
        let rgb: (Double, Double, Double)
        switch status {
        case "done":
            rgb = dark ? (0.30, 0.82, 0.65) : (0.18, 0.55, 0.45)
        case "failed":
            rgb = dark ? (1.00, 0.54, 0.48) : (0.83, 0.30, 0.27)
        case "interrupted":
            rgb = dark ? (1.00, 0.76, 0.45) : (0.80, 0.60, 0.26)
        default:
            rgb = dark ? (0.55, 0.65, 0.78) : (0.40, 0.48, 0.58)
        }
        return Color(red: rgb.0, green: rgb.1, blue: rgb.2, opacity: dark ? 0.15 : 0.13)
    }

    /// 状态徽标前景（语义色，dark 提亮）。
    static func chipForeground(_ status: String, dark: Bool) -> Color {
        switch status {
        case "done":
            return dark ? Color(0x63, 0xD6, 0xAB) : Color(0x1F, 0x6E, 0x5A)
        case "failed":
            return dark ? Color(0xFF, 0x8A, 0x7A) : Color(0xB8, 0x3E, 0x38)
        case "interrupted":
            return dark ? Color(0xFF, 0xC2, 0x73) : Color(0x8A, 0x63, 0x21)
        default:
            return dark ? Color(0xD5, 0xDE, 0xEB) : Color(0x45, 0x53, 0x64)
        }
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
                Text(title).fireSkyFont(size: 13, weight: .semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .frame(minHeight: 41)
            .background(
                disabled
                    ? AnyShapeStyle(Color(0x94, 0xA3, 0xB4).opacity(0.28))
                    : AnyShapeStyle(FSDesign.accentGradient(dark: scheme == .dark)),
                in: RoundedRectangle(cornerRadius: 8)
            )
            .foregroundStyle(scheme == .dark ? Color(0x24, 0x18, 0x10) : Color.white)
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

extension View {
    /// Scale text with the system's accessibility text size while keeping FireSky's type scale.
    func fireSkyFont(size: CGFloat, weight: Font.Weight = .regular,
                     relativeTo style: Font.TextStyle = .body) -> some View {
        modifier(FireSkyFontModifier(size: size, weight: weight, relativeTo: style))
    }
}

private struct FireSkyFontModifier: ViewModifier {
    @ScaledMetric private var scaledSize: CGFloat
    let weight: Font.Weight

    init(size: CGFloat, weight: Font.Weight, relativeTo style: Font.TextStyle) {
        self.weight = weight
        _scaledSize = ScaledMetric(wrappedValue: size, relativeTo: style)
    }

    func body(content: Content) -> some View {
        content.font(.system(size: scaledSize, weight: weight))
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
    var cornerRadius: CGFloat? = nil

    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var highContrast: Bool { contrast == .increased }

    private var radius: CGFloat {
        if let cornerRadius { return cornerRadius }
        switch role {
        case .content: return 16
        case .grouped, .overlay: return 12
        case .navigation: return 0
        }
    }

    private var solidFill: Color {
        FSDesign.surfaceFill(role: role, dark: scheme == .dark)
    }

    private var usesSystemMaterial: Bool {
        !reduceTransparency && !highContrast && (role == .navigation || role == .overlay)
    }

    private var usesShadow: Bool {
        !highContrast && (role == .content || role == .grouped || role == .overlay)
    }

    private var borderColor: Color {
        if highContrast {
            return scheme == .dark
                ? Color(0xE8, 0xEF, 0xF8).opacity(0.62)
                : Color(0x2A, 0x3C, 0x58).opacity(0.50)
        }
        return FSDesign.surfaceBorder(role: role, dark: scheme == .dark, emphasized: emphasized)
    }

    @ViewBuilder
    private var surfaceBackground: some View {
        let shadowColor = usesShadow ? FSDesign.cardShadow(dark: scheme == .dark) : Color.clear
        let shadowRadius: CGFloat = usesShadow ? (role == .content ? 12 : 8) : 0
        let shadowY: CGFloat = usesShadow ? (role == .content ? 5 : 3) : 0
        if usesSystemMaterial {
            #if compiler(>=6.2)
            if #available(macOS 26.0, *) {
                Color.clear.glassEffect(.regular, in: .rect(cornerRadius: radius))
                    .shadow(color: shadowColor, radius: shadowRadius, y: shadowY)
            } else {
                RoundedRectangle(cornerRadius: radius).fill(.regularMaterial)
                    .shadow(color: shadowColor, radius: shadowRadius, y: shadowY)
            }
            #else
            RoundedRectangle(cornerRadius: radius).fill(.regularMaterial)
                .shadow(color: shadowColor, radius: shadowRadius, y: shadowY)
            #endif
        } else {
            RoundedRectangle(cornerRadius: radius).fill(solidFill)
                .shadow(color: shadowColor, radius: shadowRadius, y: shadowY)
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
            .background {
                ZStack {
                    surfaceBackground
                    if emphasized && !highContrast {
                        RoundedRectangle(cornerRadius: radius)
                            .fill(
                                LinearGradient(
                                    colors: [
                                        FSDesign.accent(dark: scheme == .dark).opacity(scheme == .dark ? 0.10 : 0.055),
                                        Color.clear
                                    ],
                                    startPoint: .topLeading, endPoint: .bottomTrailing
                                )
                            )
                            .allowsHitTesting(false)
                    }
                }
            }
            .overlay { surfaceBorder }
    }
}

extension View {
    func fireSkySurface(role: FireSkySurfaceRole = .content, emphasized: Bool = false,
                        cornerRadius: CGFloat? = nil) -> some View {
        modifier(FireSkySurface(role: role, emphasized: emphasized, cornerRadius: cornerRadius))
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
