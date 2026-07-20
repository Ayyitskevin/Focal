import AVKit
import SwiftUI

/// Bearer-authenticated video lightbox page.
///
/// Still presentation uses the poster (or thumbnail) through the image loader.
/// Playback loads the authenticated MP4 into a temporary file and hands it to
/// `AVPlayer` — never through `UIImage(data:)`, which cannot decode video bytes.
struct AuthenticatedRemoteVideo: View {
    let stillURL: URL?
    let playbackURL: URL?
    let loader: AuthenticatedMediaLoader
    let filename: String

    @State private var localPlaybackURL: URL?
    @State private var loadFailed = false
    @State private var isLoadingPlayback = false

    var body: some View {
        ZStack {
            AuthenticatedRemoteImage(url: stillURL, loader: loader) { phase in
                switch phase {
                case let .success(image):
                    image.resizable().scaledToFit()
                case .failure, .empty:
                    Color.black
                }
            }

            if let localPlaybackURL {
                VideoPlayer(player: AVPlayer(url: localPlaybackURL))
                    .scaledToFit()
            } else if loadFailed {
                ContentUnavailableView(
                    "Preview unavailable",
                    systemImage: "video.slash",
                    description: Text(filename)
                )
                .foregroundStyle(.white)
            } else if isLoadingPlayback {
                ProgressView().tint(.white)
            }
        }
        .task(id: playbackURL) {
            await loadPlayback()
        }
        .onDisappear {
            cleanupLocalFile()
        }
    }

    private func loadPlayback() async {
        guard let playbackURL else {
            loadFailed = stillURL == nil
            return
        }
        isLoadingPlayback = true
        loadFailed = false
        defer { isLoadingPlayback = false }
        do {
            let data = try await loader.data(for: playbackURL)
            let tmp = FileManager.default.temporaryDirectory
                .appendingPathComponent("mise-video-\(UUID().uuidString).mp4")
            try data.write(to: tmp, options: .atomic)
            localPlaybackURL = tmp
        } catch is CancellationError {
            // Superseded load while paging away.
        } catch {
            loadFailed = true
        }
    }

    private func cleanupLocalFile() {
        if let localPlaybackURL {
            try? FileManager.default.removeItem(at: localPlaybackURL)
            self.localPlaybackURL = nil
        }
    }
}
