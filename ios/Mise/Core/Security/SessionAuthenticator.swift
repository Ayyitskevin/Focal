import Foundation

protocol TokenRefreshing: Sendable {
    func refresh(refreshToken: String) async throws -> AuthSession
}
struct RemoteTokenRefresher: TokenRefreshing {
    let client: any APIClientProtocol

    func refresh(refreshToken: String) async throws -> AuthSession {
        let endpoint = try MiseEndpoints.Auth.refresh(
            RefreshTokenRequest(refreshToken: refreshToken)
        )
        return try await client.send(endpoint)
    }
}

actor SessionAuthenticator: RequestAuthorizing {
    // Windows can share a Keychain account through separate authenticator actors.
    // Keep each ownership check and its write together across those actors.
    private static let persistenceLock = NSLock()
    private let persistence: any SessionPersisting
    private let refresher: any TokenRefreshing
    private let expectedOrigin: ServerOrigin
    private let now: @Sendable () -> Date
    private let onSessionEnded: @Sendable (String, String) async -> Void

    private var loaded = false
    private var currentSession: AuthSession?
    private var refreshOperation: RefreshOperation?
    private var generation: UInt64 = 0

    init(
        persistence: any SessionPersisting,
        refresher: any TokenRefreshing,
        expectedBaseURL: URL,
        now: @escaping @Sendable () -> Date = Date.init,
        onSessionEnded: @escaping @Sendable (_ cacheNamespace: String, _ sessionID: String) async -> Void = { _, _ in }
    ) {
        guard let expectedOrigin = ServerOrigin(url: expectedBaseURL) else {
            preconditionFailure("SessionAuthenticator requires a valid server origin.")
        }
        self.persistence = persistence
        self.refresher = refresher
        self.expectedOrigin = expectedOrigin
        self.now = now
        self.onSessionEnded = onSessionEnded
    }

    func sessionSnapshot() async throws -> AuthSession? {
        try await loadIfNeeded()
    }

    func install(_ session: AuthSession) throws {
        guard matchesExpectedOrigin(session) else {
            throw SessionError.workspaceMismatch
        }
        try Self.persistenceLock.withLock { try persistence.save(session) }
        refreshOperation?.task.cancel()
        refreshOperation = nil
        generation &+= 1
        currentSession = session
        loaded = true
    }

    func bearerToken() async throws -> String? {
        guard let session = try await loadIfNeeded() else {
            return nil
        }
        if session.accessTokenIsUsable(at: now()) {
            return session.accessToken
        }
        return try await rotate(session)
    }

    func refreshBearerToken(rejectedToken: String) async throws -> String? {
        guard let session = try await loadIfNeeded() else {
            return nil
        }

        if session.accessToken != rejectedToken, session.accessTokenIsUsable(at: now()) {
            return session.accessToken
        }
        return try await rotate(session)
    }

    func requestBearer() async throws -> RequestBearer? {
        let issuedGeneration = generation
        guard let token = try await bearerToken() else { return nil }
        guard generation == issuedGeneration else { throw CancellationError() }
        return RequestBearer(token: token, generation: issuedGeneration)
    }

    func refreshBearer(_ bearer: RequestBearer) async throws -> RequestBearer? {
        guard generation == bearer.generation else { throw CancellationError() }
        guard let token = try await refreshBearerToken(rejectedToken: bearer.token) else { return nil }
        guard generation == bearer.generation else { throw CancellationError() }
        return RequestBearer(token: token, generation: bearer.generation)
    }

    func invalidate(ifRejected bearer: RequestBearer) async throws {
        guard generation == bearer.generation, currentSession?.accessToken == bearer.token else {
            throw CancellationError()
        }
        await invalidate()
    }

    func invalidate() async {
        guard !loaded || currentSession != nil else { return }
        let outgoing = Self.persistenceLock.withLock {
            let stored = try? persistence.load()
            let ending = loaded ? currentSession : stored
            if let ending, ownsStoredSession(ending, stored) {
                try? persistence.delete()
            }
            return ending
        }
        refreshOperation?.task.cancel()
        refreshOperation = nil
        generation &+= 1
        currentSession = nil
        loaded = true
        await notifySessionEnded(outgoing)
    }

    private func notifySessionEnded(_ outgoing: AuthSession?) async {
        guard let outgoing, let sessionID = outgoing.sessionID, !sessionID.isEmpty else { return }
        await onSessionEnded(outgoing.workspace.cacheNamespace, sessionID)
    }

    private func loadIfNeeded() async throws -> AuthSession? {
        if !loaded {
            let stored = try persistence.load()
            loaded = true
            guard let stored else {
                currentSession = nil
                return nil
            }
            currentSession = stored
            guard matchesExpectedOrigin(stored) else {
                await invalidate()
                throw SessionError.workspaceMismatch
            }
        }
        return currentSession
    }

    private func rotate(_ session: AuthSession) async throws -> String {
        let expectedGeneration = generation
        if let operation = refreshOperation {
            return try await completeRefresh(
                operation,
                replacing: session,
                expectedGeneration: expectedGeneration
            )
        }

        guard
            session.refreshTokenIsUsable(at: now()),
            let refreshToken = session.refreshToken
        else {
            await invalidate()
            throw SessionError.expired
        }

        let refresher = self.refresher
        let operation = RefreshOperation(
            id: UUID(),
            task: Task<AuthSession, Error> {
                try await refresher.refresh(refreshToken: refreshToken)
            }
        )
        refreshOperation = operation
        return try await completeRefresh(
            operation,
            replacing: session,
            expectedGeneration: expectedGeneration
        )
    }

    private func completeRefresh(
        _ operation: RefreshOperation,
        replacing previous: AuthSession,
        expectedGeneration: UInt64
    ) async throws -> String {
        do {
            let refreshed = try await operation.task.value
            clearRefreshOperation(ifMatching: operation.id)

            guard generation == expectedGeneration else {
                throw CancellationError()
            }
            guard
                matchesExpectedOrigin(refreshed),
                refreshed.workspace.cacheNamespace == previous.workspace.cacheNamespace,
                refreshed.principal.id == previous.principal.id,
                refreshed.principal.kind == previous.principal.kind,
                previous.sessionID == nil || refreshed.sessionID == previous.sessionID
            else {
                await invalidate()
                throw SessionError.identityChanged
            }

            if currentSession?.accessToken == refreshed.accessToken {
                return refreshed.accessToken
            }
            guard currentSession?.accessToken == previous.accessToken else {
                throw SessionError.expired
            }

            do {
                // An earlier window can still use its own refreshed session
                // after another window has installed a different login.
                try Self.persistenceLock.withLock {
                    if ownsStoredSession(previous, try persistence.load()) {
                        try persistence.save(refreshed)
                    }
                }
            } catch {
                // The server already consumed the old refresh token. Keeping the
                // stale Keychain value would trigger reuse detection on the next try.
                await invalidate()
                throw error
            }
            currentSession = refreshed
            return refreshed.accessToken
        } catch let error as APIError {
            clearRefreshOperation(ifMatching: operation.id)
            switch error {
            case .unauthenticated(_), .forbidden(_), .gone(_):
                guard generation == expectedGeneration, currentSession?.accessToken == previous.accessToken else {
                    throw CancellationError()
                }
                await invalidate()
                throw SessionError.expired
            default:
                throw error
            }
        } catch {
            clearRefreshOperation(ifMatching: operation.id)
            throw error
        }
    }

    private func clearRefreshOperation(ifMatching id: UUID) {
        if refreshOperation?.id == id {
            refreshOperation = nil
        }
    }

    private func ownsStoredSession(_ session: AuthSession, _ stored: AuthSession?) -> Bool {
        guard let stored, session.workspace.cacheNamespace == stored.workspace.cacheNamespace,
              session.sessionID == stored.sessionID else { return false }
        // Older credentials have no session ID. Require the exact original token.
        if let id = session.sessionID, !id.isEmpty { return true }
        return session.accessToken == stored.accessToken
    }

    private func matchesExpectedOrigin(_ session: AuthSession) -> Bool {
        ServerOrigin(url: session.workspace.apiBaseURL) == expectedOrigin
    }
}

private struct RefreshOperation: Sendable {
    let id: UUID
    let task: Task<AuthSession, Error>
}

private struct ServerOrigin: Equatable, Sendable {
    let scheme: String
    let host: String
    let port: Int

    init?(url: URL) {
        guard
            let scheme = url.scheme?.lowercased(),
            let host = url.host?.lowercased(),
            (scheme == "https" || scheme == "http")
        else {
            return nil
        }
        self.scheme = scheme
        self.host = host
        port = url.port ?? (scheme == "https" ? 443 : 80)
    }
}

enum SessionError: LocalizedError, Sendable {
    case expired
    case identityChanged
    case workspaceMismatch

    var errorDescription: String? {
        switch self {
        case .expired:
            "Your session has expired. Sign in again."
        case .identityChanged:
            "The refreshed session did not match this workspace."
        case .workspaceMismatch:
            "The stored session belongs to a different Mise server."
        }
    }
}
