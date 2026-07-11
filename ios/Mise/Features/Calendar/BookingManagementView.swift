import SwiftUI

struct BookingRouteView: View {
    let repository: OwnerRepository
    let bookingID: Int64
    let timeZoneIdentifier: String
    let didChange: @MainActor () async -> Void

    @State private var booking: Booking?
    @State private var loading = false
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if let booking {
                BookingManagementView(
                    repository: repository,
                    booking: booking,
                    timeZoneIdentifier: timeZoneIdentifier,
                    didChange: didChange
                )
            } else if loading {
                ProgressView("Opening booking…")
            } else if let errorMessage {
                ContentUnavailableView {
                    Label("Booking unavailable", systemImage: "calendar.badge.exclamationmark")
                } description: {
                    Text(errorMessage)
                } actions: {
                    Button("Try again") { Task { await load() } }
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        guard !loading, booking == nil else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            let resource = try await repository.bookingDetail(id: bookingID)
            guard resource.value.id == bookingID else {
                throw APIError.unexpectedResponse
            }
            booking = resource.value
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct BookingManagementView: View {
    @Environment(\.dismiss) private var dismiss

    let repository: OwnerRepository
    let initialBooking: Booking
    let timeZoneIdentifier: String
    let didChange: @MainActor () async -> Void

    @State private var booking: Booking
    @State private var etag: String?
    @State private var selectedDay: Date
    @State private var slots: [BookingSlot] = []
    @State private var selectedSlot: Date?
    @State private var cancelReason = ""
    @State private var loading = false
    @State private var loadingSlots = false
    @State private var mutating = false
    @State private var errorMessage: String?
    @State private var confirmation: BookingAction?
    @State private var submittedCancel: BookingCancelRequest?
    @State private var submittedReschedule: BookingRescheduleRequest?
    @State private var commandKey = UUID()

    init(
        repository: OwnerRepository,
        booking: Booking,
        timeZoneIdentifier: String,
        didChange: @escaping @MainActor () async -> Void
    ) {
        self.repository = repository
        initialBooking = booking
        self.timeZoneIdentifier = timeZoneIdentifier
        self.didChange = didChange
        _booking = State(initialValue: booking)
        _selectedDay = State(initialValue: booking.startAt)
    }

    var body: some View {
        Form {
            Section("Booking") {
                LabeledContent("Event", value: booking.eventName)
                LabeledContent("Client", value: booking.name)
                LabeledContent("Current time") {
                    Text(booking.startAt, format: .dateTime.year().month().day().hour().minute())
                }
                if let notes = booking.notes, !notes.isEmpty {
                    Text(notes).foregroundStyle(.secondary)
                }
            }

            Section("Reschedule") {
                DatePicker("Day", selection: $selectedDay, displayedComponents: .date)
                    .environment(\.timeZone, timeZone)
                if loadingSlots {
                    ProgressView("Checking availability…")
                } else if slots.isEmpty {
                    Text("No available times on this day.")
                        .foregroundStyle(.secondary)
                } else {
                    Picker("New time", selection: $selectedSlot) {
                        Text("Choose a time").tag(Optional<Date>.none)
                        ForEach(slots) { slot in
                            Text(timeFormatter.string(from: slot.startAt))
                                .tag(Optional(slot.startAt))
                        }
                    }
                }
                Button("Review reschedule", systemImage: "calendar.badge.clock") {
                    confirmation = .reschedule
                }
                .disabled(mutating || etag == nil || selectedSlot == nil)
            }

            Section("Cancel booking") {
                TextField("Reason (optional)", text: $cancelReason, axis: .vertical)
                    .lineLimit(2...5)
                Button("Review cancellation", systemImage: "calendar.badge.minus", role: .destructive) {
                    confirmation = .cancel
                }
                .disabled(mutating || etag == nil)
            }

            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red)
                    Button("Reload latest booking") {
                        Task { await load() }
                    }
                }
            }
        }
        .navigationTitle("Manage Booking")
        .navigationBarTitleDisplayMode(.inline)
        .disabled(loading)
        .overlay {
            if loading || mutating {
                ProgressView().controlSize(.large)
            }
        }
        .alert(item: $confirmation) { action in
            Alert(
                title: Text(action.title),
                message: Text(action.message),
                primaryButton: action == .cancel
                    ? .destructive(Text("Cancel booking")) { Task { await cancel() } }
                    : .default(Text("Reschedule")) { Task { await reschedule() } },
                secondaryButton: .cancel()
            )
        }
        .task {
            if etag == nil {
                await load()
            }
        }
        .task(id: selectedDayKey) {
            await loadSlots()
        }
    }

    private var timeZone: TimeZone {
        TimeZone(identifier: timeZoneIdentifier) ?? .current
    }

    private var selectedDayKey: String {
        dayFormatter.string(from: selectedDay)
    }

    private var dayFormatter: DateFormatter {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }

    private var timeFormatter: DateFormatter {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        formatter.timeZone = timeZone
        return formatter
    }

    private func load() async {
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            let resource = try await repository.bookingDetail(id: initialBooking.id)
            booking = resource.value
            etag = resource.etag
            submittedCancel = nil
            submittedReschedule = nil
            commandKey = UUID()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadSlots() async {
        loadingSlots = true
        errorMessage = nil
        selectedSlot = nil
        defer { loadingSlots = false }
        do {
            let result = try await repository.bookingSlots(
                bookingID: initialBooking.id,
                day: LocalDate(rawValue: selectedDayKey),
                timeZone: timeZoneIdentifier
            )
            slots = result.items
        } catch is CancellationError {
            return
        } catch {
            slots = []
            errorMessage = error.localizedDescription
        }
    }

    private func cancel() async {
        guard let etag else { return }
        let payload = BookingCancelRequest(
            reason: cancelReason.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        if submittedCancel != payload {
            submittedCancel = payload
            commandKey = UUID()
        }
        mutating = true
        errorMessage = nil
        defer { mutating = false }
        do {
            _ = try await repository.cancelBooking(
                id: initialBooking.id,
                reason: payload.reason,
                etag: etag,
                idempotencyKey: commandKey
            )
            await didChange()
            dismiss()
        } catch let APIError.conflict(_) {
            submittedCancel = nil
            errorMessage = "The booking changed. Reload it before cancelling."
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func reschedule() async {
        guard let etag, let selectedSlot else { return }
        let payload = BookingRescheduleRequest(
            startAt: selectedSlot,
            timeZone: timeZoneIdentifier
        )
        if submittedReschedule != payload {
            submittedReschedule = payload
            commandKey = UUID()
        }
        mutating = true
        errorMessage = nil
        defer { mutating = false }
        do {
            _ = try await repository.rescheduleBooking(
                id: initialBooking.id,
                startAt: payload.startAt,
                timeZone: payload.timeZone,
                etag: etag,
                idempotencyKey: commandKey
            )
            await didChange()
            dismiss()
        } catch let APIError.conflict(_) {
            submittedReschedule = nil
            errorMessage = "That slot or booking changed. Reload and choose another time."
            await loadSlots()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private enum BookingAction: String, Identifiable {
    case cancel
    case reschedule

    var id: String { rawValue }

    var title: String {
        switch self {
        case .cancel: "Cancel this booking?"
        case .reschedule: "Reschedule this booking?"
        }
    }

    var message: String {
        switch self {
        case .cancel:
            "The client will be notified and the calendar hold will be removed."
        case .reschedule:
            "The old time will be released and the client will receive replacement calendar details."
        }
    }
}
