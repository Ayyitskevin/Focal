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
    case missingBookingRescheduleSession
    case bookingRescheduleSessionMismatch
    case pendingBookingRescheduleConflict
    case pendingBookingRescheduleNotFound
    case pendingBookingRescheduleMismatch
    case invalidBookingRescheduleResponse
    case inactiveSession

    var errorDescription: String? {
        switch self {
        case .invalidPagination:
            "The server returned an invalid page cursor."
        case .missingConditionalValue:
            "The server validated a cache item that is no longer available."
        case .missingBookingRescheduleSession:
            "Booking reschedule requires the current backend session identity."
        case .bookingRescheduleSessionMismatch:
            "The saved booking reschedule belongs to a different session."
        case .pendingBookingRescheduleConflict:
            "Another booking reschedule is awaiting a definitive outcome."
        case .pendingBookingRescheduleNotFound:
            "The saved booking reschedule is no longer available."
        case .pendingBookingRescheduleMismatch:
            "The requested booking reschedule does not match the saved attempt."
        case .invalidBookingRescheduleResponse:
            "The server returned an inconsistent booking reschedule result."
        case .inactiveSession:
            "This signed-out repository can no longer update local state."
        }
    }
}

private struct StoredBookingRescheduleResult: Codable, Sendable {
    let sessionID: String
    let result: BookingRescheduleResult
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
        static let pendingBookingReschedule = "booking.reschedule.pending.v1"
        static let latestBookingReschedule = "booking.reschedule.latest.v1"
        static let commercialActions = "commercial.actions.v1"
        static let companies = "commercial.companies.v1"

