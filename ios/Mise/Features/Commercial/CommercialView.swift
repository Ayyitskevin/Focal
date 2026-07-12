import SwiftUI

/// Severity → the shared status-pill tone. `CommercialSeverity` is an
/// `APIStringValue` (forward-compatible), so match by equality, not a switch.
extension CommercialSeverity {
    var tone: StatusTone {
        if self == .missing { return .clay }
        if self == .attention { return .honey }
        if self == .ok { return .ok }
        return .neutral
    }

    var pillLabel: String {
        if self == .missing { return "Missing" }
        if self == .attention { return "Attention" }
        if self == .ok { return "Ready" }
        return rawValue.capitalized
    }
}

/// Typed drill-down routes for the Commercial tab. The server hands the app a
/// structured `ActionTarget`, never an admin URL; this is where those become
/// destinations.
enum CommercialRoute: Hashable {
    case company(id: Int64, name: String)
    case closeout(projectID: Int64, title: String)
    case arChase(companyID: Int64, name: String)

    static func from(target: ActionTarget, fallbackName: String, fallbackTitle: String)
        -> CommercialRoute?
    {
        if target.kind == .arChase, let id = target.companyID {
            return .arChase(companyID: id, name: fallbackName)
        }
        if target.kind == .project, let id = target.projectID {
            return .closeout(projectID: id, title: fallbackTitle)
        }
        if let id = target.companyID {
            return .company(id: id, name: fallbackName)
        }
        return nil
    }
}

@MainActor
struct CommercialView: View {
    let model: ResourceModel<[CommercialAction]>
    let repository: OwnerRepository

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: queue,
            empty: {
                ContentUnavailableView(
                    "All clear",
                    systemImage: "checkmark.seal",
                    description: Text("No company needs attention right now.")
                )
            }
        )
        .navigationTitle("Commercial")
        .navigationDestination(for: CommercialRoute.self) { route in
            switch route {
            case let .company(id, name):
                CompanyNextActionsView(repository: repository, companyID: id, companyName: name)
            case let .closeout(projectID, title):
                ProjectCloseoutView(repository: repository, projectID: projectID, title: title)
            case let .arChase(companyID, name):
                ArChaseAssistView(repository: repository, companyID: companyID, companyName: name)
            }
        }
    }

    private func queue(_ actions: [CommercialAction]) -> some View {
        List {
            ForEach(actions) { action in
                NavigationLink(value: route(for: action)) {
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text(action.companyName).font(.headline)
                            Spacer()
                            StatusPill(label: action.severity.pillLabel, tone: action.severity.tone)
                        }
                        Text(action.title).font(.subheadline).fontWeight(.semibold)
                        Text(action.detail).font(.footnote).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .refreshable { await model.refresh() }
    }

    /// A queue action always belongs to a company, so it always resolves to a
    /// route — falling back to the company view when the target has no drill-in.
    private func route(for action: CommercialAction) -> CommercialRoute {
        CommercialRoute.from(
            target: action.target,
            fallbackName: action.companyName,
            fallbackTitle: action.title
        ) ?? .company(id: action.companyID, name: action.companyName)
    }
}

// MARK: - Company next actions

@MainActor
struct CompanyNextActionsView: View {
    @State private var model: ResourceModel<CompanyNextActions>
    let repository: OwnerRepository
    let companyName: String

    init(repository: OwnerRepository, companyID: Int64, companyName: String) {
        self.repository = repository
        self.companyName = companyName
        _model = State(initialValue: ResourceModel(
            staleAfter: 0,
            cached: { nil },
            remote: { try await repository.companyNextActions(id: companyID) }
        ))
    }

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.actions.isEmpty },
            content: { value in
                List {
                    ForEach(value.actions) { action in
                        let route = CommercialRoute.from(
                            target: action.target,
                            fallbackName: value.companyName,
                            fallbackTitle: action.title
                        )
                        actionRow(action, route: route)
                    }
                }
                .refreshable { await model.refresh() }
            },
            empty: {
                ContentUnavailableView(
                    "All caught up",
                    systemImage: "checkmark.seal",
                    description: Text("Nothing needs attention for \(companyName).")
                )
            }
        )
        .navigationTitle(companyName)
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private func actionRow(_ action: NextAction, route: CommercialRoute?) -> some View {
        let content = VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(action.title).font(.subheadline).fontWeight(.semibold)
                Spacer()
                StatusPill(label: action.severity.pillLabel, tone: action.severity.tone)
            }
            Text(action.detail).font(.footnote).foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)

        if let route {
            NavigationLink(value: route) { content }
        } else {
            content.accessibilityElement(children: .combine)
        }
    }
}

