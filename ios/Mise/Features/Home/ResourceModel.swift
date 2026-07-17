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

    var requiresSubscriptionRecovery: Bool {
        guard case let .failed(_, failure) = self else { return false }
        return failure == .subscriptionRequired
    }
}

@MainActor
@Observable
final class ResourceModel<Value: Codable & Sendable> {
    private(set) var state: ResourceLoadState<Value> = .idle
    private(set) var isRefreshing = false
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
            state = .loaded(try await remote())
        } catch is CancellationError {
            if let previous {
                state = .loaded(previous)
            } else {
                state = .idle
                loaded = false
            }
        } catch {
            state = .failed(previous, failure: ResourceLoadFailure(error))
        }
    }
}
