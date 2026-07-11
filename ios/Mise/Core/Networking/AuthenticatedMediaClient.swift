import CryptoKit
import Foundation

enum AuthenticatedMediaPurpose: String, Sendable {
    case thumbnail
    case preview
    case poster
    case download

    fileprivate var acceptHeader: String {
        switch self {
        case .thumbnail, .preview, .poster: "image/*"
        case .download: "application/octet-stream, image/*, video/*"
        }
    }

    fileprivate var maximumByteCount: Int {
        switch self {
        case .thumbnail: 8 * 1_024 * 1_024
        case .poster: 16 * 1_024 * 1_024
        case .preview: 32 * 1_024 * 1_024
        case .download: .max
        }
    }

    fileprivate var isImage: Bool { self != .download }
}

enum AuthenticatedMediaRouteProfile: Sendable {
    case clientGallery
    case ownerCull
}

enum AuthenticatedMediaError: LocalizedError, Sendable {
    case invalidURL
    case responseTooLarge
    case unsupportedContent(String?)
    case fileStorageFailed
    case downloadTooLarge

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "Mise refused an invalid media address."
        case .responseTooLarge:
            "This image is too large to open safely."
        case .unsupportedContent:
            "The studio returned an unsupported media format."
        case .fileStorageFailed:
            "Mise could not protect the downloaded file on this device."
        case .downloadTooLarge:
            "This original exceeds Mise’s protected offline storage limit."
        }
    }
}

protocol AuthenticatedMediaLoading: Sendable {
    func data(
        from url: URL,
        purpose: AuthenticatedMediaPurpose,
        contentRevision: Int64?
    ) async throws -> Data
    func download(
        from url: URL,
        suggestedFilename: String,
        expectedByteCount: Int64?
    ) async throws -> URL
    func release(_ localURL: URL) async
    func purge() async
}

extension AuthenticatedMediaLoading {
    func data(from url: URL, purpose: AuthenticatedMediaPurpose) async throws -> Data {
        try await data(from: url, purpose: purpose, contentRevision: nil)
    }

    func download(from url: URL, suggestedFilename: String) async throws -> URL {
        try await download(
            from: url,
            suggestedFilename: suggestedFilename,
            expectedByteCount: nil
        )
    }

    func release(_ localURL: URL) async {}
}

actor ClientDeliveryLifetime {
    private var generation: UInt64 = 0
    private var ended = false

    func ticket() -> UInt64? {
        ended ? nil : generation
    }

    func isActive(_ ticket: UInt64) -> Bool {
        !ended && ticket == generation
    }

    func end() {
        guard !ended else { return }
        ended = true
        generation &+= 1
    }
}

