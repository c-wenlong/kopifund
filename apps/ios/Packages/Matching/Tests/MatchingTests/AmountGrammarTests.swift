import XCTest
@testable import Matching

/// Audit: one amount grammar for the detector, the capture and the reading,
/// so a pre-tagged amount always builds a pattern that reads its own
/// sample. Invented alerts only.
final class AmountGrammarTests: XCTestCase {

    private func build(_ text: String, direction: AlertPattern.Direction = .outflow,
                       tag: (Token) -> PatternBuilder.Field?) throws -> AlertPattern {
        let tokens = Tokenizer.tokenize(text)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() { if let f = tag(t) { tags[i] = f } }
        return AlertPattern(name: "p", direction: direction, accountID: "a",
                            segments: try PatternBuilder.segments(
                                in: text, tokens: tokens,
                                spans: PatternBuilder.spans(from: tags, tokens: tokens)),
                            sampleText: text)
    }

    /// Build from the detector's own confident amount and payee, then read
    /// the sample back: the amount must come out as the grammar reads the
    /// pre-tagged text.
    private func roundTrip(_ text: String, file: StaticString = #filePath, line: UInt = #line) throws -> Decimal? {
        let tokens = Tokenizer.tokenize(text)
        let cands = Candidates.detect(in: text, tokens: tokens)
        guard let amount = cands.first(where: { $0.field == .amount && $0.confident }),
              let payee = cands.first(where: { $0.field == .payee }) else {
            XCTFail("no confident amount or payee in \(text)", file: file, line: line)
            return nil
        }
        var tags: [Int: PatternBuilder.Field] = [:]
        for i in amount.tokenIndexes { tags[i] = .amount }
        for i in payee.tokenIndexes where tags[i] == nil { tags[i] = .payee }
        let segs = try PatternBuilder.segments(in: text, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        let p = AlertPattern(name: "p", direction: .outflow, accountID: "a", segments: segs, sampleText: text)
        guard case .success(let t) = Matcher.test(p, text: text) else {
            XCTFail("the pre-tagged pattern does not read its own sample: \(text)", file: file, line: line)
            return nil
        }
        let lo = tokens[amount.tokenIndexes.first!].range.lowerBound
        let hi = tokens[amount.tokenIndexes.last!].range.upperBound
        XCTAssertEqual(t.amount, Amounts.read(String(text[lo..<hi]))?.magnitude, text, file: file, line: line)
        return t.amount
    }

    func test_pre_tagged_amounts_read_their_own_sample() throws {
        XCTAssertEqual(try roundTrip("You spent RM 45.20 at KEDAI MAKAN on 27 Oct 2026"), Decimal(string: "45.20"))
        XCTAssertEqual(try roundTrip("You charged SGD 4.80 at KOPITIAM on 27 Oct 2026"), Decimal(string: "4.80"))
        XCTAssertEqual(try roundTrip("Paid S$1,234.50 to FAIRMART FINEST today"), Decimal(string: "1234.50"))
        XCTAssertEqual(try roundTrip("A card txn of SGD38.17 to HVC CLINIC on 26 Oct"), Decimal(string: "38.17"))
        XCTAssertEqual(try roundTrip("Spent US$ 12.00 at BLUE BOTTLE in Tokyo"), Decimal(string: "12.00"))
    }

    /// A reference glued to letters is not money, and a figure the matcher
    /// cannot read is never pre-tagged.
    func test_unreadable_shapes_are_not_pre_tagged() {
        for text in ["Ref AB123456 confirmed for KOPITIAM",
                     "Order 1234567890123.00 at SHOP",
                     "Spent KWD 1.255 at SOUQ"] {
            let tokens = Tokenizer.tokenize(text)
            let confident = Candidates.amounts(in: text, tokens: tokens).filter(\.confident)
            XCTAssertTrue(confident.isEmpty, "\(text): \(confident)")
        }
    }

    /// Audit: "-S$4.50" on an outflow pattern used to keep its minus, so the
    /// sync negated it again and posted money in. The amount is the size;
    /// the direction is the pattern's.
    func test_a_printed_minus_is_not_a_second_sign() throws {
        let p = try build("Paid -S$4.50 at Toast Box") { t in
            switch t.text {
            case "-", "S", "$", "4.50": return .amount
            case "Toast", "Box": return .payee
            default: return nil
            }
        }
        guard case .success(let t) = Matcher.test(p, text: "Paid -S$12.30 at Ya Kun") else {
            return XCTFail("should read")
        }
        XCTAssertEqual(t.amount, Decimal(string: "12.30"))
        XCTAssertEqual(t.direction, .outflow)
    }

    /// Audit: a third decimal used to break the anchor after it, or get cut
    /// to two when a payee followed. It now reads exactly and is refused
    /// with a reason, since the row keeps cents.
    func test_a_third_decimal_is_refused_not_cut() throws {
        let p = try build("Spent KWD 1.255 at SOUQ today") { t in
            switch t.text {
            case "KWD", "1.255": return .amount
            case "SOUQ": return .payee
            default: return nil
            }
        }
        guard case .failure(let e) = Matcher.test(p, text: "Spent KWD 1.255 at SOUQ today") else {
            return XCTFail("a third decimal must not be cut")
        }
        XCTAssertTrue(e.reason.contains("3 decimals"), e.reason)
        let last = try build("Paid SOUQ total KWD 1.250") { t in
            switch t.text {
            case "KWD", "1.250": return .amount
            case "SOUQ": return .payee
            default: return nil
            }
        }
        guard case .failure = Matcher.test(last, text: "Paid SOUQ total KWD 1.255") else {
            return XCTFail("an amount that ends the template must not be cut to 1.25 either")
        }
    }

    /// A figure past twelve digits never reaches the conversions to cents and
    /// milliunits, where it used to wrap to a wrong number.
    func test_a_huge_figure_does_not_match() throws {
        let p = try build("Spent SGD 4.50 at SHOP ok") { t in
            switch t.text {
            case "SGD", "4.50": return .amount
            case "SHOP": return .payee
            default: return nil
            }
        }
        guard case .failure = Matcher.test(p, text: "Spent SGD 99999999999999999999 at SHOP ok") else {
            return XCTFail("a 20-digit figure must not read")
        }
        guard case .success(let t) = Matcher.test(p, text: "Spent SGD 999,999,999.99 at SHOP ok") else {
            return XCTFail("a large real figure still reads")
        }
        XCTAssertEqual(t.amount, Decimal(string: "999999999.99"))
    }

    /// Review on #69: an amount that ends the template has no anchor after
    /// it, so an oversized figure there must fail rather than read its
    /// first twelve digits.
    func test_a_huge_figure_at_the_template_end_does_not_match() throws {
        let p = try build("Paid SOUQ total KWD 12.50") { t in
            switch t.text {
            case "KWD", "12.50": return .amount
            case "SOUQ": return .payee
            default: return nil
            }
        }
        for text in ["Paid SOUQ total KWD 1234567890123.00", "Paid SOUQ total KWD 1,234,567,890,123.00",
                     "Paid SOUQ total KWD 12.5000"] {
            if case .success(let t) = Matcher.test(p, text: text) {
                XCTFail("\(text) read as \(t.amount)")
            }
        }
        guard case .success(let t) = Matcher.test(p, text: "Paid SOUQ total KWD 12.50") else {
            return XCTFail("the sample still reads")
        }
        XCTAssertEqual(t.amount, Decimal(string: "12.50"))
    }

    /// Ringgit, the most common foreign currency for an SG user, reads with
    /// its ISO code; a zero-decimal yen amount reads whole.
    func test_ringgit_and_yen_read() throws {
        let p = try build("You spent RM 45.20 at KEDAI MAKAN ok") { t in
            switch t.text {
            case "RM", "45.20": return .amount
            case "KEDAI", "MAKAN": return .payee
            default: return nil
            }
        }
        guard case .success(let t) = Matcher.test(p, text: "You spent RM 8.00 at NASI LEMAK ok") else {
            return XCTFail("ringgit should read")
        }
        XCTAssertEqual(t.currency, "MYR")
        XCTAssertEqual(t.amount, 8)
        guard case .success(let y) = Matcher.test(p, text: "You spent JPY 1,200 at LAWSON ok") else {
            return XCTFail("yen should read")
        }
        XCTAssertEqual(y.amount, 1200)
        XCTAssertEqual(y.currency, "JPY")
    }
}
