import XCTest
@testable import Mise

final class NotificationEventBridgeTests: XCTestCase {
    func testBuffersOnlySendableTokenAndEnvelopeUntilReceiverConnects() async {
        let bridge = NotificationEventBridge()
        let receiver = BridgeReceiver()
        let envelope = NotificationEnvelope(
            version: 1,
            eventID: UUID(),
            workspaceOrigin: URL(string: "https://north.mise.example")!,
            workspaceCacheNamespace: "tenant_north",
            principalKind: .studioOwner,
            principalID: "studio_owner",
            route: "/app/home"
        )

        await bridge.receiveToken(Data([0x01, 0x02]))
        await bridge.receiveNotification(envelope)
        await bridge.connect(receiver)

        let tokens = await receiver.tokens()
        let envelopes = await receiver.envelopes()
        XCTAssertEqual(tokens, [Data([0x01, 0x02])])
        XCTAssertEqual(envelopes, [envelope])
    }

    func testOnlyLatestBufferedAPNsRegistrationResultIsReplayed() async {
        let bridge = NotificationEventBridge()
        let receiver = BridgeReceiver()

        await bridge.receiveFailure("failed")
        await bridge.receiveToken(Data([0x01, 0x02]))
        await bridge.connect(receiver)

        let tokens = await receiver.tokens()
        let failures = await receiver.failures()
        XCTAssertEqual(tokens, [Data([0x01, 0x02])])
        XCTAssertTrue(failures.isEmpty)
    }
}

private actor BridgeReceiver: NotificationEventReceiving {
    private var receivedTokens: [Data] = []
    private var receivedEnvelopes: [NotificationEnvelope] = []
    private var receivedFailures: [String] = []

    func receivedRemoteNotificationToken(_ token: Data) async {
        receivedTokens.append(token)
    }

    func remoteNotificationRegistrationFailed(_ message: String) async {
        receivedFailures.append(message)
    }

    func receivedNotification(_ envelope: NotificationEnvelope) async {
        receivedEnvelopes.append(envelope)
    }

    func shouldPresentNotification(_ envelope: NotificationEnvelope) async -> Bool { true }

    func tokens() -> [Data] { receivedTokens }
    func envelopes() -> [NotificationEnvelope] { receivedEnvelopes }
    func failures() -> [String] { receivedFailures }
}
