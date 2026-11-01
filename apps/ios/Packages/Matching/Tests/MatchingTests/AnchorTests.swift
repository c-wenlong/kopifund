import XCTest
@testable import Matching

/// G24: the anchors around a capture are regex, not verbatim text. The words
/// stay fixed while the pieces that change between alerts (references,
/// counters, dates) fold into wildcards, so a new alert with the same shape
/// keeps matching. Invented alerts only, never a real inbox copy.
final class AnchorTests: XCTestCase {

    private func pattern(from text: String,
                         tag: (Int, Token) -> PatternBuilder.Field?) throws -> AlertPattern {
        let tokens = Tokenizer.tokenize(text)
        var tags: [Int: PatternBuilder.Field] = [:]
        for (i, t) in tokens.enumerated() { if let f = tag(i, t) { tags[i] = f } }
        let segs = try PatternBuilder.segments(in: text, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        return AlertPattern(name: "p", direction: .outflow, accountID: "a",
                            segments: segs, sampleText: text)
    }

    /// A reference number the bank changes every alert sits inside untagged
    /// text. It used to pin the literal and kill every later alert.
    func test_a_reference_inside_the_anchor_flexes() throws {
        let mail = "Sent SGD 4.80 to Ah Ma Noodles on 27 Oct 2026. Ref TXN-8842-1027. Balance ok"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Ah", "Ma", "Noodles": return .payee
            case "27", "Oct", "2026": return .date
            default: return nil
            }
        }
        let next = "Sent SGD 9.00 to Toast Box on 3 Nov 2026. Ref TXN-9911-1103. Balance ok"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t):
            XCTAssertEqual(t.payee, "Toast Box")
            XCTAssertEqual(t.amount, Decimal(string: "9"))
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    /// An untagged date between two fields must not anchor the pattern to
    /// the sample's date: it folds to its own date shape.
    func test_an_untagged_date_between_fields_flexes() throws {
        let mail = "Paid SGD 4.80 on 27 Oct 2026 to Ah Ma Noodles"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Ah", "Ma", "Noodles": return .payee
            default: return nil   // "on 27 Oct 2026" stays untagged
            }
        }
        let next = "Paid SGD 12.40 on 3 Nov 2026 to Toast Box"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "Toast Box")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    /// The fix for changing words beside the payee is leaving them untagged:
    /// "ref" anchors the border while its number folds inside the literal.
    func test_the_payee_border_anchors_on_fixed_words() throws {
        let mail = "You paid SGD 4.80 to Ah Ma Noodles ref TXN8842 on 27 Oct 2026"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Ah", "Ma", "Noodles": return .payee
            case "27", "Oct", "2026": return .date
            default: return nil   // "ref TXN8842" untagged
            }
        }
        let next = "You paid SGD 9.00 to Toast Box ref TXN9911 on 3 Nov 2026"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "Toast Box")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    /// A wildcard span touching the payee leaves the regex no way to draw the
    /// border the tags meant; the build refuses instead of saving a pattern
    /// that captures one letter.
    func test_a_wildcard_span_beside_the_payee_is_refused() {
        let mail = "You paid SGD 4.80 to Ah Ma Noodles ref TXN8842 on 27 Oct 2026"
        XCTAssertThrowsError(try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Ah", "Ma", "Noodles": return .payee
            case "ref", "TXN8842": return .ignore
            case "27", "Oct", "2026": return .date
            default: return nil
            }
        }) { e in XCTAssertEqual(e as? PatternBuilder.BuildError, .wildcardBesidePayee) }
    }

    /// A changing piece right beside the payee is as ambiguous as an ignore
    /// span: "\S*\d\S*" can start inside the name, so "Toast Box/8842" would
    /// capture "Toast". The build refuses until a fixed word stands there.
    func test_a_folding_piece_beside_the_payee_is_refused() {
        let mail = "Paid SGD 4.80 to Toast Box/8842 on 27 Oct 2026"
        XCTAssertThrowsError(try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Toast", "Box": return .payee
            case "27", "Oct", "2026": return .date
            default: return nil   // "/8842" untagged folds to a wildcard
            }
        }) { e in XCTAssertEqual(e as? PatternBuilder.BuildError, .wildcardBesidePayee) }
    }

    /// The blur works backwards too: an ignore span right before the payee
    /// can match empty, letting the capture swallow the wildcard's words.
    func test_a_wildcard_before_the_payee_is_refused() {
        let mail = "Paid SGD 4.80 to POS Toast Box ref done"
        XCTAssertThrowsError(try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "POS": return .ignore
            case "Toast", "Box": return .payee
            default: return nil
            }
        }) { e in XCTAssertEqual(e as? PatternBuilder.BuildError, .wildcardBesidePayee) }
    }

    /// A reference on the next line cannot reach the payee: the required
    /// line break bounds the capture first, so the folded piece after it
    /// stays allowed.
    func test_a_reference_on_the_next_line_keeps_the_border() throws {
        let mail = "Paid SGD 4.80 to Toast Box\nTXN8842 settled"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Toast", "Box": return .payee
            default: return nil
            }
        }
        let next = "Paid SGD 9.00 to KOPITIAM\nTXN9911 settled"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "KOPITIAM")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    /// The wallet line carries punctuation the account field must hold:
    /// "PayLah! Wallet (Mobile ending 2468)" could not fit the old hint
    /// class and the whole pattern failed to read its own sample.
    func test_an_account_string_with_punctuation_reads_and_pins() throws {
        let mail = "Paid SGD2.20\nFrom:\nPayLah! Wallet (Mobile ending 2468)\nTo:\nSUNRISE ICE BLENDS\nRef kept"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD2", ".", "20": return .amount
            case "PayLah", "!", "Wallet", "(", "Mobile", "ending", "2468", ")":
                return .account
            case "SUNRISE", "ICE", "BLENDS": return .payee
            default: return nil
            }
        }
        switch Matcher.match(text: mail, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t):
            XCTAssertEqual(t.payee, "SUNRISE ICE BLENDS")
            XCTAssertEqual(t.accountHint, "PayLah! Wallet (Mobile ending 2468)")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
        let next = "Paid SGD9.00\nFrom:\nPayLah! Wallet (Mobile ending 2468)\nTo:\nKOPITIAM\nRef kept"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "KOPITIAM")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
        let other = "Paid SGD9.00\nFrom:\nPayLah! Wallet (Mobile ending 9999)\nTo:\nKOPITIAM\nRef kept"
        switch Matcher.match(text: other, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTFail("wrong wallet matched: \(t.payee)")
        case .unmatched: break
        }
    }

    /// The card capture blurs into a neighbouring wildcard exactly like the
    /// payee, so the same refusal applies.
    func test_a_wildcard_beside_the_card_is_refused() {
        let mail = "Paid SGD 4.80 junk 8842 1234 done"
        XCTAssertThrowsError(try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "junk", "8842": return .ignore
            case "1234": return .account
            case "done": return .payee
            default: return nil
            }
        }) { e in XCTAssertEqual(e as? PatternBuilder.BuildError, .wildcardBesideCard) }
    }

    /// A wildcard on the next line is safe: the literal keeps the sample's
    /// line break as a required break, so the payee still ends at its line
    /// end even with an ignore span following.
    func test_a_wildcard_on_the_next_line_keeps_the_payee_border() throws {
        let mail = "Paid SGD 4.80\nTo Ah Ma Noodles\nRef 8842 settled"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Ah", "Ma", "Noodles": return .payee
            case "Ref", "8842": return .ignore
            default: return nil
            }
        }
        let next = "Paid SGD 9.00\nTo Toast Box\nRef 9911 settled"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "Toast Box")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    /// Digits inside a literal flex, so two cards that print only their last
    /// digits stay separate only when the number is tagged Card: the hint
    /// must equal the sample's or the pattern rejects the alert.
    func test_a_tagged_card_keeps_patterns_separate() throws {
        let mail = "Spent SGD 4.80 card ending 1234 to KOPITIAM on 27 Oct 2026"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "1234": return .account
            case "KOPITIAM": return .payee
            case "27", "Oct", "2026": return .date
            default: return nil
            }
        }
        let otherCard = "Spent SGD 9.00 card ending 5678 to KOPITIAM on 3 Nov 2026"
        switch Matcher.match(text: otherCard, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTFail("wrong card matched: \(t.payee)")
        case .unmatched: break
        }
        let sameCard = "Spent SGD 9.00 card ending 1234 to TOAST BOX on 3 Nov 2026"
        switch Matcher.match(text: sameCard, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "TOAST BOX")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    /// A payee never spans lines, so a new line appearing between the payee
    /// and its next anchor cannot be absorbed into the name. The alert
    /// lands in Unmatched for re-teaching rather than posting a junk payee.
    func test_a_payee_capture_cannot_swallow_the_next_line() throws {
        let mail = "Paid SGD 4.80\nTo Ah Ma Noodles\nRef kept fixed"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Ah", "Ma", "Noodles": return .payee
            default: return nil
            }
        }
        let next = "Paid SGD 9.00\nTo Toast Box\nBalance SGD 90.20\nRef kept fixed"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTFail("swallowed a line: \(t.payee)")
        case .unmatched: break
        }
    }

    /// The same payee on its own line still reads cleanly.
    func test_a_payee_capture_reads_to_its_line_end() throws {
        let mail = "Paid SGD 4.80\nTo Ah Ma Noodles\nRef kept fixed"
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "Ah", "Ma", "Noodles": return .payee
            default: return nil
            }
        }
        let next = "Paid SGD 9.00\nTo Toast Box\nRef kept fixed"
        switch Matcher.match(text: next, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "Toast Box")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }

    /// A trailing space after the tagged card is a literal that can match
    /// empty, so the lazy capture in front of it shrank "1234" to "1" and
    /// any other card's first digit equalled it. Whitespace-only literals
    /// at the tail must leave the capture greedy to its line's end.
    func test_a_card_followed_only_by_spaces_still_separates() throws {
        let mail = "Paid SGD 4.80 at KOPITIAM\nCard 1234 "
        let p = try pattern(from: mail) { _, t in
            switch t.text {
            case "SGD", "4.80": return .amount
            case "KOPITIAM": return .payee
            case "1234": return .account
            default: return nil
            }
        }
        let other = "Paid SGD 9.00 at KOPITIAM\nCard 1999 "
        switch Matcher.match(text: other, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTFail("wrong card matched: \(t.payee)")
        case .unmatched: break
        }
        let same = "Paid SGD 9.00 at TOAST BOX\nCard 1234 "
        switch Matcher.match(text: same, source: "mail", receivedAt: Date(), patterns: [p]) {
        case .matched(let t): XCTAssertEqual(t.payee, "TOAST BOX")
        case .unmatched(let r): XCTFail("unmatched: \(r)")
        }
    }
}

final class LiteralPatternTests: XCTestCase {
    func test_words_and_punctuation_stay_anchors() {
        XCTAssertEqual(LiteralPattern.pattern(for: "card ending on"),
                       #"card\s+ending\s+on"#)
    }

    func test_a_digit_run_folds() {
        XCTAssertEqual(LiteralPattern.pattern(for: "Ref TXN-8842-1027. Balance"),
                       #"Ref\s+\S*\d\S*\s+Balance"#)
    }

    func test_a_date_run_folds_to_its_shape() {
        XCTAssertEqual(LiteralPattern.pattern(for: "dated 27 Oct 2026."),
                       #"dated\s+(?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\."#)
    }

    /// Punctuation broke the date window, so the pieces fold one by one and
    /// the month name rides along instead of pinning "Oct" down.
    func test_a_split_date_keeps_the_month_flexible() {
        XCTAssertEqual(LiteralPattern.pattern(for: "(27 Oct 2026)"),
                       #"\S*\d\S*\s+[A-Za-z]{3,9}\s+\S*\d\S*"#)
    }
}
