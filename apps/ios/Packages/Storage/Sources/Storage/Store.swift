import Foundation
import SwiftData
import Matching

/// G24: one container for the app, the App Intents and the share extension.
/// `group.` lets the extension write rows the app later reads.
public enum AppGroup {
    public static let id = "group.com.kopifund.alerts"

    /// The group's shared defaults; nil only when the suite name is refused.
    public static var defaults: UserDefaults? { UserDefaults(suiteName: id) }
}

/// Why the shared store could not open. Each case reads as a sentence the app
/// can show on its error screen instead of crashing at launch.
public enum StoreError: Error, Equatable, LocalizedError {
    /// The build carries no entitlement for the App Group (an unsigned or
    /// re-signed build), so iOS hands back no shared folder for the store.
    case appGroupUnavailable(String)

    public var errorDescription: String? {
        switch self {
        case .appGroupUnavailable(let id):
            return "This build cannot reach the shared App Group \(id), so it has nowhere "
                + "to keep alerts. Sign the app and its share extension with that App Group "
                + "and install it again."
        }
    }
}

@Model
public final class PatternRecord {
    @Attribute(.unique) public var id: UUID
    public var name: String
    public var direction: String        // "outflow" | "inflow"
    public var accountID: String
    public var defaultCategoryID: String?
    public var sourceMatch: String?
    public var priority: Int
    public var autoApprove: Bool
    public var segmentsData: Data       // Codable [AlertPattern.Segment]
    public var sampleText: String

    public init(_ p: AlertPattern) {
        id = p.id
        name = p.name
        direction = p.direction.rawValue
        accountID = p.accountID
        defaultCategoryID = p.defaultCategoryID
        sourceMatch = p.sourceMatch
        priority = p.priority
        autoApprove = p.autoApprove
        segmentsData = (try? JSONEncoder().encode(p.segments)) ?? Data()
        sampleText = p.sampleText
    }

    public var pattern: AlertPattern? {
        guard let dir = AlertPattern.Direction(rawValue: direction),
              let segs = try? JSONDecoder().decode([AlertPattern.Segment].self, from: segmentsData)
        else { return nil }
        return AlertPattern(id: id, name: name, direction: dir, accountID: accountID,
                            defaultCategoryID: defaultCategoryID, sourceMatch: sourceMatch,
                            priority: priority, autoApprove: autoApprove,
                            segments: segs, sampleText: sampleText)
    }
}

/// A matched alert sitting in the review queue, then synced to YNAB.
@Model
public final class TransactionRecord {
    @Attribute(.unique) public var id: UUID
    public var payee: String
    public var amountCents: Int         // money is integer cents, never a float
    /// The ISO code the source printed ("SGD", "USD"), or the plan's when
    /// it printed none and the plan's is known; empty when neither is.
    public var currency: String
    /// A calendar day, written as `CalendarDate.storedInstant`. Read and
    /// write it through `day`, never through a time zone.
    public var date: Date
    /// True when `date` holds a `CalendarDate.storedInstant`; nil on a row
    /// an earlier build wrote, whose `date` reads in the phone's zone.
    public var dateIsDay: Bool?
    public var direction: String
    public var accountID: String
    public var categoryID: String?
    public var payeeID: String?
    public var memo: String
    public var accountHint: String?
    public var alertText: String
    public var source: String?
    public var receivedAt: Date
    public var patternID: UUID?
    public var state: String            // pending | approved | syncing | synced | failed | duplicate
    /// When another row reads as the same spend, this points at the row kept
    /// for posting and `state` holds "duplicate" so the row never syncs.
    public var duplicateOf: UUID?
    public var ynabID: String?
    public var importID: String?
    public var syncError: String?
    public var syncAttempts: Int
    /// G25: the row's pending_txn key on the hub once a linked phone tried to
    /// post it (nil until the first attempt): its being set marks only that a
    /// send started. `kopifundSent` holds the fields the hub last accepted
    /// ("date|cents|currency|payee"), so an interrupted send or a later edit
    /// reads as unsent and the next flush posts again under the same key.
    /// `kopifundError` carries the last try's refusal for the row to show.
    public var kopifundID: String?
    public var kopifundSent: String?
    public var kopifundError: String?

    public init(from t: ParsedTransaction, accountID: String, categoryID: String?) {
        id = UUID()
        payee = t.payee
        var cents = Decimal()
        var scaled = t.amount * 100
        NSDecimalRound(&cents, &scaled, 0, .plain)
        amountCents = NSDecimalNumber(decimal: cents).intValue
        currency = t.currency ?? ""
        date = t.date.storedInstant
        dateIsDay = true
        direction = t.direction.rawValue
        self.accountID = accountID
        self.categoryID = categoryID
        payeeID = nil
        memo = ""
        accountHint = t.accountHint
        alertText = t.alertText
        source = t.source
        receivedAt = t.receivedAt
        patternID = t.patternID
        state = "pending"
        duplicateOf = nil
        syncAttempts = 0
        kopifundID = nil
        kopifundSent = nil
        kopifundError = nil
    }

