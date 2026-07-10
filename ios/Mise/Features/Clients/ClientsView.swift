import SwiftUI

struct ClientsView: View {
    let model: OwnerResourceModel<[ClientSummary]>
    @State private var query = ""

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: clientList,
            empty: {
                ContentUnavailableView(
                    "No clients",
                    systemImage: "person.2",
                    description: Text("Clients will appear here once they’re added in Mise.")
                )
            }
        )
        .navigationTitle("Clients")
        .searchable(text: $query, prompt: "Name, company, or email")
    }

    private func clientList(_ clients: [ClientSummary]) -> some View {
        let matches = clients.filter(matchesQuery)
        return List {
            if matches.isEmpty {
                ContentUnavailableView.search(text: query)
                    .listRowBackground(Color.clear)
            } else {
                ForEach(matches) { client in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text(client.name).font(.headline)
                            Spacer()
                            Text("\(client.projectCount) project\(client.projectCount == 1 ? "" : "s")")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let company = client.company, !company.isEmpty {
                            Text(company).foregroundStyle(.secondary)
                        }
                        if let email = client.email, !email.isEmpty {
                            Label(email, systemImage: "envelope")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .refreshable { await model.refresh() }
    }

    private func matchesQuery(_ client: ClientSummary) -> Bool {
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return true }
        return [client.name, client.company, client.email]
            .compactMap { $0 }
            .contains { $0.localizedCaseInsensitiveContains(term) }
    }
}
