import Foundation

struct PrincipalKind: APIStringValue {
    let rawValue: String

    init(rawValue: String) {
        self.rawValue = rawValue
    }

    static let studioOwner = Self(rawValue: "studio_owner")
    static let galleryGuest = Self(rawValue: "gallery_guest")
    static let portalGuest = Self(rawValue: "portal_guest")
    static let workspaceGuest = Self(rawValue: "workspace_guest")
    static let documentGuest = Self(rawValue: "document_guest")

    var displayName: String {
        if self == .studioOwner { return "Studio owner" }
        if self == .galleryGuest { return "Gallery access" }
        if self == .portalGuest { return "Portal access" }
        if self == .workspaceGuest { return "Workspace access" }
        if self == .documentGuest { return "Document access" }
        return "Limited access"
    }
}
struct WorkspaceContext: Codable, Hashable, Sendable {
    /// Opaque, immutable namespace used to scope tenant-local IDs in caches.
    let cacheNamespace: String
    let slug: String?
    let displayName: String
    let apiBaseURL: URL
    let brandAccentHex: String?
    let timeZone: String
    let currencyCode: String
}

struct Principal: Codable, Hashable, Sendable {
    let id: String
    let kind: PrincipalKind
    let displayName: String
    let email: String?
    let scopes: [String]

    func allows(_ scope: String) -> Bool {
        scopes.contains(scope)
    }
}

struct CurrentSession: Codable, Hashable, Sendable {
    let workspace: WorkspaceContext
    let principal: Principal
    let sessionID: String?
}

struct AuthSession: Codable, Hashable, Sendable {
    let accessToken: String
    let refreshToken: String?
    let tokenType: String
    let accessTokenExpiresAt: Date
    let refreshTokenExpiresAt: Date?
    let workspace: WorkspaceContext
    let principal: Principal
    let sessionID: String?

    var context: CurrentSession {
        CurrentSession(workspace: workspace, principal: principal, sessionID: sessionID)
    }

    func accessTokenIsUsable(at date: Date, leeway: TimeInterval = 60) -> Bool {
        accessTokenExpiresAt.timeIntervalSince(date) > leeway
    }

    func refreshTokenIsUsable(at date: Date) -> Bool {
        guard refreshToken != nil else { return false }
        return refreshTokenExpiresAt.map { $0 > date } ?? true
    }
}

struct DeviceContext: Codable, Hashable, Sendable {
    let installationID: String
    let name: String
    let platform: String
    let appVersion: String

    init(
        installationID: String,
        name: String,
        platform: String = "ios",
        appVersion: String
    ) {
        self.installationID = installationID
        self.name = name
        self.platform = platform
        self.appVersion = appVersion
    }
}

struct StudioLoginRequest: Codable, Hashable, Sendable {
    let email: String?
    let password: String
    let device: DeviceContext
}

struct RefreshTokenRequest: Codable, Hashable, Sendable {
    let refreshToken: String
}

struct SharedAccessKind: APIStringValue {
    let rawValue: String

    init(rawValue: String) {
        self.rawValue = rawValue
    }

    static let gallery = Self(rawValue: "gallery")
    static let portal = Self(rawValue: "portal")
    static let workspace = Self(rawValue: "workspace")
    static let proposal = Self(rawValue: "proposal")
    static let contract = Self(rawValue: "contract")
    static let invoice = Self(rawValue: "invoice")
}

struct SharedAccessUnlockRequest: Codable, Hashable, Sendable {
    let kind: SharedAccessKind
    let slug: String
    let pin: String?
    let device: DeviceContext
}

struct TenantDescriptor: Codable, Hashable, Sendable {
    let cacheNamespace: String
    let slug: String?
    let studioName: String
    let canonicalBaseURL: URL
    let brandAccentHex: String?
    let timeZone: String
    let currencyCode: String
    let authMethods: [String]
}
