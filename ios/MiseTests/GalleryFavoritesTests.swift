import XCTest
@testable import Mise

final class GalleryFavoritesTests: XCTestCase {
    private static func asset(id: Int64, sectionID: Int64? = 8, isFavorite: Bool) -> GalleryAsset {
        GalleryAsset(
            id: id,
            galleryID: 17,
            sectionID: sectionID,
            kind: .photo,
            status: .ready,
            filename: "frame-\(id).jpg",
            width: 6000,
            height: 4000,
            durationSeconds: nil,
            byteCount: nil,
            position: Int(id),
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            isFavorite: isFavorite,
            favoriteCount: isFavorite ? 1 : 0,
            links: MediaLinks(
                thumbnailURL: nil, previewURL: nil, posterURL: nil, downloadURL: nil
            ),
            altText: nil,
            keywords: [],
            keeperScore: nil,
            heroPotential: nil,
            cullState: nil
        )
    }

    @MainActor
    func testToggleIsOptimisticAndConfirmsServerState() async {
        let favorites = GalleryFavorites(
            galleryID: 17,
            canFavorite: true,
            toggle: { assetID, selected in
                FavoriteState(
                    assetID: assetID,
                    selected: selected,
                    sectionSelectedCount: 3,
                    sectionProofTarget: 20
                )
            }
        )
        let asset = Self.asset(id: 201, isFavorite: false)

        XCTAssertFalse(favorites.isFavorite(asset))
        await favorites.toggleFavorite(asset)

        XCTAssertTrue(favorites.isFavorite(asset))
        XCTAssertNil(favorites.notice)
        let section = GallerySection(
            id: 8, galleryID: 17, name: "Ceremony", caption: nil,
            position: 0, proofTarget: 20, selectedCount: 2
        )
        XCTAssertEqual(favorites.selectedCount(for: section), 3)
    }

    @MainActor
    func testFailedToggleRevertsAndSurfacesNotice() async {
        let favorites = GalleryFavorites(
            galleryID: 17,
            canFavorite: true,
            toggle: { _, _ in
                throw APIError.conflict(
                    APIProblem(status: 409, detail: "This section already has its selections.")
                )
            }
        )
        let asset = Self.asset(id: 202, isFavorite: false)

        await favorites.toggleFavorite(asset)

        XCTAssertFalse(favorites.isFavorite(asset))
        XCTAssertEqual(favorites.notice, "This section already has its selections.")
    }

    @MainActor
    func testCannotFavoriteWithoutCapabilityOrHandler() async {
        let withoutHandler = GalleryFavorites(galleryID: 17, canFavorite: true)
        XCTAssertFalse(withoutHandler.canFavorite)

        let withoutScope = GalleryFavorites(
            galleryID: 17,
            canFavorite: false,
            toggle: { assetID, selected in
                FavoriteState(
                    assetID: assetID,
                    selected: selected,
                    sectionSelectedCount: nil,
                    sectionProofTarget: nil
                )
            }
        )
        let asset = Self.asset(id: 203, isFavorite: false)
        await withoutScope.toggleFavorite(asset)
        XCTAssertFalse(withoutScope.isFavorite(asset))
    }
}
