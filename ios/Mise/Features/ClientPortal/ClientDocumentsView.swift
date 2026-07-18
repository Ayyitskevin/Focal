import SwiftUI

/// Client Documents tab. What it shows depends on the unlocked capability:
/// a project workspace lists every client-visible proposal/contract/invoice;
/// a single-document link shows just that document; gallery and portal links
/// have no document authority at all.
///
/// Accept, Sign, and Pay intentionally open the canonical web pages —
/// signatures and Stripe checkout stay server-rendered flows in Milestone 3
/// (docs/IOS-ARCHITECTURE.md §8).
struct ClientDocumentsView: View {
    let home: ResourceModel<ClientHomeSummary>
    let repository: ClientRepository
    let policy: ClientAccessPolicy

    var body: some View {
        ResourceView(
            model: home,
            isEmpty: { _ in false },
            content: content,
            empty: { EmptyView() }
        )
        .navigationTitle("Documents")
    }

    @ViewBuilder
    private func content(_ summary: ClientHomeSummary) -> some View {
        switch policy.documentMode {
        case .projectCollections:
            if let projectID = summary.projectID, projectID > 0 {
                ProjectDocumentsList(projectID: projectID, repository: repository)
            } else {
                ContentUnavailableView(
                    "No documents here",
                    systemImage: "doc.text",
                    description: Text("This workspace has no documents available.")
                )
            }
        case .singlePreview:
            if let document = summary.document {
                SingleDocumentView(document: document)
            } else {
                ContentUnavailableView(
                    "Document unavailable",
                    systemImage: "doc.text",
                    description: Text("This document link no longer has a shared document.")
                )
            }
        case .unavailable:
            if let unavailable = policy.unavailableContent(for: .documents) {
                ContentUnavailableView(
                    unavailable.heading,
                    systemImage: unavailable.systemImage,
                    description: Text(unavailable.description)
                )
            }
        }
    }
}

private struct ProjectDocumentsList: View {
    let projectID: Int64
    @State private var model: ResourceModel<ClientDocuments>

    init(projectID: Int64, repository: ClientRepository) {
        self.projectID = projectID
        _model = State(initialValue: ResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedDocuments(projectID: projectID) },
            remote: { try await repository.refreshDocuments(projectID: projectID) }
        ))
    }

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: list,
            empty: {
                ContentUnavailableView(
                    "Nothing to review yet",
                    systemImage: "doc.text",
                    description: Text("Proposals, agreements, and invoices will appear here.")
                )
            }
        )
    }

    private func list(_ documents: ClientDocuments) -> some View {
        List {
            ForEach(documents.proposals) { proposal in
                NavigationLink {
                    ClientProposalDetailView(proposal: proposal)
                } label: {
                    DocumentRow(
                        icon: "sparkles",
                        tone: .honey,
                        title: proposal.title,
                        status: proposal.status.ownerDisplayName,
                        amount: proposal.total
                    )
                }
            }
            ForEach(documents.contracts) { contract in
                NavigationLink {
                    ClientContractDetailView(contract: contract)
                } label: {
                    DocumentRow(
                        icon: "checkmark.seal",
                        tone: StatusTone(
                            foreground: MiseDesign.terra,
                            background: MiseDesign.terraTint
                        ),
                        title: contract.title,
                        status: contract.status.ownerDisplayName,
                        amount: nil
                    )
                }
            }
            ForEach(documents.invoices) { invoice in
                NavigationLink {
                    ClientInvoiceDetailView(invoice: invoice)
                } label: {
                    DocumentRow(
                        icon: "creditcard",
                        tone: .ok,
                        title: invoice.title,
                        status: invoice.status.ownerDisplayName,
                        amount: invoice.balance.minorUnits > 0 ? invoice.balance : invoice.total
                    )
                }
            }
        }
        .refreshable { await model.refresh() }
    }
}

