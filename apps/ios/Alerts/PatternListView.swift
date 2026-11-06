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

    var body: some View {
        NavigationStack {
            List {
                ForEach(patterns) { p in
                    VStack(alignment: .leading) {
                        Text(p.name)
                        Text("\(p.direction) -> \(p.accountID) · priority \(p.priority)")
                            .font(.caption).foregroundStyle(.secondary)
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
                    PasteToBuild()
                }
            }
        }
    }

    private func loadIgnored() {
        ignored = Suppression.signatures().sorted()
    }

    struct PasteToBuild: View {
        @Environment(\.dismiss) private var dismiss
        @State private var text = ""
        var body: some View {
            VStack {
                ZStack(alignment: .topLeading) {
                    // TextEditor shows nothing when empty; a hint is needed
                    if text.isEmpty {
                        Text("Paste a real bank alert here")
                            .foregroundStyle(.tertiary)
                            .padding(.leading, 21).padding(.top, 24)
                            .allowsHitTesting(false)
                    }
                    TextEditor(text: $text)
                        .padding()
                        .frame(minHeight: 120)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                HStack {
                    Button {
                        if let s = UIPasteboard.general.string { text = s }
                    } label: {
                        Label("Paste", systemImage: "doc.on.clipboard")
                    }
                    Spacer()
                    NavigationLink("Build pattern") {
                        PatternBuilderView(sampleText: text, source: nil)
                    }
                    .disabled(text.isEmpty)
                }
                .padding(.horizontal)
            }
            .navigationTitle("Paste alert")
            .toolbar { Button("Cancel") { dismiss() } }
        }
    }
}
