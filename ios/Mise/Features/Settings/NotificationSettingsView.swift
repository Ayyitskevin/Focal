import SwiftUI

@MainActor
struct NotificationSettingsView: View {
    let notifications: NotificationCoordinator

    @State private var draft = NotificationPreferences.defaults

    var body: some View {
        Form {
            Section {
                permissionContent
            } header: {
                Text("Device permission")
            } footer: {
                Text("Mise asks only after you choose to enable timely studio updates. Notification previews are also controlled by iOS.")
            }

            if let registration = notifications.registration {
                Section("Studio updates") {
                    preferenceToggle("New bookings", isOn: $draft.newBookings)
                    preferenceToggle("Booking changes", isOn: $draft.bookingChanges)
                    preferenceToggle("Proposal responses", isOn: $draft.proposalResponses)
                    preferenceToggle("Payments", isOn: $draft.payments)

                    Button("Save preferences") {
                        Task {
                            await notifications.savePreferences(draft)
                        }
                    }
                    .disabled(
                        notifications.isWorking
                            || draft == registration.preferences
                            || !registration.active
                    )
                }
            } else if notifications.permissionState.canRegisterWithAPNs {
                Section {
                    HStack {
                        ProgressView()
                        Text("Connecting this device to the studio…")
                    }
                }
            }

            if let failure = notifications.registrationFailureMessage {
                Section {
                    Label(failure, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
            }
            if let error = notifications.errorMessage {
                Section {
                    Text(error).foregroundStyle(.red)
                }
            }

            Section("Privacy") {
                Text("The APNs token stays in memory on this device and is sent only to the active studio. Mise never places credentials, client names, payment amounts, or capability links in a notification route.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Notifications")
        .navigationBarTitleDisplayMode(.inline)
        .disabled(notifications.isWorking)
        .overlay {
            if notifications.isWorking {
                ProgressView().controlSize(.large)
            }
        }
        .task {
            if let preferences = notifications.registration?.preferences {
                draft = preferences
            }
        }
        .onChange(of: notifications.registration?.preferences) { oldValue, newValue in
            guard let newValue else { return }
            if oldValue == nil || draft == oldValue {
                draft = newValue
            }
        }
    }

    @ViewBuilder
    private var permissionContent: some View {
        switch notifications.permissionState {
        case .loading:
            HStack {
                ProgressView()
                Text("Checking notification access…")
            }
        case .notDetermined:
            VStack(alignment: .leading, spacing: 10) {
                Label("Stay ahead of bookings and payments", systemImage: "bell.badge")
                    .font(.headline)
                Text("Enable alerts when you’re ready. Mise never prompts on first launch.")
                    .foregroundStyle(.secondary)
                Button("Enable notifications") {
                    Task { await notifications.requestPermissionFromUser() }
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(.vertical, 4)
        case .denied:
            VStack(alignment: .leading, spacing: 8) {
                Label("Notifications are off", systemImage: "bell.slash")
                Text("Allow notifications in iOS Settings to receive studio updates.")
                    .foregroundStyle(.secondary)
                Button("Open notification settings") {
                    notifications.openSystemSettings()
                }
            }
        case .authorized:
            Label("Notifications are enabled", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .provisional:
            Label("Notifications are delivered quietly", systemImage: "bell")
        case .ephemeral:
            Label("Notifications are temporarily enabled", systemImage: "clock.badge.checkmark")
        case .unsupported:
            VStack(alignment: .leading, spacing: 8) {
                Label("Notification access is unavailable", systemImage: "bell.slash")
                Text("Review notification access in iOS Settings.")
                    .foregroundStyle(.secondary)
                Button("Open notification settings") {
                    notifications.openSystemSettings()
                }
            }
        }
    }

    private func preferenceToggle(_ title: String, isOn: Binding<Bool>) -> some View {
        Toggle(title, isOn: isOn)
    }
}
