import SwiftUI
import SwiftData
import Storage
import Matching
import Intents

/// G24: the step Unmatched used to skip. An unmatched row opens as a
/// readable alert first (boilerplate dimmed, the detector's guess said out
/// loud) and every row gets a way out, not just a way in.
struct AlertView: View {
    let record: UnmatchedRecord
    /// Fires back to the list so a toast can say what happened.
    var onFinished: (String) -> Void = { _ in }

    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @State private var showDismissChoices = false
    @State private var didSave = false

    private var lines: [String] { record.text.components(separatedBy: .newlines) }

    /// Detected once per record, off the main thread: inside `body` the token
    /// scan re-ran on every render and froze the open for seconds on a long
    /// alert. Nil until the scan lands, so the label simply appears late.
    @State private var found: (amount: Bool, date: Bool, payee: Bool)?
    @State private var title: String?

    nonisolated private static func detectedFields(in text: String)
        -> (amount: Bool, date: Bool, payee: Bool, title: String) {
        let tokens = Tokenizer.tokenize(text)
        let c = Candidates.detect(in: text, tokens: tokens)
        return (c.contains { $0.field == .amount && $0.confident },
                c.contains { $0.field == .date && $0.confident },
                c.contains { $0.field == .payee && $0.confident },
                AlertText.title(in: text, tokens: tokens, candidates: c))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title ?? AlertText.meaningfulLine(in: record.text))
                        .font(.headline)
                    Text("\(record.receivedAt.formatted(date: .abbreviated, time: .shortened))"
                         + (record.source.map { " · via \($0)" } ?? ""))
                        .font(.caption).foregroundStyle(.secondary)
                }

                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        if line.trimmingCharacters(in: .whitespaces).isEmpty {
                            Spacer().frame(height: 4)
                        } else if AlertText.isBoilerplate(line) {
                            Text(line).font(.caption).foregroundStyle(.tertiary)
                        } else {
                            Text(line).font(.callout).foregroundStyle(.secondary)
                        }
                    }
                }

                if !record.reasons.isEmpty {
                    DisclosureGroup("Why nothing matched") {
                        Text(record.reasons)
                            .font(.caption).foregroundStyle(.tertiary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.top, 4)
                    }
                    .font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding()
        }
        .navigationTitle("Alert")
        .safeAreaInset(edge: .bottom) {
            VStack(spacing: 10) {
                if let f = found {
                    Label(
                        f.amount || f.date
                            ? "Looks like a payment alert: "
                              + [f.amount ? "amount" : nil, f.date ? "date" : nil,
                                 f.payee ? "payee" : nil].compactMap { $0 }.joined(separator: ", ")
                              + " found"
                            : "No amount or date shapes found",
                        systemImage: f.amount || f.date ? "sparkle.magnifyingglass" : "questionmark.circle")
                        .font(.footnote).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                NavigationLink {
                    PatternBuilderView(sampleText: record.text, source: record.source) {
                        didSave = true
                        onFinished(reIngestAndRemove())
                    }
                } label: {
                    Text("Teach a pattern")
                        .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 4)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)

                Button("Not an alert", role: .cancel) { showDismissChoices = true }
                    .controlSize(.large)
                    .frame(maxWidth: .infinity)
            }
            .padding()
            .background(.regularMaterial)
        }
        .confirmationDialog("Dismiss this alert?", isPresented: $showDismissChoices,
                            titleVisibility: .visible) {
            Button("Just this alert") { dismissRecord(andSay: "Alert dismissed") }
            Button("Always ignore alerts like this") {
                Suppression.add(AlertShape.signature(of: record.text))
                dismissRecord(andSay: "Ignored. Alerts like this won't queue")
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("\"Always ignore\" drops future alerts with the same shape, from any channel.")
        }
        .onChange(of: didSave) { dismiss() }
        .task {
            // the record's text is read on the actor, the scan itself runs
            // on a detached task so the push animation never waits on it
            let text = record.text
            let f = await Task.detached(priority: .userInitiated) {
                Self.detectedFields(in: text)
            }.value
            found = (f.amount, f.date, f.payee)
            title = f.title
        }
    }

    private func dismissRecord(andSay toast: String) {
        context.delete(record)
        try? context.save()
        onFinished(toast)
        dismiss()
    }

    /// The saved pattern should also capture this alert: re-ingest so the
    /// spend reaches the queue, then drop the unmatched row.
    private func reIngestAndRemove() -> String {
        var outcome = "unmatched"
        if let container = try? Store.container() {
            outcome = (try? Ingest.alert(text: record.text, source: record.source,
                                         receivedAt: record.receivedAt,
                                         container: container)) ?? "unmatched"
        }
        context.delete(record)
        try? context.save()
        switch outcome {
        case "queued": return "Pattern saved. The alert is in your Inbox"
        case "ignored": return "Pattern saved, but this shape is ignored"
        default: return "Pattern saved. The alert still doesn't match"
        }
    }
}
