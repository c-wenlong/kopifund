import Foundation
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
enum LegacyStore {
    @Model final class TransactionRecord {
        @Attribute(.unique) var id: UUID
        var payee: String
        var amountCents: Int
        var currency: String
        var date: Date
        var direction: String
        var accountID: String
        var categoryID: String?
        var payeeID: String?
        var memo: String
        var accountHint: String?
        var alertText: String
        var source: String?
        var receivedAt: Date
        var patternID: UUID?
        var state: String
        var ynabID: String?
        var importID: String?
        var syncError: String?
        var syncAttempts: Int

        init(id: UUID, payee: String) {
            self.id = id
            self.payee = payee
            amountCents = 480
            currency = "SGD"
            date = day
            direction = "outflow"
            accountID = "a1"
            memo = ""
            alertText = "alert"
            receivedAt = day
            state = "pending"
            syncAttempts = 0
        }
    }
}

@Test func storeWrittenBeforeDuplicateOfStillOpens() throws {
    let folder = try scratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let url = folder.appendingPathComponent("alerts.store")
    let rowID = UUID()
    do {
        let schema = Schema([PatternRecord.self, LegacyStore.TransactionRecord.self,
                             UnmatchedRecord.self, MetadataRecord.self])
        let config = ModelConfiguration(schema: schema, url: url, cloudKitDatabase: .none)
        let context = ModelContext(try ModelContainer(for: schema, configurations: [config]))
        context.insert(LegacyStore.TransactionRecord(id: rowID, payee: "KOPITIAM"))
        try context.save()
    }
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
    let row = TransactionRecord(walletAmountCents: 4453, merchant: "Sgh - Digestive & Live",
                                card: "DBS Altitude", date: day, accountID: "a1")
    #expect(row.amountCents == 4453)
    #expect(row.payee == "Sgh - Digestive & Live")
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
