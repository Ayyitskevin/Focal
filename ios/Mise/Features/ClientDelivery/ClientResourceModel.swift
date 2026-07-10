import Foundation
import Observation
import SwiftUI

enum ClientLoadState<Value: Codable & Sendable>: Sendable {
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
final class ClientResourceModel<Value: Codable & Sendable> {
    private(set) var state: ClientLoadState<Value> = .idle
    private(set) var isRefreshing = false
    let staleAfter: TimeInterval
    private let cached: @Sendable () async throws -> ResourceSnapshot<Value>?
    private let remote: @Sendable () async throws -> ResourceSnapshot<Value>
    private let discardsSnapshotOnFailure: @Sendable (Error) -> Bool
    private var loaded = false

    init(
        staleAfter: TimeInterval,
        cached: @escaping @Sendable () async throws -> ResourceSnapshot<Value>?,
        remote: @escaping @Sendable () async throws -> ResourceSnapshot<Value>,
        discardsSnapshotOnFailure: @escaping @Sendable (Error) -> Bool = { _ in false }
    ) {
        self.staleAfter = staleAfter
        self.cached = cached
        self.remote = remote
        self.discardsSnapshotOnFailure = discardsSnapshotOnFailure
    }

    func load() async {
        guard !loaded else { return }
        loaded = true
        state = .loading(nil)
        if let snapshot = try? await cached() {
            state = .loaded(snapshot)
        }
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
            state = .failed(
                discardsSnapshotOnFailure(error) ? nil : previous,
                message: error.localizedDescription
            )
        }
    }

    func replace(_ value: Value, source: ResourceSnapshotSource = .network) {
        state = .loaded(ResourceSnapshot(value: value, storedAt: Date(), source: source))
    }
}

struct ClientResourceView<Value: Codable & Sendable, Content: View, Empty: View>: View {
    let model: ClientResourceModel<Value>
    let isEmpty: (Value) -> Bool
    let content: (Value) -> Content
    let empty: () -> Empty
    var failureTitle = "Couldn’t open this access"
    var failureSystemImage = "wifi.exclamationmark"

    var body: some View {
        Group {
            if let snapshot = model.state.snapshot {
                VStack(spacing: 0) {
                    if let statusMessage {
                        Label(statusMessage, systemImage: statusIsWarning ? "wifi.slash" : "arrow.triangle.2.circlepath")
                            .font(.footnote)
                            .foregroundStyle(statusIsWarning ? Color.orange : Color.secondary)
                            .padding(.horizontal)
                            .padding(.vertical, 8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.secondary.opacity(0.08))
                    }
                    if isEmpty(snapshot.value) {
                        ScrollView {
                            empty()
                                .frame(maxWidth: .infinity)
                                .containerRelativeFrame(.vertical)
                        }
                        .refreshable { await model.refresh() }
                    } else {
                        content(snapshot.value)
                    }
                }
            } else {
                switch model.state {
                case .idle, .loading:
                    ProgressView("Loading…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                case let .failed(_, message):
                    ContentUnavailableView {
                        Label(failureTitle, systemImage: failureSystemImage)
                    } description: {
                        Text(message)
                    } actions: {
                        Button("Try Again") { Task { await model.refresh() } }
                            .buttonStyle(.borderedProminent)
                    }
                case .loaded:
                    EmptyView()
                }
            }
        }
        .task { await model.load() }
    }

    private var statusMessage: String? {
        guard let snapshot = model.state.snapshot else { return nil }
        switch model.state {
        case .loading:
            return "Updating saved access…"
        case let .failed(_, message):
            return "Offline — showing a protected copy saved \(relativeAge(of: snapshot.storedAt)). \(message)"
        case .loaded where snapshot.source == .cache:
            if snapshot.isStale(after: model.staleAfter) {
                return "Saved \(relativeAge(of: snapshot.storedAt)); pull to check for updates."
            }
            return "Showing a protected copy saved \(relativeAge(of: snapshot.storedAt))."
        default:
            return nil
        }
    }

    private var statusIsWarning: Bool {
        switch model.state {
        case .failed: true
        case .loaded: model.state.snapshot?.isStale(after: model.staleAfter) == true
        default: false
        }
    }

    private func relativeAge(of date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }
}
