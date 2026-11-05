import XCTest
@testable import YNABClient

private func respond(_ status: Int, _ json: String) -> (HTTPURLResponse, Data) {
    (HTTPURLResponse(url: YNAB.base, statusCode: status, httpVersion: nil, headerFields: nil)!,
     Data(json.utf8))
}

final class YNABClientTests: XCTestCase {
    override func setUp() {
        MockProtocol.handler = nil
        MockProtocol.requests = []
    }

    private func client() -> YNABClient {
        YNABClient(token: "test-token", session: MockProtocol.session(), retryDelay: 1)
    }

    func test_plans_decode_and_filter_deleted() async throws {
        MockProtocol.handler = { req in
            XCTAssertEqual(req.url?.path, "/v1/plans")
            XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
            return respond(200, """
            {"data":{"plans":[{"id":"p1","name":"Main","deleted":false},{"id":"p2","name":"Old","deleted":true}]}}
            """)
        }
        let plans = try await client().plans()
        XCTAssertEqual(plans.map(\.name), ["Main"])
    }

    func test_accounts_categories_payees() async throws {
        MockProtocol.handler = { req in
            switch req.url?.path {
            case "/v1/plans/p1/accounts":
                return respond(200, """
                {"data":{"accounts":[{"id":"a1","name":"DBS","type":"checking","closed":false,"deleted":false},{"id":"a2","name":"X","type":"checking","closed":true,"deleted":false}]}}
                """)
            case "/v1/plans/p1/categories":
                return respond(200, """
                {"data":{"category_groups":[{"id":"g1","name":"Food","hidden":false,"deleted":false,"categories":[{"id":"c1","name":"Eating out","hidden":false,"deleted":false}]}]}}
                """)
            case "/v1/plans/p1/payees":
                return respond(200, """
                {"data":{"payees":[{"id":"y1","name":"Kopitiam","deleted":false}]}}
                """)
            default:
                return respond(404, "{}")
            }
        }
        let c = client()
        let accounts = try await c.accounts(planID: "p1")
        XCTAssertEqual(accounts.map(\.name), ["DBS"])
        let groups = try await c.categories(planID: "p1")
        XCTAssertEqual(groups.first?.categories.first?.name, "Eating out")
        let payees = try await c.payees(planID: "p1")
        XCTAssertEqual(payees.first?.name, "Kopitiam")
    }

    func test_plan_currency_reads_the_settings_iso_code() async throws {
        MockProtocol.handler = { req in
            XCTAssertEqual(req.url?.path, "/v1/plans/p1/settings")
            return respond(200, """
            {"data":{"settings":{"date_format":{"format":"DD/MM/YYYY"},"currency_format":{"iso_code":"SGD","example_format":"123,456.78","decimal_digits":2,"decimal_separator":".","symbol_first":true,"group_separator":",","currency_symbol":"S$","display_symbol":true}}}}
            """)
        }
        let code = try await client().planCurrency(planID: "p1")
        XCTAssertEqual(code, "SGD")
    }

