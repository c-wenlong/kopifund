import XCTest

/// G25: the whole link path end to end on the simulator. `link_e2e.py`
/// stands in for `tailscale serve`: the test mints a pair token on the
/// loopback hub, pastes the kopifund://pair payload into Settings, pairs
/// over the TLS forwarder on :8990, then reads the record's accounts and
/// transactions with the fresh bearer. Without the rig the test skips.
@MainActor
final class LinkUITests: XCTestCase {

    private let hub = URL(string: "http://127.0.0.1:8770")!
    private let forwarder = "https://127.0.0.1:8990"

    /// A link left in the Keychain by a failure before the cleanup tap hides
    /// the paste field for the next run, so teardown always drops it.
    override func tearDown() {
        let app = XCUIApplication()
        app.launch()
        app.tabBars.buttons["Settings"].tap()
        let unlink = app.buttons["Unlink"]
        if unlink.waitForExistence(timeout: 3) { unlink.tap() }
    }

    private func hubUp() async -> Bool {
        var req = URLRequest(url: hub.appendingPathComponent("api/health"))
        req.timeoutInterval = 3
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return false }
        return body["ok"] as? Bool == true
    }

    /// POST /api/link/pair-token on loopback (where minting is allowed) and
    /// the kfp_ token out of the payload's query.
    private func pairToken() async throws -> String {
        var req = URLRequest(url: hub.appendingPathComponent("api/link/pair-token"))
        req.httpMethod = "POST"
        req.setValue("1", forHTTPHeaderField: "X-Kopifund")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        let (data, resp) = try await URLSession.shared.data(for: req)
        XCTAssertEqual((resp as? HTTPURLResponse)?.statusCode, 200)
        let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let payload = try XCTUnwrap(body?["payload"] as? String)
        let token = URLComponents(string: payload)?
            .queryItems?.first(where: { $0.name == "token" })?.value
        return try XCTUnwrap(token)
    }

    /// The newest SGD spend in the hub's record with a plain payee, read on
    /// loopback, as the alert text prints it: the day with its year ("12 Oct
    /// 2026", so a row from another year reads right), the amount as
    /// "SGD12.50" and the payee in capitals.
    private func recordSpend() async throws -> (day: String, amount: String, payee: String) {
        var url = URLComponents(url: hub.appendingPathComponent("api/transactions"),
                                resolvingAgainstBaseURL: false)!
        url.queryItems = [URLQueryItem(name: "limit", value: "200")]
        let (data, _) = try await URLSession.shared.data(from: try XCTUnwrap(url.url))
        let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let rows = body?["rows"] as? [[String: Any]] ?? []
        let plain = try NSRegularExpression(pattern: "^[A-Za-z][A-Za-z ]{2,30}$")
        let row = try XCTUnwrap(rows.first { r in
            let payee = r["payee"] as? String ?? ""
            return r["currency"] as? String == "SGD" && (r["amount"] as? Int ?? 0) < 0
                && plain.firstMatch(in: payee, range: NSRange(payee.startIndex..., in: payee)) != nil
        }, "no SGD spend with a plain payee in the hub's record")
        let read = DateFormatter()
        read.locale = Locale(identifier: "en_US_POSIX")
        read.timeZone = TimeZone(identifier: "UTC")
        read.dateFormat = "yyyy-MM-dd"
        let print = DateFormatter()
        print.locale = read.locale
        print.timeZone = read.timeZone
        print.dateFormat = "d MMM yyyy"
        let day = try XCTUnwrap(read.date(from: row["date"] as? String ?? ""))
        let cents = -(row["amount"] as? Int ?? 0)
        return (print.string(from: day), String(format: "SGD%d.%02d", cents / 100, cents % 100),
                (row["payee"] as? String ?? "").uppercased())
    }

    /// Type the payload into Settings' paste field and wait for Linked. A
    /// phone left linked by a failed run gets unlinked first, so the paste
    /// field is always the starting point.
    private func pair(_ app: XCUIApplication, payload: String) {
        app.tabBars.buttons["Settings"].tap()
        let field = app.textFields["Paste the pairing code"]
        if !field.waitForExistence(timeout: 5) {
            app.buttons["Unlink"].tap()
            XCTAssertTrue(field.waitForExistence(timeout: 5))
        }
        field.tap()
        field.typeText(payload)
        app.buttons["Pair"].tap()
        XCTAssertTrue(app.staticTexts["Linked"].waitForExistence(timeout: 20),
                      "pairing failed; problem text: \(app.staticTexts.allElementsBoundByIndex.map(\.label))")
    }

    private func linkURL(_ token: String) -> String {
        let escaped = forwarder.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!
        return "kopifund://pair?url=\(escaped)&token=\(token)"
    }

    /// Pair through the paste field, then read: the Accounts list must show a
    /// row and the Transactions browser at least one SGD figure, all through
    /// the bearer the phone just earned.
    func testPairAndReadTheRecord() async throws {
        guard await hubUp() else {
            throw XCTSkip("link e2e rig not running: python3 apps/ios/link_e2e.py")
        }
        let app = XCUIApplication()
        app.launch()
        pair(app, payload: linkURL(try await pairToken()))
        XCTAssertTrue(app.staticTexts["Server"].exists)

        app.tabBars.buttons["Kopifund"].tap()
        XCTAssertTrue(app.staticTexts["Accounts"].waitForExistence(timeout: 15))
        // one account row is enough: the record's names stay out of the repo
        let accounts = app.staticTexts.matching(NSPredicate(format: "label MATCHES %@", "\\d+"))
        XCTAssertTrue(accounts.firstMatch.waitForExistence(timeout: 10),
                      "no transaction count under an account row")
        app.buttons["Transactions"].tap()
        XCTAssertTrue(app.staticTexts
            .matching(NSPredicate(format: "label CONTAINS %@", "SGD"))
            .firstMatch.waitForExistence(timeout: 15),
                      "no SGD figure on the Transactions page")

        // unlinked again, so the next run starts clean
        app.tabBars.buttons["Settings"].tap()
        app.buttons["Unlink"].tap()
        XCTAssertTrue(app.textFields["Paste the pairing code"].waitForExistence(timeout: 5))
    }

    /// A spend the record already covers is marked, never posted: the pasted
    /// alert reads as no saved pattern, lands in Unmatched, and the link
    /// sweep flags it "in kopifund" against a row the hub's record holds.
    /// The row is read off the hub at run time, so no spend from the data
    /// folder is written into this file.
    func testDedupeMarksACoveredAlert() async throws {
        guard await hubUp() else {
            throw XCTSkip("link e2e rig not running: python3 apps/ios/link_e2e.py")
        }
        let app = XCUIApplication()
        app.launch()
        pair(app, payload: linkURL(try await pairToken()))

        // one line: a newline in the alert's field submits its default
        // action and the dialog closes mid-typing
        let spend = try await recordSpend()
        let alert = "Transaction Alerts We refer to your PayLah! Scan & Pay Transfer dated \(spend.day). We are pleased to confirm that the transaction was completed. Date & Time: \(spend.day) 14:55 (SGT) Amount: \(spend.amount) To: \(spend.payee)"
        app.tabBars.buttons["Inbox"].tap()
        app.buttons["doc.on.clipboard"].tap()
        let field = app.textFields["Alert text"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText(alert)
        let ingest = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == 'Ingest'")).firstMatch
        XCTAssertTrue(ingest.waitForExistence(timeout: 5),
                      "no Ingest element; tree: \(app.debugDescription.prefix(4000))")
        ingest.tap()

        app.tabBars.buttons["Unmatched"].tap()
        XCTAssertTrue(app.staticTexts
            .matching(NSPredicate(format: "label CONTAINS %@", "in kopifund"))
            .firstMatch.waitForExistence(timeout: 25),
            "the record's \(spend.payee) row should cover the alert; labels: \(app.staticTexts.allElementsBoundByIndex.map(\.label))")

        app.tabBars.buttons["Settings"].tap()
        app.buttons["Unlink"].tap()
    }

    /// An approved alert posts to the hub's pending list (SPEC.md, "Phone
    /// link (G25)"): the seeded row needs no pattern or YNAB — the flush's
    /// Kopifund post runs off the link alone — and loopback GET
    /// /api/link/pending then shows it waiting (or matched, if its
    /// statement row were already loaded; "E2E Payee" matches none).
    func testApprovePostsAPendingRow() async throws {
        guard await hubUp() else {
            throw XCTSkip("link e2e rig not running: python3 apps/ios/link_e2e.py")
        }
        let app = XCUIApplication()
        // a fresh payee each run: a row left on the hub by an earlier run
        // must not satisfy the read below when nothing new posted
        let payee = "E2E Payee \(Int(Date().timeIntervalSince1970) % 100_000)"
        app.launchArguments = ["-seedSpend", "1890|SGD|\(payee)|2026-10-15"]
        app.launch()
        pair(app, payload: linkURL(try await pairToken()))

        app.tabBars.buttons["Inbox"].tap()
        let approveAll = app.buttons["Approve all"]
        XCTAssertTrue(approveAll.waitForExistence(timeout: 10),
                      "no pending row to approve: \(app.staticTexts.allElementsBoundByIndex.map(\.label))")
        approveAll.tap()

        let row = try await waitForPending(payee: payee)
        XCTAssertEqual(row["payee"] as? String, payee)
        XCTAssertEqual(row["amount_minor"] as? Int, -1890)
        XCTAssertEqual(row["currency"] as? String, "SGD")
        XCTAssertEqual(row["date"] as? String, "2026-10-15")
        XCTAssertEqual(row["state"] as? String, "waiting")
        XCTAssertTrue((row["device"] as? String)?.isEmpty == false)
        XCTAssertTrue((row["import_id"] as? String)?.hasPrefix("kph_") == true)

        app.tabBars.buttons["Settings"].tap()
        app.buttons["Unlink"].tap()
    }

    /// Loopback read of the pending list until the phone's post lands (the
    /// flush posts after the approve tap returns).
    private func waitForPending(payee: String) async throws -> [String: Any] {
        for _ in 0..<20 {
            var req = URLRequest(url: hub.appendingPathComponent("api/link/pending"))
            req.timeoutInterval = 3
            if let (data, resp) = try? await URLSession.shared.data(for: req),
               (resp as? HTTPURLResponse)?.statusCode == 200,
               let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let rows = body["rows"] as? [[String: Any]],
               let row = rows.first(where: { $0["payee"] as? String == payee }) {
                return row
            }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        XCTFail("no pending row for \(payee) on the hub after 10s")
        return [:]
    }
}
