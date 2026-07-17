import Foundation
import Observation

struct BookingRescheduleDependencies: Sendable {
    let currentSession: @Sendable () async throws -> CurrentSession
    let slots: @Sendable (Int64, LocalDate, Int64) async throws -> EventTypeSlots
    let prepare: @Sendable (Int64, Date, String) async throws
        -> PendingBookingRescheduleAttempt
    let pending: @Sendable () async throws -> PendingBookingRescheduleAttempt?
    let submit: @Sendable (PendingBookingRescheduleAttempt) async throws
        -> BookingRescheduleResult
    let discard: @Sendable (PendingBookingRescheduleAttempt) async throws -> Bool
    let latestResult: @Sendable () async throws -> BookingRescheduleResult?
    let workflow: @Sendable (UUID) async throws -> BookingWorkflowStatus
    let retryWorkflow: @Sendable (UUID) async throws -> BookingWorkflowStatus

    init(
        currentSession: @escaping @Sendable () async throws -> CurrentSession,
        slots: @escaping @Sendable (Int64, LocalDate, Int64) async throws
            -> EventTypeSlots,
        prepare: @escaping @Sendable (Int64, Date, String) async throws
            -> PendingBookingRescheduleAttempt,
        pending: @escaping @Sendable () async throws
            -> PendingBookingRescheduleAttempt?,
        submit: @escaping @Sendable (PendingBookingRescheduleAttempt) async throws
            -> BookingRescheduleResult,
        discard: @escaping @Sendable (PendingBookingRescheduleAttempt) async throws
            -> Bool,
        latestResult: @escaping @Sendable () async throws -> BookingRescheduleResult?,
        workflow: @escaping @Sendable (UUID) async throws -> BookingWorkflowStatus,
        retryWorkflow: @escaping @Sendable (UUID) async throws -> BookingWorkflowStatus
    ) {
        self.currentSession = currentSession
        self.slots = slots
        self.prepare = prepare
        self.pending = pending
        self.submit = submit
        self.discard = discard
        self.latestResult = latestResult
        self.workflow = workflow
        self.retryWorkflow = retryWorkflow
    }

    init(repository: OwnerRepository) {
        currentSession = { try await repository.currentSession() }
        slots = { eventTypeID, day, bookingID in
            try await repository.bookingSlots(
                eventTypeID: eventTypeID,
                day: day,
                rescheduleBookingID: bookingID
            )
        }
        prepare = { bookingID, startAt, timeZone in
            try await repository.prepareBookingReschedule(
                bookingID: bookingID,
                startAt: startAt,
                timeZone: timeZone
            )
        }
        pending = { try await repository.pendingBookingRescheduleAttempt() }
        submit = { try await repository.submitBookingReschedule($0) }
        discard = { try await repository.discardPendingBookingReschedule(ifMatches: $0) }
        latestResult = { try await repository.latestBookingRescheduleResult() }
        workflow = { try await repository.bookingWorkflowStatus(id: $0) }
        retryWorkflow = { try await repository.retryBookingWorkflow(id: $0) }
    }
}

/// Server-authoritative native reschedule state.
///
/// A destination can only come from the source-aware slot feed. One immutable,
/// session-bound request is persisted before POST and retained across every
/// ambiguous outcome, while a valid 200 commits the booking UI separately from
/// the durable provider workflow that follows.
@MainActor
@Observable
final class BookingRescheduleModel {
    private(set) var canReschedule = false
    private(set) var availability: EventTypeSlots?
    private(set) var selectedSlot: BookingSlot?
    private(set) var isLoadingSlots = false
    private(set) var isSubmitting = false
    private(set) var isRefreshingWorkflow = false
    private(set) var pendingAttempt: PendingBookingRescheduleAttempt?
    private(set) var recoveryIsBlocked = false
    private(set) var latestResult: BookingRescheduleResult?
    private(set) var workflowStatus: BookingWorkflowStatus?
    private(set) var workflowNotice: String?
    private(set) var workflowRetryOutcomeUnknown = false
    private(set) var transitionedBookingIDs: Set<Int64> = []
    var notice: String?

