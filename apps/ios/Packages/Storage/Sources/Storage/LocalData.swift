import Foundation
import SwiftData

/// G24: what Settings' "Delete all data" counts before it asks, and what it
/// removes: every record on this phone and the ignored alert shapes. The
/// YNAB token lives in the Keychain and stays.
public enum LocalData {
    public struct Counts: Equatable, Sendable {
        public var transactions: Int
        /// Rows YNAB has not confirmed (a lost reply leaves one failed or
        /// syncing though it landed); a duplicate or a row the record already
        /// covers ("kopifund") never goes there.
        public var unconfirmed: Int
        public var patterns: Int
        public var unmatched: Int
    }

    /// nil when the store cannot be counted: an unknown count must not read
    /// as an empty store.
    public static func counts(in context: ModelContext) -> Counts? {
        guard let all = try? context.fetchCount(FetchDescriptor<TransactionRecord>()),
              let unsynced = try? context.fetchCount(FetchDescriptor<TransactionRecord>(
                  predicate: #Predicate { $0.state == "pending" || $0.state == "approved"
                      || $0.state == "syncing" || $0.state == "failed" })),
              let patterns = try? context.fetchCount(FetchDescriptor<PatternRecord>()),
              let unmatched = try? context.fetchCount(FetchDescriptor<UnmatchedRecord>())
        else { return nil }
        return Counts(transactions: all, unconfirmed: unsynced, patterns: patterns, unmatched: unmatched)
    }

    /// Every transaction, pattern, unmatched alert and cached YNAB list, and
    /// the ignored shapes, which live outside the store (App Group defaults)
    /// so a records-only reset would leave them behind.
    public static func deleteAll(in context: ModelContext,
                                 suppressions defaults: UserDefaults? = AppGroup.defaults) {
        for row in (try? context.fetch(FetchDescriptor<TransactionRecord>())) ?? [] { context.delete(row) }
        for row in (try? context.fetch(FetchDescriptor<PatternRecord>())) ?? [] { context.delete(row) }
        for row in (try? context.fetch(FetchDescriptor<UnmatchedRecord>())) ?? [] { context.delete(row) }
        for row in (try? context.fetch(FetchDescriptor<MetadataRecord>())) ?? [] { context.delete(row) }
        Suppression.clear(in: defaults)
        try? context.save()
    }
}
