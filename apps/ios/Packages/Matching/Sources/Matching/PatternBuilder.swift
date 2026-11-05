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
    /// capture is missing, a field other than ignore is tagged twice (two
    /// captures of one name make a regex the matcher cannot read), or a date
    /// span parses under no known format (an unreadable date cannot
    /// silently become fixed text: the next alert's date would never match).
    public static func segments(in text: String, tokens: [Token], spans: [Span]) throws -> [AlertPattern.Segment] {
        for f in Field.allCases where f != .ignore && spans.filter({ $0.field == f }).count > 1 {
            throw BuildError.duplicateField(f == .account ? "card" : f.rawValue)
        }
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
                segments.append(try segment(for: s, in: text, tokens: tokens))
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

    private static func segment(for span: Span, in text: String, tokens: [Token]) throws -> AlertPattern.Segment {
        switch span.field {
        case .amount: return .amount
        case .payee: return .payee
        case .account: return .accountHint
        case .ignore: return .ignore
        case .date:
            let raw = spanText(in: text, tokens: tokens, span: span).trimmingCharacters(in: .whitespaces)
            guard let f = Dates.detectFormat(raw) else { throw BuildError.unreadableDate(raw) }
            return .date(f)
        }
    }

    /// Long literals on the free edges shed most of themselves: the whole
    /// words within ~24 chars of a capture keep their anchor job, the rest
    /// becomes `ignore`, so a changed footer or greeting does not break the
    /// pattern.
    private static func shedLooseEdges(_ segments: [AlertPattern.Segment]) -> [AlertPattern.Segment] {
        var out = segments
        if case .literal(let s) = out.first, s.count > edgeAnchor {
            out[0] = .literal(keptEdge(of: s, atEnd: true))
            out.insert(.ignore, at: 0)
        }
        if case .literal(let s) = out.last, s.count > edgeAnchor {
            out[out.count - 1] = .literal(keptEdge(of: s, atEnd: false))
            out.append(.ignore)
        }
        return out
    }

    /// Audit: the part of an edge literal that stays an anchor, cut at a word
    /// boundary. Cutting by character count kept "ct" of "Oct" as a fixed
    /// word, so the pattern died the next month. Whole words are kept from
    /// the capture's side (`atEnd`: the literal's tail, before the first
    /// capture; otherwise its head, after the last) while they fit in
    /// `edgeAnchor` characters; a word longer than that is kept whole. A
    /// month name left at the cut lost the number beside it that made it a
    /// date, so it would anchor the sample's month: it goes too.
    static func keptEdge(of s: String, atEnd: Bool) -> String {
        // the start index of every word, in order
        var starts: [String.Index] = []
        var i = s.startIndex
        while i < s.endIndex {
            if !s[i].isWhitespace, i == s.startIndex || s[s.index(before: i)].isWhitespace {
                starts.append(i)
            }
            i = s.index(after: i)
        }
        func wordEnd(_ start: String.Index) -> String.Index {
            s[start...].firstIndex(where: \.isWhitespace) ?? s.endIndex
        }
        func isMonth(_ start: String.Index) -> Bool {
            LiteralPattern.isMonthName(String(s[start..<wordEnd(start)]))
        }
        guard !starts.isEmpty else { return s }
        if atEnd {
            var k = starts.count - 1
            while k > 0, s.distance(from: starts[k - 1], to: s.endIndex) <= edgeAnchor { k -= 1 }
            if k < starts.count - 1, isMonth(starts[k]) { k += 1 }
            return String(s[starts[k]...])
        } else {
            var k = 0
            while k + 1 < starts.count,
                  s.distance(from: s.startIndex, to: wordEnd(starts[k + 1])) <= edgeAnchor { k += 1 }
            if k > 0, isMonth(starts[k]) { k -= 1 }
            return String(s[..<wordEnd(starts[k])])
        }
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
