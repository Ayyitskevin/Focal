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
    private(set) var bookingIDsInFlight: Set<Int64> = []
    private(set) var cancelledBookingIDs: Set<Int64> = []
    private(set) var justCompletedTask: TaskSummary?
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
        tasks.filter { !completedTaskIDs.contains($0.id) }
    }

    func visibleBookings(from bookings: [Booking]) -> [Booking] {
        bookings.filter { !cancelledBookingIDs.contains($0.id) }
    }

    func isTaskInFlight(_ id: Int64) -> Bool {
        taskIDsInFlight.contains(id)
    }

    func isBookingInFlight(_ id: Int64) -> Bool {
        bookingIDsInFlight.contains(id)
    }

    /// Drops optimistic task IDs once a successful dashboard refresh proves
    /// the server list has caught up. A stale/failed snapshot should not call
    /// this method, so its optimistic overlay remains intact.
    func reconcileTasks(with serverTasks: [TaskSummary]) {
        completedTaskIDs.formIntersection(taskIDsInFlight)
        let serverIDs = Set(serverTasks.lazy.map(\.id))
        if let task = justCompletedTask,
           serverIDs.contains(task.id), !taskIDsInFlight.contains(task.id)
        {
            justCompletedTask = nil
            taskNotice = nil
        }
    }

    /// Drops confirmed cancellation overlays once a successful agenda refresh
    /// no longer contains those bookings.
    func reconcileBookings(with serverBookings: [Booking]) {
        cancelledBookingIDs.formIntersection(serverBookings.lazy.map(\.id))
    }

    @discardableResult
    func completeTask(_ task: TaskSummary) async -> Bool {
        guard canWrite, taskIDsInFlight.insert(task.id).inserted else { return false }
        defer { taskIDsInFlight.remove(task.id) }

        taskNotice = nil
        completedTaskIDs.insert(task.id)
        justCompletedTask = task

        do {
            let response = try await setTaskCompletionRequest(task.id, true)
            guard response.id == task.id, response.done else {
                throw OwnerCommandResponseError.invalidTaskCompletion
            }
            return true
        } catch {
            completedTaskIDs.remove(task.id)
            if justCompletedTask?.id == task.id {
                justCompletedTask = nil
            }
            taskNotice = "Couldn’t confirm task completion. Refresh, then retry if it is still shown."
            return false
        }
    }

    @discardableResult
    func undoLastTaskCompletion() async -> Bool {
        guard canWrite, let task = justCompletedTask,
              taskIDsInFlight.insert(task.id).inserted
        else { return false }
        defer { taskIDsInFlight.remove(task.id) }

        taskNotice = nil
        do {
            let response = try await setTaskCompletionRequest(task.id, false)
            guard response.id == task.id, !response.done else {
                throw OwnerCommandResponseError.invalidTaskReopen
            }
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

    @discardableResult
    func cancelBooking(_ booking: Booking) async -> Bool {
        guard canWrite, booking.status == .confirmed,
              bookingIDsInFlight.insert(booking.id).inserted
        else { return false }
        defer { bookingIDsInFlight.remove(booking.id) }

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
