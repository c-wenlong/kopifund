import Foundation
import SwiftData
import Testing
@testable import Intents
import Matching
import Storage

/// G24: settleDuplicates decides which of two captures of the same spend keeps
/// the queue. Fixtures stand in for a Wallet row (alertText empty) and an
/// alert row (alertText set); dates share one local calendar day.
@MainActor
private func makeRow(payee: String = "Sgh - Digestive & Live", cents: Int = 4453,
                     state: String = "pending", at: Date, rich: Bool = false) -> TransactionRecord {
    let row = TransactionRecord(walletAmountCents: cents, merchant: payee,
                                card: nil, date: at, accountID: "a1")
    row.state = state
    row.receivedAt = at
    if rich { row.alertText = "A card txn of SGD44.53 ... was completed." }
    return row
}

private let day = Calendar.current.date(
    from: DateComponents(year: 2026, month: 10, day: 26, hour: 12))!

@Test @MainActor func syncedSiblingParksNewRow() throws {
    let context = ModelContext(try Store.memory())
    let sib = makeRow(payee: "SGH - DIGESTIVE & LIVE", state: "synced", at: day)
    context.insert(sib)
    let row = makeRow(at: day.addingTimeInterval(3600), rich: true)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == sib.id)
    #expect(sib.state == "synced")
}

@Test @MainActor func syncingSiblingParksNewRow() throws {
    let context = ModelContext(try Store.memory())
    let sib = makeRow(state: "syncing", at: day)
    context.insert(sib)
    let row = makeRow(at: day, rich: true)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == sib.id)
}

@Test @MainActor func failedSiblingParksNewRow() throws {
    // a failed row may have reached YNAB before its reply was lost; the new
    // row parks and the failed row keeps its importID for the retry
    let context = ModelContext(try Store.memory())
    let sib = makeRow(state: "failed", at: day)
    context.insert(sib)
    let row = makeRow(at: day, rich: true)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == sib.id)
    #expect(sib.state == "failed")
}

@Test @MainActor func richerNewRowDemotesPendingSibling() throws {
    let context = ModelContext(try Store.memory())
    let sib = makeRow(at: day)
    context.insert(sib)
    let row = makeRow(payee: "SGH - DIGESTIVE & LIVE", at: day, rich: true)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(sib.state == "duplicate")
    #expect(sib.duplicateOf == row.id)
    #expect(row.state == "pending")
}

@Test @MainActor func poorerNewRowParksAgainstRichSibling() throws {
    let context = ModelContext(try Store.memory())
    let sib = makeRow(at: day, rich: true)
    context.insert(sib)
    let row = makeRow(payee: "SGH - DIGESTIVE & LIVE", at: day)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == sib.id)
}

@Test @MainActor func kopifundPostedSiblingParksNewRow() throws {
    // G25: a row the hub already heard of holds the pending_txn it posted;
    // demoting it for a richer capture would post the one spend twice
    let context = ModelContext(try Store.memory())
    let sib = makeRow(state: "approved", at: day)
    sib.kopifundID = "kph_1"
    context.insert(sib)
    let row = makeRow(at: day, rich: true)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(sib.state == "approved")
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == sib.id)
}

@Test @MainActor func approvedSiblingCanBeDemoted() throws {
    // an approved-but-unflushed sibling is still pullable; the sync flush
    // re-reads its state before posting so a demotion mid-flush is safe
    let context = ModelContext(try Store.memory())
    let sib = makeRow(state: "approved", at: day)
    context.insert(sib)
    let row = makeRow(at: day, rich: true)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(sib.state == "duplicate")
    #expect(sib.duplicateOf == row.id)
}

@Test @MainActor func equalRankNewcomerParks() throws {
    // equal captures: the existing row keeps the queue, since it may already
    // hold review work; the newcomer parks even when it arrived earlier
    let context = ModelContext(try Store.memory())
    let sib = makeRow(at: day.addingTimeInterval(3600))
    context.insert(sib)
    let row = makeRow(at: day)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(sib.state == "pending")
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == sib.id)
}

