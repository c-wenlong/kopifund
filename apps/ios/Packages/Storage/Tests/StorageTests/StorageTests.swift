import Foundation
import CoreData
import SwiftData
import Testing
import Matching
@testable import Storage

// G24 audit: the store's records, the suppressed-shape set and the on-disk
// schema. Every store here is in memory or in a throwaway folder, and every
// defaults suite is a fresh name, so no test touches the App Group.

private let day = Date(timeIntervalSince1970: 1_793_000_000)
private let printed = CalendarDate(year: 2026, month: 10, day: 26)!

private func parsed(_ amount: String, currency: String? = "SGD") -> ParsedTransaction {
    ParsedTransaction(payee: "KOPITIAM", amount: Decimal(string: amount)!, date: printed,
                      direction: .outflow, accountHint: "1234", currency: currency,
                      patternID: UUID(), patternName: "DBS card", alertText: "alert",
                      source: "sms", receivedAt: day)
}

/// A folder under the system temp dir, removed when the test ends.
private func scratchFolder() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("storage-tests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// A defaults suite no other test or app reads.
private func scratchDefaults() -> (UserDefaults, String) {
    let name = "kopifund.storage-tests.\(UUID().uuidString)"
    return (UserDefaults(suiteName: name)!, name)
}

// MARK: - opening the store

@Test func missingAppGroupThrowsInsteadOfCrashing() {
    // an unsigned or re-signed build gets no App Group folder: the store
    // must throw a readable error the app can show, never trap
    #expect(throws: StoreError.appGroupUnavailable(AppGroup.id)) {
        _ = try Store.container(groupDirectory: nil)
    }
    let message = StoreError.appGroupUnavailable(AppGroup.id).localizedDescription
    #expect(message.contains(AppGroup.id))
    #expect(message.contains("App Group"))
}

@Test func storeInsideTheGroupFolderOpens() throws {
    let folder = try scratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let container = try Store.container(groupDirectory: folder)
    let context = ModelContext(container)
    context.insert(UnmatchedRecord(text: "hello", source: "sms", receivedAt: day, reasons: ""))
    try context.save()
    #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("alerts.store").path))
}

@Test func rowsSurviveClosingAndReopeningTheFile() throws {
    let folder = try scratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("alerts.store")
    let rowID: UUID
    do {
        let context = ModelContext(try Store.container(at: url))
        let row = try TransactionRecord(from: parsed("4.50"), accountID: "a1", categoryID: "c1")
        row.state = "approved"
        rowID = row.id
        context.insert(row)
        context.insert(PatternRecord(samplePattern()))
        context.insert(MetadataRecord(key: "accounts|p1", payload: Data("[]".utf8), fetchedAt: day))
        try context.save()
    }
    let context = ModelContext(try Store.container(at: url))
    let rows = try context.fetch(FetchDescriptor<TransactionRecord>())
    #expect(rows.map(\.id) == [rowID])
    #expect(rows.first?.state == "approved")
    #expect(rows.first?.amountCents == 450)
    #expect(try context.fetch(FetchDescriptor<PatternRecord>()).first?.pattern == samplePattern())
    #expect(try context.fetch(FetchDescriptor<MetadataRecord>()).first?.key == "accounts|p1")
}

