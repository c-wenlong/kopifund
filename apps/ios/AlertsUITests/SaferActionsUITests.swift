import XCTest

/// A pushed view must never outlive the row it shows: the list pops first
/// and deletes after. Each test acts on a fresh row, checks the list is back
/// on screen without it, and relaunches to check the delete was saved.
final class SaferActionsUITests: XCTestCase {

    private func paste(_ app: XCUIApplication, _ text: String) {
        app.tabBars.buttons["Inbox"].tap()
        app.buttons["doc.on.clipboard"].tap()
        let field = app.alerts.textFields.firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText(text)
        app.alerts.buttons["Ingest"].tap()
    }

    private func label(containing s: String, in app: XCUIApplication) -> XCUIElement {
        app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", s)).firstMatch
    }

    /// "Not an alert" inside the alert's own page pops back to Unmatched
    /// and drops just that row.
    func testDismissingFromTheAlertPagePopsAndRemovesTheRow() throws {
        let tag = "SAFER\(Int(Date().timeIntervalSince1970) % 100_000)"
        let app = XCUIApplication()
        app.launch()
        paste(app, "You made a payment of SGD 3.10 to \(tag) STALL on 29 Oct 2026.")

        app.tabBars.buttons["Unmatched"].tap()
        let row = label(containing: tag, in: app)
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        row.tap()
        app.buttons["Not an alert"].tap()
        app.buttons["Just this alert"].tap()

        XCTAssertTrue(app.navigationBars["Unmatched"].waitForExistence(timeout: 5))
        XCTAssertTrue(label(containing: tag, in: app).waitForNonExistence(timeout: 5))

        app.terminate()
        app.launch()
        app.tabBars.buttons["Unmatched"].tap()
        XCTAssertTrue(app.navigationBars["Unmatched"].waitForExistence(timeout: 5))
        XCTAssertFalse(label(containing: tag, in: app).exists)
    }

    /// Delete on a transaction's page pops back to the Inbox, and the row is
    /// gone there and after a relaunch.
    func testDeletingATransactionPopsAndRemovesTheRow() throws {
        let payee = "Safer Payee \(Int(Date().timeIntervalSince1970) % 100_000)"
        let app = XCUIApplication()
        app.launchArguments = ["-seedSpend", "420|SGD|\(payee)|2026-10-15"]
        app.launch()

        app.tabBars.buttons["Inbox"].tap()
        let row = label(containing: payee, in: app)
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        row.tap()
        let delete = app.buttons["Delete"]
        var swipes = 0
        while !delete.isHittable && swipes < 6 { app.swipeUp(); swipes += 1 }
        delete.tap()

        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))
        XCTAssertTrue(label(containing: payee, in: app).waitForNonExistence(timeout: 5))

        app.terminate()
        app.launchArguments = []
        app.launch()
        app.tabBars.buttons["Inbox"].tap()
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))
        XCTAssertFalse(label(containing: payee, in: app).exists)
    }
}
