import XCTest
@testable import Matching

/// G24: the builder's tagging as values. Invented alerts only.
final class TagDraftTests: XCTestCase {
    let text = "You made a payment of SGD 12.50 to NOODLE STALL on 29 Oct 2026."

    private func index(of word: String, in tokens: [Token]) -> Int {
        tokens.firstIndex { $0.text == word }!
    }

    func test_detection_pre_tags_the_confident_guesses() {
        let tokens = Tokenizer.tokenize(text)
        let d = TagDraft.detected(in: text, tokens: tokens)
        let fields = Set(d.spans(tokens: tokens).map(\.field))
        XCTAssertTrue(fields.isSuperset(of: [.amount, .payee, .date]))
        XCTAssertNil(TagDraft.nextMissing(in: d.spans(tokens: tokens)))
        XCTAssertEqual(d.tags[index(of: "NOODLE", in: tokens)], .payee)
    }

    func test_nothing_found_asks_for_the_amount_first() {
        XCTAssertEqual(TagDraft.nextMissing(in: []), .amount)
        let tokens = Tokenizer.tokenize("pay 4")
        let amountOnly = TagDraft(tags: [2: .amount])
        XCTAssertEqual(TagDraft.nextMissing(in: amountOnly.spans(tokens: tokens)), .payee)
    }

    func test_a_tap_tags_untags_and_accepts_a_suggestion() {
        var d = TagDraft(suggestions: [3: .date])
        d.tap(1, armed: .payee)
        XCTAssertEqual(d.tags[1], .payee)
        d.tap(1, armed: .payee)
        XCTAssertNil(d.tags[1])
        // a dashed guess accepts as its own field whatever is armed
        d.tap(3, armed: .amount)
        XCTAssertEqual(d.tags[3], .date)
        XCTAssertNil(d.suggestions[3])
        // a tag of another field is re-tagged with the armed one
        d.tap(3, armed: .amount)
        XCTAssertEqual(d.tags[3], .amount)
    }

    func test_a_span_tap_clears_its_own_field_and_retags_another() {
        var d = TagDraft(tags: [0: .payee, 2: .payee], suggestions: [2: .amount])
        d.tapSpan([0, 2], field: .payee, armed: .amount)
        XCTAssertEqual(d.tags, [0: .amount, 2: .amount])
        XCTAssertTrue(d.suggestions.isEmpty)
        d.tapSpan([0, 2], field: .amount, armed: .amount)
        XCTAssertTrue(d.tags.isEmpty)
    }

    func test_a_stroke_tags_the_words_it_covered_on_top_of_its_start() {
        let tokens = Tokenizer.tokenize("to NOODLE STALL on")
        var d = TagDraft(tags: [0: .ignore], suggestions: [2: .payee])
        d.commitStroke(2...4, armed: .payee, onto: [6: .date], tokens: tokens)
        // the whitespace between the words stays untagged
        XCTAssertEqual(d.tags, [2: .payee, 4: .payee, 6: .date])
        XCTAssertTrue(d.suggestions.isEmpty)
    }

    func test_atoms_merge_a_run_and_keep_line_breaks() {
        let text = "to NOODLE STALL\n\nok"
        let tokens = Tokenizer.tokenize(text)
        let d = TagDraft(tags: [2: .payee, 4: .payee])
        let atoms = TagDraft.atoms(in: text, tokens: tokens, spans: d.spans(tokens: tokens))
        XCTAssertEqual(atoms, [.token(0), .gap(1), .span(.payee, "NOODLE STALL", [2, 4]),
                               .newline(5, 0), .newline(5, 1), .token(6)])
        XCTAssertEqual(TagDraft.tokenRange(containing: 4, in: atoms), 2...4)
        XCTAssertEqual(TagDraft.tokenRange(containing: 6, in: atoms), 6...6)
        XCTAssertNil(TagDraft.tokenRange(containing: 1, in: atoms))
    }

    func test_a_refusal_reads_as_the_builder_says_it() {
        XCTAssertEqual(PatternBuilder.BuildError.missingField("amount").message, "tag an amount span")
        XCTAssertEqual(PatternBuilder.BuildError.duplicateField("card").message,
                       "only one card span per pattern")
        XCTAssertEqual(PatternBuilder.BuildError.unreadableDate("32 Foo").message,
                       #"date "32 Foo" is not a format the app reads"#)
        XCTAssertEqual(PatternBuilder.BuildError.wildcardBesidePayee.message,
                       "the payee needs a fixed word right beside it")
        XCTAssertEqual(PatternBuilder.BuildError.wildcardBesideCard.message,
                       "the card needs a fixed word right beside it")
    }
}
