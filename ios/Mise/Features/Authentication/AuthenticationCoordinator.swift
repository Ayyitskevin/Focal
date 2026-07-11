import Foundation
import Observation

enum SessionGatePhase {
    case loading
    case signedOut
    case signedIn(CurrentSession)
    case locked(CurrentSession, BiometricKind)
}

@MainActor
@Observable
final class AuthenticationCoordinator {
    private(set) var phase: SessionGatePhase = .loading {
        didSet { notificationCoordinator.router.authenticationDidChange(phase.routerState) }
    }
    private(set) var flow = AuthenticationFlowState()
    private(set) var isWorking = false
    private(set) var workDescription = ""
    private(set) var isUnlocking = false

    var workspaceInput = ""
    var ownerEmail = ""
    var ownerPassword = ""
    var sharedAccessInput = ""
    var sharedAccessPIN = ""
    var selectedCapability: SharedAccessCapability = .gallery
    var errorMessage: String?

    var workspace: WorkspaceSelection? { flow.workspace }

    var authenticationMode: AuthenticationMode {
        get { flow.mode }
        set { flow.mode = newValue }
    }

    var availableAuthenticationModes: [AuthenticationMode] {
        guard let descriptor = flow.workspace?.descriptor else {
            return AuthenticationMode.allCases
        }
        return Self.authenticationModes(for: descriptor)
    }

    private let environment: AppEnvironment
    private let addressParser: WorkspaceAddressParser
    private let sharedAccessParser: SharedAccessTargetParser
    private let originStore: WorkspaceOriginStore
    private let installationIdentity: InstallationIdentity
    private let notificationCoordinator: NotificationCoordinator
    private var activeWorkspace: WorkspaceEnvironment?
    private(set) var ownerRepository: OwnerRepository?
    private(set) var ownerMediaEnvironment: OwnerMediaEnvironment?
    private(set) var clientDeliveryEnvironment: ClientDeliveryEnvironment?
    private var unlockOnActivation = false
    private var applicationIsActive = true
    private var hasRestored = false
    private var deferredIncomingURL: URL?
    private var pendingSharedAccessURL: URL?

    init(
        environment: AppEnvironment,
        originStore: WorkspaceOriginStore = WorkspaceOriginStore(),
        installationIdentity: InstallationIdentity,
        notificationCoordinator: NotificationCoordinator
    ) {
        self.environment = environment
        self.originStore = originStore
        self.installationIdentity = installationIdentity
        self.notificationCoordinator = notificationCoordinator
#if DEBUG
        let permitsInsecureLoopback = true
#else
        let permitsInsecureLoopback = false
#endif
        let parser = WorkspaceAddressParser(
            platformRoot: environment.configuration.serverBaseURL,
            permitsInsecureLoopback: permitsInsecureLoopback
        )
        addressParser = parser
        sharedAccessParser = SharedAccessTargetParser(addressParser: parser)
    }

    func restore() async {
        guard !hasRestored else { return }
        hasRestored = true

        guard let origin = originStore.load(using: addressParser) else {
            phase = .signedOut
            return
        }

        let workspace = environment.workspace(at: origin)
        var savedSession: AuthSession?
        do {
            guard let storedSession = try await workspace.session.sessionSnapshot() else {
                originStore.clear()
                phase = .signedOut
                return
            }
            savedSession = storedSession
            // Do not publish protected disk snapshots until the stored access
            // token is locally usable (or has refreshed successfully). This
            // keeps an already-expired Keychain session behind the loading gate.
            let session: AuthSession
            do {
                guard try await workspace.session.bearerToken() != nil,
                      let refreshed = try await workspace.session.sessionSnapshot()
                else {
                    throw SessionError.expired
                }
                session = refreshed
            } catch {
                guard Self.isRecoverableRestoreFailure(error),
                      storedSession.refreshTokenIsUsable(at: Date())
                else {
                    throw error
                }
                // A still-refreshable offline session may render its protected
                // capability cache. The first remote load will keep the same
                // snapshot and surface its ordinary offline state.
                session = storedSession
            }
            activeWorkspace = workspace
            configureRepositories(for: session.context, workspace: workspace)
            await enterRestoredSession(session.context)
        } catch is CancellationError {
            // View/task cancellation is not a credential failure. Keep Keychain
            // and protected data intact so a later root task can retry restore.
            hasRestored = false
        } catch {
            if Task.isCancelled {
                // Some URL loading paths surface cancellation as a transport
                // error rather than CancellationError.
                hasRestored = false
                return
            }
            if let savedSession {
                await purgeStoredSessionData(savedSession, workspace: workspace)
            }
            await workspace.session.invalidate()
            ownerRepository = nil
            ownerMediaEnvironment = nil
            clientDeliveryEnvironment = nil
            originStore.clear()
            errorMessage = "Your saved session could not be restored. Sign in again."
            phase = .signedOut
        }
    }

