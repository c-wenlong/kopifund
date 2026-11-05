import SwiftUI
import SwiftData
import Storage
import YNABClient
import KopifundClient
import Security

/// The token lives in the Keychain, never in the store or a file. "Delete all
/// data" wipes the store but leaves the token; "Revoke token" drops the
/// Keychain item so the app forgets who it was.
enum KeychainToken {
    private static let service = "kopifund.alerts.ynab"
    private static let account = "pat"

    static var value: String? {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: account,
                                kSecReturnData as String: true]
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func set(_ token: String?) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        if let token, !token.isEmpty {
            SecItemDelete(q as CFDictionary)
            var add = q
            add[kSecValueData as String] = Data(token.utf8)
            SecItemAdd(add as CFDictionary, nil)
        } else {
            SecItemDelete(q as CFDictionary)
        }
    }
}

/// Wraps UIActivityViewController so a bundled .shortcut file can be handed
/// to Shortcuts ("Add Shortcut") or AirDropped to another device.
struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

struct SettingsView: View {
    @Environment(\.modelContext) private var context
    @AppStorage("planID") private var planID = ""
    @AppStorage("planName") private var planName = ""
    @AppStorage("defaultAccountID") private var defaultAccountID = "" {
        didSet { UserDefaults(suiteName: AppGroup.id)?.set(defaultAccountID, forKey: "defaultAccountID") }
    }
    @AppStorage("postApproved") private var postApproved = false

