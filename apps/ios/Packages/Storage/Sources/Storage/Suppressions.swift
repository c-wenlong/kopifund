import Foundation
import Matching

/// G24: "always ignore alerts like this", remembered across the app, the
/// share extension and the intents; all three share the App Group's
/// defaults. The set holds `AlertShape.signature`s, so a suppressed template
/// drops no matter which channel carries it in.
public enum Suppression {
    static let key = "suppressedAlertShapes"

    /// `defaults` is the App Group's everywhere but in tests, which pass a
    /// throwaway suite so they never read or write the real set.
    public static func signatures(in defaults: UserDefaults? = AppGroup.defaults) -> Set<String> {
        Set(defaults?.stringArray(forKey: key) ?? [])
    }

    public static func add(_ signature: String, in defaults: UserDefaults? = AppGroup.defaults) {
        var all = defaults?.stringArray(forKey: key) ?? []
        guard !all.contains(signature) else { return }
        all.append(signature)
        defaults?.set(all, forKey: key)
    }

    public static func contains(_ signature: String,
                                in defaults: UserDefaults? = AppGroup.defaults) -> Bool {
        signatures(in: defaults).contains(signature)
    }

    /// "Delete all data" clears these too; an accidental ignore must be
    /// undoable somewhere.
    public static func clear(in defaults: UserDefaults? = AppGroup.defaults) {
        defaults?.removeObject(forKey: key)
    }
}
