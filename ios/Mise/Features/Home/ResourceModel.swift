import Foundation
import Observation

enum ResourceLoadFailure: Equatable, Sendable {
    case subscriptionRequired
    case general(message: String)

    init(_ error: Error) {
        if let apiError = error as? APIError,
           case .subscriptionRequired = apiError
        {
            self = .subscriptionRequired
        } else {
            self = .general(message: error.localizedDescription)
        }
    }

    var message: String {
        switch self {
        case .subscriptionRequired:
            "This studio’s subscription needs attention."
        case let .general(message):
            message
        }
    }
}

enum ResourceLoadState<Value: Codable & Sendable>: Sendable {
    case idle
    case loading(ResourceSnapshot<Value>?)
    case loaded(ResourceSnapshot<Value>)
    case failed(ResourceSnapshot<Value>?, failure: ResourceLoadFailure)

    var snapshot: ResourceSnapshot<Value>? {
        switch self {
        case .idle: nil
        case let .loading(value), let .failed(value, _): value
        case let .loaded(value): value
        }
    }
}

@MainActor
@Observable
final class ResourceModel<Value: Codable & Sendable> {
    private(set) var state: ResourceLoadState<Value> = .idle
    private(set) var isRefreshing = false
    private(set) var requiresSubscriptionRecovery = false
    let staleAfter: TimeInterval
    private let cached: @Sendable () async throws -> ResourceSnapshot<Value>?
    private let remote: @Sendable () async throws -> ResourceSnapshot<Value>
    private var loaded = false
    private var refreshWaiters: [CheckedContinuation<Void, Never>] = []
    private var sessionFallback: ResourceSnapshot<Value>?

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
        state = .loading(state.snapshot ?? sessionFallback)
        if let value = try? await cached() { state = .loaded(value) }
        guard !Task.isCancelled else {
            if let snapshot = state.snapshot {
                state = .loaded(snapshot)
            } else {
                state = .idle
                loaded = false
            }
            return
        }
        await refresh()
    }

    @discardableResult
    func refresh() async -> Bool {
        guard !isRefreshing else { return false }
        isRefreshing = true
        let previous = state.snapshot
        state = .loading(previous)
        defer {
            isRefreshing = false
            let waiters = refreshWaiters
            refreshWaiters.removeAll()
            waiters.forEach { $0.resume() }
        }
        do {
            let snapshot = try await remote()
            sessionFallback = nil
            requiresSubscriptionRecovery = false
            loaded = true
            state = .loaded(snapshot)
            return true
        } catch is CancellationError {
            if let retained = state.snapshot ?? sessionFallback ?? previous {
                state = .loaded(retained)
            } else {
                state = .idle
                loaded = false
            }
            return false
        } catch {
            let failure = ResourceLoadFailure(error)
            if failure == .subscriptionRequired {
                requiresSubscriptionRecovery = true
            }
            state = .failed(
                state.snapshot ?? sessionFallback ?? previous,
                failure: failure
            )
            return false
        }
    }

    /// Supplies command-confirmed, session-only rows when no read snapshot has
    /// resolved yet. The next load/refresh still goes to the network and keeps
    /// this value only when that request cannot produce a fresher snapshot.
    func supplySessionFallback(_ value: Value, storedAt: Date = Date()) {
        guard state.snapshot == nil else { return }
        let snapshot = ResourceSnapshot(
            value: value,
            storedAt: storedAt,
            source: .session
        )
        sessionFallback = snapshot
        switch state {
        case .idle, .loaded:
            state = .loaded(snapshot)
        case .loading:
            state = .loading(snapshot)
        case let .failed(_, failure):
            state = .failed(snapshot, failure: failure)
        }
    }

    /// Waits out an older request, then performs a new fetch. Mutation flows use
    /// this instead of dropping the post-mutation refresh behind an in-flight
    /// pre-mutation request.
    @discardableResult
    func refreshAfterCurrent() async -> Bool {
        while isRefreshing {
            await withCheckedContinuation { continuation in
                if isRefreshing {
                    refreshWaiters.append(continuation)
                } else {
                    continuation.resume()
                }
            }
        }
        guard !Task.isCancelled else { return false }
        return await refresh()
    }
}
