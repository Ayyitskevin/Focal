import Foundation
import SwiftUI

private struct StudioManageBillingURLKey: EnvironmentKey {
    static let defaultValue: URL? = nil
}

extension EnvironmentValues {
    var studioManageBillingURL: URL? {
        get { self[StudioManageBillingURLKey.self] }
        set { self[StudioManageBillingURLKey.self] = newValue }
    }
}

struct ResourceView<Value: Codable & Sendable, Content: View, Empty: View>: View {
    @Environment(\.studioManageBillingURL) private var studioManageBillingURL

    let model: ResourceModel<Value>
    let isEmpty: (Value) -> Bool
    let content: (Value) -> Content
    let empty: () -> Empty

    var body: some View {
        Group {
            if model.requiresSubscriptionRecovery,
               let studioManageBillingURL
            {
                SubscriptionRequiredView(
                    manageBillingURL: studioManageBillingURL,
                    isRetrying: model.isRefreshing,
                    retry: { await model.refresh() }
                )
            } else if let snapshot = model.state.snapshot {
                VStack(spacing: 0) {
                    if let statusMessage {
                        Label(statusMessage, systemImage: statusIcon)
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
                case let .failed(_, failure):
                    ContentUnavailableView {
                        Label("Couldn’t load data", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(failure.message)
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
        case .loading where snapshot.source == .session:
            return "Updating this session’s confirmed changes…"
        case .loading:
            return "Updating data saved \(relativeAge(of: snapshot.storedAt))…"
        case let .failed(_, failure) where snapshot.source == .session:
            return "Offline — showing this session’s confirmed changes. \(failure.message)"
        case let .failed(_, failure):
            return "Offline — showing data saved \(relativeAge(of: snapshot.storedAt)). \(failure.message)"
        case .loaded where snapshot.source == .session:
            return "Showing this session’s confirmed changes. Pull to refresh."
        case .loaded where snapshot.source == .cache:
            if snapshot.isStale(after: model.staleAfter) {
                return "Saved \(relativeAge(of: snapshot.storedAt)); it may be out of date. Pull to refresh."
            }
            return "Showing data saved \(relativeAge(of: snapshot.storedAt))."
        default:
            return nil
        }
    }

    private var statusIcon: String {
        statusIsWarning ? "wifi.slash" : "arrow.triangle.2.circlepath"
    }

    private var statusIsWarning: Bool {
        switch model.state {
        case .failed: true
        case .loaded:
            model.state.snapshot?.isStale(after: model.staleAfter) == true
        default: false
        }
    }

    private func relativeAge(of date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }
}

private struct SubscriptionRequiredView: View {
    let manageBillingURL: URL
    let isRetrying: Bool
    let retry: @MainActor () async -> Void

    var body: some View {
        ContentUnavailableView {
            Label(
                "Your studio’s subscription needs attention",
                systemImage: "exclamationmark.triangle"
            )
        } description: {
            Text(
                "Update billing on the web to restore access. "
                    + "Your studio data is still there."
            )
        } actions: {
            Link(destination: manageBillingURL) {
                Label("Manage billing", systemImage: "arrow.up.right.square")
            }
            .buttonStyle(.borderedProminent)
            .accessibilityHint("Opens your studio’s billing page in the browser")

            Button {
                Task { await retry() }
            } label: {
                if isRetrying {
                    ProgressView()
                } else {
                    Text("Try again")
                }
            }
            .buttonStyle(.bordered)
            .disabled(isRetrying)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
