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
                PasteEditor(text: $text)
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
