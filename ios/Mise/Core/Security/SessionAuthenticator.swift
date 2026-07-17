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
    private let persistence: any SessionPersisting
    private let refresher: any TokenRefreshing
    private let expectedOrigin: ServerOrigin
    private let now: @Sendable () -> Date

    private var loaded = false
    private var currentSession: AuthSession?
    private var refreshOperation: RefreshOperation?
    private var generation: UInt64 = 0

    init(
        persistence: any SessionPersisting,
        refresher: any TokenRefreshing,
        expectedBaseURL: URL,
        now: @escaping @Sendable () -> Date = Date.init
    ) {
        guard let expectedOrigin = ServerOrigin(url: expectedBaseURL) else {
            preconditionFailure("SessionAuthenticator requires a valid server origin.")
        }
        self.persistence = persistence
        self.refresher = refresher
        self.expectedOrigin = expectedOrigin
        self.now = now
    }

    func sessionSnapshot() throws -> AuthSession? {
        try loadIfNeeded()
    }

    func install(_ session: AuthSession) throws {
        guard matchesExpectedOrigin(session) else {
            throw SessionError.workspaceMismatch
        }
        try persistence.save(session)
        refreshOperation?.task.cancel()
        refreshOperation = nil
        generation &+= 1
        currentSession = session
        loaded = true
    }

    func bearerToken() async throws -> String? {
        guard let session = try loadIfNeeded() else {
            return nil
        }
        if session.accessTokenIsUsable(at: now()) {
            return session.accessToken
        }
        return try await rotate(session)
    }

    func refreshBearerToken(rejectedToken: String) async throws -> String? {
        guard let session = try loadIfNeeded() else {
            return nil
        }

        if session.accessToken != rejectedToken,
           session.accessTokenIsUsable(at: now())
        {
            return session.accessToken
        }
        return try await rotate(session)
    }

    func invalidate() {
        refreshOperation?.task.cancel()
        refreshOperation = nil
        generation &+= 1
        try? persistence.delete()
        currentSession = nil
        loaded = true
    }

    private func loadIfNeeded() throws -> AuthSession? {
        if !loaded {
            let stored = try persistence.load()
            loaded = true
            guard let stored else {
                currentSession = nil
                return nil
            }
            guard matchesExpectedOrigin(stored) else {
                try? persistence.delete()
                currentSession = nil
                throw SessionError.workspaceMismatch
            }
            currentSession = stored
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
            invalidate()
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
                throw SessionError.expired
            }
            guard
                matchesExpectedOrigin(refreshed),
                refreshed.workspace.cacheNamespace == previous.workspace.cacheNamespace,
                refreshed.principal.id == previous.principal.id,
                refreshed.principal.kind == previous.principal.kind,
                previous.sessionID == nil || refreshed.sessionID == previous.sessionID
            else {
                invalidate()
                throw SessionError.identityChanged
            }

            if currentSession?.accessToken == refreshed.accessToken {
                return refreshed.accessToken
            }
            guard currentSession?.accessToken == previous.accessToken else {
                throw SessionError.expired
            }

            do {
                try persistence.save(refreshed)
            } catch {
                // The server already consumed the old refresh token. Keeping the
                // stale Keychain value would trigger reuse detection on the next try.
                invalidate()
                throw error
            }
            currentSession = refreshed
            return refreshed.accessToken
        } catch let error as APIError {
            clearRefreshOperation(ifMatching: operation.id)
            switch error {
            case .unauthenticated(_), .forbidden(_), .gone(_):
                invalidate()
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