/// Loads scoped media without ever handing a bearer-only URL to `AsyncImage`.
/// Authorization is attached only after the URL is proven to be an exact client
/// delivery or owner-cull route on the active workspace origin. Redirects are
/// rejected to prevent token disclosure.
actor AuthenticatedMediaClient: AuthenticatedMediaLoading {
    private struct MemoryEntry: Sendable {
        let data: Data
        var lastAccess: UInt64
    }

    private let origin: URL
    private let clientVersion: String
    private let session: URLSession
    private let authorizer: any RequestAuthorizing
    private let routeProfile: AuthenticatedMediaRouteProfile
    private let downloadStore: ProtectedMediaDownloadStore
    private let memoryLimit: Int
    private let maximumCachedObjectSize: Int
    private let downloadByteLimit: Int64
    private let onSessionEnded: @Sendable () async -> Void
    private let lifetime: ClientDeliveryLifetime
    private let redirectDelegate = AuthenticatedMediaRedirectDelegate()
    private var memory: [String: MemoryEntry] = [:]
    private var memoryCost = 0
    private var accessCounter: UInt64 = 0

    init(
        origin: URL,
        clientVersion: String,
        session: URLSession,
        authorizer: any RequestAuthorizing,
        cacheNamespace: String,
        routeProfile: AuthenticatedMediaRouteProfile = .clientGallery,
        memoryLimit: Int = 48 * 1_024 * 1_024,
        maximumCachedObjectSize: Int = 8 * 1_024 * 1_024,
        downloadByteLimit: Int64 = 2 * 1_024 * 1_024 * 1_024,
        downloadFileLimit: Int = 40,
        downloadRoot: URL? = nil,
        lifetime: ClientDeliveryLifetime = ClientDeliveryLifetime(),
        onSessionEnded: @escaping @Sendable () async -> Void = {}
    ) {
        self.origin = origin
        self.clientVersion = clientVersion
        self.session = session
        self.authorizer = authorizer
        self.routeProfile = routeProfile
        self.memoryLimit = max(0, memoryLimit)
        self.maximumCachedObjectSize = max(0, maximumCachedObjectSize)
        self.downloadByteLimit = max(0, downloadByteLimit)
        self.onSessionEnded = onSessionEnded
        self.lifetime = lifetime
        downloadStore = ProtectedMediaDownloadStore(
            cacheNamespace: cacheNamespace,
            rootDirectory: downloadRoot,
            byteLimit: downloadByteLimit,
            fileLimit: downloadFileLimit
        )
    }

    func data(
        from url: URL,
        purpose: AuthenticatedMediaPurpose,
        contentRevision: Int64?
    ) async throws -> Data {
        guard let lifetimeTicket = await lifetime.ticket() else {
            throw APIError.unauthenticated(nil)
        }
        guard purpose != .download else { throw AuthenticatedMediaError.invalidURL }
        let mediaURL = try validatedURL(url, purpose: purpose)
        let cacheKey = "\(contentRevision ?? 0):\(purpose.rawValue):\(mediaURL.absoluteString)"

        if var entry = memory[cacheKey] {
            guard await lifetime.isActive(lifetimeTicket) else {
                throw APIError.unauthenticated(nil)
            }
            accessCounter &+= 1
            entry.lastAccess = accessCounter
            memory[cacheKey] = entry
            return entry.data
        }

        try Task.checkCancellation()
        let token = try await requiredToken()
        let data = try await performDataRequest(
            mediaURL,
            purpose: purpose,
            bearerToken: token,
            mayRefresh: true
        )
        try Task.checkCancellation()
        guard await lifetime.isActive(lifetimeTicket) else {
            throw APIError.unauthenticated(nil)
        }
        insert(data, for: cacheKey)
        return data
    }

    /// Creates a protected app-local copy suitable for an explicit share/save action.
    /// This is a foreground async transfer, not a claim of durable background delivery:
    /// a background task cannot safely refresh an expired bearer token without the app.
    func download(
        from url: URL,
        suggestedFilename: String,
        expectedByteCount: Int64? = nil
    ) async throws -> URL {
        guard let lifetimeTicket = await lifetime.ticket() else {
            throw APIError.unauthenticated(nil)
        }
        if let expectedByteCount,
           downloadByteLimit > 0,
           expectedByteCount > downloadByteLimit
        {
            throw AuthenticatedMediaError.downloadTooLarge
        }
        let mediaURL = try validatedURL(url, purpose: .download)
        try Task.checkCancellation()
        let token = try await requiredToken()
        let temporaryURL = try await performDownload(
            mediaURL,
            bearerToken: token,
            mayRefresh: true
        )
        do {
            try Task.checkCancellation()
            guard await lifetime.isActive(lifetimeTicket) else {
                try? FileManager.default.removeItem(at: temporaryURL)
                throw APIError.unauthenticated(nil)
            }
            let stored = try await downloadStore.store(
                temporaryURL: temporaryURL,
                suggestedFilename: suggestedFilename
            )
            guard await lifetime.isActive(lifetimeTicket) else {
                await downloadStore.remove(stored)
                throw APIError.unauthenticated(nil)
            }
            return stored
        } catch is CancellationError {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw CancellationError()
        } catch let error as AuthenticatedMediaError {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw error
        } catch {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw AuthenticatedMediaError.fileStorageFailed
        }
    }

    func purge() async {
        memory.removeAll(keepingCapacity: false)
        memoryCost = 0
        await downloadStore.removeAll()
    }

    func release(_ localURL: URL) async {
        await downloadStore.remove(localURL)
    }

    private func requiredToken() async throws -> String {
        do {
            guard let token = try await authorizer.bearerToken(), Self.isSafeToken(token) else {
                throw APIError.unauthenticated(nil)
            }
            return token
        } catch {
            if Self.isTerminalSessionError(error) {
                await endSession()
            }
            throw error
        }
    }

    private func performDataRequest(
        _ url: URL,
        purpose: AuthenticatedMediaPurpose,
        bearerToken: String,
        mayRefresh: Bool
    ) async throws -> Data {
        let request = makeRequest(url, purpose: purpose, bearerToken: bearerToken)
        let bytes: URLSession.AsyncBytes
        let response: URLResponse
        do {
            (bytes, response) = try await session.bytes(for: request, delegate: redirectDelegate)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as URLError {
            throw APIError.transport(error.code)
        } catch {
            throw APIError.transport(.unknown)
        }

        let httpResponse = try validatedHTTPResponse(response)
        if httpResponse.statusCode == 401 {
            if mayRefresh, let refreshed = try await refreshedToken(rejecting: bearerToken) {
                return try await performDataRequest(
                    url,
                    purpose: purpose,
                    bearerToken: refreshed,
                    mayRefresh: false
                )
            }
            await endSession()
            throw APIError.unauthenticated(nil)
        }
        try validateSuccess(httpResponse, body: Data())

        let contentType = httpResponse.value(forHTTPHeaderField: "Content-Type")?.lowercased()
        guard !purpose.isImage || contentType?.hasPrefix("image/") == true else {
            throw AuthenticatedMediaError.unsupportedContent(contentType)
        }
        if let expected = httpResponse.expectedContentLength.nonnegativeInt,
           expected > purpose.maximumByteCount
        {
            throw AuthenticatedMediaError.responseTooLarge
        }
        var data = Data()
        if let expected = httpResponse.expectedContentLength.nonnegativeInt {
            data.reserveCapacity(min(expected, purpose.maximumByteCount))
        }
        do {
            for try await byte in bytes {
                try Task.checkCancellation()
                guard data.count < purpose.maximumByteCount else {
                    throw AuthenticatedMediaError.responseTooLarge
                }
                data.append(byte)
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as AuthenticatedMediaError {
            throw error
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as URLError {
            throw APIError.transport(error.code)
        } catch {
            throw APIError.transport(.unknown)
        }
        return data
    }

    private func performDownload(
        _ url: URL,
        bearerToken: String,
        mayRefresh: Bool
    ) async throws -> URL {
        let request = makeRequest(url, purpose: .download, bearerToken: bearerToken)
        let temporaryURL: URL
        do {
            temporaryURL = try await downloadStore.makeStagingFile()
        } catch {
            throw AuthenticatedMediaError.fileStorageFailed
        }

        // Do not use URLSession.download here. That API writes the entire body to a
        // system-owned temporary file before returning it, which makes a missing or
        // dishonest Content-Length unsafe. This data-task delegate writes bounded
        // chunks directly to a protected, app-owned staging file instead.
        let transfer = BoundedMediaDownloadTransfer(
            temporaryURL: temporaryURL,
            byteLimit: downloadByteLimit
        )
        let delegateQueue = OperationQueue()
        delegateQueue.maxConcurrentOperationCount = 1
        let transferSession = URLSession(
            configuration: session.configuration,
            delegate: transfer,
            delegateQueue: delegateQueue
        )
        defer { transferSession.invalidateAndCancel() }

        do {
            let response = try await transfer.perform(
                using: transferSession,
                request: request
            )

            let httpResponse = try validatedHTTPResponse(response)
            if httpResponse.statusCode == 401 {
                try? FileManager.default.removeItem(at: temporaryURL)
                if mayRefresh, let refreshed = try await refreshedToken(rejecting: bearerToken) {
                    return try await performDownload(
                        url,
                        bearerToken: refreshed,
                        mayRefresh: false
                    )
                }
                await endSession()
                throw APIError.unauthenticated(nil)
            }

            try validateSuccess(httpResponse, body: Data())
            return temporaryURL
        } catch {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw error
        }
    }

    private func makeRequest(
        _ url: URL,
        purpose: AuthenticatedMediaPurpose,
        bearerToken: String
    ) -> URLRequest {
        var request = URLRequest(
            url: url,
            cachePolicy: .reloadIgnoringLocalCacheData,
            timeoutInterval: purpose == .download ? 180 : 45
        )
        request.httpMethod = "GET"
        request.setValue(purpose.acceptHeader, forHTTPHeaderField: "Accept")
        request.setValue(clientVersion, forHTTPHeaderField: "X-Mise-Client-Version")
        request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        return request
    }

    private func validatedURL(
        _ suppliedURL: URL,
        purpose: AuthenticatedMediaPurpose
    ) throws -> URL {
        let resolved = URL(string: suppliedURL.relativeString, relativeTo: origin)?.absoluteURL
        guard let url = resolved,
              Self.sameOrigin(url, origin),
              url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil,
              let urlComponents = URLComponents(url: url, resolvingAgainstBaseURL: false),
              !urlComponents.percentEncodedPath.contains("%")
        else {
            throw AuthenticatedMediaError.invalidURL
        }

        let components = url.path.split(separator: "/", omittingEmptySubsequences: true)
        guard isAllowedMediaPath(components, purpose: purpose) else {
            throw AuthenticatedMediaError.invalidURL
        }
        return url
    }

    private func isAllowedMediaPath(
        _ components: [Substring],
        purpose: AuthenticatedMediaPurpose
    ) -> Bool {
        switch routeProfile {
        case .clientGallery:
            return components.count == 7
                && components[0] == "api"
                && components[1] == "v1"
                && components[2] == "client"
                && components[3] == "gallery"
                && components[4] == "assets"
                && Int64(components[5]).map { $0 > 0 } == true
                && components[6] == Substring(purpose.rawValue)
        case .ownerCull:
            // Native cull review intentionally has no original/download route.
            return (purpose == .thumbnail || purpose == .preview)
                && components.count == 8
                && components[0] == "api"
                && components[1] == "v1"
                && components[2] == "galleries"
                && Int64(components[3]).map { $0 > 0 } == true
                && components[4] == "cull"
                && components[5] == "assets"
                && Int64(components[6]).map { $0 > 0 } == true
                && components[7] == Substring(purpose.rawValue)
        }
    }

    private func validatedHTTPResponse(_ response: URLResponse) throws -> HTTPURLResponse {
        guard let response = response as? HTTPURLResponse else {
            throw APIError.unexpectedResponse
        }
        if (300..<400).contains(response.statusCode) {
            let location = response.value(forHTTPHeaderField: "Location")
                .flatMap { URL(string: $0, relativeTo: response.url)?.absoluteURL }
            throw APIError.unexpectedRedirect(location)
        }
        return response
    }

    private func validateSuccess(_ response: HTTPURLResponse, body: Data) throws {
        guard !(200..<300).contains(response.statusCode) else { return }
        let problem = Self.problem(from: body)
        switch response.statusCode {
        case 402: throw APIError.subscriptionRequired(problem)
        case 403: throw APIError.forbidden(problem)
        case 404: throw APIError.notFound(problem)
        case 410: throw APIError.gone(problem)
        case 429: throw APIError.rateLimited(retryAfter: nil, problem: problem)
        case 500...599: throw APIError.server(status: response.statusCode, problem: problem)
        default: throw APIError.http(status: response.statusCode, problem: problem)
        }
    }

    private func insert(_ data: Data, for key: String) {
        guard data.count <= maximumCachedObjectSize, memoryLimit > 0 else { return }
        accessCounter &+= 1
        if let previous = memory.updateValue(
            MemoryEntry(data: data, lastAccess: accessCounter),
            forKey: key
        ) {
            memoryCost -= previous.data.count
        }
        memoryCost += data.count

        while memoryCost > memoryLimit,
              let oldest = memory.min(by: { $0.value.lastAccess < $1.value.lastAccess })
        {
            memoryCost -= oldest.value.data.count
            memory.removeValue(forKey: oldest.key)
        }
    }

    private func endSession() async {
        await lifetime.end()
        await authorizer.invalidate()
        memory.removeAll(keepingCapacity: false)
        memoryCost = 0
        await downloadStore.removeAll()
        await onSessionEnded()
    }

    private func refreshedToken(rejecting bearerToken: String) async throws -> String? {
        do {
            guard let refreshed = try await authorizer.refreshBearerToken(
                rejectedToken: bearerToken
            ) else {
                return nil
            }
            guard Self.isSafeToken(refreshed) else {
                throw APIError.unauthenticated(nil)
            }
            return refreshed
        } catch {
            if Self.isTerminalSessionError(error) {
                await endSession()
            }
            throw error
        }
    }

    private static func sameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        guard let lhsScheme = lhs.scheme?.lowercased(),
              let rhsScheme = rhs.scheme?.lowercased(),
              let lhsHost = lhs.host?.lowercased(),
              let rhsHost = rhs.host?.lowercased()
        else {
            return false
        }
        let lhsPort = lhs.port ?? (lhsScheme == "https" ? 443 : 80)
        let rhsPort = rhs.port ?? (rhsScheme == "https" ? 443 : 80)
        return lhsScheme == rhsScheme && lhsHost == rhsHost && lhsPort == rhsPort
    }

    private static func isSafeToken(_ token: String) -> Bool {
        !token.isEmpty && token.rangeOfCharacter(from: .whitespacesAndNewlines) == nil
    }

    private static func isTerminalSessionError(_ error: Error) -> Bool {
        if error is SessionError { return true }
        if let apiError = error as? APIError,
           case .unauthenticated = apiError
        {
            return true
        }
        return false
    }

    private static func problem(from data: Data) -> APIProblem? {
        guard !data.isEmpty else { return nil }
        return try? MiseJSON.decoder().decode(APIProblem.self, from: data)
    }
}

private final class BoundedMediaDownloadTransfer: NSObject, URLSessionDataDelegate,
    URLSessionTaskDelegate, @unchecked Sendable
{
    private let lock = NSLock()
    private let temporaryURL: URL
    private let byteLimit: Int64

    private var continuation: CheckedContinuation<HTTPURLResponse, Error>?
    private var task: URLSessionDataTask?
    private var fileHandle: FileHandle?
    private var response: HTTPURLResponse?
    private var receivedByteCount: Int64 = 0
    private var acceptsBody = false
    private var finished = false
    private var cancellationRequested = false

    init(temporaryURL: URL, byteLimit: Int64) {
        self.temporaryURL = temporaryURL
        self.byteLimit = max(0, byteLimit)
    }

    // MARK: - URLSession bridge

    func perform(
        using session: URLSession,
        request: URLRequest
    ) async throws -> HTTPURLResponse {
        try await withTaskCancellationHandler(
            operation: {
                try await withCheckedThrowingContinuation { continuation in
                    let task: URLSessionDataTask
                    let wasCancelled: Bool
                    self.lock.lock()
                    if self.finished {
                        self.lock.unlock()
                        continuation.resume(throwing: CancellationError())
                        return
                    }
                    self.continuation = continuation
                    task = session.dataTask(with: request)
                    self.task = task
                    wasCancelled = self.cancellationRequested
                    if wasCancelled {
                        self.finished = true
                        self.task = nil
                        self.continuation = nil
                    }
                    self.lock.unlock()

                    if wasCancelled {
                        task.cancel()
                        continuation.resume(throwing: CancellationError())
                    } else {
                        task.resume()
                    }
                }
            },
            onCancel: { [weak self] in
                self?.cancel()
            }
        )
    }

    private func cancel() {
        let task: URLSessionDataTask?
        lock.lock()
        cancellationRequested = true
        task = self.task
        lock.unlock()
        task?.cancel()
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void
    ) {
        guard let response = response as? HTTPURLResponse else {
            complete(.failure(APIError.unexpectedResponse))
            completionHandler(.cancel)
            dataTask.cancel()
            return
        }

        guard (200..<300).contains(response.statusCode) else {
            setResponse(response)
            complete(.success(response))
            completionHandler(.cancel)
            dataTask.cancel()
            return
        }

        if byteLimit > 0,
           response.expectedContentLength >= 0,
           response.expectedContentLength > byteLimit
        {
            complete(.failure(AuthenticatedMediaError.downloadTooLarge))
            completionHandler(.cancel)
            dataTask.cancel()
            return
        }

        let fileHandle: FileHandle
        do {
            fileHandle = try FileHandle(forWritingTo: temporaryURL)
        } catch {
            complete(.failure(AuthenticatedMediaError.fileStorageFailed))
            completionHandler(.cancel)
            dataTask.cancel()
            return
        }

        lock.lock()
        guard !finished else {
            lock.unlock()
            try? fileHandle.close()
            completionHandler(.cancel)
            dataTask.cancel()
            return
        }
        self.response = response
        self.fileHandle = fileHandle
        acceptsBody = true
        lock.unlock()
        completionHandler(.allow)
    }

    private func setResponse(_ response: HTTPURLResponse) {
        lock.lock()
        guard !finished else {
            lock.unlock()
            return
        }
        self.response = response
        lock.unlock()
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive data: Data
    ) {
        guard !data.isEmpty else { return }

        lock.lock()
        guard !finished, acceptsBody, let fileHandle else {
            lock.unlock()
            return
        }
        let incomingByteCount = Int64(data.count)
        let (nextByteCount, overflow) = receivedByteCount.addingReportingOverflow(incomingByteCount)
        guard !overflow,
              byteLimit == 0 || nextByteCount <= byteLimit
        else {
            lock.unlock()
            complete(.failure(AuthenticatedMediaError.downloadTooLarge))
            dataTask.cancel()
            return
        }

        do {
            try fileHandle.write(contentsOf: data)
            receivedByteCount = nextByteCount
            lock.unlock()
        } catch {
            lock.unlock()
            complete(.failure(AuthenticatedMediaError.fileStorageFailed))
            dataTask.cancel()
        }
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        guard let error else {
            lock.lock()
            let response = self.response
            let wasCancelled = cancellationRequested
            lock.unlock()
            if wasCancelled {
                complete(.failure(CancellationError()))
            } else if let response {
                complete(.success(response))
            } else {
                complete(.failure(APIError.unexpectedResponse))
            }
            return
        }

        complete(.failure(transportError(from: error)))
    }

    private func complete(_ result: Result<HTTPURLResponse, Error>) {
        let continuation: CheckedContinuation<HTTPURLResponse, Error>?
        let fileHandle: FileHandle?

        lock.lock()
        guard !finished else {
            lock.unlock()
            return
        }
        finished = true
        acceptsBody = false
        continuation = self.continuation
        self.continuation = nil
        self.task = nil
        fileHandle = self.fileHandle
        self.fileHandle = nil
        lock.unlock()

        var finalResult = result
        if let fileHandle {
            do {
                try fileHandle.close()
            } catch {
                if case .success = finalResult {
                    finalResult = .failure(AuthenticatedMediaError.fileStorageFailed)
                }
            }
        }
        continuation?.resume(with: finalResult)
    }

    private func transportError(from error: Error) -> Error {
        if error is CancellationError {
            return CancellationError()
        }
        if let error = error as? URLError {
            return error.code == .cancelled
                ? CancellationError()
                : APIError.transport(error.code)
        }
        return APIError.transport(.unknown)
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        let location = response.value(forHTTPHeaderField: "Location")
            .flatMap { URL(string: $0, relativeTo: response.url)?.absoluteURL }
        complete(.failure(APIError.unexpectedRedirect(location)))
        completionHandler(nil)
        task.cancel()
    }

}

private actor ProtectedMediaDownloadStore {
    private let fileManager = FileManager.default
    private let directory: URL
    private let byteLimit: Int64
    private let fileLimit: Int

    init(
        cacheNamespace: String,
        rootDirectory: URL?,
        byteLimit: Int64,
        fileLimit: Int
    ) {
        let fileManager = FileManager.default
        let root = rootDirectory
            ?? fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? fileManager.temporaryDirectory
        directory = root
            .appendingPathComponent("Mise", isDirectory: true)
            .appendingPathComponent("ClientDownloads", isDirectory: true)
            .appendingPathComponent(Self.digest(cacheNamespace), isDirectory: true)
        self.byteLimit = max(0, byteLimit)
        self.fileLimit = max(1, fileLimit)
    }

    /// Hidden staging files are intentionally excluded from the finished-export
    /// quota. They are bounded by the transfer before any bytes are written and
    /// are removed on every terminal transfer path.
    func makeStagingFile() throws -> URL {
        try prepareDirectory()
        let temporaryURL = directory.appendingPathComponent(
            ".incoming-\(UUID().uuidString.lowercased())"
        )
        guard fileManager.createFile(
            atPath: temporaryURL.path,
            contents: nil,
            attributes: [.protectionKey: FileProtectionType.complete]
        ) else {
            throw AuthenticatedMediaError.fileStorageFailed
        }
        do {
            try fileManager.setAttributes(
                [.protectionKey: FileProtectionType.complete],
                ofItemAtPath: temporaryURL.path
            )
            return temporaryURL
        } catch {
            try? fileManager.removeItem(at: temporaryURL)
            throw error
        }
    }

    func store(temporaryURL: URL, suggestedFilename: String) throws -> URL {
        try prepareDirectory()
        guard let values = try? temporaryURL.resourceValues(forKeys: [.fileSizeKey]),
              let fileSize = values.fileSize
        else {
            throw AuthenticatedMediaError.fileStorageFailed
        }
        let incomingBytes = Int64(max(0, fileSize))
        guard byteLimit == 0 || incomingBytes <= byteLimit else {
            throw AuthenticatedMediaError.downloadTooLarge
        }
        try enforceQuota(incomingBytes: incomingBytes)
        let pathExtension = Self.safeExtension(from: suggestedFilename)
        var destination = directory.appendingPathComponent(UUID().uuidString.lowercased())
        if let pathExtension {
            destination.appendPathExtension(pathExtension)
        }
        do {
            try fileManager.moveItem(at: temporaryURL, to: destination)
            try fileManager.setAttributes(
                [.protectionKey: FileProtectionType.complete],
                ofItemAtPath: destination.path
            )
            return destination
        } catch {
            try? fileManager.removeItem(at: destination)
            throw error
        }
    }

    func removeAll() {
        try? fileManager.removeItem(at: directory)
    }

    func remove(_ localURL: URL) {
        let parent = localURL.deletingLastPathComponent().standardizedFileURL
        guard parent == directory.standardizedFileURL else { return }
        try? fileManager.removeItem(at: localURL)
    }

    private func prepareDirectory() throws {
        if !fileManager.fileExists(atPath: directory.path) {
            try fileManager.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [
                    .protectionKey: FileProtectionType.complete,
                ]
            )
        }
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var protectedDirectory = directory
        try protectedDirectory.setResourceValues(values)
    }

    private func enforceQuota(incomingBytes: Int64) throws {
        let keys: Set<URLResourceKey> = [
            .isRegularFileKey,
            .fileSizeKey,
            .contentModificationDateKey,
        ]
        var files = try fileManager.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: Array(keys),
            options: [.skipsHiddenFiles]
        ).compactMap { url -> (URL, Int64, Date)? in
            guard let values = try? url.resourceValues(forKeys: keys),
                  values.isRegularFile == true
            else {
                return nil
            }
            return (
                url,
                Int64(max(0, values.fileSize ?? 0)),
                values.contentModificationDate ?? .distantPast
            )
        }
        files.sort { $0.2 < $1.2 }
        var total = files.reduce(Int64(0)) { partial, file in
            let (sum, overflow) = partial.addingReportingOverflow(file.1)
            return overflow ? .max : sum
        }
        while !files.isEmpty,
              files.count >= fileLimit || (byteLimit > 0 && total > byteLimit - min(byteLimit, incomingBytes))
        {
            let oldest = files.removeFirst()
            try fileManager.removeItem(at: oldest.0)
            total = max(0, total - oldest.1)
        }
    }

    private static func safeExtension(from filename: String) -> String? {
        let value = (filename as NSString).pathExtension.lowercased()
        guard (1...10).contains(value.count),
              value.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) })
        else {
            return nil
        }
        return value
    }

    private static func digest(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }
}

private final class AuthenticatedMediaRedirectDelegate: NSObject, URLSessionTaskDelegate,
    @unchecked Sendable
{
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

private extension Int64 {
    var nonnegativeInt: Int? {
        guard self >= 0 else { return nil }
        return Int(exactly: self)
    }
}
