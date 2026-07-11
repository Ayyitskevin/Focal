import Foundation
import Observation

@MainActor
@Observable
final class GalleryGuestModel {
    let resource: ClientResourceModel<GalleryDetail>
    private(set) var favoriteMutationAssetID: Int64?
    var errorMessage: String?
    private let repository: ClientDeliveryRepository

    init(repository: ClientDeliveryRepository) {
        self.repository = repository
        resource = ClientResourceModel(
            staleAfter: 30 * 60,
            cached: { try await repository.cachedGallery() },
            remote: { try await repository.refreshGallery() }
        )
    }

    var detail: GalleryDetail? { resource.state.snapshot?.value }

    func toggleFavorite(assetID: Int64) async {
        guard favoriteMutationAssetID == nil,
              let current = detail,
              let asset = current.assets.first(where: { $0.id == assetID })
        else {
            return
        }

        favoriteMutationAssetID = assetID
        errorMessage = nil
        let target = !asset.isFavorite
        resource.replace(GalleryGuestMutation.settingFavorite(target, assetID: assetID, in: current))
        defer { favoriteMutationAssetID = nil }

        do {
            let update = try await repository.setFavorite(assetID: assetID, selected: target)
            let confirmed = update.gallery
                ?? GalleryGuestMutation.applying(update.state, to: detail ?? current)
            resource.replace(confirmed)
        } catch is CancellationError {
            resource.replace(
                GalleryGuestMutation.settingFavorite(asset.isFavorite, assetID: assetID, in: detail ?? current)
            )
        } catch {
            resource.replace(
                GalleryGuestMutation.settingFavorite(asset.isFavorite, assetID: assetID, in: detail ?? current)
            )
            errorMessage = "Your favorite wasn’t saved. \(error.localizedDescription)"
        }
    }
}

enum ProtectedDownloadState: Equatable {
    case idle
    case downloading
    case ready(URL)
    case failed(String)
}

@MainActor
@Observable
final class ProtectedDownloadModel {
    private(set) var state: ProtectedDownloadState = .idle
    private var task: Task<Void, Never>?
    private var generation: UInt64 = 0

    func start(
        url: URL,
        filename: String,
        expectedByteCount: Int64?,
        media: any AuthenticatedMediaLoading
    ) {
        task?.cancel()
        generation &+= 1
        let requestGeneration = generation
        state = .downloading
        task = Task { [weak self] in
            var localURL: URL?
            do {
                let downloaded = try await media.download(
                    from: url,
                    suggestedFilename: filename,
                    expectedByteCount: expectedByteCount
                )
                localURL = downloaded
                try Task.checkCancellation()
                guard self?.generation == requestGeneration else {
                    await media.release(downloaded)
                    return
                }
                self?.state = .ready(downloaded)
            } catch is CancellationError {
                if let localURL { await media.release(localURL) }
            } catch {
                if let localURL { await media.release(localURL) }
                guard self?.generation == requestGeneration else { return }
                self?.state = .failed(error.localizedDescription)
            }
        }
    }

    func cancel() {
        task?.cancel()
        task = nil
        generation &+= 1
        if state == .downloading { state = .idle }
    }

    func reset(media: any AuthenticatedMediaLoading) {
        let preparedURL: URL?
        if case let .ready(url) = state {
            preparedURL = url
        } else {
            preparedURL = nil
        }
        cancel()
        state = .idle
        if let preparedURL {
            Task { await media.release(preparedURL) }
        }
    }
}

enum GalleryGuestMutation {
    static func applying(_ state: FavoriteState, to detail: GalleryDetail) -> GalleryDetail {
        settingFavorite(
            state.selected,
            assetID: state.assetID,
            in: detail,
            sectionSelectedCount: state.sectionSelectedCount,
            sectionProofTarget: state.sectionProofTarget
        )
    }

    static func settingFavorite(
        _ selected: Bool,
        assetID: Int64,
        in detail: GalleryDetail,
        sectionSelectedCount: Int? = nil,
        sectionProofTarget: Int? = nil
    ) -> GalleryDetail {
        guard let index = detail.assets.firstIndex(where: { $0.id == assetID }) else {
            return detail
        }
        var assets = detail.assets
        let asset = assets[index]
        let delta = selected == asset.isFavorite ? 0 : (selected ? 1 : -1)
        assets[index] = GalleryAsset(
            id: asset.id,
            galleryID: asset.galleryID,
            sectionID: asset.sectionID,
            kind: asset.kind,
            status: asset.status,
            filename: asset.filename,
            width: asset.width,
            height: asset.height,
            durationSeconds: asset.durationSeconds,
            byteCount: asset.byteCount,
            position: asset.position,
            createdAt: asset.createdAt,
            isFavorite: selected,
            favoriteCount: max(0, asset.favoriteCount + delta),
            links: asset.links,
            altText: asset.altText,
            keywords: asset.keywords,
            keeperScore: asset.keeperScore,
            heroPotential: asset.heroPotential,
            cullState: asset.cullState
        )

        let sections = detail.sections.map { section in
            guard section.id == asset.sectionID else { return section }
            return GallerySection(
                id: section.id,
                galleryID: section.galleryID,
                name: section.name,
                caption: section.caption,
                position: section.position,
                proofTarget: sectionProofTarget ?? section.proofTarget,
                selectedCount: sectionSelectedCount ?? max(0, section.selectedCount + delta)
            )
        }
        let oldSummary = detail.summary
        let summary = GallerySummary(
            id: oldSummary.id,
            title: oldSummary.title,
            slug: oldSummary.slug,
            clientID: oldSummary.clientID,
            projectID: oldSummary.projectID,
            clientName: oldSummary.clientName,
            type: oldSummary.type,
            published: oldSummary.published,
            requiresPIN: oldSummary.requiresPIN,
            contentRevision: oldSummary.contentRevision,
            coverAssetID: oldSummary.coverAssetID,
            expiresOn: oldSummary.expiresOn,
            assetCount: oldSummary.assetCount,
            favoriteCount: max(0, oldSummary.favoriteCount + delta),
            downloadCount: oldSummary.downloadCount,
            deliveryState: oldSummary.deliveryState,
            createdAt: oldSummary.createdAt
        )
        return GalleryDetail(
            summary: summary,
            sections: sections,
            assets: assets,
            heroAssetIDs: detail.heroAssetIDs,
            vision: detail.vision,
            cullEnabled: detail.cullEnabled
        )
    }
}