    @State private var token = KeychainToken.value ?? ""
    @State private var plans: [Plan] = []
    @State private var accounts: [Account] = []
    @State private var status = ""
    /// G25: the pairing code as pasted, the scanner sheet, and what Test said.
    @State private var pairCode = ""
    @State private var scanning = false
    @State private var scanProblem = ""
    @State private var linkStatus = ""
    /// G24: the bundled shortcut chosen for export, presented as a share sheet.
    @State private var exporting: ShortcutFile?
    @State private var shortcutError = ""
    struct ShortcutFile: Identifiable {
        let url: URL
        var id: String { url.lastPathComponent }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("YNAB") {
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
                }
                Section {
                    if let d = LinkState.shared.device {
                        LabeledContent("Linked", value: d.name)
                        LabeledContent("Server", value: URL(string: d.base)?.host() ?? d.base)
                        Button("Test connection") {
                            Task { linkStatus = await LinkState.shared.test() }
                        }
                        if !linkStatus.isEmpty {
                            Text(linkStatus).font(.caption).foregroundStyle(.secondary)
                        }
                        Button("Unlink", role: .destructive) { LinkState.shared.unlink() }
                    } else {
                        TextField("Paste the pairing code", text: $pairCode)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                        Button(LinkState.shared.pairing ? "Pairing…" : "Pair") {
                            Task {
                                if await LinkState.shared.pair(text: pairCode) { pairCode = "" }
                            }
                        }
                        .disabled(pairCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                  || LinkState.shared.pairing)
                        if QRScanner.supported {
                            Button("Scan the QR") { scanProblem = ""; scanning = true }
                        }
                        if !LinkState.shared.problem.isEmpty {
                            Text(LinkState.shared.problem).font(.caption).foregroundStyle(.red)
                        }
                    }
                } header: {
                    Text("Kopifund")
                } footer: {
                    Text(LinkState.shared.isLinked
                         ? "Unlink forgets this phone; the Mac's registry still answers its bearer until Settings revokes it."
                         : "In the web app's Settings, Link a phone shows a code good for ten minutes.")
                        .font(.caption)
                }
                Section {
                    ForEach(shortcutResources(), id: \.lastPathComponent) { url in
                        Button {
                            do {
                                exporting = ShortcutFile(url: try stagedCopy(of: url))
                                shortcutError = ""
                            } catch {
                                shortcutError = "couldn't export "
                                    + url.deletingPathExtension().lastPathComponent
                            }
                        } label: {
                            Label(url.deletingPathExtension().lastPathComponent,
                                  systemImage: "square.and.arrow.up")
                        }
                    }
                    if !shortcutError.isEmpty {
                        Text(shortcutError).font(.caption).foregroundStyle(.red)
                    }
                } header: {
                    Text("Shortcuts")
                } footer: {
                    Text("Tap a shortcut, then choose Shortcuts to add it. "
                       + "In Shortcuts, create an automation for the matching trigger "
                       + "and set its action to Run Shortcut → the shortcut you added.")
                        .font(.caption)
                }
                AppIconSection()
                Section("Data") {
                    // the user asked: retry failed rows now, past their backoff
                    Button("Sync now") { Task { await SyncService.flush(context: context, force: true) } }
                    Button("Delete all data", role: .destructive) { deleteAll() }
                    Button("Revoke token", role: .destructive) {
                        token = ""
                        KeychainToken.set(nil)
                    }
                }
            }
            .navigationTitle("Settings")
            // sharing a temp copy, not the bundle resource, so UIActivityVC
            // can hand the file to Shortcuts without bundle-path quirks
            .sheet(item: $exporting) { item in
                ShareSheet(url: item.url)
            }
            .sheet(isPresented: $scanning) {
                NavigationStack {
                    ZStack {
                        QRScanner(onCode: { code in
                                      scanning = false
                                      Task { _ = await LinkState.shared.pair(text: code) }
                                  },
                                  onFailure: { msg in scanProblem = msg })
                        if !scanProblem.isEmpty {
                            VStack(spacing: 10) {
                                Image(systemName: "camera.slash")
                                    .font(.title2).foregroundStyle(.secondary)
                                Text(scanProblem)
                                    .font(.caption).foregroundStyle(.secondary)
                                Text("Paste the pairing code on Settings instead.")
                                    .font(.caption)
                            }
                            .padding()
                        }
                    }
                    .navigationTitle("Pair this phone")
                    .toolbar { ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { scanning = false }
                    } }
                }
            }
        }
    }

    private func shortcutResources() -> [URL] {
        (Bundle.main.urls(forResourcesWithExtension: "shortcut", subdirectory: nil) ?? [])
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    private func stagedCopy(of url: URL) throws -> URL {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent(url.lastPathComponent)
        try? FileManager.default.removeItem(at: tmp)
        try FileManager.default.copyItem(at: url, to: tmp)
        return tmp
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
            // cached for the pattern builder's account picker
            let row = MetadataRecord(key: "accounts|\(pid)",
                                   payload: (try? JSONEncoder().encode(accounts)) ?? Data(),
                                   fetchedAt: Date())
            context.insert(row)
            // same fetch trip: categories become the category picker's list
            if let groups = try? await c.categories(planID: pid) {
                let stubs = groups.filter { !$0.deleted && !$0.hidden }.flatMap { g in
                    g.categories.filter { !$0.deleted && !$0.hidden }
                        .map { CategoryStub(id: $0.id, name: $0.name, group: g.name) }
                }
                context.insert(MetadataRecord(key: "categories|\(pid)",
                                            payload: (try? JSONEncoder().encode(stubs)) ?? Data(),
                                            fetchedAt: Date()))
            }
            try? context.save()
        } catch { status = "failed: \(error.localizedDescription)" }
    }

    private func deleteAll() {
        for row in (try? context.fetch(FetchDescriptor<TransactionRecord>())) ?? [] { context.delete(row) }
        for row in (try? context.fetch(FetchDescriptor<PatternRecord>())) ?? [] { context.delete(row) }
        for row in (try? context.fetch(FetchDescriptor<UnmatchedRecord>())) ?? [] { context.delete(row) }
        for row in (try? context.fetch(FetchDescriptor<MetadataRecord>())) ?? [] { context.delete(row) }
        // ignores live outside the store (App Group defaults), so the
        // records-only reset would leave them behind
        Suppression.clear()
        try? context.save()
    }
}
