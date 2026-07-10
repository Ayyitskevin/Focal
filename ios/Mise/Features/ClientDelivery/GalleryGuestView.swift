import AVKit
import SwiftUI

private struct GalleryAssetSelection: Identifiable {
    let id: Int64
}

struct GalleryGuestView: View {
    let repository: ClientDeliveryRepository
    let media: any AuthenticatedMediaLoading
    @State private var model: GalleryGuestModel
    @State private var selection: GalleryAssetSelection?

    init(
        repository: ClientDeliveryRepository,
        media: any AuthenticatedMediaLoading
    ) {
        self.repository = repository
        self.media = media
        _model = State(initialValue: GalleryGuestModel(repository: repository))
    }

    var body: some View {
        ClientResourceView(
            model: model.resource,
            isEmpty: { $0.assets.isEmpty },
            content: gallery,
            empty: {
                ContentUnavailableView(
                    "No media yet",
                    systemImage: "photo.on.rectangle.angled",
                    description: Text("The studio hasn’t delivered media to this gallery yet.")
                )
            }
        )
        .navigationTitle(model.detail?.summary.title ?? "Gallery")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selection) { selection in
            GalleryLightbox(
                initialAssetID: selection.id,
                model: model,
                repository: repository,
                media: media
            )
        }
        .alert(
            "Favorite not saved",
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

    private func gallery(_ detail: GalleryDetail) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                galleryHeader(detail)
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 145), spacing: 3)],
                    spacing: 3
                ) {
                    ForEach(detail.assets) { asset in
                        galleryCell(asset, contentRevision: detail.summary.contentRevision)
                    }
                }
            }
            .padding(.top, 12)
        }
        .refreshable { await model.resource.refresh() }
    }

    private func galleryHeader(_ detail: GalleryDetail) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 16) {
                Label("\(detail.assets.count) items", systemImage: "photo.stack")
                Label("\(detail.summary.favoriteCount) favorites", systemImage: "heart.fill")
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)

            ForEach(detail.sections.filter { $0.proofTarget != nil }) { section in
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text(section.name).font(.caption.weight(.semibold))
                        Spacer()
                        Text("\(section.selectedCount) of \(section.proofTarget ?? 0) selected")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    ProgressView(
                        value: Double(section.selectedCount),
                        total: Double(max(1, section.proofTarget ?? 1))
                    )
                    .accessibilityLabel("\(section.name) favorite progress")
                }
            }
        }
        .padding(.horizontal)
    }

    private func galleryCell(_ asset: GalleryAsset, contentRevision: Int64) -> some View {
        Button {
            selection = GalleryAssetSelection(id: asset.id)
        } label: {
            ZStack(alignment: .topTrailing) {
                AuthenticatedMediaImage(
                    url: asset.gridMedia.url,
                    purpose: asset.gridMedia.purpose,
                    loader: media,
                    contentRevision: contentRevision,
                    accessibilityLabel: asset.altText ?? asset.filename
                )
                .frame(minHeight: 145)
                .aspectRatio(1, contentMode: .fill)
                .clipped()

                HStack(spacing: 5) {
                    if asset.kind == .video {
                        Image(systemName: "play.fill")
                    }
                    if asset.isFavorite {
                        Image(systemName: "heart.fill")
                    }
                }
                .font(.caption.weight(.bold))
                .foregroundStyle(.white)
                .padding(7)
                .background(.black.opacity(0.55), in: Capsule())
                .padding(7)
                .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(asset.altText ?? asset.filename)
        .accessibilityValue(asset.isFavorite ? "Favorite" : "Not favorite")
        .accessibilityHint("Opens full-screen preview")
    }
}

private struct GalleryLightbox: View {
    let model: GalleryGuestModel
    let repository: ClientDeliveryRepository
    let media: any AuthenticatedMediaLoading
    @Environment(\.dismiss) private var dismiss
    @State private var selectedAssetID: Int64
    @State private var showingComments = false
    @State private var download = ProtectedDownloadModel()

    init(
        initialAssetID: Int64,
        model: GalleryGuestModel,
        repository: ClientDeliveryRepository,
        media: any AuthenticatedMediaLoading
    ) {
        self.model = model
        self.repository = repository
        self.media = media
        _selectedAssetID = State(initialValue: initialAssetID)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                if let detail = model.detail {
                    TabView(selection: $selectedAssetID) {
                        ForEach(detail.assets) { asset in
                            GalleryAssetPage(
                                asset: asset,
                                media: media,
                                localVideoURL: localVideoURL(for: asset),
                                contentRevision: detail.summary.contentRevision
                            )
                            .tag(asset.id)
                        }
                    }
                    .tabViewStyle(.page(indexDisplayMode: .automatic))
                }
            }
            .navigationTitle(selectedAsset?.filename ?? "Preview")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(.black.opacity(0.85), for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    if let asset = selectedAsset {
                        Button {
                            Task { await model.toggleFavorite(assetID: asset.id) }
                        } label: {
                            Image(systemName: asset.isFavorite ? "heart.fill" : "heart")
                        }
                        .disabled(model.favoriteMutationAssetID != nil)
                        .accessibilityLabel(asset.isFavorite ? "Remove favorite" : "Mark favorite")

                        if asset.kind == .video {
                            Button { showingComments = true } label: {
                                Image(systemName: "text.bubble")
                            }
                            .accessibilityLabel("Review notes")
                        }

                        downloadControl(asset)
                    }
                }
            }
            .sheet(isPresented: $showingComments) {
                if let asset = selectedAsset, asset.kind == .video {
                    GalleryCommentThreadView(asset: asset, repository: repository)
                }
            }
            .alert(
                "Download failed",
                isPresented: Binding(
                    get: {
                        if case .failed = download.state { return true }
                        return false
                    },
                    set: { if !$0 { download.reset(media: media) } }
                )
            ) {
                Button("OK", role: .cancel) { download.reset(media: media) }
            } message: {
                if case let .failed(message) = download.state {
                    Text(message)
                }
            }
        }
        .onChange(of: selectedAssetID) { _, _ in download.reset(media: media) }
        .onDisappear { download.reset(media: media) }
    }

    @ViewBuilder
    private func downloadControl(_ asset: GalleryAsset) -> some View {
        switch download.state {
        case .idle, .failed:
            Button {
                guard let url = asset.links.downloadURL else { return }
                download.start(
                    url: url,
                    filename: asset.filename,
                    expectedByteCount: asset.byteCount,
                    media: media
                )
            } label: {
                Image(systemName: "arrow.down.circle")
            }
            .disabled(asset.links.downloadURL == nil)
            .accessibilityLabel(asset.kind == .video ? "Prepare video" : "Prepare original")
        case .downloading:
            ProgressView().tint(.white).accessibilityLabel("Downloading original")
        case let .ready(url):
            ShareLink(item: url) {
                Image(systemName: "square.and.arrow.up")
            }
            .accessibilityLabel("Share or save original")
        }
    }

    private var selectedAsset: GalleryAsset? {
        model.detail?.assets.first { $0.id == selectedAssetID }
    }

    private func localVideoURL(for asset: GalleryAsset) -> URL? {
        guard asset.id == selectedAssetID,
              asset.kind == .video,
              case let .ready(url) = download.state
        else {
            return nil
        }
        return url
    }
}

