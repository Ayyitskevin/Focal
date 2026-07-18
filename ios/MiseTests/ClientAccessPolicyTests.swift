import XCTest

@testable import Mise

final class ClientAccessPolicyTests: XCTestCase {
    private struct PrincipalExpectation {
        let label: String
        let kind: PrincipalKind
        let allowedDestinations: Set<ClientDestination>
        let documentMode: ClientDocumentMode
        let grantsClientAccess: Bool
    }

    private let expectations = [
        PrincipalExpectation(
            label: "gallery",
            kind: .galleryGuest,
            allowedDestinations: [.home, .gallery],
            documentMode: .unavailable,
            grantsClientAccess: true
        ),
        PrincipalExpectation(
            label: "portal",
            kind: .portalGuest,
            allowedDestinations: [.home, .gallery, .bookings],
            documentMode: .unavailable,
            grantsClientAccess: true
        ),
        PrincipalExpectation(
            label: "workspace",
            kind: .workspaceGuest,
            allowedDestinations: Set(ClientDestination.allCases),
            documentMode: .projectCollections,
            grantsClientAccess: true
        ),
        PrincipalExpectation(
            label: "document",
            kind: .documentGuest,
            allowedDestinations: [.home, .documents],
            documentMode: .singlePreview,
            grantsClientAccess: true
        ),
        PrincipalExpectation(
            label: "owner",
            kind: .studioOwner,
            allowedDestinations: [],
            documentMode: .unavailable,
            grantsClientAccess: false
        ),
        PrincipalExpectation(
            label: "unknown",
            kind: PrincipalKind(rawValue: "future_guest"),
            allowedDestinations: [],
            documentMode: .unavailable,
            grantsClientAccess: false
        ),
    ]

    func testEveryPrincipalDestinationCellMatchesTheAuthorityMatrix() {
        XCTAssertEqual(expectations.count * ClientDestination.allCases.count, 24)

        for expectation in expectations {
            let policy = ClientAccessPolicy(principalKind: expectation.kind)

            XCTAssertEqual(
                policy.grantsClientAccess,
                expectation.grantsClientAccess,
                expectation.label
            )
            XCTAssertEqual(policy.documentMode, expectation.documentMode, expectation.label)

            for destination in ClientDestination.allCases {
                let shouldAllow = expectation.allowedDestinations.contains(destination)
                XCTAssertEqual(
                    policy.allows(destination),
                    shouldAllow,
                    "\(expectation.label) -> \(destination.rawValue)"
                )

                let route = policy.route(to: destination)
                XCTAssertEqual(
                    route?.target,
                    shouldAllow ? destination : nil,
                    "direct route for \(expectation.label) -> \(destination.rawValue)"
                )
                if let route {
                    guard case let .destination(routedDestination) = route else {
                        XCTFail("A direct destination must not become a document route")
                        continue
                    }
                    XCTAssertEqual(routedDestination, destination)
                }
            }
        }
    }

    func testDestinationLabelsAndIconsRemainAccessibleAndStable() {
        let expected: [(ClientDestination, String, String)] = [
            (.home, "Home", "house"),
            (.gallery, "Gallery", "photo.on.rectangle"),
            (.documents, "Documents", "doc.text"),
            (.bookings, "Bookings", "calendar"),
        ]

        for (destination, title, icon) in expected {
            XCTAssertEqual(destination.title, title)
            XCTAssertEqual(destination.icon, icon)
        }
    }

