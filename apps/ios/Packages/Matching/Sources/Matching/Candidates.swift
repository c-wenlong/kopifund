import Foundation

/// G24: the machine guesses first. `detect` walks a real alert's tokens and
/// proposes the spans most likely to be the amount, the date and the payee,
/// so the builder's job is confirming or correcting instead of hunting
/// through a hundred words. `confident` marks the guess worth pre-tagging;
/// the rest stay dashed suggestions the user can tap to accept.
public struct Candidate: Equatable, Sendable {
    public var field: PatternBuilder.Field
    /// Indexes of the non-whitespace tokens the span covers. Adjacent tokens
    /// with only whitespace between them merge into one span when tagged.
    public var tokenIndexes: [Int]
    public var confident: Bool
    /// Lower wins: two confident payees ("from DBS ... to MERCHANT") rank by
    /// the anchor that introduced them, so the merchant beats the bank.
    public var rank: Int

    public init(field: PatternBuilder.Field, tokenIndexes: [Int], confident: Bool,
                rank: Int = 0) {
        self.field = field
        self.tokenIndexes = tokenIndexes
        self.confident = confident
        self.rank = rank
    }
}

public enum Candidates {

    public static func detect(in text: String, tokens: [Token]) -> [Candidate] {
        amounts(in: text, tokens: tokens)
            + dates(in: text, tokens: tokens)
            + payees(in: text, tokens: tokens)
    }

    // MARK: - amounts

