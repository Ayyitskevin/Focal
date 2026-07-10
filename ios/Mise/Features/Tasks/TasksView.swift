import SwiftUI

struct TasksView: View {
    let model: OwnerResourceModel<[TaskDetail]>
    let repository: OwnerRepository
    @State private var showingNewTask = false

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: taskList,
            empty: {
                ContentUnavailableView(
                    "No tasks",
                    systemImage: "checklist",
                    description: Text("Add a task to keep the studio moving.")
                )
            }
        )
        .navigationTitle("Tasks")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button("New task", systemImage: "plus") {
                    showingNewTask = true
                }
            }
        }
        .sheet(isPresented: $showingNewTask) {
            NavigationStack {
                TaskEditorView(repository: repository) {
                    showingNewTask = false
                    await model.refresh()
                }
            }
        }
    }

    private func taskList(_ tasks: [TaskDetail]) -> some View {
        List(tasks) { task in
            NavigationLink {
                TaskEditorView(repository: repository, taskID: task.id) {
                    await model.refresh()
                }
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(task.done ? Color.green : Color.secondary)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(task.title)
                            .strikethrough(task.done)
                            .foregroundStyle(task.done ? .secondary : .primary)
                        if let projectTitle = task.projectTitle {
                            Text(projectTitle)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        if let dueOn = task.dueOn {
                            Label(dueOn.rawValue, systemImage: "calendar")
                                .font(.caption)
                                .foregroundStyle(task.isOverdue ? Color.red : Color.secondary)
                        }
                    }
                }
                .padding(.vertical, 3)
                .accessibilityElement(children: .combine)
            }
        }
        .refreshable { await model.refresh() }
    }
}

private struct TaskEditorView: View {
    @Environment(\.dismiss) private var dismiss

    let repository: OwnerRepository
    let taskID: Int64?
    let didSave: @MainActor () async -> Void

    @State private var title = ""
    @State private var dueOn = ""
    @State private var projectID = ""
    @State private var done = false
    @State private var etag: String?
    @State private var loading = false
    @State private var saving = false
    @State private var deleting = false
    @State private var errorMessage: String?
    @State private var submissionKey = UUID()
    @State private var submittedPayload: TaskMutationRequest?

    init(
        repository: OwnerRepository,
        taskID: Int64? = nil,
        didSave: @escaping @MainActor () async -> Void
    ) {
        self.repository = repository
        self.taskID = taskID
        self.didSave = didSave
    }

    var body: some View {
        Form {
            Section("Task") {
                TextField("Title", text: $title)
                TextField("Due date (YYYY-MM-DD)", text: $dueOn)
                TextField("Project ID (optional)", text: $projectID)
                    .keyboardType(.numberPad)
                if taskID != nil {
                    Toggle("Completed", isOn: $done)
                }
            }

            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red)
                    if taskID != nil {
                        Button("Reload latest version") {
                            Task { await load() }
                        }
                    }
                }
            }

            if taskID != nil {
                Section {
                    Button("Delete task", role: .destructive) {
                        Task { await delete() }
                    }
                    .disabled(deleting || saving || etag == nil)
                }
            }
        }
        .navigationTitle(taskID == nil ? "New Task" : "Edit Task")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                if taskID == nil {
                    Button("Cancel") { dismiss() }
                }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") {
                    Task { await save() }
                }
                .disabled(saving || loading || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .disabled(deleting)
        .overlay {
            if loading || saving || deleting {
                ProgressView().controlSize(.large)
            }
        }
        .task {
            if taskID != nil, etag == nil {
                await load()
            }
        }
    }

    private var normalizedDueOn: LocalDate? {
        let value = dueOn.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : LocalDate(rawValue: value)
    }

    private var normalizedProjectID: Int64? {
        Int64(projectID.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private func load() async {
        guard let taskID else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            let resource = try await repository.taskDetail(id: taskID)
            title = resource.value.title
            dueOn = resource.value.dueOn?.rawValue ?? ""
            projectID = resource.value.projectID.map { String($0) } ?? ""
            done = resource.value.done
            etag = resource.etag
            submittedPayload = nil
            submissionKey = UUID()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func save() async {
        let normalizedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let payload = TaskMutationRequest(
            title: normalizedTitle,
            dueOn: normalizedDueOn,
            projectID: normalizedProjectID,
            done: done
        )
        if submittedPayload != payload {
            submissionKey = UUID()
            submittedPayload = payload
        }
        saving = true
        errorMessage = nil
        defer { saving = false }
        do {
            if let taskID {
                guard let etag else {
                    errorMessage = "Reload this task before saving."
                    return
                }
                _ = try await repository.updateTask(
                    id: taskID,
                    request: payload,
                    etag: etag,
                    idempotencyKey: submissionKey
                )
            } else {
                _ = try await repository.createTask(
                    TaskCreateRequest(
                        title: normalizedTitle,
                        dueOn: normalizedDueOn,
                        projectID: normalizedProjectID
                    ),
                    idempotencyKey: submissionKey
                )
            }
            await didSave()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func delete() async {
        guard let taskID, let etag else { return }
        deleting = true
        errorMessage = nil
        defer { deleting = false }
        do {
            _ = try await repository.deleteTask(
                id: taskID,
                etag: etag,
                idempotencyKey: submissionKey
            )
            await didSave()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
