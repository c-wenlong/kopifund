import SwiftUI
import Matching

/// How the builder names and colours each field, in its chips, its
/// document's tags and its dashed guesses.
extension PatternBuilder.Field {
    var label: String {
        switch self {
        case .amount: return "Amount"
        case .payee: return "Payee"
        case .date: return "Date"
        case .account: return "Card"
        case .ignore: return "Ignore"
        }
    }

    var color: Color {
        switch self {
        case .amount: return .green
        case .payee: return .blue
        case .date: return .purple
        case .account: return .orange
        case .ignore: return .gray
        }
    }

    /// The fill behind a tagged span, or a word inside a stroke.
    var tint: Color { color.opacity(0.25) }
}