    private let originalSession: CurrentSession
    private let commands: OwnerCommandModel
    private let dependencies: BookingRescheduleDependencies
    private var slotLoadID = UUID()
    private var workflowPollGeneration = 0
    private var restored = false

    init(
        session: CurrentSession,
        commands: OwnerCommandModel,
        repository: OwnerRepository
    ) {
        originalSession = session
        self.commands = commands
        dependencies = BookingRescheduleDependencies(repository: repository)
    }

    init(
        session: CurrentSession,
        commands: OwnerCommandModel,
        dependencies: BookingRescheduleDependencies
    ) {
        originalSession = session
        self.commands = commands
        self.dependencies = dependencies
    }

    var hasAmbiguousAttempt: Bool {
        pendingAttempt != nil || recoveryIsBlocked
    }

    var canStartNewReschedule: Bool {
        guard canReschedule, !hasAmbiguousAttempt else { return false }
        guard latestResult != nil else { return true }
        return workflowStatus?.status == .succeeded
    }

    var workflowID: UUID? {
        latestResult?.workflowID
    }

    var workflowIsTerminal: Bool {
        guard let state = workflowStatus?.status else { return false }
        return state == .succeeded || state == .blocked
    }

    var canRetryWorkflow: Bool {
        canReschedule
            && !workflowRetryOutcomeUnknown
            && workflowStatus?.status == .blocked
    }

    var workflowPollID: String {
        [
            workflowID?.uuidString ?? "none",
            workflowStatus?.status.rawValue ?? "unknown",
            String(workflowPollGeneration),
        ].joined(separator: ":")
    }

    var workflowMessage: String? {
        guard latestResult != nil else { return nil }
        guard let state = workflowStatus?.status else {
            return "Booking moved. Client and calendar updates are queued."
        }
        if state == .pending {
            return "Booking moved. Client and calendar updates are queued."
        }
        if state == .running {
            return "Booking moved. Mise is updating the client invite and linked studio records."
        }
        if state == .retry {
            return "Booking moved. Some updates were delayed; Mise will retry automatically."
        }
        if state == .blocked {
            let names = blockedEffectNames
            let subject = names.isEmpty ? "some linked updates" : names.joined(separator: ", ")
            let verb = names.count == 1 ? "needs" : "need"
            return "Booking moved, but \(subject) \(verb) attention. The booking remains at the new time."
        }
        if state == .succeeded {
            return "Booking moved. All applicable updates finished."
        }
        return "Booking moved. Delivery status: \(state.rawValue)."
    }

    func visibleBookings(from bookings: [Booking]) -> [Booking] {
        bookings.filter { !transitionedBookingIDs.contains($0.id) }
    }

    func reconcileBookings(with serverBookings: [Booking]) {
        transitionedBookingIDs.formIntersection(serverBookings.lazy.map(\.id))
    }

    func restore() async {
        guard !restored else { return }
        restored = true

        do {
            pendingAttempt = try await dependencies.pending()
            recoveryIsBlocked = false
            if pendingAttempt != nil {
                notice = Self.ambiguousNotice
            }
        } catch {
            pendingAttempt = nil
            recoveryIsBlocked = true
            notice = Self.blockedRecoveryNotice
        }

        do {
            latestResult = try await dependencies.latestResult()
            if let result = latestResult {
                transitionedBookingIDs.insert(result.originalBookingID)
                await refreshWorkflowStatus()
            }
        } catch {
            latestResult = nil
        }
    }

    /// Capability is deliberately refreshed from uncached /me. Any failure
    /// hides new reschedule actions, while an already committed workflow remains
    /// visible and recoverable.
    func refreshCapability() async {
        do {
            let current = try await dependencies.currentSession()
            canReschedule =
                originalSession.sessionID?.isEmpty == false
                && current.workspace.cacheNamespace
                    == originalSession.workspace.cacheNamespace
                && current.workspace.timeZone == originalSession.workspace.timeZone
                && current.principal.id == originalSession.principal.id
                && current.principal.kind == .studioOwner
                && current.principal.allows("studio:write")
                && current.allowsCommand("booking.reschedule")
                && TimeZone(identifier: current.workspace.timeZone) != nil
        } catch {
            canReschedule = false
        }
    }

