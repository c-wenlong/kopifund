import Foundation

/// A URLProtocol the tests point URLSession at: set `handler` and read back
/// the requests the client sent. Test-only, so it never ships in the app;
/// URL loading calls it from its own threads, so its state sits behind a lock.
final class MockProtocol: URLProtocol, @unchecked Sendable {
    typealias Handler = @Sendable (URLRequest) throws -> (HTTPURLResponse, Data)

    private static let lock = NSLock()
    nonisolated(unsafe) private static var _handler: Handler?
    nonisolated(unsafe) private static var _requests: [URLRequest] = []

    static var handler: Handler? {
        get { lock.withLock { _handler } }
        set { lock.withLock { _handler = newValue } }
    }

    static var requests: [URLRequest] {
        get { lock.withLock { _requests } }
        set { lock.withLock { _requests = newValue } }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        // record and read the handler in one step, so a handler that counts
        // `requests` sees this request already in the list
        let handler: Handler? = MockProtocol.lock.withLock {
            MockProtocol._requests.append(request)
            return MockProtocol._handler
        }
        guard let handler else { return }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }
    override func stopLoading() {}

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockProtocol.self]
        return URLSession(configuration: config)
    }
}
