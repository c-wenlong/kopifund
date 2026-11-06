import Foundation

/// G24: where an approved row goes, said the same way by the builder's Save,
/// the Inbox's Approve and the row itself. Kopifund is the app's own
/// destination and YNAB an optional extra: a linked phone sends every
/// approved row to the hub, and to YNAB too when a token and a plan are set;
/// with neither, the row waits on the phone.
public enum Destination {

    /// What a row waiting with nowhere to go says, everywhere.
    public static let waiting = "Waiting for Kopifund: link this phone in Settings"

    /// Where approved rows go: the line under Save and Approve.
    public static func summary(linked: Bool, ynab: Bool) -> String {
        switch (linked, ynab) {
        case (true, true): return "Approved rows go to Kopifund and YNAB"
        case (true, false): return "Approved rows go to Kopifund"
        case (false, true): return "Approved rows go to YNAB"
        case (false, false): return waiting
        }
    }

    /// The note an approved row shows in the Inbox while YNAB is not set up,
    /// nil when the usual state note stands (YNAB set up: its states say
    /// where the row is).
    public static func note(state: String, linked: Bool, ynab: Bool,
                            sentToKopifund: Bool) -> String? {
        guard !ynab, state == "approved" else { return nil }
        if !linked { return waiting }
        return sentToKopifund ? "in Kopifund" : "sending to Kopifund"
    }
}
