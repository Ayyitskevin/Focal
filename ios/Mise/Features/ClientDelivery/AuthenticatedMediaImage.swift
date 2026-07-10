import ImageIO
import Observation
import SwiftUI
import UIKit

private struct SendableCGImage: @unchecked Sendable {
    let value: CGImage
}

@MainActor
@Observable
private final class AuthenticatedMediaImageModel {
    enum Phase {
        case idle
        case loading
        case success(UIImage)
        case failure
    }

    private(set) var phase: Phase = .idle
    private let loader: any AuthenticatedMediaLoading
    private var generation: UInt64 = 0

    init(loader: any AuthenticatedMediaLoading) {
        self.loader = loader
    }

    func load(
        url: URL?,
        purpose: AuthenticatedMediaPurpose,
        contentRevision: Int64?
    ) async {
        generation &+= 1
        let requestGeneration = generation
        guard let url else {
            phase = .failure
            return
        }
        phase = .loading
        do {
            let data = try await loader.data(
                from: url,
                purpose: purpose,
                contentRevision: contentRevision
            )
            try Task.checkCancellation()
            guard generation == requestGeneration else { return }
            let decoded = await Task.detached(priority: .userInitiated) {
                Self.decode(data, purpose: purpose)
            }.value
            try Task.checkCancellation()
            guard generation == requestGeneration else { return }
            guard let decoded else {
                phase = .failure
                return
            }
            phase = .success(UIImage(
                cgImage: decoded.value,
                scale: UIScreen.main.scale,
                orientation: .up
            ))
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            phase = .failure
        }
    }

    nonisolated private static func decode(
        _ data: Data,
        purpose: AuthenticatedMediaPurpose
    ) -> SendableCGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let maximumDimension: Int
        switch purpose {
        case .thumbnail: maximumDimension = 768
        case .poster: maximumDimension = 1_920
        case .preview: maximumDimension = 4_096
        case .download: return nil
        }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maximumDimension,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(
            source,
            0,
            options as CFDictionary
        ) else {
            return nil
        }
        return SendableCGImage(value: image)
    }
}

struct AuthenticatedMediaImage: View {
    let url: URL?
    let purpose: AuthenticatedMediaPurpose
    let contentMode: ContentMode
    let contentRevision: Int64?
    let accessibilityLabel: String
    @State private var model: AuthenticatedMediaImageModel

    init(
        url: URL?,
        purpose: AuthenticatedMediaPurpose,
        loader: any AuthenticatedMediaLoading,
        contentMode: ContentMode = .fill,
        contentRevision: Int64? = nil,
        accessibilityLabel: String
    ) {
        self.url = url
        self.purpose = purpose
        self.contentMode = contentMode
        self.contentRevision = contentRevision
        self.accessibilityLabel = accessibilityLabel
        _model = State(initialValue: AuthenticatedMediaImageModel(loader: loader))
    }

    var body: some View {
        Group {
            switch model.phase {
            case let .success(image):
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
            case .loading:
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .idle, .failure:
                ZStack {
                    Color.secondary.opacity(0.12)
                    Image(systemName: purpose == .poster ? "video" : "photo")
                        .font(.title2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
        .task(id: taskID) {
            await model.load(
                url: url,
                purpose: purpose,
                contentRevision: contentRevision
            )
        }
    }

    private var taskID: String {
        "\(contentRevision ?? 0):\(purpose.rawValue):\(url?.absoluteString ?? "missing")"
    }
}
