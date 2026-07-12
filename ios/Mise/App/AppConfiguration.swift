import Foundation

struct AppConfiguration: Sendable {
    let serverBaseURL: URL
    let clientVersion: String
    let apnsEnvironment: APNsEnvironment
    let contentSuggestionsEnabled: Bool

    init(bundle: Bundle = .main) throws {
        guard
            let rawURL = bundle.object(forInfoDictionaryKey: "MiseServerBaseURL") as? String,
            let parsedURL = URL(string: rawURL),
            let scheme = parsedURL.scheme?.lowercased(),
            parsedURL.host != nil,
            parsedURL.user == nil,
            parsedURL.password == nil,
            parsedURL.query == nil,
            parsedURL.fragment == nil,
            parsedURL.path.isEmpty || parsedURL.path == "/"
        else {
            throw ConfigurationError.missingServerURL
        }

#if DEBUG
        guard scheme == "https" || Self.isLoopbackHTTP(parsedURL) else {
            throw ConfigurationError.insecureServerURL
        }
#else
        guard scheme == "https" else {
            throw ConfigurationError.insecureServerURL
        }
#endif

        guard
            var components = URLComponents(
                url: parsedURL,
                resolvingAgainstBaseURL: false
            )
        else {
            throw ConfigurationError.missingServerURL
        }
        components.path = ""
        guard let originURL = components.url else {
            throw ConfigurationError.missingServerURL
        }
        serverBaseURL = originURL

        let shortVersion =
            bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        clientVersion = "\(shortVersion) (\(build))"

        guard let rawEnvironment = bundle.object(
            forInfoDictionaryKey: "MiseAPNsEnvironment"
        ) as? String else {
            throw ConfigurationError.invalidAPNsEnvironment
        }
        switch rawEnvironment.lowercased() {
        case APNsEnvironment.sandbox.rawValue:
            apnsEnvironment = .sandbox
        case APNsEnvironment.production.rawValue:
            apnsEnvironment = .production
        default:
            throw ConfigurationError.invalidAPNsEnvironment
        }

        // This local kill switch intentionally fails closed. The server's
        // per-workspace capability is checked separately before controls appear.
        contentSuggestionsEnabled = Self.enabledFeatureFlag(
            bundle.object(forInfoDictionaryKey: "MiseContentSuggestionsEnabled")
        )
    }

    static func enabledFeatureFlag(_ value: Any?) -> Bool {
        switch String(describing: value ?? "").lowercased() {
        case "1", "true", "yes": true
        default: false
        }
    }

    private static func isLoopbackHTTP(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "http" else { return false }
        return ["localhost", "127.0.0.1", "::1"].contains(url.host?.lowercased() ?? "")
    }
}
extension AppConfiguration {
    enum ConfigurationError: LocalizedError {
        case missingServerURL
        case insecureServerURL
        case invalidAPNsEnvironment

        var errorDescription: String? {
            switch self {
            case .missingServerURL:
                "MiseServerBaseURL must be a server origin without credentials, a path, or a query."
            case .insecureServerURL:
                "Mise requires HTTPS outside a loopback debug environment."
            case .invalidAPNsEnvironment:
                "MiseAPNsEnvironment must be sandbox or production."
            }
        }
    }
}
