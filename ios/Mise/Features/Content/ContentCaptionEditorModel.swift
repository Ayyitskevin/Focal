import Foundation
import Observation

struct CaptionSuggestionPollingSchedule: Sendable {
    let delays: [Duration]

    static let standard = Self(delays: [
        .milliseconds(750),
        .seconds(1),
        .seconds(2),
        .seconds(3),
        .seconds(5),
        .seconds(5),
        .seconds(5),
        .seconds(5),
        .seconds(5),
        .seconds(5),
    ])
}

@MainActor
@Observable
final class ContentCaptionEditorModel {
    private struct SaveIntent: Hashable {
        let body: String
        let suggestionID: UUID?
        let etag: String
    }

    private(set) var snapshot: ContentCaptionSnapshot?
    private(set) var suggestion: CaptionSuggestion?
    private(set) var isLoading = false
    private(set) var isRefreshing = false
    private(set) var isSaving = false
    private(set) var isGenerating = false
    private(set) var isDiscarding = false
    private(set) var errorMessage: String?
    private(set) var conflictMessage: String?
    private(set) var informationalMessage: String?
    private(set) var requiresReload = false
    private(set) var hasSuggestionRecoveryConflict = false

    var body = ""
    var instruction = ""

    let captionID: Int64
    let appSuggestionsEnabled: Bool
    let canWrite: Bool

    private let cachedCaption: @Sendable (Int64) async throws -> ContentCaptionSnapshot?
    private let refreshCaption: @Sendable (Int64) async throws -> ContentCaptionSnapshot
    private let startSuggestion:
        @Sendable (ContentCaptionSnapshot, CaptionSuggestionRequest) async throws
            -> CaptionSuggestion
    private let recoverSuggestion: @Sendable (Int64) async throws -> CaptionSuggestion?
    private let pollSuggestion: @Sendable (Int64, UUID) async throws -> CaptionSuggestion
    private let clearSuggestionRecovery: @Sendable (Int64) async throws -> Void
    private let updateCaption:
        @Sendable (ContentCaptionSnapshot, CaptionBodyUpdate, UUID) async throws
            -> ContentCaptionSnapshot
    private let pollingSchedule: CaptionSuggestionPollingSchedule
    private let sleep: @Sendable (Duration) async throws -> Void
    @ObservationIgnored
    private var suggestionTask: Task<Void, Never>?
    @ObservationIgnored
    private var suggestionOperationID: UUID?
    @ObservationIgnored
    private var loaded = false
    @ObservationIgnored
    private var originalBody = ""
    @ObservationIgnored
    private var usedSuggestionID: UUID?
    @ObservationIgnored
    private var bodyBeforeSuggestionUse: String?
    @ObservationIgnored
    private var submittedSaveIntent: SaveIntent?
    @ObservationIgnored
    private var saveKey = UUID()

    convenience init(
        repository: OwnerRepository,
        captionID: Int64,
        appSuggestionsEnabled: Bool,
        canWrite: Bool
    ) {
        self.init(
            captionID: captionID,
            appSuggestionsEnabled: appSuggestionsEnabled,
            canWrite: canWrite,
            cachedCaption: { try await repository.cachedContentCaption(id: $0) },
            refreshCaption: { try await repository.refreshContentCaption(id: $0) },
            startSuggestion: {
                try await repository.startCaptionSuggestion(for: $0, request: $1)
            },
            recoverSuggestion: {
                try await repository.recoverCaptionSuggestion(captionID: $0)
            },
            pollSuggestion: {
                try await repository.pollCaptionSuggestion(
                    captionID: $0,
                    suggestionID: $1
                )
            },
            clearSuggestionRecovery: {
                try await repository.clearCaptionSuggestionRecovery(captionID: $0)
            },
            updateCaption: {
                try await repository.updateContentCaption(
                    current: $0,
                    request: $1,
                    idempotencyKey: $2
                )
            }
        )
    }

