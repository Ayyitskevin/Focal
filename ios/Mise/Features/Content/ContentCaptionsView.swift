import SwiftUI

enum ContentCaptionListFilter: String, CaseIterable, Identifiable {
    case all
    case drafts
    case approved
    case aiAssisted

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All captions"
        case .drafts: "Drafts"
        case .approved: "Approved"
        case .aiAssisted: "AI-assisted"
        }
    }

    func includes(_ caption: ContentCaptionSummary) -> Bool {
        switch self {
        case .all: true
        case .drafts: caption.status == .draft
        case .approved: caption.status == .approved
        case .aiAssisted: caption.aiAssisted
        }
    }
}

enum ContentCaptionSearch {
    static func matches(_ caption: ContentCaptionSummary, query: String) -> Bool {
        let term = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return true }
        return [
            caption.clientDisplayName,
            caption.planTitle,
            caption.period,
            caption.label,
            caption.bodyPreview,
        ].contains { $0.localizedCaseInsensitiveContains(term) }
    }
}

@MainActor
struct ContentCaptionsView: View {
    let model: OwnerResourceModel<ContentCaptionFeed>

    @State private var query = ""
    @State private var filter = ContentCaptionListFilter.all

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.captions.isEmpty },
            content: captionList,
            empty: {
                ContentUnavailableView(
                    "No captions",
                    systemImage: "text.quote",
                    description: Text(
                        "Recurring-plan captions will appear here when they are created in Mise."
                    )
                )
            }
        )
        .privacySensitive()
        .navigationTitle("Content")
        .searchable(
            text: $query,
            placement: .navigationBarDrawer(displayMode: .automatic),
            prompt: "Client, plan, period, or text"
        )
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                filterMenu
            }
        }
    }

    private func captionList(_ feed: ContentCaptionFeed) -> some View {
        let visible = feed.captions.filter {
            filter.includes($0) && ContentCaptionSearch.matches($0, query: query)
        }

        return List {
            if visible.isEmpty {
                ContentUnavailableView {
                    Label("No matching captions", systemImage: "line.3.horizontal.decrease.circle")
                } description: {
                    Text("Change the search or filter to see other captions.")
                } actions: {
                    Button("Clear search and filters") {
                        query = ""
                        filter = .all
                    }
                    .buttonStyle(.bordered)
                }
                .listRowBackground(Color.clear)
            } else {
                ForEach(visible) { caption in
                    NavigationLink(value: OwnerRoute.contentCaption(caption.id)) {
                        ContentCaptionRow(caption: caption)
                    }
                }
            }

            if feed.hasOlderCaptions {
                Section {
                    Label(
                        "Showing the latest 500 captions. Older captions remain available on the web.",
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

    private var filterMenu: some View {
        Menu {
            Picker("Caption status", selection: $filter) {
                ForEach(ContentCaptionListFilter.allCases) { value in
                    Text(value.title).tag(value)
                }
            }

            if filter != .all {
                Section {
                    Button("Clear filter", systemImage: "xmark.circle") {
                        filter = .all
                    }
                }
            }
        } label: {
            Image(systemName: filter == .all
                ? "line.3.horizontal.decrease.circle"
                : "line.3.horizontal.decrease.circle.fill")
        }
        .accessibilityLabel("Filter captions")
        .accessibilityValue(filter.title)
    }
}

private struct ContentCaptionRow: View {
    let caption: ContentCaptionSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline) {
                Text(caption.label)
                    .font(.headline)
                    .lineLimit(2)
                Spacer(minLength: 12)
                ContentCaptionStatusLabel(status: caption.status)
            }

            Text(caption.clientDisplayName)
                .font(.subheadline.weight(.medium))

            Text("\(caption.planTitle) · \(caption.period)")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(2)

            if !caption.bodyPreview.isEmpty {
                Text(caption.bodyPreview)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }

            if caption.aiAssisted {
                Label("AI-assisted", systemImage: "sparkles")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 5)
        .accessibilityElement(children: .combine)
    }
}

struct ContentCaptionStatusLabel: View {
    let status: ContentCaptionStatus

    var body: some View {
        Label(title, systemImage: icon)
            .font(.caption.weight(.semibold))
            .foregroundStyle(status == .approved ? Color.green : Color.orange)
            .accessibilityLabel("Status: \(title)")
    }

    private var title: String {
        status == .approved ? "Approved" : "Draft"
    }

    private var icon: String {
        status == .approved ? "checkmark.seal.fill" : "pencil.circle"
    }
}
