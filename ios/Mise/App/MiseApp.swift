import SwiftUI

@main
@MainActor
struct MiseApp: App {
    @UIApplicationDelegateAdaptor(MiseApplicationDelegate.self) private var appDelegate

    private let environment: AppEnvironment
    private let installationIdentity: InstallationIdentity
    @State private var notifications: NotificationCoordinator

    init() {
        do {
            let environment = try AppEnvironment.live()
            let installationIdentity = InstallationIdentity()
            self.environment = environment
            self.installationIdentity = installationIdentity
            _notifications = State(initialValue: NotificationCoordinator(
                platformRoot: environment.configuration.serverBaseURL,
                environment: environment.configuration.apnsEnvironment,
                appVersion: environment.configuration.clientVersion,
                installationIdentity: installationIdentity
            ))
        } catch {
            fatalError("Invalid Mise app configuration: \(error.localizedDescription)")
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView(
                environment: environment,
                installationIdentity: installationIdentity,
                notifications: notifications
            )
            .task {
                await appDelegate.notificationBridge.connect(notifications)
                await notifications.start()
            }
        }
    }
}
