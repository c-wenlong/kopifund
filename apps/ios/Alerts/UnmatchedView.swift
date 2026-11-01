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

    var body: some View {
        NavigationStack {
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
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        Button("Always ignore") {
                            Suppression.add(AlertShape.signature(of: r.text))
                            dismiss(r, andSay: "Ignored. Alerts like this won't queue")
                        }
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
                    AlertView(record: r) { notice = $0 }
                }
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
                if let chip {
                    Text(chip)
                        .font(.caption.weight(.semibold)).monospacedDigit()
                        .padding(.horizontal, 7).padding(.vertical, 3)
                        .background(Color.green.opacity(0.15))
                        .foregroundStyle(.green)
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
