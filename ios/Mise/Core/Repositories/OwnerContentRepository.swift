import Foundation

private enum OwnerContentCache {
    static let feed = "content.captions.v1"
    static let aiActivity = "ai-activity.v1"
    static let maximumPageCount = 5
    static let pageSize = 100
    static let maximumCursorLength = 1_024

    static func detail(_ captionID: Int64) -> String {
        "content.caption.\(captionID).v1"
    }

    static func recovery(_ captionID: Int64) -> String {
        "content.caption.\(captionID).suggestion-handle.v1"
    }

    static func legacyRecovery(_ captionID: Int64) -> String {
        "content.caption.\(captionID).suggestion-recovery.v1"
    }
}

enum OwnerContentRepositoryError: LocalizedError, Sendable {
    case invalidInstruction
    case invalidBody
    case captionNotEditable
    case suggestionsUnavailable
    case suggestionRecoveryConflict

    var errorDescription: String? {
        switch self {
        case .invalidInstruction:
            "Keep the optional direction under 1,000 characters and remove unsupported controls."
        case .invalidBody:
            "A caption must contain between 1 and 100,000 valid characters."
        case .captionNotEditable:
            "Approved captions must be reopened before they can be edited."
        case .suggestionsUnavailable:
            "Caption suggestions are not available for this draft."
        case .suggestionRecoveryConflict:
            "Another caption suggestion is still recoverable. Resume or clear it first."
        }
    }
}

/// Sensitive retry context exists only for the lifetime of this repository.
/// A pre-accept process death is therefore not auto-recoverable: the app will
/// never persist or silently replay an owner instruction to close that gap.
fileprivate struct PendingCaptionSuggestionIntent: Sendable {
    let captionID: Int64
    let versionID: String
    let baseRevision: Int64
    let sourceETag: String
    let request: CaptionSuggestionRequest
    let idempotencyKey: UUID
}

/// Mutable only through actor-isolated `OwnerRepository` methods. Keeping this
/// synchronous makes prompt insertion and session purge one serialization domain.
final class OwnerContentSessionState {
    private var pendingIntents: [Int64: PendingCaptionSuggestionIntent] = [:]

    fileprivate func intent(
        for current: ContentCaptionSnapshot,
        request: CaptionSuggestionRequest
    ) throws -> PendingCaptionSuggestionIntent {
        if let existing = pendingIntents[current.value.id] {
            guard existing.captionID == current.value.id,
                  existing.versionID == current.value.versionID,
                  existing.baseRevision == current.value.revision,
                  existing.sourceETag == current.etag,
                  existing.request == request
            else {
                throw OwnerContentRepositoryError.suggestionRecoveryConflict
            }
            return existing
        }
        let intent = PendingCaptionSuggestionIntent(
            captionID: current.value.id,
            versionID: current.value.versionID,
            baseRevision: current.value.revision,
            sourceETag: current.etag,
            request: request,
            idempotencyKey: UUID()
        )
        pendingIntents[current.value.id] = intent
        return intent
    }

    func clear(captionID: Int64) {
        pendingIntents.removeValue(forKey: captionID)
    }

    func removeAll() {
        pendingIntents.removeAll()
    }
}

/// The only suggestion state persisted on device. Disk recovery starts after
/// the server accepts an opaque suggestion UUID and contains no prompt, body,
/// instruction, candidate, source ETag, or pre-accept idempotency key.
private struct CaptionSuggestionRecovery: Codable, Hashable, Sendable {
    let captionID: Int64
    let suggestionID: UUID
    let terminalInvalidationObserved: Bool

    func observingTerminalWork() -> Self {
        Self(
            captionID: captionID,
            suggestionID: suggestionID,
            terminalInvalidationObserved: true
        )
    }
}

extension OwnerRepository {
    func cachedContentCaptions() async throws -> ResourceSnapshot<ContentCaptionFeed>? {
        let ticket = try await cacheLifetimeTicket()
        guard let record = try await validCachedContentFeed(lifetimeTicket: ticket) else {
            return nil
        }
        return ResourceSnapshot(
            value: record.value,
            storedAt: record.storedAt,
            source: .cache
        )
    }

