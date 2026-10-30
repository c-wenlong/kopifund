import XCTest
@testable import Matching

final class TokenizerTests: XCTestCase {
    func test_words_numbers_punct_and_space_are_tokens() {
        let tokens = Tokenizer.tokenize("S$4.50 at FOO")
        XCTAssertEqual(tokens.map(\.text), ["S", "$", "4.50", " ", "at", " ", "FOO"])
        XCTAssertEqual(tokens.map(\.kind),
                       [.word, .punct, .number, .whitespace, .word, .whitespace, .word])
    }

    func test_thousands_and_decimals_stay_one_number() {
        let tokens = Tokenizer.tokenize("SGD 1,234.50")
        XCTAssertEqual(tokens.map(\.text), ["SGD", " ", "1,234.50"])
    }
}

final class BuilderTests: XCTestCase {
    let sample = "You charged S$4.50 at KOPITIAM on 12 Oct 2026. Ref AB12"

    private func taggedSpans() -> ([Token], [Int: PatternBuilder.Field]) {
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "S", "$", "4.50": tags[i] = .amount
            case "KOPITIAM": tags[i] = .payee
            case "12", "Oct", "2026": tags[i] = .date
            default: break
            }
        }
        return (tokens, tags)
    }

    func test_date_span_merges_over_spaces() {
        let (tokens, tags) = taggedSpans()
        let spans = PatternBuilder.spans(from: tags, tokens: tokens)
        XCTAssertEqual(spans.map(\.field), [.amount, .payee, .date])
        let dateSpan = spans[2]
        XCTAssertEqual(PatternBuilder.spanText(in: sample, tokens: tokens, span: dateSpan), "12 Oct 2026")
    }

    func test_segments_carry_typed_captures_and_detect_the_date_format() throws {
        let (tokens, tags) = taggedSpans()
        let segs = try PatternBuilder.segments(in: sample, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        XCTAssertTrue(segs.contains(.amount))
        XCTAssertTrue(segs.contains(.payee))
        XCTAssertTrue(segs.contains(.date(.ddMmmYyyy)))
    }

    func test_a_missing_amount_stops_the_build() {
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() where t.text == "KOPITIAM" { tags[i] = .payee }
        XCTAssertThrowsError(try PatternBuilder.segments(in: sample, tokens: tokens,
                                                         spans: PatternBuilder.spans(from: tags, tokens: tokens)))
    }

    func test_a_short_edge_literal_stays_an_anchor() throws {
        let (tokens, tags) = taggedSpans()
        let segs = try PatternBuilder.segments(in: sample, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        XCTAssertEqual(segs.first, .literal("You charged "))
    }

    func test_a_long_edge_literal_sheds_to_a_wildcard() throws {
        let long = "Dear Customer, your recent transaction of S$4.50 at KOPITIAM on 12 Oct 2026 was successful"
        let tokens = Tokenizer.tokenize(long)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "S", "$", "4.50": tags[i] = .amount
            case "KOPITIAM": tags[i] = .payee
            default: break
            }
        }
        let segs = try PatternBuilder.segments(in: long, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        XCTAssertEqual(segs.first, .ignore, "the greeting is longer than the edge anchor")
    }
}

final class MatcherTests: XCTestCase {
    /// An invented alert in the shape bank SMS messages take. Real DBS, OCBC
    /// and UOB samples replace this fixture when the owner pastes them. The
    /// tagged account is a number the card keeps, so the reference beside it
    /// stays untagged and free to change.
    let sample = "You charged S$4.50 at KOPITIAM on 12 Oct 2026 acct AB12 ref TXN-77"

