import Foundation

/// G24: a reusable pattern built by tapping the spans of a real alert. Fixed
/// text becomes literal anchors and the tagged spans become typed captures.
public struct AlertPattern: Codable, Equatable, Identifiable, Sendable {
    public var id: UUID
    public var name: String
    public var direction: Direction
    /// The YNAB account transactions built on this pattern post to.
    public var accountID: String
    public var defaultCategoryID: String?
    /// Optional matcher on the alert's `source` (the sender or automation that
    /// carried it), a case-insensitive "contains".
    public var sourceMatch: String?
    public var priority: Int
    /// Off by default: every match waits in the review queue first.
    public var autoApprove: Bool
    public var segments: [Segment]
    /// The alert text the pattern was built from, kept for the builder screen
    /// and for retesting when the pattern is edited.
    public var sampleText: String

    public init(id: UUID = UUID(), name: String, direction: Direction, accountID: String,
                defaultCategoryID: String? = nil, sourceMatch: String? = nil,
                priority: Int = 0, autoApprove: Bool = false,
                segments: [Segment], sampleText: String) {
        self.id = id
        self.name = name
        self.direction = direction
        self.accountID = accountID
        self.defaultCategoryID = defaultCategoryID
        self.sourceMatch = sourceMatch
        self.priority = priority
        self.autoApprove = autoApprove
        self.segments = segments
        self.sampleText = sampleText
    }

    public enum Direction: String, Codable, CaseIterable, Sendable {
        case outflow, inflow
    }

    public enum Segment: Codable, Equatable, Sendable {
        /// Anchoring text from the alert, whitespace runs collapsed. The
        /// pieces holding digits and any date-shaped run fold to wildcards
        /// when the regex is built, so a changing reference or date cannot
        /// break the anchor.
        case literal(String)
        /// Any text, non-greedy, between anchors: a span the user left as
        /// "ignore" or the loose edges a long literal shed.
        case ignore
        case amount
        case payee
        case date(DateFormat)
        /// An account or card number the alert prints: it must equal the
        /// sample's, which is what separates patterns that differ only by
        /// card. The capture itself is kept on the transaction as a hint.
        case accountHint
    }

    /// The date shapes the builder knows how to read. `regex` is the fragment
    /// a date segment inserts, `format` the DateFormatter shape that parses
    /// the capture.
    public enum DateFormat: String, Codable, CaseIterable, Sendable {
        case dmy4Slash       // 12/10/2026 and 9/2/2026
        case dmy2Slash       // 12/10/26
        case ddMmmYyyy       // 12 Oct 2026
        case ddMmmYy         // 12 Oct 26
        case ddMmm           // 12 Oct (year from receivedAt)
        case mmmDdYyyy       // Oct 12, 2026
        case dmy4Dash        // 12-10-2026
        case dmy2Dash        // 12-10-26
        case ymdDash         // 2026-10-12
        case dmSlash         // 12/10 (year from receivedAt)

        var regex: String {
            let y4 = #"\d{4}"#, y2 = #"\d{2}"#, d = #"\d{1,2}"#, mon = #"[A-Za-z]{3,9}"#
            switch self {
            case .dmy4Slash:             return #"\#(d)/\#(d)/\#(y4)"#
            case .dmy2Slash:             return #"\#(d)/\#(d)/\#(y2)"#
            case .ddMmmYyyy:             return #"\#(d)\s+\#(mon)\s+\#(y4)"#
            case .ddMmmYy:               return #"\#(d)\s+\#(mon)\s+\#(y2)"#
            case .ddMmm:                 return #"\#(d)\s+\#(mon)"#
            case .mmmDdYyyy:             return #"\#(mon)\s+\#(d),?\s+\#(y4)"#
            case .dmy4Dash:              return #"\#(d)-\#(d)-\#(y4)"#
            case .dmy2Dash:              return #"\#(d)-\#(d)-\#(y2)"#
            case .ymdDash:               return #"\#(y4)-\#(d)-\#(d)"#
            case .dmSlash:               return #"\#(d)/\#(d)"#
            }
        }

        var formatterFormats: [String] {
            switch self {
            case .dmy4Slash:             return ["d/M/yyyy", "dd/MM/yyyy"]
            case .dmy2Slash:             return ["d/M/yy", "dd/MM/yy"]
            case .ddMmmYyyy:             return ["d MMM yyyy", "d MMMM yyyy", "dd MMM yyyy"]
            case .ddMmmYy:               return ["d MMM yy", "d MMMM yy"]
            case .ddMmm:                 return ["d MMM", "d MMMM"]
            case .mmmDdYyyy:             return ["MMM d, yyyy", "MMMM d, yyyy", "MMM d yyyy"]
            case .dmy4Dash:              return ["d-M-yyyy", "dd-MM-yyyy"]
            case .dmy2Dash:              return ["d-M-yy", "dd-MM-yy"]
            case .ymdDash:               return ["yyyy-MM-dd"]
            case .dmSlash:               return ["d/M", "dd/MM"]
            }
        }
    }
}
