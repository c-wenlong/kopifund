import Foundation
import Observation
import Storage

/// G24: what the app knows about where approved rows go right now, for
/// `Destination`'s wording. YNAB counts as set up only with both a token and
/// a plan, the same test the sync makes before it sends anything there.
/// Observed, like `LinkState`, so a token revoked or a plan picked in
/// Settings rewords the Inbox and the builder at once.
@Observable @MainActor final class DestinationState {
    static let shared = DestinationState()

    private(set) var hasToken = false
    private(set) var planID = ""

    private init() { reload() }

    /// Read the token's presence and the plan again; Settings calls this on
    /// every change, and the app on each return to the foreground.
    func reload() {
        hasToken = !(KeychainToken.value ?? "").isEmpty
        planID = UserDefaults.standard.string(forKey: "planID") ?? ""
    }

    var ynab: Bool { hasToken && !planID.isEmpty }

    var summary: String {
        Destination.summary(linked: LinkState.shared.isLinked, ynab: ynab)
    }
}
