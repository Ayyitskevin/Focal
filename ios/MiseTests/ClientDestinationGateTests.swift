import Foundation
import SwiftUI
import UIKit
import XCTest

@testable import Mise

final class ClientDestinationGateTests: XCTestCase {
    private struct ForbiddenCell {
        let label: String
        let principalKind: PrincipalKind
        let destination: ClientDestination
    }

    private let forbiddenCells = [
        ForbiddenCell(
            label: "gallery -> documents",
            principalKind: .galleryGuest,
            destination: .documents
        ),
        ForbiddenCell(
            label: "gallery -> bookings",
            principalKind: .galleryGuest,
            destination: .bookings
        ),
        ForbiddenCell(
            label: "portal -> documents",
            principalKind: .portalGuest,
            destination: .documents
        ),
        ForbiddenCell(
            label: "document -> gallery",
            principalKind: .documentGuest,
            destination: .gallery
        ),
        ForbiddenCell(
            label: "document -> bookings",
            principalKind: .documentGuest,
            destination: .bookings
        ),
        ForbiddenCell(
            label: "owner -> home",
            principalKind: .studioOwner,
            destination: .home
        ),
        ForbiddenCell(
            label: "owner -> gallery",
            principalKind: .studioOwner,
            destination: .gallery
        ),
        ForbiddenCell(
            label: "owner -> documents",
            principalKind: .studioOwner,
            destination: .documents
        ),
        ForbiddenCell(
            label: "owner -> bookings",
            principalKind: .studioOwner,
            destination: .bookings
        ),
        ForbiddenCell(
            label: "unknown -> home",
            principalKind: PrincipalKind(rawValue: "future_guest"),
            destination: .home
        ),
        ForbiddenCell(
            label: "unknown -> gallery",
            principalKind: PrincipalKind(rawValue: "future_guest"),
            destination: .gallery
        ),
        ForbiddenCell(
            label: "unknown -> documents",
            principalKind: PrincipalKind(rawValue: "future_guest"),
            destination: .documents
        ),
        ForbiddenCell(
            label: "unknown -> bookings",
            principalKind: PrincipalKind(rawValue: "future_guest"),
            destination: .bookings
        ),
    ]

    @MainActor
    func testEveryForbiddenGateKeepsItsResourceModelIdleAndNeverCallsLoaders() async {
        XCTAssertEqual(forbiddenCells.count, 13)

        for cell in forbiddenCells {
            let spy = ClientGateLoaderSpy()
            let renderProbe = ClientGateRenderProbe()
            let model = makeModel(spy: spy)
            guard let hosting = mount(
                ClientDestinationGate(
                    policy: ClientAccessPolicy(principalKind: cell.principalKind),
                    destination: cell.destination
                ) {
                    let _ = renderProbe.markContentBuilt()
                    ResourceView(
                        model: model,
                        isEmpty: { $0.isEmpty },
                        content: { values in Text(values.joined(separator: ", ")) },
                        empty: { Text("Empty") }
                    )
                }
                .onAppear { renderProbe.markRootAppeared() }
            ) else {
                continue
            }

            let didRender = await waitForRootAppearance(renderProbe)
            XCTAssertTrue(didRender, "\(cell.label) gate must enter a rendered hierarchy")
            guard didRender else {
                unmount(hosting)
                continue
            }

            guard case .idle = model.state else {
                XCTFail("\(cell.label) must leave its destination model idle")
                unmount(hosting)
                continue
            }
            XCTAssertEqual(
                renderProbe.snapshot().contentBuilds,
                0,
                "\(cell.label) must not attach its ResourceView branch"
            )
            let counts = await spy.counts()
            XCTAssertEqual(counts.cache, 0, "\(cell.label) cache loader")
            XCTAssertEqual(counts.remote, 0, "\(cell.label) remote loader")
            unmount(hosting)
        }
    }

