import SwiftUI
import KopifundClient
import Storage

/// G25: the Kopifund tab: the record the phone is paired to read. Accounts
/// come from /api/profile; the Transactions browser pages /api/transactions.
/// Money is integer cents under `amount`/`amountSGD`, printed only here.
struct KopifundView: View {
    var body: some View {
        // a re-pair to another server rebuilds the tab whole, nav stack
        // included: nothing the old device fetched survives into the new one
        NavigationStack {
            Group {
                if LinkState.shared.isLinked {
                    LinkedView()
                } else {
                    ContentUnavailableView("Not linked",
                        systemImage: "link.badge.plus",
                        description: Text("Settings links this phone: it scans the code the web app shows, once."))
                }
            }
            .navigationTitle("Kopifund")
        }
        .id(LinkState.shared.device?.deviceID)
    }
}

private struct LinkedView: View {
    @State private var profile: Profile?
    @State private var problem = ""

    var body: some View {
        List {
            if !problem.isEmpty {
                Section { Text(problem).font(.caption).foregroundStyle(.red) }
            }
            Section("Accounts") {
                if let profile, profile.accounts.isEmpty {
                    Text("No accounts yet").foregroundStyle(.secondary)
                }
                ForEach(profile?.accounts ?? []) { a in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(a.name)
                            Text([a.bank, a.number].filter { !$0.isEmpty }.joined(separator: " "))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text("\(a.transactions)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
            }
            Section {
                NavigationLink {
                    TransactionsView()
                } label: {
                    Label("Transactions", systemImage: "list.bullet.rectangle")
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        guard let c = LinkState.shared.client else { return }
        do {
            profile = try await c.profile()
            problem = ""
        } catch {
            problem = (error as? KopifundError)?.message ?? error.localizedDescription
        }
    }
}

/// The ledger's pages, newest first. A month pick narrows the same endpoint
/// the web app's list reads; "Load more" walks `offset` until `total` rows.
struct TransactionsView: View {
    /// `page` is the last answer's meta (total, months, the filter choices);
    /// `rows` accumulates every page fetched under the current month.
    @State private var page: TransactionsPage?
    @State private var rows: [TransactionRow] = []
    @State private var month = ""
    @State private var loading = false
    @State private var problem = ""

    private static let pageSize = 100

    var body: some View {
        List {
            if let months = page?.months, !months.isEmpty {
                Picker("Month", selection: $month) {
                    Text("All months").tag("")
                    ForEach(months, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.menu)
                .onChange(of: month) { Task { await load(fresh: true) } }
            }
            if !problem.isEmpty {
                Text(problem).font(.caption).foregroundStyle(.red)
            }
            ForEach(rows) { r in
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(r.payee.isEmpty ? (r.description.isEmpty ? r.merchant : r.description) : r.payee)
                            .font(.body).lineLimit(1)
                        Spacer()
                        Text(money(r)).font(.body.monospacedDigit())
                    }
                    HStack {
                        Text(caption(r)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        Spacer()
                        if let flag = r.flags.first {
                            Text(flag.text).font(.caption).foregroundStyle(.orange)
                        }
                    }
                }
            }
            if let page, rows.count < page.total {
                Button(loading ? "Loading…" : "Load more") {
                    Task { await load(fresh: false) }
                }
                .disabled(loading)
            }
        }
        .navigationTitle("Transactions")
        .task { await load(fresh: true) }
        .refreshable { await load(fresh: true) }
    }

    private func caption(_ r: TransactionRow) -> String {
        [r.date, r.category.isEmpty ? nil : r.category, r.account.isEmpty ? nil : r.account]
            .compactMap { $0 }.joined(separator: "  ·  ")
    }

    /// SGD when the record has it (a payment with no SGD value keeps its own
    /// currency's figure). Cents become dollars only for display.
    private func money(_ r: TransactionRow) -> String {
        if let sgd = r.amountSGD { return Cents.signedText(sgd, currency: "SGD") }
        return Cents.signedText(r.amount, currency: r.currency)
    }

    @State private var generation = 0

    private func load(fresh: Bool) async {
        guard let c = LinkState.shared.client else { return }
        generation += 1
        let gen = generation
        let wantedMonth = month
        loading = true
        defer { loading = false }
        do {
            let offset = fresh ? 0 : rows.count
            let next = try await c.transactions(month: wantedMonth.isEmpty ? nil : wantedMonth,
                                                limit: Self.pageSize, offset: offset)
            // a newer request or a month change owns the list now
            guard gen == generation, wantedMonth == month else { return }
            page = next
            rows = fresh ? next.rows : rows + next.rows
            problem = ""
        } catch {
            guard gen == generation else { return }
            problem = (error as? KopifundError)?.message ?? error.localizedDescription
        }
    }
}
