import XCTest
@testable import Mise

@MainActor
final class AppRouteTests: XCTestCase {
    private let parser = AppRouteParser(platformRoot: URL(string: "https://mise.example")!)

    func testParsesExactHostedOwnerRoutes() {
        XCTAssertEqual(
            parser.parseUniversalLink(URL(string: "https://north.mise.example/app/home")!),
            UniversalLinkTarget(
                origin: URL(string: "https://north.mise.example")!,
                route: .home
            )
        )
        XCTAssertEqual(parser.parsePath("/app/projects/42"), .project(42))
        XCTAssertEqual(parser.parsePath("/app/bookings/9"), .booking(9))
        XCTAssertEqual(
            parser.parsePath("/app/content/captions/81"),
            .contentCaption(81)
        )
        XCTAssertEqual(
            parser.parsePath("/app/galleries/7/assets/11"),
            .gallery(id: 7, assetID: 11)
        )
    }

    func testRejectsMalformedAndUnsupportedLinks() {
        let invalid = [
            "http://north.mise.example/app/home",
            "https://evil.example/app/home",
            "https://mise.example.evil.example/app/home",
            "https://north.mise.example:444/app/home",
            "https://user@north.mise.example/app/home",
            "https://north.mise.example/app/home?tenant=other",
            "https://north.mise.example/app/home#fragment",
            "https://north.mise.example/app/home/",
            "https://north.mise.example/app/%68ome",
            "https://north.mise.example/app/invoices/1",
            "https://north.mise.example/app/bookings/0",
            "https://north.mise.example/app/bookings/-1",
            "https://north.mise.example/app/bookings/9223372036854775808",
            "https://north.mise.example/app/content/captions/0",
            "https://north.mise.example/app/content/captions/-1",
            "https://north.mise.example/app/content/captions/1/extra",
        ]
        for value in invalid {
            XCTAssertNil(parser.parseUniversalLink(URL(string: value)!), value)
        }
    }

    func testStrictPayloadDecoderRejectsUnknownFieldsAndVersion() throws {
        var payload = validPayload()
        XCTAssertNotNil(NotificationPayloadDecoder.decode(["mise": payload]))

        payload["unexpected"] = true
        XCTAssertNil(NotificationPayloadDecoder.decode(["mise": payload]))

        payload = validPayload()
        payload["version"] = 2
        XCTAssertNil(NotificationPayloadDecoder.decode(["mise": payload]))

        payload = validPayload()
        payload["workspace_cache_namespace"] = String(repeating: "a", count: 256)
        XCTAssertNil(NotificationPayloadDecoder.decode(["mise": payload]))
    }

    func testNotificationQueuesWhileLockedThenNavigatesOnlyForExactAudience() {
        let router = AppRouter(parser: parser)
        let session = ownerSession(origin: "https://north.mise.example")
        let envelope = notification(route: "/app/bookings/9")

        router.authenticationDidChange(.locked(session))
        router.receiveNotification(envelope)
        XCTAssertNil(router.navigationRequest)

        router.authenticationDidChange(.signedIn(session))
        XCTAssertEqual(router.navigationRequest?.route, .booking(9))
    }

    func testForegroundPresentationRejectsUnsupportedPayloadVersion() {
        let router = AppRouter(parser: parser)
        router.authenticationDidChange(.signedIn(ownerSession()))
        let current = notification(route: "/app/home")
        let unsupported = NotificationEnvelope(
            version: 2,
            eventID: current.eventID,
            workspaceOrigin: current.workspaceOrigin,
            workspaceCacheNamespace: current.workspaceCacheNamespace,
            principalKind: current.principalKind,
            principalID: current.principalID,
            route: current.route
        )

        XCTAssertFalse(router.shouldPresent(unsupported))
    }

    func testForegroundPresentationAllowsExactAudienceWhileBiometricallyLocked() {
        let router = AppRouter(parser: parser)
        let envelope = notification(route: "/app/home")

        router.authenticationDidChange(.locked(ownerSession()))

        XCTAssertTrue(router.shouldPresent(envelope))
    }

