import Foundation
import Testing
import Matching
@testable import Storage

// G24 audit: a row posts only in the plan's currency, and nothing invents
// one. The defaults suites are fresh names, never the App Group.

private func scratchDefaults() -> (UserDefaults, String) {
    let name = "kopifund.currency-tests.\(UUID().uuidString)"
    return (UserDefaults(suiteName: name)!, name)
}

private func row(_ currency: String?, state: String = "approved") -> TransactionRecord {
    let t = ParsedTransaction(payee: "SHOP", amount: 12, date: Date(), direction: .outflow,
                              accountHint: nil, currency: currency, patternID: UUID(),
                              patternName: "P", alertText: "a", source: "sms", receivedAt: Date())
    let r = TransactionRecord(from: t, accountID: "a1", categoryID: nil)
    r.state = state
    return r
}

@Test func anAlertWithNoPrintedCurrencyStoresNoneRatherThanSGD() {
    #expect(row(nil).currency == "")
    let wallet = TransactionRecord(walletAmountCents: 120000, merchant: "Lawson",
                                   card: nil, date: Date(), accountID: "a1")
    #expect(wallet.currency == "")
    let tagged = TransactionRecord(walletAmountCents: 1200, merchant: "Lawson",
                                   card: nil, date: Date(), accountID: "a1", currency: "JPY")
    #expect(tagged.currency == "JPY")
}

@Test func foreignRowGoesBackToReviewWithAReason() {
    let r = row("USD")
    #expect(!r.checkCurrency(against: "SGD"))
    #expect(r.state == "pending")
    #expect(r.syncError?.contains("USD") == true)
    #expect(r.syncError?.contains("SGD") == true)
    #expect(r.currency == "USD")   // never converted
}

@Test func sameCurrencyPostsWhateverTheCase() {
    let r = row("sgd")
    #expect(r.checkCurrency(against: "SGD"))
    #expect(r.state == "approved")
    #expect(r.syncError == nil)
}

@Test func unstatedCurrencyPostsAsThePlansAndSaysSo() {
    let r = row(nil)
    #expect(r.checkCurrency(against: "SGD"))
    #expect(r.currency == "SGD")
    #expect(r.state == "approved")
}

@Test func statedCurrencyWaitsWhenThePlansIsUnknown() {
    let r = row("USD", state: "failed")
    #expect(!r.checkCurrency(against: nil))
    #expect(r.state == "failed")          // left for the next sync to check
    #expect(r.syncError?.contains("next sync") == true)
    let unstated = row(nil)
    #expect(unstated.checkCurrency(against: nil))
    #expect(unstated.currency == "")
}

@Test func postingInThePlanCurrencyReleasesAHeldRow() {
    let r = row("JPY")
    _ = r.checkCurrency(against: "SGD")
    r.amountCents = 1100
    r.postInPlanCurrency("SGD")
    #expect(r.currency == "SGD")
    #expect(r.state == "approved")
    #expect(r.syncError == nil)
    #expect(r.checkCurrency(against: "SGD"))
}

@Test func planCurrencyIsRememberedPerPlan() {
    let (defaults, name) = scratchDefaults()
    defer { defaults.removePersistentDomain(forName: name) }
    #expect(PlanCurrency.saved(in: defaults) == nil)
    PlanCurrency.remember("sgd", planID: "p1", in: defaults)
    #expect(PlanCurrency.saved(in: defaults) == "SGD")
    #expect(PlanCurrency.saved(planID: "p1", in: defaults) == "SGD")
    // a switched plan never borrows the last plan's currency
    #expect(PlanCurrency.saved(planID: "p2", in: defaults) == nil)
    // picking another plan forgets the reading for intake too
    PlanCurrency.forget(in: defaults)
    #expect(PlanCurrency.saved(in: defaults) == nil)
}

@Test(arguments: [("SGD", "SGD"), (" jpy ", "JPY"), ("usd\n", "USD")])
func currencyCodesNormalise(raw: String, code: String) {
    #expect(Currency.normalized(raw) == code)
}

@Test(arguments: ["", "S$", "$", "dollars", "US", "12A", "ÉUR"])
func nonCodesAreRefused(raw: String) {
    #expect(Currency.normalized(raw) == nil)
}

@Test func compatibleCurrencies() {
    #expect(Currency.compatible("", "SGD"))
    #expect(Currency.compatible("SGD", ""))
    #expect(Currency.compatible("SGD", "sgd"))
    #expect(!Currency.compatible("SGD", "USD"))
}
