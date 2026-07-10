import Foundation
import Observation
import SwiftUI

@MainActor
@Observable
private final class GalleryCommentThreadModel {
    let resource: ClientResourceModel<[GalleryComment]>
    private(set) var isPosting = false
    var errorMessage: String?
    private let assetID: Int64
    private let repository: ClientDeliveryRepository

    init(assetID: Int64, repository: ClientDeliveryRepository) {
        self.assetID = assetID
        self.repository = repository
        resource = ClientResourceModel(
            staleAfter: 5 * 60,
            cached: { try await repository.cachedComments(assetID: assetID) },
            remote: { try await repository.refreshComments(assetID: assetID) }
        )
    }

    func post(body: String, timecodeSeconds: Double?, parentID: Int64?) async -> Bool {
        guard !isPosting else { return false }
        let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }

        isPosting = true
        errorMessage = nil
        defer { isPosting = false }
        do {
            let comment = try await repository.addComment(
                assetID: assetID,
                body: trimmed,
                timecodeSeconds: timecodeSeconds,
                parentID: parentID
            )
            var values = resource.state.snapshot?.value ?? []
            values.removeAll { $0.id == comment.id }
            values.append(comment)
            values.sort {
                ($0.timecodeSeconds, $0.createdAt, $0.id)
                    < ($1.timecodeSeconds, $1.createdAt, $1.id)
            }
            resource.replace(values)
            return true
        } catch is CancellationError {
            return false
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}

struct GalleryCommentThreadView: View {
    let asset: GalleryAsset
    @Environment(\.dismiss) private var dismiss
    @State private var model: GalleryCommentThreadModel
    @State private var bodyText = ""
    @State private var timecodeText = ""
    @State private var replyingTo: GalleryComment?
    @FocusState private var composerFocused: Bool

    init(asset: GalleryAsset, repository: ClientDeliveryRepository) {
        self.asset = asset
        _model = State(initialValue: GalleryCommentThreadModel(
            assetID: asset.id,
            repository: repository
        ))
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ClientResourceView(
                    model: model.resource,
                    isEmpty: { $0.isEmpty },
                    content: comments,
                    empty: {
                        ContentUnavailableView(
                            "No review notes",
                            systemImage: "text.bubble",
                            description: Text("Add a note at a moment in this video.")
                        )
                    }
                )
                composer
            }
            .navigationTitle("Review notes")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .alert(
                "Couldn’t add note",
                isPresented: Binding(
                    get: { model.errorMessage != nil },
                    set: { if !$0 { model.errorMessage = nil } }
                )
            ) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(model.errorMessage ?? "Try again.")
            }
        }
    }

    private func comments(_ values: [GalleryComment]) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                ForEach(values) { comment in
                    commentRow(comment, all: values)
                }
            }
            .padding()
        }
        .refreshable { await model.resource.refresh() }
    }

    private func commentRow(_ comment: GalleryComment, all: [GalleryComment]) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline) {
                Text(comment.authorRole == "admin" ? "Studio" : "Client")
                    .font(.subheadline.weight(.semibold))
                Text(formatTimecode(comment.timecodeSeconds))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                Spacer()
                Text(comment.createdAt, style: .relative)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(comment.body)
                .font(.body)
                .textSelection(.enabled)
            HStack {
                if comment.status == .resolved {
                    Label("Resolved", systemImage: "checkmark.circle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Reply") {
                    replyingTo = comment
                    composerFocused = true
                }
                .font(.caption.weight(.semibold))
            }
        }
        .padding(12)
        .background(Color.secondary.opacity(0.09), in: RoundedRectangle(cornerRadius: 12))
        .padding(.leading, CGFloat(min(depth(of: comment, in: all), 3)) * 18)
        .accessibilityElement(children: .contain)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let replyingTo {
                HStack {
                    Text("Replying at \(formatTimecode(replyingTo.timecodeSeconds))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Cancel") { self.replyingTo = nil }
                        .font(.caption)
                }
            }
            HStack(alignment: .bottom, spacing: 10) {
                if replyingTo == nil {
                    TextField("0:12", text: $timecodeText)
                        .keyboardType(.numbersAndPunctuation)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 72)
                        .accessibilityLabel("Video timecode")
                }
                TextField("Add a review note", text: $bodyText, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                    .focused($composerFocused)
                Button {
                    Task { await submit() }
                } label: {
                    if model.isPosting {
                        ProgressView()
                    } else {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title2)
                    }
                }
                .disabled(model.isPosting || bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("Send review note")
            }
        }
        .padding()
        .background(.bar)
    }

    private func submit() async {
        let parent = replyingTo
        let timecode = parent == nil ? parseTimecode(timecodeText) : nil
        if await model.post(
            body: bodyText,
            timecodeSeconds: timecode,
            parentID: parent?.id
        ) {
            bodyText = ""
            replyingTo = nil
        }
    }

    private func depth(of comment: GalleryComment, in comments: [GalleryComment]) -> Int {
        var byID: [Int64: GalleryComment] = [:]
        for value in comments { byID[value.id] = value }
        var parentID = comment.parentID
        var visited = Set<Int64>()
        var depth = 0
        while let id = parentID, visited.insert(id).inserted, depth < 8 {
            depth += 1
            parentID = byID[id]?.parentID
        }
        return depth
    }

    private func parseTimecode(_ value: String) -> Double? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return 0 }
        let pieces = trimmed.split(separator: ":")
        if pieces.count == 2,
           let minutes = Double(pieces[0]),
           let seconds = Double(pieces[1])
        {
            return max(0, minutes * 60 + seconds)
        }
        return Double(trimmed).map { max(0, $0) }
    }

    private func formatTimecode(_ seconds: Double) -> String {
        let total = max(0, Int(seconds.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
