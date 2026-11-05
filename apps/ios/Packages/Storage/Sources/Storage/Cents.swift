import Foundation

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
