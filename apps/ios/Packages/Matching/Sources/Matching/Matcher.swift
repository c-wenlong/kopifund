import Foundation

/// G24: what a matched alert becomes before it queues for review.
public struct ParsedTransaction: Equatable, Sendable {
    public var payee: String
    /// Absolute amount as printed. `direction` gives it a sign at post time.
    public var amount: Decimal
    public var date: Date
    public var direction: AlertPattern.Direction
    public var accountHint: String?
    /// The currency the amount printed, nil when the alert printed none.
    public var currency: String?
    public var patternID: UUID
    public var patternName: String
    /// The alert's own text, kept on the queued row so a wrong match is easy
    /// to spot and a deleted pattern's matches can still be read.
    public var alertText: String
    public var source: String?
    public var receivedAt: Date

    public init(payee: String, amount: Decimal, date: Date, direction: AlertPattern.Direction,
                accountHint: String?, currency: String? = nil, patternID: UUID, patternName: String,
                alertText: String, source: String?, receivedAt: Date) {
        self.payee = payee
        self.amount = amount
        self.date = date
        self.direction = direction
        self.accountHint = accountHint
        self.currency = currency
        self.patternID = patternID
        self.patternName = patternName
        self.alertText = alertText
        self.source = source
        self.receivedAt = receivedAt
    }
}

/// Why a pattern did not take an alert, kept so Unmatched can say what was
/// tried instead of a bare "no match".
public struct Rejection: Equatable, Sendable {
    public var patternID: UUID
    public var patternName: String
    public var reason: String
}

public enum MatchOutcome: Equatable, Sendable {
    case matched(ParsedTransaction)
    case unmatched([Rejection])
}

public enum Matcher {

