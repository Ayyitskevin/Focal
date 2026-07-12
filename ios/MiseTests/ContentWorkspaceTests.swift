import XCTest
@testable import Mise

@MainActor
final class ContentWorkspaceTests: XCTestCase {
    func testFeatureFlagFailsClosedAndContentPrecedesAI() {
        XCTAssertFalse(AppConfiguration.enabledFeatureFlag(nil))
        XCTAssertFalse(AppConfiguration.enabledFeatureFlag("NO"))
        XCTAssertFalse(AppConfiguration.enabledFeatureFlag("unexpected"))
        XCTAssertTrue(AppConfiguration.enabledFeatureFlag("YES"))
        XCTAssertTrue(AppConfiguration.enabledFeatureFlag(NSNumber(value: true)))

        XCTAssertEqual(Array(OwnerDestination.allCases.suffix(2)), [.content, .ai])
        XCTAssertEqual(OwnerDestination.content.title, "Content")
        XCTAssertEqual(OwnerDestination.content.icon, "text.quote")
        XCTAssertEqual(OwnerDestination.allCases.last, .ai)
    }

    func testCaptionSearchAndFiltersUseNormalizedSummaryFields() {
        let caption = Self.summary(status: .draft, aiAssisted: true)

        XCTAssertTrue(ContentCaptionSearch.matches(caption, query: "north"))
        XCTAssertTrue(ContentCaptionSearch.matches(caption, query: "summer"))
        XCTAssertTrue(ContentCaptionSearch.matches(caption, query: "  JULY  "))
        XCTAssertFalse(ContentCaptionSearch.matches(caption, query: "invoice"))
        XCTAssertTrue(ContentCaptionListFilter.drafts.includes(caption))
        XCTAssertTrue(ContentCaptionListFilter.aiAssisted.includes(caption))
        XCTAssertFalse(ContentCaptionListFilter.approved.includes(caption))
    }

    func testCachedCaptionIsPresentedReadOnlyWhenRefreshFails() async {
        let cached = Self.snapshot(source: .cache)
        let refreshes = ContentOfflineThenNetwork(Self.snapshot())
        let model = Self.model(
            cached: { _ in cached },
            refresh: { _ in try await refreshes.next() }
        )

        await model.load()

        XCTAssertEqual(model.detail?.id, 42)
        XCTAssertEqual(model.body, "Original caption")
        XCTAssertTrue(model.isShowingSavedCopy)
        XCTAssertTrue(model.suggestionControlsVisible)
        XCTAssertFalse(model.canGenerateSuggestion)
        model.body = "Offline edit"
        XCTAssertFalse(model.canSave)
        XCTAssertTrue(model.errorMessage?.contains("read-only") == true)

        await model.appear()

        XCTAssertFalse(model.isShowingSavedCopy)
        XCTAssertEqual(model.body, "Offline edit")
        XCTAssertNil(model.errorMessage)
        XCTAssertTrue(model.canSave)
    }

    func testSuggestionControlsRequireAppAndServerFlagsAndDraftState() async {
        let appDisabled = Self.model(
            appSuggestionsEnabled: false,
            refresh: { _ in Self.snapshot() }
        )
        await appDisabled.load()
        XCTAssertFalse(appDisabled.suggestionControlsVisible)

        let serverDisabled = Self.model(
            refresh: { _ in Self.snapshot(suggestionsEnabled: false) }
        )
        await serverDisabled.load()
        XCTAssertFalse(serverDisabled.suggestionControlsVisible)

        let approved = Self.model(
            refresh: {
                _ in Self.snapshot(status: .approved, suggestionsEnabled: false)
            }
        )
        await approved.load()
        approved.body = "Attempted edit"
        XCTAssertTrue(approved.isApproved)
        XCTAssertFalse(approved.canSave)
        XCTAssertFalse(approved.suggestionControlsVisible)
    }

    func testReadOnlyOwnerScopeNeverEnablesCaptionWritesOrSuggestions() async {
        let model = Self.model(
            canWrite: false,
            refresh: { _ in Self.snapshot() }
        )

        await model.load()
        model.body = "Attempted read-only edit"

        XCTAssertFalse(model.canWrite)
        XCTAssertFalse(model.canSave)
        XCTAssertFalse(model.suggestionControlsVisible)
        XCTAssertFalse(model.canGenerateSuggestion)
        XCTAssertFalse(model.canUseSuggestion)
    }

