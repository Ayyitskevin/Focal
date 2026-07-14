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

    var errorDescription: String? {
        switch self {
        case .invalidPagination:
            "The server returned an invalid page cursor."
        case .missingConditionalValue:
            "The server validated a cache item that is no longer available."
        }
    }
}

/// Owner data access. Every persisted value is scoped to the workspace's opaque
/// cache namespace by `TenantJSONCache`.
actor OwnerRepository {
    private enum Key {
        static let dashboard = "dashboard.v1"
        static let clients = "clients.v1"
        static let projects = "projects.v1"
        static let galleries = "galleries.v1"
        static let bookings = "bookings.v1"
        static let commercialActions = "commercial.actions.v1"
        static let companies = "commercial.companies.v1"

        static func gallery(_ id: Int64) -> String {
            "gallery.\(id).v1"
        }
    }

    private let client: any APIClientProtocol
    private let cache: TenantJSONCache

    init(client: any APIClientProtocol, cache: TenantJSONCache) {
        self.client = client
        self.cache = cache
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

    func setTaskCompletion(id: Int64, completed: Bool) async throws -> TaskCompletion {
        let value = try await send(
            MiseEndpoints.Tasks.completion(id: id, completed: completed)
        )
        try? await cache.remove(Key.dashboard)
        return value
    }

    func cancelBooking(id: Int64) async throws -> Booking {
        let value = try await send(MiseEndpoints.Scheduling.cancelBooking(id: id))
        try? await cache.remove(Key.bookings)
        try? await cache.remove(Key.dashboard)
        return value
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

    // ── Commercial spine (owner read-only; queue S9) ─────────────────────────

    func cachedCommercialActions() async throws -> ResourceSnapshot<[CommercialAction]>? {
        try await cached(Key.commercialActions, as: [CommercialAction].self)
    }

    func refreshCommercialActions() async throws -> ResourceSnapshot<[CommercialAction]> {
        let values = try await fetchAll { _ in MiseEndpoints.Commercial.actions }
        return try await persist(values, key: Key.commercialActions)
    }

    func cachedCompanies() async throws -> ResourceSnapshot<[CompanySummary]>? {
        try await cached(Key.companies, as: [CompanySummary].self)
    }

    func refreshCompanies() async throws -> ResourceSnapshot<[CompanySummary]> {
        let values = try await fetchAll { cursor in
            MiseEndpoints.Commercial.companies(cursor: cursor, limit: 100)
        }
        return try await persist(values, key: Key.companies)
    }

    /// Per-company / per-project detail views are fast-changing derived data;
    /// they fetch fresh each time (no offline cache) and surface as a network
    /// snapshot, consistent with the offline policy for derived summaries.
    func companyNextActions(id: Int64) async throws -> ResourceSnapshot<CompanyNextActions> {
        try await fetched(MiseEndpoints.Commercial.nextActions(companyID: id))
    }

    func arChase(companyID: Int64, invoiceID: Int64? = nil) async throws
        -> ResourceSnapshot<ArChaseAssist>
    {
        try await fetched(MiseEndpoints.Commercial.arChase(companyID: companyID, invoiceID: invoiceID))
    }

    func projectCloseout(id: Int64) async throws -> ResourceSnapshot<ProjectCloseout> {
        try await fetched(MiseEndpoints.Projects.closeout(projectID: id))
    }

    private func fetched<Value: Codable & Sendable>(
        _ endpoint: APIEndpoint<Value>
    ) async throws -> ResourceSnapshot<Value> {
        let value = try await send(endpoint)
        return ResourceSnapshot(value: value, storedAt: Date(), source: .network)
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