    func refreshContentCaptions() async throws -> ResourceSnapshot<ContentCaptionFeed> {
        let ticket = try await cacheLifetimeTicket()
        let cached = try await validCachedContentFeed(lifetimeTicket: ticket)
        let firstResponse: APIResponse<ContentCaptionPage>
        do {
            firstResponse = try await sendWithMetadata(
                MiseEndpoints.Content.captions(
                    limit: OwnerContentCache.pageSize,
                    etag: cached?.etag
                )
            )
        } catch let APIError.notModified(responseETag) {
            try await requireActiveCacheLifetime(ticket)
            guard let cached,
                  let cachedETag = cached.etag,
                  let responseETag,
                  responseETag == cachedETag,
                  Self.isStrongContentETag(responseETag)
            else {
                throw APIError.unexpectedResponse
            }
            guard let touched = try await cache.touch(
                OwnerContentCache.feed,
                as: ContentCaptionFeed.self,
                etag: responseETag
            ) else {
                throw OwnerRepositoryError.missingConditionalValue
            }
            try Self.validateContentCaptionFeed(touched.value)
            try await requireActiveCacheLifetime(ticket)
            return ResourceSnapshot(
                value: touched.value,
                storedAt: touched.storedAt,
                source: .revalidated
            )
        }

        try await requireActiveCacheLifetime(ticket)
        guard let firstETag = firstResponse.metadata.etag,
              Self.isStrongContentETag(firstETag)
        else {
            throw APIError.unexpectedResponse
        }
        let feed = try await assembleContentCaptionFeed(
            firstPage: firstResponse.value,
            lifetimeTicket: ticket
        )
        try await requireActiveCacheLifetime(ticket)
        let record = try await cache.write(
            feed,
            key: OwnerContentCache.feed,
            etag: firstETag,
            storedAt: firstResponse.metadata.receivedAt
        )
        try await requireActiveCacheLifetime(ticket)
        return ResourceSnapshot(
            value: record.value,
            storedAt: record.storedAt,
            source: .network
        )
    }

    func cachedContentCaption(id: Int64) async throws -> ContentCaptionSnapshot? {
        guard id > 0 else { throw APIError.unexpectedResponse }
        let ticket = try await cacheLifetimeTicket()
        guard let record = try await validCachedContentDetail(
            captionID: id,
            lifetimeTicket: ticket
        ), let etag = record.etag else {
            return nil
        }
        return ContentCaptionSnapshot(
            value: record.value,
            etag: etag,
            storedAt: record.storedAt,
            source: .cache
        )
    }

    func refreshContentCaption(id: Int64) async throws -> ContentCaptionSnapshot {
        guard id > 0 else { throw APIError.unexpectedResponse }
        let ticket = try await cacheLifetimeTicket()
        let key = OwnerContentCache.detail(id)
        let cached = try await validCachedContentDetail(
            captionID: id,
            lifetimeTicket: ticket
        )
        do {
            let response = try await sendWithMetadata(
                MiseEndpoints.Content.detail(id: id, etag: cached?.etag)
            )
            try await requireActiveCacheLifetime(ticket)
            guard let responseETag = response.metadata.etag,
                  Self.isStrongContentETag(responseETag)
            else {
                throw APIError.unexpectedResponse
            }
            try Self.validateContentCaptionDetail(response.value, expectedID: id)
            let record = try await cache.write(
                response.value,
                key: key,
                etag: responseETag,
                storedAt: response.metadata.receivedAt
            )
            try await requireActiveCacheLifetime(ticket)
            return ContentCaptionSnapshot(
                value: record.value,
                etag: responseETag,
                storedAt: record.storedAt,
                source: .network
            )
        } catch let APIError.notModified(responseETag) {
            try await requireActiveCacheLifetime(ticket)
            guard let cached,
                  let cachedETag = cached.etag,
                  let responseETag,
                  responseETag == cachedETag,
                  Self.isStrongContentETag(responseETag)
            else {
                throw APIError.unexpectedResponse
            }
            guard let touched = try await cache.touch(
                key,
                as: ContentCaptionDetail.self,
                etag: responseETag
            ) else {
                throw OwnerRepositoryError.missingConditionalValue
            }
            try Self.validateContentCaptionDetail(touched.value, expectedID: id)
            try await requireActiveCacheLifetime(ticket)
            return ContentCaptionSnapshot(
                value: touched.value,
                etag: responseETag,
                storedAt: touched.storedAt,
                source: .revalidated
            )
        }
    }

