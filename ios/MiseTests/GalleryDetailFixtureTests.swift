import XCTest
@testable import Mise

final class GalleryDetailFixtureTests: XCTestCase {
    func testCanonicalGalleryManifestDecodes() throws {
        let data = Data(
            """
            {
              "summary": {
                "id": 17,
                "title": "Amelia + Sam",
                "slug": "Q6Y",
                "client_id": 9,
                "project_id": 12,
                "client_name": "Amelia + Sam",
                "type": "gallery",
                "published": true,
                "requires_pin": true,
                "content_revision": 42,
                "cover_asset_id": 201,
                "expires_on": "2026-10-01",
                "asset_count": 84,
                "favorite_count": 7,
                "download_count": 2,
                "delivery_state": "proofing",
                "created_at": "2026-07-01T12:00:00Z"
              },
              "sections": [
                {
                  "id": 8,
                  "gallery_id": 17,
                  "name": "Ceremony",
                  "caption": null,
                  "position": 0,
                  "proof_target": 20,
                  "selected_count": 7
                }
              ],
              "assets": [
                {
                  "id": 201,
                  "gallery_id": 17,
                  "section_id": 8,
                  "kind": "photo",
                  "status": "ready",
                  "filename": "KLP_1024.jpg",
                  "width": 6000,
                  "height": 4000,
                  "duration_seconds": null,
                  "byte_count": 1842201,
                  "position": 1,
                  "created_at": "2026-07-01T12:01:00Z",
                  "is_favorite": true,
                  "favorite_count": 7,
                  "links": {
                    "thumbnail_url": "https://studio.example.com/api/v1/media/galleries/17/assets/201/thumbnail",
                    "preview_url": "https://studio.example.com/api/v1/media/galleries/17/assets/201/preview",
                    "poster_url": null,
                    "download_url": "https://studio.example.com/api/v1/media/galleries/17/assets/201/download"
                  },
                  "alt_text": "A couple during their ceremony",
                  "keywords": ["ceremony", "couple"],
                  "keeper_score": 0.98,
                  "hero_potential": 0.86,
                  "cull_state": "keep"
                }
              ],
              "hero_asset_ids": [201],
              "vision": null,
              "cull_enabled": true
            }
            """.utf8
        )

        let gallery = try MiseJSON.decoder().decode(GalleryDetail.self, from: data)

        XCTAssertEqual(gallery.id, 17)
        XCTAssertEqual(gallery.summary.deliveryState, .proofing)
        XCTAssertEqual(gallery.sections.first?.proofTarget, 20)
        XCTAssertEqual(gallery.assets.first?.links.thumbnailURL?.host, "studio.example.com")
        XCTAssertEqual(gallery.heroAssetIDs, [201])
        XCTAssertTrue(gallery.cullEnabled)

        var legacyObject = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertNotNil(legacyObject.removeValue(forKey: "cull_enabled"))
        let legacyData = try JSONSerialization.data(withJSONObject: legacyObject)
        let legacy = try MiseJSON.decoder().decode(
            GalleryDetail.self,
            from: legacyData
        )
        XCTAssertFalse(legacy.cullEnabled)
        let cachedRoundTrip = try MiseJSON.decoder().decode(
            GalleryDetail.self,
            from: MiseJSON.encoder().encode(legacy)
        )
        XCTAssertFalse(cachedRoundTrip.cullEnabled)
    }
}
