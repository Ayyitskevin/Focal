import Foundation

/// Web destinations for the signed-in owner's account lifecycle. Export and
/// deletion stay server-authoritative web flows (ADR 0051) — the app links out
/// rather than reimplementing a destructive, password-confirmed operation. The
/// deletion link also satisfies App Review's expectation that a signed-in user
/// can find account deletion from inside the app (Guideline 5.1.1(v)).
struct StudioAccountLinks: Sendable {
    let workspaceOrigin: URL

    var exportStudio: URL {
        workspaceOrigin.appending(path: "admin/export-studio")
    }

    var deleteStudio: URL {
        workspaceOrigin.appending(path: "admin/delete-studio")
    }

    /// Where a billing-locked owner resolves a lapsed subscription. Equivalent to
    /// the descriptor's `manage_billing_url` for hosted tenants; used as the
    /// fallback when that field isn't retained in the session (conductor plan T1).
    var manageBilling: URL {
        workspaceOrigin.appending(path: "admin/billing")
    }
}
