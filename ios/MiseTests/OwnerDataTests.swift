import XCTest
@testable import Mise

final class OwnerDataTests: XCTestCase {
    func testCacheNamespacesCannotReadOrPurgeEachOther() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let first = TenantJSONCache(cacheNamespace: "tenant_one", rootDirectory: root)
        let second = TenantJSONCache(cacheNamespace: "tenant_two", rootDirectory: root)

        try await first.write([1, 2], key: "clients", etag: nil)
        try await second.write([9], key: "clients", etag: nil)
        try await first.removeAll()

        let firstValue = try await first.read("clients", as: [Int].self)
        let secondValue = try await second.read("clients", as: [Int].self)
        XCTAssertNil(firstValue)
        XCTAssertEqual(secondValue?.value, [9])
    }

    func testDashboardUsesETagAndTouchesCacheOnNotModified() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant_42", rootDirectory: root)
        let oldDate = Date(timeIntervalSince1970: 1_700_000_000)
        try await cache.write(Self.dashboard, key: "dashboard.v1", etag: #""dash-1""#, storedAt: oldDate)
        let client = QueuedOwnerClient(replies: [.failure(.notModified(etag: #""dash-1""#))])
        let repository = OwnerRepository(client: client, cache: cache)

        let result = try await repository.refreshDashboard()

        XCTAssertEqual(result.source, .revalidated)
        XCTAssertEqual(result.value.newInquiries, 3)
        XCTAssertGreaterThan(result.storedAt, oldDate)
        let requestETags = await client.requestETags()
        XCTAssertEqual(requestETags, [#""dash-1""#])
    }

    func testTerminalAuthenticationFailurePurgesPrivateSnapshots() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant_42", rootDirectory: root)
        try await cache.write([Self.client], key: "clients.v1", etag: nil)
        let client = QueuedOwnerClient(replies: [.failure(.unauthenticated(nil))])
        let repository = OwnerRepository(client: client, cache: cache)

        do {
            _ = try await repository.refreshClients()
            XCTFail("Expected the expired owner session to fail.")
        } catch APIError.unauthenticated(_) {
            // Expected. The repository must clear private snapshots before rethrowing.
        }

        let cached = try await cache.read("clients.v1", as: [ClientSummary].self)
        XCTAssertNil(cached)
    }

    private static let dashboard = DashboardSummary(
        generatedAt: Date(timeIntervalSince1970: 1_700_000_000),
        newInquiries: 3,
        outstanding: MoneyCount(count: 1, amount: Money(minorUnits: 12_500, currencyCode: "USD")),
        upcomingProjects14Days: 2,
        overdueInvoiceCount: 0,
        retainerDraftCount: 0,
        tasksDueCount: 4,
        actionItemCount: 4,
        kpis: DashboardKPIs(
            inquiriesDelta7Days: 1,
            bookingsDelta7Days: 2,
            collected7Days: Money(minorUnits: 50_000, currencyCode: "USD")
        ),
        openTasks: [],
        upcomingShoots: [],
        openInvoices: [],
        recentActivity: []
    )

    private static let client = ClientSummary(
        id: 7,
        name: "A. Client",
        company: nil,
        email: "client@example.com",
        phone: nil,
        market: "general",
        projectCount: 1,
        portalPublished: true,
        createdAt: Date(timeIntervalSince1970: 1_700_000_000)
    )
}

private actor QueuedOwnerClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data, APIResponseMetadata)
        case failure(APIError)
    }

    private var replies: [Reply]
    private var etags: [String?] = []

    init(replies: [Reply]) { self.replies = replies }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        etags.append(endpoint.etag)
        let reply = replies.removeFirst()
        switch reply {
        case let .value(data, metadata):
            return APIResponse(
                value: try MiseJSON.decoder().decode(Response.self, from: data),
                metadata: metadata
            )
        case let .failure(error):
            throw error
        }
    }

    func requestETags() -> [String?] { etags }
}
