import SwiftUI

/// Design tokens from the Mise Mobile design handoff (final-intent palette).
///
/// The handoff's brand accent deliberately shifts hue between modes — warm
/// terracotta in light, teal-green in dark — so every token is a dynamic
/// color, not a lightness-adjusted pair. Typography follows the handoff's
/// serif-display / sans-body split using the system New York and San
/// Francisco designs; bundling the Newsreader/Archivo webfonts is a later
/// asset decision (see ios/README.md).
enum MiseDesign {
    // Brand accent ("terra"): terracotta -> teal-green.
    static let terra = dynamic(light: 0xB3552E, dark: 0x23B487)
    static let terraTint = dynamic(light: 0xF6ECE4, dark: 0x14271F)

    // Semantic families: tinted pill background + matching text color.
    static let ok = dynamic(light: 0x566B3F, dark: 0x9CC178)
    static let okBg = dynamic(light: 0xE9EDDA, dark: 0x20271A)
    static let honey = dynamic(light: 0x976416, dark: 0xD8A857)
    static let honeyBg = dynamic(light: 0xF7EAD0, dark: 0x2B2413)
    static let neutral = dynamic(light: 0x756857, dark: 0xABA9A3)
    static let neutralBg = dynamic(light: 0xEFE9DF, dark: 0x242424)
    static let clay = dynamic(light: 0x9C3A26, dark: 0xD98A78)
    static let clayBg = dynamic(light: 0xF5E0D8, dark: 0x2E1A18)

    // Surfaces and text. System semantic colors stay the default; these are
    // for the handoff's warm-paper cards where the exact tone matters.
    static let surfaceSunk = dynamic(light: 0xF4EFE7, dark: 0x161616)
    static let inkFaint = dynamic(light: 0x968A7A, dark: 0x8C8A85)

    /// Favorited-heart fill from the handoff lightbox (same in both modes).
    static let heart = Color(red: 0xE0 / 255.0, green: 0x65 / 255.0, blue: 0x4A / 255.0)

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            UIColor(rgb: traits.userInterfaceStyle == .dark ? dark : light)
        })
    }
}

private extension UIColor {
    convenience init(rgb: UInt32) {
        self.init(
            red: CGFloat((rgb >> 16) & 0xFF) / 255.0,
            green: CGFloat((rgb >> 8) & 0xFF) / 255.0,
            blue: CGFloat(rgb & 0xFF) / 255.0,
            alpha: 1
        )
    }
}

/// One semantic pill family: text + tinted background.
struct StatusTone: Sendable {
    let foreground: Color
    let background: Color

    static let ok = StatusTone(foreground: MiseDesign.ok, background: MiseDesign.okBg)
    static let honey = StatusTone(foreground: MiseDesign.honey, background: MiseDesign.honeyBg)
    static let neutral = StatusTone(
        foreground: MiseDesign.neutral, background: MiseDesign.neutralBg
    )
    static let clay = StatusTone(foreground: MiseDesign.clay, background: MiseDesign.clayBg)
}

/// The handoff's ubiquitous status pill: 11pt/700, fully-round, tinted.
/// Never the only carrier of state — pair it with text or an icon.
struct StatusPill: View {
    let label: String
    let tone: StatusTone

    var body: some View {
        Text(label)
            .font(.system(size: 11, weight: .bold))
            .padding(.horizontal, 11)
            .padding(.vertical, 4)
            .foregroundStyle(tone.foreground)
            .background(tone.background, in: Capsule())
            .accessibilityLabel(label)
    }
}

extension GalleryDeliveryState {
    var clientDisplayName: String {
        switch self {
        case .draft: "Not published"
        case .proofing: "Proofing"
        case .expiring: "Expiring soon"
        case .delivered: "Delivered"
        default: ownerDisplayName
        }
    }

    var tone: StatusTone {
        switch self {
        case .proofing: .honey
        case .expiring: .clay
        case .delivered: .ok
        default: .neutral
        }
    }
}

extension ProposalStatus {
    var tone: StatusTone {
        switch self {
        case .accepted: .ok
        case .declined: .clay
        case .sent, .viewed: .honey
        default: .neutral
        }
    }
}

extension ContractStatus {
    var tone: StatusTone {
        switch self {
        case .signed: .ok
        case .sent, .viewed: .honey
        default: .neutral
        }
    }
}

extension InvoiceStatus {
    var tone: StatusTone {
        switch self {
        case .paid: .ok
        case .sent, .viewed, .depositPaid: .honey
        default: .neutral
        }
    }
}

extension BookingStatus {
    var tone: StatusTone {
        switch self {
        case .cancelled: .clay
        default: .ok
        }
    }
}

extension View {
    /// Handoff display type: serif for headlines/titles, sans for everything else.
    func miseDisplayFont(_ style: Font.TextStyle = .title2) -> some View {
        font(.system(style, design: .serif).weight(.medium))
    }
}
