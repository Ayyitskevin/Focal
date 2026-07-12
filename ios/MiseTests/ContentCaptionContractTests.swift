import XCTest
@testable import Mise

final class ContentCaptionContractTests: XCTestCase {
    func testWireModelsDecodeTheExactNormalizedContract() throws {
        let page = try MiseJSON.decoder().decode(
            ContentCaptionPage.self,
            from: Data(
                """
                {
                  "items": [{
                    "id": 42,
                    "version_id": "0123456789abcdef0123456789abcdef",
                    "revision": 3,
                    "client_display_name": "Avery Foods",
                    "plan_title": "Monthly Social",
                    "period": "2026-08",
                    "label": "Carousel",
                    "body_preview": "first second third",
                    "status": "draft",
                    "ai_assisted": true,
                    "updated_at": "2026-07-11T11:00:00Z"
                  }],
                  "next_cursor": null,
                  "has_more": false,
                  "suggestions_enabled": true
                }
                """.utf8
            )
        )
        let summary = try XCTUnwrap(page.items.first)
        XCTAssertEqual(summary.id, 42)
        XCTAssertEqual(summary.versionID, "0123456789abcdef0123456789abcdef")
        XCTAssertEqual(summary.revision, 3)
        XCTAssertEqual(summary.clientDisplayName, "Avery Foods")
        XCTAssertEqual(summary.status, .draft)
        XCTAssertTrue(summary.aiAssisted)
        XCTAssertTrue(page.suggestionsEnabled)

        let detail = try MiseJSON.decoder().decode(
            ContentCaptionDetail.self,
            from: Data(
                """
                {
                  "id": 42,
                  "version_id": "0123456789abcdef0123456789abcdef",
                  "revision": 3,
                  "client_display_name": "Avery Foods",
                  "plan_id": 9,
                  "plan_title": "Monthly Social",
                  "period": "2026-08",
                  "label": "Carousel",
                  "body": "Existing human caption",
                  "note": "PRIVATE CAPTION NOTE",
                  "status": "draft",
                  "ai_assisted": false,
                  "ai_drafted_at": null,
                  "suggestions_enabled": true,
                  "created_at": "2026-07-11T10:00:00Z",
                  "updated_at": "2026-07-11T11:00:00Z"
                }
                """.utf8
            )
        )
        XCTAssertEqual(detail.planID, 9)
        XCTAssertEqual(detail.note, "PRIVATE CAPTION NOTE")
        XCTAssertFalse(detail.aiAssisted)

        let suggestion = try MiseJSON.decoder().decode(
            CaptionSuggestion.self,
            from: Data(
                """
                {
                  "id": "00000000-0000-4000-8000-000000000001",
                  "caption_id": 42,
                  "state": "ready",
                  "review": "human_review",
                  "candidate_text": "Human review required",
                  "failure_reason": null,
                  "base_revision": 3,
                  "stale": false,
                  "created_at": "2026-07-11T11:01:00Z",
                  "expires_at": "2026-07-11T12:01:00Z",
                  "completed_at": "2026-07-11T11:01:05Z"
                }
                """.utf8
            )
        )
        XCTAssertEqual(suggestion.captionID, 42)
        XCTAssertEqual(suggestion.state, .ready)
        XCTAssertEqual(suggestion.review, .humanReview)
        XCTAssertEqual(suggestion.candidateText, "Human review required")
        XCTAssertNil(suggestion.failureReason)
    }

