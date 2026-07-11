import Foundation

extension OwnerRepository {
    private func cullCacheKey(galleryID: Int64) -> String {
        "gallery.\(galleryID).cull.v1"
    }

    func cachedCullPage(galleryID: Int64) async throws -> ResourceSnapshot<CullPage>? {
        let ticket = try await cacheLifetimeTicket()
        let record = try await cache.read(
            cullCacheKey(galleryID: galleryID),
            as: CullPage.self
        )
        try await requireActiveCacheLifetime(ticket)
        guard let record else { return nil }
        try Self.validateCullPage(record.value, galleryID: galleryID)
        return ResourceSnapshot(
            value: record.value,
            storedAt: record.storedAt,
            source: .cache
        )
    }

    func refreshCullPage(galleryID: Int64) async throws -> ResourceSnapshot<CullPage> {
        let ticket = try await cacheLifetimeTicket()
        let key = cullCacheKey(galleryID: galleryID)
        let cached = try await cache.read(key, as: CullPage.self)
        try await requireActiveCacheLifetime(ticket)
        do {
            let response = try await sendWithMetadata(
                MiseEndpoints.Galleries.cull(
                    galleryID: galleryID,
                    limit: 50,
                    etag: cached?.etag
                )
            )
            try await requireActiveCacheLifetime(ticket)
            try Self.validateCullPage(response.value, galleryID: galleryID)
            let record = try await cache.write(
                response.value,
                key: key,
                etag: response.metadata.etag,
                storedAt: response.metadata.receivedAt
            )
            try await requireActiveCacheLifetime(ticket)
            return ResourceSnapshot(
                value: record.value,
                storedAt: record.storedAt,
                source: .network
            )
        } catch let APIError.notModified(responseETag) {
            try await requireActiveCacheLifetime(ticket)
            guard cached != nil,
                  let touched = try await cache.touch(
                      key,
                      as: CullPage.self,
                      etag: responseETag
                  )
            else {
                throw OwnerRepositoryError.missingConditionalValue
            }
            try Self.validateCullPage(touched.value, galleryID: galleryID)
            try await requireActiveCacheLifetime(ticket)
            return ResourceSnapshot(
                value: touched.value,
                storedAt: touched.storedAt,
                source: .revalidated
            )
        }
    }

    func nextCullPage(
        galleryID: Int64,
        cursor: String
    ) async throws -> CullPage {
        guard !cursor.isEmpty else { throw OwnerRepositoryError.invalidPagination }
        let ticket = try await cacheLifetimeTicket()
        let page = try await sendWithMetadata(
            MiseEndpoints.Galleries.cull(
                galleryID: galleryID,
                cursor: cursor,
                limit: 50
            )
        ).value
        try await requireActiveCacheLifetime(ticket)
        try Self.validateCullPage(page, galleryID: galleryID)
        return page
    }

    func decideCull(
        galleryID: Int64,
        item: CullItem,
        action: CullAction,
        idempotencyKey: UUID
    ) async throws -> CullItem {
        let ticket = try await cacheLifetimeTicket()
        guard item.galleryID == galleryID else {
            throw APIError.unexpectedResponse
        }
        try Self.validateCullItem(item, galleryID: galleryID)
        let response = try await sendWithMetadata(
            MiseEndpoints.Galleries.decideCull(
                galleryID: galleryID,
                assetID: item.assetID,
                action: action,
                etag: item.etag,
                idempotencyKey: idempotencyKey
            )
        )
        try await requireActiveCacheLifetime(ticket)
        let updated = response.value
        try Self.validateCullItem(updated, galleryID: galleryID)
        let expectedState: CullState?
        switch action {
        case .keep: expectedState = .keep
        case .cut: expectedState = .cut
        case .restore: expectedState = nil
        }
        guard updated.assetID == item.assetID,
              updated.state == expectedState,
              updated.mediaRevision == item.mediaRevision,
              response.metadata.etag == updated.etag,
              !updated.etag.isEmpty
        else {
            throw APIError.unexpectedResponse
        }

        // The server mutation is authoritative. Cull pages and delivery-gated
        // gallery summaries must revalidate before another offline presentation.
        try? await cache.remove(cullCacheKey(galleryID: galleryID))
        try? await cache.remove("gallery.\(galleryID).v1")
        try? await cache.remove("galleries.v1")
        return updated
    }

    private static func validateCullPage(
        _ page: CullPage,
        galleryID: Int64
    ) throws {
        guard page.counts.total >= 0,
              page.counts.keep >= 0,
              page.counts.cut >= 0,
              page.counts.undecided >= 0,
              page.counts.scored >= 0
        else {
            throw APIError.unexpectedResponse
        }
        let (reviewed, reviewedOverflow) = page.counts.keep.addingReportingOverflow(
            page.counts.cut
        )
        let (classified, classifiedOverflow) = reviewed.addingReportingOverflow(
            page.counts.undecided
        )
        guard page.items.count <= 100,
              page.items.count <= page.counts.total,
              Set(page.items.map(\.assetID)).count == page.items.count,
              !reviewedOverflow,
              !classifiedOverflow,
              classified == page.counts.total,
              page.counts.scored <= page.counts.total,
              page.hasMore == (page.nextCursor != nil),
              page.nextCursor.map({ !$0.isEmpty && $0.count <= 1_024 }) ?? !page.hasMore
        else {
            throw APIError.unexpectedResponse
        }
        for item in page.items {
            try validateCullItem(item, galleryID: galleryID)
        }
    }

    private static func validateCullItem(
        _ item: CullItem,
        galleryID: Int64
    ) throws {
        guard galleryID > 0,
              item.galleryID == galleryID,
              item.assetID > 0,
              item.mediaRevision >= 0,
              !item.filename.isEmpty,
              item.state.map({ $0 == .keep || $0 == .cut }) ?? true,
              item.keeperScore.map({ $0.isFinite && (0 ... 1).contains($0) }) ?? true,
              item.heroPotential.map({ $0.isFinite && (0 ... 1).contains($0) }) ?? true,
              isStrongETag(item.etag),
              mediaURL(
                  item.thumbnailURL,
                  matches: item,
                  variant: "thumbnail"
              ),
              mediaURL(
                  item.previewURL,
                  matches: item,
                  variant: "preview"
              )
        else {
            throw APIError.unexpectedResponse
        }
    }

    private static func isStrongETag(_ value: String) -> Bool {
        let prefix = #""cull-asset-"#
        guard value.hasPrefix(prefix),
              value.hasSuffix(#"""#),
              value.count == prefix.count + 32 + 1
        else {
            return false
        }
        let digest = value.dropFirst(prefix.count).dropLast()
        let lowercaseHex = Set("0123456789abcdef")
        return digest.allSatisfy { lowercaseHex.contains($0) }
    }

    private static func mediaURL(
        _ url: URL?,
        matches item: CullItem,
        variant: String
    ) -> Bool {
        guard let url else { return true }
        let expectedPath = "/api/v1/galleries/\(item.galleryID)/cull/assets/\(item.assetID)/\(variant)"
        return url.user == nil
            && url.password == nil
            && url.query == nil
            && url.fragment == nil
            && url.path == expectedPath
    }
}
