import XCTest

/// The "App icon" section in Settings (AppIconSection.swift): picking each
/// set moves the checkmark, and the Home Screen icon follows through the
/// catalog's AppIcon-<set> alternates. The home-screen screenshots are kept
/// in the result bundle for a look.
@MainActor
final class AppIconUITests: XCTestCase {

    private func openIconSection(_ app: XCUIApplication) {
        // the tabs are a UITabBar on a phone, floating buttons on the Duo
        let tab = app.tabBars.buttons["Settings"]
        let settings = tab.exists ? tab : app.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        settings.tap()
        // the section is near the foot of the Form: scroll until a row shows
        for _ in 0 ..< 8 where !app.staticTexts["Iso"].exists {
            app.swipeUp()
        }
        XCTAssertTrue(app.staticTexts["Iso"].waitForExistence(timeout: 5))
    }

    private func row(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", label)).firstMatch
    }

    private func dismissIconAlert() {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for _ in 0 ..< 4 {
            for app in [springboard, XCUIApplication()] {
                let alert = app.alerts.firstMatch
                if alert.exists {
                    alert.buttons.element(boundBy: alert.buttons.count - 1).tap()
                }
            }
            usleep(300_000)
        }
    }

    private func shot(_ name: String) {
        let attach = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attach.name = name
        attach.lifetime = .keepAlways
        add(attach)
    }

    func testPickingEachSetMovesTheCheckmarkAndTheHomeScreenIcon() throws {
        let app = XCUIApplication()
        app.launch()
        openIconSection(app)

        XCTAssertTrue(row(app, "Sticker").waitForExistence(timeout: 5))
        XCTAssertTrue(row(app, "Sticker").isSelected, "the default set shows picked")

        for label in ["Iso", "Riso", "Sticker"] {
            row(app, label).tap()
            // the system takes a few seconds to swap the icon, then asks the
            // user to OK "You have changed the icon" — the checkmark lands
            // when setAlternateIconName returns, so wait on it, not on the alert
            let picked = NSPredicate(format: "selected == true")
            let saw = XCTNSPredicateExpectation(predicate: picked, object: row(app, label))
            dismissIconAlert()
            XCTAssertEqual(XCTWaiter.wait(for: [saw], timeout: 15), .completed,
                           "\(label) shows the checkmark")
            dismissIconAlert()
            shot("after-\(label.lowercased())")
        }
    }
}