@Test @MainActor func keeperIsEarliestSiblingOnEqualRank() throws {
    // several matching siblings already queued: the parked row points at the
    // one that arrived first
    let context = ModelContext(try Store.memory())
    let late = makeRow(at: day.addingTimeInterval(3600))
    let early = makeRow(at: day)
    context.insert(late)
    context.insert(early)
    let row = makeRow(at: day.addingTimeInterval(1800))
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == early.id)
}

@Test @MainActor func differentPayeesDoNotDedup() throws {
    let context = ModelContext(try Store.memory())
    let sib = makeRow(payee: "Kopitiam", at: day)
    context.insert(sib)
    let row = makeRow(at: day)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    #expect(sib.state == "pending")
    #expect(row.state == "pending")
}

@Test @MainActor func duplicateRowsAreNotReselected() throws {
    let context = ModelContext(try Store.memory())
    let parked = makeRow(state: "duplicate", at: day)
    let sib = makeRow(state: "synced", at: day)
    context.insert(parked)
    context.insert(sib)
    let row = makeRow(at: day)
    context.insert(row)
    Ingest.settleDuplicates(of: row, in: context)
    // the already-parked row must not be linked as the keeper
    #expect(row.state == "duplicate")
    #expect(row.duplicateOf == sib.id)
}

/// A batched automation run (the Email trigger can deliver several mails at
/// once) must write one record per item rather than one stitched blob.
@Test @MainActor func batchedIngestWritesOneRecordPerItem() throws {
    let container = try Store.memory()
    let context = ModelContext(container)
    let pattern = AlertPattern(name: "Card spend", direction: .outflow,
                               accountID: "a1",
                               segments: [.literal("charged"), .amount,
                                          .literal("at"), .payee],
                               sampleText: "You charged SGD5.00 at KOPITIAM")
    context.insert(PatternRecord(pattern))
    try context.save()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    let outcome = try Ingest.alert(
        texts: ["You charged SGD5.00 at KOPITIAM",
                "a reminder about a mentorship evening, no spend here",
                "   "],
        source: "email", receivedAt: day, container: container, defaults: defaults)
    #expect(outcome == "1 queued, 1 unmatched")
    #expect(try context.fetchCount(FetchDescriptor<TransactionRecord>()) == 1)
    #expect(try context.fetchCount(FetchDescriptor<UnmatchedRecord>()) == 1)
    // a single item keeps the single-ingest wording
    #expect(try Ingest.alert(texts: ["You charged SGD9.00 at STARBUCKS"],
                           source: "email", receivedAt: day,
                           container: container, defaults: defaults) == "queued")
}

// MARK: - currency and Wallet input (G24 audit)

/// A defaults suite no other test or the App Group reads.
private func scratchDefaults(planCurrency: String? = nil) -> (UserDefaults, String) {
    let name = "kopifund.intents-tests.\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: name)!
    if let planCurrency { PlanCurrency.remember(planCurrency, planID: "p1", in: defaults) }
    return (defaults, name)
}

@MainActor
private func cardPattern(autoApprove: Bool) -> AlertPattern {
    AlertPattern(name: "Card spend", direction: .outflow, accountID: "a1",
                 autoApprove: autoApprove,
                 segments: [.literal("charged"), .amount, .literal("at"), .payee],
                 sampleText: "You charged SGD5.00 at KOPITIAM")
}

@MainActor
private func store(_ pattern: AlertPattern) throws -> ModelContainer {
    let container = try Store.memory()
    let context = ModelContext(container)
    context.insert(PatternRecord(pattern))
    try context.save()
    return container
}

@MainActor
private func rows(_ container: ModelContainer) throws -> [TransactionRecord] {
    try ModelContext(container).fetch(FetchDescriptor<TransactionRecord>())
}

@Test(arguments: [Double.nan, .infinity, -.infinity, 1e13, -1e13])
@MainActor func walletRefusesAnAmountItCannotCount(amount: Double) throws {
    // untrusted Shortcuts input: a NaN used to trap on Int(amount * 100)
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    #expect(throws: WalletInputError.self) {
        _ = try Ingest.wallet(amount: amount, merchant: "Lawson", card: nil, date: day,
                              currency: nil, container: container, defaults: defaults)
    }
    #expect(try rows(container).isEmpty)
}

