import SwiftUI

enum ClientDocumentMode: Equatable, Sendable {
    case unavailable
    case singlePreview
    case projectCollections
}

struct ClientUnavailableContent: Equatable, Sendable {
    let heading: String
    let description: String
    let systemImage: String
}

enum ClientNavigationRoute: Equatable, Sendable {
    case destination(ClientDestination)
    case document(DocumentRef)

    var target: ClientDestination {
        switch self {
        case let .destination(destination): destination
        case .document: .documents
        }
    }
}

/// One client-shell authority decision, derived only from the signed-in
/// principal kind. Cached payload shape and server response emptiness never
/// widen what a shared link can open.
struct ClientAccessPolicy: Equatable, Sendable {
    let principalKind: PrincipalKind

    var grantsClientAccess: Bool {
        principalKind == .galleryGuest
            || principalKind == .portalGuest
            || principalKind == .workspaceGuest
            || principalKind == .documentGuest
    }

    func allows(_ destination: ClientDestination) -> Bool {
        if principalKind == .galleryGuest {
            return destination == .home || destination == .gallery
        }
        if principalKind == .portalGuest {
            return destination == .home
                || destination == .gallery
                || destination == .bookings
        }
        if principalKind == .workspaceGuest {
            return true
        }
        if principalKind == .documentGuest {
            return destination == .home || destination == .documents
        }
        return false
    }

    var documentMode: ClientDocumentMode {
        if principalKind == .workspaceGuest { return .projectCollections }
        if principalKind == .documentGuest { return .singlePreview }
        return .unavailable
    }

    func unavailableContent(for destination: ClientDestination) -> ClientUnavailableContent? {
        guard !allows(destination) else { return nil }

        let generic = ClientUnavailableContent(
            heading: "Client access unavailable.",
            description: "This session does not grant client access.",
            systemImage: destination.icon
        )
        guard grantsClientAccess else { return generic }

        if principalKind == .documentGuest, destination == .gallery {
            return ClientUnavailableContent(
                heading: "Galleries aren’t part of this document link.",
                description: "This link opens the document shared with you.",
                systemImage: destination.icon
            )
        }
        if principalKind == .galleryGuest, destination == .documents {
            return ClientUnavailableContent(
                heading: "Documents aren’t part of this gallery link.",
                description: "This link opens the gallery shared with you.",
                systemImage: destination.icon
            )
        }
        if principalKind == .portalGuest, destination == .documents {
            return ClientUnavailableContent(
                heading: "Documents aren’t part of this portal link.",
                description: "This link covers shared galleries and bookings.",
                systemImage: destination.icon
            )
        }
        if principalKind == .galleryGuest, destination == .bookings {
            return ClientUnavailableContent(
                heading: "Bookings aren’t part of this gallery link.",
                description: "This link opens the gallery shared with you.",
                systemImage: destination.icon
            )
        }
        if principalKind == .documentGuest, destination == .bookings {
            return ClientUnavailableContent(
                heading: "Bookings aren’t part of this document link.",
                description: "This link opens the document shared with you.",
                systemImage: destination.icon
            )
        }
        return generic
    }

    func route(to destination: ClientDestination) -> ClientNavigationRoute? {
        guard allows(destination) else { return nil }
        return .destination(destination)
    }

    /// Resolve server-provided Home actions without trusting their shape.
    /// Document actions must carry a positive ID and a variant matching their
    /// action kind. A portal Gallery action is collection-level and omits an
    /// ID; gallery/workspace actions name one positive gallery ID.
    func route(for step: NextStepAction) -> ClientNavigationRoute? {
        if step.kind == .gallery {
            let galleryReferenceIsValid = if principalKind == .portalGuest {
                step.galleryID == nil
            } else {
                step.galleryID.map { $0 > 0 } ?? false
            }
            guard
                galleryReferenceIsValid,
                step.documentVariant == nil,
                step.documentID == nil,
                allows(.gallery)
            else { return nil }
            return .destination(.gallery)
        }

        let variant: String
        if step.kind == .proposal {
            variant = "proposal"
        } else if step.kind == .contract {
            variant = "contract"
        } else if step.kind == .invoice {
            variant = "invoice"
        } else {
            return nil
        }

        guard
            step.documentVariant == variant,
            let documentID = step.documentID,
            documentID > 0,
            step.galleryID == nil,
            documentMode == .projectCollections,
            allows(.documents)
        else { return nil }

        return .document(DocumentRef(variant: variant, id: documentID))
    }

    func welcomeLine(clientDisplayName: String?) -> String {
        let firstName = clientDisplayName?
            .split(separator: " ")
            .first
            .map(String.init)

        if principalKind == .galleryGuest {
            if let firstName {
                return "It’s lovely to see you, \(firstName) — your photographs are ready whenever you are."
            }
            return "Your photographs are ready whenever you are."
        }
        if principalKind == .portalGuest {
            if let firstName {
                return "It’s lovely to see you, \(firstName) — everything shared through this portal lives here."
            }
            return "Everything shared through this portal lives here."
        }
        if principalKind == .workspaceGuest {
            if let firstName {
                return "It’s lovely to see you, \(firstName) — everything for your project lives here."
            }
            return "Everything shared through this workspace lives here."
        }
        if principalKind == .documentGuest {
            if let firstName {
                return "It’s lovely to see you, \(firstName) — your document is ready whenever you are."
            }
            return "Your document is ready whenever you are."
        }
        return "This session does not grant client access."
    }
}

/// The common render boundary used by both compact and regular client shells.
/// A forbidden branch never attaches its ResourceView, so its model stays idle
/// and neither cache nor remote loader closure can run.
struct ClientDestinationGate<Content: View>: View {
    let policy: ClientAccessPolicy
    let destination: ClientDestination
    private let content: () -> Content

    init(
        policy: ClientAccessPolicy,
        destination: ClientDestination,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.policy = policy
        self.destination = destination
        self.content = content
    }

    @ViewBuilder
    var body: some View {
        if let unavailable = policy.unavailableContent(for: destination) {
            ContentUnavailableView(
                unavailable.heading,
                systemImage: unavailable.systemImage,
                description: Text(unavailable.description)
            )
            .navigationTitle(destination.title)
        } else {
            content()
        }
    }
}
