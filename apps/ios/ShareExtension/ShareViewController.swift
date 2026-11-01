import UIKit
import UniformTypeIdentifiers
import Intents
import Storage

/// G24: receive shared alert text, run the same ingest path the paste box
/// uses, close immediately. No UI beyond the share sheet itself.
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        guard let item = extensionContext?.inputItems.first as? NSExtensionItem,
              let provider = item.attachments?.first else { return close() }
        Task { @MainActor in
            do {
                guard let text = try await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) as? String,
                      !text.isEmpty else { throw NSError(domain: "alerts", code: 1) }
                let container = try Store.container()
                _ = try Ingest.alert(text: text, source: "share", receivedAt: Date(),
                                     container: container)
                close()
            } catch {
                // a failed ingest must not look like a successful share
                extensionContext?.cancelRequest(withError: error)
            }
        }
    }

    private func close() {
        extensionContext?.completeRequest(returningItems: nil)
    }
}