    private func purgeStoredSessionData(
        _ session: AuthSession,
        workspace: WorkspaceEnvironment
    ) async {
        if session.principal.kind == .studioOwner {
            try? await TenantJSONCache(
                cacheNamespace: session.workspace.cacheNamespace
            ).removeAll()
            let ownerMedia = workspace.ownerMedia(
                workspaceCacheNamespace: session.workspace.cacheNamespace,
                principalID: session.principal.id,
                sessionID: session.sessionID
            )
            await ownerMedia.purge()
            return
        }
        let clientData = workspace.clientDelivery(
            workspaceCacheNamespace: session.workspace.cacheNamespace,
            principalID: session.principal.id,
            sessionID: session.sessionID
        )
        await clientData.purge()
    }

    func discoverWorkspace() async {
        guard !isWorking else { return }
        isWorking = true
        workDescription = "Connecting to studio"
        errorMessage = nil
        defer {
            isWorking = false
            workDescription = ""
        }

        do {
            let address = try addressParser.parse(workspaceInput)
            let (selection, workspace) = try await discover(address)
            let supportedModes = Self.authenticationModes(for: selection.descriptor)
            guard let fallbackMode = supportedModes.first else {
                throw AuthenticationCoordinatorError.authenticationUnavailable
            }
            let preferredMode = supportedModes.contains(flow.mode) ? flow.mode : fallbackMode
            activeWorkspace = workspace
            flow.didDiscover(selection, preferredMode: preferredMode)
        } catch {
            present(error)
        }
    }

    func showClientLinkEntry() {
        guard !isWorking else { return }
        clearCredentialInputs(keepingSharedAccessInput: false)
        errorMessage = nil
        activeWorkspace = nil
        ownerRepository = nil
        ownerMediaEnvironment = nil
        clientDeliveryEnvironment = nil
        flow.showClientLink()
    }

    func resetWorkspace() {
        guard !isWorking else { return }
        clearCredentialInputs(keepingSharedAccessInput: false)
        workspaceInput = ""
        errorMessage = nil
        activeWorkspace = nil
        ownerRepository = nil
        ownerMediaEnvironment = nil
        clientDeliveryEnvironment = nil
        flow.reset()
    }

    func signInStudioOwner() async {
        guard !isWorking else { return }
        guard let selection = flow.workspace, let workspace = activeWorkspace else {
            errorMessage = "Connect to the studio before signing in."
            return
        }
        guard selection.descriptor.authMethods.contains("studio_password") else {
            errorMessage = "This studio does not offer password sign-in."
            return
        }
        guard !ownerPassword.isEmpty else {
            errorMessage = "Enter your studio password."
            return
        }

        isWorking = true
        workDescription = "Signing in"
        errorMessage = nil
        defer {
            ownerPassword = ""
            isWorking = false
            workDescription = ""
        }

        do {
            let email = ownerEmail.trimmingCharacters(in: .whitespacesAndNewlines)
            let request = StudioLoginRequest(
                email: email.isEmpty ? nil : email,
                password: ownerPassword,
                device: try installationIdentity.deviceContext(
                    appVersion: environment.configuration.clientVersion
                )
            )
            let session = try await workspace.apiClient.send(
                MiseEndpoints.Auth.studioLogin(request)
            )
            try validate(
                session,
                for: selection,
                expectedPrincipal: .studioOwner,
                expectedScopePrefix: "studio:"
            )
            try await workspace.session.install(session)
            completeAuthentication(session, workspace: workspace)
        } catch {
            present(error)
        }
    }

