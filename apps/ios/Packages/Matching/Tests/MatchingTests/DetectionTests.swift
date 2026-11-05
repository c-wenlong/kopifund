import XCTest
@testable import Matching

/// G24: invented alerts only, never a real statement or inbox copy.
final class AlertTextTests: XCTestCase {
    let mail = """
        Subject: Transaction Alert
        Problems viewing this email?

        You charged SGD 4.80 at KOPITIAM on 27 Oct 2026

        If you did not make this transaction, please call our hotline.
        """

    func test_boilerplate_lines_are_recognised() {
        XCTAssertTrue(AlertText.isBoilerplate("Problems viewing this email?"))
        XCTAssertTrue(AlertText.isBoilerplate(
            "If you did not make this transaction, please call our hotline."))
        XCTAssertFalse(AlertText.isBoilerplate("You charged SGD 4.80 at KOPITIAM"))
    }

    func test_meaningful_line_skips_subject_chrome_and_footers() {
        XCTAssertEqual(AlertText.meaningfulLine(in: mail),
                       "You charged SGD 4.80 at KOPITIAM on 27 Oct 2026")
    }

    func test_meaningful_line_strips_fwd_and_subject_prefixes() {
        XCTAssertEqual(AlertText.meaningfulLine(in: "Fwd: Your receipt"),
                       "Your receipt")
    }

    func test_meaningful_line_falls_back_for_non_latin_text() {
        // no ASCII letters anywhere: better the raw line than a blank title
        XCTAssertEqual(AlertText.meaningfulLine(in: "付款成功 $4.80"), "付款成功 $4.80")
    }

    func test_boilerplate_token_indexes_cover_the_footer() {
        let tokens = Tokenizer.tokenize(mail)
        let dimmed = AlertText.boilerplateTokenIndexes(in: mail, tokens: tokens)
        let hotline = tokens.enumerated().first { $0.element.text == "hotline" }!.offset
        let amount = tokens.enumerated().first { $0.element.text == "4.80" }!.offset
        XCTAssertTrue(dimmed.contains(hotline))
        XCTAssertFalse(dimmed.contains(amount))
    }
}

final class AlertShapeTests: XCTestCase {
    func test_same_template_different_numbers_signs_identically() {
        let a = "Build #148 failed on kopifund-api. 3 tests failed."
        let b = "Build #299 failed on kopifund-api. 7 tests failed."
        XCTAssertEqual(AlertShape.signature(of: a), AlertShape.signature(of: b))
    }

    func test_different_templates_diverge() {
        let a = "Build #148 failed on kopifund-api."
        let b = "You charged SGD 4.80 at KOPITIAM."
        XCTAssertNotEqual(AlertShape.signature(of: a), AlertShape.signature(of: b))
    }

    func test_references_with_changing_digits_still_sign_identically() {
        let a = "Deployment AB12 failed, reference 771."
        let b = "Deployment AB34 failed, reference 982."
        XCTAssertEqual(AlertShape.signature(of: a), AlertShape.signature(of: b))
    }
}

final class CandidateTests: XCTestCase {
    private func detect(_ text: String) -> [Candidate] {
        Candidates.detect(in: text, tokens: Tokenizer.tokenize(text))
    }

    private func spanText(_ text: String, _ c: Candidate) -> String {
        let tokens = Tokenizer.tokenize(text)
        let lo = tokens[c.tokenIndexes.first!].range.lowerBound
        let hi = tokens[c.tokenIndexes.last!].range.upperBound
        return text[lo..<hi].trimmingCharacters(in: .whitespaces)
    }

    func test_currency_amount_is_confident() {
        let c = detect("You charged SGD 4.80 at KOPITIAM on 27 Oct 2026")
        let amount = c.first { $0.field == .amount && $0.confident }
        XCTAssertNotNil(amount)
        XCTAssertEqual(amount.map { spanText("You charged SGD 4.80 at KOPITIAM on 27 Oct 2026", $0) },
                       "SGD 4.80")
    }

    func test_symbol_amount_is_confident() {
        let c = detect("You spent S$4.50 at FOO PTE")
        let amount = c.first { $0.field == .amount && $0.confident }
        XCTAssertEqual(amount.map { spanText("You spent S$4.50 at FOO PTE", $0) }, "S$4.50")
    }

    func test_bare_integer_is_never_confident() {
        // reference and phone digits must not pre-tag as money
        let c = detect("Your OTP is 847231. Reference 12345.")
        XCTAssertNil(c.first { $0.field == .amount && $0.confident })
    }

