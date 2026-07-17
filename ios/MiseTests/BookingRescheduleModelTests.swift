import XCTest
@testable import Mise

final class BookingRescheduleModelTests: XCTestCase {
    @MainActor
    func testCapabilityRequiresExactFreshOwnerWriteCommandAndSessionID() async {
        let harness = RescheduleHarness(session: Self.session())
        let model = makeModel(harness: harness)

        XCTAssertFalse(model.canReschedule)
        await model.refreshCapability()
        XCTAssertTrue(model.canReschedule)

        await harness.setSession(Self.session(commands: ["Booking.Reschedule"]))
        await model.refreshCapability()
        XCTAssertFalse(model.canReschedule)

        await harness.setSessionFailure(.offline)
        await model.refreshCapability()
        XCTAssertFalse(model.canReschedule)

        let noSessionModel = makeModel(
            session: Self.session(sessionID: nil),
            harness: RescheduleHarness(session: Self.session())
        )
        await noSessionModel.refreshCapability()
        XCTAssertFalse(noSessionModel.canReschedule)
    }

    @MainActor
    func testAvailabilityUsesSourceBookingAndRejectsCurrentStart() async {
        let booking = Self.booking()
        let response = Self.availability(
            booking: booking,
            starts: [Self.targetStart]
        )
        let harness = RescheduleHarness(session: Self.session(), slots: response)
        let model = makeModel(harness: harness)
        await model.refreshCapability()

        await model.loadSlots(for: booking, on: Self.targetStart)

        XCTAssertEqual(model.availability, response)
        XCTAssertNil(model.selectedSlot)
        let calls = await harness.slotCalls()
        XCTAssertEqual(
            calls,
            [
                SlotCall(
                    eventTypeID: booking.eventTypeID,
                    day: LocalDate(rawValue: "2026-07-16"),
                    bookingID: booking.id
                ),
            ]
        )

        let unsafe = Self.availability(
            booking: booking,
            day: "2026-07-15",
            starts: [booking.startAt]
        )
        await harness.setSlots(unsafe)
        await model.loadSlots(for: booking, on: booking.startAt)
        XCTAssertNil(model.availability)
        XCTAssertNotNil(model.notice)
    }

    @MainActor
    func testAvailabilityRejectsUnorderedDuplicateFractionalAndWrongDaySlots() async {
        let booking = Self.booking()
        let harness = RescheduleHarness(session: Self.session())
        let model = makeModel(harness: harness)
        await model.refreshCapability()
        let later = Self.targetStart.addingTimeInterval(3_600)
        let invalidFeeds = [
            EventTypeSlots(
                eventTypeID: booking.eventTypeID,
                day: LocalDate(rawValue: "2026-07-16"),
                timeZone: "UTC",
                rescheduleBookingID: booking.id,
                slots: [Self.slot(later), Self.slot(Self.targetStart)]
            ),
            EventTypeSlots(
                eventTypeID: booking.eventTypeID,
                day: LocalDate(rawValue: "2026-07-16"),
                timeZone: "UTC",
                rescheduleBookingID: booking.id,
                slots: [
                    Self.slot(Self.targetStart),
                    BookingSlot(
                        startAt: Self.targetStart,
                        endAt: Self.targetStart.addingTimeInterval(7_200)
                    ),
                ]
            ),
            EventTypeSlots(
                eventTypeID: booking.eventTypeID,
                day: LocalDate(rawValue: "2026-07-16"),
                timeZone: "UTC",
                rescheduleBookingID: booking.id,
                slots: [Self.slot(Self.targetStart.addingTimeInterval(0.5))]
            ),
            Self.availability(
                booking: booking,
                day: "2026-07-16",
                starts: [Self.date("2026-07-17T11:00:00Z")]
            ),
        ]

        for feed in invalidFeeds {
            await harness.setSlots(feed)
            await model.loadSlots(for: booking, on: Self.targetStart)
            XCTAssertNil(model.availability)
        }
    }

    @MainActor
    func testSubmissionRequiresAvailabilityForTheExactBooking() async {
        let booking = Self.booking()
        let harness = RescheduleHarness(
            session: Self.session(),
            slots: Self.availability(booking: booking, starts: [Self.targetStart])
        )
        let model = makeModel(harness: harness)
        await model.refreshCapability()
        await model.loadSlots(for: booking, on: Self.targetStart)
        model.selectSlot(Self.slot(Self.targetStart))

        let submitted = await model.submitSelected(for: Self.booking(id: 92))

        XCTAssertFalse(submitted)
        XCTAssertNil(model.pendingAttempt)
        let submissions = await harness.submissions()
        XCTAssertTrue(submissions.isEmpty)
    }

