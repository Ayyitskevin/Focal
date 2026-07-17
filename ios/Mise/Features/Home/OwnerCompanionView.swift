import SwiftUI

enum OwnerDestination: String, CaseIterable, Identifiable {
    case home
    case clients
    case projects
    case galleries
    case calendar
    case commercial

    var id: String { rawValue }
    var title: String { rawValue.prefix(1).uppercased() + String(rawValue.dropFirst()) }

    var icon: String {
        switch self {
        case .home: "house"
        case .clients: "person.2"
        case .projects: "briefcase"
        case .galleries: "photo.on.rectangle"
        case .calendar: "calendar"
        case .commercial: "dollarsign.circle"
        }
    }
}

@MainActor
struct OwnerCompanionView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selection = OwnerDestination.home
    @State private var home: ResourceModel<DashboardSummary>
    @State private var clients: ResourceModel<[ClientSummary]>
    @State private var projects: ResourceModel<[ProjectSummary]>
    @State private var galleries: ResourceModel<[GallerySummary]>
    @State private var bookings: ResourceModel<[Booking]>
    @State private var commercial: ResourceModel<[CommercialAction]>
    @State private var commands: OwnerCommandModel
    @State private var reschedule: BookingRescheduleModel

    let session: CurrentSession
    let repository: OwnerRepository
    let mediaLoader: AuthenticatedMediaLoader
    let isSigningOut: Bool
    let signOut: @MainActor () async -> Void

    init(
        session: CurrentSession,
        repository: OwnerRepository,
        mediaLoader: AuthenticatedMediaLoader,
        isSigningOut: Bool,
        signOut: @escaping @MainActor () async -> Void
    ) {
        self.session = session
        self.repository = repository
        self.mediaLoader = mediaLoader
        self.isSigningOut = isSigningOut
        self.signOut = signOut
        let commandModel = OwnerCommandModel(
            canWrite: session.principal.allows("studio:write"),
            setTaskCompletion: { id, completed in
                try await repository.setTaskCompletion(id: id, completed: completed)
            },
            cancelBooking: { id in
                try await repository.cancelBooking(id: id)
            }
        )
        _commands = State(initialValue: commandModel)
        _reschedule = State(initialValue: BookingRescheduleModel(
            session: session,
            commands: commandModel,
            repository: repository
        ))
        _home = State(initialValue: ResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedDashboard() },
            remote: { try await repository.refreshDashboard() }
        ))
        _clients = State(initialValue: ResourceModel(
            staleAfter: 60 * 60,
            cached: { try await repository.cachedClients() },
            remote: { try await repository.refreshClients() }
        ))
        _projects = State(initialValue: ResourceModel(
            staleAfter: 30 * 60,
            cached: { try await repository.cachedProjects() },
            remote: { try await repository.refreshProjects() }
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
        _commercial = State(initialValue: ResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedCommercialActions() },
            remote: { try await repository.refreshCommercialActions() }
        ))
    }

    private var accountLinks: StudioAccountLinks {
        StudioAccountLinks(workspaceOrigin: session.workspace.apiBaseURL)
    }

    var body: some View {
        if horizontalSizeClass == .regular {
            NavigationSplitView {
                List(OwnerDestination.allCases) { destination in
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
                ownerStack(selection)
            }
        } else {
            TabView(selection: $selection) {
                ForEach(OwnerDestination.allCases) { destination in
                    ownerStack(destination)
                        .tabItem { Label(destination.title, systemImage: destination.icon) }
                        .tag(destination)
                }
            }
        }
    }

    private func ownerStack(_ destination: OwnerDestination) -> some View {
        NavigationStack {
            screen(destination)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            LabeledContent("Studio", value: session.workspace.displayName)
                            // Export/delete stay password-confirmed web flows
                            // (ADR 0051); the app links out so a signed-in owner
                            // can always reach account deletion from inside it.
                            Section {
                                Link(destination: accountLinks.exportStudio) {
                                    Label("Export studio data", systemImage: "arrow.down.doc")
                                }
                                Link(destination: accountLinks.deleteStudio) {
                                    Label("Delete studio account", systemImage: "trash")
                                }
                            }
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
    private func screen(_ destination: OwnerDestination) -> some View {
        switch destination {
        case .home:
            HomeView(model: home, commands: commands) { selection = $0 }
        case .clients:
            ClientsView(model: clients)
        case .projects:
            ProjectsView(model: projects)
        case .galleries:
            GalleriesView(model: galleries, repository: repository, mediaLoader: mediaLoader)
        case .calendar:
            CalendarAgendaView(
                model: bookings,
                timeZoneIdentifier: session.workspace.timeZone,
                commands: commands,
                reschedule: reschedule
            )
        case .commercial:
            CommercialView(model: commercial, repository: repository)
        }
    }
}
