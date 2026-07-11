import XCTest
@testable import Mise

@MainActor
final class NotificationCoordinatorTests: XCTestCase {
    func testStartDoesNotPromptOrRegisterWhenPermissionIsUndetermined() async {
        let authorization = FakeNotificationAuthorization(status: .notDetermined)
        let registrar = FakeRemoteNotificationRegistrar()
        let coordinator = makeCoordinator(authorization: authorization, registrar: registrar)

        await coordinator.start()

        XCTAssertEqual(authorization.requestCount, 0)
        XCTAssertEqual(registrar.registrationCount, 0)
    }

    func testExplicitEnableRequestsPermissionThenRegisters() async {
        let authorization = FakeNotificationAuthorization(
            status: .notDetermined,
            statusAfterRequest: .authorized
        )
        let registrar = FakeRemoteNotificationRegistrar()
        let coordinator = makeCoordinator(authorization: authorization, registrar: registrar)
        await coordinator.start()
        coordinator.bindOwner(
            session: ownerSession(),
            repository: FakeDeviceRegistrationRepository(currentIsMissing: true)
        )

        await coordinator.requestPermissionFromUser()
        await coordinator.waitForPendingOperationsForTesting()

        XCTAssertEqual(authorization.requestCount, 1)
        XCTAssertEqual(registrar.registrationCount, 1)
        XCTAssertEqual(coordinator.permissionState, .authorized)
    }

    func testPreviouslyAuthorizedOwnerBindingRegistersEveryProcessLaunch() async {
        let authorization = FakeNotificationAuthorization(status: .authorized)
        let registrar = FakeRemoteNotificationRegistrar()
        let coordinator = makeCoordinator(authorization: authorization, registrar: registrar)

        await coordinator.start()
        XCTAssertEqual(registrar.registrationCount, 0)
        coordinator.bindOwner(
            session: ownerSession(),
            repository: FakeDeviceRegistrationRepository(currentIsMissing: true)
        )
        await coordinator.waitForPendingOperationsForTesting()

        XCTAssertEqual(registrar.registrationCount, 1)
    }

    func testOwnerBoundTokenSynchronizesAndRotates() async throws {
        let authorization = FakeNotificationAuthorization(status: .authorized)
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: true)
        let coordinator = makeCoordinator(
            authorization: authorization,
            registrar: FakeRemoteNotificationRegistrar()
        )
        await coordinator.start()
        let firstToken = Data((0..<16).map { UInt8($0) })
        let rotatedToken = Data(repeating: 0x10, count: 24)
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.receivedRemoteNotificationToken(firstToken)
        await coordinator.waitForPendingOperationsForTesting()

