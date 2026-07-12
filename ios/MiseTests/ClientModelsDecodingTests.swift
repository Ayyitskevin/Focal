import XCTest
@testable import Mise

final class ClientModelsDecodingTests: XCTestCase {
    func testClientHomeSummaryDecodesWorkspaceShape() throws {
        let data = Data(
            """
            {
              "principal_kind": "workspace",
              "studio_name": "North Star Photo",
              "client_display_name": "Amelia Chen",
              "project_id": 12,
              "project_title": "Chen Wedding",
              "gallery_id": 17,
              "gallery_count": 1,
              "next_steps": [
                {
                  "id": "proposal:4",
                  "kind": "proposal",
                  "title": "Review Wedding collection",
                  "detail": "Accept or decline your proposal.",
                  "document_variant": "proposal",
                  "document_id": 4,
                  "gallery_id": null,
                  "public_url": "https://studio.example.com/p/prop-slug"
                },
                {
                  "id": "gallery:17",
                  "kind": "gallery",
                  "title": "Review your wedding gallery",
                  "detail": "Amelia + Sam",
                  "document_variant": null,
                  "document_id": null,
                  "gallery_id": 17,
                  "public_url": null
                }
              ],
              "document": null
            }
            """.utf8
        )

        let summary = try MiseJSON.decoder().decode(ClientHomeSummary.self, from: data)

        XCTAssertEqual(summary.principalKind, .workspace)
        XCTAssertEqual(summary.projectID, 12)
        XCTAssertEqual(summary.galleryID, 17)
        XCTAssertEqual(summary.nextSteps.count, 2)
        XCTAssertEqual(summary.nextSteps.first?.kind, .proposal)
        XCTAssertEqual(summary.nextSteps.first?.documentID, 4)
        XCTAssertEqual(
            summary.nextSteps.first?.publicURL,
            URL(string: "https://studio.example.com/p/prop-slug")
        )
        XCTAssertEqual(summary.nextSteps.last?.kind, .gallery)
        XCTAssertNil(summary.document)
    }

    func testClientHomeSummaryDecodesDocumentGuestShape() throws {
        let data = Data(
            """
            {
              "principal_kind": "document",
              "studio_name": "North Star Photo",
              "client_display_name": null,
              "project_id": null,
              "project_title": null,
              "gallery_id": null,
              "gallery_count": 0,
              "next_steps": [],
              "document": {
                "variant": "invoice",
                "id": 5,
                "title": "Deposit invoice",
                "status": "sent",
                "total": {"minor_units": 150000, "currency_code": "USD"},
                "balance": {"minor_units": 100000, "currency_code": "USD"},
                "public_url": "https://studio.example.com/i/inv-slug"
              }
            }
            """.utf8
        )

        let summary = try MiseJSON.decoder().decode(ClientHomeSummary.self, from: data)

        XCTAssertEqual(summary.principalKind, .document)
        let document = try XCTUnwrap(summary.document)
        XCTAssertEqual(document.variant, "invoice")
        XCTAssertEqual(document.balance?.minorUnits, 100_000)
        XCTAssertEqual(document.publicURL.path, "/i/inv-slug")
    }

    func testProposalDecodesPublicURLAndCascadedFlags() throws {
        let data = Data(
            """
            {
              "id": 4,
              "project_id": 12,
              "title": "Wedding collection",
              "intro": null,
              "line_items": [
                {
                  "label": "Full day coverage",
                  "quantity": 1,
                  "unit_price": {"minor_units": 420000, "currency_code": "USD"}
                }
              ],
              "total": {"minor_units": 420000, "currency_code": "USD"},
              "status": "viewed",
              "can_accept": true,
              "can_decline": true,
              "sent_at": "2026-07-02T09:00:00Z",
              "viewed_at": "2026-07-03T10:00:00Z",
              "accepted_at": null,
              "created_at": "2026-07-01T08:00:00Z",
              "public_url": "https://studio.example.com/p/prop-slug"
            }
            """.utf8
        )

        let proposal = try MiseJSON.decoder().decode(Proposal.self, from: data)

        XCTAssertEqual(proposal.status, .viewed)
        XCTAssertTrue(proposal.canAccept)
        XCTAssertEqual(proposal.lineItems.first?.unitPrice.minorUnits, 420_000)
        XCTAssertNil(proposal.lineItems.first?.sku)
        XCTAssertEqual(proposal.publicURL?.path, "/p/prop-slug")
    }

    func testFavoriteStateDecodesSectionProgress() throws {
        let data = Data(
            """
            {
              "asset_id": 201,
              "selected": true,
              "section_selected_count": 2,
              "section_proof_target": 20
            }
            """.utf8
        )

        let state = try MiseJSON.decoder().decode(FavoriteState.self, from: data)

        XCTAssertEqual(state.assetID, 201)
        XCTAssertTrue(state.selected)
        XCTAssertEqual(state.sectionSelectedCount, 2)
        XCTAssertEqual(state.sectionProofTarget, 20)
    }
}
