import Foundation
import Security

/// G25: what pairing leaves behind. `base` is the tailnet HTTPS name, `token`
/// the device bearer (kfd_…) the hub now answers for. The whole thing is one
/// JSON blob in one Keychain item: the bearer never sits in UserDefaults,
/// a file, or the store.
public struct LinkedDevice: Codable, Equatable, Sendable {
    public let base: String
    public let token: String
    public let deviceID: String
    public let name: String

    public init(base: String, token: String, deviceID: String, name: String) {
        self.base = base
        self.token = token
        self.deviceID = deviceID
        self.name = name
    }

    enum CodingKeys: String, CodingKey {
        case base, token, name
        case deviceID = "device_id"
    }
}

/// The paired device, behind the two seams a test replaces: `read`/`write`
/// of one secret string. `live` maps them onto a generic-password Keychain
/// item, the same treatment KeychainToken gives the YNAB token; a paired
/// phone survives "Delete all data" and a reinstall only until "Unlink".
/// `write` answers whether the value landed, so a failed save is never
/// mistaken for a live link.
public struct LinkStore: Sendable {
    private let read: @Sendable () -> String?
    private let write: @Sendable (String?) -> Bool

    public init(read: @escaping @Sendable () -> String?,
                write: @escaping @Sendable (String?) -> Bool) {
        self.read = read
        self.write = write
    }

    /// The app's real store: service `kopifund.alerts.link`, account `device`,
    /// beside the YNAB token's `kopifund.alerts.ynab`.
    public static let live = LinkStore(service: "kopifund.alerts.link", account: "device")

    public init(service: String, account: String) {
        self.init(
            read: {
                let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                        kSecAttrService as String: service,
                                        kSecAttrAccount as String: account,
                                        kSecReturnData as String: true]
                var item: CFTypeRef?
                guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
                      let data = item as? Data else { return nil }
                return String(data: data, encoding: .utf8)
            },
            write: { value in
                let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                        kSecAttrService as String: service,
                                        kSecAttrAccount as String: account]
                guard let value, !value.isEmpty else {
                    let status = SecItemDelete(q as CFDictionary)
                    return status == errSecSuccess || status == errSecItemNotFound
                }
                // Update in place first: a failed rewrite keeps the old
                // credentials, where a delete-then-add would lose them.
                let update = [kSecValueData as String: Data(value.utf8)]
                var status = SecItemUpdate(q as CFDictionary, update as CFDictionary)
                if status == errSecItemNotFound {
                    var add = q
                    add[kSecValueData as String] = Data(value.utf8)
                    status = SecItemAdd(add as CFDictionary, nil)
                }
                return status == errSecSuccess
            })
    }

    /// The paired device, or nil when this phone was never linked (or the
    /// Keychain row went with a wiped phone). Corrupt JSON reads as nil, not
    /// a crash: the phone just shows unlinked again.
    public var device: LinkedDevice? {
        guard let raw = read() else { return nil }
        return try? JSONDecoder().decode(LinkedDevice.self, from: Data(raw.utf8))
    }

    public var base: URL? { device.flatMap { URL(string: $0.base) } }

    /// Keep what redeem answered. The hub's `name` is the phone's own label.
    /// Answers false when the Keychain would not take it: the phone is then
    /// not linked, whatever the hub said.
    @discardableResult
    public func link(base: URL, _ paired: PairedDevice) -> Bool {
        let d = LinkedDevice(base: base.absoluteString, token: paired.token,
                             deviceID: paired.deviceID, name: paired.name)
        guard let json = try? JSONEncoder().encode(d),
              let raw = String(data: json, encoding: .utf8) else { return false }
        return write(raw)
    }

    /// Drop the Keychain row: the phone forgets it was ever linked. The hub's
    /// registry row still answers until it is revoked in Settings.
    @discardableResult
    public func unlink() -> Bool { write(nil) }

    /// A client for the linked device, or nil while unlinked.
    public func client() -> KopifundClient? {
        guard let d = device, let base = URL(string: d.base) else { return nil }
        return KopifundClient(baseURL: base, bearer: d.token)
    }
}
