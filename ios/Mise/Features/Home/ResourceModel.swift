import Foundation
import Observation

enum ResourceLoadState<Value: Codable & Sendable>: Sendable {
    case idle
    case loading(ResourceSnapshot<Value>?)
    case loaded(ResourceSnapshot<Value>)
    case failed(ResourceSnapshot<Value>?, message: String)

    var snapshot: ResourceSnapshot<Value>? {
        switch self {
        case .idle: nil
        case let .loading(value), let .failed(value, _): value
        case let .loaded(value): value
        }
    }
}

/// A studio-wide billing lockout surfaced when a read returns HTTP 402
/// (`tenant.subscription_required`). It is not a data-load failure — the session
/// is valid, the subscription lapsed — so the owner shell renders a dedicated
/// billing state instead of a generic error (conductor plan T1).
struct BillingLockout: Sendable, Equatable {
    let message: String?
}

@MainActor
@Observable
final class ResourceModel<Value: Codable & Sendable> {
    private(set) var state: ResourceLoadState<Value> = .idle
    private(set) var isRefreshing = false
    /// Non-nil once a refresh returned 402; cleared on the next successful load.
    private(set) var billingLockout: BillingLockout?
    let staleAfter: TimeInterval
    private let cached: @Sendable () async throws -> ResourceSnapshot<Value>?
    private let remote: @Sendable () async throws -> ResourceSnapshot<Value>
    private var loaded = false

    init(
        staleAfter: TimeInterval,
        cached: @escaping @Sendable () async throws -> ResourceSnapshot<Value>?,
        remote: @escaping @Sendable () async throws -> ResourceSnapshot<Value>
    ) {
        self.staleAfter = staleAfter
        self.cached = cached
        self.remote = remote
    }

    func load() async {
        guard !loaded else { return }
        loaded = true
        state = .loading(nil)
        if let value = try? await cached() { state = .loaded(value) }
        guard !Task.isCancelled else {
            if state.snapshot == nil {
                state = .idle
                loaded = false
            }
            return
        }
        await refresh()
    }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        let previous = state.snapshot
        state = .loading(previous)
        defer { isRefreshing = false }
        do {
            let snapshot = try await remote()
            billingLockout = nil
            state = .loaded(snapshot)
        } catch is CancellationError {
            if let previous {
                state = .loaded(previous)
            } else {
                state = .idle
                loaded = false
            }
        } catch {
            if let apiError = error as? APIError,
               case let .subscriptionRequired(problem) = apiError {
                billingLockout = BillingLockout(message: problem?.bestMessage)
            }
            state = .failed(previous, message: error.localizedDescription)
        }
    }
}
