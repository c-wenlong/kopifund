import SwiftUI
import Matching

/// "About this pattern": its name, direction and account, and behind "More
/// options" its default category, source filter, priority and auto-approve.
struct PatternMetaForm: View {
    @Binding var name: String
    @Binding var direction: AlertPattern.Direction
    @Binding var accountID: String
    @Binding var defaultCategoryID: String
    @Binding var sourceMatch: String
    @Binding var priority: Int
    @Binding var autoApprove: Bool
    let accountOptions: [(String, String)]
    let categoryOptions: [(String, String)]
    /// The name field itself was edited (not the payee span renaming it).
    let onNameEdited: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("About this pattern").font(.caption).foregroundStyle(.secondary)
            VStack(spacing: 0) {
                fieldRow("Name") {
                    TextField("e.g. PayLah! Transfer", text: $name)
                        .onChange(of: name) { onNameEdited() }
                }
                divider
                fieldRow("Direction") {
                    Picker("Direction", selection: $direction) {
                        Text("Outflow").tag(AlertPattern.Direction.outflow)
                        Text("Inflow").tag(AlertPattern.Direction.inflow)
                    }.pickerStyle(.segmented)
                }
                divider
                fieldRow("Account") {
                    Picker("Account", selection: $accountID) {
                        if accountOptions.isEmpty {
                            Text("Fetch accounts in Settings first").tag("")
                        }
                        ForEach(accountOptions, id: \.0) { Text($0.1).tag($0.0) }
                    }.labelsHidden()
                }
            }
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: 14))
            DisclosureGroup("More options") {
                VStack(spacing: 0) {
                    fieldRow("Category") {
                        if categoryOptions.isEmpty {
                            TextField("Optional", text: $defaultCategoryID)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                        } else {
                            Picker("Category", selection: $defaultCategoryID) {
                                Text("None").tag("")
                                ForEach(categoryOptions, id: \.0) { Text($0.1).tag($0.0) }
                            }.labelsHidden()
                        }
                    }
                    divider
                    fieldRow("Source has") {
                        TextField("Optional, e.g. email", text: $sourceMatch)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    divider
                    fieldRow("Priority") {
                        Stepper("", value: $priority).labelsHidden()
                        Text("\(priority)").font(.callout).monospacedDigit()
                    }
                    divider
                    fieldRow("") {
                        Toggle("Auto-approve matches", isOn: $autoApprove)
                            .font(.callout)
                    }
                }
                .background(Color(.secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .padding(.top, 6)
            }
            .font(.callout)
        }
    }

    private var divider: some View {
        Divider().padding(.leading, 14)
    }

    private func fieldRow<Content: View>(_ k: String, @ViewBuilder _ c: () -> Content) -> some View {
        HStack(spacing: 12) {
            if !k.isEmpty {
                Text(k).font(.callout).foregroundStyle(.secondary).frame(width: 84, alignment: .leading)
            }
            c().font(.callout)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }
}
