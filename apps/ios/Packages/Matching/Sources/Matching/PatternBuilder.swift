import Foundation

/// G24: turns the user's taps on a real alert into an `AlertPattern`. A tap
/// assigns a field to a token; adjacent tokens tagged the same merge into one
/// span, each span becomes a typed capture, and the text between captures
/// becomes literal anchors.
public struct PatternBuilder {

    /// The fields a span can be tagged with.
    public enum Field: String, CaseIterable, Sendable {
        case amount, payee, date, account, ignore
    }

    /// A contiguous run of tokens tagged with one field.
    public struct Span: Equatable, Sendable {
        public var field: Field
        public var tokenIndexes: [Int]
        public var firstIndex: Int { tokenIndexes.first! }
        public var lastIndex: Int { tokenIndexes.last! }
    }

    /// Merge the tag map (`tokenIndex -> field`) into spans. Two tagged tokens
    /// with only whitespace between them belong to one span, so "FOO PTE"
    /// tagged payee,payee is one capture; anything else breaks the run.
    public static func spans(from tags: [Int: Field], tokens: [Token]) -> [Span] {
        var spans: [Span] = []
        for i in tokens.indices.sorted() where tags[i] != nil {
            let field = tags[i]!
            if var last = spans.last, last.field == field,
               tokens[(last.lastIndex + 1)..<i].allSatisfy({ $0.kind == .whitespace }) {
                last.tokenIndexes.append(i)
                spans[spans.count - 1] = last
            } else {
                spans.append(Span(field: field, tokenIndexes: [i]))
            }
        }
        return spans
    }

    /// How many characters at each free end of the text stay an anchor before
    /// the rest is shed as a wildcard: alerts often carry a changing prefix
    /// ("You have") or a changing suffix (reference numbers), but the words
    /// hugging the captures ("charged", "at") are what makes the alert this
    /// alert.
    private static let edgeAnchor = 24

    /// Build the segment list from tagged spans. Throws when a required
    /// capture is missing or a date span parses under no known format.
    public static func segments(in text: String, tokens: [Token], spans: [Span]) throws -> [AlertPattern.Segment] {
        var covered = Set<Int>()
        for s in spans {
            for i in s.firstIndex...s.lastIndex { covered.insert(i) }
        }

        var segments: [AlertPattern.Segment] = []
        var literalBuffer = ""
        let spanByFirst = Dictionary(uniqueKeysWithValues: spans.map { ($0.firstIndex, $0) })
        var i = 0
        while i < tokens.count {
            if let s = spanByFirst[i] {
                flush(&literalBuffer, into: &segments)
                segments.append(segment(for: s, in: text, tokens: tokens))
                i = s.lastIndex + 1
                continue
            }
            if !covered.contains(i) { literalBuffer += tokens[i].text }
            i += 1
        }
        flush(&literalBuffer, into: &segments)

        segments = shedLooseEdges(segments)

        // A wildcard beside a line capture cannot draw the border the tags
        // meant: a lazy capture shrinks to one letter while the wildcard
        // absorbs its words, and this blurs the payee and the card alike.
        // Whatever sits right next to them must be a fixed word, so an
        // ignore, a neighbouring capture or a piece that folds (digits, a
        // date run) there is refused. A required line break in a literal
        // already bounds the capture to its line, so whitespace runs holding
        // a newline count as fixed text for this check.
        for (i, seg) in segments.enumerated() where seg == .payee || seg == .accountHint {
            let err: BuildError = seg == .payee ? .wildcardBesidePayee : .wildcardBesideCard
            var j = i + 1
            while j < segments.count, case .literal(let s) = segments[j],
                  s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !s.contains("\n") { j += 1 }
            if j < segments.count {
                switch segments[j] {
                case .ignore, .accountHint, .payee:
                    throw err
                case .literal(let s) where LiteralPattern.firstPieceIsWild(s):
                    throw err
                default: break
                }
            }
            // the same blur works backwards: an ignore or a neighbouring
            // capture right before it can match empty there and let the
            // capture swallow the words meant for the wildcard. Folded
            // pieces on the left stay safe since they cannot cross spaces.
            var k = i - 1
            while k >= 0, case .literal(let s) = segments[k],
                  s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !s.contains("\n") { k -= 1 }
            if k >= 0 {
                switch segments[k] {
                case .ignore, .accountHint, .payee:
                    throw err
                default: break
                }
            }
        }

        guard segments.contains(where: { $0 == .amount }) else { throw BuildError.missingField("amount") }
        guard segments.contains(where: { $0 == .payee }) else { throw BuildError.missingField("payee") }
        return segments
    }

    private static func flush(_ buffer: inout String, into segments: inout [AlertPattern.Segment]) {
        guard !buffer.isEmpty else { return }
        // a newline the sample printed is part of the template: keep it so
        // the regex can require the same break, while ordinary space runs
        // stay a single anchor space
        let lined = buffer.replacingOccurrences(of: #"\s*[\r\n]+\s*"#, with: "\n",
                                                options: .regularExpression)
        let collapsed = lined.replacingOccurrences(of: #"[^\S\n]+"#, with: " ",
                                                   options: .regularExpression)
        segments.append(.literal(collapsed))
        buffer = ""
    }

    private static func segment(for span: Span, in text: String, tokens: [Token]) -> AlertPattern.Segment {
        switch span.field {
        case .amount: return .amount
        case .payee: return .payee
        case .account: return .accountHint
        case .ignore: return .ignore
        case .date:
            let raw = spanText(in: text, tokens: tokens, span: span).trimmingCharacters(in: .whitespaces)
            // an unreadable date span cannot silently become fixed text: the
            // next alert with a different date would never match
            guard let f = Dates.detectFormat(raw) else { fatalError("unreachable: checked in segments(in:)") }
            return .date(f)
        }
    }

    /// Long literals on the free edges shed most of themselves: the ~24 chars
    /// nearest a capture keep their anchor job, the rest becomes `ignore`, so
    /// a changed footer or greeting does not break the pattern.
    private static func shedLooseEdges(_ segments: [AlertPattern.Segment]) -> [AlertPattern.Segment] {
        var out = segments
        if case .literal(let s) = out.first, s.count > edgeAnchor {
            out[0] = .literal(String(s.suffix(edgeAnchor)))
            out.insert(.ignore, at: 0)
        }
        if case .literal(let s) = out.last, s.count > edgeAnchor {
            out[out.count - 1] = .literal(String(s.prefix(edgeAnchor)))
            out.append(.ignore)
        }
        return out
    }

    public static func spanText(in text: String, tokens: [Token], span: Span) -> String {
        let r = tokens[span.firstIndex].range.lowerBound..<tokens[span.lastIndex].range.upperBound
        return String(text[r])
    }

    public enum BuildError: Error, Equatable {
        case missingField(String)
        case duplicateField(String)
        case unreadableDate(String)
        case wildcardBesidePayee
        case wildcardBesideCard
    }
}
