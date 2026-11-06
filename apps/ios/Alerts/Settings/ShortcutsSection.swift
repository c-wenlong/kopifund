import SwiftUI
import UIKit

/// G24: the bundled .shortcut files, each exported through the share sheet
/// so Shortcuts can add it (or AirDrop can carry it to another device).
struct ShortcutsSection: View {
    /// The file being shared; SettingsView presents the sheet.
    @Binding var exporting: ShortcutFile?
    @State private var shortcutError = ""

    struct ShortcutFile: Identifiable {
        let url: URL
        var id: String { url.lastPathComponent }
    }

    var body: some View {
        Section {
            ForEach(shortcutResources(), id: \.lastPathComponent) { url in
                Button {
                    do {
                        exporting = ShortcutFile(url: try stagedCopy(of: url))
                        shortcutError = ""
                    } catch {
                        shortcutError = "couldn't export "
                            + url.deletingPathExtension().lastPathComponent
                    }
                } label: {
                    Label(url.deletingPathExtension().lastPathComponent,
                          systemImage: "square.and.arrow.up")
                }
            }
            if !shortcutError.isEmpty {
                Text(shortcutError).font(.caption).foregroundStyle(.red)
            }
        } header: {
            Text("Shortcuts")
        } footer: {
            Text("Tap a shortcut, then choose Shortcuts to add it. "
               + "In Shortcuts, create an automation for the matching trigger "
               + "and set its action to Run Shortcut → the shortcut you added.")
                .font(.caption)
        }
    }

    private func shortcutResources() -> [URL] {
        (Bundle.main.urls(forResourcesWithExtension: "shortcut", subdirectory: nil) ?? [])
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// Sharing a temp copy, not the bundle resource, so UIActivityVC can
    /// hand the file to Shortcuts without bundle-path quirks.
    private func stagedCopy(of url: URL) throws -> URL {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent(url.lastPathComponent)
        try? FileManager.default.removeItem(at: tmp)
        try FileManager.default.copyItem(at: url, to: tmp)
        return tmp
    }
}

/// Wraps UIActivityViewController so a bundled .shortcut file can be handed
/// to Shortcuts ("Add Shortcut") or AirDropped to another device.
struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
