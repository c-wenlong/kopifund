import Foundation

/// G24: the API now says "plans" where it used to say "budgets". JSON
/// snake_case maps onto camelCase via each type's CodingKeys.

public struct Plan: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let deleted: Bool
}

public struct Account: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let type: String
    public let closed: Bool
    public let deleted: Bool
}

public struct Category: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let hidden: Bool
    public let deleted: Bool
}

public struct CategoryGroup: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let hidden: Bool
    public let deleted: Bool
    public let categories: [Category]
}

public struct Payee: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let deleted: Bool
}

/// A transaction headed for the review queue: uncleared, unapproved, with a
/// deterministic `import_id` so a retried post never duplicates.
public struct NewTransaction: Codable, Equatable, Sendable {
    public let accountID: String
    public let date: String          // yyyy-MM-dd
    public let amount: Int           // milliunits, negative for outflows
    public let payeeID: String?
    public let payeeName: String?
    public let categoryID: String?
    public let memo: String?
    public let cleared: String       // "uncleared"
    public let approved: Bool
    public let flagColor: String?
    public let importID: String

    enum CodingKeys: String, CodingKey {
        case accountID = "account_id"
        case date, amount
        case payeeID = "payee_id"
        case payeeName = "payee_name"
        case categoryID = "category_id"
        case memo, cleared, approved
        case flagColor = "flag_color"
        case importID = "import_id"
    }

    public init(accountID: String, date: String, amount: Int, payeeName: String,
                payeeID: String? = nil, categoryID: String? = nil, memo: String? = nil,
                cleared: String = "uncleared", approved: Bool = false,
                flagColor: String? = nil, importID: String) {
        self.accountID = accountID
        self.date = date
        self.amount = amount
        self.payeeID = payeeID
        self.payeeName = payeeName
        self.categoryID = categoryID
        self.memo = memo
        self.cleared = cleared
        self.approved = approved
        self.flagColor = flagColor
        self.importID = importID
    }
}

// MARK: - response envelopes
// Every list response is `{ "data": { "<key>": [...] } }`.

public struct PlansResponse: Codable, Sendable {
    public let data: Payload
    public struct Payload: Codable, Sendable { public let plans: [Plan] }
}

public struct AccountsResponse: Codable, Sendable {
    public let data: Payload
    public struct Payload: Codable, Sendable { public let accounts: [Account] }
}

public struct CategoriesResponse: Codable, Sendable {
    public let data: Payload
    public struct Payload: Codable, Sendable {
        public let categoryGroups: [CategoryGroup]
        enum CodingKeys: String, CodingKey { case categoryGroups = "category_groups" }
    }
}

public struct PayeesResponse: Codable, Sendable {
    public let data: Payload
    public struct Payload: Codable, Sendable { public let payees: [Payee] }
}

/// `{ "data": { "settings": { "currency_format": { "iso_code": "SGD", ... } } } }`
/// for GET /plans/{id}/settings. YNAB may send a null currency format.
public struct PlanSettingsResponse: Codable, Sendable {
    public let data: Payload
    public struct Payload: Codable, Sendable { public let settings: Settings }
    public struct Settings: Codable, Sendable {
        public let currencyFormat: CurrencyFormat?
        enum CodingKeys: String, CodingKey { case currencyFormat = "currency_format" }
    }
    public struct CurrencyFormat: Codable, Sendable {
        public let isoCode: String
        enum CodingKeys: String, CodingKey { case isoCode = "iso_code" }
    }
}

public struct TransactionResponse: Codable, Sendable {
    public let data: Payload
    public struct Payload: Codable, Sendable {
        public let transaction: Created
        public struct Created: Codable, Sendable { public let id: String }
    }
}

/// `{ "transaction": { ... } }` for POST /transactions.
public struct TransactionRequest: Codable, Sendable {
    public let transaction: NewTransaction
}