/// The transaction row as it shipped before `duplicateOf` (a4294e9), still
/// carrying `payeeID` (dropped as unused in the G24 audit). A store written
/// with this shape must open under today's schema, the way an older install's
/// store does on upgrade: one field added, one removed.
///
/// The old shape is a Core Data model built here, not a second `@Model`
/// class: a SwiftData class with the entity name `TransactionRecord` beside
/// the real one let SwiftData resolve today's rows against the old entity
/// while the tests ran in parallel, and the run crashed now and then with
/// "not key value coding-compliant for the key dateIsDay".
enum LegacyStore {
    /// Built per write, so no model object is shared between tests.
    static func model() -> NSManagedObjectModel {
        func attribute(_ name: String, _ type: NSAttributeType,
                       optional: Bool = false) -> NSAttributeDescription {
            let a = NSAttributeDescription()
            a.name = name
            a.attributeType = type
            a.isOptional = optional
            return a
        }
        let row = NSEntityDescription()
        row.name = "TransactionRecord"
        row.managedObjectClassName = NSStringFromClass(NSManagedObject.self)
        row.properties = [
            attribute("id", .UUIDAttributeType),
            attribute("payee", .stringAttributeType),
            attribute("amountCents", .integer64AttributeType),
            attribute("currency", .stringAttributeType),
            attribute("date", .dateAttributeType),
            attribute("direction", .stringAttributeType),
            attribute("accountID", .stringAttributeType),
            attribute("categoryID", .stringAttributeType, optional: true),
            attribute("payeeID", .stringAttributeType, optional: true),
            attribute("memo", .stringAttributeType),
            attribute("accountHint", .stringAttributeType, optional: true),
            attribute("alertText", .stringAttributeType),
            attribute("source", .stringAttributeType, optional: true),
            attribute("receivedAt", .dateAttributeType),
            attribute("patternID", .UUIDAttributeType, optional: true),
            attribute("state", .stringAttributeType),
            attribute("ynabID", .stringAttributeType, optional: true),
            attribute("importID", .stringAttributeType, optional: true),
            attribute("syncError", .stringAttributeType, optional: true),
            attribute("syncAttempts", .integer64AttributeType),
        ]
        row.uniquenessConstraints = [["id"]]
        let model = NSManagedObjectModel()
        model.entities = [row]
        return model
    }

    /// One pending row in the old shape, written to a SQLite store at `url`.
    static func write(rowID: UUID, payee: String, to url: URL) throws {
        let model = model()
        let coordinator = NSPersistentStoreCoordinator(managedObjectModel: model)
        let store = try coordinator.addPersistentStore(type: .sqlite, at: url)
        let context = NSManagedObjectContext(.privateQueue)
        context.persistentStoreCoordinator = coordinator
        try context.performAndWait {
            let row = NSEntityDescription.insertNewObject(forEntityName: "TransactionRecord",
                                                          into: context)
            let values: [String: Any] = [
                "id": rowID, "payee": payee, "amountCents": 480, "currency": "SGD",
                "date": day, "direction": "outflow", "accountID": "a1", "memo": "",
                "alertText": "alert", "receivedAt": day, "state": "pending", "syncAttempts": 0,
            ]
            row.setValuesForKeys(values)
            try context.save()
        }
        try coordinator.remove(store)
    }
}

@Test func storeWrittenBeforeDuplicateOfStillOpens() throws {
    let folder = try scratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("alerts.store")
    let rowID = UUID()
    try LegacyStore.write(rowID: rowID, payee: "KOPITIAM", to: url)
    let context = ModelContext(try Store.container(at: url))
    let rows = try context.fetch(FetchDescriptor<TransactionRecord>())
    #expect(rows.map(\.id) == [rowID])
    #expect(rows.first?.payee == "KOPITIAM")
    #expect(rows.first?.duplicateOf == nil)
}

@Test func memoryStoreHoldsEveryModel() throws {
    let context = ModelContext(try Store.memory())
    context.insert(PatternRecord(samplePattern()))
    context.insert(try TransactionRecord(from: parsed("1"), accountID: "a1", categoryID: nil))
    context.insert(UnmatchedRecord(text: "t", source: nil, receivedAt: day, reasons: "P: no"))
    context.insert(MetadataRecord(key: "k", payload: Data(), fetchedAt: day))
    try context.save()
    #expect(try context.fetchCount(FetchDescriptor<PatternRecord>()) == 1)
    #expect(try context.fetchCount(FetchDescriptor<TransactionRecord>()) == 1)
    #expect(try context.fetchCount(FetchDescriptor<UnmatchedRecord>()) == 1)
    #expect(try context.fetchCount(FetchDescriptor<MetadataRecord>()) == 1)
}

