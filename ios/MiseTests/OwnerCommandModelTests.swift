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

        model.reconcileTasks(with: [task])

        XCTAssertNil(model.justCompletedTask)
        XCTAssertNil(model.taskNotice)
        XCTAssertEqual(model.visibleTasks(from: [task]), [task])
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

private actor CallCounter {
    private var calls = 0

    func record() {
        calls += 1
    }

    func value() -> Int {
        calls
    }
}
