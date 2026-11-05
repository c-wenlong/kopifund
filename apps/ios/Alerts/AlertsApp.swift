import SwiftUI
import SwiftData
import Storage
import KopifundClient

@main
struct AlertsApp: App {
    let container: ModelContainer?
    let storeError: String?
    @State private var pendingPair: String?

    init() {
        // a default account chosen before the Settings fix never reached the
        // Wallet intent's copy; mirror it at launch, not only when Settings opens
        SettingsView.shareDefaultAccount(UserDefaults.standard.string(forKey: "defaultAccountID") ?? "")
        do {
            // a local: self is not fully initialized inside init yet
            let c = try Store.container()
            container = c
            storeError = nil
            // G25: a UI test seeds one pending row with `-seedSpend
            // cents|currency|payee|YYYY-MM-DD` so an approve can reach the
            // hub's pending post without a saved pattern or YNAB setup
            if let i = ProcessInfo.processInfo.arguments.firstIndex(of: "-seedSpend"),
               i + 1 < ProcessInfo.processInfo.arguments.count {
                let parts = ProcessInfo.processInfo.arguments[i + 1].split(separator: "|")
                let f = DateFormatter()
                f.locale = Locale(identifier: "en_US_POSIX")
                f.dateFormat = "yyyy-MM-dd"
                if parts.count == 4, let cents = Int(parts[0]),
                   let day = f.date(from: String(parts[3])) {
                    let ctx = ModelContext(c)
                    let row = TransactionRecord(
                        walletAmountCents: cents, merchant: String(parts[2]),
                        card: nil, date: day, accountID: "")
                    row.currency = String(parts[1])
                    ctx.insert(row)
                    try? ctx.save()
                }
            }
        } catch {
            // never fall back to an in-memory store: rows saved there would
            // vanish on the next launch with no warning
            container = nil
            storeError = error.localizedDescription
        }
    }

    var body: some Scene {
        WindowGroup {
            if let container {
                RootView()
                    .modelContainer(container)
                    .task {
                        if let ctx = try? ModelContext(container) {
                            await SyncService.flush(context: ctx)
                        }
                    }
                    .onOpenURL { url in
                        // G25: a kopifund://pair URL reaches us from the Camera's
                        // scan banner, the Files pasteboard, anywhere a link can
                        // open. It names the server to trust, so it is asked
                        // about first rather than redeemed on sight.
                        if LinkCode(text: url.absoluteString) != nil {
                            pendingPair = url.absoluteString
                        }
                    }
                    // the payload chooses the server, so pairing always asks
                    // first — and says so when it would replace a live link
                    .alert("Pair this phone?",
                           isPresented: Binding(get: { pendingPair != nil },
                                                set: { if !$0 { pendingPair = nil } })) {
                        Button("Pair") {
                            let text = pendingPair
                            pendingPair = nil
                            if let text { Task { _ = await LinkState.shared.pair(text: text) } }
                        }
                        Button("Cancel", role: .cancel) { pendingPair = nil }
                    } message: {
                        let code = pendingPair.flatMap { LinkCode(text: $0) }
                        let host = code?.base.host() ?? "this server"
                        let current = LinkState.shared.device.flatMap { URL(string: $0.base)?.host() }
                        Text("with \(host)" + (current.map { ", replacing the link to \($0)" } ?? ""))
                    }
            } else {
                ContentUnavailableView("Storage failed to open",
                    systemImage: "externaldrive.badge.exclamationmark",
                    description: Text(storeError ?? "unknown error"))
            }
        }
    }
}

struct RootView: View {
    var body: some View {
        TabView {
            InboxView()
                .tabItem { Label("Inbox", systemImage: "tray") }
            UnmatchedView()
                .tabItem { Label("Unmatched", systemImage: "questionmark.folder") }
            KopifundView()
                .tabItem { Label("Kopifund", systemImage: "link") }
            PatternListView()
                .tabItem { Label("Patterns", systemImage: "slider.horizontal.3") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gear") }
        }
    }
}
