import XCTest
@testable import Mise

final class InquiryInboxTests: XCTestCase {
    func testInquirySummaryDecodesWireShapeAndNullables() throws {
        let inquiry = try MiseJSON.decoder().decode(
            InquirySummary.self,
            from: Data(Self.openInquiryJSON.utf8)
        )

        XCTAssertEqual(inquiry.id, 123)
        XCTAssertEqual(inquiry.name, "Jordan Lee")
        XCTAssertNil(inquiry.business)
        XCTAssertEqual(inquiry.email, "jordan@example.com")
        XCTAssertNil(inquiry.phone)
        XCTAssertEqual(inquiry.kind, "wedding")
        XCTAssertEqual(inquiry.service, "Wedding photography")
        XCTAssertEqual(inquiry.shootOn, LocalDate(rawValue: "2026-10-03"))
        XCTAssertEqual(inquiry.messagePreview, "Hi! We’re getting married…")
        XCTAssertEqual(inquiry.status, .open)
        XCTAssertFalse(inquiry.isReplied)
        XCTAssertNil(inquiry.convertedClientID)
        XCTAssertNil(inquiry.convertedProjectID)
        XCTAssertEqual(
            inquiry.receivedAt,
            try Self.date("2026-08-01T14:22:10.123456+00:00")
        )
    }