        static func gallery(_ id: Int64) -> String {
            "gallery.\(id).v1"
        }
    }

    private let client: any APIClientProtocol
    private let cache: TenantJSONCache
    private let sessionID: String?
    private var cacheIsActive = true

    init(
        client: any APIClientProtocol,
        cache: TenantJSONCache,
        sessionID: String? = nil
    ) {
        self.client = client
        self.cache = cache
        self.sessionID = sessionID
    }

    func currentSession() async throws -> CurrentSession {
        try await send(MiseEndpoints.Auth.me)
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

    func bookingSlots(
        eventTypeID: Int64,
        day: LocalDate,
        rescheduleBookingID: Int64? = nil
    ) async throws -> EventTypeSlots {
        try await send(
            MiseEndpoints.Scheduling.eventTypeSlots(
                eventTypeID: eventTypeID,
                day: day,
                rescheduleBookingID: rescheduleBookingID
            )
        )
    }

    func prepareBookingReschedule(
        bookingID: Int64,
        startAt: Date,
        timeZone: String
    ) async throws -> PendingBookingRescheduleAttempt {
        let currentSessionID = try await requireBookingRescheduleSession()
        let normalizedStartAt = MiseJSON.wholeSecondUTCDate(startAt)
        let proposed = PendingBookingRescheduleAttempt(
            sessionID: currentSessionID,
            bookingID: bookingID,
            startAt: normalizedStartAt,
            timeZone: timeZone,
            idempotencyKey: UUID()
        )
        let attempt = try await cache.writeCommandIfAbsent(
            proposed,
            key: Key.pendingBookingReschedule,
            etag: nil
        ).value
        try await requireActiveCache()
        guard attempt.sessionID == currentSessionID else {
            _ = try? await cache.removeCommand(
                Key.pendingBookingReschedule,
                ifMatches: attempt
            )
            throw OwnerRepositoryError.bookingRescheduleSessionMismatch
        }
        guard
            attempt.bookingID == bookingID,
            attempt.startAt == normalizedStartAt,
            attempt.timeZone == timeZone
        else {
            throw OwnerRepositoryError.pendingBookingRescheduleConflict
        }
        return attempt
    }

    func pendingBookingRescheduleAttempt() async throws
        -> PendingBookingRescheduleAttempt?
    {
        let currentSessionID = try await requireBookingRescheduleSession()
        guard let attempt = try await cache.readCommand(
            Key.pendingBookingReschedule,
            as: PendingBookingRescheduleAttempt.self
        )?.value else {
            return nil
        }
        try await requireActiveCache()
        guard attempt.sessionID == currentSessionID else {
            _ = try? await cache.removeCommand(
                Key.pendingBookingReschedule,
                ifMatches: attempt
            )
            throw OwnerRepositoryError.bookingRescheduleSessionMismatch
        }
        return attempt
    }

    func submitBookingReschedule(
        _ attempt: PendingBookingRescheduleAttempt
    ) async throws -> BookingRescheduleResult {
        guard let saved = try await pendingBookingRescheduleAttempt() else {
            throw OwnerRepositoryError.pendingBookingRescheduleNotFound
        }
        guard saved == attempt else {
            throw OwnerRepositoryError.pendingBookingRescheduleMismatch
        }

        let result = try await send(
            try MiseEndpoints.Scheduling.rescheduleBooking(
                id: saved.bookingID,
                body: BookingRescheduleRequest(
                    startAt: saved.startAt,
                    timeZone: saved.timeZone
                ),
                idempotencyKey: saved.idempotencyKey
            )
        )
        try await requireActiveCache()
        guard
            result.status == .rescheduled,
            result.deliveryStatus == .pending,
            result.originalBookingID == saved.bookingID,
            result.replacementBookingID > 0,
            result.replacementBookingID != saved.bookingID,
            result.startAt == saved.startAt,
            MiseJSON.wholeSecondUTCDate(result.endAt) == result.endAt,
            result.endAt > result.startAt
        else {
            throw OwnerRepositoryError.invalidBookingRescheduleResponse
        }

        try await cache.write(
            StoredBookingRescheduleResult(
                sessionID: saved.sessionID,
                result: result
            ),
            key: Key.latestBookingReschedule,
            etag: nil
        )
        try await requireActiveCache()
        guard try await cache.removeCommand(
            Key.pendingBookingReschedule,
            ifMatches: saved
        ) else {
            throw OwnerRepositoryError.pendingBookingRescheduleMismatch
        }
        try? await cache.remove(Key.bookings)
        try? await cache.remove(Key.dashboard)
        return result
    }

    @discardableResult
    func discardPendingBookingReschedule(
        ifMatches attempt: PendingBookingRescheduleAttempt
    ) async throws -> Bool {
        guard let saved = try await pendingBookingRescheduleAttempt() else {
            return false
        }
        guard saved == attempt else { return false }
        return try await cache.removeCommand(
            Key.pendingBookingReschedule,
            ifMatches: attempt
        )
    }

    func latestBookingRescheduleResult() async throws
        -> BookingRescheduleResult?
    {
        let currentSessionID = try await requireBookingRescheduleSession()
        guard let stored = try await cache.read(
            Key.latestBookingReschedule,
            as: StoredBookingRescheduleResult.self
        )?.value else {
            return nil
        }
        guard stored.sessionID == currentSessionID else {
            try? await cache.remove(Key.latestBookingReschedule)
            throw OwnerRepositoryError.bookingRescheduleSessionMismatch
        }
        return stored.result
    }

    func bookingWorkflowStatus(id: UUID) async throws -> BookingWorkflowStatus {
        try await send(MiseEndpoints.Scheduling.bookingWorkflow(id: id))
    }

    func retryBookingWorkflow(id: UUID) async throws -> BookingWorkflowStatus {
        try await send(MiseEndpoints.Scheduling.retryBookingWorkflow(id: id))
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
        cacheIsActive = false
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

    private func requireBookingRescheduleSession() async throws -> String {
        guard cacheIsActive else {
            throw OwnerRepositoryError.inactiveSession
        }
        guard let sessionID, !sessionID.isEmpty else {
            throw OwnerRepositoryError.missingBookingRescheduleSession
        }
        return sessionID
    }

    private func requireActiveCache() async throws {
        guard cacheIsActive else {
            // A sign-out can interleave while an API request is suspended. Remove
            // any file a late response might have recreated before refusing it.
            try? await cache.removeAll()
            throw OwnerRepositoryError.inactiveSession
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
            cacheIsActive = false
            try? await cache.removeAll()
            return
        }
        guard let sessionError = error as? SessionError else { return }
        switch sessionError {
        case .expired, .identityChanged, .workspaceMismatch:
            cacheIsActive = false
            try? await cache.removeAll()
        }
    }
}