@Test @MainActor func walletRefusesAWordForACurrency() throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    #expect(throws: WalletInputError.currency("dollars")) {
        _ = try Ingest.wallet(amount: 1200, merchant: "Lawson", card: nil, date: day,
                              currency: "dollars", container: container, defaults: defaults)
    }
    #expect(WalletInputError.currency("dollars").localizedDescription.contains("three-letter"))
}

@Test @MainActor func foreignWalletTapIsHeldNotPostedAsTheLocalCurrency() throws {
    // the audit's case: JPY 1,200 in Tokyo used to read SGD 1200.00
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults(planCurrency: "SGD")
    defer { defaults.removePersistentDomain(forName: name) }
    #expect(try Ingest.wallet(amount: 1200, merchant: "Lawson", card: nil, date: day,
                              currency: "jpy", container: container, defaults: defaults) == "queued")
    let row = try #require(try rows(container).first)
    #expect(row.currency == "JPY")
    #expect(row.amountCents == 120000)
    #expect(row.state == "pending")
    #expect(row.syncError?.contains("JPY") == true)
    #expect(row.checkCurrency(against: "SGD") == false)
}

@Test @MainActor func walletTapWithNoCurrencyStaysUnstatedUntilTheSync() throws {
    // intake leaves it empty so a capture stating one can still pair with
    // it; the sync's check counts it in the plan's currency
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults(planCurrency: "SGD")
    defer { defaults.removePersistentDomain(forName: name) }
    defaults.set("acct-wallet", forKey: "defaultAccountID")
    _ = try Ingest.wallet(amount: 4.53, merchant: "Kopitiam", card: "Visa", date: day,
                          currency: nil, container: container, defaults: defaults)
    let row = try #require(try rows(container).first)
    #expect(row.currency == "")
    #expect(row.amountCents == 453)
    #expect(row.accountID == "acct-wallet")
    #expect(row.syncError == nil)
    #expect(row.checkCurrency(against: "SGD"))
    #expect(row.currency == "SGD")
}

@Test @MainActor func unstatedAlertPairsWithALaterForeignTapAndIsHeld() throws {
    // Greptile on #319: filling SGD at intake stopped this pair forming, so
    // the auto-approved alert posted JPY 1,200 as S$1,200
    let container = try store(cardPattern(autoApprove: true))
    let (defaults, name) = scratchDefaults(planCurrency: "SGD")
    defer { defaults.removePersistentDomain(forName: name) }
    _ = try Ingest.alert(text: "You charged 1200.00 at LAWSON", source: "sms",
                         receivedAt: day, container: container, defaults: defaults)
    let first = try #require(try rows(container).first)
    #expect(first.currency == "")
    #expect(first.state == "approved")
    _ = try Ingest.wallet(amount: 1200, merchant: "Lawson", card: nil, date: day,
                          currency: "JPY", container: container, defaults: defaults)
    let all = try rows(container)
    let alert = try #require(all.first { !$0.alertText.isEmpty })
    let tap = try #require(all.first { $0.alertText.isEmpty })
    #expect(tap.state == "duplicate")
    #expect(alert.currency == "JPY")
    #expect(alert.state == "pending")
    #expect(alert.syncError?.contains("JPY") == true)
}

@Test @MainActor func walletTapBeforeAnySyncLeavesCurrencyUnstated() throws {
    // no plan currency read yet: nothing is invented; the sync fills it
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    _ = try Ingest.wallet(amount: 4.53, merchant: "Kopitiam", card: nil, date: day,
                          currency: nil, container: container, defaults: defaults)
    #expect(try rows(container).first?.currency == "")
}

@Test @MainActor func foreignAlertIsHeldEvenOnAnAutoApprovePattern() throws {
    let container = try store(cardPattern(autoApprove: true))
    let (defaults, name) = scratchDefaults(planCurrency: "SGD")
    defer { defaults.removePersistentDomain(forName: name) }
    _ = try Ingest.alert(text: "You charged USD 12.00 at AMAZON", source: "sms",
                         receivedAt: day, container: container, defaults: defaults)
    let row = try #require(try rows(container).first)
    #expect(row.currency == "USD")
    #expect(row.amountCents == 1200)
    #expect(row.state == "pending")
    #expect(row.syncError?.contains("USD") == true)
}

