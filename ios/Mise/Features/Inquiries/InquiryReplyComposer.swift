import SwiftUI

private struct InquiryReplyAttempt: Sendable {
    let idempotencyKey: UUID
    let subject: String
    let body: String
}

struct InquiryReplyComposer: View {
    private let summary: InquirySummary
    private let repository: OwnerRepository
    private let onSent: @MainActor (InquiryReplyResult) async -> Void

    @Binding private var isPresented: Bool
    @State private var subject: String
    @State private var replyBody = ""
    @State private var attempt: InquiryReplyAttempt?
    @State private var isSending = false
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
                        .textContentType(.subject)
                    TextEditor(text: $replyBody)
                        .frame(minHeight: 220)
                        .accessibilityLabel("Reply body")
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
                        Button("Send email") {
                            Task { await send() }
                        }
                        .disabled(!canSend)
                    }
                }
            }
        }
    }

    private var canSend: Bool {
        !isSending
            && !subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !replyBody.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func send() async {
        guard canSend else { return }
        isSending = true
        notice = nil
        defer { isSending = false }
        let normalizedSubject = subject.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedBody = replyBody.trimmingCharacters(in: .whitespacesAndNewlines)
        if attempt == nil || attempt?.subject != normalizedSubject || attempt?.body != normalizedBody {
            attempt = InquiryReplyAttempt(
                idempotencyKey: UUID(),
                subject: normalizedSubject,
                body: normalizedBody
            )
        }
        guard let attempt else { return }
        do {
            let result = try await repository.replyToInquiry(
                id: summary.id,
                subject: attempt.subject,
                body: attempt.body,
                idempotencyKey: attempt.idempotencyKey
            )
            if result.code == "inquiry.reply_sent" {
                replyBody = ""
                self.attempt = nil
                await onSent(result)
            } else {
                notice = "Delivery is unknown. Check before sending again; no automatic retry was made."
            }
        } catch {
            notice = "The server didn’t confirm the reply. Your draft is preserved. \(error.localizedDescription)"
        }
    }
}
