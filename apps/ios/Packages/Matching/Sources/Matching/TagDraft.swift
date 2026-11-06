import Foundation

/// G24: the pattern builder's tags while the owner works on an alert, kept
/// apart from its view so every tap, stroke and pre-tag is a tested value
/// change. `tags` are the fields the pattern will capture; `suggestions`
/// are the dashed guesses one tap accepts.
public struct TagDraft: Equatable, Sendable {
    public typealias Field = PatternBuilder.Field

    public var tags: [Int: Field]
    public var suggestions: [Int: Field]

    public init(tags: [Int: Field] = [:], suggestions: [Int: Field] = [:]) {
        self.tags = tags
        self.suggestions = suggestions
    }

    /// The fields a pattern needs, in the order the builder asks for them.
    public static let required: [Field] = [.amount, .payee, .date]

    /// On open the confident guesses land as tags and the rest stay dashed.
    /// Every alternative of a tagged field remains a suggestion so a wrong
    /// guess is always one tap from being right.
    public static func detected(in text: String, tokens: [Token]) -> TagDraft {
        var d = TagDraft()
        let cands = Candidates.detect(in: text, tokens: tokens)
        // one confident guess per field becomes a pre-tag, ranked so "to
        // MERCHANT" beats "from BANK"; tokens an earlier candidate already
        // claimed stay with it ("ACME USD" must not eat "USD 4.80")
        for field in [Field.amount, .payee, .date, .account] {
            if let c = cands.filter({ $0.field == field && $0.confident })
                .min(by: { $0.rank < $1.rank }) {
                let before = d.tags
                for i in c.tokenIndexes where d.tags[i] == nil { d.tags[i] = c.field }
                // a card printed right against the payee cannot border it;
                // that guess stays a dashed suggestion, not a broken pattern
                if field == .account, d.cardCannotBorder(in: text, tokens: tokens) { d.tags = before }
            }
        }
        for c in cands {
            for i in c.tokenIndexes where d.tags[i] == nil && d.suggestions[i] == nil {
                d.suggestions[i] = c.field
            }
        }
        return d
    }

    /// The same build Save runs: only the card-beside-a-wildcard refusal
    /// undoes the card's pre-tag.
    private func cardCannotBorder(in text: String, tokens: [Token]) -> Bool {
        do {
            _ = try PatternBuilder.segments(in: text, tokens: tokens, spans: spans(tokens: tokens))
            return false
        } catch PatternBuilder.BuildError.wildcardBesideCard {
            return true
        } catch {
            return false
        }
    }

    public func spans(tokens: [Token]) -> [PatternBuilder.Span] {
        PatternBuilder.spans(from: tags, tokens: tokens)
    }

    /// A tap on a bare word with `armed` the selected field chip.
    public mutating func tap(_ i: Int, armed: Field) {
        if tags[i] == armed {
            tags[i] = nil
        } else if tags[i] == nil, let sug = suggestions[i] {
            // a dashed guess accepts with one tap whatever field is armed
            tags[i] = sug
            suggestions[i] = nil
        } else {
            tags[i] = armed
            suggestions[i] = nil
        }
    }

    /// A tap on a tagged run: the armed field clears it, any other field
    /// re-tags the whole run.
    public mutating func tapSpan(_ indexes: [Int], field: Field, armed: Field) {
        if field == armed { for i in indexes { tags[i] = nil } }
        else { for i in indexes { tags[i] = armed; suggestions[i] = nil } }
    }

    /// A stroke ends: every non-whitespace token in `range` takes the armed
    /// field on top of `base`, the tags the stroke started from.
    public mutating func commitStroke(_ range: ClosedRange<Int>, armed: Field,
                                      onto base: [Int: Field], tokens: [Token]) {
        var next = base
        for i in range where tokens[i].kind != .whitespace {
            next[i] = armed
            suggestions[i] = nil
        }
        tags = next
    }

    /// The first required field no span covers yet.
    public static func nextMissing(in spans: [PatternBuilder.Span]) -> Field? {
        let have = Set(spans.map(\.field))
        return required.first { !have.contains($0) }
    }

    // MARK: - atoms: what the builder's document renders

    /// A contiguous tagged run renders as one unit; whitespace either keeps
    /// a line (gap) or breaks it (newline); everything else is a tappable word.
    public enum Atom: Identifiable, Equatable, Sendable {
        case token(Int)
        case span(Field, String, [Int])
        case gap(Int)
        case newline(Int, Int)
        public var id: String {
            switch self {
            case .token(let i): return "t\(i)"
            case .gap(let i): return "g\(i)"
            case .newline(let i, let k): return "n\(i)-\(k)"
            case .span(_, _, let idx): return "s\(idx.first ?? -1)"
            }
        }
    }

    public static func atoms(in text: String, tokens: [Token],
                             spans: [PatternBuilder.Span]) -> [Atom] {
        let byFirst = Dictionary(uniqueKeysWithValues: spans.map { ($0.firstIndex, $0) })
        // a span's whitespace is inside its rendered text, so the whole range
        // must be skipped, not just the tagged word tokens
        var covered = Set<Int>()
        for s in spans { covered.formUnion(s.firstIndex...s.lastIndex) }
        var out: [Atom] = []
        for i in tokens.indices {
            if let s = byFirst[i] {
                out.append(.span(s.field, PatternBuilder.spanText(in: text, tokens: tokens, span: s),
                                 s.tokenIndexes))
            } else if covered.contains(i) {
                continue
            } else if tokens[i].kind == .whitespace {
                let breaks = tokens[i].text.filter { $0 == "\n" }.count
                if breaks == 0 { out.append(.gap(i)) }
                else { for k in 0..<breaks { out.append(.newline(i, k)) } }
            } else {
                out.append(.token(i))
            }
        }
        return out
    }

    /// The token range of the atom holding token `t`: one index for a bare
    /// word, the whole span for a merged run.
    public static func tokenRange(containing t: Int, in atoms: [Atom]) -> ClosedRange<Int>? {
        for atom in atoms {
            switch atom {
            case .token(let i) where i == t: return i...i
            case .span(_, _, let idx):
                if let lo = idx.first, let hi = idx.last, idx.contains(t) { return lo...hi }
            default: continue
            }
        }
        return nil
    }
}

extension PatternBuilder.BuildError {
    /// What the builder says when a tagging cannot become a pattern.
    public var message: String {
        switch self {
        case .missingField(let f): return "tag an \(f) span"
        case .duplicateField(let f): return "only one \(f) span per pattern"
        case .unreadableDate(let raw): return #"date "\#(raw)" is not a format the app reads"#
        case .wildcardBesidePayee: return "the payee needs a fixed word right beside it"
        case .wildcardBesideCard: return "the card needs a fixed word right beside it"
        }
    }
}
