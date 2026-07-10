import XCTest
@testable import Mise

final class ModelDecodingTests: XCTestCase {
    func testAuthSessionDecodesSnakeCaseAndInitialisms() throws {
        let data = Data(
            """
            {
              "access_token": "access",
              "refresh_token": "refresh",
              "token_type": "Bearer",
              "access_token_expires_at": "2026-07-09T22:45:00Z",
              "refresh_token_expires_at": "2026-08-08T22:30:00.123Z",
              "workspace": {
                "cache_namespace": "tenant_42",
                "slug": "north-star",
                "display_name": "North Star",
                "api_base_url": "https://north-star.example.com",
                "brand_accent_hex": "#2F5C45",
                "time_zone": "America/New_York",
                "currency_code": "USD"
              },
              "principal": {
                "id": "studio_owner",
                "kind": "studio_owner",
                "display_name": "North Star",
                "email": "owner@example.com",
                "scopes": ["studio:read"]
              }
            }
            """.utf8
        )

        let session = try MiseJSON.decoder().decode(AuthSession.self, from: data)

        XCTAssertEqual(session.workspace.cacheNamespace, "tenant_42")
        XCTAssertEqual(
            session.workspace.apiBaseURL,
            URL(string: "https://north-star.example.com")
        )
        XCTAssertEqual(session.principal.kind, .studioOwner)
        XCTAssertTrue(session.principal.allows("studio:read"))
    }

    func testLocalDateUsesScalarWireFormat() throws {
        let decoded = try MiseJSON.decoder().decode(LocalDate.self, from: Data(#""2026-07-09""#.utf8))
        XCTAssertEqual(decoded.rawValue, "2026-07-09")

        let encoded = try MiseJSON.encoder().encode(decoded)
        XCTAssertEqual(String(decoding: encoded, as: UTF8.self), #""2026-07-09""#)
    }

    func testUnknownStatusSurvivesDecoding() throws {
        let data = Data(#""awaiting_retouch""#.utf8)
        let status = try MiseJSON.decoder().decode(ProjectStatus.self, from: data)
        XCTAssertEqual(status.rawValue, "awaiting_retouch")
    }

    func testPluralIDInitialismDecodes() throws {
        let data = Data(
            """
            {
              "status": "done",
              "run_id": "run_1",
              "job_id": "job_1",
              "last_run_at": null,
              "analyzed_asset_count": 12,
              "hero_asset_ids": [4, 8],
              "error": null
            }
            """.utf8
        )

        let result = try MiseJSON.decoder().decode(GalleryVisionSummary.self, from: data)
        XCTAssertEqual(result.heroAssetIDs, [4, 8])
    }

    func testRequestAcronymsEncodeToDocumentedKeys() throws {
        let request = ContractSignRequest(
            signerName: "Alex Rivera",
            agreed: true,
            documentETag: #""contract-7""#
        )
        let data = try MiseJSON.encoder().encode(request)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )

        XCTAssertEqual(object["document_etag"] as? String, #""contract-7""#)
        XCTAssertEqual(object["signer_name"] as? String, "Alex Rivera")
        XCTAssertNil(object["document_e_tag"])
    }

    func testFastAPIValidationBecomesFieldViolations() throws {
        let data = Data(
            """
            {
              "detail": [
                {
                  "loc": ["body", "line_items", 0, "quantity"],
                  "msg": "Input should be greater than 0",
                  "type": "greater_than"
                }
              ]
            }
            """.utf8
        )

        let problem = try MiseJSON.decoder().decode(APIProblem.self, from: data)

        XCTAssertEqual(
            problem.errors.first?.path,
            ["body", "line_items", "0", "quantity"]
        )
        XCTAssertEqual(problem.errors.first?.code, "greater_than")
    }
}
