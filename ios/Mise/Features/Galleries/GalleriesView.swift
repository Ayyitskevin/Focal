import SwiftUI

struct GalleriesView: View {
    let model: OwnerResourceModel<[GallerySummary]>
    let repository: OwnerRepository
    let media: any AuthenticatedMediaLoading
    let canDecideCull: Bool

    var body: some View {
        OwnerResourceView(
            model: model,
            isEmpty: { $0.isEmpty },
            content: galleryList,
            empty: {
                ContentUnavailableView(
                    "No galleries",
                    systemImage: "photo.on.rectangle",
                    description: Text("Gallery deliveries will appear here.")
                )
            }
        )
        .navigationTitle("Galleries")
    }

    private func galleryList(_ galleries: [GallerySummary]) -> some View {
        List(galleries) { gallery in
            NavigationLink {
                GalleryDetailView(
                    repository: repository,
                    media: media,
                    gallery: gallery,
                    canDecideCull: canDecideCull,
                    didCullChange: { await model.refresh() }
                )
            } label: {
                HStack(spacing: 14) {
                    Image(systemName: gallery.type == .drop ? "bolt.fill" : "photo.stack")
                        .font(.title2)
                        .foregroundStyle(.tint)
                        .frame(width: 42, height: 42)
                        .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(gallery.title).font(.headline)
                        Text(gallery.clientName ?? "Unassigned client")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        Text("\(gallery.assetCount) assets · \(gallery.deliveryState.ownerDisplayName)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 3)
                .accessibilityElement(children: .combine)
            }
        }
        .refreshable { await model.refresh() }
    }
}
