import XCTest
@testable import Mise

final class OwnerTaskCoordinatorTests: XCTestCase {
    @MainActor
    func testTasksCompletionStaleHomeAndHomeUndoRefreshBothRealResources() async {
        let task = Self.task(id: 70)
        let fixture = makeFixture(task: task)
        await fixture.home.load()
        await fixture.tasks.load()

        let didComplete = await fixture.coordinator.complete(task, from: .inbox)
        XCTAssertTrue(didComplete)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [])
        let staleHomeTasks = fixture.home.state.snapshot?.value.openTasks ?? []
        XCTAssertEqual(staleHomeTasks, [task])
        XCTAssertTrue(fixture.commands.visibleTasks(from: staleHomeTasks).isEmpty)

        let didUndo = await fixture.coordinator.undo(task, from: .home)
        XCTAssertTrue(didUndo)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [task])
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [task])
        XCTAssertEqual(fixture.commands.visibleTasks(from: [task]), [task])
    }

    @MainActor
    func testHomeCompletionRefreshesStartedInboxBeforeReleasingOverlay() async {
        let task = Self.task(id: 71)
        let fixture = makeFixture(task: task)
        await fixture.home.load()
        await fixture.tasks.load()

        let didComplete = await fixture.coordinator.complete(task, from: .home)

        XCTAssertTrue(didComplete)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [])
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [])
        XCTAssertTrue(fixture.commands.completedTaskIDs.isEmpty)
    }

    @MainActor
    func testInboxUndoRefreshesHomeAfterAuthoritativeCompletionRefresh() async {
        let task = Self.task(id: 72)
        let fixture = makeFixture(task: task)
        await fixture.home.load()
        await fixture.tasks.load()

        let didComplete = await fixture.coordinator.complete(task, from: .inbox)
        let didRefreshDashboard = await fixture.coordinator.refreshDashboard()
        XCTAssertTrue(didComplete)
        XCTAssertTrue(didRefreshDashboard)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [])
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [])
        XCTAssertTrue(fixture.commands.completedTaskIDs.isEmpty)

        let didUndo = await fixture.coordinator.undo(task, from: .inbox)

        XCTAssertTrue(didUndo)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [task])
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [task])
        XCTAssertEqual(fixture.commands.visibleTasks(from: [task]), [task])
    }

    @MainActor
    func testConfirmedInboxUndoStaysVisibleWhenTaskRefreshFails() async {
        let task = Self.task(id: 73)
        let fixture = makeFixture(task: task)
        await fixture.home.load()
        await fixture.tasks.load()
        let didComplete = await fixture.coordinator.complete(task, from: .inbox)
        XCTAssertTrue(didComplete)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [])
        await fixture.server.failNextTasksRequest()

        let didUndo = await fixture.coordinator.undo(task, from: .inbox)

        XCTAssertTrue(didUndo)
        guard case let .failed(snapshot, _) = fixture.tasks.state else {
            return XCTFail("Expected the failed refresh to retain its empty snapshot")
        }
        XCTAssertEqual(snapshot?.value, [])
        XCTAssertEqual(fixture.commands.visibleTasks(from: snapshot?.value ?? []), [task])
        XCTAssertEqual(
            fixture.commands.visibleTasks(
                from: fixture.home.state.snapshot?.value.openTasks ?? []
            ),
            [task]
        )
    }

    @MainActor
    func testConfirmedInboxUndoStaysVisibleWhenDashboardRefreshFails() async {
        let task = Self.task(id: 731)
        let fixture = makeFixture(task: task)
        await fixture.home.load()
        await fixture.tasks.load()
        let didComplete = await fixture.coordinator.complete(task, from: .inbox)
        let didRefreshCompletedDashboard = await fixture.coordinator.refreshDashboard()
        XCTAssertTrue(didComplete)
        XCTAssertTrue(didRefreshCompletedDashboard)
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [])
        await fixture.server.failNextDashboardRequest()

        let didUndo = await fixture.coordinator.undo(task, from: .inbox)

        XCTAssertTrue(didUndo)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [task])
        guard case let .failed(snapshot, _) = fixture.home.state else {
            return XCTFail("Expected the failed refresh to retain its empty snapshot")
        }
        XCTAssertEqual(snapshot?.value.openTasks, [])
        XCTAssertEqual(
            fixture.commands.visibleTasks(from: snapshot?.value.openTasks ?? []),
            [task]
        )

        let didRecover = await fixture.coordinator.refreshDashboard()
        XCTAssertTrue(didRecover)
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [task])
        XCTAssertTrue(fixture.commands.visibleTasks(from: []).isEmpty)
    }

    @MainActor
    func testHomeUndoSeedsTasksWhenFirstTaskReadFails() async {
        let task = Self.task(id: 732)
        let fixture = makeFixture(task: task)
        await fixture.home.load()
        let didComplete = await fixture.coordinator.complete(task, from: .home)
        XCTAssertTrue(didComplete)
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [])
        if case .idle = fixture.tasks.state {
            // Expected: Tasks has never been opened or read.
        } else {
            XCTFail("Expected Tasks to remain idle before Undo")
        }
        await fixture.server.failNextTasksRequest()

        let didUndo = await fixture.coordinator.undo(task, from: .home)

        XCTAssertTrue(didUndo)
        guard case let .failed(snapshot, _) = fixture.tasks.state else {
            return XCTFail("Expected failed first read with a session fallback")
        }
        XCTAssertEqual(snapshot?.value, [task])
        XCTAssertEqual(snapshot?.source, .session)
        XCTAssertEqual(fixture.commands.visibleTasks(from: snapshot?.value ?? []), [task])
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [task])
    }

    @MainActor
    func testUndoQueuesNewerTaskReadBehindCompletionRefresh() async {
        let task = Self.task(id: 74)
        let fixture = makeFixture(task: task)
        await fixture.home.load()
        await fixture.tasks.load()
        await fixture.server.blockNextTasksRequest()

        let completion = Task { @MainActor in
            await fixture.coordinator.complete(task, from: .inbox)
        }
        await fixture.server.waitUntilTasksRequestBlocked()
        let undo = Task { @MainActor in
            await fixture.coordinator.undo(task, from: .inbox)
        }
        await fixture.server.waitUntilReopened()
        await fixture.server.releaseBlockedTasksRequest()

        let didComplete = await completion.value
        let didUndo = await undo.value
        let taskReadCount = await fixture.server.taskReadCount()
        XCTAssertTrue(didComplete)
        XCTAssertTrue(didUndo)
        XCTAssertEqual(taskReadCount, 3)
        XCTAssertEqual(fixture.tasks.state.snapshot?.value, [task])
        XCTAssertEqual(fixture.home.state.snapshot?.value.openTasks, [task])
        XCTAssertEqual(fixture.commands.visibleTasks(from: [task]), [task])
        XCTAssertTrue(fixture.commands.visibleTasks(from: []).isEmpty)
    }

    @MainActor
    private func makeFixture(task: TaskSummary) -> (
        server: OwnerTaskWorkflowStub,
        home: ResourceModel<DashboardSummary>,
        tasks: ResourceModel<[TaskSummary]>,
        commands: OwnerCommandModel,
        coordinator: OwnerTaskCoordinator
    ) {
        let server = OwnerTaskWorkflowStub(task: task)
        let home = ResourceModel<DashboardSummary>(
            staleAfter: 60,
            cached: { nil },
            remote: { try await server.dashboard() }
        )
        let tasks = ResourceModel<[TaskSummary]>(
            staleAfter: 60,
            cached: { nil },
            remote: { try await server.tasks() }
        )
        let commands = OwnerCommandModel(
            canWrite: true,
            setTaskCompletion: { id, completed in
                await server.setTaskCompletion(id: id, completed: completed)
            },
            cancelBooking: { _ in throw CoordinatorTestError.unused }
        )
        return (
            server,
            home,
            tasks,
            commands,
            OwnerTaskCoordinator(home: home, tasks: tasks, commands: commands)
        )
    }

    private static func task(id: Int64) -> TaskSummary {
        TaskSummary(
            id: id,
            title: "Confirm studio timeline",
            dueOn: LocalDate(rawValue: "2026-07-18"),
            projectID: 12,
            projectTitle: "Rossi tasting",
            isOverdue: false
        )
    }
}

