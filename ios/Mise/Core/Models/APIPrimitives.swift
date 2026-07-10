import Foundation

/// A forward-compatible API value whose unknown strings remain representable.
protocol APIStringValue: RawRepresentable, Codable, Hashable, Sendable
where RawValue == String {
    init(rawValue: String)
}
extension APIStringValue {
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self.init(rawValue: try container.decode(String.self))
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

struct LocalDate: APIStringValue, CustomStringConvertible {
    let rawValue: String

    init(rawValue: String) {
        self.rawValue = rawValue
    }

    var description: String { rawValue }
}

struct YearMonth: APIStringValue, CustomStringConvertible {
    let rawValue: String

    init(rawValue: String) {
        self.rawValue = rawValue
    }

    var description: String { rawValue }
}

struct Money: Codable, Hashable, Sendable {
    let minorUnits: Int64
    let currencyCode: String
}

struct APIPage<Element: Codable & Hashable & Sendable>: Codable, Hashable, Sendable {
    let items: [Element]
    let nextCursor: String?
    let hasMore: Bool
}

struct EmptyResponse: Codable, Hashable, Sendable {
    init() {}
}

/// Tenant-local database IDs overlap. Use this composite key in every local cache.
struct TenantScopedKey: Codable, Hashable, Sendable {
    let cacheNamespace: String
    let resourceKind: String
    let localID: Int64
}