    func unlockSharedAccess() async {
        guard !isWorking else { return }
        isWorking = true
        workDescription = "Opening client access"
        errorMessage = nil
        defer {
            sharedAccessPIN = ""
            isWorking = false
            workDescription = ""
        }

        do {
            let target = try sharedAccessParser.parse(
                sharedAccessInput,
                selectedCapability: selectedCapability,
                currentWorkspaceOrigin: flow.workspace?.address.origin
            )
            selectedCapability = target.capability
            let pin = sharedAccessPIN.trimmingCharacters(in: .whitespacesAndNewlines)
            guard pin.isEmpty || Self.isValidPIN(pin) else {
                throw AuthenticationCoordinatorError.invalidPIN
            }

            let selection: WorkspaceSelection
            let workspace: WorkspaceEnvironment
            if let current = flow.workspace,
               current.address.origin == target.origin,
               let activeWorkspace
            {
                selection = current
                workspace = activeWorkspace
            } else {
                let address = WorkspaceAddress(origin: target.origin, hostedSlug: nil)
                (selection, workspace) = try await discover(address)
                guard selection.descriptor.authMethods.contains("shared_access") else {
                    throw AuthenticationCoordinatorError.sharedAccessUnavailable
                }
                activeWorkspace = workspace
                flow.didDiscover(selection, preferredMode: .sharedAccess)
            }

            guard selection.descriptor.authMethods.contains("shared_access") else {
                throw AuthenticationCoordinatorError.sharedAccessUnavailable
            }

            let request = SharedAccessUnlockRequest(
                kind: target.capability.sharedAccessKind,
                slug: target.slug,
                pin: pin.isEmpty ? nil : pin,
                device: try installationIdentity.deviceContext(
                    appVersion: environment.configuration.clientVersion
                )
            )
            let session = try await workspace.apiClient.send(
                MiseEndpoints.Auth.sharedAccess(request)
            )
            try validate(
                session,
                for: selection,
                expectedPrincipal: target.capability.expectedPrincipal,
                expectedScopePrefix: target.capability.scopePrefix
            )
            try await workspace.session.install(session)
            completeAuthentication(session, workspace: workspace)
        } catch {
            present(error)
        }
    }

    @discardableResult
    func signOut() async -> Bool {
        guard !isWorking else { return false }
        isWorking = true
        workDescription = "Signing out"
        errorMessage = nil

        await notificationCoordinator.unregisterBeforeLogout()

        if let ownerRepository {
            await ownerRepository.purgeCache()
        }
        if let ownerMediaEnvironment {
            await ownerMediaEnvironment.purge()
        }
        if let clientDeliveryEnvironment {
            await clientDeliveryEnvironment.purge()
        }

        if let activeWorkspace {
            do {
                _ = try await activeWorkspace.apiClient.send(MiseEndpoints.Auth.logout)
            } catch {
                // Local revocation still wins when the server is unreachable.
            }
            await activeWorkspace.session.invalidate()
        }

        originStore.clear()
        self.activeWorkspace = nil
        ownerRepository = nil
        ownerMediaEnvironment = nil
        clientDeliveryEnvironment = nil
        clearCredentialInputs(keepingSharedAccessInput: false)
        workspaceInput = ""
        flow.reset()
        unlockOnActivation = false
        phase = .signedOut
        isWorking = false
        workDescription = ""
        return true
    }

    func sceneDidEnterBackground() {
        applicationIsActive = false
        guard case let .signedIn(context) = phase else { return }
        let kind = environment.biometricUnlock.availableKind()
        guard case .unavailable = kind else {
            phase = .locked(context, kind)
            unlockOnActivation = true
            return
        }
    }

