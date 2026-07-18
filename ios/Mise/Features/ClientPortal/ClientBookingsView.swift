import SwiftUI

/// Client Bookings tab: the client's own sessions. ClientAccessPolicy gates
/// this view before it reaches the hierarchy, so only workspace and portal
/// principals can attach its ResourceView and start loading.
struct ClientBookingsView: View {
    let model: ResourceModel<[Booking]>
    let timeZoneIdentifier: String

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: list,
            empty: {
                ContentUnavailableView(
                    "No sessions booked",
                    systemImage: "calendar",
                    description: Text(
                        "When the studio books your next session, it will appear here."
                    )
                )
            }
        )
        .navigationTitle("Bookings")
    }

    private func list(_ bookings: [Booking]) -> some View {
        List(bookings) { booking in
            NavigationLink {
                ClientBookingDetailView(
                    booking: booking,
                    timeZoneIdentifier: timeZoneIdentifier
                )
            } label: {
                row(booking)
            }
        }
        .refreshable { await model.refresh() }
    }

    private func row(_ booking: Booking) -> some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(booking.eventName).miseDisplayFont(.headline)
                Text(
                    "\(dayFormatter.string(from: booking.startAt)) · \(timeFormatter.string(from: booking.startAt))"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            StatusPill(label: booking.status.ownerDisplayName, tone: booking.status.tone)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private var timeZone: TimeZone {
        TimeZone(identifier: timeZoneIdentifier) ?? .current
    }

    private var dayFormatter: DateFormatter {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeZone = timeZone
        return formatter
    }

    private var timeFormatter: DateFormatter {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.timeZone = timeZone
        return formatter
    }
}

struct ClientBookingDetailView: View {
    let booking: Booking
    let timeZoneIdentifier: String

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    StatusPill(label: booking.status.ownerDisplayName, tone: booking.status.tone)
                    Text(booking.eventName).miseDisplayFont(.title3)
                }
                .padding(.vertical, 6)
            }

            Section {
                Label {
                    Text(dayFormatter.string(from: booking.startAt))
                } icon: {
                    Image(systemName: "calendar")
                        .foregroundStyle(MiseDesign.terra)
                }
                Label {
                    Text(
                        "\(timeFormatter.string(from: booking.startAt)) – \(timeFormatter.string(from: booking.endAt))"
                    )
                } icon: {
                    Image(systemName: "clock")
                        .foregroundStyle(MiseDesign.terra)
                }
            }

            if let notes = booking.notes, !notes.isEmpty {
                Section("Notes") {
                    Text(notes)
                        .font(.subheadline)
                }
            }
        }
        .navigationTitle("Booking")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var timeZone: TimeZone {
        TimeZone(identifier: timeZoneIdentifier) ?? .current
    }

    private var dayFormatter: DateFormatter {
        let formatter = DateFormatter()
        formatter.dateStyle = .full
        formatter.timeZone = timeZone
        return formatter
    }

    private var timeFormatter: DateFormatter {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.timeZone = timeZone
        return formatter
    }
}
