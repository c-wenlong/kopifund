import Foundation
import os
import SwiftData

/// G24: a row that says no amount or names no payee is not a spend anyone
/// can post. A Wallet tap can reach the intent with neither (the trigger
/// fired before the card's issuer sent the transaction's details, or for a
/// tap that was no purchase, such as a card check), and approving such a row
/// would post a 0.00 transaction to YNAB and the hub. The row is kept, so a
/// real purchase whose details did not come through is never lost, but it
/// waits under "Needs details" until the owner types what is missing, and
/// neither "Approve all" nor a sync ever sends it.
extension TransactionRecord {
    /// Why the row cannot be approved or posted yet, worded for the Inbox
    /// and the detail screen; nil when it can.
    public var missingDetails: String? {
        let noPayee = payee.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        switch (amountCents == 0, noPayee) {
        case (true, true): return "No amount or payee came through. Type both, or delete it."
        case (true, false): return "The amount is 0.00. Type what it cost, or delete it."
        case (false, true): return "No payee came through. Type who was paid."
        case (false, false): return nil
        }
    }

    /// The short form for a list row's note.
    public var missingDetailsNote: String? {
        let noPayee = payee.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        switch (amountCents == 0, noPayee) {
        case (true, true): return "no amount or payee"
        case (true, false): return "no amount"
        case (false, true): return "no payee"
        case (false, false): return nil
        }
    }
}

/// G24: removes, once, the empty Wallet captures builds before the
/// missing-details check queued as approvable rows (amount 0, blank payee).
/// Only a row that is certainly one of those goes: from the Wallet intent,
/// with no alert text, never sent to YNAB or the hub, and not the row
/// another one is parked against. Anything else stays where it is.
public enum EmptyWalletCleanup {
    /// Set in the app's defaults once a sweep has saved.
    public static let doneKey = "G24.emptyWalletCleanupDone"

    private static let log = Logger(subsystem: "com.kopifund.alerts", category: "cleanup")

    /// True only for a capture the old intent wrote with nothing in it.
    public static func isEmptyCapture(_ r: TransactionRecord) -> Bool {
        r.source == "wallet"
            && r.amountCents == 0
            && r.payee.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && r.alertText.isEmpty
            && ["pending", "approved", "failed", "duplicate"].contains(r.state)
            && r.ynabID == nil && r.importID == nil && r.syncAttempts == 0
            && r.kopifundID == nil
    }

    /// Delete the empty captures unless a sweep already ran, then remember
    /// that it did. Returns how many rows went, or nil when the sweep had
    /// already run. A failed save leaves the flag unset so the next launch
    /// tries again.
    @discardableResult
    public static func runOnce(in context: ModelContext, defaults: UserDefaults) throws -> Int? {
        guard !defaults.bool(forKey: doneKey) else { return nil }
        let rows = try context.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.source == "wallet" && $0.amountCents == 0 }))
        // a row another one points at as its kept copy stays, so no
        // duplicate is left pointing at nothing
        let referenced = Set(try context.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.duplicateOf != nil })).compactMap(\.duplicateOf))
        let doomed = rows.filter { isEmptyCapture($0) && !referenced.contains($0.id) }
        for row in doomed { context.delete(row) }
        try context.save()
        defaults.set(true, forKey: doneKey)
        log.notice("removed \(doomed.count, privacy: .public) empty Wallet captures (amount 0, no payee, never sent)")
        return doomed.count
    }
}
