import XCTest
@testable import Mise

final class PolicyMutationEndpointTests: XCTestCase {
    func testProposalDecisionCarriesExactVersionAndIdempotencyKey() {
        let key = UUID(uuidString: "95720AFB-5A83-451E-9028-A875CFF90633")!

        let endpoint = MiseEndpoints.ClientDelivery.decideProposal(
            accept: true,
            etag: #""proposal-v4""#,
            idempotencyKey: key
        )

        XCTAssertEqual(endpoint.method, .post)
        XCTAssertEqual(endpoint.path, "/api/v1/client/proposal/accept")
        XCTAssertEqual(endpoint.headers["If-Match"], #""proposal-v4""#)
        XCTAssertEqual(endpoint.idempotencyKey, key)
    }

    func testBookingCancelEncodesReasonAndConcurrencyHeaders() throws {
        let key = UUID(uuidString: "5A43577D-E75C-42B8-9537-A0D752459243")!
        let body = BookingCancelRequest(reason: "Owner conflict")

        let endpoint = try MiseEndpoints.Scheduling.cancel(
            bookingID: 12,
            body: body,
            etag: #""booking-v2""#,
            idempotencyKey: key
        )

        XCTAssertEqual(endpoint.path, "/api/v1/bookings/12/cancel")
        XCTAssertEqual(endpoint.headers["If-Match"], #""booking-v2""#)
        XCTAssertEqual(endpoint.idempotencyKey, key)
        XCTAssertEqual(
            try MiseJSON.decoder().decode(BookingCancelRequest.self, from: XCTUnwrap(endpoint.body)),
            body
        )
    }

    func testBookingRescheduleEncodesRFC3339Instant() throws {
        let key = UUID(uuidString: "F033DE90-E835-4BF7-A4BB-A76755E04512")!
        let start = Date(timeIntervalSince1970: 1_783_769_400)
        let body = BookingRescheduleRequest(startAt: start, timeZone: "America/New_York")

        let endpoint = try MiseEndpoints.Scheduling.reschedule(
            bookingID: 12,
            body: body,
            etag: #""booking-v2""#,
            idempotencyKey: key
        )
        let decoded = try MiseJSON.decoder().decode(
            BookingRescheduleRequest.self,
            from: XCTUnwrap(endpoint.body)
        )

        XCTAssertEqual(endpoint.path, "/api/v1/bookings/12/reschedule")
        XCTAssertEqual(endpoint.headers["If-Match"], #""booking-v2""#)
        XCTAssertEqual(decoded, body)
    }

    func testBookingSlotsUseStudioDayAndTimeZone() {
        let endpoint = MiseEndpoints.Scheduling.slots(
            bookingID: 12,
            day: LocalDate(rawValue: "2026-07-11"),
            timeZone: "America/New_York"
        )

        XCTAssertEqual(endpoint.path, "/api/v1/bookings/12/slots")
        XCTAssertEqual(
            endpoint.queryItems,
            [
                APIQueryItem(name: "day", value: "2026-07-11"),
                APIQueryItem(name: "time_zone", value: "America/New_York"),
            ]
        )
    }
}
