import Foundation

enum AuthenticationMode: String, CaseIterable, Hashable, Identifiable, Sendable {
    case studio
    case sharedAccess

    var id: String { rawValue }

    var title: String {
        switch self {
        case .studio: "Studio owner"
        case .sharedAccess: "Client access"
        }
    }
}

enum SharedAccessCapability: String, CaseIterable, Hashable, Identifiable, Sendable {
    case gallery
    case portal
    case workspace
    case proposal
    case contract
    case invoice

    var id: String { rawValue }

    var title: String {
        switch self {
        case .gallery: "Gallery"
        case .portal: "Client portal"
        case .workspace: "Project workspace"
        case .proposal: "Proposal"
        case .contract: "Contract"
        case .invoice: "Invoice"
        }
    }

    var sharedAccessKind: SharedAccessKind {
        switch self {
        case .gallery: .gallery
        case .portal: .portal
        case .workspace: .workspace
        case .proposal: .proposal
        case .contract: .contract
        case .invoice: .invoice
        }
    }

    fileprivate init?(pathPrefix: String) {
        switch pathPrefix {
        case "g": self = .gallery
        case "portal": self = .portal
        case "w": self = .workspace
        case "p": self = .proposal
        case "c": self = .contract
        case "i": self = .invoice
        default: return nil
        }
    }
}

struct WorkspaceAddress: Equatable, Sendable {
    let origin: URL
    let hostedSlug: String?
}

struct WorkspaceAddressParser: Sendable {
    let platformRoot: URL
    let permitsInsecureLoopback: Bool

    init(platformRoot: URL, permitsInsecureLoopback: Bool) {
        self.platformRoot = platformRoot
        self.permitsInsecureLoopback = permitsInsecureLoopback
    }

    func parse(_ input: String) throws -> WorkspaceAddress {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            throw WorkspaceAddressError.missingAddress
        }

        if value.contains("://") || value.contains(".") {
            let urlValue = value.contains("://") ? value : "https://\(value)"
            guard let url = URL(string: urlValue) else {
                throw WorkspaceAddressError.invalidAddress
            }
            return WorkspaceAddress(
                origin: try canonicalOrigin(for: url, allowPath: false),
                hostedSlug: nil
            )
        }

        let slug = try Self.normalizedHostedSlug(value)
        let root = try canonicalOrigin(for: platformRoot, allowPath: false)
        guard let rootHost = root.host, Self.canHaveTenantSubdomains(rootHost) else {
            throw WorkspaceAddressError.hostedSlugUnavailable
        }

        guard
            var components = URLComponents(url: root, resolvingAgainstBaseURL: false)
        else {
            throw WorkspaceAddressError.invalidAddress
        }
        components.host = "\(slug).\(rootHost)"
        components.path = ""
        guard let tenantURL = components.url else {
            throw WorkspaceAddressError.invalidAddress
        }
        return WorkspaceAddress(origin: tenantURL, hostedSlug: slug)
    }

    func canonicalOrigin(for url: URL, allowPath: Bool) throws -> URL {
        guard
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let scheme = components.scheme?.lowercased(),
            let rawHost = components.host?.lowercased(),
            !rawHost.isEmpty,
            components.user == nil,
            components.password == nil,
            components.query == nil,
            components.fragment == nil
        else {
            throw WorkspaceAddressError.invalidAddress
        }

        guard allowPath || components.path.isEmpty || components.path == "/" else {
            throw WorkspaceAddressError.pathNotAllowed
        }
        guard !rawHost.hasSuffix("."),
              rawHost.rangeOfCharacter(from: .whitespacesAndNewlines) == nil
        else {
            throw WorkspaceAddressError.invalidAddress
        }

        let secure = scheme == "https"
        let allowedLoopbackHTTP =
            permitsInsecureLoopback && scheme == "http" && Self.isLoopback(rawHost)
        guard secure || allowedLoopbackHTTP else {
            throw WorkspaceAddressError.secureConnectionRequired
        }

        var origin = URLComponents()
        origin.scheme = scheme
        origin.host = rawHost
        let defaultPort = scheme == "https" ? 443 : 80
        if let port = components.port, port != defaultPort {
            origin.port = port
        }
        guard let result = origin.url else {
            throw WorkspaceAddressError.invalidAddress
        }
        return result
    }

    private static func normalizedHostedSlug(_ input: String) throws -> String {
        let slug = input.lowercased()
        let characters = Array(slug.utf8)
        guard (3...32).contains(characters.count),
              characters.first != 45,
              characters.last != 45,
              characters.allSatisfy({ byte in
                  (byte >= 97 && byte <= 122)
                      || (byte >= 48 && byte <= 57)
                      || byte == 45
              })
        else {
            throw WorkspaceAddressError.invalidHostedSlug
        }
        return slug
    }

    private static func canHaveTenantSubdomains(_ host: String) -> Bool {
        guard !isLoopback(host), !host.contains(":") else { return false }
        return host.contains { character in
            !character.isNumber && character != "."
        }
    }

    private static func isLoopback(_ host: String) -> Bool {
        ["localhost", "127.0.0.1", "::1"].contains(host)
    }
}

enum WorkspaceAddressError: LocalizedError, Equatable, Sendable {
    case missingAddress
    case invalidAddress
    case invalidHostedSlug
    case hostedSlugUnavailable
    case pathNotAllowed
    case secureConnectionRequired

