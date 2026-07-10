import XCTest
@testable import Mise

final class AuthenticatedMediaClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.clearHandler()
        super.tearDown()
    }

    func testAttachesBearerOnlyToExactSameOriginMediaRouteAndCachesResult() async throws {
        let requestCount = LockedBox(0)
        let authorization = LockedBox<String?>(nil)
        MockURLProtocol.setHandler { request in
            requestCount.withValue { $0 += 1 }
            authorization.withValue { $0 = request.value(forHTTPHeaderField: "Authorization") }
            return (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Type": "image/jpeg"]
                ),
                Data([1, 2, 3])
            )
        }

        let authorizer = MediaAuthorizer(mode: .tokens(initial: "access", refreshed: "new"))
        let client = makeClient(authorizer: authorizer)
        let url = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/41/thumbnail")!

        let first = try await client.data(from: url, purpose: .thumbnail)
        let second = try await client.data(from: url, purpose: .thumbnail)
        let revised = try await client.data(
            from: url,
            purpose: .thumbnail,
            contentRevision: 2
        )

        XCTAssertEqual(first, Data([1, 2, 3]))
        XCTAssertEqual(second, first)
        XCTAssertEqual(revised, first)
        XCTAssertEqual(requestCount.withValue { $0 }, 2)
        XCTAssertEqual(authorization.withValue { $0 }, "Bearer access")
    }

    func testRejectsCrossOriginAndMismatchedPurposeBeforeReadingToken() async throws {
        let authorizer = MediaAuthorizer(mode: .tokens(initial: "secret", refreshed: "new"))
        let client = makeClient(authorizer: authorizer)

        for (url, purpose) in [
            (
                URL(string: "https://evil.example/api/v1/client/gallery/assets/1/thumbnail")!,
                AuthenticatedMediaPurpose.thumbnail
            ),
            (
                URL(string: "https://studio.example.com/api/v1/client/gallery/assets/1/preview")!,
                AuthenticatedMediaPurpose.thumbnail
            ),
            (
                URL(string: "https://studio.example.com/api/v1/client/gallery/assets/1/thumbnail?next=x")!,
                AuthenticatedMediaPurpose.thumbnail
            ),
        ] {
            do {
                _ = try await client.data(from: url, purpose: purpose)
                XCTFail("Expected media URL rejection.")
            } catch AuthenticatedMediaError.invalidURL {
                // Expected before any bearer token is requested.
            }
        }

        let bearerCalls = await authorizer.bearerCallCount()
        XCTAssertEqual(bearerCalls, 0)
    }

    func testRetriesOneUnauthorizedRequestWithRotatedToken() async throws {
        let count = LockedBox(0)
        let tokens = LockedBox<[String]>([])
        MockURLProtocol.setHandler { request in
            let index = count.withValue {
                $0 += 1
                return $0
            }
            tokens.withValue {
                $0.append(request.value(forHTTPHeaderField: "Authorization") ?? "")
            }
            if index == 1 {
                return (
                    Self.response(
                        url: request.url!,
                        status: 401,
                        headers: ["Content-Type": "application/problem+json"]
                    ),
                    Data(#"{"detail":"expired"}"#.utf8)
                )
            }
            return (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Type": "image/jpeg"]
                ),
                Data([9])
            )
        }

        let authorizer = MediaAuthorizer(mode: .tokens(initial: "old", refreshed: "rotated"))
        let client = makeClient(authorizer: authorizer)
        let url = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/2/preview")!

        let result = try await client.data(from: url, purpose: .preview)

        XCTAssertEqual(result, Data([9]))
        XCTAssertEqual(tokens.withValue { $0 }, ["Bearer old", "Bearer rotated"])
        let refreshCalls = await authorizer.refreshCallCount()
        let invalidations = await authorizer.invalidationCount()
        XCTAssertEqual(refreshCalls, 1)
        XCTAssertEqual(invalidations, 0)
    }

    func testProactiveTerminalSessionFailurePurgesCapabilityData() async throws {
        let ended = LockedBox(0)
        let authorizer = MediaAuthorizer(mode: .bearerFailure(.expired))
        let client = makeClient(
            authorizer: authorizer,
            onSessionEnded: { ended.withValue { $0 += 1 } }
        )
        let url = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/2/poster")!

        do {
            _ = try await client.data(from: url, purpose: .poster)
            XCTFail("Expected terminal session failure.")
        } catch SessionError.expired {
            // Expected.
        }

        XCTAssertEqual(ended.withValue { $0 }, 1)
        let invalidations = await authorizer.invalidationCount()
        XCTAssertEqual(invalidations, 1)
    }

    func testTerminalRefreshFailurePurgesCapabilityData() async throws {
        let ended = LockedBox(0)
        MockURLProtocol.setHandler { request in
            (
                Self.response(
                    url: request.url!,
                    status: 401,
                    headers: ["Content-Type": "application/problem+json"]
                ),
                Data(#"{"detail":"expired"}"#.utf8)
            )
        }
        let authorizer = MediaAuthorizer(mode: .refreshFailure(.expired))
        let client = makeClient(
            authorizer: authorizer,
            onSessionEnded: { ended.withValue { $0 += 1 } }
        )
        let url = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/2/preview")!

        do {
            _ = try await client.data(from: url, purpose: .preview)
            XCTFail("Expected terminal refresh failure.")
        } catch SessionError.expired {
            // Expected.
        }

        let invalidations = await authorizer.invalidationCount()
        XCTAssertEqual(ended.withValue { $0 }, 1)
        XCTAssertEqual(invalidations, 1)
    }

    func testBoundedMemoryCacheEvictsLeastRecentlyUsedResponse() async throws {
        let requests = LockedBox<[String]>([])
        MockURLProtocol.setHandler { request in
            requests.withValue { $0.append(request.url!.path) }
            return (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Type": "image/jpeg"]
                ),
                Data([1, 2])
            )
        }
        let client = makeClient(
            authorizer: MediaAuthorizer(mode: .tokens(initial: "access", refreshed: "new")),
            memoryLimit: 3
        )
        let first = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/1/thumbnail")!
        let second = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/2/thumbnail")!

        _ = try await client.data(from: first, purpose: .thumbnail)
        _ = try await client.data(from: second, purpose: .thumbnail)
        _ = try await client.data(from: first, purpose: .thumbnail)

        XCTAssertEqual(requests.withValue { $0 }.count, 3)
    }

    func testDownloadRejectsAdvertisedLengthBeforeWritingAStagingFile() async throws {
        let downloadRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: downloadRoot) }
        MockURLProtocol.setHandler { request in
            (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Length": "9"]
                ),
                Data(repeating: 7, count: 9)
            )
        }

        let client = makeClient(
            authorizer: MediaAuthorizer(mode: .tokens(initial: "access", refreshed: "new")),
            downloadByteLimit: 8,
            downloadRoot: downloadRoot
        )
        let url = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/9/download")!

        do {
            _ = try await client.download(from: url, suggestedFilename: "original.jpg")
            XCTFail("Expected the advertised content length to be rejected.")
        } catch AuthenticatedMediaError.downloadTooLarge {
            // Expected.
        }

        XCTAssertTrue(Self.regularFiles(in: downloadRoot).isEmpty)
    }

    func testDownloadCancelsAnUnknownLengthStreamAtTheByteCap() async throws {
        let downloadRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: downloadRoot) }
        MockURLProtocol.setHandler { request in
            (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Type": "application/octet-stream"]
                ),
                Data(repeating: 4, count: 9)
            )
        }

        let client = makeClient(
            authorizer: MediaAuthorizer(mode: .tokens(initial: "access", refreshed: "new")),
            downloadByteLimit: 8,
            downloadRoot: downloadRoot
        )
        let url = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/10/download")!

        do {
            _ = try await client.download(from: url, suggestedFilename: "original.jpg")
            XCTFail("Expected the streamed byte cap to be enforced.")
        } catch AuthenticatedMediaError.downloadTooLarge {
            // Expected.
        }

        XCTAssertTrue(Self.regularFiles(in: downloadRoot).isEmpty)
    }

    func testDownloadRefreshesOnceAndKeepsOnlyTheSuccessfulAttempt() async throws {
        let downloadRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: downloadRoot) }
        let attempts = LockedBox(0)
        let tokens = LockedBox<[String]>([])
        MockURLProtocol.setHandler { request in
            let attempt = attempts.withValue {
                $0 += 1
                return $0
            }
            tokens.withValue {
                $0.append(request.value(forHTTPHeaderField: "Authorization") ?? "")
            }
            if attempt == 1 {
                return (
                    Self.response(
                        url: request.url!,
                        status: 401,
                        headers: ["Content-Type": "application/problem+json"]
                    ),
                    Data(#"{"detail":"expired"}"#.utf8)
                )
            }
            return (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Length": "3"]
                ),
                Data([8, 6, 7])
            )
        }

        let authorizer = MediaAuthorizer(mode: .tokens(initial: "old", refreshed: "rotated"))
        let client = makeClient(
            authorizer: authorizer,
            downloadByteLimit: 8,
            downloadRoot: downloadRoot
        )
        let url = URL(string: "https://studio.example.com/api/v1/client/gallery/assets/11/download")!
        let localURL = try await client.download(from: url, suggestedFilename: "original.jpg")

        XCTAssertEqual(try Data(contentsOf: localURL), Data([8, 6, 7]))
        XCTAssertEqual(tokens.withValue { $0 }, ["Bearer old", "Bearer rotated"])
        let refreshCalls = await authorizer.refreshCallCount()
        XCTAssertEqual(refreshCalls, 1)

        await client.release(localURL)
        XCTAssertTrue(Self.regularFiles(in: downloadRoot).isEmpty)
    }

    private func makeClient(
        authorizer: MediaAuthorizer,
        memoryLimit: Int = 48 * 1_024 * 1_024,
        downloadByteLimit: Int64 = 2 * 1_024 * 1_024 * 1_024,
        downloadRoot: URL? = nil,
        onSessionEnded: @escaping @Sendable () async -> Void = {}
    ) -> AuthenticatedMediaClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        return AuthenticatedMediaClient(
            origin: URL(string: "https://studio.example.com")!,
            clientVersion: "1.0 (1)",
            session: URLSession(configuration: configuration),
            authorizer: authorizer,
            cacheNamespace: "tenant\0capability\0gallery_guest:7",
            memoryLimit: memoryLimit,
            downloadByteLimit: downloadByteLimit,
            downloadRoot: downloadRoot,
            onSessionEnded: onSessionEnded
        )
    }

    private static func response(
        url: URL,
        status: Int,
        headers: [String: String]
    ) -> HTTPURLResponse {
        HTTPURLResponse(
            url: url,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: headers
        )!
    }

    private static func regularFiles(in directory: URL) -> [URL] {
        guard let enumerator = FileManager.default.enumerator(
            at: directory,
            includingPropertiesForKeys: [.isRegularFileKey]
        ) else {
            return []
        }
        var files: [URL] = []
        while let url = enumerator.nextObject() as? URL {
            guard let values = try? url.resourceValues(forKeys: [.isRegularFileKey]),
                  values.isRegularFile == true
            else {
                continue
            }
            files.append(url)
        }
        return files
    }
}

private actor MediaAuthorizer: RequestAuthorizing {
    enum Mode: Sendable {
        case tokens(initial: String, refreshed: String?)
        case bearerFailure(SessionError)
        case refreshFailure(SessionError)
    }

    private let mode: Mode
    private var bearerCalls = 0
    private var refreshCalls = 0
    private var invalidations = 0

    init(mode: Mode) { self.mode = mode }

    func bearerToken() async throws -> String? {
        bearerCalls += 1
        switch mode {
        case let .tokens(initial, _): return initial
        case .refreshFailure: return "old"
        case let .bearerFailure(error):
            throw error
        }
    }

    func refreshBearerToken(rejectedToken: String) async throws -> String? {
        refreshCalls += 1
        switch mode {
        case let .tokens(_, refreshed): return refreshed
        case let .refreshFailure(error), let .bearerFailure(error): throw error
        }
    }

    func invalidate() { invalidations += 1 }
    func bearerCallCount() -> Int { bearerCalls }
    func refreshCallCount() -> Int { refreshCalls }
    func invalidationCount() -> Int { invalidations }
}