private struct DocumentRow: View {
    let icon: String
    let tone: StatusTone
    let title: String
    let status: String
    let amount: Money?

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon)
                .font(.body)
                .foregroundStyle(tone.foreground)
                .frame(width: 38, height: 38)
                .background(tone.background, in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(status).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if let amount {
                Text(amount.ownerDisplayValue)
                    .font(.subheadline.weight(.semibold))
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
    }
}

private struct SingleDocumentView: View {
    let document: ClientDocumentPreview
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    StatusPill(
                        label: document.status
                            .replacingOccurrences(of: "_", with: " ")
                            .capitalized,
                        tone: .honey
                    )
                    Text(document.title)
                        .miseDisplayFont(.title3)
                }
                .padding(.vertical, 6)
            }
            if let total = document.total {
                LabeledContent("Total", value: total.ownerDisplayValue)
            }
            if let balance = document.balance {
                LabeledContent("Balance") {
                    Text(balance.ownerDisplayValue).bold()
                }
            }
            Section {
                Button {
                    openURL(document.publicURL)
                } label: {
                    Label("Open and take action", systemImage: "safari")
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            } footer: {
                Text("Reviewing and responding happens on the studio’s secure page.")
            }
        }
    }
}

/// Resolves a `DocumentRef` (from a Home deep-link) to the exact document
/// detail. Self-loading so it works regardless of whether the Documents list
/// has loaded yet; the fetch is cache-first, so it usually returns instantly.
struct ClientDocumentDetailLoader: View {
    let ref: DocumentRef
    let projectID: Int64?
    @State private var model: ResourceModel<ClientDocuments>?

    init(ref: DocumentRef, projectID: Int64?, repository: ClientRepository) {
        self.ref = ref
        self.projectID = projectID
        if let projectID {
            _model = State(initialValue: ResourceModel(
                staleAfter: 15 * 60,
                cached: { try await repository.cachedDocuments(projectID: projectID) },
                remote: { try await repository.refreshDocuments(projectID: projectID) }
            ))
        } else {
            _model = State(initialValue: nil)
        }
    }

    var body: some View {
        Group {
            if let model {
                ResourceView(
                    model: model,
                    isEmpty: { _ in false },
                    content: { detail(in: $0) },
                    empty: { EmptyView() }
                )
            } else {
                notFound
            }
        }
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private func detail(in documents: ClientDocuments) -> some View {
        if ref.variant == "proposal",
           let proposal = documents.proposals.first(where: { $0.id == ref.id })
        {
            ClientProposalDetailView(proposal: proposal)
        } else if ref.variant == "contract",
                  let contract = documents.contracts.first(where: { $0.id == ref.id })
        {
            ClientContractDetailView(contract: contract)
        } else if ref.variant == "invoice",
                  let invoice = documents.invoices.first(where: { $0.id == ref.id })
        {
            ClientInvoiceDetailView(invoice: invoice)
        } else {
            notFound
        }
    }

    private var notFound: some View {
        ContentUnavailableView(
            "Document unavailable",
            systemImage: "doc.text",
            description: Text("Open the Documents tab to find it.")
        )
    }
}

struct ClientProposalDetailView: View {
    let proposal: Proposal
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    StatusPill(label: proposal.status.ownerDisplayName, tone: proposal.status.tone)
                    Text(proposal.title).miseDisplayFont(.title3)
                    if let intro = proposal.intro, !intro.isEmpty {
                        Text(intro).font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 6)
            }

