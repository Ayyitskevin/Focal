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
        let persistence = InMemorySessionPersistence(
            Self.session(
                origin: "https://other.example.com",
                accessToken: "must-not-leak",
                refreshToken: "refresh",
                accessExpiresAt: now.addingTimeInterval(900)
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
            now: { now }
        )

        do {
            _ = try await authenticator.bearerToken()
            XCTFail("Expected cross-origin credentials to be rejected.")
        } catch SessionError.workspaceMismatch {
            // Expected.
        }

        XCTAssertNil(try persistence.load())
    }

    func testRefreshPreservesSessionIDUsedByReplayJournal() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
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
            sessionID: "session-original"
        )
        let persistence = InMemorySessionPersistence(oldSession)
        let authenticator = SessionAuthenticator(
            persistence: persistence,
            refresher: SlowRefresher(response: newSession),
            expectedBaseURL: URL(string: "https://studio.example.com")!,
            now: { now }
        )

        let token = try await authenticator.bearerToken()
        XCTAssertEqual(token, "new")
        XCTAssertEqual(try persistence.load()?.sessionID, "session-original")
    }

    func testRefreshRejectsChangedSessionIDAndPurgesStoredSession() async throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
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
            now: { now }
        )

        do {
            _ = try await authenticator.bearerToken()
            XCTFail("Expected a changed backend session ID to be rejected.")
        } catch SessionError.identityChanged {
            // Expected. Idempotency replay must stay bound to one backend session.
        }

        XCTAssertNil(try persistence.load())
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