    var errorDescription: String? {
        switch self {
        case .missingAddress:
            "Enter your studio URL or hosted studio slug."
        case .invalidAddress:
            "Enter a valid studio address without credentials, a query, or a fragment."
        case .invalidHostedSlug:
            "Hosted studio slugs use 3–32 letters, numbers, or hyphens."
        case .hostedSlugUnavailable:
            "Enter the full studio URL for this server."
        case .pathNotAllowed:
            "A studio address must not include a page path."
        case .secureConnectionRequired:
            "Mise requires an HTTPS studio address."
        }
    }
}

struct WorkspaceSelection: Equatable, Sendable {
    let address: WorkspaceAddress
    let descriptor: TenantDescriptor

    init(
        address: WorkspaceAddress,
        descriptor: TenantDescriptor,
        parser: WorkspaceAddressParser
    ) throws {
        let canonical = try parser.canonicalOrigin(
            for: descriptor.canonicalBaseURL,
            allowPath: false
        )
        guard canonical == address.origin else {
            throw WorkspaceSelectionError.canonicalOriginMismatch
        }
        guard !descriptor.cacheNamespace.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw WorkspaceSelectionError.invalidDescriptor
        }
        if let expectedSlug = address.hostedSlug {
            guard descriptor.slug?.lowercased() == expectedSlug else {
                throw WorkspaceSelectionError.hostedSlugMismatch
            }
        }
        self.address = address
        self.descriptor = descriptor
    }
}

enum WorkspaceSelectionError: LocalizedError, Equatable, Sendable {
    case canonicalOriginMismatch
    case hostedSlugMismatch
    case invalidDescriptor

    var errorDescription: String? {
        switch self {
        case .canonicalOriginMismatch:
            "The server returned a different canonical studio address."
        case .hostedSlugMismatch:
            "The hosted studio slug did not match the server."
        case .invalidDescriptor:
            "The server returned an invalid studio descriptor."
        }
    }
}

struct SharedAccessTarget: Equatable, Sendable {
    let origin: URL
    let capability: SharedAccessCapability
    let slug: String
}

struct SharedAccessTargetParser: Sendable {
    let addressParser: WorkspaceAddressParser

    func parse(
        _ input: String,
        selectedCapability: SharedAccessCapability,
        currentWorkspaceOrigin: URL?
    ) throws -> SharedAccessTarget {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            throw SharedAccessTargetError.missingLinkOrSlug
        }

        if value.contains("://") || (value.contains(".") && value.contains("/")) {
            let urlValue = value.contains("://") ? value : "https://\(value)"
            guard
                let url = URL(string: urlValue),
                let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                !components.percentEncodedPath.contains("%")
            else {
                throw SharedAccessTargetError.invalidLink
            }
            let origin: URL
            do {
                origin = try addressParser.canonicalOrigin(for: url, allowPath: true)
            } catch {
                throw SharedAccessTargetError.invalidLink
            }
            let path = components.path.split(separator: "/", omittingEmptySubsequences: true)
            guard path.count == 2,
                  let capability = SharedAccessCapability(pathPrefix: String(path[0]))
            else {
                throw SharedAccessTargetError.unsupportedLink
            }
            let slug = try Self.validatedResourceSlug(String(path[1]))
            return SharedAccessTarget(
                origin: origin,
                capability: capability,
                slug: slug
            )
        }

        guard let currentWorkspaceOrigin else {
            throw SharedAccessTargetError.workspaceRequiredForSlug
        }
        return SharedAccessTarget(
            origin: currentWorkspaceOrigin,
            capability: selectedCapability,
            slug: try Self.validatedResourceSlug(value)
        )
    }

    private static func validatedResourceSlug(_ value: String) throws -> String {
        let bytes = Array(value.utf8)
        guard (1...200).contains(bytes.count),
              bytes.allSatisfy({ byte in
                  (byte >= 97 && byte <= 122)
                      || (byte >= 65 && byte <= 90)
                      || (byte >= 48 && byte <= 57)
                      || byte == 45
                      || byte == 95
              })
        else {
            throw SharedAccessTargetError.invalidSlug
        }
        return value
    }
}

enum SharedAccessTargetError: LocalizedError, Equatable, Sendable {
    case missingLinkOrSlug
    case invalidLink
    case unsupportedLink
    case invalidSlug
    case workspaceRequiredForSlug

    var errorDescription: String? {
        switch self {
        case .missingLinkOrSlug:
            "Paste a client link or enter its access slug."
        case .invalidLink:
            "Enter a valid HTTPS client link without a query or fragment."
        case .unsupportedLink:
            "That link is not a gallery, portal, workspace, proposal, contract, or invoice link."
        case .invalidSlug:
            "That access slug is invalid."
        case .workspaceRequiredForSlug:
            "Connect to the studio before using an access slug by itself."
        }
    }
}

struct AuthenticationFlowState: Equatable, Sendable {
    enum Screen: Equatable, Sendable {
        case workspace
        case clientLink
        case credentials
    }

    private(set) var screen: Screen = .workspace
    private(set) var workspace: WorkspaceSelection?
    var mode: AuthenticationMode = .studio

    mutating func showClientLink() {
        screen = .clientLink
        workspace = nil
        mode = .sharedAccess
    }

    mutating func didDiscover(
        _ selection: WorkspaceSelection,
        preferredMode: AuthenticationMode? = nil
    ) {
        workspace = selection
        mode = preferredMode ?? mode
        screen = .credentials
    }

    mutating func reset() {
        self = AuthenticationFlowState()
    }
}
