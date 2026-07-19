import Foundation
import Observation

/// Session-local command state for the first owner mutations.
///
/// Task completion is optimistic because PUT/DELETE target a stable,
/// naturally-idempotent sub-resource. Booking cancellation is deliberately
/// server-authoritative: a real transition starts best-effort client and
/// calendar side effects, so the agenda does not hide a booking before a valid
/// cancelled response arrives.
@MainActor
@Observable
final class OwnerCommandModel {
    let canWrite: Bool

    private(set) var taskIDsInFlight: Set<Int64> = []
    private(set) var completedTaskIDs: Set<Int64> = []
    private(set) var taskMutationGeneration: UInt64 = 0
    private(set) var bookingIDsInFlight: Set<Int64> = []
    private(set) var cancelledBookingIDs: Set<Int64> = []
    private(set) var justCompletedTask: TaskSummary?
    private var reopenedTasksByID: [Int64: TaskSummary] = [:]
    var taskNotice: String?
    var bookingNotice: String?

    private let setTaskCompletionRequest: @Sendable (Int64, Bool) async throws -> TaskCompletion
    private let cancelBookingRequest: @Sendable (Int64) async throws -> Booking

    init(
        canWrite: Bool,
        setTaskCompletion: @escaping @Sendable (Int64, Bool) async throws -> TaskCompletion,
        cancelBooking: @escaping @Sendable (Int64) async throws -> Booking
    ) {
        self.canWrite = canWrite
        setTaskCompletionRequest = setTaskCompletion
        cancelBookingRequest = cancelBooking
    }

    func visibleTasks(from tasks: [TaskSummary]) -> [TaskSummary] {
        var visible = tasks.filter { !completedTaskIDs.contains($0.id) }
        let visibleIDs = Set(visible.lazy.map(\.id))
        visible.append(contentsOf: reopenedTasksByID.values.filter {
            !visibleIDs.contains($0.id) && !completedTaskIDs.contains($0.id)
        })
        guard !reopenedTasksByID.isEmpty else { return visible }
        return visible.sorted(by: Self.taskPriority)
    }

    /// Removes a successful reopen overlay only after the authoritative full
    /// task feed contains that row. A missing row or failed read keeps the
    /// command-confirmed task visible in this session.
    func acknowledgeReopenedTasks(in serverTasks: [TaskSummary]) {
        for id in serverTasks.lazy.map(\.id) {
            reopenedTasksByID.removeValue(forKey: id)
        }
    }

    func visibleBookings(from bookings: [Booking]) -> [Booking] {
        bookings.filter { !cancelledBookingIDs.contains($0.id) }
    }

    func isTaskInFlight(_ id: Int64) -> Bool {
        taskIDsInFlight.contains(id)
    }

    var isTaskMutationInFlight: Bool {
        !taskIDsInFlight.isEmpty
    }

    /// A dashboard response may reconcile task overlays only if its request
    /// began outside every task mutation. A later mutation changes the returned
    /// generation and is rejected again at commit time.
    func dashboardReconciliationGeneration() -> UInt64? {
        taskIDsInFlight.isEmpty ? taskMutationGeneration : nil
    }

    func isBookingInFlight(_ id: Int64) -> Bool {
        bookingIDsInFlight.contains(id)
    }

    /// Shares one per-booking mutation lock across cancellation and
    /// rescheduling so two consequential transitions cannot race from the UI.
    func beginBookingMutation(_ id: Int64) -> Bool {
        canWrite && bookingIDsInFlight.insert(id).inserted
    }

    func endBookingMutation(_ id: Int64) {
        bookingIDsInFlight.remove(id)
    }

