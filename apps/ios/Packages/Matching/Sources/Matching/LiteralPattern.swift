import Foundation

/// G24: what a literal segment becomes in the matcher's regex. The words and
/// punctuation of the template text stay fixed anchors, but the pieces that
/// change between alerts fold into wildcards: any whitespace-free run holding
/// a digit (reference numbers, counters, hotline digits, ids like "AB12") and
/// any run a date format reads. "Transfer dated 27 Oct 2026" in the sample
/// still matches "Transfer dated 3 Nov 2026" in the next alert, while
/// "Transfer dated" and the words around it keep pinning the pattern down.
enum LiteralPattern {

    /// A piece of the literal after folding: `fixed` escapes to match its
    /// text, `wild` already holds its regex fragment.
    private enum Piece {
        case fixed(String)
        case wild(String)
        var isWild: Bool { if case .wild = self { return true }; return false }
    }

    static func pattern(for literal: String) -> String {
        // a newline the sample printed is part of the template: it becomes a
        // required break in the regex, which also bounds a capture sitting
        // on the line before it
        return literal.components(separatedBy: "\n")
            .map(linePattern(for:))
            .joined(separator: #"\s*\n\s*"#)
    }

    /// True when the literal's first word folds to a wildcard instead of
    /// anchoring: it carries a digit, opens a date-shaped run, or is a month
    /// name riding a folded run. A capture cannot border such a piece: the
    /// wildcard can start matching inside the capture's tail. Only the
    /// capture's own line matters; a line break in front of the first word
    /// already ends the capture there.
    static func firstPieceIsWild(_ literal: String) -> Bool {
        let line = literal.components(separatedBy: "\n").first ?? ""
        let words = line.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard let first = words.first else { return false }
        if dateWindow(at: 0, in: words) != nil { return true }
        if first.contains(where: { $0.isNumber }) { return true }
        if months.contains(first), words.count > 1,
           words[1].contains(where: { $0.isNumber }) || dateWindow(at: 1, in: words) != nil {
            return true
        }
        return false
    }

    private static func linePattern(for line: String) -> String {
        let words = line.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        var out: [Piece] = []
        var i = 0
        while i < words.count {
            if let w = dateWindow(at: i, in: words) {
                out.append(.wild("(?:" + w.format.regex + ")"
                    + NSRegularExpression.escapedPattern(for: w.trailing)))
                i += w.width
            } else if words[i].contains(where: { $0.isNumber }) {
                out.append(.wild(#"\S*\d\S*"#))
                i += 1
            } else {
                out.append(.fixed(words[i]))
                i += 1
            }
        }
        // a month name left fixed inside folded text is a split date
        // fragment ("(27 Oct 2026)"), so it folds like its neighbours
        for j in out.indices {
            guard case .fixed(let w) = out[j], months.contains(w) else { continue }
            let leftWild = j > 0 && out[j - 1].isWild
            let rightWild = j + 1 < out.count && out[j + 1].isWild
            if leftWild || rightWild { out[j] = .wild(#"[A-Za-z]{3,9}"#) }
        }
        return out.map { piece in
            switch piece {
            case .fixed(let w): return NSRegularExpression.escapedPattern(for: w)
            case .wild(let r): return r
            }
        }.joined(separator: #"\s+"#)
    }

    private static let months: Set<String> = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
        "Sep", "Sept", "Oct", "Nov", "Dec",
        "January", "February", "March", "April", "June", "July",
        "August", "September", "October", "November", "December",
    ]

    /// The widest run of words at `start`, capped at three (the most a known
    /// date shape takes), that `Dates.detectFormat` reads as one date.
    /// Sentence punctuation on the run's last word stays outside the date
    /// match so "…2026." still anchors its full stop.
    private static func dateWindow(at start: Int, in words: [String])
        -> (width: Int, format: AlertPattern.DateFormat, trailing: String)? {
        for w in (1...3).reversed() where start + w <= words.count {
            var last = words[start + w - 1]
            var trailing = ""
            while let c = last.last, ".,;:!?".contains(c) {
                trailing = String(c) + trailing
                last = String(last.dropLast())
            }
            let probe = (words[start..<(start + w - 1)] + [last]).joined(separator: " ")
            if let f = Dates.detectFormat(probe) { return (w, f, trailing) }
        }
        return nil
    }
}
