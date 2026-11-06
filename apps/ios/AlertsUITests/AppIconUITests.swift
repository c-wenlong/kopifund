import XCTest

/// The "App icon" section in Settings (AppIconSection.swift): picking each
/// set moves the checkmark, and the Home Screen icon follows through the
/// catalog's AppIcon-<set> alternates. The home-screen screenshots are kept
/// in the result bundle for a look.
@MainActor
final class AppIconUITests: XCTestCase {

    private func openIconSection(_ app: XCUIApplication, showing label: String) {
        // the tabs are a UITabBar on a phone, floating buttons on the Duo
        let tab = app.tabBars.buttons["Settings"]
        let settings = tab.exists ? tab : app.buttons["Settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        settings.tap()
        // the section is near the foot of the Form: scroll until its last
        // row shows
        for _ in 0 ..< 8 where !app.staticTexts[label].exists {
            app.swipeUp()
        }
        XCTAssertTrue(app.staticTexts[label].waitForExistence(timeout: 5))
    }

    private func row(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", label)).firstMatch
    }

    /// The system's "You have changed the icon" alert, in the app or in
    /// SpringBoard, if one is up: its last button is OK.
    private func dismissIconAlert() -> Bool {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for app in [springboard, XCUIApplication()] {
            let alert = app.alerts.firstMatch
            if alert.exists, alert.buttons.count > 0 {
                alert.buttons.element(boundBy: alert.buttons.count - 1).tap()
                return true
            }
        }
        return false
    }

    /// Tap OK on the icon alert whenever it shows until `row` is checked.
    /// setAlternateIconName returns, and the checkmark lands, only after the
    /// alert is answered, and the alert takes from under a second to several
    /// seconds to appear: a fixed window of polls before the wait missed a
    /// late alert, which then held the checkmark back until the wait timed
    /// out. After the checkmark, a moment more for an alert still closing.
    private func waitForCheckmark(on row: XCUIElement, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if row.exists && row.isSelected { break }
            _ = dismissIconAlert()
            usleep(250_000)
        }
        guard row.isSelected else { return false }
        let settle = Date().addingTimeInterval(2)
        while Date() < settle {
            if dismissIconAlert() { continue }
            usleep(250_000)
        }
        return true
    }

    private func shot(_ name: String) {
        let attach = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attach.name = name
        attach.lifetime = .keepAlways
        add(attach)
    }

    func testPickingEachSetMovesTheCheckmarkAndTheHomeScreenIcon() throws {
        // the build's default, from AppIcons.swift (`make app-icon SET=iso`
        // makes Iso the default), never a hardcoded set
        let primary = try XCTUnwrap(AppIcons.sets.first { $0.name == AppIcons.primary }).label
        let others = AppIcons.sets.map(\.label).filter { $0 != primary }
        let app = XCUIApplication()
        app.launch()
        openIconSection(app, showing: AppIcons.sets.last?.label ?? primary)

        XCTAssertTrue(row(app, primary).waitForExistence(timeout: 5))
        // a run that stopped midway leaves an alternate picked: go back to
        // the default first, so the check below reads this build's default
        if !row(app, primary).isSelected {
            row(app, primary).tap()
            XCTAssertTrue(waitForCheckmark(on: row(app, primary), timeout: 20))
        }
        XCTAssertTrue(row(app, primary).isSelected, "the default set shows picked")

        for label in others + [primary] {
            row(app, label).tap()
            XCTAssertTrue(waitForCheckmark(on: row(app, label), timeout: 20),
                          "\(label) shows the checkmark")
            shot("after-\(label.lowercased())")
        }
    }
}