    func sceneDidBecomeActive() async {
        applicationIsActive = true
        guard unlockOnActivation else { return }
        unlockOnActivation = false
        await unlockApp()
    }

    func unlockApp() async {
        guard !isUnlocking, case let .locked(context, kind) = phase else { return }
        isUnlocking = true
        errorMessage = nil
        defer { isUnlocking = false }

        do {
            try await environment.biometricUnlock.unlock(
                reason: "Unlock \(context.workspace.displayName) in Mise."
            )
            phase = .signedIn(context)
        } catch BiometricUnlockError.cancelled {
            // Stay locked and let the user explicitly retry or sign out.
        } catch {
            errorMessage = "Could not unlock with \(kind.displayName). Try again or sign out."
        }
    }

    private func discover(
        _ address: WorkspaceAddress
    ) async throws -> (WorkspaceSelection, WorkspaceEnvironment) {
        let workspace = environment.workspace(at: address.origin)
        let descriptor = try await workspace.apiClient.send(MiseEndpoints.Auth.tenant)
        let selection = try WorkspaceSelection(
            address: address,
            descriptor: descriptor,
            parser: addressParser
        )
        return (selection, workspace)
    }

    private static func authenticationModes(
        for descriptor: TenantDescriptor
    ) -> [AuthenticationMode] {
        AuthenticationMode.allCases.filter { mode in
            descriptor.authMethods.contains(mode.authMethod)
        }
    }

    private static func isRecoverableRestoreFailure(_ error: Error) -> Bool {
        guard let apiError = error as? APIError else { return false }
        switch apiError {
        case .transport, .rateLimited, .server:
            return true
        default:
            return false
        }
    }

    private static func isValidPIN(_ value: String) -> Bool {
        let bytes = Array(value.utf8)
        return bytes.count == 4 && bytes.allSatisfy { byte in
            byte >= 48 && byte <= 57
        }
    }

    private func validate(
        _ session: AuthSession,
        for selection: WorkspaceSelection,
        expectedPrincipal: PrincipalKind,
        expectedScopePrefix: String
    ) throws {
        let sessionOrigin = try addressParser.canonicalOrigin(
            for: session.workspace.apiBaseURL,
            allowPath: false
        )
        guard
            sessionOrigin == selection.address.origin,
            session.workspace.cacheNamespace == selection.descriptor.cacheNamespace
        else {
            throw AuthenticationCoordinatorError.workspaceMismatch
        }
        guard session.principal.kind == expectedPrincipal else {
            throw AuthenticationCoordinatorError.principalMismatch
        }
        let scopes = session.principal.scopes
        guard
            !scopes.isEmpty,
            scopes.allSatisfy({ $0.hasPrefix(expectedScopePrefix) }),
            scopes.contains(where: { $0.hasSuffix(":read") })
        else {
            throw AuthenticationCoordinatorError.scopeMismatch
        }
    }

    private func completeAuthentication(
        _ session: AuthSession,
        workspace: WorkspaceEnvironment
    ) {
        activeWorkspace = workspace
        configureRepositories(for: session.context, workspace: workspace)
        originStore.save(workspace.origin)
        clearCredentialInputs(keepingSharedAccessInput: false)
        if applicationIsActive {
            phase = .signedIn(session.context)
        } else {
            let kind = environment.biometricUnlock.availableKind()
            if case .unavailable = kind {
                phase = .signedIn(session.context)
            } else {
                phase = .locked(session.context, kind)
                unlockOnActivation = true
            }
        }
    }