// MARK: - Project closeout

@MainActor
struct ProjectCloseoutView: View {
    @State private var model: ResourceModel<ProjectCloseout>
    let title: String

    init(repository: OwnerRepository, projectID: Int64, title: String) {
        self.title = title
        _model = State(initialValue: ResourceModel(
            staleAfter: 0,
            cached: { nil },
            remote: { try await repository.projectCloseout(id: projectID) }
        ))
    }

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.items.isEmpty },
            content: { value in
                List {
                    Section {
                        HStack(spacing: 16) {
                            countTile(value.okCount, "Ready", .ok)
                            countTile(value.attentionCount, "Attention", .honey)
                            countTile(value.missingCount, "Missing", .clay)
                        }
                        .frame(maxWidth: .infinity)
                    } footer: {
                        Text(value.ready ? "This project is ready to close." : "Some items still need attention.")
                    }
                    Section {
                        ForEach(value.items) { item in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(item.title).font(.subheadline).fontWeight(.semibold)
                                    Spacer()
                                    StatusPill(label: item.badge, tone: item.severity.tone)
                                }
                                Text(item.detail).font(.footnote).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 2)
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
                .refreshable { await model.refresh() }
            },
            empty: {
                ContentUnavailableView(
                    "No checklist",
                    systemImage: "checklist",
                    description: Text("This project has no closeout items yet.")
                )
            }
        )
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func countTile(_ count: Int, _ label: String, _ tone: StatusTone) -> some View {
        VStack(spacing: 2) {
            Text("\(count)").font(.title2).fontWeight(.semibold).foregroundStyle(tone.foreground)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
}

// MARK: - AR chase assist (read-only)

@MainActor
struct ArChaseAssistView: View {
    @State private var model: ResourceModel<ArChaseAssist>
    let companyName: String

    init(repository: OwnerRepository, companyID: Int64, companyName: String) {
        self.companyName = companyName
        _model = State(initialValue: ResourceModel(
            staleAfter: 0,
            cached: { nil },
            remote: { try await repository.arChase(companyID: companyID) }
        ))
    }

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.overdueInvoices.isEmpty },
            content: { value in
                List {
                    Section {
                        LabeledContent("Open balance", value: value.owed.ownerDisplayValue)
                        LabeledContent("Status", value: value.cadence.detail)
                    }
                    Section("Past due") {
                        ForEach(value.overdueInvoices) { inv in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(inv.title ?? "Invoice #\(inv.invoiceID)")
                                        .font(.subheadline).fontWeight(.semibold)
                                    Spacer()
                                    Text(inv.owed.ownerDisplayValue).font(.subheadline)
                                }
                                if let due = inv.dueDate {
                                    Text("Due \(due.rawValue)").font(.footnote).foregroundStyle(.secondary)
                                }
                                Link("Open invoice", destination: inv.publicURL)
                                    .font(.footnote)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                    Section {
                        LabeledContent("To", value: value.draft.to)
                        LabeledContent("Subject", value: value.draft.subject)
                        Text(value.draft.body)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    } header: {
                        Text("Draft reminder")
                    } footer: {
                        Text("Review-only. Send the reminder from the web for now.")
                    }
                }
                .refreshable { await model.refresh() }
            },
            empty: {
                ContentUnavailableView(
                    "Nothing past due",
                    systemImage: "checkmark.circle",
                    description: Text("\(companyName) has no overdue invoices.")
                )
            }
        )
        .navigationTitle("AR chase")
        .navigationBarTitleDisplayMode(.inline)
    }
}
