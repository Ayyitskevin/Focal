import SwiftUI

enum ClientDestination: String, CaseIterable, Hashable, Identifiable, Sendable {
    case home
    case gallery
    case documents
    case bookings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .home: "Home"
        case .gallery: "Gallery"
        case .documents: "Documents"
        case .bookings: "Bookings"
        }
    }

    var icon: String {
        switch self {
        case .home: "house"
        case .gallery: "photo.on.rectangle"
        case .documents: "doc.text"
        case .bookings: "calendar"
        }
    }
}

/// Root experience for the four shared-access client principals — the design
/// handoff's client app (Home / Gallery / Documents / Bookings). Each tab
/// shows exactly what the unlocked capability covers and nothing more.
@MainActor
struct ClientCompanionView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selection = ClientDestination.home
    @State private var documentsPath = NavigationPath()
    @State private var home: ResourceModel<ClientHomeSummary>
    @State private var galleries: ResourceModel<[GallerySummary]>
    @State private var bookings: ResourceModel<[Booking]>

    let session: CurrentSession
    let repository: ClientRepository
    let mediaLoader: AuthenticatedMediaLoader
    let policy: ClientAccessPolicy
    let isSigningOut: Bool
    let signOut: @MainActor () async -> Void

    init(
        session: CurrentSession,
        repository: ClientRepository,
        mediaLoader: AuthenticatedMediaLoader,
        isSigningOut: Bool,
        signOut: @escaping @MainActor () async -> Void
    ) {
        self.session = session
        self.repository = repository
        self.mediaLoader = mediaLoader
        policy = ClientAccessPolicy(principalKind: session.principal.kind)
        self.isSigningOut = isSigningOut
        self.signOut = signOut
        _home = State(initialValue: ResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedHome() },
            remote: { try await repository.refreshHome() }
        ))
        _galleries = State(initialValue: ResourceModel(
            staleAfter: 30 * 60,
            cached: { try await repository.cachedGalleries() },
            remote: { try await repository.refreshGalleries() }
        ))
        _bookings = State(initialValue: ResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedBookings() },
            remote: { try await repository.refreshBookings() }
        ))
    }

    var body: some View {
        Group {
            if horizontalSizeClass == .regular {
                NavigationSplitView {
                    List(ClientDestination.allCases) { destination in
                        Button {
                            selection = destination
                        } label: {
                            HStack {
                                Label(destination.title, systemImage: destination.icon)
                                Spacer()
                                if selection == destination {
                                    Image(systemName: "checkmark")
                                        .accessibilityHidden(true)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(selection == destination ? .isSelected : [])
                    }
                    .navigationTitle(session.workspace.displayName)
                } detail: {
                    clientStack(selection)
                }
            } else {
                TabView(selection: $selection) {
                    ForEach(ClientDestination.allCases) { destination in
                        clientStack(destination)
                            .tabItem { Label(destination.title, systemImage: destination.icon) }
                            .tag(destination)
                    }
                }
            }
        }
        .tint(MiseDesign.terra)
    }

    @ViewBuilder
    private func clientStack(_ destination: ClientDestination) -> some View {
        if destination == .documents {
            // The Documents stack gets a path so a Home next-step can deep-link
            // straight to a proposal/contract/invoice, not just switch tabs.
            NavigationStack(path: $documentsPath) {
                ClientDestinationGate(policy: policy, destination: destination) {
                    screen(destination)
                }
                    .navigationDestination(for: DocumentRef.self) { ref in
                        ClientDocumentDetailLoader(
                            ref: ref,
                            projectID: policy.documentMode == .projectCollections
                                ? home.state.snapshot?.value.projectID
                                : nil,
                            repository: repository
                        )
                    }
                    .toolbar { accountToolbar }
            }
        } else {
            NavigationStack {
                ClientDestinationGate(policy: policy, destination: destination) {
                    screen(destination)
                }
                    .toolbar { accountToolbar }
            }
        }
    }

    @ToolbarContentBuilder
    private var accountToolbar: some ToolbarContent {
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

    private func navigate(_ route: ClientNavigationRoute) {
        guard policy.allows(route.target) else { return }
        switch route {
        case let .destination(destination):
            selection = destination
        case let .document(ref):
            documentsPath.append(ref)
            selection = .documents
        }
    }

    @ViewBuilder
    private func screen(_ destination: ClientDestination) -> some View {
        switch destination {
        case .home:
            ClientHomeView(
                model: home,
                policy: policy,
                navigate: navigate
            )
        case .gallery:
            ClientGalleriesView(
                model: galleries,
                repository: repository,
                mediaLoader: mediaLoader
            )
        case .documents:
            ClientDocumentsView(home: home, repository: repository, policy: policy)
        case .bookings:
            ClientBookingsView(
                model: bookings,
                timeZoneIdentifier: session.workspace.timeZone
            )
        }
    }
}
