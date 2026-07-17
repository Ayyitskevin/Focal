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
    let availableCommands: [String]
    let sessionID: String?

    init(
        workspace: WorkspaceContext,
        principal: Principal,
        availableCommands: [String] = [],
        sessionID: String? = nil
    ) {
        self.workspace = workspace
        self.principal = principal
        self.availableCommands = availableCommands
        self.sessionID = sessionID
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        workspace = try container.decode(WorkspaceContext.self, forKey: .workspace)
        principal = try container.decode(Principal.self, forKey: .principal)
        availableCommands = try container.decodeIfPresent(
            [String].self,
            forKey: .availableCommands
        ) ?? []
        sessionID = try container.decodeIfPresent(String.self, forKey: .sessionID)
    }

    func allowsCommand(_ command: String) -> Bool {
        availableCommands.contains(command)
    }

    private enum CodingKeys: String, CodingKey {
        case workspace
        case principal
        case availableCommands
        case sessionID
    }
}

struct AuthSession: Codable, Hashable, Sendable {
    let accessToken: String
    let refreshToken: String?
    let tokenType: String
    let accessTokenExpiresAt: Date
    let refreshTokenExpiresAt: Date?
    let workspace: WorkspaceContext
    let principal: Principal
    let availableCommands: [String]
    let sessionID: String?

    init(
        accessToken: String,
        refreshToken: String?,
        tokenType: String,
        accessTokenExpiresAt: Date,
        refreshTokenExpiresAt: Date?,
        workspace: WorkspaceContext,
        principal: Principal,
        availableCommands: [String] = [],
        sessionID: String? = nil
    ) {
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        self.tokenType = tokenType
        self.accessTokenExpiresAt = accessTokenExpiresAt
        self.refreshTokenExpiresAt = refreshTokenExpiresAt
        self.workspace = workspace
        self.principal = principal
        self.availableCommands = availableCommands
        self.sessionID = sessionID
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        accessToken = try container.decode(String.self, forKey: .accessToken)
        refreshToken = try container.decodeIfPresent(String.self, forKey: .refreshToken)
        tokenType = try container.decode(String.self, forKey: .tokenType)
        accessTokenExpiresAt = try container.decode(Date.self, forKey: .accessTokenExpiresAt)
        refreshTokenExpiresAt = try container.decodeIfPresent(
            Date.self,
            forKey: .refreshTokenExpiresAt
        )
        workspace = try container.decode(WorkspaceContext.self, forKey: .workspace)
        principal = try container.decode(Principal.self, forKey: .principal)
        availableCommands = try container.decodeIfPresent(
            [String].self,
            forKey: .availableCommands
        ) ?? []
        sessionID = try container.decodeIfPresent(String.self, forKey: .sessionID)
    }

    var context: CurrentSession {
        CurrentSession(
            workspace: workspace,
            principal: principal,
            availableCommands: availableCommands,
            sessionID: sessionID
        )
    }

    func accessTokenIsUsable(at date: Date, leeway: TimeInterval = 60) -> Bool {
        accessTokenExpiresAt.timeIntervalSince(date) > leeway
    }

    func refreshTokenIsUsable(at date: Date) -> Bool {
        guard refreshToken != nil else { return false }
        return refreshTokenExpiresAt.map { $0 > date } ?? true
    }

    private enum CodingKeys: String, CodingKey {
        case accessToken
        case refreshToken
        case tokenType
        case accessTokenExpiresAt
        case refreshTokenExpiresAt
        case workspace
        case principal
        case availableCommands
        case sessionID
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
    /// Hosted-only funnel links (null when self-hosted): where a new studio
    /// signs up, and where this studio's owner manages the subscription. Opened
    /// in the system browser — the app never renders a purchase UI (ADR 0070).
    /// Optional, so the synthesized decoder maps them if present and leaves them
    /// nil otherwise (the MiseJSON decoder handles the `_url` → `URL` mapping).
    let signupURL: URL?
    let manageBillingURL: URL?
}
