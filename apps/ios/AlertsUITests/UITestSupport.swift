import XCTest

/// Helpers every UI test class shares, so a fix to one (a tap lost at
/// launch, a screenshot that also lands on disk) reaches them all.
extension XCTestCase {

    /// Select a tab and wait for its page. A tap that lands while the app
    /// is still settling after launch shows the tab pressed but leaves the
    /// first tab selected (seen on iOS 27 straight after a relaunch: the
    /// tab never shows selected), so an unanswered tap is tried again
    /// before the page is called missing.
    @discardableResult
    func openTab(_ tab: String, in app: XCUIApplication) -> Bool {
        let button = app.tabBars.buttons[tab]
        XCTAssertTrue(button.waitForExistence(timeout: 10))
        for _ in 0 ..< 3 {
            button.tap()
            if app.navigationBars[tab].waitForExistence(timeout: 2) { return true }
        }
        XCTFail("the \(tab) tab never opened")
        return false
    }

    /// A screenshot kept in the result bundle; `SHOTS_DIR` (set as
    /// TEST_RUNNER_SHOTS_DIR on xcodebuild) keeps it as a PNG too.
    func keepShot(_ name: String) {
        let s = XCUIScreen.main.screenshot()
        let a = XCTAttachment(screenshot: s)
        a.name = name
        a.lifetime = .keepAlways
        add(a)
        if let dir = ProcessInfo.processInfo.environment["SHOTS_DIR"], !dir.isEmpty {
            try? s.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name).png"))
        }
    }
}