    func test_date_span_merges_and_is_confident() {
        let text = "You charged SGD 4.80 at KOPITIAM on 27 Oct 2026"
        let c = detect(text)
        let date = c.first { $0.field == .date && $0.confident }
        XCTAssertNotNil(date)
        XCTAssertEqual(date.map { spanText(text, $0) }, "27 Oct 2026")
    }

    func test_strong_anchor_payee_is_confident() {
        let text = "You charged SGD 4.80 at KOPITIAM on 27 Oct 2026"
        let c = detect(text)
        let payee = c.first { $0.field == .payee && $0.confident }
        XCTAssertEqual(payee.map { spanText(text, $0) }, "KOPITIAM")
    }

    func test_stop_word_after_anchor_is_not_a_payee() {
        // "refer to your PayLah" must not tag "your ..." ahead of the merchant
        let text = "We refer to your transfer request. To KOPITIAM on 27 Oct 2026"
        let c = detect(text)
        let payee = c.first { $0.field == .payee && $0.confident }
        XCTAssertEqual(payee.map { spanText(text, $0) }, "KOPITIAM")
    }

    func test_us_dollar_prefix_stays_in_the_amount() {
        let text = "You spent US$9.00 at DUTY FREE"
        let c = detect(text)
        let amount = c.first { $0.field == .amount && $0.confident }
        XCTAssertEqual(amount.map { spanText(text, $0) }, "US$9.00")
    }

    func test_trailing_currency_stays_literal_text() {
        // the matcher's amount only reads currency up front, so "4.80 SGD"
        // tags the figure alone and lets "SGD" anchor literally
        let text = "You paid 4.80 SGD at KOPITIAM"
        let c = detect(text)
        let amount = c.first { $0.field == .amount && $0.confident }
        XCTAssertEqual(amount.map { spanText(text, $0) }, "4.80")
    }

    func test_caps_run_becomes_a_suggestion_not_a_pretag() {
        let text = "Payment made. SGH - DIGESTIVE & LIVE billed your card."
        let c = detect(text)
        let caps = c.first { $0.field == .payee && $0.tokenIndexes.count > 1 }
        XCTAssertNotNil(caps)
        XCTAssertFalse(caps!.confident)
        XCTAssertEqual(spanText(text, caps!), "SGH - DIGESTIVE & LIVE")
    }

    func test_no_amount_no_confident_amount() {
        let c = detect("Weekly digest: three stories from your team")
        XCTAssertNil(c.first { $0.field == .amount && $0.confident })
    }

    func test_clock_hour_is_not_a_short_year() {
        // "26 Oct 11" would parse the hour as the year and store 2011
        let text = "Alert on 26 Oct 11:24 from DBS"
        let c = detect(text)
        let date = c.first { $0.field == .date && $0.confident }
        XCTAssertEqual(date.map { spanText(text, $0) }, "26 Oct")
    }

    func test_merchant_anchor_beats_bank_anchor() {
        // "from DBS ... to MERCHANT": the merchant's "at/to" anchor ranks
        // above the bank's "from/via" anchor
        let text = "SGD 4.80 from DBS/POSB card ending 1234 to SGH - DIGESTIVE"
        let c = detect(text)
        let payees = c.filter { $0.field == .payee && $0.confident }
        let best = payees.min(by: { $0.rank < $1.rank })
        XCTAssertEqual(best.map { spanText(text, $0) }, "SGH - DIGESTIVE")
    }

    func test_card_clause_stops_a_payee_run() {
        let text = "SGD 4.80 from DBS/POSB card ending 1234 to SGH"
        let c = detect(text)
        let bank = c.first { $0.field == .payee && spanText(text, $0).contains("DBS") }
        XCTAssertEqual(bank.map { spanText(text, $0) }, "DBS/POSB")
    }

    /// "ending 1234" names the card or account the charge went through, so
    /// the digits after it arrive pre-tagged as the card.
    func test_card_digits_after_ending_are_a_confident_card() {
        let text = "You made a payment of SGD 12.50 to KOPI CORNER PTE LTD on 29 Oct 2026 "
            + "using your DBS Card ending 1234."
        let card = detect(text).first { $0.field == .account && $0.confident }
        XCTAssertEqual(card.map { spanText(text, $0) }, "1234")
    }

