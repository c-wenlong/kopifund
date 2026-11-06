import Foundation
import SwiftData
import Testing
@testable import Intents
import Matching
import Storage

/// G24: the Inbox's paste box hands intake the alert with its line breaks
/// as they were copied. A single-line field joined "To:" and the merchant
/// on its next line into one line, and a line-anchored pattern no longer
/// read it. Invented wallet and merchants only.

private let multiLine = "Wallet payment\nYou paid SGD 9.10\nTo:\nLAMP SHOP\nRef E5005"

private let lineAnchored = AlertPattern(
    name: "Demo Wallet", direction: .outflow, accountID: "",
    segments: [.literal("You paid "), .amount, .literal("\nTo:\n"), .payee, .literal("\nRef")],
    sampleText: "Wallet payment\nYou paid SGD 4.20\nTo:\nKOPI CORNER\nRef A1001")

private func scratchDefaults() -> (UserDefaults, String) {
    let name = "kopifund.pasted-tests.\(UUID().uuidString)"
    return (UserDefaults(suiteName: name)!, name)
}

@Test @MainActor func aPastedAlertKeepsEveryLineBreak() async throws {
    let container = try Store.memory()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    // CRLF, a blank line and trailing space too: nothing is normalised
    let text = "Wallet payment\r\nYou paid SGD 9.10\n\nTo:  \nLAMP SHOP\n"
    #expect(try await PastedAlert.ingest(text, container: container, defaults: defaults) == "unmatched")
    let saved = try ModelContext(container).fetch(FetchDescriptor<UnmatchedRecord>())
    #expect(saved.map(\.text) == [text])
    #expect(saved.first?.source == "paste")
}

@Test @MainActor func aLineAnchoredPatternReadsThePastedAlert() async throws {
    let container = try Store.memory()
    let context = ModelContext(container)
    context.insert(PatternRecord(lineAnchored))
    try context.save()
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }

    #expect(try await PastedAlert.ingest(multiLine, container: container, defaults: defaults) == "queued")
    let row = try #require(try ModelContext(container).fetch(FetchDescriptor<TransactionRecord>()).first)
    #expect(row.payee == "LAMP SHOP")
    #expect(row.amountCents == 910)
    #expect(row.alertText == multiLine)

    // the same alert on one line, as the old single-line box gave it,
    // is what the pattern cannot read
    let joined = multiLine.replacingOccurrences(of: "\n", with: " ")
    #expect(try await PastedAlert.ingest(joined, container: container, defaults: defaults) == "unmatched")
}

@Test @MainActor func aBlankBoxIngestsNothing() async throws {
    let container = try Store.memory()
    for blank in ["", "   ", "\n\n", " \r\n\t"] {
        #expect(PastedAlert.isBlank(blank))
        #expect(try await PastedAlert.ingest(blank, container: container, defaults: nil) == "ignored")
    }
    #expect(try ModelContext(container).fetchCount(FetchDescriptor<UnmatchedRecord>()) == 0)
    #expect(!PastedAlert.isBlank("To:\nLAMP SHOP"))
}