            Section("What’s included") {
                ForEach(Array(proposal.lineItems.enumerated()), id: \.offset) { _, item in
                    LabeledContent {
                        Text(item.unitPrice.ownerDisplayValue)
                    } label: {
                        Text(item.label)
                        if item.quantity > 1 {
                            Text("Quantity \(item.quantity)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                LabeledContent("Total") {
                    Text(proposal.total.ownerDisplayValue).bold()
                }
            }

            if proposal.canAccept || proposal.canDecline {
                Section {
                    if let url = proposal.publicURL {
                        Button {
                            openURL(url)
                        } label: {
                            Label("Review and respond", systemImage: "safari")
                                .frame(maxWidth: .infinity, minHeight: 44)
                        }
                        .buttonStyle(.borderedProminent)
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                    }
                } footer: {
                    Text("Accepting or declining happens on the studio’s secure page.")
                }
            } else if proposal.status == .accepted {
                acceptedStrip
            }
        }
        .navigationTitle("Proposal")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var acceptedStrip: some View {
        Label {
            Text("Accepted\(proposal.acceptedAt.map { " " + $0.formatted(date: .abbreviated, time: .omitted) } ?? "")")
                .font(.subheadline.weight(.semibold))
        } icon: {
            Image(systemName: "checkmark.circle.fill")
        }
        .foregroundStyle(MiseDesign.ok)
        .frame(maxWidth: .infinity, alignment: .leading)
        .listRowBackground(MiseDesign.okBg)
    }
}

struct ClientContractDetailView: View {
    let contract: Contract
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    StatusPill(label: contract.status.ownerDisplayName, tone: contract.status.tone)
                    Text(contract.title).miseDisplayFont(.title3)
                }
                .padding(.vertical, 6)
            }

            Section("Agreement") {
                Text(contract.body)
                    .font(.subheadline)
                    .foregroundStyle(.primary)
            }

            if contract.status == .signed {
                Section {
                    Label {
                        Text(signedLabel).font(.subheadline.weight(.semibold))
                    } icon: {
                        Image(systemName: "checkmark.circle.fill")
                    }
                    .foregroundStyle(MiseDesign.ok)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .listRowBackground(MiseDesign.okBg)
                }
            } else if contract.canSign, let url = contract.publicURL {
                Section {
                    Button {
                        openURL(url)
                    } label: {
                        Label("Review and sign", systemImage: "safari")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                } footer: {
                    Text("Signing happens on the studio’s secure page, exactly as it appears there.")
                }
            }
        }
        .navigationTitle("Agreement")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var signedLabel: String {
        var label = "Signed"
        if let name = contract.signerName, !name.isEmpty {
            label += " by \(name)"
        }
        if let signedAt = contract.signedAt {
            label += " " + signedAt.formatted(date: .abbreviated, time: .omitted)
        }
        return label
    }
}

struct ClientInvoiceDetailView: View {
    let invoice: Invoice
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    StatusPill(label: invoice.status.ownerDisplayName, tone: invoice.status.tone)
                    Text(invoice.title).miseDisplayFont(.title3)
                }
                .padding(.vertical, 6)
            }

            Section {
                LabeledContent("Total", value: invoice.total.ownerDisplayValue)
                LabeledContent("Paid", value: invoice.paid.ownerDisplayValue)
                LabeledContent("Balance") {
                    Text(invoice.balance.ownerDisplayValue).bold()
                }
                if let dueOn = invoice.dueOn {
                    LabeledContent("Due", value: dueOn.rawValue)
                }
            }

            if invoice.status == .paid || invoice.balance.minorUnits == 0 {
                Section {
                    Label {
                        Text("Paid in full — thank you!")
                            .font(.subheadline.weight(.semibold))
                    } icon: {
                        Image(systemName: "checkmark.circle.fill")
                    }
                    .foregroundStyle(MiseDesign.ok)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .listRowBackground(MiseDesign.okBg)
                }
            } else if let url = invoice.publicURL {
                Section {
                    Button {
                        openURL(url)
                    } label: {
                        Label(
                            "Pay \(invoice.balance.ownerDisplayValue)",
                            systemImage: "creditcard"
                        )
                        .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                } footer: {
                    Text("Payment is handled by the studio’s secure checkout.")
                }
            }
        }
        .navigationTitle("Invoice")
        .navigationBarTitleDisplayMode(.inline)
    }
}
