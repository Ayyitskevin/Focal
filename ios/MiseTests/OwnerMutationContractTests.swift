import XCTest
@testable import Mise

final class OwnerMutationContractTests: XCTestCase {
    func testBookingSlotsEndpointUsesExactSourceAwareQuery() {
        let endpoint = MiseEndpoints.Scheduling.eventTypeSlots(
            eventTypeID: 8,
            day: LocalDate(rawValue: "2026-07-16"),
            rescheduleBookingID: 91
        )

        XCTAssertEqual(endpoint.method, .get)
        XCTAssertEqual(endpoint.path, "/api/v1/event-types/8/slots")
        XCTAssertEqual(
            endpoint.queryItems,
            [
                APIQueryItem(name: "day", value: "2026-07-16"),
                APIQueryItem(name: "reschedule_booking_id", value: "91"),
            ]
        )
        XCTAssertEqual(endpoint.authentication, .bearer)
        XCTAssertNil(endpoint.body)
        XCTAssertNil(endpoint.idempotencyKey)
    }

    func testBookingRescheduleEndpointUsesWholeSecondUTCBodyAndUUIDKey() throws {
        let key = try XCTUnwrap(UUID(uuidString: "86AA7D32-740E-4993-AF8D-438BB80C366B"))
        let start = try MiseJSON.decoder().decode(
            Date.self,
            from: Data(#""2026-07-16T11:00:00.875Z""#.utf8)
        )
        let endpoint = try MiseEndpoints.Scheduling.rescheduleBooking(
            id: 91,
            body: BookingRescheduleRequest(
                startAt: start,
                timeZone: "America/New_York"
            ),
            idempotencyKey: key
        )
        let body = try XCTUnwrap(endpoint.body)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: body) as? [String: String]
        )

        XCTAssertEqual(endpoint.method, .post)
        XCTAssertEqual(endpoint.path, "/api/v1/bookings/91/reschedule")
        XCTAssertEqual(endpoint.authentication, .bearer)
        XCTAssertEqual(endpoint.headers, ["Content-Type": "application/json"])
        XCTAssertEqual(endpoint.idempotencyKey, key)
        XCTAssertEqual(
            body,
            Data(#"{"start_at":"2026-07-16T11:00:00Z","time_zone":"America\/New_York"}"#.utf8)
        )
        XCTAssertEqual(
            object,
            [
                "start_at": "2026-07-16T11:00:00Z",
                "time_zone": "America/New_York",
            ]
        )

        let generic = try MiseJSON.encoder().encode(
            BookingCreateRequest(
                eventTypeID: 8,
                startAt: start,
                timeZone: "America/New_York",
                name: "Rossi",
                email: "ops@rossi.test",
                phone: nil,
                notes: nil
            )
        )
        XCTAssertTrue(String(decoding: generic, as: UTF8.self).contains(".875Z"))
    }

    func testBookingWorkflowEndpointsUseCanonicalUUIDPaths() throws {
        let workflowID = try XCTUnwrap(
            UUID(uuidString: "86AA7D32-740E-4993-AF8D-438BB80C366B")
        )
        let status = MiseEndpoints.Scheduling.bookingWorkflow(id: workflowID)
        let retry = MiseEndpoints.Scheduling.retryBookingWorkflow(id: workflowID)
        let canonical = "86aa7d32-740e-4993-af8d-438bb80c366b"

        XCTAssertEqual(status.method, .get)
        XCTAssertEqual(status.path, "/api/v1/booking-workflows/\(canonical)")
        XCTAssertNil(status.body)
        XCTAssertNil(status.idempotencyKey)
        XCTAssertEqual(retry.method, .post)
        XCTAssertEqual(retry.path, "/api/v1/booking-workflows/\(canonical)/retry")
        XCTAssertNil(retry.body)
        XCTAssertNil(retry.idempotencyKey)
    }

    func testSlotFeedPreservesWireOrderForStrictModelValidation() throws {
        let feed = try MiseJSON.decoder().decode(
            EventTypeSlots.self,
            from: Data(
                """
                {
                  "event_type_id": 8,
                  "day": "2026-07-16",
                  "time_zone": "America/New_York",
                  "reschedule_booking_id": 91,
                  "slots": [
                    {"start_at":"2026-07-16T15:00:00Z","end_at":"2026-07-16T16:00:00Z"},
                    {"start_at":"2026-07-16T13:00:00Z","end_at":"2026-07-16T14:00:00Z"}
                  ]
                }
                """.utf8
            )
        )

        XCTAssertEqual(feed.eventTypeID, 8)
        XCTAssertEqual(feed.day, LocalDate(rawValue: "2026-07-16"))
        XCTAssertEqual(feed.timeZone, "America/New_York")
        XCTAssertEqual(feed.rescheduleBookingID, 91)
        XCTAssertGreaterThan(feed.slots[0].startAt, feed.slots[1].startAt)
    }