    func startCaptionSuggestion(
        for current: ContentCaptionSnapshot,
        request: CaptionSuggestionRequest
    ) async throws -> CaptionSuggestion {
        let ticket = try await cacheLifetimeTicket()
        try Self.validateContentCaptionSnapshot(current)
        guard current.value.status == .draft else {
            throw OwnerContentRepositoryError.captionNotEditable
        }
        guard current.value.suggestionsEnabled else {
            throw OwnerContentRepositoryError.suggestionsUnavailable
        }
        let normalizedRequest = try Self.normalizedSuggestionRequest(request)
        guard try await validSuggestionRecovery(
            captionID: current.value.id,
            lifetimeTicket: ticket
        ) == nil else {
            throw OwnerContentRepositoryError.suggestionRecoveryConflict
        }
        let intent = try contentSessionState.intent(
            for: current,
            request: normalizedRequest
        )
        do {
            return try await submitCaptionSuggestion(
                intent,
                lifetimeTicket: ticket
            )
        } catch {
            if Self.suggestionCreateWasDefinitivelyRejected(error) {
                contentSessionState.clear(captionID: current.value.id)
            }
            throw error
        }
    }

    /// Resumes an accepted server operation. An ambiguous pre-accept process
    /// death returns nil rather than persisting or silently replaying its prompt.
    func recoverCaptionSuggestion(captionID: Int64) async throws -> CaptionSuggestion? {
        guard captionID > 0 else { throw APIError.unexpectedResponse }
        let ticket = try await cacheLifetimeTicket()
        guard let recovery = try await validSuggestionRecovery(
            captionID: captionID,
            lifetimeTicket: ticket
        ) else {
            return nil
        }
        return try await pollCaptionSuggestion(
            captionID: captionID,
            suggestionID: recovery.suggestionID,
            recovery: recovery,
            lifetimeTicket: ticket
        )
    }

    func pollCaptionSuggestion(
        captionID: Int64,
        suggestionID: UUID
    ) async throws -> CaptionSuggestion {
        guard captionID > 0 else { throw APIError.unexpectedResponse }
        let ticket = try await cacheLifetimeTicket()
        let recovery = try await validSuggestionRecovery(
            captionID: captionID,
            lifetimeTicket: ticket
        )
        if let recoveredID = recovery?.suggestionID,
           recoveredID != suggestionID
        {
            throw OwnerContentRepositoryError.suggestionRecoveryConflict
        }
        return try await pollCaptionSuggestion(
            captionID: captionID,
            suggestionID: suggestionID,
            recovery: recovery,
            lifetimeTicket: ticket
        )
    }

    func clearCaptionSuggestionRecovery(captionID: Int64) async throws {
        guard captionID > 0 else { throw APIError.unexpectedResponse }
        let ticket = try await cacheLifetimeTicket()
        contentSessionState.clear(captionID: captionID)
        try await requireActiveCacheLifetime(ticket)
        try await cache.remove(OwnerContentCache.legacyRecovery(captionID))
        try await cache.remove(OwnerContentCache.recovery(captionID))
        try await requireActiveCacheLifetime(ticket)
    }

