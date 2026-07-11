import XCTest
@testable import Mise

final class APIClientTests: XCTestCase {
    override func tearDown() {
        MockURLProtocol.clearHandler()
        super.tearDown()
    }

    func testAuthenticatedRequestRetriesOnceWithRotatedToken() async throws {
        let requestCount = LockedBox(0)
        let receivedTokens = LockedBox<[String]>([])

        MockURLProtocol.setHandler { request in
            let count = requestCount.withValue {
                $0 += 1
                return $0
            }
            receivedTokens.withValue {
                $0.append(request.value(forHTTPHeaderField: "Authorization") ?? "")
            }

            if count == 1 {
                return (
                    Self.response(
                        url: request.url!,
                        status: 401,
                        headers: ["Content-Type": "application/problem+json"]
                    ),
                    Data(#"{"detail":"expired"}"#.utf8)
                )
            }
            return (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Type": "application/json"]
                ),
                Self.authSessionJSON
            )
        }

        let authorizer = StubAuthorizer(initial: "old", refreshed: "new")
        let client = makeClient(authorizer: authorizer)
        let endpoint = APIEndpoint<AuthSession>(
            method: .get,
            path: "/api/v1/test/session"
        )
        let session = try await client.send(endpoint)
        let refreshCount = await authorizer.refreshCount()

        XCTAssertEqual(session.accessToken, "response-access")
        XCTAssertEqual(receivedTokens.withValue { $0 }, ["Bearer old", "Bearer new"])
        XCTAssertEqual(refreshCount, 1)
    }

