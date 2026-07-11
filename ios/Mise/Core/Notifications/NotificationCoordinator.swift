import Foundation
import Observation

@MainActor
@Observable
final class NotificationCoordinator: NotificationEventReceiving {
    private(set) var permissionState: NotificationPermissionState = .loading
    private(set) var registration: DeviceRegistration?
    private(set) var isWorking = false
    private(set) var errorMessage: String?
    private(set) var registrationFailureMessage: String?

    let router: AppRouter

    private let authorization: any NotificationAuthorizationProviding
    private let registrar: any RemoteNotificationRegistering
    private let installationIdentity: InstallationIdentity
    private let environment: APNsEnvironment
    private let appVersion: String
    private let localeIdentifier: () -> String

    private var registrationETag: String?
    private var binding: Binding?
    private var generation: UInt64 = 0
    private var operation: Task<Void, Never>?
    private var operationSequence: UInt64 = 0
    private var tokenHex: String?
    private var lastSynchronized: TokenSynchronization?
    private var started = false
    private var requestedAPNsRegistration = false

    init(
        platformRoot: URL,
        environment: APNsEnvironment,
        appVersion: String,
        installationIdentity: InstallationIdentity,
        authorization: (any NotificationAuthorizationProviding)? = nil,
        registrar: (any RemoteNotificationRegistering)? = nil,
        localeIdentifier: @escaping () -> String = { Locale.autoupdatingCurrent.identifier },
        router: AppRouter? = nil
    ) {
        self.environment = environment
        self.appVersion = appVersion
        self.installationIdentity = installationIdentity
        self.authorization = authorization ?? SystemNotificationAuthorizationProvider()
        self.registrar = registrar ?? SystemRemoteNotificationRegistrar()
        self.localeIdentifier = localeIdentifier
        self.router = router ?? AppRouter(parser: AppRouteParser(platformRoot: platformRoot))
    }

    func start() async {
        guard !started else { return }
        started = true
        await refreshPermission(registerIfAllowed: true)
    }

    func sceneDidBecomeActive() async {
        await refreshPermission(registerIfAllowed: true)
    }

    func bindOwner(
        session: CurrentSession,
        repository: any DeviceRegistrationServicing
    ) {
        guard session.principal.kind == .studioOwner,
              session.principal.allows("studio:read")
        else {
            unbindOwner()
            return
        }

        advanceGeneration()
        let value = Binding(
            id: UUID(),
            generation: generation,
            session: session,
            repository: repository
        )
        binding = value
        registration = nil
        registrationETag = nil
        errorMessage = nil
        registrationFailureMessage = nil
        reconcile(binding: value)
    }

    func unbindOwner() {
        advanceGeneration()
        binding = nil
        registration = nil
        registrationETag = nil
        registrationFailureMessage = nil
        tokenHex = nil
        lastSynchronized = nil
        // A later owner binding must ask iOS for the current process token
        // again; APNs tokens are deliberately never persisted on device.
        requestedAPNsRegistration = false
    }

