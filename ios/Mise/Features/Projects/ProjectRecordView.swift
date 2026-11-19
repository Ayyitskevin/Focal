import SwiftUI

struct ProjectRecordView: View {
    let projectID: Int64
    let repository: OwnerRepository

    private enum LoadState {
        case loading
        case loaded(ProjectSummary)
        case failed(String)
    }

    @State private var state: LoadState = .loading
    @State private var attempt = 0

    var body: some View {
        Group {
            switch state {
            case .loading:
                ProgressView("Loading project…")
            case .loaded(let project):
                List {
                    Text(project.title)
                        .font(.headline)
                        .accessibilityIdentifier("project-record-title")
                    LabeledContent("Client") {
                        Text(project.clientDisplayName)
                            .accessibilityIdentifier("project-record-client")
                    }
                    LabeledContent("Status", value: project.status.ownerDisplayName)
                    if let shootOn = project.shootOn {
                        LabeledContent("Shoot date", value: shootOn.rawValue)
                    }
                }
            case .failed(let message):
                ContentUnavailableView {
                    Label("Project record unavailable", systemImage: "briefcase")
                } description: {
                    Text(message)
                } actions: {
                    Button("Try Again") { attempt += 1 }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .navigationTitle("Project record")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: attempt) {
            state = .loading
            do {
                let project = try await repository.projectRecord(id: projectID)
                guard !Task.isCancelled else { return }
                state = .loaded(project)
            } catch {
                guard !Task.isCancelled else { return }
                state = .failed(error.localizedDescription)
            }
        }
    }
}