    func testFinalUnauthorizedInvalidatesSession() async throws {
        MockURLProtocol.setHandler { request in
            (
                Self.response(
                    url: request.url!,
                    status: 401,
                    headers: ["Content-Type": "application/problem+json"]
                ),
                Data(#"{"detail":"expired"}"#.utf8)
            )
        }

        let authorizer = StubAuthorizer(initial: "old", refreshed: "new")
        let client = makeClient(authorizer: authorizer)
        let endpoint = APIEndpoint<CurrentSession>(
            method: .get,
            path: "/api/v1/me"
        )

        do {
            _ = try await client.send(endpoint)
            XCTFail("Expected the second 401 to end the session.")
        } catch APIError.unauthenticated {
            // Expected.
        }

        let invalidationCount = await authorizer.invalidationCount()
        XCTAssertEqual(invalidationCount, 1)
    }

    func testRejectsUnexpectedRedirect() async throws {
        MockURLProtocol.setHandler { request in
            (
                Self.response(
                    url: request.url!,
                    status: 303,
                    headers: [
                        "Content-Type": "text/html",
                        "Location": "/admin/login",
                    ]
                ),
                Data()
            )
        }

        let client = makeClient(
            authorizer: StubAuthorizer(initial: "token", refreshed: "other")
        )

        do {
            _ = try await client.send(MiseEndpoints.Auth.me)
            XCTFail("Expected the redirect to be rejected.")
        } catch APIError.unexpectedRedirect {
            // Expected.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testAddsIdempotencyAndETagHeaders() async throws {
        let headers = LockedBox<[String: String]>([:])
        let key = UUID(uuidString: "0F961D34-7F9E-4D63-B4D3-8A72CF2F17A1")!

        MockURLProtocol.setHandler { request in
            headers.withValue {
                $0["Idempotency-Key"] = request.value(forHTTPHeaderField: "Idempotency-Key")
                $0["If-None-Match"] = request.value(forHTTPHeaderField: "If-None-Match")
                $0["If-Match"] = request.value(forHTTPHeaderField: "If-Match")
            }
            return (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: ["Content-Type": "application/json"]
                ),
                Data(#"{"asset_id":201,"selected":true}"#.utf8)
            )
        }

        let client = makeClient(
            authorizer: StubAuthorizer(initial: "token", refreshed: "other")
        )
        let endpoint = APIEndpoint<FavoriteState>(
            method: .put,
            path: "/api/v1/galleries/17/assets/201/favorite",
            headers: ["If-Match": #""entity-9""#],
            idempotencyKey: key,
            etag: #""gallery-42""#
        )

        let result = try await client.send(endpoint)

        XCTAssertTrue(result.selected)
        XCTAssertEqual(headers.withValue { $0["Idempotency-Key"] }, key.uuidString.lowercased())
        XCTAssertEqual(headers.withValue { $0["If-None-Match"] }, #""gallery-42""#)
        XCTAssertEqual(headers.withValue { $0["If-Match"] }, #""entity-9""#)
    }

    func testReturnsCacheMetadataWithDecodedValue() async throws {
        MockURLProtocol.setHandler { request in
            (
                Self.response(
                    url: request.url!,
                    status: 200,
                    headers: [
                        "Content-Type": "application/json",
                        "ETag": #""gallery-43""#,
                        "Last-Modified": "Fri, 10 Jul 2026 14:30:00 GMT",
                    ]
                ),
                Data(#"{"asset_id":201,"selected":true}"#.utf8)
            )
        }
        let client = makeClient()
        let endpoint = APIEndpoint<FavoriteState>(
            method: .get,
            path: "/api/v1/test/cache-metadata",
            authentication: .none
        )
        let startedAt = Date()

        let response = try await client.sendWithMetadata(endpoint)

        XCTAssertTrue(response.value.selected)
        XCTAssertEqual(response.metadata.etag, #""gallery-43""#)
        XCTAssertEqual(response.metadata.lastModified, "Fri, 10 Jul 2026 14:30:00 GMT")
        XCTAssertGreaterThanOrEqual(response.metadata.receivedAt, startedAt)
    }

    func testSurfacesNotModifiedValidatorWithoutDecodingABody() async throws {
        MockURLProtocol.setHandler { request in
            (
                Self.response(
                    url: request.url!,
                    status: 304,
                    headers: ["ETag": #""gallery-44""#]
                ),
                Data()
            )
        }
        let client = makeClient()
        let endpoint = APIEndpoint<FavoriteState>(
            method: .get,
            path: "/api/v1/test/not-modified",
            authentication: .none
        )

        do {
            _ = try await client.sendWithMetadata(endpoint)
            XCTFail("Expected a not-modified result.")
        } catch let APIError.notModified(etag) {
            XCTAssertEqual(etag, #""gallery-44""#)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testOmitsNilQueryItems() async throws {
        let receivedURL = LockedBox<URL?>(nil)
        MockURLProtocol.setHandler { request in
            receivedURL.withValue { $0 = request.url }
            return (
                Self.response(url: request.url!, status: 204, headers: [:]),
                Data()
            )
        }

        let client = makeClient()
        let endpoint = APIEndpoint<EmptyResponse>(
            method: .get,
            path: "/api/v1/items",
            queryItems: [
                APIQueryItem(name: "cursor", value: nil),
                APIQueryItem(name: "limit", value: "25"),
            ],
            authentication: .none
        )

        _ = try await client.send(endpoint)
        let components = try XCTUnwrap(
            URLComponents(url: try XCTUnwrap(receivedURL.withValue { $0 }), resolvingAgainstBaseURL: false)
        )
        XCTAssertEqual(components.queryItems, [URLQueryItem(name: "limit", value: "25")])
    }

    private func makeClient(
        authorizer: (any RequestAuthorizing)? = nil
    ) -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        let session = URLSession(configuration: configuration)

        return APIClient(
            configuration: .init(
                baseURL: URL(string: "https://studio.example.com")!,
                clientVersion: "1.0 (1)"
            ),
            session: session,
            authorizer: authorizer
        )
    }

    private static func response(
        url: URL,
        status: Int,
        headers: [String: String]
    ) -> HTTPURLResponse {
        HTTPURLResponse(
            url: url,
            statusCode: status,
            httpVersion: "HTTP/1.1",
            headerFields: headers
        )!
    }

    private static let authSessionJSON = Data(
        """
        {
          "access_token": "response-access",
          "refresh_token": "response-refresh",
          "token_type": "Bearer",
          "access_token_expires_at": "2026-07-09T22:45:00Z",
          "refresh_token_expires_at": "2026-08-08T22:30:00Z",
          "workspace": {
            "cache_namespace": "tenant_42",
            "slug": "north-star",
            "display_name": "North Star",
            "api_base_url": "https://studio.example.com",
            "brand_accent_hex": "#2F5C45",
            "time_zone": "America/New_York",
            "currency_code": "USD"
          },
          "principal": {
            "id": "studio_owner",
            "kind": "studio_owner",
            "display_name": "North Star",
            "email": "owner@example.com",
            "scopes": ["studio:read"]
          }
        }
        """.utf8
    )
}
private actor StubAuthorizer: RequestAuthorizing {
    private var token: String
    private let refreshed: String
    private var refreshes = 0
    private var invalidations = 0

    init(initial: String, refreshed: String) {
        token = initial
        self.refreshed = refreshed
    }

    func bearerToken() -> String? {
        token
    }

    func refreshBearerToken(rejectedToken: String) -> String? {
        refreshes += 1
        token = refreshed
        return token
    }

    func invalidate() {
        invalidations += 1
    }

    func refreshCount() -> Int {
        refreshes
    }

    func invalidationCount() -> Int {
        invalidations
    }
}
