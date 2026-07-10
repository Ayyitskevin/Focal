import SwiftUI

struct CalendarAgendaView: View {
    let model: OwnerResourceModel<[Booking]>
    let timeZoneIdentifier: String

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: agenda,
            empty: {
                ContentUnavailableView(
                    "No bookings",
                    systemImage: "calendar",
                    description: Text("Scheduled bookings will appear in this agenda.")
                )
            }
        )
        .navigationTitle("Calendar")
    }

    private func agenda(_ bookings: [Booking]) -> some View {
        let grouped = groupedBookings(bookings)
        return List {
            ForEach(grouped) { group in
                Section(dayFormatter.string(from: group.day)) {
                    ForEach(group.bookings) { booking in
                        HStack(alignment: .top, spacing: 14) {
                            Text(timeFormatter.string(from: booking.startAt))
                                .font(.subheadline.monospacedDigit().weight(.semibold))
                                .frame(width: 72, alignment: .leading)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(booking.eventName).font(.headline)
                                Text(booking.name).foregroundStyle(.secondary)
                                if let notes = booking.notes, !notes.isEmpty {
                                    Text(notes).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                }
                            }
                            Spacer(minLength: 0)
                            if booking.status == .cancelled {
                                Text("Cancelled").font(.caption).foregroundStyle(.red)
                            }
                        }
                        .padding(.vertical, 4)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
        .refreshable { await model.refresh() }
    }

    private func groupedBookings(_ bookings: [Booking]) -> [BookingDayGroup] {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let groups = Dictionary(grouping: bookings) { calendar.startOfDay(for: $0.startAt) }
        return groups.keys.sorted().map { day in
            BookingDayGroup(
                day: day,
                bookings: groups[day, default: []].sorted { $0.startAt < $1.startAt }
            )
        }
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

private struct BookingDayGroup: Identifiable {
    let day: Date
    let bookings: [Booking]
    var id: Date { day }
}
