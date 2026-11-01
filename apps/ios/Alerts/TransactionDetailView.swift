import SwiftUI
import SwiftData
import Storage

struct TransactionDetailView: View {
    @Bindable var row: TransactionRecord
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @AppStorage("planID") private var planID = ""
    @State private var categoryOptions: [(id: String, label: String)] = []

    var body: some View {
        Form {
            Section("Transaction") {
                if row.state == "synced" { EmptyView() }
                TextField("Payee", text: $row.payee)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                HStack {
                    Text("Amount")
                    Spacer()
                    TextField("Amount", value: centsBinding, format: .number)
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                }
                DatePicker("Date", selection: $row.date, displayedComponents: .date)
                Picker("Direction", selection: $row.direction) {
                    Text("Outflow").tag("outflow")
                    Text("Inflow").tag("inflow")
                }
                // nobody holds a YNAB category id in their head: pick from the
                // cached list, fall back to a raw field until Settings fetches
                if categoryOptions.isEmpty {
                    TextField("Category ID", text: Binding(
                        get: { row.categoryID ?? "" }, set: { row.categoryID = $0.isEmpty ? nil : $0 }))
                } else {
                    Picker("Category", selection: Binding(
                        get: { row.categoryID ?? "" }, set: { row.categoryID = $0.isEmpty ? nil : $0 })) {
                        Text("None").tag("")
                        // a saved id absent from the cache (hidden, deleted or
                        // a stale plan fetch) still shows, or the picker would
                        // read as blank
                        if let saved = row.categoryID,
                           !categoryOptions.contains(where: { $0.id == saved }) {
                            Text("\(saved) (saved)").tag(saved)
                        }
                        ForEach(categoryOptions, id: \.id) { Text($0.label).tag($0.id) }
                    }
                }
                TextField("Memo", text: $row.memo)
            }
            // a synced row already lives in YNAB; the client cannot update
            // it, so editing here would only fork the local copy
            .disabled(row.state == "synced")
            // where the alert came in from, and what it said about the card
            // and the currency: a wallet row reads "wallet", an sms one "sms"
            Section("Details") {
                if let name = YNABCache.accountName(for: row.accountID, in: context) {
                    LabeledContent("Account", value: name)
                }
                LabeledContent("Source", value: row.source ?? "unknown")
                LabeledContent("Received",
                               value: row.receivedAt.formatted(date: .abbreviated, time: .shortened))
                LabeledContent("Currency", value: row.currency)
                if let hint = row.accountHint, !hint.isEmpty {
                    LabeledContent("Card", value: hint)
                }
            }
            if !row.alertText.isEmpty {
                Section("Alert") { Text(row.alertText).font(.caption).foregroundStyle(.secondary) }
            }
            if row.state == "duplicate" {
                // parked by the dedup check: never posts unless the owner
                // says it is a real charge
                Section("Possible duplicate") {
                    if let sib = sibling {
                        Text("Matches \(sib.payee) · \(money(sib)) · via \(sib.source ?? "unknown")")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("Matches another queued transaction")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Button("Not a duplicate") {
                        row.state = "pending"
                        row.duplicateOf = nil
                        try? context.save()
                    }
                }
            }
            if row.state == "kopifund" {
                // a loaded statement row already covers this spend: the row
                // is parked out of the YNAB flush unless the owner disagrees
                Section("Already in kopifund") {
                    Text("A statement in the record covers this spend")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Not recorded") {
                        row.state = "pending"
                        try? context.save()
                    }
                }
            }
            if let err = row.syncError {
                Section("Sync error") { Text(err).foregroundStyle(.red) }
            }
        }
        .navigationTitle(row.state == "pending" ? "Review"
                         : row.state == "kopifund" ? "in kopifund" : row.state)
        .task {
            loadSibling()
            categoryOptions = YNABCache.categories(in: context, planID: planID)
                .map { ($0.id, "\($0.group) · \($0.name)") }
        }
        .toolbar {
            if row.state == "pending" {
                Button("Approve") {
                    row.state = "approved"
                    try? context.save()
                    Task { await SyncService.flush(context: context) }
                    dismiss()
                }
            }
            Button("Delete", role: .destructive) {
                context.delete(row)
                try? context.save()
                dismiss()
            }
        }
    }

    @State private var sibling: TransactionRecord?

    private func loadSibling() {
        guard let id = row.duplicateOf else { return }
        sibling = try? context.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.id == id })).first
    }

    private func money(_ r: TransactionRecord) -> String {
        let sign = r.direction == "outflow" ? "-" : "+"
        return "\(sign)\(r.currency) \(String(format: "%.2f", Double(r.amountCents) / 100))"
    }

    private var centsBinding: Binding<Double> {
        Binding(get: { Double(row.amountCents) / 100 },
                set: { row.amountCents = Int(($0 * 100).rounded()) })
    }
}
