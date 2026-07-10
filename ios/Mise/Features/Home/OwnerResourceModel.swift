import Foundation
import Observation

enum OwnerLoadState<Value: Codable & Sendable>: Sendable {
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

@MainActor
@Observable
final class OwnerResourceModel<Value: Codable & Sendable> {
    private(set) var state: OwnerLoadState<Value> = .idle
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
            state = .failed(previous, message: error.localizedDescription)
        }
    }
}
