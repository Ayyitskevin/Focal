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

        guard case let .failed(snapshot, failure) = model.state else {
            return XCTFail("Expected cached failure state")
        }
        XCTAssertEqual(snapshot?.value, ["saved client"])
        XCTAssertEqual(failure, .general(message: "Offline for test"))
        XCTAssertFalse(model.requiresSubscriptionRecovery)
    }

    @MainActor
    func testSubscriptionRequiredOverridesCachedStateAndRetryRecovers() async {
        let cached = ResourceSnapshot(
            value: ["stale client"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: .cache
        )
        let recovered = ResourceSnapshot(
            value: ["live client"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_100),
            source: .network
        )
        let remote = SubscriptionRetryStub(recovered: recovered)
        let model = ResourceModel<[String]>(
            staleAfter: 60,
            cached: { cached },
            remote: { try await remote.next() }
        )

        await model.load()

        guard case let .failed(snapshot, failure) = model.state else {
            return XCTFail("Expected subscription recovery state")
        }
        XCTAssertEqual(snapshot?.value, ["stale client"])
        XCTAssertEqual(failure, .subscriptionRequired)
        XCTAssertTrue(model.requiresSubscriptionRecovery)

        await model.refresh()

        guard case let .loaded(snapshot) = model.state else {
            return XCTFail("Expected retry to restore loaded content")
        }
        XCTAssertEqual(snapshot.value, ["live client"])
        XCTAssertFalse(model.requiresSubscriptionRecovery)
    }
}

private struct OfflineError: LocalizedError, Sendable {
    var errorDescription: String? { "Offline for test" }
}

private actor SubscriptionRetryStub {
    let recovered: ResourceSnapshot<[String]>
    private var attempts = 0

    init(recovered: ResourceSnapshot<[String]>) {
        self.recovered = recovered
    }

    func next() throws -> ResourceSnapshot<[String]> {
        attempts += 1
        if attempts == 1 {
            throw APIError.subscriptionRequired(
                APIProblem(
                    status: 402,
                    code: "tenant.subscription_required",
                    detail: "Update billing to continue."
                )
            )
        }
        return recovered
    }
}
