import Foundation
import KopifundClient
import UIKit

/// G25: the phone's one link to the hub. `device` is what the Keychain's
/// LinkStore holds; views read it through this shared observable so a pair
/// in Settings, or a kopifund:// URL the Camera hands over, lights the
/// Kopifund tab at once.
@Observable @MainActor final class LinkState {
    static let shared = LinkState()

    private(set) var device: LinkedDevice?
    private(set) var pairing = false
    /// The last pair or test failure text; "" clears on the next success.
    var problem = ""

    private init() { reload() }

    var isLinked: Bool { device != nil }

    func reload() {
        device = LinkStore.live.device
    }

    /// Bumped on unlink and every new attempt: a redeem that finishes after
    /// either must not resurrect a link the user just dropped or supersede a
    /// newer pair.
    private var epoch = 0

    /// Pair from a `kopifund://pair` payload (a paste, the in-app scanner, or
    /// a URL the Camera hands over). The hub's answer lands in the Keychain;
    /// the phone's own name is the device's label in the registry.
    @discardableResult
    func pair(text: String, name: String = UIDevice.current.name) async -> Bool {
        guard let code = LinkCode(text: text) else {
            problem = "That is not a pairing code: it starts kopifund://pair"
            return false
        }
        epoch += 1
        let attempt = epoch
        pairing = true
        defer { pairing = false }
        do {
            let paired = try await KopifundClient.pair(code: code, name: name)
            guard attempt == epoch else { return false }
            guard LinkStore.live.link(base: code.base, paired) else {
                problem = "Paired, but the Keychain would not keep it"
                return false
            }
            reload()
            problem = ""
            return true
        } catch {
            problem = describe(error)
            return false
        }
    }

    /// /api/link/check on the linked hub, with this phone's bearer:
    /// "reachable" only when the hub answers this paired device, so a phone
    /// revoked in Settings reads as unpaired, not reachable.
    func test() async -> String {
        guard let client else { return "not linked" }
        do { return try await client.check() ? "reachable" : "no answer" }
        catch let e as KopifundError where e.kind == .unpaired { return "unpaired: pair again" }
        catch { return describe(error) }
    }

    /// The phone forgets it was linked; the hub's registry row keeps its
    /// bearer until Settings revokes it.
    func unlink() {
        epoch += 1
        LinkStore.live.unlink()
        reload()
    }

    var client: KopifundClient? { LinkStore.live.client() }

    private func describe(_ error: Error) -> String {
        (error as? KopifundError)?.message ?? error.localizedDescription
    }
}
