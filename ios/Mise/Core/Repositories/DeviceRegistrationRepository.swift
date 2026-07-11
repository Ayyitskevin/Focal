import Foundation

enum DeviceRegistrationRepositoryError: LocalizedError, Sendable {
    case missingEntityTag

    var errorDescription: String? {
        "The server did not provide a version for notification preferences."
    }
}

protocol DeviceRegistrationServicing: Sendable {
    func register(
        _ request: DeviceRegistrationRequest
    ) async throws -> EditableResource<DeviceRegistration>
    func current() async throws -> EditableResource<DeviceRegistration>
    func updatePreferences(
        _ preferences: NotificationPreferences,
        etag: String
    ) async throws -> EditableResource<DeviceRegistration>
    func unregister() async throws
}

actor DeviceRegistrationRepository: DeviceRegistrationServicing {
    private let client: any APIClientProtocol

    init(client: any APIClientProtocol) {
        self.client = client
    }

    func register(
        _ request: DeviceRegistrationRequest
    ) async throws -> EditableResource<DeviceRegistration> {
        try await versioned(MiseEndpoints.Devices.register(request))
    }

    func current() async throws -> EditableResource<DeviceRegistration> {
        try await versioned(MiseEndpoints.Devices.current)
    }

    func updatePreferences(
        _ preferences: NotificationPreferences,
        etag: String
    ) async throws -> EditableResource<DeviceRegistration> {
        try await versioned(
            MiseEndpoints.Devices.updatePreferences(preferences, etag: etag)
        )
    }

    func unregister() async throws {
        _ = try await client.send(MiseEndpoints.Devices.unregister)
    }

    private func versioned(
        _ endpoint: APIEndpoint<DeviceRegistration>
    ) async throws -> EditableResource<DeviceRegistration> {
        let response = try await client.sendWithMetadata(endpoint)
        guard let etag = response.metadata.etag, !etag.isEmpty else {
            throw DeviceRegistrationRepositoryError.missingEntityTag
        }
        return EditableResource(value: response.value, etag: etag)
    }
}
