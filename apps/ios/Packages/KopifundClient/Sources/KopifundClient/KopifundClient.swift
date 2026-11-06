import Foundation

/// G25: the hub a paired phone talks to. `baseURL` is the tailnet HTTPS name
/// `tailscale serve` fronts (https://<mac>.<tailnet>.ts.net), which proxies to
/// the loopback-only server adding X-Forwarded-For — that header is what puts
/// a request on the link path, never anything this client sends.

public struct KopifundError: Error, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case unpaired               // 401: no bearer, a bad one, or a revoked device
        case notOnLink              // 403: the link refuses this route (admin, pages)
        case conflict               // 422: the pair token was spent or expired
        case http(Int)              // anything else with a status code
        case network                // no response at all (tailnet down, hub off)
        case malformed              // a body that decodes to nothing
    }
    public let kind: Kind
    public let message: String
}

/// A `kopifund://pair?url=…&token=…` deep link, from the Settings QR or a
/// paste. `token` is the single-use pair token (kfp_…), spent on redeem.
public struct LinkCode: Equatable, Sendable {
    public let base: URL
    public let token: String

    public init?(text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let c = URLComponents(string: trimmed),
              c.scheme == "kopifund", c.host == "pair",
              let raw = c.queryItems?.first(where: { $0.name == "url" })?.value,
              let token = c.queryItems?.first(where: { $0.name == "token" })?.value,
              token.hasPrefix("kfp_"),
              let base = URL(string: raw), base.scheme == "https", base.host != nil
        else { return nil }
        self.base = base
        self.token = token
    }
}

public actor KopifundClient {
    private let base: URL
    private let bearer: String?
    private let session: URLSession
    /// Retries a network drop this many times, waiting `retryDelay` between
    /// tries (a tailnet reconnect loses requests, not answers). Injected
    /// small in tests.
    private let retries: Int
    private let retryDelay: UInt64

    public init(baseURL: URL, bearer: String? = nil, session: URLSession = .shared,
                retries: Int = 3, retryDelay: UInt64 = 1_000_000_000) {
        self.base = baseURL
        self.bearer = bearer
        self.session = session
        self.retries = retries
        self.retryDelay = retryDelay
    }

    /// Pairing happens before this device has a bearer, so it is the one call
    /// that never sends Authorization. The pair token spends once, on the hub.
    public static func pair(code: LinkCode, name: String,
                            session: URLSession = .shared) async throws -> PairedDevice {
        let client = KopifundClient(baseURL: code.base, session: session)
        return try await client.pair(token: code.token, name: name)
    }

    public func health() async throws -> Bool {
        struct Health: Decodable { let ok: Bool? }
        return try await get("/api/health", as: Health.self).ok == true
    }

    /// The phone's Test: GET /api/link/check with the bearer. On the link
    /// path only a paired, unrevoked device gets an answer (a revoked one
    /// gets 401, thrown as `.unpaired`), so true means the link itself works;
    /// `health` answers any caller and says only that the hub is up.
    public func check() async throws -> Bool {
        struct Check: Decodable { let ok: Bool? }
        return try await get("/api/link/check", as: Check.self).ok == true
    }

    public func pair(token: String, name: String) async throws -> PairedDevice {
        // one nonce per pairing, in the body every retry resends: a retry after a
        // lost reply gets the same device row back, not an orphan second one
        let body = try JSONEncoder().encode(["token": token, "name": name, "nonce": UUID().uuidString])
        return try await request("/api/pair/redeem", method: "POST", body: body, authorize: false)
    }

    public func profile() async throws -> Profile {
        try await get("/api/profile", as: Profile.self)
    }

    /// One page of the ledger, newest first. `month` is YYYY-MM; `account`
    /// the id TransactionsPage.accounts lists. The hub's cap applies to
    /// `limit` (out of range clamps server-side).
    public func transactions(month: String? = nil, account: String? = nil,
                             limit: Int = 100, offset: Int = 0) async throws -> TransactionsPage {
        var items = [URLQueryItem(name: "limit", value: String(limit)),
                     URLQueryItem(name: "offset", value: String(offset))]
        if let month { items.append(URLQueryItem(name: "month", value: month)) }
        if let account { items.append(URLQueryItem(name: "account", value: account)) }
        return try await get("/api/transactions", query: items, as: TransactionsPage.self)
    }

    public func categories() async throws -> CategoriesState {
        try await get("/api/categories", as: CategoriesState.self)
    }

    /// The one bearer write the gate allows: an approved alert row into
    /// pending_txn, held until a statement row confirms it (SPEC.md, "Phone
    /// link (G25)"). The row's `importID` is its key, so a retry can never
    /// mint a second pending row.
    public func postPending(_ row: PendingPost) async throws -> LinkPosted {
        struct Body: Encodable { let rows: [PendingPost] }
        let body = try JSONEncoder().encode(Body(rows: [row]))
        return try await request("/api/link/transactions", method: "POST",
                                 body: body, authorize: true)
    }

    // MARK: - transport

    private func get<T: Decodable>(_ path: String, query: [URLQueryItem]? = nil,
                                   as type: T.Type) async throws -> T {
        try await request(path, method: "GET", query: query, body: nil, authorize: true)
    }

    private func request<T: Decodable>(_ path: String, method: String,
                                       query: [URLQueryItem]? = nil, body: Data?,
                                       authorize: Bool) async throws -> T {
        var attempt = 0
        while true {
            var comps = URLComponents(url: base, resolvingAgainstBaseURL: false)!
            comps.path = path
            comps.queryItems = query
            var url = URLRequest(url: comps.url!)
            url.httpMethod = method
            if authorize, let bearer {
                url.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization")
            }
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
                throw KopifundError(kind: .network, message: error.localizedDescription)
            }
            guard let http = response as? HTTPURLResponse else {
                throw KopifundError(kind: .network, message: "no HTTP response")
            }
            switch http.statusCode {
            case 200...299:
                do { return try JSONDecoder().decode(T.self, from: data) }
                catch { throw KopifundError(kind: .malformed, message: String(describing: error)) }
            case 401:
                throw KopifundError(kind: .unpaired, message: "this device is not paired, or was revoked")
            case 403:
                throw KopifundError(kind: .notOnLink, message: "the link does not serve this route")
            case 422:
                throw KopifundError(kind: .conflict, message: "the pair token was already spent or expired")
            default:
                throw KopifundError(kind: .http(http.statusCode), message: "HTTP \(http.statusCode)")
            }
        }
    }
}
