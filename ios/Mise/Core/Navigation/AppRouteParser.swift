import Foundation

struct AppRouteParser: Sendable {
    private let hostedRootHost: String

    init(platformRoot: URL) {
        hostedRootHost = platformRoot.host?.lowercased() ?? ""
    }

    func parseUniversalLink(_ url: URL) -> UniversalLinkTarget? {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == "https",
              let host = components.host?.lowercased(),
              isHostedDomain(host),
              components.port == nil,
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              !components.percentEncodedPath.contains("%"),
              let route = parsePath(components.path)
        else {
            return nil
        }

        var origin = URLComponents()
        origin.scheme = "https"
        origin.host = host
        guard let originURL = origin.url else { return nil }
        return UniversalLinkTarget(origin: originURL, route: route)
    }

    func parsePath(_ path: String) -> OwnerRoute? {
        guard path.first == "/", path.count > 1,
              !path.hasSuffix("/"), !path.contains("//")
        else {
            return nil
        }
        let parts = path.split(separator: "/", omittingEmptySubsequences: true)

        if parts.count == 2, parts[0] == "app", parts[1] == "home" {
            return .home
        }
        if parts.count == 3, parts[0] == "app", parts[1] == "projects",
           let id = positiveID(parts[2]) {
            return .project(id)
        }
        if parts.count == 3, parts[0] == "app", parts[1] == "bookings",
           let id = positiveID(parts[2]) {
            return .booking(id)
        }
        if parts.count == 3, parts[0] == "app", parts[1] == "galleries",
           let id = positiveID(parts[2]) {
            return .gallery(id: id, assetID: nil)
        }
        if parts.count == 4, parts[0] == "app", parts[1] == "content",
           parts[2] == "captions", let id = positiveID(parts[3]) {
            return .contentCaption(id)
        }
        if parts.count == 5, parts[0] == "app", parts[1] == "galleries",
           parts[3] == "assets", let galleryID = positiveID(parts[2]),
           let assetID = positiveID(parts[4]) {
            return .gallery(id: galleryID, assetID: assetID)
        }
        return nil
    }

    func canonicalHTTPSOrigin(_ url: URL) -> URL? {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == "https",
              let host = components.host?.lowercased(),
              components.port == nil,
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              components.path.isEmpty || components.path == "/"
        else {
            return nil
        }
        var origin = URLComponents()
        origin.scheme = "https"
        origin.host = host
        return origin.url
    }

    private func isHostedDomain(_ host: String) -> Bool {
        !hostedRootHost.isEmpty
            && (host == hostedRootHost || host.hasSuffix(".\(hostedRootHost)"))
    }

    private func positiveID(_ value: Substring) -> Int64? {
        let bytes = value.utf8
        guard !bytes.isEmpty, bytes.allSatisfy({ (48...57).contains($0) }),
              let id = Int64(value), id > 0
        else {
            return nil
        }
        return id
    }
}
