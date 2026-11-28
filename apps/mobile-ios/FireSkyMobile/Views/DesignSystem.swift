import SwiftUI

/// Native appearance tokens & Liquid Glass surface roles; aligned with macOS FSDesign.
enum FSDesign {
    static func accent(dark: Bool) -> Color {
        dark ? Color(0xFF, 0x9B, 0x6A) : Color(0xD2, 0x5A, 0x36)
    }

    static func accentGradient(dark: Bool) -> LinearGradient {
        LinearGradient(
            colors: dark
                ? [Color(0xFF, 0x9B, 0x6A), Color(0xFF, 0xC2, 0x73)]
                : [Color(0xB8, 0x4B, 0x2E), Color(0x9B, 0x40, 0x1F)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }

    static func coolAccent(dark: Bool) -> Color {
        dark ? Color(0x8E, 0xB7, 0xFF) : Color(0x4F, 0x76, 0xB8)
    }

    static func actionForeground(dark: Bool) -> Color {
        dark ? Color(0x24, 0x18, 0x10) : .white
    }

    static func pageBackground(dark: Bool) -> [Color] {
        dark
            ? [Color(0x0B, 0x12, 0x22), Color(0x12, 0x1B, 0x2E), Color(0x08, 0x0E, 0x18)]
            : [Color(0xEE, 0xF3, 0xF8), Color(0xF8, 0xF5, 0xF0), Color(0xEB, 0xF1, 0xF6)]
    }

    static func ambientGlow(dark: Bool) -> Color {
        dark ? Color(0x8A, 0x55, 0x46).opacity(0.20)
             : Color(0xF4, 0xD2, 0xAE).opacity(0.32)
    }

    static func coolGlow(dark: Bool) -> Color {
        dark ? Color(0x2B, 0x55, 0x8A).opacity(0.22)
             : Color(0xCB, 0xDF, 0xF2).opacity(0.30)
    }

    static func surface(dark: Bool) -> Color {
        dark ? Color(0x18, 0x21, 0x32) : Color(0xFC, 0xFD, 0xFF)
    }

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

    static func border(dark: Bool) -> Color {
        dark ? Color(0x4B, 0x5D, 0x78) : Color(0xC9, 0xD5, 0xE1)
    }

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

    static func cardShadow(dark: Bool) -> Color {
        dark ? Color.black.opacity(0.28) : Color(0x2A, 0x3C, 0x58).opacity(0.08)
    }

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

    static func chipBackground(_ status: String, dark: Bool) -> Color {
        chipForeground(status, dark: dark).opacity(dark ? 0.15 : 0.13)
    }

    static func chipForeground(_ status: String, dark: Bool) -> Color {
        switch status {
        case "done": dark ? Color(0x63, 0xD6, 0xAB) : Color(0x1F, 0x6E, 0x5A)
        case "failed": dark ? Color(0xFF, 0x8A, 0x7A) : Color(0xB8, 0x3E, 0x38)
        case "interrupted": dark ? Color(0xFF, 0xC2, 0x73) : Color(0x8A, 0x63, 0x21)
        default: dark ? Color(0xD5, 0xDE, 0xEB) : Color(0x45, 0x53, 0x64)
        }
    }
}

extension Color {
    init(_ red: Int, _ green: Int, _ blue: Int, _ alpha: Double = 1) {
        self.init(red: Double(red) / 255, green: Double(green) / 255,
                  blue: Double(blue) / 255, opacity: alpha)
    }
}

extension View {
    /// Scale text with the system's Dynamic Type while keeping FireSky's type scale.
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

/// Liquid Glass 分工（对齐 macOS DesignSystem）：
/// - content / grouped：稳定内容表面；
/// - navigation / overlay：系统材质（iOS 26+ 支持 Liquid Glass，低版本回落 .regularMaterial）。
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
        case .content: return 14
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
        let shadowRadius: CGFloat = usesShadow ? (role == .content ? 10 : 7) : 0
        let shadowY: CGFloat = usesShadow ? (role == .content ? 4 : 2) : 0
        if usesSystemMaterial {
            #if compiler(>=6.2)
            if #available(iOS 26.0, *) {
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
                                        Color.clear,
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

    func glassCard(emphasized: Bool = false) -> some View {
        fireSkySurface(role: .content, emphasized: emphasized)
    }
}

struct AccentButton: View {
    let title: String
    var systemImage: String? = nil
    var staleIndicator = false
    var disabled = false
    let action: () -> Void
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var typeSize

    private var titleContent: some View {
        HStack(spacing: 8) {
            Text(title)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if staleIndicator {
                Text("●")
                    .font(.caption2)
                    .foregroundStyle(
                        disabled
                            ? Color.secondary
                            : (scheme == .dark ? Color(0x24, 0x18, 0x10) : Color(red: 1.0, green: 0.93, blue: 0.62))
                    )
                    .accessibilityLabel(L10n.text("参数已变更", "Parameters changed"))
            }
        }
    }

    var body: some View {
        Button(action: action) {
            Group {
                if typeSize.isAccessibilitySize {
                    VStack(spacing: 8) {
                        if let systemImage { Image(systemName: systemImage) }
                        titleContent
                    }
                } else {
                    HStack(spacing: 8) {
                        if let systemImage { Image(systemName: systemImage) }
                        titleContent
                    }
                }
            }
            .font(.body.weight(.semibold))
            .padding(.horizontal, 14).padding(.vertical, 12)
            .frame(maxWidth: .infinity, minHeight: 48)
            .foregroundStyle(disabled ? Color.secondary : FSDesign.actionForeground(dark: scheme == .dark))
            .background(disabled ? AnyShapeStyle(Color.secondary.opacity(0.14))
                        : AnyShapeStyle(FSDesign.accentGradient(dark: scheme == .dark)),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay {
                if staleIndicator && !disabled {
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(Color.orange.opacity(0.9), lineWidth: 1.5)
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .fixedSize(horizontal: false, vertical: typeSize.isAccessibilitySize)
    }
}

/// 状态徽标：语义色 14% 底 + 图标 + 文字，圆角 6。
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

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: icon).font(.system(size: 11, weight: .semibold))
            Text(title).font(.caption.weight(.medium))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(
            FSDesign.chipBackground(status, dark: dark),
            in: RoundedRectangle(cornerRadius: 6)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 6).strokeBorder(
                contrast == .increased
                    ? (dark ? Color.white.opacity(0.6) : Color.black.opacity(0.45))
                    : FSDesign.cardBorder(status: status, dark: dark),
                lineWidth: contrast == .increased ? 1.5 : 1)
        }
        .foregroundStyle(FSDesign.chipForeground(status, dark: dark))
        .accessibilityElement(children: .combine)
    }
}