    @MainActor
    func testLateAvailabilityCannotReplaceNewerDay() async {
        let booking = Self.booking()
        let latch = SlotRequestLatch()
        let harness = RescheduleHarness(session: Self.session())
        let dependencies = makeDependencies(harness: harness, slots: {
            eventTypeID, day, bookingID in
            try await latch.request(
                SlotCall(
                    eventTypeID: eventTypeID,
                    day: day,
                    bookingID: bookingID
                )
            )
        })
        let model = BookingRescheduleModel(
            session: Self.session(),
            commands: makeCommands(),
            dependencies: dependencies
        )
        await model.refreshCapability()

        let firstDay = Self.date("2026-07-16T11:00:00Z")
        let secondDay = Self.date("2026-07-17T11:00:00Z")
        let first = Task { @MainActor in
            await model.loadSlots(for: booking, on: firstDay)
        }
        await latch.waitForCalls(1)
        let second = Task { @MainActor in
            await model.loadSlots(for: booking, on: secondDay)
        }
        await latch.waitForCalls(2)

        await latch.succeed(
            day: "2026-07-17",
            with: Self.availability(
                booking: booking,
                day: "2026-07-17",
                starts: [secondDay]
            )
        )
        await second.value
        await latch.succeed(
            day: "2026-07-16",
            with: Self.availability(
                booking: booking,
                day: "2026-07-16",
                starts: [firstDay]
            )
        )
        await first.value

        XCTAssertEqual(model.availability?.day, LocalDate(rawValue: "2026-07-17"))
    }

    @MainActor
    func testAmbiguousRetryReusesExactPersistedAttemptThenCommits() async {
        let booking = Self.booking()
        let result = Self.result()
        let harness = RescheduleHarness(
            session: Self.session(),
            slots: Self.availability(booking: booking, starts: [Self.targetStart]),
            submitReplies: [
                .failure(.transport(.timedOut)),
                .result(result),
            ],
            workflow: Self.workflow(.pending)
        )
        let model = makeModel(harness: harness)
        await model.refreshCapability()
        await model.loadSlots(for: booking, on: Self.targetStart)
        model.selectSlot(Self.slot(Self.targetStart))

        let firstSubmissionSucceeded = await model.submitSelected(for: booking)
        XCTAssertFalse(firstSubmissionSucceeded)
        XCTAssertNotNil(model.pendingAttempt)
        XCTAssertTrue(model.hasAmbiguousAttempt)
        XCTAssertEqual(
            model.notice,
            "Mise may have moved this booking. Try the same request again to check safely. "
                + "Don’t choose another time yet."
        )

        let retrySucceeded = await model.retryPending()
        XCTAssertTrue(retrySucceeded)
        XCTAssertNil(model.pendingAttempt)
        XCTAssertEqual(model.latestResult, result)
        XCTAssertTrue(model.visibleBookings(from: [booking]).isEmpty)
        let submissions = await harness.submissions()
        XCTAssertEqual(submissions.count, 2)
        XCTAssertEqual(submissions[0], submissions[1])
        XCTAssertEqual(
            submissions[0].idempotencyKey,
            UUID(uuidString: "11111111-2222-3333-4444-555555555555")
        )
    }

    @MainActor
    func testDefinitiveSlotConflictClearsAttemptAndSelection() async {
        let booking = Self.booking()
        let harness = RescheduleHarness(
            session: Self.session(),
            slots: Self.availability(booking: booking, starts: [Self.targetStart]),
            submitReplies: [
                .failure(
                    .conflict(
                        APIProblem(
                            status: 409,
                            code: "booking.slot_unavailable",
                            detail: "That time is no longer available."
                        )
                    )
                ),
            ]
        )
        let model = makeModel(harness: harness)
        await model.refreshCapability()
        await model.loadSlots(for: booking, on: Self.targetStart)
        model.selectSlot(Self.slot(Self.targetStart))

        let submissionSucceeded = await model.submitSelected(for: booking)
        XCTAssertFalse(submissionSucceeded)

        XCTAssertNil(model.pendingAttempt)
        XCTAssertNil(model.selectedSlot)
        XCTAssertEqual(
            model.notice,
            "That time is no longer available. Choose another available time."
        )
        let discardCount = await harness.discardCount()
        XCTAssertEqual(discardCount, 1)
    }