        var requests = await repository.registrationRequests()
        XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests[0].apnsToken, "000102030405060708090a0b0c0d0e0f")
        XCTAssertNil(requests[0].preferences)

        await coordinator.receivedRemoteNotificationToken(firstToken)
        await coordinator.waitForPendingOperationsForTesting()
        requests = await repository.registrationRequests()
        XCTAssertEqual(requests.count, 1, "Duplicate callbacks are coalesced in memory.")

        await coordinator.receivedRemoteNotificationToken(rotatedToken)
        await coordinator.waitForPendingOperationsForTesting()
        requests = await repository.registrationRequests()
        XCTAssertEqual(
            requests.map(\.apnsToken),
            [
                "000102030405060708090a0b0c0d0e0f",
                String(repeating: "10", count: 24),
            ]
        )
    }

    func testPreferenceSaveUsesCurrentVersionAndPublishesServerResult() async {
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: false)
        let coordinator = makeCoordinator(
            authorization: FakeNotificationAuthorization(status: .authorized),
            registrar: FakeRemoteNotificationRegistrar()
        )
        await coordinator.start()
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.waitForPendingOperationsForTesting()
        let preferences = NotificationPreferences(
            newBookings: false,
            bookingChanges: false,
            proposalResponses: true,
            payments: true
        )

        await coordinator.savePreferences(preferences)

        let updates = await repository.preferenceUpdates()
        XCTAssertEqual(updates.count, 1)
        XCTAssertEqual(updates[0].preferences, preferences)
        XCTAssertEqual(updates[0].etag, #""device-v1""#)
        XCTAssertEqual(coordinator.registration?.preferences, preferences)
    }

    func testDeniedPermissionDeactivatesRegistrationOnBinding() async {
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: false)
        let coordinator = makeCoordinator(
            authorization: FakeNotificationAuthorization(status: .denied),
            registrar: FakeRemoteNotificationRegistrar()
        )
        await coordinator.start()

        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.waitForPendingOperationsForTesting()

        let unregisterCount = await repository.unregisterCount()
        XCTAssertEqual(unregisterCount, 1)
        XCTAssertNil(coordinator.registration)
    }

    func testUndeterminedPermissionDeactivatesStaleServerRegistration() async {
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: false)
        let coordinator = makeCoordinator(
            authorization: FakeNotificationAuthorization(status: .notDetermined),
            registrar: FakeRemoteNotificationRegistrar()
        )
        await coordinator.start()

        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.waitForPendingOperationsForTesting()

        let unregisterCount = await repository.unregisterCount()
        XCTAssertEqual(unregisterCount, 1)
        XCTAssertNil(coordinator.registration)
    }

    func testBindingBeforePermissionLoadFailsClosedWithoutAStaleGet() async {
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: false)
        let coordinator = makeCoordinator(
            authorization: FakeNotificationAuthorization(status: .denied),
            registrar: FakeRemoteNotificationRegistrar()
        )

        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.start()
        await coordinator.waitForPendingOperationsForTesting()

        let currentCount = await repository.currentCount()
        let unregisterCount = await repository.unregisterCount()
        XCTAssertEqual(currentCount, 0)
        XCTAssertEqual(unregisterCount, 1)
        XCTAssertNil(coordinator.registration)
    }

    func testPermissionRevocationQueuesDeleteAfterInFlightRegistration() async {
        let authorization = FakeNotificationAuthorization(status: .authorized)
        let repository = BlockingDeviceRegistrationRepository()
        let coordinator = makeCoordinator(
            authorization: authorization,
            registrar: FakeRemoteNotificationRegistrar()
        )
        await coordinator.start()
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.receivedRemoteNotificationToken(Data(repeating: 0x4a, count: 32))
        await repository.waitUntilRegisterStarts()

        authorization.status = .denied
        await coordinator.sceneDidBecomeActive()
        await repository.resumeRegister()
        await coordinator.waitForPendingOperationsForTesting()

        let operations = await repository.operations()
        XCTAssertEqual(operations, ["current", "register", "unregister"])
        XCTAssertNil(coordinator.registration)
    }

    func testRebindingRequestsFreshInMemoryToken() async {
        let authorization = FakeNotificationAuthorization(status: .authorized)
        let registrar = FakeRemoteNotificationRegistrar()
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: true)
        let coordinator = makeCoordinator(
            authorization: authorization,
            registrar: registrar
        )
        await coordinator.start()
        XCTAssertEqual(registrar.registrationCount, 0)

        coordinator.bindOwner(session: ownerSession(), repository: repository)
        XCTAssertEqual(registrar.registrationCount, 1)
        coordinator.unbindOwner()
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.waitForPendingOperationsForTesting()

        XCTAssertEqual(registrar.registrationCount, 2)
    }

    func testLogoutUnregistersAndClearsDeliveredPendingAndRoutes() async {
        let authorization = FakeNotificationAuthorization(status: .authorized)
        let registrar = FakeRemoteNotificationRegistrar()
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: false)
        let coordinator = makeCoordinator(
            authorization: authorization,
            registrar: registrar
        )
        coordinator.router.authenticationDidChange(.signedIn(ownerSession()))
        coordinator.router.receiveNotification(notification(route: "/app/home"))
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.waitForPendingOperationsForTesting()

        await coordinator.unregisterBeforeLogout()

        XCTAssertEqual(registrar.unregistrationCount, 1)
        XCTAssertEqual(authorization.clearCount, 1)
        XCTAssertNil(coordinator.registration)
        XCTAssertNil(coordinator.router.navigationRequest)
    }

    func testLogoutDuringBlockedRegistrationCompletesLocalCleanupImmediately() async {
        let authorization = FakeNotificationAuthorization(status: .authorized)
        let registrar = FakeRemoteNotificationRegistrar()
        let repository = BlockingDeviceRegistrationRepository()
        let coordinator = makeCoordinator(authorization: authorization, registrar: registrar)
        await coordinator.start()
        coordinator.router.authenticationDidChange(.signedIn(ownerSession()))
        coordinator.router.receiveNotification(notification(route: "/app/home"))
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.receivedRemoteNotificationToken(Data(repeating: 0x3d, count: 32))
        await repository.waitUntilRegisterStarts()

        await coordinator.unregisterBeforeLogout()

        XCTAssertEqual(registrar.unregistrationCount, 1)
        XCTAssertNil(coordinator.registration)
        XCTAssertNil(coordinator.router.navigationRequest)

        await repository.resumeRegister()
        await Task.yield()
    }

    func testOfflineLogoutStillUnregistersLocallyAndRejectsLateToken() async {
        let authorization = FakeNotificationAuthorization(status: .authorized)
        let registrar = FakeRemoteNotificationRegistrar()
        let repository = FakeDeviceRegistrationRepository(currentIsMissing: true)
        let coordinator = makeCoordinator(authorization: authorization, registrar: registrar)
        await coordinator.start()
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.waitForPendingOperationsForTesting()

        await coordinator.unregisterBeforeLogout()
        await coordinator.sceneDidBecomeActive()
        XCTAssertEqual(
            registrar.registrationCount,
            1,
            "A signed-out lifecycle refresh must not re-enable the old APNs token."
        )
        await coordinator.receivedRemoteNotificationToken(Data(repeating: 0x4b, count: 32))
        coordinator.bindOwner(session: ownerSession(), repository: repository)
        await coordinator.waitForPendingOperationsForTesting()

        var registrations = await repository.registrationRequests()
        XCTAssertEqual(registrar.unregistrationCount, 1)
        XCTAssertEqual(registrar.registrationCount, 2)
        XCTAssertTrue(registrations.isEmpty, "A token callback racing with logout must be discarded.")

        await coordinator.receivedRemoteNotificationToken(Data(repeating: 0x5c, count: 32))
        await coordinator.waitForPendingOperationsForTesting()
        registrations = await repository.registrationRequests()
        XCTAssertEqual(registrations.map(\.apnsToken), [String(repeating: "5c", count: 32)])
    }

    private func makeCoordinator(
        authorization: FakeNotificationAuthorization,
        registrar: FakeRemoteNotificationRegistrar
    ) -> NotificationCoordinator {
        NotificationCoordinator(
            platformRoot: URL(string: "https://mise.example")!,
            environment: .sandbox,
            appVersion: "1.0 (42)",
            installationIdentity: InstallationIdentity(
                persistence: CoordinatorInstallationIDPersistence(),
                legacyDefaults: nil,
                deviceName: { "Test iPhone" }
            ),
            authorization: authorization,
            registrar: registrar,
            localeIdentifier: { "en_US" }
        )
    }

    private func ownerSession() -> CurrentSession {
        CurrentSession(
            workspace: WorkspaceContext(
                cacheNamespace: "tenant_north",
                slug: "north",
                displayName: "North",
                apiBaseURL: URL(string: "https://north.mise.example")!,
                brandAccentHex: nil,
                timeZone: "America/New_York",
                currencyCode: "USD"
            ),
            principal: Principal(
                id: "studio_owner",
                kind: .studioOwner,
                displayName: "Owner",
                email: nil,
                scopes: ["studio:read", "studio:write"]
            ),
            sessionID: "session-1"
        )
    }

    private func notification(route: String) -> NotificationEnvelope {
        NotificationEnvelope(
            version: 1,
            eventID: UUID(),
            workspaceOrigin: URL(string: "https://north.mise.example")!,
            workspaceCacheNamespace: "tenant_north",
            principalKind: .studioOwner,
            principalID: "studio_owner",
            route: route
        )
    }
}

