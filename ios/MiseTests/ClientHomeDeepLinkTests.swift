import XCTest

@testable import Mise

final class ClientHomeDeepLinkTests: XCTestCase {
    private func step(
        kind: NextStepKind,
        documentVariant: String?,
        documentID: Int64?,
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

    func testDocumentStepProducesDocumentRef() {
        let proposal = step(kind: .proposal, documentVariant: "proposal", documentID: 4)
        XCTAssertEqual(proposal.documentRef, DocumentRef(variant: "proposal", id: 4))

        let invoice = step(kind: .invoice, documentVariant: "invoice", documentID: 12)
        XCTAssertEqual(invoice.documentRef, DocumentRef(variant: "invoice", id: 12))
    }

    func testGalleryStepHasNoDocumentRef() {
        let gallery = step(kind: .gallery, documentVariant: nil, documentID: nil, galleryID: 17)
        XCTAssertNil(gallery.documentRef)
    }

    func testStepMissingIDHasNoDocumentRef() {
        let partial = step(kind: .proposal, documentVariant: "proposal", documentID: nil)
        XCTAssertNil(partial.documentRef)
    }
}
