import Foundation

final class MockURLProtocol: URLProtocol {
    typealias Handler = @Sendable (URLRequest) throws -> (HTTPURLResponse, Data)

    private static let storage = HandlerStorage()

    static func setHandler(_ handler: @escaping Handler) {
        storage.set(handler)
    }

    static func clearHandler() {
        storage.set(nil)
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.storage.get() else {
            client?.urlProtocol(
                self,
                didFailWithError: URLError(.resourceUnavailable)
            )
            return
        }

        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            if !data.isEmpty {
                client?.urlProtocol(self, didLoad: data)
            }
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
private final class HandlerStorage: @unchecked Sendable {
    private let lock = NSLock()
    private var handler: MockURLProtocol.Handler?

    func set(_ handler: MockURLProtocol.Handler?) {
        lock.lock()
        self.handler = handler
        lock.unlock()
    }

    func get() -> MockURLProtocol.Handler? {
        lock.lock()
        defer { lock.unlock() }
        return handler
    }
}

final class LockedBox<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Value

    init(_ value: Value) {
        self.value = value
    }

    func withValue<Result>(_ operation: (inout Value) throws -> Result) rethrows -> Result {
        lock.lock()
        defer { lock.unlock() }
        return try operation(&value)
    }
}
