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
            // G24: builds before the missing-details check queued empty
            // Wallet captures (amount 0, no payee) as approvable rows; clear
            // them once. Only rows never sent anywhere go, and the count is
            // logged (EmptyWalletCleanup)
            _ = try? EmptyWalletCleanup.runOnce(in: ModelContext(c), defaults: .standard)
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
            // `-seedNoCategories` empties the cached YNAB category lists, so a
            // UI test sees the Review page as it is before any YNAB fetch
            if ProcessInfo.processInfo.arguments.contains("-seedNoCategories") {
                let ctx = ModelContext(c)
                for m in (try? ctx.fetch(FetchDescriptor<MetadataRecord>())) ?? []
                where m.key.hasPrefix("categories|") { ctx.delete(m) }
                try? ctx.save()
            }
            #if DEBUG
            // G24: a UI test parks alerts in Unmatched with one
            // `-seedUnmatched <text>` per alert, as if each arrived before
            // any pattern could read it
            let args = ProcessInfo.processInfo.arguments
            let seeds = args.indices.filter { args[$0] == "-seedUnmatched" && $0 + 1 < args.count }
            if !seeds.isEmpty {
                let ctx = ModelContext(c)
                // each seeded run starts from an empty store, so one test's
                // patterns and rows never meet the next one's
                try? ctx.delete(model: UnmatchedRecord.self)
                try? ctx.delete(model: TransactionRecord.self)
                try? ctx.delete(model: PatternRecord.self)
                for (k, i) in seeds.enumerated() {
                    ctx.insert(UnmatchedRecord(text: args[i + 1], source: "uitest",
                                               receivedAt: Date().addingTimeInterval(Double(k) * 60),
                                               reasons: ""))
                }
                try? ctx.save()
            }
            #endif
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
                    // the run belongs to SyncService, so this task ending
                    // never cancels a send
                    .task { await SyncService.flush(context: container.mainContext) }
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
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.modelContext) private var context

    var body: some View {
        tabs
            // back from a Wallet tap, a share or a Shortcut run: the other
            // process wrote the file while this one was in the background
            .onChange(of: scenePhase) { _, phase in
                guard phase == .active else {
                    SyncService.noteBackground(context)
                    return
                }
                SyncService.refresh(context)
                DestinationState.shared.reload()
                Task { await SyncService.flush(context: context) }
            }
    }

    private var tabs: some View {
        TabView {
            // a new identity re-runs the list's query after another
            // process wrote the store (SyncService.refresh)
            InboxView()
                .id(SyncService.shared.storeGeneration)
                .tabItem { Label("Inbox", systemImage: "tray") }
            UnmatchedView()
                .id(SyncService.shared.storeGeneration)
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
