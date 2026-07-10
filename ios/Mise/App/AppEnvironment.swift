import Foundation
import Observation

struct AppEnvironment: Sendable {
    let configuration: AppConfiguration
    let biometricUnlock: BiometricUnlockService

    static func live() throws -> AppEnvironment {
        let configuration = try AppConfiguration()
        return AppEnvironment(
            configuration: configuration,
            biometricUnlock: BiometricUnlockService()
        )
    }

    /// Creates a complete authentication boundary for one canonical tenant origin.
    /// Never reuse this environment for a different host.
    func workspace(at origin: URL) -> WorkspaceEnvironment {
        let networkSession = makeBearerOnlyURLSession()
        let persistence = KeychainSessionPersistence(
            service: Bundle.main.bundleIdentifier ?? "com.ayyitskevin.mise",
            account: "authenticated-session.\(Self.originKey(for: origin))"
        )

        let anonymousClient = APIClient(
            configuration: .init(
                baseURL: origin,
                clientVersion: configuration.clientVersion
            ),
            session: networkSession
        )
        let refresher = RemoteTokenRefresher(client: anonymousClient)
        let session = SessionAuthenticator(
            persistence: persistence,
            refresher: refresher,
            expectedBaseURL: origin
        )
        let apiClient = APIClient(
            configuration: .init(
                baseURL: origin,
                clientVersion: configuration.clientVersion
            ),
            session: networkSession,
            authorizer: session
        )

        return WorkspaceEnvironment(
            origin: origin,
            apiClient: apiClient,
            session: session,
            networkSession: networkSession,
            clientVersion: configuration.clientVersion
        )
    }

    private static func makeBearerOnlyURLSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.waitsForConnectivity = true
        configuration.requestCachePolicy = .useProtocolCachePolicy
        return URLSession(configuration: configuration)
    }

    private static func originKey(for url: URL) -> String {
        let scheme = url.scheme?.lowercased() ?? "https"
        let host = url.host?.lowercased() ?? "invalid"
        let port = url.port.map(String.init) ?? (scheme == "https" ? "443" : "80")
        return "\(scheme)_\(host)_\(port)"
    }
}

struct WorkspaceEnvironment: Sendable {
    let origin: URL
    let apiClient: APIClient
    let session: SessionAuthenticator
    fileprivate let networkSession: URLSession
    fileprivate let clientVersion: String

    @MainActor
    func clientDelivery(
        workspaceCacheNamespace: String,
        principalID: String,
        sessionID: String?
    ) -> ClientDeliveryEnvironment {
        // Tenant-local resource IDs overlap, and each shared link has narrower
        // authority than its tenant. Cache and export identity includes both.
        let capabilityNamespace = [
            workspaceCacheNamespace,
            "capability",
            principalID,
            "session",
            sessionID ?? "legacy-session",
        ].joined(separator: "\0")
        let cache = TenantJSONCache(cacheNamespace: capabilityNamespace)
        let accessState = ClientDeliveryAccessState()
        let lifetime = ClientDeliveryLifetime()
        let media = AuthenticatedMediaClient(
            origin: origin,
            clientVersion: clientVersion,
            session: networkSession,
            authorizer: session,
            cacheNamespace: capabilityNamespace,
            lifetime: lifetime,
            onSessionEnded: {
                try? await cache.removeAll()
                await accessState.end()
            }
        )
        let repository = ClientDeliveryRepository(
            client: apiClient,
            cache: cache,
            lifetime: lifetime,
            onSessionEnded: {
                await media.purge()
                await accessState.end()
            }
        )
        return ClientDeliveryEnvironment(
            repository: repository,
            media: media,
            accessState: accessState,
            lifetime: lifetime
        )
    }
}

struct ClientDeliveryEnvironment: Sendable {
    let repository: ClientDeliveryRepository
    let media: AuthenticatedMediaClient
    let accessState: ClientDeliveryAccessState
    let lifetime: ClientDeliveryLifetime

    func purge() async {
        await lifetime.end()
        await repository.purgeCache()
        await media.purge()
    }
}

@MainActor
@Observable
final class ClientDeliveryAccessState {
    private(set) var sessionEnded = false

    func end() {
        sessionEnded = true
    }
}
