import XCTest

@testable import Mise

final class CommercialRouteTests: XCTestCase {
    private func target(
        kind: ActionTargetKind,
        companyID: Int64? = nil,
        projectID: Int64? = nil,
        invoiceID: Int64? = nil
    ) -> ActionTarget {
        ActionTarget(
            kind: kind,
            companyID: companyID,
            projectID: projectID,
            invoiceID: invoiceID,
            galleryID: nil,
            section: nil,
            url: nil
        )
    }

    func testArChaseTargetRoutesToArChase() {
        let route = CommercialRoute.from(
            target: target(kind: .arChase, companyID: 9),
            fallbackName: "Blue Plate",
            fallbackTitle: "Chase past-due invoice"
        )
        XCTAssertEqual(route, .arChase(companyID: 9, name: "Blue Plate"))
    }

    func testProjectTargetRoutesToCloseout() {
        let route = CommercialRoute.from(
            target: target(kind: .project, companyID: 9, projectID: 31),
            fallbackName: "Blue Plate",
            fallbackTitle: "Update deliverables"
        )
        XCTAssertEqual(route, .closeout(projectID: 31, title: "Update deliverables"))
    }

    func testCompanyTargetRoutesToCompany() {
        let route = CommercialRoute.from(
            target: target(kind: .company, companyID: 9),
            fallbackName: "Blue Plate",
            fallbackTitle: "Add billing email"
        )
        XCTAssertEqual(route, .company(id: 9, name: "Blue Plate"))
    }

    func testInvoiceTargetFallsBackToCompany() {
        let route = CommercialRoute.from(
            target: target(kind: .invoice, companyID: 9, invoiceID: 4120),
            fallbackName: "Blue Plate",
            fallbackTitle: "Send draft invoice"
        )
        XCTAssertEqual(route, .company(id: 9, name: "Blue Plate"))
    }

    func testTargetWithoutAnyIDHasNoRoute() {
        let route = CommercialRoute.from(
            target: target(kind: .other),
            fallbackName: "Blue Plate",
            fallbackTitle: "Something"
        )
        XCTAssertNil(route)
    }
}
