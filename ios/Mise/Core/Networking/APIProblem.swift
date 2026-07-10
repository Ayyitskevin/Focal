import Foundation

struct FieldViolation: Codable, Hashable, Sendable {
    let path: [String]
    let message: String
    let code: String?
}
struct APIProblem: Codable, Hashable, Sendable {
    let type: String?
    let title: String?
    let status: Int?
    let code: String?
    let detail: String?
    let requestID: String?
    let errors: [FieldViolation]

    init(
        type: String? = nil,
        title: String? = nil,
        status: Int? = nil,
        code: String? = nil,
        detail: String? = nil,
        requestID: String? = nil,
        errors: [FieldViolation] = []
    ) {
        self.type = type
        self.title = title
        self.status = status
        self.code = code
        self.detail = detail
        self.requestID = requestID
        self.errors = errors
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        type = try container.decodeIfPresent(String.self, forKey: .type)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        status = try container.decodeIfPresent(Int.self, forKey: .status)
        code = try container.decodeIfPresent(String.self, forKey: .code)
        requestID = try container.decodeIfPresent(String.self, forKey: .requestID)

        if let message = try? container.decode(String.self, forKey: .detail) {
            detail = message
            errors = (try? container.decode([FieldViolation].self, forKey: .errors)) ?? []
        } else if let validation = try? container.decode(
            [FastAPIValidationItem].self,
            forKey: .detail
        ) {
            detail = "One or more fields are invalid."
            errors = validation.map(\.fieldViolation)
        } else {
            detail = nil
            errors = (try? container.decode([FieldViolation].self, forKey: .errors)) ?? []
        }
    }

    var bestMessage: String {
        detail ?? title ?? "The server could not complete the request."
    }

    func addingRequestID(_ fallback: String?) -> APIProblem {
        APIProblem(
            type: type,
            title: title,
            status: status,
            code: code,
            detail: detail,
            requestID: requestID ?? fallback,
            errors: errors
        )
    }
}

private extension APIProblem {
    enum CodingKeys: String, CodingKey {
        case type
        case title
        case status
        case code
        case detail
        case requestID
        case errors
    }

    struct FastAPIValidationItem: Decodable {
        let location: [PathComponent]
        let message: String
        let type: String?

        enum CodingKeys: String, CodingKey {
            case location = "loc"
            case message = "msg"
            case type
        }

        var fieldViolation: FieldViolation {
            FieldViolation(
                path: location.map(\.stringValue),
                message: message,
                code: type
            )
        }
    }

    enum PathComponent: Decodable {
        case string(String)
        case integer(Int)

        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let string = try? container.decode(String.self) {
                self = .string(string)
            } else {
                self = .integer(try container.decode(Int.self))
            }
        }

        var stringValue: String {
            switch self {
            case let .string(value): value
            case let .integer(value): String(value)
            }
        }
    }
}

enum APIError: Error, Sendable {
    case invalidEndpoint
    case transport(URLError.Code)
    case unexpectedResponse
    case unexpectedRedirect(URL?)
    case unexpectedContentType(String?)
    case decoding(String)
    case notModified(etag: String?)
    case unauthenticated(APIProblem?)
    case forbidden(APIProblem?)
    case subscriptionRequired(APIProblem?)
    case notFound(APIProblem?)
    case gone(APIProblem?)
    case conflict(APIProblem?)
    case validation(APIProblem)
    case rateLimited(retryAfter: TimeInterval?, problem: APIProblem?)
    case server(status: Int, problem: APIProblem?)
    case http(status: Int, problem: APIProblem?)
}

extension APIError: LocalizedError {
    var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            "The app constructed an invalid request."
        case .transport:
            "Mise could not reach the server."
        case .unexpectedResponse:
            "Mise received an invalid server response."
        case .unexpectedRedirect:
            "The API unexpectedly redirected the request."
        case .unexpectedContentType:
            "The server returned an unsupported response."
        case let .decoding(message):
            "Mise could not read the server response: \(message)"
        case .notModified:
            "The cached response is still current."
        case let .unauthenticated(problem):
            problem?.bestMessage ?? "Your session has expired. Sign in again."
        case let .forbidden(problem):
            problem?.bestMessage ?? "You do not have access to this item."
        case let .subscriptionRequired(problem):
            problem?.bestMessage ?? "This studio is temporarily unavailable."
        case let .notFound(problem):
            problem?.bestMessage ?? "That item could not be found."
        case let .gone(problem):
            problem?.bestMessage ?? "That link has expired."
        case let .conflict(problem):
            problem?.bestMessage ?? "The item changed before this action completed."
        case let .validation(problem):
            problem.bestMessage
        case let .rateLimited(_, problem):
            problem?.bestMessage ?? "That was too fast. Try again shortly."
        case let .server(_, problem), let .http(_, problem):
            problem?.bestMessage ?? "The server could not complete the request."
        }
    }
}
