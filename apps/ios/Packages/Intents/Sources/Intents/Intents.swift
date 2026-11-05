import AppIntents
import Foundation
import Matching
import Storage
import SwiftData

/// G24: the two App Intents Shortcuts calls. Both write straight into the App
/// Group store and finish without opening the app. iOS runs the share
/// extension and these intents in the same App Group as the app.

/// Shared ingest: run an alert's text through every saved pattern, then queue
/// the match or park it in Unmatched. Called by the intent, the share
/// extension and the paste box so all three behave identically.
public enum Ingest {
    @MainActor
    public static func alert(text: String, source: String?, receivedAt: Date,
                             container: ModelContainer) throws -> String {
        let context = ModelContext(container)
        // a shape the user dismissed as noise never reaches the queue, even
        // when a pattern could read it; the same check runs in the
        // extension's process
        if Suppression.contains(AlertShape.signature(of: text)) {
            return "ignored"
        }
        let patterns = try context.fetch(FetchDescriptor<PatternRecord>())
            .compactMap { $0.pattern }
        switch Matcher.match(text: text, source: source, receivedAt: receivedAt, patterns: patterns) {
        case .matched(let t):
            let pattern = patterns.first { $0.id == t.patternID }
            let row = TransactionRecord(from: t, accountID: pattern?.accountID ?? "",
                                        categoryID: pattern?.defaultCategoryID)
            if pattern?.autoApprove == true { row.state = "approved" }
            context.insert(row)
            settleDuplicates(of: row, in: context)
            try context.save()
            return "queued"
        case .unmatched(let rejections):
            context.insert(UnmatchedRecord(text: text, source: source, receivedAt: receivedAt,
                                           reasons: rejections.map { "\($0.patternName): \($0.reason)" }
                                               .joined(separator: "\n")))
            try context.save()
            return "unmatched"
        }
    }

    /// Batched ingest for automation input: the Email and Message triggers
    /// hand one run every item they caught at once, and a shortcut that
    /// flattens that list into one string stitches several mails into a
    /// single unmatchable blob. Each item gets its own pass and its own
    /// record. The result reads like the single-ingest outcome, tallied for
    /// a batch: "unmatched", or "2 queued, 1 unmatched".
    @MainActor
    public static func alert(texts: [String], source: String?, receivedAt: Date?,
                             container: ModelContainer) throws -> String {
        var tally: [(outcome: String, count: Int)] = []
        let at = receivedAt ?? Date()
        for body in texts where !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let outcome = try alert(text: body, source: source, receivedAt: at, container: container)
            if let i = tally.firstIndex(where: { $0.outcome == outcome }) {
                tally[i].count += 1
            } else {
                tally.append((outcome, 1))
            }
        }
        guard !tally.isEmpty else { return "ignored" }
        // one item keeps the single-ingest wording; a batch tallies
        if tally.count == 1 && tally[0].count == 1 { return tally[0].outcome }
        return tally.map { "\($0.count) \($0.outcome)" }.joined(separator: ", ")
    }

    /// One spend can arrive over two channels — the Wallet tap and the bank's
    /// SMS for the same charge — and each channel writes its own row, so the
    /// same purchase would post to YNAB twice. When `row` reads as the same
    /// spend as an existing row, the poorer capture is parked: state
    /// "duplicate" keeps it out of the sync flush while it waits for review.
    /// The row holding the alert text wins, because its parsed payee, card
    /// hint and mapped account carry more than a Wallet record does; it can
    /// demote a sibling only while that sibling has not attempted a send.
    @MainActor
    public static func settleDuplicates(of row: TransactionRecord, in context: ModelContext) {
        let cutoff = row.date.addingTimeInterval(-2 * 86_400)
        let existing = (try? context.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.date >= cutoff }))) ?? []
        // A match whose send is decided or in flight must not be demoted:
        // synced/syncing already posted or are posting, a failed row may
        // have reached YNAB before its reply was lost, so only its own
        // importID can safely retry the send, and a row the hub already heard
        // of holds the pending_txn it posted — demoting it and letting this
        // row post afresh would land the one spend on the record twice.
        var sent: TransactionRecord? = nil
        var best: TransactionRecord? = nil     // still pullable
        for c in existing where c.id != row.id && c.state != "duplicate" {
            guard Dedup.likelyDuplicate(
                amountCents: row.amountCents, currency: row.currency,
                date: row.day, payee: row.payee,
                otherAmountCents: c.amountCents, otherCurrency: c.currency,
                otherDate: c.day, otherPayee: c.payee) else { continue }
            if c.state == "syncing" || c.state == "synced" || c.state == "failed" || c.kopifundID != nil {
                sent = sent.map { preferred(c, over: $0) } ?? c
            } else {
                best = best.map { preferred(c, over: $0) } ?? c
            }
        }
        if let sent {
            // a capture of this spend may already be at YNAB; parking the new
            // row is the only move that cannot double-post
            row.state = "duplicate"
            row.duplicateOf = sent.id
        } else if let sib = best {
            if rank(row) > rank(sib) {
                sib.state = "duplicate"
                sib.duplicateOf = row.id
            } else {
                row.state = "duplicate"
                row.duplicateOf = sib.id
            }
        }
    }

    private static func rank(_ r: TransactionRecord) -> Int { r.alertText.isEmpty ? 0 : 1 }

    /// The row that keeps the queue: the one holding the bank's raw text,
    /// else whichever arrived first.
    private static func preferred(_ a: TransactionRecord, over b: TransactionRecord) -> TransactionRecord {
        rank(a) > rank(b) || (rank(a) == rank(b) && a.receivedAt < b.receivedAt) ? a : b
    }
}

