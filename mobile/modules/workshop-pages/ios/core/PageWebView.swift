import Foundation
import WebKit
#if canImport(UIKit)
import UIKit
#else
import AppKit
#endif

#if os(macOS)
typealias PageWebViewBase = PointerSharingWebView
#else
class PageWebViewBase: WKWebView {
    func coverChanged() {}
}
#endif

/// The WKWebView class page views use, so the host lookup can tell them apart
/// from the shell's own WebView. On the Mac it notes the person's clicks and
/// keys for the page's navigation rules (on iOS, `PageTouchWatch` does), and
/// keeps the pointer from the page where the web UI over it takes input
/// (`PointerSharingWebView`).
final class PageWebView: PageWebViewBase {
    /// The page this view shows, for the commands a host sends from it.
    weak var page: PageView?

    /// Where the web UI draws over this view and takes its input (`PageHoles`).
    private var holes = PageHoles()

    func setCutouts(_ rects: [CGRect], modal: Bool = true) {
        holes.cutouts = rects.filter { !$0.isEmpty }
        holes.cutoutsModal = modal
        coverChanged()
    }

    func setOverlays(_ rects: [CGRect], modal: Bool) {
        holes.overlays = rects.filter { !$0.isEmpty }
        holes.overlaysModal = modal
        coverChanged()
    }

    /// Whether a click or touch at `point`, in the superview's coordinates,
    /// is this page's: the page is drawn there and the web UI over it does
    /// not take it.
    func takes(_ point: CGPoint) -> Bool {
        !isHidden && superview != nil && frame.contains(point) && !holes.shellTakes(point)
    }

    #if os(macOS)
    override func covered(_ point: CGPoint) -> Bool { !takes(point) }

    override func mouseDown(with event: NSEvent) {
        page?.noteInput()
        super.mouseDown(with: event)
    }

    override func otherMouseDown(with event: NSEvent) {
        page?.noteInput()
        super.otherMouseDown(with: event)
    }

    override func keyDown(with event: NSEvent) {
        page?.noteInput()
        super.keyDown(with: event)
    }

    override func scrollWheel(with event: NSEvent) {
        page?.backHold.scrolled(vertical: abs(event.scrollingDeltaY) > abs(event.scrollingDeltaX))
        super.scrollWheel(with: event)
    }
    #endif
}
