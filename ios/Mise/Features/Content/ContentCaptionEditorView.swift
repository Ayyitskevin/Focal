import SwiftUI

@MainActor
struct ContentCaptionEditorView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    @State private var model: ContentCaptionEditorModel

    let didSave: @MainActor () async -> Void

    init(
        repository: OwnerRepository,
        captionID: Int64,
        appSuggestionsEnabled: Bool,
        canWrite: Bool,
        didSave: @escaping @MainActor () async -> Void
    ) {
        _model = State(initialValue: ContentCaptionEditorModel(
            repository: repository,
            captionID: captionID,
            appSuggestionsEnabled: appSuggestionsEnabled,
            canWrite: canWrite
        ))
        self.didSave = didSave
    }

    var body: some View {
        Group {
            if let detail = model.detail {
                ScrollView {
                    VStack(spacing: 16) {
                        statusBanners
                        if horizontalSizeClass == .regular
                            && !dynamicTypeSize.isAccessibilitySize
                        {
                            HStack(alignment: .top, spacing: 20) {
                                editorColumn(detail)
                                    .frame(maxWidth: .infinity, alignment: .top)
                                supportingColumn(detail)
                                    .frame(width: 360, alignment: .top)
                            }
                        } else {
                            VStack(spacing: 16) {
                                editorColumn(detail)
                                supportingColumn(detail)
                            }
                        }
                    }
                    .frame(maxWidth: 1_100)
                    .padding()
                    .frame(maxWidth: .infinity)
                }
                .background(Color(uiColor: .systemGroupedBackground))
                .privacySensitive()
            } else if model.isLoading {
                ProgressView("Loading caption…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView {
                    Label("Couldn’t open caption", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(model.errorMessage ?? "This caption is unavailable.")
                } actions: {
                    Button("Try Again") {
                        Task { await model.reloadPreservingLocalWork() }
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
        }
        .navigationTitle(model.detail?.label ?? "Caption")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if model.detail?.status == .draft, model.canWrite {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            if await model.save() {
                                await didSave()
                            }
                        }
                    }
                    .disabled(!model.canSave)
                    .accessibilityHint(
                        model.requiresReload
                            ? "Reload the latest server version before saving"
                            : "Saves the edited draft without approving or publishing it"
                    )
                }
            }
        }
        .overlay {
            if model.isSaving || model.isRefreshing || model.isDiscarding {
                ZStack {
                    Color.black.opacity(0.08).ignoresSafeArea()
                    ProgressView()
                        .controlSize(.large)
                        .padding()
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                }
                .accessibilityLabel(progressLabel)
            }
        }
        .task { await model.appear() }
        .onDisappear { model.cancelSuggestionPolling() }
    }

    @ViewBuilder
    private var statusBanners: some View {
        if model.isShowingSavedCopy {
            ContentMessageBanner(
                title: "Saved copy — read only",
                message: "Reconnect and reload before editing, generating, or saving.",
                icon: "wifi.slash",
                color: .orange
            ) {
                Button("Retry latest version") {
                    Task { await model.reloadPreservingLocalWork() }
                }
                .buttonStyle(.borderedProminent)
            }
        }

        if !model.canWrite {
            ContentMessageBanner(
                title: "View-only access",
                message: "This owner session can read captions but cannot edit or request suggestions.",
                icon: "lock.fill",
                color: .secondary
            )
        }

        if let conflict = model.conflictMessage {
            ContentMessageBanner(
                title: "Reload required",
                message: conflict,
                icon: "arrow.triangle.2.circlepath",
                color: .orange
            ) {
                Button("Reload latest version") {
                    Task { await model.reloadPreservingLocalWork() }
                }
                .buttonStyle(.borderedProminent)
            }
        }

        if let error = model.errorMessage {
            ContentMessageBanner(
                title: "Mise needs your attention",
                message: error,
                icon: "exclamationmark.triangle.fill",
                color: .red
            ) {
                if model.hasSuggestionRecoveryConflict {
                    Button("Clear pending request") {
                        Task { await model.discardSuggestion() }
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button("Dismiss") { model.dismissMessage() }
                        .buttonStyle(.bordered)
                }
            }
        } else if let information = model.informationalMessage {
            ContentMessageBanner(
                title: "Caption status",
                message: information,
                icon: "info.circle.fill",
                color: .accentColor
            ) {
                Button("Dismiss") { model.dismissMessage() }
                    .buttonStyle(.bordered)
            }
        }
    }

    private func editorColumn(_ detail: ContentCaptionDetail) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            ContentCard {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(detail.label)
                            .font(.title2.weight(.semibold))
                        Spacer()
                        ContentCaptionStatusLabel(status: detail.status)
                    }
                    Text(detail.clientDisplayName)
                        .font(.headline)
                    Label(
                        "\(detail.planTitle) · \(detail.period)",
                        systemImage: "calendar.badge.clock"
                    )
                    .font(.subheadline)
                    .foregroundStyle(.secondary)

                    if detail.aiAssisted {
                        Label("AI-assisted draft", systemImage: "sparkles")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                }
                .accessibilityElement(children: .combine)
            }

            ContentCard {
                VStack(alignment: .leading, spacing: 12) {
                    Text(detail.status == .approved ? "Approved caption" : "Caption draft")
                        .font(.headline)

                    if detail.status == .approved || model.isShowingSavedCopy || !model.canWrite {
                        Text(detail.status == .approved ? detail.body : model.body)
                            .frame(maxWidth: .infinity, minHeight: 180, alignment: .topLeading)
                            .padding(12)
                            .background(
                                Color(uiColor: .secondarySystemGroupedBackground),
                                in: RoundedRectangle(cornerRadius: 12)
                            )
                    } else {
                        TextEditor(text: $model.body)
                            .frame(minHeight: 260)
                            .scrollContentBackground(.hidden)
                            .padding(8)
                            .background(
                                Color(uiColor: .secondarySystemGroupedBackground),
                                in: RoundedRectangle(cornerRadius: 12)
                            )
                            .overlay {
                                RoundedRectangle(cornerRadius: 12)
                                    .stroke(Color.secondary.opacity(0.25))
                            }
                            .accessibilityLabel("Caption draft")

                        Text("\(model.body.unicodeScalars.count) of 100,000 characters")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(
                                model.body.unicodeScalars.count > 100_000
                                    ? Color.red
                                    : Color.secondary
                            )
                    }

                    if detail.status == .approved {
                        Label(
                            "Approved captions are read-only here. This screen cannot reopen, publish, or send them.",
                            systemImage: "lock.fill"
                        )
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    } else if !model.canWrite {
                        Label(
                            "This session does not have studio write access.",
                            systemImage: "lock.fill"
                        )
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    } else if model.requiresReload {
                        Label(
                            "Your local text is preserved. Reload before Save becomes available.",
                            systemImage: "exclamationmark.arrow.triangle.2.circlepath"
                        )
                        .font(.footnote)
                        .foregroundStyle(.orange)
                    }
                }
            }

            if detail.status == .approved, model.hasUnsavedChanges {
                ContentCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Preserved local draft", systemImage: "doc.badge.clock")
                            .font(.headline)
                        Text(model.body)
                            .frame(maxWidth: .infinity, alignment: .topLeading)
                            .padding(12)
                            .background(
                                Color(uiColor: .secondarySystemGroupedBackground),
                                in: RoundedRectangle(cornerRadius: 12)
                            )
                        Text(
                            "The server caption is now approved, so this preserved local text cannot be saved from the app."
                        )
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func supportingColumn(_ detail: ContentCaptionDetail) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            if let note = detail.note, !note.isEmpty {
                ContentCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Studio note", systemImage: "note.text")
                            .font(.headline)
                        Text(note)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if model.suggestionControlsVisible {
                generationCard
            }

            if let suggestion = model.suggestion {
                suggestionCard(suggestion)
            }

            ContentCard {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Last updated", systemImage: "clock")
                        .font(.headline)
                    Text(detail.updatedAt.formatted(date: .abbreviated, time: .shortened))
                        .foregroundStyle(.secondary)
                    Text("Revision \(detail.revision)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
            }
        }
    }

    private var generationCard: some View {
        ContentCard {
            VStack(alignment: .leading, spacing: 12) {
                Label("Generate a suggestion", systemImage: "wand.and.stars")
                    .font(.headline)

                Text(
                    "Optional direction is sent only for this request. Generated text stays separate until you choose to copy it into the editor."
                )
                .font(.footnote)
                .foregroundStyle(.secondary)

                TextField(
                    "Optional direction",
                    text: $model.instruction,
                    axis: .vertical
                )
                .lineLimit(2...5)
                .disabled(model.isGenerating || model.isShowingSavedCopy)

                Text("\(model.instruction.unicodeScalars.count) of 1,000 characters")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(
                        model.instruction.unicodeScalars.count > 1_000
                            ? Color.red
                            : Color.secondary
                    )

                if model.isGenerating {
                    HStack {
                        ProgressView()
                        Text("Checking for a suggestion…")
                            .font(.subheadline)
                        Spacer()
                        Button("Cancel checking") {
                            model.cancelSuggestionPolling()
                        }
                        .buttonStyle(.bordered)
                    }
                    .accessibilityElement(children: .combine)
                } else {
                    Button("Generate Suggestion", systemImage: "wand.and.stars") {
                        model.beginSuggestion()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(
                        !model.canGenerateSuggestion
                            || model.instruction.unicodeScalars.count > 1_000
                    )
                    .accessibilityHint(
                        "Requests a separate draft for review; it does not change or save the caption"
                    )
                }
            }
        }
    }

    private func suggestionCard(_ suggestion: CaptionSuggestion) -> some View {
        ContentCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("Suggestion", systemImage: "sparkles")
                        .font(.headline)
                    Spacer()
                    Text(suggestionStateTitle(suggestion.state))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }

                if let candidate = suggestion.candidateText {
                    Text(candidate)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(
                            Color.accentColor.opacity(0.08),
                            in: RoundedRectangle(cornerRadius: 12)
                        )

                    Label(
                        "AI suggestions can be wrong. Review every word before copying or saving.",
                        systemImage: "person.crop.circle.badge.checkmark"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                } else if suggestion.state == .queued || suggestion.state == .running {
                    Text("The server is preparing a bounded, review-only suggestion.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                if model.suggestionIsStale {
                    Label(
                        "This suggestion is based on an older caption revision.",
                        systemImage: "clock.badge.exclamationmark"
                    )
                    .font(.footnote)
                    .foregroundStyle(.orange)
                }

                HStack {
                    if suggestion.state == .ready {
                        Button(model.usedSuggestion ? "Copied to Editor" : "Use Suggestion") {
                            model.useSuggestion()
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!model.canUseSuggestion || model.usedSuggestion)
                        .accessibilityHint(
                            "Copies the suggestion into the local editor without saving it"
                        )
                    } else if (suggestion.state == .queued || suggestion.state == .running)
                        && !model.isGenerating
                    {
                        Button("Resume checking") {
                            model.resumeSuggestionPolling()
                        }
                        .buttonStyle(.borderedProminent)
                    }

                    Button(model.usedSuggestion ? "Undo Use" : "Discard", role: .destructive) {
                        Task { await model.discardSuggestion() }
                    }
                    .buttonStyle(.bordered)
                    .disabled(model.isDiscarding)
                }
            }
        }
    }

    private var progressLabel: String {
        if model.isSaving { return "Saving caption" }
        if model.isDiscarding { return "Discarding suggestion" }
        return "Reloading caption"
    }

    private func suggestionStateTitle(_ state: CaptionSuggestionState) -> String {
        switch state {
        case .queued: "Queued"
        case .running: "Generating"
        case .ready: "Ready for review"
        case .failed: "Failed safely"
        case .applied: "Already used"
        case .expired: "Expired"
        default: "Unavailable"
        }
    }
}

private struct ContentCard<Content: View>: View {
    @ViewBuilder let content: () -> Content

    var body: some View {
        content()
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding()
            .background(Color(uiColor: .systemBackground), in: RoundedRectangle(cornerRadius: 16))
    }
}

private struct ContentMessageBanner<Actions: View>: View {
    let title: String
    let message: String
    let icon: String
    let color: Color
    @ViewBuilder let actions: () -> Actions

    init(
        title: String,
        message: String,
        icon: String,
        color: Color,
        @ViewBuilder actions: @escaping () -> Actions
    ) {
        self.title = title
        self.message = message
        self.icon = icon
        self.color = color
        self.actions = actions
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon)
                .font(.headline)
                .foregroundStyle(color)
            Text(message)
                .font(.subheadline)
            actions()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(color.opacity(0.1), in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .contain)
    }
}

private extension ContentMessageBanner where Actions == EmptyView {
    init(title: String, message: String, icon: String, color: Color) {
        self.init(
            title: title,
            message: message,
            icon: icon,
            color: color,
            actions: { EmptyView() }
        )
    }
}
