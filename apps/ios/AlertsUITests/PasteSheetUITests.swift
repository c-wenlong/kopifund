import XCTest

/// G24: the Inbox's paste box is a sheet with a multi-line editor, so an
/// alert typed or pasted over several lines reaches intake with its line
/// breaks. Invented wallet and merchant only.
final class PasteSheetUITests: XCTestCase {

    /// `SHOTS_DIR` (set as TEST_RUNNER_SHOTS_DIR on xcodebuild) keeps each
    /// screenshot as a PNG too; it is always attached to the result.
    private func keepShot(_ name: String) {
        let s = XCUIScreen.main.screenshot()
        let a = XCTAttachment(screenshot: s)
        a.name = name
        a.lifetime = .keepAlways
        add(a)
        if let dir = ProcessInfo.processInfo.environment["SHOTS_DIR"], !dir.isEmpty {
            try? s.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name).png"))
        }
    }

    func testAMultiLineAlertKeepsItsLines() throws {
        let tag = "PASTE\(Int(Date().timeIntervalSince1970) % 100_000)"
        let app = XCUIApplication()
        app.launch()
        app.tabBars.buttons["Inbox"].tap()
        app.buttons["doc.on.clipboard"].tap()

        let editor = app.textViews["pasteAlertText"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Paste from clipboard"].exists)
        // Ingest waits for text
        XCTAssertFalse(app.buttons["Ingest"].isEnabled)
        editor.tap()
        editor.typeText("Wallet payment\nYou paid SGD 9.10\nTo:\n\(tag) LAMP\nRef E5005")
        keepShot("paste-sheet")
        // the editor holds the line breaks the field used to join
        XCTAssertEqual((editor.value as? String)?.components(separatedBy: "\n").count, 5)
        app.buttons["Ingest"].tap()

        app.tabBars.buttons["Unmatched"].tap()
        let row = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@ OR label == 'Wallet payment'", tag)).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        // the alert page prints one line per line of the alert: "To:"
        // stands alone, the merchant below it
        XCTAssertTrue(app.staticTexts["To:"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["\(tag) LAMP"].exists)
        keepShot("paste-alert-page")
    }
}
