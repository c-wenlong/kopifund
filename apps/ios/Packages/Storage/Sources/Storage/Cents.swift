import Foundation
import Matching

/// G24 audit: the one way an amount becomes the integer cents a row stores.
/// A parsed alert hands over a Decimal, Shortcuts and the edit field a
/// Double; both round half away from zero and refuse anything past
/// `ceiling` (or a NaN) instead of wrapping. YNABClient's `Milliunits` turns
/// the cents into the wire's milliunits.
public enum Cents {
    /// Largest magnitude accepted, in currency units: far above any spend,
    /// far below where cents or milliunits would overflow an Int.
    public static let ceiling: Decimal = 1_000_000_000_000

    public struct OutOfRange: Error, Equatable, LocalizedError {
        public let amount: String
        public var errorDescription: String? {
            "Kopifund can't count \(amount) as an amount."
        }
    }

    public static func from(_ amount: Decimal) -> Int? {
        guard !amount.isNaN, abs(amount) < ceiling else { return nil }
        var scaled = amount * 100
        var rounded = Decimal()
        NSDecimalRound(&rounded, &scaled, 0, .plain)
        return Int(exactly: NSDecimalNumber(decimal: rounded).int64Value)
    }

    public static func from(_ amount: Double) -> Int? {
        guard amount.isFinite else { return nil }
        // the shortest decimal that reads back as this Double ("4.53", not
        // 4.5300000000000002), so a typed figure rounds as it was typed
        guard let exact = Decimal(string: "\(amount)", locale: Locale(identifier: "en_US_POSIX"))
        else { return nil }
        return from(exact)
    }
}

/// The one way the app prints integer cents: whole units, a point and two
/// digits, worked out in integers so no figure passes through a float.
extension Cents {
    /// "4.50" for 450; "-4.50" for -450.
    public static func text(_ cents: Int) -> String {
        (cents < 0 ? "-" : "") + digits(cents.magnitude)
    }

    /// A signed figure with its currency: "-SGD 4.50" for -450, "+SGD 4.50"
    /// for 450 (zero reads "+").
    public static func signedText(_ cents: Int, currency: String) -> String {
        "\(cents < 0 ? "-" : "+")\(currency) \(digits(cents.magnitude))"
    }

    /// The Unmatched list's money flag, in the lists' own form: "SGD2.20"
    /// as an alert printed it reads "SGD 2.20", like the Inbox row's figure
    /// without its sign (an unmatched alert has no direction yet). A bare
    /// number keeps no code; text the amount grammar cannot read is shown
    /// as printed.
    public static func chipText(printed: String) -> String {
        let raw = printed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let amount = Amounts.read(raw), let cents = from(amount.magnitude) else { return raw }
        return (amount.currency.map { "\($0) " } ?? "") + digits(UInt(cents.magnitude))
    }

    private static func digits(_ cents: UInt) -> String {
        let fraction = cents % 100
        return "\(cents / 100).\(fraction < 10 ? "0" : "")\(fraction)"
    }
}

extension TransactionRecord {
    /// The row's figure as the lists print it: an outflow reads "-SGD 4.50",
    /// an inflow "+SGD 4.50"; the stored cents are the size, the direction
    /// the sign.
    public var amountText: String {
        "\(direction == "outflow" ? "-" : "+")\(currency) \(Cents.text(amountCents))"
    }
}
