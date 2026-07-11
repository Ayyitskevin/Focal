import Foundation
import Observation

@MainActor
@Observable
final class CullReviewModel {
    private struct DecisionIntent: Hashable {
        let assetID: Int64
        let action: CullAction
        let etag: String
    }

    private(set) var items: [CullItem] = []
    private(set) var counts = CullCounts(total: 0, keep: 0, cut: 0, undecided: 0, scored: 0)
    private(set) var hasMore = false
    private(set) var isLoading = false
    private(set) var isRefreshing = false
    private(set) var isLoadingMore = false
    private(set) var mutatingAssetIDs = Set<Int64>()
    private(set) var errorMessage: String?
    private(set) var savedAt: Date?

    private let repository: OwnerRepository
    private let galleryID: Int64
    private var nextCursor: String?
    private var seenCursors = Set<String>()
    private var commandKeys: [DecisionIntent: UUID] = [:]
    private var loaded = false
    private var pageGeneration: UInt64 = 0

    init(repository: OwnerRepository, galleryID: Int64) {
        self.repository = repository
        self.galleryID = galleryID
    }

    func load() async {
        guard !loaded else { return }
        loaded = true
        isLoading = true
        errorMessage = nil

        if let cached = try? await repository.cachedCullPage(galleryID: galleryID) {
            applyFirstPage(cached.value)
            savedAt = cached.storedAt
        }
        defer { isLoading = false }
        _ = await refresh()
    }

    @discardableResult
    func refresh() async -> Bool {
        guard !isRefreshing else { return false }
        pageGeneration &+= 1
        let generation = pageGeneration
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let snapshot = try await repository.refreshCullPage(galleryID: galleryID)
            guard generation == pageGeneration else { return false }
            applyFirstPage(snapshot.value)
            savedAt = snapshot.storedAt
            errorMessage = nil
            return true
        } catch is CancellationError {
            return false
        } catch let APIError.notFound(_) {
            if generation == pageGeneration {
                errorMessage = "Cull review is not enabled for this studio."
            }
            return false
        } catch {
            if generation == pageGeneration {
                errorMessage = items.isEmpty
                    ? error.localizedDescription
                    : "Offline — showing the last saved cull review. \(error.localizedDescription)"
            }
            return false
        }
    }

    func loadMore() async {
        guard hasMore,
              !isLoadingMore,
              !isRefreshing,
              let cursor = nextCursor,
              !cursor.isEmpty
        else {
            return
        }
        guard seenCursors.insert(cursor).inserted else {
            errorMessage = OwnerRepositoryError.invalidPagination.localizedDescription
            return
        }

        let generation = pageGeneration
        isLoadingMore = true
        defer { isLoadingMore = false }
        do {
            let page = try await repository.nextCullPage(
                galleryID: galleryID,
                cursor: cursor
            )
            guard generation == pageGeneration else {
                seenCursors.remove(cursor)
                return
            }
            let existing = Set(items.map(\.assetID))
            guard existing.isDisjoint(with: Set(page.items.map(\.assetID))),
                  !page.hasMore || page.nextCursor != cursor
            else {
                throw OwnerRepositoryError.invalidPagination
            }
            items.append(contentsOf: page.items)
            counts = page.counts
            hasMore = page.hasMore
            nextCursor = page.nextCursor
            errorMessage = nil
        } catch is CancellationError {
            seenCursors.remove(cursor)
        } catch let APIError.conflict(problem)
            where problem?.code == "pagination.collection_changed"
        {
            seenCursors.remove(cursor)
            guard generation == pageGeneration else { return }
            // A score-ranked continuation is no longer authoritative. Do not
            // retry it: retain the visible snapshot and reconcile from page one.
            hasMore = false
            nextCursor = nil
            let reloaded = await refresh()
            errorMessage = reloaded
                ? "Cull scores changed while you were paging. Mise reloaded the review from the top."
                : "Cull scores changed while you were paging, but Mise could not reload them. Pull to refresh when connected."
        } catch {
            seenCursors.remove(cursor)
            if generation == pageGeneration {
                errorMessage = error.localizedDescription
            }
        }
    }

    @discardableResult
    func decide(_ action: CullAction, assetID: Int64) async -> Bool {
        guard let item = items.first(where: { $0.assetID == assetID }),
              item.galleryID == galleryID,
              !mutatingAssetIDs.contains(assetID)
        else {
            return false
        }
        let intent = DecisionIntent(assetID: item.assetID, action: action, etag: item.etag)
        let key = commandKeys[intent] ?? UUID()
        commandKeys[intent] = key
        mutatingAssetIDs.insert(item.assetID)
        errorMessage = nil
        defer { mutatingAssetIDs.remove(item.assetID) }

        do {
            let updated = try await repository.decideCull(
                galleryID: galleryID,
                item: item,
                action: action,
                idempotencyKey: key
            )
            guard let index = items.firstIndex(where: { $0.assetID == item.assetID }) else {
                throw APIError.unexpectedResponse
            }
            pageGeneration &+= 1
            let previousState = items[index].state
            items[index] = updated
            counts = counts.replacing(previousState, with: updated.state)
            commandKeys.removeValue(forKey: intent)
            return true
        } catch let APIError.conflict(_) {
            commandKeys.removeValue(forKey: intent)
            let reloaded = await refresh()
            errorMessage = reloaded
                ? "This frame changed on another device. Mise reloaded the latest review."
                : "This frame changed on another device, but Mise could not reload it. Try again when connected."
            return false
        } catch {
            // Keep the key for an exact retry after an ambiguous transport/server
            // failure. A changed action or ETag naturally creates a new intent.
            errorMessage = "Mise could not confirm that decision. Try the same action again. \(error.localizedDescription)"
            return false
        }
    }

    func isMutating(assetID: Int64) -> Bool {
        mutatingAssetIDs.contains(assetID)
    }

    func item(assetID: Int64) -> CullItem? {
        items.first { $0.assetID == assetID }
    }

    func dismissError() {
        errorMessage = nil
    }

    private func applyFirstPage(_ page: CullPage) {
        items = page.items
        counts = page.counts
        hasMore = page.hasMore
        nextCursor = page.nextCursor
        seenCursors.removeAll(keepingCapacity: true)
    }
}

private extension CullCounts {
    func replacing(_ oldState: CullState?, with newState: CullState?) -> CullCounts {
        var keep = keep
        var cut = cut
        var undecided = undecided

        switch oldState {
        case .keep: keep = max(0, keep - 1)
        case .cut: cut = max(0, cut - 1)
        default: undecided = max(0, undecided - 1)
        }
        switch newState {
        case .keep: keep += 1
        case .cut: cut += 1
        default: undecided += 1
        }
        return CullCounts(
            total: total,
            keep: keep,
            cut: cut,
            undecided: undecided,
            scored: scored
        )
    }
}