private actor OwnerTaskWorkflowStub {
    let task: TaskSummary
    private var isDone = false
    private var taskReads = 0
    private var failNextTaskRead = false
    private var failNextDashboardRead = false
    private var shouldBlockNextTaskRead = false
    private var blockedTaskRead: CheckedContinuation<ResourceSnapshot<[TaskSummary]>, Never>?
    private var blockedTaskSnapshot: ResourceSnapshot<[TaskSummary]>?
    private var blockedTaskReadObservers: [CheckedContinuation<Void, Never>] = []
    private var reopenObservers: [CheckedContinuation<Void, Never>] = []

    init(task: TaskSummary) {
        self.task = task
    }

    func setTaskCompletion(id: Int64, completed: Bool) -> TaskCompletion {
        isDone = completed
        if !completed {
            let observers = reopenObservers
            reopenObservers.removeAll()
            observers.forEach { $0.resume() }
        }
        return TaskCompletion(
            id: id,
            done: completed,
            completedAt: completed ? Date(timeIntervalSince1970: 1_700_000_000) : nil
        )
    }

    func tasks() async throws -> ResourceSnapshot<[TaskSummary]> {
        taskReads += 1
        if failNextTaskRead {
            failNextTaskRead = false
            throw CoordinatorTestError.offline
        }
        let snapshot = ResourceSnapshot(
            value: isDone ? [] : [task],
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: .network
        )
        guard shouldBlockNextTaskRead else { return snapshot }
        shouldBlockNextTaskRead = false
        let observers = blockedTaskReadObservers
        blockedTaskReadObservers.removeAll()
        observers.forEach { $0.resume() }
        blockedTaskSnapshot = snapshot
        return await withCheckedContinuation { blockedTaskRead = $0 }
    }

    func failNextTasksRequest() {
        failNextTaskRead = true
    }

    func failNextDashboardRequest() {
        failNextDashboardRead = true
    }

    func blockNextTasksRequest() {
        shouldBlockNextTaskRead = true
    }

    func waitUntilTasksRequestBlocked() async {
        guard blockedTaskRead == nil else { return }
        await withCheckedContinuation { blockedTaskReadObservers.append($0) }
    }

    func releaseBlockedTasksRequest() {
        precondition(blockedTaskRead != nil && blockedTaskSnapshot != nil)
        let snapshot = blockedTaskSnapshot!
        blockedTaskRead?.resume(returning: snapshot)
        blockedTaskRead = nil
        blockedTaskSnapshot = nil
    }

    func waitUntilReopened() async {
        guard isDone else { return }
        await withCheckedContinuation { reopenObservers.append($0) }
    }

    func taskReadCount() -> Int {
        taskReads
    }

    func dashboard() throws -> ResourceSnapshot<DashboardSummary> {
        if failNextDashboardRead {
            failNextDashboardRead = false
            throw CoordinatorTestError.offline
        }
        return ResourceSnapshot(
            value: dashboardSummary(openTasks: isDone ? [] : [task]),
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: .network
        )
    }
}

private enum CoordinatorTestError: Error {
    case unused
    case offline
}

private func dashboardSummary(openTasks: [TaskSummary]) -> DashboardSummary {
    DashboardSummary(
        generatedAt: Date(timeIntervalSince1970: 1_700_000_000),
        newInquiries: 0,
        outstanding: MoneyCount(
            count: 0,
            amount: Money(minorUnits: 0, currencyCode: "USD")
        ),
        upcomingProjects14Days: 0,
        overdueInvoiceCount: 0,
        retainerDraftCount: 0,
        tasksDueCount: openTasks.count,
        actionItemCount: openTasks.count,
        kpis: DashboardKPIs(
            inquiriesDelta7Days: 0,
            bookingsDelta7Days: 0,
            collected7Days: Money(minorUnits: 0, currencyCode: "USD")
        ),
        openTasks: openTasks,
        upcomingShoots: [],
        openInvoices: [],
        recentActivity: []
    )
}
