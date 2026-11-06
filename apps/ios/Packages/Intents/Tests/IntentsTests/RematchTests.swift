import Foundation
import SwiftData
import Testing
@testable import Intents
import Matching
import Storage

/// G24: saving a pattern re-reads every alert waiting in Unmatched. Four
/// alerts of one wallet template (invented merchants and references) sit in
/// Unmatched before the pattern exists; one save must move all four.

private let arrived = Calendar.current.date(
    from: DateComponents(year: 2026, month: 10, day: 20, hour: 9))!

private func walletAlert(_ merchant: String, _ amount: String, _ ref: String) -> String {
    "Wallet payment\nYou paid SGD \(amount)\nTo: \(merchant)\nRef \(ref)\nThank you for using Demo Wallet."
}

private let wallet = AlertPattern(
    name: "Demo Wallet", direction: .outflow, accountID: "",
    segments: [.literal("You paid "), .amount, .literal("\nTo: "), .payee, .literal("\nRef")],
    sampleText: walletAlert("KOPI CORNER", "4.20", "A1001"))

private func scratchDefaults() -> (UserDefaults, String) {
    let name = "kopifund.rematch-tests.\(UUID().uuidString)"
    return (UserDefaults(suiteName: name)!, name)
}

/// Each alert arrives with no pattern saved, the way the owner's did.
@MainActor
private func parked(_ texts: [String], in container: ModelContainer,
                    defaults: UserDefaults) async throws {
    for (i, t) in texts.enumerated() {
        let outcome = try await Ingest.alert(text: t, source: "email",
                                             receivedAt: arrived.addingTimeInterval(Double(i) * 600),
                                             container: container, defaults: defaults)
        #expect(outcome == "unmatched")
    }
}

@MainActor
private func save(_ p: AlertPattern, in container: ModelContainer) throws {
    let context = ModelContext(container)
    context.insert(PatternRecord(p))
    try context.save()
}

private let four = [walletAlert("KOPI CORNER", "4.20", "A1001"),
                    walletAlert("NOODLE HOUSE", "12.50", "B2002"),
                    walletAlert("BOOK NOOK", "31.00", "C3003"),
                    walletAlert("FRUIT STALL", "2.80", "D4004")]

@Test @MainActor func savingAPatternMovesEveryWaitingAlertOfItsTemplate() async throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    try await parked(four, in: container, defaults: defaults)
    try save(wallet, in: container)

    let result = try await Ingest.rematchUnmatched(container: container, defaults: defaults)

    #expect(result.moved.count == 4)
    #expect(result.stillUnmatched == 0)
    #expect(result.message() == "4 waiting alerts now match Demo Wallet")
    let context = ModelContext(container)
    #expect(try context.fetchCount(FetchDescriptor<UnmatchedRecord>()) == 0)
    let rows = try context.fetch(FetchDescriptor<TransactionRecord>())
    #expect(Set(rows.map(\.payee)) == ["KOPI CORNER", "NOODLE HOUSE", "BOOK NOOK", "FRUIT STALL"])
    #expect(Set(rows.map(\.amountCents)) == [420, 1250, 3100, 280])
    #expect(rows.allSatisfy { $0.state == "pending" })
    // the day each alert arrived, not the day of the re-read
    #expect(rows.allSatisfy { $0.day == CalendarDate(arrived, in: .current) })
}

@Test @MainActor func aSecondPassQueuesNothingTwice() async throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    try await parked(Array(four.prefix(2)), in: container, defaults: defaults)
    try save(wallet, in: container)

    // a save and "Try patterns again" at once: the second waits its turn
    let a = Task { @MainActor in try await Ingest.rematchUnmatched(container: container, defaults: defaults) }
    let b = Task { @MainActor in try await Ingest.rematchUnmatched(container: container, defaults: defaults) }
    let (first, second) = (try await a.value, try await b.value)

    #expect(first.moved.count + second.moved.count == 2)
    #expect(try ModelContext(container).fetchCount(FetchDescriptor<TransactionRecord>()) == 2)
    let again = try await Ingest.rematchUnmatched(container: container, defaults: defaults)
    #expect(again.moved.isEmpty)
    #expect(again.message(asked: true) == "No waiting alert matches a pattern yet")
    #expect(again.message() == "")
}

