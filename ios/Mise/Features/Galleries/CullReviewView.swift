import SwiftUI

private enum CullReviewFilter: String, CaseIterable, Identifiable {
    case all
    case undecided
    case keep
    case cut

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .undecided: "Review"
        case .keep: "Kept"
        case .cut: "Cut"
        }
    }
}

private struct CullPreviewSelection: Identifiable {
    let id: Int64
}

@MainActor
struct CullReviewView: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let galleryTitle: String
    let media: any AuthenticatedMediaLoading
    let canDecide: Bool
    @State private var model: CullReviewModel
    @State private var reconciliation: CullParentReconciler
    @State private var filter = CullReviewFilter.all
    @State private var previewSelection: CullPreviewSelection?

    init(
        repository: OwnerRepository,
        media: any AuthenticatedMediaLoading,
        galleryID: Int64,
        galleryTitle: String,
        canDecide: Bool,
        didChange: @escaping @MainActor () async -> Void
    ) {
        self.galleryTitle = galleryTitle
        self.media = media
        self.canDecide = canDecide
        _model = State(initialValue: CullReviewModel(
            repository: repository,
            galleryID: galleryID
        ))
        _reconciliation = State(initialValue: CullParentReconciler(
            reconcile: didChange
        ))
    }

    var body: some View {
        Group {
            if model.items.isEmpty, model.isLoading || model.isRefreshing {
                ProgressView("Loading cull review…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.items.isEmpty {
                ContentUnavailableView {
                    Label(
                        model.errorMessage == nil ? "No photos to review" : "Cull review unavailable",
                        systemImage: model.errorMessage == nil
                            ? "photo.stack"
                            : "photo.badge.exclamationmark"
                    )
                } description: {
                    Text(model.errorMessage ?? "Ready photos will appear here for human review.")
                } actions: {
                    Button("Try again") { Task { await model.refresh() } }
                }
            } else {
                reviewContent
            }
        }
        .navigationTitle("Cull · \(galleryTitle)")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.load() }
        .sheet(item: $previewSelection) { selection in
            CullPreviewView(
                assetID: selection.id,
                model: model,
                media: media,
                canDecide: canDecide,
                markChanged: { reconciliation.changed() }
            )
        }
        .onAppear { reconciliation.appeared() }
        .onDisappear {
            reconciliation.disappeared()
        }
    }

    private var reviewContent: some View {
        ScrollView {
            LazyVStack(spacing: 16) {
                CullReviewSummary(counts: model.counts, canDecide: canDecide)

                if dynamicTypeSize.isAccessibilitySize {
                    Picker("Cull filter", selection: $filter) {
                        ForEach(CullReviewFilter.allCases) { value in
                            Text(value.title).tag(value)
                        }
                    }
                    .pickerStyle(.menu)
                    .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    Picker("Cull filter", selection: $filter) {
                        ForEach(CullReviewFilter.allCases) { value in
                            Text(value.title).tag(value)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                if let errorMessage = model.errorMessage {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                            .accessibilityHidden(true)
                        Text(errorMessage)
                            .font(.footnote)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button("Dismiss", systemImage: "xmark") {
                            model.dismissError()
                        }
                        .labelStyle(.iconOnly)
                    }
                    .padding()
                    .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
                }

                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 280), spacing: 16)],
                    spacing: 16
                ) {
                    ForEach(filteredItems) { item in
                        CullReviewCard(
                            item: item,
                            media: media,
                            isMutating: model.isMutating(assetID: item.assetID),
                            canDecide: canDecide,
                            openPreview: {
                                previewSelection = CullPreviewSelection(id: item.assetID)
                            },
                            decide: { action in
                                Task {
                                    if await model.decide(action, assetID: item.assetID) {
                                        reconciliation.changed()
                                    }
                                }
                            }
                        )
                    }
                }

                if filteredItems.isEmpty {
                    ContentUnavailableView(
                        "No \(filter.title.lowercased()) frames loaded",
                        systemImage: "line.3.horizontal.decrease.circle",
                        description: Text(
                            model.hasMore
                                ? "Load more frames or choose another filter."
                                : "Choose another filter."
                        )
                    )
                }

                if model.hasMore {
                    Button {
                        Task { await model.loadMore() }
                    } label: {
                        if model.isLoadingMore {
                            ProgressView()
                        } else {
                            Label("Load more frames", systemImage: "arrow.down.circle")
                        }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .disabled(model.isLoadingMore || model.isRefreshing)
                }
            }
            .padding()
        }
        .refreshable { await model.refresh() }
    }

    private var filteredItems: [CullItem] {
        switch filter {
        case .all: model.items
        case .undecided: model.items.filter { $0.state == nil }
        case .keep: model.items.filter { $0.state == .keep }
        case .cut: model.items.filter { $0.state == .cut }
        }
    }
}

