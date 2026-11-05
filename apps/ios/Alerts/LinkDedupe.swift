import Foundation
import SwiftData
import Storage
import Matching
import KopifundClient

/// G25: the record's own copy of a spend an alert captured. While the phone
/// is linked, a pending or approved alert row whose amount, currency, day
/// and payee a loaded statement row already covers moves to "kopifund" so
/// SyncService never offers it to YNAB, and an unmatched alert that reads as
/// covered carries `inKopifund`. Both queues then hold only what the record
/// lacks.
@MainActor
enum LinkDedupe {
    private static let dayFmt: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    private static let monthFmt: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM"
        return f
    }()

    static func monthKey(_ d: Date) -> String { monthFmt.string(from: d) }

    /// The record's rows for `months` (YYYY-MM), as the spend tuples Dedup
    /// compares. Pages each month until its total is reached; a hub that
    /// stops answering midway yields the rows seen so far, and an empty
    /// window marks nothing.
    static func recordSpends(client: KopifundClient,
                             months: Set<String>) async -> [Dedup.RecordSpend] {
        var spends: [Dedup.RecordSpend] = []
        for month in months.sorted() {
            var offset = 0
            while true {
                let page: TransactionsPage
                do {
                    page = try await client.transactions(month: month, limit: 500, offset: offset)
                } catch { return spends }
                offset += page.rows.count
                spends += page.rows.compactMap { r in
                    guard let d = dayFmt.date(from: r.date) else { return nil }
                    return Dedup.RecordSpend(amountCents: r.amount, currency: r.currency,
                                             date: d,
                                             payee: r.payee.isEmpty ? r.description : r.payee,
                                             raw: r.merchant)
                }
                if page.rows.isEmpty || offset >= page.total { break }
            }
        }
        return spends
    }

    /// Mark what the record already covers: pending/approved rows take state
    /// "kopifund" (out of the YNAB flush), unmatched alerts take `inKopifund`.
    /// Each statement row covers at most one alert row. Answers the count
    /// newly marked; an unreachable hub or an empty window marks nothing.
    @discardableResult
    static func sweep(context: ModelContext) async -> Int {
        guard let client = LinkStore.live.client() else { return 0 }
        let rows = (try? context.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.state == "pending" || $0.state == "approved" }))) ?? []
        let unmatched = ((try? context.fetch(FetchDescriptor<UnmatchedRecord>())) ?? [])
            .filter { $0.inKopifund != true }
        guard !rows.isEmpty || !unmatched.isEmpty else { return 0 }
        var months = Set(rows.map { monthKey($0.date) })
        var guesses: [(row: UnmatchedRecord, cents: Int, currency: String,
                       date: Date, payee: String)] = []
        for u in unmatched {
            if let g = guess(u.text) {
                months.insert(monthKey(g.date))
                guesses.append((u, g.cents, g.currency, g.date, g.payee))
            }
        }
        guard !months.isEmpty else { return 0 }
        let spends = await recordSpends(client: client, months: months)
        guard !spends.isEmpty else { return 0 }
        var taken: Set<Int> = []
        var marked = 0
        // a row whose source printed no currency counts in the plan's, as
        // the sync posts it; with no plan read it covers nothing
        let planCurrency = PlanCurrency.saved(
            planID: UserDefaults.standard.string(forKey: "planID") ?? "") ?? ""
        for row in rows {
            let currency = row.currency.isEmpty ? planCurrency : row.currency
            if let i = Dedup.coveredBy(amountCents: row.amountCents, currency: currency,
                                       direction: row.direction, date: row.date,
                                       payee: row.payee, in: spends, taken: taken) {
                taken.insert(i)
                row.state = "kopifund"
                marked += 1
            }
        }
        // detected guesses carry no direction, and alerts are spend mail
        // almost always: outflow only, or a refund-shaped row could pull a
        // false mark. A miss just leaves the alert for review.
        for g in guesses {
            if let i = Dedup.coveredBy(amountCents: g.cents, currency: g.currency,
                                       direction: "outflow", date: g.date, payee: g.payee,
                                       in: spends, taken: taken) {
                taken.insert(i)
                g.row.inKopifund = true
                marked += 1
            }
        }
        if marked > 0 { try? context.save() }
        return marked
    }

    /// An unmatched alert's confident guesses: the amount, the date and the
    /// payee the detector would pre-tag, parsed into values. nil unless the
    /// money and the day both read clean — half a guess never marks a row.
    private static func guess(_ text: String) -> (cents: Int, currency: String,
                                                  date: Date, payee: String)? {
        let tokens = Tokenizer.tokenize(text)
        func span(_ c: Candidate) -> String {
            guard let lo = c.tokenIndexes.min(), let hi = c.tokenIndexes.max() else { return "" }
            return String(text[tokens[lo].range.lowerBound..<tokens[hi].range.upperBound])
                .trimmingCharacters(in: .whitespaces)
        }
        let cands = Candidates.detect(in: text, tokens: tokens)
        guard let amountC = cands.first(where: { $0.field == .amount && $0.confident }),
              let split = Amounts.split(span(amountC)),
              let dateC = cands.first(where: { $0.field == .date && $0.confident }),
              let format = Dates.detectFormat(span(dateC)),
              let date = Dates.parse(span(dateC), as: format)
        else { return nil }
        var cents = Decimal()
        var scaled = split.amount * 100
        NSDecimalRound(&cents, &scaled, 0, .plain)
        let payee = cands.filter { $0.field == .payee && $0.confident }
            .sorted { $0.rank < $1.rank }.first.map { span($0) } ?? ""
        return (abs(NSDecimalNumber(decimal: cents).intValue), split.currency ?? "SGD",
                date, payee)
    }
}
