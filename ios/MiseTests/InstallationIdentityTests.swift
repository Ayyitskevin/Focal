import XCTest
@testable import Mise

@MainActor
final class InstallationIdentityTests: XCTestCase {
    func testFreshIdentityIsStableAndPersistedAsLowercaseUUID() throws {
        let persistence = InMemoryInstallationIDPersistence()
        let first = InstallationIdentity(
            persistence: persistence,
            legacyDefaults: nil,
            deviceName: { "Test iPhone" }
        )
        let identifier = try first.identifier()
        let second = InstallationIdentity(
            persistence: persistence,
            legacyDefaults: nil,
            deviceName: { "Test iPhone" }
        )

        XCTAssertEqual(try second.identifier(), identifier)
        XCTAssertEqual(try persistence.load(), identifier.uuidString.lowercased())
        XCTAssertEqual(
            try first.deviceContext(appVersion: "1.0").installationID,
            identifier.uuidString.lowercased()
        )
    }

    func testLegacyBackupRestorableIdentifierIsDeletedNotMigrated() throws {
        let suite = "InstallationIdentityTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let legacy = UUID()
        defaults.set(legacy.uuidString.lowercased(), forKey: "mise.installation-id")
        let persistence = InMemoryInstallationIDPersistence()
        let identity = InstallationIdentity(
            persistence: persistence,
            legacyDefaults: defaults,
            deviceName: { "Test iPhone" }
        )

        let generated = try identity.identifier()

        XCTAssertNotEqual(generated, legacy)
        XCTAssertNil(defaults.string(forKey: "mise.installation-id"))
        XCTAssertEqual(try persistence.load(), generated.uuidString.lowercased())
    }

    func testLegacyIdentifierIsDeletedWhenDeviceOnlyIdentityAlreadyExists() throws {
        let suite = "InstallationIdentityTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(UUID().uuidString.lowercased(), forKey: "mise.installation-id")
        let existing = UUID()
        let persistence = InMemoryInstallationIDPersistence(
            value: existing.uuidString.lowercased()
        )
        let identity = InstallationIdentity(
            persistence: persistence,
            legacyDefaults: defaults,
            deviceName: { "Test iPhone" }
        )

        XCTAssertEqual(try identity.identifier(), existing)
        XCTAssertNil(defaults.string(forKey: "mise.installation-id"))
    }

    func testInvalidKeychainValueFailsClosed() {
        let persistence = InMemoryInstallationIDPersistence(value: "not-a-uuid")
        let identity = InstallationIdentity(
            persistence: persistence,
            legacyDefaults: nil,
            deviceName: { "Test iPhone" }
        )

        XCTAssertThrowsError(try identity.identifier()) { error in
            guard case InstallationIdentityError.invalidStoredIdentifier = error else {
                return XCTFail("Unexpected error: \(error)")
            }
        }
    }
}

private final class InMemoryInstallationIDPersistence: InstallationIDPersisting,
    @unchecked Sendable
{
    private let lock = NSLock()
    private var value: String?

    init(value: String? = nil) {
        self.value = value
    }

    func load() throws -> String? {
        lock.withLock { value }
    }

    func save(_ value: String) throws {
        lock.withLock { self.value = value }
    }

    func delete() throws {
        lock.withLock { value = nil }
    }
}
