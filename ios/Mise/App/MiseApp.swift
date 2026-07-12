import SwiftUI

@main
struct MiseApp: App {
    private let environment: AppEnvironment

    init() {
        do {
            environment = try .live()
        } catch {
            fatalError("Invalid Mise app configuration: \(error.localizedDescription)")
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView(environment: environment)
                // Brand accent from the design handoff: terracotta in light
                // mode, teal-green in dark (an intentional hue shift).
                .tint(MiseDesign.terra)
        }
    }
}