public struct IngestAlertIntent: AppIntent {
    public static let title: LocalizedStringResource = "Ingest Alert"
    public static let description = IntentDescription(
        "Match bank alert text against saved patterns and queue the transaction.")
    /// Background execution, no app launch, no UI.
    public static let openAppWhenRun = false

    /// A list, not one string: the Email and Message automation triggers can
    /// deliver several items in a single run, and a String parameter would
    /// flatten them into one blob. A shortcut binding a single text token
    /// still works; it resolves to a one-element list.
    @Parameter(title: "Text") public var text: [String]
    @Parameter(title: "Source") public var source: String?
    @Parameter(title: "Received At") public var receivedAt: Date?

    public init() {}
    public init(text: String, source: String? = nil, receivedAt: Date? = nil) {
        self.init(texts: [text], source: source, receivedAt: receivedAt)
    }
    public init(texts: [String], source: String? = nil, receivedAt: Date? = nil) {
        self.text = texts
        self.source = source
        self.receivedAt = receivedAt
    }

    @MainActor
    public func perform() async throws -> some IntentResult & ReturnsValue<String> {
        let container = try Store.container()
        let outcome = try Ingest.alert(texts: text, source: source,
                                       receivedAt: receivedAt, container: container)
        return .result(value: outcome)
    }
}

public struct IngestWalletTransactionIntent: AppIntent {
    public static let title: LocalizedStringResource = "Ingest Wallet Transaction"
    public static let description = IntentDescription(
        "Queue an Apple Pay transaction from the Shortcuts Wallet trigger.")
    public static let openAppWhenRun = false

    /// Shortcuts sends a number; AppIntents has no Decimal parameter type, so
    /// it arrives as Double and becomes integer cents before it is stored.
    @Parameter(title: "Amount") public var amount: Double
    @Parameter(title: "Merchant") public var merchant: String
    @Parameter(title: "Card") public var card: String?
    @Parameter(title: "Date") public var date: Date?

    public init() {}

    @MainActor
    public func perform() async throws -> some IntentResult & ReturnsValue<String> {
        let container = try Store.container()
        let context = ModelContext(container)
        // the app's Settings writes its Wallet default into the shared
        // App Group defaults; only then fall back to a pattern's account
        let shared = UserDefaults(suiteName: AppGroup.id)?.string(forKey: "defaultAccountID") ?? ""
        let accountID = shared.isEmpty
            ? ((try context.fetch(FetchDescriptor<PatternRecord>())
                .sorted { $0.priority > $1.priority }.first?.accountID) ?? "")
            : shared
        let row = TransactionRecord(walletAmountCents: Int((amount * 100).rounded()),
                                    merchant: merchant, card: card,
                                    date: date ?? Date(), accountID: accountID)
        context.insert(row)
        Ingest.settleDuplicates(of: row, in: context)
        try context.save()
        return .result(value: "queued")
    }
}

public struct AlertsShortcuts: AppShortcutsProvider {
    public static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: IngestAlertIntent(),
                    phrases: ["Ingest an alert with \(.applicationName)"],
                    shortTitle: "Ingest Alert",
                    systemImageName: "bell.badge")
        AppShortcut(intent: IngestWalletTransactionIntent(),
                    phrases: ["Ingest a wallet transaction with \(.applicationName)"],
                    shortTitle: "Ingest Wallet Transaction",
                    systemImageName: "wallet.pass")
    }
}
