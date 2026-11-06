import XCTest
@testable import Matching

/// G24: More options shows each field's before and after text and lets the
/// owner change it. Invented alerts only.
final class PatternAnchorsTests: XCTestCase {

    private func walletPattern(_ segs: [AlertPattern.Segment]) -> AlertPattern {
        AlertPattern(name: "p", direction: .outflow, accountID: "", segments: segs,
                     sampleText: CorpusTests.wallet())
    }

    func test_each_field_lists_the_text_around_it() throws {
        let p = try CorpusTests.teach(CorpusTests.walletTemplate)
        let a = PatternAnchors.anchors(in: p.segments)
        XCTAssertEqual(a.map(\.field), [.date, .amount, .payee])
        XCTAssertEqual(a[0].before, "Date & Time:\n")
        XCTAssertEqual(a[2].before, "To:\n")
        XCTAssertEqual(a[2].after, "\n\n")
    }

    /// The owner widens the payee's before text to the wallet line: the
    /// pattern then reads only transfers from the mobile, not a card. (Its
    /// digits still fold, as in every literal.)
    func test_an_edited_anchor_changes_what_reads() throws {
        let p = try CorpusTests.teach(CorpusTests.walletTemplate)
        let edited = try PatternAnchors.applying(
            [.init(field: .payee, side: .before, text: "(Mobile ending 4417)\nTo:\n")], to: p.segments)
        let q = walletPattern(edited)
        guard case .success(let t) = CorpusTests.read(q, CorpusTests.wallet(merchant: "TOAST CORNER")) else {
            return XCTFail("the same wallet should read")
        }
        XCTAssertEqual(t.payee, "TOAST CORNER")
        guard case .failure = CorpusTests.read(q, CorpusTests.wallet()
            .replacingOccurrences(of: "(Mobile ending", with: "(Card ending")) else {
            return XCTFail("a card transfer should not read once the anchor names the mobile")
        }
    }

    /// An after text the owner adds bounds the payee on its line.
    func test_an_after_anchor_can_be_added() throws {
        let p = try CorpusTests.teach(CorpusTests.chargeTemplate)
        let edited = try PatternAnchors.applying(
            [.init(field: .account, side: .after, text: " ref")], to: p.segments)
        XCTAssertEqual(PatternAnchors.anchors(in: edited).last?.after, " ref")
        let q = AlertPattern(name: "p", direction: .outflow, accountID: "", segments: edited,
                             sampleText: CorpusTests.chargeTemplate.sample)
        guard case .success = CorpusTests.read(q, CorpusTests.chargeSMS(ref: "TXN-123456")) else {
            return XCTFail("a new reference should still read")
        }
    }

    /// Clearing the text beside the payee leaves no border: refused like a
    /// fresh build would be.
    func test_clearing_the_payee_border_is_refused() throws {
        let p = try CorpusTests.teach(CorpusTests.chargeTemplate)
        XCTAssertThrowsError(try PatternAnchors.applying(
            [.init(field: .payee, side: .after, text: "")], to: p.segments)) {
            XCTAssertEqual($0 as? PatternBuilder.BuildError, .wildcardBesidePayee)
        }
        // an amount reads its own end, so its side may go
        XCTAssertNoThrow(try PatternAnchors.applying(
            [.init(field: .amount, side: .before, text: "")], to: p.segments))
    }

    /// A line break shows as a mark on one line and reads back as a break.
    func test_line_breaks_show_as_a_mark_and_read_back() {
        XCTAssertEqual(PatternAnchors.display("To:\n"), "To: ⏎")
        XCTAssertEqual(PatternAnchors.display("\n\n"), "⏎ ⏎")
        XCTAssertEqual(PatternAnchors.display(" ref "), "ref")
        XCTAssertEqual(PatternAnchors.display(nil), "")
        XCTAssertEqual(PatternAnchors.parse("To: ⏎"), "To:\n")
        XCTAssertEqual(PatternAnchors.parse("⏎ ⏎"), "\n\n")
        XCTAssertEqual(PatternAnchors.parse("(Mobile ending 4417) ⏎ To: ⏎"), "(Mobile ending 4417)\nTo:\n")
    }
}
