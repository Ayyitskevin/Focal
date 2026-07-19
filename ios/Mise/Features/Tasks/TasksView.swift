import Foundation
import SwiftUI

struct TaskInboxSection: Equatable, Identifiable, Sendable {
    enum Kind: String, CaseIterable, Hashable, Identifiable, Sendable {
        case overdue = "Overdue"
        case today = "Today"
        case upcoming = "Upcoming"
        case noDueDate = "No due date"

        var id: String { rawValue }
        var isOverdue: Bool { self == .overdue }
    }

    let kind: Kind
    let tasks: [TaskSummary]
    var id: Kind { kind }
}

enum TaskInboxSectioner {
    static func studioToday(
        at date: Date = Date(),
        timeZoneIdentifier: String
    ) -> LocalDate {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: timeZoneIdentifier)
            ?? TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        return LocalDate(rawValue: formatter.string(from: date))
    }

    static func sections(
        tasks: [TaskSummary],
        today: LocalDate
    ) -> [TaskInboxSection] {
        var buckets: [TaskInboxSection.Kind: [TaskSummary]] = [:]
        for task in tasks {
            let kind: TaskInboxSection.Kind
            if let dueOn = task.dueOn {
                if dueOn.rawValue < today.rawValue {
                    kind = .overdue
                } else if dueOn == today {
                    kind = .today
                } else {
                    kind = .upcoming
                }
            } else {
                kind = .noDueDate
            }
            buckets[kind, default: []].append(task)
        }

        return TaskInboxSection.Kind.allCases.compactMap { kind in
            guard var values = buckets[kind], !values.isEmpty else { return nil }
            values.sort { left, right in
                let leftDue = left.dueOn?.rawValue ?? ""
                let rightDue = right.dueOn?.rawValue ?? ""
                if leftDue != rightDue { return leftDue < rightDue }
                return left.id > right.id
            }
            return TaskInboxSection(kind: kind, tasks: values)
        }
    }
}

struct TasksView: View {
    let model: ResourceModel<[TaskSummary]>
    let timeZoneIdentifier: String
    let commands: OwnerCommandModel
    let taskCoordinator: OwnerTaskCoordinator
    @AccessibilityFocusState private var focusedTaskID: Int64?

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: {
                commands.visibleTasks(from: $0).isEmpty
                    && commands.justCompletedTask == nil
            },
            content: taskList,
            empty: emptyState
        )
        .navigationTitle("Studio tasks")
        .ownerTaskNoticeAlert(commands)
    }

    private func taskList(_ tasks: [TaskSummary]) -> some View {
        let visibleTasks = commands.visibleTasks(from: tasks)
        let today = TaskInboxSectioner.studioToday(
            timeZoneIdentifier: timeZoneIdentifier
        )
        let sections = TaskInboxSectioner.sections(tasks: visibleTasks, today: today)

        return List {
            if let completed = commands.justCompletedTask {
                Section {
                    OwnerTaskCompletionBanner(
                        completed: completed,
                        commands: commands,
                        undo: { await undoTask(completed) }
                    )
                    .id(completed.id)
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                }
            }

            ForEach(sections) { section in
                Section(section.kind.rawValue) {
                    ForEach(section.tasks) { task in
                        OwnerTaskRow(
                            task: task,
                            isOverdue: section.kind.isOverdue,
                            commands: commands,
                            complete: { await completeTask(task) }
                        )
                        .accessibilityFocused($focusedTaskID, equals: task.id)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await taskCoordinator.refreshTasks() }
    }

    private func emptyState() -> some View {
        ContentUnavailableView {
            Label("No open studio tasks", systemImage: "checkmark.circle")
        } description: {
            Text("New studio-operation tasks will appear here.")
        }
    }

    private func completeTask(_ task: TaskSummary) async {
        _ = await taskCoordinator.complete(task, from: .inbox)
    }

    private func undoTask(_ completed: TaskSummary) async {
        let didUndo = await taskCoordinator.undo(completed, from: .inbox)
        guard didUndo else { return }
        await Task.yield()
        focusedTaskID = completed.id
    }
}

struct OwnerTaskRow: View {
    let task: TaskSummary
    let isOverdue: Bool
    let commands: OwnerCommandModel
    let complete: @MainActor () async -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            if commands.canWrite {
                Button {
                    Task { await complete() }
                } label: {
                    Image(systemName: "circle")
                        .font(.title3)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .disabled(commands.isTaskMutationInFlight)
                .accessibilityLabel("Mark \(task.title) done")
                .accessibilityHint(
                    "Removes this task from the open list. You can undo during this session."
                )
            }

            VStack(alignment: .leading, spacing: 4) {
                Text(task.title)
                    .font(.body.weight(.semibold))
                if let metadata {
                    Text(metadata)
                        .font(.footnote)
                        .foregroundStyle(isOverdue ? Color.red : Color.secondary)
                }
            }
            .padding(.vertical, 10)
            .accessibilityElement(children: .combine)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
    }

    private var metadata: String? {
        var details: [String] = []
        if let projectTitle = task.projectTitle, !projectTitle.isEmpty {
            details.append(projectTitle)
        }
        if let dueOn = task.dueOn {
            details.append("Due \(dueOn.rawValue)")
        }
        if isOverdue {
            details.append("Overdue")
        }
        return details.isEmpty ? nil : details.joined(separator: " · ")
    }
}

struct OwnerTaskCompletionBanner: View {
    let completed: TaskSummary
    let commands: OwnerCommandModel
    let undo: @MainActor () async -> Void
    @AccessibilityFocusState private var undoFocused: Bool

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                status
                Spacer(minLength: 0)
                actions
            }

            VStack(alignment: .leading, spacing: 8) {
                status
                actions.frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .padding(.horizontal, 12)
        .background(MiseDesign.okBg, in: RoundedRectangle(cornerRadius: 12))
        .onAppear { undoFocused = true }
    }

    private var status: some View {
        HStack(spacing: 12) {
            if commands.isTaskInFlight(completed.id) {
                ProgressView()
                    .accessibilityLabel("Completing \(completed.title)")
            } else {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(MiseDesign.ok)
                    .accessibilityHidden(true)
            }

            Text(commands.isTaskInFlight(completed.id)
                ? "Completing \(completed.title)…"
                : "Completed \(completed.title)")
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var actions: some View {
        HStack(spacing: 8) {
            Button("Undo") {
                Task { await undo() }
            }
            .disabled(commands.isTaskMutationInFlight)
            .frame(minWidth: 44, minHeight: 44)
            .accessibilityLabel("Undo completion of \(completed.title)")
            .accessibilityHint("Reopens this task.")
            .accessibilityFocused($undoFocused)

            Button {
                commands.dismissTaskUndo()
            } label: {
                Image(systemName: "xmark")
                    .frame(width: 44, height: 44)
            }
            .disabled(commands.isTaskMutationInFlight)
            .accessibilityLabel("Dismiss completion of \(completed.title)")
        }
    }
}

extension View {
    func ownerTaskNoticeAlert(_ commands: OwnerCommandModel) -> some View {
        alert(
            "Task update",
            isPresented: Binding(
                get: { commands.taskNotice != nil },
                set: { if !$0 { commands.taskNotice = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(commands.taskNotice ?? "")
        }
    }
}
