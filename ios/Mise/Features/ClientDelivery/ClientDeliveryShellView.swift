import SwiftUI

@MainActor
struct ClientDeliveryShellView: View {
    let session: CurrentSession
    let environment: ClientDeliveryEnvironment
    let isSigningOut: Bool
    let signOut: @MainActor () async -> Void

    var body: some View {
        NavigationStack {
            Group {
                if environment.accessState.sessionEnded {
                    ContentUnavailableView {
                        Label("Access ended", systemImage: "lock.slash")
                    } description: {
                        Text("This shared session expired or was revoked. Sign in again to reconnect.")
                    } actions: {
                        Button("Sign out", role: .destructive) {
                            Task { await signOut() }
                        }
                        .disabled(isSigningOut)
                    }
                } else {
                    screen
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        LabeledContent("Studio", value: session.workspace.displayName)
                        LabeledContent("Access", value: session.principal.kind.displayName)
                        Button("Sign out", role: .destructive) {
                            Task { await signOut() }
                        }
                        .disabled(isSigningOut)
                    } label: {
                        Image(systemName: "person.crop.circle")
                            .accessibilityLabel("Account")
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var screen: some View {
        switch session.principal.kind {
        case .galleryGuest:
            GalleryGuestView(
                repository: environment.repository,
                media: environment.media
            )
        case .portalGuest:
            ClientPortalView(repository: environment.repository)
        case .workspaceGuest:
            ClientWorkspaceView(
                repository: environment.repository,
                workspaceOrigin: session.workspace.apiBaseURL
            )
        case .documentGuest:
            ClientDocumentView(
                repository: environment.repository,
                workspaceOrigin: session.workspace.apiBaseURL
            )
        default:
            ContentUnavailableView(
                "Unsupported access",
                systemImage: "lock.trianglebadge.exclamationmark",
                description: Text("Sign out and open the shared link again.")
            )
        }
    }
}
