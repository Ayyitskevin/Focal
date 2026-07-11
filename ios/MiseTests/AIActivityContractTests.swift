import XCTest
@testable import Mise

final class AIActivityContractTests: XCTestCase {
    func testRunDecodesOnlyNormalizedPrivacyBoundedFields() throws {
        let page = try MiseJSON.decoder().decode(
            APIPage<AIRun>.self,
            from: Data(
                """
                {
                  "items": [{
                    "id": 42,
                    "capability": "vision",
                    "provider": "argus",
                    "status": "provider_error",
                    "review": "human_review",
                    "latency_ms": 1430,
                    "cost_micro_usd": 12500,
                    "tokens": 912,
                    "subject": {
                      "kind": "gallery"
                    },
                    "created_at": "2026-07-11T15:30:45.123Z"
                  }],
                  "next_cursor": null,
                  "has_more": false
                }
                """.utf8
            )
        )

        let run = try XCTUnwrap(page.items.first)
        XCTAssertEqual(run.id, 42)
        XCTAssertEqual(run.capability, .vision)
        XCTAssertEqual(run.provider, .argus)
        XCTAssertEqual(run.status, .providerError)
        XCTAssertEqual(run.review, .humanReview)
        XCTAssertEqual(run.latencyMs, 1_430)
        XCTAssertEqual(run.costMicroUSD, 12_500)
        XCTAssertEqual(run.tokens, 912)
        XCTAssertEqual(run.subject?.kind, .gallery)
        XCTAssertEqual(run.subject?.title, "Gallery")
    }

