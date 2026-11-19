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

    func testSnapshotPurgePreservesDurableAndLegacyCommandJournals() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant_one", rootDirectory: root)
        try await cache.write([1, 2], key: "clients", etag: nil)
        try await cache.writeCommandIfAbsent(
            ["durable"],
            key: "booking.reschedule.pending.v2.session-a",
            etag: nil
        )
        try await cache.write(
            ["legacy-pending"],
            key: "booking.reschedule.pending.v1",
            etag: nil
        )
        try await cache.write(
            ["legacy-latest"],
            key: "booking.reschedule.latest.v1",
            etag: nil
        )

        try await cache.removeAll()

        let snapshot = try await cache.read("clients", as: [Int].self)
        let durable = try await cache.readCommand(
            "booking.reschedule.pending.v2.session-a",
            as: [String].self
        )
        let legacyPending = try await cache.readLegacyCommand(
            "booking.reschedule.pending.v1",
            as: [String].self
        )
        let legacyLatest = try await cache.readLegacyCommand(
            "booking.reschedule.latest.v1",
            as: [String].self
        )
        XCTAssertNil(snapshot)
        XCTAssertEqual(durable?.value, ["durable"])
        XCTAssertEqual(legacyPending?.value, ["legacy-pending"])
        XCTAssertEqual(legacyLatest?.value, ["legacy-latest"])
    }

    func testLateCommandCommitCannotOverwriteNewerWorkflowHandle() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant_one", rootDirectory: root)
        let commandKey = "booking.reschedule.pending.v2.session-a"
        let resultKey = "booking.reschedule.latest.v2.session-a"
        let firstCommand = CacheCommand(id: "first")
        let secondCommand = CacheCommand(id: "second")
        let firstResult = CacheResult(id: "first")
        let secondResult = CacheResult(id: "second")

        try await cache.writeCommandIfAbsent(firstCommand, key: commandKey, etag: nil)
        let firstCommit = try await cache.commitCommand(
            firstResult,
            resultKey: resultKey,
            consuming: commandKey,
            ifMatches: firstCommand
        )
        try await cache.writeCommandIfAbsent(secondCommand, key: commandKey, etag: nil)
        let secondCommit = try await cache.commitCommand(
            secondResult,
            resultKey: resultKey,
            consuming: commandKey,
            ifMatches: secondCommand
        )
        let lateFirstCommit = try await cache.commitCommand(
            firstResult,
            resultKey: resultKey,
            consuming: commandKey,
            ifMatches: firstCommand
        )
        let repeatedSecondCommit = try await cache.commitCommand(
            secondResult,
            resultKey: resultKey,
            consuming: commandKey,
            ifMatches: secondCommand
        )
        let latest = try await cache.readCommand(resultKey, as: CacheResult.self)

        XCTAssertEqual(firstCommit, .committed)
        XCTAssertEqual(secondCommit, .committed)
        XCTAssertEqual(lateFirstCommit, .conflict)
        XCTAssertEqual(repeatedSecondCommit, .alreadyCommitted)
        XCTAssertEqual(latest?.value, secondResult)
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

    func testClientRecordReadsExactIDAndDoesNotSubstituteCachedDirectoryData() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant", rootDirectory: root)
        let response = try Self.clientReply()
        let client = QueuedOwnerClient(replies: [
            response, response, .failure(.notFound(nil)), .failure(.forbidden(nil)),
        ])
        let repository = OwnerRepository(
            client: client, cache: cache, sessionID: "owner", isCurrentLogin: { true }
        )

        let record = try await repository.clientRecord(id: 7)
        XCTAssertEqual(record, Self.client)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))

        do {
            _ = try await repository.clientRecord(id: 9)
            XCTFail("A reply for client 7 must never be displayed as client 9.")
        } catch OwnerRepositoryError.invalidClientRecord {}

        try await cache.write([record], key: "clients.v1", etag: nil)
        do {
            _ = try await repository.clientRecord(id: 7)
            XCTFail("A deleted record must stay unavailable even if its directory entry exists.")
        } catch APIError.notFound(_) {}
        do {
            _ = try await repository.clientRecord(id: 7)
            XCTFail("An authorization failure must be returned even if the directory entry exists.")
        } catch APIError.forbidden(_) {}

        let requests = await client.requestPaths()
        XCTAssertEqual(requests, ["/api/v1/clients/7", "/api/v1/clients/9", "/api/v1/clients/7", "/api/v1/clients/7"])
        let endpoint = MiseEndpoints.Clients.detail(id: 7)
        XCTAssertEqual(endpoint.authentication, .bearer)
        XCTAssertEqual(endpoint.method, .get)
    }

    func testClientRecordRejectsLateSuccessDuringSignOut() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant", rootDirectory: root)
        let client = QueuedOwnerClient(replies: [try Self.clientReply()])
        let repository = OwnerRepository(
            client: client, cache: cache, sessionID: "owner", isCurrentLogin: { true }
        )
        // Local purge starts sign-out before the authenticator clears its bearer.
        await client.beforeNextReply { await repository.purgeCache() }
        do {
            _ = try await repository.clientRecord(id: 7)
            XCTFail("A successful response returned after sign-out must be discarded.")
        } catch OwnerRepositoryError.clientRecordSessionChanged {}
    }

    func testClientRecordRejectsReplacedSessionWhileRequestIsInFlight() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let active = LockedBox(true)
        let cache = TenantJSONCache(cacheNamespace: "tenant", rootDirectory: root)
        let client = QueuedOwnerClient(replies: [try Self.clientReply()])
        let repository = OwnerRepository(
            client: client, cache: cache, sessionID: "owner",
            isCurrentLogin: { active.withValue { $0 } }
        )
        await client.beforeNextReply { active.withValue { $0 = false } }
        do {
            _ = try await repository.clientRecord(id: 7)
            XCTFail("The former session must not deliver a record after its login changes.")
        } catch OwnerRepositoryError.clientRecordSessionChanged {}
        do {
            _ = try await repository.clientRecord(id: 7)
            XCTFail("The former repository must not send another request with the replacement login.")
        } catch OwnerRepositoryError.clientRecordSessionChanged {}
        let paths = await client.requestPaths()
        XCTAssertEqual(paths, ["/api/v1/clients/7"])
    }

    private static func clientReply() throws -> QueuedOwnerClient.Reply {
        .value(
            try MiseJSON.encoder().encode(client),
            APIResponseMetadata(etag: nil, lastModified: nil, receivedAt: Date())
        )
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

private struct CacheCommand: Codable, Equatable, Sendable {
    let id: String
}

private struct CacheResult: Codable, Equatable, Sendable {
    let id: String
}

private actor QueuedOwnerClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data, APIResponseMetadata)
        case failure(APIError)
    }

    private var replies: [Reply]
    private var etags: [String?] = []
    private var paths: [String] = []
    private var beforeReply: (@Sendable () async -> Void)?

    init(replies: [Reply]) { self.replies = replies }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        paths.append(endpoint.path)
        etags.append(endpoint.etag)
        if let run = beforeReply {
            beforeReply = nil
            await run()
        }
        guard !replies.isEmpty else { throw APIError.unexpectedResponse }
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
    func requestPaths() -> [String] { paths }

    func beforeNextReply(_ action: @escaping @Sendable () async -> Void) {
        beforeReply = action
    }
}
