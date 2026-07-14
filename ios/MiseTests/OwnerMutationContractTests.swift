import XCTest
@testable import Mise

final class OwnerMutationContractTests: XCTestCase {
    func testTaskCompletionEndpointsUseBodylessBearerPutAndDelete() {
        let complete = MiseEndpoints.Tasks.completion(id: 42, completed: true)
        let reopen = MiseEndpoints.Tasks.completion(id: 42, completed: false)

        XCTAssertEqual(complete.method, .put)
        XCTAssertEqual(reopen.method, .delete)
        XCTAssertEqual(complete.path, "/api/v1/tasks/42/completion")
        XCTAssertEqual(reopen.path, "/api/v1/tasks/42/completion")
        XCTAssertEqual(complete.authentication, .bearer)
        XCTAssertEqual(reopen.authentication, .bearer)
        XCTAssertNil(complete.body)
        XCTAssertNil(reopen.body)
        XCTAssertNil(complete.idempotencyKey)
        XCTAssertNil(reopen.idempotencyKey)
    }

    func testCancelBookingEndpointUsesBodylessBearerPost() {
        let endpoint = MiseEndpoints.Scheduling.cancelBooking(id: 91)

        XCTAssertEqual(endpoint.method, .post)
        XCTAssertEqual(endpoint.path, "/api/v1/bookings/91/cancel")
        XCTAssertEqual(endpoint.authentication, .bearer)
        XCTAssertNil(endpoint.body)
        XCTAssertNil(endpoint.idempotencyKey)
    }

