import Foundation

struct ProjectStatus: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let inquiryReceived = Self(rawValue: "inquiry_received")
    static let consultationCall = Self(rawValue: "consultation_call")
    static let proposalSent = Self(rawValue: "proposal_sent")
    static let contractSigned = Self(rawValue: "contract_signed")
    static let retainerPaid = Self(rawValue: "retainer_paid")
    static let sessionPlanning = Self(rawValue: "session_planning")
    static let projectClosed = Self(rawValue: "project_closed")
    static let archived = Self(rawValue: "archived")
}
struct GalleryType: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let gallery = Self(rawValue: "gallery")
    static let drop = Self(rawValue: "drop")
}

struct MediaKind: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let photo = Self(rawValue: "photo")
    static let video = Self(rawValue: "video")
}

struct MediaStatus: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let pending = Self(rawValue: "pending")
    static let ready = Self(rawValue: "ready")
    static let failed = Self(rawValue: "failed")
}

struct CullState: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let keep = Self(rawValue: "keep")
    static let cut = Self(rawValue: "cut")
}

struct ProposalStatus: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let draft = Self(rawValue: "draft")
    static let sent = Self(rawValue: "sent")
    static let viewed = Self(rawValue: "viewed")
    static let accepted = Self(rawValue: "accepted")
    static let declined = Self(rawValue: "declined")
}

struct ContractStatus: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let draft = Self(rawValue: "draft")
    static let sent = Self(rawValue: "sent")
    static let viewed = Self(rawValue: "viewed")
    static let signed = Self(rawValue: "signed")
}

struct InvoiceStatus: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let draft = Self(rawValue: "draft")
    static let sent = Self(rawValue: "sent")
    static let viewed = Self(rawValue: "viewed")
    static let depositPaid = Self(rawValue: "deposit_paid")
    static let paid = Self(rawValue: "paid")
}

struct PaymentKind: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let deposit = Self(rawValue: "deposit")
    static let balance = Self(rawValue: "balance")
    static let full = Self(rawValue: "full")
}

struct BookingStatus: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let confirmed = Self(rawValue: "confirmed")
    static let cancelled = Self(rawValue: "cancelled")
}

struct DashboardSummary: Codable, Hashable, Sendable {
    let generatedAt: Date
    let newInquiries: Int
    let outstanding: MoneyCount
    let upcomingProjects14Days: Int
    let overdueInvoiceCount: Int
    let retainerDraftCount: Int
    let tasksDueCount: Int
    let actionItemCount: Int
    let kpis: DashboardKPIs
    let openTasks: [TaskSummary]
    let upcomingShoots: [UpcomingProject]
    let openInvoices: [InvoiceSummary]
    let recentActivity: [ActivityItem]
}

struct MoneyCount: Codable, Hashable, Sendable {
    let count: Int
    let amount: Money
}

struct DashboardKPIs: Codable, Hashable, Sendable {
    let inquiriesDelta7Days: Int
    let bookingsDelta7Days: Int
    let collected7Days: Money
}

struct TaskSummary: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let title: String
    let dueOn: LocalDate?
    let projectID: Int64?
    let projectTitle: String?
    let isOverdue: Bool
}

struct UpcomingProject: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let title: String
    let clientDisplayName: String
    let shootOn: LocalDate
    let daysOut: Int
}

struct ActivityItem: Codable, Hashable, Sendable, Identifiable {
    let id: String
    let kind: String
    let title: String
    let detail: String?
    let occurredAt: Date
}

struct ClientSummary: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let name: String
    let company: String?
    let email: String?
    let phone: String?
    let market: String
    let projectCount: Int
    let portalPublished: Bool
    let createdAt: Date
}

struct ProjectSummary: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let clientID: Int64
    let clientDisplayName: String
    let title: String
    let status: ProjectStatus
    let galleryID: Int64?
    let shootOn: LocalDate?
    let workspacePublished: Bool
    let createdAt: Date
}

struct GalleryDeliveryState: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let draft = Self(rawValue: "draft")
    static let proofing = Self(rawValue: "proofing")
    static let expiring = Self(rawValue: "expiring")
    static let delivered = Self(rawValue: "delivered")
}

struct GallerySummary: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let title: String
    let slug: String
    let clientID: Int64?
    let projectID: Int64?
    let clientName: String?
    let type: GalleryType
    let published: Bool
    let requiresPIN: Bool
    let contentRevision: Int64
    let coverAssetID: Int64?
    let expiresOn: LocalDate?
    let assetCount: Int
    let favoriteCount: Int
    let downloadCount: Int
    let deliveryState: GalleryDeliveryState
    let createdAt: Date
}