    func testInquiryStatusPreservesUnknownValues() throws {
        let converted = try MiseJSON.decoder().decode(
            InquirySummary.self,
            from: Data(Self.openInquiryJSON
                .replacingOccurrences(of: #""status": "open""#, with: #""status": "converted""#)
                .replacingOccurrences(of: #""converted_client_id": null"#, with: #""converted_client_id": 77"#)
                .utf8)
        )
        let future = try MiseJSON.decoder().decode(
            InquirySummary.self,
            from: Data(Self.openInquiryJSON
                .replacingOccurrences(of: #""status": "open""#, with: #""status": "future_state""#)
                .utf8)
        )

        XCTAssertEqual(converted.status, .converted)
        XCTAssertEqual(converted.convertedClientID, 77)
        XCTAssertEqual(future.status.rawValue, "future_state")
        XCTAssertEqual(future.status.ownerDisplayName, "Future State")
    }

    func testInquiryStatusDisplayNames() {
        XCTAssertEqual(InquiryStatus.open.ownerDisplayName, "Open")
        XCTAssertEqual(InquiryStatus.converted.ownerDisplayName, "Converted")
        XCTAssertEqual(InquiryStatus.dismissed.ownerDisplayName, "Dismissed")
    }

    func testInquiryDetailDecodesStoredThreadAndHistoryFlag() throws {
        let detail = try MiseJSON.decoder().decode(
            InquiryDetail.self,
            from: Data(
                """
                {
                  "summary": \(Self.openInquiryJSON),
                  "messages": [{
                    "id": 44,
                    "direction": "out",
                    "channel": "email",
                    "body": "Thanks for reaching out.",
                    "recorded_at": "2026-08-01T15:00:00Z",
                    "is_truncated": false
                  }],
                  "has_older_messages": true
                }
                """.utf8
            )
        )

        XCTAssertEqual(detail.summary.id, 123)
        XCTAssertEqual(detail.messages.map(\.id), [44])
        XCTAssertEqual(detail.messages.first?.direction, .outbound)
        XCTAssertEqual(detail.messages.first?.body, "Thanks for reaching out.")
        XCTAssertEqual(detail.messages.first?.recordedAt, try Self.date("2026-08-01T15:00:00Z"))
        XCTAssertFalse(detail.messages.first?.isTruncated ?? true)
        XCTAssertTrue(detail.hasOlderMessages)
    }

    func testInquiryListEndpointUsesBodylessBearerGetAndBoundedQuery() {
        let defaultPage = MiseEndpoints.Inquiries.list()
        let cursorPage = MiseEndpoints.Inquiries.list(cursor: "next-page", limit: 500)

        XCTAssertEqual(defaultPage.method, .get)
        XCTAssertEqual(defaultPage.path, "/api/v1/inquiries")
        XCTAssertEqual(
            defaultPage.queryItems,
            [
                APIQueryItem(name: "cursor", value: nil),
                APIQueryItem(name: "limit", value: "25"),
            ]
        )
        XCTAssertEqual(cursorPage.authentication, .bearer)
        XCTAssertEqual(
            cursorPage.queryItems,
            [
                APIQueryItem(name: "cursor", value: "next-page"),
                APIQueryItem(name: "limit", value: "100"),
            ]
        )
        XCTAssertNil(cursorPage.body)
        XCTAssertNil(cursorPage.idempotencyKey)
        XCTAssertNil(cursorPage.etag)
    }

    func testInquiryDetailAndDismissalEndpointsUseBodylessBearerRequests() {
        let detail = MiseEndpoints.Inquiries.detail(id: 123)
        let dismiss = MiseEndpoints.Inquiries.dismissal(id: 123, dismissed: true)
        let restore = MiseEndpoints.Inquiries.dismissal(id: 123, dismissed: false)

        XCTAssertEqual(detail.method, .get)
        XCTAssertEqual(detail.path, "/api/v1/inquiries/123")
        XCTAssertEqual(detail.authentication, .bearer)
        XCTAssertNil(detail.body)
        XCTAssertNil(detail.idempotencyKey)
        XCTAssertEqual(dismiss.method, .put)
        XCTAssertEqual(dismiss.path, "/api/v1/inquiries/123/dismissal")
        XCTAssertEqual(dismiss.authentication, .bearer)
        XCTAssertNil(dismiss.body)
        XCTAssertNil(dismiss.idempotencyKey)
        XCTAssertEqual(restore.method, .delete)
        XCTAssertEqual(restore.path, dismiss.path)
        XCTAssertNil(restore.body)
        XCTAssertNil(restore.idempotencyKey)
    }

    func testInquiryConversionEndpointUsesBodylessBearerPutWithoutIdempotencyKey() {
        let endpoint = MiseEndpoints.Inquiries.conversion(id: 123)

        XCTAssertEqual(endpoint.method, .put)
        XCTAssertEqual(endpoint.path, "/api/v1/inquiries/123/conversion")
        XCTAssertEqual(endpoint.authentication, .bearer)
        XCTAssertNil(endpoint.body)
        XCTAssertNil(endpoint.idempotencyKey)
    }

    func testRefreshInquiriesAggregatesEveryPageIntoTheTenantCache() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("inquiry-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root)
        let client = QueuedInquiryClient(replies: [
            Data(
                """
                {
                  "items": [\(Self.openInquiryJSON)],
                  "next_cursor": "next-page", "has_more": true
                }
                """.utf8
            ),
            Data(
                """
                {
                  "items": [\(Self.dismissedInquiryJSON)],
                  "next_cursor": null, "has_more": false
                }
                """.utf8
            ),
        ])
        let repository = OwnerRepository(client: client, cache: cache)

        let snapshot = try await repository.refreshInquiries()
        let cached = try await repository.cachedInquiries()
        let rawCached = try await cache.read("inquiries.v1", as: [InquirySummary].self)
        let requests = await client.capturedRequests()

        XCTAssertEqual(snapshot.source, .network)
        XCTAssertEqual(snapshot.value.map(\.id), [123, 98])
        XCTAssertEqual(snapshot.value.map(\.status), [.open, .dismissed])
        XCTAssertEqual(cached?.source, .cache)
        XCTAssertEqual(cached?.value, snapshot.value)
        XCTAssertNotNil(rawCached)
        XCTAssertEqual(requests.map(\.method), [.get, .get])
        XCTAssertEqual(requests.map(\.path), ["/api/v1/inquiries", "/api/v1/inquiries"])
        XCTAssertEqual(
            requests.map(\.queryItems),
            [
                [
                    APIQueryItem(name: "cursor", value: nil),
                    APIQueryItem(name: "limit", value: "100"),
                ],
                [
                    APIQueryItem(name: "cursor", value: "next-page"),
                    APIQueryItem(name: "limit", value: "100"),
                ],
            ]
        )
    }

    func testRefreshInquiriesRejectsACyclingCursor() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("inquiry-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root)
        let cyclingPage = Data(
            """
            {
              "items": [\(Self.openInquiryJSON)],
              "next_cursor": "same-page", "has_more": true
            }
            """.utf8
        )
        let client = QueuedInquiryClient(replies: [cyclingPage, cyclingPage])
        let repository = OwnerRepository(client: client, cache: cache)

        do {
            _ = try await repository.refreshInquiries()
            XCTFail("Expected a repeated cursor to fail pagination.")
        } catch OwnerRepositoryError.invalidPagination {
            // Expected; a cycling cursor must not loop forever.
        }

        let cached = try await repository.cachedInquiries()
        XCTAssertNil(cached)
    }

    func testRefreshInquiriesCannotRecreateCacheAfterPurge() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("inquiry-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root)
        let client = SuspendedInquiryClient(reply: Data(
            """
            {
              "items": [\(Self.openInquiryJSON)],
              "next_cursor": null, "has_more": false
            }
            """.utf8
        ))
        let repository = OwnerRepository(client: client, cache: cache)
        let refresh = Task { try await repository.refreshInquiries() }

        await client.waitUntilRequested()
        await repository.purgeCache()
        await client.release()

        do {
            _ = try await refresh.value
            XCTFail("Expected a response completing after purge to be rejected.")
        } catch OwnerRepositoryError.inactiveSession {
            // Expected; signed-out repositories cannot recreate private snapshots.
        }

        let rawCached = try await cache.read("inquiries.v1", as: [InquirySummary].self)
        let cached = try await repository.cachedInquiries()
        XCTAssertNil(rawCached)
        XCTAssertNil(cached)
    }

    @MainActor
    func testStaleInquiryReadCannotOverwriteConversion() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("inquiry-tests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root)
        let convertedDetail = Self.openInquiryJSON
            .replacingOccurrences(of: #""status": "open""#, with: #""status": "converted""#)
            .replacingOccurrences(of: #""converted_client_id": null"#, with: #""converted_client_id": 77"#)
        let client = RaceInquiryClient(
            openPage: Data(
                """
                {
                  "items": [\(Self.openInquiryJSON)],
                  "next_cursor": null, "has_more": false
                }
                """.utf8
            ),
            convertedDetail: Data(
                """
                {
                  "summary": \(convertedDetail),
                  "messages": [],
                  "has_older_messages": false
                }
                """.utf8
            )
        )
        let repository = OwnerRepository(client: client, cache: cache)
        let openInquiry = try MiseJSON.decoder().decode(
            InquirySummary.self,
            from: Data(Self.openInquiryJSON.utf8)
        )
        let model = ResourceModel<[InquirySummary]>(
            staleAfter: 0,
            cached: {
                ResourceSnapshot(
                    value: [openInquiry],
                    storedAt: Date(timeIntervalSince1970: 1),
                    source: .cache
                )
            },
            remote: { try await repository.refreshInquiries() }
        )

        async let load: Void = model.load()
        await client.waitUntilRefreshStarted()
        _ = try await repository.convertInquiry(id: 123)
        await client.finishRefresh()
        await load

        let cached = try await cache.read("inquiries.v1", as: [InquirySummary].self)
        XCTAssertNil(cached)
        XCTAssertNil(model.state.snapshot)
        guard case let .failed(_, failure) = model.state else {
            XCTFail("Expected the stale read to fail instead of publishing old data.")
            return
        }
        XCTAssertEqual(failure.message, OwnerRepositoryError.staleRead.localizedDescription)
    }

    private static func date(_ value: String) throws -> Date {
        try MiseJSON.decoder().decode(Date.self, from: Data("\"\(value)\"".utf8))
    }

    private static let openInquiryJSON = """
    {
      "id": 123,
      "name": "Jordan Lee",
      "business": null,
      "email": "jordan@example.com",
      "phone": null,
      "kind": "wedding",
      "service": "Wedding photography",
      "shoot_on": "2026-10-03",
      "message_preview": "Hi! We’re getting married…",
      "status": "open",
      "is_replied": false,
      "converted_client_id": null,
      "converted_project_id": null,
      "received_at": "2026-08-01T14:22:10.123456+00:00"
    }
    """

    private static let dismissedInquiryJSON = """
    {
      "id": 98,
      "name": "Morgan Reyes",
      "business": "Reyes Bakery",
      "email": null,
      "phone": "+1 555 0100",
      "kind": "brand_session",
      "service": null,
      "shoot_on": null,
      "message_preview": "Looking for updated brand photos.",
      "status": "dismissed",
      "is_replied": true,
      "converted_client_id": null,
      "converted_project_id": null,
      "received_at": "2026-07-28T09:05:00Z"
    }
    """
}

private struct CapturedInquiryRequest: Sendable {
    let method: HTTPMethod
    let path: String
    let queryItems: [APIQueryItem]
}

private actor SuspendedInquiryClient: APIClientProtocol {
    private let reply: Data
    private var requested = false
    private var released = false

    init(reply: Data) { self.reply = reply }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        requested = true
        while !released {
            await Task.yield()
        }
        return APIResponse(
            value: try MiseJSON.decoder().decode(Response.self, from: reply),
            metadata: APIResponseMetadata(etag: nil, lastModified: nil, receivedAt: Date())
        )
    }

    func waitUntilRequested() async {
        while !requested {
            await Task.yield()
        }
    }

    func release() {
        released = true
    }
}

private actor QueuedInquiryClient: APIClientProtocol {
    private var replies: [Data]
    private var requests: [CapturedInquiryRequest] = []

    init(replies: [Data]) { self.replies = replies }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        requests.append(CapturedInquiryRequest(
            method: endpoint.method,
            path: endpoint.path,
            queryItems: endpoint.queryItems
        ))
        let data = replies.removeFirst()
        return APIResponse(
            value: try MiseJSON.decoder().decode(Response.self, from: data),
            metadata: APIResponseMetadata(etag: nil, lastModified: nil, receivedAt: Date())
        )
    }

    func capturedRequests() -> [CapturedInquiryRequest] {
        requests
    }
}

private actor RaceInquiryClient: APIClientProtocol {
    private let openPage: Data
    private let convertedDetail: Data
    private var refreshStarted = false
    private var releaseRefresh = false

    init(openPage: Data, convertedDetail: Data) {
        self.openPage = openPage
        self.convertedDetail = convertedDetail
    }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        if endpoint.method == .get {
            refreshStarted = true
            while !releaseRefresh {
                await Task.yield()
            }
        }
        let data = endpoint.method == .get ? openPage : convertedDetail
        return APIResponse(
            value: try MiseJSON.decoder().decode(Response.self, from: data),
            metadata: APIResponseMetadata(etag: nil, lastModified: nil, receivedAt: Date())
        )
    }

    func waitUntilRefreshStarted() async {
        while !refreshStarted {
            await Task.yield()
        }
    }

    func finishRefresh() {
        releaseRefresh = true
    }
}