@MainActor
private final class FakeNotificationAuthorization: NotificationAuthorizationProviding {
    var status: NotificationPermissionState
    let statusAfterRequest: NotificationPermissionState
    private(set) var requestCount = 0
    private(set) var clearCount = 0

    init(
        status: NotificationPermissionState,
        statusAfterRequest: NotificationPermissionState? = nil
    ) {
        self.status = status
        self.statusAfterRequest = statusAfterRequest ?? status
    }

    func permissionState() async -> NotificationPermissionState { status }

    func requestAuthorization() async throws -> Bool {
        requestCount += 1
        status = statusAfterRequest
        return status.canRegisterWithAPNs
    }

    func clearPendingAndDeliveredNotifications() {
        clearCount += 1
    }
}

@MainActor
private final class FakeRemoteNotificationRegistrar: RemoteNotificationRegistering {
    private(set) var registrationCount = 0
    private(set) var unregistrationCount = 0
    private(set) var settingsOpenCount = 0

    func registerForRemoteNotifications() {
        registrationCount += 1
    }

    func unregisterForRemoteNotifications() {
        unregistrationCount += 1
    }

    func openNotificationSettings() {
        settingsOpenCount += 1
    }
}

private actor FakeDeviceRegistrationRepository: DeviceRegistrationServicing {
    struct PreferenceUpdate: Sendable {
        let preferences: NotificationPreferences
        let etag: String
    }

    private let currentIsMissing: Bool
    private var requests: [DeviceRegistrationRequest] = []
    private var updates: [PreferenceUpdate] = []
    private var unregisters = 0
    private var currentCalls = 0
    private var registration: DeviceRegistration
    private var etag = #""device-v1""#

    init(currentIsMissing: Bool) {
        self.currentIsMissing = currentIsMissing
        registration = Self.makeRegistration(preferences: .defaults)
    }

    func register(
        _ request: DeviceRegistrationRequest
    ) async throws -> EditableResource<DeviceRegistration> {
        requests.append(request)
        registration = DeviceRegistration(
            active: true,
            environment: request.environment,
            locale: request.locale,
            appVersion: request.appVersion,
            preferences: registration.preferences,
            registeredAt: registration.registeredAt,
            updatedAt: Date()
        )
        return EditableResource(value: registration, etag: etag)
    }

    func current() async throws -> EditableResource<DeviceRegistration> {
        currentCalls += 1
        if currentIsMissing { throw APIError.notFound(nil) }
        return EditableResource(value: registration, etag: etag)
    }

    func updatePreferences(
        _ preferences: NotificationPreferences,
        etag: String
    ) async throws -> EditableResource<DeviceRegistration> {
        updates.append(PreferenceUpdate(preferences: preferences, etag: etag))
        registration = Self.makeRegistration(preferences: preferences)
        self.etag = #""device-v2""#
        return EditableResource(value: registration, etag: self.etag)
    }

    func unregister() async throws {
        unregisters += 1
    }

    func registrationRequests() -> [DeviceRegistrationRequest] { requests }
    func preferenceUpdates() -> [PreferenceUpdate] { updates }
    func unregisterCount() -> Int { unregisters }
    func currentCount() -> Int { currentCalls }

    private static func makeRegistration(
        preferences: NotificationPreferences
    ) -> DeviceRegistration {
        DeviceRegistration(
            active: true,
            environment: .sandbox,
            locale: "en_US",
            appVersion: "1.0 (42)",
            preferences: preferences,
            registeredAt: Date(timeIntervalSince1970: 1_700_000_000),
            updatedAt: Date(timeIntervalSince1970: 1_700_000_100)
        )
    }
}

