import SwiftUI
import SwiftData
import Storage
import Matching

/// G24: triage before teaching. Rows lead with the line that says what the
/// alert is, a chip flags the likely money figure, and noise leaves the queue
/// with a swipe instead of piling up. Reading the alert is its own step now:
/// a row opens AlertView, and only "Teach a pattern" reaches the builder.
struct UnmatchedView: View {
    @Query(sort: \UnmatchedRecord.receivedAt, order: .reverse) private var rows: [UnmatchedRecord]
    @Environment(\.modelContext) private var context
    @State private var notice = ""
    @State private var path: [UUID] = []
    // "always ignore" silences a whole template, so a swipe only asks
    @State private var confirmIgnore: UnmatchedRecord?
    // a row the pushed AlertView finished with: deleted once the list is
    // back on screen, never while a pushed view still shows it
    @State private var pendingRemoval: UUID?

    var body: some View {
        NavigationStack(path: $path) {
            List {
                ForEach(rows) { r in
                    NavigationLink(value: r.id) {
                        UnmatchedRow(r: r)
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button("Not an alert", role: .destructive) {
                            dismiss(r, andSay: "Alert dismissed")
                        }
                    }
                    .swipeActions(edge: .leading, allowsFullSwipe: false) {
                        Button("Always ignore") { confirmIgnore = r }
                            .tint(.gray)
                    }
                }
                if rows.isEmpty {
                    ContentUnavailableView("Nothing unmatched",
                        systemImage: "checkmark",
                        description: Text("Alerts that match no pattern wait here."))
                }
            }
            .navigationTitle("Unmatched")
            .task { await LinkDedupe.sweep(context: context) }
            .navigationDestination(for: UUID.self) { id in
                if let r = rows.first(where: { $0.id == id }) {
                    AlertView(record: r) { finished(id, andSay: $0) }
                }
            }
            .onAppear(perform: removePending)
            .confirmationDialog("Always ignore alerts like this?",
                                isPresented: Binding(get: { confirmIgnore != nil },
                                                     set: { if !$0 { confirmIgnore = nil } }),
                                titleVisibility: .visible, presenting: confirmIgnore) { r in
                Button("Always ignore") {
                    Suppression.add(AlertShape.signature(of: r.text))
                    dismiss(r, andSay: "Ignored. Undo it under Patterns")
                }
                Button("Cancel", role: .cancel) {}
            } message: { _ in
                Text("Future alerts with the same shape are dropped, from any channel. Patterns lists them to undo.")
            }
            .overlay(alignment: .top) {
                if !notice.isEmpty {
                    Text(notice)
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 14).padding(.vertical, 9)
                        .background(.thinMaterial, in: Capsule())
                        .shadow(radius: 6, y: 2)
                        .padding(.top, 4)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .task(id: notice) {
                            try? await Task.sleep(for: .seconds(3))
                            withAnimation { notice = "" }
                        }
                }
            }
            .animation(.default, value: notice.isEmpty)
        }
    }

    /// AlertView is done with its row: pop back to the list first, then
    /// delete. Deleting the model a pushed view shows blanked that view (its
    /// destination resolved to nothing) or crashed it on the next render.
    private func finished(_ id: UUID, andSay toast: String) {
        pendingRemoval = id
        notice = toast
        path.removeAll()
    }

    private func removePending() {
        guard let id = pendingRemoval else { return }
        pendingRemoval = nil
        if let r = rows.first(where: { $0.id == id }) {
            withAnimation {
                context.delete(r)
                try? context.save()
            }
        }
    }

    private func dismiss(_ r: UnmatchedRecord, andSay toast: String) {
        withAnimation {
            context.delete(r)
            try? context.save()
            notice = toast
        }
    }
}

/// One unmatched row. Its own view so the money guess is detected once per
/// row and off the main thread, instead of inside every list re-render.
private struct UnmatchedRow: View {
    let r: UnmatchedRecord
    @State private var chip: String?
    @State private var title: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .top) {
                Text(title ?? AlertText.meaningfulLine(in: r.text))
                    .font(.body).lineLimit(2)
                Spacer()
                // neutral, like the builder's READS line: an unmatched alert
                // has no direction yet, and green reads as money coming in
                // on what is usually a payment
                if let chip {
                    Text(chip)
                        .font(.caption.weight(.semibold)).monospacedDigit()
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .background(Color(.tertiarySystemFill))
                        .foregroundStyle(.primary)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
            Text(r.receivedAt.formatted(date: .abbreviated, time: .shortened)
                 + (r.source.map { " · \($0)" } ?? "")
                 + (r.inKopifund == true ? " · in kopifund" : ""))
                .font(.caption).foregroundStyle(.secondary)
        }
        .task {
            let text = r.text
            let found = await Task.detached(priority: .userInitiated) {
                let tokens = Tokenizer.tokenize(text)
                return (chip: Self.amountChip(text, tokens: tokens),
                        title: AlertText.title(in: text, tokens: tokens))
            }.value
            chip = found.chip
            title = found.title
        }
    }

    /// The detector's best money guess, shown as the row's flag that this is
    /// likely spend mail rather than deploy or sign-in noise.
    nonisolated private static func amountChip(_ text: String, tokens: [Token]) -> String? {
        guard let c = Candidates.amounts(in: text, tokens: tokens)
            .first(where: { $0.confident }),
              let lo = c.tokenIndexes.min(), let hi = c.tokenIndexes.max()
        else { return nil }
        return text[tokens[lo].range.lowerBound..<tokens[hi].range.upperBound]
            .trimmingCharacters(in: .whitespaces)
    }
}
