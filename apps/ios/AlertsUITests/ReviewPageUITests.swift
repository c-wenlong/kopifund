import XCTest

/// The Inbox row's Review page: money reads with two decimals, and with no
/// cached YNAB categories there is no category control to fill in.
final class ReviewPageUITests: XCTestCase {
    func testAmountShowsTwoDecimalsAndNoRawCategoryField() throws {
        let payee = "Review Payee \(Int(Date().timeIntervalSince1970) % 100_000)"
        let app = XCUIApplication()
        app.launchArguments = ["-seedNoCategories", "-seedSpend", "580|SGD|\(payee)|2026-10-15"]
        app.launch()

        let tab = app.tabBars.buttons["Inbox"]
        XCTAssertTrue(tab.waitForExistence(timeout: 5))
        tab.tap()
        let row = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", payee)).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        row.tap()

        let amount = app.textFields["amountField"]
        XCTAssertTrue(amount.waitForExistence(timeout: 5))
        XCTAssertEqual(amount.value as? String, "5.80")
        XCTAssertFalse(app.textFields["Category ID"].exists)
        XCTAssertFalse(app.staticTexts["Category ID"].exists)
        // no category control of any kind: not the Picker either
        XCTAssertFalse(app.buttons["Category"].exists)
        XCTAssertFalse(app.staticTexts["Category"].exists)
    }
}
