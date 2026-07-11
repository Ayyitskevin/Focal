import Foundation
import Observation

@MainActor
@Observable
final class AppRouter {
    private(set) var navigationRequest: OwnerNavigationRequest?
    private(set) var workspaceSwitchRequest: WorkspaceSwitchRequest?
    private(set) var errorMessage: String?

    private let parser: AppRouteParser
    private var authenticationState: RouterAuthenticationState = .loading
    private var pending: PendingRoute?
    private var receivedEventIDs = Set<UUID>()
    private var receivedEventOrder: [UUID] = []

    init(parser: AppRouteParser) {
        self.parser = parser
    }

    func authenticationDidChange(_ state: RouterAuthenticationState) {
        authenticationState = state
        switch state {
        case .signedIn:
            resolvePendingIfPossible()
        case .signedOut:
            if case let .universal(target) = pending {
                workspaceSwitchRequest = WorkspaceSwitchRequest(
                    origin: target.origin,
                    route: target.route
                )
            }
        case .loading, .locked:
            break
        }
    }

    func receiveUniversalLink(_ url: URL) {
        guard let target = parser.parseUniversalLink(url) else {
            errorMessage = "That Mise link is invalid or unsupported."
            return
        }
        route(.universal(target))
    }

    func receiveNotification(_ envelope: NotificationEnvelope) {
        guard envelope.version == 1,
              let route = parser.parsePath(envelope.route),
              receivedEventIDs.insert(envelope.eventID).inserted
        else {
            return
        }
        receivedEventOrder.append(envelope.eventID)
        if receivedEventOrder.count > 100 {
            receivedEventIDs.remove(receivedEventOrder.removeFirst())
        }
        self.route(.notification(envelope, route))
    }

    func shouldPresent(_ envelope: NotificationEnvelope) -> Bool {
        guard envelope.version == 1,
              parser.parsePath(envelope.route) != nil
        else {
            return false
        }
        let session: CurrentSession
        switch authenticationState {
        case let .signedIn(value), let .locked(value):
            session = value
        case .loading, .signedOut:
            return false
        }
        return notificationIsAuthorized(envelope, session: session)
    }

    func consumeNavigation(_ id: UUID) {
        if navigationRequest?.id == id {
            navigationRequest = nil
        }
    }

    func dismissWorkspaceSwitch() {
        workspaceSwitchRequest = nil
        pending = nil
    }

    func queueAfterWorkspaceSwitch(_ request: WorkspaceSwitchRequest) {
        navigationRequest = nil
        workspaceSwitchRequest = nil
        pending = .universal(
            UniversalLinkTarget(origin: request.origin, route: request.route)
        )
    }

    func clearForLogout() {
        pending = nil
        navigationRequest = nil
        workspaceSwitchRequest = nil
        errorMessage = nil
        authenticationState = .signedOut
        receivedEventIDs.removeAll(keepingCapacity: false)
        receivedEventOrder.removeAll(keepingCapacity: false)
    }

    private func route(_ value: PendingRoute) {
        errorMessage = nil
        switch authenticationState {
        case .loading, .locked:
            pending = value
        case .signedOut:
            pending = value
            if case let .universal(target) = value {
                workspaceSwitchRequest = WorkspaceSwitchRequest(
                    origin: target.origin,
                    route: target.route
                )
            }
        case let .signedIn(session):
            resolve(value, session: session)
        }
    }

    private func resolvePendingIfPossible() {
        guard let pending, case let .signedIn(session) = authenticationState else { return }
        self.pending = nil
        resolve(pending, session: session)
    }

    private func resolve(_ value: PendingRoute, session: CurrentSession) {
        guard session.principal.kind == .studioOwner,
              session.principal.allows("studio:read")
        else {
            return
        }

        switch value {
        case let .universal(target):
            guard sameOrigin(target.origin, session.workspace.apiBaseURL) else {
                pending = value
                workspaceSwitchRequest = WorkspaceSwitchRequest(
                    origin: target.origin,
                    route: target.route
                )
                return
            }
            publish(target.route)
        case let .notification(envelope, route):
            guard notificationIsAuthorized(envelope, session: session) else {
                return
            }
            publish(route)
        }
    }

    private func notificationIsAuthorized(
        _ envelope: NotificationEnvelope,
        session: CurrentSession
    ) -> Bool {
        guard envelope.principalKind == .studioOwner,
              envelope.principalID == session.principal.id,
              envelope.workspaceCacheNamespace == session.workspace.cacheNamespace,
              sameOrigin(envelope.workspaceOrigin, session.workspace.apiBaseURL),
              session.principal.kind == .studioOwner,
              session.principal.allows("studio:read")
        else {
            return false
        }
        return true
    }

    private func sameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        guard let left = parser.canonicalHTTPSOrigin(lhs),
              let right = parser.canonicalHTTPSOrigin(rhs)
        else {
            return false
        }
        return left == right
    }

    private func publish(_ route: OwnerRoute) {
        workspaceSwitchRequest = nil
        navigationRequest = OwnerNavigationRequest(route: route)
    }
}

private enum PendingRoute: Sendable {
    case universal(UniversalLinkTarget)
    case notification(NotificationEnvelope, OwnerRoute)
}
