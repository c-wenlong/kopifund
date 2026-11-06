import XCTest

/// G24: the Inbox's paste box is a sheet with a multi-line editor, so an
/// alert typed or pasted over several lines reaches intake with its line
/// breaks. Invented wallet and merchant only.
final class PasteSheetUITests: XCTestCase {

    func testAMultiLineAlertKeepsItsLines() throws {
        let tag = "PASTE\(Int(Date().timeIntervalSince1970) % 100_000)"
        let app = XCUIApplication()
        app.launch()
        openTab("Inbox", in: app)
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

        openTab("Unmatched", in: app)
        let row = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@ OR label == 'Wallet payment'", tag)).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        // the alert page prints one line per line of the alert: "To:"
        // stands alone, the merchant below it
        XCTAssertTrue(app.staticTexts["To:"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["\(tag) LAMP"].exists)
        keepShot("paste-alert-page")
    }

    /// The Patterns tab's paste screen is the same editor: its hint shows
    /// while empty (an opaque editor background once hid it) and "Paste
    /// from clipboard" fills it.
    func testPatternsPasteScreenShowsItsHintAndTakesTheClipboard() throws {
        let app = XCUIApplication()
        app.launch()
        openTab("Patterns", in: app)
        app.buttons["plus"].tap()

        let editor = app.textViews["pasteAlertText"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Paste alert text"].exists)
        XCTAssertFalse(app.buttons["Build pattern"].isEnabled)
        keepShot("patterns-paste-empty")

        // The simulator denies the app a clipboard the test runner wrote
        // (another process), so the text is put on the clipboard by the app
        // itself: type it, cut it with the editor's menu, then paste it back
        // with the button.
        editor.tap()
        editor.typeText("Wallet payment\nYou paid SGD 4.20\nTo:\nHINT LAMP")
        editor.press(forDuration: 1.2)
        let selectAll = app.menuItems["Select All"]
        XCTAssertTrue(selectAll.waitForExistence(timeout: 5))
        selectAll.tap()
        let cut = app.menuItems["Cut"]
        XCTAssertTrue(cut.waitForExistence(timeout: 5))
        cut.tap()
        XCTAssertTrue(app.staticTexts["Paste alert text"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Build pattern"].isEnabled)

        app.buttons["Paste from clipboard"].tap()
        sleep(1)
        XCTAssertFalse(app.staticTexts["Paste alert text"].exists)
        XCTAssertEqual((editor.value as? String)?.components(separatedBy: "\n").count, 4)
        XCTAssertTrue(app.buttons["Build pattern"].isEnabled)
        keepShot("patterns-paste-filled")
    }
}