    func testUnavailableContentUsesTheExactLinkBoundaryCopy() {
        let exact: [(PrincipalKind, ClientDestination, String, String)] = [
            (
                .documentGuest,
                .gallery,
                "Galleries aren’t part of this document link.",
                "This link opens the document shared with you."
            ),
            (
                .galleryGuest,
                .documents,
                "Documents aren’t part of this gallery link.",
                "This link opens the gallery shared with you."
            ),
            (
                .portalGuest,
                .documents,
                "Documents aren’t part of this portal link.",
                "This link covers shared galleries and bookings."
            ),
            (
                .galleryGuest,
                .bookings,
                "Bookings aren’t part of this gallery link.",
                "This link opens the gallery shared with you."
            ),
            (
                .documentGuest,
                .bookings,
                "Bookings aren’t part of this document link.",
                "This link opens the document shared with you."
            ),
        ]

        for (kind, destination, heading, description) in exact {
            assertUnavailableContent(
                policy: ClientAccessPolicy(principalKind: kind),
                destination: destination,
                heading: heading,
                description: description
            )
        }

        for kind in [
            PrincipalKind.studioOwner,
            PrincipalKind(rawValue: "future_guest"),
        ] {
            let policy = ClientAccessPolicy(principalKind: kind)
            for destination in ClientDestination.allCases {
                assertUnavailableContent(
                    policy: policy,
                    destination: destination,
                    heading: "Client access unavailable.",
                    description: "This session does not grant client access."
                )
            }
        }
    }

    func testAllowedDestinationsHaveNoUnavailableContent() {
        for expectation in expectations {
            let policy = ClientAccessPolicy(principalKind: expectation.kind)
            for destination in expectation.allowedDestinations {
                XCTAssertNil(
                    policy.unavailableContent(for: destination),
                    "\(expectation.label) -> \(destination.rawValue)"
                )
            }
        }
    }

    func testWelcomeLinesNameTheActualCapabilityBoundary() {
        let cases: [(PrincipalKind, String?, String)] = [
            (
                .galleryGuest,
                "Amelia Chen",
                "It’s lovely to see you, Amelia — your photographs are ready whenever you are."
            ),
            (
                .galleryGuest,
                nil,
                "Your photographs are ready whenever you are."
            ),
            (
                .portalGuest,
                "Amelia Chen",
                "It’s lovely to see you, Amelia — everything shared through this portal lives here."
            ),
            (
                .portalGuest,
                nil,
                "Everything shared through this portal lives here."
            ),
            (
                .workspaceGuest,
                "Amelia Chen",
                "It’s lovely to see you, Amelia — everything for your project lives here."
            ),
            (
                .workspaceGuest,
                nil,
                "Everything shared through this workspace lives here."
            ),
            (
                .documentGuest,
                "Amelia Chen",
                "It’s lovely to see you, Amelia — your document is ready whenever you are."
            ),
            (
                .documentGuest,
                nil,
                "Your document is ready whenever you are."
            ),
            (
                .studioOwner,
                "Amelia Chen",
                "This session does not grant client access."
            ),
            (
                PrincipalKind(rawValue: "future_guest"),
                nil,
                "This session does not grant client access."
            ),
        ]

        for (kind, name, expected) in cases {
            XCTAssertEqual(
                ClientAccessPolicy(principalKind: kind)
                    .welcomeLine(clientDisplayName: name),
                expected,
                "\(kind.rawValue), name: \(name ?? "nil")"
            )
        }
    }

    func testBlankWelcomeNameUsesTheUnnamedLine() {
        let policy = ClientAccessPolicy(principalKind: .portalGuest)

        XCTAssertEqual(
            policy.welcomeLine(clientDisplayName: ""),
            "Everything shared through this portal lives here."
        )
        XCTAssertEqual(
            policy.welcomeLine(clientDisplayName: "   "),
            "Everything shared through this portal lives here."
        )
    }

    private func assertUnavailableContent(
        policy: ClientAccessPolicy,
        destination: ClientDestination,
        heading: String,
        description: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        guard let content = policy.unavailableContent(for: destination) else {
            return XCTFail(
                "Expected unavailable content for \(destination.rawValue)",
                file: file,
                line: line
            )
        }
        XCTAssertEqual(content.heading, heading, file: file, line: line)
        XCTAssertEqual(content.description, description, file: file, line: line)
        XCTAssertEqual(content.systemImage, destination.icon, file: file, line: line)
    }
}