    private func pattern(priority: Int = 0, sourceMatch: String? = nil,
                         autoApprove: Bool = false) throws -> AlertPattern {
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "S", "$", "4.50": tags[i] = .amount
            case "KOPITIAM": tags[i] = .payee
            case "12", "Oct", "2026": tags[i] = .date
            case "AB12": tags[i] = .account
            default: break
            }
        }
        let segs = try PatternBuilder.segments(in: sample, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        return AlertPattern(name: "Card spend", direction: .outflow, accountID: "acct-1",
                            sourceMatch: sourceMatch, priority: priority,
                            autoApprove: autoApprove, segments: segs, sampleText: sample)
    }

    func test_the_sample_it_built_from_matches() throws {
        let p = try pattern()
        switch Matcher.match(text: sample, source: "sms", receivedAt: Date(), patterns: [p]) {
        case .matched(let t):
            XCTAssertEqual(t.payee, "KOPITIAM")
            XCTAssertEqual(t.amount, Decimal(string: "4.50"))
            XCTAssertEqual(t.accountHint, "AB12")
            XCTAssertEqual(t.direction, .outflow)
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    func test_a_fresh_alert_with_the_same_shape_matches() throws {
        let p = try pattern()
        let next = "You charged S$1,234.50 at NTUC FAIRPRICE on 3 Jan 2027 acct AB12 ref TXN-99"
        switch Matcher.match(text: next, source: "sms", receivedAt: Date(), patterns: [p]) {
        case .matched(let t):
            XCTAssertEqual(t.payee, "NTUC FAIRPRICE")
            XCTAssertEqual(t.amount, Decimal(string: "1234.50"))
            XCTAssertEqual(Calendar.current.component(.day, from: t.date), 3)
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    func test_priority_picks_the_winner() throws {
        let low = try pattern(priority: 0)
        var high = try pattern(priority: 10)
        high.name = "wins"
        high.accountID = "acct-2"
        switch Matcher.match(text: sample, source: "sms", receivedAt: Date(), patterns: [low, high]) {
        case .matched(let t): XCTAssertEqual(t.patternName, "wins")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    func test_source_filter_skips_the_pattern() throws {
        let p = try pattern(sourceMatch: "BankAlerts")
        switch Matcher.match(text: sample, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched: XCTFail("should not match")
        case .unmatched(let r):
            XCTAssertEqual(r.first?.reason.contains("BankAlerts"), true)
        }
    }

    func test_no_date_span_falls_back_to_receivedAt() throws {
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "S", "$", "4.50": tags[i] = .amount
            case "KOPITIAM": tags[i] = .payee
            default: break
            }
        }
        let segs = try PatternBuilder.segments(in: sample, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        let p = AlertPattern(name: "no date", direction: .outflow, accountID: "a",
                             segments: segs, sampleText: sample)
        let when = Date(timeIntervalSince1970: 1_700_000_000)
        switch Matcher.match(text: sample, source: nil, receivedAt: when, patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.date, when)
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    func test_garbage_text_reports_rejections() throws {
        let p = try pattern()
        switch Matcher.match(text: "your OTP is 123456", source: "sms", receivedAt: Date(), patterns: [p]) {
        case .matched: XCTFail("should not match")
        case .unmatched(let r): XCTAssertEqual(r.count, 1)
        }
    }

    /// A DBS card SMS, worded the way the bank prints it. The payee must land
    /// verbatim (all caps kept), the currency read off the amount, and the
    /// tagged card digits land in the hint.
    func test_a_dbs_style_alert_keeps_case_currency_and_card() throws {
        let sample = "A card txn of SGD44.53 from DBS/POSB card ending 6529 to SGH - DIGESTIVE & LIVE on 26 Oct 11:24 (SGT) was completed."
        let tokens = Tokenizer.tokenize(sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() {
            switch t.text {
            case "SGD44", "53": tags[i] = .amount
            case "." where i > 0 && tokens[i - 1].text == "SGD44": tags[i] = .amount
            case "6529": tags[i] = .account
            case "SGH", "-", "DIGESTIVE", "&", "LIVE": tags[i] = .payee
            case "26", "Oct": tags[i] = .date
            case "11", ":", "24": tags[i] = .ignore
            default: break
            }
        }
        let segs = try PatternBuilder.segments(in: sample, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        let p = AlertPattern(name: "DBS card", direction: .outflow, accountID: "a",
                             segments: segs, sampleText: sample)
        let next = "A card txn of SGD10.00 from DBS/POSB card ending 6529 to NTUC FAIRPRICE on 27 Oct 09:01 (SGT) was completed."
        switch Matcher.match(text: next, source: "sms", receivedAt: Date(), patterns: [p]) {
        case .matched(let t):
            XCTAssertEqual(t.payee, "NTUC FAIRPRICE")
            XCTAssertEqual(t.amount, Decimal(string: "10.00"))
            XCTAssertEqual(t.currency, "SGD")
            XCTAssertEqual(t.accountHint, "6529")
            XCTAssertEqual(t.alertText, next)
            XCTAssertEqual(t.source, "sms")
            XCTAssertEqual(Calendar.current.component(.day, from: t.date), 27)
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }
}

final class DedupTests: XCTestCase {
    func test_normalised_payee_drops_case_and_punctuation() {
        XCTAssertEqual(Dedup.normalizedPayee("SGH - DIGESTIVE & LIVE"), "sghdigestivelive")
        XCTAssertEqual(Dedup.normalizedPayee("Sgh - Digestive & Live"), "sghdigestivelive")
    }

    func test_payee_similarity() {
        XCTAssertTrue(Dedup.similarPayee("Sgh - Digestive & Live", "SGH - DIGESTIVE & LIVE"))
        XCTAssertTrue(Dedup.similarPayee("AMAZON SG", "Amazon.sg Marketplace"))
        XCTAssertFalse(Dedup.similarPayee("SGH", "Grab"))
        XCTAssertFalse(Dedup.similarPayee("7-Eleven", "Shell"))
    }

    func test_two_channels_of_one_spend_match() {
        // a Wallet row's timestamp and an alert's day-precision date share a
        // calendar day wherever the phone's zone puts midnight
        var cal = Calendar(identifier: .gregorian); cal.timeZone = .current
        let late = cal.date(from: DateComponents(year: 2026, month: 10, day: 26, hour: 23))!
        let early = cal.date(from: DateComponents(year: 2026, month: 10, day: 26, hour: 0))!
        XCTAssertTrue(Dedup.likelyDuplicate(
            amountCents: 4453, currency: "SGD", date: late, payee: "Sgh - Digestive & Live",
            otherAmountCents: 4453, otherCurrency: "SGD",
            otherDate: early, otherPayee: "SGH - DIGESTIVE & LIVE"))
    }

    func test_same_day_same_amount_different_payee_is_not_a_dupe() {
        let day = Date(timeIntervalSince1970: 1_777_000_000)
        XCTAssertFalse(Dedup.likelyDuplicate(
            amountCents: 650, currency: "SGD", date: day, payee: "NTUC",
            otherAmountCents: 650, otherCurrency: "SGD", otherDate: day, otherPayee: "Grab"))
    }

    func test_different_day_or_amount_is_not_a_dupe() {
        let day = Date(timeIntervalSince1970: 1_777_000_000)
        let next = day.addingTimeInterval(2 * 86_400)
        XCTAssertFalse(Dedup.likelyDuplicate(
            amountCents: 4453, currency: "SGD", date: day, payee: "SGH",
            otherAmountCents: 4453, otherCurrency: "SGD", otherDate: next, otherPayee: "SGH"))
        XCTAssertFalse(Dedup.likelyDuplicate(
            amountCents: 4453, currency: "SGD", date: day, payee: "SGH",
            otherAmountCents: 4454, otherCurrency: "SGD", otherDate: day, otherPayee: "SGH"))
    }
}

/// G25: an alert is covered when a loaded statement row agrees on amount,
/// currency, day and payee — and a record row covers at most one alert.
final class RecordCoverTests: XCTestCase {
    private let day = Date(timeIntervalSince1970: 1_777_000_000)

    private func spend(_ amount: Int = -4453, _ currency: String = "SGD",
                       payee: String = "", raw: String = "") -> Dedup.RecordSpend {
        Dedup.RecordSpend(amountCents: amount, currency: currency, date: day,
                          payee: payee, raw: raw)
    }

    func test_cover_matches_on_payee_or_raw() {
        let record = [spend(payee: "SGH - Digestive & Live"),
                      spend(-999, payee: "else", raw: "GRAB *RIDE 12.34 SINGAPORE")]
        XCTAssertEqual(Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                       date: day, payee: "Sgh Digestive Live",
                                       in: record, taken: []), 0)
        // the row's payee may be empty when the alert still reads its raw merchant
        XCTAssertEqual(Dedup.coveredBy(amountCents: 999, currency: "SGD", direction: "outflow",
                                       date: day, payee: "Grab Ride",
                                       in: record, taken: []), 1)
    }

    func test_sign_amount_currency_and_day_all_have_to_agree() {
        let record = [spend(payee: "SGH")]
        // an outflow alert never takes an inflow row at the same amount
        XCTAssertNil(Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                     date: day, payee: "SGH",
                                     in: [spend(4453, payee: "SGH")], taken: []))
        XCTAssertNil(Dedup.coveredBy(amountCents: 4453, currency: "USD", direction: "outflow",
                                     date: day, payee: "SGH", in: record, taken: []))
        XCTAssertNil(Dedup.coveredBy(amountCents: 4454, currency: "SGD", direction: "outflow",
                                     date: day, payee: "SGH", in: record, taken: []))
        XCTAssertNil(Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                     date: day.addingTimeInterval(86_400), payee: "SGH",
                                     in: record, taken: []))
        // two real purchases at one amount on one day: the payee still has to agree
        XCTAssertNil(Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                     date: day, payee: "NTUC", in: record, taken: []))
    }

    func test_one_record_spend_covers_one_alert() {
        let record = [spend(payee: "SGH"), spend(payee: "SGH")]
        let first = Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                    date: day, payee: "SGH", in: record, taken: [])
        XCTAssertEqual(first, 0)
        let second = Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                     date: day, payee: "SGH", in: record, taken: [0])
        XCTAssertEqual(second, 1, "the second alert takes the next row, not the taken one")
        XCTAssertNil(Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                     date: day, payee: "SGH", in: record, taken: [0, 1]))
    }

    func test_empty_payee_never_covers() {
        XCTAssertNil(Dedup.coveredBy(amountCents: 4453, currency: "SGD", direction: "outflow",
                                     date: day, payee: "",
                                     in: [spend(payee: "SGH")], taken: []))
    }
}

final class AmountAndDateTests: XCTestCase {
    func test_amount_shapes() {
        XCTAssertEqual(Amounts.read("S$4.50")?.magnitude, Decimal(string: "4.50"))
        XCTAssertEqual(Amounts.read("SGD 1,234.50")?.magnitude, Decimal(string: "1234.50"))
        XCTAssertEqual(Amounts.read("1234.50")?.magnitude, Decimal(string: "1234.50"))
        XCTAssertEqual(Amounts.read("JPY 1,200")?.magnitude, 1200)
        XCTAssertEqual(Amounts.read("JPY 1,200")?.decimals, 0)
        XCTAssertNil(Amounts.read("free"))
        XCTAssertNil(Amounts.read("4.50 SGD"), "a trailing code is anchor text, not part of the capture")
        XCTAssertNil(Amounts.read("1,2,3"))
    }

    func test_amount_currencies() {
        XCTAssertEqual(Amounts.read("SGD44.53")?.currency, "SGD")
        XCTAssertEqual(Amounts.read("S$4.50")?.currency, "SGD")
        XCTAssertEqual(Amounts.read("$4.50")?.currency, "SGD")
        XCTAssertEqual(Amounts.read("US$9.00")?.currency, "USD")
        XCTAssertEqual(Amounts.read("USD 9.00")?.currency, "USD")
        XCTAssertEqual(Amounts.read("RM 45.20")?.currency, "MYR")
        XCTAssertEqual(Amounts.read("4.50")?.currency, nil)
        XCTAssertEqual(Amounts.read("US$\n9.00")?.currency, "USD")
    }

    /// Audit: a printed minus is notation, never a second sign. The amount is
    /// the size; the pattern's direction signs it at post time.
    func test_a_printed_minus_keeps_the_size_and_says_so() {
        let a = Amounts.read("-SGD4.50")
        XCTAssertEqual(a?.magnitude, Decimal(string: "4.50"))
        XCTAssertEqual(a?.printedNegative, true)
        XCTAssertEqual(Amounts.read("-$9")?.magnitude, 9)
    }

    /// Audit: twelve digits before the point at most; a longer figure is a
    /// misread that would overflow cents or milliunits downstream.
    func test_a_huge_figure_is_not_an_amount() {
        XCTAssertEqual(Amounts.read("999,999,999,999.99")?.magnitude, Decimal(string: "999999999999.99"))
        XCTAssertNotNil(Amounts.read("123456789012"))
        XCTAssertNil(Amounts.read("1234567890123"))
        XCTAssertNil(Amounts.read("99999999999999999999"))
        XCTAssertNil(Amounts.read("1,234,567,890,123"))
    }

    func test_three_decimals_read_exactly() {
        let a = Amounts.read("KWD 1.255")
        XCTAssertEqual(a?.magnitude, Decimal(string: "1.255"))
        XCTAssertEqual(a?.decimals, 3)
        XCTAssertNil(Amounts.read("KWD 1.2555"))
    }

    func test_date_formats() {
        for (text, want) in [("12/10/2026", AlertPattern.DateFormat.dmy4Slash),
                             ("12 Oct 2026", .ddMmmYyyy),
                             ("Oct 12, 2026", .mmmDdYyyy),
                             ("2026-10-12", .ymdDash)] as [(String, AlertPattern.DateFormat)] {
            XCTAssertEqual(Dates.detectFormat(text), want, text)
        }
    }

    func test_partial_dates_take_the_fallback_year() {
        let fallback = DateFormatter()
        fallback.dateFormat = "yyyy-MM-dd"
        let when = fallback.date(from: "2026-03-01")!
        let d = Dates.parse("12 Oct", as: .ddMmm, fallback: when)
        XCTAssertEqual(Calendar.current.component(.year, from: d!), 2026)
    }
}
