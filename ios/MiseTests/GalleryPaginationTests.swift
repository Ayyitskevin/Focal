import XCTest
@testable import Mise

final class GalleryPaginationTests: XCTestCase {
    @MainActor
    func testAppendsAValidLaterPageWithoutPersistingIt() async {
        let feed = GalleryPageFeed(pages: [
            "page-2": Self.detail(assetIDs: [2, 2, 3], cursor: nil, hasMore: false)
        ])
        let pager = GalleryAssetPager(
            detail: Self.detail(assetIDs: [1], cursor: "page-2", hasMore: true),
            loadPage: { cursor in try await feed.page(cursor) }
        )

        await pager.loadNextPage()

        XCTAssertEqual(pager.assets.map(\.id), [1, 2, 3])
        XCTAssertFalse(pager.hasMore)
        XCTAssertNil(pager.nextCursor)
        XCTAssertNil(pager.notice)
        let requestedCursors = await feed.requestedCursors()
        XCTAssertEqual(requestedCursors, ["page-2"])
    }

    @MainActor
    func testRejectsDuplicateOnlyOrCursorCyclingPages() async {
        let feed = GalleryPageFeed(pages: [
            "page-2": Self.detail(assetIDs: [1], cursor: "page-2", hasMore: true)
        ])
        let pager = GalleryAssetPager(
            detail: Self.detail(assetIDs: [1], cursor: "page-2", hasMore: true),
            loadPage: { cursor in try await feed.page(cursor) }
        )

        await pager.loadNextPage()

        XCTAssertEqual(pager.assets.map(\.id), [1])
        XCTAssertTrue(pager.hasMore)
        XCTAssertEqual(pager.nextCursor, "page-2")
        XCTAssertNotNil(pager.notice)
    }

    @MainActor
    func testRejectsAnotherGalleryOrContentRevision() async {
        let feed = GalleryPageFeed(pages: [
            "wrong-gallery": Self.detail(
                galleryID: 18, assetIDs: [2], cursor: nil, hasMore: false
            ),
            "wrong-revision": Self.detail(
                assetIDs: [2], cursor: nil, hasMore: false, contentRevision: 43
            ),
        ])
        let wrongGallery = GalleryAssetPager(
            detail: Self.detail(assetIDs: [1], cursor: "wrong-gallery", hasMore: true),
            loadPage: { cursor in try await feed.page(cursor) }
        )
        let wrongRevision = GalleryAssetPager(
            detail: Self.detail(assetIDs: [1], cursor: "wrong-revision", hasMore: true),
            loadPage: { cursor in try await feed.page(cursor) }
        )

        await wrongGallery.loadNextPage()
        await wrongRevision.loadNextPage()

        XCTAssertEqual(wrongGallery.assets.map(\.id), [1])
        XCTAssertEqual(wrongRevision.assets.map(\.id), [1])
        XCTAssertNotNil(wrongGallery.notice)
        XCTAssertNotNil(wrongRevision.notice)
        XCTAssertTrue(wrongGallery.requiresRefresh)
        XCTAssertTrue(wrongRevision.requiresRefresh)
    }

    @MainActor
    func testTransientFailureRemainsRetryable() async {
        let pager = GalleryAssetPager(
            detail: Self.detail(assetIDs: [1], cursor: "page-2", hasMore: true),
            loadPage: { _ in throw APIError.unexpectedResponse }
        )

        await pager.loadNextPage()

        XCTAssertFalse(pager.requiresRefresh)
        XCTAssertNotNil(pager.notice)
    }

    @MainActor
    func testOldFailureCannotOverwriteARefreshedPager() async {
        let gate = FailingGalleryPageGate()
        let pager = GalleryAssetPager(
            detail: Self.detail(assetIDs: [1], cursor: "page-2", hasMore: true),
            loadPage: { _ in try await gate.page() }
        )

        let request = Task { await pager.loadNextPage() }
        await gate.waitUntilRequested()
        pager.reset(
            detail: Self.detail(
                assetIDs: [9],
                cursor: nil,
                hasMore: false,
                contentRevision: 43
            )
        )
        await gate.release()
        await request.value

        XCTAssertEqual(pager.assets.map(\.id), [9])
        XCTAssertNil(pager.notice)
        XCTAssertFalse(pager.isLoading)
    }

    private static func detail(
        galleryID: Int64 = 17,
        assetIDs: [Int64],
        cursor: String?,
        hasMore: Bool,
        contentRevision: Int64 = 42
    ) -> GalleryDetail {
        GalleryDetail(
            summary: GallerySummary(
                id: galleryID,
                title: "Gallery",
                slug: "gallery",
                clientID: nil,
                projectID: nil,
                clientName: nil,
                type: .gallery,
                published: true,
                requiresPIN: false,
                contentRevision: contentRevision,
                coverAssetID: nil,
                expiresOn: nil,
                assetCount: 10_001,
                favoriteCount: 0,
                downloadCount: 0,
                deliveryState: .proofing,
                createdAt: Date(timeIntervalSince1970: 1_700_000_000)
            ),
            sections: [],
            assets: assetIDs.map { Self.asset(id: $0) },
            assetsNextCursor: cursor,
            assetsHasMore: hasMore,
            heroAssetIDs: [],
            vision: nil
        )
    }

    private static func asset(id: Int64) -> GalleryAsset {
        GalleryAsset(
            id: id,
            galleryID: 17,
            sectionID: nil,
            kind: .photo,
            status: .ready,
            filename: "frame-\(id).jpg",
            width: 100,
            height: 100,
            durationSeconds: nil,
            byteCount: nil,
            position: Int(id),
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            isFavorite: false,
            favoriteCount: 0,
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
}

private actor GalleryPageFeed {
    private let pages: [String: GalleryDetail]
    private var cursors: [String] = []

    init(pages: [String: GalleryDetail]) {
        self.pages = pages
    }

    func page(_ cursor: String) throws -> GalleryDetail {
        cursors.append(cursor)
        guard let page = pages[cursor] else {
            throw APIError.unexpectedResponse
        }
        return page
    }

    func requestedCursors() -> [String] {
        cursors
    }
}

private actor FailingGalleryPageGate {
    private var requested = false
    private var released = false

    func page() async throws -> GalleryDetail {
        requested = true
        while !released {
            await Task.yield()
        }
        throw APIError.unexpectedResponse
    }

    func waitUntilRequested() async {
        while !requested {
            await Task.yield()
        }
    }

    func release() {
        released = true
    }
}