    @MainActor
    func testMalformedSuccessRemainsAmbiguousAndKeepsSourceVisible() async {
        let booking = Self.booking()
        let harness = RescheduleHarness(
            session: Self.session(),
            slots: Self.availability(booking: booking, starts: [Self.targetStart]),
            submitReplies: [.failure(.decoding("bad 200"))]
        )
        let model = makeModel(harness: harness)
        await model.refreshCapability()
        await model.loadSlots(for: booking, on: Self.targetStart)
        model.selectSlot(Self.slot(Self.targetStart))

        let submissionSucceeded = await model.submitSelected(for: booking)
        XCTAssertFalse(submissionSucceeded)

        XCTAssertNotNil(model.pendingAttempt)
        XCTAssertEqual(model.visibleBookings(from: [booking]), [booking])
        let discardCount = await harness.discardCount()
        XCTAssertEqual(discardCount, 0)
    }

    @MainActor
    func testUnknownHTTPFailureRetainsExactRecoveryAttempt() async {
        let booking = Self.booking()
        let harness = RescheduleHarness(
            session: Self.session(),
            slots: Self.availability(booking: booking, starts: [Self.targetStart]),
            submitReplies: [.failure(.http(status: 408, problem: nil))]
        )
        let model = makeModel(harness: harness)
        await model.refreshCapability()
        await model.loadSlots(for: booking, on: Self.targetStart)
        model.selectSlot(Self.slot(Self.targetStart))

        let submitted = await model.submitSelected(for: booking)

        XCTAssertFalse(submitted)
        XCTAssertEqual(model.pendingAttempt, Self.attempt())
        let discardCount = await harness.discardCount()
        XCTAssertEqual(discardCount, 0)
    }

    @MainActor
    func testRestoreKeepsPendingRecoveryAndCommittedWorkflowVisible() async {
        let result = Self.result()
        let attempt = Self.attempt()
        let harness = RescheduleHarness(
            session: Self.session(),
            pending: attempt,
            latest: result,
            workflow: Self.workflow(.retry)
        )
        let model = makeModel(harness: harness)

        await model.restore()

        XCTAssertEqual(model.pendingAttempt, attempt)
        XCTAssertEqual(model.latestResult, result)
        XCTAssertEqual(model.workflowStatus?.status, .retry)
        XCTAssertEqual(
            model.workflowMessage,
            "Booking moved. Some updates were delayed; Mise will retry automatically."
        )
    }

    @MainActor
    func testUnreadableRecoveryJournalBlocksEveryNewReschedule() async {
        let harness = RescheduleHarness(session: Self.session())
        let model = BookingRescheduleModel(
            session: Self.session(),
            commands: makeCommands(),
            dependencies: makeDependencies(
                harness: harness,
                pending: { throw TenantJSONCacheError.invalidEnvelope }
            )
        )

        await model.restore()
        await model.refreshCapability()

        XCTAssertTrue(model.recoveryIsBlocked)
        XCTAssertTrue(model.hasAmbiguousAttempt)
        XCTAssertFalse(model.canStartNewReschedule)
        XCTAssertTrue(model.notice?.contains("New reschedules are blocked") == true)
    }

    @MainActor
    func testUnreadableCommittedWorkflowHandleBlocksEveryNewReschedule() async {
        let harness = RescheduleHarness(session: Self.session())
        let model = BookingRescheduleModel(
            session: Self.session(),
            commands: makeCommands(),
            dependencies: makeDependencies(
                harness: harness,
                latestResult: { throw TenantJSONCacheError.invalidEnvelope }
            )
        )

        await model.restore()
        await model.refreshCapability()

        XCTAssertNil(model.latestResult)
        XCTAssertTrue(model.recoveryIsBlocked)
        XCTAssertTrue(model.hasAmbiguousAttempt)
        XCTAssertFalse(model.canStartNewReschedule)
        XCTAssertTrue(model.notice?.contains("New reschedules are blocked") == true)
    }

    @MainActor
    func testDefinitivePendingDiscardCannotClearWorkflowRecoveryBlock() async {
        let harness = RescheduleHarness(
            session: Self.session(),
            pending: Self.attempt(),
            submitReplies: [
                .failure(
                    .conflict(
                        APIProblem(
                            status: 409,
                            code: "booking.slot_unavailable",
                            detail: "That time is no longer available."
                        )
                    )
                ),
            ]
        )
        let model = BookingRescheduleModel(
            session: Self.session(),
            commands: makeCommands(),
            dependencies: makeDependencies(
                harness: harness,
                latestResult: { throw TenantJSONCacheError.invalidEnvelope }
            )
        )
        await model.restore()
        await model.refreshCapability()

        let replayed = await model.retryPending()

        XCTAssertFalse(replayed)
        XCTAssertNil(model.pendingAttempt)
        XCTAssertTrue(model.recoveryIsBlocked)
        XCTAssertTrue(model.hasAmbiguousAttempt)
        XCTAssertFalse(model.canStartNewReschedule)
    }

