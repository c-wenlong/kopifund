import SwiftUI
import Storage
import Matching

/// One money row, the shape the Inbox and the Kopifund tab's Transactions
/// share: who and how much on top, the details and a coloured note under it.
struct LedgerRow: View {
    let title: String
    let amount: String
    let caption: String
    /// A word on the right of the caption line (a state, a flag), if any.
    var note: (text: String, color: Color)? = nil
    /// The Transactions browser keeps each line to one; the Inbox wraps.
    var lineLimit: Int? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title).font(.body).lineLimit(lineLimit)
                Spacer()
                Text(amount).font(.body.monospacedDigit())
            }
            HStack {
                Text(caption).font(.caption).foregroundStyle(.secondary).lineLimit(lineLimit)
                Spacer()
                if let note {
                    Text(note.text).font(.caption).foregroundStyle(note.color)
                }
            }
        }
    }
}

/// The two ways out of Unmatched that teach nothing, worded once for the
/// list's swipe and the alert's own page.
enum Triage {
    static let dismissed = "Alert dismissed"

    /// "Always ignore": remember the alert's shape so the next one of its
    /// template drops at intake; answers the toast to show.
    static func ignoreShape(of text: String) -> String {
        Suppression.add(AlertShape.signature(of: text))
        return "Ignored. Undo it under Patterns"
    }
}
