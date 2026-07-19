import XCTest
@testable import Mise

final class OwnerCommandModelTests: XCTestCase {
    @MainActor
    func testTaskCompletionIsOptimisticThenConfirmsServerResponse() async {
        let latch = MutationLatch<TaskCompletion>()
        let model = makeModel(task: { id, _ in try await latch.wait(id: id) })
        let task = Self.task(id: 42)

        let operation = Task { @MainActor in await model.completeTask(task) }
        await latch.waitUntilCalled()
        XCTAssertTrue(model.visibleTasks(from: [task]).isEmpty)
        XCTAssertTrue(model.isTaskInFlight(task.id))
        XCTAssertEqual(model.justCompletedTask, task)

        await latch.succeed(TaskCompletion(id: task.id, done: true, completedAt: Date()))
        let didComplete = await operation.value
        XCTAssertTrue(didComplete)
        XCTAssertFalse(model.isTaskInFlight(task.id))
        XCTAssertNil(model.taskNotice)
    }

    @MainActor
    func testTaskFailureRollsBackOptimisticState() async {
        let model = makeModel(task: { _, _ in throw TestFailure.offline })
        let task = Self.task(id: 43)

        let didComplete = await model.completeTask(task)
        XCTAssertFalse(didComplete)

        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
        XCTAssertNil(model.justCompletedTask)
        XCTAssertEqual(
            model.taskNotice,
            "Couldn’t confirm task completion. Refresh, then retry if it is still shown."
        )
    }

    @MainActor
    func testTaskCompletionSuppressesDuplicateTapForSameID() async {
        let latch = MutationLatch<TaskCompletion>()
        let model = makeModel(task: { id, _ in try await latch.wait(id: id) })
        let task = Self.task(id: 44)

        let first = Task { @MainActor in await model.completeTask(task) }
        await latch.waitUntilCalled()
        let duplicateResult = await model.completeTask(task)
        XCTAssertFalse(duplicateResult)
        let callCount = await latch.callCount()
        XCTAssertEqual(callCount, 1)

        await latch.succeed(TaskCompletion(id: task.id, done: true, completedAt: Date()))
        let didComplete = await first.value
        XCTAssertTrue(didComplete)
    }

    @MainActor
    func testTaskCompletionSerializesDifferentIDsBehindSharedUndoSlot() async {
        let latch = MutationLatch<TaskCompletion>()
        let model = makeModel(task: { id, _ in try await latch.wait(id: id) })
        let firstTask = Self.task(id: 440)
        let secondTask = Self.task(id: 441)

        let first = Task { @MainActor in await model.completeTask(firstTask) }
        await latch.waitUntilCalled()
        XCTAssertTrue(model.isTaskMutationInFlight)
        let secondResult = await model.completeTask(secondTask)
        let callCount = await latch.callCount()
        XCTAssertFalse(secondResult)
        XCTAssertEqual(callCount, 1)

        await latch.succeed(TaskCompletion(id: firstTask.id, done: true, completedAt: Date()))
        let firstResult = await first.value
        XCTAssertTrue(firstResult)
        XCTAssertEqual(model.justCompletedTask, firstTask)
    }

    @MainActor
    func testUndoReopensJustCompletedTask() async {
        let recorder = TaskMutationRecorder()
        let model = makeModel(task: { id, completed in
            await recorder.mutate(id: id, completed: completed)
        })
        let task = Self.task(id: 45)

        let didComplete = await model.completeTask(task)
        let didUndo = await model.undoLastTaskCompletion()
        XCTAssertTrue(didComplete)
        XCTAssertTrue(didUndo)

        let recordedValues = await recorder.values()
        XCTAssertEqual(recordedValues, [true, false])
        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
        XCTAssertNil(model.justCompletedTask)
        XCTAssertNil(model.taskNotice)
    }

    @MainActor
    func testDuplicateUndoIsLockedBehindTheSharedTaskMutation() async {
        let latch = UndoMutationLatch()
        let model = makeModel(task: { id, completed in
            try await latch.mutate(id: id, completed: completed)
        })
        let task = Self.task(id: 450)
        let didComplete = await model.completeTask(task)
        XCTAssertTrue(didComplete)

        let firstUndo = Task { @MainActor in
            await model.undoLastTaskCompletion()
        }
        await latch.waitUntilUndoCalled()
        let duplicateResult = await model.undoLastTaskCompletion()
        let undoCalls = await latch.undoCallCount()
        XCTAssertFalse(duplicateResult)
        XCTAssertEqual(undoCalls, 1)

        await latch.succeedUndo(id: task.id)
        let didUndo = await firstUndo.value
        XCTAssertTrue(didUndo)
        XCTAssertEqual(model.visibleTasks(from: []), [task])
    }

