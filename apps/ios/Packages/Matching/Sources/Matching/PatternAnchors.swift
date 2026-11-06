import Foundation

/// G24: the text right before and right after each field, the "lookaround"
/// a pattern finds the field by. The builder picks it from the sample
/// (`PatternBuilder.keepAnchors`); More options shows it and lets the owner
/// change it, for a template whose nearest words are not the stable ones.
public enum PatternAnchors {

    public enum Side: String, Sendable { case before, after }

    /// One field's anchors as the pattern holds them. Nil means nothing
    /// fixed borders the field on that side: the alert's edge or an ignored
    /// stretch.
    public struct Anchor: Equatable, Sendable {
        public var field: PatternBuilder.Field
        public var before: String?
        public var after: String?
    }

    /// One edit the owner made: the text that should border `field` on
    /// `side`. An empty text lets that side go.
    public struct Edit: Equatable, Sendable {
        public var field: PatternBuilder.Field
        public var side: Side
        public var text: String
        public init(field: PatternBuilder.Field, side: Side, text: String) {
            self.field = field
            self.side = side
            self.text = text
        }
    }

    /// The mark a line break shows as in the editor, so "To:" then a break
    /// reads "To: ⏎" on one line instead of a blank second line.
    public static let lineBreak = "⏎"

    /// An anchor as the editor shows it: every line break as `lineBreak`.
    public static func display(_ anchor: String?) -> String {
        guard let anchor else { return "" }
        return anchor.replacingOccurrences(of: "\n", with: " \(lineBreak) ")
            .trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: "  ", with: " ")
    }

    /// The editor's text back as an anchor: each `lineBreak` a line break,
    /// with the spaces around it dropped.
    public static func parse(_ shown: String) -> String {
        shown.replacingOccurrences(of: #"[^\S\n]*\#(lineBreak)[^\S\n]*"#, with: "\n",
                                   options: .regularExpression)
    }

    /// Every field in the pattern's order with the literal on each side.
    public static func anchors(in segments: [AlertPattern.Segment]) -> [Anchor] {
        segments.indices.compactMap { i in
            guard let f = field(of: segments[i]) else { return nil }
            func literal(_ j: Int) -> String? {
                guard segments.indices.contains(j), case .literal(let s) = segments[j] else { return nil }
                return s
            }
            return Anchor(field: f, before: literal(i - 1), after: literal(i + 1))
        }
    }

    /// The segments with each edit applied in order, checked the way a
    /// fresh build is: the payee and the card still need a fixed word
    /// beside them. An edit for a field the pattern lacks is skipped.
    public static func applying(_ edits: [Edit],
                                to segments: [AlertPattern.Segment]) throws -> [AlertPattern.Segment] {
        var out = segments
        for e in edits {
            guard let i = out.firstIndex(where: { field(of: $0) == e.field }) else { continue }
            let text = PatternBuilder.collapsed(e.text)
            let j = e.side == .before ? i - 1 : i + 1
            if out.indices.contains(j), case .literal = out[j] {
                if text.isEmpty {
                    // the side lets go: nothing fixed borders it any more
                    out[j] = .ignore
                } else {
                    out[j] = .literal(text)
                }
            } else if !text.isEmpty {
                out.insert(.literal(text), at: e.side == .before ? i : i + 1)
            }
            // two ignores side by side read as one
            out = out.enumerated().filter { k, s in
                !(s == .ignore && k > 0 && out[k - 1] == .ignore)
            }.map(\.1)
        }
        try PatternBuilder.checkBorders(out)
        return out
    }

    private static func field(of s: AlertPattern.Segment) -> PatternBuilder.Field? {
        switch s {
        case .amount: return .amount
        case .payee: return .payee
        case .date: return .date
        case .accountHint: return .account
        case .literal, .ignore: return nil
        }
    }
}
