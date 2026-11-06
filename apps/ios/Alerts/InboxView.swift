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

    // G24: a row with no amount or payee waits apart, so "Approve all"
    // never reaches it
    private var pending: [TransactionRecord] {
        rows.filter { $0.state == "pending" && $0.missingDetails == nil } }
    private var needsDetails: [TransactionRecord] {
        rows.filter { $0.state == "pending" && $0.missingDetails != nil } }
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
                rowSection("Pending", pending, footer: DestinationState.shared.summary)
                needsDetailsSection
                rowSection("Possible duplicates", duplicates)
                // covered by a loaded statement row: parked like a
                // duplicate, but the match is the record itself
                rowSection("Already in kopifund", recorded)
                rowSection("History", rest)
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
            .confirmationDialog("Delete \(needsDetails.count) rows with no details?",
                                isPresented: $confirmDeleteAll, titleVisibility: .visible) {
                Button("Delete all", role: .destructive) { delete(needsDetails) }
                Button("Cancel", role: .cancel) {}
            }
            // G24: a sheet, not an alert: an alert's text field is one
            // line, and a pasted alert must keep its line breaks
            .sheet(isPresented: $showPaste) {
                PasteAlertSheet(text: $pasteText) { ingestPaste($0) }
                    .presentationDetents([.medium, .large])
            }
        }
    }

    /// `footer`, when set, says where approving these rows sends them.
    @ViewBuilder
    private func rowSection(_ title: String, _ section: [TransactionRecord],
                            footer: String? = nil) -> some View {
        if !section.isEmpty {
            Section {
                ForEach(section) { row in
                    NavigationLink(value: row.id) {
                        Row(row: row, accountName: accountNames[row.accountID],
                            linked: LinkState.shared.isLinked, ynab: DestinationState.shared.ynab)
                    }
                }
            } header: {
                Text(title)
            } footer: {
                if let footer { Text(footer) }
            }
        }
    }

    /// Rows waiting for an amount or a payee, each with what it lacks. A
    /// swipe deletes one; "Delete all" clears the section, since empty
    /// Wallet captures arrive in runs and none of them holds anything.
    @ViewBuilder
    private var needsDetailsSection: some View {
        if !needsDetails.isEmpty {
            Section {
                ForEach(needsDetails) { row in
                    NavigationLink(value: row.id) {
                        Row(row: row, accountName: accountNames[row.accountID],
                            linked: LinkState.shared.isLinked, ynab: DestinationState.shared.ynab)
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button("Delete", role: .destructive) { delete([row]) }
                    }
                }
            } header: {
                HStack {
                    Text("Needs details")
                    Spacer()
                    Button("Delete all", role: .destructive) { confirmDeleteAll = true }
                        .font(.caption)
                }
            } footer: {
                Text("No amount or payee came through for these. Open one to type what is "
                     + "missing; until then it is never approved or synced.")
            }
        }
    }

    @State private var confirmDeleteAll = false

    private func delete(_ doomed: [TransactionRecord]) {
        // only rows still waiting here: one an edit made complete since the
        // list loaded is no longer empty
        withAnimation {
            for r in doomed where r.state == "pending" && r.missingDetails != nil {
                context.delete(r)
            }
            try? context.save()
        }
    }

    private func approveAll() {
        // an intent may have parked one of these as a duplicate since this
        // list loaded; approving the stale copy would write over that
        SyncService.refresh(context)
        // a row with no amount or payee never posts (G24)
        for r in pending where r.state == "pending" && r.missingDetails == nil { r.state = "approved" }
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

    private func ingestPaste(_ text: String) {
        // the box can be reopened while the match runs: clear only the
        // draft this ingest took, never a newer one
        Task {
            do {
                // the view's own container: a second one on the same file
                // would not merge its save into this list
                let outcome = try await PastedAlert.ingest(text, container: context.container)
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
        let linked: Bool
        let ynab: Bool
        var body: some View {
            // "pending" inside the Pending section says nothing; the
            // caption only speaks where the state adds information
            LedgerRow(title: row.payee.trimmingCharacters(in: .whitespaces).isEmpty
                          ? "No payee" : row.payee,
                      amount: row.amountText, caption: caption(row), note: note)
        }
        private func caption(_ r: TransactionRecord) -> String {
            r.day.formatted(date: .abbreviated, time: .omitted)
                + (accountName.map { " · \($0)" } ?? "")
                + (r.source.map { " · \($0)" } ?? "")
        }
        private var note: (String, Color)? {
            if row.state == "pending" {
                return row.missingDetailsNote.map { ($0, .orange) }
            }
            // G24: with no YNAB an approved row's state alone does not say
            // where it is: on the hub, on its way, or waiting for a link
            if let note = Destination.note(state: row.state, linked: linked, ynab: ynab,
                                           sentToKopifund: row.kopifundSent != nil) {
                return (note, note == "in Kopifund" ? .green : .orange)
            }
            return (row.state, color)
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
