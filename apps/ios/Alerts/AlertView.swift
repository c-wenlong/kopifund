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
    /// Fires back to the list with the toast to show. The list pops this
    /// view and only then deletes the row: deleting the record here, under
    /// a pushed view that still shows it, blanked the page or crashed it.
    /// `remove` is false when the row stays in Unmatched.
    var onFinished: (_ toast: String, _ remove: Bool) -> Void = { _, _ in }

    @Environment(\.modelContext) private var context
    @State private var showDismissChoices = false

    private var lines: [String] { record.text.components(separatedBy: .newlines) }

    /// Detected once per record, off the main thread: inside `body` the token
    /// scan re-ran on every render and froze the open for seconds on a long
    /// alert. Nil until the scan lands, so the label simply appears late.
    @State private var found: (amount: Bool, date: Bool, payee: Bool)?
    @State private var title: String?
    /// G24: the saved pattern that came nearest, and what it could not read.
    @State private var closest: (pattern: AlertPattern, explained: ClosestPattern)?

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

                if let c = closest {
                    closestCard(c.pattern, c.explained)
                }

                NavigationLink {
                    PatternBuilderView(sampleText: record.text, source: record.source,
                                       arrivedOn: record.arrivedOn) {
                        // the list drops the row only after the re-read
                        // has queued it
                        Task {
                            let (toast, moved) = await rematch()
                            onFinished(toast, moved)
                        }
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
            Button("Just this alert") { onFinished(Triage.dismissed, true) }
            Button("Always ignore alerts like this") {
                onFinished(Triage.ignoreShape(of: record.text), true)
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("\"Always ignore\" drops future alerts with the same shape, from any channel. Patterns lists them to undo.")
        }
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
        .task { await findClosest() }
    }

    /// "Closest: Demo Wallet: payee not found after 'To:'", with the way
    /// to fix that pattern rather than teach a second one.
    private func closestCard(_ p: AlertPattern, _ c: ClosestPattern) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(c.summary, systemImage: "scope")
                .font(.footnote).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("closestPattern")
            NavigationLink {
                // the builder opens on the pattern's own sample, with this
                // alert in the live test so the fix shows as it is made
                PatternBuilderView(sampleText: p.sampleText, source: p.sourceMatch,
                                   editing: p, initialTestText: record.text,
                                   initialTestArrivedOn: record.arrivedOn) {
                    Task {
                        let (toast, moved) = await rematch()
                        onFinished(toast, moved)
                    }
                }
            } label: {
                Text("Edit that pattern").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
        }
    }

    /// Patterns are read on the actor, the explanation runs detached: it
    /// re-reads the alert once per field of every pattern.
    private func findClosest() async {
        let patterns = ((try? context.fetch(FetchDescriptor<PatternRecord>())) ?? []).compactMap(\.pattern)
        guard !patterns.isEmpty else { closest = nil; return }
        let text = record.text, source = record.source
        let at = record.receivedAt, day = record.arrivedOn
        let found = await Task.detached(priority: .userInitiated) {
            Matcher.closest(text: text, source: source, receivedAt: at, arrivedOn: day,
                            patterns: patterns)
        }.value
        closest = found.flatMap { c in patterns.first { $0.id == c.patternID }.map { ($0, c) } }
    }

    /// G24: the saved pattern should capture this alert, and every other
    /// waiting alert of its template: one pass over Unmatched queues them
    /// all. This row stays for the list to drop once this page is gone.
    /// Answers the toast and whether this alert left Unmatched.
    private func rematch() async -> (String, Bool) {
        let id = record.id
        let text = record.text
        let result: RematchResult
        switch await PatternRematch.run(context: context, keeping: id) {
        case .success(let r): result = r
        case .failure(let error): return ("Pattern saved. " + PatternRematch.failed(error), false)
        }
        let moved = result.moved.contains { $0.unmatchedID == id }
        let others = result.moved.count - (moved ? 1 : 0)
        if moved {
            return (others > 0 ? result.message() : "Pattern saved. The alert is in your Inbox", true)
        }
        if Suppression.contains(AlertShape.signature(of: text)) {
            return ("Pattern saved, but this shape is ignored", false)
        }
        if others > 0 { return ("Pattern saved. \(result.message()), but not this one", false) }
        return ("Pattern saved. The alert still doesn't match", false)
    }
}
