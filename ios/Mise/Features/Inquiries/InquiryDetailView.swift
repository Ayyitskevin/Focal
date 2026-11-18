import SwiftUI

struct InquiryDetailView: View {
    private let inquiryID: Int64
    private let initialSummary: InquirySummary
    private let repository: OwnerRepository
    private let canWrite: Bool
    private let onTriage: @MainActor () async -> Void

    @State private var detail: InquiryDetail?
    @State private var isLoading = false
    @State private var loadFailure: String?
    @State private var requestGeneration: UInt64 = 0
    @State private var isMutating = false
    @State private var mutationFailure: String?
    @State private var confirmDismissal = false

    init(
        summary: InquirySummary,
        repository: OwnerRepository,
        canWrite: Bool,
        onTriage: @escaping @MainActor () async -> Void
    ) {
        inquiryID = summary.id
        initialSummary = summary
        self.repository = repository
        self.canWrite = canWrite
        self.onTriage = onTriage
    }

    var body: some View {
        Group {
            if let detail {
                conversation(detail)
            } else if isLoading {
                ProgressView("Loading inquiry…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView {
                    Label("Couldn’t load inquiry", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(loadFailure ?? "The inquiry is unavailable.")
                } actions: {
                    Button("Try Again") { Task { await load() } }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .navigationTitle(detail?.summary.name ?? initialSummary.name)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            guard detail == nil, !isLoading else { return }
            await load()
        }
        .confirmationDialog(
            "Dismiss this inquiry?",
            isPresented: $confirmDismissal,
            titleVisibility: .visible
        ) {
            Button("Dismiss inquiry", role: .destructive) {
                Task { await setDismissed(true) }
            }
            Button("Keep inquiry", role: .cancel) {}
        } message: {
            Text("It will leave the active pipeline. You can restore it later.")
        }
        .alert(
            "Inquiry update",
            isPresented: Binding(
                get: { mutationFailure != nil },
                set: { if !$0 { mutationFailure = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(mutationFailure ?? "")
        }
    }

    private func conversation(_ detail: InquiryDetail) -> some View {
        List {
            Section {
                summary(detail.summary)
            }

            if let loadFailure {
                Section {
                    Label(loadFailure, systemImage: "wifi.slash")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
            }

            Section("Conversation") {
                if detail.hasOlderMessages {
                    Label("Older messages are not included in this view.", systemImage: "clock")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                ForEach(detail.messages) { message in
                    messageBubble(message)
                }
            }

            if canWrite, detail.summary.status != .converted {
                Section {
                    triageButton(for: detail.summary)
                } footer: {
                    Text("Dismissed inquiries stay available and can be restored later.")
                }
            }
        }
        .refreshable { await load() }
    }

    @ViewBuilder
    private func summary(_ summary: InquirySummary) -> some View {
        HStack {
            StatusPill(label: summary.status.ownerDisplayName, tone: summary.status.tone)
            if summary.isReplied {
                Label("Replied", systemImage: "arrowshape.turn.up.left.fill")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        if let business = summary.business, !business.isEmpty {
            LabeledContent("Business", value: business)
        }
        LabeledContent("Source", value: topic(summary))
        if let email = summary.email, !email.isEmpty {
            LabeledContent("Email", value: email)
        }
        if let phone = summary.phone, !phone.isEmpty {
            LabeledContent("Phone", value: phone)
        }
        if let shootOn = summary.shootOn {
            LabeledContent("Shoot date", value: shootOn.rawValue)
        }
        LabeledContent {
            Text(summary.receivedAt, format: .dateTime)
        } label: {
            Text("Received")
        }
    }

    private func messageBubble(_ message: InquiryMessage) -> some View {
        let outbound = message.direction == .outbound
        return VStack(alignment: .leading, spacing: 6) {
            Text(message.body.isEmpty ? "(no message body)" : message.body)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                Text(outbound ? "You" : "Client")
                Text("·")
                Text(message.channel)
                Text("·")
                Text(message.recordedAt, style: .relative)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            if message.isTruncated {
                Label("Message shortened for mobile.", systemImage: "scissors")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: outbound ? .trailing : .leading)
        .background(
            outbound ? MiseDesign.terraTint : Color.secondary.opacity(0.08),
            in: RoundedRectangle(cornerRadius: 12)
        )
        .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
        .listRowSeparator(.hidden)
    }

    @ViewBuilder
    private func triageButton(for summary: InquirySummary) -> some View {
        if isMutating {
            HStack {
                ProgressView()
                Text("Updating inquiry…")
            }
            .frame(minHeight: 44)
        } else if summary.status == .dismissed {
            Button("Restore inquiry", systemImage: "arrow.uturn.backward") {
                Task { await setDismissed(false) }
            }
            .frame(minHeight: 44)
        } else {
            Button(role: .destructive) {
                confirmDismissal = true
            } label: {
                Label("Dismiss inquiry", systemImage: "archivebox")
            }
            .frame(minHeight: 44)
        }
    }

    private func topic(_ summary: InquirySummary) -> String {
        let kind = summary.kind.replacingOccurrences(of: "_", with: " ")
        let display = kind.prefix(1).uppercased() + String(kind.dropFirst())
        if let service = summary.service, !service.isEmpty {
            return "\(display) · \(service)"
        }
        return display
    }

    private func load() async {
        guard !isLoading, !isMutating else { return }
        isLoading = true
        loadFailure = nil
        requestGeneration &+= 1
        let generation = requestGeneration
        defer { isLoading = false }
        do {
            let loaded = try await repository.inquiryDetail(id: inquiryID)
            guard loaded.summary.id == inquiryID else {
                throw OwnerRepositoryError.invalidInquiryResponse
            }
            guard generation == requestGeneration else { return }
            detail = loaded
        } catch {
            guard generation == requestGeneration else { return }
            loadFailure = error.localizedDescription
        }
    }

    private func setDismissed(_ dismissed: Bool) async {
        guard canWrite, !isMutating else { return }
        isMutating = true
        mutationFailure = nil
        requestGeneration &+= 1
        let generation = requestGeneration
        defer { isMutating = false }
        do {
            let updated = try await repository.setInquiryDismissed(
                id: inquiryID,
                dismissed: dismissed
            )
            guard generation == requestGeneration else { return }
            detail = updated
            await onTriage()
        } catch {
            guard generation == requestGeneration else { return }
            mutationFailure = "The server didn’t confirm the change. "
                + "Your last confirmed inquiry is still shown. \(error.localizedDescription)"
        }
    }
}
