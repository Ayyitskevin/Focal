import XCTest
@testable import Mise

final class GalleryMediaPresentationTests: XCTestCase {
    private func asset(
        kind: MediaKind,
        preview: String?,
        poster: String?,
        thumbnail: String?
    ) -> GalleryAsset {
        GalleryAsset(
            id: 1,
            galleryID: 17,
            sectionID: nil,
            kind: kind,
            status: .ready,
            filename: kind == .video ? "clip.mp4" : "still.jpg",
            width: 1920,
            height: 1080,
            durationSeconds: kind == .video ? 12 : nil,
            byteCount: 1000,
            position: 1,
            createdAt: Date(timeIntervalSince1970: 0),
            isFavorite: false,
            favoriteCount: 0,
            links: MediaLinks(
                thumbnailURL: thumbnail.flatMap(URL.init(string:)),
                previewURL: preview.flatMap(URL.init(string:)),
                posterURL: poster.flatMap(URL.init(string:)),
                downloadURL: nil
            ),
            altText: nil,
            keywords: [],
            keeperScore: nil,
            heroPotential: nil,
            cullState: nil
        )
    }

    func testVideoStillPrefersPosterNotPreviewMP4() {
        let video = asset(
            kind: .video,
            preview: "https://studio.example.com/api/v1/media/galleries/17/assets/9/preview",
            poster: "https://studio.example.com/api/v1/media/galleries/17/assets/9/poster",
            thumbnail: "https://studio.example.com/api/v1/media/galleries/17/assets/9/thumbnail"
        )
        let still = GalleryMediaPresentation.stillURL(for: video)
        let playback = GalleryMediaPresentation.playbackURL(for: video)

        XCTAssertEqual(still?.lastPathComponent, "poster")
        XCTAssertEqual(playback?.lastPathComponent, "preview")
        XCTAssertNotEqual(still, playback)
    }

    func testVideoStillFallsBackToThumbnailWhenPosterMissing() {
        let video = asset(
            kind: .video,
            preview: "https://studio.example.com/api/v1/media/galleries/17/assets/9/preview",
            poster: nil,
            thumbnail: "https://studio.example.com/api/v1/media/galleries/17/assets/9/thumbnail"
        )
        XCTAssertEqual(
            GalleryMediaPresentation.stillURL(for: video)?.lastPathComponent,
            "thumbnail"
        )
        // Still must never be the MP4 preview path.
        XCTAssertNotEqual(
            GalleryMediaPresentation.stillURL(for: video)?.lastPathComponent,
            "preview"
        )
    }

    func testPhotoStillUsesPreviewAndHasNoPlayback() {
        let photo = asset(
            kind: .photo,
            preview: "https://studio.example.com/api/v1/media/galleries/17/assets/3/preview",
            poster: nil,
            thumbnail: "https://studio.example.com/api/v1/media/galleries/17/assets/3/thumbnail"
        )
        XCTAssertEqual(
            GalleryMediaPresentation.stillURL(for: photo)?.lastPathComponent,
            "preview"
        )
        XCTAssertNil(GalleryMediaPresentation.playbackURL(for: photo))
    }
}