    /// Saves are deliberately online-only. The no-store mutation response is
    /// returned in memory, while every persisted read projection is invalidated.
    func updateContentCaption(
        current: ContentCaptionSnapshot,
        request: CaptionBodyUpdate,
        idempotencyKey: UUID
    ) async throws -> ContentCaptionSnapshot {
        let ticket = try await cacheLifetimeTicket()
        try Self.validateContentCaptionSnapshot(current)
        guard current.value.status == .draft else {
            throw OwnerContentRepositoryError.captionNotEditable
        }
        let normalizedRequest = try Self.normalizedBodyUpdate(request)
        guard current.value.revision < Int64.max else {
            throw APIError.unexpectedResponse
        }
        let response = try await sendWithMetadata(
            MiseEndpoints.Content.update(
                captionID: current.value.id,
                body: normalizedRequest,
                etag: current.etag,
                idempotencyKey: idempotencyKey
            )
        )
        try await requireActiveCacheLifetime(ticket)
        guard let responseETag = response.metadata.etag,
              Self.isStrongContentETag(responseETag),
              responseETag != current.etag
        else {
            throw APIError.unexpectedResponse
        }
        try Self.validateContentCaptionDetail(
            response.value,
            expectedID: current.value.id
        )
        guard response.value.versionID == current.value.versionID,
              response.value.revision == current.value.revision + 1,
              response.value.body == normalizedRequest.body,
              response.value.status == .draft,
              response.value.updatedAt >= current.value.updatedAt
        else {
            throw APIError.unexpectedResponse
        }
        try await invalidateContentViews(
            captionID: current.value.id,
            includingRecovery: true,
            lifetimeTicket: ticket
        )
        try await requireActiveCacheLifetime(ticket)
        return ContentCaptionSnapshot(
            value: response.value,
            etag: responseETag,
            storedAt: response.metadata.receivedAt,
            source: .network
        )
    }

    private func validCachedContentFeed(
        lifetimeTicket: UInt64
    ) async throws -> TenantCacheRecord<ContentCaptionFeed>? {
        try await requireActiveCacheLifetime(lifetimeTicket)
        let record = try await cache.read(
            OwnerContentCache.feed,
            as: ContentCaptionFeed.self
        )
        try await requireActiveCacheLifetime(lifetimeTicket)
        guard let record else { return nil }
        do {
            guard Self.isStrongContentETag(record.etag) else {
                throw APIError.unexpectedResponse
            }
            try Self.validateContentCaptionFeed(record.value)
            return record
        } catch {
            try? await cache.remove(OwnerContentCache.feed)
            try await requireActiveCacheLifetime(lifetimeTicket)
            return nil
        }
    }

    private func assembleContentCaptionFeed(
        firstPage: ContentCaptionPage,
        lifetimeTicket: UInt64
    ) async throws -> ContentCaptionFeed {
        var page = firstPage
        let suggestionsEnabled = firstPage.suggestionsEnabled
        var pageCount = 0
        var captions: [ContentCaptionSummary] = []
        var seenIDs = Set<Int64>()
        var seenVersionIDs = Set<String>()
        var seenCursors = Set<String>()
        var previousID: Int64?

        while true {
            try Task.checkCancellation()
            try await requireActiveCacheLifetime(lifetimeTicket)
            try Self.validateContentCaptionPage(
                page,
                suggestionsEnabled: suggestionsEnabled
            )
            for caption in page.items {
                try Self.validateContentCaptionSummary(caption)
                guard seenIDs.insert(caption.id).inserted,
                      seenVersionIDs.insert(caption.versionID).inserted,
                      previousID.map({ caption.id < $0 }) ?? true
                else {
                    throw APIError.unexpectedResponse
                }
                captions.append(caption)
                previousID = caption.id
            }
            pageCount += 1

            guard page.hasMore else {
                let feed = ContentCaptionFeed(
                    captions: captions,
                    hasOlderCaptions: false,
                    suggestionsEnabled: suggestionsEnabled
                )
                try Self.validateContentCaptionFeed(feed)
                return feed
            }
            guard let nextCursor = page.nextCursor,
                  seenCursors.insert(nextCursor).inserted
            else {
                throw OwnerRepositoryError.invalidPagination
            }
            guard pageCount < OwnerContentCache.maximumPageCount else {
                let feed = ContentCaptionFeed(
                    captions: captions,
                    hasOlderCaptions: true,
                    suggestionsEnabled: suggestionsEnabled
                )
                try Self.validateContentCaptionFeed(feed)
                return feed
            }

            do {
                page = try await sendWithMetadata(
                    MiseEndpoints.Content.captions(
                        cursor: nextCursor,
                        limit: OwnerContentCache.pageSize
                    )
                ).value
            } catch APIError.notModified(_) {
                throw APIError.unexpectedResponse
            }
            try await requireActiveCacheLifetime(lifetimeTicket)
        }
    }