    private func configureRepositories(
        for context: CurrentSession,
        workspace: WorkspaceEnvironment
    ) {
        if context.principal.kind == .studioOwner,
           context.principal.allows("studio:read")
        {
            let ownerMedia = workspace.ownerMedia(
                workspaceCacheNamespace: context.workspace.cacheNamespace,
                principalID: context.principal.id,
                sessionID: context.sessionID
            )
            ownerMediaEnvironment = ownerMedia
            ownerRepository = OwnerRepository(
                client: workspace.apiClient,
                cache: TenantJSONCache(cacheNamespace: context.workspace.cacheNamespace),
                onSessionEnded: {
                    await ownerMedia.purge()
                }
            )
            notificationCoordinator.bindOwner(
                session: context,
                repository: DeviceRegistrationRepository(client: workspace.apiClient)
            )
        } else {
            ownerRepository = nil
            ownerMediaEnvironment = nil
            notificationCoordinator.unbindOwner()
        }

        let kind = context.principal.kind
        if kind == .galleryGuest || kind == .portalGuest || kind == .workspaceGuest
            || kind == .documentGuest,
           Self.hasClientReadScope(context.principal)
        {
            clientDeliveryEnvironment = workspace.clientDelivery(
                workspaceCacheNamespace: context.workspace.cacheNamespace,
                principalID: context.principal.id,
                sessionID: context.sessionID
            )
        } else {
            clientDeliveryEnvironment = nil
        }
    }

    private static func hasClientReadScope(_ principal: Principal) -> Bool {
        let identity = principal.id.split(separator: ":", omittingEmptySubsequences: false)
        let prefix: String
        let expectedReadScope: String
        switch principal.kind {
        case .galleryGuest where identity.count == 2 && identity[0] == "gallery_guest":
            prefix = "gallery:"
            expectedReadScope = "gallery:\(identity[1]):read"
        case .portalGuest where identity.count == 2 && identity[0] == "portal_guest":
            prefix = "portal:"
            expectedReadScope = "portal:\(identity[1]):read"
        case .workspaceGuest where identity.count == 2 && identity[0] == "workspace_guest":
            prefix = "workspace:"
            expectedReadScope = "workspace:\(identity[1]):read"
        case .documentGuest where identity.count == 3 && identity[0] == "document_guest":
            prefix = "document:"
            expectedReadScope = "document:\(identity[1]):\(identity[2]):read"
        default: return false
        }
        return !principal.scopes.isEmpty
            && principal.scopes.allSatisfy { $0.hasPrefix(prefix) }
            && principal.scopes.contains(expectedReadScope)
    }

    private func enterRestoredSession(_ context: CurrentSession) async {
        let kind = environment.biometricUnlock.availableKind()
        if case .unavailable = kind {
            phase = .signedIn(context)
        } else {
            phase = .locked(context, kind)
            await unlockApp()
        }
    }

    var hasPendingSharedAccessSwitch: Bool {
        pendingSharedAccessURL != nil
    }

    func handleIncomingURL(_ url: URL) {
        if url.path.hasPrefix("/app/") {
            notificationCoordinator.router.receiveUniversalLink(url)
            return
        }
        if case .loading = phase {
            deferredIncomingURL = url
            return
        }

        do {
            let target = try sharedAccessParser.parse(
                url.absoluteString,
                selectedCapability: selectedCapability,
                currentWorkspaceOrigin: flow.workspace?.address.origin
            )
            switch phase {
            case .signedOut:
                prepareSharedAccess(url: url, target: target)
            case .signedIn, .locked:
                pendingSharedAccessURL = url
            case .loading:
                deferredIncomingURL = url
            }
        } catch {
            present(error)
        }
    }

    func processDeferredIncomingURL() {
        guard let url = deferredIncomingURL else { return }
        deferredIncomingURL = nil
        handleIncomingURL(url)
    }

    func cancelSharedAccessSwitch() {
        pendingSharedAccessURL = nil
    }

    func confirmSharedAccessSwitch() async {
        guard let url = pendingSharedAccessURL else { return }
        let target: SharedAccessTarget
        do {
            target = try sharedAccessParser.parse(
                url.absoluteString,
                selectedCapability: selectedCapability,
                currentWorkspaceOrigin: flow.workspace?.address.origin
            )
        } catch {
            pendingSharedAccessURL = nil
            present(error)
            return
        }
        guard await signOut() else { return }
        pendingSharedAccessURL = nil
        prepareSharedAccess(url: url, target: target)
    }

