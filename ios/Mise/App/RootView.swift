import SwiftUI

@MainActor
struct RootView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var authentication: AuthenticationCoordinator

    init(environment: AppEnvironment) {
        _authentication = State(initialValue: AuthenticationCoordinator(environment: environment))
    }

    var body: some View {
        Group {
            switch authentication.phase {
            case .loading:
                LoadingSessionView()
            case .signedOut:
                AuthenticationView(model: authentication)
            case let .signedIn(session):
                SignedInShell(
                    session: session,
                    ownerRepository: authentication.ownerRepository,
                    isSigningOut: authentication.isWorking,
                    signOut: { await authentication.signOut() }
                )
            case let .locked(session, biometricKind):
                AppLockView(model: authentication, session: session, biometricKind: biometricKind)
            }
        }
        .overlay { if scenePhase != .active { PrivacyShield() } }
        .task { await authentication.restore() }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: Task { await authentication.sceneDidBecomeActive() }
            case .inactive, .background: authentication.sceneDidEnterBackground()
            @unknown default: break
            }
        }
    }
}

private struct PrivacyShield: View {
    var body: some View {
        Color(uiColor: .systemBackground)
            .ignoresSafeArea()
            .overlay {
                Image(systemName: "camera.aperture")
                    .font(.system(size: 44, weight: .medium))
                    .foregroundStyle(.secondary)
            }
            .accessibilityHidden(true)
    }
}

private struct LoadingSessionView: View {
    var body: some View {
        ZStack {
            Color(uiColor: .systemGroupedBackground).ignoresSafeArea()
            VStack(spacing: 16) {
                Image(systemName: "camera.aperture")
                    .font(.system(size: 44, weight: .medium))
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                ProgressView("Opening Mise…").controlSize(.large)
            }
            .accessibilityElement(children: .combine)
        }
    }
}

@MainActor
private struct SignedInShell: View {
    let session: CurrentSession
    let ownerRepository: OwnerRepository?
    let isSigningOut: Bool
    let signOut: @MainActor () async -> Void

    var body: some View {
        if session.principal.kind == .studioOwner, let repository = ownerRepository {
            OwnerCompanionView(
                session: session,
                repository: repository,
                isSigningOut: isSigningOut,
                signOut: signOut
            )
        } else if session.principal.kind != .studioOwner {
            GuestConnectedView(session: session, isSigningOut: isSigningOut, signOut: signOut)
        } else {
            ContentUnavailableView {
                Label("Studio data unavailable", systemImage: "exclamationmark.triangle")
            } description: {
                Text("Sign out and reconnect to this studio.")
            } actions: {
                Button("Sign out", role: .destructive) { Task { await signOut() } }
                    .disabled(isSigningOut)
            }
        }
    }
}

@MainActor
private struct GuestConnectedView: View {
    let session: CurrentSession
    let isSigningOut: Bool
    let signOut: @MainActor () async -> Void

    var body: some View {
        NavigationStack {
            ContentUnavailableView {
                Label(title, systemImage: icon)
            } description: {
                Text("You’re securely connected to \(session.workspace.displayName). This limited session cannot open studio-owner data.")
            } actions: {
                Button("Sign out", role: .destructive) { Task { await signOut() } }
                    .disabled(isSigningOut)
            }
            .navigationTitle(session.principal.kind.displayName)
        }
    }

    private var title: String {
        switch session.principal.kind {
        case .galleryGuest: "Gallery access connected"
        case .portalGuest: "Client portal connected"
        case .workspaceGuest: "Project workspace connected"
        case .documentGuest: "Document access connected"
        default: "Limited access connected"
        }
    }

    private var icon: String {
        switch session.principal.kind {
        case .galleryGuest: "photo.on.rectangle"
        case .portalGuest: "person.crop.circle.badge.checkmark"
        case .workspaceGuest: "briefcase"
        case .documentGuest: "doc.text"
        default: "lock.shield"
        }
    }
}
