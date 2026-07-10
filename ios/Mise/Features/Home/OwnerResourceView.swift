import Foundation
import SwiftUI

struct OwnerResourceView<Value: Codable & Sendable, Content: View, Empty: View>: View {
    let model: OwnerResourceModel<Value>
    let isEmpty: (Value) -> Bool
    let content: (Value) -> Content
    let empty: () -> Empty

    var body: some View {
        Group {
            if let snapshot = model.state.snapshot {
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
                case let .failed(_, message):
                    ContentUnavailableView {
                        Label("Couldn’t load data", systemImage: "wifi.exclamationmark")
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
            return "Updating data saved \(relativeAge(of: snapshot.storedAt))…"
        case let .failed(_, message):
            return "Offline — showing data saved \(relativeAge(of: snapshot.storedAt)). \(message)"
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
