import XCTest
@testable import KopifundClient
import TestSupport

/// G25: pairing spends a kfp_ token once; after it, every read carries the
/// kfd_ bearer the hub now expects.
final class KopifundClientTests: XCTestCase {
    private let base = URL(string: "https://mac.tail1234.ts.net")!

    override func setUp() {
        MockProtocol.handler = nil
        MockProtocol.requests = []
    }

    private let respond: @Sendable (Int, String) -> (HTTPURLResponse, Data) = { status, json in
        (HTTPURLResponse(url: URL(string: "https://mac.tail1234.ts.net")!,
                         statusCode: status, httpVersion: nil, headerFields: nil)!,
         Data(json.utf8))
    }

    /// URLSession may move a POST body off `httpBody` into the stream before a
    /// protocol ever sees it, so a test reads whichever one holds the bytes.
    private func body(of req: URLRequest) -> Data {
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
        return data
    }

    private func client(bearer: String? = "kfd_abc") -> KopifundClient {
        KopifundClient(baseURL: base, bearer: bearer, session: MockProtocol.session(),
                       retries: 0)
    }

    // MARK: - LinkCode

    func testLinkCodeParsesTheQRPayload() {
        let payload = "kopifund://pair?url=https%3A%2F%2Fmac.tail1234.ts.net&token=kfp_0123456789abcdef0123456789abcdef"
        let code = LinkCode(text: payload)
        XCTAssertEqual(code?.base, base)
        XCTAssertEqual(code?.token, "kfp_0123456789abcdef0123456789abcdef")
    }

    func testLinkCodeTakesAPastedPayloadWithWhitespace() {
        let code = LinkCode(text: "  kopifund://pair?url=https%3A%2F%2Fmac.tail1234.ts.net&token=kfp_x\n")
        XCTAssertEqual(code?.token, "kfp_x")
    }

    func testLinkCodeRejectsWrongSchemeMissingTokenAndPlainHTTP() {
        XCTAssertNil(LinkCode(text: "https://pair?url=https%3A%2F%2Fmac.ts.net&token=kfp_x"))
        XCTAssertNil(LinkCode(text: "kopifund://pair?url=https%3A%2F%2Fmac.ts.net"))
        XCTAssertNil(LinkCode(text: "kopifund://pair?url=https%3A%2F%2Fmac.ts.net&token=kfd_x"),
                     "a device bearer is not a pair token")
        XCTAssertNil(LinkCode(text: "kopifund://pair?url=http%3A%2F%2Fmac.ts.net&token=kfp_x"),
                     "the link only speaks HTTPS at the tailnet edge")
        XCTAssertNil(LinkCode(text: "kopifund://other?url=https%3A%2F%2Fmac.ts.net&token=kfp_x"))
    }

    // MARK: - pairing

