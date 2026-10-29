import XCTest
@testable import YNABClient

private func respond(_ status: Int, _ json: String,
                     headers: [String: String]? = nil) -> (HTTPURLResponse, Data) {
    (HTTPURLResponse(url: YNAB.base, statusCode: status, httpVersion: nil, headerFields: headers)!,
     Data(json.utf8))
}

/// Records every wait the client asks for instead of sleeping through it.
private final class Waits: @unchecked Sendable {
    private let lock = NSLock()
    private var _all: [UInt64] = []
    var all: [UInt64] { lock.withLock { _all } }
    func record(_ n: UInt64) { lock.withLock { _all.append(n) } }
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

    func test_accounts_and_categories() async throws {
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
            default:
                return respond(404, "{}")
            }
        }
        let c = client()
        let accounts = try await c.accounts(planID: "p1")
        XCTAssertEqual(accounts.map(\.name), ["DBS"])
        let groups = try await c.categories(planID: "p1")
        XCTAssertEqual(groups.first?.categories.first?.name, "Eating out")
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

    func test_429_waits_as_long_as_retry_after_asks() async throws {
        let waits = Waits()
        MockProtocol.handler = { _ in
            if MockProtocol.requests.count < 2 { return respond(429, "{}", headers: ["Retry-After": "2"]) }
            return respond(200, #"{"data":{"plans":[]}}"#)
        }
        let c = YNABClient(token: "t", session: MockProtocol.session(), retryDelay: 1,
                           sleep: { waits.record($0) })
        _ = try await c.plans()
        XCTAssertEqual(waits.all, [2_000_000_000])
    }

    func test_429_without_retry_after_uses_the_default_delay() async throws {
        let waits = Waits()
        MockProtocol.handler = { _ in
            if MockProtocol.requests.count < 2 { return respond(429, "{}") }
            return respond(200, #"{"data":{"plans":[]}}"#)
        }
        let c = YNABClient(token: "t", session: MockProtocol.session(), retryDelay: 7,
                           sleep: { waits.record($0) })
        _ = try await c.plans()
        XCTAssertEqual(waits.all, [7])
    }

    func test_429_asking_a_long_wait_stops_at_once_with_the_wait() async throws {
        // YNAB's limit is per hour: sleeping an hour inside a sync would hang
        // it, and retrying sooner only burns more of the quota
        let waits = Waits()
        MockProtocol.handler = { _ in respond(429, "{}", headers: ["Retry-After": "3600"]) }
        let c = YNABClient(token: "t", session: MockProtocol.session(), retryDelay: 1,
                           sleep: { waits.record($0) })
        do {
            _ = try await c.plans()
            XCTFail("should throw")
        } catch let e as YNABError {
            XCTAssertEqual(e.kind, .rateLimited)
            XCTAssertEqual(e.retryAfter, 3600)
            XCTAssertTrue(e.message.contains("3600"))
        }
        XCTAssertEqual(MockProtocol.requests.count, 1)
        XCTAssertTrue(waits.all.isEmpty)
    }

    func test_retry_after_reads_seconds_and_http_dates() {
        func http(_ value: String?) -> HTTPURLResponse {
            HTTPURLResponse(url: YNAB.base, statusCode: 429, httpVersion: nil,
                            headerFields: value.map { ["Retry-After": $0] })!
        }
        let now = Date(timeIntervalSince1970: 1_793_000_000)   // Mon, 26 Oct 2026 07:33:20 GMT
        XCTAssertEqual(YNABClient.retryAfter(http("120"), now: now), 120)
        XCTAssertEqual(YNABClient.retryAfter(http("Mon, 26 Oct 2026 07:34:20 GMT"), now: now), 60)
        XCTAssertEqual(YNABClient.retryAfter(http("Mon, 26 Oct 2026 07:00:00 GMT"), now: now), 0)
        XCTAssertNil(YNABClient.retryAfter(http("-5"), now: now))
        XCTAssertNil(YNABClient.retryAfter(http("soon"), now: now))
        XCTAssertNil(YNABClient.retryAfter(http(nil), now: now))
    }

    func test_cancelled_sync_stops_retrying() async throws {
        MockProtocol.handler = { _ in respond(429, "{}") }
        let c = YNABClient(token: "t", session: MockProtocol.session(), retries: 100,
                           retryDelay: 10_000_000_000)
        let task = Task { try await c.plans() }
        try await Task.sleep(nanoseconds: 100_000_000)
        task.cancel()
        do {
            _ = try await task.value
            XCTFail("should throw")
        } catch is CancellationError {
        } catch {
            XCTFail("wrong error: \(error)")
        }
        XCTAssertEqual(MockProtocol.requests.count, 1)
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
    func test_conversion_from_cents() {
        XCTAssertEqual(Milliunits.from(cents: 1234, outflow: true), -12340)
        XCTAssertEqual(Milliunits.from(cents: 1234, outflow: false), 12340)
        XCTAssertEqual(Milliunits.from(cents: 1, outflow: true), -10)
        XCTAssertEqual(Milliunits.from(cents: 0, outflow: true), 0)
        // a printed minus already in the cents flips back, never doubles
        XCTAssertEqual(Milliunits.from(cents: -450, outflow: true), 4500)
    }

    func test_overflow_is_refused_not_wrapped() {
        XCTAssertNil(Milliunits.from(cents: Int.max / 5, outflow: true))
        XCTAssertNil(Milliunits.from(cents: Int.min / 5, outflow: false))
        XCTAssertEqual(Milliunits.from(cents: Int.max / 10, outflow: false), (Int.max / 10) * 10)
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
}
