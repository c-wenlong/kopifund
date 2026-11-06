import SwiftUI
import SwiftData
import Storage
import YNABClient

/// Settings: the phone link to Kopifund (LinkSection), the app's own
/// destination, first; then YNAB's token, plan and default account, an
/// optional extra; the bundled shortcuts (ShortcutsSection); the app icon;
/// and the data actions.
struct SettingsView: View {
    @Environment(\.modelContext) private var context
    @AppStorage("planID") private var planID = ""
    @AppStorage("planName") private var planName = ""
    @AppStorage("defaultAccountID") private var defaultAccountID = ""
    @AppStorage("postApproved") private var postApproved = false

    @State private var token = KeychainToken.value ?? ""
    @State private var plans: [Plan] = []
    @State private var accounts: [Account] = []
    @State private var status = ""
    /// G25: the scanner sheet and why its camera would not start.
    @State private var scanning = false
    @State private var scanProblem = ""
    /// G24: the bundled shortcut chosen for export, presented as a share sheet.
    @State private var exporting: ShortcutsSection.ShortcutFile?
    /// "Delete all data" asks first; the summary names what would go.
    @State private var confirmingDelete = false
    @State private var deleteSummary = ""

    var body: some View {
        NavigationStack {
            Form {
                LinkSection(scanning: $scanning, scanProblem: $scanProblem)
                Section {
                    SecureField("Personal access token", text: $token)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .onChange(of: token) { KeychainToken.set(token) }
                    Button("Fetch plans & accounts") { Task { await fetch() } }
                        .disabled(token.isEmpty)
                    if !plans.isEmpty {
                        Picker("Plan", selection: $planID) {
                            Text("Choose").tag("")
                            ForEach(plans) { Text($0.name).tag($0.id) }
                        }
                        .onChange(of: planID) {
                            planName = plans.first { $0.id == planID }?.name ?? ""
                            PlanCurrency.forget()   // G24 audit: read anew for this plan
                            Task { await fetchAccounts() }
                        }
                    }
                    if !accounts.isEmpty {
                        Picker("Default account", selection: $defaultAccountID) {
                            Text("Choose").tag("")
                            ForEach(accounts) { Text($0.name).tag($0.id) }
                        }
                    }
                    Toggle("Post transactions as approved", isOn: $postApproved)
                    if !status.isEmpty { Text(status).font(.caption).foregroundStyle(.secondary) }
                } header: {
                    Text("YNAB")
                } footer: {
                    Text("Optional. With a token and a plan, approved rows go to YNAB too.")
                }
                ShortcutsSection(exporting: $exporting)
                AppIconSection()
                Section("Data") {
                    // the user asked: retry failed rows now, past their backoff
                    Button("Sync now") { Task { await SyncService.flush(context: context, force: true) } }
                    Button("Delete all data", role: .destructive) {
                        deleteSummary = Self.summary(LocalData.counts(in: context))
                        confirmingDelete = true
                    }
                    Button("Revoke token", role: .destructive) {
                        token = ""
                        KeychainToken.set(nil)
                    }
                }
            }
            .navigationTitle("Settings")
            // the Picker writes through the @AppStorage binding, which never
            // runs a didSet, so the Wallet intent's copy is written here,
            // whatever changed the value (AlertsApp copies it at launch too)
            .onChange(of: defaultAccountID) {
                Self.shareDefaultAccount(defaultAccountID)
            }
            .confirmationDialog("Delete all data?", isPresented: $confirmingDelete,
                                titleVisibility: .visible) {
                Button("Delete all data", role: .destructive) { LocalData.deleteAll(in: context) }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(deleteSummary)
            }
            // sheets hang off the Form, not the rows that open them, so a
            // row scrolled away never takes its sheet with it
            .sheet(item: $exporting) { item in
                ShareSheet(url: item.url)
            }
            .sheet(isPresented: $scanning) {
                PairScannerSheet(scanning: $scanning, problem: $scanProblem)
            }
        }
    }

    private func fetch() async {
        status = "fetching…"
        do {
            let c = YNABClient(token: token)
            plans = try await c.plans()
            status = "\(plans.count) plan(s)"
            // a plan picked before relaunch keeps planID but leaves the
            // account list empty; fetch it with the plans
            if !planID.isEmpty { await fetchAccounts() }
        } catch { status = "failed: \(error.localizedDescription)" }
    }

    private func fetchAccounts() async {
        // read the plan once: a mid-flight switch must not write plan A's
        // lists under plan B's cache key
        let pid = planID
        guard !pid.isEmpty else { return }
        do {
            let c = YNABClient(token: token)
            accounts = try await c.accounts(planID: pid)
            // cached for the pattern builder's pickers, from one fetch trip
            YNABCache.save(accounts: accounts, planID: pid, in: context)
            if let groups = try? await c.categories(planID: pid) {
                YNABCache.save(categories: groups, planID: pid, in: context)
            }
            try? context.save()
        } catch { status = "failed: \(error.localizedDescription)" }
    }

    /// The Wallet intent runs outside the app and reads its default account
    /// from the App Group's defaults, not the app's own.
    static func shareDefaultAccount(_ id: String) {
        UserDefaults(suiteName: AppGroup.id)?.set(id, forKey: "defaultAccountID")
    }

    /// What "Delete all data" names before it asks.
    private static func summary(_ counts: LocalData.Counts?) -> String {
        guard let c = counts else {
            // an unknown count must not read as an empty store
            return "The stored data could not be counted. This deletes every transaction, "
                + "pattern, unmatched alert and ignored alert shape, whatever is there. "
                + "Transactions already in YNAB stay there, and so does the YNAB token. "
                + "This cannot be undone."
        }
        return "This deletes \(count(c.transactions, "transaction")) (\(c.unconfirmed) not confirmed in YNAB), "
            + "\(count(c.patterns, "pattern")), \(count(c.unmatched, "unmatched alert")) "
            + "and every ignored alert shape from this phone. Transactions already in YNAB "
            + "stay there, and so does the YNAB token. This cannot be undone."
    }

    private static func count(_ n: Int, _ noun: String) -> String {
        "\(n) \(noun)\(n == 1 ? "" : "s")"
    }
}
