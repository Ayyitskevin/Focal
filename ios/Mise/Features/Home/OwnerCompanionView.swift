import SwiftUI

enum OwnerDestination: String, CaseIterable, Identifiable {
    case home
    case clients
    case projects
    case galleries
    case calendar
    case tasks

    var id: String { rawValue }
    var title: String { rawValue.prefix(1).uppercased() + String(rawValue.dropFirst()) }

    var icon: String {
        switch self {
        case .home: "house"
        case .clients: "person.2"
        case .projects: "briefcase"
        case .galleries: "photo.on.rectangle"
        case .calendar: "calendar"
        case .tasks: "checklist"
        }
    }
}

@MainActor
struct OwnerCompanionView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selection = OwnerDestination.home
    @State private var home: OwnerResourceModel<DashboardSummary>
    @State private var clients: OwnerResourceModel<[ClientSummary]>
    @State private var projects: OwnerResourceModel<[ProjectSummary]>
    @State private var galleries: OwnerResourceModel<[GallerySummary]>
    @State private var bookings: OwnerResourceModel<[Booking]>
    @State private var tasks: OwnerResourceModel<[TaskDetail]>

    let session: CurrentSession
    let repository: OwnerRepository
    let isSigningOut: Bool
    let signOut: @MainActor () async -> Void

    init(
        session: CurrentSession,
        repository: OwnerRepository,
        isSigningOut: Bool,
        signOut: @escaping @MainActor () async -> Void
    ) {
        self.session = session
        self.repository = repository
        self.isSigningOut = isSigningOut
        self.signOut = signOut
        _home = State(initialValue: OwnerResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedDashboard() },
            remote: { try await repository.refreshDashboard() }
        ))
        _clients = State(initialValue: OwnerResourceModel(
            staleAfter: 60 * 60,
            cached: { try await repository.cachedClients() },
            remote: { try await repository.refreshClients() }
        ))
        _projects = State(initialValue: OwnerResourceModel(
            staleAfter: 30 * 60,
            cached: { try await repository.cachedProjects() },
            remote: { try await repository.refreshProjects() }
        ))
        _galleries = State(initialValue: OwnerResourceModel(
            staleAfter: 30 * 60,
            cached: { try await repository.cachedGalleries() },
            remote: { try await repository.refreshGalleries() }
        ))
        _bookings = State(initialValue: OwnerResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedBookings() },
            remote: { try await repository.refreshBookings() }
        ))
        _tasks = State(initialValue: OwnerResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedTasks() },
            remote: { try await repository.refreshTasks() }
        ))
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
            HomeView(model: home) { selection = $0 }
        case .clients:
            ClientsView(model: clients, repository: repository)
        case .projects:
            ProjectsView(model: projects, clientsModel: clients, repository: repository)
        case .galleries:
            GalleriesView(model: galleries, repository: repository)
        case .calendar:
            CalendarAgendaView(
                model: bookings,
                repository: repository,
                timeZoneIdentifier: session.workspace.timeZone
            )
        case .tasks:
            TasksView(model: tasks, repository: repository)
        }
    }
}
