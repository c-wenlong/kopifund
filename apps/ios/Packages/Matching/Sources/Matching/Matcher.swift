import Foundation

/// G24: what a matched alert becomes before it queues for review.
public struct ParsedTransaction: Equatable, Sendable {
    public var payee: String
    /// The amount's size, never negative: a minus the alert printed is the
    /// bank's notation, and the pattern's `direction` alone gives the sign at
    /// post time. (A kept minus used to be negated again for an outflow, so
    /// "-S$4.50" posted as money in.)
    public var amount: Decimal
    /// The calendar date the alert printed, or the day it arrived (in the
    /// phone's zone then) when it printed none. Never an instant.
    public var date: CalendarDate
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

    public init(payee: String, amount: Decimal, date: CalendarDate, direction: AlertPattern.Direction,
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
    /// too, so the most likely pattern's reason reads first. `zone` is where
    /// the phone was when the alert arrived: it decides which day `receivedAt`
    /// falls on, for an alert that prints no date or no year. `arrivedOn`,
    /// when known (a re-read of a saved alert), is that day already and wins.
    public static func match(text: String, source: String?, receivedAt: Date,
                             zone: TimeZone = .current, arrivedOn: CalendarDate? = nil,
                             patterns: [AlertPattern]) -> MatchOutcome {
        var rejections: [Rejection] = []
        for p in patterns.sorted(by: { $0.priority > $1.priority }) {
            if let s = p.sourceMatch, !s.isEmpty,
               !(source ?? "").localizedCaseInsensitiveContains(s) {
                rejections.append(Rejection(patternID: p.id, patternName: p.name,
                                            reason: #"source "\#(source ?? "")" lacks "\#(s)""#))
                continue
            }
            switch apply(p, to: text, receivedAt: receivedAt, zone: zone, arrivedOn: arrivedOn) {
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
                            receivedAt: Date = Date(),
                            zone: TimeZone = .current,
                            arrivedOn: CalendarDate? = nil) -> Result<ParsedTransaction, MatchError> {
        apply(pattern, to: text, receivedAt: receivedAt, zone: zone, arrivedOn: arrivedOn)
    }

    // MARK: - regex

    /// One named group per capture slot, in segment order. Text before and
    /// after the template is allowed: bank messages carry a changing prefix
    /// or footer, and the anchors nearest the captures are what identifies
    /// the alert.
    ///
    /// Audit: the shape keeps a failed match close to linear in the text's
    /// length. Whitespace runs are matched possessively (LiteralPattern),
    /// line captures start and end on a non-space and a lazy one reads at
    /// most `maxCaptureLength` characters, and the template is cut into
    /// blocks at its ignore spans, each read as `(?>[\s\S]*?B)` from `\A`:
    /// the first place block B reads is committed to, so a later failure
    /// never re-tries every split between two wildcards or every start
    /// position. The first place B reads also ends earliest, which leaves
    /// the most room for the blocks after it, so committing finds the same
    /// leftmost match the old backtracking did whenever there is one.
    private static func expression(for p: AlertPattern) throws -> NSRegularExpression {
        // ignore spans cut the template into blocks; an ignore at either
        // edge leaves an empty block, which adds nothing: each block already
        // skips ahead to where it reads, and nothing after the last is
        // required
        let blocks = p.segments.split(separator: .ignore, omittingEmptySubsequences: true)
        var dateGroup = 0
        let stop = restart(of: p)
        var pattern = #"\A"#
        for (b, block) in blocks.enumerated() {
            // a block that opens on a literal starts with that literal's
            // possessive whitespace, so the skip in front only needs to stop
            // where nothing was skipped or right after a non-space: stopping
            // at each space of a long run would read the same literal once
            // per space. A template that opens on the payee or the card
            // reads them from a line's start: the capture can grow over
            // anything on its line, so if it closes from any place on a
            // line it closes from the line's first word, and the leftmost
            // match always started there. Trying every other place on the
            // line only repeated the same scan.
            //
            // G24: after the first block the skip never crosses the words
            // the template opens on: with only the nearest words anchoring
            // each field, a skip that ran over a second alert's opening
            // could take the date and amount from one alert and the payee
            // from the next. `apply` retries from each opening instead.
            let any = b > 0 ? skipChar(stop) : #"[\s\S]"#
            let last = b > 0 ? (stop.map { "(?!\($0))" } ?? "") : ""
            switch block.first {
            case .literal:
                pattern += #"(?>(?:\#(any)*?\#(last)\S)??"#
            case .payee where b == 0, .accountHint where b == 0:
                pattern += #"(?>[\s\S]*?(?m:^)[^\S\r\n]*+"#
            default:
                pattern += #"(?>\#(any)*?"#
            }
            for (k, seg) in zip(block.indices, block) {
                switch seg {
                case .literal(let s):
                    pattern += LiteralPattern.pattern(for: s,
                                                      trailingSpace: k != block.index(before: block.endIndex))
                case .ignore:
                    break
                case .amount:
                    pattern += "(?<amount>" + Amounts.pattern + ")"
                case .payee:
                    // a payee never spans lines, so the capture stays inside
                    // its own: it can neither swallow the next line's footer
                    // nor collapse when the text beside it is a wildcard. A
                    // payee that ends the template reaches its line's end,
                    // not the text's, so trailing boilerplate stays out of
                    // the name.
                    //
                    // G24: a payee the sample followed with a blank line is a
                    // paragraph of its own, so a long name the bank wrapped
                    // onto a second or third line still reads whole
                    if k != block.index(before: block.endIndex),
                       case .literal(let next) = block[block.index(after: k)], next.hasPrefix("\n\n") {
                        pattern += paragraphCapture("payee", stop: stop)
                    } else {
                        pattern += lineCapture("payee", atLineEnd: atLineEnd(p, seg), stop: stop)
                    }
                case .date(let f):
                    pattern += Amounts.noNumberBefore + "(?<date\(dateGroup)>\(f.regex))"
                    dateGroup += 1
                case .accountHint:
                    // the account string prints the same way every alert, so
                    // the capture reads the whole tagged text, punctuation
                    // and all; the sample's value is what a later alert must
                    // equal
                    pattern += lineCapture("accountHint", atLineEnd: atLineEnd(p, seg), stop: stop)
                }
            }
            pattern += ")"
        }
        return try NSRegularExpression(pattern: pattern, options: [])
    }



    /// The longest payee or account a lazy capture reads. YNAB caps a payee
    /// name at 200 characters, so a longer capture could never post; the
    /// bound keeps a capture that is hunting for its closing anchor from
    /// scanning a whole multi-KB line at every place it could start.
    static let maxCaptureLength = 200

    /// A capture on one line that starts and ends on a non-space, so the
    /// whitespace around it belongs to the literals alone. The lazy form
    /// tries one character first (`??`), then grows. One that ends the
    /// template reads greedily to its line's end; nothing follows it, so it
    /// never backtracks.
    ///
    /// G24: `stop`, when set, is the words the template opens on. A name
    /// never holds them: with only the nearest words anchoring a field, a
    /// line that repeats the template ("...was declined, you charged SGD
    /// 4.80 at ...") would otherwise let the payee read across the repeat.
    private static func lineCapture(_ name: String, atLineEnd: Bool, stop: String? = nil) -> String {
        let c = char(stop)
        let open = stop.map { "(?!\($0))" } ?? ""
        return atLineEnd ? #"(?<\#(name)>\#(open)\S(?:\#(c)*\S)?)"#
                         : #"(?<\#(name)>\#(open)\S(?:\#(c){0,\#(maxCaptureLength - 2)}?\S)??)"#
    }

    /// One skipped character between blocks, never the start of `stop`.
    private static func skipChar(_ stop: String?) -> String {
        stop.map { "(?:(?!\($0))[\\s\\S])" } ?? #"[\s\S]"#
    }

    /// One character of a line capture, never the start of `stop`.
    private static func char(_ stop: String?) -> String {
        stop.map { "(?:(?!\($0))[^\r\n])" } ?? #"[^\r\n]"#
    }

    /// A payee that may wrap: its first line, then up to two more non-blank
    /// lines, as few as the blank line after it allows. Each line starts and
    /// ends on a non-space; the reading joins the lines with one space.
    private static func paragraphCapture(_ name: String, stop: String?) -> String {
        let open = stop.map { "(?!\($0))" } ?? ""
        let line = open + #"\S(?:\#(char(stop)){0,\#(maxCaptureLength - 2)}\S)?"#
        return #"(?<\#(name)>"# + line + #"(?:[^\S\n]*+\n[^\S\n]*+"# + line + "){0,2}?)"
    }

    /// Where the template starts again, as a case-blind regex: the last
    /// line of the literal before the first field, as whole words, and the
    /// field itself when it is an amount or a date ("Paid SGD 4.80", "Date &
    /// Time: 12 Oct"), so a name merely holding the words ("PREPAID", "PAID
    /// PARKING") is no restart. Nil when the template opens on a field.
    static func restart(of p: AlertPattern) -> String? {
        for (i, seg) in p.segments.enumerated() {
            switch seg {
            case .ignore: continue
            case .literal(let s):
                let line = (s.components(separatedBy: "\n").last { $0.contains(where: \.isLetter) } ?? "")
                    .trimmingCharacters(in: .whitespaces)
                guard line.contains(where: \.isLetter) else { return nil }
                let next = p.segments.indices.contains(i + 1) ? p.segments[i + 1] : nil
                // without the literal's leading `\s*+`: tried at every
                // place of a long whitespace run, it would rescan the run
                var lit = LiteralPattern.pattern(for: line, trailingSpace: false)
                if lit.hasPrefix(#"\s*+"#) { lit.removeFirst(4) }
                let words = "(?<![A-Za-z0-9])(?i:" + lit + ")"
                switch next {
                case .amount?: return words + #"\s*+(?:"# + Amounts.pattern + ")"
                case .date(let f)?: return words + #"\s*+(?:"# + f.regex + ")"
                default: return words + "(?![A-Za-z0-9])"
                }
            default: return nil
            }
        }
        return nil
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

    static func cachedExpression(for p: AlertPattern) throws -> NSRegularExpression {
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

    private static func apply(_ p: AlertPattern, to text: String, receivedAt: Date,
                              zone: TimeZone, arrivedOn known: CalendarDate?)
        -> Result<ParsedTransaction, MatchError> {
        let re: NSRegularExpression
        do { re = try cachedExpression(for: p) } catch { return .failure(MatchError("pattern builds a bad regex")) }
        let ns = text as NSString
        let m: NSTextCheckingResult
        switch searchFromEachStart(p, re, in: text) {
        case .found(let hit): m = hit
        case .none: return .failure(MatchError("text does not match"))
        case .timedOut: return .failure(MatchError("text too long to read in time"))
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
        guard let a = amountRaw, let parsed = Amounts.read(a) else {
            return .failure(MatchError("amount not readable"))
        }
        // the row keeps cents, so a third decimal (KWD, BHD, OMR fils)
        // would be cut on the way in: refuse it out loud instead
        guard parsed.decimals <= 2 else {
            return .failure(MatchError("amount \(a) has \(parsed.decimals) decimals; the app keeps cents"))
        }
        // a wrapped payee's lines join with one space
        guard let py = payee?.replacingOccurrences(of: #"\s*\n\s*"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: ".,;:-")), !py.isEmpty else {
            return .failure(MatchError("payee not readable"))
        }
        let arrivedOn = known ?? CalendarDate(receivedAt, in: zone)
        let date: CalendarDate
        if let raw = dateRaw, let f = dateFmt {
            guard let d = Dates.parse(raw.trimmingCharacters(in: .whitespaces), as: f,
                                      receivedOn: arrivedOn) else {
                return .failure(MatchError("date not readable"))
            }
            date = d
        } else {
            date = arrivedOn
        }
        // the hint's character class allows spaces, so a greedy capture can
        // keep padding around the digits
        let trimmedHint = hint?.trimmingCharacters(in: .whitespacesAndNewlines)
        // a tagged card or account is part of the pattern's identity: the
        // digits inside literals flex for references, so the hint is what
        // separates "card ending 1234" from "card ending 5678"
        // G24: a tagged card the sample no longer yields fails closed: a
        // skipped check would let another card's alerts in
        if p.segments.contains(.accountHint) {
            guard let expected = expectedHint(for: p, re: re) else {
                return .failure(MatchError("the sample's card could not be read"))
            }
            if trimmedHint != expected { return .failure(MatchError("different card or account")) }
        }
        return .success(ParsedTransaction(payee: py, amount: parsed.magnitude, date: date,
                                          direction: p.direction, accountHint: trimmedHint,
                                          currency: parsed.currency,
                                          patternID: p.id, patternName: p.name,
                                          alertText: text, source: nil, receivedAt: receivedAt))
    }

    /// The match from the text's start, or, when that fails, from each later
    /// place the template opens (`restart`), at most `restartTries` of them:
    /// a text holding a cut-off alert ahead of a whole one reads the whole
    /// one, where the skips between fields may not cross an opening.
    static func searchFromEachStart(_ p: AlertPattern, _ re: NSRegularExpression,
                                            in text: String) -> Search {
        let first = search(re, in: text)
        guard case .none = first, let stop = restart(of: p),
              let opener = try? expressions.value(for: "restart:" + stop, build: {
                  try NSRegularExpression(pattern: stop) })
        else { return first }
        let ns = text as NSString
        var tries = 0
        for o in opener.matches(in: text, range: NSRange(location: 0, length: ns.length))
        where o.range.location > 0 {
            tries += 1
            if tries > restartTries { break }
            let r = search(re, in: text, from: o.range.location)
            if case .none = r { continue }
            return r
        }
        return .none
    }

    static let restartTries = 4

    /// The account or card number the sample printed, recovered by running
    /// the same search on it, retries included: the value a later alert's
    /// hint must equal.
    private static func expectedHint(for p: AlertPattern, re: NSRegularExpression) -> String? {
        let ns = p.sampleText as NSString
        guard case .found(let m) = searchFromEachStart(p, re, in: p.sampleText) else { return nil }
        let r = m.range(withName: "accountHint")
        guard r.location != NSNotFound else { return nil }
        return ns.substring(with: r).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

extension Matcher {
    /// How long one pattern may spend on one text before it gives up. The
    /// regex is built to stay near linear, so a real alert reads in a few
    /// milliseconds; the budget is the backstop that keeps any shape the
    /// builder did not foresee from freezing the app or getting a share
    /// extension or intent killed mid-ingest.
    static let matchBudget: Duration = .milliseconds(250)

    enum Search {
        case found(NSTextCheckingResult)
        case none
        case timedOut
    }

    /// The first match, or `timedOut` once `budget` has passed. ICU calls
    /// the progress block periodically while it works, which is the only
    /// place a running match can be stopped.
    static func search(_ re: NSRegularExpression, in text: String, from start: Int = 0,
                       budget: Duration = matchBudget) -> Search {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: budget)
        var hit: NSTextCheckingResult?
        var late = false
        re.enumerateMatches(in: text, options: [.reportProgress],
                            range: NSRange(location: start, length: (text as NSString).length - start)) { m, _, stop in
            if let m {
                hit = m
                stop.pointee = true
            } else if clock.now >= deadline {
                late = true
                stop.pointee = true
            }
        }
        if let hit { return .found(hit) }
        return late ? .timedOut : .none
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

/// The reason a pattern did not take an alert, as an `Error` so `apply` can
/// hand back `Result`.
public struct MatchError: Error, Equatable, Sendable {
    public let reason: String
    public init(_ reason: String) { self.reason = reason }
}
