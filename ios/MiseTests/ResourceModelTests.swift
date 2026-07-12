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
}

private struct OfflineError: LocalizedError, Sendable {
    var errorDescription: String? { "Offline for test" }
}