    func testSuggestionRemainsSeparateUntilUseAndExplicitSave() async {
        let recorder = ContentCallRecorder()
        let suggestionID = UUID()
        let queued = Self.suggestion(id: suggestionID, state: .queued)
        let ready = Self.suggestion(
            id: suggestionID,
            state: .ready,
            candidate: "Generated candidate"
        )
        let updated = Self.snapshot(
            body: "Generated candidate",
            revision: 2,
            source: .network
        )
        let model = Self.model(
            refresh: { _ in Self.snapshot() },
            start: { _, request in
                await recorder.recordStart(request)
                return queued
            },
            poll: { _, _ in
                await recorder.recordPoll()
                return ready
            },
            update: { _, request, key in
                await recorder.recordUpdate(request, key: key)
                return updated
            },
            schedule: .init(delays: [.zero])
        )

        await model.load()
        model.instruction = "  Warm and concise  "
        model.beginSuggestion()
        await model.waitForSuggestionOperation()

        let starts = await recorder.startRequests()
        XCTAssertEqual(starts, [CaptionSuggestionRequest(instruction: "Warm and concise")])
        XCTAssertEqual(model.suggestion?.candidateText, "Generated candidate")
        XCTAssertEqual(model.body, "Original caption")
        var updateCount = await recorder.updateCount()
        XCTAssertEqual(updateCount, 0)

        model.useSuggestion()
        XCTAssertEqual(model.body, "Generated candidate")
        XCTAssertTrue(model.usedSuggestion)
        updateCount = await recorder.updateCount()
        XCTAssertEqual(updateCount, 0)

        let didSave = await model.save()
        XCTAssertTrue(didSave)
        let updates = await recorder.updates()
        XCTAssertEqual(updates.count, 1)
        XCTAssertEqual(updates[0].request.body, "Generated candidate")
        XCTAssertEqual(updates[0].request.suggestionID, suggestionID)
        XCTAssertNil(model.suggestion)
        XCTAssertEqual(model.body, "Generated candidate")
    }

    func testPollingIsBoundedAndDoesNotStartDuplicateGeneration() async {
        let recorder = ContentCallRecorder()
        let queued = Self.suggestion(state: .queued)
        let model = Self.model(
            refresh: { _ in Self.snapshot() },
            start: { _, _ in queued },
            poll: { _, _ in
                await recorder.recordPoll()
                return queued
            },
            schedule: .init(delays: [.zero, .zero, .zero])
        )

        await model.load()
        model.beginSuggestion()
        await model.waitForSuggestionOperation()

        let pollCount = await recorder.pollCount()
        XCTAssertEqual(pollCount, 3)
        XCTAssertEqual(model.suggestion?.state, .queued)
        XCTAssertFalse(model.isGenerating)
        XCTAssertFalse(model.canGenerateSuggestion)
        XCTAssertTrue(model.informationalMessage?.contains("still being prepared") == true)
    }

    func testUndoUseRestoresPriorBodyAndCannotEraseSuggestionProvenance() async {
        let recorder = ContentCallRecorder()
        let suggestionID = UUID()
        let ready = Self.suggestion(
            id: suggestionID,
            state: .ready,
            candidate: "Generated candidate"
        )
        let updated = Self.snapshot(
            body: "Manual replacement",
            revision: 2,
            source: .network
        )
        let model = Self.model(
            refresh: { _ in Self.snapshot() },
            start: { _, _ in ready },
            clear: { _ in await recorder.recordClear() },
            update: { _, request, key in
                await recorder.recordUpdate(request, key: key)
                return updated
            }
        )

        await model.load()
        model.body = "Human local draft"
        model.beginSuggestion()
        await model.waitForSuggestionOperation()
        model.useSuggestion()
        model.body += " with edits"

        await model.discardSuggestion()

        XCTAssertEqual(model.body, "Human local draft")
        XCTAssertNil(model.suggestion)
        XCTAssertFalse(model.usedSuggestion)
        XCTAssertTrue(model.informationalMessage?.contains("restored") == true)

        model.body = "Manual replacement"
        let didSave = await model.save()
        XCTAssertTrue(didSave)
        let updates = await recorder.updates()
        XCTAssertEqual(updates.count, 1)
        XCTAssertNil(updates[0].request.suggestionID)
        XCTAssertEqual(updates[0].request.body, "Manual replacement")
    }

    func testReappearanceRecoversAnAcceptedSuggestionHandle() async {
        let queued = Self.suggestion(state: .queued)
        let recoveries = ContentRecoverySequence([nil, queued])
        let model = Self.model(
            refresh: { _ in Self.snapshot() },
            recover: { _ in try await recoveries.next() },
            poll: { _, _ in queued },
            schedule: .init(delays: [])
        )

        await model.appear()
        XCTAssertNil(model.suggestion)

        model.cancelSuggestionPolling()
        await model.appear()
        await model.waitForSuggestionOperation()

        XCTAssertEqual(model.suggestion?.id, queued.id)
        XCTAssertEqual(model.suggestion?.state, .queued)
        let recoveryCalls = await recoveries.callCount()
        XCTAssertEqual(recoveryCalls, 2)
    }

