import SwiftUI

struct ProjectEditorView: View {
    @Environment(\.dismiss) private var dismiss

    let repository: OwnerRepository
    let projectID: Int64?
    let clients: [ClientSummary]
    let didSave: @MainActor () async -> Void

    @State private var clientID: Int64?
    @State private var title = ""
    @State private var status = ProjectStatus.inquiryReceived
    @State private var notes = ""
    @State private var shootOn = ""
    @State private var etag: String?
    @State private var loading = false
    @State private var saving = false
    @State private var errorMessage: String?
    @State private var submissionKey = UUID()
    @State private var submittedCreatePayload: ProjectCreateRequest?
    @State private var submittedUpdatePayload: ProjectMutationRequest?

    init(
        repository: OwnerRepository,
        projectID: Int64? = nil,
        clients: [ClientSummary],
        didSave: @escaping @MainActor () async -> Void
    ) {
        self.repository = repository
        self.projectID = projectID
        self.clients = clients
        self.didSave = didSave
        _clientID = State(initialValue: clients.first?.id)
    }

    var body: some View {
        Form {
            Section("Project") {
                if projectID == nil {
                    if clients.isEmpty {
                        ContentUnavailableView(
                            "Add a client first",
                            systemImage: "person.badge.plus",
                            description: Text("Every project belongs to a client.")
                        )
                    } else {
                        Picker("Client", selection: $clientID) {
                            ForEach(clients) { client in
                                Text(client.name).tag(Optional(client.id))
                            }
                        }
                    }
                }
                TextField("Title", text: $title)
                if projectID != nil {
                    Picker("Stage", selection: $status) {
                        ForEach(ProjectStatus.ownerPipeline, id: \.self) { value in
                            Text(value.ownerDisplayName).tag(value)
                        }
                    }
                    TextField("Shoot date (YYYY-MM-DD)", text: $shootOn)
                    TextField("Notes", text: $notes, axis: .vertical)
                        .lineLimit(3...8)
                }
            }
            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red)
                    if projectID != nil {
                        Button("Reload latest version") {
                            Task { await load() }
                        }
                    }
                }
            }
        }
        .navigationTitle(projectID == nil ? "New Project" : "Edit Project")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                if projectID == nil {
                    Button("Cancel") { dismiss() }
                }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") { Task { await save() } }
                    .disabled(
                        loading || saving ||
                            title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                            (projectID == nil && clientID == nil)
                    )
            }
        }
        .overlay {
            if loading || saving {
                ProgressView().controlSize(.large)
            }
        }
        .task {
            if projectID != nil, etag == nil {
                await load()
            }
        }
    }

    private func load() async {
        guard let projectID else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            let resource = try await repository.projectDetail(id: projectID)
            clientID = resource.value.clientID
            title = resource.value.title
            status = resource.value.status
            notes = resource.value.notes ?? ""
            shootOn = resource.value.shootOn?.rawValue ?? ""
            etag = resource.etag
            submittedUpdatePayload = nil
            submissionKey = UUID()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func save() async {
        saving = true
        errorMessage = nil
        defer { saving = false }
        do {
            let normalizedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
            if let projectID {
                let normalizedNotes = notes.trimmingCharacters(in: .whitespacesAndNewlines)
                let normalizedShootOn = shootOn.trimmingCharacters(in: .whitespacesAndNewlines)
                let payload = ProjectMutationRequest(
                    title: normalizedTitle,
                    status: status,
                    notes: normalizedNotes.isEmpty ? nil : normalizedNotes,
                    shootOn: normalizedShootOn.isEmpty
                        ? nil
                        : LocalDate(rawValue: normalizedShootOn)
                )
                if submittedUpdatePayload != payload {
                    submissionKey = UUID()
                    submittedUpdatePayload = payload
                }
                guard let etag else {
                    errorMessage = "Reload this project before saving."
                    return
                }
                _ = try await repository.updateProject(
                    id: projectID,
                    request: payload,
                    etag: etag,
                    idempotencyKey: submissionKey
                )
            } else {
                guard let clientID else { return }
                let payload = ProjectCreateRequest(clientID: clientID, title: normalizedTitle)
                if submittedCreatePayload != payload {
                    submissionKey = UUID()
                    submittedCreatePayload = payload
                }
                _ = try await repository.createProject(
                    payload,
                    idempotencyKey: submissionKey
                )
            }
            await didSave()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

extension ProjectStatus {
    static let ownerPipeline: [Self] = [
        .inquiryReceived,
        .consultationCall,
        .proposalSent,
        .contractSigned,
        .retainerPaid,
        .sessionPlanning,
        .projectClosed,
        .archived,
    ]
}
