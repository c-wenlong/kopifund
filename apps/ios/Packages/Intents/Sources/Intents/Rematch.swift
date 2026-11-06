import Foundation
import Matching
import Storage
import SwiftData

/// G24: what one pass of the saved patterns over Unmatched did.
public struct RematchResult: Equatable, Sendable {
    /// One alert that left Unmatched for the Inbox.
    public struct Moved: Equatable, Sendable {
        public var unmatchedID: UUID
        public var patternName: String
    }

    public var moved: [Moved] = []
    /// Alerts still in Unmatched after the pass (their reasons re-read).
    public var stillUnmatched = 0
    /// A moved alert queued approved (its pattern auto-approves), so the
    /// caller can send it now rather than at the next return to the app.
    public var queuedApproved = false

    public init() {}

    /// The toast after a pass: "3 waiting alerts now match DBS PayLah", or
    /// "... now match your patterns" when several patterns took them. An
    /// empty pass says so only when the owner asked for it (`asked`); after
    /// a save it says nothing.
    public func message(asked: Bool = false) -> String {
        guard !moved.isEmpty else {
            return asked ? "No waiting alert matches a pattern yet" : ""
        }
        let n = moved.count
        let names = Set(moved.map(\.patternName))
        let what = names.count == 1 ? names.first! : "your patterns"
        return n == 1 ? "1 waiting alert now matches \(what)"
                      : "\(n) waiting alerts now match \(what)"
    }
}

extension Ingest {
    /// The pass in flight, so a save and a "Try patterns again" never read
    /// the same Unmatched rows at once and queue one alert twice: each new
    /// pass waits for the one before it.
    @MainActor private static var rematchInFlight: Task<RematchResult, Error>?

    /// G24: run every saved pattern over every alert waiting in Unmatched,
    /// through the path a fresh alert takes. A match leaves Unmatched for
    /// the Inbox (duplicate and currency checks included); a miss stays, with
    /// its "Why nothing matched" re-read against today's patterns. Run after
    /// a pattern is saved or edited, and by Unmatched's "Try patterns again".
    ///
    /// The match runs off the main actor, as a fresh alert's does. Left
    /// alone: an alert the record already covers (`inKopifund`), and one
    /// whose shape the owner chose to ignore. `keeping` names an Unmatched
    /// row the caller deletes itself (the one an open alert page shows):
    /// its spend still queues, but the row stays for the caller. An alert
    /// already in the Inbox from an earlier pass (same text, same arrival)
    /// leaves Unmatched without a second row.
    @MainActor
    public static func rematchUnmatched(container: ModelContainer,
                                        keeping: UUID? = nil,
                                        defaults: UserDefaults? = AppGroup.defaults) async throws -> RematchResult {
        let previous = rematchInFlight
        let pass = Task { @MainActor in
            _ = try? await previous?.value
            return try await rematchPass(container: container, keeping: keeping, defaults: defaults)
        }
        rematchInFlight = pass
        return try await pass.value
    }

    /// What the detached matcher needs of one Unmatched row.
    private struct Waiting: Sendable {
        var id: UUID
        var text: String
        var source: String?
        var receivedAt: Date
        var arrivedOn: CalendarDate
    }

    @MainActor
    private static func rematchPass(container: ModelContainer, keeping: UUID?,
                                    defaults: UserDefaults?) async throws -> RematchResult {
        var result = RematchResult()
        let read = ModelContext(container)
        let patterns = try read.fetch(FetchDescriptor<PatternRecord>()).compactMap(\.pattern)
        let waiting = try read.fetch(FetchDescriptor<UnmatchedRecord>())
            .filter { $0.inKopifund != true }
            .map { Waiting(id: $0.id, text: $0.text, source: $0.source,
                           receivedAt: $0.receivedAt, arrivedOn: $0.arrivedOn) }
        guard !patterns.isEmpty, !waiting.isEmpty else {
            result.stillUnmatched = waiting.count
            return result
        }
        let outcomes = await Task.detached(priority: .userInitiated) {
            waiting.map {
                Matcher.match(text: $0.text, source: $0.source, receivedAt: $0.receivedAt,
                              arrivedOn: $0.arrivedOn, patterns: patterns)
            }
        }.value

        // a fresh context after the wait: a row the owner dismissed while
        // the matcher ran is gone from the store and is skipped
        let context = ModelContext(container)
        for (w, outcome) in zip(waiting, outcomes) {
            let id = w.id
            guard let record = try context.fetch(FetchDescriptor<UnmatchedRecord>(
                predicate: #Predicate { $0.id == id })).first else { continue }
            switch outcome {
            case .matched(let t):
                if Suppression.contains(AlertShape.signature(of: w.text), in: defaults) {
                    result.stillUnmatched += 1
                    continue
                }
                let text = w.text, at = w.receivedAt
                let already = try context.fetchCount(FetchDescriptor<TransactionRecord>(
                    predicate: #Predicate { $0.alertText == text && $0.receivedAt == at })) > 0
                if !already {
                    do {
                        try queue(t, patterns: patterns, in: context, defaults: defaults)
                    } catch {
                        // an amount too large to count stays put with the
                        // reason, as a fresh alert's would fail
                        record.reasons = "\(t.patternName): \(error.localizedDescription)"
                        result.stillUnmatched += 1
                        continue
                    }
                    if patterns.first(where: { $0.id == t.patternID })?.autoApprove == true {
                        result.queuedApproved = true
                    }
                }
                if id != keeping { context.delete(record) }
                result.moved.append(.init(unmatchedID: id, patternName: t.patternName))
            case .unmatched(let rejections):
                record.reasons = reasons(rejections)
                result.stillUnmatched += 1
            }
        }
        try context.save()
        return result
    }
}
