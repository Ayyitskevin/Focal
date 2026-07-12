import XCTest

@testable import Mise

final class CommercialModelsDecodingTests: XCTestCase {
    private func decoder() -> JSONDecoder { MiseJSON.decoder() }

    func testCommercialActionPageDecodesTypedTarget() throws {
        let data = Data(
            """
            {
              "items": [
                {
                  "company_id": 9,
                  "company_name": "Blue Plate Group",
                  "priority": 10,
                  "severity": "attention",
                  "title": "Chase past-due invoice",
                  "detail": "3 past due · $4,200 owed",
                  "meta": "money",
                  "target": {
                    "kind": "ar_chase", "company_id": 9, "project_id": null,
                    "invoice_id": null, "gallery_id": null, "section": null, "url": null
                  }
                }
              ],
              "next_cursor": null,
              "has_more": false
            }
            """.utf8
        )
        let page = try decoder().decode(APIPage<CommercialAction>.self, from: data)
        XCTAssertEqual(page.items.count, 1)
        XCTAssertFalse(page.hasMore)
        let action = page.items[0]
        XCTAssertEqual(action.companyID, 9)
        XCTAssertEqual(action.companyName, "Blue Plate Group")
        XCTAssertEqual(action.priority, 10)
        XCTAssertEqual(action.severity, .attention)
        XCTAssertEqual(action.target.kind, .arChase)
        XCTAssertEqual(action.target.companyID, 9)
        XCTAssertNil(action.target.url)
    }

    func testCompanyNextActionsDecodesInvoiceTarget() throws {
        let data = Data(
            """
            {
              "company_id": 9,
              "company_name": "Blue Plate Group",
              "actions": [
                {
                  "priority": 20, "severity": "attention",
                  "title": "Send draft invoice", "detail": "Downtown · December coverage",
                  "meta": "money",
                  "target": {
                    "kind": "invoice", "company_id": 9, "project_id": null,
                    "invoice_id": 4120, "gallery_id": null, "section": null, "url": null
                  }
                }
              ]
            }
            """.utf8
        )
        let value = try decoder().decode(CompanyNextActions.self, from: data)
        XCTAssertEqual(value.companyID, 9)
        XCTAssertEqual(value.actions.count, 1)
        XCTAssertEqual(value.actions[0].target.kind, .invoice)
        XCTAssertEqual(value.actions[0].target.invoiceID, 4120)
    }

    func testArChaseAssistDecodesMoneyDatesAndDraft() throws {
        let data = Data(
            """
            {
              "company_id": 9,
              "company_name": "Blue Plate Group",
              "owed": { "minor_units": 420000, "currency_code": "USD" },
              "overdue_invoices": [
                {
                  "invoice_id": 4120, "title": "November coverage", "status": "sent",
                  "due_date": "2026-06-15",
                  "total": { "minor_units": 250000, "currency_code": "USD" },
                  "paid":  { "minor_units": 50000,  "currency_code": "USD" },
                  "owed":  { "minor_units": 200000, "currency_code": "USD" },
                  "project_id": 31, "project_title": "Q4 Menu Refresh",
                  "client_id": 14, "client_name": "Blue Plate — Downtown",
                  "public_url": "https://studio.example.com/i/inv-2026-118"
                }
              ],
              "cadence": {
                "status": "recent", "followup_due": false, "days_since": 2,
                "last_sent_at": "2026-07-10T14:02:00Z", "last_sent_to": "ap@blueplate.example",
                "next_due_on": "2026-07-17", "summary": "last chased 2d ago",
                "detail": "You emailed a balance reminder 2 days ago."
              },
              "draft": {
                "to": "ap@blueplate.example",
                "subject": "Follow-up on open invoice balance - Blue Plate Group",
                "body": "Hi Blue Plate Group, ..."
              }
            }
            """.utf8
        )
        let value = try decoder().decode(ArChaseAssist.self, from: data)
        XCTAssertEqual(value.owed.minorUnits, 420000)
        XCTAssertEqual(value.overdueInvoices.count, 1)
        let inv = value.overdueInvoices[0]
        XCTAssertEqual(inv.owed.minorUnits, 200000)
        XCTAssertEqual(inv.dueDate?.rawValue, "2026-06-15")
        XCTAssertEqual(inv.publicURL.absoluteString, "https://studio.example.com/i/inv-2026-118")
        XCTAssertEqual(value.cadence.status, .recent)
        XCTAssertEqual(value.cadence.daysSince, 2)
        XCTAssertNotNil(value.cadence.lastSentAt)
        XCTAssertEqual(value.cadence.nextDueOn?.rawValue, "2026-07-17")
        XCTAssertEqual(value.draft.to, "ap@blueplate.example")
    }

    func testProjectCloseoutDecodesItemsAndTargets() throws {
        let data = Data(
            """
            {
              "project_id": 31, "ready": false,
              "ok_count": 4, "attention_count": 2, "missing_count": 1, "total": 7,
              "items": [
                {
                  "key": "deliverables", "title": "Deliverables",
                  "severity": "attention", "badge": "Needs attention", "detail": "5/8 delivered",
                  "target": {
                    "kind": "project", "company_id": null, "project_id": 31,
                    "invoice_id": null, "gallery_id": null, "section": "deliverables", "url": null
                  }
                }
              ]
            }
            """.utf8
        )
        let value = try decoder().decode(ProjectCloseout.self, from: data)
        XCTAssertEqual(value.projectID, 31)
        XCTAssertFalse(value.ready)
        XCTAssertEqual(value.total, 7)
        XCTAssertEqual(value.items.count, 1)
        let item = value.items[0]
        XCTAssertEqual(item.key, "deliverables")
        XCTAssertEqual(item.severity, .attention)
        XCTAssertEqual(item.target?.kind, .project)
        XCTAssertEqual(item.target?.projectID, 31)
        XCTAssertEqual(item.target?.section, "deliverables")
    }

    func testCompanyPageDecodes() throws {
        let data = Data(
            """
            {
              "items": [
                { "id": 9, "name": "Blue Plate Group", "email": "ops@blueplate.example",
                  "billing_email": "ap@blueplate.example" }
              ],
              "next_cursor": "abc",
              "has_more": true
            }
            """.utf8
        )
        let page = try decoder().decode(APIPage<CompanySummary>.self, from: data)
        XCTAssertEqual(page.items.first?.id, 9)
        XCTAssertEqual(page.items.first?.name, "Blue Plate Group")
        XCTAssertEqual(page.items.first?.billingEmail, "ap@blueplate.example")
        XCTAssertTrue(page.hasMore)
        XCTAssertEqual(page.nextCursor, "abc")
    }
}
