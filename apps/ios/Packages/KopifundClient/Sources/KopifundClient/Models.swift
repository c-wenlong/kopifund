import Foundation

/// G25: the hub's answers, decoded. Snake_case maps onto camelCase via each
/// type's CodingKeys. A transaction's `date` stays the statement's calendar
/// text (YYYY-MM-DD): it is never a Date, so no timezone can shift it.
/// Money is integer cents in `amount`/`amountSGD`, never a Double.

/// POST /api/pair/redeem's answer: the credentials the phone keeps. `token`
/// is the device bearer (kfd_…); only the Keychain ever holds it.
public struct PairedDevice: Codable, Equatable, Sendable {
    public let deviceID: String
    public let name: String
    public let token: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case name, token
    }
}

/// GET /api/profile: each account by display name, bank and masked number.
public struct ProfileAccount: Codable, Equatable, Sendable, Identifiable {
    public let name: String
    public let bank: String
    public let number: String        // masked: "•• 4821", or ""
    public let statements: Int
    public let transactions: Int
    public let first: String?        // first statement month
    public let last: String?         // last statement month

    public var id: String { name }
}

public struct Profile: Codable, Equatable, Sendable {
    public let accounts: [ProfileAccount]
    public let transactions: Int
}

/// One ledger row on GET /api/transactions.
public struct TransactionRow: Codable, Equatable, Sendable, Identifiable {
    public let key: String
    public let date: String          // the statement's calendar date, YYYY-MM-DD
    public let amount: Int           // cents, in `currency`
    public let amountSGD: Int?       // cents in SGD; null when the block printed no FX rate
    public let currency: String
    public let account: String       // short label, never a full number
    public let bank: String
    public let merchant: String      // the printed line, numbers masked
    public let description: String   // what the row is, when it has no payee
    public let payee: String
    public let category: String
    public let categoryGroup: String
    public let flow: String          // External / Internal / …
    public let trip: String
    public let flags: [FlagReason]   // scam-check reasons, {code, text} on the wire

    public var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, date, amount, currency, account, bank, merchant
        case amountSGD = "amount_sgd"
        case description, payee, category, flow, trip, flags
        case categoryGroup = "category_group"
    }
}

/// One scam-check reason on a flagged row.
public struct FlagReason: Codable, Equatable, Sendable {
    public let code: String
    public let text: String

    public init(code: String, text: String) {
        self.code = code
        self.text = text
    }
}

/// An account the filter row offers: `value` is the account id the query
/// takes, `label` and `bank` what the list shows.
public struct TransactionAccount: Codable, Equatable, Sendable, Identifiable {
    public let value: String
    public let label: String
    public let bank: String

    public var id: String { value }
}

public struct TransactionsPage: Codable, Equatable, Sendable {
    public let rows: [TransactionRow]
    public let total: Int
    public let sumSGD: Int           // cents
    public let inSGD: Int            // cents
    public let outSGD: Int           // cents
    public let months: [String]      // every month the ledger holds, newest first
    public let categories: [String]  // the filter's choices, "(none)" first
    public let accounts: [TransactionAccount]

    enum CodingKeys: String, CodingKey {
        case rows, total, months, categories, accounts
        case sumSGD = "sum_sgd"
        case inSGD = "in_sgd"
        case outSGD = "out_sgd"
    }
}

/// GET /api/categories: the buckets in order, each with its categories.
public struct CategoryEntry: Codable, Equatable, Sendable, Identifiable {
    public let id: Int
    public let name: String
    public let needClass: String?
    public let costType: String?
    public let group: String?

    enum CodingKeys: String, CodingKey {
        case id, name, group
        case needClass = "need_class"
        case costType = "cost_type"
    }
}

public struct CategoryGroup: Codable, Equatable, Sendable, Identifiable {
    public let id: Int
    public let name: String
    public let kind: String
    public let icon: String
    public let colour: String?
    public let shown: String
    public let fixed: Bool
    public let categories: [CategoryEntry]
}

public struct CategoriesState: Codable, Equatable, Sendable {
    public let groups: [CategoryGroup]
    public let months: Int
}

/// POST /api/link/transactions: one approved alert row for pending_txn.
/// `importID` is the idempotency key — a retry upserts, never doubles.
public struct PendingPost: Codable, Equatable, Sendable {
    public let importID: String
    public let date: String            // YYYY-MM-DD
    public let amountMinor: Int        // cents, spending negative
    public let currency: String
    public let payee: String

    public init(importID: String, date: String, amountMinor: Int,
                currency: String, payee: String) {
        self.importID = importID
        self.date = date
        self.amountMinor = amountMinor
        self.currency = currency
        self.payee = payee
    }

    enum CodingKeys: String, CodingKey {
        case importID = "import_id"
        case date
        case amountMinor = "amount_minor"
        case currency, payee
    }
}

/// POST /api/link/transactions answers how many rows were new or corrected
/// and how many waiting rows a statement already on record confirmed.
public struct LinkPosted: Codable, Equatable, Sendable {
    public let accepted: Int
    public let matched: Int
}
