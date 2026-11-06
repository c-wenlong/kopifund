import Foundation

/// G24: reading alert text as a reader, not a tokenizer: which lines are
/// boilerplate, which line tells you what the alert is, and a shape
/// signature stable enough to recognise "the same kind of email" later.
public enum AlertText {

    /// Mail client chrome and legal footers that carry no signal. A line
    /// matching any of these is dimmed in the reader and skipped when a
    /// row needs a title.
    private static let boilerplatePatterns: [NSRegularExpression] = [
        #"problems viewing"#, #"display images"#, #"view (in|this) (browser|email)"#,
        #"unsubscribe"#, #"do not (reply|respond)"#, #"auto-?generated"#,
        #"confidentiality"#, #"disclaimer"#, #"all rights reserved"#,
        #"if you did not (make|authorise|authorize)"#, #"please call"#, #"hotline"#,
        #"this e?mail (is|was) sent"#,
    ].map { try! NSRegularExpression(pattern: $0, options: .caseInsensitive) }

    public static func isBoilerplate(_ line: String) -> Bool {
        let ns = line as NSString
        return boilerplatePatterns.contains {
            $0.firstMatch(in: line, range: NSRange(location: 0, length: ns.length)) != nil
        }
    }

    /// Subject lines every bank alert shares. On their own they say nothing,
    /// so a row title keeps looking past them.
    private static let genericSubjects = try! NSRegularExpression(
        pattern: #"^(transaction alerts?|alerts?|notification|e?mail|sms)[.! ]*$"#,
        options: .caseInsensitive)

    /// The line a list row should lead with: the first that is neither
    /// boilerplate nor a generic subject, else the first non-empty line.
    /// Leading "Subject:" / "Fwd:" decorations are stripped.
    public static func meaningfulLine(in text: String) -> String {
        let lines = text.components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
        let content = lines.filter { $0.range(of: #"[A-Za-z]{3}"#, options: .regularExpression) != nil }
        func clean(_ s: String) -> String {
            s.replacingOccurrences(of: #"^(subject|fwd?|re):\s*"#, with: "",
                                   options: [.regularExpression, .caseInsensitive])
        }
        // non-Latin alerts can hold no ASCII letters at all; the raw first
        // line is a better title than nothing
        let pick = content.first { !isBoilerplate($0) && !genericSubjects.matches(in: clean($0)) }
            ?? content.first ?? lines.first { !$0.isEmpty } ?? ""
        let line = clean(pick)
        return line.count > 90 ? String(line.prefix(90)).trimmingCharacters(in: .whitespaces) + "…" : line
    }

    /// The title a list row should lead with: the detector's confident
    /// payee guess says who the money went to ("GOLDEN WOK KITCHEN"), which
    /// reads better than "Transaction Ref: PLPE…" when every alert in the
    /// queue shares that same subject line. Runs only the payee scan, but
    /// keep it off the render path anyway.
    public static func title(in text: String, tokens: [Token]) -> String {
        title(in: text, tokens: tokens,
              candidates: Candidates.payeeCandidates(in: text, tokens: tokens))
    }

    /// The same title for a caller that already ran `Candidates.detect`:
    /// the reader's header needs every field's verdict, so it reuses its
    /// own scan instead of paying for a second one.
    public static func title(in text: String, tokens: [Token],
                             candidates: [Candidate]) -> String {
        if let p = candidates.filter({ $0.field == .payee && $0.confident })
            .min(by: { $0.rank < $1.rank }),
           let lo = p.tokenIndexes.min(), let hi = p.tokenIndexes.max() {
            let span = String(text[tokens[lo].range.lowerBound..<tokens[hi].range.upperBound])
                .trimmingCharacters(in: .whitespaces)
            if !span.isEmpty { return span }
        }
        return meaningfulLine(in: text)
    }

    /// Token indexes that sit inside boilerplate lines, so the builder can
    /// render them quieter instead of offering them equal weight. One pass:
    /// the line pointer walks forward with the tokens and each line is judged
    /// once, where finding each token's line on its own made a single-line
    /// mail an O(n²) scan for the nearest newline.
    public static func boilerplateTokenIndexes(in text: String, tokens: [Token]) -> Set<Int> {
        var out = Set<Int>()
        var lineStart = text.startIndex
        var lineEnd = text[text.startIndex...].firstIndex(of: "\n") ?? text.endIndex
        var boilerplate = false
        var judged = false
        for (i, t) in tokens.enumerated() where t.kind != .whitespace {
            // a token never contains a newline, so starting at or past the
            // current line's end means the token sits on a later line
            while t.range.lowerBound >= lineEnd {
                lineStart = text.index(after: lineEnd)
                lineEnd = text[lineStart...].firstIndex(of: "\n") ?? text.endIndex
                judged = false
            }
            if !judged {
                boilerplate = isBoilerplate(String(text[lineStart..<lineEnd]))
                judged = true
            }
            if boilerplate { out.insert(i) }
        }
        return out
    }
}

/// "Always ignore alerts like this" works without a sender field: an alert's
/// shape (its head's words and punctuation with every number folded to `#`)
/// is stable across repeats of the same automated mail (deploy failures,
/// sign-in notices) while a different template diverges within a few tokens.
/// The signature covers only the head: bodies vary more than the opening.
public enum AlertShape {
    private static let headTokens = 60

    public static func signature(of text: String) -> String {
        let tokens = Tokenizer.tokenize(text).prefix(headTokens)
        return tokens.map { t -> String in
            switch t.kind {
            case .number: return "#"
            // digits inside a word are usually the changing part ("AB12",
            // "BILL-003"), so they fold the same way a bare number does
            case .word: return String(t.text.lowercased().map { $0.isNumber ? "#" : $0 })
            case .punct: return t.text
            case .whitespace: return " "
            }
        }.joined()
    }
}

private extension NSRegularExpression {
    func matches(in s: String) -> Bool {
        firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) != nil
    }
}