    func testPairPostsTheTokenWithoutABearerAndAnswersTheDevice() async throws {
        MockProtocol.handler = { [respond] _ in respond(200, #"{"device_id":"dev_1","name":"Test iPhone","token":"kfd_secret"}"#) }
        let code = LinkCode(text: "kopifund://pair?url=https%3A%2F%2Fmac.tail1234.ts.net&token=kfp_once")!
        let device = try await KopifundClient.pair(code: code, name: "Test iPhone",
                                                   session: MockProtocol.session())
        XCTAssertEqual(device, PairedDevice(deviceID: "dev_1", name: "Test iPhone", token: "kfd_secret"))
        let req = try XCTUnwrap(MockProtocol.requests.first)
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.url?.path, "/api/pair/redeem")
        XCTAssertNil(req.value(forHTTPHeaderField: "Authorization"),
                     "pairing is the one call that carries no bearer")
        let sent = try JSONSerialization.jsonObject(with: body(of: req)) as? [String: String]
        XCTAssertEqual(sent?["token"], "kfp_once")
        XCTAssertEqual(sent?["name"], "Test iPhone")
        XCTAssertEqual(sent?["nonce"]?.count, 36)
    }

    func testSpentPairTokenIsAConflict() async {
        MockProtocol.handler = { [respond] _ in respond(422, #"{"error":"already spent"}"#) }
        do {
            _ = try await client(bearer: nil).pair(token: "kfp_gone", name: "iPhone")
            XCTFail("a spent token must throw")
        } catch let e as KopifundError {
            XCTAssertEqual(e.kind, .conflict)
        } catch { XCTFail("wrong error: \(error)") }
    }

    // MARK: - reads carry the bearer

    func testHealthIsOpenWithoutABearer() async throws {
        MockProtocol.handler = { [respond] _ in respond(200, #"{"ok":true}"#) }
        let ok = try await client(bearer: nil).health()
        XCTAssertTrue(ok)
        XCTAssertNil(MockProtocol.requests.first?.value(forHTTPHeaderField: "Authorization"))
    }

    func testCheckSendsTheBearerToTheLinkCheck() async throws {
        MockProtocol.handler = { [respond] _ in respond(200, #"{"ok":true}"#) }
        let ok = try await client().check()
        XCTAssertTrue(ok)
        let req = MockProtocol.requests.first
        XCTAssertEqual(req?.url?.path, "/api/link/check")
        XCTAssertEqual(req?.httpMethod, "GET")
        XCTAssertNotNil(req?.value(forHTTPHeaderField: "Authorization"))
    }

    /// A revoked phone: the hub is up (health would say ok) but the link
    /// check refuses the bearer, so Test must not read as reachable.
    func testCheckOnARevokedDeviceIsUnpaired() async {
        MockProtocol.handler = { [respond] _ in respond(401, #"{"error":"unpaired"}"#) }
        do {
            _ = try await client().check()
            XCTFail("a revoked bearer must throw")
        } catch let e as KopifundError {
            XCTAssertEqual(e.kind, .unpaired)
        } catch { XCTFail("wrong error: \(error)") }
    }

    func testProfileDecodesAccountsAndSendsTheBearer() async throws {
        MockProtocol.handler = { [respond] _ in
            respond(200, #"{"accounts":[{"name":"OCBC 360","bank":"OCBC","number":"•• 4821","statements":12,"transactions":640,"first":"2025-11","last":"2026-10","folder":null,"pdfs":null}],"transactions":640}"#)
        }
        let profile = try await client().profile()
        XCTAssertEqual(profile.transactions, 640)
        XCTAssertEqual(profile.accounts.first?.name, "OCBC 360")
        XCTAssertEqual(profile.accounts.first?.number, "•• 4821")
        XCTAssertEqual(MockProtocol.requests.first?.value(forHTTPHeaderField: "Authorization"),
                       "Bearer kfd_abc")
    }

    func testTransactionsBuildsTheQueryAndDecodesCents() async throws {
        MockProtocol.handler = { [respond] _ in
            respond(200, #"{"rows":[{"key":"aabbccddeeff","date":"2026-10-04","amount":-1234,"amount_sgd":-1234,"currency":"SGD","account":"OCBC 360","bank":"OCBC","merchant":"GRAB *RIDE","raw":"GRAB *RIDE 12.34","description":"","payee":"Grab","category":"Transport","category_group":"Getting around","flow":"External","trip":"","flags":[{"code":"new_payee","text":"first transfer to this payee"}]}],"days":[],"total":1,"sum_sgd":-1234,"in_sgd":0,"out_sgd":-1234,"months":["2026-10"],"categories":["(none)","Transport"],"accounts":[{"value":"7","label":"OCBC 360","bank":"OCBC"}]}"#)
        }
        let page = try await client().transactions(month: "2026-10", limit: 50, offset: 100)
        XCTAssertEqual(page.total, 1)
        XCTAssertEqual(page.outSGD, -1234)
        let row = try XCTUnwrap(page.rows.first)
        XCTAssertEqual(row.date, "2026-10-04", "the statement's calendar date, untouched")
        XCTAssertEqual(row.amount, -1234, "cents, not dollars")
        XCTAssertEqual(row.payee, "Grab")
        XCTAssertEqual(row.flags, [FlagReason(code: "new_payee", text: "first transfer to this payee")],
                       "flags arrive as {code, text} objects, not strings")
        XCTAssertEqual(page.accounts.first?.value, "7")
        let q = try XCTUnwrap(URLComponents(url: MockProtocol.requests[0].url!,
                                            resolvingAgainstBaseURL: false)?.queryItems)
        XCTAssertEqual(q.first(where: { $0.name == "month" })?.value, "2026-10")
        XCTAssertEqual(q.first(where: { $0.name == "limit" })?.value, "50")
        XCTAssertEqual(q.first(where: { $0.name == "offset" })?.value, "100")
    }

    func testCategoriesDecodesGroups() async throws {
        MockProtocol.handler = { [respond] _ in
            respond(200, #"{"groups":[{"id":2,"name":"Getting around","kind":"Spend","icon":"car","colour":null,"shown":"mint","fixed":false,"categories":[{"id":9,"name":"Transport","need_class":"Want","cost_type":"Variable","group":"Getting around","transactions":3,"monthly":null,"budgets":1,"rules":2,"personal_rules":0}]}],"need_classes":{"Spend":["Need","Want"]},"months":18}"#)
        }
        let state = try await client().categories()
        XCTAssertEqual(state.groups.first?.name, "Getting around")
        XCTAssertEqual(state.groups.first?.categories.first?.needClass, "Want")
        XCTAssertEqual(state.months, 18)
    }

    func testPostPendingSendsTheRowWrappedAndCarriesTheBearer() async throws {
        MockProtocol.handler = { [respond] _ in respond(200, #"{"accepted":1,"matched":0}"#) }
        let posted = try await client().postPending(PendingPost(
            importID: "kph_abc", date: "2026-10-20", amountMinor: -1250,
            currency: "SGD", payee: "Grab"))
        XCTAssertEqual(posted, LinkPosted(accepted: 1, matched: 0))
        let req = MockProtocol.requests[0]
        XCTAssertEqual(req.url?.path, "/api/link/transactions")
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "Bearer kfd_abc")
        let sent = try JSONSerialization.jsonObject(with: body(of: req)) as? [String: Any]
        let row = (sent?["rows"] as? [[String: Any]])?.first
        XCTAssertEqual(row?["import_id"] as? String, "kph_abc")
        XCTAssertEqual(row?["amount_minor"] as? Int, -1250)
        XCTAssertEqual(row?["date"] as? String, "2026-10-20")
        XCTAssertEqual(row?["payee"] as? String, "Grab")
    }

    func testPostPendingAnUnpairedDeviceIsA401() async {
        MockProtocol.handler = { [respond] _ in respond(401, #"{"error":"unpaired"}"#) }
        do {
            _ = try await client().postPending(PendingPost(
                importID: "kph_abc", date: "2026-10-20", amountMinor: -1250,
                currency: "SGD", payee: "Grab"))
            XCTFail("an unpaired device must throw")
        } catch let e as KopifundError {
            XCTAssertEqual(e.kind, .unpaired)
        } catch {
            XCTFail("unexpected \(error)")
        }
    }

    func testUnpairedAndNotOnLink() async {
        MockProtocol.handler = { [respond] _ in respond(401, #"{"error":"pair first"}"#) }
        do {
            _ = try await client().profile()
            XCTFail("401 must throw")
        } catch let e as KopifundError {
            XCTAssertEqual(e.kind, .unpaired)
        } catch { XCTFail("wrong error: \(error)") }

        MockProtocol.handler = { [respond] _ in respond(403, #"{"error":"not on the link"}"#) }
        do {
            _ = try await client().profile()
            XCTFail("403 must throw")
        } catch let e as KopifundError {
            XCTAssertEqual(e.kind, .notOnLink)
        } catch { XCTFail("wrong error: \(error)") }
    }

    func testNetworkDropRetriesThenThrows() async {
        MockProtocol.handler = { _ in
            throw URLError(.networkConnectionLost)
        }
        let flaky = KopifundClient(baseURL: base, bearer: "kfd_abc", session: MockProtocol.session(),
                                   retries: 2, retryDelay: 1)
        do {
            _ = try await flaky.profile()
            XCTFail("a dead tailnet must throw .network")
        } catch let e as KopifundError {
            XCTAssertEqual(e.kind, .network)
            XCTAssertEqual(MockProtocol.requests.count, 3, "first try plus two retries")
        } catch { XCTFail("wrong error: \(error)") }
    }

    // MARK: - LinkStore

    /// A Sendable stand-in for the Keychain item the real store maps `read`
    /// and `write` onto.
    final class SecretBox: @unchecked Sendable { var value: String? }

    func testLinkStoreRoundTripsAndForgets() {
        let secret = SecretBox()
        let store = LinkStore(read: { secret.value }, write: { secret.value = $0; return true })
        XCTAssertNil(store.device)
        XCTAssertNil(store.client())

        store.link(base: base, PairedDevice(deviceID: "dev_1", name: "iPhone", token: "kfd_secret"))
        XCTAssertEqual(store.device?.deviceID, "dev_1")
        XCTAssertEqual(store.base, base)
        XCTAssertNotNil(store.client())

        store.unlink()
        XCTAssertNil(store.device)
    }

    func testLinkStoreReadsCorruptJSONAsUnlinked() {
        let store = LinkStore(read: { "not json {" }, write: { _ in true })
        XCTAssertNil(store.device)
    }

    func testLinkStoreReportsAFailedSave() {
        let secret = SecretBox()
        let store = LinkStore(read: { secret.value }, write: { _ in false })
        XCTAssertFalse(store.link(base: base,
                                  PairedDevice(deviceID: "dev_1", name: "iPhone", token: "kfd_secret")),
                       "a write the Keychain refused must not read as linked")
        XCTAssertNil(store.device)
    }
}
