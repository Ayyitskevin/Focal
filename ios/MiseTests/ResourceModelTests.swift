import XCTest
@testable import Mise

final class ResourceModelTests: XCTestCase {
    @MainActor
    func testFailedRefreshKeepsCachedSnapshotVisible() async {
        let cached = ResourceSnapshot(
            value: ["saved client"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: .cache
        )
        let model = ResourceModel<[String]>(
            staleAfter: 60,
            cached: { cached },
            remote: { throw OfflineError() }
        )

        await model.load()

        guard case let .failed(snapshot, message) = model.state else {
            return XCTFail("Expected cached failure state")
        }
        XCTAssertEqual(snapshot?.value, ["saved client"])
        XCTAssertEqual(message, "Offline for test")
    }

    @MainActor
    func testSubscriptionRequiredSetsBillingLockoutAndRecoveryClearsIt() async {
        let shouldLock = FlagBox(true)
        let model = ResourceModel<[String]>(
            staleAfter: 60,
            cached: { nil },
            remote: {
                if shouldLock.value {
                    throw APIError.subscriptionRequired(
                        APIProblem(status: 402, detail: "Your studio subscription is paused.")
                    )
                }
                return ResourceSnapshot(value: ["ok"], storedAt: Date(), source: .network)
            }
        )

        await model.load()
        XCTAssertEqual(model.billingLockout?.message, "Your studio subscription is paused.")

        // Once billing is fixed, a successful refresh clears the lockout.
        shouldLock.value = false
        await model.refresh()
        XCTAssertNil(model.billingLockout)
        XCTAssertEqual(model.state.snapshot?.value, ["ok"])
    }

    @MainActor
    func testNonBillingFailureLeavesBillingLockoutNil() async {
        let model = ResourceModel<[String]>(
            staleAfter: 60,
            cached: { nil },
            remote: { throw OfflineError() }
        )

        await model.load()
        XCTAssertNil(model.billingLockout)
    }
}

private struct OfflineError: LocalizedError, Sendable {
    var errorDescription: String? { "Offline for test" }
}

private final class FlagBox: @unchecked Sendable {
    var value: Bool
    init(_ value: Bool) { self.value = value }
}
