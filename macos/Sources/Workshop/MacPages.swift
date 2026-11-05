import AppKit
import WebKit

/// The Mac host for page views: they sit in the window's flipped container
/// under the shell's see-through web view, the newest on top of the other
/// pages, so whatever the web UI draws shows over them. The shell's web view
/// passes a click to the page under it (`ShellWebView.passes`). At page zoom 1
/// a CSS pixel of the shell page is a point, so a reported rect is a frame as
/// it stands.
@MainActor
final class MacPages: PageHost {
    private unowned let container: NSView
    private unowned let shell: WKWebView
    private(set) lazy var pool = PagePool(host: self)

    init(container: NSView, shell: WKWebView) {
        self.container = container
        self.shell = shell
    }

    func ensureReady() throws {}

    func attach(_ view: PageWebView) {
        container.addSubview(view, positioned: .below, relativeTo: shell)
    }

    /// Sets `view`'s frame, putting it back under the shell's web view if
    /// anything (WebKit's full screen giving it back) left it over it.
    func place(_ view: PageWebView, _ rect: PageFrame) {
        let views = container.subviews
        if view.superview === container, let at = views.firstIndex(of: view),
           let shellAt = views.firstIndex(of: shell), at > shellAt {
            container.addSubview(view, positioned: .below, relativeTo: shell)
        }
        view.frame = frame(rect)
    }

    func frame(_ rect: PageFrame) -> CGRect {
        let origin = shell.frame.origin
        return NSRect(
            x: origin.x + rect.x,
            y: origin.y + rect.y,
            width: max(0, rect.width),
            height: max(0, rect.height)
        )
    }

    /// Brings `view` up; when a page view had focus (or one just closed with
    /// it), the page shown now takes it, so page commands keep reaching pages.
    func bringToFront(_ view: PageWebView) {
        container.addSubview(view, positioned: .below, relativeTo: shell)
        guard let window = container.window, !view.isHidden else { return }
        let focus = window.firstResponder
        if focus === window || focus is PageWebView { window.makeFirstResponder(view) }
    }

    func present(_ dialog: PageDialog, from view: PageWebView) async -> PageDialogAnswer {
        let window = view.window ?? container.window
        switch dialog.kind {
        case .alert:
            _ = await Dialogs.show(dialog.message, site: dialog.site, buttons: ["OK"], in: window)
            return PageDialogAnswer(ok: true, text: nil)
        case .confirm:
            let ok = await Dialogs.show(dialog.message, site: dialog.site, buttons: ["OK", "Cancel"], in: window) == 0
            return PageDialogAnswer(ok: ok, text: nil)
        case .prompt(let defaultText):
            let text = await Dialogs.prompt(dialog.message, defaultText: defaultText, site: dialog.site, in: window)
            return PageDialogAnswer(ok: text != nil, text: text)
        }
    }
}