    func testEndpointBoundsLimitAndCarriesFirstPageValidator() {
        let upper = MiseEndpoints.AI.runs(
            cursor: "opaque",
            limit: 900,
            etag: #""ai-runs-v1""#
        )
        XCTAssertEqual(upper.method, .get)
        XCTAssertEqual(upper.path, "/api/v1/ai/runs")
        XCTAssertEqual(
            upper.queryItems,
            [
                APIQueryItem(name: "cursor", value: "opaque"),
                APIQueryItem(name: "limit", value: "100"),
            ]
        )
        XCTAssertEqual(upper.etag, #""ai-runs-v1""#)

        let lower = MiseEndpoints.AI.runs(limit: 0)
        XCTAssertEqual(lower.queryItems.last?.value, "1")
    }

    func testAttentionSemanticsAndOwnerNavigationEntry() {
        XCTAssertFalse(Self.run(id: 1, review: .none).needsAttention)
        XCTAssertTrue(Self.run(id: 2, review: .humanReview).needsAttention)
        XCTAssertTrue(
            Self.run(id: 3, status: .providerError, review: .none).needsAttention
        )
        XCTAssertEqual(OwnerDestination.allCases.last, .ai)
        XCTAssertEqual(OwnerDestination.ai.title, "AI activity")
        XCTAssertEqual(OwnerDestination.ai.icon, "sparkles")
    }

    func testRepositoryRevalidatesTheCompleteCachedFeedFromFirstPageETag() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldDate = Date(timeIntervalSince1970: 1_700_000_000)
        let oldFeed = AIActivityFeed(runs: [Self.run(id: 42)], hasOlderRuns: false)
        try await fixture.cache.write(
            oldFeed,
            key: "ai-activity.v1",
            etag: #""ai-runs-v1""#,
            storedAt: oldDate
        )
        let client = AIActivityQueuedClient(replies: [
            .failure(.notModified(etag: #""ai-runs-v1""#)),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        let snapshot = try await repository.refreshAIActivity()

        XCTAssertEqual(snapshot.source, .revalidated)
        XCTAssertEqual(snapshot.value, oldFeed)
        XCTAssertGreaterThan(snapshot.storedAt, oldDate)
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests[0].etag, #""ai-runs-v1""#)
        XCTAssertEqual(requests[0].queryItems.last?.value, "100")
    }

    func testRepositoryRejectsMismatchedFirstPageValidator() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldFeed = AIActivityFeed(runs: [Self.run(id: 42)], hasOlderRuns: false)
        try await fixture.cache.write(
            oldFeed,
            key: "ai-activity.v1",
            etag: #""ai-runs-v1""#
        )
        let client = AIActivityQueuedClient(replies: [
            .failure(.notModified(etag: #""ai-runs-different""#)),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshAIActivity()
            XCTFail("Expected a mismatched 304 validator to be rejected.")
        } catch APIError.unexpectedResponse {
            // The server must echo the exact validator sent for page one.
        }

        let cached = try await fixture.cache.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertEqual(cached?.value, oldFeed)
        XCTAssertEqual(cached?.etag, #""ai-runs-v1""#)
    }

    func testRepositoryRejectsNotModifiedFromAContinuationPage() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldFeed = AIActivityFeed(runs: [Self.run(id: 50)], hasOlderRuns: false)
        try await fixture.cache.write(oldFeed, key: "ai-activity.v1", etag: #""old""#)
        let client = AIActivityQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(Self.page(id: 100, nextCursor: "next")),
                Self.metadata(etag: #""new""#)
            ),
            .failure(.notModified(etag: #""continuation""#)),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshAIActivity()
            XCTFail("Expected a continuation 304 to be rejected.")
        } catch APIError.unexpectedResponse {
            // Only page one is conditionally requested.
        }

        let cached = try await fixture.cache.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertEqual(cached?.value, oldFeed)
        XCTAssertEqual(cached?.etag, #""old""#)
    }

    func testRepositoryStopsAfterFivePagesAndMarksOlderRuns() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let pages = [
            Self.page(id: 500, nextCursor: "cursor-1"),
            Self.page(id: 499, nextCursor: "cursor-2"),
            Self.page(id: 498, nextCursor: "cursor-3"),
            Self.page(id: 497, nextCursor: "cursor-4"),
            Self.page(id: 496, nextCursor: "cursor-5"),
        ]
        let client = AIActivityQueuedClient(replies: try pages.enumerated().map { index, page in
            .value(
                MiseJSON.encoder().encode(page),
                Self.metadata(etag: index == 0 ? #""ai-runs-v2""# : nil)
            )
        })
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        let snapshot = try await repository.refreshAIActivity()

        XCTAssertEqual(snapshot.source, .network)
        XCTAssertEqual(snapshot.value.runs.map(\.id), [500, 499, 498, 497, 496])
        XCTAssertTrue(snapshot.value.hasOlderRuns)
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 5)
        XCTAssertEqual(requests.map(\.etag), [nil, nil, nil, nil, nil])
        XCTAssertEqual(requests[1].queryItems.first?.value, "cursor-1")
        let cached = try await fixture.cache.read(
            "ai-activity.v1",
            as: AIActivityFeed.self
        )
        XCTAssertEqual(cached?.value, snapshot.value)
        XCTAssertEqual(cached?.etag, #""ai-runs-v2""#)
    }

    func testRepositoryRejectsRepeatedCursorWithoutReplacingCache() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldFeed = AIActivityFeed(runs: [Self.run(id: 20)], hasOlderRuns: false)
        try await fixture.cache.write(oldFeed, key: "ai-activity.v1", etag: #""old""#)
        let client = AIActivityQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(Self.page(id: 100, nextCursor: "repeat")),
                Self.metadata(etag: #""new""#)
            ),
            .value(
                try MiseJSON.encoder().encode(Self.page(id: 99, nextCursor: "repeat")),
                Self.metadata()
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshAIActivity()
            XCTFail("Expected the repeated continuation cursor to be rejected.")
        } catch OwnerRepositoryError.invalidPagination {
            // Expected before the newly assembled feed is persisted.
        }

        let cached = try await fixture.cache.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertEqual(cached?.value, oldFeed)
        XCTAssertEqual(cached?.etag, #""old""#)
    }

    func testRepositoryRejectsDuplicateAndOutOfOrderRunIDs() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let first = APIPage(
            items: [Self.run(id: 100), Self.run(id: 99)],
            nextCursor: "next",
            hasMore: true
        )
        let duplicate = APIPage(
            items: [Self.run(id: 99)],
            nextCursor: nil,
            hasMore: false
        )
        let client = AIActivityQueuedClient(replies: [
            .value(try MiseJSON.encoder().encode(first), Self.metadata(etag: #""new""#)),
            .value(try MiseJSON.encoder().encode(duplicate), Self.metadata()),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshAIActivity()
            XCTFail("Expected duplicate run IDs to be rejected.")
        } catch APIError.unexpectedResponse {
            // Expected.
        }

        let cached = try await fixture.cache.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertNil(cached)
    }

    func testMidRefreshFailureLeavesPreviousFeedAndValidatorUntouched() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldFeed = AIActivityFeed(runs: [Self.run(id: 50)], hasOlderRuns: false)
        try await fixture.cache.write(oldFeed, key: "ai-activity.v1", etag: #""old""#)
        let client = AIActivityQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(Self.page(id: 100, nextCursor: "next")),
                Self.metadata(etag: #""new""#)
            ),
            .failure(.transport(.timedOut)),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshAIActivity()
            XCTFail("Expected the continuation request to fail.")
        } catch let APIError.transport(code) {
            XCTAssertEqual(code, .timedOut)
        }

        let cached = try await fixture.cache.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertEqual(cached?.value, oldFeed)
        XCTAssertEqual(cached?.etag, #""old""#)
        let requests = await client.requests()
        XCTAssertEqual(requests.first?.etag, #""old""#)
        XCTAssertNil(requests.last?.etag)
    }

    func testRepositoryRejectsUnsafeMetricsAndSubjectShape() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let invalid = AIRun(
            id: 42,
            capability: .vision,
            provider: .argus,
            status: .ok,
            review: .none,
            latencyMs: -1,
            costMicroUSD: 1,
            tokens: 2,
            subject: AIActivitySubject(
                kind: AIActivitySubjectKind(rawValue: "project")
            ),
            createdAt: Date(timeIntervalSince1970: 1_720_000_000)
        )
        let page = APIPage(items: [invalid], nextCursor: nil, hasMore: false)
        let client = AIActivityQueuedClient(replies: [
            .value(try MiseJSON.encoder().encode(page), Self.metadata(etag: #""invalid""#)),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshAIActivity()
            XCTFail("Expected invalid normalized run metadata to be rejected.")
        } catch APIError.unexpectedResponse {
            // Expected.
        }

        let cached = try await fixture.cache.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertNil(cached)
    }

    func testRepositoryRejectsMissingOrWeakFirstPageValidatorWithoutCaching() async throws {
        let invalidValidators: [String?] = [nil, #"W/"weak""#]

        for validator in invalidValidators {
            let fixture = try Fixture()
            let page = APIPage(
                items: [Self.run(id: 42)],
                nextCursor: nil,
                hasMore: false
            )
            let client = AIActivityQueuedClient(replies: [
                .value(
                    try MiseJSON.encoder().encode(page),
                    Self.metadata(etag: validator)
                ),
            ])
            let repository = OwnerRepository(client: client, cache: fixture.cache)

            do {
                _ = try await repository.refreshAIActivity()
                XCTFail("Expected a strong first-page ETag.")
            } catch APIError.unexpectedResponse {
                // Expected before assembling or persisting the feed.
            }

            let cached = try await fixture.cache.read(
                "ai-activity.v1",
                as: AIActivityFeed.self
            )
            XCTAssertNil(cached)
            fixture.remove()
        }
    }

    func testTerminalOwnerAuthenticationFailurePurgesCacheAndEndsSession() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let feed = AIActivityFeed(runs: [Self.run(id: 42)], hasOlderRuns: false)
        try await fixture.cache.write(feed, key: "ai-activity.v1", etag: #""old""#)
        try await fixture.cache.write([7], key: "clients.v1", etag: nil)
        let endProbe = AIActivitySessionEndProbe()
        let client = AIActivityQueuedClient(replies: [
            .failure(.unauthenticated(nil)),
        ])
        let repository = OwnerRepository(
            client: client,
            cache: fixture.cache,
            onSessionEnded: { await endProbe.record() }
        )

        do {
            _ = try await repository.refreshAIActivity()
            XCTFail("Expected terminal owner authentication failure.")
        } catch APIError.unauthenticated(_) {
            // Expected after all tenant-private cache entries are removed.
        }

        let cachedFeed = try await fixture.cache.read(
            "ai-activity.v1",
            as: AIActivityFeed.self
        )
        let cachedClients = try await fixture.cache.read("clients.v1", as: [Int].self)
        let didEnd = await endProbe.didEnd()
        XCTAssertNil(cachedFeed)
        XCTAssertNil(cachedClients)
        XCTAssertTrue(didEnd)
    }

    func testPurgeWinsOverAResponseThatFinishesAfterLogout() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldFeed = AIActivityFeed(runs: [Self.run(id: 10)], hasOlderRuns: false)
        try await fixture.cache.write(oldFeed, key: "ai-activity.v1", etag: #""old""#)
        let client = AIActivityBlockingClient(
            data: try MiseJSON.encoder().encode(
                APIPage(items: [Self.run(id: 42)], nextCursor: nil, hasMore: false)
            ),
            metadata: Self.metadata(etag: #""new""#)
        )
        let lifetime = ClientDeliveryLifetime()
        let repository = OwnerRepository(
            client: client,
            cache: fixture.cache,
            lifetime: lifetime
        )
        let refresh = Task { try await repository.refreshAIActivity() }
        await client.waitUntilStarted()

        await repository.purgeCache()
        await client.release()

        do {
            _ = try await refresh.value
            XCTFail("Expected the ended owner lifetime to reject the late response.")
        } catch APIError.unauthenticated(_) {
            // A response from the ended session cannot recreate private cache data.
        }
        let cached = try await fixture.cache.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertNil(cached)
    }

    func testEndedCacheActorCannotBeRecreatedByARacingOrLateWrite() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldFeed = AIActivityFeed(runs: [Self.run(id: 10)], hasOlderRuns: false)
        let newFeed = AIActivityFeed(runs: [Self.run(id: 20)], hasOlderRuns: false)
        try await fixture.cache.write(oldFeed, key: "ai-activity.v1", etag: #""old""#)

        let racingWrite = Task {
            try? await fixture.cache.write(
                newFeed,
                key: "ai-activity.v1",
                etag: #""new""#
            )
        }
        let purge = Task { try? await fixture.cache.endAccessAndRemoveAll() }
        _ = await racingWrite.value
        _ = await purge.value

        do {
            _ = try await fixture.cache.write(
                newFeed,
                key: "ai-activity.v1",
                etag: #""late""#
            )
            XCTFail("Expected an ended cache actor to reject every later write.")
        } catch TenantCacheAccessError.ended {
            // The actor's ended state and filesystem removal are one serialized operation.
        }

        let verifier = TenantJSONCache(
            cacheNamespace: "tenant-ai",
            rootDirectory: fixture.root
        )
        let cached = try await verifier.read("ai-activity.v1", as: AIActivityFeed.self)
        XCTAssertNil(cached)
    }

    func testEndedCacheActorCannotDeleteANewerSessionCache() async throws {
        let fixture = try Fixture()
        defer { fixture.remove() }
        let oldSession = fixture.cache
        try await oldSession.write(
            AIActivityFeed(runs: [Self.run(id: 10)], hasOlderRuns: false),
            key: "ai-activity.v1",
            etag: #""old""#
        )
        try await oldSession.endAccessAndRemoveAll()

        let newSession = TenantJSONCache(
            cacheNamespace: "tenant-ai",
            rootDirectory: fixture.root
        )
        let newFeed = AIActivityFeed(runs: [Self.run(id: 20)], hasOlderRuns: false)
        try await newSession.write(
            newFeed,
            key: "ai-activity.v1",
            etag: #""new""#
        )

        try await oldSession.endAccessAndRemoveAll()
        try await oldSession.remove("ai-activity.v1")
        try await oldSession.removeAll()

        let cached = try await newSession.read(
            "ai-activity.v1",
            as: AIActivityFeed.self
        )
        XCTAssertEqual(cached?.value, newFeed)
        XCTAssertEqual(cached?.etag, #""new""#)
    }

    private static func run(
        id: Int64,
        status: AIRunStatus = .ok,
        review: AIReviewRequirement = .humanReview
    ) -> AIRun {
        AIRun(
            id: id,
            capability: .vision,
            provider: .argus,
            status: status,
            review: review,
            latencyMs: 850,
            costMicroUSD: 5_000,
            tokens: 300,
            subject: AIActivitySubject(
                kind: .gallery
            ),
            createdAt: Date(timeIntervalSince1970: 1_720_000_000 + Double(id))
        )
    }

    private static func page(id: Int64, nextCursor: String?) -> APIPage<AIRun> {
        APIPage(
            items: [run(id: id)],
            nextCursor: nextCursor,
            hasMore: nextCursor != nil
        )
    }

    private static func metadata(etag: String? = nil) -> APIResponseMetadata {
        APIResponseMetadata(etag: etag, lastModified: nil, receivedAt: Date())
    }
}

private struct Fixture {
    let root: URL
    let cache: TenantJSONCache

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        cache = TenantJSONCache(cacheNamespace: "tenant-ai", rootDirectory: root)
    }

    func remove() {
        try? FileManager.default.removeItem(at: root)
    }
}

private actor AIActivityQueuedClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data, APIResponseMetadata)
        case failure(APIError)
    }

    struct Request: Sendable {
        let path: String
        let queryItems: [APIQueryItem]
        let etag: String?
    }

    private var replies: [Reply]
    private var captured: [Request] = []

    init(replies: [Reply]) {
        self.replies = replies
    }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        captured.append(Request(
            path: endpoint.path,
            queryItems: endpoint.queryItems,
            etag: endpoint.etag
        ))
        guard !replies.isEmpty else { throw APIError.unexpectedResponse }
        switch replies.removeFirst() {
        case let .value(data, metadata):
            return APIResponse(
                value: try MiseJSON.decoder().decode(Response.self, from: data),
                metadata: metadata
            )
        case let .failure(error):
            throw error
        }
    }

    func requests() -> [Request] {
        captured
    }
}

private actor AIActivityBlockingClient: APIClientProtocol {
    private let data: Data
    private let metadata: APIResponseMetadata
    private var started = false
    private var gate: CheckedContinuation<Void, Never>?

    init(data: Data, metadata: APIResponseMetadata) {
        self.data = data
        self.metadata = metadata
    }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        started = true
        await withCheckedContinuation { continuation in
            gate = continuation
        }
        return APIResponse(
            value: try MiseJSON.decoder().decode(Response.self, from: data),
            metadata: metadata
        )
    }

    func waitUntilStarted() async {
        while !started {
            await Task.yield()
        }
    }

    func release() {
        gate?.resume()
        gate = nil
    }
}

private actor AIActivitySessionEndProbe {
    private var ended = false

    func record() {
        ended = true
    }

    func didEnd() -> Bool {
        ended
    }
}
