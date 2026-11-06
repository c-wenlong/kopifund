import XCTest

/// G24: four alerts of one wallet template wait in Unmatched; teaching a
/// pattern from one must move all four to the Inbox, and an alert in other
/// words must stay. Invented merchants and references only.
final class RematchUITests: XCTestCase {

    static func walletAlert(_ amount: String, _ merchant: String, _ day: Int, _ ref: String) -> String {
        "Demo Wallet: You paid SGD \(amount) to \(merchant) on \(day) Oct 2026. Ref \(ref)."
    }

    static let seeds = [
        walletAlert("4.20", "KOPI CORNER", 20, "A1001"),
        walletAlert("12.50", "NOODLE HOUSE", 21, "B2002"),
        walletAlert("31.00", "BOOK NOOK", 22, "C3003"),
        walletAlert("2.80", "FRUIT STALL", 23, "D4004"),
        // the same wallet in other words: no pattern of the four reads it
        "Demo Wallet: You paid SGD 9.10 at LAMP SHOP on 24 Oct 2026. Ref E5005.",
    ]

    func testSavingAPatternMovesEveryWaitingAlertOfItsTemplate() throws {
        let app = launchSeeded()
        XCTAssertTrue(app.buttons["Try patterns again"].exists)
        keepShot("1-unmatched-before-save")

        app.staticTexts["KOPI CORNER"].tap()
        app.buttons["Teach a pattern"].tap()
        XCTAssertTrue(app.staticTexts["3 of 3 found"].waitForExistence(timeout: 10))
        app.buttons["Save pattern"].tap()

        let toast = app.staticTexts["toast"]
        XCTAssertTrue(toast.waitForExistence(timeout: 10))
        keepShot("2-unmatched-after-save")
        XCTAssertTrue(toast.label.hasPrefix("4 waiting alerts now match"), toast.label)
        for merchant in ["KOPI CORNER", "NOODLE HOUSE", "BOOK NOOK", "FRUIT STALL"] {
            XCTAssertFalse(app.staticTexts[merchant].exists, merchant)
        }
        XCTAssertTrue(app.staticTexts["LAMP SHOP"].exists)

        openTab("Inbox", in: app)
        XCTAssertTrue(app.staticTexts["FRUIT STALL"].waitForExistence(timeout: 5))
        keepShot("3-inbox-after-save")
    }
}

/// Shared by the G24 Unmatched tests (RematchUITests, ClosestUITests).
extension XCTestCase {
    func launchSeeded() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = RematchUITests.seeds.flatMap { ["-seedUnmatched", $0] }
        app.launch()
        openTab("Unmatched", in: app)
        XCTAssertTrue(app.staticTexts["KOPI CORNER"].waitForExistence(timeout: 5))
        return app
    }
}
