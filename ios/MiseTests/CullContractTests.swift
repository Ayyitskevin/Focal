import XCTest
@testable import Mise

final class CullContractTests: XCTestCase {
    func testCullPageDecodesSnakeCaseURLsScoresCountsAndVersion() throws {
        let page = try MiseJSON.decoder().decode(
            CullPage.self,
            from: Data(
                """
                {
                  "items": [{
                    "asset_id": 41,
                    "gallery_id": 7,
                    "filename": "frame.jpg",
                    "position": 2,
                    "keeper_score": 0.82,
                    "hero_potential": 0.44,
                    "state": "cut",
                    "thumbnail_url": "https://studio.example.com/api/v1/galleries/7/cull/assets/41/thumbnail",
                    "preview_url": "https://studio.example.com/api/v1/galleries/7/cull/assets/41/preview",
                    "media_revision": 73,
                    "etag": "\\"cull-asset-11111111111111111111111111111111\\""
                  }],
                  "next_cursor": "opaque",
                  "has_more": true,
                  "counts": {
                    "total": 9,
                    "keep": 2,
                    "cut": 1,
                    "undecided": 6,
                    "scored": 8
                  }
                }
                """.utf8
            )
        )

        XCTAssertEqual(page.items.first?.assetID, 41)
        XCTAssertEqual(page.items.first?.galleryID, 7)
        XCTAssertEqual(page.items.first?.keeperScore, 0.82)
        XCTAssertEqual(page.items.first?.state, .cut)
        XCTAssertEqual(page.items.first?.thumbnailURL?.host, "studio.example.com")
        XCTAssertEqual(page.items.first?.mediaRevision, 73)
        XCTAssertEqual(
            page.items.first?.etag,
            #""cull-asset-11111111111111111111111111111111""#
        )
        XCTAssertEqual(page.counts.undecided, 6)
        XCTAssertTrue(page.hasMore)
    }

