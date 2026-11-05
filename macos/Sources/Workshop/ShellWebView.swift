import AppKit
import WebKit

/// The shell's WKWebView. It draws no background of its own and sits over
/// the page views (`MacPages`), so a page shows wherever the web UI draws
/// nothing; a click or scroll there is the page's (`passes`), and so is the
/// pointer, so the shell does not set its cursor over a page.
///
/// A right-click on a text selection gets "Read aloud" after Copy, with every
/// system item kept (Look Up, Translate, Share, Speech...). It shows exactly
/// when Copy does, and hands the selection to the web UI's reader through the
/// function the bridge script installs (mobile/native/readSelection.ts).
final class ShellWebView: WKWebView {
    static let copyItem = "WKMenuItemIdentifierCopy"
    static let readSelection = "window.__workshopReadSelection && window.__workshopReadSelection(); true;"

    /// Whether input at a point, in the superview's coordinates, belongs to a
    /// page view under this one.
    var passes: ((NSPoint) -> Bool)?

    override init(frame: CGRect, configuration: WKWebViewConfiguration) {
        super.init(frame: frame, configuration: configuration)
        setValue(false, forKey: "drawsBackground")
        underPageBackgroundColor = .clear
    }

    required init?(coder: NSCoder) { nil }

    override func hitTest(_ point: NSPoint) -> NSView? {
        passes?(point) == true ? nil : super.hitTest(point)
    }

    override func mouseMoved(with event: NSEvent) {
        if let at = superview?.convert(event.locationInWindow, from: nil), passes?(at) == true { return }
        super.mouseMoved(with: event)
    }

    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        super.willOpenMenu(menu, with: event)
        guard let copy = menu.items.firstIndex(where: { $0.identifier?.rawValue == Self.copyItem }) else {
            return
        }
        let item = NSMenuItem(title: "Read aloud", action: #selector(readAloud(_:)), keyEquivalent: "")
        item.target = self
        menu.insertItem(item, at: copy + 1)
    }

    @objc private func readAloud(_ sender: Any?) {
        evaluateJavaScript(Self.readSelection)
    }
}