    private func validCachedContentDetail(
        captionID: Int64,
        lifetimeTicket: UInt64
    ) async throws -> TenantCacheRecord<ContentCaptionDetail>? {
        let key = OwnerContentCache.detail(captionID)
        try await requireActiveCacheLifetime(lifetimeTicket)
        let record = try await cache.read(key, as: ContentCaptionDetail.self)
        try await requireActiveCacheLifetime(lifetimeTicket)
        guard let record else { return nil }
        do {
            guard Self.isStrongContentETag(record.etag) else {
                throw APIError.unexpectedResponse
            }
            try Self.validateContentCaptionDetail(record.value, expectedID: captionID)
            return record
        } catch {
            try? await cache.remove(key)
            try await requireActiveCacheLifetime(lifetimeTicket)
            return nil
        }
    }

    private func submitCaptionSuggestion(
        _ intent: PendingCaptionSuggestionIntent,
        lifetimeTicket: UInt64
    ) async throws -> CaptionSuggestion {
        let response: APIResponse<CaptionSuggestion>
        do {
            response = try await sendWithMetadata(
                MiseEndpoints.Content.createSuggestion(
                    captionID: intent.captionID,
                    body: intent.request,
                    etag: intent.sourceETag,
                    idempotencyKey: intent.idempotencyKey
                )
            )
        } catch APIError.notModified(_) {
            throw APIError.unexpectedResponse
        }
        try await requireActiveCacheLifetime(lifetimeTicket)
        try Self.validateCaptionSuggestion(
            response.value,
            captionID: intent.captionID,
            suggestionID: nil,
            baseRevision: intent.baseRevision
        )
        let accepted = CaptionSuggestionRecovery(
            captionID: intent.captionID,
            suggestionID: response.value.id,
            terminalInvalidationObserved: false
        )
        let result = try await handleObservedSuggestion(
            response.value,
            recovery: accepted,
            lifetimeTicket: lifetimeTicket
        )
        return result
    }

    private static func suggestionCreateWasDefinitivelyRejected(_ error: Error) -> Bool {
        guard let apiError = error as? APIError else { return false }
        switch apiError {
        case .unauthenticated,
             .forbidden,
             .subscriptionRequired,
             .notFound,
             .gone,
             .conflict,
             .validation,
             .rateLimited,
             .notModified:
            return true
        case let .http(status, _):
            return (400..<500).contains(status)
        case .invalidEndpoint,
             .transport,
             .unexpectedResponse,
             .unexpectedRedirect,
             .unexpectedContentType,
             .decoding,
             .server:
            return false
        }
    }

    private func pollCaptionSuggestion(
        captionID: Int64,
        suggestionID: UUID,
        recovery: CaptionSuggestionRecovery?,
        lifetimeTicket: UInt64
    ) async throws -> CaptionSuggestion {
        let response: APIResponse<CaptionSuggestion>
        do {
            response = try await sendWithMetadata(
                MiseEndpoints.Content.suggestion(
                    captionID: captionID,
                    suggestionID: suggestionID
                )
            )
        } catch APIError.notModified(_) {
            throw APIError.unexpectedResponse
        }
        try await requireActiveCacheLifetime(lifetimeTicket)
        try Self.validateCaptionSuggestion(
            response.value,
            captionID: captionID,
            suggestionID: suggestionID,
            baseRevision: nil
        )
        return try await handleObservedSuggestion(
            response.value,
            recovery: recovery,
            lifetimeTicket: lifetimeTicket
        )
    }

    private func handleObservedSuggestion(
        _ suggestion: CaptionSuggestion,
        recovery: CaptionSuggestionRecovery?,
        lifetimeTicket: UInt64
    ) async throws -> CaptionSuggestion {
        switch suggestion.state {
        case .queued, .running:
            if let recovery {
                try await writeSuggestionRecovery(
                    recovery,
                    lifetimeTicket: lifetimeTicket
                )
            }
        case .ready:
            if recovery?.terminalInvalidationObserved != true {
                try await invalidateContentViews(
                    captionID: suggestion.captionID,
                    includingRecovery: false,
                    lifetimeTicket: lifetimeTicket
                )
            }
            if let recovery {
                try await writeSuggestionRecovery(
                    recovery.observingTerminalWork(),
                    lifetimeTicket: lifetimeTicket
                )
            }
        case .failed, .applied, .expired:
            if recovery?.terminalInvalidationObserved != true {
                try await invalidateContentViews(
                    captionID: suggestion.captionID,
                    includingRecovery: false,
                    lifetimeTicket: lifetimeTicket
                )
            }
            try await requireActiveCacheLifetime(lifetimeTicket)
            try await cache.remove(OwnerContentCache.recovery(suggestion.captionID))
        default:
            throw APIError.unexpectedResponse
        }
        contentSessionState.clear(captionID: suggestion.captionID)
        try await requireActiveCacheLifetime(lifetimeTicket)
        return suggestion
    }

