import XCTest
@testable import Mise

final class DeviceRegistrationEndpointTests: XCTestCase {
    func testRegistrationUsesOwnerCurrentDeviceContractAndOmitsPreferencesOnRotation() throws {
        let request = DeviceRegistrationRequest(
            installationID: "0a90c764-d34c-4fb8-bfbe-172f36d928a1",
            apnsToken: "000102030405060708090a0b0c0d0e0f",
            environment: .sandbox,
            locale: "en_US",
            appVersion: "1.0 (42)",
            preferences: nil
        )

        let endpoint = try MiseEndpoints.Devices.register(request)
        let body = try XCTUnwrap(endpoint.body)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: body) as? [String: Any]
        )

        XCTAssertEqual(endpoint.method, .post)
        XCTAssertEqual(endpoint.path, "/api/v1/devices")
        XCTAssertEqual(object["installation_id"] as? String, request.installationID)
        XCTAssertEqual(object["apns_token"] as? String, request.apnsToken)
        XCTAssertEqual(object["environment"] as? String, "sandbox")
        XCTAssertNil(object["preferences"])
        XCTAssertNil(endpoint.idempotencyKey)
    }

    func testPreferencePatchWrapsCompletePreferencesAndRequiresIfMatch() throws {
        let preferences = NotificationPreferences(
            newBookings: false,
            bookingChanges: true,
            proposalResponses: false,
            payments: true
        )
        let endpoint = try MiseEndpoints.Devices.updatePreferences(
            preferences,
            etag: #""device-v3""#
        )
        let decoded = try MiseJSON.decoder().decode(
            NotificationPreferencesUpdate.self,
            from: XCTUnwrap(endpoint.body)
        )

        XCTAssertEqual(endpoint.method, .patch)
        XCTAssertEqual(endpoint.path, "/api/v1/devices/current")
        XCTAssertEqual(endpoint.headers["If-Match"], #""device-v3""#)
        XCTAssertEqual(decoded.preferences, preferences)
    }

    func testCurrentAndUnregisterUseNoCallerSuppliedIdentifier() {
        XCTAssertEqual(MiseEndpoints.Devices.current.path, "/api/v1/devices/current")
        XCTAssertEqual(MiseEndpoints.Devices.current.method, .get)
        XCTAssertEqual(MiseEndpoints.Devices.unregister.path, "/api/v1/devices/current")
        XCTAssertEqual(MiseEndpoints.Devices.unregister.method, .delete)
    }

    func testResponseContainsNoTokenOrInstallationIdentifier() throws {
        let data = Data(
            #"{"active":true,"environment":"production","locale":"en_US","app_version":"1.0 (42)","preferences":{"new_bookings":true,"booking_changes":false,"proposal_responses":true,"payments":false},"registered_at":"2026-07-11T14:00:00Z","updated_at":"2026-07-11T15:00:00Z"}"#.utf8
        )
        let registration = try MiseJSON.decoder().decode(DeviceRegistration.self, from: data)

        XCTAssertTrue(registration.active)
        XCTAssertEqual(registration.environment, .production)
        XCTAssertFalse(registration.preferences.bookingChanges)
        XCTAssertFalse(registration.preferences.payments)
    }
}
