import Foundation

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
            session: session
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
}
