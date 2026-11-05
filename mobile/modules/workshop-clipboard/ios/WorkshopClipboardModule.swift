import ExpoModulesCore
import UIKit

// The system pasteboard for the shell's web UI. A page in a WKWebView may write
// the clipboard only inside a user gesture; this has no such limit. The watch
// and the capture are ClipboardCapture's.
public class WorkshopClipboardModule: Module {
    public func definition() -> ModuleDefinition {
        Name("WorkshopClipboard")

        AsyncFunction("setString") { (text: String) -> Bool in
            UIPasteboard.general.string = text
            ClipboardCapture.markSeen()
            return true
        }.runOnQueue(.main)

        AsyncFunction("watchGet") { () -> String in
            ClipboardCapture.mode
        }.runOnQueue(.main)

        AsyncFunction("watchSet") { (mode: String) throws -> String in
            try ClipboardCapture.setMode(mode)
        }.runOnQueue(.main)

        AsyncFunction("configure") { (apiBase: String) in
            ClipboardCapture.configure(apiBase: apiBase)
        }.runOnQueue(.main)

        AsyncFunction("markSeen") {
            ClipboardCapture.markSeen()
        }.runOnQueue(.main)

        AsyncFunction("share") { () async in
            await ClipboardCapture.share()
        }

        AsyncFunction("captureNew") { () async throws -> Bool in
            try await ClipboardCapture.captureNew()
        }
    }
}
