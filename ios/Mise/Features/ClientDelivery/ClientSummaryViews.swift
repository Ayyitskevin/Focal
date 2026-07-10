import SwiftUI

struct ClientPortalView: View {
    @State private var model: ClientResourceModel<ClientPortalSummary>

    init(repository: ClientDeliveryRepository) {
        _model = State(initialValue: ClientResourceModel(
            staleAfter: 30 * 60,
            cached: { try await repository.cachedPortal() },
            remote: { try await repository.refreshPortal() }
        ))
    }

    var body: some View {
        ClientResourceView(
            model: model,
            isEmpty: { _ in false },
            content: portal,
            empty: { EmptyView() }
        )
        .navigationTitle("Client portal")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func portal(_ portal: ClientPortalSummary) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                ClientAccessHeader(
                    eyebrow: "CLIENT PORTAL",
                    title: portal.clientDisplayName,
                    detail: "Delivered galleries, brand assets, and usage rights from your studio."
                )

                ClientSummarySection(title: "Galleries", icon: "photo.on.rectangle") {
                    if portal.galleries.isEmpty {
                        Text("No galleries have been shared.").foregroundStyle(.secondary)
                    } else {
                        ForEach(portal.galleries) { gallery in
                            SummaryCard {
                                LabeledContent(gallery.title) {
                                    if let expiresOn = gallery.expiresOn {
                                        Text("Expires \(expiresOn.rawValue)")
                                    } else {
                                        Text("Available")
                                    }
                                }
                            }
                        }
                    }
                }

                ClientSummarySection(title: "Brand assets", icon: "shippingbox") {
                    if portal.brandAssets.isEmpty {
                        Text("No brand assets have been shared.").foregroundStyle(.secondary)
                    } else {
                        ForEach(portal.brandAssets) { asset in
                            SummaryCard {
                                LabeledContent(asset.filename) {
                                    if let bytes = asset.byteCount {
                                        Text(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file))
                                    }
                                }
                            }
                        }
                    }
                }

                ClientSummarySection(title: "Usage rights", icon: "checkmark.shield") {
                    if let note = portal.usageRightsNote, !note.isEmpty {
                        Text(note).textSelection(.enabled)
                    }
                    ForEach(Array(portal.licenses.enumerated()), id: \.offset) { _, license in
                        SummaryCard {
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text(license.title).font(.headline)
                                    Spacer()
                                    if license.exclusive {
                                        Text("Exclusive")
                                            .font(.caption.weight(.semibold))
                                            .padding(.horizontal, 8)
                                            .padding(.vertical, 4)
                                            .background(Color.accentColor.opacity(0.13), in: Capsule())
                                    }
                                }
                                Text([license.scope, license.tier, license.term]
                                    .filter { !$0.isEmpty }
                                    .joined(separator: " • "))
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                if !license.territory.isEmpty {
                                    LabeledContent("Territory", value: license.territory.joined(separator: ", "))
                                }
                                if !license.channels.isEmpty {
                                    LabeledContent("Channels", value: license.channels.joined(separator: ", "))
                                }
                            }
                        }
                    }
                }
            }
            .clientContentMargins()
        }
        .refreshable { await model.refresh() }
    }
}

struct ClientWorkspaceView: View {
    let workspaceOrigin: URL
    @State private var model: ClientResourceModel<ClientWorkspaceSummary>

    init(repository: ClientDeliveryRepository, workspaceOrigin: URL) {
        self.workspaceOrigin = workspaceOrigin
        _model = State(initialValue: ClientResourceModel(
            staleAfter: 15 * 60,
            cached: { try await repository.cachedWorkspace() },
            remote: { try await repository.refreshWorkspace() }
        ))
    }

