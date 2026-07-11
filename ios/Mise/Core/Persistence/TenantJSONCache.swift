import CryptoKit
import Foundation

struct TenantCacheRecord<Value: Codable & Sendable>: Sendable {
    let value: Value
    let storedAt: Date
    let etag: String?
}

enum TenantCacheAccessError: Error, Sendable {
    case ended
}

/// A small cache for server snapshots, isolated by the backend-provided tenant namespace.
///
/// Raw tenant names and resource keys never become path components. The namespace is
/// also embedded in every envelope and checked while decoding, so a misplaced file
/// cannot cross a tenant boundary.
actor TenantJSONCache {
    private static let schemaVersion = 1

    private let cacheNamespace: String
    private let fileManager: FileManager
    private let namespaceDirectory: URL
    private var accessEnded = false

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
        guard !accessEnded else { return nil }
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

    @discardableResult
    func write<Value: Codable & Sendable>(
        _ value: Value,
        key: String,
        etag: String?,
        storedAt: Date = Date()
    ) throws -> TenantCacheRecord<Value> {
        guard !accessEnded else { throw TenantCacheAccessError.ended }
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

    func update<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type = Value.self,
        transform: @Sendable (Value) -> Value
    ) throws -> TenantCacheRecord<Value>? {
        guard let current = try read(key, as: type) else { return nil }
        return try write(
            transform(current.value),
            key: key,
            etag: nil
        )
    }

    func remove(_ key: String) throws {
        guard !accessEnded else { return }
        let url = fileURL(for: key)
        guard fileManager.fileExists(atPath: url.path) else { return }
        try fileManager.removeItem(at: url)
    }

    func removeAll() throws {
        guard !accessEnded else { return }
        try removeAllFiles()
    }

    private func removeAllFiles() throws {
        guard fileManager.fileExists(atPath: namespaceDirectory.path) else { return }
        try fileManager.removeItem(at: namespaceDirectory)
    }

    /// Permanently closes this session's cache actor and removes its namespace.
    /// Because file writes and this method are synchronous actor operations, a
    /// racing write either finishes first and is removed here, or runs second and
    /// is rejected before recreating the directory.
    func endAccessAndRemoveAll() throws {
        guard !accessEnded else { return }
        accessEnded = true
        try removeAllFiles()
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
