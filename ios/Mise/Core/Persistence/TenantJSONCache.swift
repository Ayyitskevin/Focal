import CryptoKit
import Foundation

enum TenantJSONCacheError: Error, Sendable {
    case invalidEnvelope
}

struct TenantCacheRecord<Value: Codable & Sendable>: Sendable {
    let value: Value
    let storedAt: Date
    let etag: String?
}

/// A small cache for server snapshots, isolated by the backend-provided tenant namespace.
///
/// Raw tenant names and resource keys never become path components. The namespace is
/// also embedded in every envelope and checked while decoding, so a misplaced file
/// cannot cross a tenant boundary.
actor TenantJSONCache {
    private static let schemaVersion = 1
    private static let commandLock = NSLock()

    private let cacheNamespace: String
    private let fileManager: FileManager
    private let namespaceDirectory: URL

    init(
        cacheNamespace: String,
        rootDirectory: URL? = nil,
        fileManager: FileManager = .default
    ) {
        self.cacheNamespace = cacheNamespace
        self.fileManager = fileManager

        let root = rootDirectory ?? Self.defaultRootDirectory(fileManager: fileManager)
        namespaceDirectory = root
            .appendingPathComponent(Self.digest(cacheNamespace), isDirectory: true)
    }

    func read<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type = Value.self
    ) throws -> TenantCacheRecord<Value>? {
        let url = fileURL(for: key)
        guard fileManager.fileExists(atPath: url.path) else { return nil }

        do {
            let data = try Data(contentsOf: url)
            let envelope = try MiseJSON.decoder().decode(
                TenantCacheEnvelope<Value>.self,
                from: data
            )
            guard
                envelope.schemaVersion == Self.schemaVersion,
                envelope.cacheNamespace == cacheNamespace
            else {
                try? fileManager.removeItem(at: url)
                return nil
            }
            return TenantCacheRecord(
                value: envelope.value,
                storedAt: envelope.storedAt,
                etag: envelope.etag
            )
        } catch {
            // A partial, obsolete, protected, or corrupt cache is disposable and
            // must never prevent a live refresh.
            try? fileManager.removeItem(at: url)
            return nil
        }
    }

    /// Command journals are not disposable snapshots. A protected, corrupt, or
    /// version-mismatched file must fail closed without deleting the only replay
    /// key that can safely resolve an ambiguous mutation.
    func readCommand<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type = Value.self
    ) throws -> TenantCacheRecord<Value>? {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }
        return try readStrict(key, as: type)
    }

    /// Atomically keeps the first command written for this tenant/key across all
    /// cache actors in the app process. Callers compare the returned value with
    /// their requested command and either reuse it or surface a conflict.
    @discardableResult
    func writeCommandIfAbsent<Value: Codable & Sendable>(
        _ value: Value,
        key: String,
        etag: String?,
        storedAt: Date = Date()
    ) throws -> TenantCacheRecord<Value> {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }
        if let existing = try readStrict(key, as: Value.self) {
            return existing
        }
        return try writeUnlocked(
            value,
            key: key,
            etag: etag,
            storedAt: storedAt
        )
    }

    /// Compare-and-delete prevents a late response from erasing a different
    /// command that another scene or repository instance persisted.
    @discardableResult
    func removeCommand<Value: Codable & Sendable & Equatable>(
        _ key: String,
        ifMatches expected: Value
    ) throws -> Bool {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }
        guard let current = try readStrict(key, as: Value.self) else {
            return false
        }
        guard current.value == expected else { return false }
        try fileManager.removeItem(at: fileURL(for: key))
        return true
    }

    @discardableResult
    func write<Value: Codable & Sendable>(
        _ value: Value,
        key: String,
        etag: String?,
        storedAt: Date = Date()
    ) throws -> TenantCacheRecord<Value> {
        try writeUnlocked(
            value,
            key: key,
            etag: etag,
            storedAt: storedAt
        )
    }

    private func writeUnlocked<Value: Codable & Sendable>(
        _ value: Value,
        key: String,
        etag: String?,
        storedAt: Date
    ) throws -> TenantCacheRecord<Value> {
        try prepareDirectory()
        let envelope = TenantCacheEnvelope(
            schemaVersion: Self.schemaVersion,
            cacheNamespace: cacheNamespace,
            storedAt: storedAt,
            etag: etag,
            value: value
        )
        let data = try MiseJSON.encoder().encode(envelope)
        let url = fileURL(for: key)
        try data.write(to: url, options: .atomic)
        try fileManager.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        return TenantCacheRecord(value: value, storedAt: storedAt, etag: etag)
    }

    func touch<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type = Value.self,
        etag: String? = nil,
        at date: Date = Date()
    ) throws -> TenantCacheRecord<Value>? {
        guard let current = try read(key, as: type) else { return nil }
        return try write(
            current.value,
            key: key,
            etag: etag ?? current.etag,
            storedAt: date
        )
    }

    func remove(_ key: String) throws {
        let url = fileURL(for: key)
        guard fileManager.fileExists(atPath: url.path) else { return }
        try fileManager.removeItem(at: url)
    }

    func removeAll() throws {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }
        guard fileManager.fileExists(atPath: namespaceDirectory.path) else { return }
        try fileManager.removeItem(at: namespaceDirectory)
    }

    private func prepareDirectory() throws {
        if !fileManager.fileExists(atPath: namespaceDirectory.path) {
            try fileManager.createDirectory(
                at: namespaceDirectory,
                withIntermediateDirectories: true,
                attributes: [
                    .protectionKey: FileProtectionType.completeUntilFirstUserAuthentication,
                ]
            )
        }
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var directory = namespaceDirectory
        try directory.setResourceValues(values)
    }

    private func readStrict<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type
    ) throws -> TenantCacheRecord<Value>? {
        let url = fileURL(for: key)
        guard fileManager.fileExists(atPath: url.path) else { return nil }
        let data = try Data(contentsOf: url)
        let envelope = try MiseJSON.decoder().decode(
            TenantCacheEnvelope<Value>.self,
            from: data
        )
        guard
            envelope.schemaVersion == Self.schemaVersion,
            envelope.cacheNamespace == cacheNamespace
        else {
            throw TenantJSONCacheError.invalidEnvelope
        }
        return TenantCacheRecord(
            value: envelope.value,
            storedAt: envelope.storedAt,
            etag: envelope.etag
        )
    }

    private func fileURL(for key: String) -> URL {
        namespaceDirectory
            .appendingPathComponent(Self.digest(key), isDirectory: false)
            .appendingPathExtension("json")
    }

    private static func defaultRootDirectory(fileManager: FileManager) -> URL {
        let applicationSupport = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? fileManager.temporaryDirectory
        return applicationSupport
            .appendingPathComponent("Mise", isDirectory: true)
            .appendingPathComponent("OwnerCache", isDirectory: true)
    }

    private static func digest(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }
}

private struct TenantCacheEnvelope<Value: Codable & Sendable>: Codable, Sendable {
    let schemaVersion: Int
    let cacheNamespace: String
    let storedAt: Date
    let etag: String?
    let value: Value
}
