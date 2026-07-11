import Foundation

extension OwnerRepository {
    func cachedTasks() async throws -> ResourceSnapshot<[TaskDetail]>? {
        let ticket = try await cacheLifetimeTicket()
        let record = try await cache.read("tasks.v1", as: [TaskDetail].self)
        try await requireActiveCacheLifetime(ticket)
        guard let record else { return nil }
        return ResourceSnapshot(value: record.value, storedAt: record.storedAt, source: .cache)
    }

    func refreshTasks() async throws -> ResourceSnapshot<[TaskDetail]> {
        let ticket = try await cacheLifetimeTicket()
        let response = try await sendWithMetadata(MiseEndpoints.Tasks.list)
        try await requireActiveCacheLifetime(ticket)
        let record = try await cache.write(
            response.value.items,
            key: "tasks.v1",
            etag: response.metadata.etag,
            storedAt: response.metadata.receivedAt
        )
        try await requireActiveCacheLifetime(ticket)
        return ResourceSnapshot(value: record.value, storedAt: record.storedAt, source: .network)
    }

    func clientDetail(id: Int64) async throws -> EditableResource<ClientDetail> {
        try await editable(MiseEndpoints.Clients.detail(id: id))
    }

    func createClient(
        _ request: ClientMutationRequest,
        idempotencyKey: UUID
    ) async throws -> EditableResource<ClientDetail> {
        let result = try await editable(
            MiseEndpoints.Clients.create(request, idempotencyKey: idempotencyKey)
        )
        await reconcileClient(result.value)
        return result
    }

    func updateClient(
        id: Int64,
        request: ClientMutationRequest,
        etag: String,
        idempotencyKey: UUID
    ) async throws -> EditableResource<ClientDetail> {
        let result = try await editable(
            MiseEndpoints.Clients.update(
                id: id,
                body: request,
                etag: etag,
                idempotencyKey: idempotencyKey
            )
        )
        await reconcileClient(result.value)
        return result
    }

    func projectDetail(id: Int64) async throws -> EditableResource<ProjectDetail> {
        try await editable(MiseEndpoints.Projects.detail(id: id))
    }

    func createProject(
        _ request: ProjectCreateRequest,
        idempotencyKey: UUID
    ) async throws -> EditableResource<ProjectDetail> {
        let result = try await editable(
            MiseEndpoints.Projects.create(request, idempotencyKey: idempotencyKey)
        )
        await reconcileProject(result.value)
        return result
    }

    func updateProject(
        id: Int64,
        request: ProjectMutationRequest,
        etag: String,
        idempotencyKey: UUID
    ) async throws -> EditableResource<ProjectDetail> {
        let result = try await editable(
            MiseEndpoints.Projects.update(
                id: id,
                body: request,
                etag: etag,
                idempotencyKey: idempotencyKey
            )
        )
        await reconcileProject(result.value)
        return result
    }

    func taskDetail(id: Int64) async throws -> EditableResource<TaskDetail> {
        try await editable(MiseEndpoints.Tasks.detail(id: id))
    }

    func createTask(
        _ request: TaskCreateRequest,
        idempotencyKey: UUID
    ) async throws -> EditableResource<TaskDetail> {
        let result = try await editable(
            MiseEndpoints.Tasks.create(request, idempotencyKey: idempotencyKey)
        )
        await reconcileTask(result.value)
        return result
    }

    func updateTask(
        id: Int64,
        request: TaskMutationRequest,
        etag: String,
        idempotencyKey: UUID
    ) async throws -> EditableResource<TaskDetail> {
        let result = try await editable(
            MiseEndpoints.Tasks.update(
                id: id,
                body: request,
                etag: etag,
                idempotencyKey: idempotencyKey
            )
        )
        await reconcileTask(result.value)
        return result
    }

    @discardableResult
    func deleteTask(
        id: Int64,
        etag: String,
        idempotencyKey: UUID
    ) async throws -> TaskDetail {
        let deleted = try await sendWithMetadata(
            MiseEndpoints.Tasks.delete(
                id: id,
                etag: etag,
                idempotencyKey: idempotencyKey
            )
        ).value
        if let current = try? await cache.read("tasks.v1", as: [TaskDetail].self) {
            try? await cache.write(
                current.value.filter { $0.id != id },
                key: "tasks.v1",
                etag: nil
            )
        }
        try? await cache.remove("dashboard.v1")
        return deleted
    }

    private func editable<Value: Codable & Sendable>(
        _ endpoint: APIEndpoint<Value>
    ) async throws -> EditableResource<Value> {
        let response = try await sendWithMetadata(endpoint)
        guard let etag = response.metadata.etag, !etag.isEmpty else {
            throw OwnerRepositoryError.missingEntityTag
        }
        return EditableResource(value: response.value, etag: etag)
    }

    private func reconcileClient(_ detail: ClientDetail) async {
        let summary = ClientSummary(
            id: detail.id,
            name: detail.name,
            company: detail.company,
            email: detail.email,
            phone: detail.phone,
            market: detail.market,
            projectCount: detail.projectCount,
            portalPublished: detail.portalPublished,
            createdAt: detail.createdAt
        )
        if let current = try? await cache.read("clients.v1", as: [ClientSummary].self) {
            try? await cache.write(
                replacing(summary, in: current.value),
                key: "clients.v1",
                etag: nil
            )
        }
        try? await cache.remove("dashboard.v1")
    }

    private func reconcileProject(_ detail: ProjectDetail) async {
        let summary = ProjectSummary(
            id: detail.id,
            clientID: detail.clientID,
            clientDisplayName: detail.clientDisplayName,
            title: detail.title,
            status: detail.status,
            galleryID: detail.galleryID,
            shootOn: detail.shootOn,
            workspacePublished: detail.workspacePublished,
            createdAt: detail.createdAt
        )
        if let current = try? await cache.read("projects.v1", as: [ProjectSummary].self) {
            try? await cache.write(
                replacing(summary, in: current.value),
                key: "projects.v1",
                etag: nil
            )
        }
        try? await cache.remove("dashboard.v1")
    }

    private func reconcileTask(_ task: TaskDetail) async {
        if let current = try? await cache.read("tasks.v1", as: [TaskDetail].self) {
            try? await cache.write(
                replacing(task, in: current.value),
                key: "tasks.v1",
                etag: nil
            )
        }
        try? await cache.remove("dashboard.v1")
    }

    private func replacing<Element: Identifiable>(
        _ value: Element,
        in values: [Element]
    ) -> [Element] where Element.ID: Equatable {
        guard let index = values.firstIndex(where: { $0.id == value.id }) else {
            return [value] + values
        }
        var result = values
        result[index] = value
        return result
    }
}
