import SwiftUI

struct CalendarAgendaView: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.scenePhase) private var scenePhase

    let model: ResourceModel<[Booking]>
    let timeZoneIdentifier: String
    let commands: OwnerCommandModel
    let reschedule: BookingRescheduleModel

    @State private var bookingToCancel: Booking?
    @State private var bookingToReschedule: Booking?

    var body: some View {
        ResourceView(
            model: model,
            isEmpty: {
                visibleBookings($0).isEmpty
                    && reschedule.pendingAttempt == nil
                    && !reschedule.recoveryIsBlocked
                    && reschedule.workflowMessage == nil
            },
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
        .sheet(item: $bookingToReschedule) { booking in
            BookingRescheduleView(
                booking: booking,
                timeZoneIdentifier: timeZoneIdentifier,
                model: reschedule
            )
        }
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
        .task {
            await reschedule.restore()
            await reschedule.refreshCapability()
        }
        .task(id: reschedule.workflowPollID) {
            await reschedule.pollWorkflow()
        }
        .onChange(of: reschedule.latestResult?.workflowID) { previous, current in
            guard let current, current != previous else { return }
            Task { await refreshBookings() }
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            Task {
                await reschedule.refreshCapability()
                await reschedule.refreshWorkflowStatus()
            }
        }
    }

    private func agenda(_ bookings: [Booking]) -> some View {
        let grouped = groupedBookings(visibleBookings(bookings))
        return List {
            if let attempt = reschedule.pendingAttempt {
                Section("Reschedule recovery") {
                    Label(
                        reschedule.notice
                            ?? "A booking reschedule still needs confirmation.",
                        systemImage: "arrow.triangle.2.circlepath"
                    )
                    Text(
                        "Saved request for booking \(attempt.bookingID), "
                            + zonedDateTime(attempt.startAt)
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    Button {
                        Task {
                            if await reschedule.retryPending() {
                                await refreshBookings()
                            }
                        }
                    } label: {
                        if reschedule.isSubmitting {
                            HStack {
                                ProgressView()
                                Text("Checking same request…")
                            }
                            .frame(minHeight: 44)
                        } else {
                            Text("Try same request")
                                .frame(minHeight: 44)
                        }
                    }
                    .disabled(reschedule.isSubmitting)
                }
            }

            if reschedule.recoveryIsBlocked {
                Section("Reschedule recovery") {
                    Label(
                        reschedule.notice
                            ?? "Saved reschedule recovery data can’t be read safely.",
                        systemImage: "exclamationmark.octagon.fill"
                    )
                    Text("No new reschedule request will be sent from this device.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if let workflowMessage = reschedule.workflowMessage {
                Section("Booking updates") {
                    Label(workflowMessage, systemImage: workflowIcon)
                    if let replacementID = reschedule.latestResult?.replacementBookingID {
                        LabeledContent("Replacement booking", value: "#\(replacementID)")
                    }
                    if let notice = reschedule.workflowNotice {
                        Text(notice)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if dynamicTypeSize.isAccessibilitySize {
                        VStack(alignment: .leading, spacing: 8) {
                            workflowControls
                        }
                    } else {
                        HStack {
                            workflowControls
                        }
                    }
                }
            }

            ForEach(grouped) { group in
                Section(dayFormatter.string(from: group.day)) {
                    ForEach(group.bookings) { booking in
                        bookingRow(booking)
                    }
                }
            }
        }
        .refreshable { await refreshBookings() }
    }

    @ViewBuilder
    private func bookingRow(_ booking: Booking) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 10) {
                Text(timeFormatter.string(from: booking.startAt))
                    .font(.subheadline.monospacedDigit().weight(.semibold))
                bookingSummary(booking)
                bookingActions(booking)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .padding(.vertical, 6)
        } else {
            HStack(alignment: .top, spacing: 14) {
                Text(timeFormatter.string(from: booking.startAt))
                    .font(.subheadline.monospacedDigit().weight(.semibold))
                    .frame(width: 72, alignment: .leading)
                bookingSummary(booking)
                Spacer(minLength: 0)
                bookingActions(booking)
            }
            .padding(.vertical, 4)
        }
    }

    private func bookingSummary(_ booking: Booking) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(booking.eventName).font(.headline)
            Text(booking.name).foregroundStyle(.secondary)
            if let notes = booking.notes, !notes.isEmpty {
                Text(notes)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
            }
        }
    }

    @ViewBuilder
    private func bookingActions(_ booking: Booking) -> some View {
        if booking.status == .cancelled {
            Text("Cancelled")
                .font(.caption)
                .foregroundStyle(.red)
        } else if reschedule.pendingAttempt?.bookingID == booking.id {
            Text("Awaiting confirmation")
                .font(.caption)
                .foregroundStyle(.secondary)
                .accessibilityLabel("Reschedule awaiting confirmation")
        } else if commands.isBookingInFlight(booking.id) {
            ProgressView()
                .frame(width: 44, height: 44)
                .accessibilityLabel("Updating \(booking.eventName)")
        } else if commands.canWrite || reschedule.canStartNewReschedule {
            Menu {
                if reschedule.canStartNewReschedule {
                    Button {
                        bookingToReschedule = booking
                    } label: {
                        Label("Reschedule", systemImage: "calendar.badge.clock")
                    }
                }
                if commands.canWrite {
                    Button(role: .destructive) {
                        bookingToCancel = booking
                    } label: {
                        Label("Cancel booking", systemImage: "calendar.badge.minus")
                    }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.title3)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(
                "Booking actions for \(booking.eventName), \(booking.name)"
            )
            .accessibilityHint(bookingActionsHint)
        }
    }

    @ViewBuilder
    private var workflowControls: some View {
        Button("Refresh status") {
            Task { await reschedule.refreshWorkflowStatus() }
        }
        .frame(minHeight: 44)
        .disabled(reschedule.isRefreshingWorkflow)

        if reschedule.canRetryWorkflow {
            Button("Retry incomplete updates") {
                Task { await reschedule.retryBlockedWorkflow() }
            }
            .frame(minHeight: 44)
            .disabled(reschedule.isRefreshingWorkflow)
            .accessibilityHint(
                "Retries only blocked updates. A provider may rarely receive a duplicate."
            )
        }
    }

    private var bookingActionsHint: String {
        if commands.canWrite, reschedule.canStartNewReschedule {
            return "Reschedule or cancel this booking."
        }
        if reschedule.canStartNewReschedule {
            return "Reschedule this booking."
        }
        return "Cancel this booking."
    }

    private func visibleBookings(_ bookings: [Booking]) -> [Booking] {
        reschedule.visibleBookings(
            from: commands.visibleBookings(from: bookings)
        )
    }

    private var workflowIcon: String {
        if reschedule.workflowStatus?.status == .succeeded {
            return "checkmark.circle.fill"
        }
        if reschedule.workflowStatus?.status == .blocked {
            return "exclamationmark.triangle.fill"
        }
        return "arrow.triangle.2.circlepath"
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

    private func fullDateTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .full
        formatter.timeStyle = .short
        formatter.timeZone = timeZone
        return formatter.string(from: date)
    }

    private func zonedDateTime(_ date: Date) -> String {
        "\(fullDateTime(date)) (UTC\(utcOffset(for: date)))"
    }

    private func utcOffset(for date: Date) -> String {
        let seconds = timeZone.secondsFromGMT(for: date)
        let sign = seconds < 0 ? "-" : "+"
        let absolute = abs(seconds)
        return String(
            format: "%@%02d:%02d",
            sign,
            absolute / 3_600,
            (absolute % 3_600) / 60
        )
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
            reschedule.reconcileBookings(with: snapshot.value)
        }
        await reschedule.refreshCapability()
        await reschedule.refreshWorkflowStatus()
    }
}

private struct BookingDayGroup: Identifiable {
    let day: Date
    let bookings: [Booking]
    var id: Date { day }
}
