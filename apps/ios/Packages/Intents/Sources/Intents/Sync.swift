import Foundation
import Storage
import SwiftData
import YNABClient

/// G24: the sync engine that posts approved rows to YNAB. It lives here, not
/// in the app target, so the tests can drive it with a mock client and an
/// in-memory store. The app's `SyncService` owns when it runs; this owns what
/// one run does to the rows.
///
/// A row moves approved -> syncing -> synced, or to failed with the error
/// kept on the row. A failed row waits before an automatic retry, longer
/// after each attempt; a manual "Sync now" (`force`) retries it at once.

public struct SyncSettings: Sendable {
    public var planID: String
    /// Used for a row whose pattern mapped no account.
    public var defaultAccountID: String
    public var postApproved: Bool

    public init(planID: String, defaultAccountID: String, postApproved: Bool) {
        self.planID = planID
        self.defaultAccountID = defaultAccountID
        self.postApproved = postApproved
    }
}

/// Why a run stopped before the queue was empty. The first two are about
/// the token, not about one row, so no further row is sent.
public enum SyncStop: Equatable, Sendable {
    case unauthorized
    case rateLimited
    case cancelled
}

public struct SyncOutcome: Equatable, Sendable {
    public var posted = 0
    public var failed = 0
    public var stop: SyncStop?
    public init() {}
}

/// When a failed row may be retried without the user asking.
public enum SyncBackoff {
    public static let base: TimeInterval = 60
    public static let cap: TimeInterval = 6 * 3600
    /// After this many sends a failed row waits for "Sync now".
    public static let maxAutomaticAttempts = 8

    /// One minute after the first failed send, doubling to at most 6 hours.
    public static func delay(afterAttempts n: Int) -> TimeInterval {
        min(cap, base * pow(2, Double(max(0, n - 1))))
    }
}

/// Remembers a stop across runs, so a rejected token or a rate limit does not
/// cost a request on every launch, tab switch or approval. In memory only: a
/// relaunch tries once more.
public struct SyncGate: Equatable, Sendable {
    public private(set) var rejectedToken: String?
    public private(set) var pausedUntil: Date?
    /// How long a 429 pauses every send. YNAB allows 200 requests in a
    /// rolling hour.
    public static let ratePause: TimeInterval = 15 * 60

    public init() {}

    public func allows(token: String, now: Date, force: Bool) -> Bool {
        if force { return true }
        if let rejectedToken, rejectedToken == token { return false }
        if let pausedUntil, now < pausedUntil { return false }
        return true
    }

    public mutating func record(_ outcome: SyncOutcome, token: String, now: Date) {
        switch outcome.stop {
        case .unauthorized:
            rejectedToken = token
        case .rateLimited:
            pausedUntil = now.addingTimeInterval(Self.ratePause)
        case .cancelled:
            break
        case nil:
            rejectedToken = nil
            pausedUntil = nil
        }
    }
}

