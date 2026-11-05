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
    /// `RM`, `KWD`), or a bare `$`. A marker may follow a digit ("Ref
    /// 8842US$4.80") or a word ("Paid$4.80") but never starts inside a
    /// longer one: a code or `US$` never starts mid-word, `S$` never after
    /// a `U`, `$` never after an `S` or a `U`, so "US$4.80" is never read
    /// from its "S$" or "$" as SGD.
    static let currency = #"(?<![A-Za-z])(?:US\$|[A-Z]{2,3})|(?<!U)S\$|(?<![SU])\$"#

    /// A bare number never starts inside another: a skip or a reference
    /// run that stops early ("Ref 8|842") must not hand the rest of a
    /// reference to the capture as the amount.
    static let noNumberBefore = #"(?<![0-9])(?<![0-9][.,])"#

    /// The number: thousands grouped by commas or not, at most twelve
    /// digits before the point (a longer figure is a misread, and would
    /// overflow the conversions to cents and milliunits), up to three
    /// decimals.
    static let number = #"[0-9]{1,3}(?:,[0-9]{3}){1,3}(?:\.[0-9]{1,3})?|[0-9]{1,12}(?:\.[0-9]{1,3})?"#

    /// What an amount capture reads: an optional minus, an optional
    /// currency, the number.
    static let pattern = #"-?(?:"# + currency + #")\s*+(?:"# + number + ")" + numberEnd + "|"
        + noNumberBefore + #"-?(?:"# + number + ")" + numberEnd

    /// A capture never stops inside a longer figure: no digit, and no
    /// separator followed by a digit, may come right after it. Without this
    /// an oversized "1234567890123.00" at a template's end read as its first
    /// twelve digits instead of failing.
    static let numberEnd = #"(?![0-9]|[.,][0-9])"#

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
