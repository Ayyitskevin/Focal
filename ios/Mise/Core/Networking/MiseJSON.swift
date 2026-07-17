import Foundation

enum MiseJSON {
    static func wholeSecondUTCDate(_ date: Date) -> Date {
        Date(timeIntervalSince1970: floor(date.timeIntervalSince1970))
    }

    static func wholeSecondUTCString(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        return formatter.string(from: wholeSecondUTCDate(date))
    }

    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .custom { codingPath in
            let key = codingPath[codingPath.count - 1]
            return AnyCodingKey(stringValue: propertyName(for: key.stringValue))
        }
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let value = try container.decode(String.self)

            let fractional = ISO8601DateFormatter()
            fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = fractional.date(from: value) {
                return date
            }

            let wholeSeconds = ISO8601DateFormatter()
            wholeSeconds.formatOptions = [.withInternetDateTime]
            if let date = wholeSeconds.date(from: value) {
                return date
            }

            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Expected an RFC 3339 timestamp, received \(value)."
            )
        }
        return decoder
    }

    static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .custom { codingPath in
            let key = codingPath[codingPath.count - 1]
            return AnyCodingKey(stringValue: wireName(for: key.stringValue))
        }
        encoder.dateEncodingStrategy = .custom { date, encoder in
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            var container = encoder.singleValueContainer()
            try container.encode(formatter.string(from: date))
        }
        return encoder
    }

    private static func propertyName(for wireKey: String) -> String {
        let parts = wireKey.split(separator: "_", omittingEmptySubsequences: true)
        guard parts.count > 1 else { return wireKey }

        let initialisms = Set(["ai", "api", "etag", "id", "ids", "ip", "pin", "url", "uri", "usd", "utc"])
        var result = String(parts[0]).lowercased()

        for part in parts.dropFirst() {
            let component = String(part).lowercased()
            if initialisms.contains(component) {
                switch component {
                case "etag": result += "ETag"
                case "ids": result += "IDs"
                default: result += component.uppercased()
                }
            } else {
                result += component.prefix(1).uppercased() + component.dropFirst()
            }
        }
        return result
    }

    private static func wireName(for propertyKey: String) -> String {
        let initialisms = ["ETag", "IDs", "URL", "URI", "USD", "UTC", "PIN", "API", "AI", "ID", "IP"]
        var expanded = propertyKey
        for initialism in initialisms {
            expanded = expanded.replacingOccurrences(
                of: initialism,
                with: "_\(initialism.lowercased())_"
            )
        }
        expanded = expanded.replacingOccurrences(
            of: #"([a-z0-9])([A-Z])"#,
            with: "$1_$2",
            options: .regularExpression
        )
        return expanded
            .split(separator: "_", omittingEmptySubsequences: true)
            .map { $0.lowercased() }
            .joined(separator: "_")
    }
}
private struct AnyCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init(stringValue: String) {
        self.stringValue = stringValue
        intValue = nil
    }

    init(intValue: Int) {
        stringValue = String(intValue)
        self.intValue = intValue
    }
}
