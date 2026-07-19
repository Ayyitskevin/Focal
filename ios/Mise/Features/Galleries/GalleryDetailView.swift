import SwiftUI

struct GalleryDetailView: View {
    let gallery: GallerySummary
    let mediaLoader: AuthenticatedMediaLoader
    private let repository: OwnerRepository
    @State private var model: ResourceModel<GalleryDetail>
    @State private var favorites: GalleryFavorites

    init(
        repository: OwnerRepository,
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
        // Owners review favorites; selection itself belongs to the client's
        // gallery session (there is no owner visitor identity to favorite as).
        _favorites = State(initialValue: GalleryFavorites(
            galleryID: gallery.id,
            canFavorite: false
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
                    "No media yet",
                    systemImage: "photo",
                    description: Text("This gallery manifest is currently empty.")
                )
            }
        )
        .navigationTitle(gallery.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}
