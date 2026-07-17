import SwiftUI

@MainActor
struct AuthenticationView: View {
    @Bindable var model: AuthenticationCoordinator

    @FocusState private var focusedField: Field?

    private enum Field: Hashable {
        case workspace
        case ownerEmail
        case ownerPassword
        case sharedLink
        case sharedPIN
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(uiColor: .systemGroupedBackground)
                    .ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 24) {
                        brandHeader

                        switch model.flow.screen {
                        case .workspace:
                            workspaceCard
                        case .clientLink:
                            directClientAccessCard
                        case .credentials:
                            credentialsContent
                        }

                        if let errorMessage = model.errorMessage {
                            errorBanner(errorMessage)
                        }
                    }
                    .frame(maxWidth: 640)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 28)
                    .frame(maxWidth: .infinity)
                }
                .scrollDismissesKeyboard(.interactively)
                .disabled(model.isWorking)

                if model.isWorking {
                    workingOverlay
                }
            }
            .navigationTitle("Sign in")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if model.flow.screen != .workspace {
                    ToolbarItem(placement: .topBarLeading) {
                        Button {
                            model.resetWorkspace()
                            focusedField = nil
                        } label: {
                            Label("Start over", systemImage: "chevron.left")
                        }
                    }
                }
            }
        }
    }

    private var brandHeader: some View {
        VStack(spacing: 12) {
            Image(systemName: "camera.aperture")
                .font(.system(size: 48, weight: .medium))
                .foregroundStyle(.tint)
                .accessibilityHidden(true)

            Text("Welcome to Mise")
                .font(.largeTitle.bold())
                .multilineTextAlignment(.center)

            Text("Your studio and client work, wherever the day takes you.")
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .accessibilityElement(children: .combine)
    }

    private var workspaceCard: some View {
        AuthenticationCard(
            title: "Connect your studio",
            subtitle: "Enter a hosted studio slug or the full URL for a custom Mise server.",
            systemImage: "building.2"
        ) {
            TextField("Studio URL or slug", text: $model.workspaceInput)
                .textContentType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .submitLabel(.continue)
                .focused($focusedField, equals: .workspace)
                .onSubmit(discoverWorkspace)
                .accessibilityHint("For example, north-star or studio.example.com")
                .miseInputStyle()

            primaryButton("Continue", systemImage: "arrow.right") {
                await model.discoverWorkspace()
            }
            .disabled(model.workspaceInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

            HStack(spacing: 12) {
                Divider()
                Text("or")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Divider()
            }
            .accessibilityHidden(true)

            Button {
                model.showClientLinkEntry()
                focusedField = .sharedLink
            } label: {
                Label("Open a client access link", systemImage: "link")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)

            // New to Mise? The app never hosts signup — it opens the platform's
            // pricing page in the browser (ADR 0070; game-plan item 4).
            Link(destination: model.platformSignupURL) {
                Text("New to Mise? Start a studio")
                    .font(.footnote.weight(.medium))
                    .frame(maxWidth: .infinity, minHeight: 32)
            }
            .accessibilityHint("Opens the Mise pricing page to create a new studio")
        }
        .onAppear {
            if model.workspaceInput.isEmpty {
                focusedField = .workspace
            }
        }
    }

    private var directClientAccessCard: some View {
        AuthenticationCard(
            title: "Open client access",
            subtitle: "Paste the complete link supplied by the studio. Mise will verify its studio and access type before unlocking it.",
            systemImage: "link.badge.plus"
        ) {
            sharedAccessFields(requiresFullLink: true)
        }
        .onAppear { focusedField = .sharedLink }
    }

    @ViewBuilder
    private var credentialsContent: some View {
        if let selection = model.workspace {
            workspaceSummary(selection)

            if model.availableAuthenticationModes.count > 1 {
                Picker("Sign-in type", selection: $model.authenticationMode) {
                    ForEach(model.availableAuthenticationModes) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .accessibilityHint("Choose studio owner or client access")
            }

            switch model.authenticationMode {
            case .studio:
                ownerCredentialsCard
            case .sharedAccess:
                AuthenticationCard(
                    title: "Client access",
                    subtitle: "Enter the slug from your link, or paste the full link. Select the exact item you were invited to view.",
                    systemImage: "person.crop.circle.badge.checkmark"
                ) {
                    sharedAccessFields(requiresFullLink: false)
                }
            }
        }
    }

    private func workspaceSummary(_ selection: WorkspaceSelection) -> some View {
        HStack(spacing: 14) {
            Image(systemName: "checkmark.seal.fill")
                .font(.title2)
                .foregroundStyle(.green)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(selection.descriptor.studioName)
                    .font(.headline)
                Text(selection.address.origin.host ?? selection.address.origin.absoluteString)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            Text("Verified")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.green)
        }
        .padding(16)
        .background(
            Color(uiColor: .secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Verified studio, \(selection.descriptor.studioName)")
    }

    private var ownerCredentialsCard: some View {
        AuthenticationCard(
            title: "Studio owner",
            subtitle: "Use the same credentials as the Mise admin dashboard. Email is optional for single-owner studios.",
            systemImage: "person.badge.key"
        ) {
            TextField("Email (optional)", text: $model.ownerEmail)
                .textContentType(.username)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.emailAddress)
                .submitLabel(.next)
                .focused($focusedField, equals: .ownerEmail)
                .onSubmit { focusedField = .ownerPassword }
                .miseInputStyle()

            SecureField("Password", text: $model.ownerPassword)
                .textContentType(.password)
                .submitLabel(.go)
                .focused($focusedField, equals: .ownerPassword)
                .onSubmit(signInOwner)
                .privacySensitive()
                .miseInputStyle()

            primaryButton("Sign in", systemImage: "arrow.right.circle.fill") {
                await model.signInStudioOwner()
            }
            .disabled(model.ownerPassword.isEmpty)
        }
    }

    @ViewBuilder
    private func sharedAccessFields(requiresFullLink: Bool) -> some View {
        TextField(
            requiresFullLink ? "Client access link" : "Access link or slug",
            text: $model.sharedAccessInput
        )
        .textContentType(.URL)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .keyboardType(.URL)
        .submitLabel(.next)
        .focused($focusedField, equals: .sharedLink)
        .onSubmit { focusedField = .sharedPIN }
        .privacySensitive()
        .accessibilityHint(
            requiresFullLink
                ? "Paste the complete HTTPS link"
                : "Paste the complete link or enter its slug"
        )
        .miseInputStyle()

        if !requiresFullLink {
            Picker("Access type", selection: $model.selectedCapability) {
                ForEach(SharedAccessCapability.allCases) { capability in
                    Text(capability.title).tag(capability)
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .padding(.horizontal, 12)
            .background(
                Color(uiColor: .tertiarySystemGroupedBackground),
                in: RoundedRectangle(cornerRadius: 12, style: .continuous)
            )
            .accessibilityHint("Used for a slug; a full link supplies its own exact access type")
        }

        SecureField("PIN (if required)", text: $model.sharedAccessPIN)
            .textContentType(.password)
            .keyboardType(.numberPad)
            .submitLabel(.go)
            .focused($focusedField, equals: .sharedPIN)
            .onSubmit(unlockSharedAccess)
            .privacySensitive()
            .miseInputStyle()

        primaryButton("Open access", systemImage: "lock.open.fill") {
            await model.unlockSharedAccess()
        }
        .disabled(model.sharedAccessInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
                .accessibilityHidden(true)
            Text(message)
                .font(.subheadline)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(14)
        .background(.red.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Error: \(message)")
    }

    private var workingOverlay: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.large)
            Text(model.workDescription)
                .font(.headline)
        }
        .padding(24)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
        .shadow(radius: 12, y: 6)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(model.workDescription)
    }

    private func primaryButton(
        _ title: String,
        systemImage: String,
        action: @escaping @MainActor () async -> Void
    ) -> some View {
        Button {
            focusedField = nil
            Task { await action() }
        } label: {
            Label(title, systemImage: systemImage)
                .frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
    }

    private func discoverWorkspace() {
        guard !model.workspaceInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return
        }
        focusedField = nil
        Task { await model.discoverWorkspace() }
    }

    private func signInOwner() {
        guard !model.ownerPassword.isEmpty else { return }
        focusedField = nil
        Task { await model.signInStudioOwner() }
    }

    private func unlockSharedAccess() {
        guard !model.sharedAccessInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return
        }
        focusedField = nil
        Task { await model.unlockSharedAccess() }
    }
}

@MainActor
struct AppLockView: View {
    @Bindable var model: AuthenticationCoordinator
    let session: CurrentSession
    let biometricKind: BiometricKind

    var body: some View {
        ZStack {
            Color(uiColor: .systemGroupedBackground)
                .ignoresSafeArea()

            VStack(spacing: 22) {
                Image(systemName: biometricKind.systemImage)
                    .font(.system(size: 58, weight: .medium))
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)

                VStack(spacing: 8) {
                    Text("Mise is locked")
                        .font(.largeTitle.bold())
                    Text("Use \(biometricKind.displayName) to open \(session.workspace.displayName).")
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .accessibilityElement(children: .combine)

                if let errorMessage = model.errorMessage {
                    Text(errorMessage)
                        .font(.subheadline)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .accessibilityLabel("Error: \(errorMessage)")
                }

                Button {
                    Task { await model.unlockApp() }
                } label: {
                    if model.isUnlocking {
                        ProgressView()
                            .frame(maxWidth: .infinity, minHeight: 44)
                    } else {
                        Label("Unlock with \(biometricKind.displayName)", systemImage: biometricKind.systemImage)
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(model.isUnlocking)

                Button("Sign out", role: .destructive) {
                    Task { await model.signOut() }
                }
                .frame(minHeight: 44)
                .disabled(model.isUnlocking)
            }
            .frame(maxWidth: 440)
            .padding(28)
        }
    }
}

private struct AuthenticationCard<Content: View>: View {
    let title: String
    let subtitle: String
    let systemImage: String
    let content: Content

    init(
        title: String,
        subtitle: String,
        systemImage: String,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.subtitle = subtitle
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(title, systemImage: systemImage)
                .font(.title3.bold())

            Text(subtitle)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            content
        }
        .padding(20)
        .background(
            Color(uiColor: .secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 20, style: .continuous)
        )
    }
}

private extension View {
    func miseInputStyle() -> some View {
        frame(minHeight: 44)
            .padding(.horizontal, 12)
            .background(
                Color(uiColor: .tertiarySystemGroupedBackground),
                in: RoundedRectangle(cornerRadius: 12, style: .continuous)
            )
    }
}