private struct GalleryAssetPage: View {
    let asset: GalleryAsset
    let media: any AuthenticatedMediaLoading
    let localVideoURL: URL?
    let contentRevision: Int64

    var body: some View {
        VStack(spacing: 18) {
            if asset.kind == .video, let localVideoURL {
                ProtectedVideoPlayer(url: localVideoURL)
            } else {
                AuthenticatedMediaImage(
                    url: asset.lightboxMedia.url,
                    purpose: asset.lightboxMedia.purpose,
                    loader: media,
                    contentMode: .fit,
                    contentRevision: contentRevision,
                    accessibilityLabel: asset.altText ?? asset.filename
                )
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            if asset.kind == .video {
                VStack(spacing: 6) {
                    Text(videoMetadata).font(.subheadline.monospacedDigit())
                    if localVideoURL == nil {
                        Text("Prepare the protected original to play, share, or save it.")
                            .font(.caption)
                    }
                }
                .foregroundStyle(.white.opacity(0.82))
                .multilineTextAlignment(.center)
                .padding(.horizontal)
                .padding(.bottom, 12)
            }
        }
    }

    private var videoMetadata: String {
        var values = ["Video"]
        if let duration = asset.durationSeconds {
            let total = max(0, Int(duration.rounded()))
            values.append(String(format: "%d:%02d", total / 60, total % 60))
        }
        if let count = asset.byteCount {
            values.append(ByteCountFormatter.string(fromByteCount: count, countStyle: .file))
        }
        return values.joined(separator: " • ")
    }
}

private struct ProtectedVideoPlayer: View {
    @State private var player: AVPlayer

    init(url: URL) {
        _player = State(initialValue: AVPlayer(url: url))
    }

    var body: some View {
        VideoPlayer(player: player)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .onDisappear { player.pause() }
            .accessibilityLabel("Protected video player")
    }
}

private struct GalleryMediaSource {
    let url: URL?
    let purpose: AuthenticatedMediaPurpose
}

private extension GalleryAsset {
    var gridMedia: GalleryMediaSource {
        if kind == .video, let url = links.posterURL {
            return GalleryMediaSource(url: url, purpose: .poster)
        }
        if let url = links.thumbnailURL {
            return GalleryMediaSource(url: url, purpose: .thumbnail)
        }
        if kind != .video, let url = links.previewURL {
            return GalleryMediaSource(url: url, purpose: .preview)
        }
        return GalleryMediaSource(url: nil, purpose: kind == .video ? .poster : .thumbnail)
    }

    var lightboxMedia: GalleryMediaSource {
        if kind == .video {
            if let url = links.posterURL {
                return GalleryMediaSource(url: url, purpose: .poster)
            }
            if let url = links.thumbnailURL {
                return GalleryMediaSource(url: url, purpose: .thumbnail)
            }
            return GalleryMediaSource(url: nil, purpose: .poster)
        }
        if let url = links.previewURL {
            return GalleryMediaSource(url: url, purpose: .preview)
        }
        if let url = links.thumbnailURL {
            return GalleryMediaSource(url: url, purpose: .thumbnail)
        }
        return GalleryMediaSource(url: nil, purpose: .preview)
    }
}