    @MainActor
    func testBlockedWorkflowRetryUsesDedicatedCommandAndUpdatesState() async {
        let result = Self.result()
        let harness = RescheduleHarness(
            session: Self.session(),
            latest: result,
            workflow: Self.workflow(.blocked),
            retryWorkflow: Self.workflow(.pending)
        )
        let commands = makeCommands()
        let model = makeModel(commands: commands, harness: harness)
        await model.restore()
        await model.refreshCapability()
        XCTAssertTrue(model.canRetryWorkflow)

        await model.retryBlockedWorkflow()

        XCTAssertEqual(model.workflowStatus?.status, .pending)
        XCTAssertFalse(commands.isBookingInFlight(result.replacementBookingID))
        let retryCount = await harness.retryCount()
        XCTAssertEqual(retryCount, 1)
    }

    @MainActor
    func testNonterminalWorkflowBlocksAnotherRescheduleUntilSucceeded() async {
        let result = Self.result()
        let harness = RescheduleHarness(
            session: Self.session(),
            latest: result,
            workflow: Self.workflow(.blocked)
        )
        let model = makeModel(harness: harness)
        await model.restore()
        await model.refreshCapability()
        XCTAssertFalse(model.canStartNewReschedule)

        await harness.setWorkflow(Self.workflow(.succeeded))
        await model.refreshWorkflowStatus()

        XCTAssertTrue(model.canStartNewReschedule)
    }

    @MainActor
    func testAmbiguousWorkflowRetryRequiresSuccessfulGetBeforeAnotherPost() async {
        let harness = RescheduleHarness(
            session: Self.session(),
            latest: Self.result(),
            workflow: Self.workflow(.blocked)
        )
        let model = makeModel(harness: harness)
        await model.restore()
        await model.refreshCapability()
        await harness.setWorkflow(nil)

        await model.retryBlockedWorkflow()

        XCTAssertNil(model.workflowStatus)
        XCTAssertTrue(model.workflowRetryOutcomeUnknown)
        XCTAssertFalse(model.canRetryWorkflow)
        await model.retryBlockedWorkflow()
        let retryCount = await harness.retryCount()
        XCTAssertEqual(retryCount, 1)
    }

    @MainActor
    private func makeModel(
        session: CurrentSession? = nil,
        commands: OwnerCommandModel? = nil,
        harness: RescheduleHarness
    ) -> BookingRescheduleModel {
        BookingRescheduleModel(
            session: session ?? Self.session(),
            commands: commands ?? makeCommands(),
            dependencies: makeDependencies(harness: harness)
        )
    }

    @MainActor
    private func makeCommands() -> OwnerCommandModel {
        OwnerCommandModel(
            canWrite: true,
            setTaskCompletion: { id, done in
                TaskCompletion(id: id, done: done, completedAt: nil)
            },
            cancelBooking: { id in
                Self.booking(id: id, status: .cancelled)
            }
        )
    }

    private func makeDependencies(
        harness: RescheduleHarness,
        slots: (@Sendable (Int64, LocalDate, Int64) async throws -> EventTypeSlots)? = nil,
        pending: (@Sendable () async throws -> PendingBookingRescheduleAttempt?)? = nil,
        latestResult: (@Sendable () async throws -> BookingRescheduleResult?)? = nil
    ) -> BookingRescheduleDependencies {
        BookingRescheduleDependencies(
            currentSession: { try await harness.currentSession() },
            slots: slots ?? { eventTypeID, day, bookingID in
                try await harness.slots(
                    eventTypeID,
                    day: day,
                    bookingID: bookingID
                )
            },
            prepare: { bookingID, startAt, timeZone in
                try await harness.prepare(
                    bookingID,
                    startAt: startAt,
                    timeZone: timeZone
                )
            },
            pending: pending ?? { try await harness.pending() },
            submit: { try await harness.submit($0) },
            discard: { try await harness.discard($0) },
            latestResult: latestResult ?? { try await harness.latestResult() },
            workflow: { try await harness.workflow($0) },
            retryWorkflow: { try await harness.retryWorkflow($0) }
        )
    }