    func testCullEndpointsBoundPaginationAndEncodeExplicitDecision() throws {
        let page = MiseEndpoints.Galleries.cull(
            galleryID: 7,
            cursor: "opaque",
            limit: 900,
            etag: #""page-v2""#
        )
        XCTAssertEqual(page.method, .get)
        XCTAssertEqual(page.path, "/api/v1/galleries/7/cull")
        XCTAssertEqual(
            page.queryItems,
            [
                APIQueryItem(name: "cursor", value: "opaque"),
                APIQueryItem(name: "limit", value: "100"),
            ]
        )
        XCTAssertEqual(page.etag, #""page-v2""#)

        let key = UUID(uuidString: "C480CE8E-4322-472A-8C4B-9D2E635B734B")!
        let decision = try MiseEndpoints.Galleries.decideCull(
            galleryID: 7,
            assetID: 41,
            action: .restore,
            etag: #""cull-asset-11111111111111111111111111111111""#,
            idempotencyKey: key
        )
        XCTAssertEqual(decision.method, .patch)
        XCTAssertEqual(decision.path, "/api/v1/galleries/7/assets/41/cull")
        XCTAssertEqual(
            decision.headers["If-Match"],
            #""cull-asset-11111111111111111111111111111111""#
        )
        XCTAssertEqual(decision.idempotencyKey, key)
        XCTAssertEqual(
            try MiseJSON.decoder().decode(
                CullDecisionRequest.self,
                from: XCTUnwrap(decision.body)
            ),
            CullDecisionRequest(action: .restore)
        )
    }

    func testRepositoryRevalidatesCachedFirstPage() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        let oldDate = Date(timeIntervalSince1970: 1_700_000_000)
        try await cache.write(
            Self.page,
            key: "gallery.7.cull.v1",
            etag: #""cull-page-v1""#,
            storedAt: oldDate
        )
        let client = CullQueuedClient(replies: [
            .failure(.notModified(etag: #""cull-page-v1""#)),
        ])
        let repository = OwnerRepository(client: client, cache: cache)

        let snapshot = try await repository.refreshCullPage(galleryID: 7)

        XCTAssertEqual(snapshot.source, .revalidated)
        XCTAssertEqual(snapshot.value.items.first?.assetID, 41)
        XCTAssertGreaterThan(snapshot.storedAt, oldDate)
        let requests = await client.requests()
        XCTAssertEqual(requests.first?.etag, #""cull-page-v1""#)
        XCTAssertEqual(requests.first?.queryItems.last?.value, "50")
    }

    func testRepositoryRejectsOverflowingCullCountsWithoutTrapping() async throws {
        let page = CullPage(
            items: [],
            nextCursor: nil,
            hasMore: false,
            counts: CullCounts(
                total: Int.max,
                keep: Int.max,
                cut: Int.max,
                undecided: 0,
                scored: 0
            )
        )

        try await assertRepositoryRejects(page)
    }

    func testRepositoryRejectsMalformedItemIdentityVersionAndCursor() async throws {
        let valid = Self.page.items[0]
        let wrongMedia = CullItem(
            assetID: valid.assetID,
            galleryID: valid.galleryID,
            filename: valid.filename,
            position: valid.position,
            keeperScore: valid.keeperScore,
            heroPotential: valid.heroPotential,
            state: valid.state,
            thumbnailURL: URL(
                string: "https://studio.example.com/api/v1/galleries/7/cull/assets/99/thumbnail"
            ),
            previewURL: valid.previewURL,
            mediaRevision: valid.mediaRevision,
            etag: valid.etag
        )
        try await assertRepositoryRejects(CullPage(
            items: [wrongMedia],
            nextCursor: nil,
            hasMore: false,
            counts: CullCounts(total: 1, keep: 0, cut: 0, undecided: 1, scored: 1)
        ))

        let weakVersion = CullItem(
            assetID: valid.assetID,
            galleryID: valid.galleryID,
            filename: valid.filename,
            position: valid.position,
            keeperScore: valid.keeperScore,
            heroPotential: valid.heroPotential,
            state: valid.state,
            thumbnailURL: valid.thumbnailURL,
            previewURL: valid.previewURL,
            mediaRevision: valid.mediaRevision,
            etag: "W/\"cull-asset-11111111111111111111111111111111\""
        )
        try await assertRepositoryRejects(CullPage(
            items: [weakVersion],
            nextCursor: nil,
            hasMore: false,
            counts: CullCounts(total: 1, keep: 0, cut: 0, undecided: 1, scored: 1)
        ))

        let negativeMediaRevision = CullItem(
            assetID: valid.assetID,
            galleryID: valid.galleryID,
            filename: valid.filename,
            position: valid.position,
            keeperScore: valid.keeperScore,
            heroPotential: valid.heroPotential,
            state: valid.state,
            thumbnailURL: valid.thumbnailURL,
            previewURL: valid.previewURL,
            mediaRevision: -1,
            etag: valid.etag
        )
        try await assertRepositoryRejects(CullPage(
            items: [negativeMediaRevision],
            nextCursor: nil,
            hasMore: false,
            counts: CullCounts(total: 1, keep: 0, cut: 0, undecided: 1, scored: 1)
        ))

        try await assertRepositoryRejects(CullPage(
            items: [],
            nextCursor: "",
            hasMore: true,
            counts: CullCounts(total: 0, keep: 0, cut: 0, undecided: 0, scored: 0)
        ))
    }

    func testDecisionChecksResponseVersionAndInvalidatesAffectedSnapshots() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        try await cache.write(Self.page, key: "gallery.7.cull.v1", etag: #""page""#)
        try await cache.write([1], key: "gallery.7.v1", etag: nil)
        try await cache.write([2], key: "galleries.v1", etag: nil)

        let updated = Self.item(
            state: .cut,
            etag: #""cull-asset-22222222222222222222222222222222""#
        )
        let client = CullQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(updated),
                APIResponseMetadata(
                    etag: updated.etag,
                    lastModified: nil,
                    receivedAt: Date()
                )
            ),
        ])
        let repository = OwnerRepository(client: client, cache: cache)
        let key = UUID(uuidString: "13A2EA3D-171C-4C81-81E1-C93F3738613B")!

        let result = try await repository.decideCull(
            galleryID: 7,
            item: Self.page.items[0],
            action: .cut,
            idempotencyKey: key
        )

        XCTAssertEqual(result.state, .cut)
        let requests = await client.requests()
        XCTAssertEqual(requests.first?.headers["If-Match"], Self.page.items[0].etag)
        XCTAssertEqual(requests.first?.idempotencyKey, key)
        let cachedCull = try await cache.read("gallery.7.cull.v1", as: CullPage.self)
        let cachedGallery = try await cache.read("gallery.7.v1", as: [Int].self)
        let cachedGalleries = try await cache.read("galleries.v1", as: [Int].self)
        XCTAssertNil(cachedCull)
        XCTAssertNil(cachedGallery)
        XCTAssertNil(cachedGalleries)
    }

