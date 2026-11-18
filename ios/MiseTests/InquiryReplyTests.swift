import XCTest
@testable import Mise

final class InquiryReplyTests: XCTestCase {
    func testReplyEndpointEncodesStableRequestAndSentResult() throws {
        let key = try XCTUnwrap(UUID(uuidString: "86AA7D32-740E-4993-AF8D-438BB80C366B"))
        let endpoint = try MiseEndpoints.Inquiries.reply(
            id: 123,
            body: InquiryReplyRequest(subject: "Re: your inquiry", body: "Thanks — I can help."),
            idempotencyKey: key
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try XCTUnwrap(endpoint.body)) as? [String: String]
        )

        XCTAssertEqual(endpoint.method, .post)
        XCTAssertEqual(endpoint.path, "/api/v1/inquiries/123/reply")
        XCTAssertEqual(endpoint.authentication, .bearer)
        XCTAssertEqual(endpoint.idempotencyKey, key)
        XCTAssertEqual(object, [
            "channel": "email",
            "subject": "Re: your inquiry",
            "body": "Thanks — I can help.",
        ])

        let result = try MiseJSON.decoder().decode(
            InquiryReplyResult.self,
            from: Data(Self.sentReplyJSON.utf8)
        )
        XCTAssertEqual(result.code, "inquiry.reply_sent")
        XCTAssertEqual(result.deliveryStatus, "sent")
        XCTAssertEqual(result.messageID, 51)
        XCTAssertEqual(result.detail.summary.id, 123)
        XCTAssertTrue(result.detail.summary.isReplied)
        XCTAssertEqual(result.detail.messages.map(\.id), [51])
    }

    func testSentReplyInvalidatesOnlyInquiryAndDashboardSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write([Self.openInquiry], key: "inquiries.v1", etag: nil)
        try await context.cache.write([ClientSummary](), key: "clients.v1", etag: nil)
        try await context.cache.write([ProjectSummary](), key: "projects.v1", etag: nil)
        let key = UUID()
        let client = ReplyClient([.value(Data(Self.sentReplyJSON.utf8))])
        let repository = OwnerRepository(client: client, cache: context.cache)

        let result = try await repository.replyToInquiry(
            id: 123,
            subject: "Re: your inquiry",
            body: "Thanks — I can help.",
            idempotencyKey: key
        )
        let cachedInquiries = try await context.cache.read(
            "inquiries.v1",
            as: [InquirySummary].self
        )
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        let cachedClients = try await context.cache.read("clients.v1", as: [ClientSummary].self)
        let cachedProjects = try await context.cache.read("projects.v1", as: [ProjectSummary].self)
        let requests = await client.capturedRequests()

        XCTAssertEqual(result.deliveryStatus, "sent")
        XCTAssertNil(cachedInquiries)
        XCTAssertNil(cachedDashboard)
        XCTAssertNotNil(cachedClients)
        XCTAssertNotNil(cachedProjects)
        XCTAssertEqual(requests.map(\.method), [.post])
        XCTAssertEqual(requests.map(\.path), ["/api/v1/inquiries/123/reply"])
        XCTAssertEqual(requests.first?.idempotencyKey, key)
    }

    func testUnknownReplyPreservesSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write([Self.openInquiry], key: "inquiries.v1", etag: nil)
        let client = ReplyClient([.value(Data(Self.unknownReplyJSON.utf8))])
        let repository = OwnerRepository(client: client, cache: context.cache)

        let result = try await repository.replyToInquiry(
            id: 123,
            subject: "Re: your inquiry",
            body: "Thanks — I can help.",
            idempotencyKey: UUID()
        )

        let cachedInquiries = try await context.cache.read(
            "inquiries.v1",
            as: [InquirySummary].self
        )
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertEqual(result.code, "inquiry.delivery_unknown")
        XCTAssertEqual(result.deliveryStatus, "unknown")
        XCTAssertNil(result.messageID)
        XCTAssertNotNil(cachedInquiries)
        XCTAssertNotNil(cachedDashboard)
    }

    func testMalformedSentReplyPreservesSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write([Self.openInquiry], key: "inquiries.v1", etag: nil)
        let invalid = Self.sentReplyJSON.replacingOccurrences(of: #""message_id": 51"#, with: #""message_id": null"#)
        let client = ReplyClient([.value(Data(invalid.utf8))])
        let repository = OwnerRepository(client: client, cache: context.cache)

        do {
            _ = try await repository.replyToInquiry(
                id: 123,
                subject: "Re: your inquiry",
                body: "Thanks — I can help.",
                idempotencyKey: UUID()
            )
            XCTFail("Expected a sent result without a message ID to be rejected.")
        } catch OwnerRepositoryError.invalidInquiryResponse {
            // A malformed sent receipt cannot evict confirmed data.
        }

        let cachedInquiries = try await context.cache.read(
            "inquiries.v1",
            as: [InquirySummary].self
        )
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertNotNil(cachedInquiries)
        XCTAssertNotNil(cachedDashboard)
    }

    private func makeCache() -> (root: URL, cache: TenantJSONCache) {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("inquiry-reply-tests-\(UUID().uuidString)")
        return (root, TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root))
    }

    private static let openInquiry = InquirySummary(
        id: 123,
        name: "Jordan Lee",
        business: nil,
        email: "jordan@example.com",
        phone: nil,
        kind: "wedding",
        service: "Wedding photography",
        shootOn: LocalDate(rawValue: "2026-10-03"),
        messagePreview: "Hi! We’re getting married…",
        status: .open,
        isReplied: false,
        convertedClientID: nil,
        convertedProjectID: nil,
        receivedAt: Date(timeIntervalSince1970: 1_700_000_000)
    )

    private static let dashboard = DashboardSummary(
        generatedAt: Date(timeIntervalSince1970: 1_700_000_000),
        newInquiries: 3,
        outstanding: MoneyCount(count: 0, amount: Money(minorUnits: 0, currencyCode: "USD")),
        upcomingProjects14Days: 0,
        overdueInvoiceCount: 0,
        retainerDraftCount: 0,
        tasksDueCount: 0,
        actionItemCount: 0,
        kpis: DashboardKPIs(
            inquiriesDelta7Days: 0,
            bookingsDelta7Days: 0,
            collected7Days: Money(minorUnits: 0, currencyCode: "USD")
        ),
        openTasks: [],
        upcomingShoots: [],
        openInvoices: [],
        recentActivity: []
    )

    private static let inquiryJSON = """
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
      "is_replied": true,
      "converted_client_id": null,
      "converted_project_id": null,
      "received_at": "2026-08-01T14:22:10Z"
    }
    """

    private static let sentReplyJSON = """
    {
      "code": "inquiry.reply_sent",
      "delivery_status": "sent",
      "message_id": 51,
      "detail": {
        "summary": \(inquiryJSON),
        "messages": [{
          "id": 51,
          "direction": "out",
          "channel": "email",
          "body": "Thanks — I can help.",
          "recorded_at": "2026-08-01T15:00:00Z",
          "is_truncated": false
        }],
        "has_older_messages": false
      }
    }
    """

    private static let unknownReplyJSON = """
    {
      "code": "inquiry.delivery_unknown",
      "delivery_status": "unknown",
      "message_id": null,
      "detail": {
        "summary": \(inquiryJSON),
        "messages": [],
        "has_older_messages": false
      }
    }
    """
}

private struct CapturedReplyRequest: Sendable {
    let method: HTTPMethod
    let path: String
    let body: Data?
    let idempotencyKey: UUID?
}

private actor ReplyClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data)
        case failure(APIError)
    }

    private var replies: [Reply]
    private var requests: [CapturedReplyRequest] = []

    init(_ replies: [Reply]) { self.replies = replies }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        requests.append(CapturedReplyRequest(
            method: endpoint.method,
            path: endpoint.path,
            body: endpoint.body,
            idempotencyKey: endpoint.idempotencyKey
        ))
        switch replies.removeFirst() {
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
            metadata: APIResponseMetadata(etag: nil, lastModified: nil, receivedAt: Date())
        )
    }

    func capturedRequests() -> [CapturedReplyRequest] {
        requests
    }
}
