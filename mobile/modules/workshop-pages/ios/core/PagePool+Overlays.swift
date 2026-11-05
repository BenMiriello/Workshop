import Foundation

/// What the web UI draws over every page view of every app (its bars,
/// notices, menus and dialogs), in the shell page's CSS pixels, as the web UI
/// last reported it (`shell.overlays`, shared/native-bridge.ts).
struct PageOverlays {
    var rects: [PageFrame] = []
    var modal = false
}

extension PagePool {
    /// Gives the web UI's own layers the input over every page, kept apart
    /// from the cutouts each app sets, so no app has to know what the shell drew.
    func setOverlays(_ rects: [PageFrame], modal: Bool) {
        overlays = PageOverlays(rects: rects, modal: modal)
        pages.values.forEach(applyOverlays)
    }

    /// Applies the current overlays to `page`, once it is back if WebKit holds
    /// it in full screen. The rects are converted now, so a page that moves
    /// keeps its holes where the web UI drew them.
    func applyOverlays(_ page: PageView) {
        let now = overlays
        page.whenPlaced(.overlays) { [host] in
            page.webView.setOverlays(now.rects.map(host.frame), modal: now.modal)
        }
    }
}
