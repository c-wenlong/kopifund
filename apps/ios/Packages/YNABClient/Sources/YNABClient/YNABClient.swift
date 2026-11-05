import Foundation

/// G24: the one host the app ever calls. Everything hangs off `/v1`.
public enum YNAB {
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
    /// For `.rateLimited`: how long YNAB's `Retry-After` asked the caller to
    /// wait, in seconds, when it sent one.
    public var retryAfter: TimeInterval? = nil
}

/// When YNAB next accepts a request after a 429 asked for a long wait.
/// Shared by every client in the process (each sync makes its own), so the
/// rest of a sync, and the next one, send nothing until the wait is over
/// instead of burning more of the hour's quota.
public final class RateGate: @unchecked Sendable {
    public static let shared = RateGate()
    private let lock = NSLock()
    private var notBefore: Date?

    public init() {}

    /// Seconds still to wait, or nil when requests may go.
    func remaining(now: Date = Date()) -> TimeInterval? {
        lock.withLock {
            guard let notBefore, notBefore > now else { return nil }
            return notBefore.timeIntervalSince(now)
        }
    }

    func close(for seconds: TimeInterval, now: Date = Date()) {
        // overlapping requests can report different waits: keep the later
        lock.withLock {
            let deadline = now.addingTimeInterval(seconds)
            notBefore = max(notBefore ?? deadline, deadline)
        }
    }
}

public actor YNABClient {
    private let token: String
    private let session: URLSession
    /// Retries a 429 or a network drop this many times, waiting `retryDelay`
    /// between tries, or as long as a 429's `Retry-After` asks. A wait past
    /// `maxRetryAfter` seconds is not slept through: the call throws
    /// `.rateLimited` carrying it, so a sync stops instead of hanging.
    private let retries: Int
    private let retryDelay: UInt64
    private let maxRetryAfter: TimeInterval
    /// Waits between tries; injected in tests to read the requested waits.
    /// Throws `CancellationError` when the calling task is cancelled.
    private let sleep: @Sendable (UInt64) async throws -> Void
    private let gate: RateGate

    public init(token: String, session: URLSession = .shared, retries: Int = 3,
                retryDelay: UInt64 = 1_000_000_000, maxRetryAfter: TimeInterval = 30,
                gate: RateGate = .shared,
                sleep: @escaping @Sendable (UInt64) async throws -> Void = {
                    try await Task.sleep(nanoseconds: $0)
                }) {
        self.gate = gate
        self.token = token
        self.session = session
        self.retries = retries
        self.retryDelay = retryDelay
        self.maxRetryAfter = maxRetryAfter
        self.sleep = sleep
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

    /// Seconds a 429's `Retry-After` asks for: delta-seconds ("120") or an
    /// HTTP date, never negative; nil when the header is absent or unreadable.
    static func retryAfter(_ http: HTTPURLResponse, now: Date = Date()) -> TimeInterval? {
        guard let raw = http.value(forHTTPHeaderField: "Retry-After")?
            .trimmingCharacters(in: .whitespaces), !raw.isEmpty else { return nil }
        if let seconds = TimeInterval(raw) { return seconds.isFinite && seconds >= 0 ? seconds : nil }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "GMT")
        f.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        guard let date = f.date(from: raw) else { return nil }
        return max(0, date.timeIntervalSince(now))
    }

    /// "rate limited: YNAB asks to wait 3600 s", formatted without an
    /// integer conversion that a huge header could overflow.
    static func waitMessage(_ seconds: TimeInterval) -> String {
        "rate limited: YNAB asks to wait \(String(format: "%.0f", seconds.rounded(.up))) s"
    }

    private func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        try await request(path, method: "GET", body: nil)
    }

    private func request<T: Decodable>(_ path: String, method: String, body: Data?) async throws -> T {
        // a long wait YNAB asked for earlier still holds: send nothing
        if let left = gate.remaining() {
            throw YNABError(kind: .rateLimited, message: Self.waitMessage(left), retryAfter: left)
        }
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
                // a cancelled sync stops here rather than retrying as if
                // the network had dropped
                if Task.isCancelled { throw CancellationError() }
                attempt += 1
                if attempt <= retries {
                    try await sleep(retryDelay)
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
                let asked = Self.retryAfter(http)
                if attempt <= retries, (asked ?? 0) <= maxRetryAfter {
                    try await sleep(asked.map { UInt64($0 * 1_000_000_000) } ?? retryDelay)
                    continue
                }
                if let asked, asked > maxRetryAfter { gate.close(for: asked) }
                throw YNABError(kind: .rateLimited,
                                message: asked.map(Self.waitMessage) ?? "rate limited",
                                retryAfter: asked)
            default:
                throw YNABError(kind: .http(http.statusCode), message: "HTTP \(http.statusCode)")
            }
        }
    }
}

// MARK: - milliunits and import_id

/// YNAB counts money in milliunits: $12.34 is 12340, and outflows are
/// negative. The app keeps integer cents (Storage's `Cents` makes them), and
/// this is the one conversion to the wire.
public enum Milliunits {
    /// `cents` as signed milliunits, nil when the result would not fit an
    /// Int (a stored row never gets near; the check keeps it from wrapping).
    public static func from(cents: Int, outflow: Bool) -> Int? {
        let (milli, overflow) = cents.multipliedReportingOverflow(by: 10)
        guard !overflow else { return nil }
        return outflow ? -milli : milli
    }
}

/// A deterministic `import_id` so a retried post never writes two rows. YNAB
/// caps it at 36 characters; the hash keeps the id stable for the same alert.
public enum ImportID {
    /// `Alerts` prefix + a hash of date, milliunit amount, payee and source,
    /// 32 hex chars: `al-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` is 35. `capturedAt`,
    /// when given, is the moment the row was captured, so two real purchases
    /// of one amount at one merchant on one day get different ids; the id is
    /// saved before the first send, so a retry still reuses it.
    public static func make(date: Date, milliunits: Int, payee: String, source: String?,
                            capturedAt: Date? = nil) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let c = cal.dateComponents([.year, .month, .day], from: date)
        let day = String(format: "%04d%02d%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
        var key = "\(day)|\(milliunits)|\(payee.lowercased().trimmingCharacters(in: .whitespaces))|\(source ?? "")"
        if let capturedAt { key += "|\(Int(capturedAt.timeIntervalSince1970.rounded(.down)))" }
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