    init(
        captionID: Int64,
        appSuggestionsEnabled: Bool,
        canWrite: Bool,
        cachedCaption: @escaping @Sendable (Int64) async throws
            -> ContentCaptionSnapshot?,
        refreshCaption: @escaping @Sendable (Int64) async throws
            -> ContentCaptionSnapshot,
        startSuggestion: @escaping @Sendable (
            ContentCaptionSnapshot,
            CaptionSuggestionRequest
        ) async throws -> CaptionSuggestion,
        recoverSuggestion: @escaping @Sendable (Int64) async throws
            -> CaptionSuggestion?,
        pollSuggestion: @escaping @Sendable (Int64, UUID) async throws
            -> CaptionSuggestion,
        clearSuggestionRecovery: @escaping @Sendable (Int64) async throws -> Void,
        updateCaption: @escaping @Sendable (
            ContentCaptionSnapshot,
            CaptionBodyUpdate,
            UUID
        ) async throws -> ContentCaptionSnapshot,
        pollingSchedule: CaptionSuggestionPollingSchedule = .standard,
        sleep: @escaping @Sendable (Duration) async throws -> Void = {
            try await ContinuousClock().sleep(for: $0)
        }
    ) {
        self.captionID = captionID
        self.appSuggestionsEnabled = appSuggestionsEnabled
        self.canWrite = canWrite
        self.cachedCaption = cachedCaption
        self.refreshCaption = refreshCaption
        self.startSuggestion = startSuggestion
        self.recoverSuggestion = recoverSuggestion
        self.pollSuggestion = pollSuggestion
        self.clearSuggestionRecovery = clearSuggestionRecovery
        self.updateCaption = updateCaption
        self.pollingSchedule = pollingSchedule
        self.sleep = sleep
    }

    var detail: ContentCaptionDetail? {
        snapshot?.value
    }

    var isApproved: Bool {
        detail?.status == .approved
    }

    var isShowingSavedCopy: Bool {
        snapshot?.source == .cache
    }

    var hasUnsavedChanges: Bool {
        body != originalBody
    }

    var suggestionControlsVisible: Bool {
        canWrite
            && appSuggestionsEnabled
            && detail?.suggestionsEnabled == true
            && detail?.status == .draft
    }

    var canGenerateSuggestion: Bool {
        suggestionControlsVisible
            && !isShowingSavedCopy
            && !requiresReload
            && !isGenerating
            && !isSaving
            && !hasSuggestionRecoveryConflict
            && suggestion?.state != .ready
            && !(suggestion.map(isActive) ?? false)
    }

    var canSave: Bool {
        guard detail?.status == .draft,
              canWrite,
              !isShowingSavedCopy,
              !requiresReload,
              !isSaving,
              !isLoading,
              hasUnsavedChanges
        else {
            return false
        }
        let normalized = normalizedBody
        return !normalized.isEmpty && normalized.unicodeScalars.count <= 100_000
    }

    var canUseSuggestion: Bool {
        guard let suggestion,
              suggestion.state == .ready,
              suggestion.candidateText != nil,
              let snapshot
        else {
            return false
        }
        return !requiresReload
            && canWrite
            && !suggestionIsStale
            && suggestion.baseRevision == snapshot.value.revision
            && snapshot.value.status == .draft
            && !isShowingSavedCopy
    }

    var suggestionIsStale: Bool {
        guard let suggestion else { return false }
        guard let snapshot else { return true }
        return suggestion.stale || suggestion.baseRevision != snapshot.value.revision
    }

    var usedSuggestion: Bool {
        usedSuggestionID != nil
    }

    func load() async {
        guard !loaded else { return }
        loaded = true
        isLoading = true
        errorMessage = nil
        informationalMessage = nil

        if let cached = try? await cachedCaption(captionID) {
            install(cached, preservingLocalWork: false)
            informationalMessage = "Showing a saved copy while Mise checks for updates."
        }

        do {
            let current = try await refreshCaption(captionID)
            try Task.checkCancellation()
            install(current, preservingLocalWork: false)
            errorMessage = nil
            informationalMessage = nil
            isLoading = false
            await recoverSuggestionIfAllowed()
        } catch is CancellationError {
            isLoading = false
            loaded = false
        } catch {
            isLoading = false
            if snapshot != nil {
                errorMessage =
                    "Offline — this saved caption is read-only until Mise reconnects. "
                    + error.localizedDescription
            } else {
                errorMessage = error.localizedDescription
            }
        }
    }