    func loadSlots(for booking: Booking, on date: Date) async {
        guard canStartNewReschedule, booking.status == .confirmed else {
            clearAvailability()
            return
        }

        let requestedDay = localDay(for: date)
        let loadID = UUID()
        slotLoadID = loadID
        isLoadingSlots = true
        availability = nil
        selectedSlot = nil
        notice = nil
        defer {
            if slotLoadID == loadID {
                isLoadingSlots = false
            }
        }

        do {
            let response = try await dependencies.slots(
                booking.eventTypeID,
                requestedDay,
                booking.id
            )
            guard slotLoadID == loadID else { return }
            guard
                response.eventTypeID == booking.eventTypeID,
                response.rescheduleBookingID == booking.id,
                response.day == requestedDay,
                response.timeZone == originalSession.workspace.timeZone,
                slotsAreValid(response.slots, for: requestedDay),
                !response.slots.contains(where: {
                    MiseJSON.wholeSecondUTCDate($0.startAt)
                        == MiseJSON.wholeSecondUTCDate(booking.startAt)
                })
            else {
                throw BookingRescheduleModelError.invalidAvailability
            }
            availability = response
        } catch is CancellationError {
            return
        } catch {
            guard slotLoadID == loadID else { return }
            notice = "Available times couldn’t be loaded. Refresh, then try again."
        }
    }

    func selectSlot(_ slot: BookingSlot) {
        guard availability?.slots.contains(slot) == true, !hasAmbiguousAttempt else {
            return
        }
        selectedSlot = slot
        notice = nil
    }

    @discardableResult
    func submitSelected(for booking: Booking) async -> Bool {
        guard
            canStartNewReschedule,
            booking.status == .confirmed,
            let slot = selectedSlot,
            let availability,
            availability.eventTypeID == booking.eventTypeID,
            availability.rescheduleBookingID == booking.id,
            availability.slots.contains(slot),
            !isSubmitting,
            pendingAttempt == nil,
            commands.beginBookingMutation(booking.id)
        else { return false }
        isSubmitting = true
        notice = nil
        defer {
            isSubmitting = false
            commands.endBookingMutation(booking.id)
        }

        let attempt: PendingBookingRescheduleAttempt
        do {
            attempt = try await dependencies.prepare(
                booking.id,
                slot.startAt,
                booking.timeZone
            )
            pendingAttempt = attempt
        } catch {
            do {
                pendingAttempt = try await dependencies.pending()
                recoveryIsBlocked = false
                notice = pendingAttempt == nil
                    ? "Mise couldn’t safely save this request. Nothing was sent."
                    : Self.ambiguousNotice
            } catch {
                pendingAttempt = nil
                recoveryIsBlocked = true
                notice = Self.blockedRecoveryNotice
            }
            return false
        }
        return await submitPrepared(attempt)
    }

    /// Replays the exact saved body and UUID. It remains available even if a
    /// capability refresh fails, because the server may only be returning the
    /// already-committed session-scoped receipt.
    @discardableResult
    func retryPending() async -> Bool {
        guard
            let pendingAttempt,
            !isSubmitting,
            commands.beginBookingMutation(pendingAttempt.bookingID)
        else { return false }
        isSubmitting = true
        notice = nil
        defer {
            isSubmitting = false
            commands.endBookingMutation(pendingAttempt.bookingID)
        }
        return await submitPrepared(pendingAttempt)
    }

