import XCTest
@testable import Mise

final class ClientDeliveryModelTests: XCTestCase {
    func testDecodesAuthorityFreePortalSummary() throws {
        let value = try MiseJSON.decoder().decode(
            ClientPortalSummary.self,
            from: Data(
                """
                {
                  "id": 3,
                  "client_display_name": "Avery Foods",
                  "galleries": [{
                    "id": 7,
                    "title": "Summer Campaign",
                    "slug": "summer-gallery",
                    "expires_on": "2026-12-31",
                    "created_at": "2026-07-01T12:00:00Z"
                  }],
                  "brand_assets": [{
                    "id": 8,
                    "filename": "logo.svg",
                    "byte_count": 2048,
                    "created_at": "2026-07-02T12:00:00Z"
                  }],
                  "licenses": [{
                    "title": "Summer usage",
                    "scope": "Campaign selects",
                    "tier": "Extended",
                    "exclusive": false,
                    "territory": ["North America"],
                    "channels": ["Paid Social", "Website"],
                    "term": "Perpetual"
                  }],
                  "usage_rights_note": "Client-facing rights note"
                }
                """.utf8
            )
        )

        XCTAssertEqual(value.clientDisplayName, "Avery Foods")
        XCTAssertEqual(value.galleries.first?.expiresOn?.rawValue, "2026-12-31")
        XCTAssertEqual(value.licenses.first?.channels, ["Paid Social", "Website"])
    }

    func testDecodesWorkspaceBrowserFallbackResources() throws {
        let value = try MiseJSON.decoder().decode(
            ClientWorkspaceSummary.self,
            from: Data(
                """
                {
                  "id": 4,
                  "title": "Summer launch",
                  "client_display_name": "Avery Foods",
                  "resources": [{
                    "kind": "invoice",
                    "id": 12,
                    "title": "Final invoice",
                    "status": "sent",
                    "slug": "summer-invoice",
                    "total": {"minor_units": 10000, "currency_code": "USD"},
                    "due_on": "2026-08-15",
                    "action_url": "https://studio.example.com/i/summer-invoice"
                  }]
                }
                """.utf8
            )
        )

        XCTAssertEqual(value.resources.first?.kind, .invoice)
        XCTAssertEqual(value.resources.first?.total?.minorUnits, 10_000)
        XCTAssertEqual(
            value.resources.first?.actionURL.absoluteString,
            "https://studio.example.com/i/summer-invoice"
        )
    }

    func testDecodesAuthoritativeDocumentPaymentHistory() throws {
        let value = try MiseJSON.decoder().decode(
            ClientDocumentSummary.self,
            from: Data(
                """
                {
                  "kind": "invoice",
                  "id": 12,
                  "project_id": 4,
                  "title": "Final invoice",
                  "project_title": "Summer launch",
                  "client_display_name": "Avery Foods",
                  "status": "sent",
                  "detail": "Net 15",
                  "line_items": [{
                    "label": "Creative direction",
                    "quantity": 1,
                    "unit_price": {"minor_units": 10000, "currency_code": "USD"},
                    "sku": null
                  }],
                  "total": {"minor_units": 10000, "currency_code": "USD"},
                  "deposit": {"minor_units": 3000, "currency_code": "USD"},
                  "paid": {"minor_units": 2500, "currency_code": "USD"},
                  "balance": {"minor_units": 7500, "currency_code": "USD"},
                  "payments": [{
                    "id": 22,
                    "invoice_id": 12,
                    "amount": {"minor_units": 2500, "currency_code": "USD"},
                    "kind": "deposit",
                    "created_at": "2026-07-03T12:00:00Z"
                  }],
                  "payment_count": 501,
                  "payments_truncated": true,
                  "due_on": "2026-08-15",
                  "sent_at": "2026-07-01T12:00:00Z",
                  "viewed_at": null,
                  "completed_at": null,
                  "document_etag": null,
                  "can_act": true,
                  "action_url": "https://studio.example.com/i/summer-invoice"
                }
                """.utf8
            )
        )

        XCTAssertEqual(value.kind, .invoice)
        XCTAssertEqual(value.balance?.minorUnits, 7_500)
        XCTAssertEqual(value.payments.first?.amount.minorUnits, 2_500)
        XCTAssertEqual(value.paymentCount, 501)
        XCTAssertTrue(value.paymentsTruncated)
        XCTAssertTrue(value.canAct)
    }

    func testDecodesBoundedGalleryCommentContract() throws {
        let value = try MiseJSON.decoder().decode(
            [GalleryComment].self,
            from: Data(
                """
                [{
                  "id": 91,
                  "asset_id": 11,
                  "parent_id": null,
                  "timecode_seconds": 12.5,
                  "body": "Tighten this cut",
                  "author_role": "client",
                  "status": "open",
                  "created_at": "2026-07-10T14:30:00Z"
                }]
                """.utf8
            )
        )

        XCTAssertEqual(value.first?.assetID, 11)
        XCTAssertEqual(value.first?.timecodeSeconds, 12.5)
        XCTAssertEqual(value.first?.status, .open)
    }

    func testBrowserFallbackRequiresSameOriginAndExpectedCapabilityPath() {
        let origin = URL(string: "https://studio.example.com")!
        let valid = URL(string: "https://studio.example.com/i/summer-invoice")!

        XCTAssertEqual(
            ClientBrowserTargetValidator.validated(
                valid,
                workspaceOrigin: origin,
                allowedPathPrefix: "/i/"
            ),
            valid
        )
        XCTAssertNil(ClientBrowserTargetValidator.validated(
            URL(string: "https://evil.example/i/summer-invoice")!,
            workspaceOrigin: origin,
            allowedPathPrefix: "/i/"
        ))
        XCTAssertNil(ClientBrowserTargetValidator.validated(
            URL(string: "https://studio.example.com/p/summer-invoice")!,
            workspaceOrigin: origin,
            allowedPathPrefix: "/i/"
        ))
        XCTAssertNil(ClientBrowserTargetValidator.validated(
            URL(string: "https://studio.example.com/i/summer-invoice?continue=evil")!,
            workspaceOrigin: origin,
            allowedPathPrefix: "/i/"
        ))
        XCTAssertNil(ClientBrowserTargetValidator.validated(
            URL(string: "https://studio.example.com/i/summer-invoice/extra")!,
            workspaceOrigin: origin,
            allowedPathPrefix: "/i/"
        ))
    }
}
