import SwiftUI

struct ProjectsView: View {
    let model: ResourceModel<[ProjectSummary]>

    var body: some View {
        ResourceView(
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
    }

    private func projectList(_ projects: [ProjectSummary]) -> some View {
        List(projects) { project in
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
        .refreshable { await model.refresh() }
    }
}
