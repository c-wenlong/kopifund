import SwiftUI
import UIKit
import Intents

/// G24: the Inbox's paste box. A sheet with a multi-line editor, where the
/// old alert's single-line field joined a pasted alert's lines, so a field
/// read by the line before it ("To:", then the merchant below) no longer
/// matched. "Paste from clipboard" takes the clipboard's text as it is,
/// line breaks and all.
struct PasteAlertSheet: View {
    @Binding var text: String
    let onIngest: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                ZStack(alignment: .topLeading) {
                    // TextEditor shows nothing when empty; a hint is needed
                    if text.isEmpty {
                        Text("Paste alert text")
                            .foregroundStyle(.tertiary)
                            .padding(.horizontal, 5).padding(.vertical, 8)
                            .allowsHitTesting(false)
                    }
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
                // always enabled: a clipboard filled in another app while
                // the sheet stays open redraws nothing here, so a check
                // read at render could stay stale; an empty one changes
                // nothing
                Spacer()
            }
            .padding()
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Paste alert")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        text = ""
                        dismiss()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Ingest") {
                        onIngest(text)
                        dismiss()
                    }
                    .disabled(PastedAlert.isBlank(text))
                }
            }
        }
    }
}
