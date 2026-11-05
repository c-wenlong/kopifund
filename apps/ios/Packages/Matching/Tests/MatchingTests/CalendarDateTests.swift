import XCTest
@testable import Matching

/// Audit: a printed date is a calendar date. It used to become midnight in
/// the phone's zone, so "12/10/2026" parsed in Singapore and formatted in
/// London read 2026-10-11. Each case runs with the phone in each zone below,
/// including the ones furthest from UTC either way.
final class CalendarDateTests: XCTestCase {
    static let names = ["Asia/Singapore", "Europe/London", "Pacific/Kiritimati",
                        "America/Los_Angeles", "UTC"]
    static let zones = names.map { TimeZone(identifier: $0)! }

    private func pattern() throws -> AlertPattern {
        let sample = "You charged SGD 4.80 at KOPITIAM on 12/10/2026 ok"
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "SGD", "4.80": tags[i] = .amount
            case "KOPITIAM": tags[i] = .payee
            case "12", "/", "10", "2026": tags[i] = .date
            default: break
            }
        }
        return AlertPattern(name: "p", direction: .outflow, accountID: "a",
                            segments: try PatternBuilder.segments(
                                in: sample, tokens: tokens,
                                spans: PatternBuilder.spans(from: tags, tokens: tokens)),
                            sampleText: sample)
    }

    /// The printed day survives parsing in one zone and every later read in
    /// another: the YNAB string, the stored form read back, the display.
    func test_a_printed_date_never_shifts_between_zones() throws {
        let p = try pattern()
        let arrived = Date(timeIntervalSince1970: 1_791_763_200)   // 2026-10-12T00:00Z
        for parseZone in Self.zones {
            guard case .success(let t) = Matcher.test(p, text: "You charged SGD 9.00 at TOAST BOX on 12/10/2026 ok",
                                                      receivedAt: arrived, zone: parseZone) else {
                return XCTFail("no match in \(parseZone.identifier)")
            }
            XCTAssertEqual(t.date.iso, "2026-10-12", parseZone.identifier)
            let stored = t.date.storedInstant
            for readZone in Self.zones {
                let read = CalendarDate(stored: stored, isDay: true, legacyZone: readZone)
                XCTAssertEqual(read, t.date, "\(parseZone.identifier) -> \(readZone.identifier)")
                XCTAssertEqual(read.iso, "2026-10-12")
            }
            // the display reads the stored form in UTC, never the day before
            XCTAssertTrue(t.date.formatted(date: .abbreviated).contains("12"))
        }
    }

    /// With no printed date, the row takes the day the alert arrived where
    /// the phone was: one instant is a different day in Kiritimati and in
    /// Los Angeles.
    func test_an_alert_without_a_date_takes_its_arrival_day_where_the_phone_was() throws {
        let sample = "You charged SGD 4.80 at KOPITIAM ok"
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "SGD", "4.80": tags[i] = .amount
            case "KOPITIAM": tags[i] = .payee
            default: break
            }
        }
        let p = AlertPattern(name: "p", direction: .outflow, accountID: "a",
                             segments: try PatternBuilder.segments(
                                in: sample, tokens: tokens,
                                spans: PatternBuilder.spans(from: tags, tokens: tokens)),
                             sampleText: sample)
        let arrived = Date(timeIntervalSince1970: 1_798_747_200)   // 2026-12-31T20:00Z
        let want = ["Asia/Singapore": "2027-01-01", "Europe/London": "2026-12-31",
                    "Pacific/Kiritimati": "2027-01-01", "America/Los_Angeles": "2026-12-31",
                    "UTC": "2026-12-31"]
        for (name, zone) in zip(Self.names, Self.zones) {
            guard case .success(let t) = Matcher.test(p, text: sample, receivedAt: arrived, zone: zone) else {
                return XCTFail("no match in \(name)")
            }
            XCTAssertEqual(t.date.iso, want[name], name)
        }
    }

    /// Across New Year a yearless alert keeps last year, in every zone.
    func test_a_yearless_alert_across_new_year() throws {
        let sample = "Card txn SGD 4.80 at KOPITIAM on 31 Dec 23:58"
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "SGD", "4.80": tags[i] = .amount
            case "KOPITIAM": tags[i] = .payee
            case "31", "Dec": tags[i] = .date
            default: break
            }
        }
        let p = AlertPattern(name: "p", direction: .outflow, accountID: "a",
                             segments: try PatternBuilder.segments(
                                in: sample, tokens: tokens,
                                spans: PatternBuilder.spans(from: tags, tokens: tokens)),
                             sampleText: sample)
        // ingested a few minutes into 1 Jan 2027 in each zone
        for zone in Self.zones {
            var cal = Calendar(identifier: .gregorian); cal.timeZone = zone
            let arrived = cal.date(from: DateComponents(year: 2027, month: 1, day: 1, minute: 5))!
            guard case .success(let t) = Matcher.test(p, text: sample, receivedAt: arrived, zone: zone) else {
                return XCTFail("no match in \(zone.identifier)")
            }
            XCTAssertEqual(t.date.iso, "2026-12-31", zone.identifier)
        }
        // review on #50: a saved alert re-read after travel keeps the day it
        // arrived. Arriving on 1 Jan 2027 in Kiritimati is 31 Dec 2026 in Los
        // Angeles. A bank a day ahead may print "2 Jan": against the saved
        // day it is 2027; against Los Angeles' day it would roll to 2026
        let la = TimeZone(identifier: "America/Los_Angeles")!
        let arrived = Date(timeIntervalSince1970: 1_798_711_500)   // 2026-12-31T10:05Z
        let jan = "Card txn SGD 4.80 at KOPITIAM on 2 Jan 00:02"
        guard case .matched(let t) = Matcher.match(
            text: jan, source: nil, receivedAt: arrived, zone: la,
            arrivedOn: CalendarDate(year: 2027, month: 1, day: 1)!, patterns: [p]) else {
            return XCTFail("no match")
        }
        XCTAssertEqual(t.date.iso, "2027-01-02")
        guard case .matched(let u) = Matcher.match(
            text: jan, source: nil, receivedAt: arrived, zone: la, patterns: [p]) else {
            return XCTFail("no match")
        }
        XCTAssertEqual(u.date.iso, "2026-01-02", "without the saved day the year rolls back")
    }

    /// A row an earlier build wrote (midnight in the phone's zone then) reads
    /// back as the same day while the phone stays in that zone, as it did
    /// before, so upgrading moves no queued row.
    func test_a_row_from_an_earlier_build_reads_as_it_did() {
        for (name, zone) in zip(Self.names, Self.zones) {
            var cal = Calendar(identifier: .gregorian); cal.timeZone = zone
            let legacy = cal.date(from: DateComponents(year: 2026, month: 10, day: 12))!
            XCTAssertEqual(CalendarDate(stored: legacy, isDay: false, legacyZone: zone).iso, "2026-10-12", name)
        }
    }

    func test_iso_round_trips_and_refuses_junk() {
        XCTAssertEqual(CalendarDate(iso: "2028-02-29")?.iso, "2028-02-29")
        for junk in ["2027-02-29", "2026-1-05", "2026-13-01", "20261005", "2026-10-05T00:00", ""] {
            XCTAssertNil(CalendarDate(iso: junk), junk)
        }
        XCTAssertEqual(CalendarDate(year: 2026, month: 12, day: 31)!.adding(days: 1).iso, "2027-01-01")
        XCTAssertLessThan(CalendarDate(year: 2026, month: 12, day: 31)!, CalendarDate(year: 2027, month: 1, day: 1)!)
    }
}