    func testConflictKeepsLocalTextAndSuggestionThenReloadMarksItEffectivelyStale() async {
        let base = Self.snapshot()
        let latest = Self.snapshot(
            body: "Server edit",
            revision: 2,
            source: .network
        )
        let refreshes = ContentRefreshSequence([base, latest])
        let ready = Self.suggestion(state: .ready, candidate: "Candidate")
        let model = Self.model(
            refresh: { _ in try await refreshes.next() },
            start: { _, _ in ready },
            update: { _, _, _ in
                throw APIError.conflict(APIProblem(
                    status: 409,
                    code: "resource.version_conflict",
                    detail: "Caption changed."
                ))
            }
        )

        await model.load()
        model.beginSuggestion()
        await model.waitForSuggestionOperation()
        model.useSuggestion()
        model.body += " with local revision"

        let didSave = await model.save()
        XCTAssertFalse(didSave)
        XCTAssertTrue(model.requiresReload)
        XCTAssertEqual(model.body, "Candidate with local revision")
        XCTAssertEqual(model.suggestion?.candidateText, "Candidate")

        await model.reloadPreservingLocalWork()

        XCTAssertFalse(model.requiresReload)
        XCTAssertEqual(model.body, "Candidate with local revision")
        XCTAssertEqual(model.suggestion?.candidateText, "Candidate")
        XCTAssertTrue(model.suggestionIsStale)
        XCTAssertFalse(model.canUseSuggestion)
        XCTAssertTrue(model.canSave)
    }

    func testRecoveryConflictMustBeExplicitlyClearedBeforeAnotherRequest() async {
        let recorder = ContentCallRecorder()
        let model = Self.model(
            refresh: { _ in Self.snapshot() },
            start: { _, _ in
                throw OwnerContentRepositoryError.suggestionRecoveryConflict
            },
            clear: { _ in await recorder.recordClear() }
        )

        await model.load()
        model.beginSuggestion()
        await model.waitForSuggestionOperation()

        XCTAssertTrue(model.hasSuggestionRecoveryConflict)
        XCTAssertFalse(model.canGenerateSuggestion)
        XCTAssertTrue(model.errorMessage?.contains("pending") == true)

        await model.reloadPreservingLocalWork()
        XCTAssertTrue(model.hasSuggestionRecoveryConflict)
        XCTAssertTrue(model.errorMessage?.contains("pending") == true)

        await model.discardSuggestion()

        XCTAssertFalse(model.hasSuggestionRecoveryConflict)
        let clearCount = await recorder.clearCount()
        XCTAssertEqual(clearCount, 1)
    }

    private static func model(
        appSuggestionsEnabled: Bool = true,
        canWrite: Bool = true,
        cached: @escaping @Sendable (Int64) async throws
            -> ContentCaptionSnapshot? = { _ in nil },
        refresh: @escaping @Sendable (Int64) async throws
            -> ContentCaptionSnapshot,
        start: @escaping @Sendable (
            ContentCaptionSnapshot,
            CaptionSuggestionRequest
        ) async throws -> CaptionSuggestion = { _, _ in
            throw ContentTestError.unexpectedCall
        },
        recover: @escaping @Sendable (Int64) async throws
            -> CaptionSuggestion? = { _ in nil },
        poll: @escaping @Sendable (Int64, UUID) async throws
            -> CaptionSuggestion = { _, _ in
                throw ContentTestError.unexpectedCall
            },
        clear: @escaping @Sendable (Int64) async throws -> Void = { _ in },
        update: @escaping @Sendable (
            ContentCaptionSnapshot,
            CaptionBodyUpdate,
            UUID
        ) async throws -> ContentCaptionSnapshot = { _, _, _ in
            throw ContentTestError.unexpectedCall
        },
        schedule: CaptionSuggestionPollingSchedule = .init(delays: [])
    ) -> ContentCaptionEditorModel {
        ContentCaptionEditorModel(
            captionID: 42,
            appSuggestionsEnabled: appSuggestionsEnabled,
            canWrite: canWrite,
            cachedCaption: cached,
            refreshCaption: refresh,
            startSuggestion: start,
            recoverSuggestion: recover,
            pollSuggestion: poll,
            clearSuggestionRecovery: clear,
            updateCaption: update,
            pollingSchedule: schedule,
            sleep: { _ in }
        )
    }