    func requestPermissionFromUser() async {
        guard !isWorking else { return }
        if permissionState == .denied || permissionState == .unsupported {
            registrar.openNotificationSettings()
            return
        }
        guard permissionState == .notDetermined else {
            if permissionState.canRegisterWithAPNs, binding != nil {
                requestAPNsRegistrationIfNeeded()
            }
            return
        }

        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            _ = try await authorization.requestAuthorization()
            permissionState = await authorization.permissionState()
            if permissionState.canRegisterWithAPNs, binding != nil {
                requestAPNsRegistrationIfNeeded()
            } else {
                requestedAPNsRegistration = false
            }
            if let binding { reconcile(binding: binding) }
        } catch {
            errorMessage = "Notification permission could not be requested. Try again."
        }
    }

    func openSystemSettings() {
        registrar.openNotificationSettings()
    }

    func savePreferences(_ preferences: NotificationPreferences) async {
        guard !isWorking, let binding else { return }
        isWorking = true
        errorMessage = nil
        let task = schedule { [weak self] in
            await self?.performPreferenceSave(preferences, binding: binding)
        }
        await task.value
        isWorking = false
    }

    private func performPreferenceSave(
        _ preferences: NotificationPreferences,
        binding: Binding
    ) async {
        guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
        do {
            let etag: String
            if let registrationETag {
                etag = registrationETag
            } else {
                let current = try await binding.repository.current()
                guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
                registration = current.value
                registrationETag = current.etag
                etag = current.etag
            }
            let updated = try await binding.repository.updatePreferences(
                preferences,
                etag: etag
            )
            guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
            registration = updated.value
            registrationETag = updated.etag
        } catch APIError.conflict(_) {
            await reloadAfterPreferenceConflict(binding)
            if isCurrent(binding), permissionState.canRegisterWithAPNs {
                errorMessage = "Notification preferences changed elsewhere. Review the latest settings."
            }
        } catch {
            guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
            errorMessage = Self.message(for: error)
        }
    }

    /// Runs before bearer revocation and never waits on connectivity. The backend
    /// logout transaction independently deactivates every session-bound device.
    func unregisterBeforeLogout() async {
        advanceGeneration()
        operationSequence &+= 1
        operation?.cancel()
        operation = nil
        binding = nil
        registration = nil
        registrationETag = nil
        registrationFailureMessage = nil
        tokenHex = nil
        lastSynchronized = nil
        requestedAPNsRegistration = false
        router.clearForLogout()
        authorization.clearPendingAndDeliveredNotifications()
        // This is the offline confidentiality fallback: even if server logout
        // cannot reach Mise, this process asks iOS to stop accepting APNs deliveries
        // for the old account before local credentials disappear.
        registrar.unregisterForRemoteNotifications()
    }

    func receivedRemoteNotificationToken(_ token: Data) async {
        // Ignore a callback racing with logout/local APNs unregistration. A later
        // owner binding requests a fresh process token before synchronizing again.
        guard requestedAPNsRegistration, !token.isEmpty else { return }
        tokenHex = token.map { String(format: "%02x", $0) }.joined()
        registrationFailureMessage = nil
        scheduleSynchronization()
    }

    func remoteNotificationRegistrationFailed(_ message: String) async {
        guard requestedAPNsRegistration else { return }
        requestedAPNsRegistration = false
        registrationFailureMessage = message.isEmpty
            ? "This device could not register with Apple Push Notification service."
            : "This device could not register for notifications. Try again later."
    }

    func receivedNotification(_ envelope: NotificationEnvelope) async {
        router.receiveNotification(envelope)
    }

    func shouldPresentNotification(_ envelope: NotificationEnvelope) async -> Bool {
        router.shouldPresent(envelope)
    }

    func waitForPendingOperationsForTesting() async {
        if let operation {
            await operation.value
        }
    }

    private func refreshPermission(registerIfAllowed: Bool) async {
        permissionState = await authorization.permissionState()

        if permissionState.canRegisterWithAPNs, binding != nil {
            if registerIfAllowed {
                requestAPNsRegistrationIfNeeded()
            }
        } else {
            requestedAPNsRegistration = false
        }
        if let binding { reconcile(binding: binding) }
    }

    private func reconcile(binding: Binding) {
        guard isCurrent(binding) else { return }
        if permissionState.canRegisterWithAPNs {
            requestAPNsRegistrationIfNeeded()
            schedule { [weak self] in
                await self?.loadAndSynchronize(binding: binding)
            }
        } else if permissionState != .loading {
            schedule { [weak self] in
                await self?.deactivateAfterPermissionRevocation(binding: binding)
            }
        }
    }

    private func deactivateAfterPermissionRevocation(binding: Binding) async {
        guard isCurrent(binding), !permissionState.canRegisterWithAPNs else { return }
        do {
            try await binding.repository.unregister()
            guard isCurrent(binding), !permissionState.canRegisterWithAPNs else { return }
            registration = nil
            registrationETag = nil
            tokenHex = nil
            lastSynchronized = nil
        } catch {
            // Session-bound server revocation remains the confidentiality boundary.
        }
    }

    private func loadAndSynchronize(binding: Binding) async {
        guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
        do {
            let current = try await binding.repository.current()
            guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
            registration = current.value
            registrationETag = current.etag
        } catch APIError.notFound(_) {
            guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
            registration = nil
            registrationETag = nil
        } catch {
            guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
            errorMessage = Self.message(for: error)
        }
        await synchronizeToken(binding: binding)
    }

    private func scheduleSynchronization() {
        guard let binding else { return }
        schedule { [weak self] in
            await self?.synchronizeToken(binding: binding)
        }
    }

    private func synchronizeToken(binding: Binding) async {
        guard permissionState.canRegisterWithAPNs,
              isCurrent(binding),
              let tokenHex
        else {
            return
        }

        let synchronization = TokenSynchronization(bindingID: binding.id, tokenHex: tokenHex)
        guard lastSynchronized != synchronization else { return }

        do {
            let request = DeviceRegistrationRequest(
                installationID: try installationIdentity.identifier().uuidString.lowercased(),
                apnsToken: tokenHex,
                environment: environment,
                locale: localeIdentifier(),
                appVersion: appVersion,
                preferences: nil
            )
            let updated = try await binding.repository.register(request)
            guard isCurrent(binding), permissionState.canRegisterWithAPNs,
                  self.tokenHex == tokenHex
            else { return }
            registration = updated.value
            registrationETag = updated.etag
            lastSynchronized = synchronization
            errorMessage = nil
        } catch {
            guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
            errorMessage = Self.message(for: error)
        }
    }

    private func reloadAfterPreferenceConflict(_ binding: Binding) async {
        guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
        do {
            let current = try await binding.repository.current()
            guard isCurrent(binding), permissionState.canRegisterWithAPNs else { return }
            registration = current.value
            registrationETag = current.etag
        } catch {
            // Keep the conflict message as the actionable result.
        }
    }

    @discardableResult
    private func schedule(
        _ work: @escaping @MainActor () async -> Void
    ) -> Task<Void, Never> {
        let predecessor = operation
        operationSequence &+= 1
        let sequence = operationSequence
        let task = Task {
            if let predecessor { await predecessor.value }
            await work()
            if operationSequence == sequence {
                operation = nil
            }
        }
        operation = task
        return task
    }

    private func advanceGeneration() {
        generation &+= 1
    }

    private func requestAPNsRegistrationIfNeeded() {
        guard binding != nil, permissionState.canRegisterWithAPNs,
              !requestedAPNsRegistration
        else { return }
        requestedAPNsRegistration = true
        registrar.registerForRemoteNotifications()
    }

    private func isCurrent(_ candidate: Binding) -> Bool {
        binding?.id == candidate.id
            && binding?.generation == candidate.generation
            && generation == candidate.generation
    }

    private static func message(for error: Error) -> String {
        (error as? LocalizedError)?.errorDescription
            ?? "Mise could not update notification settings."
    }
}

private struct Binding: Sendable {
    let id: UUID
    let generation: UInt64
    let session: CurrentSession
    let repository: any DeviceRegistrationServicing
}

private struct TokenSynchronization: Equatable, Sendable {
    let bindingID: UUID
    let tokenHex: String
}
