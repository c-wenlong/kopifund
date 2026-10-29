import Foundation

/// G24 audit: a row posts to YNAB only in the currency its plan keeps. The
/// app never converts: a foreign spend waits in review until the owner
/// enters the plan-currency figure, so a JPY 1,200 tap can never land as
/// S$1,200.

/// The ISO code of the YNAB plan the app syncs to, as YNAB last reported it.
/// The sync writes it into the App Group's defaults so the intents and the
/// share extension can hold a foreign alert the moment it arrives.
public enum PlanCurrency {
    static let codeKey = "planCurrency"
    static let planKey = "planCurrencyPlanID"

    /// The saved code, nil until a sync has read it. Pass `planID` to get it
    /// only when it was read for that plan, so a switched plan never borrows
    /// the previous plan's currency.
    public static func saved(planID: String? = nil,
                             in defaults: UserDefaults? = AppGroup.defaults) -> String? {
        guard let code = defaults?.string(forKey: codeKey), !code.isEmpty else { return nil }
        if let planID, defaults?.string(forKey: planKey) != planID { return nil }
        return code
    }

    public static func remember(_ code: String, planID: String,
                                in defaults: UserDefaults? = AppGroup.defaults) {
        defaults?.set(Currency.normalized(code) ?? code, forKey: codeKey)
        defaults?.set(planID, forKey: planKey)
    }
}

public enum Currency {
    /// An ISO 4217 code in capitals, or nil for anything else ("", "S$",
    /// "dollars"). Shortcuts input is typed by hand, so case and spaces are
    /// forgiven.
    public static func normalized(_ raw: String?) -> String? {
        guard let s = raw?.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(),
              s.count == 3, s.allSatisfy({ $0.isASCII && $0.isLetter })
        else { return nil }
        return s
    }

    /// Why a row in `currency` must not post to a plan kept in
    /// `planCurrency`, or nil when it may. An empty `currency` means the
    /// source printed none, which reads as the plan's own.
    public static func holdReason(currency: String, planCurrency: String) -> String? {
        guard !currency.isEmpty, currency.uppercased() != planCurrency.uppercased() else { return nil }
        return "This spend is in \(currency) but the YNAB plan keeps \(planCurrency). "
            + "Kopifund does not convert currencies: enter the amount in \(planCurrency) "
            + "and post it in \(planCurrency), or delete the row."
    }

    /// Two captures name the same currency, an unstated one standing in for
    /// whatever the other printed. Dedup uses this, so a Wallet tap with no
    /// currency still pairs with the bank's SMS for the same charge.
    public static func compatible(_ a: String, _ b: String) -> Bool {
        a.isEmpty || b.isEmpty || a.uppercased() == b.uppercased()
    }
}

extension TransactionRecord {
    /// The last gate before a post. True when the row may go to YNAB; an
    /// unstated currency is then filled with the plan's, so the row shows
    /// what it posted as. False leaves the row unsent with the reason in
    /// `syncError`:
    /// - a foreign currency goes back to "pending" for review, never retried
    ///   on its own;
    /// - a stated currency with no known plan currency keeps its state, so
    ///   the next sync checks it again.
    public func checkCurrency(against planCurrency: String?) -> Bool {
        if currency.isEmpty {
            if let planCurrency { currency = planCurrency }
            return true
        }
        guard let planCurrency else {
            syncError = "Couldn't read the YNAB plan's currency to check this \(currency) "
                + "spend against. It is checked again on the next sync."
            return false
        }
        if let reason = Currency.holdReason(currency: currency, planCurrency: planCurrency) {
            state = "pending"
            syncError = reason
            return false
        }
        return true
    }

    /// The owner entered the plan-currency figure for a held foreign spend:
    /// the row now counts in the plan's currency and joins the queue.
    public func postInPlanCurrency(_ planCurrency: String) {
        currency = planCurrency
        syncError = nil
        state = "approved"
    }
}
