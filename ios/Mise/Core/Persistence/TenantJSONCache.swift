import CryptoKit
import Foundation

enum TenantJSONCacheError: Error, Sendable {
    case invalidEnvelope
}

enum TenantCommandCommitResult: Equatable, Sendable {
    case committed
    case alreadyCommitted
    case conflict
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
    private static let legacyCommandKeys = [
        "booking.reschedule.pending.v1",
        "booking.reschedule.latest.v1",
    ]

    private let cacheNamespace: String
    private let fileManager: FileManager
    private let namespaceDirectory: URL
    private let commandDirectory: URL

    init(
        cacheNamespace: String,
        rootDirectory: URL? = nil,
        fileManager: FileManager = .default
    ) {
        self.cacheNamespace = cacheNamespace
        self.fileManager = fileManager

        let root = rootDirectory ?? Self.defaultRootDirectory(fileManager: fileManager)
        let namespaceDigest = Self.digest(cacheNamespace)
        namespaceDirectory = root
            .appendingPathComponent(namespaceDigest, isDirectory: true)
        commandDirectory = root
            .appendingPathComponent("\(namespaceDigest).commands", isDirectory: true)
    }

    func read<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type = Value.self
    ) throws -> TenantCacheRecord<Value>? {
        let url = snapshotFileURL(for: key)
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
        return try readStrict(commandFileURL(for: key), as: type)
    }

    /// Strictly reads a command journal written by the pre-session-scoped
    /// implementation. Callers may migrate only a value owned by their session.
    func readLegacyCommand<Value: Codable & Sendable>(
        _ key: String,
        as type: Value.Type = Value.self
    ) throws -> TenantCacheRecord<Value>? {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }
        return try readStrict(snapshotFileURL(for: key), as: type)
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
        let url = commandFileURL(for: key)
        if let existing = try readStrict(url, as: Value.self) {
            return existing
        }
        return try writeUnlocked(
            value,
            at: url,
            etag: etag,
            storedAt: storedAt
        )
    }

    /// Moves an owned legacy journal into session-scoped command storage without
    /// exposing a delete/write gap to another cache actor in this process.
    func migrateLegacyCommandIfMatches<Value: Codable & Sendable & Equatable>(
        _ legacyKey: String,
        to key: String,
        expected: Value
    ) throws -> TenantCacheRecord<Value>? {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }

        let destination = commandFileURL(for: key)
        if let existing = try readStrict(destination, as: Value.self) {
            guard existing.value == expected else { return nil }
            let legacyURL = snapshotFileURL(for: legacyKey)
            if let legacy = try readStrict(legacyURL, as: Value.self) {
                guard legacy.value == expected else { return nil }
                try fileManager.removeItem(at: legacyURL)
            }
            return existing
        }
        let legacyURL = snapshotFileURL(for: legacyKey)
        guard
            let legacy = try readStrict(legacyURL, as: Value.self),
            legacy.value == expected
        else {
            return nil
        }
        let migrated = try writeUnlocked(
            legacy.value,
            at: destination,
            etag: legacy.etag,
            storedAt: legacy.storedAt
        )
        try fileManager.removeItem(at: legacyURL)
        return migrated
    }

    /// Commits a mutation receipt and consumes its replay journal as one critical
    /// section. A late response can therefore never overwrite a newer command's
    /// workflow handle before discovering that its pending journal has changed.
    func commitCommand<
        Command: Codable & Sendable & Equatable,
        Result: Codable & Sendable & Equatable
    >(
        _ result: Result,
        resultKey: String,
        consuming commandKey: String,
        ifMatches expected: Command,
        storedAt: Date = Date()
    ) throws -> TenantCommandCommitResult {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }

        let commandURL = commandFileURL(for: commandKey)
        if let command = try readStrict(commandURL, as: Command.self) {
            guard command.value == expected else { return .conflict }
            _ = try writeUnlocked(
                result,
                at: commandFileURL(for: resultKey),
                etag: nil,
                storedAt: storedAt
            )
            try fileManager.removeItem(at: commandURL)
            return .committed
        }

        let committed = try readStrict(
            commandFileURL(for: resultKey),
            as: Result.self
        )
        return committed?.value == result ? .alreadyCommitted : .conflict
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
        let url = commandFileURL(for: key)
        guard let current = try readStrict(url, as: Value.self) else {
            return false
        }
        guard current.value == expected else { return false }
        try fileManager.removeItem(at: url)
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
            at: snapshotFileURL(for: key),
            etag: etag,
            storedAt: storedAt
        )
    }

    private func writeUnlocked<Value: Codable & Sendable>(
        _ value: Value,
        at url: URL,
        etag: String?,
        storedAt: Date
    ) throws -> TenantCacheRecord<Value> {
        try prepareDirectory(url.deletingLastPathComponent())
        let envelope = TenantCacheEnvelope(
            schemaVersion: Self.schemaVersion,
            cacheNamespace: cacheNamespace,
            storedAt: storedAt,
            etag: etag,
            value: value
        )
        let data = try MiseJSON.encoder().encode(envelope)
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
        let url = snapshotFileURL(for: key)
        guard fileManager.fileExists(atPath: url.path) else { return }
        try fileManager.removeItem(at: url)
    }

    func removeAll() throws {
        Self.commandLock.lock()
        defer { Self.commandLock.unlock() }
        guard fileManager.fileExists(atPath: namespaceDirectory.path) else { return }

        // Legacy command journals predate the separate durable directory. Keep
        // those exact hashed files until an owning session can migrate them; a
        // snapshot purge must not turn an ambiguous mutation into a fresh start.
        let protectedPaths = Set(
            Self.legacyCommandKeys.map { snapshotFileURL(for: $0).path }
        )
        for url in try fileManager.contentsOfDirectory(
            at: namespaceDirectory,
            includingPropertiesForKeys: nil
        ) where !protectedPaths.contains(url.path) {
            try fileManager.removeItem(at: url)
        }
        if try fileManager.contentsOfDirectory(atPath: namespaceDirectory.path).isEmpty {
            try fileManager.removeItem(at: namespaceDirectory)
        }
    }

    private func prepareDirectory(_ directoryURL: URL) throws {
        if !fileManager.fileExists(atPath: directoryURL.path) {
            try fileManager.createDirectory(
                at: directoryURL,
                withIntermediateDirectories: true,
                attributes: [
                    .protectionKey: FileProtectionType.completeUntilFirstUserAuthentication,
                ]
            )
        }
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var directory = directoryURL
        try directory.setResourceValues(values)
    }

    private func readStrict<Value: Codable & Sendable>(
        _ url: URL,
        as type: Value.Type
    ) throws -> TenantCacheRecord<Value>? {
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

    private func snapshotFileURL(for key: String) -> URL {
        namespaceDirectory
            .appendingPathComponent(Self.digest(key), isDirectory: false)
            .appendingPathExtension("json")
    }

    private func commandFileURL(for key: String) -> URL {
        commandDirectory
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