    private func validSuggestionRecovery(
        captionID: Int64,
        lifetimeTicket: UInt64
    ) async throws -> CaptionSuggestionRecovery? {
        let key = OwnerContentCache.recovery(captionID)
        try await requireActiveCacheLifetime(lifetimeTicket)
        try await cache.remove(OwnerContentCache.legacyRecovery(captionID))
        let record = try await cache.read(key, as: CaptionSuggestionRecovery.self)
        try await requireActiveCacheLifetime(lifetimeTicket)
        guard let recovery = record?.value else { return nil }
        do {
            try Self.validateSuggestionRecovery(recovery, captionID: captionID)
            return recovery
        } catch {
            try? await cache.remove(key)
            try await requireActiveCacheLifetime(lifetimeTicket)
            return nil
        }
    }

    private func writeSuggestionRecovery(
        _ recovery: CaptionSuggestionRecovery,
        lifetimeTicket: UInt64
    ) async throws {
        try Self.validateSuggestionRecovery(
            recovery,
            captionID: recovery.captionID
        )
        try await requireActiveCacheLifetime(lifetimeTicket)
        try await cache.write(
            recovery,
            key: OwnerContentCache.recovery(recovery.captionID),
            etag: nil,
            storedAt: Date()
        )
        try await requireActiveCacheLifetime(lifetimeTicket)
    }

    private func invalidateContentViews(
        captionID: Int64,
        includingRecovery: Bool,
        lifetimeTicket: UInt64
    ) async throws {
        try await requireActiveCacheLifetime(lifetimeTicket)
        try await cache.remove(OwnerContentCache.feed)
        try await cache.remove(OwnerContentCache.detail(captionID))
        try await cache.remove(OwnerContentCache.aiActivity)
        if includingRecovery {
            contentSessionState.clear(captionID: captionID)
            try await cache.remove(OwnerContentCache.legacyRecovery(captionID))
            try await cache.remove(OwnerContentCache.recovery(captionID))
        }
        try await requireActiveCacheLifetime(lifetimeTicket)
    }

    private static func validateContentCaptionPage(
        _ page: ContentCaptionPage,
        suggestionsEnabled: Bool
    ) throws {
        let cursorIsValid = page.nextCursor.map {
            !$0.isEmpty && $0.utf8.count <= OwnerContentCache.maximumCursorLength
        } ?? !page.hasMore
        guard page.items.count <= OwnerContentCache.pageSize,
              page.hasMore == (page.nextCursor != nil),
              !page.hasMore || !page.items.isEmpty,
              page.suggestionsEnabled == suggestionsEnabled,
              cursorIsValid
        else {
            throw OwnerRepositoryError.invalidPagination
        }
    }

    private static func validateContentCaptionFeed(_ feed: ContentCaptionFeed) throws {
        guard feed.captions.count <= (
            OwnerContentCache.maximumPageCount * OwnerContentCache.pageSize
        ),
        !feed.hasOlderCaptions || !feed.captions.isEmpty
        else {
            throw APIError.unexpectedResponse
        }
        var seenIDs = Set<Int64>()
        var seenVersionIDs = Set<String>()
        var previousID: Int64?
        for caption in feed.captions {
            try validateContentCaptionSummary(caption)
            guard seenIDs.insert(caption.id).inserted,
                  seenVersionIDs.insert(caption.versionID).inserted,
                  previousID.map({ caption.id < $0 }) ?? true
            else {
                throw APIError.unexpectedResponse
            }
            previousID = caption.id
        }
    }

