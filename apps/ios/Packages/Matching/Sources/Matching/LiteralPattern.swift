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

    /// The literal's full regex, edges included. Every whitespace run is
    /// matched possessively (`\s*+`, `\s++`): the pieces on either side of a
    /// run never start or end on whitespace, so there is exactly one way to
    /// match it, and a failed match cannot retry every split of a long run
    /// (the catastrophic backtracking a 2,000-space alert hit).
    ///
    /// `trailingSpace` is false for a literal that ends its block in the
    /// matcher (an ignore span or the template's end follows): the next
    /// block opens on its own whitespace, and a possessive run here would
    /// take a line break that block requires when the ignored text is
    /// missing ("ref \nEnd" for a template "ref X\nEnd").
    static func pattern(for literal: String, trailingSpace: Bool = true) -> String {
        // a newline the sample printed is part of the template: it becomes a
        // required break in the regex, which also bounds a capture sitting
        // on the line before it. The space before the break must not eat
        // the newline itself, so it is horizontal only.
        // the sample printed the next capture glued to this literal (no
        // space between): only then may a digit run here give it characters
        // A literal that ends its block counts as glued too: the alert may
        // drop the ignored text and the space after the run ("ref 99End"
        // for a sample "ref 88 X End"), and the next block then starts
        // inside it. Nothing in the block follows the run, so ending it
        // early costs nothing.
        let glued = !(literal.last?.isWhitespace ?? true) || !trailingSpace
        let raw = literal.components(separatedBy: "\n")
        let lines = raw.enumerated().map { i, line in
            linePattern(for: line, gluedEnd: glued && i == raw.count - 1)
        }
        var out = ""
        for (i, line) in lines.enumerated() {
            if i > 0 { out += #"[^\S\n]*+\n"# }
            if !line.isEmpty { out += #"\s*+"# + line }
        }
        return trailingSpace ? out + #"\s*+"# : out
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

    private static func linePattern(for line: String, gluedEnd: Bool) -> String {
        let words = line.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        var out: [Piece] = []
        var i = 0
        while i < words.count {
            if let w = dateWindow(at: i, in: words) {
                out.append(.wild("(?:" + w.format.regex + ")"
                    + NSRegularExpression.escapedPattern(for: w.trailing)))
                i += w.width
            } else if words[i].contains(where: { $0.isNumber }) {
                out.append(.wild(digitRun))
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
        return out.enumerated().map { j, piece in
            switch piece {
            case .fixed(let w): return NSRegularExpression.escapedPattern(for: w)
            case .wild(let r) where r == digitRun && gluedEnd && j == out.count - 1: return digitRunGlued
            case .wild(let r): return r
            }
        }.joined(separator: #"\s++"#)
    }

    /// A whitespace-free run holding a digit, read once: up to its first
    /// digit, then the rest of the run, both possessive. Where the sample
    /// put whitespace after it, the run always ends at whitespace.
    private static let digitRun = #"[^\s\d]*+\d\S*+"#
    /// The same run where the sample glued the next capture to it
    /// ("Ref 8842:$4.80" with the amount tagged): the tail after the first
    /// digit is lazy, so the run ends as early as it can and the capture
    /// reads all it was tagged with. The part up to the first digit stays
    /// possessive, which keeps a failed match linear in the run's length.
    private static let digitRunGlued = #"[^\s\d]*+\d\S*?"#

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
