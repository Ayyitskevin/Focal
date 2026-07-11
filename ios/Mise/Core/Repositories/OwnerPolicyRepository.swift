import Foundation

extension OwnerRepository {
    func bookingDetail(id: Int64) async throws -> EditableResource<Booking> {
        try await editableBooking(MiseEndpoints.Scheduling.detail(id: id))
    }

    func bookingSlots(
        bookingID: Int64,
        day: LocalDate,
        timeZone: String
    ) async throws -> BookingSlots {
        try await sendWithMetadata(
            MiseEndpoints.Scheduling.slots(
                bookingID: bookingID,
                day: day,
                timeZone: timeZone
            )
        ).value
    }

    func cancelBooking(
        id: Int64,
        reason: String,
        etag: String,
        idempotencyKey: UUID
    ) async throws -> EditableResource<Booking> {
        let result = try await editableBooking(
            MiseEndpoints.Scheduling.cancel(
                bookingID: id,
                body: BookingCancelRequest(reason: reason),
                etag: etag,
                idempotencyKey: idempotencyKey
            )
        )
        await reconcileBooking(result.value, removing: id)
        return result
    }

    func rescheduleBooking(
        id: Int64,
        startAt: Date,
        timeZone: String,
        etag: String,
        idempotencyKey: UUID
    ) async throws -> EditableResource<Booking> {
        let result = try await editableBooking(
            MiseEndpoints.Scheduling.reschedule(
                bookingID: id,
                body: BookingRescheduleRequest(startAt: startAt, timeZone: timeZone),
                etag: etag,
                idempotencyKey: idempotencyKey
            )
        )
        await reconcileBooking(result.value, removing: id)
        return result
    }

    private func editableBooking<Value: Codable & Sendable>(
        _ endpoint: APIEndpoint<Value>
    ) async throws -> EditableResource<Value> {
        let response = try await sendWithMetadata(endpoint)
        guard let etag = response.metadata.etag, !etag.isEmpty else {
            throw OwnerRepositoryError.missingEntityTag
        }
        return EditableResource(value: response.value, etag: etag)
    }

    private func reconcileBooking(_ booking: Booking, removing oldID: Int64) async {
        _ = try? await cache.update("bookings.v1", as: [Booking].self) { current in
            var values = current.filter { $0.id != oldID && $0.id != booking.id }
            if booking.status == .confirmed {
                values.append(booking)
                values.sort { ($0.startAt, $0.id) < ($1.startAt, $1.id) }
            }
            return values
        }
        try? await cache.remove("dashboard.v1")
    }
}