    nonisolated private static func snapshot(
        body: String = "Original caption",
        revision: Int64 = 1,
        status: ContentCaptionStatus = .draft,
        suggestionsEnabled: Bool = true,
        source: ResourceSnapshotSource = .network
    ) -> ContentCaptionSnapshot {
        let createdAt = Date(timeIntervalSince1970: 1_720_000_000)
        return ContentCaptionSnapshot(
            value: ContentCaptionDetail(
                id: 42,
                versionID: "0123456789abcdef0123456789abcdef",
                revision: revision,
                clientDisplayName: "North Studio",
                planID: 7,
                planTitle: "Summer Retainer",
                period: "2026-07",
                label: "July social caption",
                body: body,
                note: "Keep the client voice natural.",
                status: status,
                aiAssisted: false,
                aiDraftedAt: nil,
                suggestionsEnabled: suggestionsEnabled,
                createdAt: createdAt,
                updatedAt: createdAt.addingTimeInterval(TimeInterval(revision))
            ),
            etag: "\"content-caption-v1-\(revision)-0123456789abcdef01234567\"",
            storedAt: createdAt.addingTimeInterval(TimeInterval(revision)),
            source: source
        )
    }

    nonisolated private static func summary(
        status: ContentCaptionStatus,
        aiAssisted: Bool
    ) -> ContentCaptionSummary {
        ContentCaptionSummary(
            id: 42,
            versionID: "0123456789abcdef0123456789abcdef",
            revision: 1,
            clientDisplayName: "North Studio",
            planTitle: "Summer Retainer",
            period: "2026-07",
            label: "July social caption",
            bodyPreview: "Warm launch copy",
            status: status,
            aiAssisted: aiAssisted,
            updatedAt: Date(timeIntervalSince1970: 1_720_000_000)
        )
    }

    nonisolated private static func suggestion(
        id: UUID = UUID(),
        state: CaptionSuggestionState,
        candidate: String? = nil
    ) -> CaptionSuggestion {
        let createdAt = Date(timeIntervalSince1970: 1_720_000_000)
        return CaptionSuggestion(
            id: id,
            captionID: 42,
            state: state,
            review: .humanReview,
            candidateText: candidate,
            failureReason: nil,
            baseRevision: 1,
            stale: false,
            createdAt: createdAt,
            expiresAt: createdAt.addingTimeInterval(3_600),
            completedAt: state == .ready ? createdAt.addingTimeInterval(1) : nil
        )
    }
}

private actor ContentCallRecorder {
    struct Update: Sendable {
        let request: CaptionBodyUpdate
        let key: UUID
    }

    private var starts: [CaptionSuggestionRequest] = []
    private var pollTotal = 0
    private var recordedUpdates: [Update] = []
    private var clearTotal = 0

    func recordStart(_ request: CaptionSuggestionRequest) {
        starts.append(request)
    }

    func recordPoll() {
        pollTotal += 1
    }

    func recordUpdate(_ request: CaptionBodyUpdate, key: UUID) {
        recordedUpdates.append(Update(request: request, key: key))
    }

    func recordClear() {
        clearTotal += 1
    }

    func startRequests() -> [CaptionSuggestionRequest] {
        starts
    }

    func pollCount() -> Int {
        pollTotal
    }

    func updates() -> [Update] {
        recordedUpdates
    }

    func updateCount() -> Int {
        recordedUpdates.count
    }

    func clearCount() -> Int {
        clearTotal
    }
}

private actor ContentRefreshSequence {
    private var values: [ContentCaptionSnapshot]

    init(_ values: [ContentCaptionSnapshot]) {
        self.values = values
    }

    func next() throws -> ContentCaptionSnapshot {
        guard !values.isEmpty else { throw ContentTestError.unexpectedCall }
        return values.removeFirst()
    }
}

private actor ContentRecoverySequence {
    private var values: [CaptionSuggestion?]
    private var calls = 0

    init(_ values: [CaptionSuggestion?]) {
        self.values = values
    }

    func next() throws -> CaptionSuggestion? {
        calls += 1
        guard !values.isEmpty else { throw ContentTestError.unexpectedCall }
        return values.removeFirst()
    }

    func callCount() -> Int {
        calls
    }
}

private actor ContentOfflineThenNetwork {
    private let value: ContentCaptionSnapshot
    private var attempts = 0

    init(_ value: ContentCaptionSnapshot) {
        self.value = value
    }

    func next() throws -> ContentCaptionSnapshot {
        attempts += 1
        if attempts == 1 {
            throw ContentTestError.offline
        }
        return value
    }
}

private enum ContentTestError: LocalizedError, Sendable {
    case offline
    case unexpectedCall

    var errorDescription: String? {
        switch self {
        case .offline: "Offline for test"
        case .unexpectedCall: "Unexpected content test call"
        }
    }
}