    /// Drops optimistic task IDs only when a fresh dashboard request both
    /// succeeds and did not race a task mutation. The full inbox is not an
    /// authority for this overlay because Home may still hold its six-row
    /// in-memory preview.
    @discardableResult
    func reconcileDashboardTasks(
        with serverTasks: [TaskSummary],
        ifUnchangedSince generation: UInt64,
        taskFeedSnapshot: [TaskSummary]? = nil,
        taskFeedIsFreshForGeneration: Bool = false
    ) -> Bool {
        guard generation == taskMutationGeneration else { return false }
        if let taskFeedSnapshot {
            let taskFeedIDs = Set(taskFeedSnapshot.lazy.map(\.id))
            guard
                taskFeedIsFreshForGeneration
                    || completedTaskIDs.isDisjoint(with: taskFeedIDs)
            else { return false }
        }
        completedTaskIDs.formIntersection(taskIDsInFlight)
        if let task = justCompletedTask,
           let reopened = serverTasks.first(where: { $0.id == task.id }),
           !taskIDsInFlight.contains(task.id)
        {
            reopenedTasksByID[task.id] = reopened
            justCompletedTask = nil
            taskNotice = nil
        }
        return true
    }

    /// Drops confirmed cancellation overlays once a successful agenda refresh
    /// no longer contains those bookings.
    func reconcileBookings(with serverBookings: [Booking]) {
        cancelledBookingIDs.formIntersection(serverBookings.lazy.map(\.id))
    }

    @discardableResult
    func completeTask(_ task: TaskSummary) async -> Bool {
        guard canWrite, taskIDsInFlight.isEmpty else { return false }
        taskIDsInFlight.insert(task.id)
        defer { taskIDsInFlight.remove(task.id) }

        taskMutationGeneration &+= 1
        let previousUndo = justCompletedTask
        taskNotice = nil
        completedTaskIDs.insert(task.id)
        justCompletedTask = task

        do {
            let response = try await setTaskCompletionRequest(task.id, true)
            guard
                response.id == task.id,
                response.done,
                response.completedAt != nil
            else {
                throw OwnerCommandResponseError.invalidTaskCompletion
            }
            reopenedTasksByID.removeValue(forKey: task.id)
            return true
        } catch {
            completedTaskIDs.remove(task.id)
            if justCompletedTask?.id == task.id {
                justCompletedTask = previousUndo
            }
            taskNotice = "Couldn’t confirm task completion. Refresh, then retry if it is still shown."
            return false
        }
    }

    @discardableResult
    func undoLastTaskCompletion() async -> Bool {
        guard canWrite, let task = justCompletedTask, taskIDsInFlight.isEmpty else { return false }
        taskIDsInFlight.insert(task.id)
        defer { taskIDsInFlight.remove(task.id) }

        taskMutationGeneration &+= 1
        taskNotice = nil
        do {
            let response = try await setTaskCompletionRequest(task.id, false)
            guard
                response.id == task.id,
                !response.done,
                response.completedAt == nil
            else {
                throw OwnerCommandResponseError.invalidTaskReopen
            }
            reopenedTasksByID[task.id] = task
            completedTaskIDs.remove(task.id)
            if justCompletedTask?.id == task.id {
                justCompletedTask = nil
            }
            return true
        } catch {
            taskNotice = "Couldn’t confirm the task was reopened. Refresh, then retry Undo."
            return false
        }
    }

    func dismissTaskUndo() {
        justCompletedTask = nil
    }

    private static func taskPriority(_ left: TaskSummary, _ right: TaskSummary) -> Bool {
        switch (left.dueOn?.rawValue, right.dueOn?.rawValue) {
        case let (leftDue?, rightDue?) where leftDue != rightDue:
            return leftDue < rightDue
        case (_?, nil):
            return true
        case (nil, _?):
            return false
        default:
            return left.id > right.id
        }
    }

    @discardableResult
    func cancelBooking(_ booking: Booking) async -> Bool {
        guard booking.status == .confirmed, beginBookingMutation(booking.id)
        else { return false }
        defer { endBookingMutation(booking.id) }

        bookingNotice = nil
        do {
            let response = try await cancelBookingRequest(booking.id)
            guard response.id == booking.id, response.status == .cancelled else {
                throw OwnerCommandResponseError.invalidBookingCancellation
            }
            cancelledBookingIDs.insert(booking.id)
            return true
        } catch {
            bookingNotice = "Couldn’t confirm cancellation. Refresh, then retry if it is still shown."
            return false
        }
    }
}

private enum OwnerCommandResponseError: Error {
    case invalidTaskCompletion
    case invalidTaskReopen
    case invalidBookingCancellation
}
