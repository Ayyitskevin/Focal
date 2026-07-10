import SwiftUI

@MainActor
struct RootView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var authentication: AuthenticationCoordinator

    init(environment: AppEnvironment) {
        _authentication = State(
            initialValue: AuthenticationCoordinator(environment: environment)
        )
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
                    isSigningOut: authentication.isWorking,
                    signOut: { await authentication.signOut() }
                )
            case let .locked(session, biometricKind):
                AppLockView(
                    model: authentication,
                    session: session,
                    biometricKind: biometricKind
                )
            }
        }
        .overlay {
            if scenePhase != .active {
                PrivacyShield()
            }
        }
        .task { await authentication.restore() }
        .onChange(of: scenePhase) { _, newPhase in
            switch newPhase {
            case .active:
                Task { await authentication.sceneDidBecomeActive() }
            case .inactive, .background:
                authentication.sceneDidEnterBackground()
            @unknown default:
                break
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
            Color(uiColor: .systemGroupedBackground)
                .ignoresSafeArea()
            VStack(spacing: 16) {
                Image(systemName: "camera.aperture")
                    .font(.system(size: 44, weight: .medium))
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                ProgressView("Opening Mise…")
                    .controlSize(.large)
            }
            .accessibilityElement(children: .combine)
        }
    }
}

@MainActor
private struct SignedInShell: View {
    let session: CurrentSession
    let isSigningOut: Bool
    let signOut: @MainActor () async -> Void

    var body: some View {
        NavigationStack {
            List {
                Section("Workspace") {
                    LabeledContent("Studio", value: session.workspace.displayName)
                    LabeledContent("Signed in as", value: session.principal.displayName)
                    LabeledContent("Access", value: session.principal.kind.displayName)
                }

                Section {
                    ContentUnavailableView {
                        Label("Workspace connected", systemImage: "checkmark.seal")
                    } description: {
                        Text("Your secure native session is ready for Mise features.")
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Mise")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Sign out", role: .destructive) {
                        Task { await signOut() }
                    }
                    .disabled(isSigningOut)
                }
            }
            .overlay {
                if isSigningOut {
                    ProgressView("Signing out…")
                        .padding(20)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
                }
            }
        }
    }
}
