import Foundation

enum MiseEndpoints {
    enum Auth {
        static let tenant = APIEndpoint<TenantDescriptor>(
            method: .get,
            path: "/api/v1/tenant",
            authentication: .none
        )

        static func studioLogin(
            _ body: StudioLoginRequest
        ) throws -> APIEndpoint<AuthSession> {
            try .json(
                method: .post,
                path: "/api/v1/auth/studio/login",
                body: body,
                authentication: .none
            )
        }

        static func refresh(
            _ body: RefreshTokenRequest
        ) throws -> APIEndpoint<AuthSession> {
            try .json(
                method: .post,
                path: "/api/v1/auth/refresh",
                body: body,
                authentication: .none
            )
        }

        static func sharedAccess(
            _ body: SharedAccessUnlockRequest
        ) throws -> APIEndpoint<AuthSession> {
            let path: String
            switch body.kind {
            case .gallery:
                path = "/api/v1/client-auth/gallery/unlock"
            case .portal:
                path = "/api/v1/client-auth/portal/unlock"
            case .workspace:
                path = "/api/v1/client-auth/workspace/unlock"
            case .proposal, .contract, .invoice:
                path = "/api/v1/client-auth/document/exchange"
            default:
                throw APIError.invalidEndpoint
            }
            return try .json(
                method: .post,
                path: path,
                body: body,
                authentication: .none
            )
        }

        static let me = APIEndpoint<CurrentSession>(
            method: .get,
            path: "/api/v1/me"
        )

        static let logout = APIEndpoint<EmptyResponse>(
            method: .post,
            path: "/api/v1/auth/logout"
        )
    }

    enum Devices {
        static func register(
            _ body: DeviceRegistrationRequest
        ) throws -> APIEndpoint<DeviceRegistration> {
            try .json(method: .post, path: "/api/v1/devices", body: body)
        }

        static let current = APIEndpoint<DeviceRegistration>(
            method: .get,
            path: "/api/v1/devices/current"
        )

        static func updatePreferences(
            _ body: NotificationPreferences,
            etag: String
        ) throws -> APIEndpoint<DeviceRegistration> {
            try .json(
                method: .patch,
                path: "/api/v1/devices/current",
                body: NotificationPreferencesUpdate(preferences: body),
                headers: ["If-Match": etag]
            )
        }

        static let unregister = APIEndpoint<EmptyResponse>(
            method: .delete,
            path: "/api/v1/devices/current"
        )
    }

    static let dashboard = APIEndpoint<DashboardSummary>(
        method: .get,
        path: "/api/v1/dashboard"
    )

