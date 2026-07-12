import Foundation

enum OwnerRoute: Hashable, Sendable {
    case home
    case project(Int64)
    case gallery(id: Int64, assetID: Int64?)
    case booking(Int64)
    case contentCaption(Int64)
}

struct OwnerNavigationRequest: Equatable, Identifiable, Sendable {
    let id: UUID
    let route: OwnerRoute

    init(id: UUID = UUID(), route: OwnerRoute) {
        self.id = id
        self.route = route
    }
}

struct UniversalLinkTarget: Equatable, Sendable {
    let origin: URL
    let route: OwnerRoute
}

struct NotificationEnvelope: Codable, Equatable, Sendable {
    let version: Int
    let eventID: UUID
    let workspaceOrigin: URL
    let workspaceCacheNamespace: String
    let principalKind: PrincipalKind
    let principalID: String
    let route: String
}

struct WorkspaceSwitchRequest: Equatable, Identifiable, Sendable {
    let id: UUID
    let origin: URL
    let route: OwnerRoute

    init(id: UUID = UUID(), origin: URL, route: OwnerRoute) {
        self.id = id
        self.origin = origin
        self.route = route
    }
}

enum RouterAuthenticationState: Equatable, Sendable {
    case loading
    case signedOut
    case locked(CurrentSession)
    case signedIn(CurrentSession)
}

enum NotificationPayloadDecoder {
    private static let expectedKeys: Set<String> = [
        "version",
        "event_id",
        "workspace_origin",
        "workspace_cache_namespace",
        "principal_kind",
        "principal_id",
        "route",
    ]

    /// Converts Foundation's untyped delegate payload to a bounded Sendable value
    /// before any actor hop. Unknown fields fail closed so a newer payload cannot
    /// accidentally inherit older routing semantics.
    static func decode(_ userInfo: [AnyHashable: Any]) -> NotificationEnvelope? {
        guard let raw = userInfo["mise"] as? [String: Any],
              Set(raw.keys) == expectedKeys,
              JSONSerialization.isValidJSONObject(raw),
              let data = try? JSONSerialization.data(withJSONObject: raw),
              data.count <= 4_096,
              let envelope = try? MiseJSON.decoder().decode(NotificationEnvelope.self, from: data),
              envelope.version == 1,
              (1...255).contains(envelope.workspaceCacheNamespace.utf8.count),
              (1...200).contains(envelope.principalID.utf8.count),
              envelope.route.utf8.count <= 256
        else {
            return nil
        }
        return envelope
    }
}