    func appear() async {
        if !loaded {
            await load()
            return
        }
        if snapshot == nil {
            loaded = false
            await load()
            return
        }
        if isShowingSavedCopy {
            await reloadPreservingLocalWork()
            return
        }
        guard suggestion == nil, !isGenerating else { return }
        await recoverSuggestionIfAllowed()
    }

    func reloadPreservingLocalWork() async {
        guard !isRefreshing else { return }
        cancelSuggestionPolling()
        isRefreshing = true
        errorMessage = nil
        informationalMessage = nil
        defer { isRefreshing = false }

        do {
            let current = try await refreshCaption(captionID)
            try Task.checkCancellation()
            install(current, preservingLocalWork: true)
            requiresReload = false
            conflictMessage = nil
            errorMessage = nil
            if let suggestion, isActive(suggestion) {
                beginPolling(suggestion)
            } else {
                await recoverSuggestionIfAllowed()
            }
            if hasSuggestionRecoveryConflict, errorMessage == nil {
                errorMessage =
                    "A caption-suggestion request is still pending. Clear it before "
                    + "starting another request."
            }
        } catch is CancellationError {
            return
        } catch {
            errorMessage =
                "Mise could not reload the latest caption. Your local text and suggestion "
                + "are still here. "
                + error.localizedDescription
        }
    }

    func beginSuggestion() {
        guard canGenerateSuggestion, let current = snapshot else { return }
        let request = CaptionSuggestionRequest(instruction: normalizedInstruction)
        let operationID = UUID()
        suggestionOperationID = operationID
        isGenerating = true
        errorMessage = nil
        conflictMessage = nil
        informationalMessage = nil

        suggestionTask = Task { [weak self] in
            guard let self else { return }
            do {
                let initial = try await self.startSuggestion(current, request)
                try Task.checkCancellation()
                guard self.suggestionOperationID == operationID else { return }
                self.suggestion = initial
                self.usedSuggestionID = nil
                self.hasSuggestionRecoveryConflict = false
                await self.pollUntilTerminal(initial, operationID: operationID)
            } catch is CancellationError {
                // Recovery metadata remains protected on device for a later resume.
            } catch let APIError.conflict(problem) {
                guard self.suggestionOperationID == operationID else { return }
                self.markConflict(
                    problem?.bestMessage
                        ?? "The caption or another suggestion changed before generation started."
                )
            } catch OwnerContentRepositoryError.suggestionRecoveryConflict {
                guard self.suggestionOperationID == operationID else { return }
                self.hasSuggestionRecoveryConflict = true
                self.errorMessage =
                    "A different caption-suggestion request is pending. Resume its "
                    + "accepted handle or clear the pending request before trying again."
            } catch {
                guard self.suggestionOperationID == operationID else { return }
                self.errorMessage = error.localizedDescription
            }
            self.finishSuggestionOperation(operationID)
        }
    }

    func resumeSuggestionPolling() {
        guard let suggestion,
              isActive(suggestion),
              suggestionControlsVisible,
              !requiresReload,
              !isGenerating
        else {
            return
        }
        beginPolling(suggestion)
    }

    func cancelSuggestionPolling() {
        suggestionOperationID = nil
        suggestionTask?.cancel()
        suggestionTask = nil
        isGenerating = false
    }

    func useSuggestion() {
        guard canUseSuggestion,
              let suggestion,
              let candidate = suggestion.candidateText
        else {
            return
        }
        if usedSuggestionID == nil {
            bodyBeforeSuggestionUse = body
        }
        body = candidate
        usedSuggestionID = suggestion.id
        informationalMessage =
            "Suggestion copied to the editor. Review it, make any changes, then save explicitly."
    }

