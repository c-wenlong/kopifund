import Foundation

/// Audit: the one grammar for an amount an alert prints. The matcher's
/// capture, the reading of that capture, and the detector's pre-tags all
/// use it, so the detector never proposes an amount the matcher cannot
/// read. Three grammars used to disagree: "RM 45.20" was pre-tagged but
/// never matched, and a reference like "AB123456" was pre-tagged as money.
public enum Amounts {

    /// One amount, read.
    public struct Amount: Equatable, Sendable {
        /// The size, never negative.
        public var magnitude: Decimal
        /// The currency code the alert printed, nil when it printed none.
        public var currency: String?
        /// The alert printed a minus in front.
        public var printedNegative: Bool
        /// Digits after the decimal point, 0 to 3.
        public var decimals: Int
    }

    /// A currency printed before the number, longest first so `US$` is not
    /// read as the letters "US": `US$`, `S$`, two or three capitals (`SGD`,
    /// `RM`, `KWD`), or a bare `$`.
    static let currency = #"US\$|S\$|[A-Z]{2,3}|\$"#

    /// The number: thousands grouped by commas or not, at most twelve
    /// digits before the point (a longer figure is a misread, and would
    /// overflow the conversions to cents and milliunits), up to three
    /// decimals.
    static let number = #"[0-9]{1,3}(?:,[0-9]{3}){1,3}(?:\.[0-9]{1,3})?|[0-9]{1,12}(?:\.[0-9]{1,3})?"#

    /// What an amount capture reads: an optional minus, an optional
    /// currency, the number.
    static let pattern = #"-?(?:(?:"# + currency + #")\s*+)?(?:"# + number + ")"

    private static let whole: NSRegularExpression = {
        // force-try: the grammar is a constant this package's tests compile
        try! NSRegularExpression(pattern: #"\A(?<minus>-)?(?:(?<cur>"# + currency + #")\s*+)?(?<num>"#
                                    + number + #")\z"#)
    }()

    /// Codes a bank prints that are not the ISO code: an SG alert's dollar is
    /// local, and Malaysian banks print ringgit as "RM".
    private static let codes = ["S$": "SGD", "$": "SGD", "US$": "USD", "RM": "MYR"]

    /// The amount `raw` names, or nil when it is not exactly one amount in
    /// the grammar. The record holds the code the bank meant rather than
    /// assuming one; a bare number reports no currency.
    public static func read(_ raw: String) -> Amount? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let ns = text as NSString
        guard let m = whole.firstMatch(in: text, range: NSRange(location: 0, length: ns.length))
        else { return nil }
        func group(_ name: String) -> String? {
            let r = m.range(withName: name)
            return r.location == NSNotFound ? nil : ns.substring(with: r)
        }
        guard let digits = group("num")?.replacingOccurrences(of: ",", with: ""),
              let value = Decimal(string: digits, locale: Locale(identifier: "en_US_POSIX"))
        else { return nil }
        let decimals = digits.split(separator: ".", omittingEmptySubsequences: false)
            .dropFirst().first?.count ?? 0
        let cur = group("cur")
        return Amount(magnitude: value, currency: cur.map { codes[$0] ?? $0 },
                      printedNegative: group("minus") != nil, decimals: decimals)
    }
}