    @MainActor
    func testSuccessfulUndoOverlaysTaskUntilFullFeedConfirmsIt() async {
        let model = makeModel()
        let task = Self.task(id: 451)

        let didComplete = await model.completeTask(task)
        let didUndo = await model.undoLastTaskCompletion()
        XCTAssertTrue(didComplete)
        XCTAssertTrue(didUndo)
        XCTAssertEqual(model.visibleTasks(from: []), [task])

        model.acknowledgeReopenedTasks(in: [])
        XCTAssertEqual(model.visibleTasks(from: []), [task])

        model.acknowledgeReopenedTasks(in: [task])
        XCTAssertTrue(model.visibleTasks(from: []).isEmpty)
        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
    }

    @MainActor
    func testAmbiguousUndoRetainsRetryUntilRefreshShowsTaskOpen() async {
        let model = makeModel(task: { id, completed in
            guard completed else { throw TestFailure.offline }
            return TaskCompletion(id: id, done: true, completedAt: Date())
        })
        let task = Self.task(id: 48)

        let didComplete = await model.completeTask(task)
        let didUndo = await model.undoLastTaskCompletion()

        XCTAssertTrue(didComplete)
        XCTAssertFalse(didUndo)
        XCTAssertEqual(model.justCompletedTask, task)
        XCTAssertEqual(
            model.taskNotice,
            "Couldn’t confirm the task was reopened. Refresh, then retry Undo."
        )

        let generation = model.taskMutationGeneration
        XCTAssertTrue(model.reconcileDashboardTasks(
            with: [task],
            ifUnchangedSince: generation
        ))

        XCTAssertNil(model.justCompletedTask)
        XCTAssertNil(model.taskNotice)
        XCTAssertEqual(model.visibleTasks(from: []), [task])
        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
    }

    @MainActor
    func testTaskFeedRefreshCannotReleaseOverlayBeforeStaleHomeAndUndo() async {
        let recorder = TaskMutationRecorder()
        let model = makeModel(task: { id, completed in
            await recorder.mutate(id: id, completed: completed)
        })
        let task = Self.task(id: 49)
        let staleHome = [task]
        let initialInbox = [task]

        let didComplete = await model.completeTask(task)
        XCTAssertTrue(didComplete)
        XCTAssertTrue(model.visibleTasks(from: staleHome).isEmpty)
        XCTAssertTrue(model.visibleTasks(from: initialInbox).isEmpty)

        let refreshedInbox: [TaskSummary] = []
        XCTAssertTrue(model.visibleTasks(from: refreshedInbox).isEmpty)
        XCTAssertTrue(model.visibleTasks(from: staleHome).isEmpty)
        XCTAssertEqual(model.justCompletedTask, task)

        let didUndo = await model.undoLastTaskCompletion()
        XCTAssertTrue(didUndo)
        let reopenedInbox = [task]
        XCTAssertEqual(model.visibleTasks(from: staleHome), [task])
        XCTAssertEqual(model.visibleTasks(from: reopenedInbox), [task])
        XCTAssertNil(model.justCompletedTask)
    }

    @MainActor
    func testDashboardResponseStartedBeforeMutationCannotReleaseOverlay() async {
        let model = makeModel()
        let task = Self.task(id: 50)
        let generationBeforeRequest = model.taskMutationGeneration

        let didComplete = await model.completeTask(task)
        XCTAssertTrue(didComplete)
        XCTAssertFalse(model.reconcileDashboardTasks(
            with: [],
            ifUnchangedSince: generationBeforeRequest
        ))
        XCTAssertTrue(model.visibleTasks(from: [task]).isEmpty)
        XCTAssertEqual(model.justCompletedTask, task)
    }

    @MainActor
    func testFreshDashboardCanReleaseConfirmedOverlay() async {
        let model = makeModel()
        let task = Self.task(id: 51)

        let didComplete = await model.completeTask(task)
        XCTAssertTrue(didComplete)
        let generation = model.taskMutationGeneration
        XCTAssertTrue(model.reconcileDashboardTasks(
            with: [],
            ifUnchangedSince: generation
        ))

        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
        XCTAssertEqual(model.justCompletedTask, task)
    }