    private static func session(
        commands: [String] = ["booking.reschedule"],
        sessionID: String? = "session_01J"
    ) -> CurrentSession {
        CurrentSession(
            workspace: WorkspaceContext(
                cacheNamespace: "tenant_42",
                slug: "north-star",
                displayName: "North Star",
                apiBaseURL: URL(string: "https://north-star.example.test")!,
                brandAccentHex: nil,
                timeZone: "UTC",
                currencyCode: "USD"
            ),
            principal: Principal(
                id: "studio_owner",
                kind: .studioOwner,
                displayName: "North Star",
                email: nil,
                scopes: ["studio:read", "studio:write"]
            ),
            availableCommands: commands,
            sessionID: sessionID
        )
    }

    private static func booking(
        id: Int64 = 91,
        status: BookingStatus = .confirmed
    ) -> Booking {
        Booking(
            id: id,
            eventTypeID: 8,
            eventName: "Menu tasting",
            name: "Rossi Trattoria",
            email: "ops@rossi.test",
            phone: nil,
            notes: nil,
            startAt: date("2026-07-15T10:00:00Z"),
            endAt: date("2026-07-15T11:00:00Z"),
            timeZone: "America/New_York",
            status: status,
            clientID: nil,
            projectID: nil,
            rescheduledFromID: nil,
            cancelReason: nil,
            cancelledAt: nil,
            createdAt: date("2026-07-01T12:00:00Z")
        )
    }

    private static let targetStart = date("2026-07-16T11:00:00Z")

    private static func slot(_ start: Date) -> BookingSlot {
        BookingSlot(startAt: start, endAt: start.addingTimeInterval(3_600))
    }

    private static func availability(
        booking: Booking,
        day: String = "2026-07-16",
        starts: [Date]
    ) -> EventTypeSlots {
        EventTypeSlots(
            eventTypeID: booking.eventTypeID,
            day: LocalDate(rawValue: day),
            timeZone: "UTC",
            rescheduleBookingID: booking.id,
            slots: starts.map(slot)
        )
    }

    private static func attempt() -> PendingBookingRescheduleAttempt {
        PendingBookingRescheduleAttempt(
            sessionID: "session_01J",
            bookingID: 91,
            startAt: targetStart,
            timeZone: "America/New_York",
            idempotencyKey: UUID(
                uuidString: "11111111-2222-3333-4444-555555555555"
            )!
        )
    }

    private static func result() -> BookingRescheduleResult {
        BookingRescheduleResult(
            status: .rescheduled,
            workflowID: UUID(
                uuidString: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
            )!,
            deliveryStatus: .pending,
            originalBookingID: 91,
            replacementBookingID: 92,
            startAt: targetStart,
            endAt: targetStart.addingTimeInterval(3_600)
        )
    }

    private static func workflow(_ state: BookingWorkflowState) -> BookingWorkflowStatus {
        BookingWorkflowStatus(
            workflowID: result().workflowID,
            status: state,
            sourceBookingID: 91,
            replacementBookingID: 92,
            effects: [
                BookingWorkflowEffect(
                    kind: .clientCancelICS,
                    sequence: 10,
                    status: state == .blocked ? .blocked : .pending,
                    attempts: 0,
                    nextAttemptAt: nil,
                    completedAt: nil,
                    providerRef: nil,
                    errorClass: nil,
                    errorCode: nil
                ),
            ]
        )
    }

    private static func date(_ value: String) -> Date {
        ISO8601DateFormatter().date(from: value)!
    }
}

private struct SlotCall: Equatable, Sendable {
    let eventTypeID: Int64
    let day: LocalDate
    let bookingID: Int64
}

private enum HarnessFailure: Error, Sendable {
    case offline
}

