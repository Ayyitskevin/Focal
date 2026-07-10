import XCTest
@testable import Mise

final class OwnerMutationEndpointTests: XCTestCase {
    func testClientUpdateCarriesVersionAndStableIdempotencyKey() throws {
        let key = UUID(uuidString: "5E2B024C-7C83-4C22-B6B8-1EE29FE47801")!
        let body = ClientMutationRequest(
            name: "A. Client",
            company: "Mise Co.",
            email: "client@example.com",
            phone: nil,
            notes: "Prefers email",
            usageRights: nil,
            market: "editorial"
        )

        let endpoint = try MiseEndpoints.Clients.update(
            id: 42,
            body: body,
            etag: #""client-42-v3""#,
            idempotencyKey: key
        )

        XCTAssertEqual(endpoint.method, .patch)
        XCTAssertEqual(endpoint.path, "/api/v1/clients/42")
        XCTAssertEqual(endpoint.headers["If-Match"], #""client-42-v3""#)
        XCTAssertEqual(endpoint.idempotencyKey, key)
        XCTAssertEqual(
            try MiseJSON.decoder().decode(ClientMutationRequest.self, from: XCTUnwrap(endpoint.body)),
            body
        )
    }

    func testTaskDeleteRequiresVersionAndIdempotencyKey() {
        let key = UUID(uuidString: "928F36D0-3E49-4D15-92E8-2FC423576986")!
        let endpoint = MiseEndpoints.Tasks.delete(
            id: 9,
            etag: #""task-9-v2""#,
            idempotencyKey: key
        )

        XCTAssertEqual(endpoint.method, .delete)
        XCTAssertEqual(endpoint.path, "/api/v1/tasks/9")
        XCTAssertEqual(endpoint.headers["If-Match"], #""task-9-v2""#)
        XCTAssertEqual(endpoint.idempotencyKey, key)
        XCTAssertNil(endpoint.body)
    }

    func testProjectCreateEncodesTenantLocalClientID() throws {
        let key = UUID(uuidString: "3BAA73E4-A93C-46C8-8BB5-D84423DD9CD9")!
        let body = ProjectCreateRequest(clientID: 17, title: "Summer Campaign")

        let endpoint = try MiseEndpoints.Projects.create(body, idempotencyKey: key)

        XCTAssertEqual(endpoint.method, .post)
        XCTAssertEqual(endpoint.path, "/api/v1/projects")
        XCTAssertEqual(endpoint.idempotencyKey, key)
        XCTAssertEqual(
            try MiseJSON.decoder().decode(ProjectCreateRequest.self, from: XCTUnwrap(endpoint.body)),
            body
        )
    }
}
