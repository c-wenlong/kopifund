import Foundation

/// G24: two captures of one spend arrive over different channels — a Wallet
/// tap and the bank's SMS for the same charge, an SMS and its eAlert email.
/// With no shared id between them the match is a judgement: the money and
/// the day must agree exactly, and the payees must read alike after
/// normalising. A hit parks the poorer row as a possible duplicate; it is
/// never deleted, because two real charges at the same amount on one day
/// exist and a silent merge would lose one.
public enum Dedup {
    /// Case and punctuation out, letters and digits kept: "SGH - DIGESTIVE &
    /// LIVE" from the bank and "Sgh - Digestive & Live" from Apple's Wallet
    /// record both become "sghdigestivelive".
    public static func normalizedPayee(_ s: String) -> String {
        String(s.lowercased().filter { $0.isLetter || $0.isNumber })
    }

    /// Equal after normalising, or one name inside the other ("AMAZON SG" in
    /// "Amazon.sg Marketplace"). The short side needs a few letters before a
    /// containment counts, or every Grab and 7-Eleven would match.
    public static func similarPayee(_ a: String, _ b: String) -> Bool {
        let (x, y) = (normalizedPayee(a), normalizedPayee(b))
        guard !x.isEmpty, !y.isEmpty else { return false }
        if x == y { return true }
        let (short, long) = x.count <= y.count ? (x, y) : (y, x)
        return short.count >= 4 && long.contains(short)
    }

    /// The spend is the same when the amount and currency agree, the
    /// calendar day agrees (the alert's printed day, or the day a Wallet tap
    /// happened where the phone was), and the payees read alike.
    public static func likelyDuplicate(amountCents: Int, currency: String,
                                       date: CalendarDate, payee: String,
                                       otherAmountCents: Int, otherCurrency: String,
                                       otherDate: CalendarDate, otherPayee: String) -> Bool {
        amountCents == otherAmountCents && currency == otherCurrency
            && date == otherDate
            && similarPayee(payee, otherPayee)
    }

    /// G25: one row of the kopifund record an alert can be covered by.
    /// `amountCents` is signed the record's way (spending negative); `payee`
    /// and `raw` are the two texts an alert's payee may agree with.
    public struct RecordSpend: Equatable, Sendable {
        public var amountCents: Int
        public var currency: String
        /// The day the statement printed, read from the record's ISO date.
        public var date: CalendarDate
        public var payee: String
        public var raw: String

        public init(amountCents: Int, currency: String, date: CalendarDate,
                    payee: String, raw: String) {
            self.amountCents = amountCents
            self.currency = currency
            self.date = date
            self.payee = payee
            self.raw = raw
        }
    }

    /// The index of the record spend that already covers this alert, or nil.
    /// The rules are the ledger's own: amount, currency and day agree, the
    /// flow's sign agrees (an outflow alert needs a negative record row), and
    /// the alert's payee reads like the row's payee or its raw merchant —
    /// two real purchases can share an amount and a day, so the payee has to
    /// agree. `taken` keeps the match one-to-one: a record spend already
    /// claimed by another alert row does not count again.
    public static func coveredBy(amountCents: Int, currency: String, direction: String,
                                 date: CalendarDate, payee: String,
                                 in record: [RecordSpend], taken: Set<Int>) -> Int? {
        record.indices.first { i in
            guard !taken.contains(i) else { return false }
            let r = record[i]
            let signMatches = direction == "outflow" ? r.amountCents < 0 : r.amountCents > 0
            guard signMatches, abs(r.amountCents) == amountCents, r.currency == currency,
                  r.date == date else { return false }
            return similarPayee(payee, r.payee) || similarPayee(payee, r.raw)
        }
    }
}
