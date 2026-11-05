import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// Puts a clip on the clipboard and pastes it into the app the operator was
/// in. The paste is a posted Cmd+V, which needs Accessibility; without it the
/// clip stays on the clipboard, and the first time that happens the operator
/// is told how to let Workshop paste.
@MainActor
final class PasteInto {
    static let toldKey = "pasteAccessibilityTold"
    /// Long enough for the app brought back to take keys again.
    static let settle: TimeInterval = 0.15

    private let write: (String) -> Bool

    init(write: @escaping (String) -> Bool) {
        self.write = write
    }

    func paste(_ text: String, into app: NSRunningApplication?) {
        guard write(text) else { return }
        app?.activate(options: [])
        guard AXIsProcessTrusted() else {
            tellOnce(then: app)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.settle) { Self.commandV() }
    }

    private static func commandV() {
        let source = CGEventSource(stateID: .combinedSessionState)
        let key = CGKeyCode(kVK_ANSI_V)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: down)
            event?.flags = .maskCommand
            event?.post(tap: .cghidEventTap)
        }
    }

    private func tellOnce(then app: NSRunningApplication?) {
        let defaults = UserDefaults.standard
        guard !defaults.bool(forKey: Self.toldKey) else { return }
        defaults.set(true, forKey: Self.toldKey)
        let alert = NSAlert()
        alert.messageText = "The clip is on the clipboard"
        alert.informativeText = "Press Command-V to paste it. To have Workshop paste it for you, "
            + "allow Workshop under Privacy & Security > Accessibility in System Settings."
        alert.addButton(withTitle: "Allow Pasting")
        alert.addButton(withTitle: "Not Now")
        NSApp.activate()
        let allow = alert.runModal() == .alertFirstButtonReturn
        if allow {
            // Lists Workshop under Accessibility and shows the system's own prompt.
            let prompt = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
            _ = AXIsProcessTrustedWithOptions([prompt: true] as CFDictionary)
        } else {
            app?.activate(options: [])
        }
    }
}
