import XCTest

/// G24: the pattern builder is touch-first: a drag across words tags them,
/// a pull down the document scrolls the page, and the detector's guesses
/// land pre-tagged. Nothing else on the phone exercises those gestures, so
/// the builder's own paste flow is driven end to end here.
final class PatternBuilderUITests: XCTestCase {

    private let sampleAlert = """
        Transaction Alerts
        Problems viewing this email? Select "always display images".

        Transaction Ref: PLPE8203910475561239
        Dear Sir / Madam,
        We refer to your PayLah! Scan & Pay Transfer dated 12 Oct. We are pleased to confirm that the transaction was completed.

        Date & Time:
        12 Oct 09:40 (SGT)
        Amount:
        SGD6.40
        From:
        PayLah! Wallet (Mobile ending 2468)
        To:
        GOLDEN WOK KITCHEN
        """

    private func openBuilder(_ app: XCUIApplication) {
        openTab("Patterns", in: app)
        app.buttons["plus"].waitForExistence(timeout: 5)
        app.buttons["plus"].tap()
        let editor = app.textViews.firstMatch
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        editor.tap()
        editor.typeText(sampleAlert)
        app.buttons["Build pattern"].tap()
        XCTAssertTrue(app.staticTexts["New pattern"].waitForExistence(timeout: 5))
    }

    /// The confident guesses arrive already tagged: amount, payee and date
    /// out of the box on an invented PayLah-shaped alert.
    func testDetectorPreTagsAllThreeFields() throws {
        let app = XCUIApplication()
        app.launch()
        openBuilder(app)
        XCTAssertTrue(app.staticTexts["3 of 3 found"].exists)
        XCTAssertTrue(app.staticTexts["GOLDEN WOK KITCHEN"].exists)
        XCTAssertTrue(app.staticTexts["SGD6.40"].exists)
    }

    /// A horizontal drag over words tags the whole run with the armed field:
    /// "Dear Sir / Madam," merges into one ignore span. A vertical pull on the
    /// same document is the page's scroll, not a selection, so it must tag
    /// nothing and must reveal the sections below the document card.
    func testHorizontalDragTagsAndVerticalDragScrolls() throws {
        let app = XCUIApplication()
        app.launch()
        openBuilder(app)

        // arm Ignore so the selection cannot collide with the tagged fields
        let ignoreChip = app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH 'Ignore'")).firstMatch
        XCTAssertTrue(ignoreChip.waitForExistence(timeout: 3))
        ignoreChip.tap()

        // press on the first word, drag slowly onto a word a few tokens
        // right so SwiftUI sees real motion (atoms render one token per
        // element, so "Madam," is two)
        let first = app.staticTexts["Dear"]
        let last = app.staticTexts["Madam"]
        XCTAssertTrue(first.waitForExistence(timeout: 5))
        XCTAssertTrue(last.exists)
        first.press(forDuration: 0.3, thenDragTo: last,
                    withVelocity: .slow, thenHoldForDuration: 0.4)

        // the run between them became one Ignore span: the chip counts it
        // and the merged atom renders the whole phrase as one element, so a
        // drag that stopped after "Dear" fails here
        let ignoreCount = app.buttons
            .matching(NSPredicate(format: "label == 'Ignore, 1'")).firstMatch
        XCTAssertTrue(ignoreCount.waitForExistence(timeout: 3))
        let labels = app.staticTexts.allElementsBoundByIndex.map { $0.label }
        XCTAssertTrue(labels.contains("Dear Sir / Madam"),
                      "labels: \(labels.filter { $0.contains("Dear") || $0.contains("Sir") || $0.contains("Madam") })")

        // the same stroke pulled vertically scrolls the page and tags
        // nothing: the armed field was Ignore, so a scroll registering as a
        // selection would have grown the count
        app.swipeUp()
        XCTAssertTrue(app.staticTexts["Live test"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label == 'Ignore, 1'")).firstMatch.exists)
        XCTAssertFalse(app.buttons.matching(NSPredicate(format: "label == 'Ignore, 2'")).firstMatch.exists)
    }
}
