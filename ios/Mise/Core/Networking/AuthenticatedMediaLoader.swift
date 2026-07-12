import Foundation

/// Fetches gallery media bytes with the same bearer session as `APIClient`,
/// without folding binary responses into its JSON-only decode path.
///
/// Media links are absolute, same-origin URLs built by the backend
/// (`app/mobile_media.py`), so this loader only ever attaches a bearer header
/// and retries once on a 401 -- it does not renegotiate origin or method.
actor AuthenticatedMediaLoader {
    private let session: URLSession
    private let authorizer: any RequestAuthorizing

    init(session: URLSession, authorizer: any RequestAuthorizing) {
        self.session = session
        self.authorizer = authorizer
    }

    func data(for url: URL) async throws -> Data {
        guard let token = try await authorizer.bearerToken() else {
            throw APIError.unauthenticated(nil)
        }
        return try await fetch(url: url, bearerToken: token, mayRefresh: true)
    }

    private func fetch(url: URL, bearerToken: String, mayRefresh: Bool) async throws -> Data {
        var request = URLRequest(url: url)
        request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
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

        if httpResponse.statusCode == 401 {
            if mayRefresh,
               let refreshed = try await authorizer.refreshBearerToken(rejectedToken: bearerToken)
            {
                return try await fetch(url: url, bearerToken: refreshed, mayRefresh: false)
            }
            await authorizer.invalidate()
            throw APIError.unauthenticated(nil)
        }

        guard (200..<300).contains(httpResponse.statusCode) else {
            throw APIError.http(status: httpResponse.statusCode, problem: nil)
        }
        return data
    }
}