// MARK: - TransactionRecord

@Test(arguments: [
    ("4.50", 450), ("4.505", 451), ("4.504", 450), ("0.005", 1),
    ("1200", 120000), ("0.01", 1),
] as [(String, Int)])
func centsRoundHalfUpFromTheParsedDecimal(amount: String, cents: Int) throws {
    let row = try TransactionRecord(from: parsed(amount), accountID: "a1", categoryID: nil)
    #expect(row.amountCents == cents)
}

@Test func negativeHalfCentRoundsAwayFromZero() throws {
    let row = try TransactionRecord(from: parsed("-4.505"), accountID: "a1", categoryID: nil)
    #expect(row.amountCents == -451)
}

@Test func parsedRowCarriesTheAlert() throws {
    let t = parsed("12.00", currency: "USD")
    let row = try TransactionRecord(from: t, accountID: "a1", categoryID: "c9")
    #expect(row.currency == "USD")
    #expect(row.payee == "KOPITIAM")
    #expect(row.accountID == "a1")
    #expect(row.categoryID == "c9")
    #expect(row.accountHint == "1234")
    #expect(row.alertText == "alert")
    #expect(row.source == "sms")
    #expect(row.patternID == t.patternID)
    #expect(row.direction == "outflow")
    #expect(row.state == "pending")
    #expect(row.duplicateOf == nil)
    #expect(row.importID == nil)
    #expect(row.syncAttempts == 0)
}

@Test func walletRowIsAnOutflowWithNoAlertText() {
    let row = TransactionRecord(walletAmountCents: 3817, merchant: "Hvc - Dental & Eye",
                                card: "DBS Altitude", date: day, accountID: "a1")
    #expect(row.amountCents == 3817)
    #expect(row.payee == "Hvc - Dental & Eye")
    #expect(row.accountHint == "DBS Altitude")
    #expect(row.source == "wallet")
    #expect(row.alertText.isEmpty)
    #expect(row.direction == "outflow")
    #expect(row.state == "pending")
    #expect(row.receivedAt == day)
}

@Test func rowsStoreACalendarDayWithNoZone() throws {
    // a parsed alert keeps the day it printed as UTC midnight, so reading it
    // back in any zone names the same day
    let alert = try TransactionRecord(from: parsed("4.50"), accountID: "a1", categoryID: nil)
    #expect(alert.date == printed.storedInstant)
    #expect(alert.dateIsDay == true)
    for zone in ["Pacific/Honolulu", "Europe/London", "Asia/Singapore", "Pacific/Kiritimati"] {
        #expect(CalendarDate(stored: alert.date, isDay: true,
                             legacyZone: TimeZone(identifier: zone)!) == printed)
    }
    // a Wallet tap keeps the day it happened where the phone was
    let wallet = TransactionRecord(walletAmountCents: 100, merchant: "M", card: nil,
                                   date: day, accountID: "")
    #expect(wallet.day == CalendarDate(day, in: .current))
}

@Test func aLegacyRowOnAUTCMidnightReadsInThePhonesZone() {
    // review on #50: an earlier build stored a Wallet tap's moment, which
    // can fall on a UTC midnight; with no mark it still reads where the
    // phone is, so 00:00Z on 12 Oct is the 11th in Los Angeles
    let wallet = TransactionRecord(walletAmountCents: 100, merchant: "M", card: nil,
                                   date: day, accountID: "")
    wallet.date = Date(timeIntervalSince1970: 1_791_763_200)   // 2026-10-12T00:00Z
    wallet.dateIsDay = nil
    let la = TimeZone(identifier: "America/Los_Angeles")!
    #expect(CalendarDate(stored: wallet.date, isDay: wallet.dateIsDay == true,
                         legacyZone: la).iso == "2026-10-11")
    wallet.day = CalendarDate(year: 2026, month: 10, day: 12)!
    #expect(wallet.dateIsDay == true)
    #expect(wallet.day.iso == "2026-10-12")
}

