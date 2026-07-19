import XCTest
@testable import Mise

final class ClientRepositoryTests: XCTestCase {
    private func makeRepository(
        principalScopes: [String],
        client: StubAPIClient
    ) throws -> ClientRepository {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("client-repo-tests-\(UUID().uuidString)", isDirectory: true)
        let principal = Principal(
            id: "gallery_guest:17",
            kind: .galleryGuest,
            displayName: "Gallery access",
            email: nil,
            scopes: principalScopes
        )
        return ClientRepository(
            client: client,
            cache: TenantJSONCache(cacheNamespace: "workspace_test", rootDirectory: root),
            principal: principal
        )
    }

    func testCanFavoriteFollowsScopes() throws {
        let repository = try makeRepository(
            principalScopes: ["gallery:17:read", "gallery:17:favorite"],
            client: StubAPIClient()
        )
        XCTAssertTrue(repository.canFavorite(galleryID: 17))
        XCTAssertFalse(repository.canFavorite(galleryID: 99))
    }

    func testRefreshDocumentsCombinesThreeCollections() async throws {
        let stub = StubAPIClient()
        stub.pathResponses["/api/v1/projects/12/proposals"] = Data(
            """
            {"items": [\(Self.proposalJSON)], "next_cursor": null, "has_more": false}
            """.utf8
        )
        stub.pathResponses["/api/v1/projects/12/contracts"] = Data(
            #"{"items": [], "next_cursor": null, "has_more": false}"#.utf8
        )
        stub.pathResponses["/api/v1/projects/12/invoices"] = Data(
            #"{"items": [], "next_cursor": null, "has_more": false}"#.utf8
        )
        let repository = try makeRepository(
            principalScopes: ["workspace:12:read"],
            client: stub
        )

        let snapshot = try await repository.refreshDocuments(projectID: 12)

        XCTAssertEqual(snapshot.value.proposals.count, 1)
        XCTAssertEqual(snapshot.value.proposals.first?.title, "Wedding collection")
        XCTAssertTrue(snapshot.value.contracts.isEmpty)
        XCTAssertFalse(snapshot.value.isEmpty)

        // The combined result is now served from the cache.
        let cached = try await repository.cachedDocuments(projectID: 12)
        XCTAssertEqual(cached?.value.proposals.count, 1)
        XCTAssertEqual(cached?.source, .cache)
    }

    func testSetFavoriteDropsStaleGalleryManifest() async throws {
        let stub = StubAPIClient()
        stub.pathResponses["/api/v1/client/galleries/17"] = Data(Self.galleryDetailJSON.utf8)
        stub.pathResponses["/api/v1/galleries/17/assets/201/favorite"] = Data(
            """
            {"asset_id": 201, "selected": true,
             "section_selected_count": 1, "section_proof_target": 20}
            """.utf8
        )
        let repository = try makeRepository(
            principalScopes: ["gallery:17:read", "gallery:17:favorite"],
            client: stub
        )

        _ = try await repository.refreshGallery(id: 17)
        let beforeToggle = try await repository.cachedGallery(id: 17)
        XCTAssertNotNil(beforeToggle)

        let state = try await repository.setFavorite(galleryID: 17, assetID: 201, selected: true)

        XCTAssertTrue(state.selected)
        let afterToggle = try await repository.cachedGallery(id: 17)
        XCTAssertNil(afterToggle, "a toggled favorite must invalidate the cached manifest")
    }

    private static let proposalJSON = """
    {
      "id": 4,
      "project_id": 12,
      "title": "Wedding collection",
      "intro": null,
      "line_items": [],
      "total": {"minor_units": 420000, "currency_code": "USD"},
      "status": "sent",
      "can_accept": true,
      "can_decline": true,
      "sent_at": null,
      "viewed_at": null,
      "accepted_at": null,
      "created_at": "2026-07-01T08:00:00Z",
      "public_url": "https://studio.example.com/p/prop-slug"
    }
    """

    private static let galleryDetailJSON = """
    {
      "summary": {
        "id": 17,
        "title": "Amelia + Sam",
        "slug": "amelia-wedding",
        "client_id": 9,
        "project_id": 12,
        "client_name": "Amelia Chen",
        "type": "gallery",
        "published": true,
        "requires_pin": true,
        "content_revision": 4,
        "cover_asset_id": null,
        "expires_on": null,
        "asset_count": 1,
        "favorite_count": 0,
        "download_count": 0,
        "delivery_state": "proofing",
        "created_at": "2026-07-01T12:00:00Z"
      },
      "sections": [],
      "assets": [],
      "assets_next_cursor": null,
      "assets_has_more": false,
      "hero_asset_ids": [],
      "vision": null
    }
    """
}

/// Serves canned JSON by path; unknown paths fail the request.
private final class StubAPIClient: APIClientProtocol, @unchecked Sendable {
    var pathResponses: [String: Data] = [:]

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        try await sendWithMetadata(endpoint).value
    }

    func sendWithMetadata<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> APIResponse<Response> {
        guard let data = pathResponses[endpoint.path] else {
            throw APIError.notFound(nil)
        }
        let value = try MiseJSON.decoder().decode(Response.self, from: data)
        return APIResponse(
            value: value,
            metadata: APIResponseMetadata(etag: nil, lastModified: nil, receivedAt: Date())
        )
    }
}
