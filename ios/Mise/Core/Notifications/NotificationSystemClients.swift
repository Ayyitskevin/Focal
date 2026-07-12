import UIKit
import UserNotifications

@MainActor
protocol NotificationAuthorizationProviding: AnyObject {
    func permissionState() async -> NotificationPermissionState
    func requestAuthorization() async throws -> Bool
    func clearPendingAndDeliveredNotifications()
}

@MainActor
final class SystemNotificationAuthorizationProvider: NotificationAuthorizationProviding {
    private let center: UNUserNotificationCenter

    init(center: UNUserNotificationCenter = .current()) {
        self.center = center
    }

    func permissionState() async -> NotificationPermissionState {
        await withCheckedContinuation { continuation in
            center.getNotificationSettings { settings in
                let state: NotificationPermissionState
                switch settings.authorizationStatus {
                case .notDetermined: state = .notDetermined
                case .denied: state = .denied
                case .authorized: state = .authorized
                case .provisional: state = .provisional
                case .ephemeral: state = .ephemeral
                @unknown default: state = .unsupported
                }
                continuation.resume(returning: state)
            }
        }
    }

    func requestAuthorization() async throws -> Bool {
        try await center.requestAuthorization(options: [.alert, .badge, .sound])
    }

    func clearPendingAndDeliveredNotifications() {
        center.removeAllPendingNotificationRequests()
        center.removeAllDeliveredNotifications()
    }
}

@MainActor
protocol RemoteNotificationRegistering: AnyObject {
    func registerForRemoteNotifications()
    func unregisterForRemoteNotifications()
    func openNotificationSettings()
}

@MainActor
final class SystemRemoteNotificationRegistrar: RemoteNotificationRegistering {
    func registerForRemoteNotifications() {
        UIApplication.shared.registerForRemoteNotifications()
    }

    func unregisterForRemoteNotifications() {
        UIApplication.shared.unregisterForRemoteNotifications()
    }

    func openNotificationSettings() {
        guard let url = URL(string: UIApplication.openNotificationSettingsURLString) else {
            return
        }
        UIApplication.shared.open(url)
    }
}
