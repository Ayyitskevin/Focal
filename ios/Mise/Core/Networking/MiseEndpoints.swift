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

    enum Documents {
        static func decideProposal(
            id: Int64,
            accept: Bool,
            idempotencyKey: UUID
        ) -> APIEndpoint<Proposal> {
            APIEndpoint(
                method: .post,
                path: "/api/v1/proposals/\(id)/\(accept ? "accept" : "decline")",
                idempotencyKey: idempotencyKey
            )
        }

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

        static func createBooking(
            _ body: BookingCreateRequest,
            idempotencyKey: UUID
        ) throws -> APIEndpoint<Booking> {
            try .json(
                method: .post,
                path: "/api/v1/bookings",
                body: body,
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

    /// Shared-client (guest principal) resources — Milestone 3.
    enum Client {
        static let home = APIEndpoint<ClientHomeSummary>(
            method: .get,
            path: "/api/v1/client/home"
        )

        static func galleries(
            cursor: String? = nil,
            limit: Int = 25
        ) -> APIEndpoint<APIPage<GallerySummary>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/client/galleries",
                queryItems: [
                    APIQueryItem(name: "cursor", value: cursor),
                    APIQueryItem(name: "limit", value: String(min(max(limit, 1), 100))),
                ]
            )
        }

        static func galleryDetail(id: Int64, etag: String? = nil) -> APIEndpoint<GalleryDetail> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/client/galleries/\(id)",
                etag: etag
            )
        }

        static func bookings(
            cursor: String? = nil,
            limit: Int = 25
        ) -> APIEndpoint<APIPage<Booking>> {
            APIEndpoint(
                method: .get,
                path: "/api/v1/client/bookings",
                queryItems: [
                    APIQueryItem(name: "cursor", value: cursor),
                    APIQueryItem(name: "limit", value: String(min(max(limit, 1), 100))),
                ]
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

struct BookingCreateRequest: Codable, Hashable, Sendable {
    let eventTypeID: Int64
    let startAt: Date
    let timeZone: String
    let name: String
    let email: String
    let phone: String?
    let notes: String?
}
