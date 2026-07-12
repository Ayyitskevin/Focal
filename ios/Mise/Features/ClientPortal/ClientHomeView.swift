import SwiftUI

/// Client Home: a warm studio welcome plus dynamically generated next steps —
/// only what currently needs the client's attention, straight from the server.
struct ClientHomeView: View {
    let model: ResourceModel<ClientHomeSummary>
    let navigate: (ClientDestination) -> Void

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { _ in false },
            content: content,
            empty: { EmptyView() }
        )
        .navigationTitle("Welcome back")
    }

    private func content(_ summary: ClientHomeSummary) -> some View {
        List {
            Section {
                introCard(summary)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }

            if !summary.nextSteps.isEmpty {
                Section("Next steps") {
                    ForEach(summary.nextSteps) { step in
                        Button {
                            navigate(destination(for: step))
                        } label: {
                            nextStepRow(step)
                        }
                        .buttonStyle(.plain)
                    }
                }
            } else if summary.document == nil {
                Section {
                    Label {
                        Text("You’re all caught up — nothing needs your attention right now.")
                            .font(.subheadline)
                    } icon: {
                        Image(systemName: "checkmark.circle")
                            .foregroundStyle(MiseDesign.ok)
                    }
                }
            }

            if let document = summary.document {
                Section("Your document") {
                    Button {
                        navigate(.documents)
                    } label: {
                        documentRow(document)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .refreshable { await model.refresh() }
    }

    private func introCard(_ summary: ClientHomeSummary) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(summary.studioName.uppercased())
                .font(.system(size: 11, weight: .bold))
                .tracking(0.5)
                .foregroundStyle(MiseDesign.terra)
            Text(welcomeLine(summary))
                .miseDisplayFont(.title3)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(MiseDesign.terraTint, in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }

    private func welcomeLine(_ summary: ClientHomeSummary) -> String {
        if let name = summary.clientDisplayName, !name.isEmpty {
            return "It’s lovely to see you, \(firstName(of: name)) — everything for your project lives here."
        }
        switch summary.principalKind {
        case .gallery:
            return "Your photographs are ready whenever you are."
        case .document:
            return "Your document is ready whenever you are."
        default:
            return "Everything the studio has shared with you lives here."
        }
    }

    private func firstName(of name: String) -> String {
        name.split(separator: " ").first.map(String.init) ?? name
    }

    private func nextStepRow(_ step: NextStepAction) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icon(for: step.kind))
                .font(.body)
                .foregroundStyle(tone(for: step.kind).foreground)
                .frame(width: 38, height: 38)
                .background(tone(for: step.kind).background, in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(step.title).font(.subheadline.weight(.semibold))
                Text(step.detail).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(MiseDesign.inkFaint)
                .accessibilityHidden(true)
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens \(destination(for: step).title)")
    }

    private func documentRow(_ document: ClientDocumentPreview) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icon(for: NextStepKind(rawValue: document.variant)))
                .font(.body)
                .foregroundStyle(MiseDesign.terra)
                .frame(width: 38, height: 38)
                .background(MiseDesign.terraTint, in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(document.title).font(.subheadline.weight(.semibold))
                Text(document.status.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if let balance = document.balance, balance.minorUnits > 0 {
                Text(balance.ownerDisplayValue)
                    .font(.subheadline.weight(.semibold))
            } else if let total = document.total {
                Text(total.ownerDisplayValue)
                    .font(.subheadline.weight(.semibold))
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
    }

    private func destination(for step: NextStepAction) -> ClientDestination {
        switch step.kind {
        case .gallery: .gallery
        default: .documents
        }
    }

    private func icon(for kind: NextStepKind) -> String {
        switch kind {
        case .proposal: "sparkles"
        case .contract: "checkmark.seal"
        case .invoice: "creditcard"
        case .gallery: "photo.on.rectangle"
        default: "doc.text"
        }
    }

    private func tone(for kind: NextStepKind) -> StatusTone {
        switch kind {
        case .proposal: .honey
        case .invoice: .ok
        case .contract:
            StatusTone(foreground: MiseDesign.terra, background: MiseDesign.terraTint)
        default:
            StatusTone(foreground: MiseDesign.terra, background: MiseDesign.terraTint)
        }
    }
}