@Test @MainActor func localAlertStillAutoApproves() throws {
    let container = try store(cardPattern(autoApprove: true))
    let (defaults, name) = scratchDefaults(planCurrency: "SGD")
    defer { defaults.removePersistentDomain(forName: name) }
    _ = try Ingest.alert(text: "You charged SGD 12.00 at KOPITIAM", source: "sms",
                         receivedAt: day, container: container, defaults: defaults)
    let row = try #require(try rows(container).first)
    #expect(row.state == "approved")
    #expect(row.syncError == nil)
}

@Test @MainActor func suppressedShapeIsIgnoredWithInjectedDefaults() throws {
    let container = try store(cardPattern(autoApprove: false))
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    let text = "You charged SGD 12.00 at KOPITIAM"
    Suppression.add(AlertShape.signature(of: text), in: defaults)
    #expect(try Ingest.alert(text: text, source: "sms", receivedAt: day,
                             container: container, defaults: defaults) == "ignored")
    #expect(try rows(container).isEmpty)
}

@Test @MainActor func walletRowWithNoCurrencyPairsWithTheBanksSMS() throws {
    let context = ModelContext(try Store.memory())
    let sms = makeRow(payee: "SGH - DIGESTIVE & LIVE", at: day, rich: true)
    sms.currency = "SGD"
    context.insert(sms)
    let tap = makeRow(at: day)          // Wallet initialiser: currency ""
    context.insert(tap)
    Ingest.settleDuplicates(of: tap, in: context)
    #expect(tap.state == "duplicate")
    #expect(tap.duplicateOf == sms.id)
}

@Test @MainActor func differentStatedCurrenciesDoNotDedup() throws {
    let context = ModelContext(try Store.memory())
    let usd = makeRow(at: day, rich: true)
    usd.currency = "USD"
    context.insert(usd)
    let sgd = makeRow(at: day)
    sgd.currency = "SGD"
    context.insert(sgd)
    Ingest.settleDuplicates(of: sgd, in: context)
    #expect(sgd.state == "pending")
    #expect(usd.state == "pending")
}

@Test @MainActor func keptRowTakesTheCurrencyOnlyItsParkedPairStated() throws {
    // review on #319: a JPY Wallet tap parked beside an auto-approved alert
    // that printed no currency must not let the alert post as the plan's
    let context = ModelContext(try Store.memory())
    let tap = makeRow(payee: "LAWSON", cents: 120000, at: day)
    tap.currency = "JPY"
    context.insert(tap)
    let alert = makeRow(payee: "LAWSON", cents: 120000, state: "approved", at: day, rich: true)
    context.insert(alert)
    Ingest.settleDuplicates(of: alert, in: context)
    #expect(tap.state == "duplicate")
    #expect(alert.currency == "JPY")
    #expect(alert.checkCurrency(against: "SGD") == false)
    #expect(alert.state == "pending")
}

@Test @MainActor func parkedNewRowHandsItsCurrencyToAnUnsentKeeper() throws {
    let context = ModelContext(try Store.memory())
    let alert = makeRow(payee: "LAWSON", cents: 120000, at: day, rich: true)
    context.insert(alert)
    let tap = makeRow(payee: "LAWSON", cents: 120000, at: day)
    tap.currency = "JPY"
    context.insert(tap)
    Ingest.settleDuplicates(of: tap, in: context)
    #expect(tap.state == "duplicate")
    #expect(alert.currency == "JPY")
}

@Test @MainActor func syncedKeeperKeepsItsCurrency() throws {
    let context = ModelContext(try Store.memory())
    let sent = makeRow(payee: "LAWSON", cents: 120000, state: "synced", at: day, rich: true)
    sent.currency = "SGD"
    context.insert(sent)
    let tap = makeRow(payee: "LAWSON", cents: 120000, at: day)
    context.insert(tap)
    Ingest.settleDuplicates(of: tap, in: context)
    #expect(tap.state == "duplicate")
    #expect(sent.currency == "SGD")
}
