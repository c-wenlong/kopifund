import Foundation
import Storage

/// G24: what the app knows about where approved rows go right now, for
/// `Destination`'s wording. YNAB counts as set up only with both a token and
/// a plan, the same test the sync makes before it sends anything there.
@MainActor enum DestinationState {
    static var ynab: Bool {
        !(KeychainToken.value ?? "").isEmpty
            && !(UserDefaults.standard.string(forKey: "planID") ?? "").isEmpty
    }

    static var hasToken: Bool { !(KeychainToken.value ?? "").isEmpty }

    static var summary: String {
        Destination.summary(linked: LinkState.shared.isLinked, ynab: ynab)
    }
}
