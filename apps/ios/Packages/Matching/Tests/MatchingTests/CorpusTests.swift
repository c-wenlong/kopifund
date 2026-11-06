import XCTest
@testable import Matching

/// G24: a pattern taught from one alert has to read the next alert of its
/// kind. Each template below is taught once, the way the owner tags it in
/// the builder (amount, payee, date and sometimes the card), and then has to
/// read variants a real inbox produces: other merchants and amounts, other
/// dates and times, a longer reference, a changed footer or greeting, extra
/// blank lines, a merchant that wraps onto a second line. The negatives are
/// alerts of another kind or another bank that the pattern must not read.
///
/// Every merchant, wallet, bank, reference, card digit and name here is
/// invented; none is copied from a real alert.
final class CorpusTests: XCTestCase {

    /// Every date read here is relative to this arrival: 5 Nov 2026, noon in
    /// Singapore.
    static let sgt = TimeZone(identifier: "Asia/Singapore")!
    static let arrived: Date = {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = sgt
        return cal.date(from: DateComponents(year: 2026, month: 11, day: 5, hour: 12))!
    }()

    struct Expect {
        var payee: String
        var amount: String
        var date: CalendarDate?
    }

    struct Variant {
        var label: String
        var text: String
        var expect: Expect
    }

    struct Template {
        var name: String
        var sample: String
        /// Field, the text tagged, and a marker the text sits after (the
        /// first occurrence after the marker is tagged). Nil takes the
        /// builder's own pre-tags, what the owner gets by accepting them.
        var tags: [(PatternBuilder.Field, String, String)]?
        var variants: [Variant]
    }

    // MARK: - teaching

    /// The tags a user lays down: every non-whitespace token inside the
    /// tagged text's range.
    static func teach(_ t: Template) throws -> AlertPattern {
        let tokens = Tokenizer.tokenize(t.sample)
        var tags: [Int: PatternBuilder.Field] = [:]
        if t.tags == nil { tags = TagDraft.detected(in: t.sample, tokens: tokens).tags }
        for (field, value, marker) in t.tags ?? [] {
            let from = marker.isEmpty ? t.sample.startIndex
                : t.sample.range(of: marker)!.upperBound
            guard let r = t.sample.range(of: value, range: from..<t.sample.endIndex) else {
                fatalError("\(t.name): \(value) not in sample")
            }
            for (i, tok) in tokens.enumerated()
            where tok.kind != .whitespace && tok.range.lowerBound >= r.lowerBound
                && tok.range.upperBound <= r.upperBound {
                tags[i] = field
            }
        }
        let segs = try PatternBuilder.segments(in: t.sample, tokens: tokens,
                                               spans: PatternBuilder.spans(from: tags, tokens: tokens))
        return AlertPattern(name: t.name, direction: .outflow, accountID: "",
                            segments: segs, sampleText: t.sample)
    }

    static func read(_ p: AlertPattern, _ text: String) -> Result<ParsedTransaction, MatchError> {
        Matcher.test(p, text: text, receivedAt: arrived, zone: sgt)
    }

    /// Why a variant failed, or nil when it read every field right.
    static func miss(_ p: AlertPattern, _ v: Variant) -> String? {
        switch read(p, v.text) {
        case .failure(let e): return e.reason
        case .success(let t):
            var wrong: [String] = []
            if t.payee != v.expect.payee { wrong.append("payee \(t.payee.debugDescription)") }
            if t.amount != Decimal(string: v.expect.amount) { wrong.append("amount \(t.amount)") }
            if let d = v.expect.date, t.date != d { wrong.append("date \(t.date)") }
            return wrong.isEmpty ? nil : wrong.joined(separator: ", ")
        }
    }

    static func day(_ m: Int, _ d: Int, _ y: Int = 2026) -> CalendarDate {
        CalendarDate(year: y, month: m, day: d)!
    }

    // MARK: - template 1: a wallet's transfer email

    static func wallet(ref: String = "KW8842173", kind: String = "Scan & Pay Transfer",
                       dated: String = "12 Oct", when: String = "12 Oct 14:05",
                       amount: String = "SGD4.50", mobile: String = "4417",
                       merchant: String = "LUCKY BOWL NOODLE HOUSE",
                       footer: String = "To view your transactions, please log in to the Kaya Wallet app. Thank you for choosing Merlion Bank.",
                       gap: String = "\n") -> String {
        """
        Transaction Alerts
        Transaction Ref: \(ref)

        Dear Sir / Madam,

        We refer to your Kaya Wallet \(kind) dated \(dated). We are pleased to confirm that the transaction was completed.

        Date & Time:\(gap)\(when) (SGT)\(gap)Amount:\(gap)\(amount)\(gap)From:\(gap)Kaya Wallet (Mobile ending \(mobile))\(gap)To:\(gap)\(merchant)

        \(footer)
        """
    }

