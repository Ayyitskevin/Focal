import Foundation

protocol APIClientProtocol: Sendable {
    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response
}
protocol RequestAuthorizing: Sendable {
    func bearerToken() async throws -> String?
    func refreshBearerToken(rejectedToken: String) async throws -> String?
    func invalidate() async
}

actor APIClient: APIClientProtocol {
    struct Configuration: Sendable {
        let baseURL: URL
        let clientVersion: String
        let timeout: TimeInterval

        init(baseURL: URL, clientVersion: String, timeout: TimeInterval = 30) {
            self.baseURL = baseURL
            self.clientVersion = clientVersion
            self.timeout = timeout
        }
    }

    private let configuration: Configuration
    private let session: URLSession
    private let authorizer: (any RequestAuthorizing)?
    private let redirectDelegate = RedirectRejectingDelegate()

    init(
        configuration: Configuration,
        session: URLSession,
        authorizer: (any RequestAuthorizing)? = nil
    ) {
        self.configuration = configuration
        self.session = session
        self.authorizer = authorizer
    }

    func send<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>
    ) async throws -> Response {
        let token: String?
        switch endpoint.authentication {
        case .none:
            token = nil
        case .bearer:
            guard let authorizer else {
                throw APIError.unauthenticated(nil)
            }
            token = try await authorizer.bearerToken()
            guard token != nil else {
                throw APIError.unauthenticated(nil)
            }
        }

        return try await perform(endpoint, bearerToken: token, mayRefresh: true)
    }

    private func perform<Response: Decodable & Sendable>(
        _ endpoint: APIEndpoint<Response>,
        bearerToken: String?,
        mayRefresh: Bool
    ) async throws -> Response {
        let request = try makeRequest(endpoint, bearerToken: bearerToken)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request, delegate: redirectDelegate)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError {
            throw APIError.transport(error.code)
        } catch {
            throw APIError.transport(.unknown)
        }

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.unexpectedResponse
        }

        if httpResponse.statusCode == 401, endpoint.authentication == .bearer {
            if mayRefresh,
               let rejectedToken = bearerToken,
               let authorizer,
               let refreshedToken = try await authorizer.refreshBearerToken(
                   rejectedToken: rejectedToken
               )
            {
                return try await perform(
                    endpoint,
                    bearerToken: refreshedToken,
                    mayRefresh: false
                )
            }
            await authorizer?.invalidate()
        }

        return try decode(
            Response.self,
            data: data,
            response: httpResponse
        )
    }

    private func makeRequest<Response>(
        _ endpoint: APIEndpoint<Response>,
        bearerToken: String?
    ) throws -> URLRequest {
        guard
            endpoint.path.hasPrefix("/"),
            !endpoint.path.split(separator: "/").contains(".."),
            var components = URLComponents(
                url: configuration.baseURL,
                resolvingAgainstBaseURL: false
            )
        else {
            throw APIError.invalidEndpoint
        }

        let basePath = components.path.hasSuffix("/")
            ? String(components.path.dropLast())
            : components.path
        components.path = basePath + endpoint.path
        let queryItems = endpoint.queryItems.compactMap { item -> URLQueryItem? in
            guard let value = item.value else { return nil }
            return URLQueryItem(name: item.name, value: value)
        }
        components.queryItems = queryItems.isEmpty ? nil : queryItems

        guard let url = components.url else {
            throw APIError.invalidEndpoint
        }

        var request = URLRequest(
            url: url,
            cachePolicy: .useProtocolCachePolicy,
            timeoutInterval: configuration.timeout
        )
        request.httpMethod = endpoint.method.rawValue
        request.httpBody = endpoint.body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(configuration.clientVersion, forHTTPHeaderField: "X-Mise-Client-Version")

        for (name, value) in endpoint.headers
        where !Self.reservedHeaders.contains(name.lowercased()) {
            request.setValue(value, forHTTPHeaderField: name)
        }

        if endpoint.body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let key = endpoint.idempotencyKey {
            request.setValue(key.uuidString.lowercased(), forHTTPHeaderField: "Idempotency-Key")
        }
        if let etag = endpoint.etag {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        if let bearerToken {
            guard bearerToken.rangeOfCharacter(from: .whitespacesAndNewlines) == nil else {
                throw APIError.unauthenticated(nil)
            }
            request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func decode<Response: Decodable & Sendable>(
        _ type: Response.Type,
        data: Data,
        response: HTTPURLResponse
    ) throws -> Response {
        let status = response.statusCode
        let requestID = response.value(forHTTPHeaderField: "X-Request-ID")

        if status == 304 {
            throw APIError.notModified(
                etag: response.value(forHTTPHeaderField: "ETag")
            )
        }

        if (300..<400).contains(status) {
            let location = response.value(forHTTPHeaderField: "Location")
                .flatMap { URL(string: $0, relativeTo: response.url)?.absoluteURL }
            throw APIError.unexpectedRedirect(location)
        }

        if (200..<300).contains(status) {
            if Response.self == EmptyResponse.self, data.isEmpty {
                return EmptyResponse() as! Response
            }

            let contentType = response.value(forHTTPHeaderField: "Content-Type")?.lowercased()
            guard
                contentType?.contains("application/json") == true
                    || contentType?.contains("+json") == true
            else {
                throw APIError.unexpectedContentType(contentType)
            }

            do {
                return try MiseJSON.decoder().decode(Response.self, from: data)
            } catch {
                throw APIError.decoding(String(describing: error))
            }
        }

        let problem = decodeProblem(data)?.addingRequestID(requestID)
        switch status {
        case 401:
            throw APIError.unauthenticated(problem)
        case 402:
            throw APIError.subscriptionRequired(problem)
        case 403:
            throw APIError.forbidden(problem)
        case 404:
            throw APIError.notFound(problem)
        case 409:
            throw APIError.conflict(problem)
        case 410:
            throw APIError.gone(problem)
        case 422:
            throw APIError.validation(
                problem ?? APIProblem(status: status, detail: "The request is invalid.")
            )
        case 429:
            let retryAfter = response.value(forHTTPHeaderField: "Retry-After")
                .flatMap(TimeInterval.init)
            throw APIError.rateLimited(retryAfter: retryAfter, problem: problem)
        case 500...599:
            throw APIError.server(status: status, problem: problem)
        default:
            throw APIError.http(status: status, problem: problem)
        }
    }

    private func decodeProblem(_ data: Data) -> APIProblem? {
        guard !data.isEmpty else { return nil }
        return try? MiseJSON.decoder().decode(APIProblem.self, from: data)
    }

    private static let reservedHeaders = Set([
        "accept",
        "authorization",
        "content-type",
        "host",
        "idempotency-key",
        "if-none-match",
        "x-mise-client-version",
    ])
}

private final class RedirectRejectingDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}