    private static func validateContentCaptionSummary(
        _ caption: ContentCaptionSummary
    ) throws {
        let validStatuses: Set<ContentCaptionStatus> = [.draft, .approved]
        guard caption.id > 0,
              caption.revision >= 0,
              validVersionID(caption.versionID),
              validServerText(caption.clientDisplayName, maximum: 500, allowEmpty: false),
              validServerText(caption.planTitle, maximum: 1_000, allowEmpty: false),
              validServerText(caption.period, maximum: 32, allowEmpty: false),
              validServerText(caption.label, maximum: 500, allowEmpty: false),
              validServerText(caption.bodyPreview, maximum: 320, allowEmpty: true),
              validStatuses.contains(caption.status),
              finite(caption.updatedAt)
        else {
            throw APIError.unexpectedResponse
        }
    }

    private static func validateContentCaptionDetail(
        _ detail: ContentCaptionDetail,
        expectedID: Int64
    ) throws {
        let validStatuses: Set<ContentCaptionStatus> = [.draft, .approved]
        guard expectedID > 0,
              detail.id == expectedID,
              detail.planID > 0,
              detail.revision >= 0,
              validVersionID(detail.versionID),
              validServerText(detail.clientDisplayName, maximum: 500, allowEmpty: false),
              validServerText(detail.planTitle, maximum: 1_000, allowEmpty: false),
              validServerText(detail.period, maximum: 32, allowEmpty: false),
              validServerText(detail.label, maximum: 500, allowEmpty: false),
              validServerText(detail.body, maximum: 100_000, allowEmpty: true),
              detail.note.map({
                  validServerText($0, maximum: 20_000, allowEmpty: false)
              }) ?? true,
              validStatuses.contains(detail.status),
              detail.status != .approved || !detail.suggestionsEnabled,
              detail.aiDraftedAt.map({ finite($0) }) ?? true,
              finite(detail.createdAt),
              finite(detail.updatedAt)
        else {
            throw APIError.unexpectedResponse
        }
    }

    private static func validateContentCaptionSnapshot(
        _ snapshot: ContentCaptionSnapshot
    ) throws {
        guard isStrongContentETag(snapshot.etag),
              finite(snapshot.storedAt)
        else {
            throw OwnerRepositoryError.missingEntityTag
        }
        try validateContentCaptionDetail(
            snapshot.value,
            expectedID: snapshot.value.id
        )
    }

    private static func validateCaptionSuggestion(
        _ suggestion: CaptionSuggestion,
        captionID: Int64,
        suggestionID: UUID?,
        baseRevision: Int64?
    ) throws {
        let validStates: Set<CaptionSuggestionState> = [
            .queued,
            .running,
            .ready,
            .failed,
            .applied,
            .expired,
        ]
        let validFailures: Set<CaptionSuggestionFailure> = [
            .disabled,
            .providerError,
            .invalidResponse,
            .sessionEnded,
            .unknownOutcome,
            .internal,
        ]
        guard captionID > 0,
              suggestion.captionID == captionID,
              suggestionID.map({ $0 == suggestion.id }) ?? true,
              suggestion.baseRevision >= 0,
              baseRevision.map({ $0 == suggestion.baseRevision }) ?? true,
              validStates.contains(suggestion.state),
              suggestion.review == .humanReview,
              suggestion.failureReason.map({ validFailures.contains($0) }) ?? true,
              finite(suggestion.createdAt),
              finite(suggestion.expiresAt),
              suggestion.expiresAt > suggestion.createdAt,
              suggestion.completedAt.map({
                  finite($0) && $0 >= suggestion.createdAt
              }) ?? true,
              suggestion.state == .ready || !suggestion.stale
        else {
            throw APIError.unexpectedResponse
        }

        switch suggestion.state {
        case .queued, .running:
            guard suggestion.candidateText == nil,
                  suggestion.failureReason == nil,
                  suggestion.completedAt == nil
            else {
                throw APIError.unexpectedResponse
            }
        case .ready:
            guard let candidate = suggestion.candidateText,
                  validNormalizedPlainText(
                      candidate,
                      maximum: 10_000,
                      allowEmpty: false
                  ),
                  suggestion.failureReason == nil,
                  suggestion.completedAt != nil
            else {
                throw APIError.unexpectedResponse
            }
        case .failed:
            guard suggestion.candidateText == nil,
                  suggestion.failureReason != nil,
                  suggestion.completedAt != nil
            else {
                throw APIError.unexpectedResponse
            }
        case .applied, .expired:
            guard suggestion.candidateText == nil,
                  suggestion.failureReason == nil,
                  suggestion.completedAt != nil
            else {
                throw APIError.unexpectedResponse
            }
        default:
            throw APIError.unexpectedResponse
        }
    }

