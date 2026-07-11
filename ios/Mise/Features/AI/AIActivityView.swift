import Foundation
import SwiftUI

struct AIActivityView: View {
    let model: OwnerResourceModel<AIActivityFeed>
    let timeZoneIdentifier: String

    @State private var capabilityFilter = AIActivityCapabilityFilter.all
    @State private var needsAttentionOnly = false

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.runs.isEmpty },
            content: activityList,
            empty: emptyActivity
        )
        .navigationTitle("AI activity")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                filterMenu
            }
        }
    }

    private func activityList(_ feed: AIActivityFeed) -> some View {
        let visibleRuns = feed.runs.filter { run in
            capabilityFilter.includes(run.capability)
                && (!needsAttentionOnly || run.needsAttention)
        }

        return List {
            Section {
                ReadOnlyAIAdvisory()
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }

            if visibleRuns.isEmpty {
                ContentUnavailableView {
                    Label("No matching activity", systemImage: "line.3.horizontal.decrease.circle")
                } description: {
                    Text("Change the filters to see other AI runs.")
                } actions: {
                    Button("Clear filters") {
                        capabilityFilter = .all
                        needsAttentionOnly = false
                    }
                    .buttonStyle(.bordered)
                }
                .listRowBackground(Color.clear)
            } else {
                ForEach(groupedByStudioDay(visibleRuns)) { group in
                    Section(group.title) {
                        ForEach(group.runs) { run in
                            AIActivityRow(run: run, timeZone: studioTimeZone)
                        }
                    }
                }
            }

            if feed.hasOlderRuns {
                Section {
                    Label(
                        "Showing the latest 500 runs. Older activity is not included in this mobile view.",
                        systemImage: "clock.arrow.circlepath"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.refresh() }
    }

    private func emptyActivity() -> some View {
        VStack(spacing: 20) {
            ReadOnlyAIAdvisory()
            ContentUnavailableView(
                "No AI activity",
                systemImage: "sparkles",
                description: Text("Provider runs will appear here after the studio uses an AI workflow.")
            )
        }
        .padding()
    }

    private var filterMenu: some View {
        Menu {
            Section("Capability") {
                ForEach(AIActivityCapabilityFilter.allCases) { filter in
                    Button {
                        capabilityFilter = filter
                    } label: {
                        if capabilityFilter == filter {
                            Label(filter.title, systemImage: "checkmark")
                        } else {
                            Text(filter.title)
                        }
                    }
                }
            }

            Section {
                Toggle("Needs attention", isOn: $needsAttentionOnly)
                    .accessibilityHint("Shows failed runs and runs that require review")
            }

            if filtersAreActive {
                Section {
                    Button("Clear filters", systemImage: "xmark.circle") {
                        capabilityFilter = .all
                        needsAttentionOnly = false
                    }
                }
            }
        } label: {
            Image(systemName: filtersAreActive
                ? "line.3.horizontal.decrease.circle.fill"
                : "line.3.horizontal.decrease.circle")
        }
        .accessibilityLabel("Filter AI activity")
        .accessibilityValue(filterAccessibilityValue)
    }

    private var filtersAreActive: Bool {
        capabilityFilter != .all || needsAttentionOnly
    }

    private var filterAccessibilityValue: String {
        var values = [capabilityFilter.title]
        if needsAttentionOnly { values.append("Needs attention") }
        return values.joined(separator: ", ")
    }

    private var studioTimeZone: TimeZone {
        TimeZone(identifier: timeZoneIdentifier) ?? .current
    }

    private func groupedByStudioDay(_ runs: [AIRun]) -> [AIActivityDay] {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = studioTimeZone
        let grouped = Dictionary(grouping: runs) {
            calendar.startOfDay(for: $0.createdAt)
        }
        var style = Date.FormatStyle(date: .complete, time: .omitted)
        style.timeZone = studioTimeZone

        return grouped.keys.sorted(by: >).map { day in
            AIActivityDay(
                day: day,
                title: day.formatted(style),
                runs: grouped[day, default: []].sorted {
                    if $0.createdAt == $1.createdAt { return $0.id > $1.id }
                    return $0.createdAt > $1.createdAt
                }
            )
        }
    }
}

private struct AIActivityDay: Identifiable {
    let day: Date
    let title: String
    let runs: [AIRun]

    var id: Date { day }
}

private enum AIActivityCapabilityFilter: String, CaseIterable, Identifiable {
    case all
    case vision
    case content
    case products
    case other

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All capabilities"
        case .vision: "Vision"
        case .content: "Content"
        case .products: "Products"
        case .other: "Other"
        }
    }

    func includes(_ capability: AICapability) -> Bool {
        switch self {
        case .all: true
        case .vision: capability == .vision
        case .content: capability == .content
        case .products: capability == .products
        case .other: capability == .other
        }
    }
}