    /// Highest priority pattern wins. Rejections are listed in priority order
    /// too, so the most likely pattern's reason reads first.
    public static func match(text: String, source: String?, receivedAt: Date,
                             patterns: [AlertPattern]) -> MatchOutcome {
        var rejections: [Rejection] = []
        for p in patterns.sorted(by: { $0.priority > $1.priority }) {
            if let s = p.sourceMatch, !s.isEmpty,
               !(source ?? "").localizedCaseInsensitiveContains(s) {
                rejections.append(Rejection(patternID: p.id, patternName: p.name,
                                            reason: #"source "\#(source ?? "")" lacks "\#(s)""#))
                continue
            }
            switch apply(p, to: text, receivedAt: receivedAt) {
            case .success(let parsed):
                var txn = parsed
                txn.source = source
                return .matched(txn)
            case .failure(let e):
                rejections.append(Rejection(patternID: p.id, patternName: p.name, reason: e.reason))
            }
        }
        return .unmatched(rejections)
    }

    /// One pattern against one text. `test` is the same path the matcher
    /// takes, so the builder's live test never says yes where a run says no.
    public static func test(_ pattern: AlertPattern, text: String,
                            receivedAt: Date = Date()) -> Result<ParsedTransaction, MatchError> {
        apply(pattern, to: text, receivedAt: receivedAt)
    }

    // MARK: - regex

    /// One named group per capture slot, in segment order. The regex allows
    /// leading and trailing text around the template: bank messages carry a
    /// changing prefix or footer, and the anchors nearest the captures are
    /// what identifies the alert.
    private static func expression(for p: AlertPattern) throws -> NSRegularExpression {
        var dateGroup = 0
        var parts: [String] = []
        for seg in p.segments {
            switch seg {
            case .literal(let s):
                // whitespace runs in the alert may not match the sample's, and
                // the space at a literal's edge overlaps the next capture
                parts.append(#"\s*"# + LiteralPattern.pattern(for: s) + #"\s*"#)
            case .ignore:
                parts.append(#"[\s\S]*?"#)
            case .amount:
                parts.append(#"(?<amount>-?(?:[A-Z]{3}|S\$|US\$|\$)\s*[0-9][0-9,]*(?:\.\d{1,2})?|-?[0-9][0-9,]*(?:\.\d{1,2})?)"#)
            case .payee:
                // a payee never spans lines, so the capture stays inside its
                // own: it can neither swallow the next line's footer nor
                // collapse when the text beside it is a wildcard. A payee
                // that ends the template reaches its line's end, not the
                // text's, so trailing boilerplate stays out of the name.
                parts.append(atLineEnd(p, seg) ? #"(?<payee>[^\r\n]+)"# : #"(?<payee>[^\r\n]+?)"#)
            case .date(let f):
                parts.append("(?<date\(dateGroup)>\(f.regex))")
                dateGroup += 1
            case .accountHint:
                // the account string prints the same way every alert, so the
                // capture reads the whole tagged text, punctuation and all;
                // the sample's value is what a later alert must equal
                parts.append(atLineEnd(p, seg) ? #"(?<accountHint>[^\r\n]+)"#
                                               : #"(?<accountHint>[^\r\n]+?)"#)
            }
        }
        let pattern = #"\A[\s\S]*?"# + parts.joined() + #"[\s\S]*?\z"#
        return try NSRegularExpression(pattern: pattern, options: [])
    }

    /// A capture ends its line when nothing but whitespace literals follows
    /// it. A trailing `.literal(" ")` can match empty, so a lazy capture in
    /// front of it shrinks to one character: `Card 1234 ` would capture `1`,
    /// and `Card 1999 ` would "match" the same hint. Checking only the last
    /// segment misses that, so every segment after the capture is judged.
    private static func atLineEnd(_ p: AlertPattern, _ seg: AlertPattern.Segment) -> Bool {
        guard let i = p.segments.lastIndex(of: seg) else { return true }
        return p.segments[(i + 1)...].allSatisfy {
            guard case .literal(let s) = $0 else { return false }
            return s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    /// Compiling the regex costs more than running it, and `match` re-pays
    /// that per pattern per alert while the builder repays it on every tag
    /// tap. The segments determine the pattern text fully, so they key the
    /// cache inside a lock-guarded box Intents and the share extension can
    /// share safely off the main actor.
    private static let expressions = ExpressionCache()

    private static func cachedExpression(for p: AlertPattern) throws -> NSRegularExpression {
        // the length prefix keeps a literal's text unambiguous in the key
        let key = p.segments.map { seg -> String in
            switch seg {
            case .literal(let s): return "L\(s.count):\(s)"
            case .ignore: return "I"
            case .amount: return "A"
            case .payee: return "P"
            case .date(let f): return "D\(f.rawValue)"
            case .accountHint: return "H"
            }
        }.joined()
        return try expressions.value(for: key) { try expression(for: p) }
    }

    private static func apply(_ p: AlertPattern, to text: String,
                              receivedAt: Date) -> Result<ParsedTransaction, MatchError> {
        let re: NSRegularExpression
        do { re = try cachedExpression(for: p) } catch { return .failure(MatchError("pattern builds a bad regex")) }
        let ns = text as NSString
        guard let m = re.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) else {
            return .failure(MatchError("text does not match"))
        }
        func group(_ name: String) -> String? {
            let r = m.range(withName: name)
            guard r.location != NSNotFound else { return nil }
            return ns.substring(with: r)
        }
        let amountRaw = group("amount")
        let payee = group("payee")
        let hint = group("accountHint")
        var dateRaw: String? = nil
        var dateFmt: AlertPattern.DateFormat? = nil
        var dateGroup = 0
        for seg in p.segments {
            if case .date(let f) = seg {
                dateRaw = group("date\(dateGroup)"); dateFmt = f
                dateGroup += 1
            }
        }
        guard let a = amountRaw, let parsed = Amounts.split(a) else {
            return .failure(MatchError("amount not readable"))
        }
        guard let py = payee?.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: ".,;:-")), !py.isEmpty else {
            return .failure(MatchError("payee not readable"))
        }
        let date: Date
        if let raw = dateRaw, let f = dateFmt {
            guard let d = Dates.parse(raw.trimmingCharacters(in: .whitespaces), as: f,
                                      fallback: receivedAt) else {
                return .failure(MatchError("date not readable"))
            }
            date = d
        } else {
            date = receivedAt
        }
        // the hint's character class allows spaces, so a greedy capture can
        // keep padding around the digits
        let trimmedHint = hint?.trimmingCharacters(in: .whitespacesAndNewlines)
        // a tagged card or account is part of the pattern's identity: the
        // digits inside literals flex for references, so the hint is what
        // separates "card ending 1234" from "card ending 5678"
        if p.segments.contains(.accountHint),
           let expected = expectedHint(for: p, re: re), trimmedHint != expected {
            return .failure(MatchError("different card or account"))
        }
        return .success(ParsedTransaction(payee: py, amount: parsed.amount, date: date,
                                          direction: p.direction, accountHint: trimmedHint,
                                          currency: parsed.currency,
                                          patternID: p.id, patternName: p.name,
                                          alertText: text, source: nil, receivedAt: receivedAt))
    }

    /// The account or card number the sample printed, recovered by running
    /// the same regex on it: the value a later alert's hint must equal.
    private static func expectedHint(for p: AlertPattern, re: NSRegularExpression) -> String? {
        let ns = p.sampleText as NSString
        guard let m = re.firstMatch(in: p.sampleText,
                                  range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        let r = m.range(withName: "accountHint")
        guard r.location != NSNotFound else { return nil }
        return ns.substring(with: r).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// The compiled-regex store `Matcher` shares: a `static let` of a Sendable
/// type on the outside, a mutable dictionary only ever touched under the
/// lock on the inside. Capped so a long builder session of unique segment
/// edits cannot grow it without bound.
private final class ExpressionCache: @unchecked Sendable {
    private let lock = NSLock()
    private var cache: [String: NSRegularExpression] = [:]
    private let limit = 64

    func value(for key: String,
               build: () throws -> NSRegularExpression) throws -> NSRegularExpression {
        lock.lock()
        defer { lock.unlock() }
        if let re = cache[key] { return re }
        let re = try build()
        if cache.count >= limit { cache.removeAll() }
        cache[key] = re
        return re
    }
}

/// Amounts the alerts print: `SGD 4.50`, `S$4.50`, `$4.50`, `4.50`, with
/// thousands separators and an optional leading minus.
public enum Amounts {
    /// The number and the currency symbol it printed, kept apart so the record
    /// holds the code the bank meant rather than assuming one. `S$` and a bare
    /// `$` read as SGD (an SG bank alert's dollar is local), `US$` as USD, a
    /// three-letter code as itself; a bare number reports no currency.
    public static func split(_ raw: String) -> (amount: Decimal, currency: String?)? {
        var s = raw.replacingOccurrences(of: ",", with: "")
            .trimmingCharacters(in: .whitespaces)
        var sign = ""
        if s.hasPrefix("-") { sign = "-"; s.removeFirst() }
        var currency: String? = nil
        // longest alternatives first so `US$` is not eaten as the letters US
        if let r = s.range(of: #"^(?:US\$|S\$|[A-Z]{1,3}|\$)\s*"#,
                           options: .regularExpression) {
            let sym = s[r].trimmingCharacters(in: .whitespacesAndNewlines)
            s.removeSubrange(r)
            currency = switch sym {
            case "US$": "USD"
            case "S$", "$": "SGD"
            default: sym
            }
        }
        guard let d = Decimal(string: sign + s, locale: Locale(identifier: "en_US_POSIX"))
        else { return nil }
        return (d, currency)
    }

    public static func parse(_ raw: String) -> Decimal? {
        split(raw)?.amount
    }
}

/// The reason a pattern did not take an alert, as an `Error` so `apply` can
/// hand back `Result`.
public struct MatchError: Error, Equatable, Sendable {
    public let reason: String
    public init(_ reason: String) { self.reason = reason }
}