    private static func validateSuggestionRecovery(
        _ recovery: CaptionSuggestionRecovery,
        captionID: Int64
    ) throws {
        guard captionID > 0,
              recovery.captionID == captionID
        else {
            throw APIError.unexpectedResponse
        }
    }

    private static func normalizedSuggestionRequest(
        _ request: CaptionSuggestionRequest
    ) throws -> CaptionSuggestionRequest {
        guard let instruction = request.instruction else {
            return CaptionSuggestionRequest(instruction: nil)
        }
        let normalized = normalize(instruction)
        guard validNormalizedPlainText(
            normalized,
            maximum: 1_000,
            allowEmpty: true
        ) else {
            throw OwnerContentRepositoryError.invalidInstruction
        }
        return CaptionSuggestionRequest(
            instruction: normalized.isEmpty ? nil : normalized
        )
    }

    private static func normalizedBodyUpdate(
        _ request: CaptionBodyUpdate
    ) throws -> CaptionBodyUpdate {
        let normalized = normalize(request.body)
        guard validNormalizedPlainText(
            normalized,
            maximum: 100_000,
            allowEmpty: false
        ) else {
            throw OwnerContentRepositoryError.invalidBody
        }
        return CaptionBodyUpdate(
            body: normalized,
            suggestionID: request.suggestionID
        )
    }

    private static func normalize(_ value: String) -> String {
        value
            .precomposedStringWithCanonicalMapping
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func validNormalizedPlainText(
        _ value: String,
        maximum: Int,
        allowEmpty: Bool
    ) -> Bool {
        value == normalize(value)
            && (allowEmpty || !value.isEmpty)
            && value.unicodeScalars.count <= maximum
            && !containsUnsupportedControls(value)
    }

    private static func validServerText(
        _ value: String,
        maximum: Int,
        allowEmpty: Bool
    ) -> Bool {
        value == value.precomposedStringWithCanonicalMapping
            && (allowEmpty || !value.isEmpty)
            && value.unicodeScalars.count <= maximum
            && !containsUnsupportedControls(value)
    }

    private static func containsUnsupportedControls(_ value: String) -> Bool {
        let bidiControls: Set<UInt32> = [
            0x061C,
            0x200E,
            0x200F,
            0x202A,
            0x202B,
            0x202C,
            0x202D,
            0x202E,
            0x2066,
            0x2067,
            0x2068,
            0x2069,
        ]
        return value.unicodeScalars.contains { scalar in
            let codePoint = scalar.value
            if bidiControls.contains(codePoint) {
                return true
            }
            let isControl = codePoint <= 0x1F || (0x7F ... 0x9F).contains(codePoint)
            return isControl && codePoint != 0x09 && codePoint != 0x0A
        }
    }

    private static func validVersionID(_ value: String) -> Bool {
        value.utf8.count == 32
            && value.utf8.allSatisfy { byte in
                (48 ... 57).contains(byte) || (97 ... 102).contains(byte)
            }
    }

    private static func finite(_ date: Date) -> Bool {
        date.timeIntervalSinceReferenceDate.isFinite
    }

    private static func isStrongContentETag(_ value: String?) -> Bool {
        guard let value,
              value.hasPrefix(#"""#),
              value.hasSuffix(#"""#),
              !value.hasPrefix("W/"),
              value.count > 2
        else {
            return false
        }
        let opaqueValue = value.dropFirst().dropLast()
        return opaqueValue.unicodeScalars.allSatisfy { scalar in
            scalar.value == 0x21 || (0x23 ... 0x7E).contains(scalar.value)
                || (0x80 ... 0xFF).contains(scalar.value)
        }
    }
}
