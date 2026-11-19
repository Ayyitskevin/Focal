import SwiftUI

struct InquiriesView: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let model: ResourceModel<[InquirySummary]>
    let repository: OwnerRepository
    let canWrite: Bool
    let onTriage: @MainActor () async -> Void
    @State private var query = ""

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: inquiryList,
            empty: {
                ContentUnavailableView(
                    "No inquiries",
                    systemImage: "tray",
                    description: Text("New inquiries and messages will appear here.")
                )
            }
        )
        .navigationTitle("Inquiries")
        .searchable(text: $query, prompt: "Name, business, or service")
    }

    private func inquiryList(_ inquiries: [InquirySummary]) -> some View {
        let matches = inquiries.filter(matchesQuery)
        return List {
            if matches.isEmpty {
                ContentUnavailableView.search(text: query)
                    .listRowBackground(Color.clear)
            } else {
                ForEach(matches) { inquiry in
                    NavigationLink {
                        InquiryDetailView(
                            summary: inquiry,
                            repository: repository,
                            canWrite: canWrite,
                            onTriage: onTriage
                        )
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(inquiry.name).font(.headline)
                                Spacer()
                                StatusPill(
                                    label: inquiry.status.ownerDisplayName,
                                    tone: inquiry.status.tone
                                )
                            }
                            if let business = inquiry.business, !business.isEmpty {
                                Text(business).foregroundStyle(.secondary)
                            }
                            if let contact = contactLine(inquiry) {
                                Text(contact).foregroundStyle(.secondary)
                            }
                            Text(topicLine(inquiry))
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                            if !inquiry.messagePreview.isEmpty {
                                Text(inquiry.messagePreview)
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                            }
                            HStack(spacing: 12) {
                                Text(inquiry.receivedAt, style: .relative)
                                if let shootOn = inquiry.shootOn {
                                    Label(shootOn.rawValue, systemImage: "calendar")
                                }
                                Spacer()
                                if inquiry.isReplied {
                                    Label("Replied", systemImage: "arrowshape.turn.up.left.fill")
                                }
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 4)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
        .refreshable { await model.refresh() }
    }

    private func topicLine(_ inquiry: InquirySummary) -> String {
        let kind = inquiry.kind.replacingOccurrences(of: "_", with: " ")
        let display = kind.prefix(1).uppercased() + String(kind.dropFirst())
        if let service = inquiry.service, !service.isEmpty {
            return "\(display) · \(service)"
        }
        return display
    }

    private func contactLine(_ inquiry: InquirySummary) -> String? {
        var values: [String] = []
        if let email = inquiry.email, !email.isEmpty {
            values.append(email)
        }
        if let phone = inquiry.phone, !phone.isEmpty {
            values.append(phone)
        }
        return values.isEmpty ? nil : values.joined(separator: " · ")
    }

    private func matchesQuery(_ inquiry: InquirySummary) -> Bool {
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return true }
        return [
            inquiry.name,
            inquiry.business,
            inquiry.email,
            inquiry.phone,
            inquiry.service,
            inquiry.kind,
        ]
            .compactMap { $0 }
            .contains { $0.localizedCaseInsensitiveContains(term) }
    }
}
