import SwiftUI
import VisionKit

/// G25: the in-app camera for the pairing QR. VisionKit's data scanner does
/// the video + barcode plumbing; on a simulator (or anywhere a camera is
/// not) `supported` stays false and the paste field is the only way in.
struct QRScanner: UIViewControllerRepresentable {
    static var supported: Bool {
        DataScannerViewController.isSupported && DataScannerViewController.isAvailable
    }

    let onCode: (String) -> Void
    /// Called when the camera will not start, so the sheet can say so and
    /// point at the paste field rather than sit black.
    var onFailure: (String) -> Void = { _ in }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isPinchToZoomEnabled: true,
            isGuidanceEnabled: true,
            isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        do {
            try vc.startScanning()
        } catch {
            let message = error.localizedDescription
            DispatchQueue.main.async { onFailure(message) }
        }
        return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        private let onCode: (String) -> Void
        private var sent = false   // one code pairs once; a live camera can report it twice

        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func dataScanner(_ vc: DataScannerViewController, didAdd addedItems: [RecognizedItem],
                         allItems: [RecognizedItem]) {
            guard !sent,
                  case let .barcode(code) = addedItems.first,
                  let payload = code.payloadStringValue else { return }
            sent = true
            onCode(payload)
        }
    }
}