    @MainActor
    func testAllowedGateActuallyRunsResourceViewLoaders() async {
        let spy = ClientGateLoaderSpy()
        let model = makeModel(spy: spy)
        guard let hosting = mount(
            ClientDestinationGate(
                policy: ClientAccessPolicy(principalKind: .workspaceGuest),
                destination: .gallery
            ) {
                ResourceView(
                    model: model,
                    isEmpty: { $0.isEmpty },
                    content: { values in Text(values.joined(separator: ", ")) },
                    empty: { Text("Empty") }
                )
            }
        ) else {
            return
        }

        let didLoad = await waitForLoadedModel(model, spy: spy)
        let counts = await spy.counts()

        XCTAssertTrue(didLoad, "The hosting harness must run SwiftUI .task")
        XCTAssertEqual(counts.cache, 1)
        XCTAssertEqual(counts.remote, 1)
        guard case let .loaded(snapshot) = model.state else {
            unmount(hosting)
            return XCTFail("Allowed content should finish loading")
        }
        XCTAssertEqual(snapshot.value, ["live"])
        unmount(hosting)
    }

    @MainActor
    private func makeModel(spy: ClientGateLoaderSpy) -> ResourceModel<[String]> {
        ResourceModel(
            staleAfter: 60,
            cached: { await spy.cached() },
            remote: { await spy.remote() }
        )
    }

    @MainActor
    private func mount<Content: View>(_ content: Content) -> ClientGateHosting? {
        let controller = UIHostingController(rootView: content)
        let frame = CGRect(x: 0, y: 0, width: 390, height: 844)
        let scenes = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive }
            ?? scenes.first { $0.activationState == .foregroundInactive }
        guard let scene else {
            XCTFail("The UIHostingController harness requires a foreground window scene")
            return nil
        }
        let previousKeyWindow = scene.windows.first { $0.isKeyWindow }
        let window = UIWindow(windowScene: scene)
        window.frame = frame
        window.rootViewController = controller
        window.makeKeyAndVisible()
        controller.view.setNeedsLayout()
        controller.view.layoutIfNeeded()
        return ClientGateHosting(window: window, previousKeyWindow: previousKeyWindow)
    }

    @MainActor
    private func unmount(_ hosting: ClientGateHosting) {
        hosting.window.isHidden = true
        hosting.window.rootViewController = nil
        hosting.previousKeyWindow?.makeKey()
    }

    @MainActor
    private func waitForRootAppearance(
        _ probe: ClientGateRenderProbe,
        timeout: Duration = .seconds(2)
    ) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)

        while clock.now < deadline {
            if probe.snapshot().rootAppeared {
                return true
            }
            await Task.yield()
            try? await Task.sleep(for: .milliseconds(10))
        }
        return false
    }

    @MainActor
    private func waitForLoadedModel(
        _ model: ResourceModel<[String]>,
        spy: ClientGateLoaderSpy,
        timeout: Duration = .seconds(2)
    ) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)

        while clock.now < deadline {
            let counts = await spy.counts()
            if counts.remote > 0, case .loaded = model.state {
                return true
            }
            await Task.yield()
            try? await Task.sleep(for: .milliseconds(10))
        }
        return false
    }
}

private struct ClientGateHosting {
    let window: UIWindow
    let previousKeyWindow: UIWindow?
}

private struct ClientGateRenderState: Sendable {
    let rootAppeared: Bool
    let contentBuilds: Int
}

private final class ClientGateRenderProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var rootAppeared = false
    private var contentBuilds = 0

    func markRootAppeared() {
        lock.lock()
        rootAppeared = true
        lock.unlock()
    }

    func markContentBuilt() {
        lock.lock()
        contentBuilds += 1
        lock.unlock()
    }

    func snapshot() -> ClientGateRenderState {
        lock.lock()
        let state = ClientGateRenderState(
            rootAppeared: rootAppeared,
            contentBuilds: contentBuilds
        )
        lock.unlock()
        return state
    }
}

private struct ClientGateLoaderCounts: Sendable {
    let cache: Int
    let remote: Int
}

private actor ClientGateLoaderSpy {
    private var cacheCalls = 0
    private var remoteCalls = 0

    func cached() -> ResourceSnapshot<[String]>? {
        cacheCalls += 1
        return nil
    }

    func remote() -> ResourceSnapshot<[String]> {
        remoteCalls += 1
        return ResourceSnapshot(
            value: ["live"],
            storedAt: Date(timeIntervalSince1970: 1_700_000_000),
            source: .network
        )
    }

    func counts() -> ClientGateLoaderCounts {
        ClientGateLoaderCounts(cache: cacheCalls, remote: remoteCalls)
    }
}
