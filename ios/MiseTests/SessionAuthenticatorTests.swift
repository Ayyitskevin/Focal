import XCTest
@testable import Mise

final class SessionAuthenticatorTests: XCTestCase {
    func testConcurrentRequestsShareOneRefresh() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let oldSession = Self.session(
            origin: "https://studio.example.com",
            accessToken: "old",
            refreshToken: "refresh-old",
            accessExpiresAt: now.addingTimeInterval(-1)
        )
        let newSession = Self.session(
            origin: "https://studio.example.com",
            accessToken: "new",
            refreshToken: "refresh-new",
            accessExpiresAt: now.addingTimeInterval(900)
        )
        let persistence = InMemorySessionPersistence(oldSession)
        let refresher = SlowRefresher(response: newSession)
        let authenticator = SessionAuthenticator(
            persistence: persistence,
            refresher: refresher,
            expectedBaseURL: URL(string: "https://studio.example.com")!,
            now: { now }
        )

        async let first = authenticator.bearerToken()
        async let second = authenticator.bearerToken()
        let tokens = try await [first, second]
        let refreshCount = await refresher.callCount()

        XCTAssertEqual(tokens, ["new", "new"])
        XCTAssertEqual(refreshCount, 1)
    }

    func testStoredSessionForAnotherOriginIsPurged() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let ended = LockedBox<[String]>([])
        let persistence = InMemorySessionPersistence(
            Self.session(
                origin: "https://other.example.com",
                accessToken: "must-not-leak",
                refreshToken: "refresh",
                accessExpiresAt: now.addingTimeInterval(900),
                sessionID: "wrong-origin-session"
            )
        )
        let refresher = SlowRefresher(
            response: Self.session(
                origin: "https://other.example.com",
                accessToken: "new",
                refreshToken: "new-refresh",
                accessExpiresAt: now.addingTimeInterval(900)
            )
        )
        let authenticator = SessionAuthenticator(
            persistence: persistence,
            refresher: refresher,
            expectedBaseURL: URL(string: "https://studio.example.com")!,
            now: { now },
            onSessionEnded: { namespace, id in
                ended.withValue { $0.append("\(namespace)/\(id)") }
            }
        )

        do {
            _ = try await authenticator.bearerToken()
            XCTFail("Expected cross-origin credentials to be rejected.")
        } catch SessionError.workspaceMismatch {
            // Expected.
        }

        XCTAssertNil(try persistence.load())
        XCTAssertEqual(ended.withValue { $0 }, ["tenant_42/wrong-origin-session"])
    }

    func testRefreshPreservesSessionIDUsedByReplayJournal() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let oldSession = Self.session(
            origin: "https://studio.example.com",
            accessToken: "old",
            refreshToken: "refresh-old",
            accessExpiresAt: now.addingTimeInterval(900),
            sessionID: "session-original"
        )
        let newSession = Self.session(
            origin: "https://studio.example.com",
            accessToken: "new",
            refreshToken: "refresh-new",
            accessExpiresAt: now.addingTimeInterval(900),
            sessionID: "session-original"
        )
        let persistence = InMemorySessionPersistence(oldSession)
        let refresher = SlowRefresher(response: newSession)
        let authenticator = SessionAuthenticator(
            persistence: persistence,
            refresher: refresher,
            expectedBaseURL: URL(string: "https://studio.example.com")!,
            now: { now }
        )

        let first = try await authenticator.requestBearer()
        let parallel = try await authenticator.requestBearer()
        let firstBearer = try XCTUnwrap(first)
        let parallelBearer = try XCTUnwrap(parallel)
        XCTAssertEqual(firstBearer.token, "old")
        XCTAssertEqual(parallelBearer.token, "old")
        let rotated = try await authenticator.refreshBearer(firstBearer)
        let parallelRetry = try await authenticator.refreshBearer(parallelBearer)
        XCTAssertEqual(rotated?.token, "new")
        XCTAssertEqual(parallelRetry?.token, "new")
        XCTAssertEqual(parallelRetry?.generation, firstBearer.generation)
        let refreshCount = await refresher.callCount()
        XCTAssertEqual(refreshCount, 1)
        XCTAssertEqual(try persistence.load()?.sessionID, "session-original")
    }

    func testRefreshRejectsChangedSessionIDAndPurgesStoredSession() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let ended = LockedBox<[String]>([])
        let oldSession = Self.session(
            origin: "https://studio.example.com",
            accessToken: "old",
            refreshToken: "refresh-old",
            accessExpiresAt: now.addingTimeInterval(-1),
            sessionID: "session-original"
        )
        let newSession = Self.session(
            origin: "https://studio.example.com",
            accessToken: "new",
            refreshToken: "refresh-new",
            accessExpiresAt: now.addingTimeInterval(900),
            sessionID: "session-replaced"
        )
        let persistence = InMemorySessionPersistence(oldSession)
        let authenticator = SessionAuthenticator(
            persistence: persistence,
            refresher: SlowRefresher(response: newSession),
            expectedBaseURL: URL(string: "https://studio.example.com")!,
            now: { now },
            onSessionEnded: { namespace, id in
                ended.withValue { $0.append("\(namespace)/\(id)") }
            }
        )

        do {
            _ = try await authenticator.bearerToken()
            XCTFail("Expected a changed backend session ID to be rejected.")
        } catch SessionError.identityChanged {
            // Expected. Idempotency replay must stay bound to one backend session.
        }

        XCTAssertNil(try persistence.load())
        XCTAssertEqual(ended.withValue { $0 }, ["tenant_42/session-original"])
    }

    func testGalleryRefreshPreservesDraftOn503AndPurgesOnlyTheEndedSessionOn401() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let origin = try XCTUnwrap(URL(string: "https://studio.example.com"))
        let mediaPath = "/api/v1/media/gallery/example"
        let mediaURL = try XCTUnwrap(URL(string: mediaPath, relativeTo: origin)?.absoluteURL)
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }

        let refreshStatus = LockedBox(503)
        let paths = LockedBox<[String]>([])
        MockURLProtocol.setHandler { request in
            let url = try XCTUnwrap(request.url)
            paths.withValue { $0.append(url.path) }
            let status: Int
            switch url.path {
            case mediaPath:
                XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer gallery-token")
                status = 401
            case "/api/v1/auth/refresh":
                XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
                status = refreshStatus.withValue { $0 }
            default:
                XCTFail("Unexpected gallery or refresh request.")
                throw URLError(.unsupportedURL)
            }
            let response = try XCTUnwrap(HTTPURLResponse(
                url: url, statusCode: status, httpVersion: "HTTP/1.1",
                headerFields: ["Content-Type": "application/problem+json"]
            ))
            return (response, Data())
        }
        defer { MockURLProtocol.clearHandler() }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        config.urlCache = nil
        let network = URLSession(configuration: config)
        defer { network.invalidateAndCancel() }
        let client = APIClient(configuration: .init(baseURL: origin, clientVersion: "test"), session: network)
        let old = Self.session(
            origin: origin.absoluteString, accessToken: "gallery-token", refreshToken: "refresh",
            accessExpiresAt: now.addingTimeInterval(900), sessionID: "session-a"
        )
        let persistence = InMemorySessionPersistence(old)
        let ended = LockedBox<[String]>([])
        let authenticator = SessionAuthenticator(
            persistence: persistence, refresher: RemoteTokenRefresher(client: client), expectedBaseURL: origin,
            now: { now },
            onSessionEnded: { namespace, id in
                ended.withValue { $0.append("\(namespace)/\(id)") }
                let cache = TenantJSONCache(cacheNamespace: namespace, rootDirectory: root)
                do {
                    try await cache.removeInquiryReplies(sessionID: id)
                } catch {
                    XCTFail("Session cleanup failed: \(error)")
                }
            }
        )
        let gallery = AuthenticatedMediaLoader(session: network, authorizer: authenticator)
        func openRepository(_ sessionID: String) -> OwnerRepository {
            let cache = TenantJSONCache(cacheNamespace: old.workspace.cacheNamespace, rootDirectory: root)
            return OwnerRepository(client: client, cache: cache, sessionID: sessionID)
        }
        func saveDraft(in repository: OwnerRepository, body: String) async throws -> InquiryReplyDraft {
            let observed = try await repository.inquiryReplySnapshot(id: 123)
            return try await repository.prepareInquiryReply(id: 123, subject: "Inquiry", body: body, observing: observed)
        }
        let draftA = try await saveDraft(in: openRepository("session-a"), body: "First private gallery reply")
        let draftB = try await saveDraft(in: openRepository("session-b"), body: "Second private gallery reply")

        do {
            _ = try await gallery.data(for: mediaURL)
            XCTFail("Expected the refresh service outage.")
        } catch APIError.server(let status, _) {
            XCTAssertEqual(status, 503)
        }
        let retained = try await openRepository("session-a").pendingInquiryReply(id: 123)
        XCTAssertEqual(retained, draftA)
        XCTAssertEqual(try persistence.load(), old)
        XCTAssertTrue(ended.withValue { $0.isEmpty })

        refreshStatus.withValue { $0 = 401 }
        do {
            _ = try await gallery.data(for: mediaURL)
            XCTFail("Expected the rejected gallery refresh to end the session.")
        } catch SessionError.expired {
            // Authentication owns the cleanup even though no owner repository made either request.
        }
        XCTAssertEqual(paths.withValue { $0 }, [mediaPath, "/api/v1/auth/refresh", mediaPath, "/api/v1/auth/refresh"])
        XCTAssertNil(try persistence.load())
        XCTAssertEqual(ended.withValue { $0 }, ["tenant_42/session-a"])
        let removed = try await openRepository("session-a").pendingInquiryReply(id: 123)
        let preserved = try await openRepository("session-b").pendingInquiryReply(id: 123)
        XCTAssertNil(removed)
        XCTAssertEqual(preserved, draftB)
    }

    func testLateRefreshRejectionPreservesReplacementSession() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let origin = try XCTUnwrap(URL(string: "https://studio.example.com"))
        let old = Self.session(
            origin: origin.absoluteString, accessToken: "old", refreshToken: "refresh-a",
            accessExpiresAt: now.addingTimeInterval(900), sessionID: "session-a"
        )
        let replacement = Self.session(
            origin: origin.absoluteString, accessToken: old.accessToken, refreshToken: "refresh-b",
            accessExpiresAt: now.addingTimeInterval(900), sessionID: "session-b"
        )
        let persistence = InMemorySessionPersistence(old)
        let ended = LockedBox<[String]>([])
        let refresher = SuspendedRejectedRefresh()
        let authenticator = SessionAuthenticator(
            persistence: persistence, refresher: refresher, expectedBaseURL: origin, now: { now },
            onSessionEnded: { _, id in ended.withValue { $0.append(id) } }
        )

        let issued = try await authenticator.requestBearer()
        let originalBearer = try XCTUnwrap(issued)
        let stale = Task { try await authenticator.refreshBearer(originalBearer) }
        await refresher.waitUntilStarted()
        try await authenticator.install(replacement)
        await refresher.reject()
        let newRequest = try await authenticator.requestBearer()
        let replacementBearer = try XCTUnwrap(newRequest)
        XCTAssertEqual(replacementBearer.token, originalBearer.token)
        XCTAssertNotEqual(replacementBearer.generation, originalBearer.generation)
        do {
            _ = try await stale.value
            XCTFail("The stale refresh unexpectedly succeeded.")
        } catch is CancellationError {
            // The outdated request ends without reporting expiry to the replacement.
        }
        do {
            _ = try await authenticator.refreshBearer(originalBearer)
            XCTFail("A delayed 401 must not acquire the replacement's token.")
        } catch is CancellationError {
            // A newly installed session can't retry requests from the previous one.
        }
        do {
            try await authenticator.invalidate(ifRejected: originalBearer)
            XCTFail("The old request must not invalidate the replacement on its final 401.")
        } catch is CancellationError {
            // Both requests used the same token string but carry different session generations.
        }
        let active = try await authenticator.sessionSnapshot()
        XCTAssertEqual(active, replacement)
        XCTAssertEqual(try persistence.load(), replacement)
        XCTAssertEqual(ended.withValue { $0 }, ["session-a"])
    }

    private static func session(
        origin: String,
        accessToken: String,
        refreshToken: String,
        accessExpiresAt: Date,
        sessionID: String? = nil
    ) -> AuthSession {
        AuthSession(
            accessToken: accessToken,
            refreshToken: refreshToken,
            tokenType: "Bearer",
            accessTokenExpiresAt: accessExpiresAt,
            refreshTokenExpiresAt: accessExpiresAt.addingTimeInterval(86_400),
            workspace: WorkspaceContext(
                cacheNamespace: "tenant_42",
                slug: "studio",
                displayName: "Studio",
                apiBaseURL: URL(string: origin)!,
                brandAccentHex: nil,
                timeZone: "America/New_York",
                currencyCode: "USD"
            ),
            principal: Principal(
                id: "studio_owner",
                kind: .studioOwner,
                displayName: "Studio",
                email: nil,
                scopes: ["studio:read"]
            ),
            sessionID: sessionID
        )
    }
}
private final class InMemorySessionPersistence: SessionPersisting, @unchecked Sendable {
    private let lock = NSLock()
    private var session: AuthSession?

