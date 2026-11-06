import Foundation
import Storage
import SwiftData

/// G24: the Inbox's paste box. A bank alert is several lines ("To:" on one,
/// the merchant on the next), and a pattern finds a field by the words
/// around it, line breaks included, so the pasted text reaches intake
/// exactly as it was copied: no trimming, no joined lines.
public enum PastedAlert {
    /// Nothing to ingest: an empty box, or only spaces and line breaks.
    public static func isBlank(_ text: String) -> Bool {
        text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The box's text through the shared ingest path, source "paste".
    /// Answers the ingest outcome ("queued", "unmatched", "ignored").
    @MainActor
    public static func ingest(_ text: String, receivedAt: Date = Date(),
                              container: ModelContainer,
                              defaults: UserDefaults? = AppGroup.defaults) async throws -> String {
        guard !isBlank(text) else { return "ignored" }
        return try await Ingest.alert(text: text, source: "paste", receivedAt: receivedAt,
                                      container: container, defaults: defaults)
    }
}
