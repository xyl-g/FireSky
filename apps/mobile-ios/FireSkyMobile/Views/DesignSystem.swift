import SwiftUI

/// Native appearance tokens; keep aligned with macOS FSDesign and Android Theme.kt.
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

    static func actionForeground(dark: Bool) -> Color {
        dark ? Color(0x24, 0x18, 0x10) : .white
    }

    static func pageBackground(dark: Bool) -> [Color] {
        dark
            ? [Color(0x0B, 0x12, 0x22), Color(0x12, 0x1B, 0x2E), Color(0x08, 0x0E, 0x18)]
            : [Color(0xEE, 0xF3, 0xF8), Color(0xF8, 0xF5, 0xF0), Color(0xEB, 0xF1, 0xF6)]
    }

    static func surface(dark: Bool) -> Color {
        dark ? Color(0x1B, 0x24, 0x35) : Color(0xFB, 0xFC, 0xFE)
    }

    static func border(dark: Bool) -> Color {
        dark ? Color(0x4B, 0x5D, 0x78) : Color(0xC9, 0xD5, 0xE1)
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
    init(_ red: Int, _ green: Int, _ blue: Int) {
        self.init(red: Double(red) / 255, green: Double(green) / 255, blue: Double(blue) / 255)
    }
}

struct AccentButton: View {
    let title: String
    var systemImage: String? = nil
    var disabled = false
    let action: () -> Void
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if let systemImage { Image(systemName: systemImage) }
                Text(title).multilineTextAlignment(.center)
            }
            .font(.body.weight(.semibold))
            .padding(.horizontal, 12).padding(.vertical, 12)
            .frame(maxWidth: .infinity, minHeight: 48)
            .foregroundStyle(disabled ? Color.secondary : FSDesign.actionForeground(dark: scheme == .dark))
            .background(disabled ? AnyShapeStyle(Color.secondary.opacity(0.12))
                        : AnyShapeStyle(FSDesign.accentGradient(dark: scheme == .dark)),
                        in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }
}

/// 状态徽标（方案 4.1）。
struct StatusChip: View {
    let status: String
    let title: String
    @Environment(\.colorScheme) private var scheme
    @Environment(\.colorSchemeContrast) private var contrast

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
            Image(systemName: icon)
            Text(title)
        }
        .font(.caption.weight(.medium))
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(
            FSDesign.chipBackground(status, dark: scheme == .dark),
            in: RoundedRectangle(cornerRadius: 6)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 6).strokeBorder(
                FSDesign.chipForeground(status, dark: scheme == .dark)
                    .opacity(contrast == .increased ? 0.8 : 0.3), lineWidth: 1)
        }
        .foregroundStyle(FSDesign.chipForeground(status, dark: scheme == .dark))
        .accessibilityElement(children: .combine)
    }
}
