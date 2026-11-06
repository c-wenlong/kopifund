import SwiftUI
import SwiftData
import Storage
import UIKit

struct PatternListView: View {
    @Query(sort: \PatternRecord.priority, order: .reverse) private var patterns: [PatternRecord]
    @Environment(\.modelContext) private var context
    @State private var showBuilder = false
    // "always ignore" shapes, listed here so one can be undone without
    // wiping every pattern and transaction
    @State private var ignored: [String] = []
    @State private var notice = ""

    var body: some View {
        NavigationStack {
            List {
                ForEach(patterns) { p in
                    // G24: a saved pattern opens in the builder to edit;
                    // saving the edit re-reads Unmatched like a new one
                    NavigationLink {
                        if let pattern = p.pattern {
                            PatternBuilderView(sampleText: pattern.sampleText,
                                               source: pattern.sourceMatch,
                                               editing: pattern, onSaved: patternSaved)
                        }
                    } label: {
                        VStack(alignment: .leading) {
                            Text(p.name)
                            // G24: a pattern needs no YNAB account; one shows only when set
                            Text("\(p.direction)" + (p.accountID.isEmpty ? "" : " -> \(p.accountID)")
                                 + " · priority \(p.priority)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .onDelete { idx in idx.forEach { context.delete(patterns[$0]) }; try? context.save() }
                if patterns.isEmpty {
                    ContentUnavailableView("No patterns",
                        systemImage: "slider.horizontal.3",
                        description: Text("Paste a real alert and tap its amount, payee and date."))
                }
                if !ignored.isEmpty {
                    Section {
                        ForEach(ignored, id: \.self) { shape in
                            Text(shape)
                                .font(.caption).foregroundStyle(.secondary)
                                .lineLimit(3)
                                .swipeActions {
                                    Button("Stop ignoring") {
                                        Suppression.remove(shape)
                                        loadIgnored()
                                    }
                                    .tint(.blue)
                                }
                        }
                    } header: {
                        Text("Ignored alert shapes")
                    } footer: {
                        Text("Alerts starting like these are dropped. Swipe one to let it queue again.")
                    }
                }
            }
            .navigationTitle("Patterns")
            .onAppear(perform: loadIgnored)
            .toolbar { Button { showBuilder = true } label: { Image(systemName: "plus") } }
            .sheet(isPresented: $showBuilder) {
                NavigationStack {
                    PasteToBuild(onSaved: patternSaved)
                }
            }
        }
        // on the stack, not the list: a list-level overlay never showed
        // under the iOS 27 navigation bar (seen on the simulator)
        .toast($notice)
    }

    /// G24: a new pattern can take alerts already waiting in Unmatched.
    private func patternSaved() {
        showBuilder = false
        Task {
            switch await PatternRematch.run(context: context) {
            case .success(let result):
                let message = result.message()
                notice = message.isEmpty ? "Pattern saved" : "Pattern saved. \(message)"
            case .failure(let error):
                notice = "Pattern saved. " + PatternRematch.failed(error)
            }
        }
    }

    private func loadIgnored() {
        ignored = Suppression.signatures().sorted()
    }

    struct PasteToBuild: View {
        @Environment(\.dismiss) private var dismiss
        var onSaved: () -> Void = {}
        @State private var text = ""
        var body: some View {
            VStack(alignment: .leading, spacing: 12) {
                PasteEditor(text: $text)
                HStack {
                    Spacer()
                    NavigationLink("Build pattern") {
                        PatternBuilderView(sampleText: text, source: nil, onSaved: onSaved)
                    }
                    .disabled(text.isEmpty)
                }
                Spacer()
            }
            .padding()
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Paste alert")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Cancel") { dismiss() } }
        }
    }
}
