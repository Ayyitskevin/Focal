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
        let client = ReplyClient([.value(Data(Self.sentReplyJSON.utf8))])
        let repository = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let observed = try await repository.inquiryReplySnapshot(id: 123)
        let draft = try await repository.prepareInquiryReply(
            id: 123,
            subject: "Re: your inquiry",
            body: "Thanks — I can help.",
            observing: observed
        )

        let result = try await repository.replyToInquiry(draft: draft)
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
        XCTAssertEqual(requests.first?.idempotencyKey, draft.idempotencyKey)
        let pending = try await repository.pendingInquiryReply(id: 123)
        XCTAssertNil(pending)
        try await assertDraftConflict {
            _ = try await repository.prepareInquiryReply(
                id: 123, subject: draft.subject, body: draft.body, observing: observed.withPending(draft)
            )
        }
        let afterStaleEdit = try await repository.pendingInquiryReply(id: 123)
        XCTAssertNil(afterStaleEdit)
    }

    func testUnknownReplyPreservesSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write([Self.openInquiry], key: "inquiries.v1", etag: nil)
        let client = ReplyClient([
            .value(Data(Self.unknownReplyJSON.utf8)), .value(Data(Self.sentReplyJSON.utf8)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let observed = try await repository.inquiryReplySnapshot(id: 123)
        let draft = try await repository.prepareInquiryReply(
            id: 123,
            subject: "Re: your inquiry",
            body: "Thanks — I can help.",
            observing: observed
        )

        let result = try await repository.replyToInquiry(draft: draft)

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
        try await assertRestoredRetry(root: context.root, client: client, draft: draft)
    }

    func testTransportFailureRestoresExactReplyForExplicitRetry() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let client = ReplyClient([
            .failure(.transport(.timedOut)), .value(Data(Self.sentReplyJSON.utf8)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let observed = try await repository.inquiryReplySnapshot(id: 123)
        let draft = try await repository.prepareInquiryReply(
            id: 123, subject: "Re: your inquiry", body: "Thanks — I can help.", observing: observed
        )

        do {
            _ = try await repository.replyToInquiry(draft: draft)
            XCTFail("Expected the transport timeout.")
        } catch APIError.transport(let code) {
            XCTAssertEqual(code, .timedOut)
        }
        try await assertRestoredRetry(root: context.root, client: client, draft: draft)
    }

    private func assertRestoredRetry(root: URL, client: ReplyClient, draft: InquiryReplyDraft) async throws {
        let reopened = reopenedRepository(root: root, client: client)
        let restored = try await reopened.inquiryReplySnapshot(id: draft.inquiryID)
        XCTAssertEqual(restored.draft, draft)
        let requestsBeforeRetry = await client.capturedRequests()
        XCTAssertEqual(requestsBeforeRetry.count, 1)

        let retry = try await reopened.prepareInquiryReply(
            id: draft.inquiryID, subject: " " + draft.subject, body: draft.body + "\n",
            observing: restored
        )
        XCTAssertEqual(retry, draft)
        let result = try await reopened.replyToInquiry(draft: retry)
        XCTAssertEqual(result.code, "inquiry.reply_sent")
        let requests = await client.capturedRequests()
        XCTAssertEqual(requests.map(\.idempotencyKey), [draft.idempotencyKey, draft.idempotencyKey])
        for request in requests {
            let data = try XCTUnwrap(request.body)
            let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: String])
            XCTAssertEqual(object, ["channel": "email", "subject": draft.subject, "body": draft.body])
        }
        let pending = try await reopened.pendingInquiryReply(id: draft.inquiryID)
        XCTAssertNil(pending)
    }

    func testIndependentComposersCanShareOnlyAnIdenticalFirstAttempt() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let client = ReplyClient([])
        let first = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let other = reopenedRepository(root: context.root, client: client)

        // Both composers opened before either owner action saved a draft.
        let firstView = try await first.inquiryReplySnapshot(id: 123)
        let otherView = try await other.inquiryReplySnapshot(id: 123)
        XCTAssertNil(firstView.draft)
        XCTAssertNil(otherView.draft)
        let original = try await first.prepareInquiryReply(
            id: 123, subject: "One", body: "My draft", observing: firstView
        )
        try await assertDraftConflict {
            _ = try await other.prepareInquiryReply(id: 123, subject: "Two", body: "Different", observing: otherView)
        }
        let exact = try await other.prepareInquiryReply(id: 123, subject: " One ", body: "My draft", observing: otherView)
        XCTAssertEqual(exact, original)
        let saved = try await first.pendingInquiryReply(id: 123)
        XCTAssertEqual(saved, original)
        let requests = await client.capturedRequests()
        XCTAssertTrue(requests.isEmpty)
    }

    func testStaleEmptyComposerCannotStartANewEmailAfterAnotherComposerSends() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let sent = ReplyClient.Reply.value(Data(Self.sentReplyJSON.utf8))
        let client = ReplyClient([sent, sent])
        let first = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let other = reopenedRepository(root: context.root, client: client)
        let firstView = try await first.inquiryReplySnapshot(id: 123)
        let staleView = try await other.inquiryReplySnapshot(id: 123)
        XCTAssertNil(firstView.draft)
        XCTAssertNil(staleView.draft)

        let original = try await first.prepareInquiryReply(
            id: 123, subject: "Re: your inquiry", body: "Thanks — I can help.", observing: firstView
        )
        _ = try await first.replyToInquiry(draft: original)
        let reopened = reopenedRepository(root: context.root, client: client)
        let afterSend = try await reopened.pendingInquiryReply(id: 123)
        XCTAssertNil(afterSend)

        try await assertDraftConflict {
            _ = try await other.prepareInquiryReply(
                id: 123, subject: original.subject, body: original.body, observing: staleView
            )
        }
        try await assertDraftConflict {
            _ = try await reopened.prepareInquiryReply(
                id: 123, subject: original.subject, body: "Different email", observing: staleView
            )
        }
        let requestsBeforeReopening = await client.capturedRequests()
        XCTAssertEqual(requestsBeforeReopening.map(\.idempotencyKey), [original.idempotencyKey])

        let freshView = try await reopened.inquiryReplySnapshot(id: 123)
        XCTAssertNil(freshView.draft)
        let fresh = try await reopened.prepareInquiryReply(
            id: 123, subject: original.subject, body: original.body, observing: freshView
        )
        XCTAssertNotEqual(fresh.idempotencyKey, original.idempotencyKey)
        try await assertDraftConflict {
            _ = try await other.prepareInquiryReply(
                id: 123, subject: fresh.subject, body: fresh.body, observing: staleView
            )
        }
        _ = try await reopened.replyToInquiry(draft: fresh)
        let allRequests = await client.capturedRequests()
        XCTAssertEqual(allRequests.map(\.idempotencyKey), [original.idempotencyKey, fresh.idempotencyKey])
    }

    func testExistingV1PendingReplyKeepsItsKeyAndText() async throws {
        struct LegacyDraft: Codable, Sendable {
            let sessionID: String
            let inquiryID: Int64
            let subject: String
            let body: String
            let idempotencyKey: UUID
        }
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let legacy = LegacyDraft(
            sessionID: "session-a", inquiryID: 123, subject: "Legacy subject", body: "Original email",
            idempotencyKey: UUID()
        )
        try await context.cache.writeCommandIfAbsent(legacy, key: "inquiry.reply.pending.v1.session-a.123", etag: nil)
        let client = ReplyClient([.value(Data(Self.sentReplyJSON.utf8))])
        let reopened = reopenedRepository(root: context.root, client: client)
        let recovered = try await reopened.inquiryReplySnapshot(id: 123)
        let saved = try XCTUnwrap(recovered.draft)
        XCTAssertEqual(saved.sessionID, legacy.sessionID)
        XCTAssertEqual(saved.inquiryID, legacy.inquiryID)
        XCTAssertEqual(saved.subject, legacy.subject)
        XCTAssertEqual(saved.body, legacy.body)
        XCTAssertEqual(saved.idempotencyKey, legacy.idempotencyKey)

        let retry = try await reopened.prepareInquiryReply(
            id: legacy.inquiryID, subject: legacy.subject, body: legacy.body, observing: recovered
        )
        _ = try await reopened.replyToInquiry(draft: retry)
        let requests = await client.capturedRequests()
        XCTAssertEqual(requests.map(\.idempotencyKey), [legacy.idempotencyKey])
        let next = reopenedRepository(root: context.root, client: client)
        let afterRecovery = try await next.pendingInquiryReply(id: legacy.inquiryID)
        XCTAssertNil(afterRecovery)
    }

    func testTerminalPurgeRemovesOnlyItsSessionsUnresolvedReplies() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let client = ReplyClient([])
        let first = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let second = reopenedRepository(root: context.root, client: client, sessionID: "session-b")
        let firstView = try await first.inquiryReplySnapshot(id: 123)
        let secondView = try await second.inquiryReplySnapshot(id: 123)
        let draftA = try await first.prepareInquiryReply(
            id: 123, subject: "First session", body: "First private reply", observing: firstView
        )
        let draftB = try await second.prepareInquiryReply(
            id: 123, subject: "Second session", body: "Second private reply", observing: secondView
        )

        let reopenedA = reopenedRepository(root: context.root, client: client)
        let restored = try await reopenedA.pendingInquiryReply(id: 123)
        XCTAssertEqual(restored, draftA)
        XCTAssertTrue(try cacheContains(draftA.body, at: context.root))
        await reopenedA.purgeCache()

        let afterPurgeA = reopenedRepository(root: context.root, client: client)
        let afterPurgeB = reopenedRepository(root: context.root, client: client, sessionID: "session-b")
        let removed = try await afterPurgeA.pendingInquiryReply(id: 123)
        let retained = try await afterPurgeB.pendingInquiryReply(id: 123)
        XCTAssertNil(removed)
        XCTAssertEqual(retained, draftB)
        XCTAssertFalse(try cacheContains(draftA.body, at: context.root))
    }

    private func cacheContains(_ text: String, at root: URL) throws -> Bool {
        let paths = try FileManager.default.subpathsOfDirectory(atPath: root.path)
        for path in paths where path.hasSuffix(".json") {
            let data = try Data(contentsOf: root.appendingPathComponent(path))
            if data.range(of: Data(text.utf8)) != nil { return true }
        }
        return false
    }

    func testEditedDraftReplacesOnlyTheConfirmedAttempt() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let client = ReplyClient([])
        let repository = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let empty = try await repository.inquiryReplySnapshot(id: 123)
        let original = try await repository.prepareInquiryReply(id: 123, subject: "Same", body: "Draft", observing: empty)
        let originalView = empty.withPending(original)
        let edited = try await repository.prepareInquiryReply(
            id: 123, subject: "Same", body: "Edited", observing: originalView
        )

        XCTAssertNotEqual(original.idempotencyKey, edited.idempotencyKey)
        XCTAssertEqual(edited.body, "Edited")
        try await assertDraftConflict {
            _ = try await repository.prepareInquiryReply(
                id: 123, subject: "Same", body: "Stale edit", observing: originalView
            )
        }
        try await assertDraftConflict {
            _ = try await repository.replyToInquiry(draft: original)
        }
        let removedOriginal = try await repository.discardPendingInquiryReply(ifMatches: original)
        XCTAssertFalse(removedOriginal)
        let saved = try await repository.pendingInquiryReply(id: 123)
        XCTAssertEqual(saved, edited)
        let removedEdited = try await repository.discardPendingInquiryReply(ifMatches: edited)
        XCTAssertTrue(removedEdited)
        try await assertDraftConflict {
            _ = try await repository.prepareInquiryReply(
                id: 123, subject: "Same", body: "After discard", observing: originalView.withPending(edited)
            )
        }
        let pending = try await repository.pendingInquiryReply(id: 123)
        XCTAssertNil(pending)
        let requests = await client.capturedRequests()
        XCTAssertTrue(requests.isEmpty)
    }

    private func assertDraftConflict(
        _ operation: () async throws -> Void,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async throws {
        do {
            try await operation()
            XCTFail("Expected the stale or competing draft to be rejected.", file: file, line: line)
        } catch OwnerRepositoryError.pendingInquiryReplyConflict {
            // No network request is permitted on a journal conflict.
        }
    }

    func testMalformedSentReplyPreservesSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write([Self.openInquiry], key: "inquiries.v1", etag: nil)
        let invalid = Self.sentReplyJSON.replacingOccurrences(of: #""message_id": 51"#, with: #""message_id": null"#)
        let client = ReplyClient([.value(Data(invalid.utf8))])
        let repository = OwnerRepository(client: client, cache: context.cache, sessionID: "session-a")
        let observed = try await repository.inquiryReplySnapshot(id: 123)
        let draft = try await repository.prepareInquiryReply(
            id: 123,
            subject: "Re: your inquiry",
            body: "Thanks — I can help.",
            observing: observed
        )

        do {
            _ = try await repository.replyToInquiry(draft: draft)
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

    private func reopenedRepository(root: URL, client: ReplyClient, sessionID: String = "session-a") -> OwnerRepository {
        let cache = TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root)
        return OwnerRepository(client: client, cache: cache, sessionID: sessionID)
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
        guard !replies.isEmpty else {
            XCTFail("Unexpected request to the reply transport.")
            throw APIError.transport(.badServerResponse)
        }
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