    func testDecisionRejectsMismatchedHTTPVersionWithoutInvalidatingCache() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        try await cache.write(Self.page, key: "gallery.7.cull.v1", etag: #""page""#)
        let updated = Self.item(
            state: .cut,
            etag: #""cull-asset-22222222222222222222222222222222""#
        )
        let client = CullQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(updated),
                APIResponseMetadata(
                    etag: #""cull-asset-33333333333333333333333333333333""#,
                    lastModified: nil,
                    receivedAt: Date()
                )
            ),
        ])
        let repository = OwnerRepository(client: client, cache: cache)

        do {
            _ = try await repository.decideCull(
                galleryID: 7,
                item: Self.page.items[0],
                action: .cut,
                idempotencyKey: UUID()
            )
            XCTFail("Expected the response ETag mismatch to be rejected.")
        } catch APIError.unexpectedResponse {
            // Expected before affected snapshots are invalidated.
        }

        let cached = try await cache.read("gallery.7.cull.v1", as: CullPage.self)
        XCTAssertNotNil(cached)
    }

    @MainActor
    func testTerminalOwnerAPIAuthFailurePurgesCacheAndEndsSharedAccessState() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        try await cache.write(Self.page, key: "gallery.7.cull.v1", etag: #""page""#)
        let accessState = OwnerMediaAccessState()
        let client = CullQueuedClient(replies: [
            .failure(.unauthenticated(nil)),
        ])
        let repository = OwnerRepository(
            client: client,
            cache: cache,
            onSessionEnded: {
                await accessState.end()
            }
        )

        do {
            _ = try await repository.refreshCullPage(galleryID: 7)
            XCTFail("Expected terminal owner authentication failure.")
        } catch APIError.unauthenticated(_) {
            // Expected after the shared owner surface is ended.
        }

        let cached = try await cache.read("gallery.7.cull.v1", as: CullPage.self)
        XCTAssertNil(cached)
        XCTAssertTrue(accessState.sessionEnded)
    }

    @MainActor
    func testModelKeepsStableRetryKeyAndUpdatesCountsOnlyAfterSuccess() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        try await cache.write(
            Self.page,
            key: "gallery.7.cull.v1",
            etag: #""cull-page-v1""#
        )
        let updated = Self.item(
            state: .cut,
            etag: #""cull-asset-22222222222222222222222222222222""#
        )
        let client = CullQueuedClient(replies: [
            .failure(.notModified(etag: #""cull-page-v1""#)),
            .failure(.transport(.timedOut)),
            .value(
                try MiseJSON.encoder().encode(updated),
                APIResponseMetadata(etag: updated.etag, lastModified: nil, receivedAt: Date())
            ),
        ])
        let repository = OwnerRepository(client: client, cache: cache)
        let model = CullReviewModel(repository: repository, galleryID: 7)
        await model.load()

        await model.decide(.cut, assetID: Self.page.items[0].assetID)
        XCTAssertNil(model.items.first?.state)
        XCTAssertEqual(model.counts.undecided, 1)

        await model.decide(.cut, assetID: Self.page.items[0].assetID)
        XCTAssertEqual(model.items.first?.state, .cut)
        XCTAssertEqual(model.counts.cut, 1)
        XCTAssertEqual(model.counts.undecided, 0)

        let requests = await client.requests()
        XCTAssertEqual(requests.count, 3)
        XCTAssertEqual(requests[1].idempotencyKey, requests[2].idempotencyKey)
    }

    @MainActor
    func testModelReloadsFirstPageWhenScoreRankedContinuationChanges() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        let initial = CullPage(
            items: Self.page.items,
            nextCursor: "old-cursor",
            hasMore: true,
            counts: CullCounts(total: 2, keep: 0, cut: 0, undecided: 2, scored: 2)
        )
        try await cache.write(
            initial,
            key: "gallery.7.cull.v1",
            etag: #""cull-page-v1""#
        )
        let reloaded = CullPage(
            items: [
                Self.item(
                    state: .keep,
                    etag: #""cull-asset-22222222222222222222222222222222""#
                ),
            ],
            nextCursor: nil,
            hasMore: false,
            counts: CullCounts(total: 1, keep: 1, cut: 0, undecided: 0, scored: 1)
        )
        let client = CullQueuedClient(replies: [
            .failure(.notModified(etag: #""cull-page-v1""#)),
            .failure(.conflict(APIProblem(
                status: 409,
                code: "pagination.collection_changed",
                detail: "Cull scores changed."
            ))),
            .value(
                try MiseJSON.encoder().encode(reloaded),
                APIResponseMetadata(
                    etag: #""cull-page-v2""#,
                    lastModified: nil,
                    receivedAt: Date()
                )
            ),
        ])
        let model = CullReviewModel(
            repository: OwnerRepository(client: client, cache: cache),
            galleryID: 7
        )
        await model.load()

        await model.loadMore()

        XCTAssertEqual(model.items, reloaded.items)
        XCTAssertEqual(model.counts, reloaded.counts)
        XCTAssertFalse(model.hasMore)
        XCTAssertEqual(
            model.errorMessage,
            "Cull scores changed while you were paging. Mise reloaded the review from the top."
        )
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 3)
        XCTAssertEqual(
            requests[1].queryItems.first(where: { $0.name == "cursor" })?.value,
            "old-cursor"
        )
        XCTAssertNil(requests[2].queryItems.first(where: { $0.name == "cursor" })?.value)
    }

    @MainActor
    func testModelDoesNotStartPaginationDuringFirstPageRefresh() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        let initial = CullPage(
            items: Self.page.items,
            nextCursor: "stale-cursor",
            hasMore: true,
            counts: CullCounts(total: 2, keep: 0, cut: 0, undecided: 2, scored: 2)
        )
        try await cache.write(
            initial,
            key: "gallery.7.cull.v1",
            etag: #""cull-page-v1""#
        )
        let reloaded = CullPage(
            items: [
                Self.item(
                    state: .keep,
                    etag: #""cull-asset-22222222222222222222222222222222""#
                ),
            ],
            nextCursor: nil,
            hasMore: false,
            counts: CullCounts(total: 1, keep: 1, cut: 0, undecided: 0, scored: 1)
        )
        let gate = CullTestGate()
        let client = CullQueuedClient(
            replies: [
                .failure(.notModified(etag: #""cull-page-v1""#)),
                .value(
                    try MiseJSON.encoder().encode(reloaded),
                    APIResponseMetadata(
                        etag: #""cull-page-v2""#,
                        lastModified: nil,
                        receivedAt: Date()
                    )
                ),
            ],
            gate: gate,
            blockAtRequest: 2
        )
        let model = CullReviewModel(
            repository: OwnerRepository(client: client, cache: cache),
            galleryID: 7
        )
        await model.load()

        let refreshTask = Task { await model.refresh() }
        var refreshIsWaiting = false
        for _ in 0 ..< 1_000 {
            refreshIsWaiting = await gate.isWaiting()
            if refreshIsWaiting { break }
            await Task.yield()
        }
        XCTAssertTrue(refreshIsWaiting)
        XCTAssertTrue(model.isRefreshing)

        await model.loadMore()
        let requestsDuringRefresh = await client.requests()
        XCTAssertEqual(requestsDuringRefresh.count, 2)
        XCTAssertNil(
            requestsDuringRefresh.last?.queryItems.first(where: { $0.name == "cursor" })?.value
        )

        await gate.release()
        let didRefresh = await refreshTask.value
        XCTAssertTrue(didRefresh)
        XCTAssertEqual(model.items, reloaded.items)
        XCTAssertFalse(model.hasMore)
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 2)
    }

    @MainActor
    func testParentReconciliationSurvivesLateDecisionAndCoalescesVisibleChanges() async {
        let lateDecision = expectation(description: "Late decision reconciled")
        let visibleBatch = expectation(description: "Visible changes reconciled once")
        var calls = 0
        let reconciler = CullParentReconciler {
            calls += 1
            if calls == 1 {
                lateDecision.fulfill()
            } else if calls == 2 {
                visibleBatch.fulfill()
            }
        }

        reconciler.appeared()
        reconciler.disappeared()
        reconciler.changed()
        await fulfillment(of: [lateDecision], timeout: 1)
        XCTAssertEqual(calls, 1)

        reconciler.appeared()
        reconciler.changed()
        reconciler.changed()
        reconciler.changed()
        XCTAssertEqual(calls, 1)
        reconciler.disappeared()
        await fulfillment(of: [visibleBatch], timeout: 1)
        XCTAssertEqual(calls, 2)
    }

    private static let page = CullPage(
        items: [
            item(
                state: nil,
                etag: #""cull-asset-11111111111111111111111111111111""#
            ),
        ],
        nextCursor: nil,
        hasMore: false,
        counts: CullCounts(total: 1, keep: 0, cut: 0, undecided: 1, scored: 1)
    )

    private static func item(state: CullState?, etag: String) -> CullItem {
        CullItem(
            assetID: 41,
            galleryID: 7,
            filename: "frame.jpg",
            position: 1,
            keeperScore: 0.82,
            heroPotential: 0.44,
            state: state,
            thumbnailURL: URL(
                string: "https://studio.example.com/api/v1/galleries/7/cull/assets/41/thumbnail"
            ),
            previewURL: URL(
                string: "https://studio.example.com/api/v1/galleries/7/cull/assets/41/preview"
            ),
            mediaRevision: 73,
            etag: etag
        )
    }

    private func assertRepositoryRejects(
        _ page: CullPage,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(cacheNamespace: "tenant-cull", rootDirectory: root)
        let client = CullQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(page),
                APIResponseMetadata(
                    etag: #""cull-page-invalid""#,
                    lastModified: nil,
                    receivedAt: Date()
                )
            ),
        ])
        let repository = OwnerRepository(client: client, cache: cache)

        do {
            _ = try await repository.refreshCullPage(galleryID: 7)
            XCTFail("Expected malformed cull page rejection.", file: file, line: line)
        } catch APIError.unexpectedResponse {
            // Expected before the malformed response reaches disk or UI state.
        } catch {
            XCTFail(
                "Expected APIError.unexpectedResponse, received \(error).",
                file: file,
                line: line
            )
        }

        let cached = try await cache.read("gallery.7.cull.v1", as: CullPage.self)
        XCTAssertNil(cached, file: file, line: line)
    }
}