    func confirmOwnerWorkspaceSwitch(_ request: WorkspaceSwitchRequest) async {
        guard await signOut() else { return }
        notificationCoordinator.router.queueAfterWorkspaceSwitch(request)
        workspaceInput = request.origin.absoluteString
        await discoverWorkspace()
    }

    private func prepareSharedAccess(url: URL, target: SharedAccessTarget) {
        showClientLinkEntry()
        selectedCapability = target.capability
        sharedAccessInput = url.absoluteString
    }

    private func clearCredentialInputs(keepingSharedAccessInput: Bool) {
        ownerEmail = ""
        ownerPassword = ""
        sharedAccessPIN = ""
        if !keepingSharedAccessInput {
            sharedAccessInput = ""
        }
    }

    private func present(_ error: Error) {
        guard !(error is CancellationError) else { return }
        if let apiError = error as? APIError {
            switch apiError {
            case .decoding, .unexpectedContentType, .unexpectedResponse:
                errorMessage = "The studio returned a response Mise could not read."
            default:
                errorMessage = apiError.errorDescription
            }
            return
        }
        errorMessage =
            (error as? LocalizedError)?.errorDescription
            ?? "Mise could not complete that request."
    }
}

private extension SharedAccessCapability {
    var expectedPrincipal: PrincipalKind {
        switch self {
        case .gallery: .galleryGuest
        case .portal: .portalGuest
        case .workspace: .workspaceGuest
        case .proposal, .contract, .invoice: .documentGuest
        }
    }

    var scopePrefix: String {
        switch self {
        case .gallery: "gallery:"
        case .portal: "portal:"
        case .workspace: "workspace:"
        case .proposal: "document:proposal:"
        case .contract: "document:contract:"
        case .invoice: "document:invoice:"
        }
    }
}

private extension AuthenticationMode {
    var authMethod: String {
        switch self {
        case .studio: "studio_password"
        case .sharedAccess: "shared_access"
        }
    }
}

extension BiometricKind {
    var displayName: String {
        switch self {
        case .faceID: "Face ID"
        case .touchID: "Touch ID"
        case .opticID: "Optic ID"
        case .unavailable: "biometrics"
        }
    }

    var systemImage: String {
        switch self {
        case .faceID: "faceid"
        case .touchID: "touchid"
        case .opticID: "opticid"
        case .unavailable: "lock"
        }
    }
}

enum AuthenticationCoordinatorError: LocalizedError, Sendable {
    case workspaceMismatch
    case principalMismatch
    case scopeMismatch
    case sharedAccessUnavailable
    case authenticationUnavailable
    case invalidPIN

    var errorDescription: String? {
        switch self {
        case .workspaceMismatch:
            "The authenticated session did not match this studio."
        case .principalMismatch:
            "The server returned the wrong access type for this sign-in."
        case .scopeMismatch:
            "The server returned permissions that did not match this access link."
        case .sharedAccessUnavailable:
            "This studio does not offer shared client access."
        case .authenticationUnavailable:
            "This studio does not offer a sign-in method supported by this app."
        case .invalidPIN:
            "A PIN must contain exactly four digits."
        }
    }
}

@MainActor
final class WorkspaceOriginStore {
    private let defaults: UserDefaults
    private let key = "mise.last-workspace-origin"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func load(using parser: WorkspaceAddressParser) -> URL? {
        guard let stored = defaults.string(forKey: key),
              let url = URL(string: stored)
        else {
            return nil
        }
        return try? parser.canonicalOrigin(for: url, allowPath: false)
    }

    func save(_ origin: URL) {
        // Only the public origin is persisted. Tokens, PINs, and capability
        // slugs stay in Keychain or transient memory.
        defaults.set(origin.absoluteString, forKey: key)
    }

    func clear() {
        defaults.removeObject(forKey: key)
    }
}

private extension SessionGatePhase {
    var routerState: RouterAuthenticationState {
        switch self {
        case .loading: .loading
        case .signedOut: .signedOut
        case let .signedIn(session): .signedIn(session)
        case let .locked(session, _): .locked(session)
        }
    }
}
