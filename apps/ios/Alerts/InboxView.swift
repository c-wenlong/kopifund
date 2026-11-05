import SwiftUI
import SwiftData
import Storage
import Matching
import Intents

/// The review queue: every match lands here first. Edit fields inline in the
/// detail sheet, approve, approve all, or delete.
struct InboxView: View {
    @Query(sort: \TransactionRecord.receivedAt, order: .reverse) private var rows: [TransactionRecord]
    @Environment(\.modelContext) private var context
    @State private var pasteText = ""
    @State private var showPaste = false
    @State private var accountNames: [String: String] = [:]

    private var pending: [TransactionRecord] { rows.filter { $0.state == "pending" } }
    private var duplicates: [TransactionRecord] { rows.filter { $0.state == "duplicate" } }
    private var recorded: [TransactionRecord] { rows.filter { $0.state == "kopifund" } }
    private var rest: [TransactionRecord] { rows.filter {
        $0.state != "pending" && $0.state != "duplicate" && $0.state != "kopifund" } }

    var body: some View {
        NavigationStack {
            List {
                if let problem = SyncService.shared.problem {
                    Section {
                        Label(problemText(problem), systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                    }
                }
                if !pending.isEmpty {
                    Section("Pending") {
                        ForEach(pending) { row in
                            NavigationLink(value: row.id) {
                                Row(row: row, accountName: accountNames[row.accountID])
                            }
                        }
                    }
                }
                if !duplicates.isEmpty {
                    Section("Possible duplicates") {
                        ForEach(duplicates) { row in
                            NavigationLink(value: row.id) {
                                Row(row: row, accountName: accountNames[row.accountID])
                            }
                        }
                    }
                }
                if !recorded.isEmpty {
                    // covered by a loaded statement row: parked like a
                    // duplicate, but the match is the record itself
                    Section("Already in kopifund") {
                        ForEach(recorded) { row in
                            NavigationLink(value: row.id) {
                                Row(row: row, accountName: accountNames[row.accountID])
                            }
                        }
                    }
                }
                if !rest.isEmpty {
                    Section("History") {
                        ForEach(rest) { row in
                            NavigationLink(value: row.id) {
                                Row(row: row, accountName: accountNames[row.accountID])
                            }
                        }
                    }
                }
                if rows.isEmpty {
                    ContentUnavailableView("No alerts yet",
                        systemImage: "tray",
                        description: Text("Alerts arrive through Shortcuts, Share, or Paste."))
                }
            }
            .navigationTitle("Inbox")
            .navigationDestination(for: UUID.self) { id in
                if let row = rows.first(where: { $0.id == id }) { TransactionDetailView(row: row) }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { HStack {
                    if !pending.isEmpty {
                        Button("Approve all") { approveAll() }
                    }
                    Button { showPaste = true } label: { Image(systemName: "doc.on.clipboard") }
                } }
            }
            .task {
                // sync is not started here: a view's task is cancelled when
                // the tab changes, and the app owns the runs (SyncService)
                accountNames = Dictionary(
                    uniqueKeysWithValues: YNABCache.accounts(in: context).map { ($0.id, $0.name) })
                // G25: statement coverage is checked on its own here, so a
                // pending row already in the record leaves the queue
                await LinkDedupe.sweep(context: context)
            }
            .alert("Ingest failed", isPresented: .constant(!ingestError.isEmpty)) {
                Button("OK") { ingestError = "" }
            } message: { Text(ingestError) }
            .alert("Paste alert text", isPresented: $showPaste) {
                TextField("Alert text", text: $pasteText, axis: .vertical)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Ingest") { ingestPaste() }
                Button("Cancel", role: .cancel) { pasteText = "" }
            }
        }
    }

    private func approveAll() {
        // an intent may have parked one of these as a duplicate since this
        // list loaded; approving the stale copy would write over that
        SyncService.refresh(context)
        for r in pending where r.state == "pending" { r.state = "approved" }
        try? context.save()
        Task { await SyncService.flush(context: context) }
    }

    @State private var ingestError = ""

    private func problemText(_ problem: SyncService.Problem) -> String {
        switch problem {
        case .unauthorized:
            return "YNAB rejected the token, so sync stopped. Check it in Settings, then tap Sync now."
        case .rateLimited(let until):
            return "YNAB is limiting requests, so sync is paused until "
                + until.formatted(date: .omitted, time: .shortened) + "."
        }
    }

    private func ingestPaste() {
        // the box can be reopened while the match runs: clear only the
        // draft this ingest took, never a newer one
        let text = pasteText
        Task {
            do {
                // the view's own container: a second one on the same file
                // would not merge its save into this list
                let outcome = try await Ingest.alert(text: text, source: "paste",
                                                     receivedAt: Date(),
                                                     container: context.container)
                if pasteText == text { pasteText = "" }
                // an auto-approving pattern queues the row approved
                if outcome == "queued" { await SyncService.flush(context: context) }
            } catch {
                // keep the text so a failed ingest does not lose the alert
                ingestError = error.localizedDescription
            }
        }
    }

    private struct Row: View {
        let row: TransactionRecord
        let accountName: String?
        var body: some View {
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(row.payee).font(.body)
                    Spacer()
                    Text(money(row)).font(.body.monospacedDigit())
                }
                HStack {
                    Text(caption(row)).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    // "pending" inside the Pending section says nothing; the
                    // caption only speaks where the state adds information
                    if row.state != "pending" {
                        Text(row.state).font(.caption).foregroundStyle(color)
                    }
                }
            }
        }
        private func caption(_ r: TransactionRecord) -> String {
            r.day.formatted(date: .abbreviated, time: .omitted)
                + (accountName.map { " · \($0)" } ?? "")
                + (r.source.map { " · \($0)" } ?? "")
        }
        private func money(_ r: TransactionRecord) -> String {
            let sign = r.direction == "outflow" ? "-" : "+"
            return "\(sign)\(r.currency) \(String(format: "%.2f", Double(r.amountCents) / 100))"
        }
        private var color: Color {
            switch row.state {
            case "synced": return .green
            case "failed": return .red
            case "approved", "syncing": return .orange
            default: return .secondary
            }
        }
    }
}
