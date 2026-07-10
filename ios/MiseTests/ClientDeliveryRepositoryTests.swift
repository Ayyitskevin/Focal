import XCTest
@testable import Mise

final class ClientDeliveryRepositoryTests: XCTestCase {
    func testFavoriteUsesCapabilityRouteAndPersistsOnlyConfirmedState() async throws {
        let root = Self.temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(
            cacheNamespace: "tenant\0capability\0gallery_guest:17",
            rootDirectory: root
        )
        try await cache.write(Self.gallery, key: "client.gallery.v1", etag: #""gallery-1""#)
        let client = RecordingDeliveryClient(replies: [
            .value(Data(#"{"asset_id":11,"selected":true,"section_selected_count":1,"section_proof_target":3}"#.utf8)),
        ])
        let repository = ClientDeliveryRepository(client: client, cache: cache)

        let update = try await repository.setFavorite(assetID: 11, selected: true)

        XCTAssertTrue(update.state.selected)
        XCTAssertTrue(update.gallery?.assets.first?.isFavorite == true)
        XCTAssertEqual(update.gallery?.sections.first?.selectedCount, 1)
        let requests = await client.requests()
        XCTAssertEqual(requests.first?.method, .put)
        XCTAssertEqual(requests.first?.path, "/api/v1/client/gallery/assets/11/favorite")
        let persisted = try await cache.read("client.gallery.v1", as: GalleryDetail.self)
        XCTAssertTrue(persisted?.value.assets.first?.isFavorite == true)
        XCTAssertNil(persisted?.etag, "Mutation must invalidate the old gallery ETag.")
    }

    func testCommentPostUsesStrictJSONContractAndUpdatesThreadCache() async throws {
        let root = Self.temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(
            cacheNamespace: "tenant\0capability\0gallery_guest:17",
            rootDirectory: root
        )
        let response = Data(
            """
            {
              "id": 91,
              "asset_id": 11,
              "parent_id": null,
              "timecode_seconds": 12.5,
              "body": "Tighten this cut",
              "author_role": "client",
              "status": "open",
              "created_at": "2026-07-10T14:30:00Z"
            }
            """.utf8
        )
        let client = RecordingDeliveryClient(replies: [.value(response)])
        let repository = ClientDeliveryRepository(client: client, cache: cache)

        let comment = try await repository.addComment(
            assetID: 11,
            body: "  Tighten this cut  ",
            timecodeSeconds: 12.5,
            parentID: nil
        )

        XCTAssertEqual(comment.assetID, 11)
        let requests = await client.requests()
        let request = try XCTUnwrap(requests.first)
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.path, "/api/v1/client/gallery/assets/11/comments")
        let body = try XCTUnwrap(request.body)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["body"] as? String, "Tighten this cut")
        XCTAssertEqual(json["timecode_seconds"] as? Double, 12.5)
        XCTAssertFalse(json.keys.contains("parent_id"), "Nil optionals should not fabricate a parent.")
        let cached = try await cache.read(
            "client.gallery.comments.11.v1",
            as: [GalleryComment].self
        )
        XCTAssertEqual(cached?.value.map(\.id), [91])
    }

    func testConditionalGalleryTouchUsesCapabilityETag() async throws {
        let root = Self.temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(
            cacheNamespace: "tenant\0capability\0gallery_guest:17",
            rootDirectory: root
        )
        let old = Date(timeIntervalSince1970: 1_700_000_000)
        try await cache.write(
            Self.gallery,
            key: "client.gallery.v1",
            etag: #""gallery-1""#,
            storedAt: old
        )
        let client = RecordingDeliveryClient(replies: [
            .failure(.notModified(etag: #""gallery-1""#)),
        ])
        let repository = ClientDeliveryRepository(client: client, cache: cache)

        let result = try await repository.refreshGallery()

        XCTAssertEqual(result.source, .revalidated)
        XCTAssertGreaterThan(result.storedAt, old)
        let requests = await client.requests()
        XCTAssertEqual(requests.first?.etag, #""gallery-1""#)
    }

    func testTerminalAuthenticationFailurePurgesCapabilityCache() async throws {
        let root = Self.temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let cache = TenantJSONCache(
            cacheNamespace: "tenant\0capability\0portal_guest:3",
            rootDirectory: root
        )
        try await cache.write([1, 2, 3], key: "private", etag: nil)
        let ended = LockedBox(0)
        let repository = ClientDeliveryRepository(
            client: RecordingDeliveryClient(replies: [.failure(.unauthenticated(nil))]),
            cache: cache,
            onSessionEnded: { ended.withValue { $0 += 1 } }
        )

        do {
            _ = try await repository.refreshPortal()
            XCTFail("Expected expired capability session.")
        } catch APIError.unauthenticated {
            // Expected.
        }

        let cached = try await cache.read("private", as: [Int].self)
        XCTAssertNil(cached)
        XCTAssertEqual(ended.withValue { $0 }, 1)
    }

    func testCapabilityNamespacesCannotReadOrPurgeEachOther() async throws {
        let root = Self.temporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let gallery = TenantJSONCache(
            cacheNamespace: "tenant_42\0capability\0gallery_guest:7",
            rootDirectory: root
        )
        let document = TenantJSONCache(
            cacheNamespace: "tenant_42\0capability\0document_guest:invoice:7",
            rootDirectory: root
        )
        try await gallery.write("gallery", key: "summary", etag: nil)
        try await document.write("invoice", key: "summary", etag: nil)

        try await gallery.removeAll()

        let galleryValue = try await gallery.read("summary", as: String.self)
        let documentValue = try await document.read("summary", as: String.self)
        XCTAssertNil(galleryValue)
        XCTAssertEqual(documentValue?.value, "invoice")
    }

    func testFavoriteMutationCanRollBackWithoutChangingOtherManifestFields() {
        let selected = GalleryGuestMutation.settingFavorite(true, assetID: 11, in: Self.gallery)
        let rolledBack = GalleryGuestMutation.settingFavorite(false, assetID: 11, in: selected)

        XCTAssertEqual(selected.summary.favoriteCount, 1)
        XCTAssertEqual(selected.sections.first?.selectedCount, 1)
        XCTAssertEqual(rolledBack, Self.gallery)
    }

    private static func temporaryRoot() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    private static let gallery = GalleryDetail(
        summary: GallerySummary(
            id: 7,
            title: "Launch",
            slug: "launch",
            clientID: nil,
            projectID: nil,
            clientName: nil,
            type: .gallery,
            published: true,
            requiresPIN: true,
            contentRevision: 2,
            coverAssetID: 11,
            expiresOn: nil,
            assetCount: 1,
            favoriteCount: 0,
            downloadCount: 0,
            deliveryState: .proofing,
            createdAt: Date(timeIntervalSince1970: 1_700_000_000)
        ),
        sections: [
            GallerySection(
                id: 5,
                galleryID: 7,
                name: "Selects",
                caption: nil,
                position: 0,
                proofTarget: 3,
                selectedCount: 0
            ),
        ],
        assets: [
            GalleryAsset(
                id: 11,
                galleryID: 7,
                sectionID: 5,
                kind: .photo,
                status: .ready,
                filename: "frame.jpg",
                width: 2400,
                height: 1600,
                durationSeconds: nil,
                byteCount: 2_000_000,
                position: 0,
                createdAt: Date(timeIntervalSince1970: 1_700_000_000),
                isFavorite: false,
                favoriteCount: 0,
                links: MediaLinks(
                    thumbnailURL: URL(string: "/api/v1/client/gallery/assets/11/thumbnail"),
                    previewURL: URL(string: "/api/v1/client/gallery/assets/11/preview"),
                    posterURL: nil,
                    downloadURL: URL(string: "/api/v1/client/gallery/assets/11/download")
                ),
                altText: "Portrait",
                keywords: [],
                keeperScore: nil,
                heroPotential: nil,
                cullState: nil
            ),
        ],
        heroAssetIDs: [],
        vision: nil
    )
}

private actor RecordingDeliveryClient: APIClientProtocol {
    struct Request: Sendable {
        let method: HTTPMethod
        let path: String
        let body: Data?
        let etag: String?
    }

    enum Reply: Sendable {
        case value(Data)
        case failure(APIError)
    }

    private var replies: [Reply]
    private var recorded: [Request] = []

    init(replies: [Reply]) { self.replies = replies }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        recorded.append(Request(
            method: endpoint.method,
            path: endpoint.path,
            body: endpoint.body,
            etag: endpoint.etag
        ))
        switch replies.removeFirst() {
        case let .value(data):
            return APIResponse(
                value: try MiseJSON.decoder().decode(Response.self, from: data),
                metadata: APIResponseMetadata(etag: nil, lastModified: nil, receivedAt: Date())
            )
        case let .failure(error):
            throw error
        }
    }

    func requests() -> [Request] { recorded }
}