    func test_plan_currency_is_nil_when_ynab_sends_none() async throws {
        MockProtocol.handler = { _ in
            respond(200, #"{"data":{"settings":{"date_format":null,"currency_format":null}}}"#)
        }
        let code = try await client().planCurrency(planID: "p1")
        XCTAssertNil(code)
    }

    func test_create_transaction_posts_snake_case() async throws {
        MockProtocol.handler = { req in
            XCTAssertEqual(req.httpMethod, "POST")
            var data = req.httpBody ?? Data()
            if data.isEmpty, let stream = req.httpBodyStream {
                stream.open()
                var chunk = [UInt8](repeating: 0, count: 65536)
                while stream.hasBytesAvailable {
                    let n = stream.read(&chunk, maxLength: chunk.count)
                    if n <= 0 { break }
                    data.append(contentsOf: chunk[0..<n])
                }
                stream.close()
            }
            let body = try! JSONSerialization.jsonObject(with: data) as! [String: Any]
            let t = body["transaction"] as! [String: Any]
            XCTAssertEqual(t["account_id"] as? String, "a1")
            XCTAssertEqual(t["amount"] as? Int, -12340)
            XCTAssertEqual(t["import_id"] as? String, "al-abc")
            XCTAssertEqual(t["approved"] as? Bool, false)
            return respond(201, #"{"data":{"transaction":{"id":"t1"}}}"#)
        }
        let txn = NewTransaction(accountID: "a1", date: "2026-10-12", amount: -12340,
                                 payeeName: "Kopitiam", importID: "al-abc")
        let id = try await client().createTransaction(planID: "p1", txn)
        XCTAssertEqual(id, "t1")
    }

    func test_429_retries_then_succeeds() async throws {
        MockProtocol.handler = { _ in
            if MockProtocol.requests.count < 3 { return respond(429, "{}") }
            return respond(200, #"{"data":{"plans":[]}}"#)
        }
        _ = try await client().plans()
        XCTAssertEqual(MockProtocol.requests.count, 3)
    }

    func test_429_past_retries_throws_rate_limited() async throws {
        MockProtocol.handler = { _ in respond(429, "{}") }
        do {
            _ = try await client().plans()
            XCTFail("should throw")
        } catch let e as YNABError {
            XCTAssertEqual(e.kind, .rateLimited)
        } catch {
            XCTFail("wrong error: \(error)")
        }
    }

    func test_401_is_unauthorized() async {
        MockProtocol.handler = { _ in respond(401, "{}") }
        do {
            _ = try await client().plans()
            XCTFail("should throw")
        } catch let e as YNABError {
            XCTAssertEqual(e.kind, .unauthorized)
        } catch {
            XCTFail("wrong error: \(error)")
        }
    }
}

final class MilliunitsTests: XCTestCase {
    func test_conversion() {
        XCTAssertEqual(Milliunits.from(Decimal(string: "12.34")!, outflow: true), -12340)
        XCTAssertEqual(Milliunits.from(Decimal(string: "12.34")!, outflow: false), 12340)
        XCTAssertEqual(Milliunits.from(Decimal(string: "0.01")!, outflow: true), -10)
    }
}

final class ImportIDTests: XCTestCase {
    func test_deterministic_and_short() {
        let d = Date(timeIntervalSince1970: 1_800_000_000)
        let a = ImportID.make(date: d, milliunits: -4500, payee: "Kopitiam", source: "sms")
        let b = ImportID.make(date: d, milliunits: -4500, payee: "Kopitiam", source: "sms")
        XCTAssertEqual(a, b)
        XCTAssertLessThanOrEqual(a.count, 36)
        XCTAssertTrue(a.hasPrefix("al-"))
        let c = ImportID.make(date: d, milliunits: -4500, payee: "Other", source: "sms")
        XCTAssertNotEqual(a, c)
    }

    /// Review on #50: two real Wallet purchases of one amount at one merchant
    /// on one day get different ids once each carries its capture moment;
    /// without one, the id is the old build's, so a legacy retry matches.
    func test_capture_moment_separates_same_day_purchases() {
        let day = Date(timeIntervalSince1970: 1_800_000_000)
        let morning = ImportID.make(date: day, milliunits: -4500, payee: "Kopitiam",
                                    source: "wallet", capturedAt: day.addingTimeInterval(7 * 3600))
        let evening = ImportID.make(date: day, milliunits: -4500, payee: "Kopitiam",
                                    source: "wallet", capturedAt: day.addingTimeInterval(17 * 3600))
        XCTAssertNotEqual(morning, evening)
        XCTAssertLessThanOrEqual(morning.count, 36)
        XCTAssertEqual(ImportID.make(date: day, milliunits: -4500, payee: "Kopitiam", source: "wallet"),
                       ImportID.make(date: day, milliunits: -4500, payee: "Kopitiam", source: "wallet",
                                     capturedAt: nil))
    }
}