    func testEndpointsCarryVersionAndIdempotencyContracts() throws {
        let key = try XCTUnwrap(UUID(uuidString: "00000000-0000-4000-8000-000000000099"))
        let suggestionID = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000123")
        )
        let page = MiseEndpoints.Content.captions(
            cursor: "opaque",
            limit: 900,
            etag: #""content-feed""#
        )
        XCTAssertEqual(page.method, .get)
        XCTAssertEqual(page.path, "/api/v1/content/captions")
        XCTAssertEqual(
            page.queryItems,
            [
                APIQueryItem(name: "cursor", value: "opaque"),
                APIQueryItem(name: "limit", value: "100"),
            ]
        )
        XCTAssertEqual(page.etag, #""content-feed""#)

        let detail = MiseEndpoints.Content.detail(id: 42, etag: #""detail""#)
        XCTAssertEqual(detail.path, "/api/v1/content/captions/42")
        XCTAssertEqual(detail.etag, #""detail""#)

        let create = try MiseEndpoints.Content.createSuggestion(
            captionID: 42,
            body: CaptionSuggestionRequest(instruction: "More concise"),
            etag: #""detail""#,
            idempotencyKey: key
        )
        XCTAssertEqual(create.method, .post)
        XCTAssertEqual(create.headers["If-Match"], #""detail""#)
        XCTAssertEqual(create.idempotencyKey, key)
        XCTAssertEqual(
            try MiseJSON.decoder().decode(
                CaptionSuggestionRequest.self,
                from: try XCTUnwrap(create.body)
            ),
            CaptionSuggestionRequest(instruction: "More concise")
        )

        let poll = MiseEndpoints.Content.suggestion(
            captionID: 42,
            suggestionID: suggestionID
        )
        XCTAssertEqual(
            poll.path,
            "/api/v1/content/captions/42/suggestions/00000000-0000-4000-8000-000000000123"
        )

        let update = try MiseEndpoints.Content.update(
            captionID: 42,
            body: CaptionBodyUpdate(body: "Final", suggestionID: suggestionID),
            etag: #""detail""#,
            idempotencyKey: key
        )
        XCTAssertEqual(update.method, .patch)
        XCTAssertEqual(update.path, "/api/v1/content/captions/42")
        XCTAssertEqual(update.headers["If-Match"], #""detail""#)
        XCTAssertEqual(update.idempotencyKey, key)
        XCTAssertEqual(
            try MiseJSON.decoder().decode(
                CaptionBodyUpdate.self,
                from: try XCTUnwrap(update.body)
            ),
            CaptionBodyUpdate(body: "Final", suggestionID: suggestionID)
        )
    }

    func testFeedRevalidatesOnlyFromTheExactStrongFirstPageETag() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let oldDate = Date(timeIntervalSince1970: 1_700_000_000)
        let feed = ContentCaptionFeed(
            captions: [Self.summary(id: 42)],
            hasOlderCaptions: false,
            suggestionsEnabled: true
        )
        try await fixture.cache.write(
            feed,
            key: "content.captions.v1",
            etag: #""content-feed-v1""#,
            storedAt: oldDate
        )
        let client = ContentQueuedClient(replies: [
            .failure(.notModified(etag: #""content-feed-v1""#)),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        let snapshot = try await repository.refreshContentCaptions()

        XCTAssertEqual(snapshot.source, .revalidated)
        XCTAssertEqual(snapshot.value, feed)
        XCTAssertGreaterThan(snapshot.storedAt, oldDate)
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests[0].etag, #""content-feed-v1""#)
        XCTAssertEqual(requests[0].queryItems.last?.value, "100")
    }

    func testFeedStopsAtFivePagesAndPersistsOnlyTheCompleteBoundedProjection() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let pages = (0..<5).map { index in
            ContentCaptionPage(
                items: [Self.summary(id: Int64(500 - index))],
                nextCursor: "cursor-\(index + 1)",
                hasMore: true,
                suggestionsEnabled: true
            )
        }
        let replies = try pages.enumerated().map { index, page in
            ContentQueuedClient.Reply.value(
                try MiseJSON.encoder().encode(page),
                Self.metadata(etag: index == 0 ? #""content-feed-v2""# : nil)
            )
        }
        let client = ContentQueuedClient(replies: replies)
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        let snapshot = try await repository.refreshContentCaptions()

        XCTAssertEqual(snapshot.value.captions.map(\.id), [500, 499, 498, 497, 496])
        XCTAssertTrue(snapshot.value.hasOlderCaptions)
        XCTAssertTrue(snapshot.value.suggestionsEnabled)
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 5)
        XCTAssertEqual(requests[1].queryItems.first?.value, "cursor-1")
        XCTAssertNil(requests[1].etag)
        let cached = try await fixture.cache.read(
            "content.captions.v1",
            as: ContentCaptionFeed.self
        )
        XCTAssertEqual(cached?.value, snapshot.value)
        XCTAssertEqual(cached?.etag, #""content-feed-v2""#)
    }

    func testFeedRejectsAWeakFirstPageValidatorWithoutCaching() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let page = ContentCaptionPage(
            items: [Self.summary(id: 42)],
            nextCursor: nil,
            hasMore: false,
            suggestionsEnabled: true
        )
        let client = ContentQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(page),
                Self.metadata(etag: #"W/"weak""#)
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshContentCaptions()
            XCTFail("Expected a strong first-page validator.")
        } catch APIError.unexpectedResponse {
            // Weak validators cannot protect an offline tenant-private projection.
        }

        let cached = try await fixture.cache.read(
            "content.captions.v1",
            as: ContentCaptionFeed.self
        )
        XCTAssertNil(cached)
    }

    func testFeedRejectsOutOfOrderIdentityAndLeavesPriorCacheUntouched() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let old = ContentCaptionFeed(
            captions: [Self.summary(id: 10)],
            hasOlderCaptions: false,
            suggestionsEnabled: true
        )
        try await fixture.cache.write(
            old,
            key: "content.captions.v1",
            etag: #""old""#
        )
        let invalid = ContentCaptionPage(
            items: [Self.summary(id: 50), Self.summary(id: 51)],
            nextCursor: nil,
            hasMore: false,
            suggestionsEnabled: true
        )
        let client = ContentQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(invalid),
                Self.metadata(etag: #""new""#)
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        do {
            _ = try await repository.refreshContentCaptions()
            XCTFail("Expected out-of-order IDs to be rejected.")
        } catch APIError.unexpectedResponse {
            // The new projection is rejected before replacing the last known-good feed.
        }

        let cached = try await fixture.cache.read(
            "content.captions.v1",
            as: ContentCaptionFeed.self
        )
        XCTAssertEqual(cached?.value, old)
        XCTAssertEqual(cached?.etag, #""old""#)
    }

    func testDetailUsesAProtectedStrongValidatorForConditionalReads() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let detail = Self.detail()
        try await fixture.cache.write(
            detail,
            key: "content.caption.42.v1",
            etag: #""caption-v1""#
        )
        let client = ContentQueuedClient(replies: [
            .failure(.notModified(etag: #""caption-v1""#)),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        let snapshot = try await repository.refreshContentCaption(id: 42)

        XCTAssertEqual(snapshot.source, .revalidated)
        XCTAssertEqual(snapshot.value, detail)
        XCTAssertEqual(snapshot.etag, #""caption-v1""#)
        let requests = await client.requests()
        XCTAssertEqual(requests.first?.etag, #""caption-v1""#)
    }

    func testAmbiguousSuggestionStartReusesTheExactNormalizedIntentAndUUID() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let suggestionID = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000201")
        )
        let client = ContentQueuedClient(replies: [
            .failure(.transport(.timedOut)),
            .value(
                try MiseJSON.encoder().encode(
                    Self.suggestion(id: suggestionID, state: .queued)
                ),
                Self.metadata()
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)
        let current = Self.snapshot()
        let instruction = "SENSITIVE_INSTRUCTION_Cafe\u{301}_launch"
        let request = CaptionSuggestionRequest(instruction: "  \(instruction)  ")

        do {
            _ = try await repository.startCaptionSuggestion(
                for: current,
                request: request
            )
            XCTFail("Expected the first ambiguous network attempt to time out.")
        } catch let APIError.transport(code) {
            XCTAssertEqual(code, .timedOut)
        }
        XCTAssertFalse(Self.cache(at: fixture.root, contains: "SENSITIVE_INSTRUCTION"))

        // A new repository has no disk operation to recover before the server
        // accepts a suggestion UUID, and it never auto-replays the lost prompt.
        let restartedCache = TenantJSONCache(
            cacheNamespace: "tenant-content",
            rootDirectory: fixture.root
        )
        let restartedClient = ContentQueuedClient(replies: [])
        let restartedRepository = OwnerRepository(
            client: restartedClient,
            cache: restartedCache
        )
        let processDeathRecovery = try await restartedRepository.recoverCaptionSuggestion(
            captionID: 42
        )
        XCTAssertNil(processDeathRecovery)
        let restartedRequests = await restartedClient.requests()
        XCTAssertTrue(restartedRequests.isEmpty)

        let accepted = try await repository.startCaptionSuggestion(
            for: current,
            request: request
        )

        XCTAssertEqual(accepted.id, suggestionID)
        XCTAssertEqual(accepted.state, .queued)
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 2)
        XCTAssertNotNil(requests[0].idempotencyKey)
        XCTAssertEqual(requests[0].idempotencyKey, requests[1].idempotencyKey)
        XCTAssertEqual(requests[0].headers["If-Match"], current.etag)
        for captured in requests {
            XCTAssertEqual(
                try MiseJSON.decoder().decode(
                    CaptionSuggestionRequest.self,
                    from: try XCTUnwrap(captured.body)
                ),
                CaptionSuggestionRequest(
                    instruction: "SENSITIVE_INSTRUCTION_Café_launch"
                )
            )
        }
        XCTAssertFalse(Self.cache(at: fixture.root, contains: "SENSITIVE_INSTRUCTION"))
        XCTAssertFalse(Self.cache(at: fixture.root, contains: current.etag))
        if let idempotencyKey = requests.first?.idempotencyKey {
            XCTAssertFalse(
                Self.cache(
                    at: fixture.root,
                    contains: idempotencyKey.uuidString
                )
            )
        }

        let acceptedRecoveryClient = ContentQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(
                    Self.suggestion(id: suggestionID, state: .queued)
                ),
                Self.metadata()
            ),
        ])
        let acceptedRecoveryRepository = OwnerRepository(
            client: acceptedRecoveryClient,
            cache: TenantJSONCache(
                cacheNamespace: "tenant-content",
                rootDirectory: fixture.root
            )
        )
        let recovered = try await acceptedRecoveryRepository.recoverCaptionSuggestion(
            captionID: 42
        )
        XCTAssertEqual(recovered?.id, suggestionID)
        let recoveryRequests = await acceptedRecoveryClient.requests()
        XCTAssertEqual(recoveryRequests.count, 1)
        XCTAssertEqual(recoveryRequests[0].method, .get)
        XCTAssertEqual(
            recoveryRequests[0].path,
            "/api/v1/content/captions/42/suggestions/\(suggestionID.uuidString.lowercased())"
        )
        XCTAssertNil(recoveryRequests[0].body)
        XCTAssertFalse(Self.cache(at: fixture.root, contains: "SENSITIVE_INSTRUCTION"))
    }

    func testDefinitiveSuggestionConflictClearsIntentForReloadedRevision() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let suggestionID = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000207")
        )
        let client = ContentQueuedClient(replies: [
            .failure(.conflict(APIProblem(
                status: 409,
                code: "resource.version_conflict",
                detail: "Caption changed."
            ))),
            .value(
                try MiseJSON.encoder().encode(
                    Self.suggestion(
                        id: suggestionID,
                        state: .queued,
                        baseRevision: 1
                    )
                ),
                Self.metadata()
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)
        let request = CaptionSuggestionRequest(instruction: "More concise")

        do {
            _ = try await repository.startCaptionSuggestion(
                for: Self.snapshot(),
                request: request
            )
            XCTFail("Expected the stale revision to be rejected.")
        } catch APIError.conflict {
            // A signed 409 proves the server did not accept this attempt.
        }

        let latest = Self.snapshot(
            detail: Self.detail(revision: 1),
            etag: #""caption-v2""#
        )
        let accepted = try await repository.startCaptionSuggestion(
            for: latest,
            request: request
        )

        XCTAssertEqual(accepted.id, suggestionID)
        let requests = await client.requests()
        XCTAssertEqual(requests.count, 2)
        XCTAssertNotEqual(requests[0].idempotencyKey, requests[1].idempotencyKey)
        XCTAssertEqual(requests[0].headers["If-Match"], #""caption-v1""#)
        XCTAssertEqual(requests[1].headers["If-Match"], #""caption-v2""#)
    }

    func testReadyCandidateStaysMemoryOnlyAndInvalidatesEveryReadProjection() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let suggestionID = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000202")
        )
        let candidate = "TRANSIENT_CANDIDATE_DO_NOT_CACHE"
        let feed = ContentCaptionFeed(
            captions: [Self.summary(id: 42)],
            hasOlderCaptions: false,
            suggestionsEnabled: true
        )
        try await fixture.cache.write(
            feed,
            key: "content.captions.v1",
            etag: #""feed""#
        )
        try await fixture.cache.write(
            Self.detail(),
            key: "content.caption.42.v1",
            etag: #""caption""#
        )
        try await fixture.cache.write(
            AIActivityFeed(runs: [], hasOlderRuns: false),
            key: "ai-activity.v1",
            etag: #""ai""#
        )
        let client = ContentQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(
                    Self.suggestion(id: suggestionID, state: .queued)
                ),
                Self.metadata()
            ),
            .value(
                try MiseJSON.encoder().encode(
                    Self.suggestion(
                        id: suggestionID,
                        state: .ready,
                        candidate: candidate
                    )
                ),
                Self.metadata()
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)
        let instruction = "SENSITIVE_READY_INSTRUCTION"
        _ = try await repository.startCaptionSuggestion(
            for: Self.snapshot(),
            request: CaptionSuggestionRequest(instruction: instruction)
        )
        XCTAssertFalse(Self.cache(at: fixture.root, contains: instruction))

        let ready = try await repository.pollCaptionSuggestion(
            captionID: 42,
            suggestionID: suggestionID
        )

        XCTAssertEqual(ready.state, .ready)
        XCTAssertEqual(ready.candidateText, candidate)
        let cachedFeed = try await fixture.cache.read(
            "content.captions.v1",
            as: ContentCaptionFeed.self
        )
        let cachedDetail = try await fixture.cache.read(
            "content.caption.42.v1",
            as: ContentCaptionDetail.self
        )
        let cachedAIActivity = try await fixture.cache.read(
            "ai-activity.v1",
            as: AIActivityFeed.self
        )
        XCTAssertNil(cachedFeed)
        XCTAssertNil(cachedDetail)
        XCTAssertNil(cachedAIActivity)
        XCTAssertFalse(Self.cache(at: fixture.root, contains: instruction))
        XCTAssertFalse(Self.cache(at: fixture.root, contains: candidate))
    }

    func testSupersededPromptBearingRecoveryEnvelopeIsScrubbed() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let instruction = "LEGACY_SENSITIVE_INSTRUCTION"
        let candidate = "LEGACY_SENSITIVE_CANDIDATE"
        try await fixture.cache.write(
            [
                "instruction": instruction,
                "candidate": candidate,
            ],
            key: "content.caption.42.suggestion-recovery.v1",
            etag: nil
        )
        XCTAssertTrue(Self.cache(at: fixture.root, contains: instruction))
        XCTAssertTrue(Self.cache(at: fixture.root, contains: candidate))
        let client = ContentQueuedClient(replies: [])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        let recovered = try await repository.recoverCaptionSuggestion(captionID: 42)

        XCTAssertNil(recovered)
        XCTAssertFalse(Self.cache(at: fixture.root, contains: instruction))
        XCTAssertFalse(Self.cache(at: fixture.root, contains: candidate))
        let requests = await client.requests()
        XCTAssertTrue(requests.isEmpty)
    }

    func testSaveIsOnlineOnlyNormalizesBodyAndInvalidatesRecoveryAndReads() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let suggestionID = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000203")
        )
        let saveKey = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000204")
        )
        let current = Self.snapshot()
        let feed = ContentCaptionFeed(
            captions: [Self.summary(id: 42)],
            hasOlderCaptions: false,
            suggestionsEnabled: true
        )
        try await fixture.cache.write(
            feed,
            key: "content.captions.v1",
            etag: #""feed""#
        )
        try await fixture.cache.write(
            current.value,
            key: "content.caption.42.v1",
            etag: current.etag
        )
        try await fixture.cache.write(
            AIActivityFeed(runs: [], hasOlderRuns: false),
            key: "ai-activity.v1",
            etag: #""ai""#
        )
        let updated = Self.detail(
            revision: 1,
            body: "Café final body",
            updatedAt: Self.baseDate.addingTimeInterval(60)
        )
        let client = ContentQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(
                    Self.suggestion(id: suggestionID, state: .queued)
                ),
                Self.metadata()
            ),
            .value(
                try MiseJSON.encoder().encode(updated),
                Self.metadata(etag: #""caption-v2""#)
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)
        _ = try await repository.startCaptionSuggestion(
            for: current,
            request: CaptionSuggestionRequest(instruction: "Recoverable request")
        )

        let saved = try await repository.updateContentCaption(
            current: current,
            request: CaptionBodyUpdate(
                body: "  Cafe\u{301} final body  ",
                suggestionID: nil
            ),
            idempotencyKey: saveKey
        )

        XCTAssertEqual(saved.source, .network)
        XCTAssertEqual(saved.value.body, "Café final body")
        XCTAssertEqual(saved.value.revision, 1)
        XCTAssertEqual(saved.etag, #""caption-v2""#)
        let requests = await client.requests()
        let patch = try XCTUnwrap(requests.last)
        XCTAssertEqual(patch.method, .patch)
        XCTAssertEqual(patch.headers["If-Match"], current.etag)
        XCTAssertEqual(patch.idempotencyKey, saveKey)
        XCTAssertEqual(
            try MiseJSON.decoder().decode(
                CaptionBodyUpdate.self,
                from: try XCTUnwrap(patch.body)
            ),
            CaptionBodyUpdate(body: "Café final body", suggestionID: nil)
        )
        let cachedFeed = try await fixture.cache.read(
            "content.captions.v1",
            as: ContentCaptionFeed.self
        )
        let cachedDetail = try await fixture.cache.read(
            "content.caption.42.v1",
            as: ContentCaptionDetail.self
        )
        let cachedAIActivity = try await fixture.cache.read(
            "ai-activity.v1",
            as: AIActivityFeed.self
        )
        let recovery = try await repository.recoverCaptionSuggestion(captionID: 42)
        XCTAssertNil(cachedFeed)
        XCTAssertNil(cachedDetail)
        XCTAssertNil(cachedAIActivity)
        XCTAssertNil(recovery)
    }

    func testSuggestionsDefaultToTheServerAvailabilityField() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let client = ContentQueuedClient(replies: [])
        let repository = OwnerRepository(client: client, cache: fixture.cache)
        let unavailable = Self.snapshot(
            detail: Self.detail(suggestionsEnabled: false)
        )

        do {
            _ = try await repository.startCaptionSuggestion(
                for: unavailable,
                request: CaptionSuggestionRequest(instruction: nil)
            )
            XCTFail("Expected server-authoritative suggestion availability to be enforced.")
        } catch OwnerContentRepositoryError.suggestionsUnavailable {
            // No local override can turn a server-disabled feature on.
        }

        let requests = await client.requests()
        XCTAssertTrue(requests.isEmpty)
    }

    func testUnknownSuggestionStatesAndFailureReasonsAreRejected() async throws {
        let fixture = try ContentFixture()
        defer { fixture.remove() }
        let firstID = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000205")
        )
        let secondID = try XCTUnwrap(
            UUID(uuidString: "00000000-0000-4000-8000-000000000206")
        )
        let unknownState = Self.suggestion(
            id: firstID,
            state: CaptionSuggestionState(rawValue: "finished")
        )
        let unknownFailure = Self.suggestion(
            id: secondID,
            state: .failed,
            failure: CaptionSuggestionFailure(rawValue: "private_provider_detail")
        )
        let client = ContentQueuedClient(replies: [
            .value(
                try MiseJSON.encoder().encode(unknownState),
                Self.metadata()
            ),
            .value(
                try MiseJSON.encoder().encode(unknownFailure),
                Self.metadata()
            ),
        ])
        let repository = OwnerRepository(client: client, cache: fixture.cache)

        for suggestionID in [firstID, secondID] {
            do {
                _ = try await repository.pollCaptionSuggestion(
                    captionID: 42,
                    suggestionID: suggestionID
                )
                XCTFail("Expected an unknown closed-set value to be rejected.")
            } catch APIError.unexpectedResponse {
                // Decode stays forward-compatible; the repository stays closed and safe.
            }
        }
    }

    private static let baseDate = Date(timeIntervalSince1970: 1_720_000_000)

    private static func versionID(_ id: Int64) -> String {
        let suffix = String(id, radix: 16)
        return String(repeating: "0", count: 32 - suffix.count) + suffix
    }

    private static func summary(id: Int64) -> ContentCaptionSummary {
        ContentCaptionSummary(
            id: id,
            versionID: versionID(id),
            revision: 0,
            clientDisplayName: "Avery Foods",
            planTitle: "Monthly Social",
            period: "2026-08",
            label: "Carousel",
            bodyPreview: "first second third",
            status: .draft,
            aiAssisted: false,
            updatedAt: baseDate.addingTimeInterval(TimeInterval(id))
        )
    }

    private static func detail(
        revision: Int64 = 0,
        body: String = "Existing human caption",
        status: ContentCaptionStatus = .draft,
        suggestionsEnabled: Bool = true,
        updatedAt: Date = baseDate
    ) -> ContentCaptionDetail {
        ContentCaptionDetail(
            id: 42,
            versionID: versionID(42),
            revision: revision,
            clientDisplayName: "Avery Foods",
            planID: 9,
            planTitle: "Monthly Social",
            period: "2026-08",
            label: "Carousel",
            body: body,
            note: "PRIVATE CAPTION NOTE",
            status: status,
            aiAssisted: false,
            aiDraftedAt: nil,
            suggestionsEnabled: suggestionsEnabled,
            createdAt: baseDate,
            updatedAt: updatedAt
        )
    }

    private static func snapshot(
        detail: ContentCaptionDetail = detail(),
        etag: String = #""caption-v1""#
    ) -> ContentCaptionSnapshot {
        ContentCaptionSnapshot(
            value: detail,
            etag: etag,
            storedAt: baseDate,
            source: .network
        )
    }

    private static func suggestion(
        id: UUID,
        state: CaptionSuggestionState,
        candidate: String? = nil,
        failure: CaptionSuggestionFailure? = nil,
        baseRevision: Int64 = 0
    ) -> CaptionSuggestion {
        let isPending = state == .queued || state == .running
        return CaptionSuggestion(
            id: id,
            captionID: 42,
            state: state,
            review: .humanReview,
            candidateText: candidate,
            failureReason: failure,
            baseRevision: baseRevision,
            stale: false,
            createdAt: baseDate,
            expiresAt: baseDate.addingTimeInterval(3_600),
            completedAt: isPending ? nil : baseDate.addingTimeInterval(5)
        )
    }

    private static func metadata(etag: String? = nil) -> APIResponseMetadata {
        APIResponseMetadata(
            etag: etag,
            lastModified: nil,
            receivedAt: Date()
        )
    }

    private static func cache(at root: URL, contains value: String) -> Bool {
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        ) else {
            return false
        }
        for case let url as URL in enumerator {
            guard let data = try? Data(contentsOf: url) else { continue }
            if data.range(of: Data(value.utf8)) != nil {
                return true
            }
        }
        return false
    }
}

private struct ContentFixture {
    let root: URL
    let cache: TenantJSONCache

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        cache = TenantJSONCache(
            cacheNamespace: "tenant-content",
            rootDirectory: root
        )
    }

    func remove() {
        try? FileManager.default.removeItem(at: root)
    }
}

private actor ContentQueuedClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data, APIResponseMetadata)
        case failure(APIError)
    }

    struct Request: Sendable {
        let method: HTTPMethod
        let path: String
        let queryItems: [APIQueryItem]
        let headers: [String: String]
        let body: Data?
        let idempotencyKey: UUID?
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
        captured.append(
            Request(
                method: endpoint.method,
                path: endpoint.path,
                queryItems: endpoint.queryItems,
                headers: endpoint.headers,
                body: endpoint.body,
                idempotencyKey: endpoint.idempotencyKey,
                etag: endpoint.etag
            )
        )
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
