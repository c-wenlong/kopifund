import XCTest

/// G24: an alert that still fails after a save says which pattern came
/// closest and the field it could not read, and "Edit that pattern" opens
/// the builder on that pattern with the alert in the live test.
final class ClosestUITests: XCTestCase {

    func testAStillUnmatchedAlertNamesTheClosestPattern() throws {
        let app = launchSeeded()

        app.staticTexts["KOPI CORNER"].tap()
        app.buttons["Teach a pattern"].tap()
        XCTAssertTrue(app.staticTexts["3 of 3 found"].waitForExistence(timeout: 10))
        app.buttons["Save pattern"].tap()
        XCTAssertTrue(app.staticTexts["toast"].waitForExistence(timeout: 10))

        // the alert in other words stays, and its page says why
        let lamp = app.staticTexts["LAMP SHOP"]
        XCTAssertTrue(lamp.waitForExistence(timeout: 5))
        lamp.tap()
        let closest = app.staticTexts["closestPattern"]
        XCTAssertTrue(closest.waitForExistence(timeout: 10))
        XCTAssertTrue(closest.label.hasPrefix("Closest: KOPI CORNER: payee not found after 'to"),
                      closest.label)
        rematchShot("4-alert-page-closest")

        app.buttons["Edit that pattern"].tap()
        XCTAssertTrue(app.staticTexts["Edit pattern"].waitForExistence(timeout: 10))
        // the live test holds this alert and says it does not read yet
        let verdict = app.staticTexts
            .matching(NSPredicate(format: "label BEGINSWITH 'test alert: no match'")).firstMatch
        XCTAssertTrue(verdict.waitForExistence(timeout: 10))
        rematchShot("5-edit-that-pattern")
    }
}
