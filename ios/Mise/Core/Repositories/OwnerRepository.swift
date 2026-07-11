import Foundation

enum ResourceSnapshotSource: Equatable, Sendable {
    case cache
    case network
    case revalidated
}

struct ResourceSnapshot<Value: Codable & Sendable>: Sendable {
    let value: Value
    let storedAt: Date
    let source: ResourceSnapshotSource

    func isStale(after interval: TimeInterval, now: Date = Date()) -> Bool {
        now.timeIntervalSince(storedAt) > interval
    }
}

enum OwnerRepositoryError: LocalizedError, Sendable {
    case invalidPagination
    case missingConditionalValue
    case missingEntityTag

    var errorDescription: String? {
        switch self {
        case .invalidPagination:
            "The server returned an invalid page cursor."
        case .missingConditionalValue:
            "The server validated a cache item that is no longer available."
        case .missingEntityTag:
            "The server did not provide a version for this item. Reload it and try again."
        }
    }
}

/// Read-only owner data access. Every persisted value is scoped to the workspace's
/// opaque cache namespace by `TenantJSONCache`.
actor OwnerRepository {
    private enum Key {
        static let dashboard = "dashboard.v1"
        static let clients = "clients.v1"
        static let projects = "projects.v1"
        static let galleries = "galleries.v1"
        static let bookings = "bookings.v1"
        static let tasks = "tasks.v1"

        static func gallery(_ id: Int64) -> String {
            "gallery.\(id).v1"
        }
    }

    let client: any APIClientProtocol
    let cache: TenantJSONCache
    private let onSessionEnded: @Sendable () async -> Void
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

    func cachedDashboard() async throws -> ResourceSnapshot<DashboardSummary>? {
        try await cached(Key.dashboard, as: DashboardSummary.self)
    }

    func refreshDashboard() async throws -> ResourceSnapshot<DashboardSummary> {
        try await refreshConditional(
            key: Key.dashboard,
            endpoint: MiseEndpoints.dashboard
        )
    }

    func cachedClients() async throws -> ResourceSnapshot<[ClientSummary]>? {
        try await cached(Key.clients, as: [ClientSummary].self)
    }

    func refreshClients() async throws -> ResourceSnapshot<[ClientSummary]> {
        let values = try await fetchAll { cursor in
            MiseEndpoints.Clients.list(cursor: cursor, limit: 100)
        }
        return try await persist(values, key: Key.clients)
    }

    func cachedProjects() async throws -> ResourceSnapshot<[ProjectSummary]>? {
        try await cached(Key.projects, as: [ProjectSummary].self)
    }

    func refreshProjects() async throws -> ResourceSnapshot<[ProjectSummary]> {
        let values = try await fetchAll { cursor in
            MiseEndpoints.Projects.list(cursor: cursor, limit: 100)
        }
        return try await persist(values, key: Key.projects)
    }

    func cachedGalleries() async throws -> ResourceSnapshot<[GallerySummary]>? {
        try await cached(Key.galleries, as: [GallerySummary].self)
    }

    func refreshGalleries() async throws -> ResourceSnapshot<[GallerySummary]> {
        let values = try await fetchAll { cursor in
            MiseEndpoints.Galleries.list(cursor: cursor, limit: 100)
        }
        return try await persist(values, key: Key.galleries)
    }

    func cachedBookings() async throws -> ResourceSnapshot<[Booking]>? {
        try await cached(Key.bookings, as: [Booking].self)
    }

    func refreshBookings() async throws -> ResourceSnapshot<[Booking]> {
        let values = try await fetchAll { cursor in
            MiseEndpoints.Scheduling.bookings(cursor: cursor, limit: 100)
        }
        return try await persist(values, key: Key.bookings)
    }

    func cachedGallery(id: Int64) async throws -> ResourceSnapshot<GalleryDetail>? {
        try await cached(Key.gallery(id), as: GalleryDetail.self)
    }

    func refreshGallery(id: Int64) async throws -> ResourceSnapshot<GalleryDetail> {
        try await refreshConditional(
            key: Key.gallery(id),
            endpoint: MiseEndpoints.Galleries.detail(id: id)
        )
    }

    func purgeCache() async {
        try? await cache.endAccessAndRemoveAll()
        await lifetime.end()
    }

    func cacheLifetimeTicket() async throws -> UInt64 {
        guard let ticket = await lifetime.ticket() else {
            throw APIError.unauthenticated(nil)
        }
        return ticket
    }

    func requireActiveCacheLifetime(_ ticket: UInt64) async throws {
        guard await lifetime.isActive(ticket) else {
            throw APIError.unauthenticated(nil)
        }
    }

    private func cached<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type
    ) async throws -> ResourceSnapshot<Value>? {
        let ticket = try await cacheLifetimeTicket()
        let record = try await cache.read(key, as: type)
        try await requireActiveCacheLifetime(ticket)
        guard let record else { return nil }
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
        let ticket = try await cacheLifetimeTicket()
        return try await persist(
            value,
            key: key,
            etag: etag,
            storedAt: storedAt,
            lifetimeTicket: ticket
        )
    }

    private func persist<Value: Codable & Sendable>(
        _ value: Value,
        key: String,
        etag: String?,
        storedAt: Date,
        lifetimeTicket: UInt64
    ) async throws -> ResourceSnapshot<Value> {
        try await requireActiveCacheLifetime(lifetimeTicket)
        let record = try await cache.write(
            value,
            key: key,
            etag: etag,
            storedAt: storedAt
        )
        try await requireActiveCacheLifetime(lifetimeTicket)
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
        let ticket = try await cacheLifetimeTicket()
        let cached = try await cache.read(key, as: Value.self)
        try await requireActiveCacheLifetime(ticket)
        do {
            let response = try await sendWithMetadata(
                endpoint.revalidating(with: cached?.etag)
            )
            return try await persist(
                response.value,
                key: key,
                etag: response.metadata.etag,
                storedAt: response.metadata.receivedAt,
                lifetimeTicket: ticket
            )
        } catch let APIError.notModified(responseETag) {
            try await requireActiveCacheLifetime(ticket)
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
            try await requireActiveCacheLifetime(ticket)
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
        let ticket = try await cacheLifetimeTicket()
        var items: [Element] = []
        var cursor: String?
        var seenCursors = Set<String>()
        var pageCount = 0

        repeat {
            try Task.checkCancellation()
            try await requireActiveCacheLifetime(ticket)
            let page = try await send(endpoint(cursor))
            try await requireActiveCacheLifetime(ticket)
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

    func sendWithMetadata<Response: Decodable & Sendable>(
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
            await purgeCache()
            await onSessionEnded()
            return
        }
        guard let sessionError = error as? SessionError else { return }
        switch sessionError {
        case .expired, .identityChanged, .workspaceMismatch:
            await purgeCache()
            await onSessionEnded()
        }
    }
}
