import UIKit
import UserNotifications

protocol NotificationEventReceiving: AnyObject, Sendable {
    func receivedRemoteNotificationToken(_ token: Data) async
    func remoteNotificationRegistrationFailed(_ message: String) async
    func receivedNotification(_ envelope: NotificationEnvelope) async
    func shouldPresentNotification(_ envelope: NotificationEnvelope) async -> Bool
}

actor NotificationEventBridge {
    private weak var receiver: (any NotificationEventReceiving)?
    private var bufferedToken: Data?
    private var bufferedFailure: String?
    private var bufferedNotifications: [NotificationEnvelope] = []

    func connect(_ receiver: any NotificationEventReceiving) async {
        self.receiver = receiver
        if let bufferedToken {
            self.bufferedToken = nil
            await receiver.receivedRemoteNotificationToken(bufferedToken)
        }
        if let bufferedFailure {
            self.bufferedFailure = nil
            await receiver.remoteNotificationRegistrationFailed(bufferedFailure)
        }
        let notifications = bufferedNotifications
        bufferedNotifications.removeAll(keepingCapacity: false)
        for notification in notifications {
            await receiver.receivedNotification(notification)
        }
    }

    func receiveToken(_ token: Data) async {
        guard let receiver else {
            bufferedFailure = nil
            bufferedToken = token
            return
        }
        await receiver.receivedRemoteNotificationToken(token)
    }

    func receiveFailure(_ message: String) async {
        guard let receiver else {
            bufferedToken = nil
            bufferedFailure = message
            return
        }
        await receiver.remoteNotificationRegistrationFailed(message)
    }

    func receiveNotification(_ envelope: NotificationEnvelope) async {
        guard let receiver else {
            if bufferedNotifications.count == 10 {
                bufferedNotifications.removeFirst()
            }
            bufferedNotifications.append(envelope)
            return
        }
        await receiver.receivedNotification(envelope)
    }

    func shouldPresent(_ envelope: NotificationEnvelope) async -> Bool {
        guard let receiver else { return false }
        return await receiver.shouldPresentNotification(envelope)
    }
}

final class MiseApplicationDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    let notificationBridge = NotificationEventBridge()

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let bridge = notificationBridge
        Task { await bridge.receiveToken(deviceToken) }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        let message = error.localizedDescription
        let bridge = notificationBridge
        Task { await bridge.receiveFailure(message) }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        guard let envelope = NotificationPayloadDecoder.decode(
            notification.request.content.userInfo
        ) else {
            return []
        }
        let allowed = await notificationBridge.shouldPresent(envelope)
        return allowed ? [.banner, .list, .sound] : []
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        guard let envelope = NotificationPayloadDecoder.decode(
            response.notification.request.content.userInfo
        ) else { return }
        await notificationBridge.receiveNotification(envelope)
    }
}
