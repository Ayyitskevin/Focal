import XCTest

@testable import Mise

final class StudioAccountLinksTests: XCTestCase {
    func testLinksFromBareOrigin() throws {
        let links = StudioAccountLinks(
            workspaceOrigin: try XCTUnwrap(URL(string: "https://north-star.mise.example"))
        )
        XCTAssertEqual(
            links.exportStudio.absoluteString,
            "https://north-star.mise.example/admin/export-studio"
        )
        XCTAssertEqual(
            links.deleteStudio.absoluteString,
            "https://north-star.mise.example/admin/delete-studio"
        )
        XCTAssertEqual(
            links.manageBilling.absoluteString,
            "https://north-star.mise.example/admin/billing"
        )
    }

    func testLinksFromTrailingSlashOrigin() throws {
        // The login payload's api_base_url arrives with a trailing slash
        // ("https://studio.test/") — the links must not double it.
        let links = StudioAccountLinks(
            workspaceOrigin: try XCTUnwrap(URL(string: "https://studio.test/"))
        )
        XCTAssertEqual(
            links.deleteStudio.absoluteString,
            "https://studio.test/admin/delete-studio"
        )
        XCTAssertEqual(
            links.exportStudio.absoluteString,
            "https://studio.test/admin/export-studio"
        )
        XCTAssertEqual(
            links.manageBilling.absoluteString,
            "https://studio.test/admin/billing"
        )
    }

    func testManageBillingPrefersDescriptorURL() throws {
        let preferred = try XCTUnwrap(
            URL(string: "https://billing.mise.example/portal/session")
        )
        let links = StudioAccountLinks(
            workspaceOrigin: try XCTUnwrap(URL(string: "https://north-star.mise.example")),
            preferredManageBillingURL: preferred
        )

        XCTAssertEqual(links.manageBilling, preferred)
    }
}
