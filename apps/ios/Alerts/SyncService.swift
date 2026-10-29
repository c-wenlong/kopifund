import Foundation
import SwiftData
import Storage
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
        guard let token = KeychainToken.value, !token.isEmpty else { return }
        let planID = UserDefaults.standard.string(forKey: "planID") ?? ""
        guard !planID.isEmpty else { return }
        let approved = (try? context.fetch(
            FetchDescriptor<TransactionRecord>(predicate: #Predicate {
                $0.state == "approved" || $0.state == "failed" || $0.state == "syncing" }))) ?? []
        guard !approved.isEmpty else { return }
        let client = YNABClient(token: token)
        // G24 audit: a row posts only in the plan's own currency. Read it
        // fresh each flush; fall back to this plan's last reading when YNAB
        // can't be reached, and with neither, rows that state a currency wait
        var planCurrency = PlanCurrency.saved(planID: planID)
        if let code = try? await client.planCurrency(planID: planID) {
            PlanCurrency.remember(code, planID: planID)
            planCurrency = code
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
            guard row.state == "approved" || row.state == "failed" || row.state == "syncing"
            else { continue }
            let accountID = row.accountID.isEmpty
                ? (UserDefaults.standard.string(forKey: "defaultAccountID") ?? "")
                : row.accountID
            guard !accountID.isEmpty else {
                row.state = "failed"; row.syncError = "no YNAB account mapped"
                continue
            }
            guard row.checkCurrency(against: planCurrency) else {
                try? context.save()
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
                row.ynabID = try await client.createTransaction(planID: planID, txn)
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
