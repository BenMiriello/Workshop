import AppKit

/// JavaScript alert, confirm and prompt as sheets on the window, named by the
/// site that asked.
@MainActor
enum Dialogs {
    /// The index of the button pressed.
    static func show(_ message: String, site: URL?, buttons: [String], in window: NSWindow?) async -> Int {
        let alert = alert(message, site: site)
        buttons.forEach { alert.addButton(withTitle: $0) }
        let response = await run(alert, in: window)
        return response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
    }

    static func prompt(_ message: String, defaultText: String?, site: URL?, in window: NSWindow?) async -> String? {
        let alert = alert(message, site: site)
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(string: defaultText ?? "")
        field.frame = NSRect(x: 0, y: 0, width: 280, height: 24)
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        let response = await run(alert, in: window)
        return response == .alertFirstButtonReturn ? field.stringValue : nil
    }

    private static func alert(_ message: String, site: URL?) -> NSAlert {
        let alert = NSAlert()
        alert.messageText = site?.host ?? "This page says"
        alert.informativeText = message
        return alert
    }

    private static func run(_ alert: NSAlert, in window: NSWindow?) async -> NSApplication.ModalResponse {
        guard let window else { return alert.runModal() }
        return await alert.beginSheetModal(for: window)
    }
}
