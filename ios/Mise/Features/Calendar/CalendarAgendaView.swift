import SwiftUI

struct CalendarAgendaView: View {
    let model: ResourceModel<[Booking]>
    let timeZoneIdentifier: String
    let commands: OwnerCommandModel

    @State private var bookingToCancel: Booking?

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: { commands.visibleBookings(from: $0).isEmpty },
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
        .confirmationDialog(
            "Cancel booking?",
            isPresented: Binding(
                get: { bookingToCancel != nil },
                set: { if !$0 { bookingToCancel = nil } }
            ),
            titleVisibility: .visible,
            presenting: bookingToCancel
        ) { booking in
            Button("Cancel booking", role: .destructive) {
                Task {
                    _ = await commands.cancelBooking(booking)
                    await refreshBookings()
                }
            }
            Button("Keep booking", role: .cancel) {}
        } message: { booking in
            Text(cancellationMessage(for: booking))
        }
        .alert(
            "Booking cancellation",
            isPresented: Binding(
                get: { commands.bookingNotice != nil },
                set: { if !$0 { commands.bookingNotice = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(commands.bookingNotice ?? "")
        }
    }

    private func agenda(_ bookings: [Booking]) -> some View {
        let grouped = groupedBookings(commands.visibleBookings(from: bookings))
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
                            } else if commands.canWrite {
                                if commands.isBookingInFlight(booking.id) {
                                    ProgressView()
                                        .frame(width: 44, height: 44)
                                        .accessibilityLabel("Cancelling \(booking.eventName)")
                                } else {
                                    Button("Cancel", role: .destructive) {
                                        bookingToCancel = booking
                                    }
                                    .buttonStyle(.bordered)
                                    .frame(minWidth: 44, minHeight: 44)
                                    .accessibilityLabel("Cancel \(booking.eventName) for \(booking.name)")
                                    .accessibilityHint("Requires confirmation. Mise will attempt to notify the client.")
                                }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
        }
        .refreshable { await refreshBookings() }
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

    private func cancellationMessage(for booking: Booking) -> String {
        let day = dayFormatter.string(from: booking.startAt)
        let time = timeFormatter.string(from: booking.startAt)
        return "Cancel \(booking.eventName) for \(booking.name) on \(day) at \(time)? "
            + "This marks the booking cancelled. Mise will attempt to notify the client "
            + "and remove the linked calendar event."
    }

    private func refreshBookings() async {
        await model.refresh()
        if case let .loaded(snapshot) = model.state {
            commands.reconcileBookings(with: snapshot.value)
        }
    }
}

private struct BookingDayGroup: Identifiable {
    let day: Date
    let bookings: [Booking]
    var id: Date { day }
}