private actor BlockingDeviceRegistrationRepository: DeviceRegistrationServicing {
    private var calls: [String] = []
    private var registerStarted = false
    private var registerGate: CheckedContinuation<Void, Never>?
    private var startWaiter: CheckedContinuation<Void, Never>?

    func register(
        _ request: DeviceRegistrationRequest
    ) async throws -> EditableResource<DeviceRegistration> {
        calls.append("register")
        registerStarted = true
        startWaiter?.resume()
        startWaiter = nil
        await withCheckedContinuation { continuation in
            registerGate = continuation
        }
        return EditableResource(
            value: DeviceRegistration(
                active: true,
                environment: request.environment,
                locale: request.locale,
                appVersion: request.appVersion,
                preferences: .defaults,
                registeredAt: Date(timeIntervalSince1970: 1_700_000_000),
                updatedAt: Date(timeIntervalSince1970: 1_700_000_100)
            ),
            etag: #""device-race""#
        )
    }

    func current() async throws -> EditableResource<DeviceRegistration> {
        calls.append("current")
        throw APIError.notFound(nil)
    }

    func updatePreferences(
        _ preferences: NotificationPreferences,
        etag: String
    ) async throws -> EditableResource<DeviceRegistration> {
        throw APIError.unexpectedResponse
    }

    func unregister() async throws {
        calls.append("unregister")
    }

    func waitUntilRegisterStarts() async {
        if registerStarted { return }
        await withCheckedContinuation { continuation in
            startWaiter = continuation
        }
    }

    func resumeRegister() {
        registerGate?.resume()
        registerGate = nil
    }

    func operations() -> [String] { calls }
}

private final class CoordinatorInstallationIDPersistence: InstallationIDPersisting,
    @unchecked Sendable
{
    private let lock = NSLock()
    private var value: String?

    func load() throws -> String? { lock.withLock { value } }
    func save(_ value: String) throws { lock.withLock { self.value = value } }
    func delete() throws { lock.withLock { value = nil } }
}