    /// The confident guesses, card included, tag straight into a pattern
    /// that reads the alert back with its card.
    func test_pretagged_card_builds_a_pattern_that_reads_the_card() throws {
        let text = "You made a payment of SGD 12.50 to KOPI CORNER PTE LTD on 29 Oct 2026 "
            + "using your DBS Card ending 1234."
        let tokens = Tokenizer.tokenize(text)
        var tags: [Int: PatternBuilder.Field] = [:]
        for c in Candidates.detect(in: text, tokens: tokens) where c.confident {
            if c.field == .payee, tags.values.contains(.payee) { continue }
            for i in c.tokenIndexes where tags[i] == nil { tags[i] = c.field }
        }
        let segs = try PatternBuilder.segments(in: text, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        let p = AlertPattern(name: "Kopi", direction: .outflow, accountID: "a",
                             segments: segs, sampleText: text)
        switch Matcher.test(p, text: text) {
        case .success(let t): XCTAssertEqual(t.accountHint, "1234")
        case .failure(let e): XCTFail("does not read: \(e.reason)")
        }
    }

    func test_ending_in_and_a_mobile_wallet_are_cards_too() {
        let a = "Paid SGD 4.80 with card ending in 5678 at KOPITIAM"
        XCTAssertEqual(detect(a).first { $0.field == .account }.map { spanText(a, $0) }, "5678")
        let b = "From:\nPayLah! Wallet (Mobile ending 2468)"
        XCTAssertEqual(detect(b).first { $0.field == .account }.map { spanText(b, $0) }, "2468")
    }

    func test_a_number_with_no_ending_is_no_card() {
        // a bare reference or an amount must never read as the card
        XCTAssertNil(detect("Reference 1234. You charged SGD 4.80 at KOPITIAM")
            .first { $0.field == .account })
    }

    /// A wallet app's transfer email, scrubbed: field labels carry their
    /// values on the next line.
    private let labelledMail = """
        We refer to your PayLah! Scan & Pay
        Transfer dated 27 Oct. We are pleased to
        confirm that the transaction was completed.

        Date & Time:
        27 Oct 15:30 (SGT)

        Amount:
        SGD2.20

        From:
        PayLah! Wallet (Mobile ending 2468)

        To:
        SUNRISE ICE BLENDS

        To view your transactions, login to your
        PayLah! Wallet and select "History" at the
        bottom bar of the Home page.
        """

    /// "To:" with its value on the next line is the strongest payee signal
    /// in the email; before labels existed the confident guess was the
    /// first inline "to", which lands on "confirm that".
    func test_a_label_anchor_payee_beats_inline_to() {
        let c = detect(labelledMail)
        let payees = c.filter { $0.field == .payee && $0.confident }
        let best = payees.min(by: { $0.rank < $1.rank })
        XCTAssertEqual(best.map { spanText(labelledMail, $0) }, "SUNRISE ICE BLENDS")
    }

    /// A labelled value keeps its leading stopword: the merchant's own name
    /// starts with "The".
    func test_a_labelled_value_keeps_a_leading_stopword() {
        let text = "Amount SGD 4.80\nTo:\nThe Grand Hotel\nDone"
        let c = detect(text)
        let payees = c.filter { $0.field == .payee && $0.confident }
        let best = payees.min(by: { $0.rank < $1.rank })
        XCTAssertEqual(best.map { spanText(text, $0) }, "The Grand Hotel")
    }

    /// Three inline guesses ahead of a "To:" label must not push the label
    /// out of the kept three: the cap takes the best ranks, not the first
    /// three in text order.
    func test_a_label_payee_survives_a_late_position() {
        let text = "You paid at KOPITIAM to FRIEND via APP store\nTo:\nCHINESE COOKED FOOD"
        let c = detect(text)
        XCTAssertTrue(c.contains { $0.field == .payee && spanText(text, $0) == "CHINESE COOKED FOOD" })
    }

    /// An inline payee span must not absorb the next line: the capture
    /// cannot hold a newline, so a run that crosses one only builds a
    /// pattern that never matches.
    func test_a_payee_span_stops_at_the_line_end() {
        let text = "You paid SGD 4.80 to KOPITIAM\nBank hotline 1800 111 2222"
        let c = detect(text)
        let payees = c.filter { $0.field == .payee && $0.confident }
        let best = payees.min(by: { $0.rank < $1.rank })
        XCTAssertEqual(best.map { spanText(text, $0) }, "KOPITIAM")
    }
}