    @MainActor
    func testDashboardCannotReleaseOverlayIntoStaleTaskFeed() async {
        let model = makeModel()
        let task = Self.task(id: 511)

        let didComplete = await model.completeTask(task)
        XCTAssertTrue(didComplete)
        let generation = model.taskMutationGeneration
        XCTAssertFalse(model.reconcileDashboardTasks(
            with: [],
            ifUnchangedSince: generation,
            taskFeedSnapshot: [task]
        ))

        XCTAssertTrue(model.visibleTasks(from: [task]).isEmpty)
        XCTAssertEqual(model.justCompletedTask, task)
    }

    @MainActor
    func testDashboardRequestGetsNoReconciliationTokenDuringMutation() async {
        let latch = MutationLatch<TaskCompletion>()
        let model = makeModel(task: { id, _ in try await latch.wait(id: id) })
        let task = Self.task(id: 512)

        let completion = Task { @MainActor in await model.completeTask(task) }
        await latch.waitUntilCalled()
        XCTAssertNil(model.dashboardReconciliationGeneration())

        await latch.succeed(TaskCompletion(id: task.id, done: true, completedAt: Date()))
        let didComplete = await completion.value
        XCTAssertTrue(didComplete)
        XCTAssertNotNil(model.dashboardReconciliationGeneration())
    }

    @MainActor
    func testDismissingUndoDoesNotReleaseStaleHomeOverlay() async {
        let model = makeModel()
        let task = Self.task(id: 52)

        let didComplete = await model.completeTask(task)
        XCTAssertTrue(didComplete)
        model.dismissTaskUndo()

        XCTAssertNil(model.justCompletedTask)
        XCTAssertTrue(model.visibleTasks(from: [task]).isEmpty)
    }

    @MainActor
    func testInvalidTaskResponseRollsBackAndWarns() async {
        let model = makeModel(task: { id, _ in
            TaskCompletion(id: id + 1, done: true, completedAt: Date())
        })
        let task = Self.task(id: 46)

        let didComplete = await model.completeTask(task)
        XCTAssertFalse(didComplete)
        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
        XCTAssertNotNil(model.taskNotice)
    }

    @MainActor
    func testTaskCompletionRequiresTimestampAndReopenRequiresNullTimestamp() async {
        let invalidCompletion = makeModel(task: { id, _ in
            TaskCompletion(id: id, done: true, completedAt: nil)
        })
        let first = Self.task(id: 53)
        let invalidCompletionResult = await invalidCompletion.completeTask(first)
        XCTAssertFalse(invalidCompletionResult)
        XCTAssertEqual(invalidCompletion.visibleTasks(from: [first]), [first])

        let invalidReopen = makeModel(task: { id, completed in
            TaskCompletion(id: id, done: completed, completedAt: Date())
        })
        let second = Self.task(id: 54)
        let completionResult = await invalidReopen.completeTask(second)
        let reopenResult = await invalidReopen.undoLastTaskCompletion()
        XCTAssertTrue(completionResult)
        XCTAssertFalse(reopenResult)
        XCTAssertTrue(invalidReopen.visibleTasks(from: [second]).isEmpty)
        XCTAssertEqual(invalidReopen.justCompletedTask, second)
    }

    @MainActor
    func testTaskCompletionRejectsOpenStateEvenWithTimestamp() async {
        let model = makeModel(task: { id, _ in
            TaskCompletion(id: id, done: false, completedAt: Date())
        })
        let task = Self.task(id: 541)

        let didComplete = await model.completeTask(task)

        XCTAssertFalse(didComplete)
        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
        XCTAssertNil(model.justCompletedTask)
        XCTAssertNotNil(model.taskNotice)
    }

    @MainActor
    func testTaskReopenRejectsWrongIDEvenWithOpenState() async {
        let model = makeModel(task: { id, completed in
            TaskCompletion(
                id: completed ? id : id + 1,
                done: completed,
                completedAt: completed ? Date() : nil
            )
        })
        let task = Self.task(id: 542)

        let didComplete = await model.completeTask(task)
        let didUndo = await model.undoLastTaskCompletion()

        XCTAssertTrue(didComplete)
        XCTAssertFalse(didUndo)
        XCTAssertTrue(model.visibleTasks(from: [task]).isEmpty)
        XCTAssertEqual(model.justCompletedTask, task)
        XCTAssertNotNil(model.taskNotice)
    }

