import Foundation

/// Audit: the date an alert prints is a day on the calendar, not a moment.
/// "12 Oct 2026" stays 12 Oct 2026 whatever zone the phone was in when the
/// alert arrived and whatever zone it is in when the row posts to YNAB.
/// Earlier builds stored it as midnight in the phone's zone and then read it
/// back four ways (the phone's zone at parse, `Calendar.current` in dedup,
/// UTC in the import id, the phone's zone at sync), so a row queued in
/// Singapore and synced in London posted a day early. This one value goes
/// end to end instead: parsed from the text, compared in dedup, stored on the
/// row, and written to YNAB as `yyyy-MM-dd` with no zone in between.
public struct CalendarDate: Hashable, Comparable, Sendable, CustomStringConvertible {
    public let year: Int
    public let month: Int
    public let day: Int

    /// Nil for a day that does not exist: 31 Feb, or 29 Feb outside a leap
    /// year. A misread never rolls over into the next month.
    public init?(year: Int, month: Int, day: Int) {
        guard (1...9999).contains(year), (1...12).contains(month),
              (1...Self.days(inMonth: month, of: year)).contains(day) else { return nil }
        self.year = year
        self.month = month
        self.day = day
    }

    /// The day `instant` falls on in `zone`: a Wallet tap or an alert's
    /// arrival read where the phone was when it happened.
    public init(_ instant: Date, in zone: TimeZone) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let c = cal.dateComponents([.year, .month, .day], from: instant)
        year = c.year ?? 1
        month = c.month ?? 1
        day = c.day ?? 1
    }

    /// `yyyy-MM-dd`, the form YNAB takes.
    public var iso: String { String(format: "%04d-%02d-%02d", year, month, day) }

    public var description: String { iso }

    /// Reads exactly `yyyy-MM-dd`; nil for any other text or a day that
    /// does not exist.
    public init?(iso text: String) {
        let parts = text.split(separator: "-", omittingEmptySubsequences: false)
        guard text.count == 10, parts.count == 3, parts[0].count == 4,
              parts[1].count == 2, parts[2].count == 2,
              parts.allSatisfy({ $0.allSatisfy(\.isASCII) && $0.allSatisfy(\.isNumber) }),
              let y = Int(parts[0]), let m = Int(parts[1]), let d = Int(parts[2])
        else { return nil }
        self.init(year: y, month: m, day: d)
    }

    public static func < (a: CalendarDate, b: CalendarDate) -> Bool {
        (a.year, a.month, a.day) < (b.year, b.month, b.day)
    }

    /// The day `n` days later (or earlier, for a negative `n`).
    public func adding(days n: Int) -> CalendarDate {
        CalendarDate(Self.utc.date(byAdding: .day, value: n, to: storedInstant) ?? storedInstant,
                     in: Self.utc.timeZone)
    }

    // MARK: - stored form

    /// How the day sits in a `Date` field (SwiftData keeps `Date`, not this
    /// struct): midnight UTC of the day. A UTC midnight names one calendar
    /// day with no zone left to apply, so reading it back never shifts.
    public var storedInstant: Date {
        Self.utc.date(from: DateComponents(year: year, month: month, day: day)) ?? .distantPast
    }

    /// The day a stored `Date` field holds. A UTC midnight is a day written
    /// by `storedInstant`. Anything else is a row an earlier build wrote (an
    /// alert parsed to midnight in the phone's zone, or a Wallet tap's
    /// moment) and reads the way that build read it, in the phone's zone
    /// (`legacyZone`), so upgrading moves no existing row.
    public init(stored: Date, legacyZone: TimeZone = .current) {
        if stored.timeIntervalSince1970.truncatingRemainder(dividingBy: 86_400) == 0 {
            self.init(stored, in: Self.utc.timeZone)
        } else {
            self.init(stored, in: legacyZone)
        }
    }

    /// Midnight of the day in `zone`, for a date picker that edits the day in
    /// the phone's own calendar.
    public func date(in zone: TimeZone) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        return cal.date(from: DateComponents(year: year, month: month, day: day)) ?? storedInstant
    }

    /// The day as the phone shows dates, mirroring `Date.formatted` so a
    /// view prints a calendar date the same way it prints a moment. Read in
    /// UTC, the zone the stored form was written in, so it never shows the
    /// day before in a zone west of Greenwich.
    public func formatted(date: Date.FormatStyle.DateStyle,
                          time: Date.FormatStyle.TimeStyle = .omitted) -> String {
        storedInstant.formatted(Date.FormatStyle(date: date, time: .omitted,
                                                 timeZone: Self.utc.timeZone))
    }

    // MARK: - calendar arithmetic

    static func isLeap(_ y: Int) -> Bool { y % 4 == 0 && (y % 100 != 0 || y % 400 == 0) }

    static func days(inMonth m: Int, of y: Int) -> Int {
        switch m {
        case 2: return isLeap(y) ? 29 : 28
        case 4, 6, 9, 11: return 30
        default: return 31
        }
    }

    private static let utc: Calendar = {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0)!
        return cal
    }()
}