@MainActor
public enum Sync {
    /// Post every due row, one at a time, until none is left. The queue is
    /// fetched again before each row: the fetch refreshes rows another
    /// process changed on disk (an intent that parked a row as a duplicate
    /// after this run began) and picks up rows approved while a send was in
    /// flight.
    ///
    /// `beforeSend` runs on each row just before its YNAB post and may take
    /// the row out of the queue by changing its state (G25: the app posts it
    /// to the linked record first and parks it as "kopifund" when a loaded
    /// statement already covers it). It runs here, per row, so a row
    /// approved mid-run gets the same checks as the rest.
    public static func run(context: ModelContext, client: YNABClient,
                           settings: SyncSettings, force: Bool = false,
                           now: () -> Date = Date.init,
                           beforeSend: (@MainActor (TransactionRecord) async -> Void)? = nil)
        async -> SyncOutcome {
        var outcome = SyncOutcome()
        var tried = Set<UUID>()
        guard !settings.planID.isEmpty else { return outcome }
        while true {
            if Task.isCancelled { outcome.stop = .cancelled; break }
            let queue = (try? context.fetch(FetchDescriptor<TransactionRecord>(
                predicate: #Predicate {
                    $0.state == "approved" || $0.state == "failed" || $0.state == "syncing" },
                sortBy: [SortDescriptor(\.receivedAt)]))) ?? []
            guard let row = queue.first(where: {
                !tried.contains($0.id) && isDue($0, now: now(), force: force)
            }) else { break }
            tried.insert(row.id)
            if let beforeSend {
                await beforeSend(row)
                if row.isDeleted || row.modelContext == nil { continue }
                guard ["approved", "failed", "syncing"].contains(row.state) else { continue }
            }
            if let stop = await send(row, context: context, client: client,
                                     settings: settings, now: now, outcome: &outcome) {
                outcome.stop = stop
                break
            }
        }
        return outcome
    }

    /// Approved rows and rows a killed app left `syncing` always go. A failed
    /// row goes once its backoff has passed and it has attempts left, or
    /// whenever the user forces a sync.
    public static func isDue(_ row: TransactionRecord, now: Date, force: Bool) -> Bool {
        switch row.state {
        case "approved", "syncing":
            return true
        case "failed":
            if force { return true }
            return row.syncAttempts < SyncBackoff.maxAutomaticAttempts
                && (row.nextSyncAt ?? .distantPast) <= now
        default:
            return false
        }
    }

    /// The YNAB `date` is the calendar day the alert printed, as yyyy-MM-dd.
    /// `Dates.parse` stores that day as midnight in the phone's zone, so it is
    /// read back in the phone's zone. If the stored form changes, this is the
    /// one place to change with it.
    public static func ynabDate(_ date: Date) -> String {
        let c = Calendar(identifier: .gregorian).dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// Send one row. Returns a stop when the run must end here.
    private static func send(_ row: TransactionRecord, context: ModelContext,
                             client: YNABClient, settings: SyncSettings,
                             now: () -> Date, outcome: inout SyncOutcome) async -> SyncStop? {
        let accountID = row.accountID.isEmpty ? settings.defaultAccountID : row.accountID
        guard !accountID.isEmpty else {
            // no request was made, so no backoff: the next run tries again,
            // and succeeds once Settings has a default account
            row.state = "failed"
            row.syncError = "no YNAB account mapped"
            row.nextSyncAt = nil
            try? context.save()
            outcome.failed += 1
            return nil
        }
        let prior = row.state
        let importID = row.importID ?? ImportID.make(rowID: row.id)
        // id, state and attempt count are saved before the POST, so a killed
        // app or a lost reply retries with the same id
        row.importID = importID
        row.state = "syncing"
        row.syncAttempts += 1
        try? context.save()
        let milli = Milliunits.from(Decimal(row.amountCents) / 100,
                                    outflow: row.direction == "outflow")
        let txn = NewTransaction(accountID: accountID, date: ynabDate(row.date),
                                 amount: milli, payeeName: row.payee,
                                 categoryID: row.categoryID,
                                 memo: row.memo.isEmpty ? nil : row.memo,
                                 approved: settings.postApproved, importID: importID)
        let result: Result<String, Error>
        do {
            result = .success(try await client.createTransaction(planID: settings.planID, txn))
        } catch {
            result = .failure(error)
        }
        // the row may have been deleted while the request was out; writing
        // to it now would trap
        if row.isDeleted || row.modelContext == nil { return nil }
        switch result {
        case .success(let ynabID):
            row.ynabID = ynabID
            markSynced(row)
            outcome.posted += 1
        case .failure(let error):
            if Task.isCancelled {
                // the app stopped the run, not YNAB: the row waits as it was.
                // If the POST landed, the kept import_id turns the retry into
                // a 409, which reads as synced.
                row.state = prior
                try? context.save()
                return .cancelled
            }
            let kind = (error as? YNABError)?.kind ?? .network
            switch kind {
            case .conflict:
                // the id is this row's own, so YNAB already holds an earlier
                // send of this row
                markSynced(row)
                outcome.posted += 1
            case .unauthorized, .rateLimited:
                // YNAB refused the request, so nothing was sent and the row
                // is not at fault; every later row would be refused too
                row.state = prior
                row.syncAttempts -= 1
                try? context.save()
                return kind == .unauthorized ? .unauthorized : .rateLimited
            case .notFound, .http(400..<500):
                // the request itself is wrong (an unknown account or
                // category): the same send would fail every time
                markFailed(row, (error as? YNABError)?.message ?? "\(error)",
                           retryAt: .distantFuture)
                outcome.failed += 1
            default:
                markFailed(row, (error as? YNABError)?.message ?? error.localizedDescription,
                           retryAt: now().addingTimeInterval(
                               SyncBackoff.delay(afterAttempts: row.syncAttempts)))
                outcome.failed += 1
            }
        }
        try? context.save()
        return nil
    }

    private static func markSynced(_ row: TransactionRecord) {
        row.state = "synced"
        row.syncError = nil
        row.nextSyncAt = nil
    }

    private static func markFailed(_ row: TransactionRecord, _ message: String, retryAt: Date) {
        row.state = "failed"
        row.syncError = message
        row.nextSyncAt = retryAt
    }
}