    enum Clients {
        static func list(
            cursor: String? = nil,
            limit: Int = 25
        ) -> APIEndpoint<APIPage<ClientSummary>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/clients",
                queryItems: pagination(cursor: cursor, limit: limit)
            )
        }


        static func detail(id: Int64) -> APIEndpoint<ClientDetail> {
            APIEndpoint(method: .get, path: "/api/v1/clients/\(id)")
        }

        static func create(
            _ body: ClientMutationRequest,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<ClientDetail> {
            try .json(
                method: .post,
                path: "/api/v1/clients",
                body: body,
                idempotencyKey: idempotencyKey
            )
        }

        static func update(
            id: Int64,
            body: ClientMutationRequest,
            etag: String,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<ClientDetail> {
            try .json(
                method: .patch,
                path: "/api/v1/clients/\(id)",
                body: body,
                headers: ["If-Match": etag],
                idempotencyKey: idempotencyKey
            )
        }
        private static func pagination(cursor: String?, limit: Int) -> [APIQueryItem] {
            [
                APIQueryItem(name: "cursor", value: cursor),
                APIQueryItem(name: "limit", value: String(min(max(limit, 1), 100))),
            ]
        }
    }

    enum Projects {
        static func list(
            cursor: String? = nil,
            limit: Int = 25
        ) -> APIEndpoint<APIPage<ProjectSummary>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/projects",
                queryItems: [
                    APIQueryItem(name: "cursor", value: cursor),
                    APIQueryItem(name: "limit", value: String(min(max(limit, 1), 100))),
                ]
            )
        }


        static func detail(id: Int64) -> APIEndpoint<ProjectDetail> {
            APIEndpoint(method: .get, path: "/api/v1/projects/\(id)")
        }

        static func create(
            _ body: ProjectCreateRequest,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<ProjectDetail> {
            try .json(
                method: .post,
                path: "/api/v1/projects",
                body: body,
                idempotencyKey: idempotencyKey
            )
        }

        static func update(
            id: Int64,
            body: ProjectMutationRequest,
            etag: String,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<ProjectDetail> {
            try .json(
                method: .patch,
                path: "/api/v1/projects/\(id)",
                body: body,
                headers: ["If-Match": etag],
                idempotencyKey: idempotencyKey
            )
        }
        static func proposals(projectID: Int64) -> APIEndpoint<APIPage<Proposal>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/projects/\(projectID)/proposals"
            )
        }

        static func contracts(projectID: Int64) -> APIEndpoint<APIPage<Contract>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/projects/\(projectID)/contracts"
            )
        }

        static func invoices(projectID: Int64) -> APIEndpoint<APIPage<Invoice>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/projects/\(projectID)/invoices"
            )
        }
    }

    enum Tasks {
        static let list = APIEndpoint<TaskCollection>(method: .get, path: "/api/v1/tasks")

        static func detail(id: Int64) -> APIEndpoint<TaskDetail> {
            APIEndpoint(method: .get, path: "/api/v1/tasks/\(id)")
        }

        static func create(
            _ body: TaskCreateRequest,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<TaskDetail> {
            try .json(
                method: .post,
                path: "/api/v1/tasks",
                body: body,
                idempotencyKey: idempotencyKey
            )
        }

        static func update(
            id: Int64,
            body: TaskMutationRequest,
            etag: String,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<TaskDetail> {
            try .json(
                method: .patch,
                path: "/api/v1/tasks/\(id)",
                body: body,
                headers: ["If-Match": etag],
                idempotencyKey: idempotencyKey
            )
        }

        static func delete(
            id: Int64,
            etag: String,
            idempotencyKey: UUID
        ) -> APIEndpoint<TaskDetail> {
            APIEndpoint(
                method: .delete,
                path: "/api/v1/tasks/\(id)",
                headers: ["If-Match": etag],
                idempotencyKey: idempotencyKey
            )
        }
    }

    enum Galleries {
        static func list(
            cursor: String? = nil,
            limit: Int = 25
        ) -> APIEndpoint<APIPage<GallerySummary>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/galleries",
                queryItems: [
                    APIQueryItem(name: "cursor", value: cursor),
                    APIQueryItem(name: "limit", value: String(min(max(limit, 1), 100))),
                ]
            )
        }

        static func detail(id: Int64, etag: String? = nil) -> APIEndpoint<GalleryDetail> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/galleries/\(id)",
                etag: etag
            )
        }

        static func favorite(
            galleryID: Int64,
            assetID: Int64,
            selected: Bool,
            idempotencyKey: UUID
        ) -> APIEndpoint<FavoriteState> {
            APIEndpoint(
                method: selected ? .put : .delete,
                path: "/api/v1/galleries/\(galleryID)/assets/\(assetID)/favorite",
                idempotencyKey: idempotencyKey
            )
        }

        static func cull(galleryID: Int64) -> APIEndpoint<APIPage<CullItem>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/galleries/\(galleryID)/cull"
            )
        }
    }

    enum ClientDelivery {
        static func gallery(etag: String? = nil) -> APIEndpoint<GalleryDetail> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/client/gallery",
                etag: etag
            )
        }

        static func favorite(assetID: Int64, selected: Bool) -> APIEndpoint<FavoriteState> {
            APIEndpoint(
                method: selected ? .put : .delete,
                path: "/api/v1/client/gallery/assets/\(assetID)/favorite"
            )
        }

        static func comments(assetID: Int64) -> APIEndpoint<[GalleryComment]> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/client/gallery/assets/\(assetID)/comments"
            )
        }

        static func addComment(
            assetID: Int64,
            body: GalleryCommentCreateRequest
        ) throws -> APIEndpoint<GalleryComment> {
            try .json(
                method: .post,
                path: "/api/v1/client/gallery/assets/\(assetID)/comments",
                body: body
            )
        }

        static func portal(etag: String? = nil) -> APIEndpoint<ClientPortalSummary> {
            APIEndpoint(method: .get, path: "/api/v1/client/portal", etag: etag)
        }

        static func workspace(etag: String? = nil) -> APIEndpoint<ClientWorkspaceSummary> {
            APIEndpoint(method: .get, path: "/api/v1/client/workspace", etag: etag)
        }

        static func document(etag: String? = nil) -> APIEndpoint<ClientDocumentSummary> {
            APIEndpoint(method: .get, path: "/api/v1/client/document", etag: etag)
        }
        static func decideProposal(
            accept: Bool,
            etag: String,
            idempotencyKey: UUID
        ) -> APIEndpoint<ClientDocumentSummary> {
            APIEndpoint(
                method: .post,
                path: "/api/v1/client/proposal/\(accept ? "accept" : "decline")",
                headers: ["If-Match": etag],
                idempotencyKey: idempotencyKey
            )
        }
    }

    enum Documents {
        static func signContract(
            id: Int64,
            body: ContractSignRequest,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<Contract> {
            try .json(
                method: .post,
                path: "/api/v1/contracts/\(id)/sign",
                body: body,
                idempotencyKey: idempotencyKey
            )
        }

        static func invoiceCheckout(
            id: Int64,
            idempotencyKey: UUID
        ) -> APIEndpoint<InvoiceCheckout> {
            APIEndpoint(
                method: .post,
                path: "/api/v1/invoices/\(id)/checkout",
                idempotencyKey: idempotencyKey
            )
        }
    }

    enum Scheduling {
        static func eventTypes() -> APIEndpoint<APIPage<EventType>> {
            APIEndpoint(method: .get, path: "/api/v1/event-types")
        }

        static func bookings(
            cursor: String? = nil,
            limit: Int = 25
        ) -> APIEndpoint<APIPage<Booking>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/bookings",
                queryItems: [
                    APIQueryItem(name: "cursor", value: cursor),
                    APIQueryItem(name: "limit", value: String(min(max(limit, 1), 100))),
                ]
            )
        }

        static func detail(id: Int64) -> APIEndpoint<Booking> {
            APIEndpoint(method: .get, path: "/api/v1/bookings/\(id)")
        }

        static func slots(
            bookingID: Int64,
            day: LocalDate,
            timeZone: String
        ) -> APIEndpoint<BookingSlots> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/bookings/\(bookingID)/slots",
                queryItems: [
                    APIQueryItem(name: "day", value: day.rawValue),
                    APIQueryItem(name: "time_zone", value: timeZone),
                ]
            )
        }

        static func cancel(
            bookingID: Int64,
            body: BookingCancelRequest,
            etag: String,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<Booking> {
            try .json(
                method: .post,
                path: "/api/v1/bookings/\(bookingID)/cancel",
                body: body,
                headers: ["If-Match": etag],
                idempotencyKey: idempotencyKey
            )
        }

        static func reschedule(
            bookingID: Int64,
            body: BookingRescheduleRequest,
            etag: String,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<Booking> {
            try .json(
                method: .post,
                path: "/api/v1/bookings/\(bookingID)/reschedule",
                body: body,
                headers: ["If-Match": etag],
                idempotencyKey: idempotencyKey
            )
        }
    }

    enum AI {
        static func runs(
            cursor: String? = nil
        ) -> APIEndpoint<APIPage<AIRun>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/ai/runs",
                queryItems: [APIQueryItem(name: "cursor", value: cursor)]
            )
        }
    }
}
struct FavoriteState: Codable, Hashable, Sendable {
    let assetID: Int64
    let selected: Bool
    let sectionSelectedCount: Int?
    let sectionProofTarget: Int?
}

struct ContractSignRequest: Codable, Hashable, Sendable {
    let signerName: String
    let agreed: Bool
    let documentETag: String
}

struct InvoiceCheckout: Codable, Hashable, Sendable {
    let checkoutURL: URL
    let expiresAt: Date
}
