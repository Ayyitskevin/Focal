import SwiftUI

struct HomeView: View {
    let model: OwnerResourceModel<DashboardSummary>
    let navigate: (OwnerDestination) -> Void

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { _ in false },
            content: dashboard,
            empty: { EmptyView() }
        )
        .navigationTitle("Home")
    }

    private func dashboard(_ summary: DashboardSummary) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 150), spacing: 12)],
                    spacing: 12
                ) {
                    MetricCard(title: "New inquiries", value: "\(summary.newInquiries)", icon: "sparkles")
                    MetricCard(
                        title: "Outstanding",
                        value: summary.outstanding.amount.ownerDisplayValue,
                        icon: "banknote"
                    )
                    MetricCard(
                        title: "Next 14 days",
                        value: "\(summary.upcomingProjects14Days)",
                        icon: "calendar"
                    )
                    MetricCard(title: "Tasks due", value: "\(summary.tasksDueCount)", icon: "checklist")
                }

                Text("Quick actions").font(.headline)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        QuickLink(title: "Clients", icon: "person.2", destination: .clients, action: navigate)
                        QuickLink(title: "Projects", icon: "briefcase", destination: .projects, action: navigate)
                        QuickLink(title: "Galleries", icon: "photo.on.rectangle", destination: .galleries, action: navigate)
                        QuickLink(title: "Calendar", icon: "calendar", destination: .calendar, action: navigate)
                        QuickLink(title: "AI activity", icon: "sparkles", destination: .ai, action: navigate)
                    }
                }

                if !summary.upcomingShoots.isEmpty {
                    Text("Upcoming shoots").font(.headline)
                    ForEach(summary.upcomingShoots.prefix(5)) { shoot in
                        HStack(alignment: .firstTextBaseline) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(shoot.title).font(.body.weight(.semibold))
                                Text(shoot.clientDisplayName).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(shoot.shootOn.rawValue)
                                .font(.footnote.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        .padding()
                        .background(.background, in: RoundedRectangle(cornerRadius: 14))
                    }
                }
            }
            .padding()
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .refreshable { await model.refresh() }
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon).font(.subheadline).foregroundStyle(.secondary)
            Text(value).font(.title2.weight(.semibold)).minimumScaleFactor(0.7).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.background, in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}

private struct QuickLink: View {
    let title: String
    let icon: String
    let destination: OwnerDestination
    let action: (OwnerDestination) -> Void

    var body: some View {
        Button { action(destination) } label: {
            Label(title, systemImage: icon).padding(.vertical, 8)
        }
        .buttonStyle(.bordered)
    }
}