    func testBookingWorkflowDTOsPreserveUnknownValuesAndAllEvidence() throws {
        let result = try MiseJSON.decoder().decode(
            BookingRescheduleResult.self,
            from: Data(
                """
                {
                  "status":"future_transition",
                  "workflow_id":"86aa7d32-740e-4993-af8d-438bb80c366b",
                  "delivery_status":"accepted",
                  "original_booking_id":91,
                  "replacement_booking_id":92,
                  "start_at":"2026-07-16T13:00:00Z",
                  "end_at":"2026-07-16T14:00:00Z"
                }
                """.utf8
            )
        )
        let workflow = try MiseJSON.decoder().decode(
            BookingWorkflowStatus.self,
            from: Data(
                """
                {
                  "workflow_id":"86aa7d32-740e-4993-af8d-438bb80c366b",
                  "status":"paused",
                  "source_booking_id":91,
                  "replacement_booking_id":92,
                  "effects":[{
                    "kind":"future_provider_move",
                    "sequence":70,
                    "status":"waiting_for_operator",
                    "attempts":3,
                    "next_attempt_at":"2026-07-13T18:30:05Z",
                    "completed_at":null,
                    "provider_ref":"provider-42",
                    "error_class":"TimeoutError",
                    "error_code":"exception"
                  }]
                }
                """.utf8
            )
        )

        XCTAssertEqual(result.status.rawValue, "future_transition")
        XCTAssertEqual(result.deliveryStatus.rawValue, "accepted")
        XCTAssertEqual(workflow.status.rawValue, "paused")
        XCTAssertEqual(workflow.sourceBookingID, 91)
        XCTAssertEqual(workflow.replacementBookingID, 92)
        let effect = try XCTUnwrap(workflow.effects.first)
        XCTAssertEqual(effect.kind.rawValue, "future_provider_move")
        XCTAssertEqual(effect.sequence, 70)
        XCTAssertEqual(effect.status.rawValue, "waiting_for_operator")
        XCTAssertEqual(effect.attempts, 3)
        XCTAssertNotNil(effect.nextAttemptAt)
        XCTAssertNil(effect.completedAt)
        XCTAssertEqual(effect.providerRef, "provider-42")
        XCTAssertEqual(effect.errorClass, "TimeoutError")
        XCTAssertEqual(effect.errorCode, "exception")
    }

