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

        let didRefresh = await model.refresh()

        XCTAssertTrue(didRefresh)
        guard case let .loaded(snapshot) = model.state else {
            return XCTFail("Expected retry to restore loaded content")
        }
        XCTAssertEqual(snapshot.value, ["live client"])
        XCTAssertFalse(model.requiresSubscriptionRecovery)
    }

    @MainActor
    func testCancelledRefreshRestoresSnapshotButReportsNoFreshResponse() async {
        let cached = ResourceSnapshot(
            value: ["saved task"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: .cache
        )
        let model = ResourceModel<[String]>(
            staleAfter: 60,
            cached: { cached },
            remote: { throw CancellationError() }
        )

        await model.load()
        let didRefresh = await model.refresh()

        XCTAssertFalse(didRefresh)
        guard case let .loaded(snapshot) = model.state else {
            return XCTFail("Expected cancellation to retain the prior snapshot")
        }
        XCTAssertEqual(snapshot.value, ["saved task"])
        XCTAssertEqual(snapshot.source, .cache)
    }

    @MainActor
    func testRefreshAfterCurrentQueuesANewerRequest() async {
        let initial = ResourceSnapshot(
            value: ["initial"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: .network
        )
        let older = ResourceSnapshot(
            value: ["older response"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_001),
            source: .network
        )
        let newest = ResourceSnapshot(
            value: ["post-mutation response"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_002),
            source: .network
        )
        let remote = RefreshQueueStub(initial: initial, older: older, newest: newest)
        let model = ResourceModel<[String]>(
            staleAfter: 60,
            cached: { nil },
            remote: { await remote.next() }
        )
        await model.load()

        let first = Task { @MainActor in await model.refresh() }
        await remote.waitUntilOlderRequest()
        let queued = Task { @MainActor in await model.refreshAfterCurrent() }
        await remote.finishOlderRequest()

        let firstResult = await first.value
        let queuedResult = await queued.value
        let callCount = await remote.callCount()
        XCTAssertTrue(firstResult)
        XCTAssertTrue(queuedResult)
        XCTAssertEqual(callCount, 3)
        XCTAssertEqual(model.state.snapshot?.value, ["post-mutation response"])
    }

    @MainActor
    func testSessionFallbackSurvivesFailedFirstNetworkLoad() async {
        let fallback = ResourceSnapshot(
            value: ["confirmed reopened task"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: ResourceSnapshotSource.session
        )
        let model = ResourceModel<[String]>(
            staleAfter: 60,
            cached: { nil },
            remote: { throw OfflineError() }
        )
        model.supplySessionFallback(
            fallback.value,
            storedAt: fallback.storedAt
        )

        await model.load()

        guard case let .failed(snapshot, failure) = model.state else {
            return XCTFail("Expected the failed load to preserve session data")
        }
        XCTAssertEqual(snapshot?.value, fallback.value)
        XCTAssertEqual(snapshot?.source, .session)
        XCTAssertEqual(failure, .general(message: "Offline for test"))
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

private actor RefreshQueueStub {
    private let initial: ResourceSnapshot<[String]>
    private let older: ResourceSnapshot<[String]>
    private let newest: ResourceSnapshot<[String]>
    private var calls = 0
    private var olderContinuation: CheckedContinuation<ResourceSnapshot<[String]>, Never>?
    private var olderObservers: [CheckedContinuation<Void, Never>] = []

    init(
        initial: ResourceSnapshot<[String]>,
        older: ResourceSnapshot<[String]>,
        newest: ResourceSnapshot<[String]>
    ) {
        self.initial = initial
        self.older = older
        self.newest = newest
    }

    func next() async -> ResourceSnapshot<[String]> {
        calls += 1
        if calls == 1 { return initial }
        if calls == 2 {
            let observers = olderObservers
            olderObservers.removeAll()
            observers.forEach { $0.resume() }
            return await withCheckedContinuation { olderContinuation = $0 }
        }
        return newest
    }

    func waitUntilOlderRequest() async {
        guard calls < 2 else { return }
        await withCheckedContinuation { olderObservers.append($0) }
    }

    func finishOlderRequest() {
        precondition(olderContinuation != nil)
        olderContinuation?.resume(returning: older)
        olderContinuation = nil
    }

    func callCount() -> Int {
        calls
    }
}
