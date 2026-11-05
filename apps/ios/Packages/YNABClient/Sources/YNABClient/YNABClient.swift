import Foundation

/// G24: the one host the app ever calls. Everything hangs off `/v1`.
public enum YNAB {
    public static let host = "api.ynab.com"
    public static let base = URL(string: "https://api.ynab.com/v1")!
}

public struct YNABError: Error, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case unauthorized           // 401: the token is bad or revoked
        case notFound               // 404
        case conflict               // 409: a duplicate import_id YNAB already took
        case rateLimited            // 429 after the retries ran out
        case http(Int)              // anything else with a status code
        case network                // no response at all
        case malformed              // a body that decodes to nothing
    }
    public let kind: Kind
    public let message: String
}

/// A URLProtocol the tests point URLSession at: `register` a handler per
/// request and read back the requests the client sent.
public final class MockProtocol: URLProtocol, @unchecked Sendable {
    public nonisolated(unsafe) static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?
    public nonisolated(unsafe) static var requests: [URLRequest] = []

    public override class func canInit(with request: URLRequest) -> Bool { true }
    public override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    public override func startLoading() {
        guard let handler = MockProtocol.handler else { return }
        MockProtocol.requests.append(request)
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }
    public override func stopLoading() {}

    public static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockProtocol.self]
        return URLSession(configuration: config)
    }
}

public actor YNABClient {
    private let token: String
    private let session: URLSession
    /// Retries a 429 or a network drop this many times, waiting `retryDelay`
    /// between tries. Injected small in tests.
    private let retries: Int
    private let retryDelay: UInt64

    public init(token: String, session: URLSession = .shared, retries: Int = 3,
                retryDelay: UInt64 = 1_000_000_000) {
        self.token = token
        self.session = session
        self.retries = retries
        self.retryDelay = retryDelay
    }

    public func plans() async throws -> [Plan] {
        try await get("plans", as: PlansResponse.self).data.plans.filter { !$0.deleted }
    }

    public func accounts(planID: String) async throws -> [Account] {
        try await get("plans/\(planID)/accounts", as: AccountsResponse.self).data.accounts.filter { !$0.deleted && !$0.closed }
    }

    public func categories(planID: String) async throws -> [CategoryGroup] {
        try await get("plans/\(planID)/categories", as: CategoriesResponse.self)
            .data.categoryGroups.filter { !$0.deleted && !$0.hidden }
    }

    public func payees(planID: String) async throws -> [Payee] {
        try await get("plans/\(planID)/payees", as: PayeesResponse.self).data.payees.filter { !$0.deleted }
    }

    /// The ISO code of the currency the plan keeps ("SGD"), nil when YNAB
    /// reports no currency format. The sync posts a row only in this
    /// currency (G24 audit).
    public func planCurrency(planID: String) async throws -> String? {
        try await get("plans/\(planID)/settings", as: PlanSettingsResponse.self)
            .data.settings.currencyFormat?.isoCode
    }

    /// Post the queue's next transaction. `NewTransaction.amount` is already
    /// in milliunits; `importID` is what stops a retried post becoming a
    /// duplicate.
    @discardableResult
    public func createTransaction(planID: String, _ txn: NewTransaction) async throws -> String {
        let body = try JSONEncoder().encode(TransactionRequest(transaction: txn))
        let response: TransactionResponse = try await request("plans/\(planID)/transactions", method: "POST", body: body)
        return response.data.transaction.id
    }

    // MARK: - transport

    private func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        try await request(path, method: "GET", body: nil)
    }

    private func request<T: Decodable>(_ path: String, method: String, body: Data?) async throws -> T {
        var attempt = 0
        while true {
            var url = URLRequest(url: YNAB.base.appendingPathComponent(path))
            url.httpMethod = method
            url.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            if let body {
                url.httpBody = body
                url.setValue("application/json", forHTTPHeaderField: "Content-Type")
            }
            let (data, response): (Data, URLResponse)
            do {
                (data, response) = try await session.data(for: url)
            } catch {
                attempt += 1
                if attempt <= retries {
                    try? await Task.sleep(nanoseconds: retryDelay)
                    continue
                }
                throw YNABError(kind: .network, message: error.localizedDescription)
            }
            guard let http = response as? HTTPURLResponse else {
                throw YNABError(kind: .network, message: "no HTTP response")
            }
            switch http.statusCode {
            case 200...299:
                do { return try JSONDecoder().decode(T.self, from: data) }
                catch { throw YNABError(kind: .malformed, message: String(describing: error)) }
            case 401, 403:
                throw YNABError(kind: .unauthorized, message: "token rejected")
            case 404:
                throw YNABError(kind: .notFound, message: path)
            case 409:
                throw YNABError(kind: .conflict, message: "import_id already used")
            case 429:
                attempt += 1
                if attempt <= retries {
                    try? await Task.sleep(nanoseconds: retryDelay)
                    continue
                }
                throw YNABError(kind: .rateLimited, message: "rate limited")
            default:
                throw YNABError(kind: .http(http.statusCode), message: "HTTP \(http.statusCode)")
            }
        }
    }
}

// MARK: - milliunits and import_id

/// YNAB counts money in milliunits: $12.34 is 12340, and outflows are
/// negative. Money is Decimal everywhere until this conversion at the wire.
public enum Milliunits {
    public static func from(_ amount: Decimal, outflow: Bool) -> Int {
        let scaled = amount * 1000
        var rounded = Decimal()
        var copy = scaled
        NSDecimalRound(&rounded, &copy, 0, .plain)
        let v = NSDecimalNumber(decimal: rounded).intValue
        return outflow ? -v : v
    }
}

/// A deterministic `import_id` so a retried post never writes two rows. YNAB
/// caps it at 36 characters; the hash keeps the id stable for the same alert.
public enum ImportID {
    /// `Alerts` prefix + a hash of date, milliunit amount, payee and source,
    /// 32 hex chars: `al-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` is 35.
    public static func make(date: Date, milliunits: Int, payee: String, source: String?) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let c = cal.dateComponents([.year, .month, .day], from: date)
        let day = String(format: "%04d%02d%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
        let key = "\(day)|\(milliunits)|\(payee.lowercased().trimmingCharacters(in: .whitespaces))|\(source ?? "")"
        return "al-" + fnv1a(key)
    }

    /// FNV-1a over UTF-8, 128-bit-style: two 64-bit halves seeded differently,
    /// hexed into 32 chars. Deterministic and dependency-free.
    private static func fnv1a(_ s: String) -> String {
        func hash(_ seed: UInt64) -> UInt64 {
            var h: UInt64 = seed
            for b in s.utf8 {
                h ^= UInt64(b)
                h &*= 1099511628211
            }
            return h
        }
        return String(format: "%016x%016x", hash(1469598103934665603), hash(1099511628211))
    }
}
