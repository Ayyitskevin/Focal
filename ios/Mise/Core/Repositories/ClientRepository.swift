import Foundation

/// Shared-client data access for the four guest principals. Mirrors
/// `OwnerRepository`'s cache-first shape; every persisted value is scoped to
/// the workspace's opaque cache namespace, and the principal's scopes decide
/// which capabilities (like favoriting) the UI may offer.
actor ClientRepository {
    private enum Key {
        static let home = "client.home.v1"
        static let galleries = "client.galleries.v1"
        static let bookings = "client.bookings.v1"

        static func gallery(_ id: Int64) -> String {
            "client.gallery.\(id).v1"
        }

        static func documents(_ projectID: Int64) -> String {
            "client.documents.\(projectID).v1"
        }
    }

    private let client: any APIClientProtocol
    private let cache: TenantJSONCache
    private let principal: Principal

    init(client: any APIClientProtocol, cache: TenantJSONCache, principal: Principal) {
        self.client = client
        self.cache = cache
        self.principal = principal
    }

    /// True when this session may toggle favorites in the given gallery.
    /// Only a gallery exchange mints a visitor identity; workspace and portal
    /// sessions browse read-only.
    nonisolated func canFavorite(galleryID: Int64) -> Bool {
        principal.allows("gallery:\(galleryID):favorite")
    }

    func cachedHome() async throws -> ResourceSnapshot<ClientHomeSummary>? {
        try await cached(Key.home, as: ClientHomeSummary.self)
    }

    func refreshHome() async throws -> ResourceSnapshot<ClientHomeSummary> {
        let value = try await send(MiseEndpoints.Client.home)
        return try await persist(value, key: Key.home)
    }

    func cachedGalleries() async throws -> ResourceSnapshot<[GallerySummary]>? {
        try await cached(Key.galleries, as: [GallerySummary].self)
    }

    func refreshGalleries() async throws -> ResourceSnapshot<[GallerySummary]> {
        let values = try await fetchAll { cursor in
            MiseEndpoints.Client.galleries(cursor: cursor, limit: 100)
        }
        return try await persist(values, key: Key.galleries)
    }

    func cachedGallery(id: Int64) async throws -> ResourceSnapshot<GalleryDetail>? {
        try await cached(Key.gallery(id), as: GalleryDetail.self)
    }

    func refreshGallery(id: Int64) async throws -> ResourceSnapshot<GalleryDetail> {
        try await refreshConditional(
            key: Key.gallery(id),
            endpoint: MiseEndpoints.Client.galleryDetail(id: id)
        )
    }

    func cachedBookings() async throws -> ResourceSnapshot<[Booking]>? {
        try await cached(Key.bookings, as: [Booking].self)
    }

    func refreshBookings() async throws -> ResourceSnapshot<[Booking]> {
        let values = try await fetchAll { cursor in
            MiseEndpoints.Client.bookings(cursor: cursor, limit: 100)
        }
        return try await persist(values, key: Key.bookings)
    }

    func cachedDocuments(projectID: Int64) async throws -> ResourceSnapshot<ClientDocuments>? {
        try await cached(Key.documents(projectID), as: ClientDocuments.self)
    }

    func refreshDocuments(projectID: Int64) async throws -> ResourceSnapshot<ClientDocuments> {
        async let proposals = send(MiseEndpoints.Projects.proposals(projectID: projectID))
        async let contracts = send(MiseEndpoints.Projects.contracts(projectID: projectID))
        async let invoices = send(MiseEndpoints.Projects.invoices(projectID: projectID))
        let documents = ClientDocuments(
            proposals: try await proposals.items,
            contracts: try await contracts.items,
            invoices: try await invoices.items
        )
        return try await persist(documents, key: Key.documents(projectID))
    }

    /// Server-authoritative favorite toggle. The caller owns optimistic UI;
    /// the returned state (including the section proofing count) is final.
    func setFavorite(
        galleryID: Int64,
        assetID: Int64,
        selected: Bool
    ) async throws -> FavoriteState {
        let state = try await send(
            MiseEndpoints.Galleries.favorite(
                galleryID: galleryID,
                assetID: assetID,
                selected: selected,
                idempotencyKey: UUID()
            )
        )
        // The cached manifest now disagrees with the server; drop it so the
        // next load revalidates rather than showing a stale heart.
        try? await cache.remove(Key.gallery(galleryID))
        return state
    }

    func purgeCache() async {
        try? await cache.removeAll()
    }

    private func cached<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type
    ) async throws -> ResourceSnapshot<Value>? {
        guard let record = try await cache.read(key, as: type) else { return nil }
        return ResourceSnapshot(
            value: record.value,
            storedAt: record.storedAt,
            source: .cache
        )
    }

    private func persist<Value: Codable & Sendable>(
        _ value: Value,
        key: String,
        etag: String? = nil,
        storedAt: Date = Date()
    ) async throws -> ResourceSnapshot<Value> {
        let record = try await cache.write(
            value,
            key: key,
            etag: etag,
            storedAt: storedAt
        )
        return ResourceSnapshot(
            value: record.value,
            storedAt: record.storedAt,
            source: .network
        )
    }

    private func refreshConditional<Value: Codable & Sendable>(
        key: String,
        endpoint: APIEndpoint<Value>
    ) async throws -> ResourceSnapshot<Value> {
        let cached = try await cache.read(key, as: Value.self)
        do {
            let response = try await sendWithMetadata(
                endpoint.revalidating(with: cached?.etag)
            )
            return try await persist(
                response.value,
                key: key,
                etag: response.metadata.etag,
                storedAt: response.metadata.receivedAt
            )
        } catch let APIError.notModified(responseETag) {
            guard cached != nil else {
                throw OwnerRepositoryError.missingConditionalValue
            }
            guard let touched = try await cache.touch(
                key,
                as: Value.self,
                etag: responseETag
            ) else {
                throw OwnerRepositoryError.missingConditionalValue
            }
            return ResourceSnapshot(
                value: touched.value,
                storedAt: touched.storedAt,
                source: .revalidated
            )
        }
    }

    private func fetchAll<Element: Codable & Hashable & Sendable>(
        endpoint: @Sendable (String?) -> APIEndpoint<APIPage<Element>>
    ) async throws -> [Element] {
        var items: [Element] = []
        var cursor: String?
        var seenCursors = Set<String>()
        var pageCount = 0

        repeat {
            try Task.checkCancellation()
            let page = try await send(endpoint(cursor))
            items.append(contentsOf: page.items)
            pageCount += 1

            guard page.hasMore else { return items }
            guard
                pageCount < 200,
                let next = page.nextCursor,
                !next.isEmpty,
                seenCursors.insert(next).inserted
            else {
                throw OwnerRepositoryError.invalidPagination
            }
            cursor = next
        } while true
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
            try? await cache.removeAll()
            return
        }
        guard let sessionError = error as? SessionError else { return }
        switch sessionError {
        case .expired, .identityChanged, .workspaceMismatch:
            try? await cache.removeAll()
        }
    }
}
