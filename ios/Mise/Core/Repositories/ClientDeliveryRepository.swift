import Foundation

enum ClientDeliveryRepositoryError: LocalizedError, Sendable {
    case missingConditionalValue
    case favoriteIdentityMismatch
    case commentIdentityMismatch

    var errorDescription: String? {
        switch self {
        case .missingConditionalValue:
            "The cached client item is no longer available."
        case .favoriteIdentityMismatch:
            "The studio returned a favorite for the wrong media item."
        case .commentIdentityMismatch:
            "The studio returned a comment for the wrong media item."
        }
    }
}

struct FavoriteUpdate: Sendable {
    let state: FavoriteState
    let gallery: GalleryDetail?
}

/// Capability-only client data access. It deliberately contains no owner endpoints;
/// the server session remains the final authority for every individual capability.
actor ClientDeliveryRepository {
    private enum Key {
        static let gallery = "client.gallery.v1"
        static let portal = "client.portal.v1"
        static let workspace = "client.workspace.v1"
        static let document = "client.document.v1"

        static func comments(assetID: Int64) -> String {
            "client.gallery.comments.\(assetID).v1"
        }
    }

    private let client: any APIClientProtocol
    private let cache: TenantJSONCache
    private let onSessionEnded: @Sendable () async -> Void
    private let operationLock = ClientDeliveryOperationLock()
    private let lifetime: ClientDeliveryLifetime

    init(
        client: any APIClientProtocol,
        cache: TenantJSONCache,
        lifetime: ClientDeliveryLifetime = ClientDeliveryLifetime(),
        onSessionEnded: @escaping @Sendable () async -> Void = {}
    ) {
        self.client = client
        self.cache = cache
        self.lifetime = lifetime
        self.onSessionEnded = onSessionEnded
    }

    func cachedGallery() async throws -> ResourceSnapshot<GalleryDetail>? {
        try await cached(Key.gallery, as: GalleryDetail.self)
    }

    func refreshGallery() async throws -> ResourceSnapshot<GalleryDetail> {
        try await serialized { ticket in
            try await refreshConditional(
                key: Key.gallery,
                endpoint: MiseEndpoints.ClientDelivery.gallery(),
                lifetimeTicket: ticket
            )
        }
    }

    func cachedPortal() async throws -> ResourceSnapshot<ClientPortalSummary>? {
        try await cached(Key.portal, as: ClientPortalSummary.self)
    }

    func refreshPortal() async throws -> ResourceSnapshot<ClientPortalSummary> {
        try await serialized { ticket in
            try await refreshConditional(
                key: Key.portal,
                endpoint: MiseEndpoints.ClientDelivery.portal(),
                lifetimeTicket: ticket
            )
        }
    }

    func cachedWorkspace() async throws -> ResourceSnapshot<ClientWorkspaceSummary>? {
        try await cached(Key.workspace, as: ClientWorkspaceSummary.self)
    }

    func refreshWorkspace() async throws -> ResourceSnapshot<ClientWorkspaceSummary> {
        try await serialized { ticket in
            try await refreshConditional(
                key: Key.workspace,
                endpoint: MiseEndpoints.ClientDelivery.workspace(),
                lifetimeTicket: ticket
            )
        }
    }

    func cachedDocument() async throws -> ResourceSnapshot<ClientDocumentSummary>? {
        try await cached(Key.document, as: ClientDocumentSummary.self)
    }

    func refreshDocument() async throws -> ResourceSnapshot<ClientDocumentSummary> {
        try await serialized { ticket in
            do {
                return try await refreshConditional(
                    key: Key.document,
                    endpoint: MiseEndpoints.ClientDelivery.document(),
                    lifetimeTicket: ticket
                )
            } catch {
                if ClientDeliveryFailure.isDocumentIntegrityFailure(error) {
                    try? await cache.remove(Key.document)
                }
                throw error
            }
        }
    }

    func cachedComments(assetID: Int64) async throws -> ResourceSnapshot<[GalleryComment]>? {
        try await cached(Key.comments(assetID: assetID), as: [GalleryComment].self)
    }

    func refreshComments(assetID: Int64) async throws -> ResourceSnapshot<[GalleryComment]> {
        try await serialized { ticket in
            try await refreshConditional(
                key: Key.comments(assetID: assetID),
                endpoint: MiseEndpoints.ClientDelivery.comments(assetID: assetID),
                lifetimeTicket: ticket
            )
        }
    }

    func addComment(
        assetID: Int64,
        body: String,
        timecodeSeconds: Double?,
        parentID: Int64?
    ) async throws -> GalleryComment {
        try await serialized { ticket in
            try await addCommentUnlocked(
                assetID: assetID,
                body: body,
                timecodeSeconds: timecodeSeconds,
                parentID: parentID,
                lifetimeTicket: ticket
            )
        }
    }

    private func addCommentUnlocked(
        assetID: Int64,
        body: String,
        timecodeSeconds: Double?,
        parentID: Int64?,
        lifetimeTicket: UInt64
    ) async throws -> GalleryComment {
        let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
        let request = GalleryCommentCreateRequest(
            body: trimmed,
            timecodeSeconds: timecodeSeconds.map { max(0, $0) },
            parentID: parentID
        )
        // A sign-out/revocation may have happened while this operation waited
        // for the repository serializer. Do not begin a server-side mutation
        // after that terminal boundary.
        try await requireActive(lifetimeTicket)
        let comment = try await send(
            MiseEndpoints.ClientDelivery.addComment(assetID: assetID, body: request)
        )
        guard comment.assetID == assetID else {
            throw ClientDeliveryRepositoryError.commentIdentityMismatch
        }
        try await requireActive(lifetimeTicket)

        let key = Key.comments(assetID: assetID)
        var comments = (try? await cache.read(key, as: [GalleryComment].self))?.value ?? []
        comments.removeAll { $0.id == comment.id }
        comments.append(comment)
        comments.sort {
            ($0.timecodeSeconds, $0.createdAt, $0.id)
                < ($1.timecodeSeconds, $1.createdAt, $1.id)
        }
        // The server mutation is authoritative. A protected-cache write failure
        // must not invite a duplicate comment retry.
        if (try? await cache.write(comments, key: key, etag: nil)) != nil {
            do {
                try await requireActive(lifetimeTicket)
            } catch {
                try? await cache.remove(key)
                throw error
            }
        }
        return comment
    }

    func setFavorite(assetID: Int64, selected: Bool) async throws -> FavoriteUpdate {
        try await serialized { ticket in
            try await setFavoriteUnlocked(
                assetID: assetID,
                selected: selected,
                lifetimeTicket: ticket
            )
        }
    }

    private func setFavoriteUnlocked(
        assetID: Int64,
        selected: Bool,
        lifetimeTicket: UInt64
    ) async throws -> FavoriteUpdate {
        // Keep the terminal lifetime check immediately adjacent to the only
        // server-side mutation; cache cleanup alone is not enough here.
        try await requireActive(lifetimeTicket)
        let state = try await send(
            MiseEndpoints.ClientDelivery.favorite(assetID: assetID, selected: selected)
        )
        guard state.assetID == assetID else {
            throw ClientDeliveryRepositoryError.favoriteIdentityMismatch
        }
        try await requireActive(lifetimeTicket)

        guard let current = try? await cache.read(Key.gallery, as: GalleryDetail.self) else {
            return FavoriteUpdate(state: state, gallery: nil)
        }
        let updated = Self.applyingFavorite(state, to: current.value)
        // Favorite success must remain visible even when disk protection or
        // capacity temporarily prevents persistence.
        if (try? await cache.write(updated, key: Key.gallery, etag: nil)) != nil {
            do {
                try await requireActive(lifetimeTicket)
            } catch {
                try? await cache.remove(Key.gallery)
                throw error
            }
        }
        return FavoriteUpdate(state: state, gallery: updated)
    }

    func purgeCache() async {
        await operationLock.acquire()
        try? await cache.removeAll()
        await operationLock.release()
    }

    private func cached<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type
    ) async throws -> ResourceSnapshot<Value>? {
        guard let ticket = await lifetime.ticket() else {
            throw APIError.unauthenticated(nil)
        }
        guard let record = try await cache.read(key, as: type) else { return nil }
        try await requireActive(ticket)
        return ResourceSnapshot(value: record.value, storedAt: record.storedAt, source: .cache)
    }

    private func persist<Value: Codable & Sendable>(
        _ value: Value,
        key: String,
        etag: String? = nil,
        storedAt: Date = Date(),
        lifetimeTicket: UInt64
    ) async throws -> ResourceSnapshot<Value> {
        try await requireActive(lifetimeTicket)
        let record = try await cache.write(value, key: key, etag: etag, storedAt: storedAt)
        do {
            try await requireActive(lifetimeTicket)
        } catch {
            try? await cache.remove(key)
            throw error
        }
        return ResourceSnapshot(value: record.value, storedAt: record.storedAt, source: .network)
    }

    private func refreshConditional<Value: Codable & Sendable>(
        key: String,
        endpoint: APIEndpoint<Value>,
        lifetimeTicket: UInt64
    ) async throws -> ResourceSnapshot<Value> {
        let current = try await cache.read(key, as: Value.self)
        do {
            let response = try await sendWithMetadata(
                endpoint.revalidating(with: current?.etag)
            )
            return try await persist(
                response.value,
                key: key,
                etag: response.metadata.etag,
                storedAt: response.metadata.receivedAt,
                lifetimeTicket: lifetimeTicket
            )
        } catch let APIError.notModified(responseETag) {
            try await requireActive(lifetimeTicket)
            guard current != nil,
                  let touched = try await cache.touch(
                      key,
                      as: Value.self,
                      etag: responseETag
                  )
            else {
                throw ClientDeliveryRepositoryError.missingConditionalValue
            }
            do {
                try await requireActive(lifetimeTicket)
            } catch {
                try? await cache.remove(key)
                throw error
            }
            return ResourceSnapshot(
                value: touched.value,
                storedAt: touched.storedAt,
                source: .revalidated
            )
        }
    }

    private func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        do {
            return try await client.send(endpoint)
        } catch {
            await purgeIfSessionEnded(error)
            throw error
        }
    }

    private func serialized<Value: Sendable>(
        _ operation: (UInt64) async throws -> Value
    ) async throws -> Value {
        await operationLock.acquire()
        do {
            try Task.checkCancellation()
            guard let ticket = await lifetime.ticket() else {
                throw APIError.unauthenticated(nil)
            }
            let value = try await operation(ticket)
            try await requireActive(ticket)
            await operationLock.release()
            return value
        } catch {
            await operationLock.release()
            throw error
        }
    }

    private func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        do {
            return try await client.sendWithMetadata(endpoint)
        } catch {
            await purgeIfSessionEnded(error)
            throw error
        }
    }

    private func purgeIfSessionEnded(_ error: Error) async {
        if let apiError = error as? APIError,
           case .unauthenticated = apiError
        {
            await lifetime.end()
            try? await cache.removeAll()
            await onSessionEnded()
            return
        }
        guard let sessionError = error as? SessionError else { return }
        switch sessionError {
        case .expired, .identityChanged, .workspaceMismatch:
            await lifetime.end()
            try? await cache.removeAll()
            await onSessionEnded()
        }
    }

    private func requireActive(_ ticket: UInt64) async throws {
        guard await lifetime.isActive(ticket) else {
            throw APIError.unauthenticated(nil)
        }
    }

    private static func applyingFavorite(
        _ state: FavoriteState,
        to detail: GalleryDetail
    ) -> GalleryDetail {
        guard let index = detail.assets.firstIndex(where: { $0.id == state.assetID }) else {
            return detail
        }

        var assets = detail.assets
        let previous = assets[index]
        let delta = state.selected == previous.isFavorite ? 0 : (state.selected ? 1 : -1)
        assets[index] = GalleryAsset(
            id: previous.id,
            galleryID: previous.galleryID,
            sectionID: previous.sectionID,
            kind: previous.kind,
            status: previous.status,
            filename: previous.filename,
            width: previous.width,
            height: previous.height,
            durationSeconds: previous.durationSeconds,
            byteCount: previous.byteCount,
            position: previous.position,
            createdAt: previous.createdAt,
            isFavorite: state.selected,
            favoriteCount: max(0, previous.favoriteCount + delta),
            links: previous.links,
            altText: previous.altText,
            keywords: previous.keywords,
            keeperScore: previous.keeperScore,
            heroPotential: previous.heroPotential,
            cullState: previous.cullState
        )

        let sections = detail.sections.map { section in
            guard section.id == previous.sectionID else { return section }
            return GallerySection(
                id: section.id,
                galleryID: section.galleryID,
                name: section.name,
                caption: section.caption,
                position: section.position,
                proofTarget: state.sectionProofTarget ?? section.proofTarget,
                selectedCount: state.sectionSelectedCount ?? max(0, section.selectedCount + delta)
            )
        }
        let summary = GallerySummary(
            id: detail.summary.id,
            title: detail.summary.title,
            slug: detail.summary.slug,
            clientID: detail.summary.clientID,
            projectID: detail.summary.projectID,
            clientName: detail.summary.clientName,
            type: detail.summary.type,
            published: detail.summary.published,
            requiresPIN: detail.summary.requiresPIN,
            contentRevision: detail.summary.contentRevision,
            coverAssetID: detail.summary.coverAssetID,
            expiresOn: detail.summary.expiresOn,
            assetCount: detail.summary.assetCount,
            favoriteCount: max(0, detail.summary.favoriteCount + delta),
            downloadCount: detail.summary.downloadCount,
            deliveryState: detail.summary.deliveryState,
            createdAt: detail.summary.createdAt
        )
        return GalleryDetail(
            summary: summary,
            sections: sections,
            assets: assets,
            heroAssetIDs: detail.heroAssetIDs,
            vision: detail.vision
        )
    }
}

enum ClientDeliveryFailure {
    static func isDocumentIntegrityFailure(_ error: Error) -> Bool {
        guard let apiError = error as? APIError,
              case let .conflict(problem) = apiError
        else {
            return false
        }
        return problem?.code == "document.integrity_failed"
    }
}

private actor ClientDeliveryOperationLock {
    private var held = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func acquire() async {
        if !held {
            held = true
            return
        }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func release() {
        guard !waiters.isEmpty else {
            held = false
            return
        }
        waiters.removeFirst().resume()
    }
}