private struct ReadOnlyAIAdvisory: View {
    var body: some View {
        Label {
            Text("AI suggestions can be wrong. This activity is read-only and does not approve, publish, or apply results.")
        } icon: {
            Image(systemName: "info.circle.fill")
                .foregroundStyle(.tint)
        }
        .font(.footnote)
        .foregroundStyle(.secondary)
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.1), in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .combine)
    }
}

private struct AIActivityRow: View {
    let run: AIRun
    let timeZone: TimeZone

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(capabilityTitle, systemImage: capabilityIcon)
                .font(.headline)

            Label(statusTitle, systemImage: statusIcon)
                .font(.caption.weight(.semibold))
                .foregroundStyle(statusColor)

            Text(providerTitle)
                .font(.subheadline.weight(.medium))

            if let subject = run.subject {
                Label(subject.title, systemImage: subjectIcon(subject.kind))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            Label(reviewTitle, systemImage: reviewIcon)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            Label(timestamp, systemImage: "clock")
                .font(.caption)
                .foregroundStyle(.secondary)

            if !detailParts.isEmpty {
                Text(detailParts.joined(separator: " · "))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private var capabilityTitle: String {
        switch run.capability {
        case .vision: "Vision"
        case .content: "Content"
        case .products: "Products"
        default: "Other"
        }
    }

    private var capabilityIcon: String {
        switch run.capability {
        case .vision: "eye"
        case .content: "text.quote"
        case .products: "shippingbox"
        default: "sparkles"
        }
    }

    private var providerTitle: String {
        switch run.provider {
        case .argus: "Argus"
        case .qwen: "Qwen"
        case .odysseus: "Odysseus"
        case .dionysus: "Dionysus"
        case .aphrodite: "Aphrodite"
        default: "Other provider"
        }
    }

    private var statusTitle: String {
        switch run.status {
        case .ok: "Completed"
        case .disabled: "Disabled"
        case .providerError: "Provider issue"
        case .invalidResponse: "Invalid response"
        default: "Unknown status"
        }
    }

    private var statusIcon: String {
        switch run.status {
        case .ok: "checkmark.circle.fill"
        case .disabled: "pause.circle.fill"
        case .providerError: "exclamationmark.triangle.fill"
        case .invalidResponse: "exclamationmark.circle.fill"
        default: "questionmark.circle.fill"
        }
    }

    private var statusColor: Color {
        switch run.status {
        case .ok: .green
        case .disabled: .secondary
        case .providerError: .red
        case .invalidResponse: .orange
        default: .secondary
        }
    }

    private var reviewTitle: String {
        switch run.review {
        case .none: "No review required"
        case .humanReview: "Human review required"
        case .explicitCommit: "Explicit approval required"
        default: "Review policy unavailable"
        }
    }

    private var reviewIcon: String {
        switch run.review {
        case .none: "checkmark.shield"
        case .humanReview: "person.crop.circle.badge.checkmark"
        case .explicitCommit: "hand.raised.fill"
        default: "questionmark.diamond"
        }
    }

    private func subjectIcon(_ kind: AIActivitySubjectKind) -> String {
        switch kind {
        case .gallery: "photo.stack"
        case .caption: "text.bubble"
        default: "sparkles"
        }
    }

    private var timestamp: String {
        var style = Date.FormatStyle(date: .abbreviated, time: .shortened)
        style.timeZone = timeZone
        let zone = timeZone.abbreviation(for: run.createdAt) ?? timeZone.identifier
        return "\(run.createdAt.formatted(style)) \(zone)"
    }

    private var detailParts: [String] {
        var parts = [String]()
        if let latency = run.latencyMs { parts.append("\(latency.formatted()) ms") }
        if let tokens = run.tokens { parts.append("\(tokens.formatted()) tokens") }
        if let cost = run.costMicroUSD { parts.append("Reported \(costText(cost))") }
        return parts
    }

    private func costText(_ microUSD: Int64) -> String {
        let dollars = Decimal(Int(microUSD)) / Decimal(1_000_000)
        return dollars.formatted(
            .currency(code: "USD")
                .precision(.fractionLength(2 ... 6))
        )
    }
}
