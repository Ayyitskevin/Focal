import SwiftUI

struct InquiryReplyComposer: View {
    private let summary: InquirySummary
    private let repository: OwnerRepository
    private let onSent: @MainActor (InquiryReplyResult) async -> Void

    @Binding private var isPresented: Bool
    @State private var subject: String
    @State private var replyBody = ""
    @State private var attempt: InquiryReplyDraft?
    @State private var draftLoaded = false
    @State private var isSending = false
    @State private var confirmSend = false
    @State private var notice: String?

    init(
        summary: InquirySummary,
        repository: OwnerRepository,
        isPresented: Binding<Bool>,
        onSent: @escaping @MainActor (InquiryReplyResult) async -> Void
    ) {
        self.summary = summary
        self.repository = repository
        self.onSent = onSent
        _isPresented = isPresented
        let kind = summary.kind == "booking" ? "booking request" : "inquiry"
        _subject = State(initialValue: "Re: your \(kind)")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Recipient") {
                    if let email = summary.email {
                        Text(email)
                    }
                }
                Section("Message") {
                    TextField("Subject", text: $subject)
                    TextEditor(text: $replyBody)
                        .frame(minHeight: 220)
                        .accessibilityLabel("Reply body")
                }
                .disabled(!draftLoaded || isSending)
                if attempt != nil {
                    Section {
                        Text("Delivery is unconfirmed. The customer may already have received the original email.")
                            .font(.footnote)
                    }
                }
                if let notice {
                    Section {
                        Text(notice)
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }
                }
            }
            .navigationTitle("Reply by email")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { isPresented = false }
                        .disabled(isSending)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if isSending {
                        ProgressView()
                    } else {
                        Button("Review") { confirmSend = true }
                            .disabled(!canSend)
                    }
                }
            }
            .confirmationDialog(
                confirmationTitle,
                isPresented: $confirmSend,
                titleVisibility: .visible
            ) {
                Button(confirmationAction) {
                    Task { await send() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                if let email = summary.email {
                    if replacesAttempt {
                        Text("This starts a new email to \(email). If the original went through, the customer could receive both.")
                    } else if attempt != nil {
                        Text("Retry the same email to \(email). If Focal never received the original request, it will send the email now.")
                    } else {
                        Text("Send to \(email)? SMTP acceptance does not guarantee inbox delivery.")
                    }
                }
            }
            .interactiveDismissDisabled(isSending)
            .task {
                guard !draftLoaded else { return }
                do {
                    if let saved = try await repository.pendingInquiryReply(id: summary.id) {
                        attempt = saved
                        subject = saved.subject
                        replyBody = saved.body
                    }
                    draftLoaded = true
                } catch {
                    notice = "The saved reply draft could not be read. \(error.localizedDescription)"
                }
            }
        }
    }

    private var canSend: Bool {
        draftLoaded && !isSending
            && !subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !replyBody.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var replacesAttempt: Bool {
        guard let attempt else { return false }
        return subject.trimmingCharacters(in: .whitespacesAndNewlines) != attempt.subject
            || replyBody.trimmingCharacters(in: .whitespacesAndNewlines) != attempt.body
    }

    private var confirmationTitle: String {
        if replacesAttempt { return "Send the edited reply as a new email?" }
        return attempt == nil ? "Send this email?" : "Retry the original email?"
    }

    private var confirmationAction: String {
        if replacesAttempt { return "Send as new email" }
        return attempt == nil ? "Send email" : "Retry original email"
    }

    private func send() async {
        guard canSend else { return }
        isSending = true
        notice = nil
        defer { isSending = false }
        do {
            let prepared = try await repository.prepareInquiryReply(
                id: summary.id,
                subject: subject,
                body: replyBody,
                replacing: attempt
            )
            attempt = prepared
            let result = try await repository.replyToInquiry(draft: prepared)
            if result.code == "inquiry.reply_sent" {
                replyBody = ""
                attempt = nil
                await onSent(result)
            }
        } catch OwnerRepositoryError.pendingInquiryReplyConflict {
            draftLoaded = false
            notice = "The saved reply changed. Close and reopen to review it before sending."
        } catch {
            notice = "The reply was not confirmed. \(error.localizedDescription)"
        }
    }
}
