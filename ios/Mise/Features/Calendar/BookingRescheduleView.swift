import SwiftUI

struct BookingRescheduleView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var selectedDay: Date
    @State private var showsConfirmation = false

    let booking: Booking
    let timeZoneIdentifier: String
    let model: BookingRescheduleModel

    init(
        booking: Booking,
        timeZoneIdentifier: String,
        model: BookingRescheduleModel
    ) {
        self.booking = booking
        self.timeZoneIdentifier = timeZoneIdentifier
        self.model = model
        _selectedDay = State(initialValue: booking.startAt)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Current booking") {
                    LabeledContent("Session", value: booking.eventName)
                    LabeledContent("Client", value: booking.name)
                    LabeledContent("When", value: zonedDateTime(booking.startAt))
                }

                if let attempt = model.pendingAttempt {
                    recoverySection(attempt)
                } else {
                    destinationSection
                    availableTimesSection
                }

                if let notice = model.notice {
                    Section {
                        Label(notice, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.secondary)
                            .accessibilityLabel(notice)
                    }
                }
            }
            .navigationTitle("Reschedule")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                        .disabled(model.isSubmitting)
                }
                if model.pendingAttempt == nil {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Review") { showsConfirmation = true }
                            .disabled(
                                model.selectedSlot == nil
                                    || model.isSubmitting
                                    || !model.canStartNewReschedule
                            )
                    }
                }
            }
            .confirmationDialog(
                "Reschedule \(booking.eventName)?",
                isPresented: $showsConfirmation,
                titleVisibility: .visible
            ) {
                Button("Confirm reschedule", role: .destructive) {
                    Task {
                        if await model.submitSelected(for: booking) {
                            dismiss()
                        }
                    }
                }
                Button("Keep current time", role: .cancel) {}
            } message: {
                Text(confirmationMessage)
            }
            .task(id: booking.id) {
                await model.loadSlots(for: booking, on: selectedDay)
            }
            .onChange(of: selectedDay) { _, newDay in
                Task { await model.loadSlots(for: booking, on: newDay) }
            }
            .onDisappear {
                model.clearAvailability()
            }
            .interactiveDismissDisabled(model.isSubmitting)
        }
        .environment(\.timeZone, timeZone)
    }

    private var destinationSection: some View {
        Section {
            DatePicker(
                "New day",
                selection: $selectedDay,
                in: selectionRange,
                displayedComponents: .date
            )
            .datePickerStyle(.graphical)
        } header: {
            Text("Choose a day")
        } footer: {
            Text(
                "Times come from Mise’s live \(timeZoneName) availability. "
                    + "The server checks the slot again when you confirm."
            )
        }
    }

    @ViewBuilder
    private var availableTimesSection: some View {
        Section("Available times") {
            if model.isLoadingSlots {
                HStack {
                    ProgressView()
                    Text("Checking availability…")
                        .foregroundStyle(.secondary)
                }
                .frame(minHeight: 44)
            } else if let slots = model.availability?.slots, slots.isEmpty {
                ContentUnavailableView(
                    "No available times",
                    systemImage: "calendar.badge.exclamationmark",
                    description: Text("Choose another day or refresh availability.")
                )
            } else if let slots = model.availability?.slots {
                ForEach(slots, id: \.self) { slot in
                    Button {
                        model.selectSlot(slot)
                    } label: {
                        HStack {
                            Text(slotLabel(slot))
                                .foregroundStyle(.primary)
                            Spacer()
                            if model.selectedSlot == slot {
                                Image(systemName: "checkmark.circle.fill")
                                    .accessibilityHidden(true)
                            }
                        }
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(
                        zonedDateTime(slot.startAt)
                    )
                    .accessibilityAddTraits(
                        model.selectedSlot == slot ? .isSelected : []
                    )
                }
            }
        }
    }

    private func recoverySection(
        _ attempt: PendingBookingRescheduleAttempt
    ) -> some View {
        Section {
            LabeledContent("Requested time", value: zonedDateTime(attempt.startAt))
            Button {
                Task {
                    if await model.retryPending() {
                        dismiss()
                    }
                }
            } label: {
                if model.isSubmitting {
                    HStack {
                        ProgressView()
                        Text("Checking the same request…")
                    }
                    .frame(minHeight: 44)
                } else {
                    Text("Try same request")
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
            }
            .disabled(model.isSubmitting)
        } header: {
            Text("Reschedule not confirmed")
        } footer: {
            Text(
                "Mise will reuse the saved request and safety key. "
                    + "Do not choose another time until this resolves."
            )
        }
    }

    private var confirmationMessage: String {
        guard let slot = model.selectedSlot else { return "" }
        var message =
            "Move \(booking.name) from \(zonedDateTime(booking.startAt)) "
            + "to \(zonedDateTime(slot.startAt))? "
            + "The booking changes immediately after confirmation. Mise will queue "
            + "applicable client-invite and linked-studio updates in the background."
        if booking.timeZone != timeZoneIdentifier {
            message += " Client-facing updates keep the \(booking.timeZone) time-zone context."
        }
        return message
    }

    private var timeZone: TimeZone {
        TimeZone(identifier: timeZoneIdentifier) ?? TimeZone(secondsFromGMT: 0)!
    }

    private var timeZoneName: String {
        timeZone.localizedName(for: .generic, locale: .current)
            ?? timeZoneIdentifier
    }

    private var selectionRange: ClosedRange<Date> {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let start = calendar.startOfDay(for: Date())
        let end = calendar.date(byAdding: .day, value: 365, to: start) ?? start
        return start ... end
    }

    private func slotLabel(_ slot: BookingSlot) -> String {
        let base = timeOnly(slot.startAt)
        let duplicateCount = model.availability?.slots.filter {
            timeOnly($0.startAt) == base
        }.count ?? 0
        guard duplicateCount > 1 else { return base }
        return "\(base) (UTC\(utcOffset(for: slot.startAt)))"
    }

    private func timeOnly(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.timeZone = timeZone
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    private func fullDateTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.timeZone = timeZone
        formatter.dateStyle = .full
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    private func zonedDateTime(_ date: Date) -> String {
        "\(fullDateTime(date)) (\(timeZoneName), UTC\(utcOffset(for: date)))"
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
}