    @MainActor
    func testFailedLaterCompletionRestoresPriorConfirmedUndo() async {
        let first = Self.task(id: 55)
        let second = Self.task(id: 56)
        let model = makeModel(task: { id, completed in
            if id == second.id { throw TestFailure.offline }
            return TaskCompletion(
                id: id,
                done: completed,
                completedAt: completed ? Date() : nil
            )
        })

        let firstResult = await model.completeTask(first)
        let secondResult = await model.completeTask(second)

        XCTAssertTrue(firstResult)
        XCTAssertFalse(secondResult)
        XCTAssertEqual(model.justCompletedTask, first)
        XCTAssertTrue(model.visibleTasks(from: [first, second]).map(\.id) == [second.id])
    }

    @MainActor
    func testBookingStaysVisibleUntilServerConfirmsCancellation() async {
        let latch = MutationLatch<Booking>()
        let model = makeModel(cancel: { id in try await latch.wait(id: id) })
        let booking = Self.booking(id: 91, status: .confirmed)

        let operation = Task { @MainActor in await model.cancelBooking(booking) }
        await latch.waitUntilCalled()
        XCTAssertEqual(model.visibleBookings(from: [booking]), [booking])
        XCTAssertTrue(model.isBookingInFlight(booking.id))

        await latch.succeed(Self.booking(id: booking.id, status: .cancelled))
        let didCancel = await operation.value
        XCTAssertTrue(didCancel)
        XCTAssertTrue(model.visibleBookings(from: [booking]).isEmpty)
        XCTAssertFalse(model.isBookingInFlight(booking.id))
    }

    @MainActor
    func testBookingCancellationSuppressesDuplicateTap() async {
        let latch = MutationLatch<Booking>()
        let model = makeModel(cancel: { id in try await latch.wait(id: id) })
        let booking = Self.booking(id: 92, status: .confirmed)

        let first = Task { @MainActor in await model.cancelBooking(booking) }
        await latch.waitUntilCalled()
        let duplicateResult = await model.cancelBooking(booking)
        XCTAssertFalse(duplicateResult)
        let callCount = await latch.callCount()
        XCTAssertEqual(callCount, 1)

        await latch.succeed(Self.booking(id: booking.id, status: .cancelled))
        let didCancel = await first.value
        XCTAssertTrue(didCancel)
    }

    @MainActor
    func testSharedBookingMutationLockSuppressesCancellation() async {
        let calls = CallCounter()
        let model = makeModel(cancel: { id in
            await calls.record()
            return Self.booking(id: id, status: .cancelled)
        })
        let booking = Self.booking(id: 96, status: .confirmed)

        XCTAssertTrue(model.beginBookingMutation(booking.id))
        let didCancel = await model.cancelBooking(booking)
        let callCount = await calls.value()
        XCTAssertFalse(didCancel)
        XCTAssertEqual(callCount, 0)
        XCTAssertTrue(model.isBookingInFlight(booking.id))

        model.endBookingMutation(booking.id)
        XCTAssertFalse(model.isBookingInFlight(booking.id))
    }

    @MainActor
    func testBookingFailureKeepsRowAndUsesAmbiguitySafeNotice() async {
        let model = makeModel(cancel: { _ in throw TestFailure.offline })
        let booking = Self.booking(id: 93, status: .confirmed)

        let didCancel = await model.cancelBooking(booking)
        XCTAssertFalse(didCancel)

        XCTAssertEqual(model.visibleBookings(from: [booking]), [booking])
        XCTAssertEqual(
            model.bookingNotice,
            "Couldn’t confirm cancellation. Refresh, then retry if it is still shown."
        )
    }

    @MainActor
    func testInvalidBookingResponseDoesNotHideRow() async {
        let model = makeModel(cancel: { id in
            Self.booking(id: id, status: .confirmed)
        })
        let booking = Self.booking(id: 95, status: .confirmed)

        let didCancel = await model.cancelBooking(booking)

        XCTAssertFalse(didCancel)
        XCTAssertEqual(model.visibleBookings(from: [booking]), [booking])
        XCTAssertEqual(
            model.bookingNotice,
            "Couldn’t confirm cancellation. Refresh, then retry if it is still shown."
        )
    }

    @MainActor
    func testReadOnlyCapabilityMakesBothCommandsNoOps() async {
        let calls = CallCounter()
        let model = makeModel(
            canWrite: false,
            task: { id, completed in
                await calls.record()
                return TaskCompletion(id: id, done: completed, completedAt: nil)
            },
            cancel: { id in
                await calls.record()
                return Self.booking(id: id, status: .cancelled)
            }
        )
        let task = Self.task(id: 47)
        let booking = Self.booking(id: 94, status: .confirmed)

        let didComplete = await model.completeTask(task)
        let didCancel = await model.cancelBooking(booking)
        let callCount = await calls.value()
        XCTAssertFalse(didComplete)
        XCTAssertFalse(didCancel)
        XCTAssertEqual(callCount, 0)
        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
        XCTAssertEqual(model.visibleBookings(from: [booking]), [booking])
    }

