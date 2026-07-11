import Foundation

private enum OwnerAIActivityCache {
    static let key = "ai-activity.v1"
    static let maximumPageCount = 5
    static let pageSize = 100
    static let maximumCursorLength = 1_024
}

extension OwnerRepository {
    func cachedAIActivity() async throws -> ResourceSnapshot<AIActivityFeed>? {
        let ticket = try await cacheLifetimeTicket()
        guard let record = try await validCachedAIActivity(lifetimeTicket: ticket) else {
            return nil
        }
        return ResourceSnapshot(
            value: record.value,
            storedAt: record.storedAt,
            source: .cache
        )
    }

    func refreshAIActivity() async throws -> ResourceSnapshot<AIActivityFeed> {
        let ticket = try await cacheLifetimeTicket()
        let cached = try await validCachedAIActivity(lifetimeTicket: ticket)
        let firstResponse: APIResponse<APIPage<AIRun>>
        do {
            firstResponse = try await sendWithMetadata(
                MiseEndpoints.AI.runs(
                    limit: OwnerAIActivityCache.pageSize,
                    etag: cached?.etag
                )
            )
        } catch let APIError.notModified(responseETag) {
            try await requireActiveCacheLifetime(ticket)
            guard let cached else {
                throw OwnerRepositoryError.missingConditionalValue
            }
            guard let cachedETag = cached.etag,
                  let responseETag,
                  responseETag == cachedETag,
                  Self.isStrongETag(responseETag)
            else {
                throw APIError.unexpectedResponse
            }
            guard let touched = try await cache.touch(
                OwnerAIActivityCache.key,
                as: AIActivityFeed.self,
                etag: responseETag
            ) else {
                throw OwnerRepositoryError.missingConditionalValue
            }
            try Self.validateAIActivityFeed(touched.value)
            try await requireActiveCacheLifetime(ticket)
            return ResourceSnapshot(
                value: touched.value,
                storedAt: touched.storedAt,
                source: .revalidated
            )
        }

        try await requireActiveCacheLifetime(ticket)
        guard let firstETag = firstResponse.metadata.etag,
              Self.isStrongETag(firstETag)
        else {
            throw APIError.unexpectedResponse
        }
        let feed = try await assembleAIActivityFeed(
            firstPage: firstResponse.value,
            lifetimeTicket: ticket
        )
        try await requireActiveCacheLifetime(ticket)
        let record = try await cache.write(
            feed,
            key: OwnerAIActivityCache.key,
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

    private func validCachedAIActivity(
        lifetimeTicket: UInt64
    ) async throws -> TenantCacheRecord<AIActivityFeed>? {
        try await requireActiveCacheLifetime(lifetimeTicket)
        let record = try await cache.read(
            OwnerAIActivityCache.key,
            as: AIActivityFeed.self
        )
        try await requireActiveCacheLifetime(lifetimeTicket)
        guard let record else { return nil }
        do {
            guard Self.isStrongETag(record.etag) else {
                throw APIError.unexpectedResponse
            }
            try Self.validateAIActivityFeed(record.value)
            return record
        } catch {
            // A semantically obsolete cache is disposable, just like an envelope
            // that TenantJSONCache can no longer decode.
            try? await cache.remove(OwnerAIActivityCache.key)
            return nil
        }
    }

    private func assembleAIActivityFeed(
        firstPage: APIPage<AIRun>,
        lifetimeTicket: UInt64
    ) async throws -> AIActivityFeed {
        var page = firstPage
        var pageCount = 0
        var runs: [AIRun] = []
        var seenIDs = Set<Int64>()
        var seenCursors = Set<String>()
        var previousID: Int64?

        while true {
            try Task.checkCancellation()
            try await requireActiveCacheLifetime(lifetimeTicket)
            try Self.validateAIActivityPage(page)

            for run in page.items {
                try Self.validateAIRun(run)
                guard seenIDs.insert(run.id).inserted,
                      previousID.map({ run.id < $0 }) ?? true
                else {
                    throw APIError.unexpectedResponse
                }
                runs.append(run)
                previousID = run.id
            }
            pageCount += 1

            guard page.hasMore else {
                let feed = AIActivityFeed(runs: runs, hasOlderRuns: false)
                try Self.validateAIActivityFeed(feed)
                return feed
            }

            guard let nextCursor = page.nextCursor,
                  seenCursors.insert(nextCursor).inserted
            else {
                throw OwnerRepositoryError.invalidPagination
            }

            guard pageCount < OwnerAIActivityCache.maximumPageCount else {
                let feed = AIActivityFeed(runs: runs, hasOlderRuns: true)
                try Self.validateAIActivityFeed(feed)
                return feed
            }

            do {
                page = try await sendWithMetadata(
                    MiseEndpoints.AI.runs(
                        cursor: nextCursor,
                        limit: OwnerAIActivityCache.pageSize
                    )
                ).value
            } catch APIError.notModified(_) {
                throw APIError.unexpectedResponse
            }
            try await requireActiveCacheLifetime(lifetimeTicket)
        }
    }

    private static func validateAIActivityPage(_ page: APIPage<AIRun>) throws {
        let cursorIsValid = page.nextCursor.map {
            !$0.isEmpty && $0.utf8.count <= OwnerAIActivityCache.maximumCursorLength
        } ?? !page.hasMore
        guard page.items.count <= OwnerAIActivityCache.pageSize,
              page.hasMore == (page.nextCursor != nil),
              !page.hasMore || !page.items.isEmpty,
              cursorIsValid
        else {
            throw OwnerRepositoryError.invalidPagination
        }
    }

    private static func validateAIActivityFeed(_ feed: AIActivityFeed) throws {
        guard feed.runs.count <= (
            OwnerAIActivityCache.maximumPageCount * OwnerAIActivityCache.pageSize
        ),
        !feed.hasOlderRuns || !feed.runs.isEmpty
        else {
            throw APIError.unexpectedResponse
        }

        var seenIDs = Set<Int64>()
        var previousID: Int64?
        for run in feed.runs {
            try validateAIRun(run)
            guard seenIDs.insert(run.id).inserted,
                  previousID.map({ run.id < $0 }) ?? true
            else {
                throw APIError.unexpectedResponse
            }
            previousID = run.id
        }
    }

    private static func validateAIRun(_ run: AIRun) throws {
        let validCapabilities: Set<AICapability> = [.vision, .content, .products, .other]
        let validProviders: Set<AIProvider> = [
            .argus,
            .qwen,
            .odysseus,
            .dionysus,
            .aphrodite,
            .other,
        ]
        let validStatuses: Set<AIRunStatus> = [
            .ok,
            .disabled,
            .providerError,
            .invalidResponse,
            .unknown,
        ]
        let validReviews: Set<AIReviewRequirement> = [
            .none,
            .humanReview,
            .explicitCommit,
            .unknown,
        ]

        guard run.id > 0,
              validCapabilities.contains(run.capability),
              validProviders.contains(run.provider),
              validStatuses.contains(run.status),
              validReviews.contains(run.review),
              run.latencyMs.map({ $0 >= 0 }) ?? true,
              run.costMicroUSD.map({ $0 >= 0 }) ?? true,
              run.tokens.map({ $0 >= 0 }) ?? true,
              run.createdAt.timeIntervalSinceReferenceDate.isFinite
        else {
            throw APIError.unexpectedResponse
        }

        if let subject = run.subject {
            try validateAIActivitySubject(subject)
        }
    }

    private static func validateAIActivitySubject(_ subject: AIActivitySubject) throws {
        let validKinds: Set<AIActivitySubjectKind> = [.gallery, .caption, .other]
        guard validKinds.contains(subject.kind) else {
            throw APIError.unexpectedResponse
        }
    }

    private static func isStrongETag(_ value: String?) -> Bool {
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
