import SwiftUI

/// Full-screen owner state when the studio's hosted subscription has lapsed and
/// the API answers 402 (`tenant.subscription_required`). The session is still
/// valid — the subscription isn't — so this NEVER signs the owner out. It links
/// out to the web billing panel (checkout/Stripe live there, ADRs 0049/0054;
/// the app hosts no purchase UI, ADR 0070) and offers a retry once billing is
/// resolved. Conductor plan T1.
@MainActor
struct BillingLockedView: View {
    let manageBillingURL: URL
    let detail: String?
    let isRetrying: Bool
    let retry: () async -> Void

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "creditcard.trianglebadge.exclamationmark")
                .font(.system(size: 52, weight: .medium))
                .foregroundStyle(.tint)
                .accessibilityHidden(true)

            Text("Subscription needs attention")
                .font(.title2.bold())
                .multilineTextAlignment(.center)

            Text(detail ?? defaultMessage)
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Link(destination: manageBillingURL) {
                Text("Manage billing")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent)
            .accessibilityHint("Opens your studio's billing page in the browser")

            Button {
                Task { await retry() }
            } label: {
                if isRetrying {
                    ProgressView()
                        .frame(maxWidth: .infinity, minHeight: 44)
                } else {
                    Text("Try again")
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
            }
            .buttonStyle(.bordered)
            .disabled(isRetrying)
        }
        .padding(28)
        .frame(maxWidth: 480)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(uiColor: .systemGroupedBackground))
        .accessibilityElement(children: .contain)
    }

    private var defaultMessage: String {
        "This studio's Mise subscription is paused. Update billing to restore "
            + "access — your data is safe and nothing has been deleted."
    }
}