    var body: some View {
        ClientResourceView(
            model: model,
            isEmpty: { $0.resources.isEmpty },
            content: workspace,
            empty: {
                ContentUnavailableView(
                    "No project resources",
                    systemImage: "briefcase",
                    description: Text("The studio hasn’t shared project resources yet.")
                )
            }
        )
        .navigationTitle("Workspace")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func workspace(_ workspace: ClientWorkspaceSummary) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                ClientAccessHeader(
                    eyebrow: workspace.clientDisplayName.uppercased(),
                    title: workspace.title,
                    detail: "Review project resources. Signing and payment continue on the studio’s secure webpage."
                )
                ForEach(workspace.resources) { resource in
                    SummaryCard {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(alignment: .top) {
                                Label(resource.title, systemImage: resource.kind.systemImage)
                                    .font(.headline)
                                Spacer()
                                Text(resource.status.clientStatusDisplay)
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(.secondary)
                            }
                            if let total = resource.total {
                                LabeledContent("Total", value: total.clientDisplayValue)
                            }
                            if let dueOn = resource.dueOn {
                                LabeledContent("Due", value: dueOn.rawValue)
                            }
                            SecureBrowserAction(
                                url: resource.actionURL,
                                workspaceOrigin: workspaceOrigin,
                                allowedPathPrefix: resource.kind.allowedPathPrefix,
                                title: resource.kind.actionTitle,
                                systemImage: "arrow.up.right.square"
                            )
                        }
                    }
                }
            }
            .clientContentMargins()
        }
        .refreshable { await model.refresh() }
    }
}

struct ClientDocumentView: View {
    let workspaceOrigin: URL
    @State private var model: ClientResourceModel<ClientDocumentSummary>

    init(repository: ClientDeliveryRepository, workspaceOrigin: URL) {
        self.workspaceOrigin = workspaceOrigin
        _model = State(initialValue: ClientResourceModel(
            staleAfter: 10 * 60,
            cached: { try await repository.cachedDocument() },
            remote: { try await repository.refreshDocument() },
            discardsSnapshotOnFailure: ClientDeliveryFailure.isDocumentIntegrityFailure
        ))
    }