// MARK: - PatternRecord

private func samplePattern() -> AlertPattern {
    AlertPattern(id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
                 name: "DBS card", direction: .outflow, accountID: "a1",
                 defaultCategoryID: "c1", sourceMatch: "DBS", priority: 2, autoApprove: true,
                 segments: [.literal("You spent "), .amount, .literal(" at "), .payee,
                            .literal(" on "), .date(.ddMmm), .ignore, .accountHint],
                 sampleText: "You spent SGD 4.80 at KOPITIAM on 27 Oct card 1234")
}

@Test func patternRoundTripsThroughTheRecord() {
    let p = samplePattern()
    #expect(PatternRecord(p).pattern == p)
}

@Test(arguments: AlertPattern.DateFormat.allCases)
func everyDateFormatRoundTrips(format: AlertPattern.DateFormat) {
    var p = samplePattern()
    p.segments = [.amount, .date(format)]
    #expect(PatternRecord(p).pattern?.segments == p.segments)
}

@Test func inflowDirectionRoundTrips() {
    var p = samplePattern()
    p.direction = .inflow
    #expect(PatternRecord(p).pattern?.direction == .inflow)
}

/// Saved patterns are this JSON on disk. Renaming a `Segment` or
/// `DateFormat` case would make every saved pattern decode to nothing, so
/// the shape is frozen here; a change must come with a migration.
@Test func savedSegmentJSONStillDecodes() throws {
    let saved = #"""
    [{"literal":{"_0":"You spent "}},{"amount":{}},{"payee":{}},{"ignore":{}},
     {"accountHint":{}},{"date":{"_0":"dmy4Slash"}},{"date":{"_0":"dmy2Slash"}},
     {"date":{"_0":"ddMmmYyyy"}},{"date":{"_0":"ddMmmYy"}},{"date":{"_0":"ddMmm"}},
     {"date":{"_0":"mmmDdYyyy"}},{"date":{"_0":"dmy4Dash"}},{"date":{"_0":"dmy2Dash"}},
     {"date":{"_0":"ymdDash"}},{"date":{"_0":"dmSlash"}}]
    """#
    let segments = try JSONDecoder().decode([AlertPattern.Segment].self, from: Data(saved.utf8))
    #expect(segments == [.literal("You spent "), .amount, .payee, .ignore, .accountHint]
        + AlertPattern.DateFormat.allCases.map { .date($0) })
}

@Test func unreadableSegmentsReadAsNoPattern() {
    let record = PatternRecord(samplePattern())
    record.segmentsData = Data("not json".utf8)
    #expect(record.pattern == nil)
    record.segmentsData = (try? JSONEncoder().encode(samplePattern().segments)) ?? Data()
    record.direction = "sideways"
    #expect(record.pattern == nil)
}

// MARK: - Suppression

@Test func suppressionAddsChecksAndClears() {
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    #expect(Suppression.signatures(in: defaults).isEmpty)
    #expect(!Suppression.contains("you spent # at", in: defaults))
    Suppression.add("you spent # at", in: defaults)
    Suppression.add("otp # for", in: defaults)
    #expect(Suppression.contains("you spent # at", in: defaults))
    #expect(Suppression.signatures(in: defaults) == ["you spent # at", "otp # for"])
    Suppression.clear(in: defaults)
    #expect(Suppression.signatures(in: defaults).isEmpty)
}

@Test func suppressionStoresEachSignatureOnce() {
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    Suppression.add("you spent # at", in: defaults)
    Suppression.add("you spent # at", in: defaults)
    #expect(defaults.stringArray(forKey: Suppression.key) == ["you spent # at"])
}

@Test func suppressionWithNoDefaultsIsEmptyAndHarmless() {
    Suppression.add("anything", in: nil)
    #expect(!Suppression.contains("anything", in: nil))
    Suppression.clear(in: nil)
}

@Test func suppressionRemovesOneShapeAndKeepsTheRest() {
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    Suppression.add("you spent # at", in: defaults)
    Suppression.add("otp # for", in: defaults)
    Suppression.remove("you spent # at", in: defaults)
    #expect(Suppression.signatures(in: defaults) == ["otp # for"])
    Suppression.remove("never added", in: defaults)
    #expect(Suppression.signatures(in: defaults) == ["otp # for"])
}

// MARK: - Cents

@Test(arguments: [
    (4.53, 453), (4.505, 451), (0.1, 10), (1200, 120000), (-4.505, -451), (0.005, 1),
] as [(Double, Int)])
func doubleAmountsRoundAsTyped(amount: Double, cents: Int) {
    #expect(Cents.from(amount) == cents)
}

@Test(arguments: [Double.nan, .infinity, -.infinity, 1e12, -1e12, 1e300])
func doubleAmountsPastCountingAreRefused(amount: Double) {
    #expect(Cents.from(amount) == nil)
}

@Test func decimalAmountsPastTheCeilingAreRefused() throws {
    // the alert capture reads any run of digits; a wrapped figure must never
    // reach the queue (the audit saw -200376420520688664 come back)
    let huge = Decimal(string: "99999999999999999999")!
    #expect(Cents.from(huge) == nil)
    #expect(Cents.from(Decimal.nan) == nil)
    #expect(Cents.from(Decimal(string: "999999999999.99")!) == 99_999_999_999_999)
    #expect(throws: Cents.OutOfRange.self) {
        _ = try TransactionRecord(from: parsed("99999999999999999999"), accountID: "a1",
                                  categoryID: nil)
    }
}

@Test func anUnmatchedAlertKeepsTheDayItArrivedOn() {
    // review on #50: just after midnight on 30 Oct in Kiritimati is still
    // 28 Oct in Los Angeles; the day saved at arrival does not move with
    // the phone, so a printed "30 Oct" keeps this year
    let arrived = Date(timeIntervalSince1970: 1_793_268_000)   // 2026-10-29T10:00Z
    let kiritimati = TimeZone(identifier: "Pacific/Kiritimati")!
    let u = UnmatchedRecord(text: "t", source: nil, receivedAt: arrived, reasons: "",
                            zone: kiritimati)
    #expect(u.arrivedOn.iso == "2026-10-30")
    u.receivedDay = nil
    #expect(u.arrivedOn == CalendarDate(arrived, in: .current))
}

// MARK: - LocalData ("Delete all data")

@Test func localDataCountsWhatDeleteAllWouldRemoveThenRemovesIt() throws {
    let context = ModelContext(try Store.memory())
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    context.insert(PatternRecord(samplePattern()))
    for state in ["pending", "approved", "syncing", "failed", "synced", "duplicate", "kopifund"] {
        let r = try TransactionRecord(from: parsed("1"), accountID: "a1", categoryID: nil)
        r.state = state
        context.insert(r)
    }
    context.insert(UnmatchedRecord(text: "t", source: nil, receivedAt: day, reasons: "P: no"))
    context.insert(MetadataRecord(key: "accounts|p1", payload: Data(), fetchedAt: day))
    try context.save()
    Suppression.add("You paid # to #", in: defaults)

    #expect(LocalData.counts(in: context)
            == LocalData.Counts(transactions: 7, unconfirmed: 4, patterns: 1, unmatched: 1))

    LocalData.deleteAll(in: context, suppressions: defaults)
    #expect(LocalData.counts(in: context)
            == LocalData.Counts(transactions: 0, unconfirmed: 0, patterns: 0, unmatched: 0))
    #expect(try context.fetchCount(FetchDescriptor<MetadataRecord>()) == 0)
    #expect(Suppression.signatures(in: defaults).isEmpty)
}

// MARK: - empty Wallet captures (G24, found on the owner's phone)

@MainActor
private func walletRow(_ context: ModelContext, cents: Int = 0, payee: String = "",
                       state: String = "pending") -> TransactionRecord {
    let row = TransactionRecord(walletAmountCents: cents, merchant: payee, card: nil,
                                date: day, accountID: "a1", currency: "SGD")
    row.state = state
    context.insert(row)
    return row
}

@Test func missingDetailsNamesWhatIsMissing() {
    let empty = TransactionRecord(walletAmountCents: 0, merchant: " ", card: nil,
                                  date: day, accountID: "")
    #expect(empty.missingDetails?.contains("amount or payee") == true)
    #expect(empty.missingDetailsNote == "no amount or payee")
    empty.payee = "Card check"
    #expect(empty.missingDetailsNote == "no amount")
    empty.amountCents = 264
    #expect(empty.missingDetails == nil && empty.missingDetailsNote == nil)
    empty.payee = ""
    #expect(empty.missingDetailsNote == "no payee")
}

@Test @MainActor func cleanupRemovesOnlyEmptyWalletCapturesNeverSent() throws {
    let context = ModelContext(try Store.memory())
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    let doomed = [walletRow(context), walletRow(context, payee: "  "),
                  walletRow(context, state: "approved")]
    // everything below stays
    let real = walletRow(context, cents: 264, payee: "Cheers")
    let named = walletRow(context, payee: "SimplyGo")             // amount 0, has a payee
    let noPayee = walletRow(context, cents: 450)                  // has an amount
    let synced = walletRow(context, state: "synced")
    let sent = walletRow(context)
    sent.importID = "YNAB:0:x"
    sent.syncAttempts = 1
    let onHub = walletRow(context)
    onHub.kopifundID = "kph_1"
    let sms = TransactionRecord(walletAmountCents: 0, merchant: "", card: nil,
                                date: day, accountID: "a1")
    sms.source = "sms"
    sms.alertText = "SGD 0.00 card check"
    context.insert(sms)
    // a memo or category the owner set keeps the row
    let noted = walletRow(context)
    noted.memo = "bus to work"
    let filed = walletRow(context)
    filed.categoryID = "cat-transport"
    // an empty row another row is parked against stays, so no pointer dangles
    let kept = walletRow(context)
    let parked = walletRow(context, cents: 100, payee: "Kopi", state: "duplicate")
    parked.duplicateOf = kept.id
    try context.save()

    #expect(try EmptyWalletCleanup.runOnce(in: context, defaults: defaults) == doomed.count)
    let left = Set(try context.fetch(FetchDescriptor<TransactionRecord>()).map(\.id))
    #expect(left == Set([real, named, noPayee, synced, sent, onHub, sms, noted, filed, kept, parked].map(\.id)))
    #expect(defaults.bool(forKey: EmptyWalletCleanup.doneKey))

    // once only: a later empty row is left for the owner to see
    _ = walletRow(context)
    try context.save()
    #expect(try EmptyWalletCleanup.runOnce(in: context, defaults: defaults) == nil)
    #expect(try context.fetchCount(FetchDescriptor<TransactionRecord>()) == left.count + 1)
}

@Test func unmatchedChipReadsLikeTheInboxRow() {
    // the phone showed "SGD2.20" in Unmatched beside "-SGD 0.00" in the Inbox
    #expect(Cents.chipText(printed: "SGD2.20") == "SGD 2.20")
    #expect(Cents.chipText(printed: "SGD 2.2") == "SGD 2.20")
    #expect(Cents.chipText(printed: "S$4.50") == "SGD 4.50")
    #expect(Cents.chipText(printed: "1,234.50") == "1234.50")
    #expect(Cents.chipText(printed: " USD 12 ") == "USD 12.00")
    // the same figure the Inbox prints, without the direction's sign
    let row = TransactionRecord(walletAmountCents: 220, merchant: "Cheers", card: nil,
                                date: day, accountID: "", currency: "SGD")
    #expect(row.amountText == "-" + Cents.chipText(printed: "SGD2.20"))
}
