import Foundation
import Security
import UIKit

protocol InstallationIDPersisting: Sendable {
    func load() throws -> String?
    func save(_ value: String) throws
    func delete() throws
}

struct KeychainInstallationIDPersistence: InstallationIDPersisting, @unchecked Sendable {
    private let service: String
    private let account: String

    init(service: String, account: String = "installation-id") {
        self.service = service
        self.account = account
    }

    func load() throws -> String? {
        var query = baseQuery
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        query[kSecReturnData as String] = true

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else {
            throw InstallationIdentityError.keychainStatus(status)
        }
        guard let data = item as? Data,
              let value = String(data: data, encoding: .utf8)
        else {
            throw InstallationIdentityError.invalidStoredIdentifier
        }
        return value
    }

    func save(_ value: String) throws {
        guard let data = value.data(using: .utf8) else {
            throw InstallationIdentityError.invalidStoredIdentifier
        }
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus = SecItemUpdate(baseQuery as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess { return }
        guard updateStatus == errSecItemNotFound else {
            throw InstallationIdentityError.keychainStatus(updateStatus)
        }

        var query = baseQuery
        attributes.forEach { query[$0.key] = $0.value }
        let addStatus = SecItemAdd(query as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw InstallationIdentityError.keychainStatus(addStatus)
        }
    }

    func delete() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw InstallationIdentityError.keychainStatus(status)
        }
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: false,
        ]
    }
}

@MainActor
final class InstallationIdentity {
    private let persistence: any InstallationIDPersisting
    private let legacyDefaults: UserDefaults?
    private let deviceName: () -> String
    private let legacyKey = "mise.installation-id"
    private var cachedIdentifier: UUID?

    init(
        persistence: (any InstallationIDPersisting)? = nil,
        legacyDefaults: UserDefaults? = .standard,
        deviceName: @escaping () -> String = { UIDevice.current.name }
    ) {
        self.persistence = persistence ?? KeychainInstallationIDPersistence(
            service: Bundle.main.bundleIdentifier ?? "com.ayyitskevin.mise"
        )
        self.legacyDefaults = legacyDefaults
        self.deviceName = deviceName
    }

    func identifier() throws -> UUID {
        if let cachedIdentifier { return cachedIdentifier }

        // The pre-release UserDefaults identity was backup-restorable. Always
        // remove it, including when this install already has its replacement,
        // so no stale device identifier survives in a backup.
        legacyDefaults?.removeObject(forKey: legacyKey)

        if let stored = try persistence.load() {
            guard let value = UUID(uuidString: stored) else {
                throw InstallationIdentityError.invalidStoredIdentifier
            }
            cachedIdentifier = value
            return value
        }

        // This app has not shipped. Never migrate the backup-restorable legacy
        // value: a restored backup must receive a new device-only identity.
        let value = UUID()
        try persistence.save(value.uuidString.lowercased())
        cachedIdentifier = value
        return value
    }

    func deviceContext(appVersion: String) throws -> DeviceContext {
        DeviceContext(
            installationID: try identifier().uuidString.lowercased(),
            name: deviceName(),
            appVersion: appVersion
        )
    }
}

enum InstallationIdentityError: LocalizedError, Sendable {
    case invalidStoredIdentifier
    case keychainStatus(OSStatus)

    var errorDescription: String? {
        switch self {
        case .invalidStoredIdentifier:
            "The device installation identity is invalid."
        case let .keychainStatus(status):
            "The device installation identity could not be accessed (\(status))."
        }
    }
}
