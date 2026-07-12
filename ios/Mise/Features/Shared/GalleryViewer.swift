import Foundation
import Observation
import SwiftUI

/// Session-local favorite state layered over a gallery manifest, mirroring the
/// design handoff's `favoriteOverrides` model: the manifest stays immutable
/// and the newest user intent wins until the next refresh.
///
/// Toggles are optimistic — the heart flips immediately, then reverts with a
/// message if the server refuses (offline, revoked, or a proofing-cap 409).
@MainActor
@Observable
final class GalleryFavorites {
    let canFavorite: Bool
    private(set) var overrides: [Int64: Bool] = [:]
    /// Server-confirmed picks per section, replacing manifest counts after a toggle.
    private(set) var sectionSelectedCounts: [Int64: Int] = [:]
    var notice: String?

    private let galleryID: Int64
    private let toggle: (@Sendable (Int64, Bool) async throws -> FavoriteState)?
    private var inFlight: Set<Int64> = []

    init(
        galleryID: Int64,
        canFavorite: Bool,
        toggle: (@Sendable (_ assetID: Int64, _ selected: Bool) async throws -> FavoriteState)? = nil
    ) {
        self.galleryID = galleryID
        self.canFavorite = canFavorite && toggle != nil
        self.toggle = toggle
    }

    func isFavorite(_ asset: GalleryAsset) -> Bool {
        overrides[asset.id] ?? asset.isFavorite
    }

    func selectedCount(for section: GallerySection) -> Int {
        sectionSelectedCounts[section.id] ?? section.selectedCount
    }

    func favoriteCount(in detail: GalleryDetail) -> Int {
        detail.assets.lazy.filter { self.isFavorite($0) }.count
    }

    func toggleFavorite(_ asset: GalleryAsset) async {
        guard canFavorite, let toggle, inFlight.insert(asset.id).inserted else { return }
        defer { inFlight.remove(asset.id) }

        let target = !isFavorite(asset)
        overrides[asset.id] = target
        do {
            let state = try await toggle(asset.id, target)
            overrides[asset.id] = state.selected
            if let sectionID = asset.sectionID, let count = state.sectionSelectedCount {
                sectionSelectedCounts[sectionID] = count
            }
        } catch {
            overrides[asset.id] = !target
            if case let APIError.conflict(problem) = error, let detail = problem?.detail {
                notice = detail
            } else {
                notice = "Couldn’t save that favorite — check your connection and try again."
            }
        }
    }
}

/// The shared sectioned photo grid from the design handoff — identical for the
/// owner and client roles. Tight 3-column grid, small radius, play glyph on
/// video, heart badge on favorites.
struct GalleryManifestView: View {
    let detail: GalleryDetail
    let favorites: GalleryFavorites
    let mediaLoader: AuthenticatedMediaLoader
    let refresh: @MainActor () async -> Void

    @State private var lightboxAsset: GalleryAsset?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                summaryStrip
                ForEach(sectionGroups, id: \.0) { _, section, assets in
                    sectionView(section, assets: assets)
                }
                if !unsectioned.isEmpty {
                    grid(unsectioned)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 16)
        }
        .refreshable { await refresh() }
        .fullScreenCover(item: $lightboxAsset) { asset in
            GalleryLightboxView(
                assets: detail.assets,
                initialAssetID: asset.id,
                favorites: favorites,
                mediaLoader: mediaLoader
            )
        }
        .alert(
            "Favorites",
            isPresented: Binding(
                get: { favorites.notice != nil },
                set: { if !$0 { favorites.notice = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(favorites.notice ?? "")
        }
    }

    private var summaryStrip: some View {
        HStack(spacing: 10) {
            StatusPill(
                label: detail.summary.deliveryState.clientDisplayName,
                tone: detail.summary.deliveryState.tone
            )
            Text("\(favorites.favoriteCount(in: detail)) of \(detail.assets.count) favorited")
                .font(.footnote)
                .foregroundStyle(.secondary)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }

    private var sectionGroups: [(Int64, GallerySection, [GalleryAsset])] {
        detail.sections.compactMap { section in
            let assets = detail.assets.filter { $0.sectionID == section.id }
            guard !assets.isEmpty else { return nil }
            return (section.id, section, assets)
        }
    }

    private var unsectioned: [GalleryAsset] {
        let sectionIDs = Set(detail.sections.map(\.id))
        return detail.assets.filter { asset in
            guard let sectionID = asset.sectionID else { return true }
            return !sectionIDs.contains(sectionID)
        }
    }

    private func sectionView(_ section: GallerySection, assets: [GalleryAsset]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(section.name)
                    .miseDisplayFont(.headline)
                Spacer(minLength: 8)
                if let target = section.proofTarget {
                    Text("\(favorites.selectedCount(for: section)) of \(target) picked")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            grid(assets)
        }
        .accessibilityElement(children: .contain)
    }

    private func grid(_ assets: [GalleryAsset]) -> some View {
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: 3), count: columnCount),
            spacing: 3
        ) {
            ForEach(assets) { asset in
                Button {
                    lightboxAsset = asset
                } label: {
                    GalleryAssetCell(
                        asset: asset,
                        isFavorite: favorites.isFavorite(asset),
                        mediaLoader: mediaLoader
                    )
                }
                .buttonStyle(.plain)
                .accessibilityLabel(assetLabel(asset))
                .accessibilityHint("Opens the photo viewer")
            }
        }
    }

    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    private var columnCount: Int {
        horizontalSizeClass == .regular ? 6 : 3
    }

    private func assetLabel(_ asset: GalleryAsset) -> String {
        var label = asset.altText ?? asset.filename
        if favorites.isFavorite(asset) {
            label += ", favorited"
        }
        if asset.kind == .video {
            label += ", video"
        }
        return label
    }
}

