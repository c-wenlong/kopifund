import SwiftUI
import UIKit

/// The one paste box both paste screens share (the Inbox's sheet and the
/// Patterns tab's builder entry): a hint while empty, a multi-line editor
/// that keeps the alert's line breaks, the card look, and "Paste from
/// clipboard". Each screen keeps its own actions around it.
struct PasteEditor: View {
    @Binding var text: String

    static let hint = "Paste alert text"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ZStack(alignment: .topLeading) {
                // TextEditor shows nothing when empty; a hint is needed
                if text.isEmpty {
                    Text(Self.hint)
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 5).padding(.vertical, 8)
                        .allowsHitTesting(false)
                }
                // hidden background: on iOS 26+ the editor's own opaque
                // fill would cover the hint
                TextEditor(text: $text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .scrollContentBackground(.hidden)
                    .accessibilityIdentifier("pasteAlertText")
            }
            .padding(8)
            .frame(minHeight: 220)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 12))

            Button {
                if let s = UIPasteboard.general.string { text = s }
            } label: {
                Label("Paste from clipboard", systemImage: "doc.on.clipboard")
            }
            // always enabled: a clipboard filled in another app while the
            // screen stays open redraws nothing here, so a check read at
            // render could stay stale; an empty one changes nothing
        }
    }
}
