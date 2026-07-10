import Foundation
import Security

protocol SessionPersisting: Sendable {
    func load() throws -> AuthSession?
    func save(_ session: AuthSession) throws
    func delete() throws
}
struct KeychainSessionPersistence: SessionPersisting, @unchecked Sendable {
    private let service: String
    private let account: String

    init(service: String, account: String = "authenticated-session") {
        self.service = service
        self.account = account
    }

    func load() throws -> AuthSession? {
        var query = baseQuery
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        query[kSecReturnData as String] = true

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = item as? Data else {
            throw KeychainError.unhandledStatus(status)
        }

        do {
            return try MiseJSON.decoder().decode(AuthSession.self, from: data)
        } catch {
            throw KeychainError.invalidStoredSession
        }
    }

    func save(_ session: AuthSession) throws {
        let data = try MiseJSON.encoder().encode(session)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]

        let updateStatus = SecItemUpdate(
            baseQuery as CFDictionary,
            attributes as CFDictionary
        )
        if updateStatus == errSecSuccess {
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw KeychainError.unhandledStatus(updateStatus)
        }

        var addQuery = baseQuery
        attributes.forEach { addQuery[$0.key] = $0.value }
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw KeychainError.unhandledStatus(addStatus)
        }
    }

    func delete() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.unhandledStatus(status)
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

enum KeychainError: LocalizedError, Sendable {
    case unhandledStatus(OSStatus)
    case invalidStoredSession

    var errorDescription: String? {
        switch self {
        case let .unhandledStatus(status):
            "Keychain operation failed with status \(status)."
        case .invalidStoredSession:
            "The stored Mise session is invalid."
        }
    }
}