    func testRescheduleSuccessPersistsBeforeSendAndThenCommitsCacheTransition() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let client = OwnerMutationContractClient([
            .inspect { request in
                let saved = try await context.cache.readCommand(
                    "booking.reschedule.pending.v2.session-a",
                    as: PendingBookingRescheduleAttempt.self
                )?.value
                XCTAssertEqual(saved?.bookingID, 91)
                XCTAssertEqual(saved?.idempotencyKey, request.idempotencyKey)
                return Data(Self.rescheduleResultJSON.utf8)
            },
        ])
        let repository = OwnerRepository(
            client: client,
            cache: context.cache,
            sessionID: "session-a"
        )
        let start = try Self.date("2026-07-16T13:00:00.900Z")
        let attempt = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: start,
            timeZone: "America/New_York"
        )

        let result = try await repository.submitBookingReschedule(attempt)
        let pending = try await context.cache.readCommand(
            "booking.reschedule.pending.v2.session-a",
            as: PendingBookingRescheduleAttempt.self
        )
        let cachedBookings = try await context.cache.read(
            "bookings.v1",
            as: [Booking].self
        )
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        let restoredRepository = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: context.cache,
            sessionID: "session-a"
        )
        let latest = try await restoredRepository.latestBookingRescheduleResult()

        XCTAssertEqual(result.status, .rescheduled)
        XCTAssertEqual(result.deliveryStatus, .pending)
        XCTAssertEqual(result.originalBookingID, 91)
        XCTAssertNil(pending)
        XCTAssertNil(cachedBookings)
        XCTAssertNil(cachedDashboard)
        XCTAssertEqual(latest, result)
    }

    func testRescheduleTransportFailurePreservesSnapshotsAndPendingAttempt() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let client = OwnerMutationContractClient([
            .failure(.transport(.timedOut)),
        ])
        let repository = OwnerRepository(
            client: client,
            cache: context.cache,
            sessionID: "session-a"
        )
        let attempt = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: try Self.date("2026-07-16T13:00:00Z"),
            timeZone: "America/New_York"
        )

        do {
            _ = try await repository.submitBookingReschedule(attempt)
            XCTFail("Expected the ambiguous transport failure to throw.")
        } catch APIError.transport(.timedOut) {
            // The same persisted attempt remains available for an exact replay.
        }

        let pending = try await repository.pendingBookingRescheduleAttempt()
        let cachedBookings = try await context.cache.read("bookings.v1", as: [Booking].self)
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        let latest = try await repository.latestBookingRescheduleResult()
        XCTAssertEqual(pending, attempt)
        XCTAssertNotNil(cachedBookings)
        XCTAssertNotNil(cachedDashboard)
        XCTAssertNil(latest)
    }

    func testMalformedRescheduleResponsePreservesSnapshotsAndPendingAttempt() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let client = OwnerMutationContractClient([
            .value(Data(#"{"status":"rescheduled","workflow_id":"invalid"}"#.utf8)),
        ])
        let repository = OwnerRepository(
            client: client,
            cache: context.cache,
            sessionID: "session-a"
        )
        let attempt = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: try Self.date("2026-07-16T13:00:00Z"),
            timeZone: "America/New_York"
        )

        do {
            _ = try await repository.submitBookingReschedule(attempt)
            XCTFail("Expected malformed response decoding to throw.")
        } catch is DecodingError {
            // State stays replayable until a semantically valid response is decoded.
        }

        let pending = try await repository.pendingBookingRescheduleAttempt()
        let cachedBookings = try await context.cache.read("bookings.v1", as: [Booking].self)
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertEqual(pending, attempt)
        XCTAssertNotNil(cachedBookings)
        XCTAssertNotNil(cachedDashboard)
    }

    func testSemanticallyInvalidRescheduleResponsePreservesAttemptAndSnapshots() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(Self.dashboard, key: "dashboard.v1", etag: nil)
        try await context.cache.write(
            [Self.confirmedBooking],
            key: "bookings.v1",
            etag: nil
        )
        let invalid = Self.rescheduleResultJSON
            .replacingOccurrences(of: #""delivery_status":"pending""#, with: #""delivery_status":"accepted""#)
        let client = OwnerMutationContractClient([.value(Data(invalid.utf8))])
        let repository = OwnerRepository(
            client: client,
            cache: context.cache,
            sessionID: "session-a"
        )
        let attempt = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: try Self.date("2026-07-16T13:00:00Z"),
            timeZone: "America/New_York"
        )

        do {
            _ = try await repository.submitBookingReschedule(attempt)
            XCTFail("Expected semantic validation to reject the response.")
        } catch OwnerRepositoryError.invalidBookingRescheduleResponse {
            // Forward-compatible decoding is distinct from accepting an invalid commit receipt.
        }

        let pending = try await repository.pendingBookingRescheduleAttempt()
        let cachedBookings = try await context.cache.read("bookings.v1", as: [Booking].self)
        let cachedDashboard = try await context.cache.read(
            "dashboard.v1",
            as: DashboardSummary.self
        )
        XCTAssertEqual(pending, attempt)
        XCTAssertNotNil(cachedBookings)
        XCTAssertNotNil(cachedDashboard)
    }

    func testMismatchedRescheduleStartPreservesExactReplayAttempt() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let invalid = Self.rescheduleResultJSON
            .replacingOccurrences(
                of: #""start_at":"2026-07-16T13:00:00Z""#,
                with: #""start_at":"2026-07-16T13:30:00Z""#
            )
        let repository = OwnerRepository(
            client: OwnerMutationContractClient([.value(Data(invalid.utf8))]),
            cache: context.cache,
            sessionID: "session-a"
        )
        let attempt = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: try Self.date("2026-07-16T13:00:00Z"),
            timeZone: "America/New_York"
        )

        do {
            _ = try await repository.submitBookingReschedule(attempt)
            XCTFail("Expected the mismatched committed start to be rejected.")
        } catch OwnerRepositoryError.invalidBookingRescheduleResponse {
            // A wrong 200 must not consume the exact-replay command.
        }

        let pending = try await repository.pendingBookingRescheduleAttempt()
        XCTAssertEqual(pending, attempt)
    }

    func testCorruptPendingCommandFailsLoudWithoutDeletingReplayEvidence() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.write(
            ["not-a-reschedule-attempt"],
            key: "booking.reschedule.pending.v1",
            etag: nil
        )
        let repository = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: context.cache,
            sessionID: "session-a"
        )

        do {
            _ = try await repository.prepareBookingReschedule(
                bookingID: 91,
                startAt: try Self.date("2026-07-16T13:00:00Z"),
                timeZone: "America/New_York"
            )
            XCTFail("Expected a corrupt command journal to fail closed.")
        } catch is DecodingError {
            // The command file must remain available for explicit recovery.
        }

        let evidence = try await context.cache.read(
            "booking.reschedule.pending.v1",
            as: [String].self
        )
        XCTAssertEqual(evidence?.value, ["not-a-reschedule-attempt"])
    }

    func testCorruptCommittedWorkflowHandleFailsLoudWithoutDeletingEvidence() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        try await context.cache.writeCommandIfAbsent(
            ["not-a-reschedule-result"],
            key: "booking.reschedule.latest.v2.session-a",
            etag: nil
        )
        let repository = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: context.cache,
            sessionID: "session-a"
        )

        do {
            _ = try await repository.latestBookingRescheduleResult()
            XCTFail("Expected a corrupt committed workflow handle to fail closed.")
        } catch is DecodingError {
            // The durable workflow handle must remain available for recovery.
        }

        let evidence = try await context.cache.readCommand(
            "booking.reschedule.latest.v2.session-a",
            as: [String].self
        )
        XCTAssertEqual(evidence?.value, ["not-a-reschedule-result"])
    }

    func testConcurrentRepositoriesReuseOneAtomicPendingCommand() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let first = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root),
            sessionID: "session-a"
        )
        let second = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root),
            sessionID: "session-a"
        )
        let start = try Self.date("2026-07-16T13:00:00Z")

        async let firstAttempt = first.prepareBookingReschedule(
            bookingID: 91,
            startAt: start,
            timeZone: "America/New_York"
        )
        async let secondAttempt = second.prepareBookingReschedule(
            bookingID: 91,
            startAt: start,
            timeZone: "America/New_York"
        )

        let attempts = try await (firstAttempt, secondAttempt)
        XCTAssertEqual(attempts.0, attempts.1)
    }

    func testSamePreparedAttemptReusesExactBodyAndKeyAcrossRetry() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let client = OwnerMutationContractClient([
            .failure(.transport(.networkConnectionLost)),
            .value(Data(Self.rescheduleResultJSON.utf8)),
        ])
        let repository = OwnerRepository(
            client: client,
            cache: context.cache,
            sessionID: "session-a"
        )
        let start = try Self.date("2026-07-16T13:00:00.900Z")
        let first = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: start,
            timeZone: "America/New_York"
        )
        let second = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: start,
            timeZone: "America/New_York"
        )
        XCTAssertEqual(first, second)

        do {
            _ = try await repository.submitBookingReschedule(first)
            XCTFail("Expected the first transport to fail.")
        } catch APIError.transport(.networkConnectionLost) {
            // Expected; replay below must use the same durable command identity.
        }
        _ = try await repository.submitBookingReschedule(second)

        let requests = await client.capturedRequests()
        XCTAssertEqual(requests.count, 2)
        XCTAssertEqual(requests[0].body, requests[1].body)
        XCTAssertEqual(requests[0].idempotencyKey, requests[1].idempotencyKey)
        XCTAssertEqual(requests[0].idempotencyKey, first.idempotencyKey)
    }

    func testStaleSessionPurgeCannotDeleteAnotherSessionsPendingAttempt() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let client = OwnerMutationContractClient([])
        let firstRepository = OwnerRepository(
            client: client,
            cache: TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root),
            sessionID: "session-a"
        )
        let first = try await firstRepository.prepareBookingReschedule(
            bookingID: 91,
            startAt: try Self.date("2026-07-16T13:00:00Z"),
            timeZone: "America/New_York"
        )
        let nextRepository = OwnerRepository(
            client: client,
            cache: TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root),
            sessionID: "session-b"
        )
        let next = try await nextRepository.prepareBookingReschedule(
            bookingID: 92,
            startAt: try Self.date("2026-07-17T14:00:00Z"),
            timeZone: "America/New_York"
        )

        await firstRepository.purgeCache()

        let nextAfterStalePurge = try await nextRepository.pendingBookingRescheduleAttempt()
        let auditCache = TenantJSONCache(
            cacheNamespace: "workspace_test",
            rootDirectory: root
        )
        let firstEvidence = try await auditCache.readCommand(
            "booking.reschedule.pending.v2.session-a",
            as: PendingBookingRescheduleAttempt.self
        )?.value
        let nextEvidence = try await auditCache.readCommand(
            "booking.reschedule.pending.v2.session-b",
            as: PendingBookingRescheduleAttempt.self
        )?.value

        XCTAssertEqual(firstEvidence, first)
        XCTAssertEqual(nextAfterStalePurge, next)
        XCTAssertEqual(nextEvidence, next)
    }

    func testForeignLegacyPendingRemainsForOwningSessionToMigrate() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let legacy = PendingBookingRescheduleAttempt(
            sessionID: "session-a",
            bookingID: 91,
            startAt: try Self.date("2026-07-16T13:00:00Z"),
            timeZone: "America/New_York",
            idempotencyKey: UUID()
        )
        try await context.cache.write(
            legacy,
            key: "booking.reschedule.pending.v1",
            etag: nil
        )
        let foreignRepository = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: context.cache,
            sessionID: "session-b"
        )

        let foreignPending = try await foreignRepository.pendingBookingRescheduleAttempt()
        let legacyAfterForeignRead = try await context.cache.readLegacyCommand(
            "booking.reschedule.pending.v1",
            as: PendingBookingRescheduleAttempt.self
        )?.value
        let owningRepository = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: context.cache,
            sessionID: "session-a"
        )
        let migrated = try await owningRepository.pendingBookingRescheduleAttempt()
        let legacyAfterMigration = try await context.cache.readLegacyCommand(
            "booking.reschedule.pending.v1",
            as: PendingBookingRescheduleAttempt.self
        )
        let durable = try await context.cache.readCommand(
            "booking.reschedule.pending.v2.session-a",
            as: PendingBookingRescheduleAttempt.self
        )?.value

        XCTAssertNil(foreignPending)
        XCTAssertEqual(legacyAfterForeignRead, legacy)
        XCTAssertEqual(migrated, legacy)
        XCTAssertNil(legacyAfterMigration)
        XCTAssertEqual(durable, legacy)
    }

    func testDefinitiveRejectionCanDiscardOnlyTheMatchingAttempt() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let repository = OwnerRepository(
            client: OwnerMutationContractClient([]),
            cache: context.cache,
            sessionID: "session-a"
        )
        let attempt = try await repository.prepareBookingReschedule(
            bookingID: 91,
            startAt: try Self.date("2026-07-16T13:00:00Z"),
            timeZone: "America/New_York"
        )
        let other = PendingBookingRescheduleAttempt(
            sessionID: attempt.sessionID,
            bookingID: attempt.bookingID,
            startAt: attempt.startAt,
            timeZone: attempt.timeZone,
            idempotencyKey: UUID()
        )

        let discardedOther = try await repository.discardPendingBookingReschedule(
            ifMatches: other
        )
        let stillPending = try await repository.pendingBookingRescheduleAttempt()
        let discardedAttempt = try await repository.discardPendingBookingReschedule(
            ifMatches: attempt
        )
        let pendingAfterDiscard = try await repository.pendingBookingRescheduleAttempt()
        XCTAssertFalse(discardedOther)
        XCTAssertEqual(stillPending, attempt)
        XCTAssertTrue(discardedAttempt)
        XCTAssertNil(pendingAfterDiscard)
    }

    func testWorkflowStatusAndRetryRepositoryReadsStayUncached() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let client = OwnerMutationContractClient([
            .value(Data(Self.workflowStatusJSON.utf8)),
            .value(Data(Self.workflowStatusJSON.utf8)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)
        let workflowID = try XCTUnwrap(
            UUID(uuidString: "86aa7d32-740e-4993-af8d-438bb80c366b")
        )

        let status = try await repository.bookingWorkflowStatus(id: workflowID)
        let retried = try await repository.retryBookingWorkflow(id: workflowID)
        let requests = await client.capturedRequests()

        XCTAssertEqual(status.workflowID, workflowID)
        XCTAssertEqual(retried.workflowID, workflowID)
        XCTAssertEqual(requests.map(\.method), [.get, .post])
        XCTAssertEqual(
            requests.map(\.path),
            [
                "/api/v1/booking-workflows/\(workflowID.uuidString.lowercased())",
                "/api/v1/booking-workflows/\(workflowID.uuidString.lowercased())/retry",
            ]
        )
    }

    func testCurrentSessionAndBookingSlotsReadsAlwaysUseNetwork() async throws {
        let context = makeCache()
        defer { try? FileManager.default.removeItem(at: context.root) }
        let currentSessionJSON = """
        {
          "workspace": {
            "cache_namespace":"workspace_test",
            "slug":"studio",
            "display_name":"Test Studio",
            "api_base_url":"https://studio.example.test",
            "brand_accent_hex":null,
            "time_zone":"America/New_York",
            "currency_code":"USD"
          },
          "principal": {
            "id":"owner",
            "kind":"studio_owner",
            "display_name":"Owner",
            "email":null,
            "scopes":["studio:read","studio:write"]
          },
          "available_commands":["booking.reschedule"]
        }
        """
        let slotsJSON = """
        {
          "event_type_id":8,
          "day":"2026-07-16",
          "time_zone":"America/New_York",
          "reschedule_booking_id":91,
          "slots":[]
        }
        """
        let client = OwnerMutationContractClient([
            .value(Data(currentSessionJSON.utf8)),
            .value(Data(slotsJSON.utf8)),
            .value(Data(slotsJSON.utf8)),
        ])
        let repository = OwnerRepository(client: client, cache: context.cache)

        let session = try await repository.currentSession()
        let first = try await repository.bookingSlots(
            eventTypeID: 8,
            day: LocalDate(rawValue: "2026-07-16"),
            rescheduleBookingID: 91
        )
        let second = try await repository.bookingSlots(
            eventTypeID: 8,
            day: LocalDate(rawValue: "2026-07-16"),
            rescheduleBookingID: 91
        )
        let requests = await client.capturedRequests()

        XCTAssertEqual(session.principal.kind, .studioOwner)
        XCTAssertEqual(first, second)
        XCTAssertEqual(
            requests.map(\.path),
            [
                "/api/v1/me",
                "/api/v1/event-types/8/slots",
                "/api/v1/event-types/8/slots",
            ]
        )
    }

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

    private static func date(_ value: String) throws -> Date {
        try MiseJSON.decoder().decode(Date.self, from: Data("\"\(value)\"".utf8))
    }

    private static let rescheduleResultJSON = """
    {"status":"rescheduled","workflow_id":"86aa7d32-740e-4993-af8d-438bb80c366b","delivery_status":"pending","original_booking_id":91,"replacement_booking_id":92,"start_at":"2026-07-16T13:00:00Z","end_at":"2026-07-16T14:00:00Z"}
    """

    private static let workflowStatusJSON = """
    {
      "workflow_id":"86aa7d32-740e-4993-af8d-438bb80c366b",
      "status":"retry",
      "source_booking_id":91,
      "replacement_booking_id":92,
      "effects":[{
        "kind":"client_cancel_ics",
        "sequence":10,
        "status":"retry",
        "attempts":2,
        "next_attempt_at":"2026-07-13T18:30:05Z",
        "completed_at":null,
        "provider_ref":null,
        "error_class":"TimeoutError",
        "error_code":"exception"
      }]
    }
    """

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

private struct CapturedOwnerMutationRequest: Sendable {
    let method: HTTPMethod
    let path: String
    let queryItems: [APIQueryItem]
    let body: Data?
    let idempotencyKey: UUID?
}

private actor OwnerMutationContractClient: APIClientProtocol {
    enum Reply: Sendable {
        case value(Data)
        case failure(APIError)
        case inspect(@Sendable (CapturedOwnerMutationRequest) async throws -> Data)
    }

    private var replies: [Reply]
    private var requests: [CapturedOwnerMutationRequest] = []

    init(_ replies: [Reply]) {
        self.replies = replies
    }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        let request = CapturedOwnerMutationRequest(
            method: endpoint.method,
            path: endpoint.path,
            queryItems: endpoint.queryItems,
            body: endpoint.body,
            idempotencyKey: endpoint.idempotencyKey
        )
        requests.append(request)
        let reply = replies.removeFirst()
        switch reply {
        case let .value(data):
            return try MiseJSON.decoder().decode(Response.self, from: data)
        case let .failure(error):
            throw error
        case let .inspect(inspect):
            let data = try await inspect(request)
            return try MiseJSON.decoder().decode(Response.self, from: data)
        }
    }

    func capturedRequests() -> [CapturedOwnerMutationRequest] {
        requests
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
