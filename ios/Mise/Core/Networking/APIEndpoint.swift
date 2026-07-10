import Foundation

enum HTTPMethod: String, Equatable, Sendable {
    case get = "GET"
    case post = "POST"
    case put = "PUT"
    case patch = "PATCH"
    case delete = "DELETE"
}

enum AuthenticationRequirement: Equatable, Sendable {
    case none
    case bearer
}

struct APIQueryItem: Hashable, Sendable {
    let name: String
    let value: String?
}

struct APIEndpoint<Response: Decodable & Sendable>: Sendable {
    let method: HTTPMethod
    let path: String
    let queryItems: [APIQueryItem]
    let headers: [String: String]
    let body: Data?
    let authentication: AuthenticationRequirement
    let idempotencyKey: UUID?
    let etag: String?

    init(
        method: HTTPMethod,
        path: String,
        queryItems: [APIQueryItem] = [],
        headers: [String: String] = [:],
        body: Data? = nil,
        authentication: AuthenticationRequirement = .bearer,
        idempotencyKey: UUID? = nil,
        etag: String? = nil
    ) {
        self.method = method
        self.path = path
        self.queryItems = queryItems
        self.headers = headers
        self.body = body
        self.authentication = authentication
        self.idempotencyKey = idempotencyKey
        self.etag = etag
    }

    static func json<Body: Encodable & Sendable>(
        method: HTTPMethod,
        path: String,
        body: Body,
        headers: [String: String] = [:],
        authentication: AuthenticationRequirement = .bearer,
        idempotencyKey: UUID? = nil,
        etag: String? = nil
    ) throws -> APIEndpoint<Response> {
        var resolvedHeaders = headers
        resolvedHeaders["Content-Type"] = "application/json"
        return APIEndpoint(
            method: method,
            path: path,
            headers: resolvedHeaders,
            body: try MiseJSON.encoder().encode(body),
            authentication: authentication,
            idempotencyKey: idempotencyKey,
            etag: etag
        )
    }

    /// Returns an otherwise identical conditional endpoint.
    /// Keeping validators here prevents feature code from mutating raw headers.
    func revalidating(with etag: String?) -> APIEndpoint<Response> {
        APIEndpoint(
            method: method,
            path: path,
            queryItems: queryItems,
            headers: headers,
            body: body,
            authentication: authentication,
            idempotencyKey: idempotencyKey,
            etag: etag
        )
    }
}
