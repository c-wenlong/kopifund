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
    let outcome = try Ingest.alert(
        texts: ["You charged SGD5.00 at KOPITIAM",
                "a reminder about a mentorship evening, no spend here",
                "   "],
        source: "email", receivedAt: day, container: container)
    #expect(outcome == "1 queued, 1 unmatched")
    #expect(try context.fetchCount(FetchDescriptor<TransactionRecord>()) == 1)
    #expect(try context.fetchCount(FetchDescriptor<UnmatchedRecord>()) == 1)
    // a single item keeps the single-ingest wording
    #expect(try Ingest.alert(texts: ["You charged SGD9.00 at STARBUCKS"],
                           source: "email", receivedAt: day,
                           container: container) == "queued")
}