    func discardSuggestion() async {
        guard suggestion != nil
            || usedSuggestionID != nil
            || hasSuggestionRecoveryConflict
        else {
            return
        }
        let restoredBody = bodyBeforeSuggestionUse
        let wasUsed = usedSuggestionID != nil
        cancelSuggestionPolling()
        suggestion = nil
        usedSuggestionID = nil
        bodyBeforeSuggestionUse = nil
        if let restoredBody {
            body = restoredBody
        }
        errorMessage = nil
        isDiscarding = true
        informationalMessage = wasUsed
            ? "Suggestion use undone. Your prior caption text was restored."
            : "Suggestion discarded. Your caption text was not changed."
        defer { isDiscarding = false }
        do {
            try await clearSuggestionRecovery(captionID)
            hasSuggestionRecoveryConflict = false
        } catch {
            hasSuggestionRecoveryConflict = true
            errorMessage =
                "The suggestion was removed from this screen, but Mise could not clear "
                + "its recovery handle. "
                + error.localizedDescription
        }
    }

    @discardableResult
    func save() async -> Bool {
        guard canSave, let current = snapshot else { return false }
        let suggestionID = saveSuggestionID(for: current)
        let request = CaptionBodyUpdate(
            body: normalizedBody,
            suggestionID: suggestionID
        )
        let intent = SaveIntent(
            body: request.body,
            suggestionID: suggestionID,
            etag: current.etag
        )
        if submittedSaveIntent != intent {
            submittedSaveIntent = intent
            saveKey = UUID()
        }

        isSaving = true
        errorMessage = nil
        conflictMessage = nil
        informationalMessage = nil
        defer { isSaving = false }

        do {
            let updated = try await updateCaption(current, request, saveKey)
            install(updated, preservingLocalWork: false)
            suggestion = nil
            usedSuggestionID = nil
            bodyBeforeSuggestionUse = nil
            submittedSaveIntent = nil
            saveKey = UUID()
            informationalMessage = "Caption saved."
            return true
        } catch let APIError.conflict(problem) {
            markConflict(
                problem?.bestMessage
                    ?? "This caption changed on another device before Mise could save it."
            )
            return false
        } catch {
            errorMessage =
                "Mise could not confirm the save. Your local text and suggestion are "
                + "still here. Retry Save when connected. "
                + error.localizedDescription
            return false
        }
    }

    func dismissMessage() {
        errorMessage = nil
        informationalMessage = nil
    }

    func waitForSuggestionOperation() async {
        await suggestionTask?.value
    }