    /// Money shapes: `SGD 4.80`, `S$4.50`, `$4.50`, `SGD44.53`, `4.80 SGD`,
    /// `1,234.50`. A plain integer is weak (phones, refs and card digits are
    /// integers too), so it only ever becomes a suggestion, never a pre-tag.
    /// The amount-only path: list rows flagging likely money should not pay
    /// for a full detect's date and payee scans per row render.
    public static func amounts(in text: String, tokens: [Token]) -> [Candidate] {
        var scored: [(span: [Int], score: Int)] = []

        for i in tokens.indices {
            let t = tokens[i]
            var span: [Int] = []
            var score = 0
            if t.kind == .number {
                span = [i]
                if t.text.range(of: #"\.\d{1,2}$"#, options: .regularExpression) != nil { score += 3 }
                if t.text.contains(",") { score += 1 }
                if t.text.range(of: #"^\d{5,}$"#, options: .regularExpression) != nil { score -= 3 }
            } else if t.kind == .word,
                      t.text.range(of: #"^[A-Z]{2,3}\d+$"#, options: .regularExpression) != nil {
                // "SGD44": the letters ride on the number, so check for a
                // trailing ".53" split off as punct + number
                span = [i]
                score = 4
                if let d = nextNonSpace(i, in: tokens), tokens[d].kind == .punct, tokens[d].text == ".",
                   let n = nextNonSpace(d, in: tokens), tokens[n].kind == .number {
                    span += [d, n]
                    score += 2
                }
            } else {
                continue
            }

            // absorb a currency on the left: "SGD 4.80", "S$ 4.50", "$ 4.50"
            if let l = prevNonSpace(span.first!, in: tokens) {
                let lt = tokens[l]
                if lt.kind == .word, lt.text.range(of: #"^[A-Z]{2,3}$"#, options: .regularExpression) != nil {
                    span.insert(l, at: 0); score += 4
                } else if lt.kind == .punct, lt.text == "$" {
                    var left = [l]
                    // the dollar prefixes Amounts reads: "S$" and "US$"
                    if let ll = prevNonSpace(l, in: tokens),
                       tokens[ll].kind == .word,
                       tokens[ll].text == "S" || tokens[ll].text == "US" { left.insert(ll, at: 0) }
                    span = left + span; score += 4
                }
            }
            // a trailing currency ("4.80 SGD") stays out of the span on
            // purpose: the matcher's amount only reads currency up front, so
            // the code anchors better as literal text than as a capture

            if score >= 1 { scored.append((span, score)) }
        }

        scored.sort { $0.score > $1.score }
        var out: [Candidate] = []
        for (k, s) in scored.prefix(4).enumerated() {
            out.append(Candidate(field: .amount, tokenIndexes: s.span,
                                 confident: k == 0 && s.score >= 3, rank: k))
        }
        return out
    }

    // MARK: - dates

    /// Every short token run `Dates.detectFormat` can read is a candidate.
    /// Longer runs with a year beat fragments, and overlapping windows
    /// collapse to their longest.
    private static func dates(in text: String, tokens: [Token]) -> [Candidate] {
        let tapp = tokens.indices.filter { tokens[$0].isTappable }
        var found: [(span: [Int], score: Int)] = []
        for (pos, i) in tapp.enumerated() {
            var span = [i]
            for j in tapp.dropFirst(pos + 1).prefix(5) {
                guard onlySpaceBetween(span.last!, j, in: tokens) else { break }
                span.append(j)
                let raw = text[tokens[i].range.lowerBound..<tokens[j].range.upperBound]
                guard let f = Dates.detectFormat(String(raw)) else { continue }
                // "26 Oct 11:24" parses "26 Oct 11" as a short-year date and
                // would store 2011: when the window's last number is short
                // and a ":" follows, it is a clock hour, not a year
                if f.hasYear {
                    let lastTok = tokens[span.last!]
                    if lastTok.kind == .number, lastTok.text.count <= 2,
                       let n = nextNonSpace(span.last!, in: tokens),
                       tokens[n].kind == .punct, tokens[n].text == ":" { continue }
                }
                found.append((span, (f.hasYear ? 2 : 0) + span.count))
            }
        }
        // keep the longest window per start token
        var byStart: [Int: (span: [Int], score: Int)] = [:]
        for f in found where (byStart[f.span.first!]?.score ?? -1) < f.score {
            byStart[f.span.first!] = f
        }
        var best = byStart.values.sorted {
            $0.score == $1.score ? $0.span.first! < $1.span.first! : $0.score > $1.score
        }
        // drop windows strictly inside a better one ("Oct" inside "27 Oct 2026")
        best = best.filter { s in !best.contains { o in
            o.span.count > s.span.count && o.span.contains(s.span.first!) && o.span.contains(s.span.last!)
        } }
        return best.prefix(3).enumerated().map { (k, s) in
            Candidate(field: .date, tokenIndexes: s.span, confident: k == 0, rank: k)
        }
    }

    // MARK: - payees

    /// The one field without a shape: a payee is just words. Anchors like
    /// "at", "to", "from" usually precede it, so the run after one is a
    /// candidate; a run of ALL-CAPS words is a weaker, suggestion-only one.
    private static let anchors = ["at", "to", "from", "via", "paid", "spent",
                                  "merchant", "payee", "beneficiary", "received"]
    private static let strongAnchors = ["at", "to", "from", "via"]
    private static let stopWords: Set<String> = ["on", "for", "dated", "was", "is", "were", "are",
        "a", "an", "the", "of", "ref", "date", "amt", "amount", "you", "your",
        "we", "please", "has", "have", "will", "with", "by", "and", "or", "if",
        // "from DBS card ending 1234" must stop before the card clause
        "card", "ending", "account", "acct", "wallet",
        // email boilerplate verbs: "to confirm that", "to view", "to verify"
        // open a sentence, they do not open a merchant name
        "confirm", "verify", "view", "click", "log", "sign", "review",
        "check", "see", "download", "visit", "contact", "reply", "manage",
        "update", "access", "monitor", "track"]
    private static let innerPunct: Set<String> = ["-", "&", "/", "'", ",", ".", "!"]

    /// Only the payee scan: a row title needs the merchant guess and
    /// nothing else, so it should not pay for the amount and date windows
    /// too. `detect` callers reuse their result through `AlertText.title`.
    public static func payeeCandidates(in text: String, tokens: [Token]) -> [Candidate] {
        payees(in: text, tokens: tokens)
    }

    private static func payees(in text: String, tokens: [Token]) -> [Candidate] {
        var out: [Candidate] = []
        for i in tokens.indices {
            let t = tokens[i]
            guard t.kind == .word,
                  let anchor = anchors.firstIndex(of: t.text.lowercased()) else { continue }
            // a ":" turns the anchor into a field label ("To:", "Merchant:"):
            // the value sits right after it, same line or the next, and the
            // whole value is the answer, so a leading stopword ("The") stays
            // and the run ends only at its line
            var probe = i
            var labelled = false
            if let c = nextNonSpace(i, in: tokens),
               tokens[c].kind == .punct, tokens[c].text == ":" {
                probe = c
                labelled = true
            }
            guard let j = nextNonSpace(probe, in: tokens), tokens[j].kind == .word,
                  tokens[j].text.range(of: #"[A-Za-z]"#, options: .regularExpression) != nil
            else { continue }
            // a bare "to" on another line is no anchor; the label form is
            // the only one allowed to cross the break
            guard onlySpaceBetween(probe, j, in: tokens) || labelled else { continue }
            // the first word of an inline anchor takes the same stop test
            // the rest do: "to your PayLah" must not become the payee ahead
            // of the real merchant
            if !labelled, stopWords.contains(tokens[j].text.lowercased()) { continue }
            var span = [j]
            // the span stays on its line: a payee capture cannot hold a
            // newline, so absorbing the next line's words only builds a
            // pattern that never matches
            while span.count < (labelled ? 8 : 5),
                  let n = nextNonSpace(span.last!, in: tokens),
                  onlySpaceBetween(span.last!, n, in: tokens) {
                let nt = tokens[n]
                let isWord = nt.kind == .word
                    && nt.text.range(of: #"[A-Za-z]"#, options: .regularExpression) != nil
                if isWord, !labelled, stopWords.contains(nt.text.lowercased()) { break }
                guard isWord || (nt.kind == .punct && innerPunct.contains(nt.text)) else { break }
                span.append(n)
            }
            // a payee ends on a word, not on "&" or "-"
            while let last = span.last, tokens[last].kind == .punct { span.removeLast() }
            guard !span.isEmpty else { continue }
            // "at"/"to" precede the merchant, "from"/"via" usually the bank;
            // a field label beats any inline anchor, and rank only orders
            // confident candidates against each other
            let strong = strongAnchors.firstIndex(of: anchors[anchor])
            let rank = labelled
                ? (strong ?? anchors.count + anchor)
                : ((strong.map { anchors.count + $0 }) ?? anchors.count + anchor)
            out.append(Candidate(field: .payee, tokenIndexes: span,
                                 confident: strong != nil, rank: rank))
        }

        // fallback suggestion: a run of 2+ ALL-CAPS words reads merchant-like.
        // Whitespace and joiners ("SGH - DIGESTIVE") ride inside the run.
        var run: [Int] = []
        var words = 0
        func flush() {
            while let last = run.last, tokens[last].kind == .punct { run.removeLast() }
            if words >= 2, !covered(run, by: out) {
                out.append(Candidate(field: .payee, tokenIndexes: run,
                                     confident: false, rank: 100))
            }
            run = []; words = 0
        }
        for i in tokens.indices {
            let t = tokens[i]
            if t.kind == .whitespace { continue }
            let isCaps = t.kind == .word && t.text.count > 1
                && t.text == t.text.uppercased()
                && t.text.range(of: #"[A-Z]"#, options: .regularExpression) != nil
            let isJoiner = words > 0 && t.kind == .punct && innerPunct.contains(t.text)
            if isCaps { run.append(i); words += 1; continue }
            if isJoiner { run.append(i); continue }
            flush()
        }
        flush()
        // rank before the cap, not after: a "To:" label sitting behind
        // three inline guesses is still the explicit payee, and keeping the
        // first three in text order would drop it unread
        return Array(out.sorted { $0.rank < $1.rank }.prefix(3))
    }

    private static func covered(_ span: [Int], by cands: [Candidate]) -> Bool {
        cands.contains { Set($0.tokenIndexes).isSuperset(of: Set(span)) }
    }

    private static func nextNonSpace(_ i: Int, in tokens: [Token]) -> Int? {
        (i + 1..<tokens.count).first { tokens[$0].kind != .whitespace }
    }
    private static func prevNonSpace(_ i: Int, in tokens: [Token]) -> Int? {
        (0..<i).reversed().first { tokens[$0].kind != .whitespace }
    }
    /// Two non-whitespace tokens with only non-newline whitespace between
    /// them belong to the same line's run.
    private static func onlySpaceBetween(_ a: Int, _ b: Int, in tokens: [Token]) -> Bool {
        guard a < b else { return false }
        return tokens[(a + 1)..<b].allSatisfy { $0.kind == .whitespace && !$0.text.contains("\n") }
    }
}
