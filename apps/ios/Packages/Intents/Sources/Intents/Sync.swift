import Foundation
import Matching
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
    /// The plan's ISO currency, nil until a sync has read it. A row posts
    /// only in this currency (`TransactionRecord.checkCurrency`).
    public var planCurrency: String?

    public init(planID: String, defaultAccountID: String, postApproved: Bool,
                planCurrency: String? = nil) {
        self.planID = planID
        self.defaultAccountID = defaultAccountID
        self.postApproved = postApproved
        self.planCurrency = planCurrency
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
            // what an intake saved since this context last read the row: a
            // park, a hold, or a currency a pairing moved onto it
            reread(row, in: context)
            guard ["approved", "failed", "syncing"].contains(row.state) else { continue }
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

    /// Copy onto `row` what another context or process saved for it since
    /// this context read it. A long-lived context can hand back its cached
    /// copy, so the read takes a fresh context on the same container, which
    /// goes to the store. A state moves with its duplicate link and reason,
    /// and a currency with its amount: a conversion saved elsewhere sets
    /// both, and the currency check must never pass the plan's code on the
    /// foreign figure.
    public static func reread(_ row: TransactionRecord, in context: ModelContext) {
        let id = row.id
        guard let live = (try? ModelContext(context.container).fetch(
            FetchDescriptor<TransactionRecord>(predicate: #Predicate { $0.id == id })))?.first
        else { return }
        var changed = false
        if live.state != row.state {
            row.state = live.state
            row.duplicateOf = live.duplicateOf
            row.syncError = live.syncError
            changed = true
        }
        if live.currency != row.currency || live.amountCents != row.amountCents {
            row.currency = live.currency
            row.amountCents = live.amountCents
            changed = true
        }
        if changed { try? context.save() }
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
        // a row posts only in the plan's own currency: a foreign one goes
        // back to review with the reason, and a stated one waits while the
        // plan's is unknown. No request was made, so no attempt counts.
        guard row.checkCurrency(against: settings.planCurrency) else {
            try? context.save()
            return nil
        }
        let prior = row.state
        let milli = Milliunits.from(Decimal(row.amountCents) / 100,
                                    outflow: row.direction == "outflow")
        // the calendar day the row printed, read now, so a date corrected
        // on the detail screen is the one sent; no zone is applied
        let day = row.day
        // A row an earlier build wrote keeps that build's hash input, its
        // raw date, so a send it began and never recorded gets the same
        // import_id on retry and YNAB refuses the duplicate. A new row adds
        // the moment it was captured, so two real purchases of one amount at
        // one merchant on one day never share an id.
        let importID = row.importID ?? (row.dateIsDay == true
            ? ImportID.make(date: day.storedInstant, milliunits: milli, payee: row.payee,
                            source: row.source, capturedAt: row.receivedAt)
            : ImportID.make(date: row.date, milliunits: milli, payee: row.payee,
                            source: row.source))
        // id, state and attempt count are saved before the POST, so a killed
        // app or a lost reply retries with the same id
        row.importID = importID
        row.state = "syncing"
        row.syncAttempts += 1
        try? context.save()
        let txn = NewTransaction(accountID: accountID, date: day.iso,
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
