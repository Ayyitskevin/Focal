import Foundation

struct APNsEnvironment: APIStringValue {
    let rawValue: String

    init(rawValue: String) {
        self.rawValue = rawValue
    }

    static let sandbox = Self(rawValue: "sandbox")
    static let production = Self(rawValue: "production")
}

struct NotificationPreferences: Codable, Hashable, Sendable {
    var newBookings: Bool
    var bookingChanges: Bool
    var proposalResponses: Bool
    var payments: Bool

    static let defaults = NotificationPreferences(
        newBookings: true,
        bookingChanges: true,
        proposalResponses: true,
        payments: true
    )
}

struct DeviceRegistrationRequest: Codable, Hashable, Sendable {
    let installationID: String
    let apnsToken: String
    let environment: APNsEnvironment
    let locale: String
    let appVersion: String
    /// Nil during ordinary token rotation so a launch can never reset preferences.
    let preferences: NotificationPreferences?
}

struct DeviceRegistration: Codable, Hashable, Sendable {
    let active: Bool
    let environment: APNsEnvironment
    let locale: String
    let appVersion: String
    let preferences: NotificationPreferences
    let registeredAt: Date
    let updatedAt: Date
}

struct NotificationPreferencesUpdate: Codable, Hashable, Sendable {
    let preferences: NotificationPreferences
}

enum NotificationPermissionState: Equatable, Sendable {
    case loading
    case notDetermined
    case denied
    case authorized
    case provisional
    case ephemeral
    case unsupported

    var canRegisterWithAPNs: Bool {
        switch self {
        case .authorized, .provisional, .ephemeral: true
        case .loading, .notDetermined, .denied, .unsupported: false
        }
    }
}