private actor CullQueuedClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data, APIResponseMetadata)
        case failure(APIError)
    }

    struct Request: Sendable {
        let method: HTTPMethod
        let path: String
        let queryItems: [APIQueryItem]
        let headers: [String: String]
        let idempotencyKey: UUID?
        let etag: String?
    }

    private var replies: [Reply]
    private var captured: [Request] = []
    private let gate: CullTestGate?
    private let blockAtRequest: Int?

    init(
        replies: [Reply],
        gate: CullTestGate? = nil,
        blockAtRequest: Int? = nil
    ) {
        self.replies = replies
        self.gate = gate
        self.blockAtRequest = blockAtRequest
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
            method: endpoint.method,
            path: endpoint.path,
            queryItems: endpoint.queryItems,
            headers: endpoint.headers,
            idempotencyKey: endpoint.idempotencyKey,
            etag: endpoint.etag
        ))
        let requestNumber = captured.count
        let reply = replies.removeFirst()
        if let blockAtRequest, requestNumber == blockAtRequest, let gate {
            await gate.wait()
        }
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

    func requests() -> [Request] {
        captured
    }
}

private actor CullTestGate {
    private var waiting = false
    private var released = false
    private var continuations: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !released else { return }
        waiting = true
        await withCheckedContinuation { continuation in
            continuations.append(continuation)
        }
    }

    func isWaiting() -> Bool {
        waiting
    }

    func release() {
        released = true
        waiting = false
        let pending = continuations
        continuations.removeAll(keepingCapacity: false)
        for continuation in pending {
            continuation.resume()
        }
    }
}
