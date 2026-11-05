import Foundation
import os
import SwiftData
import Storage
import Matching
import Intents
import KopifundClient
import YNABClient

/// Decides when the queue syncs; `Sync.run` (Intents package) decides what
/// one YNAB run does to the rows, and `HubLane` below posts each row to a
/// linked Kopifund record first (G25).
///
/// The app owns every run, not a view: a run lives in a task this object
/// holds, so switching tabs or popping a screen never cancels a send half
/// way and marks rows failed. Runs serialize. A call that arrives while one
/// is going asks for another pass once it ends, so a row approved mid-run is
/// never left waiting for the next launch.
@MainActor
@Observable
final class SyncService {
    static let shared = SyncService()

    /// What the Inbox banner says when YNAB refused the last run.
    enum Problem: Equatable {
        case unauthorized
        case rateLimited(until: Date)
    }
    private(set) var problem: Problem?

    @ObservationIgnored private var running: Task<Void, Never>?
    @ObservationIgnored private var again = false
    @ObservationIgnored private var againForced = false
    @ObservationIgnored private var gate = SyncGate()

    /// Start a run, or ask the running one for another pass, and wait for
    /// it. `force` is the user's "Sync now": it skips the backoff and a
    /// remembered token or rate-limit stop.
    static func flush(context: ModelContext, force: Bool = false) async {
        await shared.request(context: context, force: force)
    }

    private func request(context: ModelContext, force: Bool) async {
        if let running {
            again = true
            againForced = againForced || force
            await running.value
            return
        }
        let task = Task { await self.loop(context: context, force: force) }
        running = task
        await task.value
    }

    private func loop(context: ModelContext, force: Bool) async {
        var force = force
        repeat {
            again = false
            await runOnce(context: context, force: force)
            force = againForced
            againForced = false
        } while again
        running = nil
    }

    private func runOnce(context: ModelContext, force: Bool) async {
        let hub = await HubLane.start(context: context)
        // the hub owes no YNAB setup: rows a linked phone still owes the
        // record go first, whatever their YNAB state
        if let hub { await hub.sendOwed(context: context) }

        // no YNAB configured: the pending post above still corroborates the
        // row on the hub; the spend itself stays parked here
        guard let token = KeychainToken.value, !token.isEmpty else { return }
        let defaults = UserDefaults.standard
        let settings = SyncSettings(
            planID: defaults.string(forKey: "planID") ?? "",
            defaultAccountID: defaults.string(forKey: "defaultAccountID") ?? "",
            postApproved: defaults.bool(forKey: "postApproved"))
        guard !settings.planID.isEmpty,
              gate.allows(token: token, now: Date(), force: force) else { return }
        let outcome = await Sync.run(
            context: context,
            // no quiet retries: a 429 must reach the gate after one request,
            // not after four a second apart
            client: YNABClient(token: token, retries: 0),
            settings: settings, force: force,
            beforeSend: { row in await hub?.prepare(row, context: context) })
        gate.record(outcome, token: token, now: Date())
        switch outcome.stop {
        case .unauthorized: problem = .unauthorized
        case .rateLimited: problem = gate.pausedUntil.map { .rateLimited(until: $0) }
        case .cancelled: break
        case nil: problem = nil
        }
    }

    /// Bumped when the store changed under the app while it was in the
    /// background. RootView keys the list tabs on it, so their queries run
    /// again and show what the other process wrote.
    private(set) var storeGeneration = 0
    @ObservationIgnored private var fingerprintAtBackground: Int?

    /// Called when the app leaves the front (inactive, then background):
    /// what the store held then. The first call wins, so a write that lands
    /// between the two phases still counts as a change.
    static func noteBackground(_ context: ModelContext) {
        guard shared.fingerprintAtBackground == nil else { return }
        shared.fingerprintAtBackground = fingerprint(context)
    }

    /// Re-read the queue from disk. The share extension and an intent run in
    /// another process write the same file, and their saves never reach this
    /// process's contexts by themselves. A fetch refreshes every row it
    /// returns that has no unsaved edit here, but it neither redraws an open
    /// list nor re-runs its query (both checked on the simulator), so a
    /// change since the app left the front also bumps `storeGeneration`.
    /// Called when the app comes to the front and before a bulk approve, so
    /// a row an intent parked as a duplicate is not approved from a stale
    /// copy.
    static func refresh(_ context: ModelContext) {
        let now = fingerprint(context)
        if let before = shared.fingerprintAtBackground, before != now {
            // a new identity rebuilds the tab and its navigation, so an open
            // pattern draft would be lost: the re-key waits until it closes
            if drafts.withLock({ $0 }) > 0 {
                shared.rebuildOwed = true
            } else {
                shared.storeGeneration += 1
            }
        }
        shared.fingerprintAtBackground = nil
    }

    @ObservationIgnored private var rebuildOwed = false
    nonisolated static let drafts = OSAllocatedUnfairLock(initialState: 0)

    /// Held by a pattern draft (the builder's state) for as long as it
    /// lives, through tab switches and trips to the background.
    final class DraftHold: Sendable {
        init() { SyncService.drafts.withLock { $0 += 1 } }
        deinit {
            let last = SyncService.drafts.withLock { n -> Bool in
                n = max(0, n - 1)
                return n == 0
            }
            if last { Task { @MainActor in SyncService.shared.applyOwedRebuild() } }
        }
    }

    private func applyOwedRebuild() {
        guard rebuildOwed, Self.drafts.withLock({ $0 }) == 0 else { return }
        rebuildOwed = false
        storeGeneration += 1
    }

