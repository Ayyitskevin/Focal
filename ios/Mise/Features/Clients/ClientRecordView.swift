import SwiftUI

struct ClientRecordView: View {
    let clientID: Int64
    let repository: OwnerRepository

    private enum LoadState {
        case loading
        case loaded(ClientSummary)
        case failed(String)
    }

    @State private var state: LoadState = .loading
    @State private var attempt = 0

    var body: some View {
        Group {
            switch state {
            case .loading:
                ProgressView("Loading client…")
            case .loaded(let client):
                List {
                    Section {
                        Text(client.name)
                            .font(.headline)
                            .accessibilityIdentifier("client-record-name")
                        if let company = client.company, !company.isEmpty {
                            LabeledContent("Company", value: company)
                        }
                        if let email = client.email, !email.isEmpty {
                            LabeledContent("Email", value: email)
                        }
                        if let phone = client.phone, !phone.isEmpty {
                            LabeledContent("Phone", value: phone)
                        }
                    }
                    LabeledContent("Projects", value: client.projectCount.formatted())
                }
            case .failed(let message):
                ContentUnavailableView {
                    Label("Client record unavailable", systemImage: "person.crop.circle.badge.exclamationmark")
                } description: {
                    Text(message)
                } actions: {
                    Button("Try Again") { attempt += 1 }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .navigationTitle("Client record")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: attempt) {
            state = .loading
            do {
                let client = try await repository.clientRecord(id: clientID)
                guard !Task.isCancelled else { return }
                state = .loaded(client)
            } catch {
                guard !Task.isCancelled else { return }
                state = .failed(error.localizedDescription)
            }
        }
    }
}
