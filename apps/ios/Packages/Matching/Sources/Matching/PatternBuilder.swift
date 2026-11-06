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

    /// The most characters an anchor keeps beside a capture. A single word
    /// longer than this is still kept whole.
    private static let edgeAnchor = 24

    /// The most words that do not change between alerts an anchor keeps
    /// beside a capture: "Date & Time:", "card ending 3141 to", "You
    /// charged". Two pin the field down; a third word away from it is
    /// usually the part of the sentence that changes ("Scan & Pay Transfer
    /// dated" vs "Transfer dated").
    private static let anchorWords = 2

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

        segments = keepAnchors(segments)
        try checkBorders(segments)

        guard segments.contains(where: { $0 == .amount }) else { throw BuildError.missingField("amount") }
        guard segments.contains(where: { $0 == .payee }) else { throw BuildError.missingField("payee") }
        return segments
    }

    /// The payee and the card need a fixed word right beside them; see
    /// `segments`. Also run on segments whose anchors the user edited.
    static func checkBorders(_ segments: [AlertPattern.Segment]) throws {
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
    }

    /// Whitespace runs as a literal keeps them: one space, or a line break.
    static func collapsed(_ s: String) -> String {
        s.replacingOccurrences(of: #"\s*[\r\n]+\s*"#, with: "\n", options: .regularExpression)
            .replacingOccurrences(of: #"[^\S\n]+"#, with: " ", options: .regularExpression)
    }

    private static func flush(_ buffer: inout String, into segments: inout [AlertPattern.Segment]) {
        guard !buffer.isEmpty else { return }
        // a newline the sample printed is part of the template: keep it so
        // the regex can require the same break, while ordinary space runs
        // stay a single anchor space
        //
        // A blank line straight after a capture is kept as "\n\n" for now:
        // after the payee it says the name is a paragraph of its own and may
        // wrap (`keepAnchors` drops it everywhere else).
        let blank = buffer.range(of: #"\A[^\S\n]*\r?\n[^\S\n]*\r?\n"#, options: .regularExpression) != nil
        var lined = buffer.replacingOccurrences(of: #"\s*[\r\n]+\s*"#, with: "\n",
                                                options: .regularExpression)
        if blank { lined = "\n" + lined }
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

    /// G24: a field is found by the stable words right around it, not by
    /// every word of the alert. The literal before a capture keeps only its
    /// nearest words (`anchorBefore`) and the literal after it only what
    /// bounds it (`anchorAfter`); the rest becomes `ignore`, so a changed
    /// greeting, a reworded sentence, a new footer or an extra line between
    /// the fields no longer breaks the pattern. A literal short enough that
    /// both anchors cover it stays whole. A literal beside an ignore span the
    /// user tagged stays as it is: the user drew that border.
    ///
    /// After the last capture only its own line counts: a payee or card
    /// keeps the words that end it, an amount or a date (which read their
    /// own end) one word, and a capture that ends its line just the line
    /// break. The rest of the alert, its footer, is let go.
    static func keepAnchors(_ segments: [AlertPattern.Segment]) -> [AlertPattern.Segment] {
        func isCapture(_ s: AlertPattern.Segment?) -> Bool {
            guard let s else { return false }
            switch s {
            case .literal, .ignore: return false
            default: return true
            }
        }
        var out: [AlertPattern.Segment] = []
        for (i, seg) in segments.enumerated() {
            guard case .literal(let s) = seg else { out.append(seg); continue }
            let prev = i > 0 ? segments[i - 1] : nil
            let next = i + 1 < segments.count ? segments[i + 1] : nil
            // a blank line counts only right after the payee
            let text = prev == .payee ? s : collapseBlank(s)
            switch (prev, next) {
            case (nil, let n) where isCapture(n):
                let tail = anchorBefore(text)
                if tail.count < text.count { out.append(.ignore) }
                out.append(.literal(tail))
            case (let p?, nil) where isCapture(p):
                // an amount or a date reads its own end, so one word after
                // it is enough to tell this alert from a look-alike
                let head = anchorAfter(text, stableWords: p == .amount || isDate(p) ? 1 : anchorWords)
                if !head.isEmpty { out.append(.literal(head)) }
                if head.count < text.count { out.append(.ignore) }
            case (let p, let n) where isCapture(p) && isCapture(n):
                let head = anchorAfter(text)
                let tail = anchorBefore(text)
                if head.count + tail.count >= text.count {
                    out.append(.literal(text))
                } else {
                    out.append(.literal(head))
                    out.append(.ignore)
                    out.append(.literal(tail))
                }
            default:
                out.append(.literal(text))
            }
        }
        return out
    }

    private static func isDate(_ s: AlertPattern.Segment) -> Bool {
        if case .date = s { return true }
        return false
    }

    private static func collapseBlank(_ s: String) -> String {
        s.hasPrefix("\n\n") ? String(s.dropFirst()) : s
    }

    /// One whitespace-free word of a literal, by index range.
    private struct Word {
        var range: Range<String.Index>
        var text: Substring
    }

    private static func words(in s: Substring) -> [Word] {
        var out: [Word] = []
        var i = s.startIndex
        while i < s.endIndex {
            if s[i].isWhitespace { i = s.index(after: i); continue }
            let j = s[i...].firstIndex(where: \.isWhitespace) ?? s.endIndex
            out.append(Word(range: i..<j, text: s[i..<j]))
            i = j
        }
        return out
    }

    /// A word that reads the same in the next alert: letters, no digit, not
    /// a month name. Digits and dates fold to wildcards in the regex, and a
    /// mark on its own ("&", "-", "/") says little, so neither counts
    /// toward `anchorWords`.
    private static func isStable(_ w: Substring) -> Bool {
        !w.contains(where: \.isNumber) && w.contains(where: \.isLetter)
            && !LiteralPattern.isMonthName(String(w))
    }

    private static func endsClause(_ w: Substring) -> Bool {
        guard let c = w.last else { return false }
        return ".,;:!?".contains(c)
    }

    /// The end of a literal that stays in front of the next capture: the
    /// words on the capture's own line nearest it, at most `anchorWords`
    /// stable ones, at most `edgeAnchor` characters, never reaching back
    /// past the end of an earlier clause ("Dear Customer, you charged"
    /// keeps "you charged"). A capture that starts its line keeps the
    /// line before it instead, which is usually its label ("Amount:").
    /// A month name left at the cut lost the number that made it a date,
    /// so it goes too.
    static func anchorBefore(_ s: String) -> String {
        var lineEnd = s.endIndex
        var lineStart = s[..<lineEnd].lastIndex(of: "\n").map { s.index(after: $0) } ?? s.startIndex
        var ws = words(in: s[lineStart..<lineEnd])
        if ws.isEmpty {
            // the capture starts its line: anchor on the label line above
            guard let nl = s[..<lineEnd].lastIndex(of: "\n") else { return s }
            lineEnd = nl
            lineStart = s[..<lineEnd].lastIndex(of: "\n").map { s.index(after: $0) } ?? s.startIndex
            ws = words(in: s[lineStart..<lineEnd])
            if ws.isEmpty { return String(s[nl...]) }
        }
        var k = ws.count - 1
        var stable = isStable(ws[k].text) ? 1 : 0
        while k > 0 {
            let w = ws[k - 1]
            if endsClause(w.text) || stable >= anchorWords
                || s.distance(from: w.range.lowerBound, to: lineEnd) > edgeAnchor { break }
            k -= 1
            if isStable(w.text) { stable += 1 }
        }
        if k < ws.count - 1, LiteralPattern.isMonthName(String(ws[k].text)) { k += 1 }
        return String(s[ws[k].range.lowerBound...])
    }

    /// The start of a literal that stays after a capture: the words on the
    /// capture's line that follow it, by the same limits as `anchorBefore`,
    /// up to and including a word that ends a clause. A capture that ends
    /// its line keeps only the line break (and a blank line after it, which
    /// lets a payee wrap): the next line is a new field or a footer, and
    /// either may change.
    static func anchorAfter(_ s: String, stableWords: Int = anchorWords) -> String {
        let lineEnd = s.firstIndex(of: "\n") ?? s.endIndex
        let ws = words(in: s[s.startIndex..<lineEnd])
        guard !ws.isEmpty else {
            guard lineEnd < s.endIndex else { return s }
            let rest = s[lineEnd...]
            return String(rest.prefix(while: { $0 == "\n" }))
        }
        var k = 0
        var stable = isStable(ws[0].text) ? 1 : 0
        // a mark alone (". Thank you") may be punctuation inside a name
        // ("ST. CLEMENT"), so the words after it stay too
        if !endsClause(ws[0].text) || !ws[0].text.contains(where: \.isLetter) {
            while k + 1 < ws.count {
                let w = ws[k + 1]
                if stable >= stableWords
                    || s.distance(from: s.startIndex, to: w.range.upperBound) > edgeAnchor { break }
                k += 1
                if isStable(w.text) { stable += 1 }
                if endsClause(w.text) { break }
            }
        }
        if k > 0, LiteralPattern.isMonthName(String(ws[k].text)) { k -= 1 }
        return String(s[..<ws[k].range.upperBound])
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