    func testMalformedRouteDoesNotPoisonCorrectedEventRetry() {
        let router = AppRouter(parser: parser)
        router.authenticationDidChange(.signedIn(ownerSession()))
        let invalid = notification(route: "/app/unsupported")
        let corrected = NotificationEnvelope(
            version: invalid.version,
            eventID: invalid.eventID,
            workspaceOrigin: invalid.workspaceOrigin,
            workspaceCacheNamespace: invalid.workspaceCacheNamespace,
            principalKind: invalid.principalKind,
            principalID: invalid.principalID,
            route: "/app/home"
        )

        for _ in 0..<150 {
            router.receiveNotification(notification(route: "/app/unsupported"))
        }
        router.receiveNotification(invalid)
        router.receiveNotification(corrected)

        XCTAssertEqual(router.navigationRequest?.route, .home)
    }

    func testNotificationRejectsWorkspacePrincipalAndScopeMismatch() {
        let envelope = notification(route: "/app/projects/42")
        let mismatches = [
            ownerSession(origin: "https://other.mise.example"),
            ownerSession(origin: "https://north.mise.example", cacheNamespace: "tenant_other"),
            ownerSession(origin: "https://north.mise.example", principalID: "another_owner"),
            ownerSession(origin: "https://north.mise.example", scopes: ["studio:write"]),
            ownerSession(
                origin: "https://north.mise.example",
                principalKind: .galleryGuest,
                scopes: ["gallery:1:read"]
            ),
        ]

        for session in mismatches {
            let router = AppRouter(parser: parser)
            router.authenticationDidChange(.signedIn(session))
            router.receiveNotification(envelope)
            XCTAssertNil(router.navigationRequest)
            XCTAssertFalse(router.shouldPresent(envelope))
        }
    }

    func testDuplicateEventIDPublishesOnlyOnce() {
        let router = AppRouter(parser: parser)
        router.authenticationDidChange(.signedIn(ownerSession()))
        let envelope = notification(route: "/app/home")

        router.receiveNotification(envelope)
        let first = try? XCTUnwrap(router.navigationRequest)
        if let first { router.consumeNavigation(first.id) }
        router.receiveNotification(envelope)

        XCTAssertNil(router.navigationRequest)
    }

    func testCrossWorkspaceUniversalLinkRequiresDeliberateSwitch() {
        let router = AppRouter(parser: parser)
        router.authenticationDidChange(.signedIn(ownerSession()))

        router.receiveUniversalLink(
            URL(string: "https://other.mise.example/app/bookings/9")!
        )

        XCTAssertNil(router.navigationRequest)
        XCTAssertEqual(router.workspaceSwitchRequest?.origin.host, "other.mise.example")
    }

    func testColdLaunchUniversalLinkSurfacesAfterRestoreFindsNoSession() {
        let router = AppRouter(parser: parser)
        router.receiveUniversalLink(
            URL(string: "https://north.mise.example/app/bookings/9")!
        )

        XCTAssertNil(router.workspaceSwitchRequest)
        router.authenticationDidChange(.signedOut)

        XCTAssertEqual(router.workspaceSwitchRequest?.route, .booking(9))
        XCTAssertEqual(router.workspaceSwitchRequest?.origin.host, "north.mise.example")
    }

    private func validPayload() -> [String: Any] {
        [
            "version": 1,
            "event_id": UUID().uuidString.lowercased(),
            "workspace_origin": "https://north.mise.example",
            "workspace_cache_namespace": "tenant_north",
            "principal_kind": "studio_owner",
            "principal_id": "studio_owner",
            "route": "/app/home",
        ]
    }

    private func notification(route: String) -> NotificationEnvelope {
        NotificationEnvelope(
            version: 1,
            eventID: UUID(),
            workspaceOrigin: URL(string: "https://north.mise.example")!,
            workspaceCacheNamespace: "tenant_north",
            principalKind: .studioOwner,
            principalID: "studio_owner",
            route: route
        )
    }

    private func ownerSession(
        origin: String = "https://north.mise.example",
        cacheNamespace: String = "tenant_north",
        principalID: String = "studio_owner",
        principalKind: PrincipalKind = .studioOwner,
        scopes: [String] = ["studio:read", "studio:write"]
    ) -> CurrentSession {
        CurrentSession(
            workspace: WorkspaceContext(
                cacheNamespace: cacheNamespace,
                slug: "north",
                displayName: "North",
                apiBaseURL: URL(string: origin)!,
                brandAccentHex: nil,
                timeZone: "America/New_York",
                currencyCode: "USD"
            ),
            principal: Principal(
                id: principalID,
                kind: principalKind,
                displayName: "Owner",
                email: nil,
                scopes: scopes
            ),
            sessionID: "session-1"
        )
    }
}
