import Foundation
import WebKit

/// What the shell asks of where a page view stands, in the order they are
/// applied when one has waited out a full screen.
enum PagePlacement: CaseIterable {
    case rect, cutouts, overlays, front
}

/// Element full screen (the Fullscreen API, a site player's full screen
/// button). WebKit takes the page's view out of the shell's window into a
/// window of its own, and on the way out puts it back where it stood. Until
/// then the view is WebKit's: the shell's moves, layering, cutouts and overlays wait and
/// are applied once it is back, the latest of each kind.
extension PageView {
    /// Calls `changed` on the main actor whenever WebKit's full screen state moves.
    func watchFullscreen(_ changed: @escaping @MainActor () -> Void) -> NSKeyValueObservation? {
        guard #available(iOS 16.0, macOS 13.0, *) else { return nil }
        return webView.observe(\.fullscreenState) { _, _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { changed() } }
        }
    }

    /// Whether WebKit holds the view for full screen, entering and leaving included.
    var webKitFullscreen: Bool {
        guard #available(iOS 16.0, macOS 13.0, *) else { return false }
        return webView.fullscreenState != .notInFullscreen
    }

    /// Does `change` now, or once the view is back from full screen.
    func whenPlaced(_ kind: PagePlacement, _ change: @escaping () -> Void) {
        guard fullscreen || webKitFullscreen else { return change() }
        placements[kind] = change
    }

    func fullscreenStateChanged() {
        guard webView.navigationDelegate != nil else { return }
        let now = webKitFullscreen
        guard now != fullscreen else { return }
        fullscreen = now
        if !now {
            let waiting = placements
            placements = [:]
            PagePlacement.allCases.forEach { waiting[$0]?() }
        }
        onFullscreen?(self)
    }

    /// Takes the view off screen; one in full screen leaves it first, so
    /// WebKit puts it back before it goes.
    func removeView() {
        guard webKitFullscreen else { return webView.removeFromSuperview() }
        let view = webView
        view.closeAllMediaPresentations { view.removeFromSuperview() }
    }
}
