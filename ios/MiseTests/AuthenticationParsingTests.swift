import Foundation
import XCTest
@testable import Mise

final class AuthenticationParsingTests: XCTestCase {
    private let productionParser = WorkspaceAddressParser(
        platformRoot: URL(string: "https://mise.example")!,
        permitsInsecureLoopback: false
    )

    func testHostedSlugIsLowercasedAndScopedToPlatformRoot() throws {
        let address = try productionParser.parse("  North-Star  ")

        XCTAssertEqual(address.hostedSlug, "north-star")
        XCTAssertEqual(address.origin, URL(string: "https://north-star.mise.example"))
    }

    func testCustomOriginIsCanonicalized() throws {
        let address = try productionParser.parse("https://Studio.Example.com:443/")

        XCTAssertNil(address.hostedSlug)
        XCTAssertEqual(address.origin, URL(string: "https://studio.example.com"))
    }

    func testWorkspaceAddressRejectsCredentialsPathsQueriesAndInsecureHosts() {
        assertWorkspaceError("https://owner:secret@studio.example.com", is: .invalidAddress)
        assertWorkspaceError("https://studio.example.com/admin", is: .pathNotAllowed)
        assertWorkspaceError("https://studio.example.com?tenant=other", is: .invalidAddress)
        assertWorkspaceError("http://studio.example.com", is: .secureConnectionRequired)
        assertWorkspaceError("-invalid", is: .invalidHostedSlug)
    }

    func testDebugParserAllowsOnlyLoopbackHTTP() throws {
        let parser = WorkspaceAddressParser(
            platformRoot: URL(string: "https://mise.example")!,
            permitsInsecureLoopback: true
        )

        XCTAssertEqual(
            try parser.parse("http://127.0.0.1:8000").origin,
            URL(string: "http://127.0.0.1:8000")
        )
        XCTAssertThrowsError(try parser.parse("http://studio.example.com"))
    }

    func testTenantDescriptorMustMatchRequestedCanonicalWorkspace() throws {
        let address = try productionParser.parse("north-star")
        let selection = try WorkspaceSelection(
            address: address,
            descriptor: descriptor(
                origin: "https://north-star.mise.example",
                slug: "north-star"
            ),
            parser: productionParser
        )

        XCTAssertEqual(selection.descriptor.studioName, "North Star Photo")

        XCTAssertThrowsError(
            try WorkspaceSelection(
                address: address,
                descriptor: descriptor(
                    origin: "https://other.mise.example",
                    slug: "north-star"
                ),
                parser: productionParser
            )
        ) { error in
            XCTAssertEqual(error as? WorkspaceSelectionError, .canonicalOriginMismatch)
        }
    }

    func testHostedTenantDescriptorMustEchoTheRequestedSlug() throws {
        let address = try productionParser.parse("north-star")

        XCTAssertThrowsError(
            try WorkspaceSelection(
                address: address,
                descriptor: descriptor(
                    origin: "https://north-star.mise.example",
                    slug: "another-studio"
                ),
                parser: productionParser
            )
        ) { error in
            XCTAssertEqual(error as? WorkspaceSelectionError, .hostedSlugMismatch)
        }
    }

    func testEveryPublicLinkPathMapsToItsExactCapability() throws {
        let parser = SharedAccessTargetParser(addressParser: productionParser)
        let cases: [(String, SharedAccessCapability)] = [
            ("g", .gallery),
            ("portal", .portal),
            ("w", .workspace),
            ("p", .proposal),
            ("c", .contract),
            ("i", .invoice),
        ]

        for (path, expectedCapability) in cases {
            let target = try parser.parse(
                "https://studio.example.com/\(path)/Resource_42",
                selectedCapability: .gallery,
                currentWorkspaceOrigin: nil
            )

            XCTAssertEqual(target.origin, URL(string: "https://studio.example.com"))
            XCTAssertEqual(target.capability, expectedCapability)
            XCTAssertEqual(target.slug, "Resource_42")
        }
    }

    func testFullClientLinkRejectsTrackingAndEncodedPathData() {
        let parser = SharedAccessTargetParser(addressParser: productionParser)

        assertSharedLinkError(
            "https://studio.example.com/g/gallery-1?tracking=1",
            parser: parser,
            is: .invalidLink
        )
        assertSharedLinkError(
            "https://studio.example.com/g/gallery%2D1",
            parser: parser,
            is: .invalidLink
        )
        assertSharedLinkError(
            "https://studio.example.com/unknown/gallery-1",
            parser: parser,
            is: .unsupportedLink
        )
    }

    func testBareSharedSlugRequiresAndUsesCurrentWorkspace() throws {
        let parser = SharedAccessTargetParser(addressParser: productionParser)

        XCTAssertThrowsError(
            try parser.parse(
                "invoice_42",
                selectedCapability: .invoice,
                currentWorkspaceOrigin: nil
            )
        ) { error in
            XCTAssertEqual(error as? SharedAccessTargetError, .workspaceRequiredForSlug)
        }

        let target = try parser.parse(
            "invoice_42",
            selectedCapability: .invoice,
            currentWorkspaceOrigin: URL(string: "https://studio.example.com")!
        )
        XCTAssertEqual(target.capability, .invoice)
        XCTAssertEqual(target.origin, URL(string: "https://studio.example.com"))
    }

    func testAuthenticationFlowTransitionsDoNotRetainPreviousWorkspace() throws {
        let address = try productionParser.parse("north-star")
        let selection = try WorkspaceSelection(
            address: address,
            descriptor: descriptor(
                origin: "https://north-star.mise.example",
                slug: "north-star"
            ),
            parser: productionParser
        )
        var flow = AuthenticationFlowState()

        XCTAssertEqual(flow.screen, .workspace)
        XCTAssertNil(flow.workspace)

        flow.didDiscover(selection, preferredMode: .studio)
        XCTAssertEqual(flow.screen, .credentials)
        XCTAssertEqual(flow.workspace, selection)

        flow.showClientLink()
        XCTAssertEqual(flow.screen, .clientLink)
        XCTAssertNil(flow.workspace)
        XCTAssertEqual(flow.mode, .sharedAccess)

        flow.reset()
        XCTAssertEqual(flow, AuthenticationFlowState())
    }

    private func descriptor(
        origin: String,
        slug: String?,
        cacheNamespace: String = "workspace_42"
    ) -> TenantDescriptor {
        TenantDescriptor(
            cacheNamespace: cacheNamespace,
            slug: slug,
            studioName: "North Star Photo",
            canonicalBaseURL: URL(string: origin)!,
            brandAccentHex: "#2F5C45",
            timeZone: "America/New_York",
            currencyCode: "USD",
            authMethods: ["studio_password", "shared_access"],
            signupURL: nil,
            manageBillingURL: nil
        )
    }

    private func assertWorkspaceError(
        _ input: String,
        is expected: WorkspaceAddressError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(
            try productionParser.parse(input),
            file: file,
            line: line
        ) { error in
            XCTAssertEqual(error as? WorkspaceAddressError, expected, file: file, line: line)
        }
    }

    private func assertSharedLinkError(
        _ input: String,
        parser: SharedAccessTargetParser,
        is expected: SharedAccessTargetError,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertThrowsError(
            try parser.parse(
                input,
                selectedCapability: .gallery,
                currentWorkspaceOrigin: nil
            ),
            file: file,
            line: line
        ) { error in
            XCTAssertEqual(error as? SharedAccessTargetError, expected, file: file, line: line)
        }
    }
}
