import SwiftUI

struct ClientEditorView: View {
    @Environment(\.dismiss) private var dismiss

    let repository: OwnerRepository
    let clientID: Int64?
    let didSave: @MainActor () async -> Void

    @State private var name = ""
    @State private var company = ""
    @State private var email = ""
    @State private var phone = ""
    @State private var notes = ""
    @State private var usageRights = ""
    @State private var market = "general"
    @State private var etag: String?
    @State private var loading = false
    @State private var saving = false
    @State private var errorMessage: String?
    @State private var submissionKey = UUID()
    @State private var submittedPayload: ClientMutationRequest?

    init(
        repository: OwnerRepository,
        clientID: Int64? = nil,
        didSave: @escaping @MainActor () async -> Void
    ) {
        self.repository = repository
        self.clientID = clientID
        self.didSave = didSave
    }

    var body: some View {
        Form {
            Section("Client") {
                TextField("Name", text: $name)
                    .textContentType(.name)
                TextField("Company", text: $company)
                    .textContentType(.organizationName)
                TextField("Email", text: $email)
                    .textContentType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.emailAddress)
                TextField("Phone", text: $phone)
                    .textContentType(.telephoneNumber)
                    .keyboardType(.phonePad)
                TextField("Market", text: $market)
            }
            Section("Studio notes") {
                TextField("Notes", text: $notes, axis: .vertical)
                    .lineLimit(3...8)
                TextField("Usage rights", text: $usageRights, axis: .vertical)
                    .lineLimit(2...6)
            }
            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red)
                    if clientID != nil {
                        Button("Reload latest version") {
                            Task { await load() }
                        }
                    }
                }
            }
        }
        .navigationTitle(clientID == nil ? "New Client" : "Edit Client")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                if clientID == nil {
                    Button("Cancel") { dismiss() }
                }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") { Task { await save() } }
                    .disabled(
                        loading || saving ||
                            name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
            }
        }
        .overlay {
            if loading || saving {
                ProgressView().controlSize(.large)
            }
        }
        .task {
            if clientID != nil, etag == nil {
                await load()
            }
        }
    }

    private func optional(_ value: String) -> String? {
        let result = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return result.isEmpty ? nil : result
    }

    private var request: ClientMutationRequest {
        ClientMutationRequest(
            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            company: optional(company),
            email: optional(email),
            phone: optional(phone),
            notes: optional(notes),
            usageRights: optional(usageRights),
            market: market.trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }

    private func load() async {
        guard let clientID else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            let resource = try await repository.clientDetail(id: clientID)
            name = resource.value.name
            company = resource.value.company ?? ""
            email = resource.value.email ?? ""
            phone = resource.value.phone ?? ""
            notes = resource.value.notes ?? ""
            usageRights = resource.value.usageRights ?? ""
            market = resource.value.market
            etag = resource.etag
            submittedPayload = nil
            submissionKey = UUID()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func save() async {
        let payload = request
        if submittedPayload != payload {
            submissionKey = UUID()
            submittedPayload = payload
        }
        saving = true
        errorMessage = nil
        defer { saving = false }
        do {
            if let clientID {
                guard let etag else {
                    errorMessage = "Reload this client before saving."
                    return
                }
                _ = try await repository.updateClient(
                    id: clientID,
                    request: payload,
                    etag: etag,
                    idempotencyKey: submissionKey
                )
            } else {
                _ = try await repository.createClient(
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
