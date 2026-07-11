import SwiftUI

struct GalleryDetailView: View {
    private let galleryID: Int64
    private let fallbackTitle: String
    private let initialAssetID: Int64?
    @State private var model: OwnerResourceModel<GalleryDetail>
    @State private var selectedAsset: GalleryAsset?

    init(repository: OwnerRepository, gallery: GallerySummary) {
        self.init(
            repository: repository,
            galleryID: gallery.id,
            title: gallery.title,
            initialAssetID: nil
        )
    }

    init(
        repository: OwnerRepository,
        galleryID: Int64,
        title: String = "Gallery",
        initialAssetID: Int64? = nil
    ) {
        self.galleryID = galleryID
        fallbackTitle = title
        self.initialAssetID = initialAssetID
        _model = State(initialValue: OwnerResourceModel(
            staleAfter: 60 * 60,
            cached: { try await repository.cachedGallery(id: galleryID) },
            remote: { try await repository.refreshGallery(id: galleryID) }
        ))
    }

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.assets.isEmpty },
            content: manifest,
            empty: {
                ContentUnavailableView(
                    "No media yet",
                    systemImage: "photo",
                    description: Text("This gallery manifest is currently empty.")
                )
            }
        )
        .navigationTitle(model.state.snapshot?.value.summary.title ?? fallbackTitle)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selectedAsset) { asset in
            AssetPreview(asset: asset)
        }
    }

    private func manifest(_ detail: GalleryDetail) -> some View {
        ScrollView {
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 130), spacing: 3)],
                spacing: 3
            ) {
                ForEach(detail.assets) { asset in
                    Button { selectedAsset = asset } label: {
                        AssetThumbnail(asset: asset)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(asset.altText ?? asset.filename)
                    .accessibilityHint("Opens preview")
                }
            }
            .padding(3)
        }
        .refreshable { await model.refresh() }
        .onAppear {
            selectInitialAsset(from: detail)
        }
        .onChange(of: detail.assets.map(\.id)) { _, _ in
            selectInitialAsset(from: detail)
        }
    }

    private func selectInitialAsset(from detail: GalleryDetail) {
        guard selectedAsset == nil, let initialAssetID,
              let asset = detail.assets.first(where: {
                  $0.id == initialAssetID && $0.galleryID == galleryID
              })
        else {
            return
        }
        selectedAsset = asset
    }
}

private struct AssetThumbnail: View {
    let asset: GalleryAsset

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            Group {
                if let url = asset.links.thumbnailURL ?? asset.links.previewURL {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case let .success(image):
                            image.resizable().scaledToFill()
                        case .failure:
                            assetPlaceholder
                        default:
                            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                    }
                } else {
                    assetPlaceholder
                }
            }
            .frame(minHeight: 130)
            .aspectRatio(1, contentMode: .fill)
            .clipped()

            if asset.kind == .video {
                Image(systemName: "play.circle.fill")
                    .font(.title2)
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, .black.opacity(0.6))
                    .padding(7)
                    .accessibilityHidden(true)
            }
        }
        .contentShape(Rectangle())
    }

    private var assetPlaceholder: some View {
        VStack(spacing: 6) {
            Image(systemName: asset.kind == .video ? "video" : "photo")
            Text(asset.filename).font(.caption2).lineLimit(2)
        }
        .foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.secondary.opacity(0.12))
    }
}

private struct AssetPreview: View {
    let asset: GalleryAsset
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                if let url = asset.links.previewURL ?? asset.links.posterURL {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case let .success(image): image.resizable().scaledToFit()
                        case .failure: unavailable
                        default: ProgressView().tint(.white)
                        }
                    }
                } else {
                    unavailable
                }
            }
            .navigationTitle(asset.filename)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private var unavailable: some View {
        ContentUnavailableView(
            "Preview unavailable",
            systemImage: asset.kind == .video ? "video.slash" : "photo.badge.exclamationmark",
            description: Text(asset.filename)
        )
        .foregroundStyle(.white)
    }
}
