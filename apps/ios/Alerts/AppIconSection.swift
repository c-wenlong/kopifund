import SwiftUI
import UIKit

/// The phone's own app icon, one of the sets in app-icons/ (SPEC.md, "App
/// icon"). AppIcons.swift lists them; a pick switches to the set's
/// AppIcon-<name>, and the default goes back to AppIcon with nil.
struct AppIconSection: View {
    @State private var current = AppIcons.primary
    @State private var problem = ""

    var body: some View {
        Section {
            ForEach(AppIcons.sets, id: \.name) { set in
                Button {
                    Task { await choose(set.name) }
                } label: {
                    HStack(spacing: 12) {
                        Image("IconPicker-\(set.name)")
                            .resizable()
                            .frame(width: 44, height: 44)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(set.label).foregroundStyle(.primary)
                            Text(set.about).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if current == set.name {
                            Image(systemName: "checkmark").foregroundStyle(.tint)
                        }
                    }
                }
                .accessibilityAddTraits(current == set.name ? .isSelected : [])
            }
            if !problem.isEmpty {
                Text(problem).font(.caption).foregroundStyle(.red)
            }
        } header: {
            Text("App icon")
        }
        .onAppear { current = Self.shown() }
    }

    /// The set on the Home Screen now: the alternate's name, else the default.
    private static func shown() -> String {
        let alternate = UIApplication.shared.alternateIconName ?? ""
        let name = String(alternate.dropFirst("AppIcon-".count))
        return alternate.hasPrefix("AppIcon-") && AppIcons.sets.contains { $0.name == name }
            ? name : AppIcons.primary
    }

    private func choose(_ name: String) async {
        guard name != current, UIApplication.shared.supportsAlternateIcons else { return }
        do {
            try await UIApplication.shared.setAlternateIconName(
                name == AppIcons.primary ? nil : "AppIcon-\(name)")
            current = name
            problem = ""
        } catch {
            problem = "Couldn't change the icon: \(error.localizedDescription)"
        }
    }
}
