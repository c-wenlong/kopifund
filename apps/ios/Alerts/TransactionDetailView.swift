import SwiftUI
import SwiftData
import Matching
import Storage

struct TransactionDetailView: View {
    @Bindable var row: TransactionRecord
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @AppStorage("planID") private var planID = ""
    @State private var categoryOptions: [(id: String, label: String)] = []
    /// The plan-currency figure the owner types for a held foreign spend.
    @State private var converted: Double?
    // a typed conversion is a draft: a store refresh waits to re-key the
    // Inbox (and drop this screen) until the screen is gone
    @State private var draftHold = SyncService.DraftHold()
    // set by Delete: the row goes once this view is off screen, so no
    // render ever reads a deleted model
    @State private var deleteOnExit = false

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
                // the picker edits the calendar day in the phone's own
                // calendar; the row keeps it as a zone-free day
                DatePicker("Date", selection: Binding(
                    get: { row.day.date(in: .current) },
                    set: { row.day = CalendarDate($0, in: .current) }),
                           displayedComponents: .date)
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
                LabeledContent("Currency",
                               value: row.currency.isEmpty ? "Not stated" : row.currency)
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
            if let plan = heldFor {
                // G24 audit: a foreign spend never posts as the plan's
                // currency on its own; the owner types the converted figure
                Section {
                    HStack {
                        Text("Amount in \(plan)")
                        Spacer()
                        TextField("Required", value: $converted, format: .number)
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                    }
                    Button("Post in \(plan)") {
                        guard let cents = convertedCents else { return }
                        row.postInPlanCurrency(plan, amountCents: cents)
                        try? context.save()
                        Task { await SyncService.flush(context: context) }
                        dismiss()
                    }
                    .disabled(convertedCents == nil)
                } header: {
                    Text("Different currency")
                } footer: {
                    Text("This spend is in \(row.currency) and the YNAB plan keeps \(plan). "
                       + "Type what it cost in \(plan), then post it.")
                }
            } else if let err = row.syncError {
                Section("Sync error") { Text(err).foregroundStyle(.red) }
            }
            if let err = row.kopifundError {
                // a refused or held send to the linked Kopifund record
                Section("Kopifund") { Text(err).foregroundStyle(.red) }
            }
        }
        // an edit to what the hub was sent (payee, amount, day, direction,
        // currency) owes it a repost: clearing the accepted copy puts the
        // row back in the sync's hub pass
        .onChange(of: "\(row.payee)|\(row.amountCents)|\(row.day.iso)|\(row.direction)|\(row.currency)") {
            if row.kopifundID != nil, row.kopifundSent != nil {
                row.kopifundSent = nil
                try? context.save()
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
            if row.state == "pending" && heldFor == nil {
                Button("Approve") {
                    row.state = "approved"
                    try? context.save()
                    Task { await SyncService.flush(context: context) }
                    dismiss()
                }
            }
            Button("Delete", role: .destructive) {
                deleteOnExit = true
                dismiss()
            }
            // a row in flight is still being written by the sync; deleting
            // it under that write traps
            .disabled(row.state == "syncing")
        }
        .onDisappear {
            guard deleteOnExit else { return }
            // a sync that picked the row up meanwhile re-reads it before each
            // write and leaves a deleted row alone (Sync.run, HubLane), so
            // the delete need not wait, and the row never lingers in the
            // list to be reopened
            context.delete(row)
            try? context.save()
        }
    }

    @State private var sibling: TransactionRecord?

    /// The plan's currency when this pending row is in another one, so it
    /// waits for the owner instead of offering a plain Approve.
    private var heldFor: String? {
        guard row.state == "pending", let plan = PlanCurrency.saved(planID: planID),
              Currency.holdReason(currency: row.currency, planCurrency: plan) != nil
        else { return nil }
        return plan
    }

    private func loadSibling() {
        guard let id = row.duplicateOf else { return }
        sibling = try? context.fetch(FetchDescriptor<TransactionRecord>(
            predicate: #Predicate { $0.id == id })).first
    }

    private func money(_ r: TransactionRecord) -> String {
        let sign = r.direction == "outflow" ? "-" : "+"
        return "\(sign)\(r.currency) \(String(format: "%.2f", Double(r.amountCents) / 100))"
    }

    /// The typed plan-currency figure as cents, nil until a positive one
    /// is entered; the post button stays off until then.
    private var convertedCents: Int? {
        guard let c = converted, c > 0, let cents = Cents.from(c), cents > 0 else { return nil }
        return cents
    }

    private var centsBinding: Binding<Double> {
        Binding(get: { Double(row.amountCents) / 100 },
                // the same rounding as intake; a figure past counting is ignored
                set: { if let cents = Cents.from($0) { row.amountCents = cents } })
    }
}
