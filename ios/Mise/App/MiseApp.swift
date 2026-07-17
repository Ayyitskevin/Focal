import SwiftUI

@main
struct MiseApp: App {
    private let launch: LaunchState

    private enum LaunchState {
        case ready(AppEnvironment)
        case misconfigured(String)
    }

    init() {
        do {
            launch = .ready(try .live())
        } catch {
            // A bad MiseServerBaseURL must degrade to a visible screen, never a
            // launch crash — a crash here is unrecoverable for a user and an
            // instant rejection in App Store review builds.
            launch = .misconfigured(error.localizedDescription)
        }
    }

    var body: some Scene {
        WindowGroup {
            switch launch {
            case .ready(let environment):
                RootView(environment: environment)
                    // Brand accent from the design handoff: terracotta in light
                    // mode, teal-green in dark (an intentional hue shift).
                    .tint(MiseDesign.terra)
            case .misconfigured(let message):
                LaunchConfigurationErrorView(message: message)
                    .tint(MiseDesign.terra)
            }
        }
    }
}

/// Shown instead of the app when the build's server configuration is invalid.
/// There is nothing a user can do beyond reporting it, so the screen says
/// exactly that and surfaces the underlying reason for the report.
struct LaunchConfigurationErrorView: View {
    let message: String

    var body: some View {
        ContentUnavailableView {
            Label("Mise can't start", systemImage: "exclamationmark.triangle")
        } description: {
            Text(
                "This build was packaged with an invalid server configuration. "
                    + "Please report this to support.\n\n\(message)"
            )
        }
    }
}
