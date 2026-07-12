import SwiftUI

/// Client Bookings tab: the client's own sessions. Only capabilities that
/// resolve to a real client (workspace, portal) have bookings to show;
/// gallery and single-document links get a gentle explanation instead.
struct ClientBookingsView: View {
    let model: OwnerResourceModel<[Booking]>
    let accessKind: PrincipalKind
    let timeZoneIdentifier: String

    private var hasBookingAuthority: Bool {
        accessKind == .workspaceGuest || accessKind == .portalGuest
    }

    var body: some View {
        Group {
            if hasBookingAuthority {
                OwnerResourceView(
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
            } else {
                ContentUnavailableView(
                    "Bookings aren’t part of this link",
                    systemImage: "calendar.badge.exclamationmark",
                    description: Text(
                        "This access link covers your \(accessKind == .galleryGuest ? "gallery" : "document"). Ask the studio for your project workspace link to see bookings."
                    )
                )
            }
        }
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