    init(_ session: AuthSession?) {
        self.session = session
    }

    func load() throws -> AuthSession? {
        lock.lock()
        defer { lock.unlock() }
        return session
    }

    func save(_ session: AuthSession) throws {
        lock.lock()
        self.session = session
        lock.unlock()
    }

    func delete() throws {
        lock.lock()
        session = nil
        lock.unlock()
    }
}

private actor SlowRefresher: TokenRefreshing {
    private let response: AuthSession
    private var calls = 0

    init(response: AuthSession) {
        self.response = response
    }

    func refresh(refreshToken: String) async throws -> AuthSession {
        calls += 1
        try await Task.sleep(nanoseconds: 50_000_000)
        return response
    }

    func callCount() -> Int {
        calls
    }
}

private actor SuspendedRejectedRefresh: TokenRefreshing {
    private var pending: CheckedContinuation<AuthSession, Error>?
    private var started: CheckedContinuation<Void, Never>?
    private var rejected = false

    func refresh(refreshToken: String) async throws -> AuthSession {
        if rejected { throw APIError.unauthenticated(nil) }
        return try await withCheckedThrowingContinuation { continuation in
            pending = continuation
            started?.resume()
            started = nil
        }
    }

    func waitUntilStarted() async {
        if pending != nil { return }
        await withCheckedContinuation { started = $0 }
    }

    func reject() {
        rejected = true
        pending?.resume(throwing: APIError.unauthenticated(nil))
        pending = nil
    }
}
