import SwiftUI

/// G25: the phone link in Settings. Linked, it names the device and server
/// and offers Test and Unlink; unlinked, it takes a pasted pairing code or
/// opens the scanner, which SettingsView presents.
struct LinkSection: View {
    @Binding var scanning: Bool
    @Binding var scanProblem: String
    /// The pairing code as pasted, and what Test said.
    @State private var pairCode = ""
    @State private var linkStatus = ""

    var body: some View {
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
    }
}

/// The camera on the pairing QR; a camera that will not start says so and
/// points back at the paste field.
struct PairScannerSheet: View {
    @Binding var scanning: Bool
    @Binding var problem: String

    var body: some View {
        NavigationStack {
            ZStack {
                QRScanner(onCode: { code in
                              scanning = false
                              Task { _ = await LinkState.shared.pair(text: code) }
                          },
                          onFailure: { msg in problem = msg })
                if !problem.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "camera.slash")
                            .font(.title2).foregroundStyle(.secondary)
                        Text(problem)
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
