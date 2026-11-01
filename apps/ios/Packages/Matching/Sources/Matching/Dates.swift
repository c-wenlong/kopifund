import Foundation

/// G24: parsing the text a date span captured. `detectFormat` picks the one
/// `DateFormat` that parses the sample, `parse` reads a capture back later.
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

    /// A `DateFormatter` costs about as much to make as a regex to compile,
    /// so one lives per format string. The box owns the lock the compiler
    /// cannot see past, and parsing runs inside it, so shared formatters are
    /// safe when Intents or the share extension call in off the main actor.
    private static let formatterCache = FormatterCache()

    /// The first format whose regex covers the whole text and whose
    /// DateFormatter reads it back. The regex gate matters: `DateFormatter`
    /// accepts text a `d/M/yyyy` shape never printed, so a bare formatter
    /// check picks the wrong format. Every known format carries a `\d`, so a
    /// window with no digit at all can skip the scan outright.
    public static func detectFormat(_ text: String) -> AlertPattern.DateFormat? {
        guard text.contains(where: { $0.isNumber }) else { return nil }
        return AlertPattern.DateFormat.allCases.first { parse(text, as: $0) != nil }
    }

    /// Parse a capture under a known format. Formats with no year take the
    /// year of `fallback` (usually `receivedAt`, else now).
    public static func parse(_ text: String, as format: AlertPattern.DateFormat,
                           fallback: Date? = nil) -> Date? {
        guard let re = compiled[format],
              re.firstMatch(in: text,
                            range: NSRange(location: 0, length: (text as NSString).length)) != nil
        else { return nil }
        guard let d = formatterCache.date(from: text, formats: format.formatterFormats)
        else { return nil }
        if format.hasYear { return d }
        var comps = Calendar(identifier: .gregorian).dateComponents([.month, .day], from: d)
        comps.year = Calendar(identifier: .gregorian).component(.year, from: fallback ?? Date())
        return Calendar(identifier: .gregorian).date(from: comps)
    }
}

/// The lock-guarded formatter pool `Dates` shares: a `static let` of a
/// Sendable type on the outside, a mutable dictionary only ever touched under
/// the lock on the inside.
private final class FormatterCache: @unchecked Sendable {
    private let lock = NSLock()
    private var formatters: [String: DateFormatter] = [:]

    /// First `formats` entry whose formatter reads `text` wins. The whole
    /// loop holds the lock so a shared formatter sees one reader at a time.
    func date(from text: String, formats: [String]) -> Date? {
        lock.lock()
        defer { lock.unlock() }
        for f in formats {
            let fmt = formatters[f] ?? {
                let x = DateFormatter()
                x.locale = Locale(identifier: "en_US_POSIX")
                x.calendar = Calendar(identifier: .gregorian)
                x.dateFormat = f
                formatters[f] = x
                return x
            }()
            if let d = fmt.date(from: text) { return d }
        }
        return nil
    }
}

extension AlertPattern.DateFormat {
    var hasYear: Bool {
        switch self {
        case .ddMmm, .dmSlash: return false
        default: return true
        }
    }
}
