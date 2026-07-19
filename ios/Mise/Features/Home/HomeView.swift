import SwiftUI

struct HomeView: View {
    let model: ResourceModel<DashboardSummary>
    let tasks: ResourceModel<[TaskSummary]>
    let timeZoneIdentifier: String
    let commands: OwnerCommandModel
    let taskCoordinator: OwnerTaskCoordinator
    let navigate: (OwnerDestination) -> Void
    @AccessibilityFocusState private var focusedTaskID: Int64?

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { _ in false },
            content: dashboard,
            empty: { EmptyView() }
        )
        .navigationTitle("Home")
        .ownerTaskNoticeAlert(commands)
    }

    private func dashboard(_ summary: DashboardSummary) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 150), spacing: 12)],
                    spacing: 12
                ) {
                    MetricCard(title: "New inquiries", value: "\(summary.newInquiries)", icon: "sparkles")
                    MetricCard(
                        title: "Outstanding",
                        value: summary.outstanding.amount.ownerDisplayValue,
                        icon: "banknote"
                    )
                    MetricCard(
                        title: "Next 14 days",
                        value: "\(summary.upcomingProjects14Days)",
                        icon: "calendar"
                    )
                    MetricCard(title: "Tasks due", value: "\(summary.tasksDueCount)", icon: "checklist")
                }

                Text("Quick actions").font(.headline)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        QuickLink(title: "Clients", icon: "person.2", destination: .clients, action: navigate)
                        QuickLink(title: "Projects", icon: "briefcase", destination: .projects, action: navigate)
                        QuickLink(title: "Galleries", icon: "photo.on.rectangle", destination: .galleries, action: navigate)
                        QuickLink(title: "Calendar", icon: "calendar", destination: .calendar, action: navigate)
                    }
                }

                taskSection(summary.openTasks)

                if !summary.upcomingShoots.isEmpty {
                    Text("Upcoming shoots").font(.headline)
                    ForEach(summary.upcomingShoots.prefix(5)) { shoot in
                        HStack(alignment: .firstTextBaseline) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(shoot.title).font(.body.weight(.semibold))
                                Text(shoot.clientDisplayName).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(shoot.shootOn.rawValue)
                                .font(.footnote.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        .padding()
                        .background(.background, in: RoundedRectangle(cornerRadius: 14))
                    }
                }
            }
            .padding()
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .refreshable { await refreshDashboard() }
    }

    @ViewBuilder
    private func taskSection(_ tasks: [TaskSummary]) -> some View {
        let visibleTasks = Array(commands.visibleTasks(from: tasks).prefix(6))

        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text("Up next").font(.headline)
                    Spacer()
                    NavigationLink {
                        TasksView(
                            model: self.tasks,
                            timeZoneIdentifier: timeZoneIdentifier,
                            commands: commands,
                            taskCoordinator: taskCoordinator
                        )
                    } label: {
                        Text("View all")
                    }
                    .frame(minWidth: 44, minHeight: 44)
                    .accessibilityHint("Opens the complete studio-task inbox")
                }
                Text("Showing \(visibleTasks.count)")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Showing \(visibleTasks.count) studio tasks")
            }

            if let completed = commands.justCompletedTask {
                OwnerTaskCompletionBanner(
                    completed: completed,
                    commands: commands,
                    undo: { await undoTask(completed) }
                )
                .id(completed.id)
            }

            if visibleTasks.isEmpty {
                Text("No studio tasks in this preview. View all to check the full inbox.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
                    .background(.background, in: RoundedRectangle(cornerRadius: 14))
            } else {
                ForEach(visibleTasks) { task in
                    OwnerTaskRow(
                        task: task,
                        isOverdue: task.isOverdue,
                        commands: commands,
                        complete: { await completeTask(task) }
                    )
                    .accessibilityFocused($focusedTaskID, equals: task.id)
                }
            }
        }
    }

    private func completeTask(_ task: TaskSummary) async {
        _ = await taskCoordinator.complete(task, from: .home)
    }

    private func undoTask(_ completed: TaskSummary) async {
        let didUndo = await taskCoordinator.undo(completed, from: .home)
        guard didUndo else { return }
        await Task.yield()
        focusedTaskID = completed.id
    }

    private func refreshDashboard() async {
        _ = await taskCoordinator.refreshDashboard()
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon).font(.subheadline).foregroundStyle(.secondary)
            Text(value).font(.title2.weight(.semibold)).minimumScaleFactor(0.7).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.background, in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}

private struct QuickLink: View {
    let title: String
    let icon: String
    let destination: OwnerDestination
    let action: (OwnerDestination) -> Void

    var body: some View {
        Button { action(destination) } label: {
            Label(title, systemImage: icon).padding(.vertical, 8)
        }
        .buttonStyle(.bordered)
    }
}
