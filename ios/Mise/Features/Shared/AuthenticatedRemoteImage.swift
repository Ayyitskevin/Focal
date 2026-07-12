import SwiftUI

/// A bearer-authenticated drop-in replacement for `AsyncImage`.
///
/// Gallery media links point at `/api/v1/media/...`, which requires the same
/// rotating bearer session as every other request -- a plain `AsyncImage`
/// would send no `Authorization` header and see nothing but 401s. This loads
/// bytes through `AuthenticatedMediaLoader` (which shares the workspace's
/// session authorizer) and decodes them locally.
struct AuthenticatedRemoteImage<Content: View>: View {
    enum Phase {
        case empty
        case success(Image)
        case failure
    }

    private let url: URL?
    private let loader: AuthenticatedMediaLoader
    private let content: (Phase) -> Content

    @State private var phase: Phase = .empty

    init(
        url: URL?,
        loader: AuthenticatedMediaLoader,
        @ViewBuilder content: @escaping (Phase) -> Content
    ) {
        self.url = url
        self.loader = loader
        self.content = content
    }

    var body: some View {
        content(phase)
            .task(id: url) {
                await load()
            }
    }

    private func load() async {
        guard let url else {
            phase = .failure
            return
        }
        phase = .empty
        do {
            let data = try await loader.data(for: url)
            guard let uiImage = UIImage(data: data) else {
                phase = .failure
                return
            }
            phase = .success(Image(uiImage: uiImage))
        } catch is CancellationError {
            // A superseded load (e.g. lightbox paged away); leave phase as-is.
        } catch {
            phase = .failure
        }
    }
}
