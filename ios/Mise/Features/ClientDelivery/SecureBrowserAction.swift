import SafariServices
import SwiftUI

private struct BrowserTarget: Identifiable {
    let id = UUID()
    let url: URL
}

struct SecureBrowserAction: View {
    let url: URL
    let workspaceOrigin: URL
    let allowedPathPrefix: String
    let title: String
    let systemImage: String
    @Environment(\.openURL) private var openURL
    @State private var target: BrowserTarget?

    var body: some View {
        Menu {
            Button {
                guard let safeURL else { return }
                target = BrowserTarget(url: safeURL)
            } label: {
                Label("Open securely in Mise", systemImage: "safari")
            }
            Button {
                guard let safeURL else { return }
                openURL(safeURL)
            } label: {
                Label("Open in Safari", systemImage: "arrow.up.right.square")
            }
        } label: {
            Label(title, systemImage: systemImage)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .disabled(safeURL == nil)
        .sheet(item: $target) { target in
            SafariView(url: target.url)
                .ignoresSafeArea()
        }
        .accessibilityHint("Opens the studio’s secure webpage without sharing your app session")
    }

    private var safeURL: URL? {
        ClientBrowserTargetValidator.validated(
            url,
            workspaceOrigin: workspaceOrigin,
            allowedPathPrefix: allowedPathPrefix
        )
    }
}

enum ClientBrowserTargetValidator {
    static func validated(
        _ url: URL,
        workspaceOrigin: URL,
        allowedPathPrefix: String
    ) -> URL? {
        let expectedPath = allowedPathPrefix.split(
            separator: "/",
            omittingEmptySubsequences: true
        )
        let path = url.path.split(separator: "/", omittingEmptySubsequences: true)
        guard sameOrigin(url, workspaceOrigin),
              url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              !components.percentEncodedPath.contains("%"),
              expectedPath.count == 1,
              path.count == 2,
              path[0] == expectedPath[0]
        else {
            return nil
        }
        return url
    }

    private static func sameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        guard let lhsScheme = lhs.scheme?.lowercased(),
              let rhsScheme = rhs.scheme?.lowercased(),
              let lhsHost = lhs.host?.lowercased(),
              let rhsHost = rhs.host?.lowercased(),
              lhsScheme == "https",
              rhsScheme == "https"
        else {
            return false
        }
        let lhsPort = lhs.port ?? 443
        let rhsPort = rhs.port ?? 443
        return lhsScheme == rhsScheme && lhsHost == rhsHost && lhsPort == rhsPort
    }
}

private struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let configuration = SFSafariViewController.Configuration()
        configuration.entersReaderIfAvailable = false
        configuration.barCollapsingEnabled = true
        return SFSafariViewController(url: url, configuration: configuration)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}
