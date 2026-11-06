import SwiftUI
import SwiftData
import Storage
import Intents

/// G24: the app's side of `Ingest.rematchUnmatched`. A saved or edited
/// pattern, and Unmatched's "Try patterns again", re-read every waiting
/// alert through the shared container, so the Unmatched and Inbox lists
/// update at once; an auto-approved match is sent now.
@MainActor
enum PatternRematch {
    /// `keeping` is the Unmatched row an open alert page shows: its spend
    /// queues, and the page's list deletes the row once the page is gone.
    static func run(context: ModelContext, keeping: UUID? = nil) async -> RematchResult {
        guard let container = try? Store.container(),
              let result = try? await Ingest.rematchUnmatched(container: container, keeping: keeping)
        else { return RematchResult() }
        if result.queuedApproved { Task { await SyncService.flush(context: context) } }
        return result
    }
}

/// A short message at the top of a list that goes away by itself.
struct ToastOverlay: ViewModifier {
    @Binding var notice: String

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .top) {
                if !notice.isEmpty {
                    Text(notice)
                        .font(.caption.weight(.medium))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 14).padding(.vertical, 9)
                        .background(.thinMaterial, in: Capsule())
                        .shadow(radius: 6, y: 2)
                        .padding(.top, 4)
                        .padding(.horizontal)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .accessibilityIdentifier("toast")
                        .task(id: notice) {
                            try? await Task.sleep(for: .seconds(3))
                            withAnimation { notice = "" }
                        }
                }
            }
            .animation(.default, value: notice.isEmpty)
    }
}

extension View {
    func toast(_ notice: Binding<String>) -> some View { modifier(ToastOverlay(notice: notice)) }
}
