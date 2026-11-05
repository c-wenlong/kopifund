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
    ///
    /// The store is read and written on the main actor, but the match itself
    /// runs on a detached task: a pattern can spend up to its budget on a
    /// long alert, and a phone with many patterns would otherwise hold the
    /// main thread, and a share extension's UI, for all of them. `defaults`
    /// is the App Group's everywhere but in tests: it holds the suppressed
    /// shapes and the plan's currency. `arrivedOn` is the day a saved alert
    /// first arrived (Unmatched's re-read after teaching a pattern), so a
    /// yearless date reads against where the phone was then, not where it
    /// is now.
    @MainActor
    public static func alert(text: String, source: String?, receivedAt: Date,
                             arrivedOn: CalendarDate? = nil,
                             container: ModelContainer,
                             defaults: UserDefaults? = AppGroup.defaults) async throws -> String {
        let context = ModelContext(container)
        // a shape the user dismissed as noise never reaches the queue, even
        // when a pattern could read it; the same check runs in the
        // extension's process
        if Suppression.contains(AlertShape.signature(of: text), in: defaults) {
            return "ignored"
        }
        let patterns = try context.fetch(FetchDescriptor<PatternRecord>())
            .compactMap { $0.pattern }
        let arrived = arrivedOn ?? CalendarDate(receivedAt, in: .current)
        let outcome = await Task.detached(priority: .userInitiated) {
            Matcher.match(text: text, source: source, receivedAt: receivedAt,
                          arrivedOn: arrived, patterns: patterns)
        }.value
        switch outcome {
        case .matched(let t):
            let pattern = patterns.first { $0.id == t.patternID }
            let row = try TransactionRecord(from: t, accountID: pattern?.accountID ?? "",
                                        categoryID: pattern?.defaultCategoryID)
            if pattern?.autoApprove == true { row.state = "approved" }
            context.insert(row)
            settleDuplicates(of: row, in: context)
            holdKeeperIfForeign(of: row, in: context, defaults: defaults)
            try context.save()
            return "queued"
        case .unmatched(let rejections):
            context.insert(UnmatchedRecord(text: text, source: source, receivedAt: receivedAt,
                                           arrivedOn: arrived,
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
                             container: ModelContainer,
                             defaults: UserDefaults? = AppGroup.defaults) async throws -> String {
        var tally: [(outcome: String, count: Int)] = []
        let at = receivedAt ?? Date()
        for body in texts where !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let outcome = try await alert(text: body, source: source, receivedAt: at,
                                          container: container, defaults: defaults)
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

    /// A Wallet tap from the Shortcuts trigger. `amount` arrives as a Double
    /// from untrusted Shortcuts input, so a NaN, an infinity or an absurd
    /// figure is refused with a readable error instead of trapping on the
    /// cents conversion. `currency` is the ISO code when the shortcut passes
    /// one; without it the row takes the plan's currency.
    @MainActor
    public static func wallet(amount: Double, merchant: String, card: String?, date: Date?,
                              currency: String?, container: ModelContainer,
                              defaults: UserDefaults? = AppGroup.defaults) throws -> String {
        let cents = try WalletInputError.cents(amount)
        var code: String? = nil
        if let currency, !currency.trimmingCharacters(in: .whitespaces).isEmpty {
            guard let c = Currency.normalized(currency) else {
                throw WalletInputError.currency(currency)
            }
            code = c
        }
        let context = ModelContext(container)
        // the app's Settings writes its Wallet default into the shared
        // App Group defaults; only then fall back to a pattern's account
        let shared = defaults?.string(forKey: "defaultAccountID") ?? ""
        let accountID = shared.isEmpty
            ? ((try context.fetch(FetchDescriptor<PatternRecord>())
                .sorted { $0.priority > $1.priority }.first?.accountID) ?? "")
            : shared
        let row = TransactionRecord(walletAmountCents: cents,
                                    merchant: merchant, card: card,
                                    date: date ?? Date(), accountID: accountID,
                                    currency: code)
        context.insert(row)
        settleDuplicates(of: row, in: context)
        holdKeeperIfForeign(of: row, in: context, defaults: defaults)
        try context.save()
        return "queued"
    }

    /// Keeps a foreign spend in review with the reason on the row, once a
    /// sync has read the plan's currency. Runs after pairing, on the row the
    /// pair kept: a currency only the parked capture stated has moved onto
    /// it by then. An unstated currency stays empty here so a later capture
    /// that states one can still pair with it; the sync counts it in the
    /// plan's currency when it posts, and checks every row again, so a row
    /// ingested before the plan's currency was known is still caught.
    @MainActor
    static func holdKeeperIfForeign(of row: TransactionRecord, in context: ModelContext,
                                    defaults: UserDefaults?) {
        var keeper = row
        if row.state == "duplicate" {
            guard let id = row.duplicateOf,
                  let kept = (try? context.fetch(FetchDescriptor<TransactionRecord>(
                      predicate: #Predicate { $0.id == id })))?.first else { return }
            keeper = kept
        }
        holdIfForeign(keeper, defaults: defaults)
    }

    /// Returns true when the row is held. Only a row still waiting to post
    /// (pending or approved) is moved; one syncing or sent is past holding.
    @MainActor @discardableResult
    static func holdIfForeign(_ row: TransactionRecord, defaults: UserDefaults?) -> Bool {
        guard row.state == "pending" || row.state == "approved",
              let plan = PlanCurrency.saved(in: defaults),
              let reason = Currency.holdReason(currency: row.currency, planCurrency: plan)
        else { return false }
        row.state = "pending"
        row.syncError = reason
        return true
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
            // an unstated currency (a Wallet tap) pairs with whatever the
            // other capture printed; two stated ones must agree
            guard Currency.compatible(row.currency, c.currency) else { continue }
            let shared = row.currency.isEmpty ? c.currency : row.currency
            guard Dedup.likelyDuplicate(
                amountCents: row.amountCents, currency: shared,
                date: row.day, payee: row.payee,
                otherAmountCents: c.amountCents, otherCurrency: shared,
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
            inheritCurrency(into: sent, from: row)
        } else if let sib = best {
            if rank(row) > rank(sib) {
                sib.state = "duplicate"
                sib.duplicateOf = row.id
                inheritCurrency(into: row, from: sib)
            } else {
                row.state = "duplicate"
                row.duplicateOf = sib.id
                inheritCurrency(into: sib, from: row)
            }
        }
    }

    /// The kept row of a pair is the same spend as the parked one, so a
    /// currency only the parked capture stated (a Wallet tap passing JPY
    /// beside an alert that printed none) moves onto the keeper. The sync's
    /// currency check then holds it instead of posting it as the plan's.
    /// A keeper already syncing or synced is past changing.
    private static func inheritCurrency(into keeper: TransactionRecord,
                                        from parked: TransactionRecord) {
        guard keeper.currency.isEmpty, !parked.currency.isEmpty,
              keeper.state != "syncing", keeper.state != "synced" else { return }
        keeper.currency = parked.currency
        // the hub was sent the keeper without this currency: clearing its
        // accepted copy puts it back in the sync's hub pass for a repost
        if keeper.kopifundID != nil { keeper.kopifundSent = nil }
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
        let outcome = try await Ingest.alert(texts: text, source: source,
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
    /// The ISO code ("JPY") when the shortcut can pass one. Left empty, the
    /// tap counts in the YNAB plan's currency.
    @Parameter(title: "Currency") public var currency: String?

    public init() {}

    @MainActor
    public func perform() async throws -> some IntentResult & ReturnsValue<String> {
        let outcome = try Ingest.wallet(amount: amount, merchant: merchant, card: card,
                                        date: date, currency: currency,
                                        container: try Store.container())
        return .result(value: outcome)
    }
}

/// Shortcuts input the Wallet intent refuses, worded for the Shortcuts
/// error sheet.
public enum WalletInputError: Error, Equatable, LocalizedError,
                              CustomLocalizedStringResourceConvertible {
    case amount(String)
    case currency(String)

    /// Integer cents for a Wallet amount, or a thrown error for NaN, an
    /// infinity or anything past `Cents.ceiling`.
    static func cents(_ amount: Double) throws -> Int {
        guard let cents = Cents.from(amount) else {
            throw WalletInputError.amount(String(describing: amount))
        }
        return cents
    }

    public var errorDescription: String? {
        switch self {
        case .amount(let raw):
            return "Kopifund can't read \(raw) as an amount. Check the shortcut passes the "
                + "transaction's Amount."
        case .currency(let raw):
            return "Kopifund can't read \"\(raw)\" as a currency. Pass a three-letter code "
                + "such as SGD or JPY, or leave Currency empty."
        }
    }

    public var localizedStringResource: LocalizedStringResource {
        LocalizedStringResource(stringLiteral: errorDescription ?? "Invalid Wallet input")
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
