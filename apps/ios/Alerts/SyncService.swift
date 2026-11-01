import Foundation
import SwiftData
import Storage
import Matching
import KopifundClient
import YNABClient

/// Flush approved rows to YNAB. Each row moves pending -> approved -> syncing
/// -> synced, or failed with the error kept on the row for a retry.
@MainActor
enum SyncService {
    private static let dateFmt: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private static var flushing = false

    /// Flushes serialize: a second caller returns rather than racing the
    /// first over the same rows. Rows left `syncing` by a killed app are
    /// picked up again, safe because their `import_id` persisted before the
    /// first send.
    @MainActor
    static func flush(context: ModelContext) async {
        guard !flushing else { return }
        flushing = true
        defer { flushing = false }
        // kopifundError/kopifundID widen the net past YNAB's own states: a
        // synced row whose hub post failed or never got its reply is fetched
        // for the hub send alone, and never reaches the YNAB section below
        let approved = (try? context.fetch(
            FetchDescriptor<TransactionRecord>(predicate: #Predicate {
                $0.state == "approved" || $0.state == "failed" || $0.state == "syncing"
                    || $0.kopifundError != nil || $0.kopifundID != nil }))) ?? []
        guard !approved.isEmpty else { return }
        let link = LinkStore.live.client()
        // YNAB may not be set up on this phone at all: the Kopifund pending
        // post and the statement dedupe need only the link, so they run
        // either way and the YNAB post below skips when unconfigured
        let token = KeychainToken.value ?? ""
        let planID = UserDefaults.standard.string(forKey: "planID") ?? ""
        let ynab = (!token.isEmpty && !planID.isEmpty) ? YNABClient(token: token) : nil
        // G25: a linked phone asks the record first — a spend a loaded
        // statement already covers is marked "kopifund", never posted
        var record: [Dedup.RecordSpend] = []
        var taken: Set<Int> = []
        if let link {
            record = await LinkDedupe.recordSpends(
                client: link, months: Set(approved.map { LinkDedupe.monthKey($0.date) }))
        }
        let postApproved = UserDefaults.standard.bool(forKey: "postApproved")
        // a store view on its own context: an App Intent's dedup write lands
        // on the same file but never merges into this context's copies, so a
        // row parked since the batch was fetched must be re-read before post
        let storeView = try? ModelContext(Store.container())
        for row in approved {
            let rowID = row.id
            if let storeView,
               let live = (try? storeView.fetch(FetchDescriptor<TransactionRecord>(
                   predicate: #Predicate { $0.id == rowID })))?.first,
               live.state != row.state {
                row.state = live.state
                row.duplicateOf = live.duplicateOf
                try? context.save()
            }
            // G25: the same row lands on the hub as a pending_txn a later
            // statement load confirms. A first send is owed only by rows in
            // the YNAB lane — a sibling parked as a duplicate meanwhile must
            // never post — while a retry is owed whenever what the hub last
            // accepted (kopifundSent) differs from the row as it reads now:
            // a refusal, an interrupted send whose id persisted but whose
            // reply never came, or an edit since. The import_id is the row's
            // own, so every repost upserts the one pending row, and a row can
            // owe a retry in any state, so this runs before the YNAB gate.
            let sent = "\(dateFmt.string(from: row.date))|" +
                "\(row.direction == "outflow" ? -row.amountCents : row.amountCents)|" +
                "\(row.currency)|\(row.payee)"
            let inLane = row.state == "approved" || row.state == "failed" || row.state == "syncing"
            let owesHub = row.kopifundError != nil
                || (row.kopifundID == nil ? inLane : row.kopifundSent != sent)
            if let link, owesHub {
                let pendingID = row.kopifundID ?? "kph_\(row.id.uuidString.lowercased())"
                if row.kopifundID == nil { row.kopifundID = pendingID }
                let post = PendingPost(
                    importID: pendingID, date: dateFmt.string(from: row.date),
                    amountMinor: row.direction == "outflow" ? -row.amountCents : row.amountCents,
                    currency: row.currency, payee: row.payee)
                do {
                    _ = try await link.postPending(post)
                    row.kopifundSent = sent
                    row.kopifundError = nil
                } catch {
                    row.kopifundError = (error as? KopifundError)?.message ?? error.localizedDescription
                }
                try? context.save()
            }
            guard row.state == "approved" || row.state == "failed" || row.state == "syncing"
            else { continue }
            if row.state == "approved",
               let i = Dedup.coveredBy(amountCents: row.amountCents, currency: row.currency,
                                       direction: row.direction, date: row.date,
                                       payee: row.payee, in: record, taken: taken) {
                taken.insert(i)
                row.state = "kopifund"
                try? context.save()
                continue
            }
            // no YNAB configured: the pending post above still corroborates
            // the row on the hub; the spend itself stays parked here
            guard let ynab else { continue }
            let accountID = row.accountID.isEmpty
                ? (UserDefaults.standard.string(forKey: "defaultAccountID") ?? "")
                : row.accountID
            guard !accountID.isEmpty else {
                row.state = "failed"; row.syncError = "no YNAB account mapped"
                continue
            }
            row.state = "syncing"
            try? context.save()
            let outflow = row.direction == "outflow"
            let milli = Milliunits.from(Decimal(row.amountCents) / 100, outflow: outflow)
            // the first send's import_id is saved before posting, so an edit
            // or a lost reply can never mint a second one for the same row
            let importID = row.importID ?? ImportID.make(date: row.date, milliunits: milli,
                                                         payee: row.payee, source: row.source)
            row.importID = importID
            let txn = NewTransaction(accountID: accountID,
                                     date: dateFmt.string(from: row.date),
                                     amount: milli, payeeName: row.payee,
                                     categoryID: row.categoryID,
                                     memo: row.memo.isEmpty ? nil : row.memo,
                                     approved: postApproved, importID: importID)
            do {
                row.ynabID = try await ynab.createTransaction(planID: planID, txn)
                row.state = "synced"
                row.syncError = nil
            } catch let e as YNABError {
                if e.kind == .conflict {   // import_id already taken: synced before
                    row.state = "synced"
                } else {
                    row.state = "failed"
                    row.syncError = e.message
                }
            } catch {
                row.state = "failed"
                row.syncError = error.localizedDescription
            }
            row.syncAttempts += 1
            try? context.save()
        }
    }
}