    /// `currency` nil means the Wallet record did not say; the row then
    /// stores "" rather than guessing one.
    public init(walletAmountCents: Int, merchant: String, card: String?, date: Date,
                accountID: String, currency: String? = nil) {
        id = UUID()
        payee = merchant
        amountCents = walletAmountCents
        self.currency = currency ?? ""
        // the tap's day where the phone was when it happened
        self.date = CalendarDate(date, in: .current).storedInstant
        dateIsDay = true
        direction = "outflow"
        self.accountID = accountID
        memo = ""
        accountHint = card
        alertText = ""
        source = "wallet"
        receivedAt = date
        state = "pending"
        duplicateOf = nil
        syncAttempts = 0
        kopifundID = nil
        kopifundSent = nil
        kopifundError = nil
    }
}

/// An alert no pattern took, parked until the user builds a pattern from it.
@Model
public final class UnmatchedRecord {
    @Attribute(.unique) public var id: UUID
    public var text: String
    public var source: String?
    public var receivedAt: Date
    /// Debug trail: "PatternName: reason" lines in priority order.
    public var reasons: String
    /// G25: the record's statement rows already cover this spend, so the row
    /// is noise rather than something to teach a pattern for. Optional so
    /// old stores migrate on their own.
    public var inKopifund: Bool?
    /// The calendar day the alert arrived where the phone was then, as a
    /// `CalendarDate.storedInstant`: a yearless date it prints reads against
    /// this day even after the phone has moved zones. Nil on rows an earlier
    /// build wrote. Read it through `arrivedOn`.
    public var receivedDay: Date?

    public init(text: String, source: String?, receivedAt: Date,
                arrivedOn: CalendarDate? = nil, reasons: String, zone: TimeZone = .current) {
        id = UUID()
        self.text = text
        self.source = source
        self.receivedAt = receivedAt
        self.reasons = reasons
        inKopifund = false
        receivedDay = (arrivedOn ?? CalendarDate(receivedAt, in: zone)).storedInstant
    }

    /// The day the alert arrived: the saved one, or for an older row the
    /// day `receivedAt` falls on in the phone's zone now.
    public var arrivedOn: CalendarDate {
        receivedDay.map { CalendarDate(stored: $0, isDay: true) }
            ?? CalendarDate(receivedAt, in: .current)
    }
}

/// Cached YNAB pickers, one row per kind+plan so the app starts without a call.
@Model
public final class MetadataRecord {
    @Attribute(.unique) public var key: String   // "accounts|<planID>" etc.
    public var payload: Data                     // Codable array
    public var fetchedAt: Date

    public init(key: String, payload: Data, fetchedAt: Date) {
        self.key = key
        self.payload = payload
        self.fetchedAt = fetchedAt
    }
}

public enum Store {
    static let schema = Schema([PatternRecord.self, TransactionRecord.self,
                                UnmatchedRecord.self, MetadataRecord.self])

    /// The shared container both the app and the extension write through.
    /// `group.` URLs land under the App Group, so the intents' writes show up
    /// on the app's next launch or model-context merge. Throws
    /// `StoreError.appGroupUnavailable` when the build has no App Group
    /// entitlement, so the app can say so rather than crash at launch.
    public static func container() throws -> ModelContainer {
        try container(groupDirectory: FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: AppGroup.id))
    }

    /// `container()` with the App Group lookup passed in, so a test can
    /// stand in for a build that has no entitlement.
    static func container(groupDirectory: URL?) throws -> ModelContainer {
        guard let groupDirectory else { throw StoreError.appGroupUnavailable(AppGroup.id) }
        return try container(at: groupDirectory.appendingPathComponent("alerts.store"))
    }

    /// The store file at `url`. No versioned schema yet: a field added as an
    /// optional (as `duplicateOf` was) migrates on its own, which the tests
    /// pin; a field that is neither optional nor defaulted needs a
    /// `SchemaMigrationPlan` first.
    static func container(at url: URL) throws -> ModelContainer {
        let config = ModelConfiguration(schema: schema, url: url, cloudKitDatabase: .none)
        return try ModelContainer(for: schema, configurations: [config])
    }

    /// Unit tests and previews get a throwaway in-memory store.
    public static func memory() throws -> ModelContainer {
        try ModelContainer(for: schema,
                           configurations: [ModelConfiguration(isStoredInMemoryOnly: true)])
    }
}

extension TransactionRecord {
    /// The row's calendar day: the one value the inbox shows, dedup
    /// compares and the sync posts. Setting it marks the row's `date` as a
    /// zone-free day.
    public var day: CalendarDate {
        get { CalendarDate(stored: date, isDay: dateIsDay == true) }
        set {
            date = newValue.storedInstant
            dateIsDay = true
        }
    }
}
