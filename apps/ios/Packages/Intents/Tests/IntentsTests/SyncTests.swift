import Foundation
import SwiftData
import Testing
@testable import Intents
import Storage
import YNABClient

/// G24: the sync engine against a mock YNAB. MockProtocol keeps its handler
/// in a static, so this suite runs serially.

private func respond(_ status: Int, _ json: String = "{}") -> (HTTPURLResponse, Data) {
    (HTTPURLResponse(url: YNAB.base, statusCode: status, httpVersion: nil, headerFields: nil)!,
     Data(json.utf8))
}

private func created(_ id: String) -> (HTTPURLResponse, Data) {
    respond(201, #"{"data":{"transaction":{"id":"\#(id)"}}}"#)
}

/// The import_id of a POST, read from the body stream URLSession hands the
/// protocol.
private func importID(of req: URLRequest) -> String? {
    var data = req.httpBody ?? Data()
    if data.isEmpty, let stream = req.httpBodyStream {
        stream.open()
        var chunk = [UInt8](repeating: 0, count: 65536)
        while stream.hasBytesAvailable {
            let n = stream.read(&chunk, maxLength: chunk.count)
            if n <= 0 { break }
            data.append(contentsOf: chunk[0..<n])
        }
        stream.close()
    }
    let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    return (body?["transaction"] as? [String: Any])?["import_id"] as? String
}

/// Thread-safe log of what the mock saw.
private final class Log: @unchecked Sendable {
    private let lock = NSLock()
    private var ids: [String] = []
    func add(_ id: String?) { lock.lock(); ids.append(id ?? "?"); lock.unlock() }
    var all: [String] { lock.lock(); defer { lock.unlock() }; return ids }
}

private let settings = SyncSettings(planID: "p1", defaultAccountID: "", postApproved: false)
private let day = Date(timeIntervalSince1970: 1_800_000_000)

private func client() -> YNABClient {
    YNABClient(token: "t", session: MockProtocol.session(), retries: 0, retryDelay: 1)
}

@MainActor
private func approved(_ context: ModelContext, payee: String = "Kopitiam", cents: Int = 480,
                      account: String = "a1", at: Date = day) -> TransactionRecord {
    let row = TransactionRecord(walletAmountCents: cents, merchant: payee, card: nil,
                                date: at, accountID: account)
    row.state = "approved"
    context.insert(row)
    try? context.save()
    return row
}

@Suite(.serialized) @MainActor
struct SyncTests {
    init() {
        MockProtocol.handler = nil
        MockProtocol.requests = []
    }

    @Test func importIDIsPerRowAndFitsYNAB() {
        let id = UUID()
        let a = ImportID.make(rowID: id)
        #expect(a == ImportID.make(rowID: id))
        #expect(a != ImportID.make(rowID: UUID()))
        #expect(a.count <= 36)
        #expect(a.hasPrefix("al-"))
    }

    @Test func twoIdenticalChargesBothReachYNAB() async throws {
        let context = ModelContext(try Store.memory())
        let a = approved(context)
        let b = approved(context)
        let log = Log()
        MockProtocol.handler = { req in
            log.add(importID(of: req))
            return created("y\(log.all.count)")
        }
        let out = await Sync.run(context: context, client: client(), settings: settings)
        #expect(out.posted == 2)
        #expect(Set(log.all).count == 2)
        #expect(a.state == "synced" && b.state == "synced")
        #expect(a.ynabID != nil && b.ynabID != nil)
    }

    @Test func retryReusesTheImportIDAndA409ReadsAsSynced() async throws {
        let context = ModelContext(try Store.memory())
        let row = approved(context)
        let log = Log()
        MockProtocol.handler = { req in
            log.add(importID(of: req))
            return log.all.count == 1 ? respond(500) : respond(409)
        }
        var out = await Sync.run(context: context, client: client(), settings: settings)
        #expect(out.failed == 1)
        #expect(row.state == "failed")
        #expect(row.syncAttempts == 1)
        out = await Sync.run(context: context, client: client(), settings: settings, force: true)
        #expect(row.state == "synced")
        #expect(log.all.count == 2)
        #expect(log.all[0] == log.all[1])
        #expect(log.all[0] == ImportID.make(rowID: row.id))
    }

    @Test func unauthorizedStopsAfterOneRequest() async throws {
        let context = ModelContext(try Store.memory())
        let rows = [approved(context), approved(context, payee: "B"), approved(context, payee: "C")]
        MockProtocol.handler = { _ in respond(401) }
        let out = await Sync.run(context: context, client: client(), settings: settings)
        #expect(out.stop == .unauthorized)
        #expect(MockProtocol.requests.count == 1)
        // the token is at fault, not the rows: they stay queued, uncounted
        for r in rows {
            #expect(r.state == "approved")
            #expect(r.syncAttempts == 0)
        }
    }

    @Test func rateLimitStopsTheRunAndTheGateHoldsLaterRuns() async throws {
        let context = ModelContext(try Store.memory())
        _ = approved(context)
        _ = approved(context, payee: "B")
        MockProtocol.handler = { _ in respond(429) }
        let out = await Sync.run(context: context, client: client(), settings: settings)
        #expect(out.stop == .rateLimited)
        #expect(MockProtocol.requests.count == 1)
        var gate = SyncGate()
        gate.record(out, token: "t", now: day)
        #expect(!gate.allows(token: "t", now: day.addingTimeInterval(60), force: false))
        #expect(gate.allows(token: "t", now: day.addingTimeInterval(60), force: true))
        #expect(gate.allows(token: "t", now: day.addingTimeInterval(SyncGate.ratePause), force: false))
    }

    @Test func aRejectedTokenIsNotRetriedUntilItChanges() {
        var gate = SyncGate()
        var stopped = SyncOutcome()
        stopped.stop = .unauthorized
        gate.record(stopped, token: "old", now: day)
        #expect(!gate.allows(token: "old", now: day, force: false))
        #expect(gate.allows(token: "new", now: day, force: false))
        gate.record(SyncOutcome(), token: "new", now: day)
        #expect(gate.allows(token: "old", now: day, force: false))
    }

    @Test func aFailedRowBacksOffUntilItsTimeOrAForcedSync() async throws {
        let context = ModelContext(try Store.memory())
        let row = approved(context)
        MockProtocol.handler = { _ in respond(503) }
        _ = await Sync.run(context: context, client: client(), settings: settings, now: { day })
        #expect(row.state == "failed")
        #expect(row.nextSyncAt == day.addingTimeInterval(SyncBackoff.base))
        // still waiting: no request
        _ = await Sync.run(context: context, client: client(), settings: settings,
                           now: { day.addingTimeInterval(30) })
        #expect(MockProtocol.requests.count == 1)
        // due: second attempt doubles the wait
        _ = await Sync.run(context: context, client: client(), settings: settings,
                           now: { day.addingTimeInterval(61) })
        #expect(MockProtocol.requests.count == 2)
        #expect(row.nextSyncAt == day.addingTimeInterval(61 + 2 * SyncBackoff.base))
        // the user asked: no wait
        _ = await Sync.run(context: context, client: client(), settings: settings, force: true,
                           now: { day.addingTimeInterval(62) })
        #expect(MockProtocol.requests.count == 3)
    }

    @Test func backoffDoublesToItsCapAndStopsAfterMaxAttempts() {
        #expect(SyncBackoff.delay(afterAttempts: 1) == 60)
        #expect(SyncBackoff.delay(afterAttempts: 3) == 240)
        #expect(SyncBackoff.delay(afterAttempts: 30) == SyncBackoff.cap)
        let row = TransactionRecord(walletAmountCents: 1, merchant: "x", card: nil, date: day, accountID: "a")
        row.state = "failed"
        row.syncAttempts = SyncBackoff.maxAutomaticAttempts
        #expect(!Sync.isDue(row, now: .distantFuture, force: false))
        #expect(Sync.isDue(row, now: day, force: true))
    }

    @Test func aBadRequestWaitsForTheUser() async throws {
        let context = ModelContext(try Store.memory())
        let row = approved(context)
        MockProtocol.handler = { _ in respond(400) }
        _ = await Sync.run(context: context, client: client(), settings: settings)
        #expect(row.state == "failed")
        #expect(row.nextSyncAt == .distantFuture)
        _ = await Sync.run(context: context, client: client(), settings: settings)
        #expect(MockProtocol.requests.count == 1)
    }

    @Test func noAccountFailsWithoutARequestAndRetriesFreely() async throws {
        let context = ModelContext(try Store.memory())
        let row = approved(context, account: "")
        MockProtocol.handler = { _ in created("y1") }
        _ = await Sync.run(context: context, client: client(), settings: settings)
        #expect(row.state == "failed")
        #expect(row.syncError == "no YNAB account mapped")
        #expect(MockProtocol.requests.isEmpty)
        let withDefault = SyncSettings(planID: "p1", defaultAccountID: "a9", postApproved: false)
        _ = await Sync.run(context: context, client: client(), settings: withDefault)
        #expect(row.state == "synced")
    }

    @Test func aRowApprovedDuringARunIsPostedByThatRun() async throws {
        let context = ModelContext(try Store.memory())
        _ = approved(context)
        let late = TransactionRecord(walletAmountCents: 999, merchant: "Late", card: nil,
                                     date: day, accountID: "a1")
        context.insert(late)
        try context.save()
        MockProtocol.handler = { _ in
            Thread.sleep(forTimeInterval: 0.3)
            return created("y")
        }
        let run = Task { await Sync.run(context: context, client: client(), settings: settings) }
        try await Task.sleep(nanoseconds: 100_000_000)
        late.state = "approved"
        try context.save()
        let out = await run.value
        #expect(out.posted == 2)
        #expect(late.state == "synced")
    }

    @Test func cancellingARunLeavesTheRowQueued() async throws {
        let context = ModelContext(try Store.memory())
        let row = approved(context)
        MockProtocol.handler = { _ in
            Thread.sleep(forTimeInterval: 0.5)
            return created("y")
        }
        let run = Task { await Sync.run(context: context, client: client(), settings: settings) }
        try await Task.sleep(nanoseconds: 100_000_000)
        run.cancel()
        let out = await run.value
        #expect(out.stop == .cancelled)
        #expect(row.state == "approved")
        #expect(row.syncError == nil)
        // the id stays, so a send that did land turns the retry into a 409
        #expect(row.importID == ImportID.make(rowID: row.id))
    }

    @Test func aRowDeletedDuringItsSendIsLeftAlone() async throws {
        let context = ModelContext(try Store.memory())
        let row = approved(context)
        MockProtocol.handler = { _ in
            Thread.sleep(forTimeInterval: 0.3)
            return created("y")
        }
        let run = Task { await Sync.run(context: context, client: client(), settings: settings) }
        try await Task.sleep(nanoseconds: 100_000_000)
        context.delete(row)
        try context.save()
        _ = await run.value
        #expect(try context.fetchCount(FetchDescriptor<TransactionRecord>()) == 0)
    }

    @Test func aRowParkedByAnotherContainerMidRunIsNotSent() async throws {
        // two containers on one file stand in for the app and an intent in
        // another process: their saves never merge, only a fetch sees them
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString).appendingPathExtension("store")
        let app = try Store.container(at: url)
        let other = try Store.container(at: url)
        let context = app.mainContext
        _ = approved(context, payee: "A")
        let b = approved(context, payee: "B")
        b.receivedAt = day.addingTimeInterval(60)
        try context.save()
        MockProtocol.handler = { _ in
            Thread.sleep(forTimeInterval: 0.3)
            return created("y")
        }
        let run = Task { await Sync.run(context: context, client: client(), settings: settings) }
        try await Task.sleep(nanoseconds: 100_000_000)
        let elsewhere = ModelContext(other)
        let bid = b.id
        let copy = try #require(try elsewhere.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.id == bid })).first)
        copy.state = "duplicate"
        try elsewhere.save()
        let out = await run.value
        #expect(out.posted == 1)
        #expect(MockProtocol.requests.count == 1)
        // a fetch by id refreshes the app's copy from disk
        let live = try context.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.id == bid })).first
        #expect(live?.state == "duplicate")
        #expect(b.state == "duplicate")
    }

    @Test func beforeSendCanTakeARowOutOfTheQueue() async throws {
        let context = ModelContext(try Store.memory())
        let covered = approved(context, payee: "Covered")
        let kept = approved(context, payee: "Kept")
        MockProtocol.handler = { _ in created("y") }
        var seen: [String] = []
        let out = await Sync.run(context: context, client: client(), settings: settings,
                                 beforeSend: { row in
            seen.append(row.payee)
            if row.payee == "Covered" { row.state = "kopifund" }
        })
        #expect(Set(seen) == ["Covered", "Kept"])
        #expect(out.posted == 1)
        #expect(MockProtocol.requests.count == 1)
        #expect(covered.state == "kopifund")
        #expect(kept.state == "synced")
    }
}