private actor RescheduleHarness {
    enum SubmitReply: Sendable {
        case result(BookingRescheduleResult)
        case failure(APIError)
    }

    private var sessionValue: CurrentSession
    private var sessionFailure: HarnessFailure?
    private var slotsValue: EventTypeSlots?
    private var slotCallValues: [SlotCall] = []
    private var pendingValue: PendingBookingRescheduleAttempt?
    private var latestValue: BookingRescheduleResult?
    private var submitReplies: [SubmitReply]
    private var submissionValues: [PendingBookingRescheduleAttempt] = []
    private var discardValues = 0
    private var workflowValue: BookingWorkflowStatus?
    private var retryWorkflowValue: BookingWorkflowStatus?
    private var retryValues = 0

    init(
        session: CurrentSession,
        slots: EventTypeSlots? = nil,
        pending: PendingBookingRescheduleAttempt? = nil,
        latest: BookingRescheduleResult? = nil,
        submitReplies: [SubmitReply] = [],
        workflow: BookingWorkflowStatus? = nil,
        retryWorkflow: BookingWorkflowStatus? = nil
    ) {
        sessionValue = session
        slotsValue = slots
        pendingValue = pending
        latestValue = latest
        self.submitReplies = submitReplies
        workflowValue = workflow
        retryWorkflowValue = retryWorkflow
    }

    func setSession(_ value: CurrentSession) {
        sessionValue = value
        sessionFailure = nil
    }

    func setSessionFailure(_ value: HarnessFailure) {
        sessionFailure = value
    }

    func currentSession() throws -> CurrentSession {
        if let sessionFailure { throw sessionFailure }
        return sessionValue
    }

    func setSlots(_ value: EventTypeSlots) {
        slotsValue = value
    }

    func setWorkflow(_ value: BookingWorkflowStatus?) {
        workflowValue = value
    }

    func slots(
        _ eventTypeID: Int64,
        day: LocalDate,
        bookingID: Int64
    ) throws -> EventTypeSlots {
        slotCallValues.append(
            SlotCall(
                eventTypeID: eventTypeID,
                day: day,
                bookingID: bookingID
            )
        )
        guard let slotsValue else { throw HarnessFailure.offline }
        return slotsValue
    }

    func slotCalls() -> [SlotCall] {
        slotCallValues
    }

    func prepare(
        _ bookingID: Int64,
        startAt: Date,
        timeZone: String
    ) throws -> PendingBookingRescheduleAttempt {
        if let pendingValue {
            return pendingValue
        }
        let value = PendingBookingRescheduleAttempt(
            sessionID: sessionValue.sessionID ?? "",
            bookingID: bookingID,
            startAt: MiseJSON.wholeSecondUTCDate(startAt),
            timeZone: timeZone,
            idempotencyKey: UUID(
                uuidString: "11111111-2222-3333-4444-555555555555"
            )!
        )
        pendingValue = value
        return value
    }

    func pending() -> PendingBookingRescheduleAttempt? {
        pendingValue
    }

    func submit(
        _ attempt: PendingBookingRescheduleAttempt
    ) throws -> BookingRescheduleResult {
        submissionValues.append(attempt)
        guard !submitReplies.isEmpty else { throw HarnessFailure.offline }
        switch submitReplies.removeFirst() {
        case let .result(result):
            pendingValue = nil
            latestValue = result
            return result
        case let .failure(error):
            throw error
        }
    }

    func submissions() -> [PendingBookingRescheduleAttempt] {
        submissionValues
    }

    func discard(_ attempt: PendingBookingRescheduleAttempt) -> Bool {
        guard pendingValue == attempt else { return false }
        pendingValue = nil
        discardValues += 1
        return true
    }

    func discardCount() -> Int {
        discardValues
    }

    func latestResult() -> BookingRescheduleResult? {
        latestValue
    }

    func workflow(_ id: UUID) throws -> BookingWorkflowStatus {
        guard let workflowValue, workflowValue.workflowID == id else {
            throw HarnessFailure.offline
        }
        return workflowValue
    }

    func retryWorkflow(_ id: UUID) throws -> BookingWorkflowStatus {
        retryValues += 1
        guard let retryWorkflowValue, retryWorkflowValue.workflowID == id else {
            throw HarnessFailure.offline
        }
        workflowValue = retryWorkflowValue
        return retryWorkflowValue
    }

    func retryCount() -> Int {
        retryValues
    }
}

private actor SlotRequestLatch {
    private var calls: [SlotCall] = []
    private var continuations: [String: CheckedContinuation<EventTypeSlots, any Error>] = [:]
    private var observers: [CheckedContinuation<Void, Never>] = []

    func request(_ call: SlotCall) async throws -> EventTypeSlots {
        calls.append(call)
        let currentObservers = observers
        observers.removeAll()
        currentObservers.forEach { $0.resume() }
        return try await withCheckedThrowingContinuation {
            continuations[call.day.rawValue] = $0
        }
    }

    func waitForCalls(_ count: Int) async {
        while calls.count < count {
            await withCheckedContinuation { observers.append($0) }
        }
    }

    func succeed(day: String, with response: EventTypeSlots) {
        continuations.removeValue(forKey: day)?.resume(returning: response)
    }
}