    private var normalizedInstruction: String? {
        let value = instruction
            .precomposedStringWithCanonicalMapping
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    private var normalizedBody: String {
        body
            .precomposedStringWithCanonicalMapping
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func install(
        _ current: ContentCaptionSnapshot,
        preservingLocalWork: Bool
    ) {
        let localBody = body
        let localSuggestion = suggestion
        let localUsedSuggestionID = usedSuggestionID
        let localBodyBeforeSuggestionUse = bodyBeforeSuggestionUse
        let shouldPreserve = preservingLocalWork
            && (hasUnsavedChanges || localSuggestion != nil || localUsedSuggestionID != nil)

        snapshot = current
        originalBody = current.value.body
        body = shouldPreserve ? localBody : current.value.body
        if shouldPreserve {
            suggestion = localSuggestion
            usedSuggestionID = localUsedSuggestionID
            bodyBeforeSuggestionUse = localBodyBeforeSuggestionUse
        } else {
            suggestion = nil
            usedSuggestionID = nil
            bodyBeforeSuggestionUse = nil
            instruction = ""
        }
        submittedSaveIntent = nil
        saveKey = UUID()
    }

    private func recoverSuggestionIfAllowed() async {
        guard suggestionControlsVisible, !isShowingSavedCopy, !requiresReload else {
            return
        }
        do {
            guard let recovered = try await recoverSuggestion(captionID) else {
                return
            }
            suggestion = recovered
            hasSuggestionRecoveryConflict = false
            observeTerminalMessage(recovered)
            if isActive(recovered) {
                beginPolling(recovered)
            }
        } catch is CancellationError {
            return
        } catch let APIError.conflict(problem) {
            markConflict(
                problem?.bestMessage
                    ?? "Mise could not safely resume the previous suggestion."
            )
        } catch OwnerContentRepositoryError.suggestionRecoveryConflict {
            hasSuggestionRecoveryConflict = true
            errorMessage =
                "A different caption-suggestion request is pending. Clear it before "
                + "starting another request."
        } catch {
            errorMessage = "Mise could not resume the previous suggestion. "
                + error.localizedDescription
        }
    }

    private func beginPolling(_ initial: CaptionSuggestion) {
        let operationID = UUID()
        suggestionOperationID = operationID
        isGenerating = true
        suggestionTask = Task { [weak self] in
            guard let self else { return }
            await self.pollUntilTerminal(initial, operationID: operationID)
            self.finishSuggestionOperation(operationID)
        }
    }

    private func pollUntilTerminal(
        _ initial: CaptionSuggestion,
        operationID: UUID
    ) async {
        var current = initial
        if !isActive(current) {
            observeTerminalMessage(current)
            return
        }

        do {
            for delay in pollingSchedule.delays {
                try Task.checkCancellation()
                try await sleep(delay)
                try Task.checkCancellation()
                let next = try await pollSuggestion(captionID, current.id)
                guard suggestionOperationID == operationID else { return }
                suggestion = next
                current = next
                if !isActive(next) {
                    observeTerminalMessage(next)
                    return
                }
            }
            guard suggestionOperationID == operationID else { return }
            informationalMessage =
                "The suggestion is still being prepared. You can resume checking later."
        } catch is CancellationError {
            return
        } catch let APIError.conflict(problem) {
            guard suggestionOperationID == operationID else { return }
            markConflict(
                problem?.bestMessage
                    ?? "The caption changed while Mise was checking the suggestion."
            )
        } catch {
            guard suggestionOperationID == operationID else { return }
            errorMessage =
                "Mise stopped checking, but the suggestion may still finish. Resume "
                + "checking when connected. "
                + error.localizedDescription
        }
    }

    private func observeTerminalMessage(_ value: CaptionSuggestion) {
        switch value.state {
        case .ready:
            informationalMessage = value.stale
                ? "This suggestion is based on an older caption version. Reload before using it."
                : "Suggestion ready for your review."
        case .failed:
            errorMessage = failureMessage(value.failureReason)
        case .expired:
            errorMessage = "This suggestion expired. Discard it before generating another."
        case .applied:
            informationalMessage = "This suggestion has already been used."
        case .queued, .running:
            break
        default:
            errorMessage = "Mise received an unsupported suggestion state."
        }
    }

    private func failureMessage(_ failure: CaptionSuggestionFailure?) -> String {
        switch failure {
        case .disabled:
            "Suggestions are currently unavailable."
        case .providerError:
            "The suggestion service could not complete this request."
        case .invalidResponse:
            "The suggestion service returned text Mise could not safely use."
        case .sessionEnded:
            "This suggestion stopped because the owner session ended."
        case .unknownOutcome:
            "Mise could not safely determine whether the provider completed this request."
        case .internal:
            "Mise could not complete this suggestion safely."
        default:
            "Mise could not complete this suggestion."
        }
    }

    private func saveSuggestionID(for current: ContentCaptionSnapshot) -> UUID? {
        guard let usedSuggestionID,
              let suggestion,
              suggestion.id == usedSuggestionID,
              suggestion.state == .ready,
              !suggestion.stale,
              suggestion.baseRevision == current.value.revision
        else {
            return nil
        }
        return usedSuggestionID
    }

    private func isActive(_ value: CaptionSuggestion) -> Bool {
        value.state == .queued || value.state == .running
    }

    private func markConflict(_ message: String) {
        requiresReload = true
        conflictMessage =
            message
            + " Your local text and suggestion were kept. Reload the server version, "
            + "review the preserved work, then retry."
    }

    private func finishSuggestionOperation(_ operationID: UUID) {
        guard suggestionOperationID == operationID else { return }
        suggestionOperationID = nil
        suggestionTask = nil
        isGenerating = false
    }
}