private struct GalleryAssetCell: View {
    let asset: GalleryAsset
    let isFavorite: Bool
    let mediaLoader: AuthenticatedMediaLoader

    var body: some View {
        ZStack {
            AuthenticatedRemoteImage(
                url: asset.links.thumbnailURL ?? asset.links.previewURL,
                loader: mediaLoader
            ) { phase in
                switch phase {
                case let .success(image):
                    image.resizable().scaledToFill()
                case .failure:
                    placeholder
                case .empty:
                    MiseDesign.surfaceSunk
                }
            }
            .aspectRatio(1, contentMode: .fill)
            .clipShape(RoundedRectangle(cornerRadius: 4))

            VStack {
                Spacer()
                HStack {
                    if asset.kind == .video {
                        Image(systemName: "play.circle.fill")
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, .black.opacity(0.55))
                    }
                    Spacer()
                    if isFavorite {
                        Image(systemName: "heart.fill")
                            .font(.caption)
                            .foregroundStyle(MiseDesign.heart)
                            .padding(3)
                            .background(.black.opacity(0.35), in: Circle())
                    }
                }
                .padding(5)
            }
            .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
    }

    private var placeholder: some View {
        ZStack {
            MiseDesign.surfaceSunk
            Image(systemName: asset.kind == .video ? "video" : "photo")
                .foregroundStyle(.secondary)
        }
    }
}

/// Fullscreen paging lightbox shared by both roles: black background, close
/// top-left, "{n} of {total}" counter, contain-fit image, edge-aware chevrons,
/// and a functional heart when the session may favorite.
struct GalleryLightboxView: View {
    let assets: [GalleryAsset]
    let initialAssetID: Int64
    let favorites: GalleryFavorites
    let mediaLoader: AuthenticatedMediaLoader

    @Environment(\.dismiss) private var dismiss
    @State private var selectedID: Int64?

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            TabView(selection: $selectedID) {
                ForEach(assets) { asset in
                    AuthenticatedRemoteImage(
                        url: asset.links.previewURL ?? asset.links.posterURL,
                        loader: mediaLoader
                    ) { phase in
                        switch phase {
                        case let .success(image):
                            image.resizable().scaledToFit()
                        case .failure:
                            ContentUnavailableView(
                                "Preview unavailable",
                                systemImage: asset.kind == .video
                                    ? "video.slash" : "photo.badge.exclamationmark",
                                description: Text(asset.filename)
                            )
                            .foregroundStyle(.white)
                        case .empty:
                            ProgressView().tint(.white)
                        }
                    }
                    .tag(Optional(asset.id))
                    .accessibilityLabel(asset.altText ?? asset.filename)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))

            overlayControls
        }
        .onAppear {
            if selectedID == nil {
                selectedID = initialAssetID
            }
        }
        .preferredColorScheme(.dark)
    }

    private var currentIndex: Int? {
        guard let selectedID else { return nil }
        return assets.firstIndex(where: { $0.id == selectedID })
    }

    private var currentAsset: GalleryAsset? {
        currentIndex.map { assets[$0] }
    }

    private var overlayControls: some View {
        VStack {
            HStack {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.white)
                        .frame(width: 44, height: 44)
                        .background(.white.opacity(0.12), in: Circle())
                }
                .accessibilityLabel("Close")

                Spacer()

                if let index = currentIndex {
                    Text("\(index + 1) of \(assets.count)")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.85))
                        .accessibilityLabel("Photo \(index + 1) of \(assets.count)")
                }

                Spacer()

                // Mirror the close button's width so the counter stays centered.
                Color.clear.frame(width: 44, height: 44)
            }
            .padding(.horizontal, 14)

            Spacer()

            HStack {
                arrowButton(direction: -1, systemImage: "chevron.left")
                Spacer()
                if favorites.canFavorite, let asset = currentAsset {
                    heartButton(asset)
                }
                Spacer()
                arrowButton(direction: 1, systemImage: "chevron.right")
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 24)
        }
    }

    @ViewBuilder
    private func arrowButton(direction: Int, systemImage: String) -> some View {
        let target = currentIndex.map { $0 + direction }
        if let target, assets.indices.contains(target) {
            Button {
                withAnimation {
                    selectedID = assets[target].id
                }
            } label: {
                Image(systemName: systemImage)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.white)
                    .frame(width: 44, height: 44)
                    .background(.white.opacity(0.12), in: Circle())
            }
            .accessibilityLabel(direction < 0 ? "Previous photo" : "Next photo")
        } else {
            Color.clear.frame(width: 44, height: 44)
        }
    }

    private func heartButton(_ asset: GalleryAsset) -> some View {
        Button {
            Task { await favorites.toggleFavorite(asset) }
        } label: {
            Image(systemName: favorites.isFavorite(asset) ? "heart.fill" : "heart")
                .font(.title3)
                .foregroundStyle(favorites.isFavorite(asset) ? MiseDesign.heart : .white)
                .frame(width: 52, height: 52)
                .background(.white.opacity(0.12), in: Circle())
        }
        .accessibilityLabel(
            favorites.isFavorite(asset) ? "Remove favorite" : "Favorite this photo"
        )
        .accessibilityValue(favorites.isFavorite(asset) ? "Favorited" : "Not favorited")
    }
}
