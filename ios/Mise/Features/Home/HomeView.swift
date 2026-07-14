import SwiftUI

struct HomeView: View {
    let model: ResourceModel<DashboardSummary>
    let commands: OwnerCommandModel
    let navigate: (OwnerDestination) -> Void

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { _ in false },
            content: dashboard,
            empty: { EmptyView() }
        )
        .navigationTitle("Home")
        .alert(
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
        let visibleTasks = commands.visibleTasks(from: tasks)

        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Open tasks").font(.headline)
                Spacer()
                Text("\(visibleTasks.count)")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("\(visibleTasks.count) open tasks")
            }

            if let completed = commands.justCompletedTask {
                taskCompletionBanner(completed)
            }

            if visibleTasks.isEmpty {
                Text("You’re all caught up.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
                    .background(.background, in: RoundedRectangle(cornerRadius: 14))
            } else {
                ForEach(visibleTasks) { task in
                    taskRow(task)
                }
            }
        }
    }

    private func taskRow(_ task: TaskSummary) -> some View {
        HStack(alignment: .top, spacing: 12) {
            if commands.canWrite {
                Button {
                    Task {
                        _ = await commands.completeTask(task)
                        await refreshDashboard()
                    }
                } label: {
                    Image(systemName: "circle")
                        .font(.title3)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .disabled(commands.isTaskInFlight(task.id))
                .accessibilityLabel("Mark \(task.title) done")
                .accessibilityHint("Removes this task from the open list. You can undo during this session.")
            }

            VStack(alignment: .leading, spacing: 4) {
                Text(task.title)
                    .font(.body.weight(.semibold))
                if let metadata = taskMetadata(task) {
                    Text(metadata)
                        .font(.footnote)
                        .foregroundStyle(task.isOverdue ? Color.red : Color.secondary)
                }
            }
            .padding(.vertical, 10)
            .accessibilityElement(children: .combine)

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
    }

    private func taskMetadata(_ task: TaskSummary) -> String? {
        var details: [String] = []
        if let projectTitle = task.projectTitle, !projectTitle.isEmpty {
            details.append(projectTitle)
        }
        if let dueOn = task.dueOn {
            details.append("Due \(dueOn.rawValue)")
        }
        if task.isOverdue {
            details.append("Overdue")
        }
        return details.isEmpty ? nil : details.joined(separator: " · ")
    }

    private func taskCompletionBanner(_ completed: TaskSummary) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                taskCompletionStatus(completed)
                Spacer(minLength: 0)
                taskCompletionActions(completed)
            }

            VStack(alignment: .leading, spacing: 8) {
                taskCompletionStatus(completed)
                taskCompletionActions(completed)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .padding(.horizontal, 12)
        .background(MiseDesign.okBg, in: RoundedRectangle(cornerRadius: 12))
    }

    private func taskCompletionStatus(_ completed: TaskSummary) -> some View {
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

    private func taskCompletionActions(_ completed: TaskSummary) -> some View {
        HStack(spacing: 8) {
            Button("Undo") {
                Task {
                    _ = await commands.undoLastTaskCompletion()
                    await refreshDashboard()
                }
            }
            .disabled(commands.isTaskInFlight(completed.id))
            .frame(minWidth: 44, minHeight: 44)
            .accessibilityLabel("Undo completion of \(completed.title)")
            .accessibilityHint("Reopens this task.")

            Button {
                commands.dismissTaskUndo()
            } label: {
                Image(systemName: "xmark")
                    .frame(width: 44, height: 44)
            }
            .disabled(commands.isTaskInFlight(completed.id))
            .accessibilityLabel("Dismiss completion of \(completed.title)")
        }
    }

    private func refreshDashboard() async {
        await model.refresh()
        if case let .loaded(snapshot) = model.state {
            commands.reconcileTasks(with: snapshot.value.openTasks)
        }
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
