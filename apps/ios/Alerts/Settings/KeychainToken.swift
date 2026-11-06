import Foundation
import Security

/// The token lives in the Keychain, never in the store or a file. "Delete all
/// data" wipes the store but leaves the token; "Revoke token" drops the
/// Keychain item so the app forgets who it was.
enum KeychainToken {
    private static let service = "kopifund.alerts.ynab"
    private static let account = "pat"

    static var value: String? {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: account,
                                kSecReturnData as String: true]
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func set(_ token: String?) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        if let token, !token.isEmpty {
            SecItemDelete(q as CFDictionary)
            var add = q
            add[kSecValueData as String] = Data(token.utf8)
            SecItemAdd(add as CFDictionary, nil)
        } else {
            SecItemDelete(q as CFDictionary)
        }
    }
}
