import SwiftUI

struct ProjectsView: View {
    let model: OwnerResourceModel<[ProjectSummary]>
    let clientsModel: OwnerResourceModel<[ClientSummary]>
    let repository: OwnerRepository
    @State private var showingNewProject = false

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: projectList,
            empty: {
                ContentUnavailableView(
                    "No projects",
                    systemImage: "briefcase",
                    description: Text("Projects will appear here as your pipeline grows.")
                )
            }
        )
        .navigationTitle("Projects")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button("New project", systemImage: "plus") {
                    showingNewProject = true
                }
            }
        }
        .sheet(isPresented: $showingNewProject) {
            NavigationStack {
                ProjectEditorView(repository: repository, clients: availableClients) {
                    showingNewProject = false
                    await model.refresh()
                }
            }
        }
        .task {
            await clientsModel.load()
        }
    }

    private var availableClients: [ClientSummary] {
        clientsModel.state.snapshot?.value ?? []
    }

    private func projectList(_ projects: [ProjectSummary]) -> some View {
        List(projects) { project in
            NavigationLink {
                ProjectEditorView(
                    repository: repository,
                    projectID: project.id,
                    clients: []
                ) {
                    await model.refresh()
                }
            } label: {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(project.title).font(.headline)
                        Spacer()
                        Text(project.status.ownerDisplayName)
                            .font(.caption.weight(.medium))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color.accentColor.opacity(0.12), in: Capsule())
                    }
                    Text(project.clientDisplayName).foregroundStyle(.secondary)
                    if let shootOn = project.shootOn {
                        Label(shootOn.rawValue, systemImage: "calendar")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 4)
                .accessibilityElement(children: .combine)
            }
        }
        .refreshable { await model.refresh() }
    }
}