    func testTaskCompletionDecodesServerTimestampAndReopenNull() throws {
        let completed = try MiseJSON.decoder().decode(
            TaskCompletion.self,
            from: Data(
                #"{"id":42,"done":true,"completed_at":"2026-07-13T14:15:16Z"}"#.utf8
            )
        )
        let reopened = try MiseJSON.decoder().decode(
            TaskCompletion.self,
            from: Data(#"{"id":42,"done":false,"completed_at":null}"#.utf8)
        )

        XCTAssertEqual(completed.id, 42)
        XCTAssertTrue(completed.done)
        XCTAssertNotNil(completed.completedAt)
        XCTAssertFalse(reopened.done)
        XCTAssertNil(reopened.completedAt)
    }

    func testTaskSuccessInvalidatesOnlyDashboardSnapshot() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(
            Self.dashboard,
            key: "dashboard.v1",
            etag: #""dashboard-1""#
        )
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let client = OwnerMutationContractClient([
            .value(Data(
                #"{"id":42,"done":true,"completed_at":"2026-07-13T14:15:16Z"}"#.utf8
            )),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)

        let completion = try await repository.setTaskCompletion(id: 42, completed: true)
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        let cachedBookings = try await context.cache.read(
            "bookings.v1",
            as: [Booking].self
        )

        XCTAssertTrue(completion.done)
        XCTAssertNil(cachedDashboard)
        XCTAssertNotNil(cachedBookings)
    }

    func testTaskFailurePreservesDashboardSnapshot() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        let client = OwnerMutationContractClient([
            .failure(.server(status: 503, problem: nil)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)

        do {
            _ = try await repository.setTaskCompletion(id: 42, completed: true)
            XCTFail("Expected the failed task mutation to throw.")
        } catch let APIError.server(status, _) {
            XCTAssertEqual(status, 503)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        let cached = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertEqual(cached?.value.newInquiries, Self.dashboard.newInquiries)
    }

    func testMalformedTaskResponsePreservesDashboardSnapshot() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        let client = OwnerMutationContractClient([
            .value(Data(#"{"id":"not-an-integer","done":true,"completed_at":null}"#.utf8)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)

        do {
            _ = try await repository.setTaskCompletion(id: 42, completed: true)
            XCTFail("Expected the malformed response to fail decoding.")
        } catch is DecodingError {
            // Expected. Cache invalidation happens only after a decoded result.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        let cached = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertEqual(cached?.value.newInquiries, Self.dashboard.newInquiries)
    }

    func testCancelSuccessDecodesResponseAndInvalidatesBookingAndDashboardSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let client = OwnerMutationContractClient([
            .value(Data(Self.cancelledBookingJSON.utf8)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)

        let booking = try await repository.cancelBooking(id: 91)
        let cachedBookings = try await context.cache.read(
            "bookings.v1",
            as: [Booking].self
        )
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )

        XCTAssertEqual(booking.id, 91)
        XCTAssertEqual(booking.status, .cancelled)
        XCTAssertEqual(booking.cancelReason, "Cancelled from the studio app")
        XCTAssertNotNil(booking.cancelledAt)
        XCTAssertNil(cachedBookings)
        XCTAssertNil(cachedDashboard)
    }

    func testCancelFailurePreservesBookingAndDashboardSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let client = OwnerMutationContractClient([
            .failure(.server(status: 503, problem: nil)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)

        do {
            _ = try await repository.cancelBooking(id: 91)
            XCTFail("Expected the failed booking mutation to throw.")
        } catch let APIError.server(status, _) {
            XCTAssertEqual(status, 503)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        let cachedBookings = try await context.cache.read(
            "bookings.v1",
            as: [Booking].self
        )
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertNotNil(cachedBookings)
        XCTAssertNotNil(cachedDashboard)
    }

    func testTerminalAuthenticationFailureStillPurgesAllSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let client = OwnerMutationContractClient([
            .failure(.unauthenticated(nil)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)

        do {
            _ = try await repository.setTaskCompletion(id: 42, completed: true)
            XCTFail("Expected the expired owner session to fail.")
        } catch APIError.unauthenticated(_) {
            // Expected. Terminal authentication failures intentionally override
            // ordinary mutation-failure cache preservation.
        }

        let cachedBookings = try await context.cache.read(
            "bookings.v1",
            as: [Booking].self
        )
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertNil(cachedBookings)
        XCTAssertNil(cachedDashboard)
    }

    private func makeCache() -> (root: URL, cache: TenantJSONCache) {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("owner-mutation-tests-\(UUID().uuidString)")
        return (
            root,
            TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root)
        )
    }

    private static let dashboard = DashboardSummary(
        generatedAt: Date(timeIntervalSince1970: 1_700_000_000),
        newInquiries: 3,
        outstanding: MoneyCount(
            count: 1,
            amount: Money(minorUnits: 12_500, currencyCode: "USD")
        ),
        upcomingProjects14Days: 2,
        overdueInvoiceCount: 0,
        retainerDraftCount: 0,
        tasksDueCount: 1,
        actionItemCount: 1,
        kpis: DashboardKPIs(
            inquiriesDelta7Days: 1,
            bookingsDelta7Days: 2,
            collected7Days: Money(minorUnits: 50_000, currencyCode: "USD")
        ),
        openTasks: [
            TaskSummary(
                id: 42,
                title: "Cull the tasting menu",
                dueOn: nil,
                projectID: nil,
                projectTitle: nil,
                isOverdue: false
            ),
        ],
        upcomingShoots: [],
        openInvoices: [],
        recentActivity: []
    )

    private static let confirmedBooking = Booking(
        id: 91,
        eventTypeID: 8,
        eventName: "Menu tasting",
        name: "Rossi Trattoria",
        email: "ops@rossi.test",
        phone: nil,
        notes: nil,
        startAt: Date(timeIntervalSince1970: 4_074_865_200),
        endAt: Date(timeIntervalSince1970: 4_074_867_900),
        timeZone: "America/New_York",
        status: .confirmed,
        clientID: nil,
        projectID: nil,
        rescheduledFromID: nil,
        cancelReason: nil,
        cancelledAt: nil,
        createdAt: Date(timeIntervalSince1970: 1_700_000_000)
    )

    private static let cancelledBookingJSON = """
    {
      "id": 91,
      "event_type_id": 8,
      "event_name": "Menu tasting",
      "name": "Rossi Trattoria",
      "email": "ops@rossi.test",
      "phone": null,
      "notes": null,
      "start_at": "2099-02-01T15:00:00Z",
      "end_at": "2099-02-01T15:45:00Z",
      "time_zone": "America/New_York",
      "status": "cancelled",
      "client_id": null,
      "project_id": null,
      "rescheduled_from_id": null,
      "cancel_reason": "Cancelled from the studio app",
      "cancelled_at": "2026-07-13T14:15:16Z",
      "created_at": "2026-07-01T12:00:00Z"
    }
    """
}

private actor OwnerMutationContractClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data)
        case failure(APIError)
    }

    private var replies: [Reply]

    init(_ replies: [Reply]) {
        self.replies = replies
    }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        let reply = replies.removeFirst()
        switch reply {
        case let .value(data):
            return try MiseJSON.decoder().decode(Response.self, from: data)
        case let .failure(error):
            throw error
        }
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        APIResponse(
            value: try await send(endpoint),
            metadata: APIResponseMetadata(
                etag: nil,
                lastModified: nil,
                receivedAt: Date()
            )
        )
    }
}