    func refreshWorkflowStatus() async {
        guard let result = latestResult, !isRefreshingWorkflow else { return }
        let expectedWorkflowID = result.workflowID
        isRefreshingWorkflow = true
        defer { isRefreshingWorkflow = false }
        do {
            let status = try await dependencies.workflow(result.workflowID)
            guard latestResult?.workflowID == expectedWorkflowID else { return }
            try validate(status, for: result)
            workflowStatus = status
            workflowRetryOutcomeUnknown = false
            workflowNotice = nil
        } catch is CancellationError {
            return
        } catch {
            guard latestResult?.workflowID == expectedWorkflowID else { return }
            workflowNotice = "Booking moved, but its update status couldn’t be refreshed."
        }
    }

    func pollWorkflow(
        maxAttempts: Int = 20,
        interval: Duration = .seconds(3)
    ) async {
        guard latestResult != nil else { return }
        for attempt in 0 ..< max(1, maxAttempts) {
            if Task.isCancelled { return }
            await refreshWorkflowStatus()
            if workflowIsTerminal { return }
            guard attempt + 1 < maxAttempts else { return }
            do {
                try await Task.sleep(for: interval)
            } catch {
                return
            }
        }
    }

    func retryBlockedWorkflow() async {
        guard
            canRetryWorkflow,
            let result = latestResult,
            !isRefreshingWorkflow,
            commands.beginBookingMutation(result.replacementBookingID)
        else { return }
        defer { commands.endBookingMutation(result.replacementBookingID) }

        isRefreshingWorkflow = true
        defer { isRefreshingWorkflow = false }
        do {
            let status = try await dependencies.retryWorkflow(result.workflowID)
            guard latestResult?.workflowID == result.workflowID else { return }
            try validate(status, for: result)
            workflowStatus = status
            workflowRetryOutcomeUnknown = false
            workflowNotice = nil
            workflowPollGeneration += 1
        } catch {
            // Retry has no idempotency key. Resolve its state with GET instead
            // of blindly POSTing again after an ambiguous response.
            workflowStatus = nil
            workflowRetryOutcomeUnknown = true
            isRefreshingWorkflow = false
            await refreshWorkflowStatus()
            if workflowRetryOutcomeUnknown {
                workflowNotice =
                    "Mise couldn’t confirm the update retry. Refresh status before trying again."
            }
        }
    }

    func clearAvailability() {
        slotLoadID = UUID()
        availability = nil
        selectedSlot = nil
        isLoadingSlots = false
    }

    func localDay(for date: Date) -> LocalDate {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(
            identifier: originalSession.workspace.timeZone
        ) ?? TimeZone(secondsFromGMT: 0)!
        let components = calendar.dateComponents([.year, .month, .day], from: date)
        return LocalDate(
            rawValue: String(
                format: "%04d-%02d-%02d",
                components.year ?? 0,
                components.month ?? 0,
                components.day ?? 0
            )
        )
    }

    private func submitPrepared(_ attempt: PendingBookingRescheduleAttempt) async -> Bool {
        do {
            let result = try await dependencies.submit(attempt)
            pendingAttempt = nil
            recoveryIsBlocked = false
            latestResult = result
            workflowStatus = nil
            transitionedBookingIDs.insert(result.originalBookingID)
            clearAvailability()
            notice = nil
            return true
        } catch {
            if Self.isDefinitive(error) {
                let discarded = (try? await dependencies.discard(attempt)) == true
                if discarded {
                    pendingAttempt = nil
                    recoveryIsBlocked = false
                    handleDefinitive(error)
                } else {
                    do {
                        pendingAttempt = try await dependencies.pending()
                        recoveryIsBlocked = false
                    } catch {
                        pendingAttempt = nil
                        recoveryIsBlocked = true
                    }
                    if recoveryIsBlocked {
                        notice = Self.blockedRecoveryNotice
                    } else if pendingAttempt == nil {
                        handleDefinitive(error)
                    } else {
                        notice = Self.savedRecoveryNotice
                    }
                }
            } else {
                pendingAttempt = attempt
                notice = Self.ambiguousNotice
            }
            return false
        }
    }

