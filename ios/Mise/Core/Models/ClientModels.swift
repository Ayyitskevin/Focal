import Foundation

/// Which capability shaped a `/client/home` summary. Mirrors the backend's
/// exact-authority model: there is no unified client account, only the four
/// shared-access principals (see docs/IOS-ARCHITECTURE.md §3).
struct ClientAccessKind: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let gallery = Self(rawValue: "gallery")
    static let portal = Self(rawValue: "portal")
    static let workspace = Self(rawValue: "workspace")
    static let document = Self(rawValue: "document")
}

struct NextStepKind: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let proposal = Self(rawValue: "proposal")
    static let contract = Self(rawValue: "contract")
    static let invoice = Self(rawValue: "invoice")
    static let gallery = Self(rawValue: "gallery")
}

struct NextStepAction: Codable, Hashable, Sendable, Identifiable {
    let id: String
    let kind: NextStepKind
    let title: String
    let detail: String
    let documentVariant: String?
    let documentID: Int64?
    let galleryID: Int64?
    let publicURL: URL?

    /// A document deep-link for this step, when it points at a specific
    /// proposal/contract/invoice (used to open the exact document, not just
    /// the Documents tab).
    var documentRef: DocumentRef? {
        guard let variant = documentVariant, let id = documentID else { return nil }
        return DocumentRef(variant: variant, id: id)
    }
}

/// Identifies one client-visible document for navigation. Deep-links from Home
/// carry only (variant, id); the Documents tab resolves it to the full object.
struct DocumentRef: Hashable, Sendable {
    let variant: String
    let id: Int64
}

struct ClientDocumentPreview: Codable, Hashable, Sendable, Identifiable {
    let variant: String
    let id: Int64
    let title: String
    let status: String
    let total: Money?
    let balance: Money?
    let publicURL: URL
}

struct ClientHomeSummary: Codable, Hashable, Sendable {
    let principalKind: ClientAccessKind
    let studioName: String
    let clientDisplayName: String?
    let projectID: Int64?
    let projectTitle: String?
    let galleryID: Int64?
    let galleryCount: Int
    let nextSteps: [NextStepAction]
    let document: ClientDocumentPreview?
}

/// One project's client-visible documents, fetched together for the Documents tab.
struct ClientDocuments: Codable, Hashable, Sendable {
    let proposals: [Proposal]
    let contracts: [Contract]
    let invoices: [Invoice]

    var isEmpty: Bool {
        proposals.isEmpty && contracts.isEmpty && invoices.isEmpty
    }
}
