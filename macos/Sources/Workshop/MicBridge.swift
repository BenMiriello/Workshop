import AppKit
import AVFoundation

/// The app's microphone permission, for the web UI (shared/native-bridge.ts
/// `mic`). WebKit inside the app cannot raise the system prompt for its host,
/// and a page cannot open System Settings, so both come through here.
@MainActor
enum MicBridge {
    static let settingsPane = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"

    static func install(on bridge: Bridge) {
        bridge.handle("mic", "status") { _, _ in state() }
        bridge.handle("mic", "request") { _, _ in
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                _ = await AVCaptureDevice.requestAccess(for: .audio)
            }
            return state()
        }
        bridge.handle("mic", "openSettings") { _, _ in
            if let url = URL(string: settingsPane) { NSWorkspace.shared.open(url) }
            return nil
        }
    }

    private static func state() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "granted"
        case .denied: return "denied"
        case .restricted: return "restricted"
        default: return "undetermined"
        }
    }
}