@MainActor
final class CullParentReconciler {
    private let reconcile: @MainActor () async -> Void
    private var isVisible = false
    private var changeGeneration: UInt64 = 0
    private var reconciledGeneration: UInt64 = 0
    private var reconciliationTask: Task<Void, Never>?

    init(reconcile: @escaping @MainActor () async -> Void) {
        self.reconcile = reconcile
    }

    func appeared() {
        isVisible = true
    }

    func disappeared() {
        isVisible = false
        startIfNeeded()
    }

    func changed() {
        changeGeneration &+= 1
        if !isVisible {
            startIfNeeded()
        }
    }

    private func startIfNeeded() {
        guard reconciliationTask == nil,
              reconciledGeneration != changeGeneration
        else {
            return
        }
        // Retain this coordinator until the parent refresh completes. The view
        // may already have been popped when an in-flight PATCH returns.
        reconciliationTask = Task { [self] in
            await run()
        }
    }

    private func run() async {
        while !Task.isCancelled, reconciledGeneration != changeGeneration {
            let targetGeneration = changeGeneration
            await reconcile()
            reconciledGeneration = targetGeneration
        }
        reconciliationTask = nil
        if !isVisible, reconciledGeneration != changeGeneration {
            startIfNeeded()
        }
    }
}

private struct CullReviewSummary: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let counts: CullCounts
    let canDecide: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("AI-assisted, human-decided", systemImage: "person.crop.circle.badge.checkmark")
                .font(.headline)
            Text(
                canDecide
                    ? "Keeper scores are suggestions. Only the explicit controls below change delivery, and every cut can be restored."
                    : "Keeper scores are suggestions. This owner session has read-only access, so decisions are disabled."
            )
                .font(.subheadline)
                .foregroundStyle(.secondary)
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 10) {
                    metric("Review", counts.undecided)
                    metric("Kept", counts.keep)
                    metric("Cut", counts.cut)
                    metric("Scored", counts.scored)
                }
            } else {
                HStack(spacing: 18) {
                    metric("Review", counts.undecided)
                    metric("Kept", counts.keep)
                    metric("Cut", counts.cut)
                    metric("Scored", counts.scored)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 16))
    }

    private func metric(_ label: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value, format: .number).font(.title3.bold())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct CullReviewCard: View {
    let item: CullItem
    let media: any AuthenticatedMediaLoading
    let isMutating: Bool
    let canDecide: Bool
    let openPreview: () -> Void
    let decide: (CullAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(action: openPreview) {
                AuthenticatedMediaImage(
                    url: item.thumbnailURL ?? item.previewURL,
                    purpose: item.thumbnailURL == nil ? .preview : .thumbnail,
                    loader: media,
                    contentMode: .fill,
                    contentRevision: item.mediaRevision,
                    accessibilityLabel: "Preview \(item.filename)"
                )
                .frame(minHeight: 210)
                .aspectRatio(4 / 3, contentMode: .fill)
                .clipped()
                .overlay(alignment: .topTrailing) {
                    CullStateBadge(state: item.state)
                        .padding(10)
                }
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens a larger preview")

            VStack(alignment: .leading, spacing: 5) {
                Text(item.filename)
                    .font(.headline)
                    .lineLimit(2)
                if let score = item.keeperScore {
                    LabeledContent("AI keeper suggestion") {
                        Text(score, format: .percent.precision(.fractionLength(0)))
                    }
                    .font(.subheadline)
                    .accessibilityElement(children: .combine)
                    .accessibilityValue(
                        "Keeper score, \(score.formatted(.percent.precision(.fractionLength(0))))"
                    )
                } else {
                    Text("Not scored")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }

            CullDecisionBar(
                state: item.state,
                isWorking: isMutating,
                isEnabled: canDecide && hasMedia,
                decide: decide
            )
            if !hasMedia {
                Label(
                    "Preview unavailable — decisions disabled",
                    systemImage: "photo.badge.exclamationmark"
                )
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else if !canDecide {
                Label("Read-only owner access", systemImage: "lock")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding()
        .background(Color(uiColor: .secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay {
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.secondary.opacity(0.18))
        }
    }

    private var hasMedia: Bool {
        item.thumbnailURL != nil || item.previewURL != nil
    }
}

private struct CullStateBadge: View {
    let state: CullState?

    var body: some View {
        Label(title, systemImage: icon)
            .font(.caption.bold())
            .padding(.horizontal, 9)
            .padding(.vertical, 6)
            .foregroundStyle(.white)
            .background(color, in: Capsule())
    }

    private var title: String {
        switch state {
        case .keep: "Kept"
        case .cut: "Cut"
        default: "Review"
        }
    }

    private var icon: String {
        switch state {
        case .keep: "checkmark"
        case .cut: "xmark"
        default: "questionmark"
        }
    }

    private var color: Color {
        switch state {
        case .keep: .green
        case .cut: .red
        default: .gray
        }
    }
}

private struct CullDecisionBar: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let state: CullState?
    let isWorking: Bool
    let isEnabled: Bool
    let decide: (CullAction) -> Void

    var body: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(spacing: 8) {
                    actions
                }
            } else {
                HStack(spacing: 8) {
                    actions
                }
            }
        }
        .disabled(isWorking || !isEnabled)
        .overlay {
            if isWorking {
                ProgressView().controlSize(.small)
            }
        }
    }

    @ViewBuilder
    private var actions: some View {
        action("Keep", systemImage: "checkmark", action: .keep, disabled: state == .keep)
        action("Cut", systemImage: "xmark", action: .cut, disabled: state == .cut)
        action(
            "Reset",
            systemImage: "arrow.uturn.backward",
            action: .restore,
            disabled: state == nil
        )
    }

    private func action(
        _ title: String,
        systemImage: String,
        action: CullAction,
        disabled: Bool
    ) -> some View {
        Button(title, systemImage: systemImage) {
            decide(action)
        }
        .buttonStyle(.bordered)
        .frame(maxWidth: .infinity, minHeight: 44)
        .disabled(disabled || isWorking || !isEnabled)
    }
}

private struct CullPreviewView: View {
    let assetID: Int64
    let model: CullReviewModel
    let media: any AuthenticatedMediaLoading
    let canDecide: Bool
    let markChanged: @MainActor () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if let item = model.item(assetID: assetID) {
                    VStack(spacing: 16) {
                        AuthenticatedMediaImage(
                            url: item.previewURL ?? item.thumbnailURL,
                            purpose: item.previewURL == nil ? .thumbnail : .preview,
                            loader: media,
                            contentMode: .fit,
                            contentRevision: item.mediaRevision,
                            accessibilityLabel: item.filename
                        )
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .background(Color.black)

                        CullDecisionBar(
                            state: item.state,
                            isWorking: model.isMutating(assetID: assetID),
                            isEnabled: canDecide
                                && (item.previewURL != nil || item.thumbnailURL != nil)
                        ) { action in
                            Task {
                                if await model.decide(action, assetID: assetID) {
                                    markChanged()
                                }
                            }
                        }
                        .padding(.horizontal)
                        Group {
                            if item.previewURL == nil, item.thumbnailURL == nil {
                                Label(
                                    "Preview unavailable — decisions disabled",
                                    systemImage: "photo.badge.exclamationmark"
                                )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            } else if !canDecide {
                                Label("Read-only owner access", systemImage: "lock")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.bottom)
                    }
                    .navigationTitle(item.filename)
                } else {
                    ContentUnavailableView(
                        "Frame unavailable",
                        systemImage: "photo.badge.exclamationmark"
                    )
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