@Test @MainActor func anAlertAlreadyInTheInboxLeavesWithoutASecondRow() async throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    try await parked([four[0]], in: container, defaults: defaults)
    try save(wallet, in: container)
    // the same alert queued once already, as an earlier pass left it
    let kept = try await Ingest.rematchUnmatched(
        container: container,
        keeping: try ModelContext(container).fetch(FetchDescriptor<UnmatchedRecord>()).first?.id,
        defaults: defaults)
    #expect(kept.moved.count == 1)
    // `keeping` left the row for its caller; the next pass finds its spend
    // already queued and only clears the row
    #expect(try ModelContext(container).fetchCount(FetchDescriptor<UnmatchedRecord>()) == 1)
    let next = try await Ingest.rematchUnmatched(container: container, defaults: defaults)
    #expect(next.moved.count == 1)
    let context = ModelContext(container)
    #expect(try context.fetchCount(FetchDescriptor<UnmatchedRecord>()) == 0)
    #expect(try context.fetchCount(FetchDescriptor<TransactionRecord>()) == 1)
}

@Test @MainActor func aMissStaysWithTodaysReasons() async throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    let other = "Card alert: a charge of SGD 9.00 was made at LAMP SHOP."
    try await parked([four[0], other], in: container, defaults: defaults)
    try save(wallet, in: container)

    let result = try await Ingest.rematchUnmatched(container: container, defaults: defaults)

    #expect(result.moved.count == 1)
    #expect(result.stillUnmatched == 1)
    #expect(result.message() == "1 waiting alert now matches Demo Wallet")
    let left = try ModelContext(container).fetch(FetchDescriptor<UnmatchedRecord>())
    #expect(left.map(\.text) == [other])
    // it arrived when no pattern existed; it now says what Demo Wallet tried
    #expect(left.first?.reasons.hasPrefix("Demo Wallet: ") == true)
}

@Test @MainActor func coveredAndIgnoredAlertsStayPut() async throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    try await parked([four[0], four[1]], in: container, defaults: defaults)
    let context = ModelContext(container)
    let waiting = try context.fetch(FetchDescriptor<UnmatchedRecord>(
        sortBy: [SortDescriptor(\.receivedAt)]))
    // the record already holds the first; the owner ignores the second's shape
    waiting[0].inKopifund = true
    try context.save()
    Suppression.add(AlertShape.signature(of: four[1]), in: defaults)
    try save(wallet, in: container)

    let result = try await Ingest.rematchUnmatched(container: container, defaults: defaults)

    #expect(result.moved.isEmpty)
    #expect(try ModelContext(container).fetchCount(FetchDescriptor<UnmatchedRecord>()) == 2)
    #expect(try ModelContext(container).fetchCount(FetchDescriptor<TransactionRecord>()) == 0)
}

@Test @MainActor func anAutoApprovingPatternSaysSo() async throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    try await parked([four[2]], in: container, defaults: defaults)
    var auto = wallet
    auto.autoApprove = true
    try save(auto, in: container)

    let result = try await Ingest.rematchUnmatched(container: container, defaults: defaults)

    #expect(result.queuedApproved)
    #expect(try ModelContext(container).fetch(FetchDescriptor<TransactionRecord>()).first?.state == "approved")
}

@Test func severalPatternsShareOneToast() {
    var r = RematchResult()
    r.moved = [.init(unmatchedID: UUID(), patternName: "Demo Wallet"),
               .init(unmatchedID: UUID(), patternName: "Card spend")]
    #expect(r.message() == "2 waiting alerts now match your patterns")
}
