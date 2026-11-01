// Written by app-icons/apply.py from app-icons/sets.json: run `make app-icon`, do not edit.

/// The icon sets in Assets.xcassets: `AppIcon` is `primary`, and every set is
/// also `AppIcon-<name>`, an alternate icon, with `IconPicker-<name>` its 60 pt tile.
enum AppIcons {
    struct IconSet: Sendable {
        let name: String
        let label: String
        let about: String
    }

    static let primary = "sticker"
    static let sets: [IconSet] = [
        IconSet(name: "sticker", label: "Sticker", about: "Kopi in a tied bag with a coin, on yellow"),
        IconSet(name: "iso", label: "Iso", about: "A cup on a stack of coins, on green"),
        IconSet(name: "riso", label: "Riso", about: "A printed cup before a pink sun"),
    ]
}
