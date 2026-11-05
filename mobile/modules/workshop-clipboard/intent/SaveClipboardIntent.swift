import AppIntents
import UIKit
import WorkshopClipboard

// Compiled into the app target by plugins/withClipboardIntent.js: Shortcuts
// finds App Intents only in the app's own sources, not in a module pod.

/// Saves text to Workshop's copies the way a copy is saved: the text passed
/// in, or the clipboard's when none is.
struct SaveClipboardToWorkshop: AppIntent {
    static var title: LocalizedStringResource = "Save Clipboard to Workshop"
    static var description = IntentDescription(
        "Saves the clipboard's text to Workshop, or the text given instead.")
    static var openAppWhenRun = false

    @Parameter(title: "Text")
    var text: String?

    @MainActor
    func perform() async throws -> some IntentResult {
        let value = text ?? UIPasteboard.general.string ?? ""
        try await ClipboardCapture.save(value)
        ClipboardCapture.markSeen()
        return .result()
    }
}

struct WorkshopShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: SaveClipboardToWorkshop(),
            phrases: ["Save clipboard to \(.applicationName)"])
    }
}