    static let walletTemplate = Template(
        name: "Kaya Wallet transfer",
        sample: wallet(),
        tags: [(.date, "12 Oct", "Date & Time:\n"), (.amount, "SGD4.50", ""),
               (.payee, "LUCKY BOWL NOODLE HOUSE", "")],
        variants: [
            Variant(label: "the sample", text: wallet(),
                    expect: Expect(payee: "LUCKY BOWL NOODLE HOUSE", amount: "4.50", date: day(10, 12))),
            Variant(label: "other merchant and amount",
                    text: wallet(amount: "SGD12.30", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "12.30", date: day(10, 12))),
            Variant(label: "a thousands amount",
                    text: wallet(amount: "SGD1,234.00", merchant: "HOMEFIX HARDWARE"),
                    expect: Expect(payee: "HOMEFIX HARDWARE", amount: "1234.00", date: day(10, 12))),
            Variant(label: "other date and time",
                    text: wallet(dated: "3 Nov", when: "3 Nov 09:41", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(11, 3))),
            Variant(label: "a short reference",
                    text: wallet(ref: "KW12", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "a long reference",
                    text: wallet(ref: "KWX99887766554433", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "Transfer, not Scan & Pay Transfer",
                    text: wallet(kind: "Transfer", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with &",
                    text: wallet(merchant: "MEI & SONS PROVISION"),
                    expect: Expect(payee: "MEI & SONS PROVISION", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with an apostrophe",
                    text: wallet(merchant: "AH HUAT'S KOPI"),
                    expect: Expect(payee: "AH HUAT'S KOPI", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with a full stop",
                    text: wallet(merchant: "ST. CLEMENT BAKERY"),
                    expect: Expect(payee: "ST. CLEMENT BAKERY", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with digits",
                    text: wallet(merchant: "SUPERMART 24 PTE LTD"),
                    expect: Expect(payee: "SUPERMART 24 PTE LTD", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with a slash",
                    text: wallet(merchant: "GREENLEAF/TAMPINES"),
                    expect: Expect(payee: "GREENLEAF/TAMPINES", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with brackets",
                    text: wallet(merchant: "SUNNY SNACKS (BEDOK)"),
                    expect: Expect(payee: "SUNNY SNACKS (BEDOK)", amount: "4.50", date: day(10, 12))),
            Variant(label: "extra blank lines",
                    text: wallet(merchant: "TOAST CORNER", gap: "\n\n"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "a changed footer",
                    text: wallet(merchant: "TOAST CORNER",
                                 footer: "Need help? Reach us at 6123 4567. This is a system generated message."),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "no footer",
                    text: wallet(merchant: "TOAST CORNER", footer: ""),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "a two-line merchant",
                    text: wallet(merchant: "HAPPY GARDEN SEAFOOD\nRESTAURANT"),
                    expect: Expect(payee: "HAPPY GARDEN SEAFOOD RESTAURANT", amount: "4.50", date: day(10, 12))),
            Variant(label: "Windows line endings",
                    text: wallet(merchant: "TOAST CORNER").replacingOccurrences(of: "\n", with: "\r\n"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "trailing spaces and non-breaking spaces",
                    text: wallet(merchant: "TOAST\u{00A0}CORNER").replacingOccurrences(of: "\n", with: " \n"),
                    expect: Expect(payee: "TOAST\u{00A0}CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "everything at once",
                    text: wallet(ref: "KW7", kind: "Transfer", dated: "1 Nov", when: "1 Nov 23:59",
                                 amount: "SGD88.00", merchant: "OCEAN'S 8 SEAFOOD & GRILL (CLEMENTI)",
                                 footer: "Thank you.", gap: "\n\n"),
                    expect: Expect(payee: "OCEAN'S 8 SEAFOOD & GRILL (CLEMENTI)", amount: "88.00", date: day(11, 1))),
        ])

    // MARK: - template 2: a card SMS

    static func cardSMS(amount: String = "SGD44.53", card: String = "6529",
                        merchant: String = "GOLDEN SPOON EATERY", when: String = "26 Oct 11:24",
                        tail: String = "was completed.") -> String {
        "A card txn of \(amount) from MERLION/HBR card ending \(card) to \(merchant) on \(when) (SGT) \(tail)"
    }

    static let smsTemplate = Template(
        name: "Merlion card SMS",
        sample: cardSMS(),
        tags: [(.amount, "SGD44.53", ""), (.account, "6529", ""),
               (.payee, "GOLDEN SPOON EATERY", ""), (.date, "26 Oct", "EATERY")],
        variants: [
            Variant(label: "the sample", text: cardSMS(),
                    expect: Expect(payee: "GOLDEN SPOON EATERY", amount: "44.53", date: day(10, 26))),
            Variant(label: "other merchant and amount",
                    text: cardSMS(amount: "SGD3.20", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "3.20", date: day(10, 26))),
            Variant(label: "a thousands amount",
                    text: cardSMS(amount: "SGD2,450.00", merchant: "HOMEFIX HARDWARE"),
                    expect: Expect(payee: "HOMEFIX HARDWARE", amount: "2450.00", date: day(10, 26))),
            Variant(label: "other date and time",
                    text: cardSMS(merchant: "TOAST CORNER", when: "2 Nov 07:03"),
                    expect: Expect(payee: "TOAST CORNER", amount: "44.53", date: day(11, 2))),
            Variant(label: "merchant with & and a dash",
                    text: cardSMS(merchant: "MEI - NOODLES & RICE"),
                    expect: Expect(payee: "MEI - NOODLES & RICE", amount: "44.53", date: day(10, 26))),
            Variant(label: "merchant with an apostrophe and a full stop",
                    text: cardSMS(merchant: "AH HUAT'S KOPI PTE. LTD"),
                    expect: Expect(payee: "AH HUAT'S KOPI PTE. LTD", amount: "44.53", date: day(10, 26))),
            Variant(label: "merchant with digits, a slash and brackets",
                    text: cardSMS(merchant: "MART 24/7 (JURONG)"),
                    expect: Expect(payee: "MART 24/7 (JURONG)", amount: "44.53", date: day(10, 26))),
            Variant(label: "merchant with the word on",
                    text: cardSMS(merchant: "CAFE ON THE HILL"),
                    expect: Expect(payee: "CAFE ON THE HILL", amount: "44.53", date: day(10, 26))),
            Variant(label: "a changed tail",
                    text: cardSMS(merchant: "TOAST CORNER",
                                  tail: "was successful. Not you? Call 6000 1234 now."),
                    expect: Expect(payee: "TOAST CORNER", amount: "44.53", date: day(10, 26))),
            Variant(label: "no tail",
                    text: cardSMS(merchant: "TOAST CORNER", tail: ""),
                    expect: Expect(payee: "TOAST CORNER", amount: "44.53", date: day(10, 26))),
        ])

    // MARK: - template 3: a short charge SMS

    static func chargeSMS(amount: String = "S$4.50", merchant: String = "KOPI CORNER",
                          date: String = "12 Oct 2026", ref: String = "TXN-77") -> String {
        "You charged \(amount) at \(merchant) on \(date) acct AB12 ref \(ref)"
    }

    static let chargeTemplate = Template(
        name: "charge SMS",
        sample: chargeSMS(),
        tags: [(.amount, "S$4.50", ""), (.payee, "KOPI CORNER", ""),
               (.date, "12 Oct 2026", ""), (.account, "AB12", "")],
        variants: [
            Variant(label: "the sample", text: chargeSMS(),
                    expect: Expect(payee: "KOPI CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "other merchant, amount and date",
                    text: chargeSMS(amount: "S$1,234.50", merchant: "NORTHPOINT GROCER", date: "3 Jan 2027"),
                    expect: Expect(payee: "NORTHPOINT GROCER", amount: "1234.50", date: day(1, 3, 2027))),
            Variant(label: "a long reference",
                    text: chargeSMS(merchant: "TOAST CORNER", ref: "TXN-20261012-99881"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with & and an apostrophe",
                    text: chargeSMS(merchant: "LIM & TAN'S DELI"),
                    expect: Expect(payee: "LIM & TAN'S DELI", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with a full stop, digits and a slash",
                    text: chargeSMS(merchant: "NO. 8 BAKERY/WEST"),
                    expect: Expect(payee: "NO. 8 BAKERY/WEST", amount: "4.50", date: day(10, 12))),
            Variant(label: "merchant with brackets",
                    text: chargeSMS(merchant: "SUNNY SNACKS (BEDOK)"),
                    expect: Expect(payee: "SUNNY SNACKS (BEDOK)", amount: "4.50", date: day(10, 12))),
        ])

    // MARK: - template 4: a bank's card email

    static func bankMail(subject: String = "Subject: Transaction Alert",
                         greeting: String = "Dear Customer,",
                         amount: String = "SGD 4.80", merchant: String = "KOPI CORNER",
                         date: String = "27 Oct 2026",
                         footer: String = "If you did not make this transaction, please call our hotline at 1800 555 0101.\nMerlion Bank Ltd. All rights reserved.",
                         gap: String = "\n\n") -> String {
        "\(subject)\nProblems viewing this email?\(gap)\(greeting)\(gap)You charged \(amount) at \(merchant) on \(date).\(gap)\(footer)"
    }

    static let mailTemplate = Template(
        name: "Merlion card email",
        sample: bankMail(),
        tags: [(.amount, "SGD 4.80", ""), (.payee, "KOPI CORNER", ""), (.date, "27 Oct 2026", "")],
        variants: [
            Variant(label: "the sample", text: bankMail(),
                    expect: Expect(payee: "KOPI CORNER", amount: "4.80", date: day(10, 27))),
            Variant(label: "other merchant, amount and date",
                    text: bankMail(amount: "SGD 1,020.40", merchant: "HOMEFIX HARDWARE", date: "4 Nov 2026"),
                    expect: Expect(payee: "HOMEFIX HARDWARE", amount: "1020.40", date: day(11, 4))),
            Variant(label: "a greeting by name",
                    text: bankMail(greeting: "Dear TAN BEE LING,", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.80", date: day(10, 27))),
            Variant(label: "a forwarded subject",
                    text: bankMail(subject: "Fwd: Transaction Alert", merchant: "TOAST CORNER"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.80", date: day(10, 27))),
            Variant(label: "a changed footer",
                    text: bankMail(merchant: "TOAST CORNER",
                                   footer: "Questions? Visit our help centre.\nThis email was sent by Merlion Bank."),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.80", date: day(10, 27))),
            Variant(label: "one blank line more",
                    text: bankMail(merchant: "TOAST CORNER", gap: "\n\n\n"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.80", date: day(10, 27))),
            Variant(label: "Windows line endings",
                    text: bankMail(merchant: "TOAST CORNER").replacingOccurrences(of: "\n", with: "\r\n"),
                    expect: Expect(payee: "TOAST CORNER", amount: "4.80", date: day(10, 27))),
            Variant(label: "merchant with &, an apostrophe and brackets",
                    text: bankMail(merchant: "MEI & SONS' CAFE (TAMPINES)"),
                    expect: Expect(payee: "MEI & SONS' CAFE (TAMPINES)", amount: "4.80", date: day(10, 27))),
            Variant(label: "merchant with a full stop, digits and a slash",
                    text: bankMail(merchant: "ST. 21 BAKERY/EAST"),
                    expect: Expect(payee: "ST. 21 BAKERY/EAST", amount: "4.80", date: day(10, 27))),
        ])

    /// The same wallet email taught by accepting the builder's guesses: it
    /// also tags the mobile's last digits as the card.
    static let walletPreTagged: Template = {
        var t = walletTemplate
        t.name = "Kaya Wallet transfer, pre-tagged"
        t.tags = nil
        return t
    }()

    static let templates = [walletTemplate, walletPreTagged, smsTemplate, chargeTemplate, mailTemplate]

    // MARK: - negatives: other kinds and other banks

    /// Alerts no taught pattern may read: a different bank's layout, an
    /// inflow, a one-time code.
    static let strangers: [(String, String)] = [
        ("another bank's card email", """
            Harbour Bank Card Alert
            Card: HB Visa ending 4417
            Merchant: LUCKY BOWL NOODLE HOUSE
            Amount: SGD 4.50
            Date: 12 Oct 2026 14:05
            """),
        ("another bank's card SMS",
         "Harbour Bank: SGD44.53 spent on card ending 6529 at GOLDEN SPOON EATERY on 26 Oct. Not you? Call 6000 0000."),
        ("money received", "You have received SGD 20.00 from TAN AH BENG on 12 Oct 2026 via Quick Transfer."),
        ("a one-time code", "Your OTP is 384920. It expires in 5 minutes. Never share it."),
        ("another wallet's receipt", """
            Payment Receipt
            Paid to: TOAST CORNER
            Total: SGD 4.50
            Paid on: 12 Oct 2026
            Thanks for paying with Dorian Pay.
            """),
    ]

    // MARK: - the tests

    /// Every variant of every template reads right. The message reports the
    /// pass rate so a regression says how much broke.
    func test_a_pattern_reads_the_next_email_of_its_kind() throws {
        var passed = 0, total = 0
        var misses: [String] = []
        for t in Self.templates {
            let p = try Self.teach(t)
            for v in t.variants {
                total += 1
                if let why = Self.miss(p, v) { misses.append("\(t.name) / \(v.label): \(why)") }
                else { passed += 1 }
            }
        }
        print("CORPUS: \(passed)/\(total) variants read right")
        XCTAssertEqual(passed, total, "corpus \(passed)/\(total):\n" + misses.joined(separator: "\n"))
    }

    /// A pattern reads only its own kind: no template's pattern takes another
    /// template's alerts, another bank's, or an alert that is not a spend.
    func test_a_pattern_does_not_read_another_kind_of_alert() throws {
        var leaks: [String] = []
        for t in Self.templates {
            let p = try Self.teach(t)
            var others: [(String, String)] = Self.strangers
            for o in Self.templates where o.sample != t.sample {
                others += o.variants.map { ("\(o.name) / \($0.label)", $0.text) }
            }
            for (label, text) in others {
                if case .success(let r) = Self.read(p, text) {
                    leaks.append("\(t.name) read \(label) as \(r.payee.debugDescription)")
                }
            }
        }
        XCTAssertEqual(leaks, [], leaks.joined(separator: "\n"))
    }

    /// A tagged card stays part of the pattern's identity: the same SMS from
    /// another card is not this pattern's.
    func test_a_tagged_card_still_separates_cards() throws {
        let p = try Self.teach(Self.smsTemplate)
        guard case .failure = Self.read(p, Self.cardSMS(card: "9001")) else {
            return XCTFail("another card's SMS read")
        }
        let c = try Self.teach(Self.chargeTemplate)
        guard case .failure = Self.read(c, "You charged S$4.50 at KOPI CORNER on 12 Oct 2026 acct ZZ99 ref TXN-77") else {
            return XCTFail("another account's SMS read")
        }
    }

    /// Review on #112: a merchant holding the template's opening word
    /// ("PREPAID" in a "Paid ..." template) still reads; only the opening
    /// with its amount counts as the template starting again.
    func test_a_name_holding_the_opening_word_still_reads() throws {
        let t = Template(name: "paid SMS", sample: "Paid SGD 4.80 to KOPI CORNER on 27 Oct 2026",
                         tags: [(.amount, "SGD 4.80", ""), (.payee, "KOPI CORNER", ""),
                                (.date, "27 Oct 2026", "")], variants: [])
        let p = try Self.teach(t)
        for name in ["PREPAID MOBILE TOPUP", "PAID PARKING LOT 3"] {
            guard case .success(let r) = Self.read(p, "Paid SGD 9.00 to \(name) on 3 Nov 2026") else {
                return XCTFail("\(name) should read")
            }
            XCTAssertEqual(r.payee, name)
        }
    }

    /// Review on #112: a cut-off alert ahead of a whole one never lends the
    /// whole one its date and amount; the whole one reads on its own.
    func test_a_cut_off_alert_never_lends_its_fields() throws {
        let p = try Self.teach(Self.walletTemplate)
        let text = "Date & Time:\n12 Oct 14:05 (SGT)\nAmount:\nSGD4.50\n"
            + Self.wallet(dated: "3 Nov", when: "3 Nov 09:41", amount: "SGD9.00", merchant: "TOAST CORNER")
        guard case .success(let r) = Self.read(p, text) else { return XCTFail("the whole alert should read") }
        XCTAssertEqual(r.payee, "TOAST CORNER")
        XCTAssertEqual(r.amount, Decimal(string: "9.00"))
        XCTAssertEqual(r.date, Self.day(11, 3))
    }

    /// Review on #112: a full stop right after the payee may be part of the
    /// next name, so the words after it anchor too.
    func test_a_full_stop_after_the_payee_keeps_the_words_after_it() throws {
        let t = Template(name: "thanks", sample: "Paid SGD 4.80 to SHOP. Thank you.",
                         tags: [(.amount, "SGD 4.80", ""), (.payee, "SHOP", "")], variants: [])
        let p = try Self.teach(t)
        guard case .success(let r) = Self.read(p, "Paid SGD 9.00 to ST. CLEMENT BAKERY. Thank you.") else {
            return XCTFail("should read")
        }
        XCTAssertEqual(r.payee, "ST. CLEMENT BAKERY")
    }
}