    var body: some View {
        ClientResourceView(
            model: model,
            isEmpty: { _ in false },
            content: document,
            empty: { EmptyView() },
            failureTitle: "Document integrity check failed",
            failureSystemImage: "exclamationmark.shield"
        )
        .navigationTitle("Document")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func document(_ document: ClientDocumentSummary) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                ClientAccessHeader(
                    eyebrow: document.kind.rawValue.uppercased(),
                    title: document.title,
                    detail: document.projectTitle
                )

                SummaryCard {
                    VStack(alignment: .leading, spacing: 10) {
                        LabeledContent("Client", value: document.clientDisplayName)
                        LabeledContent("Status", value: document.status.clientStatusDisplay)
                        if let dueOn = document.dueOn {
                            LabeledContent("Due", value: dueOn.rawValue)
                        }
                        if let detail = document.detail, !detail.isEmpty {
                            Divider()
                            Text(String(detail.prefix(20_000))).textSelection(.enabled)
                            if detail.count > 20_000 {
                                Text("Preview shortened for performance. Open the secure document for the complete text.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                if !document.lineItems.isEmpty {
                    ClientSummarySection(title: "Details", icon: "list.bullet.rectangle") {
                        ForEach(Array(document.lineItems.enumerated()), id: \.offset) { _, item in
                            SummaryCard {
                                LabeledContent("\(item.quantity) × \(item.label)") {
                                    Text(item.unitPrice.clientDisplayValue)
                                }
                            }
                        }
                    }
                }

                if document.total != nil || document.balance != nil {
                    ClientSummarySection(title: "Financial summary", icon: "creditcard") {
                        SummaryCard {
                            VStack(spacing: 10) {
                                if let total = document.total {
                                    LabeledContent("Total", value: total.clientDisplayValue)
                                }
                                if let deposit = document.deposit {
                                    LabeledContent("Deposit", value: deposit.clientDisplayValue)
                                }
                                if let paid = document.paid {
                                    LabeledContent("Paid", value: paid.clientDisplayValue)
                                }
                                if let balance = document.balance {
                                    LabeledContent("Balance", value: balance.clientDisplayValue)
                                        .fontWeight(.semibold)
                                }
                            }
                        }
                    }
                }

                if !document.payments.isEmpty {
                    ClientSummarySection(title: "Payment history", icon: "checkmark.circle") {
                        if document.paymentsTruncated {
                            Text("Showing the latest \(document.payments.count) of \(document.paymentCount) payments.")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        ForEach(document.payments) { payment in
                            SummaryCard {
                                HStack {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(payment.kind.ownerDisplayName)
                                            .font(.subheadline.weight(.semibold))
                                        Text(payment.createdAt, format: .dateTime.year().month().day())
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Text(payment.amount.clientDisplayValue)
                                        .font(.body.monospacedDigit().weight(.semibold))
                                }
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }
                }

                if document.canAct {
                    VStack(alignment: .leading, spacing: 8) {
                        SecureBrowserAction(
                            url: document.actionURL,
                            workspaceOrigin: workspaceOrigin,
                            allowedPathPrefix: document.kind.allowedPathPrefix,
                            title: document.kind.actionTitle,
                            systemImage: "lock.shield"
                        )
                        Text("For legal and payment safety, this action is completed on the studio’s secure webpage.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    SecureBrowserAction(
                        url: document.actionURL,
                        workspaceOrigin: workspaceOrigin,
                        allowedPathPrefix: document.kind.allowedPathPrefix,
                        title: "View secure document",
                        systemImage: "doc.text"
                    )
                }
            }
            .clientContentMargins()
        }
        .refreshable { await model.refresh() }
    }
}

private struct ClientAccessHeader: View {
    let eyebrow: String
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(eyebrow)
                .font(.caption.weight(.bold))
                .foregroundStyle(.tint)
                .tracking(0.8)
            Text(title).font(.largeTitle.bold())
            Text(detail).font(.subheadline).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct ClientSummarySection<Content: View>: View {
    let title: String
    let icon: String
    let content: Content

    init(
        title: String,
        icon: String,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.icon = icon
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: icon).font(.title3.bold())
            content
        }
    }
}

private struct SummaryCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.background, in: RoundedRectangle(cornerRadius: 16))
            .overlay {
                RoundedRectangle(cornerRadius: 16)
                    .stroke(Color.secondary.opacity(0.18), lineWidth: 1)
            }
    }
}

private extension View {
    func clientContentMargins() -> some View {
        frame(maxWidth: 820, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity)
    }
}

private extension Money {
    var clientDisplayValue: String { ownerDisplayValue }
}

private extension String {
    var clientStatusDisplay: String {
        replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + String($0.dropFirst()) }
            .joined(separator: " ")
    }
}

private extension ClientWorkspaceResourceKind {
    var systemImage: String {
        if self == .proposal { return "doc.badge.ellipsis" }
        if self == .contract { return "signature" }
        if self == .invoice { return "creditcard" }
        if self == .gallery { return "photo.on.rectangle" }
        return "doc"
    }

    var actionTitle: String {
        if self == .proposal { return "Review proposal" }
        if self == .contract { return "Review or sign securely" }
        if self == .invoice { return "Review or pay securely" }
        if self == .gallery { return "Open gallery" }
        return "Open secure resource"
    }

    var allowedPathPrefix: String {
        if self == .proposal { return "/p/" }
        if self == .contract { return "/c/" }
        if self == .invoice { return "/i/" }
        if self == .gallery { return "/g/" }
        return "/invalid/"
    }
}

private extension ClientDocumentKind {
    var actionTitle: String {
        if self == .proposal { return "Review proposal securely" }
        if self == .contract { return "Review or sign securely" }
        if self == .invoice { return "Review or pay securely" }
        return "Continue securely"
    }

    var allowedPathPrefix: String {
        if self == .proposal { return "/p/" }
        if self == .contract { return "/c/" }
        if self == .invoice { return "/i/" }
        return "/invalid/"
    }
}
