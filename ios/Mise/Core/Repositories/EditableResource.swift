import Foundation

/// A server representation paired with the strong version required for safe mutation.
struct EditableResource<Value: Codable & Sendable>: Sendable {
    let value: Value
    let etag: String
}