    private func handleDefinitive(_ error: Error) {
        let code = Self.problemCode(error)
        if code == "booking.slot_unavailable" {
            selectedSlot = nil
            notice = "That time is no longer available. Choose another available time."
        } else if code == "booking.workflow_in_progress" {
            notice = "Booking delivery is still in progress. Refresh, then try again."
        } else if code == "booking.reschedule_unavailable" {
            canReschedule = false
            notice = "Reschedule is temporarily unavailable. No booking was changed."
        } else {
            notice = (error as? LocalizedError)?.errorDescription
                ?? "The booking wasn’t moved. Refresh, then choose an available time."
        }
    }

    private static func isDefinitive(_ error: Error) -> Bool {
        if error is CancellationError { return false }
        guard let apiError = error as? APIError else { return false }
        switch apiError {
        case .invalidEndpoint,
             .unauthenticated,
             .forbidden,
             .subscriptionRequired,
             .notFound,
             .gone,
             .conflict,
             .validation:
            return true
        case let .server(status, problem):
            return status == 503 && problem?.code == "booking.reschedule_unavailable"
        case .transport,
             .unexpectedResponse,
             .unexpectedRedirect,
             .unexpectedContentType,
             .decoding,
             .notModified,
             .rateLimited,
             .http:
            return false
        }
    }

    private static func problemCode(_ error: Error) -> String? {
        guard let apiError = error as? APIError else { return nil }
        switch apiError {
        case let .unauthenticated(problem),
             let .forbidden(problem),
             let .subscriptionRequired(problem),
             let .notFound(problem),
             let .gone(problem),
             let .conflict(problem):
            return problem?.code
        case let .validation(problem):
            return problem.code
        case let .rateLimited(_, problem),
             let .server(_, problem),
             let .http(_, problem):
            return problem?.code
        default:
            return nil
        }
    }

    private static let ambiguousNotice =
        "Mise may have moved this booking. Try the same request again to check safely. "
        + "Don’t choose another time yet."

    private static let savedRecoveryNotice =
        "The server refused this request, but Mise couldn’t clear its saved recovery state. "
        + "Try the same request again before choosing another time."

    private static let blockedRecoveryNotice =
        "Mise can’t safely read this device’s saved reschedule recovery data. "
        + "New reschedules are blocked; contact support before trying another time."

    private func slotsAreValid(_ slots: [BookingSlot], for day: LocalDate) -> Bool {
        let starts = slots.map(\.startAt)
        let startsAreStrictlyIncreasing = zip(starts, starts.dropFirst())
            .allSatisfy { pair in pair.0 < pair.1 }
        return startsAreStrictlyIncreasing
            && slots.allSatisfy {
                $0.endAt > $0.startAt
                    && MiseJSON.wholeSecondUTCDate($0.startAt) == $0.startAt
                    && MiseJSON.wholeSecondUTCDate($0.endAt) == $0.endAt
                    && localDay(for: $0.startAt) == day
            }
    }

    private func validate(
        _ status: BookingWorkflowStatus,
        for result: BookingRescheduleResult
    ) throws {
        guard
            status.workflowID == result.workflowID,
            status.sourceBookingID == result.originalBookingID,
            status.replacementBookingID == result.replacementBookingID
        else {
            throw BookingRescheduleModelError.invalidWorkflow
        }
    }

    private var blockedEffectNames: [String] {
        let names = workflowStatus?.effects.compactMap { effect -> String? in
            guard effect.status == .blocked else { return nil }
            if effect.kind == .clientCancelICS { return "the previous client invite" }
            if effect.kind == .clientRequestICS { return "the new client invite" }
            if effect.kind == .studioRescheduleNotice { return "the studio notice" }
            if effect.kind == .notionBookingPatch { return "the Notion booking" }
            if effect.kind == .notionSessionLink { return "the Notion session" }
            if effect.kind == .googleCalendarMove { return "Google Calendar" }
            return "a linked update"
        } ?? []
        var seen = Set<String>()
        return names.filter { seen.insert($0).inserted }
    }
}

private enum BookingRescheduleModelError: Error {
    case invalidAvailability
    case invalidWorkflow
}