    @MainActor
    private func makeModel(
        canWrite: Bool = true,
        task: @escaping @Sendable (Int64, Bool) async throws -> TaskCompletion = {
            id, completed in
            TaskCompletion(id: id, done: completed, completedAt: completed ? Date() : nil)
        },
        cancel: @escaping @Sendable (Int64) async throws -> Booking = {
            OwnerCommandModelTests.booking(id: $0, status: .cancelled)
        }
    ) -> OwnerCommandModel {
        OwnerCommandModel(
            canWrite: canWrite,
            setTaskCompletion: task,
            cancelBooking: cancel
        )
    }

    private static func task(id: Int64) -> TaskSummary {
        TaskSummary(
            id: id,
            title: "Confirm menu selections",
            dueOn: LocalDate(rawValue: "2026-07-14"),
            projectID: 12,
            projectTitle: "Rossi tasting",
            isOverdue: false
        )
    }

    private static func booking(id: Int64, status: BookingStatus) -> Booking {
        Booking(
            id: id,
            eventTypeID: 8,
            eventName: "Menu tasting",
            name: "Rossi Trattoria",
            email: "ops@rossi.test",
            phone: nil,
            notes: nil,
            startAt: Date(timeIntervalSince1970: 4_074_865_200),
            endAt: Date(timeIntervalSince1970: 4_074_867_900),
            timeZone: "America/New_York",
            status: status,
            clientID: nil,
            projectID: nil,
            rescheduledFromID: nil,
            cancelReason: status == .cancelled ? "Cancelled from the studio app" : nil,
            cancelledAt: status == .cancelled ? Date() : nil,
            createdAt: Date(timeIntervalSince1970: 1_700_000_000)
        )
    }
}

private enum TestFailure: Error {
    case offline
}

private actor MutationLatch<Value: Sendable> {
    private var calls = 0
    private var continuation: CheckedContinuation<Value, any Error>?
    private var callObservers: [CheckedContinuation<Void, Never>] = []

    func wait(id _: Int64) async throws -> Value {
        calls += 1
        let observers = callObservers
        callObservers.removeAll()
        observers.forEach { $0.resume() }
        return try await withCheckedThrowingContinuation { continuation = $0 }
    }

    func waitUntilCalled() async {
        guard calls == 0 else { return }
        await withCheckedContinuation { callObservers.append($0) }
    }

    func callCount() -> Int {
        calls
    }

    func succeed(_ value: Value) {
        precondition(continuation != nil, "Mutation result delivered before a request was waiting")
        continuation?.resume(returning: value)
        continuation = nil
    }
}

private actor TaskMutationRecorder {
    private var completedValues: [Bool] = []

    func mutate(id: Int64, completed: Bool) -> TaskCompletion {
        completedValues.append(completed)
        return TaskCompletion(
            id: id,
            done: completed,
            completedAt: completed ? Date() : nil
        )
    }

    func values() -> [Bool] {
        completedValues
    }
}

private actor UndoMutationLatch {
    private var undoCalls = 0
    private var undoContinuation: CheckedContinuation<TaskCompletion, any Error>?
    private var undoObservers: [CheckedContinuation<Void, Never>] = []

    func mutate(id: Int64, completed: Bool) async throws -> TaskCompletion {
        if completed {
            return TaskCompletion(id: id, done: true, completedAt: Date())
        }
        undoCalls += 1
        let observers = undoObservers
        undoObservers.removeAll()
        observers.forEach { $0.resume() }
        return try await withCheckedThrowingContinuation { undoContinuation = $0 }
    }

    func waitUntilUndoCalled() async {
        guard undoCalls == 0 else { return }
        await withCheckedContinuation { undoObservers.append($0) }
    }

    func undoCallCount() -> Int {
        undoCalls
    }

    func succeedUndo(id: Int64) {
        precondition(undoContinuation != nil)
        undoContinuation?.resume(
            returning: TaskCompletion(id: id, done: false, completedAt: nil)
        )
        undoContinuation = nil
    }
}

private actor CallCounter {
    private var calls = 0

    func record() {
        calls += 1
    }

    func value() -> Int {
        calls
    }
}
