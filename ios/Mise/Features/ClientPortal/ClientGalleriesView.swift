import SwiftUI

/// Client Gallery root: a vertical list of gallery rows (cover thumb, title,
/// count, status pill), per the design handoff — not the owner's card grid.
struct ClientGalleriesView: View {
    let model: ResourceModel<[GallerySummary]>
    let repository: ClientRepository
    let mediaLoader: AuthenticatedMediaLoader

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: galleryList,
            empty: {
                ContentUnavailableView(
                    "No galleries yet",
                    systemImage: "photo.on.rectangle",
                    description: Text(
                        "Your photographs will appear here the moment the studio shares them."
                    )
                )
            }
        )
        .navigationTitle("Gallery")
    }

    private func galleryList(_ galleries: [GallerySummary]) -> some View {
        List(galleries) { gallery in
            NavigationLink {
                ClientGalleryDetailView(
                    repository: repository,
                    mediaLoader: mediaLoader,
                    gallery: gallery
                )
            } label: {
                row(gallery)
            }
        }
        .refreshable { await model.refresh() }
    }

    private func row(_ gallery: GallerySummary) -> some View {
        HStack(spacing: 14) {
            ZStack {
                MiseDesign.surfaceSunk
                Image(systemName: "photo.stack")
                    .foregroundStyle(.secondary)
            }
            .frame(width: 56, height: 56)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 4) {
                Text(gallery.title)
                    .miseDisplayFont(.headline)
                Text(
                    gallery.assetCount == 1
                        ? "1 photograph"
                        : "\(gallery.assetCount) photographs"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            Spacer(minLength: 8)

            StatusPill(
                label: gallery.deliveryState.clientDisplayName,
                tone: gallery.deliveryState.tone
            )
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

/// Client gallery detail: identical shared component to the owner side —
/// sectioned grid plus lightbox — with favoriting live when this session's
/// capability includes it.
struct ClientGalleryDetailView: View {
    let gallery: GallerySummary
    let mediaLoader: AuthenticatedMediaLoader
    private let repository: ClientRepository
    @State private var model: ResourceModel<GalleryDetail>
    @State private var favorites: GalleryFavorites

    init(
        repository: ClientRepository,
        mediaLoader: AuthenticatedMediaLoader,
        gallery: GallerySummary
    ) {
        self.gallery = gallery
        self.mediaLoader = mediaLoader
        self.repository = repository
        _model = State(initialValue: ResourceModel(
            staleAfter: 60 * 60,
            cached: { try await repository.cachedGallery(id: gallery.id) },
            remote: { try await repository.refreshGallery(id: gallery.id) }
        ))
        _favorites = State(initialValue: GalleryFavorites(
            galleryID: gallery.id,
            canFavorite: repository.canFavorite(galleryID: gallery.id),
            toggle: { assetID, selected in
                try await repository.setFavorite(
                    galleryID: gallery.id,
                    assetID: assetID,
                    selected: selected
                )
            }
        ))
    }

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.assets.isEmpty },
            content: { detail in
                GalleryManifestView(
                    detail: detail,
                    favorites: favorites,
                    mediaLoader: mediaLoader,
                    refresh: { await model.refresh() },
                    loadPage: { cursor in
                        try await repository.galleryPage(id: gallery.id, cursor: cursor)
                    }
                )
            },
            empty: {
                ContentUnavailableView(
                    "No photos uploaded yet.",
                    systemImage: "photo",
                    description: Text("Check back soon — the studio is working on it.")
                )
            }
        )
        .navigationTitle(gallery.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}
