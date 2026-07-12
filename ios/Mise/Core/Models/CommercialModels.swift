import Foundation

/// Owner commercial-spine models — the native mirror of the F&B operator
/// surfaces (ADRs 0039–0046), matching the `/api/v1` contract in
/// `app/mobile_commercial_api.py`. All money is integer minor units; a
/// "company" is a root client. The server never sends admin URLs — each row
/// carries a typed `ActionTarget` the app routes itself.

/// Severity of a derived action/checklist row (maps the admin tone).
struct CommercialSeverity: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let ok = Self(rawValue: "ok")
    static let attention = Self(rawValue: "attention")
    static let missing = Self(rawValue: "missing")
}

/// Where a row points. Resolved by the app's own router; `url` is only ever a
/// public link (a live workspace), never an admin path.
struct ActionTargetKind: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let company = Self(rawValue: "company")
    static let arChase = Self(rawValue: "ar_chase")
    static let project = Self(rawValue: "project")
    static let invoice = Self(rawValue: "invoice")
    static let gallery = Self(rawValue: "gallery")
    static let workspace = Self(rawValue: "workspace")
    static let other = Self(rawValue: "other")
}

struct ActionTarget: Codable, Hashable, Sendable {
    let kind: ActionTargetKind
    let companyID: Int64?
    let projectID: Int64?
    let invoiceID: Int64?
    let galleryID: Int64?
    let section: String?
    let url: URL?
}

struct NextAction: Codable, Hashable, Sendable, Identifiable {
    let priority: Int
    let severity: CommercialSeverity
    let title: String
    let detail: String
    let meta: String?
    let target: ActionTarget

    /// Stable identity for lists: the target plus the title uniquely name a row.
    var id: String { "\(target.kind.rawValue):\(target.companyID ?? 0):\(title)" }
}

struct CommercialAction: Codable, Hashable, Sendable, Identifiable {
    let companyID: Int64
    let companyName: String
    let priority: Int
    let severity: CommercialSeverity
    let title: String
    let detail: String
    let meta: String?
    let target: ActionTarget

    var id: Int64 { companyID }
}

struct CompanySummary: Codable, Hashable, Sendable, Identifiable {
    let id: Int64
    let name: String
    let email: String?
    let billingEmail: String?
}

struct CompanyNextActions: Codable, Hashable, Sendable {
    let companyID: Int64
    let companyName: String
    let actions: [NextAction]
}

struct ArChaseStatus: APIStringValue {
    let rawValue: String
    init(rawValue: String) { self.rawValue = rawValue }

    static let never = Self(rawValue: "never")
    static let recent = Self(rawValue: "recent")
    static let due = Self(rawValue: "due")
}

struct ArChaseCadence: Codable, Hashable, Sendable {
    let status: ArChaseStatus
    let followupDue: Bool
    let daysSince: Int?
    let lastSentAt: Date?
    let lastSentTo: String?
    let nextDueOn: LocalDate?
    let summary: String
    let detail: String
}

struct OverdueInvoice: Codable, Hashable, Sendable, Identifiable {
    let invoiceID: Int64
    let title: String?
    let status: String
    let dueDate: LocalDate?
    let total: Money
    let paid: Money
    let owed: Money
    let projectID: Int64?
    let projectTitle: String?
    let clientID: Int64?
    let clientName: String?
    let publicURL: URL

    var id: Int64 { invoiceID }
}

/// Read-only preview of the chase email. Sending stays a web/M4 flow.
struct ArChaseDraft: Codable, Hashable, Sendable {
    let to: String
    let subject: String
    let body: String
}

struct ArChaseAssist: Codable, Hashable, Sendable {
    let companyID: Int64
    let companyName: String
    let owed: Money
    let overdueInvoices: [OverdueInvoice]
    let cadence: ArChaseCadence
    let draft: ArChaseDraft
}

struct CloseoutItem: Codable, Hashable, Sendable, Identifiable {
    let key: String
    let title: String
    let severity: CommercialSeverity
    let badge: String
    let detail: String
    let target: ActionTarget?

    var id: String { key }
}

struct ProjectCloseout: Codable, Hashable, Sendable {
    let projectID: Int64
    let ready: Bool
    let okCount: Int
    let attentionCount: Int
    let missingCount: Int
    let total: Int
    let items: [CloseoutItem]
}
