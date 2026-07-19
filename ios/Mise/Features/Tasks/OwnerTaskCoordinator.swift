import Foundation

/// Coordinates session-local task overlays with Home's preview and the complete
/// in-memory inbox. The inbox never reconciles completion overlays; it only
/// records whether its snapshot is fresh enough for a later dashboard response
/// to reconcile without revealing stale rows.
@MainActor
final class OwnerTaskCoordinator {
    enum Surface: Equatable, Sendable {
        case home
        case inbox
    }

    let home: ResourceModel<DashboardSummary>
    let tasks: ResourceModel<[TaskSummary]>
    let commands: OwnerCommandModel
    private var taskFeedGeneration: UInt64?

    init(
        home: ResourceModel<DashboardSummary>,
        tasks: ResourceModel<[TaskSummary]>,
        commands: OwnerCommandModel
    ) {
        self.home = home
        self.tasks = tasks
        self.commands = commands
    }

    @discardableResult
    func complete(_ task: TaskSummary, from surface: Surface) async -> Bool {
        guard await commands.completeTask(task) else { return false }

        if surface == .inbox || taskFeedHasStarted {
            _ = await refreshTasks()
        }
        if surface == .home {
            _ = await refreshDashboardAfterCurrent()
        }
        return true
    }

    @discardableResult
    func undo(_ task: TaskSummary, from surface: Surface) async -> Bool {
        let didUndo = await commands.undoLastTaskCompletion()
        if didUndo { supplyTaskFallbackIfNeeded() }

        if surface == .inbox || taskFeedHasStarted {
            _ = await refreshTasks()
        }
        _ = await refreshDashboardAfterCurrent()
        supplyTaskFallbackIfNeeded()
        return didUndo
    }

    @discardableResult
    func refreshTasks() async -> Bool {
        let generation = commands.dashboardReconciliationGeneration()
        let didRefresh = await tasks.refreshAfterCurrent()
        if didRefresh,
           let generation,
           generation == commands.taskMutationGeneration
        {
            taskFeedGeneration = generation
        }
        return didRefresh
    }

    @discardableResult
    func refreshDashboard() async -> Bool {
        await refreshDashboard(using: { await home.refresh() })
    }

    private var taskFeedHasStarted: Bool {
        if case .idle = tasks.state { return false }
        return true
    }

    private func supplyTaskFallbackIfNeeded() {
        guard tasks.state.snapshot == nil else { return }
        let reopened = commands.visibleTasks(from: [])
        guard !reopened.isEmpty else { return }
        tasks.supplySessionFallback(reopened)
    }

    private func refreshDashboardAfterCurrent() async -> Bool {
        await refreshDashboard(using: { await home.refreshAfterCurrent() })
    }

    private func refreshDashboard(
        using request: @MainActor () async -> Bool
    ) async -> Bool {
        let generation = commands.dashboardReconciliationGeneration()
        let didRefresh = await request()
        guard
            didRefresh,
            let generation,
            case let .loaded(snapshot) = home.state
        else { return false }

        let taskFeedSnapshot = tasks.state.snapshot?.value
        let taskFeedIsFresh = taskFeedGeneration == generation
        let didReconcile = commands.reconcileDashboardTasks(
            with: snapshot.value.openTasks,
            ifUnchangedSince: generation,
            taskFeedSnapshot: taskFeedSnapshot,
            taskFeedIsFreshForGeneration: taskFeedIsFresh
        )
        if didReconcile, taskFeedIsFresh, let taskFeedSnapshot {
            commands.acknowledgeReopenedTasks(in: taskFeedSnapshot)
        }
        return didReconcile
    }
}
