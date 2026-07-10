import LocalAuthentication

struct BiometricUnlockService: Sendable {
    @MainActor
    func availableKind() -> BiometricKind {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &error
        ) else {
            return .unavailable
        }

        switch context.biometryType {
        case .faceID: return .faceID
        case .touchID: return .touchID
        case .opticID: return .opticID
        case .none: return .unavailable
        @unknown default: return .unavailable
        }
    }

    @MainActor
    func unlock(reason: String) async throws {
        let context = LAContext()
        context.localizedCancelTitle = "Cancel"

        var policyError: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &policyError
        ) else {
            throw BiometricUnlockError.unavailable
        }

        do {
            let succeeded = try await context.evaluatePolicy(
                .deviceOwnerAuthenticationWithBiometrics,
                localizedReason: reason
            )
            if !succeeded {
                throw BiometricUnlockError.failed
            }
        } catch let error as LAError where error.code == .userCancel {
            throw BiometricUnlockError.cancelled
        } catch {
            throw BiometricUnlockError.failed
        }
    }
}
enum BiometricKind: Sendable {
    case faceID
    case touchID
    case opticID
    case unavailable
}

enum BiometricUnlockError: LocalizedError, Sendable {
    case unavailable
    case cancelled
    case failed

    var errorDescription: String? {
        switch self {
        case .unavailable:
            "Biometric unlock is not available on this device."
        case .cancelled:
            "Biometric unlock was cancelled."
        case .failed:
            "Biometric unlock failed."
        }
    }
}
