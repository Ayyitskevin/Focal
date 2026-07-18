import XCTest

@testable import Mise

final class ClientHomeDeepLinkTests: XCTestCase {
    private func step(
        kind: NextStepKind,
        documentVariant: String? = nil,
        documentID: Int64? = nil,
        galleryID: Int64? = nil
    ) -> NextStepAction {
        NextStepAction(
            id: "\(kind.rawValue):\(documentID ?? galleryID ?? 0)",
            kind: kind,
            title: "Title",
            detail: "Detail",
            documentVariant: documentVariant,
            documentID: documentID,
            galleryID: galleryID,
            publicURL: nil
        )
    }

    func testAllowedDocumentStepsPreserveEveryExactReference() {
        let policy = ClientAccessPolicy(principalKind: .workspaceGuest)
        let cases: [(NextStepKind, String, Int64)] = [
            (.proposal, "proposal", 4),
            (.contract, "contract", 8),
            (.invoice, "invoice", 12),
        ]

        for (kind, variant, id) in cases {
            let action = step(kind: kind, documentVariant: variant, documentID: id)
            XCTAssertEqual(action.documentRef, DocumentRef(variant: variant, id: id))

            guard let route = policy.route(for: action) else {
                XCTFail("Expected \(variant):\(id) to route")
                continue
            }
            XCTAssertEqual(route.target, .documents)
            guard case let .document(ref) = route else {
                XCTFail("Expected \(variant):\(id) to remain an exact document route")
                continue
            }
            XCTAssertEqual(ref, DocumentRef(variant: variant, id: id))
        }
    }

    func testGalleryStepsRouteWithAnExactOrCollectionReference() {
        let exact = step(kind: .gallery, galleryID: 17)
        let exactRoute = ClientAccessPolicy(principalKind: .galleryGuest).route(for: exact)

        guard case let .destination(exactDestination)? = exactRoute else {
            return XCTFail("Expected gallery access to route its exact gallery step")
        }
        XCTAssertEqual(exactDestination, .gallery)
        XCTAssertEqual(exactRoute?.target, .gallery)

        // Portal Home legitimately returns a collection-level Gallery step with no ID.
        let collection = step(kind: .gallery)
        let collectionRoute = ClientAccessPolicy(principalKind: .portalGuest).route(
            for: collection
        )

        guard case let .destination(collectionDestination)? = collectionRoute else {
            return XCTFail("Expected portal access to route its gallery collection step")
        }
        XCTAssertEqual(collectionDestination, .gallery)
        XCTAssertEqual(collectionRoute?.target, .gallery)
    }

    func testGalleryStepIDShapeMustMatchThePrincipalKind() {
        let exact = step(kind: .gallery, galleryID: 17)
        let collection = step(kind: .gallery)

        XCTAssertNil(
            ClientAccessPolicy(principalKind: .galleryGuest).route(for: collection)
        )
        XCTAssertNil(
            ClientAccessPolicy(principalKind: .workspaceGuest).route(for: collection)
        )
        XCTAssertNil(
            ClientAccessPolicy(principalKind: .portalGuest).route(for: exact)
        )

        guard case .destination(.gallery)? = ClientAccessPolicy(
            principalKind: .workspaceGuest
        ).route(for: exact) else {
            return XCTFail("Workspace gallery steps require and preserve a positive ID")
        }
    }

    func testMalformedDocumentStepsDoNotRoute() {
        let policy = ClientAccessPolicy(principalKind: .workspaceGuest)
        let malformed = [
            step(kind: .proposal, documentVariant: nil, documentID: 4),
            step(kind: .proposal, documentVariant: "proposal", documentID: nil),
            step(kind: .proposal, documentVariant: "contract", documentID: 4),
            step(kind: .contract, documentVariant: "proposal", documentID: 4),
            step(kind: .invoice, documentVariant: "receipt", documentID: 4),
            step(kind: .proposal, documentVariant: "proposal", documentID: 0),
            step(kind: .proposal, documentVariant: "proposal", documentID: -4),
            step(
                kind: .proposal,
                documentVariant: "proposal",
                documentID: 4,
                galleryID: 17
            ),
            step(
                kind: NextStepKind(rawValue: "future_step"),
                documentVariant: "proposal",
                documentID: 4
            ),
        ]

        for action in malformed {
            XCTAssertNil(policy.route(for: action), action.id)
        }
    }

    func testMalformedGalleryStepsDoNotRoute() {
        let policy = ClientAccessPolicy(principalKind: .workspaceGuest)
        let malformed = [
            step(kind: .gallery, galleryID: 0),
            step(kind: .gallery, galleryID: -17),
            step(kind: .gallery, documentVariant: "proposal"),
            step(kind: .gallery, documentID: 4),
            step(
                kind: .gallery,
                documentVariant: "proposal",
                documentID: 4,
                galleryID: 17
            ),
        ]

        for action in malformed {
            XCTAssertNil(policy.route(for: action), action.id)
        }
    }

    func testForbiddenStepsDoNotRoute() {
        let proposal = step(kind: .proposal, documentVariant: "proposal", documentID: 4)
        let gallery = step(kind: .gallery, galleryID: 17)
        let galleryCollection = step(kind: .gallery)
        let unknown = PrincipalKind(rawValue: "future_guest")

        XCTAssertNil(ClientAccessPolicy(principalKind: .galleryGuest).route(for: proposal))
        XCTAssertNil(ClientAccessPolicy(principalKind: .portalGuest).route(for: proposal))
        XCTAssertNil(ClientAccessPolicy(principalKind: .documentGuest).route(for: proposal))
        XCTAssertNil(ClientAccessPolicy(principalKind: .documentGuest).route(for: gallery))
        XCTAssertNil(
            ClientAccessPolicy(principalKind: .documentGuest).route(for: galleryCollection)
        )
        XCTAssertNil(ClientAccessPolicy(principalKind: .studioOwner).route(for: proposal))
        XCTAssertNil(ClientAccessPolicy(principalKind: .studioOwner).route(for: gallery))
        XCTAssertNil(ClientAccessPolicy(principalKind: unknown).route(for: proposal))
        XCTAssertNil(ClientAccessPolicy(principalKind: unknown).route(for: gallery))
    }

    func testMissingDocumentIDStillHasNoRawDocumentRef() {
        let partial = step(kind: .proposal, documentVariant: "proposal")
        XCTAssertNil(partial.documentRef)
    }

    func testGalleryStepHasNoDocumentRef() {
        let gallery = step(kind: .gallery, galleryID: 17)
        XCTAssertNil(gallery.documentRef)
    }
}