    /// Every row's id and state, and every unmatched alert's id. Fetching
    /// them is also what refreshes the in-memory rows from disk.
    private static func fingerprint(_ context: ModelContext) -> Int {
        var h = Hasher()
        let rows = (try? context.fetch(FetchDescriptor<TransactionRecord>(
            sortBy: [SortDescriptor(\.receivedAt)]))) ?? []
        for row in rows { h.combine(row.id); h.combine(row.state) }
        let unmatched = (try? context.fetch(FetchDescriptor<UnmatchedRecord>(
            sortBy: [SortDescriptor(\.receivedAt)]))) ?? []
        for u in unmatched { h.combine(u.id) }
        return h.finalize()
    }
}

/// G25: the linked phone's half of a run. Each row lands on the hub as a
/// pending_txn a later statement load confirms, and an approved row a loaded
/// statement already covers moves to "kopifund" so it never reaches YNAB.
@MainActor
final class HubLane {
    private let link: KopifundClient
    /// The record's spends for `months`, fetched as rows need them.
    private var record: [Dedup.RecordSpend] = []
    private var months: Set<String> = []
    private var taken: Set<Int> = []
    private var done: Set<UUID> = []

    private static let dateFmt: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private init(link: KopifundClient) {
        self.link = link
    }

    /// nil when the phone is not linked.
    static func start(context: ModelContext) async -> HubLane? {
        guard let link = LinkStore.live.client() else { return nil }
        return HubLane(link: link)
    }

    /// Load the record's month for `date` once per run. A month is fetched
    /// when an approved row first needs it, so a row approved mid-run from
    /// a month no other row touched is still checked against the record.
    private func loadMonth(of date: Date) async {
        let month = LinkDedupe.monthKey(date)
        guard months.insert(month).inserted else { return }
        // appended, so the indexes already in `taken` stay valid
        record += await LinkDedupe.recordSpends(client: link, months: [month])
    }

    /// kopifundError/kopifundID widen the net past YNAB's own states: a
    /// synced row whose hub post failed or never got its reply is fetched
    /// for the hub send alone.
    private static func owedRows(_ context: ModelContext) -> [TransactionRecord] {
        (try? context.fetch(FetchDescriptor<TransactionRecord>(predicate: #Predicate {
            $0.state == "approved" || $0.state == "failed" || $0.state == "syncing"
                || $0.kopifundError != nil || $0.kopifundID != nil }))) ?? []
    }

    /// Every row the hub is owed, before the YNAB run starts. The candidates
    /// are gathered once, and each is fetched again just before its turn:
    /// the fetch refreshes a row an intent changed on disk while an earlier
    /// send was out (a Wallet capture parked as a duplicate must not post).
    /// A row approved after this point reaches `prepare` through the YNAB
    /// run's `beforeSend`.
    func sendOwed(context: ModelContext) async {
        for id in Self.owedRows(context).map(\.id) where !done.contains(id) {
            guard let row = (try? context.fetch(FetchDescriptor<TransactionRecord>(
                predicate: #Predicate { $0.id == id })))?.first else { continue }
            await prepare(row, context: context)
        }
    }

    /// Post `row` to the hub when it owes a send, then park it as "kopifund"
    /// when the record covers it. Once per row per run.
    func prepare(_ row: TransactionRecord, context: ModelContext) async {
        guard done.insert(row.id).inserted else { return }
        // A first send is owed only by rows in the YNAB lane (a sibling
        // parked as a duplicate meanwhile must never post), while a retry is
        // owed whenever what the hub last accepted (kopifundSent) differs
        // from the row as it reads now: a refusal, an interrupted send whose
        // id persisted but whose reply never came, or an edit since. The
        // import_id is the row's own, so every repost upserts the one
        // pending row, and a row can owe a retry in any state.
        let day = Self.dateFmt.string(from: row.date)
        let signed = row.direction == "outflow" ? -row.amountCents : row.amountCents
        let sent = "\(day)|\(signed)|\(row.currency)|\(row.payee)"
        let inLane = row.state == "approved" || row.state == "failed" || row.state == "syncing"
        let owesHub = row.kopifundError != nil
            || (row.kopifundID == nil ? inLane : row.kopifundSent != sent)
        if owesHub {
            let pendingID = row.kopifundID ?? "kph_\(row.id.uuidString.lowercased())"
            if row.kopifundID == nil { row.kopifundID = pendingID }
            let post = PendingPost(importID: pendingID, date: day, amountMinor: signed,
                                   currency: row.currency, payee: row.payee)
            do {
                _ = try await link.postPending(post)
                if row.isDeleted || row.modelContext == nil { return }
                row.kopifundSent = sent
                row.kopifundError = nil
            } catch {
                if row.isDeleted || row.modelContext == nil { return }
                row.kopifundError = (error as? KopifundError)?.message ?? error.localizedDescription
            }
            try? context.save()
        }
        guard row.state == "approved" else { return }
        // a linked phone asks the record first: a spend a loaded statement
        // already covers is marked "kopifund", never posted to YNAB
        await loadMonth(of: row.date)
        if row.isDeleted || row.modelContext == nil || row.state != "approved" { return }
        if let i = Dedup.coveredBy(amountCents: row.amountCents, currency: row.currency,
                                   direction: row.direction, date: row.date,
                                   payee: row.payee, in: record, taken: taken) {
            taken.insert(i)
            row.state = "kopifund"
            try? context.save()
        }
    }
}
