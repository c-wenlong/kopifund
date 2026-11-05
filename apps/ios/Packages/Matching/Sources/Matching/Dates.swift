import Foundation

/// G24: parsing the text a date span captured. `detectFormat` picks the one
/// `DateFormat` that reads the sample, `parse` reads a capture back later as
/// a `CalendarDate`. Reading is by hand, not through `DateFormatter`: a
/// formatter works in a time zone (the phone's, so "12/10/2026" became an
/// instant that read as the 11th in London) and rolls an impossible day
/// over ("31/02/2026" became 3 March) where a misread should fail.
public enum Dates {
    /// Compiled once, not once per call. `Candidates.dates` asks
    /// `detectFormat` about thousands of short token windows on a long alert,
    /// and a fresh `NSRegularExpression` per format per window was the
    /// seconds-long stall when an unmatched row opened. `static let` sets the
    /// ten compiled patterns up once and safely, for callers on and off the
    /// main actor alike.
    private static let compiled: [AlertPattern.DateFormat: NSRegularExpression] = {
        var out: [AlertPattern.DateFormat: NSRegularExpression] = [:]
        for f in AlertPattern.DateFormat.allCases {
            out[f] = try? NSRegularExpression(pattern: "^(?:" + f.regex + ")$")
        }
        return out
    }()

    /// The first format whose regex covers the whole text and whose fields
    /// name a real day. The regex gate matters: the numbers alone cannot
    /// tell `d/M/yyyy` from a shape the text never printed. Every known
    /// format carries a `\d`, so a window with no digit at all skips the
    /// scan outright. A yearless "29 Feb" counts as a date: whether it
    /// exists depends on the year it is read in, not on its shape.
    public static func detectFormat(_ text: String) -> AlertPattern.DateFormat? {
        guard text.contains(where: { $0.isNumber }) else { return nil }
        return AlertPattern.DateFormat.allCases.first { f in
            guard let c = fields(text, as: f) else { return false }
            return CalendarDate(year: c.year ?? 2000, month: c.month, day: c.day) != nil
        }
    }

    /// Read a capture under a known format. A format with no year takes the
    /// latest year that puts the day no later than the day after
    /// `receivedOn` (the day the alert arrived; today when nil): "31 Dec"
    /// arriving on 1 Jan 2027 is 31 Dec 2026, never 31 Dec 2027.
    ///
    /// This is the server's rule for a yearless statement row
    /// (`iso_date` in server/finance/parsers/parse_statements.py: a row
    /// cannot post after the statement that prints it, so a month later than
    /// the statement's rolls back a year), with the alert's arrival as the
    /// reference. The one day of grace is for time zones: a bank in a zone
    /// ahead of the phone can print tomorrow's date, and that is still this
    /// year's tomorrow, not last year's. A day that does not exist in the
    /// year chosen ("29 Feb" in 2027) is nil, never rolled into March.
    public static func parse(_ text: String, as format: AlertPattern.DateFormat,
                             receivedOn: CalendarDate? = nil) -> CalendarDate? {
        guard let c = fields(text, as: format) else { return nil }
        if let y = c.year { return CalendarDate(year: y, month: c.month, day: c.day) }
        let limit = (receivedOn ?? CalendarDate(Date(), in: .current)).adding(days: 1)
        let year = (c.month, c.day) > (limit.month, limit.day) ? limit.year - 1 : limit.year
        return CalendarDate(year: year, month: c.month, day: c.day)
    }

    /// The year (nil when the format prints none), month and day the text
    /// names under `format`, before any check that the day exists. The regex
    /// gate fixes the shape, so the letter and digit runs fall in the
    /// format's order. A two-digit year is in this century.
    private static func fields(_ text: String, as format: AlertPattern.DateFormat)
        -> (year: Int?, month: Int, day: Int)? {
        guard let re = compiled[format],
              re.firstMatch(in: text,
                            range: NSRange(location: 0, length: (text as NSString).length)) != nil
        else { return nil }
        let runs = text.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
        func number(_ i: Int) -> Int? {
            guard i < runs.count, runs[i].allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
            return Int(runs[i])
        }
        func month(_ i: Int) -> Int? { i < runs.count ? monthNumbers[runs[i].lowercased()] : nil }
        let y: Int?, m: Int?, d: Int?
        switch format {
        case .dmy4Slash, .dmy4Dash: (d, m, y) = (number(0), number(1), number(2))
        case .dmy2Slash, .dmy2Dash: (d, m, y) = (number(0), number(1), number(2).map { 2000 + $0 })
        case .ddMmmYyyy:            (d, m, y) = (number(0), month(1), number(2))
        case .ddMmmYy:              (d, m, y) = (number(0), month(1), number(2).map { 2000 + $0 })
        case .mmmDdYyyy:            (m, d, y) = (month(0), number(1), number(2))
        case .ymdDash:              (y, m, d) = (number(0), number(1), number(2))
        case .ddMmm:                (d, m) = (number(0), month(1)); return d.flatMap { d in m.map { (nil, $0, d) } }
        case .dmSlash:              (d, m) = (number(0), number(1)); return d.flatMap { d in m.map { (nil, $0, d) } }
        }
        guard let y, let m, let d else { return nil }
        return (y, m, d)
    }

    /// English month names, full or the three-letter abbreviation, in any
    /// case, plus "Sept", which banks print and the folding in
    /// LiteralPattern already treats as a month.
    private static let monthNumbers: [String: Int] = {
        let names = ["january", "february", "march", "april", "may", "june", "july",
                     "august", "september", "october", "november", "december"]
        var out: [String: Int] = ["sept": 9]
        for (i, n) in names.enumerated() {
            out[n] = i + 1
            out[String(n.prefix(3))] = i + 1
        }
        return out
    }()
}

extension AlertPattern.DateFormat {
    var hasYear: Bool {
        switch self {
        case .ddMmm, .dmSlash: return false
        default: return true
        }
    }
}