struct GalleryDetail: Codable, Hashable, Sendable, Identifiable {
    var id: Int64 { summary.id }

    let summary: GallerySummary
    let sections: [GallerySection]
    let assets: [GalleryAsset]
    let heroAssetIDs: [Int64]
    let vision: GalleryVisionSummary?
}

struct GallerySection: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let galleryID: Int64
    let name: String
    let caption: String?
    let position: Int
    let proofTarget: Int?
    let selectedCount: Int
}

struct MediaLinks: Codable, Hashable, Sendable {
    let thumbnailURL: URL?
    let previewURL: URL?
    let posterURL: URL?
    let downloadURL: URL?
}

struct GalleryAsset: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let galleryID: Int64
    let sectionID: Int64?
    let kind: MediaKind
    let status: MediaStatus
    let filename: String
    let width: Int?
    let height: Int?
    let durationSeconds: Double?
    let byteCount: Int64?
    let position: Int
    let createdAt: Date
    let isFavorite: Bool
    let favoriteCount: Int
    let links: MediaLinks
    let altText: String?
    let keywords: [String]
    let keeperScore: Double?
    let heroPotential: Double?
    let cullState: CullState?
}

struct GalleryVisionSummary: Codable, Hashable, Sendable {
    let status: String
    let runID: String?
    let jobID: String?
    let lastRunAt: Date?
    let analyzedAssetCount: Int?
    let heroAssetIDs: [Int64]
    let error: String?
}

struct LineItem: Codable, Hashable, Sendable {
    let label: String
    let quantity: Int
    let unitPrice: Money
    let sku: String?
}

struct Proposal: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let projectID: Int64
    let title: String
    let intro: String?
    let lineItems: [LineItem]
    let total: Money
    let status: ProposalStatus
    let canAccept: Bool
    let canDecline: Bool
    let sentAt: Date?
    let viewedAt: Date?
    let acceptedAt: Date?
    let createdAt: Date
}

struct Contract: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let projectID: Int64
    let title: String
    let body: String
    let status: ContractStatus
    let canSign: Bool
    let fullyExecuted: Bool
    let documentETag: String
    let signerName: String?
    let sentAt: Date?
    let viewedAt: Date?
    let signedAt: Date?
    let countersignedAt: Date?
    let createdAt: Date
}

struct InvoiceSummary: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let projectID: Int64
    let title: String
    let clientDisplayName: String
    let total: Money
    let balance: Money
    let status: InvoiceStatus
    let dueOn: LocalDate?
    let isOverdue: Bool
}

struct Invoice: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let projectID: Int64
    let title: String
    let lineItems: [LineItem]
    let total: Money
    let deposit: Money
    let paid: Money
    let balance: Money
    let status: InvoiceStatus
    let netDays: Int?
    let dueOn: LocalDate?
    let terms: String?
    let purchaseOrderNumber: String?
    let payments: [Payment]
    let sentAt: Date?
    let viewedAt: Date?
    let paidAt: Date?
    let createdAt: Date
}

struct Payment: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let invoiceID: Int64
    let amount: Money
    let kind: PaymentKind
    let createdAt: Date
}

struct EventType: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let slug: String
    let name: String
    let description: String
    let durationMinutes: Int
    let location: String
    let colorHex: String
    let bufferBeforeMinutes: Int
    let bufferAfterMinutes: Int
    let minimumNoticeHours: Int
    let maximumPerDay: Int?
    let bookingWindowDays: Int
    let slotStepMinutes: Int
    let active: Bool
}

struct Booking: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let eventTypeID: Int64
    let eventName: String
    let name: String
    let email: String
    let phone: String?
    let notes: String?
    let startAt: Date
    let endAt: Date
    let timeZone: String
    let status: BookingStatus
    let clientID: Int64?
    let projectID: Int64?
    let rescheduledFromID: Int64?
    let cancelReason: String?
    let cancelledAt: Date?
    let createdAt: Date
}

struct AIRun: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let capability: String
    let provider: String
    let status: String
    let review: String
    let model: String?
    let latencyMilliseconds: Int?
    let costUSD: Decimal?
    let tokens: Int?
    let error: String?
    let subjectType: String?
    let subjectID: Int64?
    let correlationID: String?
    let createdAt: Date
}

struct CullItem: Codable, Hashable, Sendable, Identifiable {
    var id: Int64 { assetID }

    let assetID: Int64
    let filename: String
    let score: Double?
    let state: CullState?
    let previewURL: URL
}
